# Forward-recorded forecast benchmark

> [!WARNING]
> **Experimental, in progress.** Forecasts are recorded for measurement only. Not the production research pipeline, not a trading signal and not financial advice. No conclusion is allowed before 26 matured weekly batches (about six months).

## Why forward

The [historical benchmark](HISTORICAL.md) cannot rule out revised data or model memorization, and it consumed its period: tuning on it would only fit the past. A forward test cannot leak. Each forecast is sealed before its entry price exists, so the outcome is unknowable when the model answers. It also lets the model see real tickers, dates and collected news, which the anonymized historical arm could not.

## Arms, frozen on the first record

| Arm | Input | Rule |
| --- | --- | --- |
| `lfm3_news` | Ticker, past-price features and up to 5 accepted news signals (alert/digest, published within 7 days) | LFM3 1.7B, temperature 0, UP/DOWN/ABSTAIN |
| `lfm3_price` | Same anonymized price features and prompt as the historical run | LFM3 1.7B, out-of-sample continuation |
| `news_signal` | The same news rows | Bullish minus bearish classifier count; tie or none abstains |
| `momentum20` | Past 20-session return | Sign; zero counts as UP |
| `always_up` | Nothing | UP |

Assets: AAPL, NVDA, MSFT, BTC-USD, ETH-USD. News coverage is currently Apple and NVIDIA official newsrooms only, so the news arms mostly abstain or run price-only for the other three. That is a measured limitation, not a hidden one.

The protocol (prompts, arms, windows, rules) is written to `protocol.json` on the first record. A later run with any change is refused: a new idea needs a new `--root` and starts from zero.

## Timing and integrity

- **Inputs:** completed daily bars only. A bar dated the current UTC day is excluded even if the provider returns it.
- **Entry:** the first provider daily bar that opens strictly after the seal. Recorded Monday before 14:30 UTC (17:30 Istanbul), shares enter at Monday's US open and crypto at Tuesday 00:00 UTC.
- **Exit:** each asset's daily close dated on the fifth SPY session after the seal. Crypto therefore holds slightly fewer hours than shares.
- **One open batch:** a new batch is refused until the previous batch's five sessions complete. No overlapping holdings.
- **Ledger:** `ledger.jsonl` is append-only and hash-chained (`prev_sha256`), with `head.json` holding the last hash. Scoring refuses a broken chain, a changed protocol or a missing head.
- **Public timestamp:** each batch appends its labels and ledger hash to [forward-ledger.jsonl](forward-ledger.jsonl), committed to GitHub before entry. The commit time is independent evidence the forecast existed first.
- **Errors and abstentions** stay in the all-case denominator. Missing outcomes are listed, not dropped silently.

## Weekly routine

1. Monday morning, before 17:30 Istanbul: collect news in the dashboard.
2. `python -m qualify.forward record --root data/forward_benchmark --publish docs/benchmarks/forward-ledger.jsonl`
3. Commit and push `docs/benchmarks/forward-ledger.jsonl` before the US open.
4. Any time after Friday's session: `python -m qualify.forward score --root data/forward_benchmark`

No scheduler runs this. Missed weeks are simply missing, not backfilled.

## Reading the score

`report.json` lists matured and pending batches, per-arm accuracy, coverage, balanced accuracy, errors, abstentions and an illustrative long/cash return (10 bps per side, no shorting, cash earns zero). `claim_allowed` stays false until 26 batches mature. Even then, five correlated assets over six months is a small sample: beating momentum and always-UP is necessary for a usefulness claim, not proof of a durable edge.
