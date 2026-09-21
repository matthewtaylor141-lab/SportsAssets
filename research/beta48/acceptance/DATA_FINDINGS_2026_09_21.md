# MEASURED DATA FINDINGS — 2026-09-21

Seven questions the acceptance package had been answering from inference.
All seven are now answered by query against the production read replica.

**Read-only.** `research/bettor_schema_probe.sql` and
`research/bettor_depth_findings.sql` via the `research-sql` workflow, which
refuses any file containing a mutating keyword. Runs
[35638341730](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35638341730)
and
[35639558767](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35639558767).
Nothing was altered. `mirror_live=false`, no orders, production `3349219`.

**Four of the seven contradict something I previously asserted.** Each is
named below with what I had said.

---

## 1. `yes_depth` IS THE FIVE-LEVEL SUM — ON EVERY ROW

| rows compared | = top level | = five-level sum | = neither |
|---|---|---|---|
| **939** | 111 | **939** | **0** |

The 111 that equal the top level are exactly the 111 where the top level *is*
the sum (`top_is_sum = 111`) — a one-level book. There is no row where
`yes_depth.ask` is the quantity at the quote and not the sum.

**Ratio of sum to top-of-book:**

| min | median | max |
|---|---|---|
| 1.00 | **15.29** | **550,001.00** |

**What I had said:** first that depth was `ABSENT_IN_CAPTURE_SCHEMA` (wrong —
it is there), then, having found it, that `yes_depth.ask` was the executable
size (wrong — it is the sum). I also wrote a test asserting
`ask_size == 2702.0`, locking the second error in.

**The consequence is not a 15× size error, it is a wrong price.** The worked
row: quote 0.7200 with **17** contracts behind it, `yes_depth.ask` **4903.69**,
the remaining 4,886 sitting at 0.73, 0.74, 0.75 and 0.76. An order sized from
the cumulative figure is not a large order at the quote — it is an order that
walks four levels up a book the venue never showed at the touch.

Corrected in `bettor_observation_adapter`: executable size is the ladder's
best level, which must agree with the quoted price or the row is rejected;
the sum is carried as `cumulative_ask_size` where nothing can mistake it.

## 2. EXECUTABLE DEPTH AT THE TOUCH IS SMALL

| rows | p10 | median | p90 |
|---|---|---|---|
| 939 | **2.00** | **75.00** | 3000.00 |

At the 10th percentile there are **two contracts** at the quote. The v1 pilot
proposal's 1-contract quote and the v2 proposal's 5-contract quote are both
material relative to the touch on a large minority of markets — which is a
finding about *market impact*, not just about sizing.

**For capacity:** total top-of-book across all 939 observations is 2,105,925
contracts. **That is a snapshot sum, not a flow, and it is displayed depth.**
Track B-L BLOCK_4 measured a 22,297-share displayed bid queue against 180
shares traded in 16 minutes with **zero touches**. Displayed depth is an upper
bound on what could trade, and a loose one. It does not bound what will.

## 3. THE 11 "COMPLEMENT CANDIDATES" HAVE NO LEG-LEVEL IDENTITY

All 11 market_ids carrying two `outcome_leg` labels:

| | |
|---|---|
| distinct `instrument_id` per market | **1** — on all 11 |
| distinct `condition_id` per market | **1** — on all 11 |
| `condition_id` populated anywhere | **0 of 1,538 rows** (all `NOT_IDENTIFIED`) |
| `identity_status` | `VENUE_NATIVE_RESOLVED` on all 1,538 |

`instrument_id` **equals `market_id`** in this capture, so it identifies the
market and carries no leg-level information at all. `condition_id` is the
sentinel everywhere. The venue's identity resolution succeeded — but what it
resolved to is the market, not the outcome.

**Therefore: whether two `outcome_leg` labels are genuine complements cannot
be established from our data.** Not "is not yet verified" — **not checkable**,
because no leg-level venue identifier is captured and no settlement predicate
is captured either.

**What I had said:** RELEASE_CANDIDATE.md §3 — *"Venue-wide, 11 contracts have
two distinct outcome legs observed — so pairs exist."* The first clause is
true and the inference is not. Two labels under one market_id, one
instrument_id and one sentinel condition_id is consistent with genuine
complements and equally consistent with one instrument relabelled between
observations. The data does not separate them.

