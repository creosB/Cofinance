# Evidence and experiment index

> [!WARNING]
> Experimental findings, not trading signals or financial advice. There is no single Cofinance accuracy percentage: source matching, answer quality, forecast ranking and runtime speed measure different things.

**Primary question: does structured research beat direct Q&A? Not established.** The [architecture comparison](METHOD.md#the-architecture-comparison) specifies the evidence, compute and grading controls. A [fixed document-route screen](benchmarks/DOCUMENT_DECISION_GATE.md) returned **NO-GO**: 3/12 jointly correct workflow answers vs 6/12 direct, on identical archived evidence. This applies to that route, not every task.

## Research quality and runtime

| What we tested | What happened | Interpretation |
| --- | --- | --- |
| [Keyword vs Hy-Memory hybrid recall (30 follow-ups)](benchmarks/MEMORY_RECALL.md) | On 30 authored, subagent-reviewed follow-ups across 6 sessions (isolated temp store), hybrid (`Qwen3-Embedding-0.6B`) matched 10/10 lexical, recovered 11/15 zero-overlap paraphrases (vs 0/15 FTS5; +0.44 R@2, CI `[+0.24, +0.64]`), held 5/5 clean negative abstentions, 0 cross-session/forget leaks, and 118.5 ms p50. | Confirms local hybrid recall beats FTS5 on paraphrases without leaking across sessions or after forget. Still needs 30 real user follow-ups in `chat_v2.db` (`0/30` today) to measure real-world mix. |
| [LiquidAI d1-2B vs GLiNER headline judge (CPU)](benchmarks/D1_JUDGE.md) | On 24 authored headlines, `d1-2B` (`float32` CPU) scored 1.000 material AUC (vs 0.854), 0.880 urgent AUC (vs 0.500), 8/8 traps (vs 5/8), 0 errors, and 5.37 s p50 (vs 0.65 s). Paired 95% CIs still touch/cross zero. | Frozen gate `REJECT_TOO_SLOW_ON_CPU` (exceeded 5.0 s CPU cap); do not integrate yet. Worth testing on human-verified real headlines and quantized ONNX/GPU. |
| [Linking unnamed restrictions to claims](benchmarks/RESTRICTION_LINK.md) | On 24 fresh matched notices with titles kept as read-only context, two-stage linking hit 10/24 malformed replies, 0/12 restrictions, and 5/12 noisy controls. Direct hit 7/24 malformed and linked every valid two-claim restriction (4/4) to both notices. | Frozen gate INCONCLUSIVE; do not integrate. Keep restrictions grouped inside their source notice rather than cross-linking with the 1.7B model. |
| [Full-packet counterevidence search](benchmarks/COUNTEREVIDENCE.md) | On 20 new authored packets, a focused second reader found 8/12 caveats vs 7/12 direct. It returned 23 extra spans and cited irrelevant material on 8/11 controls. | Did not meet the precision or control-noise requirements. No integration, filtering or automatic verdict. |
| [Source selection without a verdict](benchmarks/SOURCE_SELECTION.md) | Frozen rules returned the exact minimal spans on 12/18 packets, but missed all four designated counterevidence passages. In a same-case formatting follow-up, direct selection got 5/18 exact and found 3/4 such passages. | Useful as a research pointer, not an evidence filter or completeness check. No integration. |
| [Announced vs actually rolled out](benchmarks/ROLLOUT_STAGE.md) | Rules led 24/24 vs 6/24 on the development screen, but on 10 usable separately worded packets got 0/10 vs 1/10 for direct on verdict plus decisive sources. The rules withheld on all six answerable questions despite selecting the right evidence. | No integration as a general rollout judge. Keep source selection separate from interpreting the claim. |
| [Packet coverage](benchmarks/PACKET_COVERAGE.md) | On 12 authored four-page packets, treating the top three hits as complete yielded 6 unsupported verified verdicts. Checking the full supplied packet got 12/12 expected states with required sources, including 4 withheld judgments. | Verify the stated evidence scope; this was not an end-to-end product or model-accuracy test. |
| [Evidence receipts and blockers](benchmarks/EVIDENCE_RECEIPTS.md) | All four status types matched on 36 variants of 12 reused packets; 35/36 also returned the keyed receipts. New PDF uploads now retain bounded extracted text before normalization; old attachments remain search-only unless original evidence exists. | Better source provenance, not proof that arbitrary tables or diagrams are understood. |
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
