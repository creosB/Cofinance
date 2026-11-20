# Price regimes and news follow-through

> [!WARNING]
> Experimental retrospective research. Neither screen passed its frozen decision rule. Nothing changed in live forecasts. AUC is ranking, not percentage correct, and these scores are not probabilities or financial advice.

[Both machine-readable results](price-news-methods-v1.json) include fold counts, paired intervals, calls and controls. No new market records, source requests, model downloads, package installs or inference were needed. Each command uses the cache-only simulation guard. A missing cache is an error, not a reason to fetch replacements.

## 1. Can price history supply a useful prior?

`qualify/regime_news.py` (`235913a`, protocol `c9696473101a6288269fbfc03a57f58b61f6c1130864e69d57710143088b7310`). Rules, candidate, controls and thresholds were frozen before reading this run's results. The panel of news outcomes was already familiar; freezing a new method does not make those outcomes fresh.

A class-balanced ridge logistic model combines normalized short/medium returns, moves relative to SPY, fast/slow volatility, and interactions with the market trend and volatility. It learns scales on the training set only. Each annual fold trains on up to 1,260 prior sessions; training targets must have exited strictly before the test cutoff. Every fifth SPY origin predicts next-session open to fifth-session close. BTC is mapped to SPY dates. No expanding search over feature sets, penalties or thresholds.

The historical price screen covers **3,134 asset forecasts at 392 distinct origins** across eight annual folds (2019 through October 2026). Model AUC **0.479**, balanced accuracy **0.481**; 20-session momentum **0.512 / 0.508**. Annual AUC cleared 0.5 in only **one of eight** folds. Model-minus-chance AUC -0.021, paired interval [-0.050, +0.006]; model-minus-momentum -0.033 [-0.073, +0.009]. Poor point estimates, not proof of a reliably negative edge or a case for inverting the model. On the full pre-news grid, three missing-feature and five flat rows were excluded across training and testing.

The news panel uses a separately fitted model trained only on exits before October 27. For 96 cases (72 prior dev, 24 from an already scored window), compare price alone, a small clipped signed overlay and a sign-blind overlay given only the number of unique stories. Signed overlay AUC **0.672** vs price **0.619** and price plus count **0.673**. Its difference vs count overlay was -0.001 [-0.042, +0.029]. In the 346 comparable pairs with equal headline count, the signed overlay ranked at 0.618 vs 0.572 for the blind control, a descriptive subgroup, not a validated increment. Shuffling whole title scores within each asset (retaining counts and repeated exposures) matched or beat the signed overlay in 15.25% of 2,000 draws. The earlier/later hybrid AUCs were 0.658/0.588 vs current headline sum 0.625/0.681. The news addition failed its gate as well.

## 2. Does the market corroborate the story?

`qualify/news_followthrough.py` (`a80f903`, protocol `9631b91e26eeb04080ef228adfaf0c70c90477cf4170b1f2c475931c2c62f7e9`). This second hypothesis was frozen after seeing screen 1 and before running screen 2; both use the same known news panel. No claim of independent confirmation.

Cluster titles that share the same first eligible market session; average the signed judge scores in each cluster. Estimate the asset's expected open-to-close move from the preceding 60 sessions and SPY, then measure the realized deviation. Only a full session dated strictly after the news availability day and no later than decision day is usable. A move in the predicted direction increases that cluster's weight; an opposing move reduces it. A pending reaction gets neutral weight. No later price enters the prediction. This captures delayed follow-through, **not** a release-time surprise or proof that the headline caused a move. BTC and US equities do not share identical intraday hours.

| Method on the same 96 cases | Ranking AUC | Balanced accuracy at live threshold | Calls made |
| --- | ---: | ---: | ---: |
| Current headline sum | 0.645 | 0.435 | 62 |
| Group related reaction sessions only | 0.672 | 0.398 | 55 |
| Group and weight by market reaction | 0.688 | 0.404 | 52 |
| Market reaction alone | 0.652 | 0.323 | 40 |

Among calls actually made, the candidate got 33/52 right (63.5%) vs 36/62 (58.1%) for the current method. It abstained ten more times and got three fewer cases right overall. These counts are uniquely implied by the recorded UP/DOWN totals, 35 up and 61 down outcomes, and unrounded all-case balanced accuracy. Different coverage is not a like-for-like precision comparison.

Live-threshold balanced accuracy counts abstentions as misses. The all-direction comparison at zero, treating exact ties as half-credit, also did not improve: **0.574 vs 0.591**. Do not compare all-direction scores with an abstention-penalized figure.

Reaction weighting raised pooled rank AUC by +0.043 vs current, paired interval [0.000, +0.102]. Against grouping alone, the difference was just +0.016 [-0.033, +0.035]. Dev improved 0.625 to 0.687, but the later 24 cases fell from 0.681 to 0.650, and the candidate made only 6 calls there vs 11 currently. On the 74 cases with news, AUC was 0.667 vs 0.597. Good ranking in a slice did not translate into a better call policy. **Keep the current method.**

### Same call budget, exploratory follow-up

After inspecting screen 2, a separate diagnostic fixed one budget before computing it: at each origin retain only tickers with nonzero scores from both methods; each method takes up to four of those, sorted by absolute score, breaking ties by ticker, before looking at outcomes. No budget sweep. The methods can choose different tickers but make equally many calls every day. Out of **47 calls each**, follow-through got **29 right (61.7%)** vs **26 (55.3%)**. The paired five-origin block interval for the change in called accuracy was **-6.5 to +19.1 percentage points**. On dev it gained five correct (23 vs 18 of 35); on the later subset it lost two (6 vs 8 of 12). This is a clue about ranking under a call budget, not a rescue of the failed decision rule. Protocol `3669b1745eebe6b906f6f78f5ab335fa99637c98442d55037673a889c621ead5`.

## Checks and limits

- 96/96 reconstructed targets match the old cached targets; no missing served judgment or cached judgment failure in either screen. 52 distinct asset-title pairs within these cases. There are 91 observable and 37 pending reaction-group occurrences, including repeated groups across origins. The 16 later cases outside the original early score stay excluded.
- Two local Yahoo snapshots agree on open/close across 29,003 overlapping asset-days at relative tolerance 1e-8. This checks storage consistency, not correctness against an independent provider or historical data vintage.
- Intervals: 1,000 paired circular moving-block draws of whole cross-sections, width eight historical origins or five news origins. Only 12 news origins, with overlapping five-session outcomes. The stock universe was selected retrospectively; yearly folds are chronological, not 3,134 independent observations. No price-anchored crowd baseline matches these targets.
- Unit tests use invented data to check training-target purging, unchanged predictions after perturbing future prices, signed vs count-only controls, persistent story shuffles, reaction timing, weekend grouping, and abstention denominators. 13 + 8 focused tests passed; 14/14 + 10/10 deliberate breaks caught and restored tests passed. Full backend suite: 720 tests run, 21 skipped, OK (159 seconds). The 21 new method tests plus 15 invariants passed again after the result write-up. No frontend or production method changed. Neither result is a full-system validation.

## Reproduce with existing files only

Use fresh, ignored output folders and keep the frozen historical runs intact:

```console
python -B -m qualify.regime_news freeze --root data/regime_news_replica
python -B -m qualify.regime_news run --root data/regime_news_replica
python -B -m qualify.news_followthrough freeze --root data/news_followthrough_replica
python -B -m qualify.news_followthrough run --root data/news_followthrough_replica
```

Inputs, implementation and rules are hashed. Each run is one-look and refuses changed inputs or a second score. Reproduction is not new evidence.
