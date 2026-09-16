# BETA48 FORWARD — the sequential evidence program

The retrospective phase is closed. This is the prospective measurement of
the architecture in `BETTOR_ARCHITECTURE.md`.

---

## PRESERVED PERMANENTLY

```
BETA48_ENGINE_B_GATE = FAIL
ENGINE_B             = NO-GO ON CURRENT RETROSPECTIVE EVIDENCE
```

**NOT established, and must not be inferred:**
`BETTOR_NATIVE_SYSTEM = IMPOSSIBLE` · `PAIRING = INVALID` ·
`MARKET_MAKING = INVALID` · `MAKER_REBATE_ECONOMICS = INVALID`.

```
DUTCH_ARBITRAGE_ON_CLOB      = NOT_PRESENT in the tested near-synchronous sample
EXECUTABLE_ROUND_TRIP_SPREAD = 1.0 c
MID_IS_RECONSTRUCTIBLE       = YES
THIN_TOUCH_IS_WORSE_PRICED   = OBSERVED
M1                           = KILLED
M2                           = FAILED FINAL ECONOMIC GATE
FAVOURITE_LONGSHOT_AT_MID    = NOT_IDENTIFIED
```

**M2 holdout disclosure, permanently attached:** one holdout statistic was
inadvertently displayed before the formal holdout read. **That holdout is
not pristine.** No retuning of M1 or M2, no reopened holdouts, no
threshold mining of either.

---

## PHASE 1 — forward market-making observability  ▸ **BUILT AND RUNNING**

`research/beta48/forward/fwd_collect.py`, gated by
`test_fwd_collect.py` (18 tests) which runs **before any venue contact**.

Two sampling jobs, deliberately separated — confusing them is how a fill
probability gets invented:

| | BREADTH | DEPTH |
|---|---|---|
| population | **every** two-outcome event on the board | rotating cohort, nearest to start |
| selection rule | "it was on the board" | declared, time-to-event |
| cadence | one pass per segment (2 h) | every 5 s |
| answers | fair value, settlement sample | quote changes, touches, markouts |

Captured per observation: timestamped two-sided BBO, full ladders both
sides, `leg_gap_ns` between the two legs (Run 84: an uncontrolled gap
*is* the result), market/event identity, sport, league tags,
`gameStartTime`, `endDate`, `sportsMarketTypeV2`, status, venue
rate-limit headers, dual local clocks, response hash and byte count.

**The settlement registry is the step whose absence was the entire gap.**
Every slug BREADTH sees is enrolled and re-read after its `endDate`
until the venue calls it resolved. A market that cannot yet have settled
is not polled.

### Maker measurement — designed, offline, not yet computable

At frozen quote distances, from the DEPTH series:
`QUOTE_OPPORTUNITIES`, `TOUCHES`, `QUEUE_AHEAD`, `QUEUE_DEPLETION`,
`F0/F1/F2_FILL_ESTIMATE`, `F3_TOUCH_UPPER_BOUND`, markouts at
5s/30s/60s/5m, `ONE_SIDED_INVENTORY_DURATION`,
`COMPLEMENT_OPPORTUNITY`, `PAIR_CONVERSION_OPPORTUNITY`,
`SPREAD_CAPTURE`, `MAKER_REBATE`, `VERIFIED_LIQUIDITY_REWARD`,
`ADVERSE_SELECTION`, `CAPITAL_OCCUPANCY`.

**TOUCH is never written into a FILL field.** The collector computes no
fills at all — a test proves it contains no `fill_probability`,
`expectancy`, `roi`, `edge`, `rebate` or `pnl` token in executable code.
F0–F2 are *models* applied offline; F3 is the touch **upper bound**,
reported separately and never as a fill.

---

## PHASE 2 — fair value, feature families frozen BEFORE outcomes

Declared now, before any settlement is observed. **No giant feature
search.**

| family | available pre-entry from this capture? |
|---|---|
| **MARKET_INFORMATION** — mid, spread, reconstructed bid, tick size, last trade, volume, open interest | **YES** |
| **ORDER_BOOK_INFORMATION** — ladder depth both sides, touch size, imbalance, depth slope, quote-change rate | **YES** (this is the genuinely new capability; the retrospective set had ask-side only) |
| **EVENT/SPORT_INFORMATION** — sport, league tag, market type, time-to-event | **YES** for sport/league/type/`gameStartTime`; **NO** for score state, live state, team strength |
| **CROSS_MARKET_INFORMATION** — the complement, and other markets on the same event | **PARTIAL** — complement yes; sibling markets only where the board exposes them on one `eventSlug` |

