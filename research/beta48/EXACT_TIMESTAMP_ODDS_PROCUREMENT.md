# EXACT-TIMESTAMP ODDS — PROCUREMENT DECISION PACKAGE

Status: RESEARCH ONLY. **Nothing has been purchased. Nothing has been
requested. No credential exists.** No orders, no capital, `mirror_live=false`.

This document exists to let management decide what data access to buy, if any.
It reports what the experiment needs in requests and credits, and it is explicit
about which numbers were verified and which were not.

---

## 1. Why this is now the primary data question

Settlement forecasting has been measured and the market wins. On 47 common
events both independent lanes are worse than `P_MARKET_RAW` standalone, and
neither adds detected incremental value. The corrected incremental ladder needs
**4,654 events at the point estimate and 8,702 at P90** to resolve a 0.010
effect in the high-integrity lane. That is not reachable from the current
corpus.

Meanwhile the one genuinely independent *market* observation the programme could
hold — an outside consensus priced at a known instant — does not exist. Current
external odds are `COARSE_PREMATCH_UNTIMESTAMPED`: without a snapshot time there
is no way to say whether the consensus a model is compared against was formed
before or after the observation being scored, and that *is* the comparison.

So the gap is timing, not sport.

## 2. What is verified, and what is not

**The credit formula is VERIFIED** (owner's independent check of the public
documentation, 2026-09-17):

```
CREDITS_PER_HISTORICAL_REQUEST = 10 × NUMBER_OF_MARKETS × NUMBER_OF_REGIONS
```

**Plan pricing is VERIFIED** as of **2026-09-17**. A vendor price list is a
snapshot, not a constant — re-check before any purchase is authorised.

| Plan | Credits | USD / month |
|---|---|---|
| 20K | 20,000 | $30 |
| 100K | 100,000 | $59 |
| 5M | 5,000,000 | $119 |
| 15M | 15,000,000 | $249 |

**Still not verified:** the per-region bookmaker lists. Provider hosts remain
egress-blocked from this environment, so no bookmaker count is asserted
anywhere in this document.

## 2a. The billing unit was wrong, and it is corrected

The previous version of this document billed `EVENTS × MARKETS × HORIZONS`, as
though each target game needed its own API call. **It does not.** The featured
historical endpoint returns *all* events for a sport at a requested snapshot, so
five Premier League matches kicking off together at 15:00 share one request at
T−2H.

The billable unit is `(SPORT_KEY, SNAPSHOT_TIME, MARKET_SET, REGION_SET)`,
deduplicated.

**How much that actually saves — measured on 4,178 real fixtures with real
kickoff times across the eight target leagues:**

| Target events | Static (3 horizons) | Dense 6h |
|---|---|---|
| 100 | 3.0% | 4.4% |
| 500 | 10.7% | 15.2% |
| 1,000 | 18.3% | 27.4% |
| 5,000 | 38.4% | 57.1% |

Deduplication only fires when two events in the **same league** share a snapshot
instant. At 100 events spread across eight leagues and a season, collisions are
rare. At full-season density the Saturday and Sunday blocks overlap heavily, and
on a five-minute grid a 15:00 kickoff's T−2H request *is* a 13:00 kickoff's T−0.

**So dedup is real but it is not the big lever.** The big levers are markets
(h2h only, not three) and regions (one, not two) — together they divide the bill
by six. That correction matters far more than the collision rate.

## 3. Two pilots, not one — and the join direction is reversed

Three snapshots per event cannot estimate 5, 15, 30 and 60-minute following
dynamics. These are separate designs and are costed separately.

**A. Historical matched-static** — *does external consensus look useful on the
market states BETTOR already observed?*

**B. Prospective capture-aligned** — *does external consensus lead the venue, on
a venue series sampled by a clock rather than by somebody's trading?*

**The earlier version of this document costed pilot A the wrong way round.** It
picked attractive horizons — T−24H, T−2H, T−15M — and assumed a venue
observation would exist at each. Measured, one mostly does not: the historical
series has a median of 3 observations per market slug and a forward point at
+60m only 9.3% of the time. An external snapshot at a horizon where no venue
state exists is an unmatched row, and buying it is buying nothing.

So the join is reversed. **Select the Poly observation first, then request the
external snapshot at or before that instant.** Every request is anchored to an
observation already held.

That reversal caps the pilot at the corpus. **222 settled soccer events carry at
least one observation.** A 500-event static experiment is not available at any
price — the 500-event line in the old table was describing events that do not
exist.

## 4. Phase 1 is deliberately narrow

**h2h only. One region.** Spreads and totals triple the bill; a second region
doubles it again. Neither is worth buying before any external lead/lag value has
been demonstrated at all.

**Region choice is PROVISIONAL and NOT MEASURED.** `uk` is suggested because the
eight evaluated leagues are European and UK-listed books price them as primary
markets — that is a reason to *test* uk first, not to skip the test. The
`/sports` and current-odds endpoints are free or near-free relative to
historical calls, so **on the day a credential exists, one current-odds call per
candidate region returns the bookmaker list and settles the choice by
measurement. Do that before spending historical credits.**

## 5. What pilot A actually costs — the matched cohort, measured

Credits per request = 10 × 1 × 1 = **10**. These figures are computed by
`historical_matched_cohort.plan_external_requests()` against the real
observation corpus, under the frozen `EARLIEST_ELIGIBLE_POLY_OBSERVATION` rule,
one observation per independent event, five-minute buckets.

| Target events | Requests | **Credits** | Credits / matched event |
|---|---|---|---|
| 100 | 90 | **900** | 9.0 |
| 250 | 203 | **2,030** | 9.1 |
| 500 | 203 | **2,030** | 9.1 |

