import os
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, UTC
from typing import Dict, List, Optional, Tuple

from .db import AGENT_DB


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS facts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        score REAL DEFAULT 1.0,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        path TEXT,
        content TEXT,
        meta TEXT,
        created_at TEXT NOT NULL
    )
    """,
]


@contextmanager
def _conn():
    conn = sqlite3.connect(AGENT_DB)
    try:
        yield conn
    finally:
        conn.close()


def init_memory() -> None:
    with _conn() as conn:
        cur = conn.cursor()
        for stmt in SCHEMA_STATEMENTS:
            cur.execute(stmt)
        conn.commit()


class MemoryStore:
    def __init__(self) -> None:
        init_memory()

    def save_message(self, session_id: str, role: str, content: str) -> None:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO messages(session_id, role, content, created_at) VALUES(?,?,?,?)",
                (session_id, role, content, datetime.now(UTC).isoformat()),
            )
            conn.commit()
            # Log event for message
            try:
                self.log_event(session_id, "AGENT_MESSAGE", {"role": role})
            except Exception:
                pass

    def get_messages(self, session_id: str, limit: Optional[int] = None) -> List[Tuple[str, str, str, str]]:
        q = "SELECT session_id, role, content, created_at FROM messages WHERE session_id=? ORDER BY id ASC"
        if limit:
            q += f" LIMIT {int(limit)}"
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(q, (session_id,))
            return cur.fetchall()

    def add_entity(self, session_id: str, entity_type: str, value: str) -> None:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO entities(session_id, entity_type, value, created_at) VALUES(?,?,?,?)",
                (session_id, entity_type, value, datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def get_entities(self, session_id: str) -> List[Tuple[int, str, str, str, str]]:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, session_id, entity_type, value, created_at FROM entities WHERE session_id=? ORDER BY id DESC",
                (session_id,),
            )
            return cur.fetchall()

    def add_fact(self, session_id: str, key: str, value: str, score: float = 1.0) -> None:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO facts(session_id, key, value, score, created_at) VALUES(?,?,?,?,?)",
                (session_id, key, value, float(score), datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def get_facts(self, session_id: str, limit: int = 20) -> List[Tuple[int, str, str, str, float, str]]:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, session_id, key, value, score, created_at FROM facts WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, int(limit)),
            )
            return cur.fetchall()

    def log_event(self, session_id: str, event_type: str, payload: Dict) -> None:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO events(session_id, event_type, payload, created_at) VALUES(?,?,?,?)",
                (session_id, event_type, json.dumps(payload), datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def get_events(self, session_id: str, limit: int = 50, types: Optional[List[str]] = None):
        q = "SELECT event_type, payload, created_at FROM events WHERE session_id=?"
        params: List = [session_id]
        if types:
            placeholders = ",".join(["?"] * len(types))
            q += f" AND event_type IN ({placeholders})"
            params.extend(types)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
            # decode json payloads
            decoded = []
            for et, pl, ts in rows:
                try:
                    decoded.append((et, json.loads(pl or "{}"), ts))
                except Exception:
                    decoded.append((et, {"raw": pl}, ts))
            return decoded

    def save_artifact(
        self,
        session_id: str,
        kind: str,
        path: Optional[str] = None,
        content: Optional[str] = None,
        meta: Optional[Dict] = None,
    ) -> None:
        with _conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO artifacts(session_id, kind, path, content, meta, created_at) VALUES(?,?,?,?,?,?)",
                (
                    session_id,
                    kind,
                    path,
                    content,
                    json.dumps(meta or {}),
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()


TICKER_PATTERN = re.compile(r"\$?[A-Z]{1,5}")


def extract_entities_from_text(text: str) -> Dict[str, List[str]]:
    """Very lightweight entity extractor for tickers and simple intents."""
    tickers = set()
    for token in TICKER_PATTERN.findall(text or ""):
        clean = token[1:] if token.startswith("$") else token
        if 1 <= len(clean) <= 5 and clean.isupper():
            tickers.add(clean)

    intents = []
    lower = (text or "").lower()
    if any(k in lower for k in ["compare", "versus", "vs", "side-by-side"]):
        intents.append("comparison_requested")
    if any(k in lower for k in ["deep dive", "detailed", "fundamental"]):
        intents.append("deep_analysis")

    return {"tickers": sorted(list(tickers)), "intents": intents}


def compact_session_history(session_id: str, model_config) -> None:
    """
    Compacts the session history by summarizing older messages.
    Keeps the system prompt and the last few messages intact.
    """
    from phi.storage.agent.sqlite import SqlAgentStorage
    from phi.llm.message import Message
    from agents.memory_agent import summarize_chat_history

    storage = SqlAgentStorage(table_name="agent_sessions", db_url=f"sqlite:///{AGENT_DB}")
    session_data = storage.read(session_id=session_id)
    
    if not session_data or not session_data.memory or 'messages' not in session_data.memory:
        return

    messages = session_data.memory['messages']
    
    # Threshold to trigger compaction (e.g., > 10 messages)
    if len(messages) > 10:
        # Identify system message (usually the first one)
        system_msg = None
        start_idx = 0
        
        first_msg = messages[0]
        first_role = getattr(first_msg, 'role', None) or (first_msg.get('role') if isinstance(first_msg, dict) else None)
        
        if first_role == 'system':
            system_msg = first_msg
            start_idx = 1
        
        # We want to keep the last 5 messages
        msgs_to_keep = messages[-5:]
        # Summarize everything in between
        msgs_to_summarize = messages[start_idx:-5]
        
        if not msgs_to_summarize:
            return

        # Prepare history for the summarizer
        history_dicts = []
        for m in msgs_to_summarize:
            role = getattr(m, 'role', None) or (m.get('role') if isinstance(m, dict) else None)
            content = getattr(m, 'content', None) or (m.get('content') if isinstance(m, dict) else None)
            if role and content:
                # Truncate content to avoid token overflow in summarizer
                truncated_content = content[:500] + "...[truncated]" if len(content) > 500 else content
                history_dicts.append({"role": role, "content": truncated_content})
        
        try:
            summary = summarize_chat_history(history_dicts, model_config)
            summary_content = f"**[PREVIOUS CONVERSATION SUMMARY]**: {summary}"
            
            # Create summary message
            # Check if we are dealing with objects or dicts based on the first message
            is_obj = not isinstance(first_msg, dict)
            
            if is_obj:
                summary_msg = Message(role="system", content=summary_content)
            else:
                summary_msg = {"role": "system", "content": summary_content}
                
            new_messages = []
            if system_msg:
                new_messages.append(system_msg)
            new_messages.append(summary_msg)
            new_messages.extend(msgs_to_keep)
            
            session_data.memory['messages'] = new_messages
            storage.upsert(session_data)
            
        except Exception as e:
            print(f"Error compacting history: {e}")
