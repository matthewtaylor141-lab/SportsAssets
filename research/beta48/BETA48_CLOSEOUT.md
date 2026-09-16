# BETA48_CLOSEOUT — canonical source of truth

Every other rendering of this sprint (the management artifact, any PDF) is a
**view of this file**. Where they disagree, this file is correct.

```
SPRINT_CLOSEOUT_STATUS   = RESEARCH / ARCHITECTURE SUBSTANTIALLY COMPLETE
BETTOR_V1_BETA_STATUS    = SPECIFIED, SHADOW-ONLY, NOT BUILT
MAKER_ENGINE_GATE_V2     = BLOCKED
MAKER_PROFITABILITY      = NOT_ESTABLISHED
STAGE2_CENSUS_STATUS     = IN FLIGHT
MICRO_LIVE_AUTHORIZED    = NO
mirror_live              = false
```

The sprint is **not downgraded** because a production-validation gate remains.
Two different things are being graded:

```
ARCHITECTURE / RESEARCH SPRINT   = SUBSTANTIALLY COMPLETE
PRODUCTION EXECUTION VALIDATION  = NEXT PHASE
```

---

## 1. THE MANAGEMENT NARRATIVE

**48 hours ago.** BETTOR was trying to replicate profitable whale behaviour.
The working hypothesis was that opening a cheap first leg predicts profitable
pair completion, and six large reference accounts were believed to demonstrate
it. We had no measurement of our own execution environment at all.

**What we discovered.**

- **No single whale mechanism explains all the profits.** Six of six accounts
  reproduce the textbook merge curve; one of six has money that follows it.
  `WHY_SOME_WHALES_PROFIT = NOT_IDENTIFIED`, and band mix is refuted as the
  explanation.
- **Completed-pair profitability can coexist with destructive residual
  inventory.** kch123's cheapest band: `MERGE_ROI +59.49%`,
  `TOTAL_ROI −92.84%` — $4,336 merged against −$142,150 settled.
- **Directional economics matter.** Three accounts made their money in the
  bands where their pairing did worst.
- **Execution and capital recycling matter.** A one-tick spread is worth
  $0.0100 per contract *if captured*; reaching the front of the queue takes one
  to two hours, before any fill.
- **Blind copying is not sufficient** — and is not even well-defined, because
  nobody has isolated what the profitable accounts are doing.

**What we built.** A modular BETTOR-native architecture that chooses among
MAKE, TAKE, PAIR, HEDGE, HOLD, EXIT and NO TRADE on expected economics, with
fifteen separate P&L ledgers, event-level exposure limits bound on a proved
lower bound, and six kill switches.

**What we killed.**

```
blind whale copying
cheap-first-leg pair entry
same-venue "free arbitrage"
automatic cash-outs
blended pair / residual P&L
assumed fill probability
assumed board generalization
```

**What exists now.** Candidate-market discovery; eligibility architecture;
maker/taker/no-trade decision architecture; pair/residual inventory rules;
capital recycling rules; P&L ledger architecture; risk and kill framework;
micro-live validation protocol; institutional data roadmap.

**What remains.** Fair-value edge validation; non-UFC execution
characterization; actual passive fills; actual adverse selection; actual
capital-turnover economics.

---

## 2. THE SUPER-MACHINE — BETTOR IS NOT A CLONE OF ANY ROW

