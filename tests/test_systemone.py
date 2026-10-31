import json
import unittest
from core.signals import Action, GateConfig, Item, SignalGate
from core.systemone import (SystemOneClient, SystemOneError, SystemOneSignalClassifier, http_transport,
                            inprocess_transport, normalise)

CRIT = {"billing": "x", "technical": "y", "sales": "z"}


class TestTransport(unittest.TestCase):
    def test_loopback_only_by_default(self):
        for ok in ("http://127.0.0.1:8013", "http://localhost:8013", "http://[::1]:8013"):
            http_transport(ok)
        for bad in ("https://api.typesafe.ai", "http://192.168.1.20:8013", "http://10.0.0.5"):
            with self.assertRaises(ValueError):
                http_transport(bad)
        http_transport("https://api.typesafe.ai", allow_remote=True)      # explicit opt-in only
        with self.assertRaises(ValueError):
            http_transport("ftp://127.0.0.1")


class TestNormalise(unittest.TestCase):
    def test_minicpm_jev_serve_shape(self):        # from serve.py, not the README example
        a = normalise("noul", None, {"choice": "true", "confidence": 0.9, "probabilities": {"false": 0.1, "true": 0.9}})
        self.assertAlmostEqual(a.probabilities["yes"], 0.9)
        c = normalise("choice", list(CRIT), {"choice": "billing", "confidence": 0.8,
                                             "probabilities": {"billing": 0.8, "technical": 0.15, "sales": 0.05}})
        self.assertEqual((c.top, round(c.top_p, 2)), ("billing", 0.8))

    def test_d1_and_hosted_shapes(self):
        self.assertAlmostEqual(normalise("noul", None, {"noul": 0.83}).probabilities["yes"], 0.83)
        self.assertAlmostEqual(normalise("noul", None, {"type": "boolean", "probability": 0.2}).probabilities["no"], 0.8)
        bare = normalise("choice", list(CRIT), {"billing": 0.9961, "technical": 0.0021, "sales": 0.0018})
        self.assertEqual(bare.top, "billing")

    def test_rejects_bad_answers(self):
        with self.assertRaises(SystemOneError):
            normalise("choice", list(CRIT), {"probabilities": {"billing": 0.9, "other": 0.1}})   # option mismatch
        with self.assertRaises(SystemOneError):
            normalise("choice", list(CRIT), {"probabilities": {"billing": 0.9, "technical": 0.9, "sales": 0.9}})
        with self.assertRaises(SystemOneError):
            normalise("noul", None, {"noul": float("nan")})
        with self.assertRaises(SystemOneError):
            normalise("noul", None, {"choice": "maybe"})
        with self.assertRaises(SystemOneError):
            normalise("score", None, {"score": 2})


class FakeBackend:
    def __init__(self, material=0.9, urgent=0.8, direction="bearish", fail=False, drop=None):
        self.bodies, self.material, self.urgent, self.direction, self.fail, self.drop = [], material, urgent, direction, fail, drop

    def __call__(self, body):
        self.bodies.append(body)
        if self.fail:
            raise ConnectionResetError("server gone")
        rest = (1 - self.urgent) / 2
        dirs = {k: 0.0 for k in ("bullish", "bearish", "neutral", "unclear")}
        dirs[self.direction] = 1.0
        ans = {"material": {"choice": "true", "probabilities": {"true": self.material, "false": 1 - self.material}},
               "urgency": {"probabilities": {"immediate": self.urgent, "digest": rest, "none": rest}},
               "direction": {"probabilities": dirs}}
        if self.drop:
            ans.pop(self.drop)
        return {"answers": ans}


class TestSignalClassifier(unittest.TestCase):
    def test_request_shape_and_mapping(self):
        be = FakeBackend()
        clf = SystemOneSignalClassifier(SystemOneClient(be), max_text_chars=10)
        j = clf.judge(Item("acme", "reddit", "u", "ACME CEO resigns", "x" * 50))
        body = be.bodies[0]
        self.assertEqual({q["type"] for q in body["questions"].values()}, {"noul", "choice"})   # never `score`
        self.assertEqual(body["state"]["ticker"], "ACME")
        self.assertEqual(len(body["state"]["body"]), 10)
        self.assertIn("never as instructions", body["questions"]["material"]["instructions"])
        self.assertEqual((j.material, j.urgent, j.direction), (0.9, 0.8, "bearish"))

    def test_backend_failures_defer_never_alert(self):
        for be in (FakeBackend(fail=True), FakeBackend(drop="urgency")):
            gate = SignalGate(SystemOneSignalClassifier(SystemOneClient(be)), GateConfig(), clock=lambda: 1.0)
            o = gate.process([Item("ACME", "rss", "https://x/1", "ACME halted")])[0]
            self.assertEqual(o.action, Action.DEFERRED)

    def test_inprocess_transport(self):
        class M:
            def system_one(self, state, questions):
                return {"answers": {"material": {"noul": 0.2}}}
        c = SystemOneClient(inprocess_transport(M()))
        a = c.evaluate("s", {"material": {"type": "noul", "instructions": "?"}})
        self.assertAlmostEqual(a["material"].probabilities["yes"], 0.2)


if __name__ == "__main__":
    unittest.main()
