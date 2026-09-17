# BETTOR EV CORE V1 — FIRST VALIDATION REPORT

*No order placed. No capital deployed. No credential used. `mirror_live = false`.
Every number below comes from sealed evidence already on disk. No venue was
contacted. run85, the frozen substantive capture and the MICRO_LIVE no-submit
boundary are untouched.*

---

## The headline, stated before the detail

**`FAIR_VALUE_STATUS = NOT_YET_VALIDATED`.**

The champion fair-value model is **the venue's own traded price**. No model built
in this pass beat it on unseen chronological events. That is the result, it is
not a placeholder, and it is more useful than a flattering one would have been.

The venue price is not a weak benchmark. On 821 untouched holdout events its
calibration slope is **0.9984** and its intercept **0.0025** — a near-perfect
probability forecast. Beating it requires information it does not already
contain, and the central finding of this pass is that **we currently hold no
such information source.**

---

## 1. Dataset inventory

Built by joining the whale-fill probe corpus to venue settlements, with an
as-of gate that refuses any observation not strictly earlier than the
settlement it is labelled with.

```
DATASET_EVENTS                  4,103
DATASET_MARKETS                 9,335
DATASET_OBSERVATIONS          112,535     (one priced observation of one contract)
DATASET_FILLS (source corpus) 214,609     whale trades, 35 days, BUY-only
DATASET_SETTLEMENTS             9,542     resolved contracts, 38 days
BASE_RATE_YES                   0.4857
DATE_RANGE                      2026-08-06 -> 2026-09-12
```

Join accounting — nothing is silently dropped:

```
OBSERVATIONS_READ                    214,609
ROWS_BUILT                           112,535
NO_SETTLEMENT_FOR_SLUG               100,372   (market never settled in window)
OBSERVED_AT_OR_AFTER_SETTLEMENT        1,684   REFUSED by the as-of gate
OUTCOME_NOT_IN_SETTLEMENT                 18
```

**Those 1,684 refusals matter.** Each was an observation timestamped at or after
the settlement it would have been labelled with. Admitting them would have been
straightforward leakage, and they were caught by a rule rather than by luck.

By sport: Soccer 41,413 · Tennis 40,821 · Other-Sports 13,693 · Non-Sports 6,776
· NFL 4,846 · MLB 4,198 · NBA 565 · MMA 223.

By market family: MONEYLINE 82,302 · TOTAL 16,751 · DRAW 4,247 · SPREAD 2,966 ·
EXACT_SCORE 2,498 · FIRST_HALF_TOTAL 1,872 · HALFTIME 759 · BTTS 666 ·
HANDICAP 474.

**A correction to the brief's premise.** The directive refers to "millions of
reconstructed whale fills". The retained corpus holds **214,609** fill rows. That
is a large and useful dataset, but it is not millions, and 100,372 of those rows
point at markets with no settlement in the window. The usable labelled set is
112,535 observations over **4,103 independent events** — and it is the event
count, not the row count, that sets every confidence interval.

### Which features are causally available, and which are not

Legal at decision time: `P_VENUE_TRADE`, `BEST_ASK`, `ASK_DEPTH_USD`,
`DEPTH_LEVELS`, `WHALE_SIDE`, `WHALE_NOTIONAL`, `SPORT`, `MARKET_FAMILY`.

**Illegal, and enforced as such:** `TIME_TO_SETTLEMENT_S` (uses the future —
diagnostic stratification only, never a model input; scheduled kickoff time
would be the legal analogue and the corpus does not carry it) and `SETTLED_YES`
(the label).

**Absent entirely:** external bookmaker or exchange odds; any sport fundamental
(team strength, lineup, pitcher, weather, rest, travel); any live match state
(score, clock, server, possession); any BETTOR order or fill.

---

## 2. Outcome reconstruction — recovering the event from its contracts

A settlement says a contract paid 1 or 0. It does not say the score. But the
venue lists and settles *many* contracts per fixture, and that set is
over-determined, so the event can be recovered from it: the totals ladder
brackets total goals, an exact-score YES gives the score outright, the moneyline
and draw give the winner, BTTS constrains each side.

Intersecting those constraints over a 13×13 score grid, across all 1,130 soccer
fixtures:

```
UNIQUE (exact score recovered)      156
UNDERDETERMINED                     819
NO_CONSTRAINTS                      155
CONTRADICTORY                         0
TOTAL_GOALS_KNOWN                   230
WINNER_KNOWN                        212
```

