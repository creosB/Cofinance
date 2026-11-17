# Base narrator versus AtomicChat Uncensored

> [!WARNING]
> **Experimental development screen.** Output-guard acceptance is not factual accuracy or financial prediction skill. Not a trading signal or financial advice. Cofinance never trades.

**Result: keep the base narrator.** AtomicChat AD-Q4_K_M loads on the existing Vulkan runtime and passes the frozen narrator smoke checks, but does not pass this screen's historical-news guard non-regression rule. It produces cleaner, shorter replies without resolving unsupported claims or source attribution.

[Compact results and protocol fingerprints](narrator-atomic-2026-11-17.json).

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

The candidate remains an isolated experiment. The base model stays selected. The live model catalog, existing qualification state and sealed forecasts were not changed.
