"""Cascade judge: a cheap System-1 screens every item, a stronger one adjudicates the hard cases.

  primary (GLiNER2.5-Decide, 340M, CPU)  -> every item
  adjudicator (MiniCPM5-2B-Jev or d1-2B)  -> only the gray zone and ALERT candidates

Rules (thresholds come from GateConfig so the cascade and SignalGate cannot drift apart):
  * material < gray_low                     -> primary only (cheap IGNORE). Known blind spot: primary false
                                               negatives are never re-checked; measure recall and tune gray_low.
  * gray_low <= material < alert_p          -> adjudicator decides (its judgment replaces the primary's)
  * ALERT candidate (material >= alert_p and urgent >= alert_urgent_p)
                                            -> both must agree: min() of the two probabilities;
                                               bullish vs bearish disagreement -> direction "unclear"
  * primary error                           -> raises -> SignalGate DEFERRED (retry)
  * adjudicator error                       -> primary judgment with urgent capped below the alert line:
                                               the item can still DIGEST, it can never ALERT on one model.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import replace

from .signals import Classifier, GateConfig, Item, Judgment

_POLAR = {"bullish", "bearish"}


class CascadeClassifier:
    def __init__(self, primary: Classifier, adjudicator: Classifier, gate: GateConfig,
                 gray_low: float = 0.35) -> None:
        if not 0.0 <= gray_low <= gate.alert_p:
            raise ValueError("gray_low must be within [0, alert_p]")
        self.primary, self.adjudicator, self.gate, self.gray_low = primary, adjudicator, gate, gray_low
        self.routes: Counter = Counter()

    def judge(self, item: Item) -> Judgment:
        p = self.primary.judge(item)
        alert_candidate = p.material >= self.gate.alert_p and p.urgent >= self.gate.alert_urgent_p
        gray = self.gray_low <= p.material < self.gate.alert_p
        if not (alert_candidate or gray):
            self.routes["primary_only"] += 1
            return p
        try:
            a = self.adjudicator.judge(item)
        except Exception:
            self.routes["adjudicator_down"] += 1
            return replace(p, urgent=min(p.urgent, max(0.0, self.gate.alert_urgent_p - 1e-3)))
        if not alert_candidate:
            self.routes["adjudicated"] += 1
            return a
        self.routes["alert_agreement"] += 1
        conflict = p.direction in _POLAR and a.direction in _POLAR and p.direction != a.direction
        return Judgment(material=min(p.material, a.material), urgent=min(p.urgent, a.urgent),
                        direction="unclear" if conflict else a.direction,
                        direction_p=min(p.direction_p, a.direction_p) if conflict else a.direction_p)
