# Prediction-market calibration (Polymarket, read-only)

> [!WARNING]
> **Experimental result.** One retrospective measurement of recorded Polymarket prices for markets that closed between Nov 2025 and Oct 2026. It describes how well the crowd was calibrated in the past. It is not a Cofinance forecast, not a trading signal and not financial advice. Cofinance never places orders, holds keys or connects a wallet.

## Why this exists

Cofinance can show Polymarket crowd odds next to a chat answer as a **read-only reference**. Before showing those odds, we measured how often past crowd prices came true. The same measurement checks the longshot idea in the old Polymarket bot (its Strategy B bought cheap outcomes).

## Method

- **Universe:** markets that closed 2025-11-01 to 2026-11-01 with lifetime volume of at least $50,000, exactly two outcomes and a clean settlement (final prices 1 and 0). Gamma refuses deep paging, so markets come from 100 seeded random scheduled end-date days in that year: 48,232 eligible, 2,000 sampled (seed 20261110). During collection, 137 markets actually closed outside the window and 149 did not settle cleanly; both groups were excluded.
- **Scored outcome:** the first listed outcome (Yes, Up, or the first team). Many two-outcome markets are not Yes/No. The frozen protocol's notes still say "binary Yes/No"; that line is stale wording, and `scored_outcome` describes the actual rule.
- **Snapshots:** the recorded CLOB price 1, 7 and 30 days before the actual close. A snapshot uses the last point at or before that time, and is dropped if that point is more than one day old.
- **Metrics:** Brier score, Brier skill against the base rate, log loss and calibration buckets with Wilson 95% intervals. Event counts are reported because markets in one event are correlated.
- **Frozen before scoring:** sample and protocol were written before any price history was downloaded. Protocol sha256 `e4d73ebccdcb09496a6fe1fc7a1caddf164047a97d43355e6a290ec09baa697c`. History coverage: 2,000 of 2,000, 0 errors (17 markets had no recorded prices).

## Results

| Horizon before close | Markets | Events | Brier | Brier skill | First outcome won |
| --- | --- | --- | --- | --- | --- |
| 1 day | 1,049 | 982 | 0.1756 | 0.277 | 41.6% |
| 7 days | 525 | 479 | 0.1684 | 0.276 | 36.8% |
| 30 days | 119 | 108 | 0.0941 | 0.376 | 18.5% |

Lower Brier is better; skill above 0 means better than always guessing the base rate.

Selected calibration buckets (price is the recorded mean price; "won" is how often the scored outcome happened):

| Horizon | Price bucket | Markets | Mean price | Won | 95% interval |
| --- | --- | --- | --- | --- | --- |
| 1 day | 0 to 2 cents | 178 | 0.5% | 0.6% | 0.1% to 3.1% |
| 1 day | 2 to 5 cents | 35 | 3.2% | 5.7% | 1.6% to 18.6% |
| 1 day | 20 to 30 cents | 60 | 24.7% | 38.3% | 27.1% to 51.0% |
| 1 day | 40 to 50 cents | 158 | 45.5% | 50.0% | 42.3% to 57.7% |
| 1 day | 50 to 60 cents | 253 | 52.5% | 53.0% | 46.8% to 59.0% |
| 1 day | 98 to 100 cents | 26 | 99.4% | 100% | 87.1% to 100% |
| 7 days | 0 to 2 cents | 72 | 0.6% | 0% | 0% to 5.1% |
| 7 days | 40 to 50 cents | 83 | 46.0% | 55.4% | 44.7% to 65.6% |
| 30 days | below 20 cents (4 buckets) | 70 | about 4.7% | 0% | see JSON |

By category at 1 day: sports is the hardest (650 markets, Brier 0.210), then crypto (220, 0.162), politics (21, 0.135) and other (153, 0.058). The first and second halves of the period scored similarly (1 day: 0.182 and 0.169).

All buckets: [polymarket-calibration.json](polymarket-calibration.json).

## What it means

1. **The crowd was reasonably calibrated a day and a week out.** Most buckets contain their price inside the 95% interval. That makes the odds useful as context next to research, labeled as crowd opinion.
2. **No demonstrated longshot edge.** At 1 and 7 days, cheap outcomes won about as often as priced, within wide intervals. Buying them (the old Strategy B) has no measured edge, and recorded prices understate the real cost of buying.
3. **Hypotheses only, not findings.** A month out, 70 markets priced below 20 cents produced no winners when roughly 3 were expected (probability about 0.04 if prices were fair). At 1 day, the 20 to 30 cent bucket won more often than priced. Many buckets were inspected, so either result can be chance. Both need a fresh, separately frozen sample before anyone relies on them.

## How chat uses it

- Python picks and formats the odds; the narrator never sees or rewrites them, and the guards do not treat them as evidence.
- A market is shown only with 24h event volume of at least $1,000, a spread of at most 10 cents and a future end date. Per event, the market closest to 50% is chosen; at most three markets per asset and three assets per answer.
- A history line (for example, how often outcomes priced like this came true a week out) is shown only when the matching bucket has at least 30 markets.
- Each card says the odds are crowd opinion, not facts, not a Cofinance forecast and not advice. Set `COFINANCE_CROWD_ODDS=off` to disable lookups. Errors show as "unavailable" and never block the answer.
- Only keyless GET requests to `gamma-api.polymarket.com` and `clob.polymarket.com` are allowed: no redirects, no proxies, size-limited responses.

**Lookup speed (local, 2026-11-10):** 3 rounds over BTC, ETH, NVDA and AAPL. Median 0.17 s, slowest 0.29 s, 0 errors. AAPL had no qualifying market.

## Caveats

- Recorded prices are not executable asks. Spreads, fees and slippage would make any "buy" result worse.
- One year of markets, dominated by sports and crypto. Category labels come from a keyword heuristic.
- Markets inside one event (for example, several price thresholds on one date) move together, so the effective sample is closer to the event count.
- This is a descriptive measurement, not a tuned model. It says nothing about Cofinance's own predictive ability.

## Reproduce

```text
set PYTHONPATH=.
python -m qualify.pm_lab collect --root data\pm_lab_20261110
python -m qualify.pm_lab history --root data\pm_lab_20261110 --shard 0 --shards 4   (repeat for shards 1 to 3)
python -m qualify.pm_lab evaluate --root data\pm_lab_20261110
python -m qualify.pm_lab publish --root data\pm_lab_20261110
```

`collect` refuses to overwrite a frozen protocol. Raw samples and histories stay in the ignored `data/` folder; only the compact table above is published.
