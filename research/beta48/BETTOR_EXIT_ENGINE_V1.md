# BETTOR_EXIT_ENGINE_V1

The residual-inventory engine. This is where BETTOR attempts to improve most on
the reference accounts, and the improvement claimed is **architectural**, not
performance.

```
EXIT_ENGINE_V1        = SPECIFIED; RECORDER BUILT; FROZEN FOR DATA COLLECTION
ORDER_CAPABLE         = NO
MICRO_LIVE_AUTHORIZED = NO
mirror_live           = false

BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE = YES   (sections 11-13)
HISTORICAL_WHALE_RESEARCH_FROZEN     = YES
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

---

## 12. THE TWO ROBUSTNESS CHECKS

### 12a. THE SWISSTONY EXCLUSION IS LOAD-BEARING

Since the exclusion reason is `NOT_IDENTIFIED_IN_THIS_WORKSPACE`, it cannot be
shown to be *necessary*, so its cost was measured rather than argued. Both
consensus versions were built with the **identical** pooled interval-risk-set
method, so any difference between them is a difference of membership and not
of method.

| interval | LAMBDA_3 | LAMBDA_4 | ABS_DIFF | REL_DIFF |
|---|---|---|---|---|
| 0s–5s | 0.508601 | 0.476766 | −0.031835 | −6.26% |
| 5s–10s | 0.244821 | 0.220043 | −0.024778 | −10.12% |
| 10s–30s | 0.134944 | 0.122428 | −0.012515 | −9.27% |
| 30s–60s | 0.095357 | 0.081806 | −0.013551 | −14.21% |
| 60s–120s | 0.068186 | 0.055584 | −0.012602 | −18.48% |
| 120s–300s | 0.046631 | 0.037160 | −0.009471 | −20.31% |
| 300s–600s | 0.029743 | 0.023490 | −0.006253 | −21.02% |
| 600s–1800s | 0.015389 | 0.011860 | −0.003529 | −22.93% |
| 1800s–3600s | 0.006763 | 0.005342 | −0.001421 | −21.01% |

```
PAIRING_INTENSITY_DECAYS_WITH_TIME_3 = YES   first/last 75.2x
PAIRING_INTENSITY_DECAYS_WITH_TIME_4 = YES   first/last 89.2x

EVENTUAL_COMPLETION_3 = 0.74426    EVENTUAL_COMPLETION_4 = 0.75049
TIME_TO_25_PERCENT_EVENTUAL   3 = 104.8s     4 = 141.1s
TIME_TO_50_PERCENT_EVENTUAL   3 = 510.3s     4 = 856.1s
TIME_TO_75_PERCENT_EVENTUAL   3 = 1789.5s    4 = BEYOND_3600S_GRID
```

```
PRIOR_SENSITIVITY_TO_SWISSTONY = MATERIAL
```

**The verdict is split, and reporting one word would hide half of it.** The
qualitative conclusion survives — decay is `YES` in both, and the eventual
completion rate barely moves (0.744 vs 0.750). The **timing quantities that
`EV_WAIT` would actually consume do not**: lambda falls 6% to 23% with the gap
widening in time, the 50% milestone moves 510s → 856s, and the 75% milestone
leaves the measurable grid entirely. Since the split touches exactly the
numbers the engine consumes, the conservative single verdict is `MATERIAL`.

**Why it moves:** swisstony is the *largest* account in the cohort by
first-side acquisitions (367,896 vs RN1's 259,271) and the *slowest* to pair,
so risk-set pooling gives it the heaviest weight precisely where it disagrees
most. The correction the previous check introduced is what makes this visible.

**Decision.** The frozen 3-account consensus **remains** the shadow protocol's
prior. Changing it would break preregistration, and the 4-account version is
not adopted — not because it reads worse, but because it is not shown to be
*more correct*, only different. `PURPOSE =
SENSITIVITY_EVIDENCE_ONLY_NOT_A_PROTOCOL_CHANGE`.

**Open item, recorded not acted on:** the exclusion is now known to be
load-bearing for the timing quantities, so resolving the upstream two-source
discrepancy is worth more than it appeared.

### 12b. THE SUBDISTRIBUTION IS NOT A HAZARD, AND THE CODE REFUSES IT

```
BASIS_CEILING_LAMBDA_IS               SUBDISTRIBUTION_QUANTITY
TRUE_CAUSE_SPECIFIC_COMPLETION_HAZARD NOT_IDENTIFIED
CAUSE_SPECIFIC_HAZARD_MODEL           NOT_IDENTIFIED
COMPETING_RISK_MODEL                  NOT_IDENTIFIED
```

`lambda_for(..., ceiling=...)` now **raises `SubdistributionMisuse`** unless
the caller passes `subdistribution_acknowledged=True` for descriptive use, and
`decision_row` always takes the any-basis hazard regardless of the ceiling
asked for. A documented convention that only lives in prose gets violated; one
that raises does not.

**Time-to-completion and completion-quality are separated.** The engine uses:

```
ANY_COMPLETION_HAZARD(t)         P(pairing occurs | still genuinely unpaired)
BASIS_QUALITY_DISTRIBUTION       P(basis <= B | a completion occurs)
```

and multiplies only at the end, via the exact identity

```
P(complete next interval AND basis <= B)
  = P(complete next interval) x P(basis <= B | complete)
