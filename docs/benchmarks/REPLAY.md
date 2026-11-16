# Replay benchmark: historical research and decisions

> [!WARNING]
> **Experimental dev-window results.** No predictive edge demonstrated. Not a trading signal or financial advice. Cofinance never trades.

## Method

`qualify/replay.py` runs the live router, Data Analyst, News Researcher, Team Lead and guards with historical replacements for the two data-tool ports. An **additional benchmark-only Decision call**, not a production forecast feature, returns UP, DOWN or ABSTAIN from the accepted team answer and specialist summaries.

- Market bars are restricted to origin T. News must pass the live gate and be available within seven days before the end of T, UTC.
- Target: next session open to the fifth subsequent US-session close.
- Baselines: always-UP, trailing 20-session momentum, and the majority direction of served news items.
- Predictions are sealed before scoring. Existing sealed variants are never overwritten.
- Tests include poisoned future prices. Three deliberate breaks were caught: removed news cutoff, future-derived all-time high, and scoring without a seal.

## Development window

Nine SPY-session origins, 2026-10-27 through 2026-11-06, across NVDA, AAPL, MSFT, AMZN, TSLA, AMD, GOOGL and BTC: **72 correlated cases**, not 72 independent weeks. Of 213 unique news items, 70 passed the gate. Outcomes rose in 31 of 72 cases.

## Results

| Arm | Accuracy, all 72 | Balanced accuracy | Labels |
| --- | --- | --- | --- |
| v1 | 41.7% | 0.484 | 71 UP, 1 error |
| v2 | 43.1% | 0.500 | 71 UP, 1 error |
| v3 | 0% including abstentions/errors | 0.000 | 63 ABSTAIN, 9 errors |
| Always-UP | 43.1% | 0.500 | 72 UP |
| Momentum20 | 52.8% | 0.507 | 25 UP, 47 DOWN |
| News rule | 38.9% | 0.404 | 29 UP, 17 DOWN, 26 ABSTAIN |

News-rule accuracy among its 46 directional calls was 60.9%. v3 made **no directional calls**, so its accuracy among calls is undefined, not zero. Errors and abstentions remain misses in the all-case metric. Zero missing outcomes and zero logged date-cutoff violations were reported. A date audit is not proof against every form of leakage or training contamination.

### v1: unrestricted specialist output

The Data Analyst hit its token cap in 51 cases. Team Lead call errors occurred in seven; 59 final candidates passed the guards. Median case latency was 38.7 seconds. The near-constant UP output did not demonstrate useful direction discrimination.

[v1 measured report](replay-v1-2026-11-16.json)

### v2: stop the Data Analyst at its first blank line

A four-case diagnostic found usable first paragraphs followed by repetition until the token cap. v2 added a first-blank-line stop, without increasing the 256-token budget.

| Measurement | v1 | v2 |
| --- | --- | --- |
| Data Analyst token-cap errors | 51 | 0 |
| Analyst summaries passing guards | 21 | 72 |
| Team Lead call errors | 7 | 6 |
| Final answers passing guards | 59 | 61 |
| Median seconds per case | 38.7 | 28.9 |

The analyst stop is the live default. This is a reliability improvement, **not evidence of better forecasts**: direction remained UP on every valid call. The accuracy difference reflects the error moving between cases. News and Team Lead fallbacks remain; the whole pipeline is not failure-free.

[v2 measured report](replay-v2-2026-11-16.json)

### v3: counting instruction, not adopted

The Decision prompt instructed the model to sum last-session price direction and bullish/bearish headline counts, then choose UP, DOWN or ABSTAIN. It returned 63 abstentions and nine malformed responses under the existing 24-token output budget.

A Python reference computed from raw snapshots and served-news labels produced 31 UP, 28 DOWN and 13 ABSTAIN, with 26 of 59 calls correct. Only 11 of 72 labels matched the model. **This is not an isolated arithmetic-adherence test:** the model received narrative reports, which can omit individual labels, while the reference used raw items. Truncation and the response contract are additional confounds. These results do not establish that the model is incapable of arithmetic.

[v3 measured report](replay-v3-2026-11-17.json)

## Fast Decision screening

`qualify.decision_lab` reuses a sealed replay's **exact model-visible team reports**. It does not rerun the router or specialists, collect data, or expose outcomes during prediction. Structured labels are validated; malformed replies stay errors. Source, plan, prompt and prediction hashes are checked before scoring.

```text
python -m qualify.decision_lab run --root data/replay_20261116 --out data/screen_contract --candidate contract-v1
python -m qualify.decision_lab score --root data/replay_20261116 --out data/screen_contract
```

The default diagnostic selects the first and last origin per asset without looking at outcomes. `--sample full` explicitly screens all cached cases. The supplied contract and evidence-guidance candidates are experiments, not adopted product behavior.

This is a cheap way to reject broken candidates, **not a substitute for end-to-end validation**. A shortlisted candidate still needs a full-team check and independent completed windows. Changing an upstream agent invalidates downstream cached reports.

## Cached diagnostic results, 2026-11-17

Both candidates were fixed before their runs and used identical saved v2 inputs: first and last origin per asset, 16 cases across two correlated days. These are diagnostic dev results, not full-system accuracy estimates.

| Candidate | Correct / all 16 | UP / DOWN / ABSTAIN / error | Directional coverage | Decision-loop seconds |
| --- | --- | --- | --- | --- |
| Cached v2 reference | 4 / 16 | 16 / 0 / 0 / 0 | 100% | Not rerun |
| Structured output, contract-v1 | 2 / 16 | 3 / 2 / 11 / 0 | 31.3% | 75.6 |
| Evidence guidance, evidence-v1 | 2 / 16 | 5 / 2 / 9 / 0 | 43.8% | 92.5 |

Both were rejected without another 72-case full-team run. They produced valid output, including DOWN labels, but did not improve all-case accuracy. Abstentions count as misses in that metric; accuracy among directional calls was 2/5 and 2/7 respectively. Always-UP scored 4/16, momentum20 9/16, and the news rule 5/16 (5/8 called).

The contract candidate tied the source on the early day and worsened on the late day; the guidance candidate improved on the early day and worsened on the late day. Origin-cluster bootstrap intervals for the all-case accuracy difference versus cached v2 were [-0.25, 0.00] and [-0.375, 0.125]. **Two clusters are insufficient for reliable inference**, and neither result establishes an improvement. No missing outcomes or recorded date-cutoff violations. Timing excludes model startup and includes incidental PC load; this is not a controlled speed comparison. Upstream agents were not rerun.

[Compact results, per-asset and split checks](decision-screen-2026-11-17.json)

## Limits

- Known dev outcomes and overlapping origins make these descriptive measurements, not independent performance estimates.
- The nine-day window cannot establish a generalizable predictive improvement. No significance claim is made from the point estimates.
- News timestamps are item publish/update/submit times, not archived first-seen times; day-only timestamps count at day end.
- Stock fundamentals lack point-in-time values. Prices are a current download and can contain revisions.
- No second-source cross-check was added for these replay inputs.
- LFM3 was released on 2026-10-27; its training cutoff is unpublished. Release timing and a negative memorization probe cannot prove absence of training contamination.
- No change to the frozen forward protocol or adoption of a new forecast feature follows from these dev results.
