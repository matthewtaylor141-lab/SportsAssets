# BETTOR_EXIT_ENGINE_V1

The residual-inventory engine. This is where BETTOR attempts to improve most on
the reference accounts, and the improvement claimed is **architectural**, not
performance.

```
EXIT_ENGINE_V1        = SPECIFIED; RECORDER BUILT; FROZEN FOR DATA COLLECTION
ORDER_CAPABLE         = NO
MICRO_LIVE_AUTHORIZED = NO
mirror_live           = false

BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE = YES   (section 11)
NEXT_MILESTONE                       = SHADOW_EXIT_LEARNING_V1

BETTOR_WILL_OUTPERFORM_THE_WHALES = NOT CLAIMED
```

---

## 1. WHY AN EXIT ENGINE AT ALL

Four independent implementations, and **24–32% of their first legs never
complete**. Ferrari's settled channel gave back **78.4%** of a $10.79M pair
engine. Two of the four never sold a single position; the two that did placed
sells that band-level settled economics do not explain.

None of that proves the whales were wrong — their capital constraints and
objectives are unknown. It establishes that **residual inventory is the base
case, not the exception**, and that no account in the cohort managed it with an
explicit economic comparison.

---

## 2. THE THREE EVIDENCE LEVELS

Every feature carries one, and the label binds what may be claimed of it.

```
LEVEL_A  DIRECT_WHALE_EVIDENCE      measured in retained historical data
LEVEL_B  WHALE_DERIVED_PRIOR        aggregate relationship, useful as a prior
LEVEL_C  BETTOR_PROSPECTIVE_ONLY    requires BETTOR's own shadow/live state
```

| feature | level |
|---|---|
| COMPLETION_HAZARD by time × basis | **A** |
| PRICE-BAND SETTLED ECONOMICS | **A** |
| BASIS-TIME FRONTIER | **A** |
| EVENTUAL COMPLETION RATE / SURVIVAL | **A** |
| INDIVIDUAL RESIDUAL LOSS PROBABILITY | **B** |
| SELL-BEHAVIOUR HYPOTHESES | **B** |
| EXECUTABLE PASSIVE EXIT | **C** |
| EXECUTABLE AGGRESSIVE EXIT | **C** |
| OPTIMAL EXIT TIME | **C** |
| CAPITAL OPPORTUNITY COST | **C** |

**A LEVEL_C feature has no historical value and must not be given one.**
`EV_EXIT_HISTORICAL = NOT_IDENTIFIED`, permanently, for this archive.

---

## 3. THE EIGHT MODULES

### PAIR_COMPLETION_ENGINE
Values completing the pair **now**, at the complement's current executable
price. Live book only. Outputs `EV_PAIR_NOW`, and the resulting locked basis.

### RESIDUAL_FAIR_VALUE_ENGINE
Independent fair value for the unpaired leg. `FV_BASIS = VENUE_IMPLIED` does
not count — it is the market's opinion restated. Today `NOT_IDENTIFIED`, so
every EV that depends on it is `NOT_IDENTIFIED`.

### COMPLETION_HAZARD_ENGINE  — LEVEL_A prior, live update
Prior from `whale_exit_priors_v1.json`, conditioned on price band, basis
ceiling and `TIME_UNPAIRED`. The engine reasons in
**`CONTINUOUS_HAZARD_LAMBDA`**, `-ln(S(t2)/S(t1)) / minutes`, because that is
the only one of the three time metrics that is additive across unequal
intervals. `INTERVAL_COMPLETION_H` is used where a wall-clock interval question
is being asked, and `H / minutes` is carried **descriptive only** and is never
called a rate.

```
PAIRING_INTENSITY_DECAYS_WITH_TIME = YES
  strictly non-increasing lambda in all four accounts, across all nine
  well-defined intervals; first/last ratio 58x-105x.
  The settlement tail has no defined duration and no lambda.
```

Outputs `P(COMPLETION_IN_NEXT_INTERVAL | band, basis, time_unpaired)` from
lambda and the interval length, so the answer adapts to whatever decision
cadence the engine is running at rather than being locked to the archive's
bucket edges.