```

Both factors are separately identified — the first from the risk set, the
second from differencing the retained interval completion counts (checked
well-formed: no negative interval count, none exceeding its any-basis count).
So `JOINT_TIME_BASIS_MODEL = DECOMPOSED_ANY_HAZARD_x_CONDITIONAL_BASIS`, at
`ACCOUNT × INTERVAL` granularity — **not** crossed with price band.

The conditional is also a finding in its own right. On RN1:

| interval | P(≤0.90 \| completion) | P(≤1.00 \| completion) |
|---|---|---|
| 0s–5s | 0.0219 | 0.9720 |
| 1800s–3600s | 0.3305 | 0.6068 |

A fast completion is a **tight, near-1.00** completion. A slow one is
**dispersed in both directions** — far more mass below 0.90 *and* far more
above 1.00. Waiting does not simply buy a cheaper pair; it widens the
distribution of what the pair costs. Preserved descriptively; it is not a
policy.

### 12c. WHAT PROSPECTIVE DATA IS FOR

The archive is cumulative counts, so it cannot distinguish `PAIR_AT_GOOD_BASIS`
/ `PAIR_AT_WORSE_BASIS` / `SELL` / `SETTLEMENT` / `OTHER` as competing terminal
transitions for an individual leg. Neither model is manufactured. The recorder
now writes an ordered per-position event history —

```
EVENT_KINDS      ENTRY, BOOK_STATE, PAIR_OPPORTUNITY_APPEARED,
                 PASSIVE_EXIT_AVAILABLE, AGGRESSIVE_EXIT_AVAILABLE,
                 HEDGE_AVAILABLE, ACTION_SELECTED, ACTION_NOT_SELECTED,
                 FILL, SETTLEMENT
TERMINAL_STATES  PAIRED, EXITED_PASSIVE, EXITED_AGGRESSIVE, HEDGED,
                 SETTLED, OTHER
```

— with out-of-order events **refused rather than sorted** (order is the data;
silently sorting a bad clock hides the bug the hazard would then be built on),
and `ACTION_NOT_SELECTED` written per rejected action with its EV. From enough
of these, `CAUSE_SPECIFIC_COMPLETION_HAZARD`, `CAUSE_SPECIFIC_EXIT_HAZARD`,
`COMPETING_RISKS` and `OPTIMAL_STOPPING_POLICY` become estimable. None is
claimed from them today.

### 12d. THE POSTERIOR UPDATES AT THE CELL

```
POSTERIOR_UPDATE_KEY = (SPORT, MARKET_TYPE, PRICE_BAND, TIME_UNPAIRED,
                        PAIR_BASIS_STATE)
