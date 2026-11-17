# Narrator speed: CPU and Vulkan GPU

> [!WARNING]
> **Experimental speed measurement on one PC.** Faster answers are not better decisions. Not a trading signal or financial advice. Cofinance never trades.

Tokens per second limit every narrated step: headline judgments, replays and chat answers. This page measures the pinned LFM3 1.7B Q4_K_M on llama.cpp `b12110`, same model file, CPU build versus the Vulkan build of the same release.

Method: Ryzen 5 3600X (6 cores, 12 threads), 16 GB RAM, Radeon RX 5700 XT 8 GB. Real Cofinance prompts from the sealed replay: a 562-token Decision card (24 output tokens), a 133-token headline judgment, and a 160-token narration. Temperature 0, thinking off, prompt cache off, median of 3 after one warmup. Timings are llama-server's own; wall time is the full request.

| Setting | Prompt tok/s | Output tok/s | Decision card | Headline | 160-token narration |
| --- | --- | --- | --- | --- | --- |
| CPU, previous (`--threads 6`) | 128 | 25 | 5.34 s | 1.36 s | 10.12 s |
| CPU, now (`--threads 6 --threads-batch 12 --flash-attn on`) | 167 to 171 | 27 to 28 | 4.27 s | 1.25 s | 8.30 s |
| **Vulkan GPU, now** (`--device Vulkan0 --n-gpu-layers 99 --flash-attn on`) | **about 1,500** | **about 158** | **0.56 s** | **0.23 s** | **1.29 s** |

Prompt reading dominated on the CPU: 4.4 of the 5.3 s Decision call. Batch and micro-batch 256 with one CPU thread gave the same GPU speed.

## Same answers?

Speed counts only if decisions stay the same. On the 40 dev-window headlines of the shortlisted headline-v1 technique, the GPU gave the same label on 39 of 40 (one borderline ABSTAIN became UP); the mean change in a headline's score was 0.023. Ranking AUC 0.574 on the GPU versus 0.585 on the CPU, still shortlisted, 0 failures. The full screen took 10 s instead of 55 s. Both runtimes pass the narrator qualification gates (narration and number faithfulness).

## How it runs

The Vulkan build is used first and the CPU build stays as a qualified fallback. If the GPU runtime cannot start, the narrator starts on the CPU and says so. Each runtime is pinned by SHA-256 and qualified on its own: the qualification fingerprint covers the model, the runtime build and every server flag, so a flag change always forces a new qualification. Benchmarks use the same command line as the live narrator (`COFINANCE_BACKEND=cpu` forces the CPU for a comparison).

Limits: one PC, three prompt shapes, three repeats. GPU and CPU floating-point order differ, so outputs are close, not bit-identical.