| | PAIR_COMPLETION | DIRECTIONAL_HOLD | RESIDUAL_INVENTORY | CAPITAL_RECYCLING | EXIT_BEHAVIOR | MAKER / REBATE | KNOWN_STRENGTH | KNOWN_FAILURE_MODE |
|---|---|---|---|---|---|---|---|---|
| **RN1** | 78,500 merges | primary, takes at once | held to settlement | via merge/redeem only | **zero sells** — exits by buying the complement | pure taker; the maker facing him lost 0.90¢/share (Polymarket CLOB, 2026-08-06..09-11) | informed entry; band economics monotone | two sources disagree by up to 21.2 pp; good only cheap |
| **Ferrari** | pair-positive, ρ −1.000 | present, not separable | settlement | merge-driven | ≤0.54% of stake | none observed | best total ROI, +7.06%; independent of discovery | merge curve perfect, total ρ only −0.257 |
| **SwissTony** | ρ −1.000 | NOT_IDENTIFIED | settlement | NOT_IDENTIFIED | ≤0.54% | none observed | none adoptable — excluded | 6.06M fills for +0.10%; volume ≠ edge |
| **HomeRunHazard** | pair-negative | money came from its worst pairing band | settlement | none observable | **exactly zero sells** | none observed | none | textbook curve, negative money |
| **kch123** | ρ −0.943, total ρ **+0.029** | NOT_IDENTIFIED | catastrophic | none | none | none | none | **33× loss ratio** on its best-looking band |
| **w2c33** | pair-negative | money from its worst pairing band | settlement | none | none | none | none | worst total ROI, −8.04% |
| **BETTOR** | **ADOPT validated pair economics** — never as an entry reason | **ADOPT independent fair value** as the entry test | **ADOPT separate residual management**, own ledger | **ADOPT capital recycling** — completed pairs monetize when superior | **ADOPT** sale AND complement-buy; **REJECT forced cash-out** | **ADOPT maker-first where justified**; rebate never rationalises a negative trade | chooses per opportunity instead of imitating | **execution unproven — that is why the gate is BLOCKED** |

**The BETTOR row in full:**

```
ADOPT   VALIDATED PAIR ECONOMICS
ADOPT   INDEPENDENT FAIR VALUE
ADOPT   MAKER-FIRST WHERE JUSTIFIED
ADOPT   CAPITAL RECYCLING
ADOPT   SEPARATE RESIDUAL MANAGEMENT

REJECT  BLIND CHEAP-FIRST-LEG ENTRY
REJECT  FORCED CASH-OUT
REJECT  PNL BLENDING
REJECT  COPYING WITHOUT EXECUTION PROOF

NO TRADE IS A FIRST-CLASS ACTION
```

---

## 3. THE ARCHITECTURE

```
            MARKET DATA / FAIR VALUE
                      |
                      v
        ELIGIBILITY / MARKET SELECTION
                      |
                      v
        +--------------------------------+
        | EV_MAKER | EV_TAKER | NO TRADE |
        +--------------------------------+
                      |
                      v
                  EXECUTION
                      |
                      v
             PER-LEG INVENTORY
                      |
     +----------> COMPLEMENT / PAIR
     |                    |
     |                    v
     |            LOCK / REALIZE / RECYCLE
     |
     +----------> HOLD
     +----------> HEDGE
     +----------> EXIT
     +----------> SETTLEMENT
                      |
                      v
              CAPITAL ALLOCATOR
                      |
                      v
              NEXT OPPORTUNITY
```

Completed economic pairs **default toward monetization and capital recycling**
when that is economically superior. Residual legs remain **independently
managed** on fair value, inventory, time and exit economics. Every arrow can
return NO TRADE; the pipeline is not a funnel that must produce an order.

Full rules: `BETTOR_V1_ARCHITECTURE.md`.

---

## 4. THE P&L LEDGERS

```
DIRECTIONAL_TRADING_PNL     PAIR_COMPLETION_PNL     RESIDUAL_INVENTORY_PNL
EXIT_HEDGE_PNL              SETTLEMENT_PNL          SPREAD_CAPTURE
ADVERSE_SELECTION           OTHER_COSTS
MAKER_REBATES               TAKER_FEES              TAKER_REBATES
LIQUIDITY_INCENTIVES        FILL_INCENTIVES         VOLUME_INCENTIVES
NEGOTIATED_MM_INCENTIVES
                    |
                    v
        TRADING_NET_EX_INCENTIVES      <- reported FIRST, independently
        INCENTIVE_CONTRIBUTION
        TOTAL_NET                      <- reported LAST
```

```
PAIR PROFITS CAN NEVER HIDE RESIDUAL LOSSES.
INCENTIVES CAN NEVER HIDE NEGATIVE TRADING ECONOMICS.
```

If `TRADING_NET_EX_INCENTIVES <= 0` the run is labelled `INCENTIVE_DEPENDENT`
in every output, whatever `TOTAL_NET` says. MODEL A (structural) and MODEL B
(incentive-supported) are both legitimate businesses and are never blended.

---

## 5. TOP 5 PROVEN FINDINGS

1. **The merge curve is universal and uninformative about money.** 6/6 accounts
   replicate it (ρ −0.94 to −1.00); 2/6 clear the preregistered bar on total
   economics; **14 of 36 bands flip sign**. All six reconcile at $0.00.
