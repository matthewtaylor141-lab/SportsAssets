# BETTOR EV ENGINE V2 — RESEARCH HARDENING

Public research is **SECONDARY**. It never overrides whale evidence and never
supplies a number. It supplies *shapes* — the structure a component should have
so that whale priors and BETTOR observations can be poured into it correctly.

```
OFFLINE. VENUE_CONTACT = 0  ORDERS = 0  CAPITAL = 0  mirror_live = false
```

Code: `shadow/ev_semantics.py`. Schema: `BETTOR_EV_COMPONENT_SCHEMA_V2.json`.
Full source list and per-idea status: `BETTOR_PUBLIC_RESEARCH_ARCHITECTURE_AUDIT.md`.

---

## 1. PART I FIRST: A CORRECTION TO MY OWN EARLIER CLAIM

The previous audit asserted that `position_state.ev_pair_now` double counts by
summing risk terms unconditionally. **That was wrong, and the instruction to
inspect conditioning before modifying anything is what caught it.**

```
PROBES[A_AGGRESSIVE_COMPLEMENT_PAIR] = "COMPLEMENT_EXECUTABLE_NOW"
ev_pair_now.__doc__: "EV of completing the pair at the currently executable
                      complement"
```

`ev_pair_now` prices an action that **executes now**. An immediately executed
action has no fill uncertainty, so unconditional terms are exactly right and
there is nothing to fix. The terms are registered `CERTAIN`, and a test refuses
to let a `CERTAIN` term be placed in a fill branch.

Two later corrections narrow what "executes now" is allowed to mean, without
changing the arithmetic:

- `CERTAIN` requires **proven displayed depth for the full size** from a timed,
  fresh captured snapshot — `snapshot_executability_gate()`. A best bid or ask
  proves a price and no size.
- Passing that gate establishes `SNAPSHOT_FULL_SIZE_EXECUTABLE = YES` and
  nothing about a later order. `LIVE_FULL_SIZE_FILL_CERTAINTY` stays
  `NOT_ESTABLISHED` on every branch, the calculation is labelled
  `SNAPSHOT_EXECUTION_COUNTERFACTUAL`, and `live_fill_certainty()` refuses to
  convert one into the other. **No `P_FILL` re-enters the snapshot sum.**

### The actual gap

`A_PASSIVE_COMPLEMENT_PAIR` and `A_PASSIVE_SELL_EXIT` are feasible actions —
`PROBES` gives them `COMPLEMENT_PASSIVE_PLACEABLE` and `PASSIVE_EXIT_PLACEABLE`
— **with no fill-conditional EV function anywhere in the module.** Priced
through `ev_simple` (a flat named sum) a passive action either:

- silently omits the no-fill branch, overstating EV; or
- invites exactly the double count I wrongly attributed to `ev_pair_now`, the
  moment someone multiplies that flat sum by `P_FILL`.

**The fix is to ADD the missing structure, not to modify a function that was
already correct.** `ev_semantics.ev_maker_quote` is that structure.

---

## 2. CONDITIONING IS NOW A DECLARED, CHECKED PROPERTY

Every term carries UNIT, SIGN, CONDITIONING, TIME_HORIZON, SOURCE, UNCERTAINTY.

```
CONDITIONAL_ON_FILL   inside the branch; never multiplied by P_FILL twice
UNCONDITIONAL         already contains its own P_FILL; never multiplied at all
CERTAIN               the action executes now; there is no P_FILL anywhere
```

The name states the conditioning — `ADVERSE_SELECTION_CONDITIONAL_ON_FILL` vs
`EXPECTED_ADVERSE_SELECTION_UNCONDITIONAL` — and a test enforces the suffix, so
a reader never has to look it up.

`check_no_double_count()` raises on three shapes:

1. the same `ECONOMIC_QUANTITY` inside **and** outside the branch;
2. an `UNCONDITIONAL` term placed inside (it would be scaled by P_FILL twice);
3. a `CONDITIONAL_ON_FILL` term placed outside (it would be charged even when
   nothing filled).

