# BETA48 — Engine B/C dataset inventory

Every figure below was recomputed from the raw files for this document,
not copied from a prior write-up. Where a prior write-up disagrees, the
disagreement is stated.

---

## PRESERVED PERMANENTLY — Engine A, closed

```
BETA48_DATA_GATE                = PASS
ENGINE_A_BAND_CANDIDATE         = DEAD
STRUCTURAL_EDGE_VALIDATED       = NO
BETTOR_EXECUTION_VALIDATED      = NO
READY_FOR_SHADOW                = NO
READY_FOR_MICRO_LIVE            = NO
UNPAIRED_LEG_EXIT_COST          = NOT_IDENTIFIED

COMPLETED_PAIR_EDGE             = OBSERVED
TOTAL_PAIR-ENGINE_EXPECTANCY    = NOT VALIDATED
```

The 6/6 monotone MERGE relationship is a real descriptive mechanism
result. Residual/unpaired inventory prevents promotion into a BETTOR
strategy. Engine A is closed for this sprint: no threshold mining, no
rescue, no promotion of the 0.90–1.01 TOTAL observation.

---

## A MATERIAL CORRECTION TO THE PRIOR ARCHAEOLOGY

`TRACK_P_DATA_ARCHAEOLOGY.md` records, for the CLOB snapshot:

> `BID_AVAILABLE` | **NO**

and concludes the two-sided-state-plus-outcome join is empty.

That is true of a literal `best_bid` field and true of the **PMUS** join.
It is **not** true of the CLOB snapshot's economics, because on a binary
one-hot market the complement's ask reconstructs the missing bid:

```
bid(leg 0)  =  1 − ask(leg 1)
mid(leg 0)  =  ( ask(leg 0) + 1 − ask(leg 1) ) / 2
```

RN1 is a pair trader, so he trades **both** legs of the same condition,
and the probe fires on each trade. The snapshot therefore contains ask
observations on both legs of the same condition, frequently seconds
apart.

| | |
|---|---|
| distinct conditions | 17,753 |
| conditions with BOTH legs observed | **9,057 (51.0%)** |
| of those, exactly-two-outcome (binary) | **8,989** |

This does not overturn the PMUS finding and does not make the DATA-B
forward capture unnecessary — see the limits below — but it does mean a
two-sided state **with outcomes** exists today and was never tested.

### The first thing that state says, and it is decisive

`ask(leg0) + ask(leg1)`, by how far apart in time the two reads were:

| gap ≤ | pairs | median sum | min | share < 1.00 |
|---|---|---|---|---|
| **1 s** | 4,101 | **1.0100** | **1.0000** | **0.00%** |
| 5 s | 6,855 | 1.0100 | 0.5200 | 0.93% |
| 15 s | 4,269 | 1.0100 | 0.7300 | 6.63% |
| 60 s | 11,618 | 1.0200 | 0.5800 | 17.95% |
| 1 h | 30,564 | 1.0150 | 0.0400 | 41.38% |

At near-simultaneity there is **not one** sub-parity observation in
4,101 pairs. The apparent "arbitrage" grows monotonically with the time
gap, which is the signature of **stale prices**, not of opportunity.

Two consequences, both load-bearing:

1. **`DUTCH_ARBITRAGE_ON_CLOB = NOT_PRESENT`** — measured, not assumed.
   Mechanism class B (relative value on a *known* payoff relationship)
   is closed on this data before any model was built.
2. **It explains Track P's premise.** Track P's Q1 found `SETTLE − ASK`
   negative in 9 of 10 price bands and read the ask as "systematically
   expensive". Both asks are expensive, by the round-trip spread of
   ~1.0 cent. A quantity that is symmetric on both sides of a binary
   carries no direction. The Track P premise was the spread.

**`EXECUTABLE_ROUND_TRIP_SPREAD = 1.0 cent (median), 1.95 cents (mean)`**
on CLOB in this window. That is a measured cost floor every Engine B
candidate must clear, and it is now a number rather than an assumption.

