# Polymarket replay: team probability versus the crowd price

> [!WARNING]
> **Experimental historical record; no outcome score is reported here.** Forecasts are recorded for measurement only. Crowd prices are crowd opinion, not facts. Not a trading signal or financial advice. Cofinance never trades: no orders, keys or wallets.

## Question

Given an open Polymarket question about one of our assets, plus the local news digest and price facts at time T, does the narrator's probability for the first listed outcome beat the crowd's price at T? The crowd is the bar because it is the strongest reference we have: the [calibration study](POLYMARKET.md) found it fairly calibrated a day and a week out, and on daily Bitcoin thresholds it [beat a plain volatility model](BTC_VS_CROWD.md).

## Why forward

The live news store expires old rows and no point-in-time news archive exists, so a replay over past markets would quietly feed the model later news. Markets are therefore picked and forecasts sealed at T, published before settlement, and scored afterwards. This reduces hindsight risk; it does not protect against every form of leakage.

## Rules (v3, frozen before its first record)

- **Listing:** Gamma events under the tags bitcoin, ethereum and stocks, active, ending 1 to 8 days after T, pages of 20, deduplicated by event; any truncation is recorded.
- **Selection:** the event title names exactly one of AAPL, NVDA, MSFT, BTC, ETH; sports tags dropped; event volume at least $10k; one market per event, the one priced closest to 0.5, inside 0.02 to 0.98; at most 40 per record (seeded sample above that).
- **Inputs:** the question, first outcome and end time; last completed daily close, 20-session return and one-year high and low; the News Researcher's Python digest of accepted local headlines for the asset.
- **Arms:** crowd price; narrator blind (no crowd price); narrator shown the crowd price; coin at 0.5. The narrator is the default AtomicChat LFM3 1.7B, pinned by SHA-256, temperature 0, one output token.
- **Readout:** among the first token's top 20 alternatives, the probability mass of tokens that can start YES (Y, YE, YES) over the mass that can start YES or NO. No such token is a failure, scored 0.5 and counted.
- **Cadence:** records at least five days apart; an event is never recorded twice.
- **Outcome:** the first outcome's clean settlement within seven days of the scheduled end; anything else is excluded and counted.
- **Verdict:** paired Brier difference, blind arm minus crowd, with a 95% interval from 2,000 bootstrap resamples of (record, asset) clusters, because same-asset markets in one record share one price path. "Team beats crowd" needs at least 100 scored events in at least 20 clusters, a failure rate of at most 10%, a negative mean and an interval entirely below zero. Fewer events: not scored yet, whatever the numbers say.

The $10k event-volume floor was chosen after counting open listings by volume on the day of the freeze (52 eligible-looking events at $0, 30 at $1k, 18 at $10k, 8 at $50k). That probe read listings and volumes only, no outcomes.

[Protocol v3](pm-replay-protocol-v3.json) (sha256 `795bc41f57f1ad96dbca97cd7534b52c337b90a4cbd72c64114581ef92863013`) · [Public ledger v3](pm-replay-ledger-v3.jsonl)

## Versions

| Version | What changed | Records |
| --- | --- | --- |
| [v1](pm-replay-protocol.json) | First rules: general market listing, whole-token YES/NO readout | none: a probe on made-up questions showed AtomicChat writes YES as "Y" + "ES", so this readout would have ignored most of the YES mass |
| [v2](pm-replay-protocol-v2.json) | Readout counts first tokens that can start an answer; nothing else changed | 1 record, 1 market: the general listing barely reaches our assets ([ledger](pm-replay-ledger.jsonl)) |
| v3 | Events listed by asset tag, event-volume floor, cluster verdict | first record sealed 2026-11-18 |

Each version lives in its own folder with its own code path; a sealed record is always scored under the rules it was recorded with.

## Record 1 (v3, sealed 2026-11-18 16:08 UTC)

110 events listed, no truncation, 18 selected: 10 BTC, 7 ETH, 1 NVDA. Zero readout failures and zero request errors; price facts present for all 18; **news for 1 of 18** (the local store has company newsrooms for Apple and NVIDIA and little crypto coverage).

| Arm | Lowest | Median | Highest |
| --- | --- | --- | --- |
| Crowd | 0.280 | 0.370 | 0.610 |
| Narrator, blind | 0.994 | 0.996 | 0.997 |
| Narrator, shown the crowd | 0.983 | 0.991 | 0.996 |

No outcome is known yet, but one thing is already visible without any: **the probabilities are not coherent.** On November 19, 20 and 21 the record holds "Bitcoin above $64,000" and "Bitcoin between $62,000 and $64,000" with the same end time. Both cannot happen, so their probabilities should sum to at most 1. The blind narrator's sum is 1.99 on all three days; the crowd's is 0.73, 0.67 and 0.65. Showing the crowd price moved the narrator toward it on 15 of 18 markets, by a median of 0.0055.

What this shows: the first-token YES share measures how the model starts its reply, and on these inputs that is almost always "Y". It has **not been shown to be an event probability**. What it does not show: whether the scores rank outcomes at all, because nothing has settled. Brier and AUC are computed only after settlement, and the frozen verdict waits for 100 events.

## Subsequent findings

No new records are planned. The original rules still govern the sealed record. The separate [coherence test](READOUT_CHECK.md) found no reliable probability readout. Do not interpret the narrator score as a calibrated probability.

## Reproduce

```text
python -m qualify.pm_replay3 freeze --root data/pm_replay3 --publish docs/benchmarks/pm-replay-protocol-v3.json
python -m qualify.pm_replay3 record --root data/pm_replay3 --publish docs/benchmarks/pm-replay-ledger-v3.jsonl
python -m qualify.pm_replay3 score  --root data/pm_replay3
```

`record` refuses a changed protocol, a record under five days after the last one, and narrator weights that differ from the frozen SHA-256. The private ledger (every input, every top-20 logprob, hash-chained) stays in the ignored `data/` folder; the public ledger line carries each market's three probabilities and the ledger hash.

## Limits

- One record so far, mostly crypto, with almost no news in the inputs. Markets of one asset in one record are strongly correlated.
- The narrator answers with one token at temperature 0; this is a single model call over a fact card, not the full research team.
- Daily closes come from Yahoo; Polymarket settles on its own stated source. No second-source check yet.
- Recorded crowd prices are not executable prices; spreads and fees are ignored.
