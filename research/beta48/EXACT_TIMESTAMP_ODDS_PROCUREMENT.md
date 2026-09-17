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

## 3. Two experiments, not one

Three snapshots per event cannot estimate 5, 15, 30 and 60-minute following
dynamics. These are separate designs and are costed separately.

**A. Static timestamped consensus** — T−24H, T−2H, T−15M. *Does external
consensus improve settlement / fair value at selected decision points?*

**B. Dense lead/lag** — a 5-minute time series over T−2H→T−0 or T−6H→T−0. *Does
external consensus lead the venue, and at what horizon?*

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

## 5. What the experiments cost (h2h, one region, deduplicated)

Credits per request = 10 × 1 × 1 = **10**.

| Experiment | Events | Naive credits | **Deduped credits** | Saving |
|---|---|---|---|---|
| Static (3 horizons) | 100 | 3,000 | **2,910** | 3.0% |
| Static (3 horizons) | 500 | 15,000 | **13,390** | 10.7% |
| Dense T−2H @ 5min | 100 | 25,000 | **24,500** | 2.0% |
| Dense T−2H @ 5min | 500 | 125,000 | **111,650** | 10.7% |
| Dense T−6H @ 5min | 100 | 73,000 | **70,810** | 3.0% |
| Dense T−6H @ 5min | 500 | 365,000 | **312,270** | 14.4% |

Against verified pricing:

| Requirement | Smallest tier that fits | Cost |
|---|---|---|
| Static 500 (13,390) | **20K** | **$30/mo** |
| Dense 2H / 100 (24,500) | **100K** | **$59/mo** |
| Dense 6H / 100 (70,810) | **100K** | **$59/mo** |
| Dense 2H / 500 (111,650) | 5M | $119/mo |
| Dense 6H / 500 (312,270) | 5M | $119/mo |

Note the cliff: dense-2H at 500 events needs 111,650 credits and **just**
overruns the 100K tier. Trimming to ~440 events fits $59/month instead of $119.

## 6. Recommendation

**100K plan, $59/month.** It covers the static 500-event experiment *and* a
dense pilot at 100 events on either window, with headroom.

Sequence:
1. Settle the region by measurement (one near-free current-odds call per region).
2. Static 500 events — 13,390 credits.
3. Dense T−2H, 100 events — 24,500 credits.
4. Total ≈ 38,000 credits, inside 100K, leaving room to re-run.

The 5M tier is unnecessary until the pilot produces evidence. The 20K tier is
enough only if the dense pilot is dropped, which would leave the lead/lag
question — the one that motivated this whole lane — unanswered.

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

## 9. The decision being asked for

1. Authorise a credential for The Odds API on the **100K plan at $59/month**,
   or decline. That covers the static 500-event experiment plus a 100-event
   dense pilot with headroom (~38,000 of 100,000 credits).
2. Nothing else. Betfair is rank 2 and can wait for the lead/lag result;
   OddsJam's history starts too late to be a first purchase.
3. Note the sequencing constraint from section 7: the **dense** half of the
   spend only becomes worth making alongside the prospective continuous
   capture, because the historical venue series is too sparse and too selected
   to pair against. The static half stands on its own today.

Nothing in this document has been purchased, requested, or committed to.