---

## DATASET 1 — `AUDIT_SNAPSHOT_V1` (the only train/validate/holdout asset)

```
DATASET                     = AUDIT_SNAPSHOT_V1 (u2_events_v1 + settlement_v1)
MARKETS                     = Polymarket CLOB, condition_id + ERC-1155 token id
TIME RANGE                  = 2026-08-06 .. 2026-09-11 (36 days)
OBSERVATIONS                = 214,609 (trade, probe) rows, book_ok on 100%
                              17,753 distinct conditions
                              8,989 binary conditions with BOTH legs
TARGET                      = one-hot settlement payout, settlement_v1
                              9,545 resolved one-hot binary conditions
PRE-ENTRY FEATURES AVAILABLE= best_ask + ask ladder (median 8 levels) on the
                              traded leg; complement ask -> implied bid, mid,
                              spread; ask-side depth and notional; sport;
                              market_slug; probe_at; RN1's own trade price/size
                              (usable only as a SELECTION marker, never as a
                              forward feature)
EXECUTABLE PRICE AVAILABLE  = YES for BUY (best_ask is executable size-limited);
                              SELL executable price = NOT_IDENTIFIED (implied
                              bid is arithmetic, not an observed resting bid)
DEPTH AVAILABLE             = ASK SIDE ONLY, ladder + top-of-book notional.
                              Bid-side depth = NOT_IDENTIFIED
SETTLEMENT AVAILABLE        = YES, with resolved_at, one-hot verified
FEES VERIFIED               = NO for CLOB. Track P applied a cross-venue fee
                              ASSUMPTION, not a verified schedule.
                              FEES_VERIFIED = NOT_IDENTIFIED
LEAKAGE RISK                = MEDIUM-HIGH, and specifically:
                              (a) SELECTION: every row exists because RN1
                                  traded that token. Not the tradable universe.
                              (b) IMPACT: probe fires ~37 s median AFTER his
                                  trade, so the observed ask may contain his
                                  own impact.
                              (c) resolved-at-probe rows must be excluded
                                  (1,684 of them); resolved_at > probe_at is
                                  enforced here.
                              (d) RN1's trade price/size must never enter a
                                  decision feature.
CURRENT-REGIME COVERAGE     = NO. Ends 2026-09-11; today is 2026-09-16.
                              5-day gap, and the venue is CLOB, not PMUS
                              where BETTOR trades.
SUITABLE_FOR_TRAINING       = YES, with the selection caveat carried
SUITABLE_FOR_VALIDATION     = YES, chronologically split
SUITABLE_FOR_FINAL_HOLDOUT  = YES, chronologically split, ONE use only
```

### The two-sided, settled, leakage-controlled subset (new)

Pairs of opposite-leg probes ≤ 5 s apart, on binary conditions, with a
one-hot settlement whose `resolved_at` is strictly after both probes:

| | |
|---|---|
| observations | **7,149** |
| **independent conditions** | **1,583** |
| time range | 2026-08-06 .. 2026-09-11 |
| sports | Tennis 4,156 · Soccer 1,635 · NFL 479 · Other 434 · Non-Sports 211 · MLB 207 · NBA 20 · MMA 7 |
| base rate (leg 0 settles 1) | 0.5156 |
| probe → resolution | median 1.76 h (p10 0.74 h, p90 3.66 h) |
| `ask0+ask1` | median 1.0100, mean 1.0195 |

**1,583 independent conditions is BELOW the ≈2,500 that `DATA-B` set as
the bar for resolving a 2 pp calibration deviation.** It is enough to
detect a large mispricing and not enough to certify a small one. That
constrains what may be concluded, and the gate below is set accordingly.

---

## DATASET 2 — PMUS two-sided captures (Run 85 Phase 2 + Track B-L)

