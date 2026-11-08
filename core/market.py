"""Headless, bounded market-data tools. Public Yahoo chart data, optional fundamentals,
Alternative.me sentiment. Never generates demo values or calls Streamlit.
"""
from __future__ import annotations
import json
import math
import re
import statistics
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

CRYPTO = {"BTC","ETH","SOL","ADA","DOGE","XRP","BNB","AVAX","DOT","LINK"}
SYMBOL = re.compile(r"[A-Z][A-Z0-9.\-]{0,11}")


def valid_symbol(symbol):
    if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid ticker symbol.")
    return symbol


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
                self.add_all_time_high(snap,symbol,price)
                try:
                    sentiment=self.fetch("https://api.alternative.me/fng/?limit=1")
                    item=sentiment["data"][0];value=int(item["value"])
                    if not 0<=value<=100:raise ValueError("Invalid sentiment")
                    snap.update(fear_greed_index=value,fear_greed_label=str(item["value_classification"]),fear_greed_as_of=datetime.fromtimestamp(int(item["timestamp"]),timezone.utc).isoformat())
                    self.evidence.append({"title":"Crypto Fear & Greed Index (market-wide, not asset-specific)","url":"https://alternative.me/crypto/fear-and-greed-index/","source":"Alternative.me","as_of":snap["fear_greed_as_of"]})
                except (OSError,ValueError,KeyError,IndexError,TypeError):snap["sentiment_status"]="Unavailable, no neutral score substituted"
            else:
                # The chart response never carries fundamentals (the old reads here were always empty). Missing fields
                # remain visibly absent; each present one keeps its own as-of date.
                self.add_fundamentals(snap,symbol)
                self.add_all_time_high(snap,symbol,price)
            self.evidence.append({"title":ticker+" market data","url":"https://finance.yahoo.com/quote/"+quote(symbol,safe=""),"source":"Yahoo Finance","as_of":snap["price_as_of"]})
        except (OSError,ValueError,KeyError,IndexError,TypeError,OverflowError):
            snap["error"]="Source request failed or returned invalid market data. No values were substituted."
        self.snapshots[ticker]=snap
        return snap

    def plot_history(self,ticker):
        self.check()
        return "chart://"+ticker if self.charts.get(ticker,{}).get("points") else ""

    def add_to_watchlist(self,ticker):
        raise RuntimeError("Research tools cannot write the watchlist. Use the explicit watchlist action.")
