# P_BETTOR_INDEPENDENT_V1 — RESULT

**Directive sections 12, 13 and 14. No orders, no capital, no credentials,
`mirror_live=false`, run85 untouched, no production database contacted.**

---

## The headline

BETTOR now has a fair value that has never seen a venue price. It is built,
walk-forward tested, and it **does not work yet**.

| | log loss | Brier | calibration slope | resolution |
|---|---|---|---|---|
| B0 (`P_MARKET_RAW`, last trade) | **0.500313** | **0.166270** | 0.941 | 0.084654 |
| `P_BETTOR_INDEPENDENT_V1` | 0.675965 | 0.231530 | 0.504 | 0.039372 |

12,213 contract observations across **157 independent events**.

Losing standalone was expected and is not the finding. The finding is section
14's test, which is the one that decides whether the model is worth keeping:

```
VERDICT                          = NO_MEASURABLE_ADDITION_CONDITIONAL_ON_MARKET
RAW_MARKET                         log loss 0.500313
RAW_CANDIDATE                      log loss 0.675965
RECALIBRATED_MARKET                log loss 0.511306
MARKET_PLUS_CANDIDATE              log loss 0.514854
LOG_LOSS_IMPROVEMENT             = -0.003637  [-0.009501, +0.001940]
COEFFICIENTS                     = market +0.9449   candidate -0.0077
```

Conditional on the venue price, the model's disagreement carries **no**
information about the outcome. The fitted coefficient on its logit is −0.0077 —
indistinguishable from zero — and the out-of-fold log-loss change is negative
with a confidence interval spanning zero.

**This model is not a source of edge and must not be treated as one.**

## Why this is still progress

Three things are now true that were not true before:

1. **The independent channel exists.** Data, identity, fit, distribution,
   contract pricing, walk-forward and evaluation are all built and tested end to
   end. The next independent model plugs into the same pipeline and gets
   measured the same way within one turn, not five.
2. **The measurement discipline is in place.** The orthogonality test has
   event-grouped folds, out-of-fold predictions only, a recalibrated baseline,
   and an event-clustered bootstrap on the improvement itself. A future model
   that claims to add information will have to survive it.
3. **The negative result is informative about where to look.** See below.

## What the numbers say about where the model fails

**Calibration slope 0.504.** The model is roughly twice as confident as it
should be. Its probabilities need to be pulled halfway back toward the base rate
to be honest. That is over-dispersion, and it is what a Dixon-Coles fit does when
team strengths are estimated from too few recent matches — the 2026-27 season was
only 18–42 matches old per league at the corpus window.

**Resolution 0.039 against B0's 0.085.** It does discriminate — resolution is
well above zero, so it is not noise — but it separates outcomes less than half as
well as the price does.

**Two families are actively harmful.** Per-family, adding the candidate to the
price makes things measurably worse in exactly two places:

| family | rows | events | Δ log loss (positive = candidate helps) |
|---|---|---|---|
| MONEYLINE_OR_OTHER | 5,119 | 109 | +0.0209 [−0.0658, +0.0836] |
| TOTAL | 4,210 | 136 | +0.0188 [−0.0242, +0.0625] |
| EXACT_SCORE | 822 | 88 | +0.0006 [−0.0134, +0.0123] |
| HALFTIME_RESULT | 211 | 57 | −0.0067 [−0.0353, +0.0275] |
| **FIRST_HALF_TOTAL** | 470 | 70 | **−0.0343 [−0.0564, −0.0080]** |
| **DRAW** | 1,203 | 68 | **−0.0648 [−0.1157, −0.0162]** |
| BTTS | 178 | 39 | −0.0795 [−0.1797, +0.0207] |

Moneyline and totals lean positive but their intervals contain zero at this
sample size. Draw and first-half-total are negative with intervals that exclude
zero. The Dixon-Coles `rho` correction governs exactly the low-score cells that
drive the draw price, and the half-time fit is the thinnest fit in the model —
both are the obvious suspects and both are checkable.

**A methodological note that strengthens the negative.** The recalibrated market
(0.5113) is *worse* than the raw market (0.5003). The venue's last trade is
already well enough calibrated that fitting a two-parameter recalibration to it
adds noise. That means the baseline used in the test is handicapped relative to
the real one — and the candidate still did not beat it.

## What this does not establish

157 independent events is a small sample. A confidence interval spanning zero
means "not detected here", not "proven absent". The honest statement is that
**no orthogonal information was detected at this sample size**, and that the
point estimate is negative rather than merely uncertain.

## The data (section 12)

`football-data.co.uk` is denied by this environment's egress policy:

```
curl: (56) CONNECT tunnel failed, response 403
[agent-proxy] www.football-data.co.uk:443 — connect_rejected
```

