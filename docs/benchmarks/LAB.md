# Historical strategy lab

> [!WARNING]
> **Experimental result.** Retrospective prices, five handpicked assets, simple rules and small models. Not the production research pipeline, not a trading signal and not financial advice. Do not use these numbers to make investment decisions.

## Why

The first [historical benchmark](HISTORICAL.md) had 125 cases: too few to tune and test on the same rows. The lab uses the same target and features on long history, so ideas can be iterated quickly without fooling ourselves.

## Setup

- **Data:** public Yahoo daily OHLC from Sep 2014 (same approved host), cutoff exclusive 2026-11-07. 2,826 cases after warm-up and missing bars (ETH starts in late 2017).
- **Target:** next-open entry to fifth-session close, every fifth SPY session, as in the historical benchmark.
- **Features:** past returns (1/5/20/60), 20-session volatility, 60-session drawdown, gaps to 20/50-session averages, SPY 5/20-session returns and volatility, crypto flag. All from bars up to the observation day.
- **Splits by time, with an embargo:**

| Split | Entries | Cases | Use |
| --- | --- | --- | --- |
| Dev | 2014-11 to 2023-12 (scored 2018+) | 2,121 (1,500 scored) | Tune freely |
| Validation | 2024-01 to 2025-04 | 330 | Check selections; keep peeks rare |
| Test | 2025-05 to 2026-11 | 375 | Locked. Only `run --final`; every peek is appended to `test_peeks.jsonl` |

Rows straddling a boundary are dropped. Models (logistic regression, small gradient boosting) train walk-forward by year on rows that exited before the first row they predict. Note: the test window overlaps the May-Nov 2026 period already viewed once in the first benchmark.

- **Promotion gate (frozen before results):** on validation AND test, balanced accuracy at least 55%, all-case accuracy at least 3 points above the better of always-UP and momentum, and a one-sided binomial p-value at most 0.05 against that baseline accuracy (not against 50%: markets rose in about 55% of these weeks).

## First run (2026-11-09): nothing passes

Direction is close to a coin flip plus upward drift. On dev, **always-UP had the best accuracy (54.9%)**. No arm beat the best baseline with p below 0.05 on either split. The test split was not opened.

| Arm | Dev acc | Dev balanced | Val acc | Val balanced | Dev return / drawdown | Val return / drawdown |
| --- | --- | --- | --- | --- | --- | --- |
| always-UP | 54.9% | 50.0% | 54.2% | 50.0% | +148% / 59% | +31% / 27% |
| momentum 20 | 53.1% | 52.1% | 52.4% | 52.4% | +213% / 35% | +22% / 12% |
| market trend (SPY 20) | 52.7% | 51.1% | 56.4% | 55.0% | +148% / 54% | +49% / 14% |
| dual momentum (20 and 60 agree, else cash) | 53.8% covered, 71% coverage | 36.8% | 54.0% covered, 72% coverage | 38.2% | +286% / 20% | +19% / 11% |
| logistic regression | 54.7% | 50.3% | 53.3% | 49.7% | +62% / 60% | +32% / 26% |
| gradient boosting | 51.0% | 48.4% | 52.1% | 49.3% | +0% / 58% | +31% / 20% |

Return is an illustrative equal-weight weekly long/cash calculation with 10 bps per side, no shorting, cash at zero, drawdown measured at weekly exits only. Balanced accuracy for abstaining arms counts abstentions as misses.

Market trend looked best on validation, but it was worse than always-UP on dev, so that is most likely noise. The models mostly relearned "usually up".

## What it does show

Trend filters did not predict direction better, but they **cut drawdowns sharply**. On dev, dual momentum went from 59% (always-UP) to 20% with a higher illustrative return. On validation it went from 27% to 11%, with a lower return. That is a risk-regime signal, not a forecast. It is consistent with long-known trend-following behavior and still needs the locked test before any claim.

## Reproduce

```text
python -m qualify.lab collect --root data/lab_20261109
python -m qualify.lab build   --root data/lab_20261109
python -m qualify.lab run     --root data/lab_20261109
python -m unittest tests.test_lab
```

[Dev and validation report](lab-2026-11-09.json). Raw prices and per-case rows stay in ignored `data/`.
