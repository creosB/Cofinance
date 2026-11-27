from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping

RECENT = timedelta(minutes=10)
CLOSING_TOLERANCE = timedelta(minutes=5)
LAST_KNOWN_LIMIT = timedelta(days=4)
_DECISION = re.compile(r'\b(?:should\s+(?:i|we)\s+(?:buy|sell|hold|invest)|(?:buy|sell|hold)\s+(?:now|today)\b|worth\s+(?:buying|selling|investing))', re.I)
_CURRENT_PRICE = re.compile(r'\b(?:now|currently|at\s+present|live|real[- ]time|right\s+now|current\s+(?:quote|price)|(?:is|are)\s+trading\s+at)\b', re.I)
_NEGATED_REALTIME = re.compile(r'\bnot\s+(?:a\s+)?(?:now|currently|current(?:\s+(?:quote|price))?|live|real[- ]time)\b', re.I)


def provider_time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def quote_status(meta: Mapping, observed: datetime, *, continuous: bool = False) -> dict:
    stamp = provider_time(meta.get('regularMarketTime'))
    period = meta.get('currentTradingPeriod') or {}
    regular = period.get('regular') or {} if isinstance(period, Mapping) else {}
    if not isinstance(regular, Mapping):
        regular = {}
    started, ended = provider_time(regular.get('start')), provider_time(regular.get('end'))
    delay = meta.get('exchangeDataDelayedBy')
    delay = delay if type(delay) in (int, float) and math.isfinite(delay) and 0 <= delay <= 120 else None
    valid_period = started is not None and ended is not None and started < ended
    session = 'continuous' if continuous else 'regular' if valid_period and started <= observed < ended else 'outside_regular' if valid_period else 'unknown'
    if stamp is None:
        state = 'timestamp_unknown'
    elif stamp > observed:
        state = 'timestamp_invalid'
    elif observed - stamp > LAST_KNOWN_LIMIT:
        state = 'stale'
    elif session == 'outside_regular':
        state = 'last_session' if started <= stamp <= ended and ended <= observed and ended - stamp <= CLOSING_TOLERANCE else 'last_known'
    elif session in {'regular', 'continuous'}:
        recent = observed - stamp <= RECENT + timedelta(minutes=delay or 0)
        in_period = session == 'continuous' or stamp >= started
        state = ('delayed' if delay else 'recent') if recent and in_period else 'stale'
    else:
        state = 'last_known'
    return {'state': state, 'session': session, 'as_of': stamp.isoformat() if stamp else None,
            'observed_at': observed.isoformat(), 'provider_delay_minutes': delay}


QUOTE_LABEL = {
    'unavailable': 'Market data unavailable', 'timestamp_unknown': 'Quote time unavailable',
    'timestamp_invalid': 'Source quote time is invalid', 'stale': 'Stale quote, not a current price',
    'last_session': 'Last regular-session quote, not live', 'last_known': 'Last known quote, freshness not confirmed',
    'delayed': 'Delayed quote, not live', 'recent': 'Recent provider quote, not guaranteed live',
}


def decision_request(question: str) -> bool:
    return bool(_DECISION.search(question))


_PREVIOUS_CLOSE = re.compile(
    r"\b(?:(?:previous|prior|last\s+session(?:'s|s)?|yesterday(?:'s|s)?)\s+(?:session(?:'s|s)?\s+)?(?:close|closing\s+price)"
    r"|close(?:d)?\s+(?:at\s+)?(?:yesterday|in\s+the\s+(?:previous|prior|last)\s+session|last\s+session)"
    r"|(?:previous|prior)\s+day(?:'s|s)?\s+(?:close|closing\s+price))\b",
    re.I,
)
_PREVIOUS_CLOSE_EXCLUDED = re.compile(
    r"\b(?:news|headline|headlines|quarter|quarterly|year|annual|month|monthly|week|weekly|compare|versus|vs\.?"
    r"|trading\s+at|current\s+(?:price|quote)|market\s+cap|valuation|enterprise\s+value|earnings|revenue|p/e|pe\s+ratio"
    r"|move|moved|change|changed|percent|%|delta)\b",
    re.I,
)


