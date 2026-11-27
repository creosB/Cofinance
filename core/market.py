"""Headless, bounded market-data tools. Public Yahoo chart data, optional fundamentals,
Alternative.me sentiment. Never generates demo values or calls Streamlit.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

CRYPTO = {"BTC","ETH","SOL","ADA","DOGE","XRP","BNB","AVAX","DOT","LINK"}
SYMBOL = re.compile(r"[A-Z^][A-Z0-9.\-=]{0,14}")
SEARCH_QUOTE_TYPES = frozenset({"EQUITY", "ETF", "INDEX", "CRYPTOCURRENCY", "FUTURE", "CURRENCY", "MUTUALFUND"})
_SYMBOL_CACHE: dict[str, tuple[float, dict | None]] = {}
_SYMBOL_CACHE_TTL = 300.0


def valid_symbol(symbol):
    if not isinstance(symbol,str) or ".." in symbol or not SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid ticker symbol.")
    return symbol


_EXPLICIT_TICKER = re.compile(r"\$?[A-Z][A-Z0-9.\-=]{0,9}")
_QUERY_STOP = frozenset({"the", "of", "and", "in", "for", "inc", "corp", "co", "ltd"})
_TYPE_CUES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\b(?:stocks?|shares?|equit(?:y|ies))\b", re.I), "EQUITY"),
    (re.compile(r"\b(?:etfs?|exchange[- ]traded\s+funds?)\b", re.I), "ETF"),
    (re.compile(r"\b(?:crypto|cryptocurrenc(?:y|ies)|coins?)\b", re.I), "CRYPTOCURRENCY"),
    (re.compile(r"\b(?:futures?|commodit(?:y|ies))\b", re.I), "FUTURE"),
    (re.compile(r"\b(?:ind(?:ex|ices))\b", re.I), "INDEX"),
)
_MARKET_CUES: tuple[tuple[str, re.Pattern, frozenset[str], tuple[str, ...], tuple[str, ...]], ...] = (
    ("TR", re.compile(r"\b(?:turkish|turkey|t[uü]rkiye|istanbul|bist)\b", re.I), frozenset({"IST"}), (".IS",), ("turk", "türk", "turkey", "istanbul")),
    ("DE", re.compile(r"\b(?:german|germany|frankfurt|xetra|dax)\b", re.I), frozenset({"GER", "FRA", "ETR", "DUS", "MUN", "HAM", "BER"}), (".DE", ".F"), ("german",)),
    ("GB", re.compile(r"\b(?:british|uk|london|ftse|lse)\b", re.I), frozenset({"LSE", "LON", "IOB"}), (".L",), ("london",)),
    ("JP", re.compile(r"\b(?:japanese|japan|tokyo|nikkei)\b", re.I), frozenset({"JPX", "TYO"}), (".T",), ("japan", "tokyo")),
    ("FR", re.compile(r"\b(?:french|france|paris|cac)\b", re.I), frozenset({"PAR"}), (".PA",), ("france", "paris")),
    ("CA", re.compile(r"\b(?:canadian|canada|toronto|tsx)\b", re.I), frozenset({"TOR", "TSX", "VAN"}), (".TO", ".V"), ("canada",)),
    ("HK", re.compile(r"\b(?:hong\s+kong|hkex|hsi)\b", re.I), frozenset({"HKG"}), (".HK",), ("hong kong",)),
    ("IN", re.compile(r"\b(?:indian|india|mumbai|nse|bse|sensex|nifty)\b", re.I), frozenset({"NSI", "BOM", "BSE"}), (".NS", ".BO"), ("india",)),
    ("KR", re.compile(r"\b(?:korean|korea|seoul|kospi|kosdaq)\b", re.I), frozenset({"KSC", "KOE"}), (".KS", ".KQ"), ("korea",)),
    ("AU", re.compile(r"\b(?:australian|australia|sydney|asx)\b", re.I), frozenset({"ASX"}), (".AX",), ("australia",)),
    ("BR", re.compile(r"\b(?:brazilian|brazil|bovespa|sao\s+paulo)\b", re.I), frozenset({"SAO"}), (".SA",), ("brazil",)),
    ("CH", re.compile(r"\b(?:swiss|switzerland|zurich)\b", re.I), frozenset({"EBS"}), (".SW",), ("swiss",)),
    ("NL", re.compile(r"\b(?:dutch|netherlands|amsterdam|aex)\b", re.I), frozenset({"AMS"}), (".AS",), ("netherlands",)),
    ("ES", re.compile(r"\b(?:spanish|spain|madrid|ibex)\b", re.I), frozenset({"MCE"}), (".MC",), ("spain",)),
    ("IT", re.compile(r"\b(?:italian|italy|milan)\b", re.I), frozenset({"MIL"}), (".MI",), ("italy",)),
    ("CN", re.compile(r"\b(?:chinese|china|shanghai|shenzhen)\b", re.I), frozenset({"SHH", "SHZ"}), (".SS", ".SZ"), ("china",)),
    ("US", re.compile(r"\b(?:american|nyse|nasdaq)\b|\bu\.?s\.?\s+(?:stock|equity|market|etf)\b", re.I), frozenset({"NMS", "NYQ", "NGM", "NCM", "PCX", "ASE", "BTS"}), (), ()),
)


def _extract_query_cues(context_query: str) -> tuple[str | None, tuple[str, frozenset[str], tuple[str, ...], tuple[str, ...]] | None]:
    text = context_query or ""
    if not text:
        return None, None
    want_type = next((qtype for pat, qtype in _TYPE_CUES if pat.search(text)), None)
    want_market = next(((code, exchs, sfxs, hints) for code, pat, exchs, sfxs, hints in _MARKET_CUES if pat.search(text)), None)
    return want_type, want_market


def _matches_market(symbol: str, exchange: str, name: str, market_cue) -> bool:
    if not market_cue:
        return False
    code, exchs, sfxs, hints = market_cue
    if exchange.upper() in exchs:
        return True
    if sfxs and any(symbol.upper().endswith(s) for s in sfxs):
        return True
    if code == "US" and "." not in symbol and "=" not in symbol and not symbol.startswith("^"):
        return True
    low_name = name.lower()
    return any(h in low_name for h in hints)


def _overlap_tokens(text: str) -> set[str]:
    toks = {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _QUERY_STOP}
    longer = {t for t in toks if len(t) >= 2}
    return longer or toks


def resolve_yahoo_symbol(term: str, fetch=None, *, context_query: str = "") -> dict | None:
    query = " ".join((term or "").strip().split())
    if not query or len(query) > 80:
        return None
    clean_key = query.lstrip("$").lower()
    explicit_ticker = bool(_EXPLICIT_TICKER.fullmatch(query))
    q_tokens = _overlap_tokens(query.lstrip("$"))
    want_type, want_market = _extract_query_cues(context_query)
    cache_key = f"{query}|{want_market[0] if want_market else ''}|{want_type or ''}"
    use_cache = fetch is None or fetch is fetch_json
    now = time.monotonic()
    if use_cache:
        cached = _SYMBOL_CACHE.get(cache_key)
        if cached and now - cached[0] < _SYMBOL_CACHE_TTL:
            return dict(cached[1]) if cached[1] is not None else None
    fn = fetch or fetch_json
    url = "https://query1.finance.yahoo.com/v1/finance/search?q=" + quote(query, safe="") + "&quotesCount=6&newsCount=0"
    found = None
    try:
        raw = fn(url)
        quotes = raw.get("quotes") if isinstance(raw, dict) else None
        rows = []
        for idx, item in enumerate(quotes or []):
            if not isinstance(item, dict):
                continue
            qtype = str(item.get("quoteType") or "").upper()
            if qtype not in SEARCH_QUOTE_TYPES:
                continue
            raw_sym = str(item.get("symbol") or "").strip().upper()
            name = str(item.get("shortname") or item.get("longname") or "").strip()
            if not raw_sym or not name:
                continue
            sym = raw_sym[:-4] if qtype == "CRYPTOCURRENCY" and raw_sym.endswith("-USD") and raw_sym[:-4] in CRYPTO else raw_sym
            try:
                valid_symbol(sym)
            except ValueError:
                continue
            raw_score = item.get("score")
            score = float(raw_score) if finite(raw_score) and raw_score >= 0 else None
            exact_sym = sym.lower() == clean_key or raw_sym.lower() == clean_key
            cand_tokens = _overlap_tokens(f"{sym} {raw_sym} {name}")
            overlap = bool(q_tokens & cand_tokens) or any(
                len(qt) >= 3 and ct.startswith(qt) for qt in q_tokens for ct in cand_tokens
            )
            exchange = str(item.get("exchange") or "")
            rows.append({
                "idx": idx,
                "score": score,
                "exact_sym": exact_sym,
                "overlap": overlap,
                "market_match": _matches_market(sym, exchange, name, want_market),
                "type_match": bool(want_type and qtype == want_type),
                "entry": {
                    "symbol": sym,
                    "name": name,
                    "quote_type": qtype,
                    "exchange": exchange,
                    "source": "Yahoo Finance search",
                    **({"score": round(score, 2)} if score is not None else {}),
                },
            })
        if rows:
            has_scores = any(r["score"] is not None for r in rows)
            top_score = max((r["score"] for r in rows if r["score"] is not None), default=0.0)
            any_market_match = bool(want_market and any(r["market_match"] for r in rows))
            any_type_match = bool(want_type and any(r["type_match"] for r in rows))

            def rank_key(row):
                sc = row["score"]
                m_rank = (0 if row["market_match"] else 1) if any_market_match else 0
                t_rank = (0 if row["type_match"] else 1) if any_type_match else 0
                promote_exact = (
                    row["exact_sym"]
                    and m_rank == 0
                    and t_rank == 0
                    and (
                        explicit_ticker
                        or (not has_scores)
                        or (sc is not None and top_score > 0 and sc >= 0.85 * top_score)
                    )
                )
                return (m_rank, t_rank, 0 if promote_exact else 1, -(sc if sc is not None else -1.0), row["idx"])

            rows.sort(key=rank_key)
            best = rows[0]
            low_score_junk = (
                best["score"] is not None
                and best["score"] < 25000.0
                and not best["exact_sym"]
                and not best["overlap"]
                and not best["market_match"]
            )
            if not low_score_junk:
                found = dict(best["entry"])
                found["alternatives"] = [
                    {k: r["entry"][k] for k in ("symbol", "name", "quote_type", "exchange", "score") if k in r["entry"]}
                    for r in rows[:3]
                ]
                if (
                    len(rows) >= 2
                    and not best["exact_sym"]
                    and not best["market_match"]
                    and best["score"] is not None
                    and rows[1]["score"] is not None
                    and rows[1]["score"] >= 0.70 * best["score"]
                    and rows[1]["type_match"] == best["type_match"]
                    and (best["type_match"] or rows[1]["entry"]["quote_type"] == best["entry"]["quote_type"])
                    and not (best["overlap"] and not rows[1]["overlap"])
                ):
                    found["ambiguous"] = True
    except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError, AttributeError):
        found = None
    if use_cache:
        _SYMBOL_CACHE[cache_key] = (now, dict(found) if found is not None else None)
    return found


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def fetch_json(url, limit=4*1024*1024):
    p=urlsplit(url)
    if p.scheme!="https" or p.hostname not in {"query1.finance.yahoo.com","query2.finance.yahoo.com","api.alternative.me"} or p.username or p.port:
        raise ValueError("Unapproved market-data endpoint.")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): raise ValueError("Market-data redirects are not allowed.")
    op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 Cofinance/2","Accept":"application/json"})
    with op.open(req,timeout=12) as r:
        body=r.read(limit+1)
        if len(body)>limit:raise ValueError("Market response too large.")
        return json.loads(body)


class MarketTools:
    def __init__(self, cancel=None, fetch=fetch_json):
        self.fetch,self.cancel=fetch,cancel
        self.charts={}
        self.snapshots={}
        self.evidence=[]

    def check(self):
        if self.cancel and self.cancel.is_set():
            from .local_models import Cancelled
            raise Cancelled("Run cancelled.")

    # (Yahoo timeseries type, snapshot field, money field). Probed live 2026-11-08 (data/_fundamentals_probe.py): keyless,
    # same approved host, no cookie or crumb. Money fields are used only when their currency equals the quote currency.
    FUNDAMENTALS = (("trailingPeRatio", "pe_ratio", False), ("trailingForwardPeRatio", "forward_pe_ratio", False),
                    ("trailingMarketCap", "market_cap", True), ("trailingTotalRevenue", "revenue_ttm", True),
                    ("trailingDilutedEPS", "eps", True))

    def add_fundamentals(self, snap, symbol):
        """Latest value of each field with its own as-of date and period type (shown in the UI, never narrated).
        A missing field stays missing: nothing is estimated, derived or carried over from another field."""
        now = int(time.time())
        url = ("https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/" + quote(symbol, safe="")
               + "?symbol=" + quote(symbol, safe="") + "&type=" + ",".join(f for f, _, _ in self.FUNDAMENTALS)
               + f"&period1={now - 2 * 365 * 86400}&period2={now}")
        as_of = {}
        try:
            raw = self.fetch(url); self.check()
            by_type = {}
            for r in ((raw.get("timeseries") or {}).get("result") or []):
                kind = ((r.get("meta") or {}).get("type") or [None])[0] if isinstance(r, dict) else None
                if isinstance(kind, str):
                    by_type[kind] = r.get(kind) or []
            for field, name, money in self.FUNDAMENTALS:
                points = [p for p in by_type.get(field, []) if isinstance(p, dict) and isinstance(p.get("reportedValue"), dict)
                          and finite(p["reportedValue"].get("raw")) and isinstance(p.get("asOfDate"), str)]
                if not points:
                    continue
                last = max(points, key=lambda p: p["asOfDate"])
                if money and str(last.get("currencyCode") or "") != snap.get("currency"):
                    continue  # a value in another currency is never shown next to this quote
                value = last["reportedValue"]["raw"]
                snap[name] = int(value) if money and float(value).is_integer() else round(value, 4)
                as_of[name] = {"as_of": last["asOfDate"][:10], "period": str(last.get("periodType") or "")}
        except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError, AttributeError):
            as_of = {k: v for k, v in as_of.items() if k in snap}
        snap["fundamentals_as_of"] = as_of
        snap["fundamentals_status"] = "Available" if as_of else "Unavailable from this source"
        if as_of:
            snap["fundamentals_source"] = "Yahoo Finance fundamentals timeseries"

    def add_all_time_high(self, snap, symbol, price):
        """Highest monthly high in the source's full history (split-adjusted Yahoo bars), and the drawdown from it.
        Labelled with where the history starts, because a young listing's "all-time" is short."""
        url = "https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?range=max&interval=1mo"
        try:
            raw = self.fetch(url); self.check()
            result = (raw.get("chart", {}).get("result") or [None])[0]
            q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            bars = [(t, h) for t, h in zip(result.get("timestamp") or [], q.get("high") or []) if finite(t) and finite(h) and h > 0]
            if len(bars) < 12:
                raise ValueError("Too little history for an all-time high.")
            when, high = max(bars, key=lambda b: b[1])
            high = max(high, price)  # the live price can run ahead of the last monthly bar
            month = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m")
            snap["all_time_high"] = round(high, 6 if high < 1 else 4)
            snap["drawdown_from_all_time_high_pct"] = round((1 - price / high) * 100, 4)
            snap["all_time_high_month"] = month(when) if high != price else "current"
            snap["history_start"] = month(bars[0][0])
            snap["ath_drawdown"] = "Highest monthly high in source history since " + snap["history_start"] + " (split-adjusted)"
        except (OSError, ValueError, KeyError, IndexError, TypeError, OverflowError, AttributeError):
            snap["ath_drawdown"] = "Unavailable: no full-history highs from the source; a 52-week high is not an all-time high"

    def _fetch_sentiment(self):
        try:
            sentiment=self.fetch("https://api.alternative.me/fng/?limit=1"); self.check()
            item=sentiment["data"][0];value=int(item["value"])
            if not 0<=value<=100:raise ValueError("Invalid sentiment")
            as_of=datetime.fromtimestamp(int(item["timestamp"]),timezone.utc).isoformat()
            return ({"fear_greed_index":value,"fear_greed_label":str(item["value_classification"]),"fear_greed_as_of":as_of},
                    {"title":"Crypto Fear & Greed Index (market-wide, not asset-specific)","url":"https://alternative.me/crypto/fear-and-greed-index/","source":"Alternative.me","as_of":as_of})
        except (OSError,ValueError,KeyError,IndexError,TypeError):
            return {"sentiment_status":"Unavailable, no neutral score substituted"},None

    def get_snapshot(self,ticker):
        valid_symbol(ticker);self.check()
        symbol=ticker+"-USD" if ticker in CRYPTO else ticker
        url="https://query1.finance.yahoo.com/v8/finance/chart/"+quote(symbol,safe="")+"?range=1y&interval=1d"
        now=datetime.now(timezone.utc).isoformat()
        snap={"ticker":ticker,"source":"Yahoo Finance chart API","fetched_at":now,"status":"unavailable"}
        try:
            raw=self.fetch(url); self.check()
            result=(raw.get("chart",{}).get("result") or [None])[0]
            if not isinstance(result,dict):raise ValueError("No market series returned.")
            meta=result.get("meta",{})
            price=meta.get("regularMarketPrice")
            if not finite(price) or price<=0:raise ValueError("No valid source price.")
            snap.update(price=price,currency=str(meta.get("currency") or "Unknown"),name=str(meta.get("longName") or meta.get("shortName") or ticker),status="available")
            ts=meta.get("regularMarketTime")
            snap["price_as_of"]=datetime.fromtimestamp(ts,timezone.utc).isoformat() if finite(ts) else "Not supplied by source"
            # chartPreviousClose is the close BEFORE the requested window (about a year ago for range=1y),
            # not yesterday's close. It is kept under an explicit name and never narrated as a previous close.
            for field,name in (("previousClose","previous_close"),("chartPreviousClose","close_before_window"),("fiftyTwoWeekHigh","high_52_week"),("fiftyTwoWeekLow","low_52_week"),("regularMarketVolume","volume")):
                if finite(meta.get(field)):snap[name]=meta[field]
            timestamps=result.get("timestamp") or []
            closes=((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
            points=[{"time":datetime.fromtimestamp(t,timezone.utc).isoformat(),"close":round(c,6)} for t,c in zip(timestamps,closes) if finite(t) and finite(c) and c>0][-400:]
            # Previous session close comes from the same daily series: the bar before the price's session.
            if points and finite(ts):
                session=datetime.fromtimestamp(ts,timezone.utc).date().isoformat()
                earlier=[p for p in points if p["time"][:10]<session]
                if earlier:
                    close=earlier[-1]["close"]
                    snap["previous_session_close"]=round(close,6 if close<1 else 4)
                    snap["previous_session_date"]=earlier[-1]["time"][:10]
                    # Direction is computed here, never inferred by the narrator (live 2026-11-08: "186.64 (down from 181.6)").
                    snap["change_from_previous_session_pct"]=round((price/close-1)*100,4)
            self.charts[ticker]={"ticker":ticker,"currency":snap["currency"],"source":"Yahoo Finance","fetched_at":now,"points":points,"window":"1 year","adjustment":"Source Close, not total return"}
            if ticker in CRYPTO:
                high=snap.get("high_52_week")
                if finite(high) and high>0:snap["drawdown_from_52_week_high_pct"]=round((1-price/high)*100,4)
                returns=[math.log(b["close"]/a["close"]) for a,b in zip(points,points[1:])]
                if len(returns)>=20:snap["annualized_daily_log_volatility_pct"]=round(statistics.stdev(returns)*math.sqrt(365)*100,4)
                ath_out={}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_ath=pool.submit(self.add_all_time_high,ath_out,symbol,price)
                    f_sent=pool.submit(self._fetch_sentiment)
                    f_ath.result()
                    sent_fields,sent_ev=f_sent.result()
                snap.update(ath_out)
                snap.update(sent_fields)
                if sent_ev:
                    self.evidence.append(sent_ev)
            else:
                # The chart response never carries fundamentals (the old reads here were always empty). Missing fields
                # remain visibly absent; each present one keeps its own as-of date.
                fund_out={"currency":snap.get("currency")}
                ath_out={}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_fund=pool.submit(self.add_fundamentals,fund_out,symbol)
                    f_ath=pool.submit(self.add_all_time_high,ath_out,symbol,price)
                    f_fund.result()
                    f_ath.result()
                fund_out.pop("currency",None)
                snap.update(fund_out)
                snap.update(ath_out)
            self.evidence.append({"title":ticker+" market data","url":"https://finance.yahoo.com/quote/"+quote(symbol,safe=""),"source":"Yahoo Finance","as_of":snap["price_as_of"]})
        except (OSError,ValueError,KeyError,IndexError,TypeError,OverflowError):
            snap["error"]="Source request failed or returned invalid market data. No values were substituted."
        self.snapshots[ticker]=snap
        return snap

    def resolve_symbol(self, term, context_query=""):
        self.check()
        out = resolve_yahoo_symbol(term, fetch=self.fetch, context_query=context_query)
        self.check()
        return out

    def plot_history(self,ticker):
        self.check()
        return "chart://"+ticker if self.charts.get(ticker,{}).get("points") else ""

    def add_to_watchlist(self,ticker):
        raise RuntimeError("Research tools cannot write the watchlist. Use the explicit watchlist action.")