It runs **before any arithmetic**, so a mis-specified EV crashes rather than
returning a plausible number that is wrong by a factor of P_FILL.

### The maker quote

```
EV = P_FULL    * Σ(conditional terms at full size)
   + P_PARTIAL * Σ(conditional terms at partial size)
   + P_NO_FILL * VALUE_IF_NO_FILL
   + Σ(unconditional terms)
```

- `VALUE_IF_NO_FILL` defaults to 0 — no fill, no position, no inventory, no
  markout — but is a **parameter, not a constant**, because an expired quote can
  leave a retained queue position behind, and that is an observation.
- FULL and PARTIAL are **mutually exclusive branches**, so the same quantity
  legitimately appears in both at different sizes. Each is checked against the
  unconditional bag *separately*. (The first version merged them and flagged the
  legitimate repeat — the test caught it; the comment now blocks the
  "simplification" back.)
- A `NOT_IDENTIFIED` anywhere propagates and names itself, as `ev_sum` does.

---

## 3. THE THREE SEMANTIC GUARDS

**`p(1-p)` is TERMINAL_BERNOULLI_PAYOUT_VARIANCE — not inventory risk.** It is
one component. Inventory risk also depends on position size, time to settlement,
current price, short-horizon dynamics, information regime, liquidity,
correlation, exit alternatives and capital occupancy. A model that charges
inventory risk as `q·γ·p(1-p)` alone has priced a stationary binary and ignored
everything that makes a live position dangerous.

**Settlement is not a free exit.** The correct phrasing is carried in code:
*settlement provides the contract's terminal payoff subject to resolution,
timing, collateral and venue risk.* The earlier "guaranteed zero-spread exit"
framing was too strong — it treats resolution as certain and instantaneous, and
it is neither. `SETTLEMENT_IS_NOT = A_GUARANTEED_ZERO_SPREAD_EXIT` is pinned.

**Queue imbalance is a MICROSTRUCTURE_DIAGNOSTIC** until BETTOR has admitted
fills. `QUEUE_IMBALANCE_IS_NOT_YET = BETTOR_ADVERSE_SELECTION_CONDITIONAL_ON_FILL`.
A book-state predictor of the future mid is not a model of what happens to *our*
fills, because we have no fills to condition on. It may inform
`FV_EXECUTION_SHORT_HORIZON`; it may not be relabelled as adverse selection.

**`EV_NO_TRADE` is not forced to zero.** Waiting may carry option value — future
information, quote improvement, toxicity decay, capital preservation, better
opportunities, future completion states. `EV_WAIT_NUMERIC_VALUE =
NOT_IDENTIFIED`: the components are named, the number is earned.

---

## 4. DUAL FAIR VALUE

```
FV_SETTLEMENT               prices inventory held to resolution and the
                            SETTLE branch of the exit decision
FV_EXECUTION_SHORT_HORIZON  prices markout, close cost, adverse selection,
                            quote placement and timing
```

`FV_SETTLEMENT` components: `FV_EXTERNAL_PRIOR`, `FV_INTERNAL_MODEL_RAW`,
`FV_SETTLEMENT_CALIBRATED`, `FV_SETTLEMENT_UNCERTAINTY`, `FV_SOURCE_DISPERSION`,
and `FV_TARGET_MARKET_REFERENCE` — **recorded for comparison only**.
`position_state.fair_value_status` already refuses `FV_VENUE_IMPLIED` as
validated, with the tautology argument in its docstring; that stays.

`FV_EXECUTION_SHORT_HORIZON` challengers: queue imbalance, depth imbalance,
micro-price, order-flow imbalance, adds/cancels, trade direction, spread,
short-horizon movement. **Do not transplant equity coefficients — shadow-test
them.** OFI in particular is blocked on data, not theory: it needs an
event-level add/cancel/trade stream, and BETTOR captures snapshots.

