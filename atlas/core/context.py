# atlas/core/context.py
from __future__ import annotations
import json
from typing import Any
from atlas.core.router import SlotAwareRouter, TierProfile


class ContextBudgeter:
    """
    Enforces strict per-slot token budgets and automatic compaction.
    More concurrent slots on Tier A / Tier C -> tighter per-slot context -> earlier compaction.
    """

    def __init__(self, router: SlotAwareRouter, compaction_ratio: float = 0.75) -> None:
        self.router = router
        self.compaction_ratio = compaction_ratio

    @staticmethod
    def estimate_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
        raw_text = json.dumps(messages, ensure_ascii=False)
        if tools:
            raw_text += json.dumps(tools, ensure_ascii=False)
        return max(1, len(raw_text) // 4)

    @staticmethod
    def compact_tool_schemas(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strips verbose descriptions and redundant schema metadata to save context."""
        compacted = []
        for tool in mcp_tools:
            fn = tool.get("function", tool)
            compacted.append(
                {
                    "type": "function",
                    "function": {
                        "name": fn.get("name", ""),
                        "description": (fn.get("description", "") or "")[:140],
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    },
                }
            )
        return compacted

    @staticmethod
    def truncate_tool_output(output: str, max_chars: int = 3200) -> str:
        if len(output) <= max_chars:
            return output
        half = max_chars // 2
        return (
            output[:half]
            + f"\n... [TRUNCATED {len(output) - max_chars} CHARS FOR CONTEXT BUDGET] ...\n"
            + output[-half:]
        )

    async def enforce_budget(
        self,
        role: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], int, int]:
        tier: TierProfile = self.router.get_tier_for_role(role)
        limit = tier.slot_context_limit
        used = self.estimate_tokens(messages, tools)

        if used < int(limit * self.compaction_ratio) or len(messages) <= 4:
            return messages, used, limit

        # Keep system prompt + earliest user task + last 2 turns; summarize the middle
        head = messages[:2]
        middle = messages[2:-2]
        tail = messages[-2:]

        middle_dump = "\n".join(
            f"[{m.get('role')}]: {str(m.get('content'))[:600]}" for m in middle
        )
        summary_prompt = [
            {
                "role": "system",
                "content": (
                    "Compress the following agent execution history into a dense State & Findings Digest. "
                    "Preserve exact file paths, tool outcomes, constraints, and unresolved errors."
                ),
            },
            {"role": "user", "content": middle_dump},
        ]

        resp = await self.router.complete(role=role, messages=summary_prompt, max_tokens=512)
        digest = (
            resp.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "Compacted state.")
        )

        compacted_messages = (
            head
            + [
                {
                    "role": "system",
                    "content": f"[COMPACTED HISTORY DIGEST]:\n{digest}",
                }
            ]
            + tail
        )
        new_used = self.estimate_tokens(compacted_messages, tools)
        return compacted_messages, new_used, limit
