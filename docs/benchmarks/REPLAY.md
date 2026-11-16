# Replay benchmark: the full system at past dates

> [!WARNING]
> **Experimental.** A dev-window measurement of a research tool. Not a trading signal, not financial advice. Cofinance never trades.

## Question

The models are local and offline. So the whole live pipeline can be handed old data as if it were current, and its call can be scored against what happened next. This measures the system as it runs (router, Data Analyst, News Researcher, Team Lead, guards), not one prompt in isolation. Changes to guidance, parallelism and structure are each measured here, one at a time.

## Method (`qualify/replay.py`)

- **Tools cut at the origin day T.** Prices: bars on or before T only. News: items that passed the live pre-gate and SignalGate at their own time and were available within 7 days before the end of T (UTC). The team is never told the date.
- **Unchanged live code:** GLiNER router, the three agents, the guards. Only the two tool ports are swapped.
- **Decision step:** one extra LFM3 call turns the accepted answer into UP, DOWN or ABSTAIN (first word of the reply).
- **Target:** direction from T+1's open to the 5th session's close.
- **Baselines:** always-UP; momentum20 (20-session close change); a news rule (more bullish than bearish served items means UP).
- **Discipline:** predictions are resumable, sealed with hashes of the predictions and the code, and scored only after the seal. A sealed variant never reruns; a change is a new variant.
- **Proof:** a test run with every future price poisoned to 99999 never shows it to any model; 3 of 3 deliberate breaks caught (no news cutoff, all-time high reading the future, scoring before the seal).

## Window v1 (dev)

SPY sessions 2026-10-27 to 2026-11-06 (after LFM3's release on 10/27), 9 origins x 8 assets (NVDA, AAPL, MSFT, AMZN, TSLA, AMD, GOOGL, BTC) = 72 cases. News: 213 unique items, 70 accepted by the gate. Only 43% of cases rose: a falling window.

## Result, variant v1 (sealed 2026-11-16)

| Arm | Accuracy (all 72) | Balanced accuracy | Calls |
| --- | --- | --- | --- |
| System v1 | 41.7% | 0.484 | 71 UP, 1 error |
| always-UP | 43.1% | 0.500 | 72 UP |
| momentum20 | 52.8% | 0.507 | 25 UP, 47 DOWN |
| News rule | 38.9% (60.9% of the 46 it called) | 0.404 | 26 abstained |

**Verdict: v1 is always-UP.** The full system said UP on 71 of 72 cases, including weeks where the price trend and the news pointed down, so it added no information over the simplest baseline. Momentum's edge comes from NVDA and BTC, which fell through the whole window (9 of 9 each); one trending window says nothing general.

## What the pipeline did

- Routes: all 72 went to thesis review with both specialists.
- **Data Analyst ran out of tokens in 51 of 72 cases** and fell back to the plain fact list. Team Lead ran out in 7 (its guard then saw an empty candidate).
- Team Lead answers passed the number guard in 59 of 72; others were rejected for an empty candidate (7), an unsupported risk (4) or unsupported terms (2), and fell back.
- Served news: median 2 items per case, at most 5; 57 cases had some.
- Decision always began with UP, then an explanation.
- Leaks: 0 (every case's latest bar is T, every served item is before the cutoff). Median 38.7 s per case on CPU.

## Levers this points to (v2, one at a time, same window)

1. **Data Analyst budget or prompt**, so the analysis exists instead of a template.
2. **Decision guidance:** a rubric that weighs trend, drawdown and news direction, with ABSTAIN allowed.
3. **Parallel views:** a bull and a bear reader before Decision.

## Limits

Dev window, so tuning on it is expected and nothing here is a claim; cases overlap and are correlated (same week, same market). News availability is the item's own time, not an archived first-seen time. No point-in-time fundamentals for stocks. Prices are a current download. LFM3's training cutoff is unpublished. A claim needs a sealed variant on the test window (weeks after 11/13, replayed once they complete).

Result file: [replay-v1-2026-11-16.json](replay-v1-2026-11-16.json)
