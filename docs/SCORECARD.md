# Evidence and experiment index

> [!WARNING]
> Experimental findings, not trading signals or financial advice. There is no single Cofinance accuracy percentage: source matching, answer quality, forecast ranking and runtime speed measure different things.

**Primary question: does structured research beat direct Q&A? Not established.** The [architecture comparison](METHOD.md#the-architecture-comparison) specifies the evidence, compute and grading controls. A [fixed document-route screen](benchmarks/DOCUMENT_DECISION_GATE.md) returned **NO-GO**: 3/12 jointly correct workflow answers vs 6/12 direct, on identical archived evidence. This applies to that route, not every task.

## Research quality and runtime

| What we tested | What happened | Interpretation |
| --- | --- | --- |
| [Source scope and checked decisions](benchmarks/SOURCE_SCOPE.md) | On 12 authored packets a checked card raised model verdicts from 4 to 8 correct, but neither model arm selected exactly the decisive sources with the correct verdict. Code alone did so on 7. | Separate evidence extraction, decision correctness and citation selection. Not a live model change. |
| [Grounding](benchmarks/grounding-2026-11-17.json) | In 72 replay cases, 393/393 final-answer numbers and 613/613 specialist-summary numbers traced to supplied values. Guards accepted 61 final answers; 11 used fallback. | Value provenance, not independently graded meaning. |
| [Replay and synthesis](benchmarks/REPLAY.md) | Bounding specialist output fixed observed truncations. Several news-narration variants still misattributed sources or overstated announcements. | Keep the Python news digest; a fluent summary need not be more faithful. |
| [Semantic surprise and quote review](benchmarks/REVIEWED_HEADLINES.md) | The semantic prompt got both sides right on 0/8 naturally worded challenge pairs. The explicit arithmetic checker applied to 0/213 cached titles. | A correct comparison on templated inputs does not imply comprehension or real-world coverage. |
| [Narrator comparison](benchmarks/NARRATOR-CANDIDATE.md), [GPU speed](benchmarks/SPEED.md), [Deep think](benchmarks/THINK_LAB.md) | A local GPU shortened calls. Optional thinking cost more time and tokens; it did not improve headline ranking in the development screen. | Runtime and qualification are separate from research-answer quality. AtomicChat is the default narrator; headline thinking stays off. |

## Finance-specific diagnostics

| Method and evidence | Finding and disposition |
| --- | --- |
| [Price-only historical forecast](benchmarks/HISTORICAL.md); [strategy baselines](benchmarks/LAB.md); [risk flags](benchmarks/RISK_LAB.md) | The price-only narrator called UP on all 125 cases. No direction rule passed the strategy screen. A volatility flag did not confirm across new assets. No general edge established. |
| [Per-headline judgments](benchmarks/REPLAY.md); [one-look window result](benchmarks/window-2026-11-09-headline-result.json) | AUC 0.681 on 24 of 40 sealed cases with known exits, across three dates. Passed the narrow frozen rule, not broad validation. The summed signal remains an experimental card. |
| [Cached-news simulation](benchmarks/LOCAL_NEWS_SIMULATION.md) | On reused outcomes, headline count ranked nearly as well as the model. Removing the strongest headline weakened ranking. |
| [Price regimes and news follow-through](benchmarks/PRICE_NEWS_METHODS.md) | Neither passed the adoption screen. At an exploratory equal budget, follow-through got 29/47 vs 26/47, but lost on the later subset. |
| [Quote-backed judgment](benchmarks/REVIEWED_HEADLINES.md) | On 96 reused cases, quote review got 35/62 calls correct vs 36/62 at matched coverage. No change to the live method. |
| [Crowd calibration](benchmarks/POLYMARKET.md); [Bitcoin vs crowd](benchmarks/BTC_VS_CROWD.md); [tuned baseline](benchmarks/BTC_BASELINE.md) | Crowd calibration describes market prices, not Cofinance. Tuning the Python model or adding its score to the crowd did not establish an improvement. |
| [Model readout](benchmarks/READOUT_CHECK.md); [Polymarket records](benchmarks/PM_REPLAY.md); [forward ledger](benchmarks/FORWARD.md) | No tested narrator readout passed the coherence checks. Existing ledgers preserve protocols and sealed records, not an ongoing schedule or a validated probability. |

## Reading the numbers

AUC measures ranking (0.5 is chance), not the fraction of calls correct. Check the sample, dates, coverage and abstention policy before comparing percentages. Brier measures probability error (lower is better). A reused replay and a fresh evaluation do not carry the same weight. Each report retains its original protocol, evidence and limits.

[Project scope](../README.md) | [Methods](METHOD.md)
