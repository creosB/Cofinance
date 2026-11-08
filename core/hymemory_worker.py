"""Hy-Memory lite worker. Runs ONLY under data/hymemory/venv/Scripts/python.exe, launched by core.hymemory_port.

Protocol: one JSON object per line on stdin, one JSON reply per line on stdout ({"ok": true, ...} or
{"ok": false, "error": ...}). Ops: ping, add, search, delete, count. stdout carries protocol only; library output goes to
stderr (the parent logs it). Lite mode: raw embedding writes and vector search, no LLM calls. The parent sets the
LLM endpoint to a closed local port and routes every non-local connection to a dead proxy, so a hidden network or
LLM call fails instead of leaving the PC.
"""
import json
import os
import sys

USER, AGENT, SOURCE = "cofinance", "cofinance-chat", "cofinance_chat"


def main() -> int:
    proto = sys.stdout
    sys.stdout = sys.stderr  # keep library prints off the protocol stream
    if os.environ.get("MEMORY_MODE") != "lite":
        proto.write(json.dumps({"ok": False, "error": "worker refuses any mode except lite"}) + "\n"); proto.flush()
        return 2
    # Hy-Memory uses tiktoken only to count tokens, with a built-in character-estimate fallback. tiktoken downloads
    # its tokenizer from openaipublic.blob.core.windows.net on first use (caught by the 2026-11-08 network trap),
    # so it is made unimportable here: no download attempt, deterministic fallback.
    sys.modules["tiktoken"] = None
    from hy_memory import HyMemoryClient
    client = HyMemoryClient(mode="lite")
    proto.write(json.dumps({"ok": True, "ready": True}) + "\n"); proto.flush()
    for line in sys.stdin:
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "ping":
                out = {"pong": True}
            elif op == "add":
                meta = {"run_id": req.get("run_id") or "", "tickers": ",".join(req.get("tickers") or []),
                        "question": (req.get("question") or "")[:500]}
                res = client.add([{"role": "user", "content": req["question"]}, {"role": "assistant", "content": req["answer"]}],
                                 user_id=USER, agent_id=AGENT, session_id=req["session_id"], source=SOURCE, meta=meta,
                                 request_id=req.get("run_id") or None, language="en")
                out = {"result": res}
            elif op == "search":
                res = client.search(req["query"], user_ids=[USER], agent_ids=[AGENT], session_ids=[req["session_id"]],
                                    include_sources=[SOURCE], limit=int(req.get("limit", 4)),
                                    min_score=float(req.get("min_score", 0.4)), rerank=False)
                out = {"result": res}
            elif op == "delete":
                # kind=chat avoids AmbiguousMemoryIdError when an id could match several namespaces.
                try:
                    from hy_memory import MemoryKind
                    kind = MemoryKind("chat")
                except Exception:
                    kind = "chat"
                res = client.delete(str(req["memory_id"]), kind=kind)
                out = {"result": res}
            elif op == "exists":
                # Live 2026-11-08: a repeat delete of an id returns success False, deleted_count 0. get() returns None for
                # a missing id, so the caller can tell "already gone" from "delete failed".
                try:
                    from hy_memory import MemoryKind
                    kind = MemoryKind("chat")
                except Exception:
                    kind = "chat"
                out = {"result": {"exists": client.get(str(req["memory_id"]), kind=kind) is not None}}
            elif op == "delete_session":
                # Forget-all backstop. Always scoped to one user, one agent and exactly one conversation: an empty list
                # would mean "every session" in the SDK, so a missing id is refused here, never passed through.
                sid = str(req.get("session_id") or "")
                if not sid:
                    raise ValueError("delete_session needs one conversation id")
                res = client.delete_all(user_id=USER, agent_ids=[AGENT], session_ids=[sid])
                out = {"result": res}
            elif op == "count":
                res = client.list_memories(user_id=USER, agent_ids=[AGENT], session_ids=[req["session_id"]], include_sources=[SOURCE], limit=500)
                out = {"result": res}
            else:
                raise ValueError("unknown op")
            proto.write(json.dumps({"ok": True, **out}, default=str) + "\n")
        except Exception as exc:  # report, never crash the loop
            proto.write(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}) + "\n")
        proto.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