**Zero contradictions across 1,130 fixtures.** Every settled soccer contract set
on this venue is mutually consistent with at least one score — a strong
independent check on the venue's settlement integrity, and on the reconstructor.

It did not start that way. The first run reported 29 contradictions, all of the
form `DRAW_YES` and `DRAW_NO` on one fixture. The cause was mine: `-draw$` also
matches `-halftime-result-draw`, and the half-time guard ran after the draw
check. Segment patterns are now tested first, and the comment in the code says
why. A contradiction count is a defect detector, and it worked on its author.

---

## 3. Model zoo results — walk-forward, event-grouped

Five chronological rolling-origin folds over 3,282 events, plus an untouched
final holdout of 821. **Every fixture is wholly in train or wholly in test** — a
random row split would let a model read the answer off a sibling contract on the
same game.

`LEAKAGE_CHECK = CLEAN` (event overlap, chronological order, holdout isolation).

| model | mean log loss | mean Brier | folds |
|---|---:|---:|---:|
| **B0_VENUE_PRICE** | **0.521580** | **0.175247** | 5 |
| B4_GLOBAL_LOGIT | 0.522059 | 0.175416 | 5 |
| B4F_FAMILY_LOGIT | 0.522174 | 0.175447 | 5 |
| B4S_SPORT_FAMILY_LOGIT | 0.524032 | 0.175967 | 5 |
| B3_BASE_RATE | 0.693832 | 0.250341 | 5 |

**The raw market price wins.** Every recalibration is worse, and the more
granular the recalibration, the worse it gets — the signature of fitting noise.

25 model-fold comparisons were run. That count is reported because with this
many looks, a subgroup that appears to beat the market is expected by chance.

---

## 4. The in-sample signal that did not survive

Measured on whale-buy moments, `mean(outcome) - mean(price)` is **+0.85 cents
per $1**, 95% event-clustered CI [+0.12, +1.51]. Broken out, it concentrates:

| slice | edge | 95% CI (event-clustered) |
|---|---:|---|
| Soccer | +1.69c | [+0.49, +2.78] excludes 0 |
| TOTAL | +2.98c | [+1.29, +4.61] excludes 0 |
| EXACT_SCORE | +2.68c | [+1.22, +4.04] excludes 0 |
| MONEYLINE | +0.35c | [-0.56, +1.49] |
| Tennis | +0.12c | [-1.03, +1.41] |

That looks like an edge in the derivative contracts. **It does not survive
walk-forward validation.**

On the predeclared soccer totals + exact-score subset (6,938 out-of-sample rows,
440 independent events), recalibration beat the raw price in 4 of 5 folds — and
the pooled paired advantage is:

```
mean log-loss advantage over raw price   +0.001014
95% event-clustered CI                   [-0.002744, +0.004928]   INCLUDES ZERO
bootstrap draws favouring recalibration  68.2%
subgroup looks taken to find this slice  16   (7 sports + 9 families)
Bonferroni-adjusted alpha for 16 looks   0.0031
```

68% is not evidence. The interval includes zero before any multiplicity
correction, and the subset was chosen *after* seeing the in-sample result — the
textbook way to manufacture a finding.

**This is the most important thing in the report.** The framework's own
discipline killed a result that would have looked like a discovery. Reported as
a failure, not buried.

Two readings remain open and neither is established: the whale may have real
edge in thin derivative contracts that a price-only recalibration cannot
express, or the in-sample slice may be selection noise. Distinguishing them
needs more events, not more model.

---

## 5. Champion, and the untouched holdout

Scored **once**, after the champion was frozen.

```
CHAMPION                       B0_VENUE_PRICE (the venue's own traded price)
HOLDOUT_FROM                   2026-09-08T14:08:18Z
HOLDOUT_EVENTS                 821          HOLDOUT_ROWS  43,677

LOG_LOSS                       0.549570   [0.522561, 0.575301]
BRIER                          0.187784   [0.176726, 0.198869]
RELIABILITY                    0.000184     (≈ perfectly calibrated)
RESOLUTION                     0.061810
UNCERTAINTY                    0.249597
CALIBRATION_SLOPE              0.998364     (perfect = 1.0)
CALIBRATION_INTERCEPT          0.002524     (perfect = 0.0)
SHARPNESS                      0.205170
```

Reliability of 0.000184 against an uncertainty of 0.2496 means essentially all
of the Brier score is irreducible problem difficulty, not miscalibration.

