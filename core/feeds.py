"""Small, read-only official company feeds. No credentials, model calls or article fetching.

Feed timestamps retain their meaning: Atom updated is NOT article publication time.
These are company-authored announcements, not independent confirmation of their claims.
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable

MAX_BYTES = 1_048_576
ATOM = "{http://www.w3.org/2005/Atom}"


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class FeedSource:
    name: str
    ticker: str
    url: str
    article_hosts: tuple[str, ...]


SOURCES = {
    "apple_newsroom": FeedSource("apple_newsroom", "AAPL", "https://www.apple.com/newsroom/rss-feed.rss",
                                 ("www.apple.com", "apple.com")),
    "nvidia_newsroom": FeedSource("nvidia_newsroom", "NVDA", "https://nvidianews.nvidia.com/releases.xml",
                                  ("nvidianews.nvidia.com", "blogs.nvidia.com", "www.nvidia.com")),
}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def plain(value: str, limit: int = 2000) -> str:
    parser = _Text()
    parser.feed(value)
    return " ".join(html.unescape(" ".join(parser.parts)).split())[:limit]


def iso_time(value: str) -> str:
    """Require a timezone. Missing/malformed dates stay unknown, never become 'now'."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return ""
    if dt.tzinfo is None:
        return ""
    try:
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, ValueError):
        return ""


def safe_article_url(value: str, source: FeedSource) -> str:
    try:
        p = urllib.parse.urlsplit(value.strip())
        if (p.scheme != "https" or p.hostname not in source.article_hosts or p.username or p.password
                or p.port not in (None, 443)):
            return ""
    except ValueError:
        return ""
    return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))


def parse_feed(raw: bytes, source: FeedSource) -> tuple[list[dict], dict[str, int]]:
    """Parse RSS 2 or Atom, returning records plus explicit rejected-row counts."""
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise FeedError("feed exceeds byte limit or is not bytes")
    if b"\x00" in raw or re.search(br"<!\s*(DOCTYPE|ENTITY)\b", raw, re.I):
        raise FeedError("DTD, entity declarations and NUL bytes are forbidden")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedError("malformed feed XML") from exc
    atom = root.tag == ATOM + "feed"
    if atom:
        nodes = root.findall(ATOM + "entry")
    elif root.tag == "rss" and root.find("channel") is not None:
        nodes = root.findall("channel/item")
    else:
        raise FeedError("not an RSS 2 or Atom feed")
    if len(nodes) > 500:
        raise FeedError("feed exceeds entry limit")
    rows: list[dict] = []
    rejected: dict[str, int] = {}
    for node in nodes:
        prefix = ATOM if atom else ""
        def text(name: str) -> str:
            child = node.find(prefix + name)
            return "" if child is None else "".join(child.itertext())
        title = plain(text("title"), 500)
        if atom:
            links = node.findall(ATOM + "link")
            url = next((link.get("href", "") for link in links
                        if link.get("rel", "alternate") == "alternate"), "")
            published, updated = iso_time(text("published")), iso_time(text("updated"))
            summary = text("summary") or text("content")
        else:
            url = text("link")
            published, updated = iso_time(text("pubDate")), iso_time(text("modDate"))
            summary = text("description")
        url = safe_article_url(url, source)
        reason = "missing_title" if not title else "unsafe_url" if not url else "missing_date" if not (published or updated) else ""
        if reason:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        rows.append({"ticker": source.ticker, "source": source.name, "source_kind": "company_primary",
                     "title": title, "text": plain(summary), "url": url, "published_at": published,
                     "updated_at": updated, "timestamp": published or updated,
                     "timestamp_kind": "published" if published else "updated"})
    return rows, rejected


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FeedError("feed redirect refused")


def fetch_feed(name: str, *, timeout: float = 15.0, opener: Callable | None = None) -> bytes:
    """Only registered HTTPS endpoints. Direct connection; no inherited proxy credentials."""
    if name not in SOURCES:
        raise FeedError("unknown official feed")
    source = SOURCES[name]
    open_url = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect()).open
    request = urllib.request.Request(source.url, headers={
        "User-Agent": "CofinanceResearch/0.1 (public news feed reader)",
        "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
        "Accept-Encoding": "identity",
    })
    try:
        with open_url(request, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != source.url:
                raise FeedError("unexpected feed response or endpoint")
            raw = response.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FeedError(f"feed fetch failed: {type(exc).__name__}") from exc
    if len(raw) > MAX_BYTES:
        raise FeedError("feed exceeds byte limit")
    return raw
