# P_BETTOR_INDEPENDENT V3 — RESULT

Status: RESEARCH ONLY. No orders. No capital. No credentials. `mirror_live=false`.
Nothing here authorises a trade, and nothing here is a production change.

This document answers directive K. It reports what was added, what was measured,
what was withdrawn, and — most importantly — what the measurement is and is not
capable of saying.

---

## 1. The claim that was withdrawn

The previous report said the V2 evaluation ran on a **pregame-proven** sample.
That claim is withdrawn.

It rested on `OBSERVATION_TIME < SETTLEMENT_TIME − 3.1h`. A settlement lag is not
a kickoff time. The venue does not promise a fixed lag, the lag is not
independently verified, and a match that settled late would have admitted an
in-play observation under that rule.

`event_start_time.PREGAME_PROVEN_CLAIM = "WITHDRAWN"`.

### What the venue itself carries

Every retained venue-native source was searched for a start-time field.

| Quantity | Value |
|---|---|
| `VENUE_NATIVE_START_TIME_COVERAGE` | **0.0** |
| Slugs anywhere in the retained capture carrying `gameStartTime` | 2,369 |
| Settled events carrying a venue-native start time | **0 of 5,788** |
| Settled **soccer** events carrying one | **0 of 293** |
| `VENUE_NATIVE_START_TIME_SOURCE` | `NOT_IDENTIFIED_FOR_ANY_SETTLED_EVENT` |

The field exists in the venue's schema. It is not present on a single event this
programme can evaluate. That is a fact about the capture, and it is recorded
rather than routed around.

## 2. Start-time confidence classes

| Class | Meaning | Uncertainty | Observed |
|---|---|---|---|
| A | `VENUE_NATIVE_EXACT` | 0.05 h | **0 events (0.0%)** |
| B | `EXTERNAL_CROSS_VALIDATED` — two independent public sources agreeing after the measured country offset, with a plausible implied settlement lag | 1.0 h | **89 events (80.2%)** |
| C | `APPROXIMATE_OR_UNCERTAIN` | 3.0 h | **22 events (19.8%)** |

Mean uncertainty across the matched set: **1.40 h**.

Horizon claimability on the best class actually available (B):

| Horizon | Claimable |
|---|---|
| T−24h | yes |
| T−6h | yes |
| T−2h | yes |
| T−1h | **no** |
| T−30m | **no** |
| T−15m | **no** |

`TIGHT_HORIZONS_REQUIRE = CLASS_A`, and class A is empty. No T−15m or T−30m
result may be produced from this data, by anyone, until a venue-native start time
exists. Class C may never support a tight horizon under any circumstances.

The 22 class-C events are not class C because the sources disagreed — they are
class C because the implied settlement lag was outside the plausible band, which
is exactly the failure mode the old pregame rule could not see.

## 3. B7 recalibration (§3)

Four methods were fitted on development data only, chronologically, with the Gen2
prospective holdout untouched.

| Calibration | Event-equal log loss | Slope |
|---|---|---|
| Raw B7 | 0.5748 | — |
| Platt | worse | overshoots |
| Beta | worse | overshoots |
| Temperature | 0.6275 | 3.5 |
| Isotonic | refused below 200 events (`ISOTONIC_MIN_EVENTS`) |

`B7_CALIBRATION_STATUS = RECALIBRATED_NO_IMPROVEMENT`.

The post-calibration conditional test on the market is still
`NOT_DETECTED` (−0.01362, 95% CI [−0.03211, +0.00220]).

`CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION = True`. A recalibration that made
the challenger look better would have been a re-labelling of the same forecast,
not a discovery. It did not, and either way the conclusion would have been the
same.

## 4. Shot quality and expected goals

`XG_DATA_STATUS = NOT_IDENTIFIED`.

| Source | Outcome |
|---|---|
| understat.com | `EGRESS_DENIED` (host-scoped policy refuses CONNECT) |
| xgabora `Matches.csv` | `NO_XG_COLUMN_PRESENT` — the 48-column header has no expected-goals field of any kind |
| Fantasy-Premier-League merged data | no xG in the retained seasons |
| openfootball | scores only |

What the model actually carries is `SHOTS5`, `TARGET5`, `CORNERS5` — trailing
five-match means built strictly from earlier matches.