2. **Resting at the touch against one informed taker was value-destructive.**
   −$0.0090/share net, 95% CI [−0.0143, −0.0038], clustered by condition,
   112,553 trades over 9,337 conditions, **fills observed not modelled**,
   marked **to settlement**. Adverse selection is 2.8× the half-spread earned.
   **Scope, which travels with the number:** Polymarket CLOB — *a different
   venue from every other figure here* — 2026-08-06..09-11, one counterparty,
   an anonymous resting offer that was not ours.
   `THIS_IS_ACTUAL_BETTOR_ADVERSE_SELECTION = NO`. Full provenance in
   `MAKER_ENGINE_GATE_V2.md`.
3. **The maker rebate does not exist at small clip size.** Banker-rounded per
   fill, so a one-contract fill earns **exactly $0.00**; the minimum clip is
   U-shaped in price (41 contracts at p=0.01, 2 at p=0.30–0.70, 41 at p=0.99).
4. **Market count can massively overstate independent capital opportunities.**
   On the strong-identity subset: `STRONG_IDENTITY_SUBSET_MARKETS = 4,783`,
   `PROVEN_DISTINCT_CONTESTS_IN_SUBSET = 72` — 66.4 markets per contest, from
   the venue's own team and start-time fields with no heuristic in the chain.
   This is a SUBSET, not the board:
   `EXACT_FULL_BOARD_INDEPENDENT_EVENT_COUNT = NOT_IDENTIFIED`.
5. **The one-tick spread OPPORTUNITY is widespread; capture is not
   established.** One tick in **8,357 of 11,290** two-sided markets (74.0%)
   board-wide — `ONE_TICK_SPREAD_OPPORTUNITY_IS_WIDESPREAD = OBSERVED`. But
   `BETTOR_REALIZED_SPREAD_CAPTURE = NOT_ESTABLISHED`: realizing it needs a
   passive fill, a queue position, a survivable adverse-selection cost and
   inventory management, none of which are established. Queue depth and trade
   frequency need a book read and are measured on 1.9% of the routed universe.

## 6. TOP 5 REMAINING GATES

1. `ACTUAL_BETTOR_PASSIVE_FILL_RATE` — no observation resolves it; only our own
   resting order does. **The blocker.**
2. `FAIR_VALUE_EDGE` — `NOT_IDENTIFIED`. Without it every EV is
   `NOT_IDENTIFIED` and the beta correctly returns NO TRADE everywhere.
3. `NON_UFC_EXECUTION_CHARACTERIZATION` — **in flight now**, free.
4. `ACTUAL_ADVERSE_SELECTION` on BETTOR's own fills. The −$0.0140 figure is a
   **constraint from a different flow**, not this panel's number.
5. `UNPAIRED_LEG_EXIT_COST` — no reference account supplies it, because none of
   the six sell. It may not be assumed cheap.

---

## 7. WHAT A3 DOES AND DOES NOT SAY

```
SAYS      UFC is 174 of 9,185 stage-1 BROAD routed markets = 1.9%.
          UFC microstructure is therefore INSUFFICIENT to characterize the
          broader observed prefix.
          The other 98.1% is the PRIMARY GENERALIZATION TARGET.

DOES NOT  "The other 98.1% has better economics."
SAY       That has not been measured. The census now running is what would
          measure it, and it may come back worse.
```

Terminology, fixed at the source and asserted by a test: **9,185 =
`STAGE1_BROAD_ROUTED_MARKETS`**, never `PROVEN_MAKER_ELIGIBLE_MARKETS`.
`ACTIVE`, `HIGH_ACTIVITY` and every execution term are unknown for them.

---

## 8. WHY BLOCKED IS DECISIVE, AND IS NOT FAILURE

```
ONE_TICK_SPREAD_OPPORTUNITY              OBSERVED, widespread (74.0%)
BETTOR_REALIZED_SPREAD_CAPTURE           NOT_ESTABLISHED
UFC EXECUTION ENVIRONMENT                POOR (1 trade / 44.9 market-minutes;
                                         16 of 23 markets traded zero times)
NON-UFC TRADE FREQUENCY AND DEPTH        INSUFFICIENTLY MEASURED
ACTUAL BETTOR FILL PROBABILITY           UNMEASURED
ADVERSE SELECTION                        INSUFFICIENTLY MEASURED
FAIR-VALUE EDGE                          NOT_IDENTIFIED
=> TOTAL NET ECONOMICS                   NOT_IDENTIFIED
=> MAKER_ENGINE_GATE_V2                  BLOCKED
```