### LOSS_HAZARD_ENGINE  — LEVEL_B
`P(LARGE_RESIDUAL_LOSS | state)`, from band-level settled economics as a prior,
refined per position only once BETTOR has its own data. **Never used alone to
exit.** Combined with EV, because a positive-EV position with unacceptable tail
concentration may still need reducing — and a negative-tail reading alone is
not a reason to sell into a bad book.

### LIQUIDITY_EXIT_ENGINE  — LEVEL_C
What can actually be sold, at what price, in what size, from the live book.
Passive and aggressive priced separately. **This module has no historical
counterpart and is the single largest native capability.**

### CAPITAL_OPPORTUNITY_ENGINE  — LEVEL_C
```
CAPITAL_RELEASED                 what the action frees
EXPECTED_NEXT_OPPORTUNITY_RETURN from BETTOR's own live opportunity set
EXPECTED_WAIT_FOR_NEXT_OPPORTUNITY
CAPITAL_OPPORTUNITY_COST         derived from the three
```
A mathematically superior exit **may accept a small realised loss today** if the
released capital has greater expected value elsewhere. The converse binds
equally: **do not exit a high-EV residual to make the book look clean.**

### EVENT_RISK_ENGINE
Exposure on the family key, correlated inventory, time to event start, time to
settlement. Bound on `INDEPENDENT_CAPACITY_LOWER_BOUND` because
`EVENT_KEY_VALIDATED = NO`.

### FINAL_ACTION_ALLOCATOR
Compares every feasible action **on one economic scale**.

---

## 4. THE DECISION

At every decision state, for every unpaired leg:

```
EV_PAIR_NOW
EV_WAIT_ONE_INTERVAL
EV_PASSIVE_EXIT
EV_AGGRESSIVE_EXIT
EV_HEDGE
EV_DIRECTIONAL_HOLD
EV_SETTLEMENT
```

each with explicit uncertainty, then

```
BEST_ACTION = argmax expected net economic value
subject to EVENT_EXPOSURE_LIMIT, CORRELATED_EXPOSURE_LIMIT, CAPITAL_LIMIT,
           DRAWDOWN_LIMIT, TIME_LIMIT, LIQUIDITY_LIMIT
```

**No action is ever selected because P&L crossed a percentage.** There is no
stop-loss in this engine. A percentage stop is a rule about a number on a
screen; this is a comparison of economic values.

### PAIR_WAIT_OPTION_VALUE

```
PAIR_WAIT_OPTION_VALUE = remaining completion probability (LEVEL_A prior)
                       x expected completion economics (LEVEL_A prior)
```

and then, explicitly:

```
EV_EXIT_HISTORICAL = NOT_IDENTIFIED        <- never subtracted
EV_EXIT_NOW        = from the live executable book, at runtime
```

The archive can price *waiting*. Only the live book can price *leaving*. That
asymmetry is the whole reason the native engine can be smarter than the
archive, and it is why no counterfactual exit P&L appears anywhere in this
specification.

### Completed pairs

```
compare EV_REALIZE_AND_RECYCLE_NOW  vs  EV_CONTINUE_HOLDING_TO_SETTLEMENT
including locked profit, transaction costs, merge/close mechanics,
capital released, time to settlement, operational constraints
DEFAULT: REALIZE / RECYCLE when the locked economics survive the round trip
         and capital has positive opportunity value.
```

Not blind waiting for settlement — and not forced realisation either.

---

## 5. THE PROSPECTIVE COLLECTION SPEC — MANDATORY

**This is how we build the position-level dataset the whale archive lacks.**
The archive is aggregate; no amount of analysis recovers per-position book
state from it. BETTOR records it from the first shadow position onward.

Every shadow position, at every decision tick, records:

```
TIMESTAMP                     the decision clock, per row, never a batch clock
ENTRY_STATE                   price, size, time, maker/taker, FV and EV at entry
CURRENT_BOOK                  full visible depth our side
COMPLEMENT_BOOK               full visible depth the complement side
PAIR_BASIS                    current, executable
FAIR_VALUE                    value + FV_BASIS + FV_CONFIDENCE
ALL_FEASIBLE_EXIT_PRICES      passive at each level, aggressive through depth
QUEUE_AHEAD                   displayed size ahead of a hypothetical rest
TRADE_ACTIVITY                LEVEL_B count/elapsed, never recency-as-rate
TIME_UNPAIRED                 since entry
EVENT_STATE                   time to start, time to settlement, family exposure
CAPITAL_OCCUPANCY             dollar-hours to date on this leg
ALL_ACTION_EVS                every EV above, with uncertainty
ACTION_CHOSEN
ACTIONS_NOT_CHOSEN            and their EVs -- the counterfactual, recorded live
LATER_OUTCOME                 what actually happened, stamped when known
```

