# Replay benchmark: historical research and decisions

> [!WARNING]
> **Experimental dev-window results.** No predictive edge demonstrated. Not a trading signal or financial advice. Cofinance never trades.

## Method

`qualify/replay.py` runs the live router, Data Analyst, News Researcher, Team Lead and guards with historical replacements for the two data-tool ports. An **additional benchmark-only Decision call**, not a production forecast feature, returns UP, DOWN or ABSTAIN from the accepted team answer and specialist summaries.

- Market bars are restricted to origin T. News must pass the live gate and be available within seven days before the end of T, UTC.
- Target: next session open to the fifth subsequent US-session close.
- Baselines: always-UP, trailing 20-session momentum, and the majority direction of served news items.
- Predictions are sealed before scoring. Existing sealed variants are never overwritten.
- Tests include poisoned future prices. Three deliberate breaks were caught: removed news cutoff, future-derived all-time high, and scoring without a seal.

## Development window

Nine SPY-session origins, 2026-10-27 through 2026-11-06, across NVDA, AAPL, MSFT, AMZN, TSLA, AMD, GOOGL and BTC: **72 correlated cases**, not 72 independent weeks. Of 213 unique news items, 70 passed the gate. Outcomes rose in 31 of 72 cases.

## Results

| Arm | Accuracy, all 72 | Balanced accuracy | Labels |
| --- | --- | --- | --- |
| v1 | 41.7% | 0.484 | 71 UP, 1 error |
| v2 | 43.1% | 0.500 | 71 UP, 1 error |
| v3 | 0% including abstentions/errors | 0.000 | 63 ABSTAIN, 9 errors |
| Always-UP | 43.1% | 0.500 | 72 UP |
| Momentum20 | 52.8% | 0.507 | 25 UP, 47 DOWN |
| News rule | 38.9% | 0.404 | 29 UP, 17 DOWN, 26 ABSTAIN |

News-rule accuracy among its 46 directional calls was 60.9%. v3 made **no directional calls**, so its accuracy among calls is undefined, not zero. Errors and abstentions remain misses in the all-case metric. Zero missing outcomes and zero logged date-cutoff violations were reported. A date audit is not proof against every form of leakage or training contamination.

### v1: unrestricted specialist output

The Data Analyst hit its token cap in 51 cases. Team Lead call errors occurred in seven; 59 final candidates passed the guards. Median case latency was 38.7 seconds. The near-constant UP output did not demonstrate useful direction discrimination.

[v1 measured report](replay-v1-2026-11-16.json)

### v2: stop the Data Analyst at its first blank line

A four-case diagnostic found usable first paragraphs followed by repetition until the token cap. v2 added a first-blank-line stop, without increasing the 256-token budget.

| Measurement | v1 | v2 |
| --- | --- | --- |
| Data Analyst token-cap errors | 51 | 0 |
| Analyst summaries passing guards | 21 | 72 |
| Team Lead call errors | 7 | 6 |
| Final answers passing guards | 59 | 61 |
| Median seconds per case | 38.7 | 28.9 |

The analyst stop is the live default. This is a reliability improvement, **not evidence of better forecasts**: direction remained UP on every valid call. The accuracy difference reflects the error moving between cases. News and Team Lead fallbacks remain; the whole pipeline is not failure-free.

[v2 measured report](replay-v2-2026-11-16.json)

### v3: counting instruction, not adopted

The Decision prompt instructed the model to sum last-session price direction and bullish/bearish headline counts, then choose UP, DOWN or ABSTAIN. It returned 63 abstentions and nine malformed responses under the existing 24-token output budget.

A Python reference computed from raw snapshots and served-news labels produced 31 UP, 28 DOWN and 13 ABSTAIN, with 26 of 59 calls correct. Only 11 of 72 labels matched the model. **This is not an isolated arithmetic-adherence test:** the model received narrative reports, which can omit individual labels, while the reference used raw items. Truncation and the response contract are additional confounds. These results do not establish that the model is incapable of arithmetic.

[v3 measured report](replay-v3-2026-11-17.json)

## Fast Decision screening

`qualify.decision_lab` reuses a sealed replay's **exact model-visible team reports**. It does not rerun the router or specialists, collect data, or expose outcomes during prediction. Structured labels are validated; malformed replies stay errors. Source, plan, prompt and prediction hashes are checked before scoring.

```text
python -m qualify.decision_lab run --root data/replay_20261116 --out data/screen_contract --candidate contract-v1
python -m qualify.decision_lab score --root data/replay_20261116 --out data/screen_contract
```

