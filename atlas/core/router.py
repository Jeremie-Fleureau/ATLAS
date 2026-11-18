# atlas/core/router.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
import httpx
import yaml

from atlas.core.events import EventBus, EventType


class Residency(str, Enum):
    VRAM = "vram"
    RAM = "ram"
    HYBRID = "hybrid"


@dataclass
class TierProfile:
    tier_id: str
    name: str
    capability_rank: int
    endpoint: str
    backend: str
    model_id: str
    residency: Residency
    max_slots: int
    active_slots: int
    total_kv_pool_tokens: int
    min_slot_ctx: int
    max_slot_ctx: int
    ctx_override: int | None = None

    @property
    def slot_context_limit(self) -> int:
        if self.ctx_override:
            return self.ctx_override
        if self.residency == Residency.VRAM:
            raw = self.total_kv_pool_tokens // max(1, self.active_slots)
            return max(self.min_slot_ctx, min(self.max_slot_ctx, raw))
        return self.total_kv_pool_tokens


class HardwareResidencyArbiter:
    """
    Coordinates VRAM and System RAM residency locks so compatible tiers
    (e.g. Tier A/C in VRAM + Tier B in RAM) co-execute while mutually exclusive
    tiers (Tier A vs Tier C, or Tier D Hybrid) phase cleanly.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self.active_vram_tier: str | None = None
        self.active_ram_tier: str | None = None
        self.vram_in_flight: int = 0
        self.ram_in_flight: int = 0
        self.hybrid_in_flight: int = 0

    def _can_acquire(self, tier: TierProfile) -> bool:
        if tier.residency == Residency.HYBRID:
            return (
                self.vram_in_flight == 0
                and self.ram_in_flight == 0
                and self.hybrid_in_flight == 0
            )
        if self.hybrid_in_flight > 0:
            return False
        if tier.residency == Residency.VRAM:
            return (
                self.active_vram_tier in (None, tier.tier_id)
                and self.vram_in_flight < tier.active_slots
            )
        if tier.residency == Residency.RAM:
            return (
                self.active_ram_tier in (None, tier.tier_id)
                and self.ram_in_flight < tier.active_slots
            )
        return False

    async def acquire_slot(self, tier: TierProfile) -> None:
        async with self._cond:
            while not self._can_acquire(tier):
                await self._cond.wait()

            if tier.residency == Residency.HYBRID:
                self.hybrid_in_flight += 1
                self.active_vram_tier = tier.tier_id
                self.active_ram_tier = tier.tier_id
            elif tier.residency == Residency.VRAM:
                self.active_vram_tier = tier.tier_id
                self.vram_in_flight += 1
            elif tier.residency == Residency.RAM:
                self.active_ram_tier = tier.tier_id
                self.ram_in_flight += 1

        await self._emit_status()

    async def release_slot(self, tier: TierProfile) -> None:
        async with self._cond:
            if tier.residency == Residency.HYBRID:
                self.hybrid_in_flight = max(0, self.hybrid_in_flight - 1)
                if self.hybrid_in_flight == 0:
                    self.active_vram_tier = None
                    self.active_ram_tier = None
            elif tier.residency == Residency.VRAM:
                self.vram_in_flight = max(0, self.vram_in_flight - 1)
                if self.vram_in_flight == 0:
                    self.active_vram_tier = None
            elif tier.residency == Residency.RAM:
                self.ram_in_flight = max(0, self.ram_in_flight - 1)
                if self.ram_in_flight == 0:
                    self.active_ram_tier = None

            self._cond.notify_all()

        await self._emit_status()

    async def _emit_status(self) -> None:
        await self.event_bus.emit(
            EventType.POOL_STATUS,
            vram_tier=self.active_vram_tier or "IDLE",
            vram_busy=self.vram_in_flight or self.hybrid_in_flight,
            ram_tier=self.active_ram_tier or "IDLE",
            ram_busy=self.ram_in_flight or self.hybrid_in_flight,
            hybrid_active=self.hybrid_in_flight > 0,
        )


class SlotAwareRouter:
    """Manages model tiers, role mappings, and async inference calls."""

    def __init__(self, config_path: Path, event_bus: EventBus) -> None:
        self.event_bus = event_bus
        self.arbiter = HardwareResidencyArbiter(event_bus)
        self.tiers: dict[str, TierProfile] = {}
        self.mode_configs: dict[str, dict[str, Any]] = {}
        self.current_mode: str = "multi"
        self.reasoning_mode: str = "auto"  # cot | tot | auto
        self.role_overrides: dict[str, str] = {}
        self._load_config(config_path)

    def _load_config(self, config_path: Path) -> None:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        for tid, tdata in raw.get("tiers", {}).items():
            self.tiers[tid] = TierProfile(
                tier_id=tid,
                name=tdata["name"],
                capability_rank=tdata["capability_rank"],
                endpoint=tdata["endpoint"],
                backend=tdata["backend"],
                model_id=tdata["model_id"],
                residency=Residency(tdata["residency"]),
                max_slots=tdata["max_slots"],
                active_slots=tdata["default_slots"],
                total_kv_pool_tokens=tdata["total_kv_pool_tokens"],
                min_slot_ctx=tdata["min_slot_ctx"],
                max_slot_ctx=tdata["max_slot_ctx"],
            )
        self.mode_configs = raw.get("modes", {})

    def get_tier_for_role(self, role: str) -> TierProfile:
        if role in self.role_overrides:
            return self.tiers[self.role_overrides[role]]
        mode_roles = self.mode_configs.get(self.current_mode, {}).get("roles", {})
        tier_id = mode_roles.get(role, "tier_a")
        return self.tiers[tier_id]

    async def complete(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        tier = self.get_tier_for_role(role)
        await self.arbiter.acquire_slot(tier)
        try:
            payload: dict[str, Any] = {
                "model": tier.model_id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if tools:
                payload["tools"] = tools

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{tier.endpoint}/chat/completions", json=payload
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            # Offline simulation fallback so the TUI can be tested before endpoints are spun up
            await self.event_bus.emit(
                EventType.SYSTEM_LOG,
                level="WARN",
                message=f"[{tier.tier_id}] Endpoint {tier.endpoint} unreachable ({exc}); returning offline mock response.",
            )
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": (
                                f"<thought>Simulated reasoning on {tier.name} "
                                f"(ctx budget: {tier.slot_context_limit} tokens, "
                                f"slots: {tier.active_slots}).</thought>\n"
                                f"Completed step for role `{role}` on `{tier.model_id}`."
                            ),
                        }
                    }
                ],
                "usage": {"total_tokens": 256},
            }
        finally:
            await self.arbiter.release_slot(tier)