**These are shot quantity and shot accuracy. They are not shot quality.** A
tap-in and a thirty-yard effort are one shot each. Calling them xG features would
be a false provenance claim, and `NO_XG_SERIES_HAS_BEEN_FABRICATED = True`.

Missing xG is a reason the independent model is weaker than it could be, and
therefore a reason **not** to close the fundamental line of research. It is not a
reason to discount the market's observed superiority on this sample.

## 5. B4 repaired (§12)

The V2 B4 was disqualified: it fitted a shared component and then predicted
through an independent-Poisson grid that discarded it. Event-equal log loss
4.068, calibration slope 2.7 × 10⁸. That was a plumbing defect, and it was
never evidence about bivariate Poisson models.

V3 emits the exact bivariate PMF, and the directive's unit test now pins the
property that was violated:

```python
a = V3.bivariate_grid(1.4, 1.1, 0.0)
b = V3.bivariate_grid(1.4, 1.1, 0.25)
assert a != b
assert sum(1 for k in a if abs(a[k] - b[k]) > 1e-9) > 20
```

Verified alongside it: `bivariate_covariance(l1,l2,l3) == l3` exactly, and
`l3 = 0` recovers the independent-Poisson product.

Fitted shared component: **l3 = 0.0401**, log-likelihood gain **+8.59** on 21,556
matches. `B4_REPAIR_STATUS = REPAIRED_SHARED_COMPONENT_DRIVES_THE_EMITTED_PMF`.

## 6. Internal team strength (§6)

The model no longer depends on one repository's provisional Elo. Three internal
challengers are fitted from match results alone, strictly read-before-update:
classic Elo, goal-difference Elo, and EWMA goals for/against — twelve features.

`ELO_SOURCE_PROVISIONAL` from 2025-06-15 onward remains flagged in the ingest
module. The external Elo is now one input among several rather than the spine.

## 7. Evidence ladder and the size of the sample (§10, corrected under §21)

**The ladder published in §10 was wrong, and wrong in the flattering
direction.** Control 21 forced the variance to be computed from the real paired
event-level differences, and the error surfaced immediately.

The old SD of 0.1680 was measured between the market and a **50/50 blend** of
the market with the V2 champion. A half-weight blend sits much closer to the
market than the challenger does, so its differences are much smaller. The
contrast under test is the challenger against B0, so the SD has to be of *that*
difference.

There are in fact **two** ladders, and conflating them was the whole of the
error:

**Standalone** — can the challenger, on its own, be told apart from B0?
SD(D_EVENT) ≈ 0.34.

| Effect | Events for 80% power |
|---|---|
| 0.002 | 236,954 |
| 0.005 | 37,913 |
| 0.010 | **9,479** |
| 0.020 | 2,370 |

**Incremental** — does *adding* the challenger to the market improve the blend?
This is the §14 question. SD(D_EVENT) = **0.0301**, because a stacked blend
differs from the market only by the small amount the fitted coefficient lets the
challenger move it.

| Effect | Events for 80% power |
|---|---|
| 0.002 | 1,777 |
| 0.005 | 285 |
| 0.010 | **72** |
| 0.020 | 18 |

So on the question that matters, **27 test events against 72** — a shortfall of
2.7×, not the 82× previously reported. The 27 events already clear the 0.020
rung. The standalone question remains out of reach and always will be.

`SUPERSEDED_PAIRED_EVENT_SD` and `WHY_THE_OLD_LADDER_WAS_WRONG` are kept in the
register; the number was not quietly replaced.

### Why rows are not tries

The row-level score differences correlate **ρ ≈ 0.55** within a fixture, because
a challenger prices every contract on a match from **one** score grid: a grid
that is wrong for that match is wrong on all ~14 of its rows in the same
direction. Estimating variance from rows and comparing it against the 932 rows
held understates the shortfall by about **6×**. `row_level_sd_forbidden` exists
so that error has a name and a number rather than a warning in a comment.

## 7a. The sign error — correction

The previous report said the negative Q4 point estimates "sit on the improving
side." **That was backwards.** `DELTA_LOG_LOSS` is MARKET_ONLY − BLEND, so
*positive* means the blend is better. All three values were negative, which
means adding the challenger made the held-out forecast **worse** by about 0.013
log loss.

What does not change: the status is still `NOT_DETECTED_AT_THIS_SAMPLE_SIZE`,
the market is still the strongest settlement forecast, and no negative claim
about fundamental alpha is licensed. What does change is the encouraging gloss —
there is no observed tendency for the challengers to help, and the point
estimates lean the other way.