The default diagnostic selects the first and last origin per asset without looking at outcomes. `--sample full` explicitly screens all cached cases. The supplied contract and evidence-guidance candidates are experiments, not adopted product behavior.

This is a cheap way to reject broken candidates, **not a substitute for end-to-end validation**. A shortlisted candidate still needs a full-team check and independent completed windows. Changing an upstream agent invalidates downstream cached reports.

## Cached diagnostic results, 2026-11-17

Both candidates were fixed before their runs and used identical saved v2 inputs: first and last origin per asset, 16 cases across two correlated days. These are diagnostic dev results, not full-system accuracy estimates.

| Candidate | Correct / all 16 | UP / DOWN / ABSTAIN / error | Directional coverage | Decision-loop seconds |
| --- | --- | --- | --- | --- |
| Cached v2 reference | 4 / 16 | 16 / 0 / 0 / 0 | 100% | Not rerun |
| Structured output, contract-v1 | 2 / 16 | 3 / 2 / 11 / 0 | 31.3% | 75.6 |
| Evidence guidance, evidence-v1 | 2 / 16 | 5 / 2 / 9 / 0 | 43.8% | 92.5 |

Both were rejected without another 72-case full-team run. They produced valid output, including DOWN labels, but did not improve all-case accuracy. Abstentions count as misses in that metric; accuracy among directional calls was 2/5 and 2/7 respectively. Always-UP scored 4/16, momentum20 9/16, and the news rule 5/16 (5/8 called).

The contract candidate tied the source on the early day and worsened on the late day; the guidance candidate improved on the early day and worsened on the late day. Origin-cluster bootstrap intervals for the all-case accuracy difference versus cached v2 were [-0.25, 0.00] and [-0.375, 0.125]. **Two clusters are insufficient for reliable inference**, and neither result establishes an improvement. No missing outcomes or recorded date-cutoff violations. Timing excludes model startup and includes incidental PC load; this is not a controlled speed comparison. Upstream agents were not rerun.

[Compact results, per-asset and split checks](decision-screen-2026-11-17.json)

### signals-v1: a Python fact card, all 72 cached cases

The narrative reports compress or drop per-headline gate labels and price facts. signals-v1 keeps the contract-v1 output schema and appends a card that Python builds from the same point-in-time tool outputs: last-session change, position in the 52-week range, distance below the all-time high, fear and greed for BTC, and every served headline with its gate label, plus label counts. No dates, outcomes or baselines. The shortlist rule was committed before the run: at most two malformed replies, and balanced accuracy above 0.500 overall and in both halves of the origins.

| Arm | Correct / 72 | UP / DOWN / ABSTAIN / error | Correct among calls | Balanced accuracy |
| --- | --- | --- | --- | --- |
| Cached v2 reference | 31 | 71 / 0 / 0 / 1 | 31 / 71 | 0.500 |
| signals-v1 | 28 | 20 / 30 / 22 / 0 | 28 / 50 | 0.381 |
| Always-UP | 31 | 72 / 0 / 0 / 0 | 31 / 72 | 0.500 |
| Momentum20 | 38 | 25 / 47 / 0 / 0 | 38 / 72 | 0.507 |
| News rule | 28 | 29 / 17 / 26 / 0 | 28 / 46 | 0.404 |

**Not shortlisted.** Balanced accuracy was 0.382 and 0.406 in the two halves; abstentions count as misses in that metric. The paired all-case difference versus cached v2 was -0.042 (origin-cluster interval -0.181 to 0.097, nine correlated clusters), which includes zero.

What changed is behavior: the same model on the same upstream reports moved from 71 UP to 20 UP, 30 DOWN and 22 ABSTAIN with zero malformed replies, and agreed with the news rule on 47 of 72. On the 50 cases it called, it was right on 28; always-UP was right on 22 and momentum20 on 26 of those same cases. DOWN calls were right 18 of 30 and UP calls 10 of 20, in a window where most outcomes fell. This is descriptive dev evidence that information lost in the summaries limited the Decision step. It is **not** a predictive improvement.

The screen took 6.0 minutes for 72 cases (median 4.97 seconds per Decision call, excluding model startup), versus about 35 minutes for a full-team replay at the v2 median.

### prob-v1: label probabilities, Python makes the call

Same prompt and card as signals-v1. The local runtime also returns the log-probability of each label at the label token; Python makes the call: UP when P(UP) / (P(UP) + P(DOWN)) is at least 0.6, DOWN at 0.4 or less, otherwise ABSTAIN. The measure is **ranking skill**: AUC of log P(UP) minus log P(DOWN) against the outcome, where 0.5 is no skill whether the market rose or fell, so a constant UP call scores exactly 0.5. Rule committed before the run: AUC above 0.55 overall and above 0.50 in both halves, at most two failures.

