# Historical news from GDELT

Experimental research input, not a forecast or financial advice. This collector never changes the live news store.

```console
python -m qualify.gdelt freeze --root data/gdelt_news_v1
python dev.py run --bg gdelt_collect -- python -B -m qualify.gdelt collect --root data/gdelt_news_v1
python dev.py wait gdelt_collect
python -m qualify.gdelt verify --root data/gdelt_news_v1
```

## Contract

- Standard-library HTTPS GET to the fixed GDELT DOC endpoint only. No API key, package install, publisher crawl or scheduler.
- Rules and collector hash frozen first. At most 31 days per run, known asset queries, English results, asset alias required in title.
- Requests start with seven-day intervals. A full 250-record response is not complete: bisect down to 15 minutes. Unresolved caps, exhausted request budget or source errors produce an **incomplete** corpus that evaluation refuses.
- Requests are spaced by ten seconds, with two bounded retries (20 and 60 seconds). An interval that still fails stops collection rather than hammering the service.
- Every request records its URL, status, response body or error, observed time and hashes. Raw receipts, candidates and protocol are checked before loading.
- `seendate` is retained exactly as `gdelt_seen`, including midnight. It is not presented as original publication time. Each interval is half-open, and out-of-range timestamps are excluded, even if the API returns them.
- Earliest-seen canonical URL or normalized-title duplicates are removed per asset. Different-worded syndicated stories can remain.
- Only the API's headline is used. Today's article text is never fetched and backdated. An archived index timestamp does not establish that today's returned title is the historical title.
- `complete` means all requested index intervals were resolved without detected gaps, not all news on the web. Search coverage, ticker ambiguity and survivorship are still limitations.
- A frozen or finished run cannot be overwritten. Interrupted or incomplete runs retain their evidence; use a new folder for another attempt.

State diagram and terminal rules: [state charts](../docs/statecharts.md).

## Research boundary

The [coverage-screen protocol](../docs/benchmarks/gdelt-news-protocol-v1.json) is fixed before the new collection.
It compares existing headlines with existing plus GDELT on the same cached dates, using the same gate and headline judge.
These price outcomes were already inspected: a comparison is development evidence, never an independent confirmation.
It cannot auto-promote a source or method. A failed collection produces no accuracy result.

The initial availability probe returned November and August 2026 articles; a January request received HTTP 429.
That does not establish a full year of usable history. Historical announcements describe several different search
ranges, so this collector verifies returned timestamps instead of assuming a range from old documentation.

Official API documentation: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
Historical search-range update: https://blog.gdeltproject.org/doc-2-0-updates-1-5-year-searching-and-updated-mobile-interface/

## Verification

`python -B -m unittest tests.test_gdelt -v`: synthetic network responses only. Tests cover caps and splitting,
cutoff boundaries, midnight semantics, duplicate handling, request limits/retries, raw errors, receipt hashes,
frozen code, terminal states and a fixed-seed transition model. All 14 tests passed; 12 of 12 deliberately planted
breaks were caught, followed by a clean restored run. Backend regression: 683 tests run, 21 skipped, OK.

Live attempt on 2026-11-19: all three requests for the first interval returned HTTP 429, including the bounded
backoffs. The run sealed as incomplete with zero candidates and all 32 requested intervals unresolved (one failed,
31 not attempted). No accuracy screen was run and no production behavior changed. Successful large-interval
collection, independently archived title history and broader cross-source coverage remain unverified.
