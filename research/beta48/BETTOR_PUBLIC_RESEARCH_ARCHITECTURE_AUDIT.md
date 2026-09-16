# BETTOR — PUBLIC RESEARCH ARCHITECTURE AUDIT

```
VENUE_CONTACT = 0      ORDERS = 0      CAPITAL = 0      CREDENTIALS = NONE
mirror_live   = false  SUBSTANTIVE_CAPTURE = NOT_DISPATCHED_BY_THIS_DOCUMENT
```

Machine-readable companion: `research_audit.json` (same entry IDs, same field set,
enum-validated by `research_audit.py`).

---

## 0. WHAT THIS AUDIT FOUND, BEFORE THE DETAIL

The starting assumption of an exercise like this is usually "the system is naive
and the literature will improve it." Reading the code first changed the finding.

**BETTOR is already unusually well shaped in four places where systems normally
fail**, and those are recorded here as ADOPT_ARCHITECTURALLY *with no change*, so
that a later simplification cannot delete them without contradicting a citation:

| Already right | Where | Why it matters |
|---|---|---|
| The venue's own price is refused as fair value | `position_state.fair_value_status` returns `FV_VENUE_IMPLIED`, never `FV_VALIDATED` | Using it makes every EV a tautology in which the market is always correct. Wolfers & Zitzewitz (2006) is the reason this is not merely fussy. |
| A touch is never a fill | `book_metrics.MoveThroughIsNotAFill`, `maker_fill` has no branch returning a positive fill from ticks, `tick_semantics.SHARES_TRADED_DELTA_STATUS = CONSERVATIVE_DIAGNOSTIC_ONLY` | This is the single most common way a maker backtest lies. BETTOR already refuses it in three independent places. |
| Incentives never rescue trading economics | `position_state.incentive_split`, `INCENTIVE_DEPENDENT` | Directly implements the separation Stream 9 asks for. |
| Uncertainty is three-valued and propagates | `ev_sum` returns `NOT_IDENTIFIED` if *any* term is, and names which | A missing term does not default to zero. Most EV code silently zeroes. |

**The genuinely missing pieces are three, and they are structural rather than
numeric:**

1. **There is one fair value where there should be two.** `FV_SETTLEMENT` and
   `FV_EXECUTION_SHORT_HORIZON` answer different questions and feed different EV
   terms. (§2)
2. **`EV_MAKER`'s risk terms sit outside the fill branch.** `ev_pair_now` sums
   inventory and execution terms *unconditionally*. That double counts, because
   none of them is incurred when the quote does not fill. (§6 — this is the
   one correctness defect the audit found.)
3. **`ev_cell` has bound slots and `robust_dominance` has logic, but nothing
   defensible fills them**, so `BOUND_METHODOLOGY_VALIDATED = False` keeps the
   whole robust path switched off. Calibration uncertainty (§1) is the missing
   input, and supplying it is what turns that path on and gives §8 its sizing
   rule. (§1, §8)

Everything else in this document is a challenger to be measured, a capability
blocked on data we do not have, or a named rejection.

**Nothing here is evidence that BETTOR will earn more.** Entry `S9-05` records
that as a first-class finding rather than a caveat.

---

## 1. THE CURRENT ARCHITECTURE, AS READ FROM CODE

Not from the design docs — from what is committed, so the delta in §11 is real.

```
FAIR VALUE      position_state.fair_value_status(obs)
                  -> NOT_IDENTIFIED | FV_VENUE_IMPLIED | FV_VALIDATED
                  ONE value. No uncertainty. No scoring definition of VALIDATED.
                edge-engine/fairvalue/devig.py: multiplicative + power de-vig
                  (a SEPARATE system — buy-and-hold, not the PMUS maker engine)

ELIGIBILITY     position_state.candidate_universe, event_identity.index_events
                  1,557 contest markets -> 87 contests
                  EVENT_COUNT_IS_NOT_CAPITAL_CAPACITY = True

OPPORTUNITY     position_state.states / feasibility / feasible_actions
                  STATE DETERMINES FEASIBILITY. ECONOMICS CHOOSES.
                  No action is hard-coded from a state.

EV              ev_pair_now, ev_wait, ev_simple -> ev_sum
                  ev_cell(point, lower, upper) -> EV_PRICED | EV_BOUNDED |
                                                  NOT_IDENTIFIED | EV_INFEASIBLE
                  robust_dominance(...) DISABLED: BOUND_METHODOLOGY_VALIDATED=False
                  allocate(...) DECLINES if any feasible action is unpriced

EXECUTION       ten A_* actions incl. the passive/aggressive split
                  A_PASSIVE_COMPLEMENT_PAIR vs A_AGGRESSIVE_COMPLEMENT_PAIR
                  A_PASSIVE_SELL_EXIT       vs A_AGGRESSIVE_SELL_EXIT

FILL            maker_fill: tick data yields NOT_FILLED or UNKNOWN, never FILLED
                book_metrics.FORBIDDEN_RENAMES bans "FILL_RATE" on book quantities
                  -> BETTOR has NO global passive fill rate today. Good.

INVENTORY       inventory.py: TIME_TO_OPPOSITE_FILL, NET_SPREAD_CAPTURE,
                  INVENTORY_MARKOUT, MAE/MFE, CAPITAL_OCCUPANCY (dollar-seconds),
                  FAILURE_TO_CLOSE, SETTLEMENT_OUTCOME
                  PASSIVE_CLOSE = a hope with a price. AGGRESSIVE_CLOSE = measured
                  to displayed depth, NOT_IDENTIFIED beyond it.

EXIT            position_state.TIME_UNPAIRED_BUCKETS (frozen grid, never
                  interpolated), whale lambdas, FRESH_SECONDS = 60

CAPITAL         live mirror: per-order clip, per-game cap, proportional rule
                  loss stop currently OFF by owner order

FEES            fees_v2: Fee = THETA * C * p * (1-p)
                  THETA_TAKER 0.06 -> 0.0695 at 2026-09-17T03:59Z
                  THETA_MAKER -0.0125 (a REBATE)
                  TIER_VERIFIED = False, regimes never pooled
```

