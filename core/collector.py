"""Clock 1 collector: last30days as an isolated subprocess, normalised into `Item`s, plus a replay source.

Contract: last30days' versioned agent JSON export (`--emit=json`, schema_version 1.x, documented in its
docs/reference/json-export.md). Only `results[]` and `source_status` are read; unknown fields are ignored.

Isolation rules:
  * no shell, fixed argv, `--research-only` so the topic is never treated as a command, validated tickers;
  * allow-listed child environment: LAST30DAYS_* keys that look cookie / browser / session related are
    dropped, so a browser profile holding financial sessions is never handed to the scraper from here
    (its own config file is out of our reach: check `--preflight` output before the first real run);
  * hard timeout; any failure raises CollectorError and the run records it per ticker;
  * output is data: truncated, never executed, and pre-gated before a model sees it.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .signals import Item

SUPPORTED_MAJOR = "1"
CRYPTO = frozenset({"BTC", "ETH", "SOL", "ADA", "DOGE", "XRP", "BNB", "AVAX"})
_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
_ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY",
})
_ENV_BLOCK = ("COOKIE", "BROWSER", "PROFILE", "SESSION", "CHROME", "FIREFOX", "SAFARI", "EDGE")


class CollectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchResult:
    items: List[Item]
    source_status: Dict[str, str] = field(default_factory=dict)


def child_env(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    src = os.environ if base is None else base
    out: Dict[str, str] = {}
    for k, v in src.items():
        up = k.upper()
        if up in _ENV_ALLOW or (up.startswith("LAST30DAYS_") and not any(b in up for b in _ENV_BLOCK)):
            out[k] = v
    out["PYTHONIOENCODING"] = "utf-8"
    return out


def epoch(value: Any) -> float:
    """RFC 3339 timestamp or ISO date -> epoch seconds; 0.0 when unknown (the gate then skips staleness)."""
    if value in (None, ""):
        return 0.0
    s = str(value).strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def validate_ticker(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not _TICKER.match(t):
        raise CollectorError(f"invalid ticker {ticker!r}")
    return t


def parse_agent_export(report: Any, ticker: str, max_text_chars: int = 2000) -> FetchResult:
    if not isinstance(report, Mapping):
        raise CollectorError("last30days export is not a JSON object")
    version = str(report.get("schema_version", ""))
    if version.split(".")[0] != SUPPORTED_MAJOR:
        raise CollectorError(f"unsupported last30days schema_version {version!r}")
    tkr = validate_ticker(ticker)
    status = {str(k): str(v) for k, v in (report.get("source_status") or {}).items()}
    items: List[Item] = []
    for r in report.get("results") or []:
        if not isinstance(r, Mapping):
            continue
        title = " ".join(str(r.get("title") or "").split())
        if not title:
            continue
        items.append(Item(ticker=tkr, source=str(r.get("source") or "unknown"), url=str(r.get("url") or "").strip(),
                          title=title[:500], text=str(r.get("summary") or "")[:max_text_chars],
                          published_at=epoch(r.get("published_at"))))
    return FetchResult(items, status)


class Last30DaysCollector:
    def __init__(self, script: str, *, python: str = sys.executable, days: int = 7, timeout_s: float = 180.0,
                 extra_args: Sequence[str] = (), query_template: str = "{ticker} stock news",
                 crypto_template: str = "{ticker} crypto news", run: Callable[..., Any] = subprocess.run) -> None:
        if not 1 <= int(days) <= 30:
            raise ValueError("days must be within 1..30")
        self.script, self.python, self.days, self.timeout_s = script, python, int(days), float(timeout_s)
        self.extra_args, self.query_template, self.crypto_template = list(extra_args), query_template, crypto_template
        self._run = run

    def query_for(self, ticker: str) -> str:
        t = validate_ticker(ticker)
        return (self.crypto_template if t in CRYPTO else self.query_template).format(ticker=t)

    def argv(self, query: str, output: str) -> List[str]:
        return [self.python, self.script, query, "--research-only", "--emit=json", "--days", str(self.days),
                "--output", output, *self.extra_args]

    def _invoke(self, args: List[str], output: Optional[str]) -> str:
        try:
            cp = self._run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=self.timeout_s, env=child_env(), shell=False,
                           cwd=os.path.dirname(os.path.abspath(self.script)) or None)
        except subprocess.TimeoutExpired as e:
            raise CollectorError(f"last30days timed out after {self.timeout_s:.0f}s") from e
        except OSError as e:
            raise CollectorError(f"could not start last30days: {e}") from e
        if cp.returncode != 0:
            raise CollectorError(f"last30days exited {cp.returncode}: {(cp.stderr or '')[-300:]}")
        if output and os.path.exists(output):
            with open(output, encoding="utf-8") as f:
                return f.read()
        return cp.stdout or ""

    def __call__(self, ticker: str) -> FetchResult:
        with tempfile.TemporaryDirectory(prefix="cofinance-l30-") as tmp:
            out = os.path.join(tmp, "export.json")
            raw = self._invoke(self.argv(self.query_for(ticker), out), out)
        try:
            report = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CollectorError(f"last30days did not emit JSON: {e}") from e
        return parse_agent_export(report, ticker)

    def preflight(self) -> Any:
        raw = self._invoke([self.python, self.script, "--preflight", "--emit=json"], None)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise CollectorError(f"preflight did not emit JSON: {e}") from e


class ReplayCollector:
    """Deterministic source over a recorded file: {"format": "cofinance-replay/1", "recorded_at": ..., "reports": {TICKER: export}}."""

    def __init__(self, path: str) -> None:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, Mapping) or data.get("format") != "cofinance-replay/1":
            raise CollectorError("not a cofinance-replay/1 file")
        self.recorded_at = epoch(data.get("recorded_at"))
        self.reports = {validate_ticker(k): v for k, v in (data.get("reports") or {}).items()}

    @property
    def tickers(self) -> List[str]:
        return list(self.reports)

    def __call__(self, ticker: str) -> FetchResult:
        rep = self.reports.get(validate_ticker(ticker))
        return FetchResult([], {"replay": "no-results"}) if rep is None else parse_agent_export(rep, ticker)
