# THE POLICY-IMPROVEMENT LOOP

`backend/tools/desk_experiments.py` · artifacts under
`research/beta48/learning/experiments_<stamp>/report.json`

---

## 1. THE TRAINING ENVIRONMENT, STATED BEFORE ANY RESULT

Everything below is a property of the data, not a choice I made about it.

| | |
|---|---|
| **Source data** | `trades`, keyed on `whale_id` (`backend/migrations/001_init.sql:56`) |
| **Event class** | `SINGLE_ACCOUNT_EXECUTIONS`. Every row is **one tracked account's own fill**. It is **not** a market tape. |
| **Venue** | `POLYMARKET_GLOBAL_POLYGON`. The ingest listener uses `polygon_ws_url` / `polygon_http_url` / `PM_EXCHANGE_V3_ADDRESSES`, and stamps `ts_provenance="polygon_block_timestamp"`. |
| **Fee schedule applied** | `PMUS`. **A different venue.** The combination is labelled `TRANSFERRED_SCENARIO` on every artifact and is not same-venue execution evidence. |
| **Corpus** | 1,006 conditions, 19,890 prints, from `extract_ferrari_20260923T1308Z.json` |
| **Settlement** | from `fairvalue_ferrari_20260923T1325Z.json`. Legs with no settlement record are **unresolved**, never assumed to have paid zero. |
| **Learned component** | frozen isotonic curve `curve_frozen_20260923T1325Z.json`, consulted and never refitted inside the loop |

### What is missing, named rather than assumed

- **No contemporaneous order book.** We have prints, not quotes. Depth,
  spread and queue position at decision time are all `NOT_IDENTIFIED`.
- **`P_FILL` is `NOT_IDENTIFIED` and stays that way.** A print through
  our price is *evidence an execution happened there*, not proof ours
  would have been the one filled. Ferrari's fills are not proof our
  orders would fill.
- **Market context** (news, correlated markets, the rest of the book)
  is absent entirely.
- **Selection.** These are the prices *one account chose to trade*. "The
  venue misprices X" and "this account overpays for X" fit this data
  identically.

### Execution assumptions, declared

`PRINT_THROUGH_WITH_QUEUE_SHARE_V1`. A resting order fills only when an
**observed execution** passes through its price, and takes at most
`queue_share` of that print. Because `P_FILL` is unidentified, every
comparison runs across a **declared scenario grid** —
`queue_share ∈ {0.10, 0.25, 0.50}` — and a candidate must win under
**all three**. Winning at 0.50 and losing at 0.10 is a liquidity
assumption, not an edge. A consumption ledger keyed on evidence id
prevents the same printed volume filling two hypothetical orders.

### Partitions

Chronological **by a market's first event**, and a condition lands
**entirely** in one partition — so a position's entry and its outcome are
never split, which is the overlapping-outcome leak handled explicitly.

| | conditions | role |
|---|---|---|
| TRAIN | 603 | development. Inspected. Candidates proposed against it. |
| VALIDATION | 201 | selection. Used once per cycle. |
| HELD_BACK | 202 | **not used, and not a substitute for fresh data** |
| **FINAL** | — | **the live shadow lane, forward of the freeze instant** |

FINAL is not a slice of history *because there is no uninspected
history left*. The loss decomposition was built from this corpus and
these candidates were proposed from that decomposition. A "hold-out"
carved from data already read is not uninspected, and treating it as
though it were is the easiest way to manufacture a positive result.

---

## 2. CYCLE 1 — RESULT: **NO CANDIDATE QUALIFIED**

Gate **V1**, declared before any candidate ran. 5 variants tried, all
rejected. **The active policy stays frozen and unchanged.**

| candidate | targets (measured loss line) | TRAIN | VALIDATION |
|---|---|---|---|
| C1_NARROW_BAND `[0.45,0.55]` | SETTLEMENT −$1,358.88 | accept | reject — worse drawdown |
| C2_REQUIRE_CLEARANCE `0.03` | EXIT_VS_BASIS −$921.16 | reject — worse drawdown | accept |
| C3_PATIENT_EXIT `0.06` | EXIT_VS_BASIS | reject — 0/3 scenarios | reject — 0/3 scenarios |
| C4_SMALLER_CLIP `$120` | STRANDED_CAPITAL $24,530 | accept | reject — worse drawdown |
| C5_SHORTER_REST `300s` | TURNOVER, 782 expiries | reject — worse drawdown | reject — 2/3 scenarios |

