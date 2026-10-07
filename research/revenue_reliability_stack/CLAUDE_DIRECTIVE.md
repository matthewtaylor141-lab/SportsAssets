# CLAUDE — BETTOR REVENUE RELIABILITY STACK V1

Integrate the attached package as a **separate additive research/PAPER-SHADOW
workstream first**. Do not rewrite it as a plan-only response.

## Why this package exists

This is not a generic architecture. It was built against BETTOR's current
contracts at tested closeout SHA 659edaf21c2deb1dcc032932f38a7ff1ec8456c2.

It intentionally uses the system you already have:

- lifecycle states from migration 290
- score tournament from 300
- zero-capital counterfactuals from 305
- all-in profitability/CASH bind from 309
- counterfactual variants from 311
- Xavier value-add from 206
- Karen challenge/false-block records from 207
- Audrey governed improvement/holdout system from 155
- current agent authorities from 266

Do not create a parallel capital-authority system.

## Phase A — import unchanged

Create a separate branch/worktree such as:

`claude/revenue-reliability-v1`

Vendor under:

`research/revenue_reliability_stack/`

Run the package tests unchanged.

Return:
- NEW research SHA
- exact files
- test/pass count

## Phase B — real-data read-only adapters

Use `sql/current_system_readonly_extract.sql` as the basis, adapting column names
only where current production has legitimately moved forward.

Generate these six receipts from real BETTOR evidence:

### 1. Agent Certification

For every canonical agent:

- Derek
- Karen
- Chief Allocator / Allie
- Archer
- Xavier
- Audrey
- Scout
- Adriana

return:

- current authority status
- independent event count
- expected contribution
- realized contribution
- lower-bound incremental value
- exact blocker
- economic certification:
  LICENSED / PROBATION / SHADOW_ONLY / SUSPENDED

Certification must never grant authority.

### 2. Champion / Challenger

Use current `opportunity_score_tournament`, profitability evaluations and
settled evidence.

CASH is the incumbent.

Require:
- independent forward events
- all-in positive lower bound
- no material calibration failure
- no lifecycle block

Do not promote a least-negative strategy.

### 3. Regime Authority

Produce `sport × family × regime` rows with:

- independent event count
- calibration
- freshness
- settlement proof
- all-in lower-bound EV
- status:
  ABSTAIN / SHADOW_ONLY / CASH / ELIGIBLE_FOR_EXISTING_GATED_PATH

No unsupported segment gets capital merely because a global strategy is good.

### 4. Revenue Reliability Plan

For strategies already eligible under existing BETTOR gates, compute:

- expected daily P&L
- lower-bound daily P&L
- variance
- drawdown
- expected losing streak if measurable
- $/capital-hour
- positive executable capacity
- cross-strategy correlation
- reliability score
- shadow allocation
- CASH

Never optimize to a turnover target.

### 5. Agent Counterfactual Value-Add

Use existing frozen evidence wherever possible.

Derek:
- actual entry economics vs market-prior/CASH baseline

Karen:
- saved loss vs false-block opportunity cost

Allie:
- actual allocator result vs equal-weight eligible-strategy benchmark

Archer:
- execution result vs best feasible execution benchmark

Xavier:
- `ACTUAL_XAVIER` vs frozen `HOLD_TO_SETTLEMENT`
- `ACTUAL_XAVIER` vs frozen `IMMEDIATE_EXIT`

Audrey:
- unexplained reconciliation residual

Report both mean and conservative lower bound by agent.

### 6. Daily Revenue Readiness Brief

Produce a machine-readable receipt and human summary showing:

- bankroll
- proven positive capacity today
- planned deployment
- CASH
- expected daily net profit
- per-strategy blocker
- per-agent certification
- regimes currently disabled
- reasons capital stayed unused

## Phase C — use Audrey's existing governed improvement framework

After the first receipts, identify only the largest verified operational
failures.

For each proposed improvement:
- create a governed improvement candidate
- immutable hypothesis
- training boundary
- evaluation boundary
- success metric
- harm metric
- holdout budget
- rollback procedure
- separate proposer/evaluator/approver

Do not bypass migration 155.

## Phase D — PAPER binding only after proof

Examples of valid later proposals:
- automatically SHADOW an agent whose lower-bound value-add becomes negative
- quarantine a regime with repeated calibration failure
- allocate only among positive-capacity strategies
- surface the daily revenue-readiness brief in Command
- require positive counterfactual value-add before an agent earns LICENSED

But these become production/PAPER behavior only through the existing approval
and capital-critical path.

## Non-negotiable safety

- SMALL LIVE remains SHADOW
- historical PAPER is immutable
- no new order authority
- no new cancel authority
- no cap/limit increases
- no new credential access
- no weakening freshness/settlement/risk/profitability gates
- lifecycle remains an additional gate, never a grant
- CASH remains valid and preferred when positive economics are unproven

## Next acceptable checkpoint

Return only implementation proof:

1. NEW research SHA
2. package tests/pass count
3. real-data adapter tests/pass count
4. current-system contract validation
5. agent certification receipt
6. CASH-vs-challenger tournament receipt
7. regime abstention receipt
8. reliability/capacity/correlation receipt
9. counterfactual agent value-add receipt
10. daily revenue-readiness receipt
11. exact recommended Audrey improvement candidates, if any
12. explicit statement that no authority or live capital was changed

Do not tune thresholds simply to produce LICENSED agents or a positive capital
plan. Truth first.
