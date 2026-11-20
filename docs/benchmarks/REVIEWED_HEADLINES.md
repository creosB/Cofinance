# Reviewed headlines: what the model understood and what the market did

> [!WARNING]
> Experimental research, not investment advice. Understanding a reported surprise and predicting a five-session return are different tasks. No live behavior changes on either of these datasets.

The review architecture is inspired by [Open Code Review](https://github.com/alibaba/open-code-review): separate case selection, a narrowly scoped model judgment, and deterministic evidence checks. No third-party review software, hosted inference or external data feed is required. The code-review project's own benchmark is not evidence about financial forecasting.

## A. Does the judge understand a surprise?

The 152 invented headlines have an answer independent of subsequent returns: did the reported result beat, miss or match the stated prior expectation, or is there no valid comparison? UP means a favorable surprise here, not a price forecast. The existing five-session prompt is a diagnostic reference: it was not trained or instructed to abstain on every semantically unsupported comparison.

Eight metric families include revenue growth, churn, operating margin, expenses, EPS, per-share loss (magnitude), units and free cash flow. In 128 controlled cases, compare beat/miss pairs leaving reported values fixed, reverse clause order, and test equal values, missing expectations, wrong metric, wrong period, historical-only and contradictory expectations. The 24 separately phrased cases add unit changes, negative amounts, break-even, less formal wording and distracting baselines. These are author-written texts and labels, not a third-party holdout. There are only eight metric families, not 152 independent experiments.

Six methods share the same texts. (1) Existing price-direction prompt; (2) direct semantic judge; (3) Python-only explicit comparison; (4) local model selects source-clause indices, Python checks roles, metric, period and units and does signed decimal arithmetic; (5) Python comparison with direct semantic fallback; (6) model-selected and verified comparison with the same fallback. A fallback output is not verified evidence. Exact pair scores require **both** sides correct. Macro recall weights favorable, unfavorable and abstention classes equally; a model error stays in the denominator. Paired intervals resample metric families, not individual rewrites. Runtime: the pinned AtomicChat weights, temperature 0, thinking off; only the local managed llama-server is started. A Python audit hook allows numeric loopback, not remote connections; this is not OS-level isolation. Prompts receive no gold, case type or split.

Frozen before model calls: `2b5ca54`, protocol `f4dd80ce4de01e907fd934a5a78a193153311f8801a07ac0b6fe7097e3cb2eeb`. A title-only coverage audit counts how often the strict comparison can actually be grounded in the local historical cache; it does not treat rejected titles as unsound reporting.

A separate descriptive sensitivity check used all 213 unique cached asset-title pairs. Adding up to the first 600 characters of each available distinct stored snippet (101 of 213) still produced zero fully verified numeric comparisons. A short list of literal consensus/estimate phrases also matched zero. This does not prove that no article contained a surprise: the full articles were not fetched, and the checker is intentionally narrow.

## B. Does evidence checking improve real cached forecasts?

Separate protocol `33c20fe671f7af364c03be46e6c55f7ff356933713004241cb0fb9d16a449bb7`, code `42ebb07`, frozen before inspecting outputs. The model is **not** asked to forecast. It classifies direct operational, legal or capital events and returns a verbatim title phrase. Python checks the exact span, the explicit company/product aliases, and the output schema. It does not independently prove an event happened, a phrase is material or a brand association is unique. The old signed judgment gets weight 1 if the evidence is accepted and 0.5 otherwise; failures keep the row. No thresholds were optimized.

Controls: unmodified judgments, uniform shrinkage, and a pure name/product match without a reviewer. The same titles are also reviewed against an alternative asset whose aliases are absent. Quote acceptance on swaps is zero **by construction**; raw model positives, before the asset gate, are the diagnostic. Whole-story, within-asset weight shuffles preserve the number of exposures and repeated titles. At matched budgets the old and new methods make equally many calls per date from the same nonzero pool, using only scores for selection. Outcomes are joined after the reviews; all 96 market cases and their split have been inspected before. No resealing, no additional outcomes, no full-system or independent-window validation.

## Example: positive growth can be a miss

The direct semantic prompt answered UP to the fictional company reporting 10% sales growth against an 8% consensus, ABSTAIN to the same 10% against 12%, and ABSTAIN to growth compared only with last year's 8%. The key was UP, DOWN, ABSTAIN respectively. It got 11/24 challenge labels correct, including 2/8 unfavorable surprises; none of the eight matched challenge pairs had both sides right. The typed Python rule abstained on these free-form phrasings and the extra model review fell back to direct judgment in every challenge case. These are semantic failures against constructed labels, not mislabeled market returns.

## Assertion scope: a separate falsification test

A frozen 22-item stress test (`18a7b8ad`) kept the arithmetic intact and changed who made the claim, its accounting basis or whether it was still asserted. The first numeric verifier kept all 6 valid control directions but declined only 6 of 16 unsupported cases. It missed mismatched issuers/bases, a denial, a conditional and a subsequent retraction. Exact numeric agreement alone does not establish that the comparison is legitimate.

The isolated follow-up (`qualify/surprise_scope.py`, `ee82385`) accepts a positive grammar for **every clause**, not a growing blacklist of warning words. It binds the issuer and checks roles, period, measure, unit and accounting basis; unrecognized scope abstains with no model override. This repair kept 6/6 valid directions and declined 16/16 unsupported cases **on the same fixtures that exposed the problem**. Five new tests include 256 seeded comparisons in both clause orders and a foreign-issuer transformation; 5/5 deliberate breaks were caught. Neither the repair nor its regression score was substituted into the frozen model comparison. The tighter checker still verified 0/213 real cached titles. This is a correctness fix for supported language, not a market accuracy or general-language claim.

[Compact results](reviewed-headlines-v1.json)

## Results and decision

Local AtomicChat on vulkan, thinking off. Surprise test: 456 calls, errors by arm `{}`; materiality replay: 104 calls, 0 original-title errors and 0 swap errors. All errors count. No hosted inference.

| Semantic method | Controlled macro recall | Both correct / 16 | Challenge macro recall | Both correct / 8 | Challenge abstention recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| Live price prompt (different target) | 0.486 | 1 | 0.333 | 0 | 0.375 |
| Direct surprise prompt | 0.625 | 5 | 0.458 | 0 | 0.750 |
| Python rules only | 1.000 | 16 | 0.333 | 0 | 1.000 |
| Model-selected evidence only | 0.604 | 6 | 0.333 | 0 | 1.000 |
| Rules + semantic fallback | 0.938 | 16 | 0.458 | 0 | 0.750 |
| Reviewed evidence + semantic fallback | 0.729 | 9 | 0.458 | 0 | 0.750 |

Semantic router gate: **False**. Additional-reviewer gate: **False**. On the 24 challenge headlines, reviewed router minus rules + direct fallback macro recall: +0.000 [+0.000 to +0.000]. Versus the direct prompt: +0.000 [+0.000 to +0.000]. Reviewer routing by split: `{'challenge': {'semantic_fallback': 24}, 'controlled': {'semantic_fallback': 113, 'verified': 15}}`. A gain from the redefined semantic task is not a gain in market prediction.

Strict numeric-comparison coverage: **0/213** unique gated asset-title pairs and **0/52** in the actual 96-case replay. This does not measure how many real headlines are economically meaningful, only how many this rule can verify from the title.

| Real market replay | AUC | Full-coverage sign balanced accuracy | Calls at live threshold | All-case balanced accuracy, abstentions miss |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current sum | 0.645 | 0.591 | 62/96 | 0.435 |
| Uniform 0.75 | 0.645 | 0.591 | 59/96 | 0.413 |
| Name/product match | 0.645 | 0.591 | 62/96 | 0.435 |
| Quote + name review | 0.651 | 0.591 | 50/96 | 0.364 |

Review minus current AUC +0.006, paired block interval [-0.017, +0.043]; versus name-only +0.006 [-0.017, +0.043]. Dev AUC current/review 0.625/0.638; reused later subset 0.681/0.631. At equal daily call budgets: **35/62 correct for review vs 36/62 for current**; paired called-accuracy difference -0.016 [-0.053 to +0.000]. Gate: **False**. Live method unchanged.

True asset/title assignments: 13/52 raw model positives, 13 accepted after verification. Swapped targets: 2/52 raw positives; the name gate mechanically rejects all swaps. 71.9% of 2,000 within-asset weight permutations reached or exceeded the realized review AUC. No independent materiality labels or price provider. Forecast panel: 96 reused cases, 74 with headlines, 12 overlapping origins.

Verification: 12 semantic/guard tests and 7 quote/coverage tests; 14/14 and 9/9 planted breaks caught in isolated copies, then restored tests passed. Independent readback checked all split/arm semantic counts and score pairs, all accepted quotes, original scores, paired-budget correct counts and raw pairwise AUCs. No production code changed.

## Reproduction

Requires the original private local cache and installed matching weights; no download step. With a new folder per replication:

```console
python -B -m qualify.surprise_lab freeze --root data/surprise_replica
python -B -m qualify.surprise_lab run --root data/surprise_replica
python -B -m qualify.headline_evidence_lab freeze --root data/headline_evidence_replica
python -B -m qualify.headline_evidence_lab run --root data/headline_evidence_replica
```

Both protocols hash their inputs, rules and implementation. A started run cannot be overwritten. Reproduction on the same texts is not new evidence.
