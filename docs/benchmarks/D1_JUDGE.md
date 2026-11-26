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

## Real-headline follow-up (`n = 20`, protocol `62e6845d`)

To check whether the 24-headline authored result held on real market news, the exact same frozen harness (`qualify/d1_eval.py`) was run once in a new folder (`data/d1_eval_real20`) on 20 stratified real headlines from the local `news_corpus_20261108` corpus, labelled via a strictly blind two-pass subagent review (labeler + independent reviewer, both blind to the 2026-11-08 Opus 5.5 labels; inter-rater agreement 19/20 = 0.90 kappa on material, 18/20 = 0.76 kappa on urgent; not a human Baris review). See [compact real-20 result](d1-eval-real20.json).

**Decision: `REJECT_NO_SKILL_GAIN` for `d1` and `REJECT_NO_GAIN_AND_SLOW` for `cascade_gliner_d1`.**

| Arm (Real 20) | Material AUC | Urgent AUC | Balanced Acc | Macro-F1 | Trap Acc (3) | Alert Recall (7) | False Alerts | Brier | ECE | Errors | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `GLiNER2.5-Decide` (`encoder_fp32`) | 0.9192 | 0.3407 | 0.7323 | 0.7333 | 1.0000 (3/3) | 0.0000 (0/7) | 0.0000 | 0.1793 | 0.2103 | 0 | 667.0 | 1,397.0 |
| `LiquidAI/d1-2B` (`float32` CPU) | 0.8384 | 0.8352 | 0.7424 | 0.7442 | 1.0000 (3/3) | 0.1429 (1/7) | 0.0000 | 0.1658 | 0.1271 | 0 | 4,413.5 | 6,770.9 |
| `Cascade (GLiNER + d1)` | 0.8485 | 0.8352 | 0.7424 | 0.7442 | 1.0000 (3/3) | 0.1429 (1/7) | 0.0000 | 0.1656 | 0.1374 | 0 | 5,030.6 | 8,167.9 |

Paired 2,000-resample bootstrap differences (`d1 - gliner` on real 20):
- **Material ROC AUC:** `-0.0808` (95% CI `[-0.3000, +0.0952]`)
- **Balanced accuracy:** `+0.0101` (95% CI `[-0.2143, +0.2143]`)
- **Macro-F1:** `+0.0109` (95% CI `[-0.2238, +0.2400]`)

Split-half check on real 20 (both halves agreed that GLiNER ranked materiality higher):
- **First half (10 rows):** GLiNER `0.9600` material AUC vs `d1` `0.9200` (both `0.8000` balanced acc).
- **Second half (10 rows):** GLiNER `0.8333` material AUC vs `d1` `0.6667` (both `0.6667` balanced acc).

Takeaway: on real headlines, `GLiNER2.5-Decide` (`encoder_fp32`) ranked materiality higher (`0.9192` vs `0.8384`) and ran 6.6x faster (`667 ms` vs `4,414 ms` p50). `d1-2B`'s clear advantage was non-inverted urgency ranking (`0.8352` vs GLiNER's inverted `0.3407`), which Clock 1 already covers with deterministic `core/urgency.py` rules. Do not replace GLiNER or adopt `d1-2B` on CPU.

## Scope limits and what was not tested

- **Small samples (`n = 24` authored, `n = 20` real):** paired 95% confidence intervals cross or touch zero on both sets.
- **Authored split limitation:** `qualify/headlines.jsonl` places all 12 positives in the first half and 12 negatives in the second, so per-half AUC is undefined on that file (the stratified real-20 follow-up resolved this with balanced halves).
- **Provenance of real-20 labels:** generated by a blind two-pass subagent review (not a human Baris review); a human spot-check is still needed before treating real-corpus numbers as human-verified.
- **Not tested:** quantized `LiquidAI/d1-2B-ONNX` (requires `onnxruntime==1.30.0` for ONNX IR 13 vs installed `1.20.1`) or GPU execution.

## Reproduction

```text
python -B -m qualify.d1_eval freeze data/d1_eval_new
python -B -m qualify.d1_eval run data/d1_eval_new
```

Requires `transformers==5.22.2` and the pinned local Hugging Face caches for `fastino/GLiNER2.5-Decide` (`9d1bfb84`) and `LiquidAI/d1-2B` (`a6bce774`).
