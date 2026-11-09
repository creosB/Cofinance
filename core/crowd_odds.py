"""Crowd odds for a research run: live Polymarket markets about the run's assets. Read-only and deterministic.

Python selects and formats every value; the narrator never sees or restates them, so they never pass through the
number guards. Crowd prices are what traders are paying: not facts, not Cofinance forecasts and not advice.
Each market can carry how often markets at a similar price and time-to-close resolved that way in the measured
calibration study (docs/benchmarks/polymarket-calibration.json), when that bucket has enough markets.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from .polymarket import parse_market, search_events

TERMS = {
    "BTC": ("bitcoin",), "ETH": ("ethereum",), "SOL": ("solana",), "XRP": ("xrp",), "DOGE": ("dogecoin",),
    "BNB": ("bnb",), "NVDA": ("nvidia",), "AAPL": ("apple",), "MSFT": ("microsoft",), "TSLA": ("tesla",),
    "AMZN": ("amazon",), "GOOGL": ("google", "alphabet"), "GOOG": ("google", "alphabet"), "META": ("meta",),
    "NFLX": ("netflix",), "COIN": ("coinbase",), "MSTR": ("microstrategy",), "AMD": ("amd",), "INTC": ("intel",),
    "PLTR": ("palantir",), "SPY": ("s&p 500",), "QQQ": ("nasdaq",),
}
PER_ASSET = 3
MAX_ASSETS = 3
MAX_SPREAD = 0.10
MIN_VOLUME_24H = 1000.0
MIN_BUCKET_N = 30
NOTE = ("Crowd prices are what Polymarket traders are paying. They are not facts, not a Cofinance forecast and not "
        "advice. Cofinance never trades.")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,200}")


def _when(value) -> Optional[float]:
    if not value:
        return None
    s = str(value).strip().replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def load_calibration(path: Path) -> Optional[dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("horizons"), dict) else None


def history_for(calibration: Optional[dict], price: float, days_left: float) -> Optional[dict]:
    """How often markets at this price, this far from close, resolved for the priced outcome. None if thin."""
    if not calibration:
        return None
    horizon = "1d" if days_left <= 3 else "7d" if days_left <= 15 else "30d"
    for row in calibration["horizons"].get(horizon) or []:
        lo, hi = row.get("lo"), row.get("hi")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and (lo <= price < hi or (hi >= 1 and price == 1)):
            if int(row.get("n") or 0) < MIN_BUCKET_N:
                return None
            return {"horizon": horizon, "lo": lo, "hi": hi, "n": int(row["n"]), "resolved_rate": row["yes_rate"],
                    "ci95": row.get("ci95"), "study": calibration.get("study")}
    return None


def _price(m: dict) -> tuple[Optional[float], str]:
    bid, ask, last = m.get("best_bid"), m.get("best_ask"), m.get("last_price")
    if bid is not None and ask is not None and 0 <= bid <= ask <= 1 and ask - bid <= MAX_SPREAD:
        return round((bid + ask) / 2, 4), "midpoint"
    if last is not None and 0 <= last <= 1 and (bid is None or ask is None or ask - bid <= MAX_SPREAD):
        return round(last, 4), "last trade"
    return None, ""


def select(ticker: str, events: Sequence[dict], now: float, calibration: Optional[dict] = None) -> list[dict]:
    """One market per event: the one closest to an even bet (the crowd's dividing line), busiest events first."""
    terms = TERMS.get(ticker.upper(), (ticker.lower(),))
    pattern = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9])", re.I)
    picks = []
    for e in events:
        title = str(e.get("title") or "")[:300]
        volume = _num(e.get("volume24hr")) or 0.0
        slug = str(e.get("slug") or "").lower()
        if volume < MIN_VOLUME_24H or not _SLUG.fullmatch(slug):
            continue
        best = None
        for raw in e.get("markets") or []:
            m = parse_market(raw)
            if not m or not m["active"] or m["closed"]:
                continue
            if not pattern.search(m["question"] + " " + title):
                continue
            end = _when(m["end"]) or _when(e.get("endDate"))
            if not end or end <= now:
                continue
            p, basis = _price(m)
            if p is None:
                continue
            if best is None or abs(p - .5) < abs(best[0] - .5):
                best = (p, basis, m, end)
        if best:
            p, basis, m, end = best
            picks.append({"ticker": ticker, "event": title, "question": m["question"], "outcome": m["outcomes"][0],
                          "probability": p, "price_basis": basis, "bid": m["best_bid"], "ask": m["best_ask"],
                          "volume_24h": round(volume), "ends": _iso(end), "url": "https://polymarket.com/event/" + slug,
                          "history": history_for(calibration, p, (end - now) / 86400)})
    picks.sort(key=lambda x: -x["volume_24h"])
    return picks[:PER_ASSET]


def lookup(tickers: Sequence[str], *, search: Callable[[str], list] = search_events, now: Optional[float] = None,
           calibration: Optional[dict] = None) -> dict:
    now = time.time() if now is None else now
    markets, errors = [], []
    for ticker in list(dict.fromkeys(tickers))[:MAX_ASSETS]:
        term = TERMS.get(ticker.upper(), (ticker.lower(),))[0]
        try:
            markets += select(ticker, search(term), now, calibration)
        except Exception as exc:  # crowd odds are an aid: a failure never blocks research
            errors.append(f"{ticker}: {type(exc).__name__}")
    return {"source": "Polymarket public API, read-only", "fetched_at": _iso(now), "markets": markets, "errors": errors,
            "calibration_study": (calibration or {}).get("study"), "note": NOTE}
