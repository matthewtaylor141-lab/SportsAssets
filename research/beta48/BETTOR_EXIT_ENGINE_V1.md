# BETTOR_EXIT_ENGINE_V1

The residual-inventory engine. This is where BETTOR attempts to improve most on
the reference accounts, and the improvement claimed is **architectural**, not
performance.

```
EXIT_ENGINE_V1        = SPECIFIED, SHADOW-ONLY, NOT BUILT
ORDER_CAPABLE         = NO
MICRO_LIVE_AUTHORIZED = NO
mirror_live           = false

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
Prior from the whale grid: `H(t1,t2)` conditioned on price band and basis
ceiling, with the **per-minute** rate used for reasoning and the per-interval
figure used for wall-clock planning. The archive's per-interval hazard rises to
a peak at 600–1800s purely because those buckets are longer; per minute it
decays monotonically, and the engine uses the decaying form.

Outputs `P(COMPLETION_IN_NEXT_INTERVAL | band, basis, time_unpaired)`.

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

---

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
BETTOR_POLICY_IS_MORE_COMPLETE_AND_ECONOMICALLY_EXPLICIT_THAN_ANY_SINGLE
REFERENCE_POLICY  = the engineering objective of this document

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
```