---

## 5. THE REMAINING COMPONENT SHAPES

| Component | Shape | Status |
|---|---|---|
| Calibration | Brier + Murphy partition primary; log loss companion; calibration intercept/slope with CIs; ECE **diagnostic only** | ADOPT_ARCHITECTURALLY |
| Calibrator choice | identity / Platt / beta / isotonic, selected **out of sample**, isotonic only above a preregistered n | SHADOW_CHALLENGER |
| Segmentation | hierarchical shrinkage, not hard thresholds | ADOPT_ARCHITECTURALLY |
| Fill model | `P_FULL_FILL` / `P_PARTIAL_FILL` / `P_NO_FILL` by state; **competing risks**, cancels as competing events not censoring | ADOPT_ARCHITECTURALLY |
| Adverse selection | `EXPECTED_MARKOUT_CONDITIONAL_ON_FILL(state, horizon)` | ADOPT_ARCHITECTURALLY |
| Toxicity | `DATA_FRESHNESS`, `EVENT_FEED_LATENCY`, `RECENT_PRICE_JUMP`, `ORDER_FLOW_BURST`, `DEPTH_WITHDRAWAL`, `CANCEL_ACCELERATION`, `TOXICITY_STATE` — **no invented latency numbers** | DEFER_NEEDS_DATA |
| Queue value | `QUEUE_AHEAD_ESTIMATE`, `QUEUE_PRIORITY_VALUE`, `REQUOTE_PRIORITY_LOSS`; compare KEEP / REQUOTE / AGGRESSIVE / WAIT economically instead of a fixed timer | ADOPT_ARCHITECTURALLY |
| Inventory-aware quoting | AS-inspired `INVENTORY_ADJUSTED_RESERVATION_VALUE`, **shadow only**, coefficients never transplanted | SHADOW_CHALLENGER |
| Sizing | `EDGE_MEAN`, `EDGE_UNCERTAINTY`, `EDGE_LOWER_CONFIDENCE_BOUND`, risk-constrained | ADOPT_ARCHITECTURALLY |
| Full plug-in Kelly | — | **REJECT** |
| Deep RL / large neural systems | — | **DEFER** until interpretable systems prove insufficient |

### Uncertainty is used once

Uncertainty enters **robustness gates, confidence bounds and capital sizing**.
It is **not** subtracted from EV and then penalised again in size — that is the
same caution charged twice, and it is the sizing analogue of the double count
§2 prevents.

### Robust dominance stays off

`ev_cell` already carries `EV_LOWER_BOUND` / `EV_UPPER_BOUND`;
`robust_dominance` already compares a floor against every other ceiling; the
path is disabled by `BOUND_METHODOLOGY_VALIDATED = False`. It stays disabled.
**Numbers appearing in the bound fields is not a reason to switch it on** —
coverage and interpretation must be validated first: an 80% interval must
contain the realised value about 80% of the time, out of sample, before a
decision rule is allowed to lean on it.

---

## 6. OFF-POLICY EVALUATION: WHY IT IS NOT_IDENTIFIED TODAY

Doubly Robust Policy Evaluation, Counterfactual Risk Minimization and
conservative offline RL all require, at minimum: the candidate action set, the
historical policy's action probabilities, the unchosen actions, and the complete
contemporaneous context.

**The whale archive has none of those.** It has fills. So true counterfactual
policy evaluation on whale data is `NOT_IDENTIFIED`, and applying these methods
to it mechanically would produce a confident number about an unidentified
quantity.

What *can* be done now is to log prospectively so BETTOR's own future data
supports it: state snapshot, available actions, chosen action, rejected actions,
predicted EVs per component, action probability where meaningful, realised
outcome. That is the `OFF_POLICY_LOGGING` section of
`WHALE_EV_PROVENANCE_SCHEMA_V1.json`, and building it now is cheap; retrofitting
it later is impossible.