## 7b. Nested calibration and orthogonality (§22)

The conditional-market test was re-run under a strict four-window protocol so
that no probability entering the test was shaped by a test outcome.

```
TRAIN (W0)        87,443 external matches, DATE < 2026-05-01
CALIBRATE (W1)    489 external matches, 2026-05-01 .. 2026-08-01, 1,956 (p,y) pairs
STACK (W2)        40 evaluation events
TEST (W3)         27 evaluation events, 520 rows
```

Evaluation fixtures run 2026-08-07 to 2026-09-02, so **no evaluation fixture
sits inside W0 or W1**. `LEAK_CHECK = CLEAN`, disjointness enforced by *event*,
not by row — two contracts on one fixture share a scoreline.

| | |
|---|---|
| Calibrator selected | **IDENTITY**, by k-fold CV *inside* W1 |
| Δ log loss (blend − market) | **−0.00926** (blend worse) |
| 95% event-clustered CI | [−0.00222, +0.02036] |
| `INCREMENTAL_SIGNAL_STATUS` | `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` |

Two things were learned building this.

**Method selection is part of fitting.** An in-sample comparison inside W1
systematically hands the prize to the most flexible candidate: isotonic scored
0.4925 in-sample against identity's 0.5259, but cross-validated at 0.5702 —
*worse than doing nothing*. Selection is now by k-fold CV inside W1, which
charges for flexibility without touching any outcome outside W1.

**The leak was measured, not assumed.** The forbidden variant was run
deliberately — same protocol, calibrator fitted **on** the test events. The
difference was **0.00000** log loss, because the selected calibrator is the
identity map and an identity map cannot carry outcome information wherever it is
fitted. The control bound nothing on *this* run. That is a fact about this run
and not a reason to drop the control: a run that selects isotonic or beta would
leak, and nothing in the numbers would show it.

One assumption is recorded rather than buried: W1 is external-league fixtures
while W3 is venue contracts, so the calibrator is transported across
populations. `POPULATION_TRANSPORT_ASSUMED = True`.

## 7c. As-of provenance (§23)

Every source now answers three questions before any feature drawn from it may
enter a tight as-of claim: `PUBLICATION_TIMESTAMP_AVAILABLE`, `EVENT_TIMESTAMP`,
`DATA_BECAME_KNOWN_TIMESTAMP`.

| Source | Status |
|---|---|
| Venue settlement / observation stamps | `PROVEN` |
| xgabora match results, shot counts, openfootball | `ASSUMED_BOUNDED` |
| **xgabora Elo** | `NOT_PROVEN` |
| xgabora odds | `NOT_PROVEN` |
| xG — any provider | `NOT_PROVEN` |
| Player availability — any provider | `NOT_PROVEN` |

**Elo is the dangerous one.** The `date` column is the date the rating applies
*to*, not the date it was computed. A rating series regenerated in one pass over
completed history embeds later results in an earlier row, and nothing in the file
distinguishes that from a genuine contemporaneous rating. The internal strength
models of §6 exist partly so the programme is not dependent on it.

Applying the gate to the 37 model features:

- `HIGH_INTEGRITY` lane: **0 of 37 admitted** — 34 `ASSUMED_BOUNDED`, 3
  `NOT_PROVEN` (the Elo trio).
- `EXPLORATORY` lane: 37 of 37.
- `TIGHT_ASOF_CLAIM_ALLOWED = False`.

The gate currently refuses everything the programme holds. **That refusal is the
finding, not a bug in the gate.** `ASSUMED_BOUNDED` means we believe the
publication lag is smaller than the gap to the predicted match but have no
timestamp proving it — admissible to loose horizons and the exploratory lane,
never to a tight as-of claim. It costs nothing today because start-time class A
is empty and no tight horizon is claimable anyway.

For lineups and injuries the binding timestamp is the **announcement** time, not
the match date. That is written into the register in advance, so a future source
must clear it before admission.

`THE_LADDER_WAS_SET_BEFORE_THE_NEXT_RESULT = True`.

## 8. Event-count expansion (§9)

| Quantity | Value |
|---|---|
| `TOTAL_VENUE_SOCCER_EVENTS` | 1,318 |
| `PUBLIC_DATA_MATCHED_EVENTS` | **111** |
| `UNMATCHED_EVENTS` | 1,207 |