POSTERIOR_CELL_MIN_SUPPORT = 30
POSTERIOR_BACKOFF_ORDER    = PAIR_BASIS_STATE, PRICE_BAND, MARKET_TYPE, SPORT
POSTERIOR_CELL_UPDATE_ACTIVE = NO
```

A pooled BETTOR hazard would let a thousand fast-pairing tennis moneyline legs
displace the prior for a thin futures market they say nothing about. Thin cells
**coarsen the key** rather than pooling everything. Inactive — no shadow
observation exists to key yet — but recorded now so the collector writes the
fields the update will need, which is the one thing that cannot be fixed
retrospectively.

---

## 13. FREEZE

```
BETTOR_EXIT_ENGINE_V1_PRIOR_COMPLETE = YES
HISTORICAL_WHALE_RESEARCH_FROZEN     = YES
NEXT_SOURCE_OF_INTELLIGENCE          = SHADOW_EXIT_LEARNING_V1
```

The historical cohort is the PRIOR. BETTOR's prospective observations become
the DATA. No further optimisation of this engine against the whale evidence.

---

## 14. PHASE_2A — SHADOW_EXIT_LEARNING_V1, FIRST RESULT

```
PHASE_2_STAGE   = PHASE_2A   LIVE_PUBLIC_MARKET_STATE_AND_DECISION_TELEMETRY
MODULES         shadow/collect.py   shadow/lifecycle.py   shadow/position_state.py
TESTS           181 in shadow/; 1,390 across beta48
VENUE CONTACT   ZERO new reads -- the exercise ran on the sealed census
```

### 14a. THE PAIR TRADE ON THIS VENUE IS MAKER-ONLY. MEASURED.

**I built the collector on a wrong assumption and the data corrected it.** I
expected the Polymarket shape — YES and NO as separately quoted tokens in
independent books whose asks can sum below 1.00. The captured board refutes
that on **all 20,000 rows**:

```
slugs             exactly one
side_identifiers  the SAME slug twice
side_descriptions ["Yes", "No"]
```

Every market is **one binary book carrying both sides**. Buying the complement
*is* selling the own side. There is no sibling contract to pair with, so the
pair basis is not a sum of two asks — it is one of two arithmetic facts,
measured over **9,143 routed two-sided books**:

| | formula | P10 / P50 / P90 | below 1.00 |
|---|---|---|---|
| cross both legs | `ask + (1 − bid) = 1 + spread` | 1.01 / 1.01 / 1.01 | **0 of 9,143** |
| rest both legs | `bid + (1 − ask) = 1 − spread` | 0.99 / 0.99 / 0.99 | **9,143 of 9,143** |

```
AGGRESSIVE_COMPLEMENT_PAIR_PROFITABLE_BEFORE_FEES = NO, STRUCTURALLY
PASSIVE_COMPLEMENT_PAIR_PROFITABLE_BEFORE_FEES    = YES, IF BOTH RESTS FILL
```

**This is the most consequential thing Phase 2A has produced, and it is not
good news.** The pair channel is not dead here — but it is available *only to a
maker*. Crossing is arithmetically certain to lose; resting both sides is
arithmetically certain to win **if both sides fill**. So on this venue the pair
channel does not route *around* `ACTUAL_BETTOR_FILL_PROBABILITY` — it collapses
**onto** it. It removes a hoped-for way past the blocker rather than adding a
new one.

It also scopes the whale prior precisely. The λ apparatus describes how long a
first leg waits for its complement on a venue where the complement is
independently quoted. Here, "waiting for the complement" and "waiting for the
other side of my own two-sided quote to fill" are the same event.

### 14b. THE PLUMBING EXERCISE — 788 rows, on sealed data

Run over the 788 HIGH_ACTIVITY markets from census 35105863528. No new reads.

```
MARKETS_OBSERVED                        9,709
SHADOW_ENTRY_OPPORTUNITIES                788
TELEMETRY_ROWS                            788
COUNTERFACTUAL_MAKER_FILLS                  0   (none supported; no rest was
                                                 ever placed, so TOUCH != FILL
                                                 has nothing to promote)
SHADOW_POSITIONS_CREATED                    0

POSITIONS_WITH_ALL_ACTIONS_PRICED           0
POSITIONS_BLOCKED_BY_FAIR_VALUE           788
POSITIONS_BLOCKED_BY_FILL_UNCERTAINTY       0
POSITIONS_BLOCKED_BY_OTHER_UNKNOWN          0

PRIOR3_VS_PRIOR4_ACTION_DISAGREEMENT        0
PRIOR3_VS_PRIOR4_ACTION_AGREEMENT         788

