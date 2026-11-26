from __future__ import annotations

from typing import List

from phi.agent import Agent
import streamlit as st

from .protocol import A2AEvent, EventBus, ToolCall
from .data_agent import get_data_agent
from .news_agent import get_news_agent
from .team_lead import get_team_lead
from utils.memory import MemoryStore


@st.cache_resource
def _orch_team_store():
    """Shared cache mapping keys to cached teams.

    This exists as a Streamlit resource so it survives reruns but not process restarts.
    We store a dict keyed by a session-scoped key to ensure per-session isolation.
    """
    return {}


class Orchestrator:
    def __init__(self, model_config, session_id: str, thinking_mode: bool = False, bus: EventBus | None = None) -> None:
        self.model_config = model_config
        self.session_id = session_id
        self.thinking_mode = thinking_mode
        self.bus = bus or EventBus()
        self.memory = MemoryStore()
        self._team: Agent | None = None

    def team(self) -> Agent:
        # Use a Streamlit cached resource to avoid repeated agent instantiation across
        # streamlit reruns which can cause memory blowups in long-lived deployments.
        # We keep a dictionary in a cached resource and use a session-scoped key
        # (session_id) to separate per-user teams.
        cache = _orch_team_store()
        # Include thinking_mode in the cache key so toggling thinking mode causes
        # the team to be rebuilt with the correct behavior/instructions.
        cache_key = f"{self.model_config.__class__.__name__}:{getattr(self.model_config, 'id', '')}:{self.session_id}:thinking={self.thinking_mode}"

        if self._team is None:
            # Return cached team if present
            if cache_key in cache:
                self._team = cache[cache_key]
            else:
                data = get_data_agent(self.model_config)
                news = get_news_agent(self.model_config)
                team = get_team_lead(self.model_config, [data, news], self.session_id, self.thinking_mode)
                cache[cache_key] = team
                self._team = team
        return self._team

    def log_tool_calls(self, chunk) -> None:
        try:
            if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                for tc in chunk.tool_calls:
                    evt = A2AEvent(
                        session_id=self.session_id,
                        event_type="TOOL_CALL",
                        payload={
                            "name": tc.get("name"),
                            "arguments": tc.get("arguments", {}),
                        },
                    )
                    self.bus.publish(evt)
                    self.memory.log_event(self.session_id, evt.event_type, evt.payload)
        except Exception:
            # Best-effort logging
            pass
