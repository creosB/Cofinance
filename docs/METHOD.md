# How Cofinance works and how to test it

> [!WARNING]
> Experimental research software. The goal is a better evidence-supported answer, not a guaranteed forecast. [Results and limits](SCORECARD.md).

## The intended loop and the implementation today

The intended engine gathers evidence, asks what supports or contradicts a conclusion, and states what remains unknown. Finance supplies the first tools and questions. Transferring the approach to another field would require different tools, evidence standards, safeguards and evaluation.

| Stage | What runs today | What that does not establish |
| --- | --- | --- |
| Collection | Explicit collection stores gated headlines. Price and fundamental tools return provider data or unavailable fields. PDF retrieval selects passages with page references. | That the information set is complete, current or independently corroborated. |
| Routing and specialists | GLiNER chooses relevant finance routes. Python prepares evidence; a small local narrator writes the Data Analyst report. The news specialist quotes source-grouped headlines directly. | That several roles are independent models or independent sources. |
| Synthesis | News-only questions use the digest. Other supported paths can use a Team Lead to combine specialist reports. Guards check numbers, wording, risk claims and direction; retries and fallbacks appear in the trace. | That all supported-looking claims are true. A revision after a guard failure is not a critic testing a competing explanation. |

## The architecture comparison

**Design, not a reported win:** compare a strong direct answer, the current pipeline, and a research-only variant with an explicit evidence critic. Use the same local model, question, frozen evidence and comparable total generation budget. Freeze cases, expected facts and a grading rubric before opening outputs. Include incomplete and conflicting packets where withholding judgment is correct. Keep answer identity hidden during grading.

Measure whether claims are supported, important counterevidence is acknowledged, and the conclusion stays within the evidence. Report missing answers, abstentions, actual tokens and latency separately. An improvement over direct Q&A that disappears against a budget-matched direct baseline is not evidence for an architectural advantage.

## How to read existing experiments

- **Grounding is not truth.** The [grounding check](benchmarks/grounding-2026-11-17.json) matches emitted numbers against supplied values. It uses the runtime matcher. Reading the wrong field can still pass.
- **Historical replay is not forward confirmation.** Use only inputs available by the decision date. State timestamp and revision limits. Frozen code on outcomes already inspected is development evidence, not a new holdout. The [sealed headline window](benchmarks/window-2026-11-09-headline-result.json) was scored once on 24 of 40 cases with exits available; the other 16 were not subsequently scored. Three decision dates cannot establish general skill.
- **Match the metric to the question.** Use blinded factual grading for research answers, pair correctness for semantic contrasts, ranking AUC and balanced accuracy for direction, and paired Brier differences for probabilities. Compare the same cases and call coverage, count failures, and account for shared dates or events when estimating uncertainty.
- **Change one stage at a time.** Reuse fixed upstream reports for cheap screening. Compare against a simple non-model rule, not just the previous prompt. Timing a stage or passing a smoke test is not an end-to-end quality result.
- **Keep receipts.** Store case definitions, model/runtime identity and compact results. Distinguish predeclared rules from exploratory follow-ups. Test critical guards by deliberately breaking them and confirming the restored check passes. Where the evidence depends on private caches or local weights, say so rather than promising exact reproduction from public files alone.

[Start here](../README.md) | [Experiment index](SCORECARD.md)
