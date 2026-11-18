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

One run on 2026-11-19, after the rules were published. Compact result:
[think-lab-v1.json](think-lab-v1.json).

| | Thinking off | Thinking on |
| --- | --- | --- |
| Smoke gates (narration, number faithfulness) | passed | passed, 0 incomplete |
| Median seconds per chat answer | 0.39 | 2.55 |
| Median completion tokens per chat answer | 36 | 426 |
| Headline ranking AUC, 72 dev cases | **0.625** (halves 0.621, 0.617) | **0.508** (halves 0.475, 0.528) |
| Origin-cluster interval | 0.592 to 0.672 | 0.409 to 0.600 |
| Headline labels UP / DOWN / ABSTAIN | 13 / 9 / 18 | 22 / 8 / 10 |
| Near certain (top label at least 0.99) | 15 of 40 | 32 of 40 |
| Median seconds per headline | 0.31 | 2.92 |

**Decisions under the frozen rules.**

- **Chat:** the thinking arm passed every gate, so an opt-in Deep think switch is allowed. It costs about 6.6
  times the time and 12 times the output tokens per answer. Whether to build it is Baris's call; it needs the live
  narrator restarted with thinking flags and qualified on that fingerprint.
- **Headline judgments:** not a candidate. Thinking lost the ranking skill the thinking-off judge showed on this
  dev window, leaned UP, and became near certain on most headlines, which flattens the graded scores the ranking
  needs. Headline judging stays thinking-off.

These are dev inputs with known outcomes; the gap is a reason not to pursue thinking for judgments, not a
confirmed property of the model.

## Not tested

- Other thinking budgets; a different budget is a new run.
- Answer quality beyond the smoke gates (number faithfulness and length).
- Unseen data: these are dev inputs with known outcomes.