Unmatched by reason:

| Reason | Count |
|---|---|
| `LEAGUE_HAS_NO_PUBLIC_SOURCE` | **1,096** |
| `NO_PUBLIC_FIXTURE_IN_WINDOW` | 64 |
| `CLUB_CODE_UNBOUND` | 47 |

91% of the shortfall is leagues the two retained public repositories do not
cover. That is a **data acquisition** problem, not a matching-quality problem. No
improvement in matching fixes it; only more league coverage does.

The identity standard was not lowered to get the number up. No title parsing, no
fuzzy matching, no nearest-start-time join, no single-team inference. The 47
unbound club codes stay unbound.

Of the 111 matched events, **67** carry both a settled contract and at least one
observation this programme is now willing to call pregame; those 67 events (932
rows) are the evaluation set.

## 9. The V3 challengers, measured (§13, §14)

932 rows, 67 events, event-equal weighted, pregame observations only.

| Expert | EE log loss | EE Brier | Slope | Error corr. with B0 |
|---|---|---|---|---|
| `P_MARKET_RAW` | **0.536585** | **0.179498** | 0.681 | — |
| `P_V3_GBM` | 0.642480 | 0.225218 | 0.634 | +0.912 |
| `P_V2_B7` | 0.645954 | 0.227070 | 0.665 | +0.910 |
| `P_V3_B4` | 0.674279 | 0.238589 | 0.727 | +0.904 |

`V3_CHAMPION = P_V3_GBM`, ahead of the V2 champion by 0.0035 event-equal log
loss.

Conditional on the market (the §14 test — complementary information, not
standalone winner):

| Challenger | Δ log loss | 95% event-clustered CI | Status |
|---|---|---|---|
| `P_V3_GBM` | −0.01288 | [−0.02998, +0.00228] | `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` |
| `P_V2_B7` | −0.01363 | [−0.03212, +0.00220] | `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` |
| `P_V3_B4` | +0.00553 | [−0.02672, +0.02963] | `NOT_DETECTED_AT_THIS_SAMPLE_SIZE` |

Δ is MARKET_ONLY − BLEND: **positive means the blend is better**. Two of the
three are negative, so on this sample adding the challenger made the blend
*worse*. See §7a — the previous report read these the wrong way round.

This table predates the §22 nesting. The valid conditional measurement is the
nested one in §7b.

**`NOT_DETECTED` here is arithmetic about the sample, not a finding about
football.** Every interval above is consistent with a real improvement and with
a real degradation. The sign of the point estimate is not evidence.

Error correlations of 0.90–0.91 say the challengers are reading the same matches
with weaker instruments. They are not finding an independent view, and they are
not merely rediscovering the market either.

## 10. What this does and does not license

**It licenses:** continuing to build the independent model; acquiring league
coverage and exact-timestamp odds; treating the market as the strongest
settlement forecast currently held.

**It does not license:** any claim that fundamental alpha is exhausted; any
negative result on incremental signal (27 test events against a declared rung of
72); any trade; any tight-horizon pregame claim; any feature entering a
high-integrity lane on as-of grounds.

`MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT` binds: below the matching rung, the
only admissible statement is that the experiment was underpowered.

## 11. Management interpretation (§19)

> Richer fundamentals improved BETTOR's independent model substantially. The
> market remains the strongest settlement forecast. **No incremental independent
> signal has yet been detected on a sufficiently large prospective sample.**
>
> That is different from: *fundamentals have no value*.

The honest summary is that the experiment is not yet capable of answering the
question it was built to answer — but it is **closer than the previous report
claimed**. Corrected arithmetic puts the incremental question about 2.7× short
of resolution at a 0.010 effect, roughly 72 clean test events, which is a data
acquisition target within reach rather than an impossibility. That is the one
number in this report that moved in a favourable direction, and it moved because
a mistake was corrected, not because a model improved.

Against that, two things moved the other way. The challengers' point estimates
lean toward *hurting* the blend, not helping it. And no feature the programme
holds can support a tight as-of claim.

The next gain still comes from data acquisition — leagues, exact-timestamp odds,
availability, real xG, and publication timestamps for all of them — not from
another model class fitted to the same 67 events.

`DO NOT TUNE A MODEL UNTIL IT LOOKS PROFITABLE` was observed.
`NO_MODEL_WAS_TUNED_UNTIL_IT_LOOKED_PROFITABLE = True`.
