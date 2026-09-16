# BETTOR DAY-1 EV ARCHITECTURE — canonical

The single human-readable description of how BETTOR calculates EV at launch.
Where this document and any other disagree, this one is wrong until reconciled.

```
OFFLINE.  VENUE_CONTACT = 0   ORDERS = 0   CAPITAL = 0   CREDENTIALS = NONE
mirror_live = false           run85 UNTOUCHED    rate experiment UNTOUCHED
WHALE_ANCHORED_MODE_ACTIVE = False

PRIMARY    observed whale behaviour and reconstructed whale economics
SECONDARY  established public research
TERTIARY   BETTOR's own prospective observations and fills
```

| Companion | Covers |
|---|---|
| `WHALE_NATIVE_EV_BRIDGE_V1.md` | what the whale data is and is not |
| `WHALE_REFERENCE_PRIORS_V1.json` | the priors, derived not authored |
| `WHALE_SUPPORT_MODEL_V1.md` | how much evidence stands behind a state |
| `DAY1_WHALE_ANCHORED_MODE.md` | the launch safeguard |
| `BETTOR_EV_ENGINE_V2_RESEARCH_HARDENING.md` | the research shapes |
| `BETTOR_EV_COMPONENT_SCHEMA_V2.json` | every EV term's semantics |
| `WHALE_EV_PROVENANCE_SCHEMA_V1.json` | the decision receipt |
| `BETTOR_MODEL_VALIDATION_STANDARD_V2.md` | how anything gets promoted |
| `MANAGEMENT_EV_EXPLAINER.md` | the same thing in plain English |

---

## 1. THE CHAIN

```
WHALE OBSERVATION            fills and economics, reconstructed
   -> WHALE MECHANISM        completion / directional / live / recycling
   -> WHALE EMPIRICAL PRIOR  on NAMED COMPONENTS, per coarse cell
   -> CURRENT MARKET STATE   book, spread, depth, queue, fees, toxicity
   -> BETTOR FV / EXECUTION / RISK INTELLIGENCE
   -> POSTERIOR ACTION EV    per action, with bounds
   -> ACTION OR NO TRADE     allocate() declines on any unpriced alternative
   -> REALIZED OUTCOME       joined to the ex-ante prediction per component
   -> BETTOR-NATIVE LEARNING prior weight falls as native evidence rises
```

**There is no single blended score.** No "EV = 50% whale + 50% BETTOR". The
prior attaches to `P_COMPLETION`, `COMPLETION_HAZARD`, `PAIR_CLOSE_COST`,
`RESIDUAL_OUTCOME`, `SIZE_CAPACITY`, `TIME_IN_INVENTORY`,
`MARKET_CLASS_PERFORMANCE` — each updated by BETTOR's observations of that same
component.

---

## 2. HOW ONE DECISION IS PRICED

```
1  IDENTIFY      market, EVENT (the allocation unit), opportunity class
2  SUPPORT       WHALE_SUPPORT_LEVEL x VENUE_MECHANISM_EQUIVALENCE
3  FAIR VALUE    FV_SETTLEMENT (+ uncertainty)   and
                 FV_EXECUTION_SHORT_HORIZON      -- two objects, never one
4  EXECUTION     P_FULL_FILL / P_PARTIAL_FILL / P_NO_FILL by state
                 EXPECTED_MARKOUT_CONDITIONAL_ON_FILL
                 TOXICITY_STATE, QUEUE_AHEAD_ESTIMATE
5  PRICE ACTIONS every feasible action, including the ones not chosen
6  SIZE          EDGE_LOWER_CONFIDENCE_BOUND under a drawdown constraint
7  DECIDE        act, no-trade, shadow, or DECLINE if any option is unpriced
8  RECEIPT       the full provenance record
9  ATTRIBUTE     ex-ante prediction per component -> realised outcome
```

### The maker action, correctly conditioned

```
EV = P_FULL    * Σ(conditional terms at full size)
   + P_PARTIAL * Σ(conditional terms at partial size)
   + P_NO_FILL * VALUE_IF_NO_FILL
   + Σ(unconditional terms)
```

