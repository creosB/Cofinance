# Risk lab: is next week's risk predictable?

> [!WARNING]
> **Experimental result.** Retrospective prices, five handpicked assets, one simple volatility measure. It says nothing about direction. Not a trading signal and not financial advice.

## Why

The [strategy lab](LAB.md) found that direction is close to a coin flip plus upward drift, while trend filters cut drawdowns. That suggests the predictable part is risk, not direction. This lab asks whether recent volatility tells us how big next week's move will be.

## Setup

- **Data:** the frozen strategy-lab cases (hash-checked, no new downloads): AAPL, NVDA, MSFT, BTC-USD, ETH-USD, next open to fifth-session close, every fifth SPY session, with the same dev, validation and locked test splits.
- **Forecast:** 20-session daily volatility scaled to a week, with the scale learned from the same asset's earlier cases. **Baseline:** the asset's usual weekly risk from those same earlier cases. Both need 52 earlier cases (52 per asset skipped).
- **Statistics:** one value per week (assets in a week move together), one-sided moving-block bootstrap (8-week blocks, 4,000 draws, seed 20261116).
- **Rules fixed in code and committed before each look:** v1 in `1ee0bf1`, v2 in `5d8f552` (`qualify/risk_lab.py`, 10 tests, 5 deliberate breaks caught).

## v1: level forecast (failed)

QLIKE loss, lower is better. A pass needed a gain above 0 with p at most 0.05 on validation and test.

| Split | Weeks | Gain vs usual risk | p | Result |
| --- | --- | --- | --- | --- |
| Dev 2015-12 to 2023-12 | 405 | -0.10 | 0.995 | worse |
| Validation 2024-01 to 2025-04 | 66 | -0.08 | 0.97 | worse |

The test split stayed closed. A dev-only check showed the scale rule was not the cause: the other two scale rules lost by more. Twenty sessions is too noisy, and calm weeks get hit by jumps (BTC and ETH were worst). The level claim is closed.

## v2: elevated-risk flag (passed, fragile)

v1 showed, as a side number, that flagged weeks had more big moves. That became a new hypothesis, fixed before any v2 number: a week is **flagged** when the forecast is above 1.25 times usual risk, and a **bigger-than-usual week** is a move above one usual standard deviation. Dev and validation had been seen, so they were only a same-direction precondition. The claim rests on one look at the unseen test split (difference above 0, p at most 0.05).

| Split | Flagged cases | Bigger-than-usual, flagged | Bigger-than-usual, other | p |
| --- | --- | --- | --- | --- |
| Dev | 747 | 33.5% | 19.8% | 0.0002 |
| Validation | 81 | 29.6% | 21.7% | 0.026 |
| **Test (one look)** | **57** | **31.6%** | **15.4%** | **0.014** |

Moves above two usual standard deviations (side number): 12.3% of flagged test cases vs 3.1% of the rest. The test opening is logged in `test_peeks.jsonl` (peek 1).

## Benchmark checklist

- **Small:** 57 flagged cases in 31 weeks of the test split.
- **Uneven across assets (test):** MSFT 13 of 26 flagged vs 11 of 49 other; AAPL 4 of 18 vs 16 of 57; BTC 0 of 4 vs 4 of 71; ETH 0 of 7 vs 8 of 68; NVDA 1 of 2 vs 10 of 73. MSFT carries the result; AAPL, BTC and ETH point the other way.
- **Uneven over time (test):** first half 21% vs 13%, second half 35% vs 18%.
- **Repeatable:** fixed seed; rerunning reproduces every number, and the v1 numbers were unchanged by the v2 refactor.
- **Known effect:** volatility clustering is long documented. This measures it on our cases; it is not a discovery.
- **Overlap:** the test window overlaps the May to Nov 2026 period already viewed once by the first [historical benchmark](HISTORICAL.md), for direction only.

## What it means

A pre-registered pass, not yet a robust product signal. Cofinance does not show a risk flag in chat. Next: the same frozen rule on more assets, or forward weeks, before any chat use.

## Reproduce

```text
python -m qualify.risk_lab run --root data/lab_20261109
python -m qualify.risk_lab run --root data/lab_20261109 --final   (refuses: the test was already opened once)
```

Result file: [risk-lab-2026-11-16.json](risk-lab-2026-11-16.json).
