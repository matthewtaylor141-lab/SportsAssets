# Acceptance package — 2026-09-21

Branch `claude/session-njaewf`. Every figure below is either a
production measurement with its run id, or a harness result with the
workload stated. Where a thing is unresolved it says so.

**Scope note.** The four R5 repairs are complete as implementation.
Execution safety overall, account reconciliation and collector capacity
remain OPEN. Nothing here is evidence of readiness for capital.

Standing restrictions unchanged and unexamined by this work:
`mirror_live=false`, no new real orders, no new capital authorization,
COMMAND undeployed, no Phase X re-dispatch, no Track A modification.

---

## Decisions

| # | Item | Decision |
|---|---|---|
| R5 | The four authorized repairs | **PASS** (implementation only) |
| 1 | Account reconciliation | **BLOCKED — venue credentials unavailable** |
| 2 | Execution gate | **PASS** (implementation and tests) |
| 3 | Collector capacity | **FAIL — workload infeasible at current budget** |
| 4 | Release discipline | **PASS** |

---

## SHAs and deployments

| SHA | What |
|-----|------|
| `6ec7bc9` | R5 fail-closed: whale-exit default, `active_venue()` in `mirror_exit` |
| `1fbfb26` | R5 bench repair — **deployed 12:07:34Z**, carried `cc5d3e5` as a passenger |
| `cc5d3e5` | Capacity-aware admission V1 — shipped unintentionally |
| `3349219` | Admission V2 measure-only — **deployed 12:18:44Z**, current production |
| `4c20f8c` | render-ops restored (512 KB ceiling) |
| `5a7d7fc` | Release discipline controls |
| `d535109` | Venue reconciler + workflow |
| `ce92ccf` | Execution gate + capacity harness |

Currently deployed on `sportsassets-workers`: **`3349219`**.
Nothing in this package after `3349219` has been deployed.

---

## 1. Account reconciliation — what the ledger says, and what is unresolved

### The ledger's own figures (research-sql run 35597694700, 12:07:25Z)

```
HISTORICAL_REAL_FILLS              52
CURRENT_UNSETTLED_FILLED_ORDERS    52
CURRENT_OPEN_REAL_POSITIONS        47 distinct markets
CURRENT_REAL_GROSS_EXPOSURE_USD    16,180.53
CURRENT_REAL_NET_EXPOSURE_USD      16,180.53   (all BUY)
CURRENT_REAL_CAPITAL_AT_RISK_USD   16,180.53
LAST_REAL_ORDER_SUBMITTED_AT       2026-09-10 17:06:50Z
LAST_REAL_FILL_AT                  2026-09-10 12:07:17Z
```

**$16,180.53 is a sum of acquisition costs over fill records.** It is
not confirmed current exposure and not an account-wide upper bound.

### Two internal facts that contradict it

Run 35598087871:

- 37 of the 52 ($11,349.75) sit on markets our own `markets` table marks
  **closed**; 15 ($4,830.78) have no market row at all.
- `mirror_books` — the reconciler's own view — reports **zero open**:
  1,624 closed, 6 closing, 1 frozen.

A market marked closed is not proof the position settled or that cash
arrived. The two internal ledgers disagree, and neither is venue truth.

### A correction I owe on this item

I earlier flagged "52 filled and 52 unsettled" as suspicious. It is not
a finding: settlement **moves the status** (`settled` 5,271 rows, all
carrying `settled_at` and `pnl`), so `status='filled' AND settled_at IS
NULL` is equivalent to `status='filled'`. The filter measured nothing.

### Venue reconciliation — built, not yet returned

`backend/sportsassets/venue_reconcile.py`, workflow `venue-reconcile`.
Reads venue positions, resting orders, and **account-wide** TRADE and
POSITION_RESOLUTION activity — account-wide because `recent_trades`
pages by market slug and cannot find a position we never recorded.

Joins on `order_id`, summing executions per order (one order can fill
in pieces while the ledger keeps one row; without that every partial
fill reports a false quantity mismatch). Four verdicts kept apart:
`MATCH`, `PRICE_OR_FEE_DIFFERENCE`, `QUANTITY_MISMATCH`,
`NOT_FOUND_AT_VENUE`.