BLOCKED means **the architecture is built and the remaining validation gates
are explicit**. It is not PASS and must not be softened into one. It is not
failure: nothing measured shows the maker engine loses money.

We did not manufacture PASS to make the sprint feel successful, and we did not
substitute conditional arithmetic — "if we filled at rate r we would earn X" —
for execution evidence.

**The arithmetic we deliberately did not do.** Measured adverse selection
against informed flow is −$0.0140/share, which is **91.5%** of the $0.0153
gross available on the panel. Subtracting one from the other would produce a
tidy net from two unrelated samples. It is carried as a constraint on how large
the unmeasured term could be, not as a result.

---

## 9. CORRECTIONS ISSUED THIS SPRINT

Kept at the same prominence as the findings.

```
RETRACTED  "20,000 markets overstates the effective sample by more than an
           order of magnitude." The derived event key fails validation in BOTH
           directions -- it over-merges (394 markets, 32 participant sets, one
           key) and under-merges (8 keys, one 32-team roster, one settlement
           date). Errors in opposite directions do not cancel to a known
           quantity. SURVIVES: market-level independence is false, now PROVED.

WITHDRAWN  A first-pass collapse that merged 409 distinct US House races into
           one "event". The middle slug segment is a market TYPE in one family
           and a CONTEST IDENTITY in another.

FIXED      A cluster where NOBODY filled in gameStartTime was promoted to a
           proven single contest, because {None} has length one. Caught by a
           test, not by reading the code.

REJECTED   `breadth --book-reads N` as the census mechanism: it reads the first
           N markets in BOARD ORDER, a systematic ordering and a prefix of a
           prefix. The census routes on STAGE1_BROAD instead.
```

---

## 10. END-OF-DAY STATUS BOARD

```
WHALE DECOMPOSITION                 COMPLETE
FAILURE-MODE IDENTIFICATION         COMPLETE
BETTOR V1 ARCHITECTURE              COMPLETE
PAIR / RESIDUAL ACCOUNTING          COMPLETE
MARKET-SELECTION ARCHITECTURE       COMPLETE
MAKER ECONOMIC FRAMEWORK            COMPLETE
RISK / KILL ARCHITECTURE            COMPLETE
MICRO-LIVE PROTOCOL                 COMPLETE
UFC EXECUTION STUDY                 COMPLETE
OBSERVED-PREFIX STAGE-1 STUDY       COMPLETE
NON-UFC STAGE-2 EXECUTION STUDY     IN FLIGHT
ACTUAL BETTOR PASSIVE FILLS         NOT YET AUTHORIZED
PRODUCTION CAPITAL                  NOT YET AUTHORIZED

48-HOUR SPRINT STATUS
    RESEARCH / ARCHITECTURE = SUBSTANTIALLY COMPLETE
    PRODUCTION VALIDATION   = NEXT PHASE
```

---

## 11. FILES PRODUCED

```
research/beta48/
  BETA48_CLOSEOUT.md              <- this file, canonical
  WHALE_COHORT_LESSONS.md         cohort decomposition + adopt/reject
  BETTOR_V1_ARCHITECTURE.md       frozen pipeline + 15 ledgers
  BETTOR_V1_BETA_SPEC.md          16 capabilities, shadow-only
  MICRO_LIVE_VALIDATION.md        protocol, MICRO_LIVE_AUTHORIZED = NO
  forward/
    MAKER_ENGINE_GATE_V2.md       the gate and its evidence
    DECISION_REPORT_S10.md        the v1 gate, retained unedited
    EVIDENCE_SET_FREEZE.md        roles fixed before inspection
    MAKER_ELIGIBLE_UNIVERSE_V1.md preregistration + CL-1..CL-28
    eligibility.py                frozen screen, event identity, audit
    census_collect.py             stage-2 census (NEW)
    test_census_collect.py        14 structural tests (NEW)
    fwd_collect.py                UNCHANGED, 0-line diff
.github/workflows/
  beta48-stage2-census.yml        the census job (NEW)
```

Management rendering: https://claude.ai/artifact/KzRXmPYYjmU8LvDHB5V2mP

---

