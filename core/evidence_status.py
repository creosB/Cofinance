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
_NOT_REALTIME = re.compile(r'\b(?:last\s+(?:known|traded)|as\s+of|delay(?:ed)?|not\s+(?:current|live|real[- ]time))\b', re.I)


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


_PREVIOUS_CLOSE = re.compile(r"^\s*(?:what\s+(?:was|is)\s+(?:the|its|their)\s+)?(?:previous|prior|last\s+session'?s?)\s+close[?.!\s]*$", re.I)


def previous_close_request(question: str) -> bool:
    return bool(_PREVIOUS_CLOSE.fullmatch(question))


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


def claims_current_price(clause: str) -> bool:
    return bool(_CURRENT_PRICE.search(clause) and not _NOT_REALTIME.search(clause))


def decision_assessment(tickers, snapshots, *, news_items: int, news_complete: bool, news_error: bool = False, news_collected: bool = True) -> dict:
    states = {ticker: (snapshots.get(ticker, {}).get('quote_status') or {}).get('state', 'timestamp_unknown')
              for ticker in tickers}
    limitations = [f'{ticker}: {QUOTE_LABEL.get(state, "Quote freshness not confirmed").lower()}'
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
