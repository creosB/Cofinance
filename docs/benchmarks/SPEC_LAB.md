# Does zero-weight n-gram speculative decoding speed up LFM3 1.7B on Vulkan?

> [!WARNING]
> Experimental. One-look local A/B lab on the pinned `LFM3-1.7B-Uncensored-AD-Q4_K_M.gguf` narrator (`llama.cpp` `b12110-vulkan`, Radeon RX 5700 XT). Not a trading signal, not financial advice.

**Decision: do not integrate.** Zero-weight `--spec-type ngram-mod` is slower than plain autoregressive decoding on LFM3 1.7B in Deep Think (0.83x default, 0.65x `n_match=8`), breaks exact greedy parity on 2 of 7 Deep Think cases at `n_match=8`, and never drafts a single token in standard mode at default settings. Keep live server flags and qualification fingerprints unchanged.

## Protocol

- Frozen protocol: `qualify/spec_lab.py`, protocol SHA-256 `632728d5dfe846bf75e0fb63ae72a1a2bcf68b95bf0d4f89b0b14e3aa49b4038`
- Frozen cases: `qualify/cases.jsonl` (`narration` + `number_faithfulness`, `n = 7`, SHA-256 `0eb5fcb53186701d4b8c6dcf0646acbfa8e46b3bf883eb6977b2357f12b007d8`, read-only)
- Compact result file: [spec-lab-v1.json](spec-lab-v1.json)
- Baseline: exact pinned `server_flags("vulkan")` and `thinking_server_flags("vulkan")` (`temperature=0`, `seed=0`, one warm-up call excluded per server launch)
- Frozen adoption gate: 0 errors, 100% exact greedy content and reasoning parity vs baseline, paired 95% bootstrap CI lower bound >= 1.10x on eval speedup, and both split halves faster (> 1.0x)

## Results (`n = 7`, 0 errors across all 42 calls)

| Mode | Arm | Exact content | Exact reasoning | Drafts accepted / gen | Median eval (ms) | Eval speedup [95% CI] | Split halves | Decision |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Standard | Baseline | 7 / 7 | 7 / 7 | 0 / 0 | 272.6 | 1.000x | - | baseline |
| Standard | `ngram-mod` (default) | 7 / 7 | 7 / 7 | 0 / 0 (0.0%) | 264.6 | 1.412x [1.066, 1.836] | 1.130 / 1.637 | `REJECT_INSUFFICIENT_GAIN` |
| Standard | `ngram-mod` (`n_match=8`) | 7 / 7 | 7 / 7 | 23 / 88 (26.1%) | 324.3 | 1.005x [0.519, 1.824] | 0.917 / 1.061 | `REJECT_INSUFFICIENT_GAIN` |
| Deep Think | Baseline | 7 / 7 | 7 / 7 | 0 / 0 | 3154.5 | 1.000x | - | baseline |
| Deep Think | `ngram-mod` (default) | 7 / 7 | 7 / 7 | 85 / 1216 (7.0%) | 3291.1 | 0.831x [0.744, 0.941] | 0.761 / 0.868 | `REJECT_INSUFFICIENT_GAIN` |
| Deep Think | `ngram-mod` (`n_match=8`) | 5 / 7 | 4 / 7 | 686 / 1760 (39.0%) | 3961.1 | 0.648x [0.496, 0.805] | 0.459 / 0.802 | `REJECT_PARITY_DRIFT` |

## What happened

- **Why standard `ngram-mod` (default) looks 1.41x faster despite 0 drafts:** `standard/baseline` ran first right after cold Vulkan startup (one short 16-token warm-up was not enough to stabilize GPU clocks across all 7 cases), while `draft_n = 0` proves `ngram-mod` (default `n_match = 24`) did not activate at all. That `1.412x` figure is an order-confounded estimate rather than a speculative speedup: medians were nearly identical (272.6 ms vs 264.6 ms), and the frozen CI gate rejected it.
- **Why Deep Think slowed down when speculation actually fired:** `baseline` ran before the `ngram-mod` arms, so GPU warm-up favored the candidates, yet both were slower in both split halves (`0.831x` at default, `0.648x` at `n_match=8`). A plausible hypothesis consistent with the slowdown (not directly profiled here) is that batch verification and recurrent/short-conv state rollback overhead in hybrid LFM3 outweigh single-token Vulkan decode when 61% to 93% of draft tokens are rejected.
- **Greedy parity drift at `n_match=8`:** Two Deep Think cases (`narr-1`, `narr-3`) diverged in final content and three of seven diverged in reasoning under block verification versus single-token decode. That disqualifies `n_match=8` on this 7-case gate even if it had been faster (and `7/7` exact parity on the other arms proves parity on this frozen suite, not model-wide).

## Limits and what was not tested

- Small case count (`n = 7` frozen narration and number-faithfulness cases) on one Windows PC (`Radeon RX 5700 XT`, `llama.cpp` `b12110-vulkan`).
- Arms were executed sequentially (`baseline`, `ngram_mod_default`, `ngram_mod_n8`) rather than interleaved, which allowed cold-GPU clock drift on the first standard baseline pass (explicitly exposed by `draft_n = 0`).
- Stochastic sampling, long multi-PDF contexts (> 4K tokens), and trained draft models (such as DFlash or DFly2, which have no LFM3 checkpoints) were not tested.
