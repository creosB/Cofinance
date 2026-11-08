import unittest
from core.cascade import CascadeClassifier
from core.signals import Action, GateConfig, Item, Judgment, SignalGate


class Stub:
    def __init__(self, j=None, fail=False):
        self.j, self.fail, self.calls = j, fail, 0

    def judge(self, item):
        self.calls += 1
        if self.fail:
            raise TimeoutError("down")
        return self.j


def run(primary, adj, **kw):
    g = GateConfig()
    c = CascadeClassifier(primary, adj, g, **kw)
    o = SignalGate(c, g, clock=lambda: 1000.0).process([Item("ACME", "rss", "https://x/1", "h")])[0]
    return c, o


HOT = Judgment(0.95, 0.9, "bearish", 0.9)


class TestCascade(unittest.TestCase):
    def test_confident_noise_skips_adjudicator(self):
        adj = Stub(HOT)
        c, o = run(Stub(Judgment(0.1, 0.1, "neutral", 0.6)), adj)
        self.assertEqual((o.action, adj.calls, c.routes["primary_only"]), (Action.IGNORE, 0, 1))

    def test_gray_zone_adjudicator_decides(self):
        _, o = run(Stub(Judgment(0.6, 0.2, "bullish", 0.6)), Stub(Judgment(0.2, 0.1, "neutral", 0.7)))
        self.assertEqual(o.action, Action.IGNORE)
        _, o = run(Stub(Judgment(0.4, 0.2, "neutral", 0.6)), Stub(Judgment(0.7, 0.2, "bullish", 0.8)))
        self.assertEqual(o.action, Action.DIGEST)

    def test_alert_needs_both(self):
        _, o = run(Stub(HOT), Stub(Judgment(0.92, 0.85, "bearish", 0.8)))
        self.assertEqual(o.action, Action.ALERT)
        _, o = run(Stub(HOT), Stub(Judgment(0.6, 0.85, "bearish", 0.8)))
        self.assertEqual(o.action, Action.DIGEST)

    def test_direction_conflict_becomes_unclear(self):
        _, o = run(Stub(HOT), Stub(Judgment(0.9, 0.8, "bullish", 0.7)))
        self.assertEqual(o.judgment.direction, "unclear")

    def test_adjudicator_down_can_digest_but_never_alert(self):
        c, o = run(Stub(HOT), Stub(fail=True))
        self.assertEqual((o.action, c.routes["adjudicator_down"]), (Action.DIGEST, 1))

    def test_primary_down_defers(self):
        _, o = run(Stub(fail=True), Stub(HOT))
        self.assertEqual(o.action, Action.DEFERRED)

    def test_gray_low_validated(self):
        with self.assertRaises(ValueError):
            CascadeClassifier(Stub(HOT), Stub(HOT), GateConfig(), gray_low=0.9)


if __name__ == "__main__":
    unittest.main()