PASSIVE_PAIR_BASIS  min 0.95  P50 0.99  max 0.999   788 of 788 below 1.00
AGGRESSIVE_PAIR_BASIS                                 0 of 788 below 1.00
```

**Every row blocked on fair value, exactly as specified in advance.** The two
priors agree on all 788 — which is not evidence that they are equivalent: they
agree because *neither* selects an action while `EV_WAIT` is `NOT_IDENTIFIED`
under both. `ACTION_DISAGREEMENT` only becomes informative once actions are
being selected, and the column exists now so that it will be there when they
are.

### 14c. EVENT IDENTITY IS STILL UNRESOLVED, AND THE CAPTURE IS WHY

```
VALID_EVENTS                              0
INDEPENDENT_CAPACITY                      NOT_IDENTIFIED
MARKETS_WITH_UNRESOLVED_EVENT_IDENTITY  788
COMPLEMENT_REFUSAL: IDENTITY_LEVEL_C    773      NO_SIBLING_IN_FAMILY  15
```

> **SUPERSEDED BY §15a — and the diagnosis below was half wrong.** It was not a
> capture limitation either: `board_raw.jsonl.gz` already holds `marketSides`
> on all 20,000 rows. The defect was a FIELD SHAPE misread in the reader. No
> board walk was needed, and §15a measures the result from the sealed census
> with **zero** new venue reads.

Not a venue limitation — a **capture** limitation, and worth naming precisely:
`underlying_event_key` reads team ids and provider ids from `marketSides`, and
the board walk stores `side_identifiers` / `side_descriptions` instead.
`rows_with_eventSlug = 0`, so LEVEL_A is unavailable, and LEVEL_B cannot be
reached from what was retained. **This is a cheap fix on the next board walk,
not a research problem.** Until then capacity stays `NOT_IDENTIFIED` and every
market is treated as fully correlated — conservative, and deliberate.

### 14d. WHAT PHASE_2A HAS NOT PRODUCED

```
PROFITABILITY            NOT_REPORTABLE_THIS_PHASE
WIN_RATE                 NOT_REPORTABLE_THIS_PHASE
EXPECTED_MONTHLY_RETURN  NOT_REPORTABLE_THIS_PHASE
BETTOR_EXIT_ENGINE_PNL   NOT_IDENTIFIED
```

Zero shadow positions exist. Nothing here is a strategy result, and the
summariser refuses to emit those three fields as numbers at all.

### 14e. THE NEXT TWO EXPERIMENTS, IN COST ORDER

1. **Re-walk the board retaining `marketSides`.** Free, one board walk, no new
   authority. Converts `VALID_EVENTS` from 0 and makes event-stratified
   capacity possible for the first time.
2. **Live tick capture on a bounded candidate set.** Free, paced, public. Gives
   `TIME_UNPAIRED` real values and the basis a time series rather than one
   snapshot — the input `SHADOW_EXIT_LEARNING_V1` actually consumes.

Neither establishes a fill. Only an order does, and none is authorised.

---

## 15. PHASE_2A — THE TWO EXPERIMENTS, AND WHAT THE FILL QUESTION TURNS OUT TO COST

### 15a. EXPERIMENT 1 — VENUE-NATIVE EVENT IDENTITY. ZERO NEW VENUE READS.

§14c called `VALID_EVENTS = 0` a capture limitation and proposed a board
re-walk. **Both halves of that were wrong, and the correction is cheaper than
the plan.** The sealed census already contains `board_raw.jsonl.gz` with FULL
rows: every one of 20,000 carries `marketSides` (exactly 2), `gameStartTime`,
`side.teamId`, `side.team.league` and `side.team.providerId`.

The real defect, named exactly:

```
eligibility.underlying_event_key  reads  side.team.providerIds  as a LIST OF DICTS
this venue sends                         side.team.providerId   as a SCALAR
```

So it never reached LEVEL_B on a real row and fell to LEVEL_C on 773 of 788.
`shadow/event_identity.py` is an **additive second reader** — `eligibility`
stays frozen, so its published validation result still describes what it
validated — and it reads BOTH shapes, so a venue change in either direction
degrades instead of silently returning nothing.

```
FULL BOARD (20,000 rows)
  contest 1,557   subject 1,942   no-binding 16,501
  VALID_EVENTS 87
  MARKETS_PER_EVENT   min 1 / P50 15 / MAX 41 / mean 17.9
  INDEPENDENT_CAPACITY  NOT_IDENTIFIED (18,443 unresolved)   LOWER_BOUND 87

HIGH_ACTIVITY (788)
  contest 142     subject 121     no-binding 525
  HIGH_ACTIVITY_EVENTS 56         leagues cfb 29 / nfl 16 / ufc 11
  MARKETS_PER_EVENT   min 1 / P50 1 / MAX 15
  INDEPENDENT_CAPACITY  NOT_IDENTIFIED (646 unresolved)      LOWER_BOUND 56