The 250 and 500 rows are identical because **the universe caps at 222 events**.
The other two selection rules land in the same place (MEDIAN 2,100; LATEST
1,930), so the rule choice does not move the bill.

**The whole matched historical cohort costs about 2,030 credits — inside the
smallest tier, roughly 2% of a 100K plan.** Money is not the constraint on this
pilot. The constraint is that 222 RN1-selected events cannot answer a question
whose incremental ladder needs thousands.

## 5a. What pilot B costs — not yet computable, and that is the correct state

The capture-aligned request set is a function of timestamps the capture has not
written yet. `capture_external_backfill.plan_from_capture()` will compute it
exactly once the capture completes; until then the figure is
`NOT_IDENTIFIED`, not an estimate.

What can be said now is the shape: the capture samples every ~4 seconds, but
deduplication is by five-minute bucket, so a 90-minute run produces **at most 18
distinct buckets per sport** however many ticks it writes. Dense venue sampling
does not imply a dense external bill.

This is the property that makes pilot B the better purchase: historical external
snapshots are retrievable *after the fact*, so the capture needs no odds
credential running alongside it, and the purchase is **sized to an experiment
that already exists** rather than bought in the hope one materialises.

## 6. Recommendation — and it is smaller than the previous one

**Neither pilot needs a purchase decision this week.** The earlier
recommendation — 100K at $59/month to cover "static 500 plus a dense 100" — was
sized against an experiment that does not exist. Corrected:

1. **Settle the region by measurement.** One near-free current-odds call per
   candidate region returns the bookmaker list. Do this before spending any
   historical credits; `uk` remains provisional and unmeasured.
2. **Pilot A, if it is wanted at all, is a $30/month 20K plan** and uses ~2,030
   of those credits. It answers one descriptive question about a selected
   sample. It is cheap enough that its cost is not the decision — its
   *interpretability* is.
3. **Pilot B is the one worth waiting for**, and its plan tier cannot be chosen
   until the capture completes and `plan_from_capture()` returns a real number.

The 5M tier remains unnecessary. The 100K tier is no longer justified by
anything measured.

## 7. The problem the odds data cannot solve on its own

**`HISTORICAL_POLY_LEAD_LAG_COVERAGE = INSUFFICIENT.`**

A lead/lag experiment needs *both* sides at matched times. The venue side was
measured directly:

| | |
|---|---|
| Soccer observation rows | 32,162 |
| Distinct market slugs | 2,912 |
| Observations per market | **median 3**, max 353 |
| Inter-observation gap | median 0.4 min, p90 10 min |
| Forward point exists at +5m | 49.3% |
| at +15m | 31.3% |
| at +30m | 18.6% |
| at +60m | **9.3%** |

Worse than the sparsity: those observations are **RN1-trade-triggered**. They
cluster around when a whale traded, so they are a *selected sample*, not a
sampling grid. Buying dense external odds against this venue series would pair
5-minute external snapshots with a venue series that has a forward point only 9%
of the time at 60 minutes, and only where a whale happened to act.

**Therefore the historical lead/lag experiment is not worth buying dense data
for yet.** The prospective continuous capture is what makes dense external odds
worth having, and the two should be commissioned together.

No interpolation will be used to paper over this. Where a matched venue
observation does not exist, `LEAD_LAG_ROW = UNAVAILABLE`.

## 8. What is already built, awaiting only data

| Component | Status |
|---|---|
| `HistoricalOddsSnapshotProvider` | built; enforces `SNAPSHOT_TIMESTAMP <= REQUESTED_AS_OF_TIMESTAMP` |
| The Odds API adapter | built; refuses without transport or credential |
| Betfair historical adapter | built; five price objects modelled separately |
| Consensus estimators | built (mean, median, trimmed, quality-weighted, hierarchical) |
| Lead/lag experiment | built; event-clustered intervals |
| Disagreement features | built; nine candidates, none assumed to be alpha |
| Procurement calculator | built; this table is its output |

Every one of these returns `NO_EXTERNAL_DATA` today. That is the honest status,
and it is a status rather than a zero.

## 9. The purchase gate, and its current reading

Nothing may be bought until condition A or condition B is true.

| | Condition | Met? |
|---|---|---|
| **A** | the matched-static cohort is defined with enough usable event N that a small purchase answers a *specific* question | **YES** — 222 events, ~2,030 credits |
| **B** | the continuous capture completes and the exact timestamp-aligned request plan is known | **NO** — the clean-start gate is still blocked |

`PURCHASE_STATUS = NOT_PURCHASED_CONDITION_A_MET_AWAITING_AUTHORIZATION`

**A met gate is a technical precondition, not an authorization.** And condition
A clears only the ~2,030-credit descriptive pilot. It does **not** clear the
dense spend — that stays behind condition B, because the historical venue series
is too sparse and too RN1-selected to pair dense external odds against. Reading
a met A as clearance for the whole document would be exactly the error the gate
exists to prevent.

## 10. The decision being asked for

1. **Whether to run pilot A at all.** It costs ~2,030 credits on a $30/month
   20K plan. It cannot prove market-wide external-consensus alpha; it can only
   describe external consensus on the states RN1 happened to trade. That
   limitation is structural and no amount of spending fixes it — so the
   question is whether a descriptive answer on a selected sample is worth a
   credential.
2. **Nothing else yet.** Betfair is rank 2 and waits for the lead/lag result;
   OddsJam's history starts too late to be a first purchase.
3. **The real recommendation is to wait for the capture.** Pilot B has no
   selection problem on the venue side, its cost is computable exactly rather
   than estimated, and it is the design that can actually answer the lead/lag
   question that motivated this lane.

Nothing in this document has been purchased, requested, or committed to. No
credential exists.
