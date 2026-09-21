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

---

# PART II — THE OPPORTUNITY, BOUNDED BY MEASUREMENT

`research/bettor_traded_volume.sql` and `research/bettor_opportunity_bound.sql`,
runs
[35640158450](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35640158450)
and
[35640056878](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35640056878).
Observation window 2026-09-20 19:23 → 2026-09-21 18:26 UTC, about 23 hours.

## 8. TRADED VOLUME — THE CEILING ON TURNOVER

`stats_shares_traded` is the venue's own traded-volume statistic, captured per
observation. **It was never used.** The capacity analysis costed a
$500,000/day target against software throughput and never asked whether the
markets trade that much.

| per observation (609 rows) | p10 | median | p90 | p99 | max |
|---|---|---|---|---|---|
| shares traded | 2.23 | **100.00** | 4,401 | 109,822 | 3,627,618 |

Across **distinct markets**, taking each market's largest observed figure so
repeated observations are not summed into more than the market ever traded:

| markets | total shares | **total traded notional** | mean shares/market |
|---|---|---|---|
| 396 | 1,106,312 | **$396,361** | 2,794 |

| markets trading ≥ | 1 | 10 | 100 | 1,000 | 10,000 |
|---|---|---|---|---|---|
| count (of 606) | 573 | 481 | 303 | 116 | 36 |

## 9. THE CORRECTED CAPACITY ANALYSIS

### 9.1 Turnover definitions, because the earlier one was ambiguous

| term | meaning |
|---|---|
| **executed notional** | Σ (price × contracts) over every fill, **both sides of a round trip counted**. This is what "$500,000/day turnover" normally means. |
| **position notional** | Σ (price × contracts) over ENTRIES only. A buy-then-sell round trip is $X of position notional and $2X of executed notional. |
| **working capital** | position notional ÷ turns per day. Needs a **holding period**. |

### 9.2 The arithmetic, corrected

$500,000/day of executed notional at ~$0.50/contract:

| | |
|---|---|
| contract executions/day | **1,000,000** (≈ **11.57/sec**) |
| at 10 contracts/order, orders/day | 100,000 (≈ 1.16/sec) |
| position notional implied (round trips) | ~$250,000/day |

**The earlier version labelled the ORDER count as the CONTRACT count and was
10× wrong.** That correction stands.

### 9.3 The binding constraint is the market, not the software

| requirement | measured | verdict |
|---|---|---|
| Decision throughput | 21,917 obs/sec, single-threaded | **clears by ~4 orders of magnitude** |
| **Traded notional available** | **$396,361/day across all 396 observed markets** | **$500k executed notional needs ~63% of every dollar traded in the observed universe** (or ~126% if measured as executed rather than position notional) |
| Books meeting every pilot condition | **130 rows / 130 markets** | of 1,526 markets observed |
| Eligible books fresh enough to decide on | **18 of 449 within 10 s** (4.0%); median eligible age **406.5 s** | the feed is the constraint |
| Executable size at the touch | p10 **2**, median **75**, p90 3,000 contracts | 1,000,000 executions/day needs ~2,540 per market per day across 396 markets |
| Spread distribution (795 rows) | p10 0.0100, median **0.0300**, p90 **0.9400** | 451 rows ≥ 2 ticks; the top decile is effectively untradeable |
| **Measured holding period** | **NONE EXISTS** — 0 settlement rows, **0 fills**, 0 settled | working capital is **NOT_IDENTIFIED** |

**The conclusion is not "narrow the gap by scaling."** To execute $500,000/day
of notional, BETTOR would have to be a majority of everything that trades in
the markets it observes. That is a market-share statement, not a throughput
one, and no amount of engineering changes it.

### 9.4 "One hour and one day are scenarios, not bounds" — confirmed

Working capital = position notional ÷ turns per day, and turns per day is the
reciprocal of the holding period. **The repository contains zero fills**
(`bettor_state_settlements`: 0 rows, `is_not_a_fill IS FALSE`: 0). There is no
measured holding period anywhere, so there is no capital figure — only a
formula with an unmeasured denominator. Any capital number quoted for this
strategy is a scenario.

### 9.5 What these figures do NOT establish

- **Our observed universe is not the venue.** 1,526 markets were observed by a
  research sampler that was not built for coverage. The venue may be larger.
  Expanding the universe is now a **specific, measurable requirement** rather
  than an assumption — but nothing here measures the whole venue.
- **Traded volume is what the market did WITHOUT us.** Our participation would
  change it, in either direction, by an unknown amount.
- **Displayed depth is a loose upper bound on executable depth.** Track B-L
  BLOCK_4: a 22,297-share displayed bid queue against 180 shares traded in 16
  minutes, with zero touches.
- **The volume figure is a snapshot statistic**, taken at observation time. It
  is the best bound available and it is not a flow measurement.

## 10. THE SINGLE MOST USEFUL NUMBER IN THIS DOCUMENT

**$396,361.** The total traded notional across every market BETTOR observed in
a day. Every capacity claim in the acceptance package was made without it, and
it was one query away the whole time.

---

# PART III — THE ACCOUNT READS, AND THE ONE BLOCKER THAT IS NOT MINE