```
No credentials. No authenticated connection. No orders. No capital.
No production activation. No Track A modification. No Phase X re-dispatch.
mirror_live = false.
```

---

# CLOSEOUT_ADDENDUM_STAGE2

```
RUN_ID            35105863528     STATUS  completed / success  (queried, not inferred)
SCAN              2026-09-16 14:04:57Z -> 15:27Z, 4,937.3s = 82.3 min
REQUESTS_USED     9,715           RATE    1.97 reads/s
SEALED            SHA256 over board.json, board_raw.jsonl, census.jsonl, census_plan.json
```

**This addendum adds directly measured fields. It rewrites no historical
conclusion, and it cannot change `ACTUAL_BETTOR_FILL_RATE`,
`ACTUAL_BETTOR_ADVERSE_SELECTION`, `FAIR_VALUE_EDGE` or
`MAKER_PROFITABILITY` — no order was sent. `MAKER_ENGINE_GATE_V2 = BLOCKED`
stands.**

## The count reconciliation, extended

```
9,267  earlier seg-8 board, stage-1 BROAD under the pre-amendment rule
9,182  RETRACTED -- a 9h26m-stale join of seg-10 quotes to seg-8 status
9,185  the corrected figure for THAT board snapshot
9,174  THIS census's own board, enumerated fresh at 2026-09-16 14:03Z
```

**9,174 is a fourth board, not a fourth count of the same board.** The board
turns over daily; a market that settled overnight is gone and a new one is
listed. Nothing here revises 9,185, which remains correct for its snapshot.

## What the census measured

| | ROUTED | AUDIT (stage-1 rejects) |
|---|---|---|
| books read | 9,168 | 541 |
| BROAD_AT_DECISION | 9,056 (98.78%) | 10 (1.85%) |
| ACTIVE_AT_DECISION | 5,558 (60.62%) | 8 (1.48%) |
| HIGH_ACTIVITY_AT_DECISION | 788 (8.60%) | 0 (0.00%) |
| spread ≤ 1 tick at decision | 89.58% | 0.87% |
| spread ticks P10/P50/P90 | 1 / 1 / 2 | 9 / 26 / 97.2 |
| last-trade timestamp MISSING | 171 (1.87%) | 426 (78.74%) |
| trade recency P10/P50/P90 (s) | 3,874 / 52,744 / 248,840 | 15,628 / 158,741 / 588,661 |

```
STAGE2_BOOK_READS   9,715      STAGE2_VALID_BOOKS  9,709      UNREADABLE  6
NEGATIVE_RECENCY_CLOCKS  0
```

**Missingness is reported separately and is never read as staleness.** 597
markets board-wide have no `lastTradeSetTime` at all; a market that never
traded and one that traded two days ago fail the same tier for entirely
different reasons.

## The routing rule is not badly censoring

```
ROUTING_FALSE_NEGATIVE_BROAD_RATE   0.0185   (10 of 541)
ROUTING_FALSE_NEGATIVE_ACTIVE_RATE  0.0148   ( 8 of 541)
```

And the audit lane was genuinely interleaved, not parked at the end where
every market would have had maximum time to tighten:

```
ROUTED_ELAPSED  P10 482.1s   P50 2,450.4s   P90 4,450.8s
AUDIT_ELAPSED   P10 576.7s   P50 2,568.5s   P90 4,452.1s
MEDIAN_ELAPSED_GAP 118.1s    TIMING_COMPARABLE = true
SCHEDULE_FAIRNESS_ASSERTED_FROM_POSITION = false
```

A fair schedule *position* was never accepted as a fair observation *time*;
the elapsed clocks were measured and they agree.

## UFC vs the other 98.1% — the answer is MIXED, and one half is worse

| | ROUTED_UFC | ROUTED_NON_UFC |
|---|---|---|
| N | 172 | 8,996 |
| BROAD_AT_DECISION | 98.26% | 98.79% |
| **ACTIVE_AT_DECISION** | **87.79%** | **60.10%** |
| **HIGH_ACTIVITY_AT_DECISION** | **14.53%** | **8.48%** |
| spread ≤ 1 tick | 87.72% | 89.61% |
| trade recency P50 | 14,148s | 53,918s |

```
SPREAD_GENERALIZES_FROM_UFC            YES  (non-UFC is marginally BETTER)
ACTIVITY_GENERALIZES_FROM_UFC          NO
NON_UFC_BOARD_HAS_BETTER_ECONOMICS     NOT_CLAIMED -- on activity it is WORSE
```

