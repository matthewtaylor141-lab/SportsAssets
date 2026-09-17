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

## 7. Evidence ladder and the size of the sample (§10)

Observed paired event-level SD: **0.1680**.

| Effect to detect (log loss) | Events for 80% power | Events for a 95% CI |
|---|---|---|
| 0.002 | **55,386** | 27,109 |
| 0.005 | 8,862 | 4,338 |
| 0.010 | 2,216 | 1,085 |
| 0.020 | 554 | 272 |

`INDEPENDENT_TEST_EVENTS_CURRENT = 27`.

Twenty-seven against 2,216. The shortfall is a factor of **82** for the smallest
effect the programme would plausibly act on, and a factor of **2,051** for a
0.002 effect.

The ladder was declared before the V3 result was computed.
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

**`NOT_DETECTED` here is arithmetic about the sample, not a finding about
football.** Every interval above is consistent with a real improvement of up to
three log-loss points *and* with a real degradation. The sign of the point
estimate is not evidence.

Error correlations of 0.90–0.91 say the challengers are reading the same matches
with weaker instruments. They are not finding an independent view, and they are
not merely rediscovering the market either.

## 10. What this does and does not license

**It licenses:** continuing to build the independent model; acquiring league
coverage and exact-timestamp odds; treating the market as the strongest
settlement forecast currently held.

**It does not license:** any claim that fundamental alpha is exhausted; any
negative result on incremental signal (the sample is two orders of magnitude
short of the declared rung); any trade; any tight-horizon pregame claim.

`MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT` binds: below the matching rung, the
only admissible statement is that the experiment was underpowered.

## 11. Management interpretation (§19)

> Richer fundamentals improved BETTOR's independent model substantially. The
> market remains the strongest settlement forecast. **No incremental independent
> signal has yet been detected on a sufficiently large prospective sample.**
>
> That is different from: *fundamentals have no value*.

The honest summary is that the experiment is not yet capable of answering the
question it was built to answer. The next gain comes from data acquisition —
leagues, exact-timestamp odds, availability, real xG — not from another model
class fitted to the same 67 events.

`DO NOT TUNE A MODEL UNTIL IT LOOKS PROFITABLE` was observed.
`NO_MODEL_WAS_TUNED_UNTIL_IT_LOOKED_PROFITABLE = True`.
