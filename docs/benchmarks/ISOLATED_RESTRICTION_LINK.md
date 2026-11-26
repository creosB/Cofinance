# Does per-document isolated reading fix unnamed-restriction linking on LFM3 1.7B?

> [!WARNING]
> Experimental. Diagnostic dev screen on the 24 previously authored matched notices (`qualify/restriction_link_cases.json`) using `LFM3-1.7B-Uncensored-AD-Q4_K_M.gguf` (`llama.cpp` `b12110-vulkan`). Not a fresh held-out evaluation, not a live change, and not financial advice.

**Decision: do not integrate (both model arms missed the frozen gate).** Reading each notice in its own isolated call with a fixed-size per-span role schema (`isolated_notice_mask`) eliminated schema invalidity (0/24 malformed vs 6/24 for `full_packet_mask`), eliminated cross-notice leaks by construction (0/6 wrong-target hits on two-claim packets), and lifted restriction recall from 33.3% to 100.0% (12/12) and link F1 from 0.286 to 0.786. However, `isolated_notice_mask` still triggered false restriction links on **2/12 clean controls** (frozen gate required `<= 1`), yielding `REJECT_LOW_ACCURACY_OR_NOISE`, and trailed the deterministic `lexical` baseline on exact-link accuracy (19/24 vs 22/24).

## Protocol

- Frozen protocol: `qualify/isolated_restriction_link.py`, frozen at local commit `236cfa5` before execution, protocol SHA-256 `86af600f4f0c2a07cafce7093db1fa24ffdcd530e6912bd21574d4f0b16b755d`
- Cases (development-set reuse, not held-out): `qualify/restriction_link_cases.json` (`n = 24`, 12 matched positive/control families across fees, dates, entitlements; SHA-256 `a043e003981921ded4a0550592cdd4ec5fbc785f3e1bb13253b8b5052ee1abd2`, previously used in [RESTRICTION_LINK.md](RESTRICTION_LINK.md))
- Compact result file: [isolated-restriction-v1.json](isolated-restriction-v1.json)
- Structural design:
  - `isolated_notice_mask`: one LFM3 1.7B call per notice with a fixed-size `{span_id: "none"|"claim"|"restriction"}` JSON schema; Python links restrictions only to claims inside that same notice.
  - `full_packet_mask`: one LFM3 1.7B call over the full multi-notice packet with `{span_id: {role, target}}` JSON schema; Python validates that every restriction target is a declared claim span.
  - Baselines: `lexical` (regex + same-notice last-claim linker) and `select_all`.
- Frozen gate for `CANDIDATE_FOR_FRESH_UNSEEN_TEST`: 0 malformed outputs, 0/6 wrong-target hits on two-claim positives, exact-link accuracy >= 0.75, restriction recall >= 0.60, and <= 1/12 clean controls with any predicted link.

## Results (`n = 24`)

| Arm | Malformed | Claim F1 | Restr. prec / rec | Link F1 | Exact link | Wrong target (two-claim) | Clean controls w/ link | Tokens (in / out) | Time (s) | Decision |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `isolated_notice_mask` | 0 / 24 | 0.925 | 0.546 / 1.000 | 0.786 | 19 / 24 (79.2%) | 0 / 6 | 2 / 12 | 8,261 / 1,000 | 29.6 | `REJECT_LOW_ACCURACY_OR_NOISE` |
| `full_packet_mask` | 6 / 24 | 0.774 | 0.800 / 0.333 | 0.286 | 14 / 24 (58.3%) | 0 / 6 | 0 / 12 | 5,811 / 2,125 | 31.6 | `INCONCLUSIVE_MALFORMED` |
| `lexical` (baseline) | 0 / 24 | 0.947 | 1.000 / 0.917 | 0.870 | 22 / 24 (91.7%) | 1 / 6 | 0 / 12 | 0 / 0 | 0.0 | baseline |
| `select_all` (baseline) | 0 / 24 | 0.434 | 0.092 / 1.000 | 0.038 | 0 / 24 (0.0%) | 6 / 6 | 12 / 12 | 0 / 0 | 0.0 | baseline |

Paired family-cluster bootstrap (`isolated_notice_mask` minus `full_packet_mask`, 2,000 draws across 12 families):

- Restriction recall delta: **+0.6667**, 95% CI `[+0.4167, +0.9167]`
- Link F1 delta: **+0.5000**, 95% CI `[+0.1400, +0.8889]`
- Exact-link accuracy delta: **+0.2084**, 95% CI `[-0.0417, +0.4167]` (crosses zero)

## What happened

- **Why `full_packet_mask` broke (`6/24` malformed, `5/12` on two-claim packets):** Every single malformed row came from LFM3 1.7B pointing a restriction's `target` at a non-claim span (3 self-links such as `b007 -> b007`, and 3 links to `none` background spans). Across all 12 positive cases (including the 6 malformed outputs), 8 restrictions were missed; among the 6 valid positive rows, 2 restrictions were missed and 2 more were tagged as restrictions with `target="none"` (unlinked).
- **What structural isolation solved:** Splitting the call per notice and dropping free-form target IDs cut malformed replies from 6/24 (and 7/24 to 10/24 in [RESTRICTION_LINK.md](RESTRICTION_LINK.md)) down to **0/24**, raised restriction recall to **12/12 (100%)**, and guaranteed **0/6** cross-notice leaks on this single-claim-per-notice fixture while running slightly faster than `full_packet_mask` (29.6 s vs 31.6 s) on half the output tokens (1,000 vs 2,125).
- **Why `isolated_notice_mask` still failed the gate:** It over-tagged spans as restrictions (12 true positives vs 22 predicted restrictions, 54.6% precision), producing false links on 2/12 clean controls (`rl_ent_credit_ctrl`, `rl_fee_atm_ctrl`), extra links on 2/6 two-claim positives (`rl_fee_atm_pos`, `rl_ent_credit_pos`), and one single-notice miss where the claim span itself was misclassified as a restriction (`rl_fee_wire_pos`). Note also that `19/24` measures **exact link-set accuracy**, not exact role accuracy: on 3 additional clean controls (`rl_date_filing_ctrl`, `rl_fee_wire_ctrl`, `rl_date_grant_ctrl`), `isolated_notice_mask` misclassified a claim span as a restriction, leaving no local claim to link so `links` stayed empty (`exact_link = True`) while restriction false positives still grew.

## Limits and what was not tested

- **Development-set reuse (not held-out):** These 24 authored notices were previously used in `restriction_link_v1`; this is a diagnostic mechanism screen, not an unseen held-out accuracy estimate.
- **Single-claim-per-notice fixture only:** `assemble_isolated_packet` pairs every local restriction with every local claim inside the same notice. Because every notice in this fixture has one gold claim, this screen does not test intra-notice multi-claim disambiguation (where Cartesian local linking would over-link by construction).
- **Arm budget asymmetry:** `isolated_notice_mask` runs one call per notice (`cap_isolated = 160` each) whereas `full_packet_mask` runs one call per packet (`cap_full = 280`); this tests the isolation mechanism rather than an equal-compute comparison.
- **No cross-document overrides:** Per-notice structural isolation forbids cross-notice links by design, so it cannot represent a separate addendum or retraction notice that intentionally overrides an earlier document.
- **Small sample (`n = 24`, 12 families):** Exact-link accuracy CI against `full_packet_mask` crossed zero (`[-0.0417, +0.4167]`), and the deterministic `lexical` baseline remained higher on exact-link accuracy (22/24 vs 19/24) on this authored fixture.
- Live multi-PDF document synthesis and fresh unseen issuer notices were not changed or tested.