```

**1,557 contest markets are 87 contests.** That is the correlation a market
count hides, now read from the venue's own fields rather than from a slug. The
capacity lower bound moves 0 → 56 on the candidate set; the exact figure stays
`NOT_IDENTIFIED` while any market's identity is unresolved, and subject-level
rows are never merged on start time alone. **A start time by itself is refused
as an identity at every level** — that is precisely the error that killed the
slug key, and refusing it is the whole point of the module.

### 15b. EXPERIMENT 2 — BOUNDED LIVE TICK CAPTURE

24 markets, 6 each across nfl/cfb/mlb/ufc, frozen into
`shadow/tick_universe.json` **before any tick was seen**, ordered by salted hash
within stratum and NOT by activity rank. Each tick carries BID/ASK/ladders/
depth plus what a snapshot cannot give: `QUOTE_LIFETIME_S`, `TIME_AT_BID_S`,
`TIME_AT_ASK_S`, `BID/ASK/DEPTH_CHANGED`, `SHARES_TRADED_DELTA`.
`TRADE_OCCURRED` is the only field in the capture that may be read as trading.

### 15c. THE COUNTERFACTUAL MAKER-FILL MODEL — IDENTIFICATION IS ASYMMETRIC

`shadow/maker_fill.py`. Per hypothetical quote: `QUOTE_TIME`, `QUOTE_PRICE`,
`QUEUE_AHEAD_ESTIMATE`, `BOOK_STATE_AT_ENTRY`, `TRADE_FLOW_AFTER_ENTRY`,
`PRICE_MOVEMENT_AFTER_ENTRY` → `FILL_STATUS`.

**The result that matters is a negative one, and it is structural.** A tick row
has no per-print tape, so for a quote resting at price *p*:

```
volume traded AT p      NOT_IDENTIFIED     which side consumed it   NOT_IDENTIFIED
our queue position      NOT_IDENTIFIED     the aggressor            NOT_IDENTIFIED
```

Every model in `fill_model_v2` shares the precondition `at > 0`. From ticks
alone that precondition is not identified, so **no positive fill support comes
out of tick data at all**. `fill_status()` has no branch that can return a
`COUNTERFACTUAL_FILL_*`, and `test_maker_fill.py` proves the absence by walking
the AST rather than trusting the docstring.

What ticks CAN settle is the refutation, by an upper bound nobody can argue
with. Credit **every share the whole market traded** to our price and our side:

```
MAXIMAL_ATTRIBUTION = sum of SHARES_TRADED_DELTA over the window

F0 refuted if  MAX <  QUEUE_AHEAD + QUOTE_SIZE
F1 refuted if  MAX <= QUEUE_AHEAD
F2 refuted only if MAX == 0      (a cancellation can clear a queue; it cannot
F3 refuted only if MAX == 0       create a fill, and F3 was never a fill)
```

`QUEUE_AHEAD_ESTIMATE` is the DISPLAYED size at our level; hidden size and
orders joining between polls only make the true queue LONGER, so a refutation
computed against it is conservative in the right direction.

Two consequences worth stating before any number is quoted:

1. **Joining a deep displayed queue is refutable from aggregate volume;
   improving the price to the front of the book (QUEUE_AHEAD = 0) makes the
   fill question entirely tape-dependent.** The cheaper the queue, the less our
   own data can say about it.
2. **The tape join has a precondition of its own.**
   `fill_model_v2.classify_execution` reaches `CLOB_EXECUTION` only through a
   venue execution-type flag or a block index; an unflagged, unindexed print is
   `UNKNOWN_EXECUTION_TYPE`, which depletes no queue. **A raw tape with no
   block publication supports nothing, however much volume it shows.** That is
   the second gate between this programme and its first counterfactual fill,
   and it is easy to mistake for a bug in the join.

`summarise_fills` refuses to divide fills by quotes while any outcome is
UNKNOWN: treating an unresolved quote as a miss is the same error as treating
one as a fill, just in the flattering direction for a cautious-sounding number.

### 15d. INVENTORY-CLOSURE LEARNING

`shadow/inventory.py`. `open_inventory()` refuses anything whose `FILL_STATUS`
is not a `COUNTERFACTUAL_FILL_*`, and **UNKNOWN is refused as firmly as
NOT_FILLED** — an unresolved fill is not a small fill. Combined with §15c, the
honest first output on tick-only data is **no inventory rows at all**. That is
the measurement, and it names exactly what the next capture must add.

The two closes are different kinds of object and are never mixed:

```
PASSIVE_CLOSE_PRICE     the price we would REST at, carried WITH its own
                        FILL_STATUS. A hope with a price attached.
AGGRESSIVE_CLOSE_PRICE  the price available NOW by crossing displayed size,
                        walked across the captured ladder and NOT_IDENTIFIED
                        beyond it. A measured floor.
