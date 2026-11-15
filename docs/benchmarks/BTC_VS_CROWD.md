# Bitcoin: a simple volatility model vs the Polymarket crowd

> [!WARNING]
> **Experimental result.** A retrospective comparison on resolved markets (Nov 2025 to Nov 2026). It is not a trading signal or financial advice, and Cofinance never trades.

## Question

Can a plain, untuned model beat the crowd on Polymarket's daily "Bitcoin above ___ on <date>?" markets? If even a blend of the two beat the crowd, Cofinance would have something to add. If not, the crowd's price is the better reference.

## Method (frozen before any price was downloaded)

- **Markets:** every daily "Bitcoin above" event from 2025-11-01 to 2026-11-08 with a clean settlement and at least $5,000 volume per threshold: 3,779 markets on 365 days (8 days had no event, 267 thresholds were below the volume floor).
- **Settlement:** Polymarket's own result (Binance BTC/USDT 1-minute close at 12:00 New York time).
- **Crowd:** the recorded CLOB price of Yes at the snapshot, at most one hour old.
- **Model:** driftless lognormal. Spot is Yahoo BTC-USD's hourly close at the snapshot, volatility is the standard deviation of the previous 720 hourly log returns, scaled to the hours left. Nothing was fitted.
- **Blend:** the average of crowd and model.
- **Snapshots:** 24 hours before settlement (primary) and 6 hours before (secondary).
- **Scoring:** Brier score and log loss. The primary set is "contested" markets, where the crowd or the model priced Yes between 5% and 95%; the rest are near-certain and say little. Intervals are 95% bootstrap intervals that resample whole days, because thresholds on one day move together.
- **Claim rule:** an arm beats the crowd only if its 24h contested interval is entirely below zero and it wins in both halves of the year.

## Results

| Snapshot | Set | Markets | Days | Crowd Brier | Model Brier | Blend Brier | Model minus crowd (95% interval) | Blend minus crowd (95% interval) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 24 h | contested | 856 | 309 | 0.1340 | 0.1378 | 0.1350 | +0.0038 (+0.0008 to +0.0069) | +0.0009 (-0.0006 to +0.0025) |
| 24 h | all | 3,362 | 316 | 0.0370 | 0.0380 | 0.0373 | +0.0010 (+0.0003 to +0.0018) | +0.0002 (-0.0001 to +0.0006) |
| 6 h | contested | 470 | 299 | 0.1555 | 0.1549 | 0.1538 | -0.0006 (-0.0058 to +0.0047) | -0.0017 (-0.0044 to +0.0009) |
| 6 h | all | 3,405 | 318 | 0.0246 | 0.0246 | 0.0244 | -0.0000 (-0.0007 to +0.0007) | -0.0002 (-0.0006 to +0.0001) |

Lower is better. At 24 h, the model was worse than the crowd in both halves (contested Brier 0.1347 vs 0.1306 and 0.1405 vs 0.1369). When the two disagreed by more than 10 points (90 markets), the model was closer to the outcome only 25 times.

**Verdict: the crowd wins.** A day out, the simple model is measurably worse than the crowd, and blending it in does not help. Six hours out the two are indistinguishable. No arm meets the claim rule. Cofinance should show the crowd price as the reference, not its own threshold probabilities.

## Checks before trusting the numbers

- **Errors:** 0 price-history errors over 3,779 markets. 32 markets had no crowd price 24 h out (no recorded price in the hour before the snapshot).
- **Missing spot:** 385 markets at 24 h had no usable spot or volatility because Yahoo's hourly series has gaps (one of 101 hours from 2025-11-17, five shorter). They fall in Nov and Dec 2025 (363), May and Jul 2026. The gaps come from the data provider, not from outcomes, but the first half of the year is thinner.
- **Did the model see the right price?** At settlement time the Yahoo price was on the same side of the threshold as Polymarket's result for 99.8% of 3,691 markets; 62 thresholds were within 0.2% of the price, where the two sources can disagree.
- **Repeatable:** both halves of the year point the same way.
- **Untuned on purpose:** this tests one plain model. A better volatility estimate (faster-reacting or fat-tailed) could do better; it would need its own frozen run, not a tweak to this one.

## Reproduce

```text
python -m qualify.btc_crowd collect  --root data/btc_crowd_20261116
python -m qualify.btc_crowd history  --root data/btc_crowd_20261116 --shard 0 --shards 4   (shards 1 to 3 as well)
python -m qualify.btc_crowd spot     --root data/btc_crowd_20261116
python -m qualify.btc_crowd evaluate --root data/btc_crowd_20261116
```

The compact report is [btc-vs-crowd.json](btc-vs-crowd.json). Raw markets, prices and spot data stay in the ignored `data/` folder.