`bettor-capability-probe.yml`, run
[35641228446](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35641228446),
conclusion **success**, every venue read **failed**.

## 11. `capability_probe()` AND `account_identity()` — RUN, AND BLOCKED

| read | result |
|---|---|
| `portfolio.activities` | `AuthenticationError` |
| `portfolio.balances` | `AuthenticationError` |
| `orders.list` | `AuthenticationError` |
| `portfolio.positions` | `AuthenticationError` |
| `account_identity()` | verdict **`unreadable`** |
| `identity_fields_found` | `[]` |

**The cause is in the job's own environment block:**

```
PMUS_KEY_ID:
PMUS_SECRET_KEY:
```

**Both empty.** `secrets.PMUS_KEY_ID` and `secrets.PMUS_SECRET_KEY` are not
set for this repository, or not exposed to this workflow. With neither
present, `pmus._get_client()` takes its documented public-only branch —
`PolymarketUS()` with no credentials — and every account endpoint rejects the
call. One cause, four failures.

**This is a precise access blocker, not a capability gap.** The reads are
built, they are proven order-free by an AST test that runs in a step holding
no credentials, and they execute. What is missing is a credential, and
supplying one is an owner action, not an engineering one.

**What it does NOT tell us**, and must not be reported as if it did:

- It does **not** establish `holds_both_legs_independently`. That stays
  UNKNOWN and keeps blocking `PAIR_BUY` on every decision.
- It does **not** establish account identity. That stays unverified, and
  §2 of the pilot proposal still holds: *a pilot on an account we cannot
  name is not a pilot.*
- It does **not** establish that the credentials are wrong. Absent and
  rejected are different failures; this one is absent.

**What is needed:** `PMUS_KEY_ID` and `PMUS_SECRET_KEY` present in the
repository secrets, or the same probe run inside the API service, which
already holds them in its own environment. Either answers all three
questions in one read. Nothing else in the account path is blocked on
anything I can write.

## 12. THE RESOLUTION FIELD IS STILL A GUESS

The probe's third question — does a market payload carry a resolution field,
and what is it called — was skipped because no slug was supplied, and would
have been answerable without credentials since `markets.list` is public. That
is a gap in **my dispatch**, not in the code: `resolution_fields()` takes a
slug and returns key names only.

Until it runs, `bettor_settlement_ingest`'s `OUTCOME_FIELDS` remains a
hypothesis, and the module says so — a payload matching none of them returns
`UNREADABLE` **with the key names that were present**, which corrects the list
with one read instead of more guessing.

## 13. THE RESOLUTION FIELD — MY HYPOTHESIS WAS WRONG, AND ONE READ SHOWED IT

Second probe run,
[35641425742](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35641425742),
on a real slug. The market listing is **public** and returned `ok: true`
without credentials.

**`outcome_field: null`.** Not one of the eight names I guessed
(`resolvedOutcome`, `resolved_outcome`, `winningOutcome`, `winning_outcome`,
`settledOutcome`, `settled_outcome`, `result`, `outcome`) is on the payload.
The module was written to return the key names for exactly this case, and
that is what corrected it — in one read, instead of more guessing.

**The 34 keys the payload actually carries:**

```
active archived assetPriceTerms category closed comboEnabled createdAt
description endDate ep3Status ep3SyncedAt feeCoefficient gameStartTime
hidden id line manualActivation marketSides marketType minimumTradeQty
orderPriceMinTickSize outcomePrices outcomes question rulesDisclaimer
rulesDisclaimerPopup slug sportsMarketType sportsMarketTypeV2
spreadTotalSuffix startDate status tags updatedAt
```

### 13.1 The venue does not report a winner on this endpoint

`outcomes` is the side labels and `outcomePrices` the current prices — both
present on open markets, neither a resolution. At settlement the prices
converge to 1 and 0, and **reading a price of 1 as "this side won" is an
inference the venue did not make.**

So it gets its own status, `RESOLVED_DERIVED`, which is **never counted as
`RESOLVED`**, is written with
`SETTLEMENT_SEMANTICS_STATUS = SEMANTICS_DERIVED_FROM_CONVERGED_PRICES_UNVERIFIED`,
and requires **exact** convergence: a market at 0.99 has not settled, it is
nearly certain, and those are different facts.

### 13.2 Three fields the pilot proposal assumed and the venue reports

| field | why it matters |
|---|---|
| `minimumTradeQty` | the pilot's size floor is **readable**, not assumed |
| `orderPriceMinTickSize` | the "spread ≥ 2 ticks" rule now has a venue-supplied tick |
| **`feeCoefficient`** | **a direct check on which published schedule is in force** — 0.06 is the 2026-07-01 θ_taker, 0.0695 the 2026-09-17 one |

`feeCoefficient` is the one that matters most: it can move the fee schedule
from `PUBLISHED` toward verified application without a settled statement, and
it costs one public read. It is not in this run's output because the probe
returns key names only; reading its **value** is a public market parameter,
not account data, and is the obvious next read.

### 13.3 `endDate` is the only settled-at field present

Of the eight `SETTLED_AT_FIELDS` candidates, only `endDate` exists. It is a
scheduled end, not necessarily a resolution time, which is one more reason a
derived outcome may not claim verified semantics.
