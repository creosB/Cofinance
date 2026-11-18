# Deep think dev screen (v1)

> [!WARNING]
> Experimental dev screen. Known dev outcomes and small samples. Not a forecast, trading signal or financial advice.

Does the narrator's thinking mode earn its cost? LFM3 can reason before it answers. Cofinance runs it with
thinking off everywhere, and the narrator was qualified that way. This screen turns thinking on for the default
narrator (AtomicChat LFM3 1.7B, AD-Q4_K_M) on the same runtime build and GPU backend, and compares it with the
existing thinking-off results on the same inputs.

Rules: [think-lab-protocol-v1.json](think-lab-protocol-v1.json), frozen and published before any thinking output
on these inputs. Code: `qualify/think_lab.py`.

## What runs

- **Thinking settings:** `--reasoning on --reasoning-budget 384 --reasoning-format deepseek` and
  `enable_thinking: true` per request. Thinking text goes to a separate field; only the answer is scored.
- **Stage A, chat narration:** the frozen narrator smoke cases and gates, thinking on. The thinking-off side is the
  existing qualification on the same model, runtime and flags, pinned in the protocol by its fingerprint.
- **Stage B, headline judgments:** the 40 unique headlines of the 2026-11-17 AtomicChat dev screen, thinking on,
  scored with the same Python aggregation and ranking (AUC over 72 dev cases) as that thinking-off screen.
- **Cost:** median seconds and completion tokens per call, both arms.
- **Near-certain share:** how often the top label carries at least 0.99 of the label mass. A probe on made-up
  inputs showed a thought-through label coming out near certain, which would flatten the graded scores the ranking
  uses.

## Decision rules

- **Chat Deep think switch:** offered as an opt-in only if the thinking arm passes every frozen smoke gate.
  Default stays off. The cost is reported, not gated. The switch never changes headline judgments, Polymarket
  records or any sealed benchmark.
- **Headline judgments:** thinking becomes a candidate for the next unseen window only if at most 2 judgments
  fail and the case-score AUC is above 0.55, above the thinking-off AUC, and above 0.50 in both halves. Otherwise
  headline judging stays thinking-off. Nothing is adopted from dev results.

## Result

Pending: one run after the rules were published.

## Not tested

- Other thinking budgets; a different budget is a new run.
- Answer quality beyond the smoke gates (number faithfulness and length).
- Unseen data: these are dev inputs with known outcomes.
