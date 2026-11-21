# Can a second reader find what the first passage leaves out?

> [!WARNING]
> Experimental: retrieval on authored packets, not field accuracy, answer quality or forecasting skill. [Compact results and selected IDs](counterevidence-v1.json) | [Packets and sealed keys](../../qualify/counterevidence_cases.json).

**Do not integrate this reader.** It found eight of twelve material caveats, one more than direct full-packet selection, but returned 23 extra citations. On a household fee waiver, both model arms selected the promise and the title of the rate card. Neither selected the sentence saying the waiver applied only to the first transfer each month. Silence from a second search is not evidence that no restriction exists.

## Frozen comparison

The [original selector](SOURCE_SELECTION.md) was useful for finding a starting passage but missed qualifications. This follow-up kept the full packet visible. The protocol, prompts, code fingerprints, arm budgets and advancement criteria were committed before the new packets were written (`9d33cb7`); the texts and keys were then sealed before any predictions (`cc5c299`). No new feed, model or download. No production import, verdict or change to document chat.

Twenty authored packets: nine matched positive/control pairs across restrictions, retractions and conflicting accounts, plus two no-evidence controls. Each positive had one or two material caveats; nine of twelve caveats omitted the subject name. Matched controls switched territory, product, date or event without removing plausible negative wording. Shuffled document identifiers and packet order; every arm used the same three exhibits and question. All spans, including titles, were separately selectable. Keys identify minimal sets, not external truth. Wording and keys came from the same investigator who designed the experiment. Fresh questions are not independent labels or naturally occurring issuer documents.

- **Seed:** unchanged deterministic selector, using only its source receipts, not its verdict.
- **Direct:** pinned local AtomicChat selects relevant spans from the whole packet. Same boolean-per-span format as the repaired previous comparison.
- **Second reader:** the same model sees the same full packet plus the seed as untrusted hints. Its one call looks specifically for additional material restrictions, withdrawals and contradictions. Python unions those selections with the seed. The model cannot remove a bad seed. Direct and second reader each have one model call, no retries and the same 192-token output ceiling.
- **Everything:** select every span to show the price of perfect recall without discrimination.

The advancement rule required more caveats than both direct and seed, no kind worse than direct, at least two additional unnamed caveats versus seed, no fewer exact packets than direct, precision of at least 75%, at most one extra span more than direct and noise on at most one control. Invalid responses stay in the denominator. Even a pass would justify only an independent retest.

| Selector | Caveats /12 | Unnamed /9 | Exactly the keyed passages /20 | Keyed / selected | Extra spans | Controls with extras /11 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Seed | 1 | 0 | 8 | 18/21 | 3 | 3 |
| Direct | 7 | 4 | 3 | 25/49 | 24 | 8 |
| Second reader | 8 | 5 | 4 | 26/49 | 23 | 8 |
| Everything | 12 | 9 | 0 | 30/132 | 102 | 11 |

**Failed criteria:** second-reader precision was 53.1%, below 75%, and eight controls attracted extra citations, above the allowed one. All 40 actual model replies were valid; zero exclusions or retries. Restrictions found: 1/4 for both readers. Retractions: 4/4 second vs 3/4 direct. Conflicts: 3/4 for both. On the two genuinely empty packets, direct returned nothing twice; the union carried an irrelevant seed into both. These are retrieval outcomes, not model verdicts.

**Uncertainty and cost:** the paired family-bootstrap interval for the caveat-recall difference was [0.000, 0.231]; for exact packet agreement, [-0.188, 0.238]. Both include zero. Eleven families, 2,000 draws, correlated hand-written cases; neither interval establishes a population advantage. In two descriptive ten-packet halves ordered by opaque case ID, caveats were 5/7 vs 4/7 and 3/5 vs 3/5 (second vs direct); not a time split. Local Vulkan AtomicChat with thinking off: direct used 5,302 input and 882 output tokens; second used 5,645 and 936. Median model-arm time was 4.66 s and 4.81 s, respectively, excluding startup and seed extraction.

## What to keep from the failure

- The sole extra caveat was the register entry withdrawing **circular 47**. The same second-reader response selected every span in that packet, including weekday support hours. Finding the retraction did not make the citation set precise.
- Of 23 second-reader spans outside the frozen key, 17 were titles and six body passages. Direct had 21 extra titles and three extra body passages. The key treated the title *Payment circular 47* as extra even though its number helps readers connect the withdrawal to the original notice. Treat that as a limitation of the key, not a reason to change scores after seeing them. A new evaluation can distinguish an identity-bearing title from an independent factual claim.
- A narrower research problem is linking an unnamed restriction to the particular entitlement it limits, with an explicit wrong-entitlement control. Do not hide titles or other unselected text, and do not add a verdict stage.

## Reproduction and verification

From the repository root with the already installed pinned model and runtime, choose a **new** local run directory. Run `python -B -m qualify.counterevidence freeze <dir>`, then `seal <dir>` and `run <dir>` with the same module. The published texts/keys are public and reusable, not fresh for future model development. Normalized code fingerprints and a separate packet/key digest are verified before inference; a started run cannot be reopened. Local model receipts stay private; the compact JSON includes IDs for every arm to audit against the public packet text.

All 80 arm/packet rows were independently decoded or reconstructed and checked against the raw source slices, selection keys and score totals. Deterministic seed suggestions were unchanged in all 120 tested document orderings; that is not a model order-invariance result. Fifteen of fifteen planted faults were caught after strengthening one test that previously exercised the wrong key error. Restored tests passed. No independent corpus, end-to-end answer evaluation or deployment.
