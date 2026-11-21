# Did the evidence establish that the target was met?

> [!WARNING]
> Experimental. These are authored cash-floor and uptime packets, not live financial data or a general answer-quality estimate. Neither screen changes the application.

**Finding:** a narrowly checked comparison can improve the verdict without improving the model's choice of which sources prove it. On packets with a verified comparison, the code could return both directly. Outside its grammar, the code withheld the answer.

## 1. Can we extract the right facts?

The [extractor](../../qualify/scope_extraction.py) matches whole served excerpts. It preserves the entity, metric, unit, time window, observed amount, threshold and operator; a definitive comparison needs both a rule and a signed observation. An explicit correction can replace one named compatible record. Unreconciled conflicts and unknown qualifiers block a decision. Each interpreted source carries a document name, page, exact quote, offsets and a content hash. This is scoped to the pages supplied, not a claim about unseen documents.

The [frozen screen](../../qualify/scope_extraction_lab.py) checked all fields, cited pages and verdict against labels recorded before implementation. Two reused design packets both matched. On 18 new controlled packets, all 9 decidable cases matched exactly; the extractor abstained on all 9 indeterminate cases. On 10 separately worded challenge packets, it resolved 2 of 4 decidable cases and abstained on all 6 indeterminate cases. A paraphrased cash statement and a period in a footnote defeated the parser. No wrong definitive card appeared in these 30 packets; that is not a guarantee on new documents. All intended pages were served. All 67 returned source spans matched the original text; all 180 document-order permutations preserved the card or abstention.

A simple number-only comparator used the largest amount outside the rule document and treated equality as sufficient. It is not the live orchestrator. It got 4/18 controlled and 2/10 challenge verdicts right, counting justified unknowns as correct. The paired bootstrap difference on the challenge spanned zero. The rules and examples were authored within the same project.

## 2. Does a checked card improve the answer?

Twelve additional authored questions with new entities were answered by the same local AtomicChat on Vulkan, thinking off, with the same excerpts, schema and 72-token output ceiling. One arm saw the excerpts alone; another also saw the fully checked comparison, including the computed verdict when available. Missing cards were not evidence that an underlying question was unanswerable. A third, deterministic arm simply returned the verified card with its two source IDs, otherwise UNKNOWN without fabricated citations. No model call was needed for that arm.

| Arm | Correct verdict | Correct verdict and exact decisive sources | Wrong definite verdicts | Withheld |
| --- | ---: | ---: | ---: | ---: |
| Excerpts alone, model | 4/12 | 0/12 | 8 | 0 |
| Excerpts plus checked card, model | 8/12 | 0/12 | 4 | 0 |
| Card alone, no model | 10/12 | 7/12 | 0 | 5 |

The deterministic arm's 10 correct verdicts include three justified UNKNOWNs; it supplied no decisive citations on those three and received no joint credit. Two further abstentions missed answerable prose. All 7 cards that resolved were jointly correct. Across 24 model calls there were no parse errors or truncations. Across 12 packets, input/output token totals were 3,042/408 for direct vs 3,698/405 for card-assisted. Inputs were not compute-matched.

The prespecified joint score requires the rule and the minimal source set that determines the verdict. A relevant but nondecisive source is not fabricated; it simply does not belong in that set. On one packet the card helped the model say MISSED: 99.6% is not strictly above 99.6%. The model identified the full-month ledger and policy but also cited the first-week dashboard as decisive. Ignoring extra references after the fact, 7 assisted answers vs 2 direct answers had the right verdict, rule and primary source. That sensitivity check did not replace the frozen score. Assisted vs direct tied on joint correctness for each of 12 packets. On the 7 resolvable packets it did not beat code alone.

**Disposition:** no evidence here for inserting another model call to rewrite verified verdicts and citations. Test deterministic rendering and evidence-backed explanations for UNKNOWN on new packets before integration. Exact source selection is narrower than judging the quality of a free-form research answer. No production routing, medical transfer, or independent field evaluation was tested.

[Compact results](source-scope-v1.json). Reproduce locally with `python -m qualify.scope_extraction_lab freeze` then `run`, each with `--root data/<new-folder>`; use [the paired model runner](../../qualify/scope_card_lab.py) the same way with a different new folder and the pinned local runtime already installed. The original protocol hashes identify the historical rules. No external data is required.
