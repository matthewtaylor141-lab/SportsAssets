# The five deliverables, against persisted evidence — 23 September 2026

Authorization: *"complete the integration and deploy the functioning
shadow systems… Report completion only from persisted end-to-end evidence
and the published interface. Name any genuinely missing external
dependency precisely."*

Everything below is read out of production Postgres or the Render deploy
record. Where a deliverable is not complete it says so and names what is
missing, separating **unfinished engineering** from **statistical
validation** from **external dependencies** — three different things that
need three different answers.

---

## 1. RN1-seeded positions under the frozen policy — **RUNNING**

Live commit `84cc665`, API, since 19:35:49Z. Control row `rn1x_shadow =
true` since 19:24:36Z.

| At (UTC) | positions | decisions | orders | fills | outcomes | cursor |
|---|---|---|---|---|---|---|
| 19:23:28 | 0 | 0 | 0 | 0 | 0 | (control row absent) |
| 19:28:47 | 19 | 1,158 | 40 | 4 | 19 | 41 |
| 19:35:58 | — | 1,706 | — | — | — | 145 |
| 19:39:06 | — | 1,937 | — | — | — | 178 |

Decisions by operating state at 19:39:06Z: `ORDER_WORKING 1530`,
`NO_RESIDUAL 271`, `HOLD_NO_FEASIBLE_PAIR 136`. `reconciles = true`.

The policy is `MANAGEMENT_PAIR_091_STOP_16_V1`, frozen before evaluation,
**management-defined and experimental — not learned, not fitted, and not
shown to be profitable.** The frozen arithmetic is unchanged: basis 0.57
still prices the completion at **0.32**, management's confirmed threshold.

**Only the pairing half of the policy is under test.** The second-half
loss exit has never fired and cannot, because no event-progress feed
exists — see blockers.

## 2. Independently selected EV shadow entries — **NOT DELIVERABLE BY
ENGINEERING. The blocker is a measured negative result.**

This is the one deliverable I cannot close by writing code, and the reason
matters more than the status.

The primary lane runs and records: `workers/all.py` →
`workers/shadow_bettor.py` → `shadow_bettor.decide` →
`bettor_ev_bridge.evaluate`. It is registered, it collects, it writes
decisions. Every one is `NO_TRADE`, and the code says exactly why:

```
"actionEvStatus": NOT_IDENTIFIED,
"settlementEvStatus": SETTLEMENT_EV_NOT_IDENTIFIED,
"bestAction": "NO_TRADE",
```

because `bettor_ev_bridge.fair_value` returns the **venue midpoint** and
labels itself:

> *"no model in the zoo beat the venue price out of sample, so it is the
> benchmark. A benchmark is not a belief and cannot be evidence against
> itself"*

**WHAT IS MISSING IS NOT PLUMBING.** A versioned model that beats the
venue price out of sample does not exist — that is a statistical finding
already recorded, not an unwritten function. The two ways to make this
panel show activity are to delete the refusal or to relabel the midpoint
as independent value, and both would be fabricating the result. The
audit named both and so does this document.

`EXECUTION COST` **is** identified for the aggressive actions; settlement
EV is not. Those are different facts and the payload keeps them apart.

**To close it:** a registered model/feature scorer that demonstrably beats
the venue benchmark out of sample, with sizing and risk, through the same
verified persistent order path. Until then the honest output of this lane
is `NO_TRADE`, and it is producing it.

## 3. Durable orders, inventory and accounting — **VERIFIED ON REAL
POSTGRES**

The audit's exact objection was: *"DB recovery tests use the repository's
fake transaction store; they are not a real-Postgres or production restart
acceptance."*

A throwaway cluster was stood up, the migrations applied, and the new
tests drive real rows. 20 of them, including:

- **Rollback.** A failure injected mid-transaction leaves **zero**
  positions and zero orders. Orders without a position is the shape that
  made `acct_fc2d773a2afa4851` ACCOUNTING_UNCERTAIN.
- **Idempotence across a restart.** The cursor is rewound and the same
  evidence re-run; position, decision and order counts are unchanged.
  Keys are *derived* from the experiment id and source trade id, never
  generated.
- **The cursor does not advance past a write that failed.** The saved
  value is read back out of the database, and a mutation that advances it
  makes the test fail — checked.
- **The three clocks** are ordered by the schema's own CHECK, not by a
  promise in a comment.
- **Fail-closed control** on absent / false / malformed / unreadable.

## 4. Continuous learning — **RUNNING. THREE REJECTIONS AND ONE
ELIGIBILITY**

`rn1x_learn` loop, own lock, own control row, own env flag. It writes one
receipt per challenger per cycle into the **existing**
`bettor_learn_model` register — not a new one.