One structural fact from `fees_v2` deserves to be lifted out, because it
recurs in §3 and §7 and is not in any paper:

> **The PMUS fee is `θ·C·p·(1-p)` — proportional to `p(1-p)`, which is exactly
> the terminal variance of a binary contract.** The venue charges in proportion
> to the very quantity that measures inventory risk. Fee and risk are maximal
> together at 50c and vanish together at the extremes.

---

## 2. THE ANTI-OVERFITTING STANDARD

Applied to every entry that proposes a parameter or a segmentation. An entry
that cannot answer 4, 6, 7 and 8 cannot be a `SHADOW_CHALLENGER` — it is
`DEFER_NEEDS_DATA`.

1. What is the hypothesis? 2. What public evidence motivates it? 3. Is that
evidence from the same market structure? 4. What BETTOR-native evidence would
confirm it? 5. What sample size is required? 6. What is the out-of-sample test?
7. What benchmark must it beat? 8. What would falsify it? 9. Can hierarchical
shrinkage replace a hard threshold? 10. Does it improve expected economics
enough to justify the complexity?

Question 9 is the one that does the most work here, and it is why `S1-06`
(hierarchical shrinkage) is rated the highest-value structural import in the
audit: **BETTOR's problem is many thin segments, not one thick one**, and
shrinkage answers that without a single hard `n >= k` threshold.

---

## 3. STREAM 1 — FAIR VALUE AND CALIBRATION

**Should BETTOR build `FV_PRIOR_EXTERNAL` / `FV_MODEL_RAW` /
`FV_MODEL_CALIBRATED` / `FV_CALIBRATION_UNCERTAINTY`? Yes — all four, and the
fourth is the one that unlocks the most.**

| ID | Idea | Status |
|---|---|---|
| S1-01 | Proper scoring rules as the selection objective (Gneiting & Raftery 2007; Brier 1950; Murphy 1973) | ADOPT_ARCHITECTURALLY |
| S1-02 | Calibration intercept/slope in preference to ECE | ADOPT_ARCHITECTURALLY |
| S1-03 | Platt default, isotonic above a sample floor (Niculescu-Mizil & Caruana 2005) | SHADOW_CHALLENGER |
| S1-04 | Shin as a third de-vig, selected per segment (Shin 1991/93; Štrumbelj 2014) | SHADOW_CHALLENGER |
| S1-05 | Favourite–longshot bias learned, never applied as a formula (Snowberg & Wolfers 2010) | SHADOW_CHALLENGER |
| S1-06 | Hierarchical shrinkage as the segmentation backbone (Efron & Morris 1975) | ADOPT_ARCHITECTURALLY |
| S1-07 | FV carries its own uncertainty | ADOPT_ARCHITECTURALLY |
| S1-08 | Cross-market consensus by log-odds pooling + learned extremisation | SHADOW_CHALLENGER |
| S1-09 | Venue price may not be the independent FV | ADOPT_ARCHITECTURALLY *(already true)* |

### Three judgements worth defending explicitly

**Brier primary, log score companion, never averaged.** Both are strictly proper,
so this is not a propriety argument — it is an operational one. The log score is
unbounded below. On binaries quoted at 0.01/0.99, one confident miss dominates
the sample mean, and model ranking becomes hostage to a single settled market.
Brier is bounded. Report both; the log score is what exposes tail
overconfidence, which Brier under-punishes. Report the **Murphy partition**
(reliability / resolution / uncertainty) because it separates *calibration* from
*discrimination* — precisely the separation Stream 9 demands.