def previous_close_request(question: str) -> bool:
    text = (question or '').strip()
    if not text or len(text) > 140 or decision_request(text) or _PREVIOUS_CLOSE_EXCLUDED.search(text):
        return False
    return bool(_PREVIOUS_CLOSE.search(text))


def previous_close_answer(tickers, snapshots) -> str:
    lines = []
    for ticker in tickers:
        snap = snapshots.get(ticker, {})
        amount, unit, date = snap.get('previous_session_close'), snap.get('currency'), snap.get('previous_session_date')
        if type(amount) not in (int, float) or not math.isfinite(amount) or not date:
            lines.append(f'{ticker}: the source did not provide a dated previous-session close.')
        else:
            lines.append(f'{ticker} previous-session close: {amount} {unit or "(currency not supplied)"} on {date}.')
    return ' '.join(lines)


_VALUATION_FIELDS = (
    ('enterprise_value', re.compile(r'\benterprise\s+value\b', re.I)),
    ('market_cap', re.compile(r'\bmarket\s+cap(?:italization|italisation)?\b', re.I)),
    ('share_price', re.compile(r'\b(?:share\s+price|(?:price|worth)\s+per\s+share)\b', re.I)),
)


def requested_valuation(query: str, proposed: str) -> str:
    explicit = [label for label, pattern in _VALUATION_FIELDS if pattern.search(query)]
    if len(explicit) > 1:
        return 'other'
    return explicit[0] if explicit else proposed


def valuation_answer(metric, tickers, snapshots, display_value) -> str:
    field = 'price' if metric == 'share_price' else 'market_cap'
    lines = []
    for ticker in tickers:
        snap = snapshots.get(ticker, {})
        if field == 'market_cap':
            label = 'market capitalization'
        else:
            label = 'last quote per share' if snap.get('quote_type') in {'EQUITY', 'ETF'} else 'last quoted price'
        amount, unit = snap.get(field), snap.get('currency')
        if snap.get('error') or type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            lines.append(f'{ticker}: the source did not provide {label}.')
            continue
        time = (snap.get('quote_status') or {}).get('as_of') if field == 'price' else (
            (snap.get('fundamentals_as_of') or {}).get(field) or {}).get('as_of')
        qualifier = f' (source as of {time})' if time else ' (source time not supplied)'
        shown = display_value(field, amount)[0]
        lines.append(f'{ticker} {label}: {shown} {unit or "(currency not supplied)"}{qualifier}.')
    return ' '.join(lines)


def claims_current_price(clause: str) -> bool:
    return bool(_CURRENT_PRICE.search(_NEGATED_REALTIME.sub('', clause)))


def decision_assessment(tickers, snapshots, *, news_items: int, news_complete: bool, news_error: bool = False, news_collected: bool = True) -> dict:
    states = {ticker: (snapshots.get(ticker, {}).get('quote_status') or {}).get('state', 'timestamp_unknown')
              for ticker in tickers}
    failures = {ticker: (snapshots.get(ticker, {}).get('quote_status') or {}).get('failure') for ticker in tickers}
    limitations = [f'{ticker}: '+('market-data provider timed out' if failures[ticker] == 'provider_timeout'
                   else QUOTE_LABEL.get(state, 'Quote freshness not confirmed').lower())
                   for ticker, state in states.items() if state != 'recent']
    if news_error:
        news_state, news = 'unavailable', 'Local news store unavailable'
    elif not news_collected:
        news_state, news = 'not_collected', 'Local news has not been collected'
    elif not news_items:
        news_state, news = 'no_accepted_headlines', 'No accepted headlines in local coverage'
    elif not news_complete:
        news_state, news = 'partial', 'Local news coverage is incomplete'
    else:
        news_state, news = 'available_in_store', ''
    if news:
        limitations.append(news)
    text = 'The available evidence does not establish whether to buy, sell, or hold.'
    if limitations:
        text += ' ' + '; '.join(limitations) + '.'
    else:
        text += ' Recent prices and stored news describe market conditions, not whether an action is justified.'
    return {'status': 'insufficient_evidence', 'text': text, 'quote_states': states,
            'news_items': news_items, 'news_state': news_state}
