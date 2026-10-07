# BETTOR_REVENUE_RELIABILITY_STACK_V1

Additive research / PAPER-SHADOW package aligned to BETTOR's current production
contracts. It is designed to improve operations immediately by giving Claude a
concrete economic scorecard for each agent, a champion/challenger process that
can keep CASH as the incumbent, a regime abstention layer, a reliability-aware
portfolio plan, counterfactual agent value-add, and a daily revenue-readiness
contract.

This package does **not** grant new execution authority.

## Verified current-system contracts

The package was authored against tested closeout SHA:

`659edaf21c2deb1dcc032932f38a7ff1ec8456c2`

and intentionally mirrors the existing schema/behavior instead of inventing a
parallel system.

### Existing strategy lifecycle — migration 290

Current states:

- `ACTIVE_CHAMPION`
- `ACTIVE_CHALLENGER`
- `REDUCED_SIZE`
- `SHADOW_ONLY`
- `QUARANTINED`
- `RETIRED`

Current behavior already treats lifecycle as an **additional gate, never a
grant**. `SHADOW_ONLY / QUARANTINED / RETIRED` refuse new PAPER entries and
`REDUCED_SIZE` can only shrink. History is append-only.

### Existing opportunity tournament — migration 300

BETTOR already records a decision-time V1/V2 opportunity-score tournament in
`opportunity_score_tournament` with:

`authority = SHADOW_NO_AUTHORITY`

and explicitly requires later out-of-sample evidence before any promotion.

This package extends that operating idea with an absolute-positive
champion/challenger contract in which **CASH is allowed to remain champion**.

### Existing capital-authority shadow evidence — migration 305

BETTOR already records:

- `paper_shadow_counterfactuals`
- `paper_shadow_counterfactual_outcomes`
- `paper_entry_refusal_census`

These are zero-capital, append-only, and explicitly `NOT_REALIZED_PNL`.

This package consumes that philosophy: refused decisions remain useful evidence
without becoming orders.

### Existing profitability bind — migration 309

BETTOR already records learned:

- `CALIBRATION`
- `EXECUTION`
- `RESIDUAL`

models and per-entry:

- raw probability
- probability used
- market price
- calibration weight
- capacity factor
- correlation factor
- capital-hour factor
- all-in executable EV
- EV per capital-hour
- residual haircut
- adverse-selection estimate
- fill probability
- `ENTER / CASH`

The bind already says it can only **refuse or shrink** and records explicit CASH
passes.

This package treats those fields as the system of record and adds reliability
scoring above them rather than replacing them.

### Existing counterfactual variants — migration 311

BETTOR already records:

- `AS_BOUND`
- `POLICY_SIZE`
- `HALF_SIZE`
- `MAKER`
- `HOLD_TO_SETTLEMENT`
- `EARLY_EXIT`

plus settled counterfactual outcomes that are never counted as realized P&L.

This package uses those records to score agent economic contribution.

### Existing Xavier management value-add — migration 206

BETTOR already freezes Xavier's management counterfactuals at entry:

- `HOLD_TO_SETTLEMENT`
- `IMMEDIATE_EXIT`
- `ACTUAL_XAVIER`

and records immutable `xavier_value_add`.

The new counterfactual scorer therefore evaluates Xavier against the system's
existing frozen baselines instead of inventing hindsight alternatives.

### Existing agent authorities — migration 266

Current canonical authority contracts:

- Derek: `ENTRY_REQUEST_THROUGH_GATED_PATH`
- Xavier: `MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH`
- Audrey: `AUDIT_NO_ORDER_PATH`
- Karen: `CHALLENGE_ONLY_ZERO_AUTHORITY`
- Chief Allocator / Allie: `SHADOW_WEIGHTS_ONLY`
- Archer: `SHADOW_ONLY`
- Scout: `RESEARCH_SHADOW_ONLY`
- Adriana: `SHADOW_ONLY`

This package **never changes any of them**.

### Existing Karen challenge discipline — migration 207

Karen is challenge-only, has no approval/release/order/control authority, and
the current schema records false blocks and downstream impact. The certification
engine therefore scores Karen on **saved loss versus false-block opportunity
cost**, not on raw number of challenges.

### Existing Audrey improvement framework — migration 155

BETTOR already has:

- immutable improvement candidates
- separation of proposer / evaluator / approver
- holdout budgets
- write-once evaluations
- improvement trials
- release/canary records
- protected keys

This package's recommended integration uses that governed improvement system,
not a bypass around it.

---

# Modules

## 1. Agent Certification Engine

`bettor_revenue_reliability/certification.py`

