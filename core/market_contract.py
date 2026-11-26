"""Full market contract definition parser and barrier validator (stdlib only).

Parses explicit settlement source, metric, comparator, DST-aware settlement time,
observation window, and deterministic barrier-already-touched status from full
market rules. Ambiguous or unsupported contracts abstain with explicit reasons.
"""
from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class AssessmentStatus(str, enum.Enum):
    SUPPORTED_READY = "supported_ready"
    SUPPORTED_INCOMPLETE_DATA = "supported_incomplete_data"
    UNSUPPORTED_ABSTAIN = "unsupported_abstain"
    MALFORMED_ABSTAIN = "malformed_abstain"


class ContractArchetype(str, enum.Enum):
    EXPIRY_THRESHOLD = "expiry_threshold"
    BARRIER_TOUCH = "barrier_touch"
    EXPIRY_RANGE = "expiry_range"


@dataclass(frozen=True)
class OHLCBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    provider: str
    symbol: str
    interval_s: int


@dataclass(frozen=True)
class MarketDefinition:
    market_id: str
    asset: str
    archetype: ContractArchetype
    source_provider: str
    source_symbol: str
    metric: str
    interval_s: Optional[int]
    comparator: str
    strike: Optional[Decimal]
    lower: Optional[Decimal]
    upper: Optional[Decimal]
    lower_inclusive: Optional[bool]
    upper_inclusive: Optional[bool]
    bucket_tie_rule: Optional[str]
    direction: Optional[str]
    settlement_at: datetime
    observation_start: Optional[datetime]
    observation_end: Optional[datetime]
    timezone_name: str
    rule_text_hash: str


@dataclass(frozen=True)
class ContractAssessment:
    status: AssessmentStatus
    definition: Optional[MarketDefinition]
    time_left_s: Optional[float]
    barrier_touched: Optional[bool]
    reasons: Tuple[str, ...] = ()


ASSET_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("BTC", re.compile(r"\b(?:bitcoin|btc)\b", re.I)),
    ("ETH", re.compile(r"\b(?:ethereum|eth)\b", re.I)),
    ("SOL", re.compile(r"\b(?:solana|sol)\b", re.I)),
    ("AAPL", re.compile(r"\b(?:apple|aapl)\b", re.I)),
    ("NVDA", re.compile(r"\b(?:nvidia|nvda)\b", re.I)),
    ("MSFT", re.compile(r"\b(?:microsoft|msft)\b", re.I)),
    ("TSLA", re.compile(r"\b(?:tesla|tsla)\b", re.I)),
    ("AMZN", re.compile(r"\b(?:amazon|amzn)\b", re.I)),
    ("GOOGL", re.compile(r"\b(?:alphabet|google|googl)\b", re.I)),
    ("META", re.compile(r"\b(?:meta platforms|meta)\b", re.I)),
)

UNSUPPORTED_METRIC_RE = re.compile(
    r"\b(?:market\s+cap|largest\s+company|most\s+valuable|tvl|total\s+value\s+locked|"
    r"trading\s+volume|revenue|earnings|eps|market\s+share|dominance|ranked\s+#?\d)\b",
    re.I,
)
RELATIVE_CLAIM_RE = re.compile(
    r"\b(?:outperform|outperforms|higher\s+return\s+than|beat\s+[A-Z]{2,5}|flip\s+(?:bitcoin|ethereum|btc|eth))\b",
    re.I,
)
MULTI_CONDITION_RE = re.compile(
    r"\b(?:and\s+(?:close|dip|reach|hit|finish|settle)\s+(?:above|below|to|at)|both\s+.{1,40}\s+and\s+)\b",
    re.I,
)

MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}
MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))

DATE_TIME_TZ_RE = re.compile(
    rf"(?P<month>{MONTH_ALT})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>20\d{{2}})"
    rf"(?:,?\s*(?:at\s+)?(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})\s*(?P<ampm>AM|PM)?\s*(?P<tz>ET|EST|EDT|UTC|GMT)?)",
    re.I,
)
TIME_ON_DATE_RE = re.compile(
    rf"(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})\s*(?P<ampm>AM|PM)?\s*(?P<tz>ET|EST|EDT|UTC|GMT)?"
    rf"\s+on\s+(?P<month>{MONTH_ALT})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>20\d{{2}})",
    re.I,
)
ISO_DT_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2}))\b")
DATE_ONLY_RE = re.compile(rf"\b(?P<month>{MONTH_ALT})\s+(?P<day>\d{{1,2}}),?\s+(?P<year>20\d{{2}})\b", re.I)

