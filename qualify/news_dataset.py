"""Private headline corpus: collect -> blind human labels -> grouped train/held-out export.

No models or guessed labels. Raw snapshots, timestamp semantics and provenance are retained.
See qualify/NEWS_DATASET.md. CLI outputs are restricted to this checkout's ignored data/ tree.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import tempfile
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from core.collector import epoch, parse_agent_export, validate_ticker
from core.feeds import MAX_BYTES, SOURCES, FeedError, fetch_feed, parse_feed

FORMAT = "cofinance-news-corpus/1"
FIELDS = ("id", "ticker", "title", "text", "url", "timestamp_kind", "timestamp", "material", "urgent",
          "direction", "trap", "event_group", "notes")
DIRECTIONS = {"bullish", "bearish", "neutral", "unclear"}
TRAPS = {"none", "denial", "speculation", "retrospective", "peer_mention", "injection"}


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode("utf-8")).hexdigest()


def canonical_url(value: str) -> str:
    p = urlsplit(value.strip())
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        raise ValueError("invalid evidence URL")
    if p.port not in (None, 80, 443):
        raise ValueError("nonstandard evidence URL port")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    # Only the host is case-insensitive; paths and evidence-bearing parameters are not.
    return urlunsplit(("https", p.hostname.lower(), p.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def title_key(title: str) -> str:
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", title).casefold()))


class Groups:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self.parent.setdefault(key, key)
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while key != root:
            key, self.parent[key] = self.parent[key], root
        return root

    def join(self, a: str, b: str) -> None:
        a, b = self.find(a), self.find(b)
        self.parent[max(a, b)] = min(a, b)


def deduplicate(records: list[dict]) -> list[dict]:
    """URL/title connected components span tickers, avoiding obvious split leakage.

    Semantic event duplicates with different headlines still need human event_group review.
    """
    groups = Groups()
    for row in records:
        groups.join("url:" + canonical_url(row["url"]), "title:" + title_key(row["title"]))
    merged: dict[tuple[str, str], dict] = {}
    for record in sorted(records, key=lambda r: (r["source_kind"] != "company_primary", r["source"], r["url"],
                                                 digest(json.dumps(r, sort_keys=True, ensure_ascii=True)))):
        group = digest(groups.find("url:" + canonical_url(record["url"])))
        key = (record["ticker"], group)
        origin = {k: record.get(k, "") for k in ("source", "source_kind", "url", "published_at", "updated_at",
                                                 "timestamp", "timestamp_kind", "snapshot", "snapshot_sha256", "observed_at")}
        if key not in merged:
            merged[key] = {**record, "id": digest(record["ticker"] + "|" + group)[:24],
                           "group_id": group, "label_status": "unlabelled", "origins": []}
        merged[key]["origins"].append(origin)
    return sorted(merged.values(), key=lambda r: r["id"])


def csv_safe(value: object) -> str:
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")) else text


def write_candidates(folder: Path, rows: list[dict], manifest: dict) -> None:
    (folder / "candidates.jsonl").write_text("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in rows), encoding="utf-8")
    with (folder / "label.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(row[k]) if k in row else "" for k in FIELDS})
    manifest.update({"format": FORMAT, "rows": len(rows), "candidate_sha256": digest((folder / "candidates.jsonl").read_bytes()),
                     "by_ticker": dict(Counter(r["ticker"] for r in rows)),
                     "by_source": dict(Counter(r["source"] for r in rows)), "labels": "human review required",
                     "warning": "Not an accuracy benchmark; source claims are unverified. HN times are submissions; updated times are not publication."})
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def collect(folder: Path, hn_exports: list[tuple[str, Path]], days: int = 30, now: float | None = None) -> dict:
    if not 1 <= days <= 30:
        raise ValueError("days must be within 1..30")
    now = time.time() if now is None else now
    observed = datetime.fromtimestamp(now, timezone.utc).isoformat()
    raw_dir = folder / "raw"
    raw_dir.mkdir()
    records, sources = [], []
    rejected: Counter = Counter()
    def keep(rows: list[dict], snapshot: Path, raw: bytes) -> None:
        for row in rows:
            ts = epoch(row["timestamp"])
            why = "outside_window" if ts < now - days * 86400 else "future_date" if ts > now + 86400 else ""
            if why:
                rejected[why] += 1
                continue
            records.append({**row, "snapshot": str(snapshot.relative_to(folder)),
                            "snapshot_sha256": digest(raw), "observed_at": observed})
    for name, source in SOURCES.items():
        try:
            raw = fetch_feed(name)
            snapshot = raw_dir / (name + ".xml")
            snapshot.write_bytes(raw)
            rows, dropped = parse_feed(raw, source)
            rejected.update(dropped)
            keep(rows, snapshot, raw)
            sources.append({"source": name, "url": source.url, "status": "ok", "parsed": len(rows), "sha256": digest(raw)})
        except FeedError as exc:
            sources.append({"source": name, "url": source.url, "status": "failed", "error": str(exc)})
    seen_tickers: set[str] = set()
    for ticker, path in hn_exports:
        ticker = validate_ticker(ticker)
        if ticker in seen_tickers:
            raise ValueError("only one HN snapshot per ticker per collection")
        seen_tickers.add(ticker)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("HN export exceeds byte limit")
        raw = path.read_bytes()
        report = json.loads(raw)
        parsed = parse_agent_export(report, ticker)
        if any(item.source != "hackernews" for item in parsed.items):
            raise ValueError("HN import contains a different source")
        snapshot = raw_dir / ("hn_" + ticker + ".json")
        snapshot.write_bytes(raw)
        rows = [{"ticker": ticker, "source": "hackernews", "source_kind": "discussion_secondary",
                 "title": item.title, "text": item.text, "url": item.url, "published_at": "", "updated_at": "",
                 "timestamp": datetime.fromtimestamp(item.published_at, timezone.utc).isoformat() if item.published_at else "",
                 "timestamp_kind": "submitted", "source_generated_at": report.get("generated_at", "")}
                for item in parsed.items]
        rejected["missing_date"] += sum(not r["timestamp"] for r in rows)
        keep([r for r in rows if r["timestamp"]], snapshot, raw)
        sources.append({"source": "hackernews", "ticker": ticker, "status": report.get("source_status", {}).get("hackernews", "unknown"),
                        "parsed": len(rows), "sha256": digest(raw)})
    rows = deduplicate(records)
    manifest = {"collected_at": observed, "window_days": days, "sources": sources, "rejected": dict(rejected),
                "before_dedupe": len(records), "duplicates_merged": len(records) - len(rows)}
    write_candidates(folder, rows, manifest)
    return manifest


def labelled_rows(rows: list[dict], annotations: list[dict]) -> list[dict]:
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(rows) or not rows:
        raise ValueError("empty corpus or duplicate candidate IDs")
    if len({a.get("id") for a in annotations}) != len(annotations) or {a.get("id") for a in annotations} != set(by_id):
        raise ValueError("labels must contain each candidate ID exactly once")
    result = []
    for a in annotations:
        row = by_id[a["id"]]
        # Labels cannot silently change evidence text, URLs, ticker or timestamp semantics.
        for key in FIELDS[:7]:
            if a.get(key) != csv_safe(row[key]):
                raise ValueError(f"{row['id']}: immutable evidence column changed: {key}")
        values = {k: (a.get(k) or "").strip().lower() for k in ("material", "urgent", "direction", "trap")}
        if values["material"] not in {"true", "false"} or values["urgent"] not in {"true", "false"}:
            raise ValueError(f"{row['id']}: material and urgent require explicit true/false human labels")
        if values["direction"] not in DIRECTIONS or values["trap"] not in TRAPS:
            raise ValueError(f"{row['id']}: invalid or missing direction/trap")
        event = (a.get("event_group") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", event):
            raise ValueError(f"{row['id']}: event_group required (letters, digits, . _ : -)")
        material, urgent = values["material"] == "true", values["urgent"] == "true"
        if urgent and not material:
            raise ValueError(f"{row['id']}: urgent cannot be true when material is false")
        notes = a.get("notes") or ""
        if len(notes) > 2000:
            raise ValueError("notes exceed length limit")
        result.append({**row, "label_status": "human", "material": material, "urgent": urgent,
                       "direction": values["direction"], "trap": None if values["trap"] == "none" else values["trap"],
                       "event_group": event, "notes": notes})
    return result


def split_rows(rows: list[dict], seed: str = "cofinance-v2-1") -> dict[str, list[dict]]:
    groups = Groups()
    for row in rows:
        groups.join("source:" + row["group_id"], "event:" + row["event_group"])
    splits: dict[str, list[dict]] = {"train": [], "heldout": []}
    for row in sorted(rows, key=lambda r: r["id"]):
        group = groups.find("source:" + row["group_id"])
        split = "heldout" if int(digest(seed + "|" + group)[:16], 16) % 4 == 0 else "train"
        splits[split].append({**row, "split_group": digest(group), "split": split})
    return splits


def readiness(splits: dict[str, list[dict]], minimum: int = 120, min_traps: int = 20) -> list[str]:
    rows = splits["train"] + splits["heldout"]
    issues = []
    if len(rows) < minimum:
        issues.append(f"need at least {minimum} rows; have {len(rows)}")
    if sum(bool(r.get("trap")) for r in rows) < min_traps:
        issues.append(f"need at least {min_traps} human-identified traps")
    for name, batch in splits.items():
        if {r["material"] for r in batch} != {True, False}:
            issues.append(name + " needs both materiality classes")
        if not any(r["urgent"] for r in batch):
            issues.append(name + " needs urgent examples")
    if len(splits["heldout"]) < 20:
        issues.append("heldout needs at least 20 rows")
    return issues


def private_destination(value: str) -> Path:
    path = Path(value).resolve()
    private = (Path(__file__).resolve().parents[1] / "data").resolve()
    if path == private or not path.is_relative_to(private) or path.exists():
        raise ValueError("output must be a new directory under this checkout's ignored data/ tree")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    commands = ap.add_subparsers(dest="command", required=True)
    c = commands.add_parser("collect")
    c.add_argument("--out-dir", required=True)
    c.add_argument("--days", type=int, default=30)
    c.add_argument("--hn-export", action="append", default=[], metavar="TICKER=PATH")
    f = commands.add_parser("finalize")
    f.add_argument("--corpus", required=True)
    f.add_argument("--labels", required=True)
    f.add_argument("--out-dir", required=True)
    a = ap.parse_args(argv)
    try:
        if a.command == "finalize":
            corpus = Path(a.corpus)
            manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
            raw = (corpus / "candidates.jsonl").read_bytes()
            if manifest.get("format") != FORMAT or digest(raw) != manifest.get("candidate_sha256"):
                raise ValueError("corpus manifest or hash mismatch")
            rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
            labels_raw = Path(a.labels).read_bytes()
            reader = csv.DictReader(io.StringIO(labels_raw.decode("utf-8-sig"), newline=""))
            if tuple(reader.fieldnames or ()) != FIELDS:
                raise ValueError("label CSV header does not match the template")
            annotations = list(reader)
            splits = split_rows(labelled_rows(rows, annotations))
            issues = readiness(splits)
            if issues:
                raise ValueError("not benchmark-ready: " + "; ".join(issues))
        dest = private_destination(a.out_dir)
        with tempfile.TemporaryDirectory(prefix=".news-corpus-", dir=dest.parent) as temp:
            folder = Path(temp) / "bundle"
            folder.mkdir()
            if a.command == "collect":
                exports = []
                for value in a.hn_export:
                    ticker, sep, path = value.partition("=")
                    if not sep:
                        raise ValueError("--hn-export requires TICKER=PATH")
                    exports.append((ticker, Path(path)))
                result = collect(folder, exports, a.days)
            else:
                for name, batch in splits.items():
                    (folder / (name + ".jsonl")).write_text("".join(json.dumps(r, ensure_ascii=True) + "\n" for r in batch), encoding="utf-8")
                provenance_path = corpus / "label_provenance.json"
                provenance = json.loads(provenance_path.read_text(encoding="utf-8")) if provenance_path.is_file() else {"labeler": "human"}
                result = {"format": "cofinance-labelled-split/1", "seed": "cofinance-v2-1",
                          "corpus_sha256": manifest["candidate_sha256"], "labels_sha256": digest(labels_raw),
                          "counts": {k: len(v) for k, v in splits.items()},
                          # Model labels are never recorded as human labels.
                          "human_labels": provenance.get("labeler") == "human", "label_provenance": provenance}
                (folder / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            if dest.exists():
                raise ValueError("output appeared during run; refusing to overwrite")
            folder.rename(dest)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Dataset stopped: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