| Score | AUC, all 72 |
| --- | --- |
| prob-v1 model score | 0.511 (halves 0.555 and 0.491) |
| Net headline labels from the gate, context only | 0.556 |
| 20-session return, context only | 0.406 |

**Not shortlisted.** Origin-cluster interval for the model score 0.41 to 0.61. Python's call: 29 UP, 38 DOWN, 5 ABSTAIN, zero failures, balanced accuracy 0.478. The model's own labels matched signals-v1's counts exactly (20 / 30 / 22), as expected at temperature 0 with the same input. DOWN was absent from the top 20 tokens in 6 cases and took the floor.

On this window the model's confidence does not order outcomes. The gate's headline labels order them slightly better, without an interval and over nine correlated days, so that is context, not a finding. The 20-session return ranks below 0.5: momentum's 52.8% accuracy here came from calling DOWN in a falling window, not from ordering assets. Ranking skill is the measure for later candidates. 346 seconds for 72 cases.

### headline-v1: the model judges each headline, Python adds them up

Each headline the team was served is judged once, from the asset and the title only (no gate label): UP, DOWN or ABSTAIN, read as probabilities. A case scores the sum over its headlines of (P(UP) minus P(DOWN)) divided by all label mass, so a no-effect judgment counts near zero; cases without news score 0. Rule committed before the run: AUC above 0.55 and above the gate labels' own AUC on the same cases, above 0.50 in both halves, at most two failed judgments.

| Score | AUC, all 72 | Early half | Late half |
| --- | --- | --- | --- |
| headline-v1 | **0.585** | 0.566 | 0.622 |
| Gate net labels, context only | 0.556 | | |
| 20-session return, context only | 0.406 | | |

**Shortlisted: the first candidate to pass its rule.** Origin-cluster interval 0.525 to 0.654. 40 unique headlines judged in 55 seconds (median 1.4 seconds each), zero failures; the model disagreed with the gate's label on 15 of 40. Python's call at plus or minus 0.5: 32 UP, 14 DOWN, 26 ABSTAIN, right on 27 of 46 calls. The call lines were not tuned and abstentions keep balanced accuracy at 0.404, so what passed is the ranking, not the call.

Limits: dev window of nine correlated days in a falling market, 40 headlines, 15 cases without news. The margin over the gate labels has no paired interval. Shortlisted means it goes to unseen data next; it is not adopted.

### pair-v1: rise and fall asked as two separate questions

Two yes/no questions per case over the signals-v1 input, "will it close higher" and "will it close lower", each read as log P(YES) minus log P(NO); the case score is rise minus fall, so a model that says yes to everything, or always leans UP, cancels to zero. Same rule as headline-v1, committed before the run.

**Not shortlisted.** AUC 0.540 (halves 0.614 and 0.504), origin-cluster interval 0.436 to 0.668, below the gate labels' 0.556. Zero failures. Python's call: 25 UP, 39 DOWN, 8 ABSTAIN, right on 33 of 64 calls. 144 model calls in 622 seconds (median 8.6 seconds per case). Asking both directions removed the UP lean but did not add ordering beyond what the headline labels already carry.

## Unseen window: headline-v1 sealed before the outcomes

The shortlisted headline-v1 ran unchanged on origins 2026-11-09 to 2026-11-13 (40 cases, 29 with news, 34 unique headlines, zero failures), using only the session calendar and the gated corpus; no prices enter its prediction. Calls: 10 UP, 12 DOWN, 18 ABSTAIN. Sealed 2026-11-17 06:37 UTC, before most of these exits (2026-11-16 to 2026-11-20) had closed and with no outcome on disk; the hashes are [published here](window-2026-11-09-headline-sealed.json). Confirmation needs AUC above 0.50 with the origin-cluster interval excluding 0.50.

**Scored 2026-11-19 (one look, early).** The window was scored before it fully matured, on prices through 2026-11-18, the last completed US session. Origins 11/09 to 11/11 had their exits; the 16 cases at 11/12 and 11/13 did not, and they stay unscored because the scorer refuses a second look. On the 24 scored cases (3 origins, 4 of 24 rose):

