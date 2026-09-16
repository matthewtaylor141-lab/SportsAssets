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
