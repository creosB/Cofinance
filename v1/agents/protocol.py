from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import json


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class A2AMessage:
    session_id: str
    sender: str
    receiver: Optional[str]
    kind: str  # e.g., "task", "result", "status"
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_json(self) -> str:
        return json.dumps(
            {
                "session_id": self.session_id,
                "sender": self.sender,
                "receiver": self.receiver,
                "kind": self.kind,
                "content": self.content,
                "tool_calls": [tc.__dict__ for tc in self.tool_calls],
                "created_at": self.created_at,
            }
        )


@dataclass
class A2AEvent:
    session_id: str
    event_type: str  # e.g., "AGENT_MESSAGE", "TOOL_CALL", "TOOL_RESULT", "ERROR"
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_json(self) -> str:
        return json.dumps(
            {
                "session_id": self.session_id,
                "event_type": self.event_type,
                "payload": self.payload,
                "created_at": self.created_at,
            }
        )


class EventBus:
    """Simple in-process pub/sub event bus for agent-to-agent events."""

    def __init__(self) -> None:
        self._subscribers: List[Callable[[A2AEvent], None]] = []

    def subscribe(self, handler: Callable[[A2AEvent], None]) -> None:
        self._subscribers.append(handler)

    def publish(self, event: A2AEvent) -> None:
        for handler in list(self._subscribers):
            try:
                handler(event)
            except Exception:
                # Best-effort bus; handlers should not crash the app
                pass
