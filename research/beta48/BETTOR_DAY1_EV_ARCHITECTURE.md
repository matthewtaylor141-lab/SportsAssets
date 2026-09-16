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

### The evidence scope, which governs every number below

```
PRIMARY_WHALE_PRIOR_ACCOUNTS       rn1, ferrarichampions2026, homerunhazard
SWISSTONY_STATUS                   GHOST_PRIOR_SENSITIVITY_ONLY
SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY NOT_ESTABLISHED
WHALE_SELECTION_CONDITION          OBSERVED_WHALE_ENTERED_POSITIONS_ONLY
POSITION_LEVEL_N                   available
INDEPENDENT_EFFECTIVE_N            NOT_IDENTIFIED
PRICE_TIME_JOINT_PRIOR             NOT_IDENTIFIED
BETTOR_P_FILL_SOURCE               BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED
WHALE_COMPLETION_AS_P_FILL         FORBIDDEN
PASSIVE_NO_FILL_BRANCH             EXPLICIT
AGGRESSIVE_CERTAIN_EXECUTION_GATE  PROVEN_EXECUTABLE_DEPTH_FOR_FULL_SIZE
PAIR_BASIS_ABOVE_PAR_IS            GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES
BLOBS_V3_RICH_CELL_STATUS          NOT_AVAILABLE_AGGREGATE_ONLY
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
   + P_NO_FILL * EV_IF_NO_FILL          <- DECLARED, never defaulted to zero
   + Σ(unconditional terms)
```

Every risk term that is only incurred *because we filled* sits inside the
branch. Terms that already contain their own `P_FILL` sit outside and are never
scaled again. `check_no_double_count()` raises before any arithmetic runs.

**The no-fill branch is mandatory and states what survives the miss.** An
earlier version defaulted `VALUE_IF_NO_FILL` to zero. That is right for a new
quote on a flat book and **wrong for a passive exit**, where the position is
still ours when the quote misses — and one function prices both. So the state is
declared:

```
NO_EXPOSURE_CARRIED       zero is justified: no fill, no position, no markout
QUEUE_POSITION_RETAINED   needs a horizon and a value
EXPOSURE_CONTINUES        needs a horizon and a value; unpriced => NOT_IDENTIFIED
```

`EV_IF_NO_FILL` is never silently zero while we remain exposed; an unpriced
continuation propagates as `NOT_IDENTIFIED` and names itself, so a missed exit
cannot be priced as free.

**`P_FILL` may not be seeded from whale completion.** `ev_maker_quote` checks a
declared `p_fill_source` and `whale_bridge.assert_p_fill_source` raises on every
whale completion field. Completion asks whether the market's other side
arrives; `P_FILL` asks whether our order executes.

**Actions that execute now have no fill branch — once executability is
proven.** `ev_pair_now` prices `A_AGGRESSIVE_COMPLEMENT_PAIR`
(`COMPLEMENT_EXECUTABLE_NOW`), and its unconditional terms are correct. An
earlier audit of mine called that a double count; it was wrong, and this
document records the correction because the wrong version was circulated.

But **`CERTAIN` now requires proven depth for the full size**, not a quoted
price: `certain_execution_gate()` returns `CERTAIN` only from a captured book
snapshot, timed and fresh, whose executable depth covers the required size. A
best bid or best ask, a quote with no size, a last trade, or an untimed
snapshot all return `NOT_ESTABLISHED` — and a one-lot book does not make a
500-lot pair certain, because the remainder is a fill branch wearing another
name.

---

## 3. WHAT THE WHALE PRIOR ACTUALLY SAYS ON DAY 1

**Three primary accounts, reconstructed independently, plus a ghost.** The
sentences below are about the **retained whale-entered positions** in each band
— not about the band, and not about an arbitrary entry at that price.

```
MERGE CHANNEL SIGN      <0.50 band   POSITIVE in all three primaries
                        >0.70 band   NEGATIVE in all three primaries
                        (swisstony agrees; it is a SENSITIVITY row and counts
                         towards no consensus)

GROSS MEAN_PAIR_BASIS   rises monotonically with band in all of them
                        crosses 1.00 (paid MORE, in gross prices, than the $1
                        it redeems) in ferrari and homerunhazard top bands,
                        and in swisstony's top band
                        RN1 never crosses -- max 0.99825

RESIDUAL_RATE           U-shaped: completion is HARDEST at the extremes
                        RN1 0.5479 at 0.00-0.10 vs 0.1395 at 0.30-0.50
                        (a WHALE COMPLETION rate, not BETTOR's P_FILL)
```

