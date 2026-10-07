# BETTOR_PROFITABILITY_STACK_V1

One independent, research/PAPER-SHADOW-only package containing five profitability workstreams for BETTOR:

1. `BETTOR_EXECUTION_TRUTH_LAB_V1`
2. `BETTOR_STRUCTURAL_ARB_GRAPH_V1`
3. `BETTOR_DIGITAL_TWIN_EXCHANGE_V1`
4. `BETTOR_PROFITABILITY_GOVERNOR_V1`
5. `BETTOR_PNL_ATTRIBUTION_V1`

The stack is deliberately isolated from live trading. It contains **no network client, no credential reader, no order submit/cancel integration, and no capital-limit mutation**.

Its purpose is to make it much harder for BETTOR to mistake:
- prediction accuracy for tradable edge,
- quoted spread for realizable maker profit,
- repeated observations for independent evidence,
- backtest performance for forward profitability,
- or high model confidence for capital eligibility.

## Non-negotiable system invariant

> Capital is earned by positive conservative executable expectancy, not by a model confidence percentage and not by a turnover target.

If no strategy clears the evidence and capacity gates, the correct allocation is **CASH**.

---

# 1. Execution Truth Lab

`bettor_profit_stack/execution_truth.py`

Answers:

> Can BETTOR actually monetize a forecast or price discrepancy after fills and costs?

It provides:

- taker EV
- maker EV conditional on fill
- fill-probability uncertainty
- conditional adverse selection
- cancel-failure cost
- inventory-risk cost
- fee / slippage / management cost
- rebate handling
- wait economics
- MAKE / TAKE / WAIT / REFUSE selection
- edge-decay slope
- lower-bound execution admission

Important rule:

```text
maker profitability != quoted spread
maker profitability = P(fill) × profit conditional on fill
```

and fill-conditioned adverse selection belongs in the cost model.

---

# 2. Structural Arb Graph

`bettor_profit_stack/structural_arb.py`

Answers:

> Can BETTOR form a portfolio whose payoff is guaranteed across every explicitly modeled settlement state for less than that guaranteed payout?

Includes:

- YES/NO complement checks
- exhaustive mutually-exclusive baskets
- generic long-only superhedge solver using linear programming
- per-contract depth caps
- fees/slippage
- explicit settlement-state payoff vectors
- settlement-key compatibility
- implication-price-bound checks

The generic solver minimizes:

```text
total all-in cost
```

subject to:

```text
portfolio payoff >= target payout
for every valid settlement state
```

No two contracts are assumed equivalent merely because their labels look similar. Settlement compatibility must be explicit in the payoff state space.

---

# 3. Digital Twin Exchange

`bettor_profit_stack/digital_twin.py`

Answers:

> Would the current production decision logic actually have made money if replayed through the historical event stream without lookahead?

Includes a deterministic event-by-event exchange simulator with:

- order-book events
- trade events
- settlement events
- submit latency
- cancel latency
- queue-ahead approximation
- partial fills
- marketable-on-ack behavior
- resting-order crossing
- queue consumption before fills
- settlement reconciliation
- read-only strategy views

The strategy callback receives only state observed at or before the current event.

This is intended to wrap actual BETTOR decision code later, not replace it with a simplified strategy.

---

# 4. Profitability Governor

`bettor_profit_stack/profitability_governor.py`

Answers:

> Has a strategy earned the right to consume capital?

Includes a time-uniform bounded-mean confidence sequence using an alpha-spending / union-bound Hoeffding construction.

Use the **independent event/settlement** as the observation unit. Do **not** feed repeated 15-minute re-evaluations of the same market as independent evidence.

The governor can enforce:

- minimum independent forward events
- positive lower confidence bound
- calibration proof
- execution-economics proof
- settlement-identity proof
- executable capacity
- CASH fallback
- strategy concentration caps

Statuses are deliberately conservative:

- `SHADOW_ONLY`
- `CASH`
- `ACTIVE_CHALLENGER`

This package intentionally does not auto-issue `ACTIVE_CHAMPION`; BETTOR management can layer a stronger promotion contract on top after sufficient forward evidence.

---

# 5. P&L Attribution

`bettor_profit_stack/pnl_attribution.py`

Answers:

> Why did realized P&L differ from expected P&L?

Every settled decision can be decomposed into:

- expected profit
- realized profit
- total residual
- forecast residual vs market prior
- outcome variance vs calibrated probability
- execution residual
- management residual
- settlement residual
- reconciliation error

The accounting identity for expected-vs-realized residual is:

```text
realized - expected
=
outcome variance
+ execution residual
+ management residual
+ settlement residual
```

The separate `forecast_residual` reports how far the calibrated model moved away from the market prior; it is not double-counted in the residual reconciliation.

Aggregation helpers support:
- strategy
- sport
- family
- regime
- venue

---

# Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pytest -q
```

Run the synthetic smoke demo:

```bash
python examples/run_demo.py
```

The demo is **not** a profitability claim.

---

# Recommended BETTOR integration sequence

## Phase 1 — research only
Vendor this package under a separate branch/worktree such as:

```text
claude/profitability-stack
research/profitability_stack/
```

Do not merge it into the active closeout release.

## Phase 2 — immutable input adapters

Build read-only adapters from BETTOR records into:

### Execution Truth
- candidate probability
- displayed bid/ask
- actual fill / non-fill
- queue/depth
- fees/rebates
- subsequent marks at 100ms / 500ms / 1s / 5s
- cancel/replace timings

### Structural Arb
- canonical contract identity
- venue
- exact payoff state
- settlement rule
- executable depth
- fees/slippage

### Digital Twin
- event-time ordered book observations
- trades
- decisions
- order acks/cancels if available
- fills
- settlement

### Governor
- one independent event-level net result per strategy/family/regime
- declared bounded return range
- forward-only evidence

### P&L Attribution
- decision-time expected values
- actual fill
- actual costs
- realized management effect
- settlement effect
- final outcome

## Phase 3 — shadow receipts

Every run should produce immutable hashed receipts.

Do not overwrite prior receipts when methodology changes.

## Phase 4 — PAPER controls

Only after evidence:
- all-in executable EV gate may affect PAPER admission
- no positive eligible strategy => CASH
- capacity is a ceiling, not a turnover requirement
- unknown/unmeasured cannot justify growth

## Phase 5 — live eligibility

Outside this package.

Requires explicit BETTOR approval and the existing production safety gates.

---

# What this package does NOT promise

It does not guarantee profit.

No responsible system can guarantee substantial consistent profit from day one.

It instead supplies five controls that make false-positive profitability substantially harder:
- execution falsification
- structural-arbitrage proof
- no-lookahead replay
- evidence-gated capital
- exact P&L diagnosis

That is the foundation required to determine whether a strategy truly deserves live capital.