**ECE is a diagnostic, never a gate.** The ECE estimator is binning-dependent
and biased (Naeini et al. 2015; Kumar et al. 2019). Calibration intercept and
slope come with standard errors and support an actual hypothesis test. Using a
biased estimator as a promotion gate is how a well-calibrated model gets
rejected and a badly-binned one promoted.

**Shin applies to the *external prior*, not to the PMUS book.** Shin models the
insider share inside a *bookmaker's* overround. A CLOB has no bookmaker and no
overround in that sense. So Shin belongs in `FV_PRIOR_EXTERNAL` where a
sportsbook feed is being de-vigged — and the existing `devig.py` is the right
home. Its own docstring already says the method should be graded per league in
shadow; this audit agrees and adds a third method and a selection rule, not a
winner.

**On favourite–longshot bias, the recommendation is to add nothing.** The bias
is a property of a market's participants. Snowberg & Wolfers (2010) find
misperceptions fit better than risk-love *in racetrack betting*; the sign is not
universal and reverse bias is documented elsewhere. The edge-engine already
measures edge **by price band** empirically, which is the correct shape: a
per-band calibration map absorbs whatever bias exists with its sign *measured*.
Adding a parametric FLB correction on top would fit the same effect twice.

### The segmentation, and how it is learned

```
GLOBAL -> SPORT -> LEAGUE -> MARKET_TYPE -> PRICE_BAND -> TIME_TO_EVENT
       -> PREGAME/LIVE -> LIQUIDITY_REGIME
```

Not as a cascade of hard sample-size gates, but as **hierarchical shrinkage**: a
thin segment shrinks towards its parent by a credibility weight. BETTOR already
has this machinery — `posterior_weight`, `posterior_weight_precision`,
`blended_lambda` — built for the whale pairing lambda. The proposal is to
*promote what exists* to a general backoff, not to write something new.

---

## 4. STREAM 2 — TWO FAIR VALUES

**Yes, separate them.** Not because the gap is known to be large on PMUS — it is
`NOT_IDENTIFIED` — but because the two quantities enter *different EV terms*, and
conflating them is a category error whatever the magnitude:

```
FV_SETTLEMENT                prices  inventory held to resolution
                                     the SETTLE branch of the exit decision
FV_EXECUTION_SHORT_HORIZON   prices  the markout
                                     the close cost
                                     the adverse-selection term
```

| ID | Idea | Status | Can test now |
|---|---|---|---|
| S2-01 | Separate the two fair values | ADOPT_ARCHITECTURALLY | Representation yes, magnitude no |
| S2-02 | Micro-price (Stoikov 2018) | SHADOW_CHALLENGER | **YES** |
| S2-03 | Queue imbalance → forward markout | SHADOW_CHALLENGER | **YES** |
| S2-04 | Order-flow imbalance (Cont, Kukanov & Stoikov 2014) | DEFER_NEEDS_DATA | No |

### The one thing that can be tested today with no new data

**S2-03.** The sealed tick captures already carry top-5 ladders with displayed
size, and `book_metrics` already computes markouts at `MARKOUT_HORIZONS_S =
(30, 60, 300)`. Nothing currently joins the two. Computing
`QI = (bid_size − ask_size)/(bid_size + ask_size)` and regressing the forward mid
change on it — per sport and price band, with shrinkage — needs **no venue
contact, no new capture, and no fills**. It is the highest-value immediately
executable item in this audit.

### Why micro-price is a challenger and not an import

Stoikov's estimator is strong in equities. Two PMUS-specific reasons to doubt
transfer, both of which the validation must actually answer:

- **Tick coarseness.** The micro-price is a sub-tick adjustment to the mid. PMUS
  ticks are 1c on a 1.00 range. Over much of the price range the adjustment may
  quantise away entirely.
- **The complement book is a second observation of the same event** — there is no
  equity analogue, and it may carry information the single-book estimator ignores.

### Why OFI is blocked on data, not on theory

OFI is built from **add / cancel / trade events at the touch**. BETTOR captures
**snapshots**. `tick_semantics` already records
`SHARES_TRADED_RESET_SEMANTICS = NOT_IDENTIFIED`, and `maker_fill` refuses to
derive anything positive from the aggregate delta — for the exact reason that a
quiet feed is indistinguishable from a quiet market. Approximating OFI from
snapshot differences would produce a *confident wrong answer*. Recorded as the
capability an event-level feed would unlock; left unbuilt.

---

## 5. STREAM 3 — MAKER QUOTING

| ID | Idea | Status |
|---|---|---|
| S3-01 | Inventory-adjusted reservation price, adapted to binary settlement | SHADOW_CHALLENGER |
| S3-02 | Transplanting AS numeric coefficients from equities | **REJECT** |
| S3-03 | Bounded inventory, expressed per EVENT | ADOPT_ARCHITECTURALLY |