This is also consistent with the other measured fact: `no_ask` / `no_bid` are
`NOT_IDENTIFIED` on 100% of rows. The sibling instrument is never read.

## 4. 552 OBSERVATIONS ARE OF EVENTS THAT HAD ALREADY STARTED

| rows | markets | event started (`time_to_event_s` < 0) | `live_status` ≠ PREGAME |
|---|---|---|---|
| 1,538 | 1,526 | **552** | **552** |

Earliest observation 2026-09-20 19:23 UTC. `bettor_state_settlements` holds
**0 rows**.

**An event starting is not an event resolving**, and the capture records no
event end time, so how many of the 552 concluded is not readable. But the
settlement table's emptiness has a known cause that is not maturity:
`record_settlement()` is **defined and never called**.

**What I had said:** RELEASE_CANDIDATE.md §3 — *"No outcome has matured for
any observation."* Unsupported then and still unsupported. The correct
statement is that we cannot tell, and the reason we cannot tell is an
unwired ingestion path rather than a property of the markets.

## 5. THERE IS NO CLOCK DISAGREEMENT — AND THE FEED IS NINE MINUTES STALE

| rows with both stamps | source stamped AFTER receipt | min delay | **median delay** | max delay |
|---|---|---|---|---|
| 1,203 | **0** | 0.307 s | **549.6 s** | 250,709 s |

**Zero rows** have a source timestamp in our receipt's future. Transport only
runs forward, so a negative delay is the only thing that can demonstrate the
clocks disagree, and there are none. **There is no measured clock skew.**

The old check took `abs(received − source)` and called anything over 120 s
"skew". On this data it would have flagged **the majority of rows** as
unverifiable clock disagreement when what it was measuring was the sampler's
read cadence.

**The real finding is the median: 549.6 seconds — 9.2 minutes.** That is how
old a captured book typically is *when we receive it*, before any decision
latency at all. Against a 10-second decision bound that is a shortfall of
**~55×**, and it is a property of the research sampler (60/300/900/3600 s
horizons), not of the venue. The 36-row sample that showed ages of 5.8–28 s
was the freshest tail of the distribution, ordered by `book_age_s` ascending;
it is not representative and was never claimed to be.

**This is the binding constraint on a decision-latency feed**, and it is
now measured rather than estimated.

## 6. THE SETTLEMENT TABLE'S SHAPE

`observation_id`, `settlement_outcome`, `settlement_ts`, `settlement_status`,
`settlement_semantics_status`, `written_at`, `is_not_a_fill`. Seven columns,
zero rows. The ingestion path to fill them is the work, not the schema.

## 7. THE RECEIPT TIMESTAMP EXISTS

`book_received_ts` is populated on the captured rows, and
`bettor_prospective_runner.live_freshness` treated it as **optional** —
`if r_ts is not None` — so a row lacking it skipped the check entirely and
was treated as verified rather than unverifiable. Now required.

---

## WHAT THIS CHANGES IN THE ACCEPTANCE PACKAGE

| claim | status |
|---|---|
| "depth is absent from the capture schema" | **withdrawn** (twice over) |
| `ask_size` = `yes_depth.ask` | **withdrawn** — it is the five-level sum on 939/939 rows |
| "11 contracts have two legs, so pairs exist" | **narrowed** — the count is right, the inference is not checkable |
| "no outcome has matured" | **withdrawn** — unsupported; 552 rows are of started events |
| "clock skew" checks | **withdrawn** — zero rows show disagreement; the quantity measured was transport |
| replay: "15 accepted, 15/15 NO_TRADE" | **stale** — the sample predates the depth requirement and now yields 0 accepted |

## THE PATTERN

Four of the six withdrawals above are the same mistake: **I asserted that a
capability or a fact was missing without querying the thing it was about.**
Depth (twice), settlement maturity, and the published fee schedule — which had
been implemented in `research/run85_trackb_fees.py` since 2026-07-01 while I
described it as unavailable — plus `client.portfolio.activities`, which I
listed as a reconciliation blocker while it existed.

Checking costs one read-only query. Each assertion cost a false entry in an
acceptance package that a person was expected to rely on.
