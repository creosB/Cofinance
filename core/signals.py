"""Signal gate: the 'System 1' loop pattern, adapted for market news.

Plain code does dedupe / cooldown / budget. A pluggable classifier supplies calibrated
probabilities. Code (not a model) turns probabilities into IGNORE / DIGEST / ALERT.

Invariants (tested):
  * item text is DATA: the gate never executes or forwards it to anything with tools;
  * a classifier failure can never produce ALERT;
  * the same item is judged at most once; alerts are rate-limited per ticker and per run;
  * the judgment budget is enforced before any classifier call.
"""
from __future__ import annotations

import enum
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, MutableSet, Optional, Protocol


@dataclass(frozen=True)
class Item:
    ticker: str
    source: str            # "reddit" | "hn" | "stocktwits" | "rss" | ...
    url: str
    title: str
    text: str = ""
    published_at: float = 0.0   # epoch seconds

    @property
    def key(self) -> str:
        basis = self.url.strip().lower() or re.sub(r"\W+", " ", self.title.lower()).strip()
        return hashlib.sha1(f"{self.ticker.upper()}|{basis}".encode()).hexdigest()


@dataclass(frozen=True)
class Judgment:
    material: float                 # P(item plausibly changes the thesis / price within ~1 week)
    urgent: float                   # P(needs attention before the next scheduled digest)
    direction: str = "unclear"      # bullish | bearish | neutral | unclear
    direction_p: float = 0.0


class Classifier(Protocol):
    def judge(self, item: Item) -> Judgment: ...


class Action(str, enum.Enum):
    IGNORE = "ignore"
    DIGEST = "digest"
    ALERT = "alert"
    DEFERRED = "deferred"           # not judged (budget or classifier error); retry later


@dataclass
class GateConfig:
    digest_p: float = 0.50
    alert_p: float = 0.85
    alert_urgent_p: float = 0.70
    ticker_cooldown_s: float = 3600.0
    max_judgments: int = 200
    max_alerts: int = 5
    max_age_s: float = 30 * 86400.0


@dataclass
class Outcome:
    item: Item
    action: Action
    reason: str
    judgment: Optional[Judgment] = None


class SignalGate:
    def __init__(self, classifier: Classifier, config: Optional[GateConfig] = None,
                 seen: Optional[MutableSet[str]] = None,
                 last_alert: Optional[Dict[str, float]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.classifier = classifier
        self.cfg = config or GateConfig()
        self.seen = seen if seen is not None else set()
        self.last_alert = last_alert if last_alert is not None else {}
        self.clock = clock

    def process(self, items: Iterable[Item]) -> List[Outcome]:
        out: List[Outcome] = []
        judged = alerts = 0
        now = self.clock()
        for it in items:
            if it.published_at and now - it.published_at > self.cfg.max_age_s:
                out.append(Outcome(it, Action.IGNORE, "stale"))
                continue
            if it.key in self.seen:
                out.append(Outcome(it, Action.IGNORE, "duplicate"))
                continue
            if judged >= self.cfg.max_judgments:
                out.append(Outcome(it, Action.DEFERRED, "judgment_budget"))   # not marked seen
                continue
            judged += 1
            try:
                j = self.classifier.judge(it)
            except Exception as exc:                                   # fail closed, never ALERT
                out.append(Outcome(it, Action.DEFERRED, f"classifier_error:{type(exc).__name__}"))
                continue
            self.seen.add(it.key)
            out.append(self._decide(it, j, now, alerts))
            if out[-1].action is Action.ALERT:
                alerts += 1
        return out

    def _decide(self, it: Item, j: Judgment, now: float, alerts_so_far: int) -> Outcome:
        c = self.cfg
        if j.material < c.digest_p:
            return Outcome(it, Action.IGNORE, "below_digest_threshold", j)
        wants_alert = j.material >= c.alert_p and j.urgent >= c.alert_urgent_p
        if not wants_alert:
            return Outcome(it, Action.DIGEST, "material_not_urgent", j)
        tkr = it.ticker.upper()
        if now - self.last_alert.get(tkr, -1e18) < c.ticker_cooldown_s:
            return Outcome(it, Action.DIGEST, "alert_downgraded:ticker_cooldown", j)
        if alerts_so_far >= c.max_alerts:
            return Outcome(it, Action.DIGEST, "alert_downgraded:run_cap", j)
        self.last_alert[tkr] = now
        return Outcome(it, Action.ALERT, "material_and_urgent", j)