Avellaneda–Stoikov (2008) gives, for a diffusive mid:

```
reservation price   r = s − q·γ·σ²·(T−t)
optimal spread      δᵃ+δᵇ = γσ²(T−t) + (2/γ)·ln(1 + γ/k)
```

**The economic principle transfers. The mathematics needs three changes, and
they are not cosmetic.**

1. **Terminal value is 0 or 1, not a terminal mid.** For inventory carried to
   settlement, per-contract terminal variance is **`p(1−p)`**, not `σ²(T−t)`.
   That is *maximal at 50c and vanishing at the extremes* — a price-dependent
   inventory risk with no analogue in the AS setting, where `σ²` is a property
   of the asset rather than of the current price. A naive AS port would charge
   the same inventory penalty at 3c as at 50c.
2. **The correct variance is a mixture over exit modes.** Inventory closed early
   bears *path* variance; inventory held to settlement bears `p(1−p)`. The
   reservation price needs the mixture, weighted by the exit-mode probabilities
   that Stream 7 produces.
3. **The terminal time is real and known.** A sports contract has a genuine
   settlement date, which suits the AS finite-horizon structure *better* than an
   arbitrary equity session close.

And the fee observation from §1 returns: since the fee is `θ·C·p·(1−p)`, **fee
and binary inventory risk are the same shape**, so they can and should be
carried in one price-dependent term rather than two that happen to covary.

`AS_ADAPTED_CHALLENGER` is therefore specified as:

```
r = FV_EXECUTION_SHORT_HORIZON − q·γ·V_binary·H
      V_binary = exit-mode mixture of p(1−p) and path variance
      H        = remaining exposure horizon
      γ        = a DECLARED risk preference, not a fitted parameter
half-spread from the fill-intensity model of §6 — never from an assumed k
```

It may not become authoritative on a paper argument. `S3-02` exists so that
building `S3-01` cannot quietly import `k` and `γ` from Avellaneda & Stoikov's
equity calibration.

**S3-03 is a real and cheap win.** `event_identity` already collapses 1,557
contest markets to 87 contests. A per-*market* inventory cap therefore
understates correlated exposure by roughly that ratio. Guéant et al.'s bounded
inventory formulation says to bound the thing you actually carry risk in — which
is the **event**, not the market.

---

## 6. STREAM 4 — FILL HAZARD

| ID | Idea | Status |
|---|---|---|
| S4-01 | `P_FILL(horizon \| state)` as a competing-risks survival model | ADOPT_ARCHITECTURALLY |
| S4-02 | Inferring fills from touch or aggregate volume | **REJECT** *(already refused)* |

**BETTOR must not use one global passive fill rate — and today it does not**, by
construction: `book_metrics.FORBIDDEN_RENAMES` bans calling a book quantity a
`FILL_RATE`, and `move_through_fill_probability` raises `MoveThroughIsNotAFill`.
The proposal is what replaces the absence.

### The methodological point that is usually got wrong

A resting order's life ends in one of several ways. Treating cancel or
move-away as **censoring** rather than as a **competing event** biases the
estimated fill probability *upward* — which is exactly the direction that
flatters a maker strategy.

```
TERMINAL EVENTS   FILLED
                  CANCELLED_BY_US
                  MOVED_AWAY_UNFILLED
                  EXPIRED
CENSORED          STILL_RESTING at end of observation
```

Estimate with a competing-risks model (Fine & Gray 1999 subdistribution hazard,
or cause-specific Cox), **not** a plain survival model with cancels censored.
Snapshot observation additionally makes the event times *interval-censored*, and
that must survive into the likelihood rather than being rounded to the poll.

State vector (preregistered, then shrunk — never mined): queue ahead, touch
depth, book imbalance, distance from touch, quote age, recent trades, recent
cancels/adds, spread, volatility, price movement, time to event, pregame/live,
sport, market type, current inventory, quote side.

### The data already exists and is being generated now

`DATA_REQUIRED` is our own resting-order lifecycles with placement, amendment,
cancel and fill timestamps plus the book state at each. **The live mirror
produces exactly this as a by-product of maker-first operation** — post-only
rests, post-only rejections, requotes, cancels. No new venue load is needed to
begin accumulating the training set; what is needed is that the lifecycle be
*recorded in the shape the estimator wants*.

---

## 7. STREAM 5 — ADVERSE SELECTION

| ID | Idea | Status |
|---|---|---|
| S5-01 | `EXPECTED_MARKOUT_AFTER_FILL(state, horizon)`, conditional on the fill | ADOPT_ARCHITECTURALLY |
| S5-02 | Markout horizons below the capture cadence | DEFER_NEEDS_DATA |
| S5-03 | VPIN as a toxicity measure | **REJECT** |