Every risk term that is only incurred *because we filled* sits inside the
branch. Terms that already contain their own `P_FILL` sit outside and are never
scaled again. `check_no_double_count()` raises before any arithmetic runs.

**Actions that execute now have no fill branch at all.** `ev_pair_now` prices
`A_AGGRESSIVE_COMPLEMENT_PAIR` (`COMPLEMENT_EXECUTABLE_NOW`), and its
unconditional terms are correct. An earlier audit of mine called that a double
count; it was wrong, and this document records the correction because the wrong
version was circulated.

---

## 3. WHAT THE WHALE PRIOR ACTUALLY SAYS ON DAY 1

Four accounts, all reconstructed independently:

```
MERGE CHANNEL SIGN      <0.50 band   POSITIVE in all four (unanimous)
                        >0.70 band   NEGATIVE in all four (unanimous)

MEAN_PAIR_BASIS         rises monotonically with band in all four
                        crosses 1.00 (paid MORE than the $1 it redeems) in
                        ferrari, swisstony and homerunhazard top bands
                        RN1 never crosses -- max 0.99825

RESIDUAL_RATE           U-shaped: completion is HARDEST at the extremes
                        RN1 0.5479 at 0.00-0.10 vs 0.1395 at 0.30-0.50
```

Reading (`HYPOTHESIS_CONSISTENT_WITH_THE_SIGNS`, not a causal claim): the band
is the price of the **first leg acquired**. Cheap leg first completes at a good
basis; expensive leg first completes at or above par.

**And it is a trade, not free money.** The band with the best completion
economics (0.00–0.10) is also where the other side is least likely to arrive —
RN1 completes only ~45% of them. That tension is what the EV comparison exists
to resolve.

**Consensus is on the SIGN, never the magnitude.**

---

## 4. THE REQUIRED MATRIX

| | RN1 | Ferrari | HomeRunHazard | SwissTony |
|---|---|---|---|---|
| **Observed evidence** | 4,692,866 fills / 259,271 positions, 2025-07→2026-09 | 1,958,843 / 101,611, 2026-03→2026-09 | 595,223 / 55,092, 2026-04→2026-09 | 6,059,643 / 367,896, 2025-08→2026-08 |
| **Mechanism** | two-sided passive MM, completion, capital recycling | two-sided passive MM, completion | directional pricing, settlement hold | live directional, in-play, hedge/close |
| **Known failure mode** | residual rate 0.55 at the cheapest band; merge negative above 0.70 | **0.10–0.30: merge +3.84M, settled −4.44M, total −0.56M**; basis above par in top bands | completion negative above 0.50; basis above par | merge negative from 0.50 up; residual 0.72 at top band; excluded from consensus |
| **BETTOR improvement** | market selection, size control, explicit capital-occupancy cost, completion-hazard awareness | per-EVENT inventory cap, exit engine, EV_WAIT, separate residual ledger | directional EV ≠ pair/close EV, enforced | entry/hedge/close/merge/settle labelled as distinct actions |
| **Public-research support** | credibility shrinkage; competing-risks hazard | drawdown-constrained sizing; conditional markout | proper scoring rules; calibration | optimal stopping shape |
| **Day-one prior** | merge sign by band; completion hazard by interval | same, plus the residual warning | directional negative control | reference only (excluded from consensus) |
| **Current-live inputs** | book, spread, depth, queue, fees, toxicity, inventory, event exposure | ← same | ← same | ← same |
| **Uncertainty** | CI95 on hazard present; **effective n = POSITION, a floor** | as RN1 | smallest n of the four | flagged discrepancy |
| **Validation status** | prior only, never validated on BETTOR data | prior only | prior only | prior only, consensus-excluded |
| **Out-of-distribution rule** | shadow unless Route B | shadow unless Route B | shadow unless Route B | shadow unless Route B |
| **Native learning path** | `NATIVE_WEIGHT = n/(n+k)` per component; regime shift zeroes the prior | ← same | ← same | ← same |

---

## 5. WHAT IS PRESERVED AND MAY NOT BE WEAKENED