Four valuations, never mixed: acquisition cost; cost basis
(`position_basis`); mark-to-bid at a stated instant; settled proceeds.

**It does not write, and that is checked, not asserted.** 12 tests
AST-walk the module for any order-capable name, for `pool.execute`, for
any SQL verb but SELECT, and for credential tokens. The workflow runs
those tests in a step with **no credentials in its environment**, before
the step that has them.

### STATUS: BLOCKED — and this is the exact blocker

Run 35604948208 (13:20:54Z). The database resolved and connected. The
venue refused:

```
AuthenticationError: API key credentials required for authenticated
endpoints. Provide key_id and secret_key when initializing the client.
```

**`PMUS_KEY_ID` and `PMUS_SECRET_KEY` are not available as repository
secrets.** GitHub substitutes an empty string for a secret that does
not exist, so the SDK client was constructed with no credentials and
refused at the first authenticated call. `calibration-evidence.yml`
references the same two names, but a reference in a workflow file is
not evidence that a secret is set.

**MISSING PERMISSION, precisely:** repository secrets `PMUS_KEY_ID` and
`PMUS_SECRET_KEY` on `matthewtaylor141-lab/SportsAssets`, scoped to
read-only venue endpoints (`portfolio.positions`, `portfolio.activities`,
`orders.list`). No write or order scope is needed and none should be
granted for this.

**What I did not do.** The running `sportsassets-workers` service holds
working venue credentials in its environment. I did not read them, and
I will not: recovering a credential to work around a missing grant is
not a workaround, it is an exfiltration, and the standing instruction
forbids it. The grant is the owner's to make.

**Everything else on this item is finished and waiting on that one
input.** The module, the read-only proof, the workflow, the DSN
resolution and the concurrency reservation are all in place and
exercised — the last run got past install, past the read-only proof,
past the database, and died at the venue handshake. One secret away.

Row-level and account-level reconciliation output is therefore
**absent, not zero**. I have not substituted internal-ledger agreement
for venue confirmation, and no accounting record has been altered.

---

## 2. Execution gate — PASS

### The complete route inventory, derived not remembered

`backend/tests/test_order_route_census.py` walks the AST every run and
fails when the set changes. Hand-written census: 6 routes. Derived: **11
submitting call sites across 5 modules**, plus one the hand-written list
had missed entirely.

| Module | Sites | Routes |
|---|---|---|
| `live_executor.py` | 6 | copy entry, manual buy, manual slug, manual limit, manual sell, whale exit |
| `workers/underdog.py` | 3 | enter, cashout sweep, copy-exit sweep |
| `workers/mirror_live.py` | 2 | S4 probe, reserved place |
| `calibration_adapter.py` | 1 | stored reference (`self._submit = pmus.submit_fok`) |
| `pmus.py` | 1 | the boundary itself |

**THE ROUTE NOBODY HAD LISTED.** `live_executor._submit_fok` builds its
own `py_clob_client` and calls `post_order` directly — a complete second
submission path to **polymarket-clob**, a different venue. Gating the
`pmus` boundary did nothing for it. Found by walking for venue-client
constructors outside the adapter. It is gated now.

The census also follows **stored references**, not just calls: a
call-site walker does not see `calibration_adapter`'s
`self._submit = pmus.submit_fok`.

### The boundary and the controls

Authorization sits in `pmus.submit_fok`, `pmus.close_position` and
`live_executor._submit_fok` — the three functions that reach a venue.

| Control | Scope | Applies to |
|---|---|---|
| `live_trading_paused` | **global** | every route |
| `active_venue` (`LIVE_TRADING_ENABLED` + credentials) | **global** | every route |
| `LIVE_COPY_HALT` | copy | every lane except `manual` |
| `mirror_loss_stop` | copy | every lane except `manual` |
| `copy_overspend_halt` | copy | every lane except `manual` |
| cancellation | **ungated, deliberately** | — |