**Why it won:** it is a market price, aggregating everyone who traded. **What
would falsify it:** any forecast that is both calibrated and sharper on unseen
events — which requires information the price does not contain.

---

## 6. Whale signal tests

```
WHALE_SIGNALS_RETAINED     0
WHALE_SIGNALS_REJECTED     1   (whale-buy moment as a price correction —
                                fails walk-forward, CI includes zero)
WHALE_SIGNALS_UNTESTABLE   4   (pair-completion hazard, slicing, capital
                                recycling, residual-inventory destruction —
                                all need per-account position sequences the
                                joined corpus does not carry)
```

A structural limit worth naming: the corpus is **BUY-only**. There are no whale
sells in it, so exit behaviour, pair completion and residual destruction — the
specific lessons §6 asks for from Ferrari, SwissTony, kch123 and w2c33 — cannot
be estimated from this dataset at all. Not "not yet measured": not present.

---

## 7. What was built, and what was not

**Built and exercised on real data:** outcome reconstruction; the as-of gated
dataset builder with full join accounting; proper scoring with Brier
decomposition, reliability curves, calibration slope/intercept and
event-clustered bootstrap; chronological event-grouped walk-forward with a
leakage check and an isolated holdout; a five-member model zoo with shrunken
per-group recalibration.

**Built, not validated:** the event-distribution model (Poisson / Dixon-Coles
families with coherent contract derivation and monotonicity tests) — the
reconstructed-score sample of 156 fixtures is too small to fit team strengths
without fitting noise.

**Not built, blocked on data:** external consensus and de-vig (no bookmaker
odds); fundamental models (no sport data); live-state models (no match state);
microstructure and execution fair value (the tick sample has a frozen touch);
BETTOR fill hazard and conditional-on-fill toxicity (no BETTOR order has ever
existed); ensemble pooling (only one expert exists — pooling a forecast with
transformations of itself cannot add information).

**Not built, deliberately:** Kelly sizing, admission thresholds, and the action
Q-value layer. §21 and §24 are explicit that these are downstream of a validated
fair value, and there is not one.

---

## 8. The §35 hard standard, item by item

| requirement | status |
|---|---|
| 1. no leakage | **PASS** — leakage check clean; 1,684 as-of violations refused |
| 2. calibration ≥ strongest baseline | **FAIL** — no model matches B0 |
| 3. proper score competitive | **FAIL** — every challenger is worse |
| 4. coherent across linked families | **NOT TESTED** — needs a fitted distribution |
| 5. enough independent events | **PARTIAL** — 4,103 overall; 440 in the candidate slice |
| 6. no dependence on future whale behaviour | **PASS** — features are as-of only |
| 7. no invalid cross-event weighting | **PASS** — every interval event-clustered |
| 8. uncertainty reported honestly | **PASS** |

Four of eight pass, two fail, one is untested and one partial. The standard is
not met. **`FAIR_VALUE_STATUS = NOT_YET_VALIDATED`.**

---

## 9. The honest diagnosis

The binding constraint is **not** modelling sophistication. Adding gradient
boosting, neural sequence models or a deeper ensemble to a dataset whose only
probability feature is the market price cannot produce information the price
does not already contain — it can only relearn the price and add variance.

BETTOR holds exactly **one** probability source. §3 and §5 of the brief call for
a consensus layer and an independent fundamental model precisely because a
single-expert ensemble is not an ensemble. Neither input exists in the retained
data.

**The shortest path to a champion that beats the market is a second opinion, not
a better fit to the first one.** In order of expected value per unit of effort:

1. **An external odds feed** (§3). Turns `P_EXTERNAL_CONSENSUS` from blocked into
   available, makes Štrumbelj's de-vig work testable, and gives Ranjan &
   Gneiting a second expert to pool. Single highest-value missing input.
2. **A sport fundamentals feed** for one league (§5) — fixtures, results,
   lineups. Enables the event-distribution model already built, and supplies the
   first probability genuinely independent of any market.
3. **More settled events.** 38 days is thin for team strengths; the corpus grows
   on its own with time, at no cost but patience.
4. **BETTOR's own resting orders** (§14) — the only route to fill hazard and
   conditional-on-fill value, and out of scope under the current boundaries.

Until at least the first of these exists, the correct fair value for BETTOR is
the venue price, the correct EV is the one that follows from it, and the correct
status is the one reported at the top of this page.
