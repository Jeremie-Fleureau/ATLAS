# atlas/cli/commands.py
from __future__ import annotations
from typing import TYPE_CHECKING

from atlas.core.events import EventType

if TYPE_CHECKING:
    from atlas.cli.app import AtlasApp


class SlashCommandHandler:
    """Parses and executes Hermes-style slash commands inside the ATLAS TUI."""

    def __init__(self, app: AtlasApp) -> None:
        self.app = app

    async def handle(self, line: str) -> str:
        parts = line.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/help":
            return (
                "ATLAS Commands:\n"
                "  /mode <single|multi|draft|swarm>   Switch execution mode\n"
                "  /tier <role> <tier_a|tier_b|tier_c|tier_d>  Bind role to hardware tier\n"
                "  /slots <tier_id> <count>           Adjust active slots & KV context scaling\n"
                "  /ctx <tier_id> <tokens>            Override per-slot context ceiling\n"
                "  /reasoning <cot|tot|auto>          Select CoT or concurrency-gated ToT\n"
                "  /steer <instruction>               Steer running agents mid-execution\n"
                "  /checkpoint [label]                Create git/workspace snapshot\n"
                "  /rollback [checkpoint_id]          Revert workspace to snapshot\n"
                "  /status                            Show detailed tier & residency table"
            )

        if cmd == "/mode":
            if not args or args[0] not in ("single", "multi", "draft", "swarm"):
                return "Usage: /mode <single|multi|draft|swarm>"
            self.app.router.current_mode = args[0]
            self.app.update_header_bar()
            return f"Switched active mode to [{args[0].upper()}]."

        if cmd in ("/tier", "/model"):
            if len(args) < 2:
                return "Usage: /tier <role> <tier_a|tier_b|tier_c|tier_d>"
            role, tier_id = args[0], args[1]
            if tier_id not in self.app.router.tiers:
                return f"Unknown tier `{tier_id}`. Available: {', '.join(self.app.router.tiers)}"
            self.app.router.role_overrides[role] = tier_id
            tier = self.app.router.tiers[tier_id]
            return (
                f"Bound role `{role}` -> {tier.name} "
                f"(residency={tier.residency.value}, slots={tier.active_slots}, ctx={tier.slot_context_limit})"
            )

        if cmd == "/slots":
            if len(args) < 2:
                return "Usage: /slots <tier_id> <count>"
            tier_id, count_str = args[0], args[1]
            if tier_id not in self.app.router.tiers:
                return f"Unknown tier `{tier_id}`."
            tier = self.app.router.tiers[tier_id]
            new_slots = max(1, min(tier.max_slots, int(count_str)))
            tier.active_slots = new_slots
            return (
                f"[{tier_id}] active_slots={tier.active_slots}/{tier.max_slots} -> "
                f"Dynamic per-slot context scaled to {tier.slot_context_limit} tokens."
            )

        if cmd == "/ctx":
            if len(args) < 2:
                return "Usage: /ctx <tier_id> <tokens|auto>"
            tier_id, val = args[0], args[1]
            if tier_id not in self.app.router.tiers:
                return f"Unknown tier `{tier_id}`."
            tier = self.app.router.tiers[tier_id]
            tier.ctx_override = None if val == "auto" else int(val)
            return f"[{tier_id}] per-slot context limit set to {tier.slot_context_limit} tokens."

        if cmd == "/reasoning":
            if not args or args[0] not in ("cot", "tot", "auto"):
                return "Usage: /reasoning <cot|tot|auto>"
            self.app.router.reasoning_mode = args[0]
            self.app.update_header_bar()
            return f"Reasoning engine set to [{args[0].upper()}]."

        if cmd == "/steer":
            if not args:
                return "Usage: /steer <instruction>"
            directive = " ".join(args)
            count = self.app.broadcast_steering(directive)
            return f"Broadcasted steering directive to {count} active agent(s)."

        if cmd == "/checkpoint":
            label = args[0] if args else "manual-snap"
            await self.app.event_bus.emit(
                EventType.CHECKPOINT, action="CREATED", label=label
            )
            return f"Checkpoint `{label}` recorded."

        if cmd == "/rollback":
            target = args[0] if args else "HEAD~1"
            await self.app.event_bus.emit(
                EventType.CHECKPOINT, action="ROLLBACK", label=target
            )
            return f"Rolled back workspace to `{target}`."

        if cmd == "/status":
            lines = ["Hardware Residency & Tier Profiles:"]
            for tid, t in self.app.router.tiers.items():
                lines.append(
                    f"  - {tid} ({t.residency.value.upper()}): {t.model_id} | "
                    f"slots={t.active_slots}/{t.max_slots} | slot_ctx={t.slot_context_limit}"
                )
            return "\n".join(lines)

        return f"Unknown command `{cmd}`. Type /help for available commands."
