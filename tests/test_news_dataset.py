import copy
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.feeds import MAX_BYTES, SOURCES, FeedError, fetch_feed, iso_time, parse_feed, plain
from qualify.news_dataset import (FIELDS, Groups, canonical_url, collect, csv_safe, deduplicate, digest,
                                  labelled_rows, main, readiness, split_rows, write_candidates)

APPLE = SOURCES["apple_newsroom"]
NVIDIA = SOURCES["nvidia_newsroom"]
ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Apple update</title>
<updated>2026-11-03T18:24:58Z</updated><link href="https://www.apple.com/newsroom/update/"/>
<content>Details &amp; evidence.</content></entry></feed>'''
RSS = b'''<rss version="2.0"><channel><item><title>NVIDIA reports results</title>
<link>https://blogs.nvidia.com/blog/results/</link><pubDate>Fri, 06 Nov 2026 16:00:00 GMT</pubDate>
<description>&lt;p&gt;Company announcement&lt;/p&gt;</description></item></channel></rss>'''


def record(ticker="AAPL", source="apple_newsroom", url="https://www.apple.com/newsroom/a/", title="Apple deal"):
    return {"ticker": ticker, "source": source, "source_kind": "company_primary" if source != "hackernews" else "discussion_secondary",
            "url": url, "title": title, "text": "Company announced a deal.", "timestamp": "2026-11-06T16:00:00Z",
            "timestamp_kind": "published", "published_at": "2026-11-06T16:00:00Z", "updated_at": "",
            "snapshot": "raw/example.xml", "snapshot_sha256": "hash", "observed_at": "2026-11-07T01:00:00Z"}


def annotation(row, event="event_1"):
    return {**{k: csv_safe(row[k]) for k in FIELDS[:7]}, "material": "true", "urgent": "true",
            "direction": "unclear", "trap": "none", "event_group": event, "notes": "Human review"}


class TestFeeds(unittest.TestCase):
    def test_atom_updated_is_not_publication(self):
        rows, rejected = parse_feed(ATOM, APPLE)
        self.assertEqual(rejected, {})
        self.assertEqual(rows[0]["published_at"], "")
        self.assertEqual(rows[0]["timestamp_kind"], "updated")
        self.assertEqual(rows[0]["timestamp"], "2026-11-03T18:24:58Z")

    def test_atom_published_and_enclosure(self):
        raw = ATOM.replace(b'<updated>', b'<published>2026-11-02T10:00:00Z</published><updated>')
        raw = raw.replace(b'<link href=', b'<link rel="enclosure" href="https://evil.test/a"/><link href=', 1)
        rows, _ = parse_feed(raw, APPLE)
        self.assertEqual(rows[0]["timestamp_kind"], "published")
        self.assertEqual(rows[0]["url"], "https://www.apple.com/newsroom/update/")

    def test_rss_and_html(self):
        rows, _ = parse_feed(RSS, NVIDIA)
        self.assertEqual(rows[0]["published_at"], "2026-11-06T16:00:00Z")
        self.assertEqual(rows[0]["text"], "Company announcement")
        self.assertEqual(plain("<style>bad</style><script>bad</script><p>good</p>"), "good")

    def test_bad_dates_stay_unknown(self):
        for value in ("", "nonsense", "2026-11-01", "2026-11-01T10:00:00"):
            self.assertEqual(iso_time(value), "")
        self.assertEqual(iso_time("2026-11-01T10:00:00+03:00"), "2026-11-01T07:00:00Z")

    def test_untrusted_xml_is_rejected(self):
        for raw in (b'<!DOCTYPE feed [<!ENTITY x "boom">]><feed/>', b'<html/>', b'<broken', b'\x00<feed/>', b'x' * (MAX_BYTES + 1)):
            with self.subTest(raw=raw[:40]), self.assertRaises(FeedError):
                parse_feed(raw, APPLE)

    def test_unsafe_links_missing_titles_and_dates(self):
        for url in (b'https://www.apple.com.evil.test/a', b'http://www.apple.com/a', b'https://u:p@www.apple.com/a', b'file:///a', b'https://www.apple.com:8443/a'):
            rows, reasons = parse_feed(ATOM.replace(b'https://www.apple.com/newsroom/update/', url), APPLE)
            self.assertEqual(rows, [])
            self.assertEqual(reasons, {"unsafe_url": 1})
        rows, reasons = parse_feed(ATOM.replace(b'Apple update', b''), APPLE)
        self.assertEqual(reasons, {"missing_title": 1})
        rows, reasons = parse_feed(ATOM.replace(b'2026-11-03T18:24:58Z', b'invalid'), APPLE)
        self.assertEqual(reasons, {"missing_date": 1})

    def test_fetch_is_fixed_bounded_and_credential_free(self):
        calls = []
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return APPLE.url
        def opener(request, **kwargs):
            calls.append((request, kwargs))
            return Response(ATOM)
        self.assertEqual(fetch_feed("apple_newsroom", opener=opener), ATOM)
        request, kwargs = calls[0]
        self.assertEqual(request.full_url, APPLE.url)
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Cookie", request.headers)
        self.assertEqual(kwargs["timeout"], 15)
        with self.assertRaises(FeedError):
            fetch_feed("https://evil.test", opener=opener)
        with self.assertRaises(FeedError):
            fetch_feed("apple_newsroom", opener=lambda *a, **k: Response(b'x' * (MAX_BYTES + 1)))

    def test_changed_endpoint_is_refused(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return "https://evil.test/feed"
        with self.assertRaises(FeedError):
            fetch_feed("apple_newsroom", opener=lambda *a, **k: Response(ATOM))


class TestDataset(unittest.TestCase):
    def test_canonical_urls_preserve_case_sensitive_evidence(self):
        self.assertEqual(canonical_url("https://EXAMPLE.test/A/?utm_source=hn&id=2#section"), "https://example.test/A?id=2")
        self.assertNotEqual(canonical_url("https://example.test/A"), canonical_url("https://example.test/a"))
        for url in ("javascript:alert(1)", "https://user:pass@example.test", "https://example.test:8000/a"):
            with self.assertRaises(ValueError):
                canonical_url(url)

    def test_transitive_groups_and_duplicate_provenance(self):
        records = [record(), record(source="hackernews", url="https://www.apple.com/newsroom/a/?utm_source=hn", title="Different title"),
                   record(ticker="NVDA", url="https://other.test/story", title="Different title")]
        rows = deduplicate(records)
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["group_id"] for r in rows}), 1)
        apple = next(r for r in rows if r["ticker"] == "AAPL")
        self.assertEqual(apple["source_kind"], "company_primary")
        self.assertEqual(len(apple["origins"]), 2)
        self.assertEqual(rows, deduplicate(list(reversed(records))))
        self.assertNotIn("material", apple)

    def test_groups_are_transitive_without_recursion(self):
        g = Groups()
        for n in range(2000, 0, -1):
            g.join(str(n).zfill(5), str(n - 1).zfill(5))
        self.assertEqual(g.find("02000"), "00000")

    def test_csv_formula_injection_and_blank_labels(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            rows = deduplicate([record(title="=HYPERLINK(evil)")])
            write_candidates(folder, rows, {})
            with (folder / "label.csv").open(encoding="utf-8-sig", newline="") as f:
                labels = list(csv.DictReader(f))
            self.assertTrue(labels[0]["title"].startswith("'="))
            self.assertEqual(labels[0]["material"], "")
            self.assertEqual(labels[0]["event_group"], "")
            with self.assertRaises(ValueError):
                labelled_rows(rows, labels)
        for value in ("=bad", "+bad", "-bad", "@bad", "  =bad", "\tbad"):
            self.assertTrue(csv_safe(value).startswith("'"))

    def test_human_labels_are_explicit_and_evidence_immutable(self):
        rows = deduplicate([record()])
        a = annotation(rows[0])
        result = labelled_rows(rows, [a])[0]
        self.assertIs(result["material"], True)
        self.assertIsNone(result["trap"])
        for key, value in (("material", ""), ("material", "1"), ("urgent", "maybe"), ("direction", "up"),
                           ("trap", ""), ("event_group", ""), ("title", "edited evidence")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                labelled_rows(rows, [{**a, key: value}])
        with self.assertRaises(ValueError):
            labelled_rows(rows, [{**a, "material": "false", "urgent": "true"}])
        with self.assertRaises(ValueError):
            labelled_rows(rows, [a, a])
        with self.assertRaises(ValueError):
            labelled_rows(rows, [])

    def test_human_event_groups_cannot_leak_across_split(self):
        rows = deduplicate([record(), record(ticker="NVDA", title="Partner announces deal", url="https://other.test/partner")])
        labelled = labelled_rows(rows, [annotation(r, "same_event") for r in rows])
        split = split_rows(labelled)
        self.assertTrue(not split["train"] or not split["heldout"])
        self.assertEqual(split, split_rows(list(reversed(labelled))))

    def test_readiness_blocks_tiny_or_unbalanced_empty_splits(self):
        row = {**record(), "id": "a", "group_id": "g", "event_group": "e", "material": True, "urgent": True, "trap": None}
        issues = readiness(split_rows([row]))
        self.assertTrue(any("120" in issue for issue in issues))
        self.assertTrue(any("20 human" in issue for issue in issues))
        self.assertTrue(any("both materiality" in issue for issue in issues))

    def test_collect_preserves_hn_submission_semantics_and_raw_snapshots(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            export = root / "hn.json"
            export.write_text(json.dumps({"schema_version": "1.4", "source_status": {"hackernews": "ok"},
                                          "results": [{"title": "AAPL discussion", "source": "hackernews", "url": "https://example.test/a",
                                                       "published_at": "2026-11-06", "summary": "Discussion"}]}), encoding="utf-8")
            folder = root / "bundle"
            folder.mkdir()
            with patch("qualify.news_dataset.fetch_feed", side_effect=lambda name: ATOM if name == "apple_newsroom" else RSS):
                result = collect(folder, [("AAPL", export)], now=1794013200)
            rows = [json.loads(line) for line in (folder / "candidates.jsonl").read_text().splitlines()]
            hn = next(r for r in rows if r["source"] == "hackernews")
            self.assertEqual(hn["timestamp_kind"], "submitted")
            self.assertEqual(hn["published_at"], "")
            self.assertEqual((folder / "raw/hn_AAPL.json").read_bytes(), export.read_bytes())
            self.assertEqual(result["rows"], 3)
            self.assertEqual(digest((folder / "candidates.jsonl").read_bytes()), result["candidate_sha256"])

    def test_collection_records_partial_feed_outage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            with patch("qualify.news_dataset.fetch_feed", side_effect=FeedError("unreachable")):
                result = collect(folder, [], now=1794013200)
            self.assertEqual(result["rows"], 0)
            self.assertTrue(all(s["status"] == "failed" for s in result["sources"]))

    def test_finalize_refuses_unlabelled_corpus_without_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_candidates(folder, deduplicate([record()]), {})
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                code = main(["finalize", "--corpus", str(folder), "--labels", str(folder / "label.csv"), "--out-dir", str(folder / "split")])
            self.assertEqual(code, 2)
            self.assertIn("explicit true/false", output.getvalue())
            self.assertFalse((folder / "split").exists())

    def test_finalize_detects_modified_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_candidates(folder, deduplicate([record()]), {})
            with (folder / "candidates.jsonl").open("a") as f:
                f.write("\n")
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                code = main(["finalize", "--corpus", str(folder), "--labels", str(folder / "label.csv"), "--out-dir", str(folder / "split")])
            self.assertEqual(code, 2)
            self.assertIn("hash mismatch", output.getvalue())


class TestExportIntegrity(unittest.TestCase):
    def test_extreme_timezone_dates_are_unknown_not_crashes(self):
        for value in ("9999-12-31T23:59:59-23:59", "0001-01-01T00:00:00+23:59"):
            self.assertEqual(iso_time(value), "")

    def test_duplicate_tie_breaking_is_order_independent(self):
        records = [record(title="Earlier wording"), record(title="Later wording")]
        self.assertEqual(deduplicate(records), deduplicate(list(reversed(records))))

    def test_window_filter_rejects_old_and_future_items(self):
        old = ATOM.replace(b"2026-11-03T18:24:58Z", b"2020-11-03T18:24:58Z")
        future = RSS.replace(b"Fri, 06 Nov 2026", b"Sat, 06 Nov 2027")
        with tempfile.TemporaryDirectory() as temp:
            with patch("qualify.news_dataset.fetch_feed", side_effect=[old, future]) as fetch:
                result = collect(Path(temp), [], now=1794013200)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(result["rows"], 0)
            self.assertEqual(result["rejected"], {"outside_window": 1, "future_date": 1})

    def test_successful_export_uses_exact_processed_labels_and_eval_contract(self):
        from qualify.decide_eval import load_rows
        # Fictional, offline unit-test rows only. These are not real labels or benchmark evidence.
        rows = deduplicate([record(title=f"Fictional event {i}", url=f"https://example.test/event/{i}") for i in range(160)])
        labels = []
        for i, row in enumerate(rows):
            labels.append({**annotation(row, f"fictional_event_{i}"), "material": "true" if i % 2 else "false",
                           "urgent": "true" if i % 2 else "false", "trap": "speculation" if i % 4 == 0 else "none"})
        self.assertEqual(readiness(split_rows(labelled_rows(rows, labels))), [])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            corpus = root / "corpus"
            corpus.mkdir()
            write_candidates(corpus, rows, {})
            label_path = root / "reviewed.csv"
            with label_path.open("w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(labels)
            processed_hash = digest(label_path.read_bytes())
            dest = root / "final"
            def destination(_):
                # Simulate a concurrent edit AFTER labels have been parsed.
                label_path.write_text("changed after read", encoding="utf-8")
                return dest
            with patch("qualify.news_dataset.private_destination", side_effect=destination), patch("sys.stdout", new_callable=io.StringIO):
                code = main(["finalize", "--corpus", str(corpus), "--labels", str(label_path), "--out-dir", str(dest)])
            self.assertEqual(code, 0)
            manifest = json.loads((dest / "manifest.json").read_text())
            self.assertEqual(manifest["labels_sha256"], processed_hash)
            train, heldout = load_rows(str(dest / "train.jsonl")), load_rows(str(dest / "heldout.jsonl"))
            self.assertEqual(len(train) + len(heldout), 160)
            self.assertFalse({r["split_group"] for r in train} & {r["split_group"] for r in heldout})
            self.assertTrue(all(type(r["material"]) is bool and type(r["urgent"]) is bool for r in train + heldout))
            self.assertEqual(list(root.glob(".news-corpus-*")), [])


if __name__ == "__main__":
    unittest.main()
