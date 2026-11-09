"""Read-only Polymarket client. Public, keyless GET endpoints only: no orders, wallets, keys or signing.

Hosts: gamma-api.polymarket.com (market metadata) and clob.polymarket.com (price history). Redirects, proxies,
credentials and other hosts are refused. Prices are crowd probabilities, not facts, forecasts we endorse or advice.
"""
from __future__ import annotations

import json
import math
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode, urlsplit

HOSTS = {"gamma-api.polymarket.com", "clob.polymarket.com"}
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


class PolymarketError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError("Polymarket redirects are not allowed.")


def fetch(url: str, limit: int = 8 * 1024 * 1024, timeout: float = 15.0) -> Any:
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in HOSTS or p.username or p.password or p.port:
        raise ValueError("Unapproved Polymarket endpoint.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 Cofinance/2 (read-only research)", "Accept": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        body = r.read(limit + 1)
    if len(body) > limit:
        raise PolymarketError("Polymarket response too large.")
    return json.loads(body)


def _list(value: Any) -> list:
    """Gamma encodes outcome lists as JSON strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _num(value: Any) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_market(m: dict) -> Optional[dict]:
    """Normalize one two-outcome market (Yes/No, Up/Down, team vs team). `yes_token` is the FIRST listed outcome;
    `yes_no` says whether it is literally "Yes". Multi-outcome or token-less markets return None."""
    if not isinstance(m, dict):
        return None
    labels = [str(o).strip()[:60] for o in _list(m.get("outcomes"))]
    tokens = [str(t) for t in _list(m.get("clobTokenIds"))]
    if len(labels) != 2 or len(tokens) != 2 or not all(tokens) or not all(labels):
        return None
    prices = [_num(p) for p in _list(m.get("outcomePrices"))]
    events = m.get("events") if isinstance(m.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    tags = event.get("tags") if isinstance(event.get("tags"), list) else []
    return {
        "id": str(m.get("id") or ""), "question": str(m.get("question") or "")[:300],
        "slug": str(m.get("slug") or "")[:200], "start": m.get("startDate"), "end": m.get("endDate"),
        "closed_time": m.get("closedTime"),
        "closed": bool(m.get("closed")), "active": bool(m.get("active")),
        "outcomes": labels, "yes_no": [x.lower() for x in labels] == ["yes", "no"],
        "yes_token": tokens[0], "no_token": tokens[1],
        "prices": prices if len(prices) == 2 and all(p is not None for p in prices) else None,
        "volume": _num(m.get("volumeNum")) or _num(m.get("volume")) or 0.0,
        "liquidity": _num(m.get("liquidityNum")) or _num(m.get("liquidity")) or 0.0,
        "best_bid": _num(m.get("bestBid")), "best_ask": _num(m.get("bestAsk")),
        "last_price": _num(m.get("lastTradePrice")),
        "event_id": str(event.get("id") or ""), "event_title": str(event.get("title") or "")[:300],
        "tags": sorted({str(t.get("slug") or t.get("label") or "").lower() for t in tags if isinstance(t, dict)} - {""}),
        "category": str(m.get("category") or "").lower(),
    }


def resolved_yes(market: dict) -> Optional[bool]:
    """Did the FIRST listed outcome win? True/False only for a cleanly settled market (final prices 1/0), else None."""
    p = market.get("prices")
    if not market.get("closed") or not p:
        return None
    if p[0] >= 0.999 and p[1] <= 0.001:
        return True
    if p[0] <= 0.001 and p[1] >= 0.999:
        return False
    return None


def list_markets(**params: Any) -> list[dict]:
    raw = fetch(GAMMA + "/markets?" + urlencode(params))
    if not isinstance(raw, list):
        raise PolymarketError("Unexpected markets response.")
    return raw


def search_events(term: str, limit: int = 8, timeout: float = 8.0) -> list[dict]:
    """Active events matching a keyword, each with its markets (public search, read-only)."""
    raw = fetch(GAMMA + "/public-search?" + urlencode({"q": term, "limit_per_type": int(limit), "events_status": "active"}),
                timeout=timeout)
    events = raw.get("events") if isinstance(raw, dict) else None
    if not isinstance(events, list):
        raise PolymarketError("Unexpected search response.")
    return [e for e in events if isinstance(e, dict)]


def price_history(token: str, start_ts: int, end_ts: int, fidelity: int = 60) -> list[tuple[int, float]]:
    """(unix seconds, price) points from the public CLOB history. Price is the market's recorded price, not an ask."""
    raw = fetch(CLOB + "/prices-history?" + urlencode({"market": token, "startTs": int(start_ts),
                                                       "endTs": int(end_ts), "fidelity": int(fidelity)}))
    points = raw.get("history") if isinstance(raw, dict) else None
    if not isinstance(points, list):
        raise PolymarketError("Unexpected price-history response.")
    out = []
    for row in points:
        t, p = (row.get("t"), _num(row.get("p"))) if isinstance(row, dict) else (None, None)
        if isinstance(t, (int, float)) and p is not None and 0 <= p <= 1:
            out.append((int(t), p))
    return sorted(out)