**The non-UFC board is not a better hunting ground; on the cost side it is a
worse one.** Its median market last traded 53,918 seconds ago — **3.7x** the
UFC median — and it clears HIGH_ACTIVITY at 8.48% against UFC's 14.53%. The
earlier reading that "depth and frequency do not generalize" is confirmed and
sharpened: they do not generalize, and the direction is unfavourable.

## Composition of what survives

```
ROUTED    nfl 4,168   cfb 2,501   epl 390   mlb 264   ufc 172   lal 116
ACTIVE    nfl 2,544   cfb 1,259   mlb 227   epl 185   ufc 151   lal  85
HIGH      nfl   327   cfb   120   mlb  61   ufc  25   sea  23   ucl  23
```

Why routed markets fail ACTIVE, primary reason, in priority order:

```
FAIL_STALE_TRADE          3,330
FAIL_NO_TRADE_TIMESTAMP     168
FAIL_SPREAD                  87
FAIL_NO_TWO_SIDED_BOOK       25
```

Staleness is the binding constraint, by a factor of twenty over everything
else combined.

## Depth at the touch, from the ARRIVING book

```
ROUTED  best bid qty P10/P50/P90    5.1 /   282.0 / 52,565.0
        best ask qty P10/P50/P90    4.7 /   109.1 / 11,235.3
AUDIT   best bid qty P10/P50/P90    1.0 /   105.0 /    325.0
        best ask qty P10/P50/P90   10.0 /    76.9 /  4,276.6
```

`QUEUE_AHEAD` for a BETTOR rest is bounded below by the displayed size at the
touch; it is **not** a fill rate and must never be read as one.

## What this does and does not establish

```
ACTIVE_AT_DECISION                   MEASURED     5,558 of 9,168 routed
HIGH_ACTIVITY_AT_DECISION            MEASURED       788 of 9,168 routed
ROUTING_CENSORING                    MEASURED     ~1.9% false-negative
ONE_TICK_SPREAD_OPPORTUNITY          OBSERVED     89.58% of routed
BETTOR_REALIZED_SPREAD_CAPTURE       NOT_ESTABLISHED
ACTUAL_BETTOR_FILL_RATE              NOT_IDENTIFIED
ACTUAL_BETTOR_ADVERSE_SELECTION      NOT_IDENTIFIED
FAIR_VALUE_EDGE                      NOT_IDENTIFIED
MAKER_PROFITABILITY                  NOT_ESTABLISHED
MAKER_ENGINE_GATE_V2                 BLOCKED  (unchanged)
INDEPENDENT_CAPACITY                 still bounded by the family key, not 788
```

`STAGE2_BOOK_READ_BUDGET_WASTED = ZERO`.

## 48_HOUR_SPRINT_COMPLETE

```
48_HOUR_SPRINT_COMPLETE = YES
```

All eight deliverables exist, the census addendum that was the last open item
has landed, and the two frozen gates are unchanged. **YES does not mean the
programme cleared its gates.** It means the sprint's questions were answered,
including the ones whose answer is BLOCKED:

```
MAKER_ENGINE_GATE_V2 = BLOCKED      precisely named, cheapest next experiment
                                    specified, and it is not a public read
MICRO_LIVE_AUTHORIZED = NO
BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE = YES
HISTORICAL_WHALE_RESEARCH_FROZEN     = YES
```

The remaining blocker is unchanged and was never going to move here: **no
amount of public reading establishes a BETTOR fill.** That requires an order,
and no order was authorised, sent, or built a path for.

## PHASE 2

```
1. SHADOW ENGINE BUILT          DONE   shadow/position_state.py, 103 tests
2. BETTER REAL-TIME DATA        NEXT   feed the recorder live public books;
                                       the census says where: 788 HIGH_ACTIVITY
                                       markets, nfl/cfb/mlb/ufc-led
3. COUNTERFACTUAL EXECUTION     THEN   F0-F3 against the recorded books;
                                       ghost policies; no assumed fills
4. MICRO_LIVE_VALIDATION        GATED  requires authorisation not given
5. SCALE DECISION               GATED  requires 4
```

No further broad whale-analysis cycle. The historical cohort is the prior;
BETTOR's own shadow rows are the data.
