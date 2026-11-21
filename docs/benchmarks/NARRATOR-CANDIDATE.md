# Base narrator versus AtomicChat Uncensored

> [!WARNING]
> **Experimental development screen.** Output-guard acceptance is not factual accuracy or financial prediction skill. Not a trading signal or financial advice. Cofinance never trades.

**Result: two jobs, two answers.** For written news summaries, AtomicChat AD-Q4_K_M is a wash: it passes the frozen narrator smoke checks and writes shorter, cleaner replies, but fails this screen's historical-news guard non-regression rule and still invents sources and implications. For headline judgments (the shortlisted headline-v1 technique), it ranked dev outcomes better than the base and passes the frozen shortlist rule, so it is sealed as a second arm on the unseen 11/09 to 11/13 window. That one look has since been scored: [window result](window-2026-11-09-headline-result.json). The judges tied on the 24 available outcomes; AtomicChat remains the live judge.

[Compact results and protocol fingerprints](narrator-atomic-2026-11-17.json). [Headline dev screen and sealed window arm](window-2026-11-09-headline-atomic-sealed.json).

## Headline judgments

Same headline-v1 prompt, label-probability readout, sum per case and plus or minus 0.5 call line; same GPU runtime path as the base GPU screen; 40 unique headlines over 72 dev cases (9 origins, outcomes known); 0 failures in both arms.

| Ranking skill (AUC, 0.5 = none) | Base, GPU | AtomicChat |
| --- | --- | --- |
| All dev cases | 0.574 | 0.625 |
| Early and late halves | 0.548 / 0.619 | 0.621 / 0.617 |
| Origin-cluster 95% interval | 0.505 to 0.652 | 0.592 to 0.672 |
| Mean aggregation, post hoc | 0.659 | 0.694 |

Gate labels alone rank at 0.556 on the same cases. AtomicChat abstains more (18 of 40 headlines versus 12) and gives the same label as the base on 34 of 40. The intervals overlap, the cases are correlated and the outcomes were known, so this is a reason to test, not evidence of a better model. The sealed window arm makes the same call as the base on 38 of 40 cases; the predeclared rule allowed replacement only if its window AUC is above 0.50 with the interval excluding 0.50 and at least the base arm's AUC. That rule and the arm's hashes were published before any window outcome was scored.

## Method

- Compare LiquidAI/LFM3-1.7B-GGUF Q4_K_M with AtomicChat/LFM3-1.7B-Uncensored-GGUF AD-Q4_K_M. Both files and the runtime are pinned by SHA-256 in the result. The candidate is a different quantization as well as a weight edit, so this is a package comparison, not an isolated test of abliteration.
- Same llama.cpp b12110 Vulkan binary, RX 5700 XT, full offload, context 4096, temperature zero, thinking disabled and prompt cache disabled. One identical greeting warmup per arm; base ran first, candidate second.
- Seven existing qualification cases: four narration, three number faithfulness. Required pass rate is 100% in both categories. Maximum 256 output tokens.
- News Researcher only: 27 distinct nonempty historical input sets representing 57 correlated development cases, plus eight fictional financial-news fixtures. Sensitive topics: sanctions, war, fraud, sexual misconduct allegations, opioids, ransomware, layoffs and terrorism. No market outcomes were loaded. Maximum 320 output tokens.
- Both arms received identical rendered messages, asserted after the run. Existing output guards and deterministic fallback behavior were unchanged. Store inputs, protocol and hashes were frozen before either arm ran.
- Frozen screening rule: do not shortlist if smoke gates fail, historical guard passes decrease, or sensitive-news refusals increase. Passing this screen would still not authorize adoption or establish factual accuracy.

## Observed results

| Measurement | Base | AtomicChat |
| --- | --- | --- |
| Frozen qualification | 7/7 | 7/7 |
| Historical news guard acceptance | 9/27 | 8/27 |
| Sensitive-news guard acceptance | 2/8 | 4/8 |
| Sensitive-news refusals, manually reviewed | 0/8 | 0/8 |
| News request failures or truncations | 0/35 | 0/35 |
| Model-added word-count annotations in raw replies | 21/35 | 0/35 |
| Total news request time, excluding startup/qualification | 25.34 s | 20.96 s |
| Completion tokens across news requests | 2,521 | 2,073 |
| Median request decode throughput | 148.3 tok/s | 161.4 tok/s |

Historical paired guards: five inputs passed for both, four only for base, three only for AtomicChat, fifteen for neither. The one-case decline fails the predefined screening rule; it does not establish statistically significant inferiority. Combining historical and synthetic cases would conceal that tradeoff.

AtomicChat's observed shorter request time combines shorter output with higher measured decode throughput in this single fixed-order run. It is not a repeated, randomized speed benchmark, and no general speed advantage is claimed.

## Factuality review

All paired candidate summaries were inspected, but review was neither blinded nor independent. No factual-accuracy percentage is claimed. Both models made unsupported assertions, including outputs accepted by the existing guards.

Examples from AtomicChat:

- A single company statement about a missile strike became reporting from "digest and bearish outlets" and a possible "strategic move". Digest and bearish are application labels, not external sources.
- A headline disclosing a Bitcoin Core vulnerability became an "outage", which the input did not report.
- "China withholds import approval" became "import bans".

Fewer unnecessary refusals cannot repair these grounding errors. Neither model refused the eight sensitive-news fixtures, so this screen demonstrates no refusal advantage for the candidate on that narrow workload.

## Limits and disposition

The inputs are development data, with overlapping historical stories, and only eight synthetic sensitive-news cases. Automatic guards have known false positives and semantic blind spots. Exact inputs, raw responses and a result seal are retained locally; this compact public report alone does not enable an exact independent rerun. No independent financial outcomes, new-window accuracy, CPU compatibility or full chat-path qualification of the candidate were measured.

This comparison ran as an isolated experiment and changed no sealed forecast. Since then both models are pinned, separately qualified narrators that a user can choose in the Models view. AtomicChat became the live narrator on 2026-11-17 and the default for a fresh install on 2026-11-18; the base is an optional download, and a saved choice of either is kept. Headline judgments are stored per narrator, and the chat card names the judge and its own dev result. Making AtomicChat the default before the unseen-window look is a product choice (shorter, cleaner replies), not a validated accuracy upgrade. The switch rule above decides only which model judges headlines.
