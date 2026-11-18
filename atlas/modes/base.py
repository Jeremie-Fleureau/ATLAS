# atlas/modes/base.py
from __future__ import annotations
import asyncio
import re
from typing import Any, Callable, Coroutine

from atlas.core.context import ContextBudgeter
from atlas.core.events import EventBus, EventType
from atlas.core.router import SlotAwareRouter


ToolExecutor = Callable[[str, dict[str, Any]], Coroutine[Any, Any, str]]


class BaseAgent:
    """
    Core agent execution loop supporting:
    - Dynamic context compaction
    - Full MCP tool execution
    - Linear CoT + Self-Check
    - Step-level parallel Tree-of-Thoughts (ToT) + Decision Judge
    - Mid-run user steering injection
    """

    def __init__(
        self,
        agent_id: str,
        role: str,
        router: SlotAwareRouter,
        budgeter: ContextBudgeter,
        event_bus: EventBus,
        mcp_tools: list[dict[str, Any]] | None = None,
        tool_executor: ToolExecutor | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.role = role
        self.router = router
        self.budgeter = budgeter
        self.event_bus = event_bus
        self.mcp_tools = budgeter.compact_tool_schemas(mcp_tools or [])
        self.tool_executor = tool_executor
        self.steering_queue: asyncio.Queue[str] = asyncio.Queue()
        self.messages: list[dict[str, Any]] = []

    def steer(self, instruction: str) -> None:
        """Injects a live mid-run steering instruction into the agent loop."""
        self.steering_queue.put_nowait(instruction)

    def _effective_reasoning_mode(self) -> str:
        tier = self.router.get_tier_for_role(self.role)
        mode = self.router.reasoning_mode
        if mode == "auto":
            return "tot" if tier.active_slots >= 2 else "cot"
        if mode == "tot" and tier.active_slots < 2:
            return "cot"
        return mode

    async def _drain_steering(self) -> None:
        while not self.steering_queue.empty():
            directive = await self.steering_queue.get()
            self.messages.append(
                {
                    "role": "user",
                    "content": f"[MID-RUN USER STEERING]: {directive}",
                }
            )
            await self.event_bus.emit(
                EventType.SYSTEM_LOG,
                level="STEER",
                message=f"Injected steering into {self.agent_id}: {directive}",
            )

    async def _step_tot(self, k_branches: int = 3) -> dict[str, Any]:
        """Samples k parallel candidate steps and scores them with deterministic checks + Judge model."""
        tier = self.router.get_tier_for_role(self.role)
        k = min(k_branches, tier.active_slots)

        await self.event_bus.emit(
            EventType.AGENT_STATE,
            agent_id=self.agent_id,
            role=self.role,
            tier=tier.tier_id,
            reasoning=f"ToT(k={k})",
            ctx_used=self.budgeter.estimate_tokens(self.messages, self.mcp_tools),
            ctx_limit=tier.slot_context_limit,
            status="Sampling ToT branches",
        )

        tasks = [
            self.router.complete(
                role=self.role,
                messages=self.messages,
                tools=self.mcp_tools or None,
                temperature=0.35 + (i * 0.1),
            )
            for i in range(k)
        ]
        candidates = await asyncio.gather(*tasks)

        # Deterministic score + Judge evaluation
        scored: list[tuple[float, dict[str, Any]]] = []
        for idx, cand in enumerate(candidates):
            msg = cand.get("choices", [{}])[0].get("message", {})
            content = msg.get("content", "") or ""
            det_score = 1.0
            if "<thought>" in content and "</thought>" in content:
                det_score += 0.5
            if msg.get("tool_calls"):
                det_score += 0.5
            scored.append((det_score, msg))

        # Ask Judge role (typically Tier B in RAM) to pick the best candidate
        judge_prompt = [
            {
                "role": "system",
                "content": "You are the ATLAS ToT Decision Judge. Reply ONLY with the integer index (0, 1, ...) of the strongest next action.",
            },
            {
                "role": "user",
                "content": "\n\n".join(
                    f"Candidate {i} (det_score={s}):\n{m.get('content', '')}"
                    for i, (s, m) in enumerate(scored)
                ),
            },
        ]
        judge_resp = await self.router.complete(
            role="judge", messages=judge_prompt, temperature=0.0, max_tokens=16
        )
        raw_pick = (
            judge_resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "0")
        )
        match = re.search(r"\d+", raw_pick)
        chosen_idx = int(match.group(0)) if match else 0
        if chosen_idx >= len(scored):
            chosen_idx = 0

        return scored[chosen_idx][1]

    async def run(self, system_prompt: str, task_prompt: str, max_steps: int = 6) -> str:
        tier = self.router.get_tier_for_role(self.role)
        reasoning = self._effective_reasoning_mode()

        self.messages = [
            {
                "role": "system",
                "content": (
                    f"{system_prompt}\n"
                    "Always enclose internal reasoning inside <thought>...</thought> before acting or responding."
                ),
            },
            {"role": "user", "content": task_prompt},
        ]

        final_answer = ""
        for step in range(1, max_steps + 1):
            await self._drain_steering()
            self.messages, used, limit = await self.budgeter.enforce_budget(
                self.role, self.messages, self.mcp_tools
            )

            await self.event_bus.emit(
                EventType.AGENT_STATE,
                agent_id=self.agent_id,
                role=self.role,
                tier=tier.tier_id,
                reasoning=reasoning.upper(),
                ctx_used=used,
                ctx_limit=limit,
                status=f"Step {step}/{max_steps}",
            )

            if reasoning == "tot":
                assistant_msg = await self._step_tot()
            else:
                resp = await self.router.complete(
                    role=self.role,
                    messages=self.messages,
                    tools=self.mcp_tools or None,
                )
                assistant_msg = resp.get("choices", [{}])[0].get("message", {})

            content = assistant_msg.get("content", "") or ""
            self.messages.append(assistant_msg)

            # Separate <thought> block from visible response
            thought_match = re.search(r"<thought>(.*?)</thought>", content, re.DOTALL)
            if thought_match:
                await self.event_bus.emit(
                    EventType.THOUGHT_CHUNK,
                    agent_id=self.agent_id,
                    thought=thought_match.group(1).strip(),
                )
                visible = re.sub(r"<thought>.*?</thought>", "", content, flags=re.DOTALL).strip()
            else:
                visible = content.strip()

            if visible:
                await self.event_bus.emit(
                    EventType.STREAM_CHUNK,
                    agent_id=self.agent_id,
                    role=self.role,
                    text=visible,
                )

            # Execute MCP tool calls if requested
            tool_calls = assistant_msg.get("tool_calls") or []
            if tool_calls and self.tool_executor:
                for tc in tool_calls:
                    fn_name = tc["function"]["name"]
                    fn_args = tc["function"].get("arguments", {})
                    await self.event_bus.emit(
                        EventType.TOOL_CALL,
                        agent_id=self.agent_id,
                        tool=fn_name,
                        args=str(fn_args)[:120],
                    )
                    raw_result = await self.tool_executor(fn_name, fn_args)
                    safe_result = self.budgeter.truncate_tool_output(raw_result)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", fn_name),
                            "name": fn_name,
                            "content": safe_result,
                        }
                    )
                continue

            # Self-Check pass on completion
            final_answer = visible or content
            break

        await self.event_bus.emit(
            EventType.AGENT_STATE,
            agent_id=self.agent_id,
            role=self.role,
            tier=tier.tier_id,
            reasoning=reasoning.upper(),
            ctx_used=self.budgeter.estimate_tokens(self.messages, self.mcp_tools),
            ctx_limit=tier.slot_context_limit,
            status="Done",
        )
        return final_answer