No candidate passed **both** partitions. Nothing is promoted.

### 2a. The two findings that matter more than the verdicts

**(i) Realized P&L responds to `queue_share` in OPPOSITE DIRECTIONS in
the two partitions.** Same policy, same engine, same execution model:

| baseline, realized P&L | qs 0.10 | qs 0.25 | qs 0.50 |
|---|---|---|---|
| TRAIN | −$544 | −$969 | −$1,769 |
| VALIDATION | +$41 | +$377 | +$974 |

On TRAIN, filling more loses more. On VALIDATION, filling more earns
more. The sign of the response to the single execution assumption we
cannot identify **flips between partitions**. That is the strongest
result in the run, and it says there is no stable edge here yet. It also
means the VALIDATION figure "+$974 at qs=0.50" must not be quoted as a
profitable result — it is the half of a sign flip that happens to be
positive.

**(ii) The gate's own selection statistic could not do its job.** V1
ranked on the **zero-marked lower terminal bound**. Three measured
defects:

- **No resolving power.** `lower = realized − unresolved_cost`, and the
  bound spans **~$34,000 on $100,000** of cash while the realized
  figures it is meant to separate are **±$1,000**. The instrument is
  9–34× wider than the effect.
- **A degenerate objective.** Minimising it means holding no inventory,
  so its optimum is *trade nothing*. `MIN_DECIDED = 50` was meant to
  stop that and **did not bind** — every candidate decided 300–1,800
  orders. Both of V1's TRAIN accepts, C1 and C4, are **dose
  reductions**: a narrower band and a smaller clip. Less of a losing
  strategy loses less; that is arithmetic, not an edge.
- **It inverted the sign.** V1 scored baseline VALIDATION *worse* at
  qs=0.50 (−$4,435 vs −$3,315) while its realized P&L **rose** (+$974 vs
  +$41). Marking open inventory at zero made more trading look worse
  when more trading had earned more.

### 2b. Three marks, so the width is visible

Because `cash + inventory_cost − realized == starting_cash`, the three
terminal marks are all derivable from one run:

| mark on unresolved legs | TRAIN qs 0.25 | VALIDATION qs 0.25 |
|---|---|---|
| **zero** (lower bound) | −$16,558 | −$4,023 |
| **cost** (= realized) | **−$969** | **+$377** |
| **one** (upper bound) | +$17,976 | +$5,544 |

24,542 unresolved shares at a mean cost of $0.44 each. Reporting only
the lower bound would have been a $16,558 "loss" that is an artifact of
marking open inventory at total loss.

---

## 3. GATE V2 — DECLARED HERE, BEFORE CYCLE 2 RUNS

V2 ranks on the **cost-marked** terminal (= `realized`) and keeps the
bounds as a **required disclosure** rather than the selector. Then it
closes the three holes that opens.

| | criterion |
|---|---|
| **G1** | activity floor **and** turnover ≥ 50% of the baseline's — a dose reduction cannot win by shrinking |
| **G2** | improves cost-marked P&L in **every** scenario |
| **G3** | unresolved cost not >10% above baseline — P&L may not be flattered by parking capital |
| **G4** | the margin must **exceed** the change in terminal-bound width, else `NOT_RESOLVED` |
| **G5** | drawdown and peak committed capital not worse by >10% |
| **G6** | the ledger identity reconciles in every scenario |
| **G7** | `d(realized)/d(queue_share)` has the **same sign** on TRAIN and VALIDATION |

**G7 is the one criterion cycle 1 could not have had**, because it tests
the property cycle 1 is what discovered. It is applied to the
**baseline too** — if the frozen policy is itself unstable, that is the
finding, and testing only the challengers would hide it.

**V2 is not reapplied to cycle 1.** Cycle 1's verdicts stand as V1
produced them. Rescoring a finished cycle under a gate written after
seeing its results is exactly the tuning this loop exists to prevent.

---

## 4. CYCLE 2 — RESULT: **NO CANDIDATE QUALIFIED**

Same five variants, same opportunity set, same capital, same scenario
grid. Gate V2, declared in commit `2e23798` **before** this cycle ran.

