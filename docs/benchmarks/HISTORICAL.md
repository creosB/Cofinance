# Historical forecast experiment

## What this does, and does not, test

Cofinance's live workflow produces guarded research summaries, not a price forecast or a trading order. This experiment asks a narrower question: **does the retained LFM3 narrator extract useful next-week direction from past prices, compared with trivial rules?**

It is a benchmark-only forecast arm, not a backtest of the complete production router/news/specialist/guard pipeline. A good score here cannot establish that the full product predicts markets. A bad score rules out a predictive-edge claim for this prompt and price-only setup; the evidence-retrieval and research product can still be useful.

## Protocol frozen before inference

- Assets: AAPL, NVDA, MSFT, BTC-USD and ETH-USD. Fixed in advance, not chosen by their measured performance. SPY supplies the US-market session calendar, not a prediction target.
- Data: public Yahoo daily OHLC, downloaded on 2026-11-09. Extra warm-up history starts 2025-05-01. Outcomes end at the last completed US session, 2026-11-06. Bars on or after 2026-11-07 are excluded even if the provider returns them.
- Sampling: 25 non-overlapping five-session windows per asset, 125 forecasts total. Observations start 2026-05-11 and end 2026-10-30. Targets extend to 2026-11-06. All assets use the same SPY calendar, so crypto weekends are deliberately not separate forecast origins.
- Forecast target: whether the fifth following US-session close is above or below the **next session's open**. Entry is not the already-observed close. An observation is available after the whole source day ends (00:00 UTC the next date); the simulated entry is no earlier.
- Model input: returns over 1/5/20/60 past sessions, daily volatility over the last 20, drawdown from the trailing 60-session high, and 20 rebased closing values. Every input comes from completed bars through the observation. No future prices, ticker, date, news, fundamentals, sentiment or conversation memory is sent to the model.
- Model: existing pinned LFM3 1.7B Q4_K_M on llama.cpp b12110, CPU, 6 threads. Temperature 0, thinking off, 16 output tokens. Exact labels: UP, DOWN, ABSTAIN. Malformed output and incomplete generation count as errors, not discarded samples. No retries or prompt selection on these outcomes.
- Baselines: always UP, and follow the sign of trailing 20-session momentum. All arms are scored on the same rows.
- Evaluation windows: entries since Oct 9, Aug 9 and May 9 (last 1/3/6 calendar months relative to Nov 9). Windows are nested, not three independent tests. Cases with incomplete outcomes are not generated; every generated case remains in the denominator.

## Metrics

Primary: directional accuracy across all cases, directional accuracy on non-abstained calls, coverage, and balanced accuracy (mean up/down recall). Errors and abstentions are incorrect in all-case accuracy. Per-asset scores prevent an aggregate from hiding a weak asset.

An illustrative long/cash calculation is secondary: UP holds the asset from the next open to the fifth-session close; DOWN, ABSTAIN and errors hold cash. No shorting. Each round trip costs 10 bps per side. Equal-weight average asset returns are compounded per window. This is **not** buy-and-hold, alpha, an executable strategy, or a risk-adjusted performance claim. Drawdown is measured at weekly exits only and misses intraperiod losses. Cash earns zero; taxes, spread, slippage and market impact are not modeled.

No statistical confidence or significance is claimed from 25 time windows. Assets share market shocks and the 1/3/6-month slices overlap. A higher hit rate alone is not evidence of a durable edge.

## Leakage controls and remaining limits

`features.jsonl` and `outcomes.jsonl` are separate. The predictor never opens the outcomes or raw sources. The protocol and feature hashes are checked before inference; forecasts are written and sealed before evaluation loads outcomes. Evaluation refuses edited protocol, inputs, outcomes, missing predictions, duplicate predictions or a broken prediction seal. Existing predictions cannot be silently rerun.

Those controls block pipeline lookahead, not every form of historical contamination:

- Yahoo history was downloaded now, not archived when each forecast would have been made. Prices may be revised or retrospectively split-adjusted. This is not an immutable point-in-time feed.
- The model's training cutoff is unknown. Removing asset/date identities and rebasing prices reduces direct memorization but does not prove it absent.
- This handpicked surviving universe has selection/survivorship bias and limited market coverage.
- Historical news and fundamentals with first-seen timestamps are not available in this run. Current values must never be backfilled into past cases.
- Nothing is fitted here. Changing the prompt or policy after reading these scores consumes this experiment. Validate any new choice on a new untouched period and, ideally, forward-recorded forecasts.

## Reproduce

The runtime requires the already installed managed narrator, not a new model download. Use a new private folder when collecting again; providers can revise history, so fresh downloads are not guaranteed to be byte-identical.

```text
python -m qualify.history_collect --root data/history_benchmark_new
python -m qualify.historical prepare --root data/history_benchmark_new
python -m qualify.history_runtime --root data/history_benchmark_new
python -m qualify.historical evaluate --root data/history_benchmark_new
python -m unittest tests.test_historical tests.test_guard_boundaries
```

The default experiment dates and assets are deliberately explicit in `qualify/historical.py`. Change them only for a separately registered experiment, not to improve an already observed score. Raw source snapshots and per-case forecasts/outcomes remain in ignored `data/`. Published summaries contain scores, methodology, counts and hashes, not private chats or credentials.

## Result

First measured run (2026-11-09): **no predictive edge demonstrated**. LFM3 returned UP on all 125/125 cases. No malformed responses, request errors or abstentions occurred. Balanced accuracy was 50% in every slice.

| Slice | Cases | LFM3 / always-UP accuracy | Momentum accuracy |
| --- | --- | --- | --- |
| 1 month | 20 | 20.0% | 65.0% |
| 3 months | 60 | 53.3% | 60.0% |
| 6 months | 125 | 51.2% | 53.6% |

Six-month LFM3 accuracy by asset (25 cases each): AAPL 76%, NVDA 52%, MSFT 48%, BTC 40%, ETH 40%. These are exactly those assets' up-period frequencies, not evidence of asset-specific forecasting skill.

Secondary illustrative six-month long/cash return after the stated round-trip costs: LFM3 / always-UP +0.67%, momentum +5.74%. Weekly-exit maximum drawdown: 15.62% vs 5.49%. These figures omit intraperiod drawdowns and execution effects and do not establish tradable alpha.

[Measured report](history-2026-11-09.json) and [frozen protocol](history-2026-11-09-protocol.json) include counts and hashes. Collection: six requested symbols covered (five targets plus calendar), no generated-case exclusions. Processing: 125 generated / 125 scored, no missing forecasts. Full per-case records and source responses stay in ignored local data.

Decision: do not promote this forecast prompt into the product or retune it against these outcomes. Keep LFM3 as the research narrator. The untested hypothesis is whether a timestamp-safe **full research** pipeline adds value with historical news and fundamentals; this price-only experiment does not answer it.