Target: an **economically useful probability**, scored against
settlement — not next-tick direction.

---

## PHASE 3 — integrated economics

Per opportunity: `BETTOR_FAIR_VALUE`, `MARKET_PRICE`, `FAIR_VALUE_EDGE`,
then separately `EV_MAKER`, `EV_TAKER`, `EV_PAIR_CONVERSION`, `EV_HOLD`,
`EV_EXIT`, and choose `MAX(..., 0 = NO TRADE)`. The decision
architecture, not permission to trade.

---

## PHASE 4 — rebates as first-class economics

`PMUS_MAKER_REBATE`, `PMUS_TAKER_FEE`, `PMUS_VOLUME_TIERS`,
`PMUS_LIQUIDITY_INCENTIVES`, `ELIGIBILITY`, `ROUNDING`,
`PAYMENT_MECHANICS`, `CAPACITY` — **all NOT_IDENTIFIED today.** None is
inferred from whale behaviour. Reporting stays split:
`TRADING_EDGE_BEFORE_REBATE`, `SPREAD_CAPTURE`, `MAKER_REBATE`,
`LIQUIDITY_REWARD`, `ADVERSE_SELECTION`, `INVENTORY_PNL`, `FEES`,
`TOTAL_NET_EDGE`.

The retrospective already set the bar this must clear: against RN1's
flow, `EXPECTED_MAKER_NET_VALUE = −0.0090/share` before rebates, so the
**break-even rebate is 0.90 c/share (1.81% of notional at 50c)**. A
rebate below that cannot rescue quoting into informed flow.

---

## PHASE 5 — sequential checkpoints, and why the early ones only kill

Looks at **N = 100, 250, 500, 1000, 2500** independent settled markets.
One-sided α = 0.05 overall, O'Brien–Fleming spending, computed not
asserted:

| look | N | t = N/2500 | cumulative α spent | nominal z to declare success |
|---|---|---|---|---|
| 1 | 100 | 0.04 | 0.00000 | 7.03 |
| 2 | 250 | 0.10 | 0.00000 | 5.07 |
| 3 | 500 | 0.20 | 0.00024 | 3.50 |
| 4 | 1,000 | 0.40 | 0.00930 | 2.36 |
| 5 | 2,500 | 1.00 | 0.10000 | 1.34 |

**The arithmetic makes the design honest: the first three looks cannot
declare success and are not meant to.** They exist to KILL. Precision
says the same thing — with per-trade return sd ≈ 0.5 the 95% half-width
is ±9.8 pp at N=100 and ±6.2 pp at N=250, so a realistic 3 pp edge is
invisible there. A +3.0 pp edge only becomes distinguishable from zero
at **N ≥ 1,068**.

```
EARLY_KILL_RULE   at every look: stop if the 95% UPPER bound on net edge
                  falls BELOW the minimum economically interesting edge
                  (+3.0 pp net of verified fees). That is the honest
                  early stop -- not "it looks bad" but "we can already
                  EXCLUDE the edge we came for".
                  Additional hard kills, any look:
                    * measured adverse selection exceeds measured spread
                      capture + VERIFIED rebate  -> maker-first is dead
                      on this venue and is reported as such;
                    * DISCOVERY_LIST_EXHAUSTED = NO persists -> the
                      population is a prefix, and the fair-value question
                      cannot be posed; fix before continuing;
                    * settlement join rate stays near zero -> the Phase 1
                      failure has recurred and must be fixed, not
                      analysed around.
CONTINUE_RULE     not killed and not yet at look 4.
FINAL_GATE        net expectancy at executable prices, after verified
                  fees, realistic slippage, measured adverse selection,
                  residual/inventory cost and capital usage, with a
                  clustered CI lower bound above zero at the look-4 or
                  look-5 boundary above. Calibration, Brier, log-loss and
                  accuracy CANNOT pass a candidate.
MULTIPLE-LOOK
CONTROL           O'Brien-Fleming spending as tabulated; the boundaries
                  are fixed now, before any settlement is observed, and
                  do not move.
```