| candidate | TRAIN verdict | VALIDATION verdict |
|---|---|---|
| C1_NARROW_BAND | reject — `NOT_RESOLVED`: $381 improvement vs $6,071 bound-width change | reject — dose reduction, turnover 43% of baseline |
| C2_REQUIRE_CLEARANCE | reject — `NOT_RESOLVED`: $191 vs $968 | reject — `NOT_RESOLVED`: $3 vs $41 |
| C3_PATIENT_EXIT | reject — capital parked: unresolved $17,241 vs $15,589 | reject — improves 0/3 scenarios |
| C4_SMALLER_CLIP | reject — `NOT_RESOLVED`: $237 vs $8,739 | reject — improves 2/3 scenarios |
| C5_SHORTER_REST | reject — improves 0/3 scenarios | reject — improves 0/3 scenarios |

### G7 applies to the baseline, and the baseline fails it

| policy | TRAIN slope | VALIDATION slope | |
|---|---|---|---|
| **BASELINE** | negative | positive | **UNSTABLE** |
| C1 … C5 | negative | positive | **UNSTABLE** |

`d(realized)/d(queue_share)` flips sign between partitions for **every
policy tested, including the frozen active one.**

### What cycle 2 establishes

1. **No promotion.** The active policy stays frozen. Two cycles, ten
   variant-evaluations, zero acceptances.
2. **The dominant rejection reason changed** from "worse drawdown"
   (V1, a noise-scale comparison) to **`NOT_RESOLVED`** (V2) — the
   candidates' effects are genuinely smaller than the measurement
   uncertainty. That is a sharper and more useful statement.
3. **C1 and C4's apparent wins were dose reductions**, and V2 names them
   as such rather than accepting them.
4. **The instability is a property of the corpus, not of the
   challengers.** It is not fixable by another entry/exit/sizing
   variant.

### Therefore the next justified experiment is not another policy variant

Five more knob-turns would produce five more `NOT_RESOLVED` verdicts.
The binding constraint is **measurement**, so the next cycle targets
uncertainty rather than P&L:

- **Resolve more inventory.** 24,542 unresolved shares carry the whole
  bound width. Extending settlement coverage narrows the instrument
  directly, and costs nothing in trading risk.
- **Reduce the fill-assumption dependence.** The sign flip is a
  response to `queue_share` — the one thing we cannot identify. A
  contemporaneous book is what would remove it, and until then no
  variant's result can be stable.
- **Accrue the FINAL set.** The live shadow lane is now writing
  prospective decisions forward of the freeze instant (§5). That is the
  only genuinely uninspected evidence that will exist.

---

## 5. THE LIVE LANE IS WRITING — the FINAL set has started accruing

Verified `2026-09-23T14:41:49Z` against the production database, six
minutes after the loop came up:

| table | rows | newest |
|---|---|---|
| `bettor_desk_decisions` | 375 | 9 s old |
| `bettor_desk_orders` | 35 | 6 s old |
| `bettor_desk_positions` | 20 | 6 s old |
| `bettor_desk_ledger` | 18 | 6 s old |

Decisions: `NO_TRADE` 133 · `HOLD` 121 · `FILLED` 86 · `ENTER` 24 ·
`COMPLETE_PAIR` 11. Orders: `RESTING` 14 · `PARTIALLY_FILLED` 16 ·
`FILLED` 5. Cursor `221,451,008` and advancing.

**The ledger identity holds on every snapshot** (`invariant_ok = t`,
18 of 18): cash $98,850.22 + inventory $1,149.78 − realized $0.00 =
$100,000.00 exactly.

This closes the chain the audit found broken — live market data →
independent selection → risk check → resting shadow order → simulated
execution → inventory → reconciled ledger — all of it persistent and
restart-safe. These prospective decisions are recorded **before** their
outcomes, which is what makes them usable as FINAL evidence later.

They are **not yet evidence of anything**: six minutes, realized $0.00,
and no position has settled. Reporting them as a result would be the
error this document exists to prevent.

---

## 6. STANDING CONSTRAINTS

- **The learner never raises its own risk limits.** Candidate params are
  checked against the policy's declared knobs and a mistyped knob is a
  hard refusal, not a silently unchanged policy.
- `P_FILL` stays `NOT_IDENTIFIED`. No candidate may qualify on a fill
  assumption.
- **Funded trading disabled.** There is no venue client in this module's
  import graph.
- The observation collector is untouched.
- Inactivity is reported, never rewarded.