Produces economic statuses:

- `LICENSED`
- `PROBATION`
- `SHADOW_ONLY`
- `SUSPENDED`

Certification **does not grant authority**. It describes whether an agent has
demonstrated positive incremental economic value under its existing role.

Suggested scorecards:

### Derek
Value versus market-prior/CASH baseline.

### Karen
Saved loss minus false-block opportunity cost.

### Allie / Chief Allocator
Realized portfolio value versus equal-weight allocation among already eligible
positive strategies.

### Archer
Realized execution value versus the best feasible execution benchmark.

### Xavier
Actual managed P&L versus both frozen `HOLD_TO_SETTLEMENT` and
`IMMEDIATE_EXIT` counterfactuals.

### Audrey
Unexplained reconciliation residual; target is effectively zero.

## 2. Champion–Challenger Tournament

`tournament.py`

The incumbent can be `CASH`.

No model/strategy wins because it is merely least negative. Promotion requires:

- sufficient independent events
- evidence completeness
- absolute positive lower-bound net economics

Brier/log-loss/calibration/drawdown are tie-breakers and safety diagnostics, not
substitutes for positive economics.

## 3. Regime / Abstention Authority

`regime.py`

Each `sport × family × regime` must independently prove:

- fresh evidence
- settlement identity
- sufficient independent events
- calibration
- positive lower-bound executable EV

Otherwise the recommendation is one of:

- `ABSTAIN`
- `SHADOW_ONLY`
- `CASH`

The module only returns `ELIGIBLE_FOR_EXISTING_GATED_PATH`; it never bypasses
BETTOR's existing entry/risk/profitability gates.

## 4. Revenue Reliability Optimizer

`reliability.py`

Builds a reliability-aware shadow allocation across strategies that have already
proven positive lower-bound economics.

It considers:

- independent sample size
- lower-bound daily profit
- daily variance
- drawdown
- profit per capital-hour
- capacity
- lifecycle state
- correlation

If no strategy qualifies, the entire bankroll remains `CASH`.

Turnover is **not** a target. Positive executable capacity is a ceiling.

## 5. Agent Counterfactual Scoring

`counterfactuals.py`

Measures the economic contribution of each agent against a predeclared baseline.

This plugs directly into current BETTOR evidence:

- Xavier → migration 206 frozen management counterfactuals
- Archer → execution evidence / current counterfactual variants
- Allie → allocator benchmark
- Derek → market prior / CASH benchmark
- Karen → challenge saved-loss vs false-block cost
- Audrey → reconciliation residual

## 6. Daily Revenue Readiness Contract

`daily_contract.py`

Produces a daily capital plan:

- available bankroll
- proven positive capacity
- planned allocation
- CASH
- expected daily profit
- per-strategy lifecycle / event count / lower bound / blocker

This replaces "how much did we trade?" with:

> How much proven positive capacity existed, and how much of it did BETTOR
> rationally allocate?

## 7. Integrated Shadow Cycle

`integration.py`

Runs all six layers together and explicitly returns:

`mode = RESEARCH_PAPER_SHADOW_ONLY`

and

`authority_changed = False`

---

# Read-only current-system extract

`sql/current_system_readonly_extract.sql`

The provided SQL is SELECT-only and reads the current verified BETTOR tables:

- `paper_strategy_lifecycle_current_v`
- `paper_profitability_evaluations`
- `paper_cash_decisions`
- `opportunity_score_tournament`
- `paper_counterfactual_variants`
- `paper_counterfactual_variant_outcomes`
- `derek_entry_decisions`
- `karen_challenges`
- `xavier_entry_theses`
- `xavier_value_add`
- `improvement_candidates`
- `improvement_trials`

This allows Claude to build the first real-data receipts without inventing a new
database authority layer.

---

# Immediate operational use

This package should first run as an evidence layer beside the current system.

It can immediately improve operations by producing:

1. an agent-by-agent economic certification table;
2. a CASH-vs-strategy champion/challenger table;
3. a sport/family/regime abstention table;
4. a reliability-aware shadow capital plan;
5. agent counterfactual value-add;
6. a daily revenue-readiness brief.

Only after those outputs are proven should Claude propose a governed
`improvement_candidate` through Audrey's current holdout/release framework.

---

# Safety

The package has:

- no network client
- no credential access
- no order submit/cancel path
- no live capital authority
- no historical PAPER mutation
- no lifecycle writes
- no risk-cap changes
- no production migration

It is intended to improve the system by making profitability evidence sharper,
not by weakening the gates that currently prevent bad capital deployment.
