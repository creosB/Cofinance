from utils.memory import MemoryStore


def test_memory_store_roundtrip():
    store = MemoryStore()
    sid = "test-session"
    store.save_message(sid, "user", "Analyze NVDA vs AMD")
    store.add_entity(sid, "ticker", "NVDA")
    store.add_entity(sid, "ticker", "AMD")
    store.add_fact(sid, "intent", "comparison_requested", score=1.0)

    msgs = store.get_messages(sid)
    ents = store.get_entities(sid)
    facts = store.get_facts(sid)

    assert any("NVDA" in (m[2] or "") for m in msgs)
    assert any(e[3] == "NVDA" for e in ents)
    assert any(f[2] == "intent" and f[3] == "comparison_requested" for f in facts)
