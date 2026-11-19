# Local cached-news simulation (v1)

> [!WARNING]
> Experimental diagnostic on previously inspected outcomes. Not an independent validation, forecast, trading signal
> or financial advice. Cofinance never trades. Shuffles do not create new market observations.

Code: `qualify/local_simulation.py`. Rules are `SPEC` in that module, committed locally before the run. The frozen
run also hashes every input file and the code. Protocol SHA-256:
`ba4a6ce8095e8c5f0a0a710f835beb9df7ee84cdf561ebd4504f1d98021bcd47`.
Compact result: [local-news-simulation-v1.json](local-news-simulation-v1.json).

## Method

Use cached AtomicChat headline-v1 judgments, the original served-news lists and cached Yahoo prices. No download,
new model call or changed production method. Python audit hooks in the command-line runner reject socket, DNS,
subprocess and shell operations, including attempted fallback fetches. This is a process-local guard, not an OS sandbox.

The panel is 96 cases: eight assets at 12 origins from 2026-10-27 through 2026-11-11. Entry is the next SPY session
open, exit the close of the fifth SPY session from entry; Bitcoin uses that same calendar. Later 16 sealed-window
cases are outside the fixed scope and remain untouched. No already-scored report is modified.

All 96 outcomes were inspected in earlier work, including the 24-case window subset. The protocol is fixed before
this diagnostic, not before those outcomes originally became known. Only synthetic fixtures were used to test the
new math. There is no trained model, blind relabelling or candidate selection here.

- Ordinary AUC: all positive/negative pairs, 0.5 means no ranking skill.
- Conditional AUC: compare only pairs within the same origin, or within the same asset. Pair-weighted, not the mean
  of per-group AUCs; groups without both classes contribute no pairs.
- Asset shuffle: 2,000 seeded permutations of entire asset score paths, preserving each path's time dependence.
- Date shift: all 11 nonzero circular shifts of the entire cross-asset vector. These preserve the joint vector and
  chronological order except the wrap. They are diagnostics, not independent trials or a significance test.
- Stress: remove all news, duplicate every score, remove the largest absolute headline score in each case, reverse
  every score's sign. Real outcomes stay unchanged. These do not simulate newly generated news or validate judgments
  on genuinely different wording.
- References: gate-label count, 20-session return, served-headline count and always-UP. No matching crowd forecast
  is available for these asset/session targets.

## Result

| Arm | AUC | Within origin | Within asset | Balanced accuracy |
| --- | ---: | ---: | ---: | ---: |
| Headline score | 0.645 | 0.652 | 0.648 | 0.435 |
| Headline count only | 0.626 | 0.648 | 0.660 | 0.471 |
| Gate-label count | 0.583 | 0.590 | 0.512 | 0.419 |
| 20-session return | 0.443 | 0.469 | 0.167 | 0.528 |
| Always-UP | 0.500 | 0.500 | 0.500 | 0.500 |

Abstentions count as misses in balanced accuracy. Headline score made 42 UP, 20 DOWN and 34 ABSTAIN calls;
35/96 outcomes were up. The news-only subset has 74 cases and AUC 0.597. Conditional AUCs use 145 within-origin
and 203 within-asset positive/negative pairs, not independent observations.

Split check: dev 72 cases AUC 0.625; reused window 24 cases AUC 0.681. Within-asset AUC is 0.745 vs 0.583, but
that window comparison has just six eligible pairs. Do not read it as a stable subgroup result.

Paired origin-cluster bootstrap (2,000 draws, seed 29): headline minus gate AUC +0.062, 95% percentile interval
[-0.003, +0.131]; headline minus momentum +0.202 [+0.133, +0.269]. The gate comparison includes zero. Adjacent
five-session outcomes overlap, so this cluster scheme still does not remove all dependence. No fresh-sample edge claim.

The asset-path shuffle matched or exceeded observed AUC in **13.05%** of draws (median 0.527, central range
0.354 to 0.730). None of the 11 circular date shifts beat the original, but the best shifted AUC was **0.639**,
close to 0.645. Neither exercise establishes reliable asset-specific or timing skill.

| Stress | AUC | Calls made |
| --- | ---: | ---: |
| Original | 0.645 | 62/96 |
| No news | 0.500 | 0/96 |
| Duplicate all | 0.645 | 66/96 |
| Remove strongest headline per case | 0.525 | 23/96 |
| Reverse signs | 0.355 | 62/96 |

## Interpretation

Conditional AUC point estimates remain above 0.5 after restricting comparisons by date or asset, but this small,
reused sample does not establish conditional skill. Count alone is nearly as strong, asset shuffles can do as well,
and removing the strongest headline collapses most of the discrimination. These are reasons to isolate the value of judgment beyond coverage,
not evidence that more news necessarily helps. Uniform duplication leaves ranking unchanged but changes four calls,
showing that the fixed sum threshold is sensitive to repeated evidence. No method is promoted from this diagnostic.

## Accounting and verification

- No model failures or missing served judgments; 16 later-origin cases excluded by protocol, not silently dropped.
- 168 overlapping entry/exit checks matched between two cached downloads of Yahoo. This is a consistency check
  against the same provider, **not an independent source**; the remaining 24 price checks lack the older snapshot.
- 16 focused tests passed; 13/13 deliberate breaks caught, followed by a clean restored run. Audit-hook tests use
  synthetic events, so even the broken-guard test never sends a network request. Full backend: 699 tests run,
  21 skipped, OK (128 seconds); invariant checks passed again after the write-up. No frontend code changed.
- Limits: small, correlated, reused sample; snapshot price revisions; news availability is source-defined; no
  independently archived headline versions, new inference, full-system simulation or fresh outcomes.

## Reproduce

The private cache must already exist. Missing files fail; the runner never fetches replacements. Use a new folder:

```console
python -B -m qualify.local_simulation freeze --root data/local_news_simulation_new
python -B -m qualify.local_simulation run --root data/local_news_simulation_new
```

A changed input, changed code or second run is refused. Reproducing an already-viewed diagnostic is not a fresh validation.