`ESTIMATED_TIME_TO_FIRST_CHECKPOINT` = **NOT_IDENTIFIED until throughput
is measured.** `PMUS_SETTLEMENT_THROUGHPUT` has never been established;
the archive walk that suggested ~12 slugs/day was a bounded prefix and
is a floor, not an estimate. The capture measures board size, achieved
request rate and settlement arrivals in its first segments, and every
calendar figure is then `N / (measured settlements per day)`. No
duration is invented before that.

---

## PHASE 6 — cross-venue readiness, without re-dispatching Phase X

Phase X stays frozen. The schema is nonetheless cross-venue-ready:
every observation carries `eventSlug`, both `slugs`, `gameStartTime`,
`endDate`, `sportsMarketTypeV2` and league tags, so a second venue can
later be joined on **event identity plus start time plus market type**.

**Contract equivalence remains mandatory and title-only matching is
forbidden.** Phase X1 recorded a side-orientation *contradiction*, so
equivalence is contradicted, not merely unverified; any future join must
re-establish it per market before a single number crosses venues.

---

## PHASE 7 — what can start without capital

| | |
|---|---|
| `READ_ONLY_CAN_START_NOW` | **YES — started.** Board enumeration, breadth, depth, settlement follow-up. Public gateway, GET only, no credential. |
| `SHADOW_CAN_START_NOW` | **YES, offline.** Hypothetical quotes at frozen distances are computed from captured books; nothing is sent. |
| `REQUIRES_CREDENTIALS` | Only the authenticated websocket (sub-second markouts). **Not requested** — REST polling is the resolution limit and that is stated rather than discovered later. |
| `REQUIRES_NETWORK_EGRESS` | This container cannot reach the venue. **GitHub Actions can** — already proven by the run85 collector and by this run's first steps. |
| `REQUIRES_REAL_MONEY` | Only true fill confirmation. **Not started.** |
| `REQUIRES_USER_APPROVAL` | Any order, any capital, production activation, Track A change, Phase X re-dispatch. |

---

## STATUS

```
FORWARD_EXPERIMENT_READY        = YES — built, gated, and RUNNING
DATA_SOURCE                     = PMUS public gateway (gateway.polymarket.us),
                                  GET only, no credential, 2.0 rps ceiling
MARKET_COVERAGE                 = whole active sports board, paginated to a
                                  terminal boundary; a prefix is MARKED as a
                                  prefix (DISCOVERY_LIST_EXHAUSTED)
EXPECTED_REQUEST_RATE           = <= 2.0 rps, Retry-After honoured exactly
SETTLEMENT_CHECKPOINTS          = N = 100 / 250 / 500 / 1000 / 2500
EARLY_KILL_RULE                 = 95% upper bound on net edge below +3.0 pp,
                                  or adverse selection > spread + verified
                                  rebate, or a persistent prefix population,
                                  or a near-zero settlement join rate
MAKER_EXECUTION_MEASUREMENT     = DEPTH series; F0/F1/F2 offline models,
                                  F3 touch upper bound reported separately;
                                  TOUCH never written as FILL
REBATE_MODEL_STATUS             = NOT_IDENTIFIED — to be verified in Phase 4
LIQUIDITY_REWARD_STATUS         = NOT_IDENTIFIED — eligibility unverified
FAIR_VALUE_FEATURE_SET          = frozen above; MARKET + ORDER_BOOK fully
                                  available, EVENT partial, CROSS_MARKET partial
PAIR_CONVERSION_MEASUREMENT     = complement book captured on every event, so
                                  conversion cost is observable per timestamp
CROSS_VENUE_READY_SCHEMA        = YES — event identity + start time + market
                                  type carried; equivalence still mandatory
FIRST_DECISION_CHECKPOINT       = N = 100 settled markets, KILL-ONLY
ESTIMATED_TIME_TO_FIRST_CHECKPOINT = NOT_IDENTIFIED until throughput is
                                  measured; derived as N / (measured
                                  settlements per day) from the first segments
BIGGEST_BLOCKER                 = PMUS settlement throughput is unmeasured, so
                                  calendar time to ANY checkpoint is unknown.
                                  It is now being measured rather than assumed.
```

`mirror_live = false`. No orders, no capital, no credential, no new
secret, no Track A change, no Phase X re-dispatch, no production
activation.