**The conditioning matters more than the horizon.** `E[markout | FILLED, state]`
is *not* `E[markout | state]`. We fill precisely when the other side wanted to
trade, so the unconditional markout — which is what `book_metrics` computes today
as a book diagnostic — is systematically more favourable than the one a maker
actually experiences. Using the unconditional figure in maker EV is the single
most effective way to make a losing maker strategy look profitable.

**Do not assume a passive fill is profitable.** Maker EV must be *net* of this
term, signed against our own side.

**On the 5s horizon:** run85's grid is 2.5 s. A 5 s markout on a 2.5 s grid has
essentially no precision, and slower captures cannot estimate it at all.
Report `NOT_IDENTIFIED` rather than interpolating — interpolating a markout at
sub-cadence resolution manufactures exactly the confident wrong answer
`tick_semantics` was written to prevent.

**On VPIN:** Easley, López de Prado & O'Hara (2012) is materially disputed —
Andersen & Bondarenko (2014) argue its predictive content is largely mechanical
in volatility and volume bucketing. It also requires trade classification BETTOR
cannot perform without an aggressor flag. Rejected as an EV input; admissible as
a diagnostic only, if an event feed ever arrives.

---

## 8. STREAM 6 — ENTRY ECONOMICS

**This is where the audit found an actual defect, not an improvement.**

| ID | Idea | Status |
|---|---|---|
| S6-01 | Corrected `EV_MAKER`: risk terms live **inside** the fill branch | ADOPT_ARCHITECTURALLY |
| S6-02 | `EV_TAKER` with size-bounded certainty of execution | ADOPT_ARCHITECTURALLY |
| S6-03 | `EV_NO_TRADE` as the declared numeraire | ADOPT_ARCHITECTURALLY |

### The proposed formulation, and what is wrong with it

```
EV_MAKER = P_FILL · CONDITIONAL_VALUE_IF_FILLED
         + P_NO_FILL · VALUE_IF_NOT_FILLED
         − INVENTORY_RISK
         − ADVERSE_SELECTION
         − OPPORTUNITY_COST
         + INCENTIVES
```

Three double-counts:

1. **`ADVERSE_SELECTION` is a property of the fill.** It belongs *inside*
   `CONDITIONAL_VALUE_IF_FILLED`. Subtracting it outside charges it again —
   including on the branch where we did not fill and could not have been
   adversely selected.
2. **`INVENTORY_RISK` is incurred only if we filled.** Same error.
3. **`OPPORTUNITY_COST` and `VALUE_IF_NOT_FILLED` are the same quantity.** If
   `VALUE_IF_NOT_FILLED` is set to the value of the alternative use of capital
   *and* opportunity cost is subtracted separately, the same economics appears
   twice with opposite signs on opposite branches.

### The correct formulation

```
EV_MAKER(quote) =
      P_FILL(h | s) · (   SPREAD_CAPTURE
                        + MAKER_REBATE
                        + VERIFIED_INCENTIVES
                        − E[ ADVERSE_MARKOUT        | FILL, s ]
                        − E[ INVENTORY_HOLDING_COST | FILL, s ]
                        − E[ CLOSE_COST             | FILL, s ] )
    + ( 1 − P_FILL(h | s) ) · 0
    − CAPITAL_RESERVATION_COST(rest duration)
```

- **Every risk term is conditional on the fill and lives inside the first
  branch.** That is the fix.
- **`VALUE_IF_NOT_FILLED = 0`** for the quote itself: no fill means no position,
  no inventory, no markout.
- The only genuine residual is the cost of capital or quote slot *reserved while
  resting*, charged **once**, outside both branches. If the venue does not
  reserve capital against a resting post-only order, this term is near zero and
  should be measured, not assumed.

```
EV_TAKER = (FV − ask)·size
         − TAKER_FEE(size, p, regime, tier)
         − E[INVENTORY_HOLDING_COST] − E[CLOSE_COST]
         + VERIFIED_INCENTIVES

   IN THE SNAPSHOT COUNTERFACTUAL, priced as CERTAIN only up to the
   displayed size at the crossed levels, and NOT_IDENTIFIED beyond it.
```

That size boundary is not new — `inventory.py` already applies exactly this rule
to the *aggressive close*. The proposal is to reuse it on *entry*, where it is
currently absent.

