# Readout coherence check (v1)

> [!WARNING]
> Experimental dev screen of how a probability is read from the local model. Not a forecast, trading signal or
> financial advice. Cofinance never trades.

Before any market outcome is scored, a probability has to behave like one. This check asks the default narrator
(AtomicChat LFM3 1.7B, AD-Q4_K_M, Vulkan GPU, thinking off) questions whose answers are either printed on its own
input card or tied together by logic, and reads every answer three ways. No market outcome is read. Passing would
be necessary, not sufficient, for forecasting skill.

Rules: [readout-check-protocol-v1.json](readout-check-protocol-v1.json), frozen and published before the run.
Code: `qualify/readout_check.py`. Before freezing, a plumbing smoke on invented prices only checked that each
readout parses on the real runtime; nothing was tuned on it.

## What runs

- **Inputs:** the price cards (last close, one-year high and low) of Bitcoin, Ethereum and NVIDIA sealed in
  Polymarket v3 record 1, with the same prompt shape and "no accepted news" line as the replay.
- **Known answers (24):** "Was the last close / one-year high / one-year low above X?" with X a multiple of the
  printed value, half true and half false.
- **Ladder (21):** "Will the price be above X on November 25?" for X from 0.7 to 1.3 times the last close.
  Probabilities must not rise as X rises.
- **Complements (9):** the same question with "below" at 0.9, 1.0 and 1.1 times the close. Above plus below
  should be close to 1.
- **Swapped labels (9 per label arm):** the instruction asks for NO when the event happens. A coherent readout
  gives the same event probability.
- **Readouts:** `prefix-v2`, the first-token YES share the Polymarket replay uses; `label`, a JSON YES/NO label
  read from the label token's probabilities, as headline judging reads labels; `stated`, a JSON whole number from
  0 to 100.
- **Gates:** known-answer AUC at least 0.90 and accuracy at least 0.75; complement error and swap error at most
  0.15; ladder violations and failures at most 10%. Every gate must hold.

## Result

One run on 2026-11-19 (180 calls, 90 s). Compact result: [readout-check-v1.json](readout-check-v1.json).

| | prefix-v2 (replay readout) | label | stated |
| --- | --- | --- | --- |
| Known-answer AUC (0.5 = no skill) | 0.63 | 0.73 | **0.48** |
| Known-answer accuracy | 50% | 46% | 54% |
| Complement error (above + below vs 1) | **0.99** | 0.23 | 0.09 |
| Ladder steps that rise | 0% | **50%** | 17% |
| Swap error | 0.00 | 0.31 | not asked |
| Failed readouts | 0% | 0% | 0% |
| Verdict | not coherent | not coherent | not coherent |

**What the answers look like.**

- **prefix-v2** gives about 0.996 YES to every question, true or false, above or below, so "above X" and "below
  X" both come out near certain. Its 0% ladder violations and 0.00 swap error are not coherence: a constant
  answer cannot rise or flip. This is the readout behind the Polymarket replay's team arm, and it matches record 1
  (about 0.99 YES on all 18 markets).
- **label** ranks true facts somewhat above false ones (AUC 0.73) but puts most of both below 0.5 (averages 0.30
  and 0.21). Its ladder is flat around 0.64 with half its steps rising, and swapping the labels moves the answer
  by 0.31.
- **stated** writes 55 on 26 of 54 questions. Its ladder is flat, and on known facts it mostly writes 0 or 85
  whether the fact is true or false (averages 0.38 and 0.37). It is the only arm whose above and below roughly add up, because 55 and 45 do.

**Decision under the frozen rules.** No readout is coherent on these checks. No model-read probability is used
as a forecast. The next accuracy step starts from a Python baseline (price, volatility, horizon) and adds model
features only if they improve ranking and Brier on held-out data. The Polymarket replay records continue under
their own frozen rules; their team arm should be read as a near-constant YES, not as the model's view.

## Not tested

- Other prompt shapes, larger models, thinking on, or few-shot examples; each is a new protocol.
- Other assets or dates: 3 price cards, 54 questions, one run.
- Whether the model reads the card at all in other tasks: chat answers are checked number by number and do not
  depend on this readout.
