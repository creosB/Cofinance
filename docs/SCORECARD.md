# System scorecard

> [!WARNING]
> **Experimental measurements of a research tool.** Not a trading signal or financial advice. Cofinance never trades. Every number links to its method and result file.

One page, every measurement side by side. Direction skill is reported as **ranking AUC**: whether the system puts more weight on UP for the assets that then rose. 0.50 is no skill whatever the market did, so a constant UP call scores exactly 0.50. Accuracy against a lopsided window is reported only as context.

## Is the system grounded?

| Check | Result | Source |
| --- | --- | --- |
| Numbers in final answers traced to a tool value or served headline | 393 of 393 | [Grounding](benchmarks/REPLAY.md) |
| Numbers in Data Analyst and News Researcher summaries traced | 613 of 613 | [Result file](benchmarks/grounding-2026-11-17.json) |
| Final answers accepted by the guards (rest fall back to Python-built text) | 61 of 72 | [Replay v2](benchmarks/REPLAY.md) |
| Stock cases with fundamentals unavailable, answers inventing one | 63 cases, 0 untraced numbers | [Result file](benchmarks/grounding-2026-11-17.json) |
| Citation chips on a live NVDA question | 5 of 5 to the right field (2026-11-16) | live check |

## Is the pipeline healthy?

| Check | Before | After | Source |
| --- | --- | --- | --- |
| Data Analyst token-cap failures, 72 replay cases | 51 | 0 | [Replay v1 to v2](benchmarks/REPLAY.md) |
| Median seconds per full-team case, CPU | 38.7 | 28.9 | [Replay v1 to v2](benchmarks/REPLAY.md) |
| Screening a Decision change, 72 cases | about 35 minutes (full team) | 1 to 10 minutes (cached reports) | [Fast screening](benchmarks/REPLAY.md) |

News Researcher summaries passed the guards in only 17 of 72 cases, and accepted ones still misattributed sources. Since 2026-11-17 the News Researcher is a Python digest: the served headlines, quoted and grouped by their source (company announcements, shared links), with no model narration. Four prompt and guard variants were tried first; none met a rule written before its data. The Team Lead still narrates the final answer under the number, wording and risk guards.

## Does the decision have skill?

Dev window: nine overlapping origins (2026-10-27 to 2026-11-06), eight assets, 72 correlated cases, a falling market. Tuning here is expected; no claim rests on it.

| Decision technique | Ranking AUC | Outcome |
| --- | --- | --- |
| Team answer, one-word call (v1, v2) | 0.50 by construction (UP on every valid call) | no skill |
| Model label probabilities, Python call (prob-v1) | 0.511 | no skill |
| Rise and fall asked separately (pair-v1) | 0.540 | below the gate labels |
| **Model judges each headline, Python sums (headline-v1)** | **0.585**, interval 0.525 to 0.654 | **shortlisted** |
| Gate's own headline labels, context | 0.556 | reference |
| 20-session return, context | 0.406 | reference |

The shortlisted technique is sealed on the unseen window 2026-11-09 to 2026-11-13 and scored once after it matures. It also runs live as a labeled experimental line under chat answers: headlines are judged after each news collection with the same code as the screen, and Python sums the judgments of exactly the headlines that answer was served. It is not narrated and not confirmed. A second aggregation, the mean instead of the sum (dev AUC 0.666 versus 0.585, found after looking at dev outcomes), is registered on the same sealed judgments before any exit price existed, and is scored in the same single look: [addendum](benchmarks/window-2026-11-09-headline-mean-addendum.json). [Method and every candidate](benchmarks/REPLAY.md) · [Compact results](benchmarks/decision-screen-2026-11-17.json)

## Other measurements