**Scope correction.** An earlier draft of this block wrote "`P_FILL = 1` up to
displayed size". That reads as a claim about a live order and it is not one.
Displayed depth establishes `SNAPSHOT_FULL_SIZE_EXECUTABLE = YES` — the captured
book could have absorbed that size at that instant — while
`LIVE_FULL_SIZE_FILL_CERTAINTY` stays `NOT_ESTABLISHED_UNTIL_ACTUAL_EXECUTION`,
because the book can move across `OBSERVATION → DECISION → ORDER_TRANSMISSION →
VENUE_ARRIVAL` and none of that depth is reserved for us. The arithmetic is
unchanged: no hypothetical `P_FILL` enters the same-snapshot sum. Only the label
is — it is a `SNAPSHOT_EXECUTION_COUNTERFACTUAL`, and the realised figure comes
from the actual execution. **Which FV enters depends on the intended exit**: `FV_SETTLEMENT`
for a hold-to-resolution intent, `FV_EXECUTION_SHORT_HORIZON` for an intent to
close early. This is the first place where §4's separation earns its keep.

```
EV_NO_TRADE = 0   BY CONSTRUCTION
```

Declaring the numeraire is not a formality. Without it, an opportunity-cost term
gets added to the trading side *and* a positive continuation value to the
no-trade side — the same quantity, twice, in opposite directions, biasing the
engine toward action or inaction depending purely on which side received it.

---

## 9. STREAM 7 — EXIT ECONOMICS

| ID | Idea | Status |
|---|---|---|
| S7-01 | Settlement is a guaranteed zero-spread exit with a known date | ADOPT_ARCHITECTURALLY |
| S7-02 | Passive→aggressive conversion as optimal stopping with a deadline | SHADOW_CHALLENGER |
| S7-03 | Almgren–Chriss optimal liquidation scheduling | **REJECT** |

### The structural fact that should reshape the exit logic

On a binary contract, **holding to settlement is a guaranteed exit at 0 or 1,
costing no spread and no taker fee, terminating with certainty at a known date.**
Its only costs are capital occupancy — which `inventory.py` already measures in
dollar-seconds — and the binary variance `p(1−p)`.

This **inverts the usual equity intuition that flat is free and inventory is
dangerous.** Here, `SETTLE` is a strong baseline, and an aggressive close must
clear a *high* bar: it pays the spread and the taker fee to avoid a risk that
will resolve on a known date anyway.

**Recommendation:** make `A_SETTLEMENT_HOLD` the explicit benchmark that every
other exit action must beat, rather than one option among ten.

### Thresholds stay unearned

`TIME_UNPAIRED_BUCKETS` is a frozen grid deliberately matched to the whale prior
and explicitly never interpolated. **Keep it.** The change is architectural only:
replace any implicit fixed age threshold with a per-tick comparison of
`EV_PASSIVE_CONTINUE` vs `EV_AGGRESSIVE_NOW` vs `EV_SETTLE`, so the conversion
time becomes an **output** rather than a parameter. No numeric threshold is
proposed anywhere in this document.

### Why Almgren–Chriss does not apply

It prices impact-vs-timing for an order *large relative to displayed liquidity*,
over a horizon with *no natural terminal payoff*. BETTOR's clips are small
relative to the book and the contract self-liquidates at settlement. The binding
constraint is adverse selection on a single clip, not scheduling across many.
Revisit only if clip size ever becomes large relative to depth — in which case
the relevant term is the ladder walk `inventory.py` already bounds.

---

## 10. STREAM 8 — CAPITAL ALLOCATION

| ID | Idea | Status |
|---|---|---|
| S8-01 | Full Kelly | **REJECT** |
| S8-02 | Risk-constrained Kelly (Busseti, Ryu & Boyd 2016) | ADOPT_ARCHITECTURALLY |
| S8-03 | `EDGE_LOWER_CONFIDENCE_BOUND` sizing | ADOPT_ARCHITECTURALLY |
| S8-04 | Correlation / event concentration as the allocation unit | ADOPT_ARCHITECTURALLY |

**Full Kelly is rejected on three specific grounds, not on taste:**

1. Kelly assumes `p` is **known**. BETTOR estimates it. The growth function is
   asymmetric — overbetting is far more damaging than underbetting, with growth
   reaching *zero* at twice the Kelly fraction — so an overestimated edge is
   punished much harder than an underestimated one.
2. The log-growth objective presumes many **independent** repetitions.
   Correlated same-event contracts violate this directly.
3. It carries **no drawdown constraint**, and BETTOR operates under one.

**Risk-constrained Kelly is the right shape.** Busseti, Ryu & Boyd keep the
log-growth objective and add a constraint bounding the probability of hitting a
drawdown level, yielding a convex problem. The fraction of full Kelly becomes an
**output of a declared risk preference** rather than an arbitrary one-half or
one-quarter. The drawdown bound is an owner decision, not a fitted parameter.
Hard caps remain as an outer envelope, because a constraint that depends on an
*estimated* `p` must never be the only thing standing between BETTOR and a large
position.

### Should sizing go to zero as uncertainty about edge increases? Yes — and here is the mechanism

```
EDGE_LCB = lower quantile of ( FV_posterior − price )
size on EDGE_LCB, not on the point edge
```