```
DATASET                     = run85_phase2 segments/tarballs + trackbl BLOCK_1..4
MARKETS                     = PMUS (gateway.polymarket.us), marketSlug + side id
TIME RANGE                  = 2026-09-13T02:16 .. 2026-09-15T09:04
OBSERVATIONS                = 15,350 book reads; 6,737 genuinely two-sided
                              12 markets in segments, 30 across everything
TARGET                      = NONE. No market here appears in any resolved set.
PRE-ENTRY FEATURES AVAILABLE= full bid AND ask ladders, lastTradePx,
                              sharesTraded, openInterest, gameStartTime,
                              sportsMarketTypeV2, dual clocks
EXECUTABLE PRICE AVAILABLE  = YES, both sides
DEPTH AVAILABLE             = YES, both sides, full ladders
SETTLEMENT AVAILABLE        = NO   <-- fatal for prediction
FEES VERIFIED               = PARTIAL (run85_trackb_fees); PMUS schedule probed
LEAKAGE RISK                = LOW (selection independent of RN1)
CURRENT-REGIME COVERAGE     = YES, 3 days old, and it is BETTOR's own venue
SUITABLE_FOR_TRAINING       = NO  (12-30 markets, no label)
SUITABLE_FOR_VALIDATION     = NO
SUITABLE_FOR_FINAL_HOLDOUT  = NO
USE                         = execution-cost and spread evidence only
```

## DATASET 3 — PMUS resolved archive rows

```
DATASET                     = PMUS resolved market rows (BLOCK_2 archive walk)
OBSERVATIONS                = 10,585 rows / 10,257 distinct slugs
TARGET                      = YES (outcomes, outcomePrices 1/0)
PRE-ENTRY FEATURES          = gameStartTime, sportsMarketTypeV2, league tags,
                              participants
EXECUTABLE PRICE AVAILABLE  = NO — quotes on the row are POST-resolution
DEPTH AVAILABLE             = NO
SETTLEMENT AVAILABLE        = YES
LEAKAGE RISK                = FATAL if its prices are used as pre-entry state
CURRENT-REGIME COVERAGE     = settlement dates 2025-10-31 .. 2027-04-25
SUITABLE_FOR_TRAINING       = NO (label table only)
SUITABLE_FOR_VALIDATION     = NO
SUITABLE_FOR_FINAL_HOLDOUT  = NO
NOTE                        = intersection with DATASET 2 book markets = 0
```

## DATASET 4 — Run 83.6 CLOB protocol capture

4 tokens, 3 × 75 s, 5,951 frames, 63 two-sided book snapshots. A
protocol reconstruction study four minutes long. Not a dataset for any
purpose here.

## DATASET 5 — engine ledger / run84 pair candidates / U0 timing witness

Our own fills (not market history); derived pair candidates with no time
series; U0 carries `trade_id / condition_id / ts / source` and **no
prices at all**. None is usable for Engine B.

---

## WHAT IS ACTUALLY TESTABLE INSIDE THE REMAINING SPRINT

Testable now, offline, no new collection:

1. **Is the reconstructed MID calibrated to settlement?** This is the
   question Track P could not pose, because it only had an execution
   price. 7,149 observations / 1,583 independent conditions.
2. **Does ask-side depth/ladder shape carry information about the
   outcome, beyond the mid?** Same sample, pre-entry only.
3. **What does an executable BUY at the ask cost against the mid?**
   Already answered: ~0.5 c per side, 1.0 c round trip (median).

Not testable inside the sprint, and stated so rather than attempted:

- Anything requiring PMUS outcomes (no label exists).
- Any maker/resting-fill assumption (`BETTOR_PASSIVE_FILL_PROBABILITY`
  is NOT_IDENTIFIED; BLOCK_4 stands — 22,297 displayed shares, 180
  traded in 16 min, zero touches).
- Any cross-venue equivalence claim (Phase X frozen; Phase X1 recorded a
  side-orientation contradiction).
- Current-regime validation on CLOB (data ends 2026-09-11).

`mirror_live = false`. Read only. No orders, no capital, no production
activation, no Track A change, no Phase X re-dispatch.