| Benchmark | Result | Source |
| --- | --- | --- |
| Narrator speed, Vulkan GPU vs CPU | Decision call 0.56 s vs 5.3 s; headline labels 39 of 40 identical, AUC 0.574 vs 0.585 | [Speed](benchmarks/SPEED.md) |
| Price-only LFM3 forecast, 125 cases | UP every time, balanced accuracy 50% | [Historical](benchmarks/HISTORICAL.md) |
| Strategy lab, 2,826 cases, 2014 to 2026 | no direction rule passes; dual momentum cut dev drawdown 59% to 20% | [Lab](benchmarks/LAB.md) |
| Risk flag on new assets | not confirmed: 5 of 9 assets agreed, 6 needed | [Risk lab](benchmarks/RISK_LAB.md) |
| Polymarket crowd calibration | Brier 0.176, 0.168, 0.094 at 1, 7, 30 days; two cheap-outcome patterns not confirmed on a fresh sample | [Polymarket](benchmarks/POLYMARKET.md) |
| Bitcoin threshold odds, model vs crowd | crowd better a day out (Brier 0.1340 vs 0.1378) | [BTC vs crowd](benchmarks/BTC_VS_CROWD.md) |
| Forward weekly ledger | in progress; no claim before 26 matured batches | [Forward](benchmarks/FORWARD.md) |
| Polymarket replay, narrator probability vs crowd price | v3 recording since 2026-11-18, nothing settled yet. Record 1: 18 markets sealed before settlement; the narrator said YES at 0.99 or more on all 18 (crowd 0.28 to 0.61), and gave 1.99 in total to pairs of outcomes that cannot both happen. The readout is not yet shown to be a probability. No claim before 100 events in 20 clusters | [Polymarket replay](benchmarks/PM_REPLAY.md) · [ledger](benchmarks/pm-replay-ledger-v3.jsonl) |
| Python baseline for Bitcoin thresholds (price, volatility, horizon), 2026-11-19 | No claim holds. Chosen on the first half (Student-t, 4 df), scored once on the second: Brier 0.1399 vs crowd 0.1369, difference +0.0030 (-0.0016 to +0.0071); vs untuned -0.0006 (-0.0027 to +0.0015). Adding the price model to the crowd's price did not help (+0.0014, weight on the model about 0). The crowd stays the reference | [Baseline](benchmarks/BTC_BASELINE.md) · [result](benchmarks/btc-baseline-v1.json) |
| Readout coherence (can a probability be read from the model?), AtomicChat on GPU, 2026-11-19 | No. On 54 questions answered by its own price card or tied by logic, none of three readouts passes: the replay's first-token readout says about 0.996 YES to everything (above + below = 1.99), the JSON label ranks known facts at AUC 0.73 but half its ladder steps rise, the stated number is 55 on 26 of 54 (known-fact AUC 0.48). No model-read probability is used as a forecast | [Readout check](benchmarks/READOUT_CHECK.md) · [result](benchmarks/readout-check-v1.json) |
| Deep think (thinking mode), AtomicChat on GPU, 2026-11-19 | Chat answers: passes every smoke gate, but 2.5 s and 426 tokens per answer vs 0.39 s and 36. Headline judging: worse, AUC 0.508 vs 0.625 without thinking; near certain on 32 of 40 headlines vs 15. Headline judging stays thinking-off | [Deep think](benchmarks/THINK_LAB.md) · [result](benchmarks/think-lab-v1.json) |
| Narrator smoke comparison, CPU, 2026-11-09 | LFM3 1.7B, MiniCPM5-1B and 2B all passed 7 unique cases; MiniCPM5-1B faster, not better. AtomicChat compared separately | [Recorded scores](../frontend/src/lib/narrator-benchmark.json) · [AtomicChat](benchmarks/NARRATOR-CANDIDATE.md) |
| Headline materiality judge, 91 model-labeled rows | GLiNER 72.5% accuracy, AUC 0.790, kept. MiniCPM5-2B-Jev AUC 0.874 but compressed probabilities and about 48 s per row: not adopted. d1-2B failed to load | train split only, labels written by a model |

## What this page does not show

No independent window has been scored yet. Grounding is value matching with the same matcher the guards use, not an independent judge of meaning. No fresh install, long-running use or production readiness has been verified.

How these numbers are produced, and which habits carry over to other systems: [method note](METHOD.md).