**`ACTIONS_NOT_CHOSEN` is the field that makes the dataset worth having.**
Recording only the chosen action reproduces the whale archive's central defect:
you learn what was done and never what the alternative was worth. Recording the
rejected EVs *at the moment of rejection*, alongside the book that priced them,
creates the counterfactual prospectively instead of trying to manufacture it
afterwards.

Every row is labelled `COUNTERFACTUAL` while shadow-only. A shadow P&L is never
reported as realised.

### 5a. THE SPEC IS NOW CODE

```
MODULE          research/beta48/shadow/position_state.py
TESTS           research/beta48/shadow/test_position_state.py   65 passing
STATUS          BUILT, NOT YET FED A LIVE BOOK
```

Sections 5 through 5f of this document are implemented. The module holds the
frozen `TIME_UNPAIRED` grid (asserted equal to the priors artifact's own
interval list, so a bucket label here cannot drift from the lambda it
addresses), the state machine, the nine-action set, the EV functions with the
propagation rule enforced in one place, the allocator, the decision-row writer,
the five ghost policies, and the `WHALE_PRIOR -> BETTOR_POSTERIOR` shrinkage.

**Its boundary is structural, not configured.**

```
SHADOW_ONLY = YES   ORDER_PATH_EXISTS = NO   CREDENTIAL_PATH = NONE
mirror_live = false
```

The module imports no HTTP client and no `os`; it takes the book as an
argument from a caller that did the public read. Both facts are proved by
walking the AST, never by grepping the source — a substring scan matches the
prose that states the guarantee, which is exactly what a file like this
contains, and that error class has already appeared three times in this
programme.

**What it outputs on a live book today, stated in advance:**

```
ACTION_CHOSEN = A_NO_ACTION_RECORDED
ACTION_REASON = COMPARISON_NOT_IDENTIFIED
```

on essentially every tick, because `EV_WAIT` is `NOT_IDENTIFIED` by derivation
(§5c) and the allocator refuses to choose among the priced subset when a
feasible alternative is unpriced — the unpriced one could have dominated, and
picking the best identified EV would quietly assume it did not. Every rejected
EV is still written to `ACTIONS_NOT_CHOSEN` alongside the book that priced it.
**That is the deliverable.** The dataset accumulates from the first tick; the
decisions begin when a fair value is identified.

---

## 5b. THE DECISION STATE MACHINE

**State determines FEASIBLE actions. Economics chooses the action.** No action
is ever hard-coded from a state.

```
FRESH_UNPAIRED              TIME_UNPAIRED <= 60s
AGING_UNPAIRED              TIME_UNPAIRED > 60s
PAIR_AVAILABLE              a complement is executable now
PASSIVE_EXIT_AVAILABLE      a post-only rest is placeable
AGGRESSIVE_EXIT_AVAILABLE   depth exists to cross
HEDGE_AVAILABLE             a correlated instrument is executable
DIRECTIONAL_HOLD            no feasible pair or exit improves on holding
COMPLETED_PAIR              economics locked
SETTLED                     terminal
```

`TIME_UNPAIRED` is a **first-class state variable**, bucketed on the frozen
grid so the prior is directly addressable:

```
0-5s  5-10s  10-30s  30-60s  60-120s  120-300s  300-600s  600-1800s
1800-3600s  >3600s
```

**A 5-second leg and a 60-minute orphan never share a pairing prior.** The
lambda series makes the difference roughly two orders of magnitude, and the
engine is not permitted to average across it. Later BETTOR may learn a smooth
hazard; until then the buckets are the resolution the evidence supports.

## 5c. THE ACTION SET

```
A_PAIR_NOW           A_WAIT              A_PASSIVE_EXIT
A_AGGRESSIVE_EXIT    A_HEDGE             A_DIRECTIONAL_HOLD
A_SETTLEMENT_HOLD

and once economically paired:
A_REALIZE_AND_RECYCLE                    A_HOLD_LOCKED_PAIR
```