`GLOBAL_ONLY_LANES` is an **allowlist** (`{"manual"}`). An undeclared
route arrives as `unknown` and gets everything. A blocklist would let
any new caller skip the breakers by saying nothing — absence granting
permission, which is the shape of the original defect.

**Cancellation is not gated**, and a test enforces it. A kill switch
that blocks cancels freezes resting orders in the market at the moment
you most want them gone.

### Fail-closed coverage

| State | Result |
|---|---|
| gate never bound | DENY |
| DB read raised | DENY |
| read timed out | DENY |
| switch value malformed | DENY |
| switch row **absent** | DENY |
| snapshot older than 5s | DENY |

The absent-row case is the original defect: `_is_paused` returned False
on `None`, so the control documented as "no further orders" had never
been armed.

### Authorization cannot be captured before a pause

The gate takes **no token**. `authorize()`'s signature is asserted to
accept nothing meaning "already approved", so queued or retried work has
nothing to present. It reads at the moment of submission, from the
worker thread, through the caller's own event loop. Test:
`test_pausing_between_check_and_submit_blocks_the_submit` authorizes,
pauses, then submits — and the submit fails.

### Behavioural results

`backend/tests/test_execution_gate.py` — **33 tests**, venue replaced by
a recorder that counts calls.

- 6 denying states × 2 boundary functions: **zero venue submissions**,
  preview not sent either.
- authorized submit and close **still work** — without which the suite
  would pass against a gate that refuses everything.
- manual sell stopped by the kill switch.
- copy loss breaker does **not** stop a manual operator.
- same breaker **does** stop the copy lane.
- undeclared lane gets the strictest treatment.
- **no production flag is changed**, asserted by scanning this file.

Census: 12 tests. Total for item 2: **45 passing**.

### Pre-existing environment failures, reported separately

`tests/test_pmus_post_only.py` fails to import and
`tests/test_live_executor_ladder.py` fails 8 tests, both
`ModuleNotFoundError: No module named 'polymarket_us'`. Verified
pre-existing against a baseline worktree at `fc80f95`.

**Do they prevent meaningful validation of a changed path?** Partly, and
I will not gloss it: the ladder tests exercise `maybe_execute`, which is
a gated route. Its gating is covered by the census and by
`test_execution_gate.py` against the boundary, but the ladder's
end-to-end copy path cannot run in this environment and was not
validated end to end.

---

## 3. Collector capacity — FAIL, workload infeasible

### Measured production inputs (research-sql run 35604377344, 12 h, 568 ticks)

| Quantity | Value | Unit |
|---|---|---|
| arrivals | 0.777 | observations/min |
| follow-up tasks created | 3.106 | tasks/min |
| service | 2.435 | reads/min |
| tick gap | 69.03 | s |
| pacing | 2.054 | — |
| follow-up reads per tick | 2.80 | reads/tick |
| backlog | 11.8–17.0 avg, 28 max | tasks |
| drain time | 317–487 | s |

**Oversubscription 1.28×. Sustainable intake 0.609 obs/min against
0.777 arriving.**

### On-time service, unchanged classification, all four horizons

| Horizon | ON_TIME | LATE_RECOVERY | rate |
|---|---|---|---|
| 60s | 145 | 307 | 32.1% |
| 300s | 115 | 317 | 26.6% |
| 900s | 109 | 313 | 25.8% |
| 3600s | 107 | 330 | 24.5% |
| **all** | **476** | **1,267** | **27.3%** |

The rate is uniform across horizons. **Per-horizon starvation is fixed**
— W2's defect is gone. What remains is pure throughput.

### Median lag, and the structural ceiling

| Horizon | p50 lag | p90 | p99 |
|---|---|---|---|
| 60s | 257.4s | 559.6s | 636.2s |
| 300s | 485.8s | 807.4s | 898.1s |
| 900s | 1132.2s | 1411.0s | 1488.4s |
| 3600s | 3879.5s | 4112.1s | 4194.9s |