Provenance (DISPATCH_SHA == EXECUTED_SHA_ACTUAL, four aspects) · three-valued
uncertainty (`NOT_IDENTIFIED` never evaluates to zero) · censoring stays
censoring · a touch is never a fill · incentives reported after trading
economics · re-entry motives prohibited and size independent of sunk P&L ·
`allocate()` declines when any feasible action is unpriced · fee regimes never
pooled across the 2026-09-17T03:59Z cutover.

**The hardened engine declines MORE often at first, not less** — it prices more
terms, and each new term can be `NOT_IDENTIFIED`.

---

## 6. THE FINAL DECISION QUESTIONS

**A. Can BETTOR enter Day-One whale-anchored mode without claiming unproven
native alpha?**
**Yes** — because the mode never claims native alpha. Route A requires whale
support **and** positive BETTOR EV; Route B requires a structural edge that does
not depend on a directional model. Neither asserts our model is good. The claim
is only "we act where both agree," which is a statement about agreement.

**B. Which EV components can be populated from existing whale evidence today?**
Completion hazard by time-unpaired interval (with CI95). Channel sign by price
band. Residual rate by band. Mean pair basis by band. **Sign, in coarse cells.**
Nothing by sport, league, market type or time to event.

**C. Which require BETTOR-native execution data?**
`P_FULL_FILL` / `P_PARTIAL_FILL`, `EXPECTED_MARKOUT_CONDITIONAL_ON_FILL`,
`QUEUE_PRIORITY_VALUE`, `REQUOTE_PRIORITY_LOSS`, `CAPITAL_RESERVATION_COST`,
toxicity states, and every close-cost estimate. **None exists today** — BETTOR
has never had an admitted fill.

**D. Which require independent fair-value validation?**
`FV_SETTLEMENT` and its uncertainty, and therefore every EV term that reads it:
settlement value, inventory cost, close cost, and the entire directional class.
`fair_value_status` currently returns `FV_VENUE_IMPLIED` — meaning we do not yet
have an independent fair value at all.

**E. Biggest remaining source of model risk?**
**That the whale priors are far coarser than the decisions they will inform.**
A prior indexed by price band alone will be applied to a state that also has a
sport, a league, a time to kickoff and a liquidity regime — and it is silent on
all of them. The risk is not that the prior is wrong; it is that it is *mute*
and gets treated as though it spoke.

**F. Biggest improvement available before first live capital?**
**Re-derive the whale cells from `blobs_v3` with market and event identity
retained.** It needs no venue access, unlocks the sport/league/market-type cells,
enables event-level clustering and an event-blocked bootstrap, and directly
attacks (E). Everything else is second.

**G. What makes BETTOR more robust than simply copying?**
1. Completion and residual economics can never be summed — the exact error that
   cost Ferrari $560k in one band.
2. The allocation unit is the **event**, not the market.
3. Sizing on a **lower confidence bound**, so size falls to zero as uncertainty
   grows, with no threshold.
4. `NOT_IDENTIFIED` propagates; the engine declines rather than guessing.
5. Conditioning is machine-checked, so a cost cannot be charged twice.
6. Every decision carries a receipt answering *where did this EV come from*.
7. The prior decays as we learn, and a regime shift zeroes it.

**H. What cannot yet be claimed?**
- That BETTOR has native alpha.
- That BETTOR would have outperformed any whale historically —
  `COUNTERFACTUAL_PNL_IMPROVEMENT = NOT_IDENTIFIED`, because we do not have the
  prices available to them at the moments they did not act.
- That the whale mechanisms transfer to PMUS: `COMPLETION` equivalence is
  **PARTIAL**, and `HEDGE_INVENTORY_CLOSE` is `NOT_IDENTIFIED`.
- Any magnitude from whale evidence — sign only.
- That we know their policy: `WHALE_ORDER_POLICY = NOT_IDENTIFIED`.
- Any counterfactual policy evaluation on whale data — the action set and
  policy probabilities are absent.
- That the priors generalise beyond price band and time-unpaired interval.
- Any profitability, win rate or expected return figure whatsoever.