Two consequences follow automatically, and **neither needs a threshold**:

- A **wide posterior shrinks the position smoothly toward zero.**
- A posterior whose lower quantile **does not clear the price produces NO TRADE**
  with no separate rule.

The quantile level is a *declared risk preference*, preregistered, not fitted.

And this is the same missing input as §3: `ev_cell` already has
`EV_LOWER_BOUND`, `robust_dominance` already compares a floor against every
other ceiling, and the whole path is disabled by
`BOUND_METHODOLOGY_VALIDATED = False`. **`FV_CALIBRATION_UNCERTAINTY` is what
fills those slots defensibly** — one input turns on both the robust allocator and
uncertainty-adjusted sizing.

**S8-04:** make the **event** the allocation unit, using the existing
`event_identity` index. Contracts on the same contest are not independent bets;
sizing them as if they were overstates diversification by roughly the 1,557→87
ratio.

---

## 11. STREAM 9 — VALIDATION

| ID | Idea | Status |
|---|---|---|
| S9-01 | Walk-forward with purging and embargo | ADOPT_ARCHITECTURALLY |
| S9-02 | Five separated metrics, never one headline | ADOPT_ARCHITECTURALLY |
| S9-03 | Multiple-testing control (White 2000; Hansen 2005) | ADOPT_ARCHITECTURALLY |
| S9-04 | Selecting models on in-sample P&L | **REJECT** |
| S9-05 | Whether any of this improves realised economics | **NOT_IDENTIFIED** |

Sports contracts have **long-lived, overlapping labels** — a forecast made at
T resolves days later — so a random k-fold split leaks future information into
training. Walk-forward with label purging across the settlement horizon and an
embargo after each fold is the minimum defensible protocol. Random k-fold is
forbidden **by name**.

### Profitability and prediction accuracy are not the same metric

Leitch & Tanner (1991, *AER*) found conventional error measures correlate poorly
with realised profits. That cuts **both ways**, and both directions matter here:

- a profitable-looking model need not be accurate — so **BETTOR must be able to
  reject it on weak statistical evidence**, which is what the user asked for;
- an accurate model need not be profitable — so a good Brier score is **not**
  authority to trade.

Hence five metrics reported side by side, never collapsed:

```
PROBABILITY_MODEL_QUALITY        Brier + Murphy partition + calibration slope
TRADING_EDGE_BEFORE_EXECUTION    FV vs price at decision time
EXECUTION_QUALITY                realised vs decision price, fill rates, markouts
INCENTIVE_PNL                    already separated by incentive_split
TOTAL_REALIZED_PNL               last, never first
```

BETTOR is already unusually good here: `incentive_split` separates
`TRADING_NET_EX_INCENTIVES` and stamps `INCENTIVE_DEPENDENT`, and the
architecture doc already mandates separate directional / pair / residual / exit
ledgers. The proposal **completes** an existing instinct rather than importing a
new one.

**Multiple testing.** This audit alone proposes several challengers across many
segments. Without a control, one will look significant by construction. Any
challenger promoted out of a family must clear a White/Hansen-family test against
the incumbent, with **the full family declared before the comparison**.

---

## 12. THE DELTA: CURRENT BETTOR vs RESEARCH-HARDENED BETTOR

Not "the new one is better." Here is exactly which decisions change.

| # | Decision | CURRENT | RESEARCH-HARDENED | Why it changes |
|---|---|---|---|---|
| 1 | What fair value is | One `fair_value_status` | `FV_SETTLEMENT` **and** `FV_EXECUTION_SHORT_HORIZON`, each with status + uncertainty | They feed different EV terms; conflating them is a category error (§4) |
| 2 | Where maker risk terms sit | `ev_pair_now` sums them **unconditionally** | Inside the `P_FILL` branch, conditional on the fill | Correctness: the current form double counts (§8) |
| 3 | What "FV is VALIDATED" means | Undefined; a status with no test | A **scored** state: Brier + Murphy partition + calibration slope, walk-forward | Otherwise the gate is unfalsifiable (§3) |
| 4 | How thin segments are handled | Whale lambda only, via `blended_lambda` | **Every** segmented parameter shrinks to its parent through the same backoff | Removes hard `n ≥ k` thresholds entirely (§3, Q9) |
| 5 | Whether EV bounds exist | Slots exist; `BOUND_METHODOLOGY_VALIDATED = False` disables robust dominance | `FV_CALIBRATION_UNCERTAINTY` supplies defensible bounds; robust path can turn on | One input unlocks the allocator **and** sizing (§3, §10) |
| 6 | How size is chosen | Proportional mirror + clip + per-game cap | Size on **`EDGE_LCB`**, under a declared drawdown bound, with hard caps as outer envelope | Size → 0 as uncertainty grows, with no threshold (§10) |
| 7 | The allocation unit | Per market / per game | Per **EVENT**, via `event_identity` | 1,557 markets are 87 contests; per-market caps overstate diversification (§10) |
| 8 | Fill probability | No model (deliberately) | `P_FILL(h\|state)` as **competing risks**, cancels as competing events not censoring | Censoring cancels biases fill probability upward (§6) |
| 9 | Markout's role | Unconditional book **diagnostic** | `E[markout \| FILL, state]`, a **required term** of maker EV | Unconditional markout flatters a maker strategy (§7) |
| 10 | When to close | Frozen bucket grid + implicit age logic | Per-tick comparison vs `EV_SETTLE`, conversion time an **output** | Settlement is a free, certain, dated exit — a high bar (§9) |
| 11 | De-vig method | Multiplicative or power, chosen globally | Three methods incl. **Shin**, selected **per segment** out-of-sample | No de-vig is universally correct (§3) |
| 12 | Model selection | Not formalised | Walk-forward + purge/embargo + multiple-testing control; **never** in-sample P&L | Overlapping labels leak; families need control (§11) |
| 13 | Reporting | Separate P&L ledgers + incentive split | Adds the five-metric split with `PROBABILITY_MODEL_QUALITY` first, `TOTAL_REALIZED_PNL` last | A model may be rejected while profitable (§11) |

