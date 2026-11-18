# atlas/core/events.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine


class EventType(str, Enum):
    AGENT_STATE = "agent_state"
    POOL_STATUS = "pool_status"
    STREAM_CHUNK = "stream_chunk"
    THOUGHT_CHUNK = "thought_chunk"
    TOOL_CALL = "tool_call"
    SWARM_MSG = "swarm_msg"
    CHECKPOINT = "checkpoint"
    SYSTEM_LOG = "system_log"


@dataclass
class AtlasEvent:
    event_type: EventType
    payload: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now().strftime("%H:%M:%S")
    )


SubscriberCallback = Callable[[AtlasEvent], Coroutine[Any, Any, None]]


class EventBus:
    """Async pub-sub event bus driving the Textual multi-pane telemetry UI."""

    def __init__(self) -> None:
        self._subscribers: list[SubscriberCallback] = []

    def subscribe(self, callback: SubscriberCallback) -> None:
        self._subscribers.append(callback)

    async def emit(self, event_type: EventType, **payload: Any) -> None:
        event = AtlasEvent(event_type=event_type, payload=payload)
        for sub in self._subscribers:
            try:
                await sub(event)
            except Exception:
                pass