**No default action, except risk-kill conditions.** Every feasible action is
evaluated at every decision point.

### EV_PAIR_NOW

```
EV_PAIR_NOW = locked pair value
            - execution costs
            + maker rebate where applicable   (zero below the clip floor)
            + verified incentives where applicable
            - incremental inventory / execution risk
            + CAPITAL_RECYCLING_VALUE if the action releases capital
```

**Never merge merely because a pair exists.** Pair when its economic value
dominates the alternatives.

### EV_WAIT — where the case-study intelligence becomes machine logic

```
EV_WAIT = P_COMPLETE_NEXT_INTERVAL x EV_IF_COMPLETED
        + (1 - P_COMPLETE_NEXT_INTERVAL) x EXPECTED_CONTINUATION_VALUE
        - CAPITAL_OPPORTUNITY_COST
        - INVENTORY_RISK_COST
```

```
P_COMPLETE_NEXT_INTERVAL   from lambda (LEVEL_A prior -> BETTOR posterior)
EV_IF_COMPLETED            from the live basis economics
EXPECTED_CONTINUATION_VALUE  NOT_IDENTIFIED in V1 shadow
CAPITAL_OPPORTUNITY_COST     NOT_IDENTIFIED until BETTOR has an opportunity set
INVENTORY_RISK_COST          NOT_IDENTIFIED until fair value exists
```

**`EXPECTED_CONTINUATION_VALUE` is honestly `NOT_IDENTIFIED` in V1**, because it
requires fair value and a model of future state transitions, and neither is
measured. `EV_WAIT` is therefore `NOT_IDENTIFIED` in V1 shadow mode — and by
the propagation rule that makes the whole comparison `NOT_IDENTIFIED`, so the
engine records the state and declines to act. That is the correct V1 behaviour:
the machinery is exercised, the numbers are not fabricated.

## 5d. THE BELLMAN / OPTIMAL-STOPPING FRAME

The mature form is a control problem, not a threshold:

```
V(state_t) = max { EV_PAIR_NOW,
                   EV_PASSIVE_EXIT,
                   EV_AGGRESSIVE_EXIT,
                   EV_HEDGE,
                   EV_DIRECTIONAL_HOLD,
                   EV_SETTLEMENT,
                   EXPECTED_VALUE_OF_WAITING }
```

where `EXPECTED_VALUE_OF_WAITING` is itself the discounted expectation of
`V(state_{t+1})`. **This is the correct architecture for the exit problem.**

```
OPTIMAL_POLICY_LEARNED = NO
```

The frame is stated; the policy is not solved. Claiming otherwise would be
claiming knowledge of transition dynamics we have not measured.

## 5e. BASIS-TIME EFFICIENCY

From the frontier, for adjacent basis ceilings at each horizon:

```
BASIS_RELAXATION_EFFICIENCY = DELTA_COMPLETION_PROBABILITY / DELTA_BASIS_COST
```

**Descriptive.** Maximum completion probability is not maximum profit — a pair
completed at basis 1.00 completes at zero economics. The decision compares
`MARGINAL_COMPLETION_PROBABILITY` against `MARGINAL_BASIS_COST`, and a
tenfold completion gain for eight points of basis may or may not be worth
taking depending on what the completion is worth.

## 5f. PROSPECTIVE LEARNING LOOP

```
WHALE_PRIOR  ->  BETTOR_POSTERIOR
```

The whale priors are **initial priors only**. As shadow data accumulates BETTOR
tracks its own:

```
BETTOR_COMPLETION_HAZARD        BETTOR_BASIS_FRONTIER
BETTOR_PASSIVE_EXIT_FILL        BETTOR_AGGRESSIVE_EXIT_VALUE
BETTOR_DIRECTIONAL_HOLD_OUTCOME BETTOR_CAPITAL_OCCUPANCY
BETTOR_EXIT_ACTION_VALUE
```

and the whale prior's weight **decays as BETTOR's own evidence becomes
statistically credible**. The machine is not frozen to historical whale
behaviour — a prior from four accounts on a different venue is a starting
point, not a law.

## 5g. GHOST POLICY COMPARISON

Every shadow position runs **all** policies simultaneously, no capital:

```
POLICY_WHALE_BASELINE      POLICY_ALWAYS_HOLD      POLICY_PAIR_FIRST
POLICY_EXIT_ENGINE_V1      POLICY_NO_PAIR_DIRECTIONAL
```

Outcomes tracked separately, per policy, per position. This builds our own
prospective policy-comparison dataset — the thing the whale archive could never
provide.

**Hypothetical passive fills obey the counterfactual fill model and are never
assumed.** A ghost policy that rests an order does not get a fill because the
touch traded; it gets one only under F0–F3, with `clob_after_entry > 0`, and
`UNKNOWN_EXECUTION_TYPE != CLOB_EXECUTION` still binds. A ghost policy allowed
to assume its own fills would beat every other policy by construction and
measure nothing.

## 6. THE EXIT LEDGERS

```
PAIR_COMPLETION_PNL        RESIDUAL_HOLD_PNL        PASSIVE_EXIT_PNL
AGGRESSIVE_EXIT_PNL        HEDGE_PNL                SETTLEMENT_PNL
CAPITAL_RECYCLING_VALUE    EXIT_FEES                ADVERSE_SELECTION
                        TOTAL_EXIT_ENGINE_VALUE_ADDED
```

```
EXIT_ENGINE_VALUE_ADDED = BETTOR_POLICY_PNL
                        - REFERENCE_WHALE_POLICY_COUNTERFACTUAL
```

**Today this is `NOT_IDENTIFIED` and will stay so for some time.** The whale
counterfactual is only "genuinely supportable" for policies the archive can
price — hold-to-settlement, and pair-completion at a basis threshold. It is
**not** supportable for passive or aggressive exit, because no historical
executable exit price exists. Any `EXIT_ENGINE_VALUE_ADDED` quoting an exit
policy against the archive would be comparing a real number to an invented one.

Scope, fixed now so it cannot drift later:

```
SUPPORTABLE_WHALE_COUNTERFACTUALS   HOLD_TO_SETTLEMENT
                                    COMPLETE_PAIR_AT_BASIS_THRESHOLD
UNSUPPORTABLE                       PASSIVE_EXIT, AGGRESSIVE_EXIT,
                                    TIME_BASED_EXIT, FAIR_VALUE_EXIT,
                                    INVENTORY_RISK_EXIT, HYBRID/OPTIMAL-STOPPING
```

---

## 7. WHAT THE IMPROVEMENT ACTUALLY IS

We do not know the whales' internal reasoning and never claim to. What is
claimed is narrower and checkable: **at every decision BETTOR will explicitly
hold, and compare on one scale**, quantities the archive shows no evidence of
any reference account comparing:

```
time since entry            current completion hazard      pair basis
current executable exit     fair value                     cost of waiting
capital opportunity cost    event risk                     correlated inventory
tail risk
```

That is the architectural claim. It is an assertion about the **decision
procedure**, not about the P&L.

```
BETTOR_EXIT_ARCHITECTURE_IS_MORE_EXPLICIT_AND_MORE_COMPLETE_THAN_THE
BEHAVIORAL_POLICY_RECONSTRUCTIBLE_FROM_THE_FOUR_WHALE_ARCHIVE
    = what we can legitimately say today

BETTOR_IS_MORE_PROFITABLE_THAN_THE_WHALES
    = CANNOT YET BE SAID. Requires prospective validation.

SUCCESS_DEFINED_AS   BETTOR_EXIT_ENGINE_VALUE_ADDED > 0 on PROSPECTIVE
                     HELD-OUT data. Never historical fit.

PERFORMANCE_SUPERIORITY = MUST BE PROVEN PROSPECTIVELY
```

A more complete decision procedure is not a better one until measured. It could
be worse: more terms means more terms to estimate badly, and the engine's
own priors come from four accounts on a different venue. The prospective
collection spec in §5 exists precisely so that question is answerable.

---

## 8. WHAT V1 DOES NOT DO

```
- No stop-loss, at any percentage.
- No exit priced from historical data.
- No pair completion at any basis merely to flatten the book.
- No bid-side cash-out to make inventory disappear.
- No treating settlement as a default -- or as a failure.
- No NOT_IDENTIFIED term silently evaluating to zero.
- No order. No capital. No credential.
- No ghost policy that assumes its own fills.
- No claim that the optimal stopping policy has been learned.
```