Four declared challengers, each questioning one number, plus
`HOLD_TO_SETTLEMENT` as the null. The gate is `learn_gate.gate_v2`, moved
into the package **unchanged** so the offline tool and the runtime loop
share one implementation; `backend/tools/` is not in the API image, so the
alternative was a second copy that would drift.

**IT RAN, AND THE RECEIPTS ARE IN THE REGISTER.** Read out of
`bettor_learn_model` at 19:42:27Z, all four on dataset `b088f4022d15`,
420 decided orders:

| Ver | Challenger | Verdict | The gate's own reason |
|---|---|---|---|
| 1 | `PAIR_090` | RETAIN_CHAMPION | *NOT ROBUST: improves cost-marked P&L in 0 of 3 scenarios… NOT_RESOLVED: the 0 improvement is smaller than the 0 change in the terminal-value bound width.* |
| 2 | `PAIR_092` | **CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT** | *passed every declared V2 condition* |
| 3 | `STOP_80` | RETAIN_CHAMPION | same as PAIR_090 — and **expected**: with the loss exit unavailable, a different stop fraction changes nothing, so this arm is numerically identical to the champion. |
| 4 | `HOLD_TO_SETTLEMENT` | RETAIN_CHAMPION | *DOSE REDUCTION, NOT AN EDGE: turnover 0 is 0% of the baseline's 63.* |

### On PAIR_092, carefully

A challenger cleared the gate. **This is not a result to act on yet, and
it is not a profitability finding.** What it says precisely: on 19
historical replayed positions, with MODELLED fills licensed by other
people's prints at three declared queue shares, under a TRANSFERRED PMUS
fee scenario, a 0.92 combined-cost ceiling beat the frozen 0.91 ceiling on
cost-marked P&L in all three scenarios without breaching the turnover,
unresolved-capital, drawdown, committed-capital or reconciliation
conditions.

What it does **not** say: that 0.92 is better. Nineteen positions is a
small sample, the fills are modelled, the fees are the wrong venue's, and
`P_FILL` is `NOT_IDENTIFIED`. The economically interesting mechanism —
a looser ceiling completes more pairs at a thinner margin — is exactly the
kind of effect that reverses with more data.

**It changed nothing.** The champion is still
`MANAGEMENT_PAIR_091_STOP_16_V1`, unchanged, and the loop has no code path
that could change it. ELIGIBLE is a recommendation to a human.

### One defect this surfaced, in my own gate plumbing

`gate_v2` zips the champion's scenario rows against the challenger's **by
index**, and `evaluate` was dropping an empty scenario row. An arm
producing nothing at queue_share 0.10 would have had its 0.25 row compared
against the champion's 0.10 — one execution assumption against another,
reported as like-for-like. `recommend` checked *length*, which does not
catch a reordering.

Fixed: an empty scenario is kept as a hole, and `recommend` refuses on
either a queue_share mismatch or any scenario with no rows. A third test
asserts an aligned, genuinely better candidate still reaches ELIGIBLE —
without it, the refusal tests could be satisfied by a gate that can never
pass, which is the same defect facing the other way.

The production verdict above stands as computed: all four arms produced
three rows from the same scenario tuple, so they were aligned.

### Re-evaluated under the fixed code, and the lock handover verified live

`d71cfc1` went live at **19:45:47Z**. The new instance booted, found the
evaluator lock still held by the outgoing instance, and reported
`{"state": "STANDBY_NOT_THE_EVALUATOR"}` at 19:45:45Z — then **took the
lock on its next retry and re-evaluated**, writing versions 5–8 at
19:51:13Z:

| Ver | Challenger | Verdict | Decided |
|---|---|---|---|
| 5 | `PAIR_090` | RETAIN_CHAMPION | 598 |
| 6 | `PAIR_092` | CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT | 598 |
| 7 | `STOP_80` | RETAIN_CHAMPION | 598 |
| 8 | `HOLD_TO_SETTLEMENT` | RETAIN_CHAMPION (*turnover 0 is 0% of the baseline's 129*) | 598 |

**This is the audit's defect 7 demonstrated on a real deploy, not in a
test.** The old standby did `if not acquired: while True: sleep` and could
never take over — the containment incident at 16:41 that I wrongly
attributed to a deploy. Here the standby retried, acquired, and resumed
evaluating. (asyncpg's pooled-connection reset runs
`SELECT pg_advisory_unlock_all()` — connection.py:1748 — so the outgoing
instance's cancelled task genuinely frees the lock.)

### And the loop was polluting the register it writes to

Reviewing what I had left running, `_next_version` always returns `max + 1`,
so the `ON CONFLICT (model_key, version)` clause could never fire and every
cycle appended four more rows to `bettor_learn_model` — the EXISTING shared
register that `render-ops sql learn-inventory` reads. Sixteen an hour.

The first fix (skip when dataset digest AND verdict are unchanged) would
not have helped, and I checked that before claiming it had: while the
experiment is still backfilling, every cycle genuinely sees new positions,
so the digest never matches and four *legitimate* rows land anyway. Those
are worse than duplicates in one respect — real evaluations on marginally
more data, which nothing would flag as noise.

So the substantive gate is `MIN_NEW_POSITIONS = 5`: a verdict resting on
hundreds of decided orders does not change on one more settled position,
and a cycle below the threshold returns `TOO_FEW_NEW_POSITIONS` with both
counts, so "working, not enough new evidence" stays visible. `CYCLE_S` went
900 → 3600 for cost. The gate deliberately does **not** block the first
evaluation — a gate that did would leave the register permanently empty and
the panel permanently blank, which is rigour that produces no evidence.

**THE TWO EVALUATIONS ARE NOT INDEPENDENT REPLICATIONS.** The 598-decided
dataset CONTAINS the 420-decided one; the experiment kept seeding between
the two runs. PAIR_092 clearing twice on nested data is one result
observed at two sample sizes, not two confirmations. Its earlier caveats
stand unchanged.

**IT HAS NO PROMOTION PATH.** `recommend()` returns `RETAIN_CHAMPION` or
`CHALLENGER_ELIGIBLE_PENDING_MANAGEMENT` and there is no third value. The
champion is management-defined and frozen; a loop that could swap it would
be an agent rewriting management policy. A test asserts the source
contains no UPDATE against the register or the policy constants.

What is deliberately **not** built: a runtime model loader with rollback.
There is nothing for it to load, and building one would be building the
mechanism for the thing that is not authorized.

## 5. Command-centre views — **PUBLISHED**

Four routes: `/api/command/rn1x/overview`, `/positions`, `/learning`,
`/trace/{position_id}`. A new tab, `RN1 seeded management`, published to
the Netlify branch with `[skip render]`.

Four sections, each drawn whether or not it has a value: whether the loop
is operating; decisions **by operating state**; results with the learning
verdict; and the blockers — that last panel **unconditional**, because a
screen that hides its blockers once rows arrive is how "the loss exit has
never been available" stops being visible.

---

## The blockers, named precisely

| Blocker | Kind | What is actually missing |
|---|---|---|
| `SECOND_HALF_UNDEFINED` | external dependency | A per-sport event clock with observed, timestamped period boundaries, joined point-in-time to the decision instant, with freshness validation. `SECOND_HALF_MAPPING` is empty **by construction**; the loss exit is unavailable on every position. |
| `NO_CONTEMPORANEOUS_BOOK` | external dependency | An archived same-venue order book (bid, ask, depth) at these instants. Tape prints in `trades` are executions by others, not a standing book, so no executable bid is supplied and a trigger needing one cannot fire. |
| `FEED_POSTDATES_SETTLEMENT` | external dependency | Detection latency low enough to decide before resolution. Measured: 15.07 h lag on the Santos seed, postdating settlement by 11.22 h. No forward lane can be seeded from this feed; the experiment is HISTORICAL_REPLAY by necessity, not by choice. |
| Independent fair value | **statistical validation** | A model that beats the venue price out of sample. Already tested; none did. Not an engineering gap. |
| Same-venue historical fees | external dependency | A verified venue/date fee provider. Replay fees are a labelled TRANSFERRED PMUS-latest scenario, and `fee_basis` says so on every row. |

## What none of this establishes

- **No realised return.** Every fill is MODELLED, licensed by an observed
  print by someone else at a declared queue share. `P_FILL` remains
  `NOT_IDENTIFIED` and nothing here is evidence our order would have
  filled.
- **No profitability claim** for the management policy. It is frozen and
  under test; the first evaluation was ineligible, not favourable.
- **No capital, no orders, no venue traffic.** Both loops are off unless
  an env flag *and* a database control row both say otherwise. The schema
  CHECKs `is_modelled`.
- **Containment is unchanged.** `acct_fc2d773a2afa4851` is still
  `paused = t`, `ACCOUNTING_UNCERTAIN`, and the last desk decision is
  still 16:37:30Z. Nothing in this work reads or writes the desk's tables.

## Two defects of my own, found by checking my own claims

1. **The cursor advanced past a failed write.** I had written the comment
   *"the cursor advances only after the writes committed"* over code that
   saved it regardless, and a test docstring repeating the claim. That is
   the desk's recovery defect in another place, and it loses evidence
   permanently. Fixed, and the test now reads the saved cursor back.
2. **A tautological assertion, again** — `assert armed >= 0` guarding the
   dynamic no-order-path check. With nothing armed, the import proves only
   that the import works.

And one instrument defect: the readback query filtered
`service = 'rn1x_shadow'`, so the learning loop was invisible to the only
tool I was using to check it. I read an empty register three times with no
way to tell whether the loop was evaluating, blocked or dead. It reads
every `rn1x_*` service now — which immediately showed the real answer:
the loop read its control row one second before I set it.