### What deliberately does NOT change

Venue-implied FV still refused. A touch is still never a fill. `NOT_IDENTIFIED`
still never evaluates to zero. Censoring stays censoring. Incentives still
reported after trading economics. Re-entry motives still prohibited and size
still independent of sunk P&L. The allocator still **declines** when any feasible
action is unpriced — the research-hardened version declines *more often at
first*, not less, because it prices more terms and each new term can be
`NOT_IDENTIFIED`.

### Cost of the change

Nine of the thirteen deltas are **representation and comparison logic**, testable
offline with `NOT_IDENTIFIED` terms and no venue contact. Two (#8, #9) are
blocked on our own execution data — which the live mirror generates as a
by-product. One (#5) is blocked on calibration data. One (#11) is testable today
on the edge-engine's historical feed.

**Immediately executable with zero new data: S2-03 (queue imbalance → markout),
S3-03/S8-04 (per-event exposure), and deltas #2, #3, #7 as code changes.**

---

## 13. SOURCES

Avellaneda & Stoikov (2008) *Quant. Finance* 8(3) · Almgren & Chriss (2000)
*J. Risk* · Andersen & Bondarenko (2014) *[VPIN critique]* · Baron et al. (2014)
*Decision Analysis* · Bates & Granger (1969) · Bertsimas & Lo (1998) · Brier
(1950) *Mon. Wea. Rev.* · Bühlmann *[credibility]* · Busseti, Ryu & Boyd (2016)
*Risk-Constrained Kelly Gambling* · Clemen (1989) *Int. J. Forecasting* · Cont &
de Larrard (2013) *SIAM J. Fin. Math.* · Cont, Kukanov & Stoikov (2014) *J. Fin.
Econometrics* · Cox (1958, 1972) · Easley, López de Prado & O'Hara (2012) *RFS*
· Efron & Morris (1975) *JASA* · Fine & Gray (1999) *JASA* · Gelman & Hill ·
Glosten & Milgrom (1985) *JFE* · Gneiting & Raftery (2007) *JASA* 102(477) ·
Granger & Pesaran (2000) · Guéant, Lehalle & Fernandez-Tapia (2013) *Math. Fin.
Econ.* · Hansen (2005) *JBES* · Huang, Lehalle & Rosenbaum (2015) *JASA* · Kelly
(1956) *Bell Syst. Tech. J.* · Kumar et al. (2019) *NeurIPS* · Leitch & Tanner
(1991) *AER* · López de Prado (2018) *[practitioner; cited for purge/embargo
only]* · MacLean, Thorp & Ziemba (2011) · Murphy (1973) *J. Appl. Meteor.* ·
Naeini et al. (2015) *AAAI* · Niculescu-Mizil & Caruana (2005) *ICML* · Platt
(1999) · Romano & Wolf · Satopää et al. (2014) *Int. J. Forecasting* · Shin
(1991, 1993) *Economic Journal* · Snowberg & Wolfers (2010) *JPE* · Stoikov
(2018) *Quant. Finance* · Štrumbelj (2014) *Int. J. Forecasting* · Van Calster
et al. (2019) *J. Clin. Epidemiol.* · White (2000) *Econometrica* · Wolfers &
Zitzewitz (2004) *JEP*, (2006) · Zadrozny & Elkan (2002) *KDD*

Citations are to the public record as known offline; **no claim in this document
rests on a figure taken from a paper.** Every number BETTOR uses must still be
earned from BETTOR's own out-of-sample evidence.