```

Per closed round trip: `GROSS_SPREAD`, `EXIT_TAKER_FEE`,
`TRADING_NET_EX_INCENTIVES` **first**, then `MAKER_REBATE` /
`LIQUIDITY_INCENTIVE` / `OTHER_INCENTIVE` and only then `TOTAL_NET`. The maker
rebate sits on the INCENTIVE side of that split, which is what makes "the
spread was negative and the rebate saved it" visible instead of netted away.
Also `INVENTORY_MARKOUT` (a frozen horizon set, never one horizon promoted to
be *the* markout), `MAX_ADVERSE_EXCURSION`, `MAX_FAVORABLE_EXCURSION`,
`CAPITAL_OCCUPANCY` in dollar-seconds with both factors kept, `FAILURE_TO_CLOSE`
with a reason, and `SETTLEMENT_OUTCOME = NOT_IDENTIFIED` — a tick window is
minutes long and settlement is days away.

**`INCENTIVE_DEPENDENT` is now three-valued.** A row with negative trading
economics and an unmeasured incentive total used to read `NO`, which is
reassurance the evidence does not carry; it now reads `NOT_IDENTIFIED` until
both sides of the question are numbers.

`FEE_REGIME` travels with every row and a straddle is named, never pooled. This
matters within hours: the taker curve changes at **2026-09-17 03:59 UTC**
(theta 0.06 → 0.0695), so tonight's capture is JUL2026 and tomorrow's is not.

The whale comparison is available and guarded:
`compare_close_times()` raises `EstimandMismatch` unless the caller states that
`WHALE_PRIOR_EXPECTED_CLOSE_TIME` (time to pair completion by any means, on a
two-token venue) and `BETTOR_OBSERVED_COUNTERFACTUAL_CLOSE_TIME` (time to a
passive inventory close on one binary book) are **not the same estimand**. A gap
between them mixes a venue-structure difference with a speed difference, and
`DIFFERENCE_IS_EVIDENCE_ABOUT_BETTOR_SPEED` stays `NOT_IDENTIFIED`.

### 15e. WHAT BETTOR IS, IN ONE LINE FOR MANAGEMENT

**BETTOR IS A MAKER-FIRST TWO-SIDED MARKET-MAKING SYSTEM.** It opens inventory
passively, prefers to close it passively, and treats an aggressive close as a
priced option rather than a failure. It is not a directional betting system and
it is not an arbitrage system: the structural spread is captured only when the
relevant legs execute passively, and that capture is **GROSS**.

```
GROSS_TWO_SIDED_MAKER_EDGE        OBSERVED
REALIZED_BETTOR_TWO_SIDED_EDGE    NOT_ESTABLISHED
SPREAD_STATEMENT_QUALIFIER        IF_BOTH_FILLS_OCCUR
```

### 15f. THE PHASE_2A SUCCESS CRITERION, ANSWERED AS FAR AS IT CAN BE

```
How often a maker quote would have been filled        NOT_IDENTIFIED  (§15c)
  ... and how often it is REFUTED                     MEASURABLE FROM TICKS
How long inventory stays one-sided                    NOT_IDENTIFIED  (no fills)
How often the opposite passive close arrives          NOT_IDENTIFIED  (no fills)
What gross spread a completed round trip captures     FORMULA READY, NO SAMPLE
Markouts while waiting                                MEASURABLE FROM TICKS
How often an aggressive close is needed               MEASURABLE FROM TICKS
Capital-time per round trip                           FORMULA READY, NO SAMPLE
```

Every quantity that depends on a FILL is `NOT_IDENTIFIED` and will stay so
until the execution tape is joined to the tick capture with a block index or a
venue execution-type column. Every quantity that depends only on the BOOK is
computable from the capture now. **None of this is profitability**, and the
summarisers refuse to emit `PROFITABILITY`, `WIN_RATE` or
`EXPECTED_MONTHLY_RETURN` as numbers at all.

```
ORDERS_PLACED   0        CAPITAL_DEPLOYED  0
CREDENTIALS     NONE     mirror_live       false
```

---

## 16. TWO PRECISION CORRECTIONS, BOTH AGAINST §15

Both of these cut against conclusions §15 had just drawn, and the first one
removes the only positive-sounding output the tick capture was going to produce.

### 16a. THE VOLUME BOUND WAS ARITHMETIC OVER AN UNPROVEN FIELD

§15c refuted counterfactual fills from an upper bound on traded volume. The
arithmetic is sound. **The input was not established**, and the project state
already said so:

```
SHARES_TRADED_RESET_SEMANTICS = NOT_IDENTIFIED
```

An upper bound is only a bound if the quantity it bounds is the quantity we
think it is. Every one of these breaks it and none was excluded: the field being
INTERVAL rather than cumulative; a session reset reading as a negative or a tiny
delta; a SIDE-SPECIFIC count; blocks in or out; and — the dangerous one — a
**lazy update**, under which an unchanged `SHARES_TRADED` gave
`MAXIMAL_ATTRIBUTION = 0` and refuted every model including the touch bound. A
quiet field and a quiet market are indistinguishable from one snapshot pair, and
the quiet field is exactly what a thin feed does. That is not a missed nuance;
it is a **confident wrong answer**, which is worse than an unknown.

`shadow/tick_semantics.py` now owns the gate and keeps the two questions apart,
because they have different answers and different owners:

```
RUNTIME BEHAVIOUR   what the field DID in our capture. Ours to measure.
  MONOTONIC_WITHIN_MARKET / NEGATIVE_DELTAS_OBSERVED / RESET_EVENTS_OBSERVED
  MISSING_TRANSITIONS / FIELD_UPDATE_FREQUENCY
  SAME_VALUE_DESPITE_BOOK_CHANGES / LARGE_DISCONTINUITIES