| Arm | AUC | Origin-cluster interval | Python call (UP / DOWN / ABSTAIN) | Under the frozen rule |
| --- | --- | --- | --- | --- |
| headline-v1, base judge, sum | 0.681 | 0.619 to 0.857 | 6 / 6 / 12 | confirmed |
| Mean addendum, base judge | 0.669 | 0.619 to 0.857 | | confirmed, but below the sum: live stays on the sum |
| headline-v1, AtomicChat judge, sum | 0.681 | 0.619 to 0.857 | 5 / 6 / 13 | confirmed, at least the base: AtomicChat stays the live judge |

Read it with its limits: three origin clusters make the interval coarse, the week fell, and counting the gate's own headline labels ranked almost as well (0.663; the 20-session return 0.625). Called cases were right 7 of 12 (base) and 7 of 11 (AtomicChat); balanced accuracy over all 24 was 0.375 because abstentions count as misses. The rule passed; a general claim needs more windows. [Compact result](window-2026-11-09-headline-result.json)

**Dev screens after the window (2026-11-19, post hoc, every outcome already seen).** Pooling the 72 dev cases and the 24 scored window cases (96 cases, 12 origins, 61 unique headlines), two cheap checks looked for a change that helps on both parts and for both judges:

- **Aggregation (cached judgments, no model calls):** mean, recency weighting (half-life 2 days), strongest headline, net sign count, story dedupe and per-source averaging. None beat the live sum on both parts for both judges. The mean wins on dev but lost on the window for both judges (base 0.669 vs 0.681, AtomicChat 0.656 vs 0.681). Dedupe and per-source averaging changed nothing: no case had two copies of a story or two sources.
- **Judge prompt with the source kind and the stored first sentence (AtomicChat, rule written before the run: at least +0.02 on both parts):** sum 0.603 on dev (live prompt 0.625) and 0.694 on the window (0.681), so it fails. Rerunning the live prompt reproduced all 61 cached judgments exactly.

The live technique stays as it is. With 61 headlines over 12 origins, the data is too small to tell such changes apart; more news per asset is the lever, not a new aggregation.

Two more entries were registered on the same window before any exit price was on disk, and are scored in the same single look on the same price download:

- **Mean addendum.** On dev, the mean of headline impacts ranked better than the sum (AUC 0.666 versus 0.585). That was found after looking at dev outcomes, so it is registered as an addendum bound to the sealed judgments, not as a replacement: [addendum](window-2026-11-09-headline-mean-addendum.json).
- **AtomicChat arm.** The same technique with the AtomicChat narrator as judge (dev AUC 0.625), sealed with a switch rule published next to its hashes: it replaces the base for headline judging only if its window AUC is above 0.50, its origin-cluster interval excludes 0.50 and its AUC is at least the base arm's. Python applies the rule and writes the decision once: [arm seal](window-2026-11-09-headline-atomic-sealed.json), [comparison](NARRATOR-CANDIDATE.md).

## Grounding: every number traced to a tool

`qualify.grounding` runs the live citation matcher over the sealed v2 outputs, against the same point-in-time tool values and served headlines the team saw.

| Text shown | Numbers | Not traced to a tool or headline |
| --- | --- | --- |
| Final answers (72) | 393 | **0** |
| Data Analyst summaries (72) | 533 | **0** |
| News Researcher summaries (72) | 80 | **0** |

Guards accepted 61 of 72 final answers; 11 fell back to Python-built text (5 empty candidates, 4 unsupported terms, 2 unsupported risks). Data Analyst summaries passed 72 of 72; News Researcher summaries 17 of 72, the rest are Python-built news lists. Since 2026-11-17 the live News Researcher is always a Python digest of the served headlines, so these replay summaries describe the earlier narrated version. Stock fundamentals were unavailable in 63 cases and no answer carries an untraced number that could stand in for one. A planted invented number is reported by the check; 2 of 2 deliberate breaks caught.

Limits: this is value matching with the same matcher the guards use, so it shows the rule holds end to end, not that an independent judge agrees; a number that matches the wrong field still counts as traced. [Grounding result](grounding-2026-11-17.json)

## Limits

- Known dev outcomes and overlapping origins make these descriptive measurements, not independent performance estimates.
- The nine-day window cannot establish a generalizable predictive improvement. No significance claim is made from the point estimates.
- News timestamps are item publish/update/submit times, not archived first-seen times; day-only timestamps count at day end.
- Stock fundamentals lack point-in-time values. Prices are a current download and can contain revisions.
- No second-source cross-check was added for these replay inputs.
- LFM3 was released on 2026-10-27; its training cutoff is unpublished. Release timing and a negative memorization probe cannot prove absence of training contamination.
- No change to the frozen forward protocol or adoption of a new forecast feature follows from these dev results.
