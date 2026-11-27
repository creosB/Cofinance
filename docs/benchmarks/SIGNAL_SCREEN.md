# Offline Financial-Signal Aggregation Screen (Track 2)

> [!WARNING]
> **Experimental.** Offline retrospective screen over existing cached windows (72 dev cases and 24 already-scored 11/09 window cases). Not a fresh unseen evaluation, not a trading signal, and not financial advice. Cofinance never trades.

- **Artifact:** [`signal-screen-v1.json`](signal-screen-v1.json)
- **Code:** `qualify/signal_screen.py`
- **Protocol hash:** `protocol_sha256` recorded inside `signal-screen-v1.json`
- **Baseline:** `sum_v1` (current `headline-v1` sum of AtomicChat headline impacts, call line +/-0.5)
- **GLiNER materiality:** scored offline with standalone ONNX FP32 `GLiNER2.5-Decide` (`include_urgency=False`; inverted urgency head retired)

## Results

### Dev cached replay (n = 72 cases, 9 origins, known outcomes)

| Arm | AUC (all) | Early / Late AUC | News-only AUC | Delta AUC vs `sum_v1` (95% CI) | Bal. acc (all) | Bal. acc (cov) | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sum_v1` (baseline) | 0.6247 | 0.6205 / 0.6165 | 0.5739 | +0.0000 | 0.4363 | 0.5556 | 70.8% |
| `mean_v1` | 0.6939 | 0.7023 / 0.6867 | 0.6823 | +0.0692 (+0.0306 to +0.1097) | 0.3312 | 0.5636 | 51.4% |
| `materiality_weighted_mean` | 0.6806 | 0.7023 / 0.6817 | 0.6613 | +0.0559 (+0.0201 to +0.0980) | 0.3312 | 0.5215 | 56.9% |
| `dedup_mean` | 0.6939 | 0.7023 / 0.6867 | 0.6823 | +0.0692 (+0.0306 to +0.1097) | 0.3312 | 0.5636 | 51.4% |
| `materiality_dedup_source_mean` | 0.7034 | 0.7023 / 0.7118 | 0.6970 | +0.0787 (+0.0376 to +0.1190) | 0.3958 | 0.5425 | 62.5% |

### Retrospective 11/09 window (n = 24 previously scored cases, 3 origins; 16 missing exits not reopened)

| Arm | AUC (all) | Early / Late AUC | News-only AUC | Delta AUC vs `sum_v1` (95% CI) | Bal. acc (all) | Bal. acc (cov) | Coverage |
|---|---:|---:|---:|---:|---:|---:|---:|
| `sum_v1` (baseline) | 0.6813 | 0.6667 / 0.8333 | 0.6442 | +0.0000 | 0.3750 | 0.6458 | 45.8% |
| `mean_v1` | 0.6562 | 0.6667 / 0.7000 | 0.6058 | -0.0251 (-0.1428 to 0.0000) | 0.2000 | 0.5500 | 29.2% |
| `materiality_weighted_mean` | 0.6562 | 0.6667 / 0.7000 | 0.6058 | -0.0251 (-0.1428 to 0.0000) | 0.2000 | 0.5500 | 29.2% |
| `dedup_mean` | 0.6438 | 0.6667 / 0.7000 | 0.5865 | -0.0375 (-0.1428 to 0.0000) | 0.2000 | 0.5500 | 29.2% |
| `materiality_dedup_source_mean` | 0.6438 | 0.6667 / 0.7000 | 0.5865 | -0.0375 (-0.1428 to 0.0000) | 0.2000 | 0.5500 | 29.2% |

## Decision and Scope Limits

- **Decision (`NO_LIVE_SWITCH_WITHOUT_FRESH_UNSEEN_WINDOW`):** Keep live `core/headline_signal.py` on `sum_v1`. While `materiality_dedup_source_mean` gained +0.0787 AUC on the known dev split (CI excluding 0, balanced halves), all mean-based arms trailed `sum_v1` by -0.0251 to -0.0375 AUC on the 24 retrospective window cases (wide cluster CI crossing 0) and dropped coverage from 45.8% to 29.2%.
- **Errors and abstentions:** 0 failed AtomicChat headline judgments in both splits; abstentions remain counted in `balanced_accuracy_all` and `coverage`; the 16 missing 11/09 exits were not reopened.
- **Limits:** Both splits had previously inspected outcomes, headline titles only (no article bodies), and small origin counts (9 dev origins, 3 scored window origins).
