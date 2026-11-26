# Does LiquidAI d1-2B earn a place in the local headline gate on CPU?

> [!WARNING]
> Experimental. A frozen one-look screen on 24 authored headlines (`qualify/headlines.jsonl`), not real live market feeds, not a trading signal, and not financial advice. See [compact result](d1-eval-v1.json).

## Frozen protocol

Protocol `033ae167af1d9c2add70a00c69f6aae9c664d58dc2ffbdc3fe046a9b62656789` (code commit `9d758b0`) was frozen and hashed before running inference on `qualify/headlines.jsonl` (SHA-256 `cb6ede1f...`, 24 rows: 12 material, 12 non-material, 6 urgent, 8 traps). Three arms were scored on the exact same 24 rows under `GateConfig(digest_p=0.50, alert_p=0.85, alert_urgent_p=0.70)`:

1. **Baseline (`gliner`):** `fastino/GLiNER2.5-Decide` (`9d1bfb84`), verified `encoder_fp32` ONNX CPU worker.
2. **Candidate (`d1`):** `LiquidAI/d1-2B` (`a6bce774e6dde338dcc3036ceb5d91f78bf4c19e`), `transformers==5.22.2`, `torch.float32` on CPU via `SystemOneSignalClassifier` (`noul` material + `choice` urgency + `choice` direction).
3. **Offline cascade (`cascade_gliner_d1`):** routes GLiNER gray-zone (`0.25 <= material_p <= 0.85`) and alert candidates to `d1` using the exact sealed outputs of both arms.

Frozen adoption gate: zero regression on errors, false alerts, or balanced accuracy; meaningful skill gain (`macro_f1_gain >= +0.05` or `material_auc_diff >= +0.05` with paired 95% CI above zero); and practical local CPU latency (`p50_ms <= 5000.0`).

## Sealed result

**Decision: `REJECT_TOO_SLOW_ON_CPU` (do not integrate into live Clock 1 on CPU).** No production gate behavior changed.

| Arm | Material AUC | Urgent AUC | Balanced Acc | Macro-F1 | Trap Acc (8) | Alert Recall (6) | False Alerts | Brier | ECE | Errors | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `GLiNER2.5-Decide` (`encoder_fp32`) | 0.8542 | 0.5000 | 0.8750 | 0.8730 | 0.6250 (5/8) | 0.0000 (0/6) | 0.0000 | 0.1729 | 0.2328 | 0 | 648.7 | 1,242.0 |
| `LiquidAI/d1-2B` (`float32` CPU) | 1.0000 | 0.8796 | 0.9583 | 0.9583 | 1.0000 (8/8) | 0.6667 (4/6) | 0.0000 | 0.0476 | 0.1327 | 0 | 5,367.4 | 21,805.5 |
| `Cascade (GLiNER + d1)` | 1.0000 | 0.8796 | 0.9583 | 0.9583 | 1.0000 (8/8) | 0.6667 (4/6) | 0.0000 | 0.0495 | 0.1396 | 0 | 6,108.8 | 22,914.6 |

Paired 2,000-resample bootstrap differences (`d1 - gliner`, identical for cascade):
- **Material ROC AUC:** `+0.1458` (95% CI `[0.0000, +0.3407]`)
- **Balanced accuracy:** `+0.0833` (95% CI `[-0.0714, +0.2500]`)
- **Macro-F1:** `+0.0853` (95% CI `[-0.0828, +0.2571]`)

Qualitative breakdown:
- `d1-2B` caught all three traps that fooled GLiNER: prompt injection (`acme-inject`, 0.0349 vs 0.7337), retrospective (`acme-history`, 0.1902 vs 0.6136), and peer distractor (`gamma-peer`, 0.1260 vs 0.5919).
- `d1-2B`'s single threshold miss was `beta-listicle` (0.6040 material, 0.0203 urgent), which still ranked below the lowest true positive (`acme-halt`, 0.6278), yielding 1.0000 material AUC.
- The first two `d1-2B` calls took 32.9 s and 24.3 s; warm rows ran between 4.4 s and 7.7 s (median 5,367.4 ms, 8.3x slower than GLiNER's 648.7 ms, missing the frozen 5,000 ms CPU cap).
- Under the default `[0.25, 0.85]` gray zone, GLiNER escalated 22 of 24 headlines to `d1`, so cascading did not save wall time (188.3 s vs 180.7 s for `d1` alone).

## Scope limits and what was not tested

- **Small authored sample (`n = 24`):** every paired 95% confidence interval touches or crosses zero. This is promising signal, not proof of superiority on real financial news.
- **Split check limitation:** `qualify/headlines.jsonl` places all 12 positive rows in the first half and all 12 negative rows in the second half, so per-half ROC AUC and balanced accuracy are undefined (`null`).
- **Not tested:** human-verified real market headlines (waiting on the 20-item human spot-check of `news_corpus_20261108`), quantized `LiquidAI/d1-2B-ONNX` CPU speed/calibration, or GPU execution.

## Reproduction

```text
python -B -m qualify.d1_eval freeze data/d1_eval_new
python -B -m qualify.d1_eval run data/d1_eval_new
```

Requires `transformers==5.22.2` and the pinned local Hugging Face caches for `fastino/GLiNER2.5-Decide` (`9d1bfb84`) and `LiquidAI/d1-2B` (`a6bce774`).