The proxy reports `selective: false`, so this is an upstream host policy. But the
block is **host-scoped, not blanket** — `api.football-data.org` and `datahub.io`
are also denied, while `raw.githubusercontent.com` and `api.github.com` pass. So
section 12 was executed from permitted hosts rather than reported as blocked.

**Ingested:** `openfootball/football.json`, 32 season files, **11,679 fixtures**,
**9,030 played**, **202 clubs**, eight leagues (`epl elc lal sea bun fl1 ere
por`), seasons 2023-24 through 2026-27. Every file carries url, HTTP status, byte
count and sha256 in `research/data/public_soccer/manifest_v1.json`.

**Not used:** StatsBomb open-data is reachable and its competition list is
recorded, but its latest season for anything in the corpus is 2023/24 and for
most competitions 2015/16–2020/21. Club strength three to ten seasons stale is
not a prior for 2026-27 fixtures. Recorded as a measured gap, not an omission.

## The identity (sections 12 and 8 of the prior directive)

Joining `epl-che-bri-2026-08-30` to `Chelsea FC` is the forbidden class of
inference. Two mechanisms close it without a guess.

**The venue names its own clubs.** A spread row carries both clubs as outcome
labels, slug side first. 813 codes resolve; 68 conflict (all MLB and NFL, whose
spread rows use a different side convention) and are dropped with no tie-break.
The join to the public vocabulary is exact normalised equality — case and
whitespace only, diacritics kept, because stripping accents is the first step of
fuzzy matching.

**What exact equality cannot reach is closed by schedule, not by name.** `BV
Borussia 09 Dortmund` and `Borussia Dortmund` never compare equal, and no alias
table is permitted to assert they are the same. So the names are not compared at
all: an unbound code's fixtures against already-bound opponents are matched
one-to-one against an unbound public club's. Ten clubs close this way —
Dortmund, Sporting Clube de Portugal, RC Deportivo La Coruña, Real Racing Club de
Santander, Torino, Udinese, West Bromwich Albion, Angers, Alverca, Gil Vicente.
Ambiguous and contested residuals refuse. `NAMES_COMPARED = False`.

Bind rate **90.5%** of 222 soccer events; every refusal named.

**The check that matters:** on the 37 bound events where both sources know the
score — the corpus reconstructing it from its own settled contracts, the public
source publishing it — they **agree 37 times and disagree 0**.

## Coverage losses, honestly

Of 41,413 soccer contract observations, 12,213 were priced. The rest:

| reason | rows |
|---|---|
| both club codes unbound (leagues with no public data) | 25,932 |
| club seen too few times in the fit | 1,456 |
| club not in the fit at all | 663 |
| away club code unbound | 567 |
| spread family refused (handicap sign not established) | 251 |
| public fixture date outside tolerance | 203 |
| home club code unbound | 128 |

The dominant loss is leagues the free source does not cover. The corpus trades 32
soccer league codes; openfootball covers eight of them.

## The spread refusal

`epl-che-bri-2026-08-30-spread-home-2pt5` does not say which side gives the 2.5
goals. The two readings are complements, so guessing would be right half the time
and wrong half the time — and no metric would reveal which. The rule is
recoverable from the corpus's own settled spread rows against reconstructed
scores. Until it has been recovered, the family refuses. 251 rows.

## If management wants to buy data

| | |
|---|---|
| **PROVIDER** | Football-Data.org, or Opta / Stats Perform |
| **DATA NEEDED** | kickoff-stamped fixtures, final and half-time scores, shots and expected goals per match, lineups and availability at kickoff |
| **HISTORICAL DEPTH** | five completed seasons plus the live season |
| **TIMESTAMP RESOLUTION** | kickoff to the minute; in-play events to the minute |
| **EXPECTED COVERAGE** | the 484 clubs the corpus names across 32 league codes; the free source reaches 8 of those codes |
| **WHY IT MATTERS** | Free sources give a final score and a date. No kickoff time means no live or T-minus surface can ever be built from them. And expected goals is the single feature most likely to fix the specific failure measured above — a goals-only model over-fits recent scorelines, which is exactly what calibration slope 0.504 looks like. |
| **STATUS** | NOT PURCHASED, NOT REQUESTED |

## Next, in order

1. **Recalibrate before ensembling.** Slope 0.504 is a fixable defect, not a
   verdict. Re-run the orthogonality test on the recalibrated model.
2. **Establish the spread handicap sign** from settled rows, recovering 251 rows
   and the largest refused family.
3. **Fix or drop the draw and half-time branches**, which currently subtract.
4. **More seasons.** Four seasons of eight leagues is a thin base for a
   league-local fit; openfootball has more history and it is free.

---

*Modules: `public_soccer_ingest.py`, `public_soccer_identity.py`,
`p_bettor_independent.py`, `ev_core_orthogonal.py`. Tests:
`test_public_soccer.py`, `test_p_bettor_independent.py`. Suite: 1,336 passing.*
