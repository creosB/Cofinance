import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from qualify import memory_eval as me


def _make_chat_db(path: Path, session_turn_counts: list[int]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE chat_sessions (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE chat_messages (id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        mid = 0
        for s_idx, turns in enumerate(session_turn_counts):
            sid = f"s{s_idx}"
            conn.execute("INSERT INTO chat_sessions (id) VALUES (?)", (sid,))
            for t in range(turns):
                mid += 1
                conn.execute("INSERT INTO chat_messages VALUES (?, ?, 'user', ?)", (f"u{mid}", sid, f"q{t}"))
                mid += 1
                conn.execute("INSERT INTO chat_messages VALUES (?, ?, 'assistant', ?)", (f"a{mid}", sid, f"a{t}"))
        conn.commit()
    finally:
        conn.close()


class TestMemoryEval(unittest.TestCase):
    def test_enforces_30_real_followup_threshold_and_counts_shadow_keyword(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "chat_v2.db"
            traces = root / "traces"
            traces.mkdir()

            _make_chat_db(db, [1, 1, 2, 3])
            (traces / "t1.json").write_text(
                json.dumps({
                    "spans": [{
                        "name": "Recall conversation memory",
                        "outputs": {
                            "backend": "hybrid: keyword + Hy-Memory lite (rank fusion)",
                            "shadow_keyword": {"agrees": True},
                        },
                    }]
                }),
                encoding="utf-8",
            )
            (traces / "t2.json").write_text(
                json.dumps({
                    "spans": [{
                        "name": "Recall conversation memory",
                        "outputs": {
                            "backend": "hybrid: keyword + Hy-Memory lite (rank fusion)",
                            "shadow_keyword": {"agrees": False},
                        },
                    }]
                }),
                encoding="utf-8",
            )

            rep = me.evaluate_memory(db, traces)
            real = rep["real_usage"]
            self.assertEqual(real["status"], "waiting_for_data")
            self.assertFalse(real["ready"])
            self.assertEqual(real["sessions"], 4)
            self.assertEqual(real["user_messages"], 7)
            self.assertEqual(real["sessions_with_followups"], 2)
            self.assertEqual(real["real_followups"], 3)
            self.assertEqual(real["remaining_followups_needed"], 27)
            self.assertEqual(real["shadow_keyword"]["compared"], 2)
            self.assertEqual(real["shadow_keyword"]["agrees"], 1)
            self.assertEqual(real["shadow_keyword"]["disagrees"], 1)
            self.assertEqual(real["shadow_keyword"]["agreement_rate"], 0.5)

            plumbing = rep["synthetic_plumbing"]
            self.assertTrue(plumbing["synthetic_plumbing_only"])
            self.assertTrue(plumbing["passed"])
            self.assertEqual(plumbing["cross_session_leaks"], 0)
            self.assertEqual(plumbing["forgotten_leaks"], 0)
            self.assertGreater(plumbing["paraphrase_hits"]["hybrid"], plumbing["paraphrase_hits"]["keyword"])

            db_ready = root / "chat_ready.db"
            _make_chat_db(db_ready, [16, 16])
            rep_ready = me.inspect_real_followups(db_ready, traces)
            self.assertTrue(rep_ready["ready"])
            self.assertEqual(rep_ready["status"], "ready_for_comparison")
            self.assertEqual(rep_ready["real_followups"], 30)
            self.assertEqual(rep_ready["remaining_followups_needed"], 0)

            base_kwargs = dict(
                kw_lex=2,
                hyb_lex=2,
                lex_total=2,
                kw_para=0,
                hyb_para=3,
                cross_leaks=0,
                forgotten_leaks=0,
                note_tagged_untrusted=True,
            )
            self.assertTrue(me.plumbing_passed(**base_kwargs))
            self.assertFalse(me.plumbing_passed(**{**base_kwargs, "cross_leaks": 1}))
            self.assertFalse(me.plumbing_passed(**{**base_kwargs, "forgotten_leaks": 1}))


if __name__ == "__main__":
    unittest.main()