VENUE SEMANTICS     what the field MEANS. NOT ours to infer, all NOT_IDENTIFIED.
  WHAT_SHARES_TRADED_COUNTS / CUMULATIVE_OR_INTERVAL
  MARKET_WIDE_OR_SIDE_SPECIFIC / INCLUDES_BLOCKS / RESET_BOUNDARY
```

A field can be monotone all afternoon and still reset at midnight, still count
one side, still include blocks — so `semantics_established()` requires **both**,
and runtime evidence alone never opens it. Until it does:

```
SHARES_TRADED_DELTA_STATUS = CONSERVATIVE_DIAGNOSTIC_ONLY
FILL_REFUTATION_STATUS     = NOT_IDENTIFIED
WHY                        = AGGREGATE_VOLUME_UPPER_BOUND_INSUFFICIENT
                             (NOT equivalent to observed execution evidence)
PROVEN_NOT_FILLED          = REFUSED_NOT_AN_OUTPUT_OF_THIS_PROGRAMME
```

**Consequence: `fill_status()` on tick data now returns UNKNOWN in every case.**
It cannot support a fill (no per-price attribution) and it may not refute one
(no field semantics). The bound is still computed and carried as
`AGGREGATE_VOLUME_BOUND_ARITHMETIC`; it is simply not a verdict.

### 16b. THREE EVENTS, NEVER COLLAPSED

```
TOUCH                the market reached our price
TRADE_EVIDENCE       a QUALIFYING execution occurred at our price after entry
COUNTERFACTUAL_FILL  that evidence PLUS the queue model

TOUCH != TRADE_EVIDENCE != COUNTERFACTUAL_FILL
```

All three are separate fields on every row and three separate counts in every
summary, so the gap between them is visible rather than inferred. A block print
at our price is a trade and is **not** trade evidence — it never touched the
book — and an untyped print is not either.

### 16c. AN EVENT COUNT IS NOT A CAPACITY — ALSO MINE

§15a reported 56 validated HIGH_ACTIVITY events as
`INDEPENDENT_CAPACITY_LOWER_BOUND`. That turned a count of events into a claim
about how much capital the venue can absorb. An event with nothing fillable has
a capacity of zero. Corrected everywhere:

```
VALIDATED_DISTINCT_HIGH_ACTIVITY_EVENTS = 56
MARKETS_PER_HIGH_ACTIVITY_EVENT         = min 1 / P50 1 / MAX 15
INDEPENDENT_EVENT_COUNT                 = 56
DEPLOYABLE_CAPITAL_CAPACITY             = NOT_IDENTIFIED
```

`DEPLOYABLE_CAPITAL_CAPACITY` additionally requires actual fillability,
available depth, quote size, inventory duration, capital turnover, the event
exposure limit, correlation, the risk budget and execution costs. **None is
measured.** `event_identity.capacity()` is gone; the function is now
`independent_event_count()`, and a test asserts the old name no longer exists.

RESIDUAL, NAMED RATHER THAN EDITED: `forward/eligibility.py` still emits
`INDEPENDENT_CAPACITY*` fields. It is frozen so its published validation result
keeps describing what it validated, and its value there is already
`NOT_IDENTIFIED`. The naming is on the list to correct if that module is ever
unfrozen.

### 16d. THE CENTRAL PMUS RESULT, IN ITS CANONICAL LABELS

```
PMUS_VENUE_STRUCTURE  ONE BINARY BOOK; YES and NO are economic complements
Crossing both directions structurally pays      1 + spread
Resting opposite passive executions can produce 1 - spread