Reading (`HYPOTHESIS_CONSISTENT_WITH_THE_SIGNS`, not a causal claim): the band
is the price of the **first leg acquired**. Cheap leg first completes at a good
basis; expensive leg first completes at or above par.

**It is a trade, not free money.** The band with the best completion economics
(0.00–0.10) is also where the other side was least likely to arrive — RN1
completed only ~45% of its own entries there. That tension is what the EV
comparison exists to resolve.

**Consensus is on the SIGN, never the magnitude.** And no band statement may be
written in the unconditional form — "price below 0.50 is profitable" — which is
refused by name in `scan_for_unconditional_phrasing()`. **Whale evidence never
creates a trade by itself.**

**Basis above par is GROSS.** It establishes what was paid in trade prices, and
not a net outcome: rebates and reward programmes are `NOT_SEPARATELY_RETAINED`,
so `NET_OF_INCENTIVES_PAIR_OUTCOME = NOT_IDENTIFIED`.

---

## 4. THE REQUIRED MATRIX

| | RN1 | Ferrari | HomeRunHazard | SwissTony |
|---|---|---|---|---|
| **Prior role** | PRIMARY | PRIMARY | PRIMARY | **GHOST / SENSITIVITY**, `PRIMARY_PRIOR_ELIGIBILITY = NOT_ESTABLISHED` |
| **Observed evidence** | 4,692,866 fills / 259,271 positions, 2025-07→2026-09 | 1,958,843 / 101,611, 2026-03→2026-09 | 595,223 / 55,092, 2026-04→2026-09 | 6,059,643 / 367,896, 2025-08→2026-08 |
| **Selection condition** | `OBSERVED_WHALE_ENTERED_POSITIONS_ONLY` | ← same | ← same | ← same |
| **Mechanism** | two-sided passive MM, completion, capital recycling | two-sided passive MM, completion | directional pricing, settlement hold | live directional, in-play, hedge/close |
| **Known failure mode** | residual rate 0.55 at the cheapest band; merge negative above 0.70 | **0.10–0.30: merge +3.84M, settled −4.44M, total −0.56M**; gross basis above par in top bands | completion negative above 0.50; gross basis above par | merge negative from 0.50 up; residual 0.72 at top band; held out of the prior |
| **BETTOR improvement** | market selection, size control, explicit capital-occupancy cost, completion-hazard awareness | per-EVENT inventory cap, exit engine, EV_WAIT, separate residual ledger | directional EV ≠ pair/close EV, enforced | entry/hedge/close/merge/settle labelled as distinct actions |
| **Public-research support** | credibility shrinkage; competing-risks hazard | drawdown-constrained sizing; conditional markout | proper scoring rules; calibration | optimal stopping shape |
| **Day-one prior** | merge sign by band; completion hazard by interval (never as `P_FILL`) | same, plus the residual warning | directional negative control | **sensitivity run only**, never canonical |
| **Current-live inputs** | book, spread, depth, queue, fees, toxicity, inventory, event exposure | ← same | ← same | ← same |
| **Uncertainty** | CI95 on hazard present; **`POSITION_LEVEL_N` = 259,271, `INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED`**, interval width is a lower bound | as RN1 | smallest n of the four, same status | same status, plus the flagged discrepancy |
| **Validation status** | prior only, never validated on BETTOR data | prior only | prior only | prior only, held out |
| **Out-of-distribution rule** | shadow unless Route B | shadow unless Route B | shadow unless Route B | shadow unless Route B |
| **Native learning path** | `NATIVE_WEIGHT = n/(n+k)` per component, **HEURISTIC and capped at 0.95 either way** at position level; regime shift zeroes the prior | ← same | ← same | ← same |

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
band. Residual rate by band. Gross mean pair basis by band. **Sign, in coarse
cells, conditioned on whale-selected entries.** Nothing by sport, league,
market type or time to event — and **not the price × time cross product**,
which is `NOT_IDENTIFIED` and machine-guarded against construction.

