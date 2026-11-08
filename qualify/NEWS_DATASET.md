# Private real-news qualification corpus

## Scope

`core/feeds.py` reads fixed Apple and NVIDIA official endpoints with stdlib Python only.
`qualify/news_dataset.py` snapshots these feeds, imports already-collected HN agent exports,
and creates an unlabelled human-review corpus. It does not run a model, tune thresholds,
send notifications, change Clock 1, or write user/watchlist memory.

Source announcements are company claims, not independent evidence of market impact.
HN is supplementary discussion, not a complete finance wire. Bitcoin currently has HN only.
Regulatory/filing sources are not implemented in this slice.

## Collect

From the repo root, use a NEW directory inside ignored `data/`:

```text
python -S -E -B -m qualify.news_dataset collect --out-dir data/news_corpus_20261107 --days 30 --hn-export "AAPL=C:\path\HN_AAPL.export.json" --hn-export "NVDA=C:\path\HN_NVDA.export.json" --hn-export "BTC=C:\path\HN_BTC.export.json"
```

Each official feed is fetched once, without credentials or proxy inheritance, with a
15-second socket timeout, a 1 MiB body limit, no redirects and a 500-entry XML limit.
Only registered feeds can be fetched. Article links are validated but never fetched.
DTD/entity declarations and UTF-16/NUL-containing XML are refused. Source failures are
explicit in the manifest; successful process exit does not imply every source worked.
Nothing is installed, and there are no third-party dependencies.

Output directory:

- `raw/`: exact feed bytes and imported HN exports, preserved for replay/audit.
- `candidates.jsonl`: normalized rows and all merged origins, raw snapshot hashes and timestamp semantics.
- `label.csv`: formula-escaped evidence columns and blank human-label columns. No suggested labels or split names.
- `manifest.json`: source outcomes, rejection counts, collection window, dedupe counts and candidate-file hash.

Existing outputs are never overwritten. A bundle is published by directory rename only
after the run completes. Temporary output directories are cleaned if preparation fails.
Do not commit, upload or publish `data/` or metrics without explicit approval.

## Timestamp rules

- RSS `pubDate` or Atom `published`: `timestamp_kind=published`.
- Atom `updated` without `published`: `timestamp_kind=updated`; publication stays unknown.
- HN export `published_at`: `timestamp_kind=submitted`; it dates the HN submission, not necessarily the article.
- Unknown/malformed dates are rejected; no timestamp is invented.
- The collection window filters the timestamp with its stated semantics, NOT a guaranteed event date.

Apple commonly supplies only `updated`. Check the linked article before treating an old
announcement updated recently as a fresh catalyst. Human labels describe what the row
actually says, not what the collector's date might imply. Text is only a feed summary or
HN excerpt; leave it unlabelled if the evidence is insufficient, rather than guessing.

## Label

Copy `label.csv` locally if desired and fill ONLY the last six columns:

| Column | Allowed values |
| --- | --- |
| material | `true` / `false`: plausibly changes the thesis or price within about one week |
| urgent | `true` / `false`: needs attention before the next scheduled digest; must imply material |
| direction | `bullish`, `bearish`, `neutral`, `unclear` |
| trap | `none`, `denial`, `speculation`, `retrospective`, `peer_mention`, `injection` |
| event_group | Your event identifier, e.g. `apple_earnings_2026q4`; use the SAME ID for the same event across sources/tickers |
| notes | Optional rationale, up to 2000 characters |

Keep every candidate ID exactly once and leave evidence columns unchanged. Preserve
leading apostrophes that protect spreadsheet formulas. Unknown labels remain blank and
block export; blank never means negative. A human must establish these labels.

Exact URL/title duplicates are grouped automatically. Semantically duplicate events
with different titles require the shared human `event_group`, otherwise leakage can
still occur. Do not label the same event differently just to improve a score.

## Freeze and export

```text
python -S -E -B -m qualify.news_dataset finalize --corpus data/news_corpus_20261107 --labels data/news_corpus_20261107/label.csv --out-dir data/news_labelled_v1
```

Validation checks the corpus hash, exact CSV header, evidence columns, complete IDs,
explicit labels, enum values, urgency consistency and human event groups. Minimum
readiness checks require 120 rows, 20 traps, both materiality classes and urgent examples
in BOTH partitions, and at least 20 held-out rows. These are readiness checks, not proof
of representative class balance or sufficient statistical power. Review source, ticker,
class and trap coverage before interpreting any benchmark.

Source groups and human event groups form connected components ACROSS tickers.
A fixed SHA-256 group assignment sends approximately 25% to held-out. Ratio and class
balance are not guaranteed. Do not reroll seeds or event IDs to chase favourable results.
Freeze the entire labelled version; fit only on train and inspect held-out once. Adding
new evidence or correcting event grouping requires a new version, not silently changing
an already-used held-out set.

Successful finalization writes `train.jsonl`, `heldout.jsonl`, and a provenance manifest
with hashes of the candidate corpus and labels. Both JSONL files are compatible with
`qualify.decide_eval`; the unlabelled candidate file is NOT benchmark input.

```text
python -m qualify.decide_eval --arm gliner --data data/news_labelled_v1/train.jsonl --out reports/gliner_real_train.json
```

The existing evaluator reports a best threshold fitted on whichever input it receives.
Do NOT use its `best_digest_threshold_on_this_set` from held-out for threshold fitting.
A separate fixed-threshold held-out verification remains necessary after selecting a
threshold on train. This slice does not change evaluation or signal-gate thresholds.

## Verification

```text
python -S -E -B -m unittest tests.test_news_dataset -v
```

Tests use fictional offline feed/export fixtures. Live feed collection is separate
runtime evidence, never an accuracy estimate. No automated human-label claim is made.