Tolerance is ±30s. The **60-second** horizon is served a median of 257s
after it comes due.

There is a ceiling independent of capacity: the tolerance window is 60s
wide and the tick period is 69.03s, so at best **86.9%** of due-moments
have a tick inside their window. Observed 27.3%. The gap from 86.9% to
27.3% is queueing.

### The V1 diagnosis — third statement, and this one has the arithmetic

Measured pacing 2.054 → tick budget `int(10/2.054) = 4` → follow-up
reserve 2. V1's sustainable term is `fu_reserve // horizons_per_obs`:

```
2 // 4 == 0
```

| budget | reserve | V1 admits | V3 admits |
|---|---|---|---|
| 10 | 5 | 1 (brake can still zero it) | 1 |
| 6 | 3 | **0 always** | 1 |
| 4 | 2 | **0 always**  ← production | 1 |
| 2 | 1 | **0 always** | 1 |

Integer division floors to zero whenever the reserve drops below the
number of horizons, which under backoff is most of the time. At
production's budget V1 admits nothing on every tick **regardless of
backlog** — the level-versus-flow brake is never even reached.

**I described this failure twice before and got the mechanism wrong
both times**: first as a latching brake, then — after running the
harness at an *assumed* budget of 10 — as a twenty-three-minute
transient. Both accounts were about a budget production rarely has.
Pacing backoff is the normal condition, not the exception.

### The harness, and the two fidelity gaps it needed

`backend/sportsassets/bettor_capacity_harness.py` drives the **real**
policy functions from `bettor_admission.py`.

Two things had to be right before it could see anything:

1. **Backlog must be counted as `mids_outstanding` counts it** — a
   630-second window per horizon, not "due within tolerance". Modelled
   narrowly, V1 saturates 3 ticks of 201 instead of every one.
2. **The queue must be inherited.** From rest, V1 scores 91.5% and looks
   healthy. Production handed it eleven hours of V4 observations whose
   later horizons had not yet come due.

With both, replaying production's exact 7-tick window:

| | production | harness |
|---|---|---|
| observations written | 0 | **0** |
| saturated ticks | 7/7 | **7/7** |

Pinned by `test_the_harness_reproduces_the_v1_blackout`.

### Comparison — identical workload, unchanged classification

Measured inputs (0.777 obs/min, 69.03s tick, budget 6, inherited queue):

| policy | obs | tasks | on_time | late | expired | deferred | rate |
|---|---|---|---|---|---|---|---|
| V4 none | 185 | 824 | 604 | 20 | 133 | 67 | 73.3% |
| V1 | **0** | 84 | 55 | 14 | 15 | 0 | 65.5% |
| V2 measure-only | 185 | 824 | 604 | 20 | 133 | 67 | 73.3% |
| V3 drain-time | 185 | 824 | 604 | 20 | 133 | 67 | 73.3% |

**The harness's absolute on-time rate is optimistic and I am not
reporting it as a production forecast.** Arrivals are phase-aligned to
tick boundaries, so due-moments land conveniently; production's
observations are written at arbitrary offsets within a tick. The
harness's value is the *comparison between policies on one workload*
and the feasibility arithmetic — not the level.

### Accounting is closed

`created == on_time + late + expired + deferred`, asserted every run,
for every policy. Declined observations are reported **beside** the rate
and never inside it: V1 scored 11.1% in production while writing
nothing, and coverage and timing stay two numbers.

### The shortfall, and the feasible choices

At the measured budget the workload **cannot** be served:

- tasks created 3.106/min against 2.435/min served — 1.28× over;
- and even at balance, a 69.03s tick against a ±30s tolerance caps
  on-time at 86.9%, with any queue at all pushing it far below.

Quantified choices, none of which is free:

| Choice | Magnitude | Cost |
|---|---|---|
| admit less | cut intake to 0.609 obs/min (78% of current) | narrower sampling frame; a versioned sampling change |
| read more | raise follow-up reserve ~1.28× | contends with the money path on a shared gateway that 429s |
| fewer horizons | 4 → 3 | loses a declared horizon from the design |
| shorten the tick | 69s → ≤60s | raises request rate; same gateway contention |