**C. Which require BETTOR-native execution data?**
`P_FULL_FILL` / `P_PARTIAL_FILL`, `EXPECTED_MARKOUT_CONDITIONAL_ON_FILL`,
`QUEUE_PRIORITY_VALUE`, `REQUOTE_PRIORITY_LOSS`, `CAPITAL_RESERVATION_COST`,
toxicity states, and every close-cost estimate. **None exists today** — BETTOR
has never had an admitted fill, so `BETTOR_P_FILL_STATUS = NOT_IDENTIFIED`.

There is a tempting substitute and it is blocked: whale completion hazard is
*not* a fill probability. It measures whether the market's other side arrived
for someone else, on another venue, over positions they chose to open.
`WHALE_COMPLETION_AS_P_FILL = FORBIDDEN`.

**D. Which require independent fair-value validation?**
`FV_SETTLEMENT` and its uncertainty, and therefore every EV term that reads it:
settlement value, inventory cost, close cost, and the entire directional class.
`fair_value_status` currently returns `FV_VENUE_IMPLIED` — meaning we do not yet
have an independent fair value at all.

**E. Biggest remaining source of model risk?**
**That the whale priors are far coarser, and far more conditioned, than the
decisions they will inform.** A prior indexed by price band alone will be
applied to a state that also has a sport, a league, a time to kickoff and a
liquidity regime — and it is silent on all of them. It is also silent about
every market these accounts declined, because the sample is the ones they
entered. The risk is not that the prior is wrong; it is that it is *mute* and
*selected*, and gets treated as though it spoke about everything.

**F. Biggest improvement available before first live capital?**
**A fresh upstream extraction that retains per-row market and event identity.**

An earlier version of this answer said to "re-derive the cells from `blobs_v3`",
claiming that unlocks sport/league/market-type cells and event-level clustering.
**The probe read the files and refuted it** (`probe_blobs_v3.py`,
`BLOBS_V3_PROBE_V1.json`): `blobs_v3` is aggregate exactly as
`whale_exit_priors_v1` is. All fourteen probed fields — per-position rows,
market id, event id, entry/second-leg timestamps and prices, sport, league, game
start, liquidity, book state, order identity, fill identity — are **ABSENT at
0% coverage**. The one key that looks like a taxonomy, `BY_SPORT_OR_QUESTION`,
is the top 25 question titles truncated to 24 characters, covering 3.04%–20.06%
of opens and colliding many-to-one across fixtures.

`BLOBS_V3_RICH_CELL_STATUS = NOT_AVAILABLE_AGGREGATE_ONLY`.

The probe does add two genuine cells at 100% coverage that the retained priors
lack — `ACCOUNT × FILL_SIZE_BUCKET` and `ACCOUNT × ISO_WEEK` — and they are
still account-level marginals, not the grid (E) needs.

So the real work is a **re-extraction, not a reprocessing**: the ids existed at
extraction time (CENSUS counts 138,498 of them for RN1) and were not written
down. It still needs no PMUS venue access, it still directly attacks (E), and it
is honestly larger than "re-derive from files we already hold."

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
- That the priors generalise beyond price band and time-unpaired interval —
  **or across them**: `PRICE_TIME_JOINT_PRIOR = NOT_IDENTIFIED`.
- That any band statistic describes an arbitrary entry at that price. The
  sample is `OBSERVED_WHALE_ENTERED_POSITIONS_ONLY` and
  `COUNTERFACTUAL_ENTRY_OUTCOME = NOT_IDENTIFIED`.
- That a gross pair basis above 1.00 establishes a net loss —
  `NET_OF_INCENTIVES_PAIR_OUTCOME = NOT_IDENTIFIED`.
- That swisstony belongs in the prior: `SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY =
  NOT_ESTABLISHED`, and a zero reconciliation residual does not clear it.
- That we have an independent effective sample size:
  `INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED`; position counts are upper bounds
  and every interval width computed from them is a lower bound.
- That `blobs_v3` supplies the richer cells:
  `BLOBS_V3_RICH_CELL_STATUS = NOT_AVAILABLE_AGGREGATE_ONLY`.
- Any profitability, win rate or expected return figure whatsoever.
