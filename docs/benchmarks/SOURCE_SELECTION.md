# Select evidence before drawing a conclusion

> [!WARNING]
> Experimental: this is a source-span task, not answer accuracy, market skill, or proof that a notice is true. [Compact results](source-selection-v1.json).

## What was compared

Eighteen new packets: eight questions on archived issuer text and ten authored contrast questions. Fourteen unique archived issuer URLs did not appear in the two preceding document/rollout screens; the broader cache was already used in research. Each method saw the same entire three-document packet, subject, scope and question. The candidate is the previous rollout checker, unchanged: discard its verdict and evaluate only its exact evidence spans. The lexical reference picks a single span using question-term overlap; it is incapable of covering two required facets. The strong direct model selects sentence/title IDs only. A fourth arm shows the same model untrusted code highlights AND all the original evidence. No model is asked to issue a rollout verdict. Exact citation strings are sliced by code, not written by the model.

Labels and advancement rules were frozen before the first model calls. Credit requires exactly one acceptable witness per required fact with no extra spans. Equivalent text is allowed where keyed. Count all required facts, extra spans, omissions, empty answers on nonempty cases, and missed counterevidence separately. Four contrast packets require retaining a conflicting, qualifying or withdrawing passage. One no-evidence packet permits a valid empty answer. Invalid responses remain in the denominator and cannot earn a correct abstention.

## First, frozen ID-list run

| Selector | Exact span set | Issuer /8 | Contrast /10 | Qualifying passages found /4 | Valid /18 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen rules | 12/18 | 7 | 5 | 0 | 18 |
| Lexical top 1 | 8/18 | 5 | 3 | 0 | 18 |
| Direct AtomicChat | 5/18 | 1 | 4 | 4 | 15 |
| AtomicChat + untrusted highlights | 6/18 | 1 | 5 | 4 | 16 |

The prespecified rule marked both comparisons INCONCLUSIVE: three direct replies and two hinted replies repeated span IDs, exceeding the one-invalid-response limit. No cases were retried, dropped or silently deduplicated. Those endpoints are descriptive, not a win.

## Boolean-format sensitivity on the same questions

| Selector | Exact span set | Issuer /8 | Contrast /10 | Qualifying passages found /4 | Valid /18 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen rules | 12/18 | 7 | 5 | 0 | 18 |
| Lexical top 1 | 8/18 | 5 | 3 | 0 | 18 |
| Direct AtomicChat | 5/18 | 1 | 4 | 3 | 18 |
| AtomicChat + untrusted highlights | 6/18 | 2 | 4 | 3 | 18 |

Format repair only: one required true/false setting per span; same pinned weights, evidence, keys, selection task and original code receipts. The output ceiling rose from 96 to 192 tokens to accommodate the verbose format; actual input/output tokens are reported in the compact file. This follow-up was designed after the five repeated-ID replies and uses the same outcomes; it is **not** a pristine comparison or an independent validation. Its mechanical gate outputs are rules `NO_PROMOTION`, hinted `NO_PROMOTION`. Even an advance would authorize only another independent evaluation.

**Disposition: no integration as a selector that filters the evidence.** The frozen rules were exact on 7/8 issuer questions and 5/10 contrast questions, with 17 relevant facts among 18 suggested passages (94.4% micro precision). They missed all 4 designated counterevidence facets. With the boolean output, direct selection found 3/4; giving highlights to the model found the same 3/4, adding only one exact packet and more extraneous spans. All 18 responses in each model arm decoded. No independent rerun on fresh cases. A targeted next test is to preserve the initial source and search the whole packet specifically for scope changes, withdrawals, and opposing reports; never interpret no match as proof that no qualification exists.

## What the rule actually missed

In one packet, the opening sentence said enrollment opened across Chile. The next sentence in the same source restricted enrollment outside Santiago to invitations. The rule identified the correct document and its first sentence, but not the second. The other required qualifiers involved a retraction, conflicting regional evidence, and trial participants. Selecting a relevant *document* is not the same as selecting all decisive *passages*. Do not discard unselected text or use this component as an evidence-completeness gate.

Question and key authors knew the earlier method. The four explicitly labeled counterevidence cases are constructed and correlated with their benign twins; the eight archival questions do not test contradiction recall. Seven split/family clusters, 2,000 descriptive paired bootstrap draws; intervals on exact-rate differences and pair wins/ties/losses are in the JSON. These are not independent samples of field performance. The study did not change runtime behavior or rerun the document debate workflow.

## Verification and reproduction

The archive can be re-scored only with the same local cached notices and pinned weights. Use a new directory for each run. `python -B -m qualify.source_selection freeze <base>` then `run <base>`. Sensitivity: `python -B -m qualify.source_selection_mask freeze <base> <followup>` then `run <base> <followup>`. Never overwrite the original labels or receipts. Both original model arms ran on the same local runtime, thinking off. Audited all 72 arm/case rows per format, raw code-point slices and source hashes; rules/lexical stable over 108 packet orders per format. These permutations are not additional independent cases. All 14 planted faults were caught (11 in isolation, scoring and gates; three in the strict boolean decoder); restored checks passed. Full backend suite: 838 tests run, 21 skipped, OK. A separate clean export of the committed code, with no private corpus, passed all 139 tests from the selected research and PDF modules. No production selector or PDF change was made in this experiment.