**I am not calling this fixed.** V3 is a candidate with one structural
guarantee — `max(1, …)` makes zero admission unreachable at every
budget, backlog and divisor, so neither of V1's faults can stop the
collector again. It has **not** been deployed. V2 measure-only remains
the production baseline as instructed.

---

## 4. Release discipline — PASS

### Workflow size and validation

`scripts/check_workflows.py`. GitHub caps a workflow at 512,000 bytes.
`render-ops.yml` — the lever that operates Render, reads the production
database and holds the kill switch — reached 512,388 and every dispatch
returned `startup_failure`. **Two runs were dispatched at the dead lever
before I checked.** `yaml.safe_load` passed throughout; the file was
valid YAML and always had been.

Three checks: size against the ceiling **and** a 32 KiB headroom
minimum; the structure GitHub requires; `bash -n` over every `run:`
block with `${{ }}` substituted. `commit-guard` also runs **actionlint**.

**Headroom is the control.** The version that "passed" sat at 511,808
bytes — under the limit, green on everything, and 192 bytes from death.
Both halves are pinned: the 512,388-byte file fails, and so does the
511,808-byte one.

Repair: 67 comment blocks moved verbatim into
`.github/workflows/RENDER_OPS_NOTES.md`. 431,772 bytes now, **80,228
free**. Verified equivalent against the last-good commit: 330
non-comment lines before and after, exactly one differing.

### Full-diff inspection

`scripts/deploy_manifest.py <deployed-sha> <proposed-sha>` prints every
commit and changed file between them, grouped by component, with
order-capable, collector and migration paths called out.

Replayed against the incident (`af6ddf1` → `1fbfb26`) it reports what
that approval actually carried: **17 commits, 15 of which asked not to
deploy**, 2 migrations, 4 collector components and the sampling rule —
for what was approved as a two-file safety fix.

It reports; it does not gate. The gate is a person reading it.

---

## Reproduction

```bash
# item 2 — execution gate and route census
cd backend && python -m pytest -q -p no:randomly \
    tests/test_execution_gate.py tests/test_order_route_census.py

# item 3 — policies, regression, accounting
cd backend && python -m pytest -q -p no:randomly \
    tests/test_admission_policies.py

# item 3 — the comparison, at measured production inputs
cd backend && python -m sportsassets.bettor_capacity_harness \
    arrivals_obs_per_min=0.777 tick_period_s=69.03 \
    total_budget_reads=6 minutes=240 inflight_obs_minutes=60

# item 3 — production's exact V1 window
cd backend && python -m sportsassets.bettor_capacity_harness \
    arrivals_obs_per_min=2.1 inflight_obs_minutes=60 minutes=9

# item 4 — workflow guard, and what a deploy would carry
python3 scripts/check_workflows.py
python3 scripts/deploy_manifest.py 3349219 HEAD

# item 1 — venue reconciliation (needs PMUS_* and RENDER_API_KEY secrets)
#   Actions -> venue-reconcile -> Run workflow
```

Evidence locations: GitHub Actions run ids given inline; the
reconciliation uploads `venue-reconciliation-<run_id>` (JSON, stdout,
stderr), 30-day retention. No secret appears in any of it.

---

## What remains open

1. **Venue reconciliation BLOCKED** on repository secrets
   `PMUS_KEY_ID` / `PMUS_SECRET_KEY` (read-only venue scope). Everything
   else on the item is built, tested and exercised; the last run reached
   the venue handshake and stopped there.
2. **`$16,180.53` is unconfirmed** as current exposure. Two internal
   ledgers disagree and neither is venue truth.
3. **The collector workload is infeasible** at the current read budget.
   A capacity decision is required; it is not an engineering fix.
4. **V3 is a candidate, not a deployment.** No admission policy has been
   shipped since V2 measure-only.
5. **`test_pmus_post_only.py` and 8 ladder tests** cannot run here, so
   the end-to-end copy path is not validated in this environment.