---

## 9. THE PRIORS ARTIFACT

```
research/beta48/evidence/whale_audit/whale_exit_priors_v1.json
research/beta48/build_exit_priors.py    pure derivation, no network
research/beta48/test_exit_priors.py     39 tests
```

Account-specific priors (`RN1_PRIOR`, `FERRARI_PRIOR`, `SWISSTONY_PRIOR`,
`HRH_PRIOR`) and a `CROSS_WHALE_CONSENSUS_PRIOR` weighted by the pooled
interval risk set, with swisstony excluded from the consensus and member
lambdas retained beside it so disagreement survives aggregation.

---

## 10. THE THREE PRECISION CHECKS, BEFORE THE FREEZE

### 10a. THE CONSENSUS IS WEIGHTED BY THE RISK SET

The question was whether the denominator exists at the state the hazard is
conditioned on. It does, for exactly the cells the consensus uses:

```
CELL_ENTRY_N_AVAILABLE                  YES_PER_ACCOUNT_AND_PER_PRICE_BAND
INTERVAL_RISK_SET_N_AVAILABLE           YES_ANY_BASIS
COMPLETION_COUNT_BY_INTERVAL_AVAILABLE  YES_ANY_BASIS_AND_CEILING

FOUR_WAY_CELL_ACCOUNT x BAND x CEILING x INTERVAL   NOT_IDENTIFIED
```

The last line is the boundary: the completion grid is crossed with
`BASIS_CEILING` but **not** with `PRICE_BAND`, and the per-band table carries
only three fixed horizons and no ceilings. The ideal four-way weight does not
exist and is not implied.

The consensus is therefore built from **pooled risk-set evidence** —
`H_pooled = Σd / Σn`, `λ = −ln(1−H_pooled)/m` — and the superseded
acquisition-weighted series is retained beside it. RN1's weight at 1800s falls
from 62.33% to 57.72%; the tail lambda moves about 1.4%. The concern was
correct in direction and modest in magnitude, because the three members'
survival curves are similar. `CONSENSUS_STATISTICAL_OPTIMALITY =
NOT_ESTABLISHED`.

### 10b. THE EXCLUSION TRAVELS WITH THE ARTIFACT

```
SWISSTONY_INCLUDED_IN_ACCOUNT_SPECIFIC_PRIOR  YES
SWISSTONY_INCLUDED_IN_CONSENSUS               NO
EXCLUSION_REASON      FLAGGED_EXCLUDED from clean ground truth on an
                      unresolved two-source discrepancy, assigned as the
                      account's ROLE before any priors were built
WHICH_FIELDS_PREVENT_INCLUSION   NOT_IDENTIFIED_IN_THIS_WORKSPACE
WHETHER_EXCLUSION_WAS_FROZEN_BEFORE_RESULT   YES  (commit ee7329c, 2026-09-15)
EFFECT_IF_INCLUDED    NOT_COMPUTED
```

Two things are worth stating plainly. **It is not a reconciliation failure** —
swisstony reconciles at residual $0.00 stake and $0.00 P&L against
`lot_s`/`lot_p`, the same bar the included accounts clear; that reconciliation
does not clear the exclusion, because they are different defects. And **the
specific two-source comparison behind the flag is not retained in this
workspace**, so the field naming it says so rather than reconstructing a
plausible reason. The account remains available for DESCRIPTION — it is in the
sign-agreement tally and in the cohort time-decay finding, where a sign is not
an economic estimate.

### 10c. n=30 IS A SCALE CONSTANT, NOT A CLIFF

```
POSTERIOR_WEIGHT_FORMULA                    w = n / (n + 30)
HARD_N30_SWITCH                             NO
POSTERIOR_WEIGHTING_IS_CONTINUOUS           YES
V1_HEURISTIC                                YES
STATISTICALLY_OPTIMAL_POSTERIOR_WEIGHTING   NOT_ESTABLISHED
```

Nothing jumps at n=30; it is where the two sources carry equal weight. What
the rule genuinely is, is a heuristic that shrinks on **count alone** and is
blind to how noisy either estimate is — a whale cell with 250,000 at risk and
one with 40 are displaced at identical rates, which is wrong. The successor,
`posterior_weight_precision`, is inverse-variance weighted, is written, and is
**inactive**: changing the blending rule mid-collection would make the two
halves of the frozen shadow run incomparable. It gets tested prospectively
against the incumbent, not substituted for it on the strength of being
better-looking mathematics.

