# Does the document workflow beat direct Q&A on research decisions?

> [!WARNING]
> Experimental. A one-look decision about further research, not a product-accuracy claim, financial advice, or an evaluation of all of Cofinance. See [compact result](document-decision-gate-v1.json).

## What was compared

Twelve previously unused analyst-style questions over archived issuer newsroom excerpts, grouped into rollout, scope, deployment and reporting. Examples: distinguish pre-order from retail availability; decide whether a supported-title announcement establishes eligibility for a particular broadcast; distinguish a planned infrastructure expansion from commissioned capacity; and separate fiscal period end from a scheduled earnings call. There are four supported claims, four contradicted claims and four cases where the appropriate conclusion is not established. These are plausible tasks authored for this screen, not a list submitted by independent users. Nineteen distinct cached sources supply 36 exhibits. Some notices recur across questions and were used in earlier finance research. Nothing was downloaded or certified as true of the outside world. The question is what the supplied sources support.

The pinned local AtomicChat model, thinking off on Vulkan, received the same document text in both methods. A strong direct prompt asks for a short answer and contrary evidence; the other method uses the actual document reader, production Lead, normal checks and fallback, with routing fixed to documents. Both get the same request to cite only the decisive exhibits. Conditional retries are part of the workflow. Shared budget: at most 512 available completion tokens and 4096 actual input-plus-output tokens per packet and method; record actual usage and latency, not just ceilings. Inference order is randomized within question. No live code or qualification file is changed.

## What counted

Freeze before generation: `052f547`, protocol `e59e299a`. A joint success needs (1) a warranted decision or justified uncertainty, (2) materially supported reasoning that resolves the question, and (3) the exact decisive source set. Merely printing retrieved exhibits is not a decision. Classify a refusal on an answerable case as unnecessary; count a wrong definite answer separately. A reader grades anonymized outputs before opening method identities. Six calibration examples check the reading rubric. This is a single assistant reviewer involved in designing the cases; controls check consistency, not independence. Recognizable answer styles can weaken masking. An independent blank grading sheet is retained locally.

GO requires at least two more joint successes than direct, positive net differences in at least two of four families, no more confident mistakes or unnecessary abstentions, and both total tokens and median latency at most 1.5 times direct. A valid tie or loss is NO-GO for expanding debate on this path. A broken experiment, a budget violation or a failed calibration is inconclusive. A selection decision is not proof of general superiority or inferiority.

## Sealed result

**NO-GO under the frozen research rule.** No production behavior changed.

| Measure | Direct Q&A | Current document workflow |
| --- | ---: | ---: |
| Joint success (out of 12) | 6 | 3 |
| Correct verdict | 9 | 3 |
| Grounded reasoning | 7 | 3 |
| Exact decisive references | 10 | 3 |
| Wrong definite verdict | 3 | 0 |
| Unnecessary abstention/non-answer | 0 | 5 |
| No discernible decision | 0 | 9 |
| Fallback to excerpts | 0 | 9 |
| Truncated completions | 0 | 0 |
| Method errors | 0 | 0 |
| Prompt tokens, total | 4251 | 12530 |
| Completion tokens, total | 2366 | 2659 |
| Median seconds per question | 16.16 | 22.45 |

On joint success the workflow won 1, tied 7, and lost 4 paired questions. Family net changes: deployment +0, reporting +1, rollout -2, scope -2.
Descriptive four-family bootstrap interval for the change in joint success rate: -0.67 to +0.17. Only four related clusters; this is not an independent generalization result.
Calibration agreement (out of 6 each): verdict 6, rationale 6, source set 6. Same-reader controls, not evidence of independent grading. Actual total tokens workflow/direct: 15189/6617. Recomputed all totals and per-family differences from the stored outputs and masked grades.

Regression: 782 backend tests run, 21 skipped, OK. Seven focused tests. Twelve of twelve planted scoring and blinding faults caught in shadow copies after strengthening one test. No new dependency, model or source.

The workflow's one paired win was fiscal-period ordering: it correctly placed September 26 before October 25, while direct Q&A quoted both dates and then reversed their order. But all nine workflow fallbacks reproduced the excerpts without stating a conclusion. Five of those questions were answerable; on the other four the appropriate answer was an explained uncertainty, not a bare exhibit list. Direct Q&A made three wrong definite decisions, so it is a baseline, not an unattended decision-maker. A post hoc check ignoring exact citations still gives direct 7/12 versus current 3/12 for correct decisions with supported reasoning. The cost ratio was 2.30 for total tokens and 1.39 for median time. Do not add debate stages to this document route on this evidence. Keep deterministic evidence verification as a separate, narrower research direction.

## Reproduction

`python -B -m qualify.decision_gate freeze --root data/decision_gate_new` freezes the local cache and code; use `run` on the same new root to generate the answers. Record masked grades in `grades.json` using the documented stance, grounding and citations rubric before running `score`. These stages do not overwrite previous results. Reproduction needs the existing matching news snapshot and local model/runtime. New inputs are a new experiment. No external service is required by the runner.
