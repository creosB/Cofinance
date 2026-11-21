# Announced or actually rolled out?

> [!WARNING]
> Experimental development screen, not a field-accuracy estimate. Claims are about what the supplied notices establish, not confirmation of the outside world. [Compact results](rollout-stage-v1.json).

## What ran

A deterministic, scope-aware rollout checker vs one strong direct AtomicChat answer. Both saw the same three excerpts, product and target scope per question. Publication, preview, pre-order or a future plan alone does not establish public availability; a definitive negative needs explicit nonavailability. The code looks for asserted events linked to the product and channel, treats inconsistent notices as unresolved, and recognizes a narrow, explicitly linked correction. It selects exact source character spans itself. It is a deliberately narrow English grammar, not a general event extractor. The direct model was asked to consider all evidence and contrary reports and supply only decisive source IDs; no exemplars, retries or narration. Both were scored automatically against a prewritten key.

Twelve fresh questions used 15 cached issuer-newsroom URLs (8 also appeared in the previous document gate). Twelve authored contrasts cover rollout, restricted pilots, regional scope, an announcement that remains under review, supersession and unresolved conflicts. Source order was shuffled before inference. Source selectors and gold were frozen before implementing the rules, but the rule author knew the domain, texts and labeling design. These are not independently authored blind cases. Archive evidence contains no explicitly negative gold cases; explicit nonavailability is measured on constructed controls. Many packets share exhibits or product families. A clean score here is a reason to seek different wording, not to enable the checker in the app.

| Measure (24 questions) | Rules, no model | Direct AtomicChat |
| --- | ---: | ---: |
| Correct verdict + exact decisive references | 24 | 6 |
| Correct verdict alone | 24 | 17 |
| Correct references alone | 24 | 9 |
| False confirmations (LIVE when not established/not live) | 0 | 1 |
| Missed answerable questions (unknown or invalid when determinate) | 0 | 6 |
| Withheld a definite answer | 11 | 16 |

Joint scores: 12/12 vs 4/12 on archived questions and 12/12 vs 2/12 on constructed contrasts. The 24 rule results include 11 warranted insufficient-evidence decisions, not 24 confirmed launches. One example: with a launch bulletin and a second bulletin stating the launch date was under review, direct Q&A called the product live even while citing both exhibits; the rule kept the verdict unresolved and cited both. Direct Q&A also lost strict joint points when its verdict was correct but it included a nondecisive source. This is not the same as fabricating a source.

Paired joint wins/ties/losses 18/6/0; descriptive interval on the code-minus-direct rate difference +0.615 to +0.870 using six split/family clusters. Inference: 24 local calls, thinking off on Vulkan, 7,581 prompt and 764 completion tokens, median 3.79 seconds; no invalid outputs. Both received the same text, but compute was not matched (rules need no model). Independent arithmetic cross-check of all 48 arm rows and 28 source spans; output invariant to all 144 exhibit-order permutations for the rule. No claim of model order-invariance. Twelve deliberately broken rules and scoring checks caught, restored tests clean.

**Decision:** the frozen threshold advanced the method only to an independent retest; live behavior unchanged. Do not train on this score or treat announcement/time/scope wording outside the grammar as established. The direct baseline remains necessary when the rules cannot interpret the source.

## Fresh wording transfer: no integration

The [transfer runner](../../qualify/rollout_transfer.py), commit `0ffdef3`, froze the unchanged verifier and direct prompt, the base-model author instructions and selection criteria before generating prose (protocol `edd62ff3`). A separate installed base narrator produced 12 fictional, three-notice packets without seeing the rules or first-screen outputs. A reviewer read the actual text before predicting or unmasking either arm; 10 packets were usable (3 live, 3 explicitly not live, 4 insufficient). Two were excluded up front: two equally sufficient decisive negative citations were possible, or a direct-purchase link was ambiguous about actual access. No re-generation, grammar patch, or selection based on model output. The synthetic writer shares the generator model family; reviewer was also involved in design. This is a limited transfer test, not independent customer validation.

| Frozen transfer metric (10 usable) | Rules | Direct AtomicChat |
| --- | ---: | ---: |
| Correct verdict + decisive citations | 0 | 1 |
| Correct verdict alone | 4 | 6 |
| Exact references alone | 6 | 1 |
| False rollout confirmations | 0 | 0 |
| Missed answerable questions | 6 | 4 |
| Withheld a definite answer | 10 | 8 |

All six answerable cases had the right source IDs selected by the rules, yet all six were labeled insufficient. For example, the notice that users could download and use the desktop app that day was present and cited, but did not match a supported release construction. This is a parser limitation, not evidence against public access. On unresolved packets, redundant or missing context also cost exact-citation credit. Joint paired wins/ties/losses 0/9/1; descriptive paired interval -0.30 to 0.00. No false positive from a rule that never confirms anything is not a safety win. Twenty item scores, costs, 11 exact source spans, and 60 order permutations cross-checked. Nine transfer-gate faults caught, restored clean. Full backend: 830 tests run, 21 skipped, OK.

**Disposition: do not integrate these rules as an open-prose verdict engine.** Keep their ability to suggest source spans as a separately testable component. In any future proposal distinguish unsupported wording from evidence of nonrelease. Do not tune the grammar on this transfer set and present the result as a fresh win.

## Reproduction

Protocol `b0595650`, archived label hash `28c40ed3`, commit `5408c0c`. With the original local cache and pinned weights installed, seal a fresh root using `qualify.rollout_packets.freeze_labels(root)`, then `python -B -m qualify.rollout_screen freeze <root>` and `run <root>`. The original receipts are write-once. The public repo does not contain the private cache or local model.