### 10d. INFEASIBLE IS NOT UNKNOWN

The no-action rule is preserved — `UNKNOWN != ZERO`, `UNKNOWN != WORSE`,
`UNKNOWN != UNAVAILABLE` — and sharpened. Feasibility is now three-valued:

```
FEASIBLE         the book shows it can be done
INFEASIBLE       it cannot physically occur     -> DROPPED from the comparison
NOT_IDENTIFIED   nobody observed whether it can -> BLOCKS the comparison
```

An action that cannot happen is not an alternative, so no hedge instrument, no
quoted complement and a settled market no longer freeze the allocator. An
action whose feasibility was never observed still does, because absence of
observation is not evidence — and the row reports which of the two blocked it
(`FEASIBILITY_NOT_IDENTIFIED` vs `COMPARISON_NOT_IDENTIFIED`).

This also fixed a real defect. `A_DIRECTIONAL_HOLD` used to enter the action
set only when nothing else existed, so the allocator could never compare
holding against pairing. Holding needs no counterparty; it is always feasible
on an open leg, and now it is always priced against the alternatives.

### 10e. EV INTERVALS AND ROBUST DOMINANCE — DESIGNED, INACTIVE

Every action can carry `EV_POINT_ESTIMATE`, `EV_LOWER_BOUND`,
`EV_UPPER_BOUND` and `EV_STATUS ∈ {PRICED, BOUNDED, NOT_IDENTIFIED,
INFEASIBLE}`, and the successor decision rule is:

```
LOWER_BOUND(A) > MAX[ UPPER_BOUND(every other action in play) ]  ->  act
```

which permits acting even when an alternative has no point estimate, because
nothing inside that alternative's own bound could have won.

```
ROBUST_DOMINANCE_ACTIVE      = NO
BOUND_METHODOLOGY_VALIDATED  = NO
```

It stays off, and forcing the flag does not turn it on — the bound-methodology
gate is separate and a test proves it. A dominance test run on bounds that are
too narrow is not conservative; it is the old error wearing an interval.

---

## 11. BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE

```
BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE = YES
```

| criterion | status | where |
|---|---|---|
| four whale behaviours represented | YES | four `ACCOUNT_PRIORS`, four case studies |
| completion hazard represented | YES | `HAZARD_BY_BASIS_CEILING`, 10 intervals |
| time decay represented | YES | λ monotone non-increasing, 58–105× |
| basis frontier represented | YES | 9 basis ceilings + `any_basis` |
| channel risk represented | YES | MERGE / SELL / SETTLED / TOTAL × 6 bands |
| uncertainty represented honestly | YES | risk sets, SEs, CI95, `COHORT_AGREEMENT`; `NOT_IDENTIFIED` where counts cannot support it |
| live executable alternatives specified | YES | nine actions, three-valued feasibility |
| missing evidence explicitly NOT_IDENTIFIED | YES | propagation rule in one function |
| prospective instrumentation complete | YES (BUILT, NOT YET FED A LIVE BOOK) | `shadow/position_state.py`, 85 tests |
| no action from an incomplete comparison | YES | allocator declines; both blockers named |

**What YES means and does not mean.** The PRIOR is complete: everything the
four-account archive can yield about pair completion, timing, basis and
channel risk has been extracted, bounded, and made machine-readable, and the
machinery that will consume it is built and exercised. The LIKELIHOOD is
empty. No BETTOR shadow position has been recorded, so
`BETTOR_COMPLETION_HAZARD`, `BETTOR_PASSIVE_EXIT_FILL` and every other
`BETTOR_*` series is `NOT_IDENTIFIED`, and `EXIT_ENGINE_VALUE_ADDED` remains
`NOT_IDENTIFIED` and will for some time.

**The exit engine is now FROZEN for data collection.** Further redesign before
prospective observations exist would be tuning a model against a dataset of
size zero. The next milestone is `SHADOW_EXIT_LEARNING_V1`, and its input is
BETTOR's own decision rows — not another historical research cycle.
