# Can a small local model link an unnamed restriction to the exact claim it limits?

> [!WARNING]
> Experimental: claim, restriction and link extraction on 24 fresh authored notices, not field accuracy, issuer validation, answer quality or forecasting skill. [Compact results and per-packet outputs](restriction-link-v1.json) | [Packets and sealed keys](../../qualify/restriction_link_cases.json).

**Do not integrate the two-stage linker (`INCONCLUSIVE`, failed every promotion check).** On 24 fresh matched notices with identifying titles kept visible as read-only context outside selectable body IDs, the two-stage pipeline had 10/24 malformed outputs, recovered **0/12** restrictions and **0/12** links, and emitted false links on 5/12 clean controls. Direct full-packet reading had 7/24 malformed outputs and, on **all 4/4 valid positive two-claim packets**, found the exact unnamed restriction and both claims, then linked that restriction to **both** claims across both notices. Do not rely on the 1.7B local narrator to cross-link unnamed restrictions across separate notices.

## Frozen comparison

The [counterevidence screen](COUNTEREVIDENCE.md) showed that a second retrieval pass missed three of four restrictions and penalized identifying notice titles such as *Circular 47*. This follow-up separated identifying titles from selectable body evidence and scored claims, unnamed restrictions, and `(claim, restriction)` links independently.

The protocol, prompts, JSON schemas, lexical cues, scoring rules, bootstrap seed, and advancement gate were committed and frozen before any packets were written (`2f245db`, protocol SHA `e70f9bc0`). All 24 packets and keys were then authored and sealed before inference (`e32519d`, cases SHA `a043e003`). No new feed, model download, parser repair, retries, or live code change.

Twenty-four authored packets across three domains (`fees`, `dates`, `entitlements`): 12 matched positive/control families (6 single-claim pairs and 6 two-claim disambiguation pairs). Every positive restriction omits the claim subject. In two-claim packets, one notice carries a restricted claim and a second notice carries a parallel unrestricted claim (restricted notice first in half the pairs, second in the other half). Notice titles stay visible as read-only headers outside selectable `[bNNN]` body IDs.

- **Lexical:** deterministic body-only regex cues linking each matched restriction to the nearest preceding lexical claim inside the same notice.
- **Direct:** single full-packet call to pinned local AtomicChat LFM3 1.7B (Vulkan GPU, temperature 0, strict JSON schema, 256-token cap) returning `claims`, `restrictions`, and `links`.
- **Two-stage:** two full-packet calls to the same pinned model. Stage 1 extracts only primary claims (128-token cap); Stage 2 sees the full packet plus the fixed Stage 1 claims and extracts restrictions and links only from remaining body spans (256-token cap).
- **Select all:** every body span as both claim and restriction, with every ordered distinct pair linked, to verify false-link penalties.

Invalid responses remain in every denominator. More than one invalid reply in either model arm marks the frozen gate `INCONCLUSIVE`. Promotion to an independent retest required >=0.15 restriction recall gain over direct (95% CI > 0), >=0.10 exact-link accuracy gain (95% CI > 0), claim F1 and restriction precision within 0.05 of direct, 0/6 wrong-claim misattributions on two-claim positives, 0/12 clean controls with any predicted link, and 0 malformed replies in both model arms.

| Method | Valid /24 | Claim F1 | Restriction recall (/12) | Link F1 | Exact link packets /24 | Wrong-claim hits /6 | Clean controls with link /12 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Lexical | 24 | 0.9474 | 0.9167 (11) | 0.8696 | 22 | 1 | 0 |
| Direct | 17 | 0.7714 | 0.4167 (5) | 0.4000 | 12 | 4 | 0 |
| Two-stage | 14 | 0.5063 | 0.0000 (0) | 0.0000 | 5 | 0 | 5 |
| Select all | 24 | 0.4337 | 1.0000 (12) | 0.0377 | 0 | 6 | 12 |

## What failed

- **Cross-notice over-linking in Direct:** on 6/24 packets Direct selected a body span as both a claim and a restriction, and on 1/24 it referenced an undeclared span (7/24 malformed). On all 4 valid two-claim positives (`rl_fee_fx_pos`, `rl_ent_leave_pos`, `rl_date_appeal_pos`, `rl_ent_credit_pos`), Direct found the exact unnamed restriction and both claims, then linked the restriction to **both** claims across both notices (for example, linking the Student Travel Card 500-euro cap to Corporate Travel Cards too).
- **Stage-1 claim swallowing in Two-stage:** Stage 1 routinely included the unnamed restriction (and sometimes neutral sentences) inside `claims`, making them unavailable to Stage 2. Stage 2 then either returned empty sets, linked unrelated neutral spans (5/12 noisy clean controls), repeated span IDs (4/24), or hit the 256-token cap (4/24). Stage 1 also repeated claim IDs on 2/24 packets (10/24 malformed total).
- **Uncertainty and cost:** paired family-cluster bootstrap (12 families, 2,000 draws, seed 1729) for `two_stage - direct`: restriction recall delta 95% CI `[-0.6667, -0.1667]`, exact-link accuracy delta `[-0.4167, -0.1667]`, link F1 delta `[-0.5385, -0.1429]`. Direct took 14.47 s total; two-stage took 37.03 s total.
- **Why Lexical scored 22/24:** same-notice boundary plus nearest preceding claim prevented cross-notice leakage by construction on authored notices matching the cue regexes. This is a structural baseline on authored phrasing, not proof that regex cues handle arbitrary real issuer notices.

## Practical takeaway

Keep notice and document structure explicit when presenting multi-source evidence: group each restriction inside its own source notice and identifying header rather than asking the 1.7B model to reconcile unnamed restrictions across separate notices.

## Reproduction and verification

With the pinned local AtomicChat model installed, pick a new directory and run `python -B -m qualify.restriction_link freeze <dir>`, `seal <dir>`, and `run <dir>`. Code fingerprints and packet digests are verified before inference, and a started run cannot be reopened. Unit tests (`tests/test_restriction_link.py`) and 8 of 8 deliberate breaks verified schema validation, subject-omission checks, misattribution scoring, clean-control penalties, gate rules, and one-look protection. Not tested: real issuer PDFs, independent human annotation, larger models, or live dashboard integration.
