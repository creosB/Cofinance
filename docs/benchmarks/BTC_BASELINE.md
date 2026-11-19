# Bitcoin threshold baseline (v1)

> [!WARNING]
> **Experimental result.** A retrospective comparison with recorded crowd prices on resolved markets. Not a
> forecast, trading signal or financial advice. Cofinance never trades.

## Question

The narrator cannot be read as a probability ([readout check](READOUT_CHECK.md)), so any forecast has to start
from Python. Can a better price, volatility and horizon model match the Polymarket crowd on daily "Bitcoin above
___ on <date>?" markets, and does such a model add anything on top of the crowd's own price?

## Method (frozen before the second half was scored)

Rules: [btc-baseline-protocol-v1.json](btc-baseline-protocol-v1.json). Code: `qualify/btc_baseline.py`.

- **Markets:** the frozen sample of the [first comparison](BTC_VS_CROWD.md): 3,779 resolved markets from
  2025-11-01 to 2026-11-08, the same filters, and only contested ones (crowd or the untuned model between 5% and
  95%). Nothing was downloaded again; the source files are pinned by hash.
- **Split:** markets before 2026-05-01 choose and fit; markets from 2026-05-01 are scored once.
- **Candidates (Python only):** `v1`, the untuned driftless lognormal on 720 hours of volatility; `ewma`, the same
  with an exponentially weighted volatility (half-life 24, 72, 168 or 336 hours); `student_t`, fat tails with the
  same variance (3, 4, 6 or 10 degrees of freedom); `recal`, a logistic recalibration of v1. The lowest
  leave-one-month-out Brier on the first half is the baseline.
- **Feature test:** `crowd_plus`, a logistic fit of the outcome on the crowd's price and v1's. If v1 carries
  information the crowd lacks, this beats the crowd.
- **Scoring:** Brier (primary), log loss and ranking AUC. Differences carry a 95% interval that resamples whole
  days, because thresholds on one day move together.
- **Claims, 24 hours before settlement:** each holds only if its interval is entirely below zero.
- **Disclosure:** the first comparison already reported the crowd, v1 and their average on both halves. None of
  the fitted candidates had been scored on the second half before this run.

## Results

One run on 2026-11-19. Compact result: [btc-baseline-v1.json](btc-baseline-v1.json).

**24 hours out (primary).** First half: 392 markets on 125 days. Second half: 464 markets on 184 days, 50% Yes.

| | First half, out-of-month Brier | Second half Brier | Log loss | AUC | Brier minus crowd (95% interval) |
| --- | --- | --- | --- | --- | --- |
| Crowd | 0.1306 | **0.1369** | 0.427 | 0.885 | |
| v1 (untuned) | 0.1347 | 0.1405 | 0.441 | 0.878 | +0.0036 (-0.0009 to +0.0081) |
| Baseline: Student-t, 4 df (chosen) | 0.1342 | 0.1399 | 0.441 | 0.878 | +0.0030 (-0.0016 to +0.0071) |
| Crowd plus v1 (feature test) | 0.1304 | 0.1383 | 0.432 | 0.885 | +0.0014 (-0.0023 to +0.0049) |

The other candidates scored 0.1355 (EWMA) and 0.1348 (recalibration) on the first half. The chosen baseline
minus v1 on the second half: -0.0006 (-0.0027 to +0.0015).

**6 hours out (secondary, no claim).** 218 and 252 markets. EWMA with a 72-hour half-life was chosen: Brier
0.1594 vs crowd 0.1604, difference -0.0011 (-0.0080 to +0.0058). Crowd plus v1: -0.0025 (-0.0064 to +0.0011).

**Verdict under the frozen rules: no claim holds.**

- **Better volatility does not close the gap.** Fat tails help a little on both halves, but the improvement over
  v1 is not distinguishable from zero, and the crowd stays ahead a day out.
- **The price model adds nothing to the crowd.** Fitted on the first half, the weight on v1 is close to zero
  (-0.04 against 1.21 on the crowd), and on the second half the combination is slightly worse than the crowd
  alone. The crowd already prices what spot and volatility know.
- Cofinance keeps showing the crowd's price as the reference for these markets. A Python probability is useful
  only where no crowd exists, and even then it is a volatility estimate, not an edge.

## Checks

- **Same markets as the first comparison:** 856 contested at 24 h and 470 at 6 h, as there. Excluded: 32 with no
  crowd price (24 h only), 385 (24 h) and 374 (6 h) with no spot or volatility because of Yahoo gaps, the rest
  near certain.
- **Settlement proxy:** inherited from the first comparison (Yahoo matched Polymarket's result on 99.8%).
- **Both halves:** the crowd leads on both, and Student-t is slightly ahead of v1 on both. Only the chosen
  baseline, v1, the crowd and the feature test were scored on the second half.
- **Size:** a few hundred markets per half, correlated within a day; the intervals account for that.

## Not tested

- Other assets, other market types (ranges, touch markets), longer horizons.
- Inputs beyond price and volatility: order flow, funding, news. A model feature joins only if it improves on
  this baseline on held-out data.

## Reproduce

```text
python -m qualify.btc_baseline freeze --root data/btc_baseline_v1
python -m qualify.btc_baseline run    --root data/btc_baseline_v1
```

The source is the first comparison's folder, `data/btc_crowd_20261116`, which stays in the ignored `data/` folder.