GROSS_PASSIVE_TWO_SIDED_SPREAD_OPPORTUNITY = OBSERVED
ACTUAL_BETTOR_TWO_SIDED_FILL_RATE          = NOT_IDENTIFIED
ACTUAL_BETTOR_REALIZED_SPREAD_CAPTURE      = NOT_ESTABLISHED
```

before fees, rebates, rewards, adverse selection, partial fills, inventory risk
and capital occupancy. The middle line is the gap the shadow programme exists to
close, and §16a is why it is still open.

### 16e. WHAT THE CAPTURE *CAN* ANSWER — `shadow/book_metrics.py`

Not identifying a fill does not make the capture worthless. Every one of these
is a property of the BOOK, fully observable, and an execution input a maker
needs before it quotes anything:

```
SPREAD P10/P50/P90            ONE_TICK_UPTIME            SPREAD_PERSISTENCE_S
QUOTE_LIFETIME_AT_TOUCH       TIME_AT_PRICE              TOUCH_SIZE (both sides)
BOOK_MOVE_FREQUENCY           MID_MOVE_FREQUENCY         DEPTH_CHANGE_RATE
PRICE_IMPROVEMENT_FREQUENCY   DISPLAYED_QUEUE_CHANGE
QUEUE_AHEAD_AT_HYPOTHETICAL_ENTRY
MARKET_MOVED_THROUGH_QUOTE_FREQUENCY        POST_QUOTE_BOOK_MARKOUT
```

**None of them may be renamed into anything that reads as an execution
outcome.** `FILL_RATE`, `MAKER_FILL_RATE`, `EXECUTION_RATE`, `FILL_PROBABILITY`
and `HIT_RATE` are listed as forbidden, and a test walks every measured key to
assert none is named like an outcome. `MARKET_MOVED_THROUGH_QUOTE` is the
closest any of this comes, and it is a fact about where the price went.

### 16f. THE INVENTORY CLOCK

Preserved and now explicit: only an admitted counterfactual fill sets
`INVENTORY_START_TIME`. `UNKNOWN` does not. `NOT_FILLED` does not. Every
duration — `TIME_TO_OPPOSITE_CLOSE`, the markouts, MAE, MFE,
`CAPITAL_OCCUPANCY`, the round trip — is measured from it, and the row records
`INVENTORY_CLOCK_STARTED_BY` so the fill that started it travels with the
position.

### 16g. WHAT BETTOR IS

```
BETTOR IS A MAKER-FIRST TWO-SIDED MARKET-MAKING AND INVENTORY-MANAGEMENT SYSTEM.

It attempts to earn   SPREAD, MAKER REBATES, LIQUIDITY / MARKET-MAKING REWARDS
When one side fills first, BETTOR TEMPORARILY OWNS INVENTORY, and the exit
engine chooses on expected economics among
  PASSIVE_INVENTORY_CLOSE / REQUOTED_PASSIVE_CLOSE / AGGRESSIVE_INVENTORY_CLOSE
  HEDGE_EXTERNALLY / HOLD_INVENTORY / SETTLE

DO_NOT_DESCRIBE_PMUS_AS = BUY_LEG_1_THEN_BUY_LEG_2
```

That last is imported Polymarket two-token language. On PMUS there is one book,
and what a first fill creates is INVENTORY, not a leg.

### 16h. THE HARVEST, AND THE ANSWER IT IS BUILT TO GIVE

`shadow/harvest.py` reads the sealed capture off disk and reports, in order: the
capture (markets, events, sport mix, rows, errors); the field (runtime and venue
semantics, kept apart); the book (§16e); the four ladder counts
(`HYPOTHETICAL_QUOTES` / `TOUCHES` / `TRADE_EVIDENCE` /
`ADMITTED_COUNTERFACTUAL_FILLS`); and then

```
PUBLIC_TICK_DATA_SUFFICIENT_FOR_FILL_IDENTIFICATION = NO
WHY = no execution evidence exists in this capture: the tick feed carries no
      per-print tape, no side and no queue position, so TRADE_EVIDENCE is
      NOT_IDENTIFIED and no fill can be admitted

EVIDENCE_THAT_WOULD_IDENTIFY_A_FILL
  PUBLIC_EXECUTION_TAPE_WITH_TIMESTAMP_PRICE_QUANTITY
  AN_EXECUTION_TYPE_COLUMN_OR_A_BLOCK_PUBLICATION
  A_SIDE_OR_AGGRESSOR_FLAG
  QUEUE_POSITION_OR_ORDER_LEVEL_BOOK_UPDATES

NEXT_STEP = obtain evidence capable of resolving execution and queue, OR a
            bounded micro-live validation AFTER EXPLICIT AUTHORIZATION
DO_NOT_INVENT_ANOTHER_FILL_APPROXIMATION = True
```

A third fill heuristic would be the first two's mistake with more machinery
around it. The report is written so that saying NO is the easy path.

```
ORDERS_PLACED   0        CAPITAL_DEPLOYED  0
CREDENTIALS     NONE     mirror_live       false
```
