# Method note: what transfers

> [!WARNING]
> **Experimental research tool.** Not a trading signal or financial advice. Cofinance never trades. This page describes how Cofinance is measured, not a claim that it predicts anything. The numbers live in the [scorecard](SCORECARD.md).

Cofinance is a small local model inside a multi-agent research loop, judged against markets that rarely reward anyone. Most of what we built to keep that honest has nothing to do with finance. These seven habits carry over to any system where a model narrates or decides from tool outputs.

## 1. Freeze, then look once

Every forecast is sealed before its outcome exists: inputs, prompt, model weights and the scoring rule go into a file whose SHA-256 is published in a public commit. The commit timestamp is the independent proof of order.

- **One look.** A sealed window is scored exactly once. The scorer refuses a second run, and the [window seal](benchmarks/window-2026-11-09-headline-sealed.json) is bound to its hashes.
- **Late ideas get their own seal.** An idea found after looking at dev outcomes (the mean instead of the sum of headline judgments) is registered as an [addendum](benchmarks/window-2026-11-09-headline-mean-addendum.json) on the same sealed judgments. The code refuses to register it if any exit price is already on disk.
- **Decisions are code, not a reading.** When a second model is sealed as a competing arm, its switch rule is published with the seals. After both are scored, Python applies that rule and writes the decision once. Nobody interprets two reports.
- **A spent set stays spent.** Once a held-out set has been looked at, the next candidate tested on it is labeled a screen, and confirmation waits for fresh data.

**Transfers to:** any evaluation where you can tune. Without a seal, a good number only describes how hard you looked.

## 2. Record forward when the past leaks

A replay is only honest if every input existed at the replay time. Our live news store expires old rows and no point-in-time archive exists, so a replay over past prediction markets would quietly feed the model later news. The [Polymarket replay](benchmarks/pm-replay-protocol-v3.json) is therefore recorded forward: markets are picked and forecasts sealed at time T, and they are scored after settlement. The weekly [forward ledger](benchmarks/FORWARD.md) works the same way, hash-chained and published before the US open. Both state up front how many matured cases they need before any claim (100 events and 26 batches).

**Transfers to:** anything with a moving information set (news, prices, documents, user behavior). Slower, but the only design that cannot leak.

## 3. Screen one stage, reuse the rest

A full replay reruns every agent: about 35 minutes for 72 cases on a CPU. Most changes touch one stage. When the upstream reports are byte-identical to sealed ones, the screen reuses them and reruns only the changed stage: all 72 cases in 1 to 10 minutes. A quicker smoke pass takes the first and last origin per asset, chosen without outcomes and treated as diagnostic only; the first two took 76 and 93 seconds and rejected both candidates. A full-team run is still required before anything is adopted.

**Transfers to:** multi-step pipelines generally. Cache by content hash, not by name, so a changed upstream can never pass as cached.

## 4. Measure skill that ignores the base rate

In a rising market, "always up" looks smart; in a falling one it looks dumb. Neither is skill. Direction is scored as **ranking AUC**: does the system put more weight on UP for the assets that then rose? A constant call scores exactly 0.50 whatever the market did. That turned up two things accuracy hid: the team's one-word call is 0.50 by construction, and a momentum rule with 52.8% accuracy ranks at 0.406, so its accuracy was window luck. Probabilities are scored by **paired Brier against the strongest available reference**. On prediction markets that reference is the crowd's price, not a coin. Abstentions and failures are counted, never dropped.

**Transfers to:** any classifier or forecaster on an imbalanced or drifting target. Pick the metric whose "no skill" value cannot move.

## 5. Trace every number

A model that narrates tool outputs can invent figures that read perfectly. The grounding check matches every number in replayed answers and summaries to a tool value or a served headline: 1,006 of 1,006 traced, and a planted invented number is caught. The limit is stated next to the result: it uses the same matcher as the runtime guards, so it confirms values, words and signs, not meaning.

**Transfers to:** any RAG or tool-using answer. Count untraced numbers before reading a single answer for quality.

## 6. Let code say what the model cannot say faithfully

The 1.7B narrator kept turning headlines into new claims: wrong sources, reversals, a link called "a company announcement". Four prompt and guard variants each beat the old prompt, and none met a rule written before its data. Every new guard just moved the error somewhere else. The News Researcher is now a Python digest: the served headlines quoted verbatim and grouped by source, with no model call. It is faithful by construction, and it costs one model call fewer per question. Models still classify and narrate where their output can be checked; Python owns every number, every write and every decision rule.

**Transfers to:** small-model products. When a check keeps catching the same class of error, remove the model from that step instead of adding a fifth guard.

## 7. Count a check only after it catches a planted break

A passing test proves little until it fails for the right reason. Each new rule gets one deliberate break: a script swaps in a wrong line, runs the tests, restores the file, and reports caught or missed. For example, all 16 frozen Polymarket rules each catch their break. First passes regularly find gaps: a verdict that ignored its interval, a status path with no test. They also find tooling traps worth knowing:

- A killed run can leave its break in the file, so always confirm the restore.
- A two-line anchor can miss on Windows line endings, which shows as "not run", not "missed".
- A same-length break restored within the same second can leave stale bytecode, so run break scripts without it.

**Transfers to:** any test suite. Mutation testing is cheap when it is one script per change, not a framework.

## What this method does not give you

It gives no independent proof that a forecast works: no window has been scored yet. It cannot replace a human reading answers for meaning. And a small, correlated sample stays small however carefully it is sealed. Each result page states its own limits next to its numbers.

[Scorecard](SCORECARD.md) · [Replay and screening](benchmarks/REPLAY.md) · [Forward ledger](benchmarks/FORWARD.md) · [Polymarket calibration](benchmarks/POLYMARKET.md) · [Polymarket replay protocol](benchmarks/pm-replay-protocol-v3.json)
