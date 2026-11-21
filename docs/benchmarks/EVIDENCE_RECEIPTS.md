# Can a verifier explain uncertainty without inventing its reason?

> [!WARNING]
> Experimental, local lab only. The result concerns supplied, text-bearing pages in the narrow cash-floor and uptime grammar. It does not measure real-world research accuracy or PDF layout understanding. [Compact results](evidence-receipts-v1.json).

## Contract first, indexing first at runtime

[The lab](../../qualify/receipt_contract.py) indexes supplied pages before interpreting them. Receipts carry document ID, name, page number, half-open Unicode character offsets, exact quoted text and a hash of the original supplied page. The index recognizes explicit paragraph boundaries, row delimiters, heading markers and footnote markers. It is not a layout engine, table-cell resolver, OCR system or coordinate index into PDF bytes.

One validated result is rendered with no model call. `VERIFIED` for a threshold decision requires the observed value and the rule, with compatible subject, measure, period, unit and operator. `CONFLICT` requires two incompatible, same-scope statements of the same role. `WITHDRAWN` requires a notice and its uniquely identified target. An explicit replacement record is not automatically a blanket withdrawal. `UNINTERPRETED` identifies a blocker and makes no claim about the world. A missing or ambiguous input with no reliable passage is an input error, not a fabricated blocker.

Wire proposals are untrusted, including any future model proposal. Exact offsets alone do not establish a relation: the boundary checks source text, hashes and typed semantics, rederives the packet result, and renders only a validated variant. Invalid proposals return `UNINTERPRETED` with an indexed blocker and a structured failure event; the lab runner persists the events. No stronger model or production route is connected. A model cannot make unsupported wording authoritative by pointing to a real sentence.

## One-look matrix

Ground-truth labels, reference sets and pass criteria were sealed **before implementing the checker**. The [36 variants](../../qualify/receipt_matrix.py) come from **12 previously used parent packets** (7 resolvable, 5 unresolved). They are related, authored controls, not statistically independent documents. Wording variants use meaning-preserving changes; words that change legal scope, such as adding cash equivalents, are not presumed equivalent. A scope amendment changes the entity, period, metric or currency. A correction variant adds a retraction, signed conflicting observation or unresolved qualifier. [Scoring](../../qualify/receipt_screen.py) checks status, receipt identities, designated blockers, exact spans and normalized verdict/value for resolvable cases.

| Variant | Exact state and required receipts | Other checks |
| --- | --- | --- |
| Wording | 11/12 | All seven previously verified verdicts and values preserved. |
| Scope | 12/12 | Every modified scope abstained, including already unresolved parents. |
| Correction | 12/12 | None remained falsely verified. |

All 36 tags matched. On the one receipt mismatch, two documents reported only the first week; the checker pointed to the dashboard instead of the prespecified ledger. That is still a miss under the frozen rubric. The earlier extractor resolved precisely the same seven wording cases and withheld on the rest. The benefit here is evidence for *why* the checker withheld, not broader answer coverage. The new run returned 7 verified, 4 conflict, 4 withdrawn and 21 uninterpreted results. All 52 source spans were checked against raw pages. Across 216 document orderings the output was unchanged; 108 intentionally malformed proposals downgraded and logged. The original lost-notice replay returned the notice and target; dropping the target from the proposed result returned an uninterpreted notice, not a withdrawal claim.

## Two important follow-up checks

1. Two generated text PDFs were run through the actual extraction path. Before whitespace normalization the research checker saw a distinct ledger paragraph and withdrawal footnote and could cite both. Production normalization merged those two blocks; applying the research checker to the stored page correctly withheld, but could not identify a withdrawal. This is *not* a live dashboard feature or a test on arbitrary third-party PDFs. A future integration needs an original-text witness sidecar (or raw page plus offset map) alongside searchable normalized text.
2. After freezing the matrix, a deliberately faulty internal producer proposed an earlier, locally well-supported verdict despite a new caveat. Its citations were authentic. A separate **whole-packet veto** now catches uncited caveats, contradictory observations and withdrawal notices even when the producer ignores them. Three stale-proposal controls failed before and passed after the repair. This is a post hoc safety improvement, not a retuned benchmark win: all 36 original output payloads remained unchanged.

## Verification

The final backend suite ran 800 tests, 21 skipped, OK. Eighteen focused tests plus 15 general invariants also passed. Thirteen isolated faults in the original contract were caught after strengthening layer-specific tests. A fourteenth fault that disabled the later whole-packet veto reproduced all three stale-proposal failures; restoring it cleared all three. No imports from the research contract in the tracked core or UI modules.

## Reproduction and limits

The original one-look run uses commit `4c30831`, label SHA-256 `26258c9e1c4ab357cac6ba373045940cfd7cfaf534408201ae6d0c0102b4e82a`, protocol `566734b316b9dd799814dd211dff19b0547d3b2a8dfce67e958a469a7d54eb43`. To reproduce, use a new local directory, call `qualify.receipt_matrix.prepare(directory)`, then `python -B -m qualify.receipt_screen freeze <directory>` and `run <directory>`. Local fixture packages from the project are required. The hardened validator is `f0a3a95`. No mutation of sealed artifacts or actual user documents, no remote data, and no production integration. Actual issuer authenticity, scanned documents, reading order across columns, ambiguous table headers, and field performance on independently authored documents remain unverified.
