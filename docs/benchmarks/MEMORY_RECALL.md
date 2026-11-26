# Does hybrid memory beat SQLite FTS5 keyword recall on 30 follow-ups?

> [!WARNING]
> Experimental. A frozen one-look benchmark on 30 authored, subagent-reviewed follow-ups across six illustrative sessions (`qualify/memory_benchmark_cases.json`), not live user chats from `data/chat_v2.db` (which still has 0/30 real follow-ups). See [compact result](memory-recall-v1.json).

## Frozen protocol

Protocol `f45da61b4307123859be23d5fcc5eea2974eb6e213ab40d4df93e88fc51ab2de` (commit `0362757`) was frozen and hashed before running in an isolated temporary store (zero touch to `data/chat_v2.db` or `data/hymemory/store`). Six conversations (30 stored turns across NVDA, AAPL, MSFT, TSLA, BTC, ETH) were queried with 30 follow-ups (`limit=2`): 10 lexical follow-ups, 15 zero-overlap paraphrases, and 5 negative/cross-session traps, plus 2 forget/delete checks.

1. **Baseline (`keyword_fts5`):** `LocalKeywordMemory` (SQLite FTS5 with Porter stemming).
2. **Candidate (`hybrid_fts5_hymemory`):** `HybridMemory` (RRF `k=60`) fusing `LocalKeywordMemory` with local `HyMemoryLite` (`Qwen3-Embedding-0.6B-Q8_0.gguf` on loopback `llama-server.exe`, `min_score=0.40`, zero external network calls).

## Sealed result

**Result: hybrid memory keeps 100% lexical parity and recovers 11/15 zero-overlap paraphrases at 118.5 ms p50 with zero cross-session or forget leaks.** No production memory wiring changed (`HybridMemory` is already the default when `HyMemoryLite` is installed).

| Arm | Positive R@1 / R@2 / MRR (25) | Lexical R@2 (10) | Paraphrase R@2 (15) | Negative Clean Abstention (5) | Cross-session Leaks | Forget Leaks (2) | Errors | p50 ms | p95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `keyword_fts5` | 0.4000 / 0.4000 / 0.4000 | 1.0000 (10/10) | 0.0000 (0/15) | 1.0000 (5/5) | 0 | 0 | 0 | 2.17 | 2.94 |
| `hybrid_fts5_hymemory` | 0.8400 / 0.8400 / 0.8400 | 1.0000 (10/10) | 0.7333 (11/15) | 1.0000 (5/5) | 0 | 0 | 0 | 118.48 | 146.89 |

Paired 2,000-resample bootstrap difference (`hybrid - keyword` across the 25 positive follow-ups):
- **Recall@1, Recall@2, and MRR:** `+0.4400` (95% CI `[+0.2400, +0.6400]`)

Split-half check (first 15 follow-ups vs last 15 follow-ups):
- **First half (13 positive, 2 negative):** hybrid `0.8462` R@2 (100% abstention) vs keyword `0.4615` R@2 (100% abstention).
- **Second half (12 positive, 3 negative):** hybrid `0.8333` R@2 (100% abstention) vs keyword `0.3333` R@2 (100% abstention).

Failure breakdown on the 4 missed paraphrases (hybrid):
- Two abstained below `min_score=0.40` (`f14` takeover reserves vs balance-sheet cushion; `f27` network-security incentives/sanctions vs staking validators).
- Two returned a sibling turn in the same session (`f03` buildout/residual liquidity returned packaging/export turns instead of capex/FCF; `f20` utility installations returned auto gross margin instead of energy storage).

## Scope limits and what was not tested

- **Authored, not live user history:** the 30 follow-ups were authored and reviewed by subagents; Baris's live `data/chat_v2.db` remains at 0/30 real follow-ups (`python dev.py memory-eval`).
- **By-design lexical separation:** the 15 paraphrases were deliberately audited to remove Porter-stem overlap with target turns, which reflects the ceiling advantage of semantic recall over pure keyword search rather than the natural lexical-overlap ratio of everyday user chats.
- **Recall only:** measures retrieval precision, abstention, isolation, deletion, and latency, not downstream narrator answer quality.

## Reproduction

```text
python -B -m qualify.memory_benchmark freeze data/memory_bench_new
python -B -m qualify.memory_benchmark run data/memory_bench_new
```

Requires the local `HyMemoryLite` install (`data/hymemory/venv`, `Qwen3-Embedding-0.6B-Q8_0.gguf`, and `llama-server.exe`).