PRICE_TOKEN = r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM])?"
RANGE_RE = re.compile(rf"\bbetween\s+{PRICE_TOKEN}\s+(?:and|to|-)\s+{PRICE_TOKEN}", re.I)
TOUCH_UP_RE = re.compile(rf"\b(?:reach|hit|touch|climb\s+to|rise\s+to)\s+{PRICE_TOKEN}", re.I)
TOUCH_DOWN_RE = re.compile(rf"\b(?:dip\s+to|drop\s+to|fall\s+to|decline\s+to|crash\s+to)\s+{PRICE_TOKEN}", re.I)
SINGLE_PRICE_RE = re.compile(PRICE_TOKEN)


def _rule_hash(question: str, description: str, resolution_source: str) -> str:
    payload = "\n".join([question.strip(), description.strip(), resolution_source.strip()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_decimal(num_text: str, suffix: Optional[str]) -> Optional[Decimal]:
    try:
        val = Decimal(num_text.replace(",", ""))
    except InvalidOperation:
        return None
    if suffix:
        s = suffix.lower()
        if s == "k":
            val *= Decimal("1000")
        elif s == "m":
            val *= Decimal("1000000")
    return val


def _parse_iso_utc(raw: Any) -> Optional[datetime]:
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _to_utc(year: int, month: int, day: int, hour: int, minute: int, ampm: Optional[str], tz_token: Optional[str]) -> Tuple[Optional[datetime], Optional[str], Optional[str]]:
    if not tz_token:
        return None, None, "timezone_unspecified"
    h = hour
    if ampm:
        ap = ampm.upper()
        if not 1 <= h <= 12:
            return None, None, "malformed_time"
        if ap == "AM":
            h = 0 if h == 12 else h
        else:
            h = 12 if h == 12 else h + 12
    elif not 0 <= h <= 23:
        return None, None, "malformed_time"
    if not 0 <= minute <= 59:
        return None, None, "malformed_time"
    tz_upper = tz_token.upper()
    if tz_upper in ("ET", "EST", "EDT"):
        tz = ET
        tz_name = "America/New_York"
    elif tz_upper in ("UTC", "GMT"):
        tz = timezone.utc
        tz_name = "UTC"
    else:
        return None, None, "timezone_unspecified"
    try:
        local_dt = datetime(year, month, day, h, minute, tzinfo=tz)
    except ValueError:
        return None, None, "malformed_time"
    return local_dt.astimezone(timezone.utc), tz_name, None


def _extract_datetimes(text: str) -> Tuple[list[Tuple[datetime, str]], list[str]]:
    found: list[Tuple[datetime, str]] = []
    errors: list[str] = []
    seen_spans: list[Tuple[int, int]] = []

    for pat in (TIME_ON_DATE_RE, DATE_TIME_TZ_RE):
        for m in pat.finditer(text):
            span = m.span()
            if any(not (span[1] <= a or span[0] >= b) for a, b in seen_spans):
                continue
            seen_spans.append(span)
            dt, tz_name, err = _to_utc(
                int(m.group("year")),
                MONTHS[m.group("month").lower()],
                int(m.group("day")),
                int(m.group("hour")),
                int(m.group("minute")),
                m.group("ampm"),
                m.group("tz"),
            )
            if err:
                errors.append(err)
            elif dt and tz_name:
                found.append((dt, tz_name))

    for m in ISO_DT_RE.finditer(text):
        dt = _parse_iso_utc(m.group(1))
        if dt is not None:
            found.append((dt, "UTC"))

    return found, errors


def _extract_source(rules_text: str, asset: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    lower = rules_text.lower()
    interval_s: Optional[int] = None
    if re.search(r"\b1[-\s]?m(?:in(?:ute)?)?\s+candle\b|\b1m\b", rules_text, re.I):
        interval_s = 60
    elif re.search(r"\b5[-\s]?m(?:in(?:ute)?)?\s+(?:candle|twap)\b|\b5m\b", rules_text, re.I):
        interval_s = 300
    elif re.search(r"\b60[-\s]?s(?:ec(?:ond)?)?\s+twap\b", rules_text, re.I):
        interval_s = 60

    if "binance" in lower:
        pair = re.search(r"\b([A-Z]{2,6})\s*/\s*(USDT|USD|USDC)\b|\b([A-Z]{2,6})(USDT|USDC)\b", rules_text)
        symbol = f"{pair.group(1) or pair.group(3)}/{pair.group(2) or pair.group(4)}" if pair else f"{asset}/USDT"
        return "binance", symbol.upper(), interval_s
    if "coinbase" in lower:
        pair = re.search(r"\b([A-Z]{2,6})[-/](USD|USDT|USDC)\b", rules_text)
        symbol = f"{pair.group(1)}/{pair.group(2)}" if pair else f"{asset}/USD"
        return "coinbase", symbol.upper(), interval_s
    if "chainlink" in lower:
        pair = re.search(r"\b([A-Z]{2,6})[-/](USD|USDT)\b", rules_text)
        symbol = f"{pair.group(1)}/{pair.group(2)}" if pair else f"{asset}/USD"
        return "chainlink", symbol.upper(), interval_s
    if "yahoo finance" in lower or "finance.yahoo.com" in lower:
        return "yahoo_finance", asset, interval_s or 86400
    if "nasdaq" in lower:
        return "nasdaq", asset, interval_s or 86400
    if "pyth" in lower:
        return "pyth", asset, interval_s or 60
    return None, None, interval_s


def _extract_comparator(description: str) -> Optional[str]:
    low = description.lower()
    if any(p in low for p in ("equal to or greater than", "greater than or equal to", "at or above", "or higher", ">=")):
        return ">="
    if any(p in low for p in ("strictly higher than", "strictly above", "strictly greater than", "higher than", "greater than")):
        return ">"
    if any(p in low for p in ("equal to or lower than", "less than or equal to", "at or below", "or lower", "<=")):
        return "<="
    if any(p in low for p in ("strictly lower than", "strictly below", "strictly less than", "lower than", "less than")):
        return "<"
    return None


def _extract_range_boundary_rule(description: str) -> Tuple[Optional[bool], Optional[bool], Optional[str]]:
    low = description.lower()
    if "higher bracket" in low or "upper bracket" in low:
        return True, False, "exact_boundary_to_higher_bracket"
    if "lower bracket" in low:
        return False, True, "exact_boundary_to_lower_bracket"
    if "both bounds inclusive" in low or "inclusive of both" in low:
        return True, True, "inclusive_both"
    if "strictly between" in low or "exclusive of both" in low:
        return False, False, "exclusive_both"
    return None, None, None


def evaluate_barrier_touch(
    definition: MarketDefinition,
    bars: Sequence[OHLCBar],
    *,
    now: Optional[datetime] = None,
) -> Tuple[Optional[bool], Tuple[str, ...]]:
    """Deterministically evaluate whether a touch barrier was already hit.

    Returns (True, ()) if any valid in-window bar hits the barrier.
    Returns (False, ()) ONLY when gap-free coverage from observation_start through
    min(now, observation_end) contains zero hits.
    Otherwise returns (None, reasons).
    """
    if definition.archetype is not ContractArchetype.BARRIER_TOUCH or definition.strike is None:
        return None, ("not_a_barrier_contract",)
    if definition.observation_start is None or definition.observation_end is None:
        return None, ("missing_observation_window",)
    if definition.interval_s is None or definition.interval_s <= 0:
        return None, ("wrong_granularity",)

    now_utc = _parse_iso_utc(now) if now is not None else definition.observation_end
    if now_utc is None:
        return None, ("timezone_unspecified",)
    target_end = min(now_utc, definition.observation_end)

    in_window: list[OHLCBar] = []
    prev_ts: Optional[datetime] = None
    for bar in bars:
        if bar.provider.lower() != definition.source_provider.lower():
            return None, ("wrong_provider",)
        if bar.symbol.upper() != definition.source_symbol.upper():
            return None, ("wrong_symbol",)
        if bar.interval_s != definition.interval_s:
            return None, ("wrong_granularity",)
        if bar.timestamp.tzinfo is None:
            return None, ("timezone_unspecified",)
        ts = bar.timestamp.astimezone(timezone.utc)
        if prev_ts is not None and ts <= prev_ts:
            return None, ("non_monotonic_bars",)
        prev_ts = ts
        if any(not v.is_finite() for v in (bar.open, bar.high, bar.low, bar.close)):
            return None, ("invalid_ohlc",)
        if bar.low > bar.high or not (bar.low <= bar.open <= bar.high) or not (bar.low <= bar.close <= bar.high):
            return None, ("invalid_ohlc",)
        if definition.observation_start <= ts <= target_end:
            in_window.append(bar)

    for bar in in_window:
        if definition.direction == "up" and bar.high >= definition.strike:
            return True, ()
        if definition.direction == "down" and bar.low <= definition.strike:
            return True, ()

    if target_end < definition.observation_start:
        return False, ()
    if not in_window:
        return None, ("coverage_incomplete",)

    step = timedelta(seconds=definition.interval_s)
    first_ts = in_window[0].timestamp.astimezone(timezone.utc)
    if first_ts > definition.observation_start:
        return None, ("coverage_incomplete",)

    for prev_bar, cur_bar in zip(in_window, in_window[1:]):
        delta = cur_bar.timestamp.astimezone(timezone.utc) - prev_bar.timestamp.astimezone(timezone.utc)
        if delta != step:
            return None, ("ohlc_gap",)

    last_ts = in_window[-1].timestamp.astimezone(timezone.utc)
    if last_ts + step < target_end:
        return None, ("coverage_incomplete",)

    return False, ()


def parse_market_definition(
    market: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    bars: Optional[Sequence[OHLCBar]] = None,
    clarification_loaded: bool = True,
) -> ContractAssessment:
    if not isinstance(market, Mapping):
        return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("malformed_market",))

    question = str(market.get("question") or "").strip()
    description = str(market.get("description") or "").strip()
    res_source = str(market.get("resolution_source") or market.get("resolutionSource") or "").strip()
    if not question:
        return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("missing_question",))

    if (market.get("has_clarifications") or market.get("clarifications")) and not clarification_loaded:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unresolved_clarification",))

    full_text = f"{question}\n{description}\n{res_source}"
    rules_only = f"{description}\n{res_source}".strip()
    if not rules_only:
        return ContractAssessment(
            AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("missing_rules_text", "missing_resolution_source")
        )

    if UNSUPPORTED_METRIC_RE.search(full_text):
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_metric",))
    if RELATIVE_CLAIM_RE.search(full_text):
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_relative_claim",))
    if MULTI_CONDITION_RE.search(full_text):
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_multi_condition",))

    matched_assets = [code for code, pat in ASSET_PATTERNS if pat.search(question) or pat.search(description)]
    if not matched_assets:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_asset",))
    if len(matched_assets) > 1:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_relative_claim",))
    asset = matched_assets[0]

    provider, symbol, interval_s = _extract_source(rules_only, asset)
    if not provider or not symbol:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("missing_resolution_source",))

    dts, dt_errors = _extract_datetimes(description)
    if dt_errors and not dts:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, tuple(dict.fromkeys(dt_errors)))

    if not dts and provider in ("yahoo_finance", "nasdaq", "pyth"):
        if re.search(r"\b(?:official\s+closing\s+price|official\s+close|regular\s+(?:trading\s+)?session\s+close|market\s+close)\b", description, re.I):
            dm = DATE_ONLY_RE.search(description)
            if dm:
                dt, tz_name, err = _to_utc(
                    int(dm.group("year")),
                    MONTHS[dm.group("month").lower()],
                    int(dm.group("day")),
                    16,
                    0,
                    None,
                    "ET",
                )
                if dt and tz_name and not err:
                    dts.append((dt, tz_name))

    if not dts:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("missing_settlement_time",))

    rhash = _rule_hash(question, description, res_source)
    now_utc = _parse_iso_utc(now) if now is not None else None
    if now is not None and now_utc is None:
        return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("timezone_unspecified",))

    range_match = RANGE_RE.search(question) or RANGE_RE.search(description)
    touch_up = TOUCH_UP_RE.search(question) or TOUCH_UP_RE.search(description)
    touch_down = TOUCH_DOWN_RE.search(question) or TOUCH_DOWN_RE.search(description)

    if range_match:
        lo = _parse_decimal(range_match.group(1), range_match.group(2))
        hi = _parse_decimal(range_match.group(3), range_match.group(4))
        if lo is None or hi is None or lo >= hi:
            return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("malformed_range",))
        lo_inc, hi_inc, tie_rule = _extract_range_boundary_rule(description)
        if tie_rule is None:
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("missing_range_boundary_rule",))
        settlement_at, tz_name = dts[-1]
        metric = "official_close" if provider in ("yahoo_finance", "nasdaq") else ("twap" if provider == "chainlink" else "close")
        defn = MarketDefinition(
            market_id=str(market.get("id") or ""),
            asset=asset,
            archetype=ContractArchetype.EXPIRY_RANGE,
            source_provider=provider,
            source_symbol=symbol,
            metric=metric,
            interval_s=interval_s,
            comparator="range",
            strike=None,
            lower=lo,
            upper=hi,
            lower_inclusive=lo_inc,
            upper_inclusive=hi_inc,
            bucket_tie_rule=tie_rule,
            direction=None,
            settlement_at=settlement_at,
            observation_start=None,
            observation_end=settlement_at,
            timezone_name=tz_name,
            rule_text_hash=rhash,
        )
        time_left = (settlement_at - now_utc).total_seconds() if now_utc else None
        return ContractAssessment(AssessmentStatus.SUPPORTED_READY, defn, time_left, None, ())

    if touch_up or touch_down:
        if touch_up and touch_down:
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("unsupported_multi_condition",))
        m = touch_up or touch_down
        direction = "up" if touch_up else "down"
        barrier = _parse_decimal(m.group(1), m.group(2))
        if barrier is None:
            return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("malformed_barrier",))
        low_desc = description.lower()
        if direction == "up" and not re.search(r"\bhigh\b", low_desc):
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("ambiguous_contract_type",))
        if direction == "down" and not re.search(r"\blow\b", low_desc):
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("ambiguous_contract_type",))
        if interval_s is None:
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("wrong_granularity",))

        if len(dts) >= 2:
            obs_start, _ = dts[0]
            obs_end, tz_name = dts[-1]
        else:
            obs_end, tz_name = dts[0]
            if re.search(r"\bmarket\s+(?:creation|open|launch)\b", low_desc):
                obs_start = _parse_iso_utc(market.get("start") or market.get("startDate"))
            else:
                obs_start = None
        if obs_start is None or obs_start > obs_end:
            return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("missing_observation_window",))

        defn = MarketDefinition(
            market_id=str(market.get("id") or ""),
            asset=asset,
            archetype=ContractArchetype.BARRIER_TOUCH,
            source_provider=provider,
            source_symbol=symbol,
            metric="high" if direction == "up" else "low",
            interval_s=interval_s,
            comparator=">=" if direction == "up" else "<=",
            strike=barrier,
            lower=None,
            upper=None,
            lower_inclusive=None,
            upper_inclusive=None,
            bucket_tie_rule=None,
            direction=direction,
            settlement_at=obs_end,
            observation_start=obs_start,
            observation_end=obs_end,
            timezone_name=tz_name,
            rule_text_hash=rhash,
        )
        time_left = (obs_end - now_utc).total_seconds() if now_utc else None
        if bars is None:
            return ContractAssessment(AssessmentStatus.SUPPORTED_INCOMPLETE_DATA, defn, time_left, None, ("missing_ohlc_bars",))
        touched, reasons = evaluate_barrier_touch(defn, bars, now=now_utc)
        status = AssessmentStatus.SUPPORTED_READY if touched is not None else AssessmentStatus.SUPPORTED_INCOMPLETE_DATA
        return ContractAssessment(status, defn, time_left, touched, reasons)

    comparator = _extract_comparator(description)
    if comparator is None:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("ambiguous_comparator",))
    price_m = SINGLE_PRICE_RE.search(question) or SINGLE_PRICE_RE.search(description)
    if not price_m:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("ambiguous_contract_type",))
    strike = _parse_decimal(price_m.group(1), price_m.group(2))
    if strike is None:
        return ContractAssessment(AssessmentStatus.MALFORMED_ABSTAIN, None, None, None, ("malformed_strike",))

    settlement_at, tz_name = dts[-1]
    if provider in ("yahoo_finance", "nasdaq"):
        metric = "official_close"
    elif provider == "chainlink":
        metric = "twap"
    elif re.search(r"\bclose\b", description, re.I):
        metric = "close"
    else:
        return ContractAssessment(AssessmentStatus.UNSUPPORTED_ABSTAIN, None, None, None, ("ambiguous_contract_type",))

    defn = MarketDefinition(
        market_id=str(market.get("id") or ""),
        asset=asset,
        archetype=ContractArchetype.EXPIRY_THRESHOLD,
        source_provider=provider,
        source_symbol=symbol,
        metric=metric,
        interval_s=interval_s,
        comparator=comparator,
        strike=strike,
        lower=None,
        upper=None,
        lower_inclusive=None,
        upper_inclusive=None,
        bucket_tie_rule=None,
        direction=None,
        settlement_at=settlement_at,
        observation_start=None,
        observation_end=settlement_at,
        timezone_name=tz_name,
        rule_text_hash=rhash,
    )
    time_left = (settlement_at - now_utc).total_seconds() if now_utc else None
    return ContractAssessment(AssessmentStatus.SUPPORTED_READY, defn, time_left, None, ())
