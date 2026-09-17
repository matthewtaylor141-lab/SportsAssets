# MARKET_SURFACE_V1_ASOF — RESULT

**Directive sections 10 and 11. No orders, no capital, no credentials,
`mirror_live=false`, run85 untouched, no production database contacted.**

---

## Two findings, and the second one is the important one

**1. The corpus cannot support a pregame surface at the named horizons.**
Not thinly — at all. T-24H has **zero** of 1,092 soccer events with four linked
contemporaneous quotes. T-6H has **six**.

**2. Most of the surface's apparent disagreement with the market is the fit
reproducing its own inputs, and what survives the honest hold-out points the
wrong way.**

---

## §10 — the as-of surface, and what the data is

`MARKET_SURFACE_V1_ASOF` is built and tested: one common decision timestamp T
per surface, every input the latest observation of its contract at or before T,
none older than `MAX_QUOTE_AGE_S`, quote age carried per input and summarised as
median / p90 / max. Nothing after T reaches the fit.

### The anchor problem is real and is not a technicality

"T minus 24 hours" needs something to be 24 hours before, and the corpus does
not contain kick-off. It contains `RESOLVED_AT`. So anchors are named and their
uncertainty is enforced rather than assumed away:

| anchor | uncertainty | consequence |
|---|---|---|
| `RESOLVED_AT` | ±3 h | T-1H, T-15M, T-5M refused — one hour before settlement is the second half |
| `PUBLIC_KICKOFF` | ±1 h | the public source writes a local clock time with **no timezone** |
| `OBSERVATION_QUANTILE` | 0 | a quantile of the event's own quote times; carries **no horizon label** |

A horizon shorter than its anchor's uncertainty is refused by name. Reporting a
T-5M surface from a ±3h anchor would be inventing precision.

### Where the observations actually are

Against the public kick-off, on the 201 events bound to the public source
(14,583 observations):

| | hours relative to kick-off |
|---|---|
| p1 | −2.01 |
| p25 | −1.15 |
| **p50** | **−0.49** |
| p75 | −0.10 |
| p90 | +0.58 |
| p99 | +0.88 |

**79.9% pregame, 20.1% in-play, 0.0% post-match. Only 0.3% more than six hours
before kick-off.** Settlement follows kick-off by a median of 1.88 h, which
sanity-checks the whole picture.

So the corpus is better than V0's description implied — four fifths of it is
genuinely pregame, not contaminated by known goals — but it is a **last-two-hours
sample**, because `OBSERVATION_SAMPLING = WHALE_TRADE_TRIGGERED`: a contract is
priced only when RN1 happened to trade it, and he trades near kick-off.

### Contract density at the named horizons

Against `RESOLVED_AT`, events with at least four linked quotes alive:

| horizon | max quote age 2 h | 6 h | 24 h | 72 h |
|---|---|---|---|---|
| T-24H | 0 | 0 | 0 | 0 |
| T-6H | 4 | 5 | 6 | 6 |

Of 1,092. Widening the staleness window does not help, because the observations
are not there to be found.

**The horizons this corpus could support are T-2H, T-90M, T-60M, T-30M.**
Measuring them needs kick-off to the minute in a stated timezone. Two
independent routes: buy it (already in the paid-data request), or collect prices
on a clock of our own rather than on RN1's trading — which is exactly what the
substantive public-book capture does.

## §11 — the circularity guard, run

Because the named horizons are empty, the hold-outs were run at
`ANCHOR_OBSERVATION_QUANTILE` — the 75th percentile of each event's own quote
times. That is a real moment at which those prices stood together, which is all
the hold-out needs, and it claims no horizon.

**213 events, 1,915 contracts, 2-hour quote-age limit.**

| | mean absolute residual |
|---|---|
| in fit | **0.0738** |
| leave one contract out | **0.1034** |
| leave one family out | **0.1546** |

**The residual more than doubles under the binding hold-out.** Roughly half of
what looked like "the surface disagrees with this price by seven points" was the
fit reproducing its own input. Leave-one-contract-out recovers only part of
that, exactly as predicted: dropping one totals line while keeping five others
barely moves the fit, so `LEAVE_ONE_FAMILY_OUT` is the binding test and a
residual that survives only `LEAVE_ONE_CONTRACT_OUT` has not survived.

### Does the held-out residual point at the outcome?

Standardised mean difference between residuals on contracts that settled YES and
those that settled NO. Positive means the surface leaned the right way.

| family | in fit | **leave one family out** | n (yes/no) |
|---|---|---|---|
| **ALL** | +0.070 | **−0.229** | 855 / 853 |
| TOTAL | +0.003 | **−0.395** | 382 / 432 |
| DRAW | −0.159 | **−0.329** | 114 / 112 |
| BTTS | −0.121 | −0.168 | 52 / 57 |
| EXACT_SCORE | +0.420 | **+0.340** | 307 / 252 |

Read the ALL row twice. In fit, the surface leans the **right** way (+0.070).
Held out, it leans the **wrong** way (−0.229). The in-fit reading was not a weak
positive result; it was an artefact, and the sign flips once the artefact is
removed. That is precisely what §11 was for.

`EXACT_SCORE` is the one family that survives with its sign intact (+0.340).
Holding out the whole exact-score family still leaves totals, draw and BTTS
constraining the grid, so the fit is not starved — which makes this worth a
follow-up. It is one family out of four, on 559 contracts, from a market-derived
object, and it is **not** claimed as alpha.

### What none of this establishes

`P_MARKET_SURFACE` is market-derived. Every input is a venue price, so a residual
is a statement about internal consistency, never about the world.
`IS_INDEPENDENT_ALPHA` is False at every level of the output. And a residual that
leaned the right way would still have to clear the spread, survive the fee and
be fillable — none of which is measured here.

## What changed in the code

- `ev_core_surface_asof.py` — as-of snapshot, three named anchors with enforced
  uncertainty, horizon admissibility, the two hold-outs, the settlement-direction
  read, and the measured corpus-density constants.
- `family_of()` tests segment-qualified patterns **first**, so
  `-first-half-total-2pt5` is never filed as `-total-2pt5` and
  `-halftime-result-draw` is never filed as `-draw` — the same ordering defect
  that produced 29 false contradictions in the outcome reconstructor, caught the
  same way.

## Next

1. **Kick-off to the minute**, by purchase or by our own capture clock. Without
   it no horizon inside two hours can be labelled, and that is where the data is.
2. **Chase `EXACT_SCORE`.** It is the only family whose held-out residual keeps
   its sign. Check whether it survives an as-of split by quote age and whether
   the effect is concentrated in thin, stale exact-score quotes — which would
   make it a staleness detector rather than a view.
3. **Do not use any surface residual as a signal** until a family clears the
   leave-one-family-out test with the sign the right way round. Today none does
   except exact score.

---

*Modules: `ev_core_surface_asof.py`. Tests: `test_ev_core_surface_asof.py`
(23). Suite: 1,385 passing.*
