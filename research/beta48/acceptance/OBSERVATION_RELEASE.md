# THE ISOLATED OBSERVATION RELEASE

Branch **`claude/bettor-observation-release`**, based on **`3349219`**.
Decision-only. No orders, no capital, no credential moved, no
accounting record touched, `mirror_live=false` unchanged.

> **READ `ACTIVATION.md` §0 FIRST.** This document describes the
> release as an isolated diff against `3349219`, and that is still
> what it is — but `3349219` is **not** what production runs.
> Measured 2026-09-21 through `render-ops action=deploys`:
> `sportsassets-workers` is live on `0442d57`, the tip of
> `claude/session-njaewf`, and every SportsAssets service tracks that
> branch with `autoDeploy=yes`. The isolation here is real and still
> the point; the **deployment** is a merge onto the live branch, whose
> marginal diff is 24 files, and the prepared merge is
> `claude/bettor-observation-activation` (`e293344`).

---

## 1. WHAT IS IN THE RELEASE, AND WHAT IS NOT

```
git diff --stat 3349219..claude/bettor-observation-release
```

**One existing production file is modified. Everything else is new.**

| file | Δ | why |
|---|---|---|
| **`workers/all.py`** | **+25 / −6** | **the registration**: one import line, one `LOOPS` entry, and the comment block that says what the loop is and how to stop it |
| `render.yaml` | +34 | **comment only.** The exact `disk:` block and its consequences, written down and *not applied* — §3 |

Everything else in the diff is a file that does not exist at
`3349219`: eleven worker modules, one storage module, one measurement
module, one migration, nine test files, four scripts and the data
fixtures.

### What is NOT here

**The execution gate.** `execution_gate.py`, the `pmus.py`
authorization calls, the `live_executor.py` lane gating and the
`api/app.py` binding are all absent. They stay on the development
branch as their own release, on their own evidence.

### The dependency question, answered precisely

The review asked: *if the observation worker genuinely depends on one
of those production changes, identify that dependency precisely.*

**It does not.** The worker's only reach into `pmus` is
`pmus._get_client()`, inside `_discover`, to list markets. It never
calls `submit_fok` and never calls `close_position`, which are the
only two functions the gate change touches.

Asserted three ways rather than argued:

1. **`test_its_import_closure_reaches_no_order_path`** walks the
   worker's top-level import graph and fails if `execution_gate`,
   `pmus` or `live_executor` appears in it. None does — `pmus` is a
   deferred, function-level import.
2. **`test_no_order_call_is_reachable`** parses the module and fails on
   any call named `submit_fok`, `close_position`, `submit`,
   `place_order`, `cancel_order` or `authorize`.
3. **The branch runs green on `3349219`'s unmodified `pmus.py`.** The
   268 tests that passed on the 68-commit branch pass here with no
   production file changed but the registration.

## 2. DURABILITY

### The defect

`/var/tmp/bettor` survived a process restart and died on every
redeploy. Every write was `flush()`ed and `fsync()`ed and every ledger
save was an atomic `os.replace`. **None of that makes ephemeral
storage persistent** — `/var/tmp` is part of the container's writable
layer, and a redeploy replaces the container.

| event | `/var/tmp` | Postgres |
|---|---|---|
| process restart | survives | survives |
| service restart | **dies** | survives |
| **redeploy** | **dies** | **survives** |

### What was implemented

`bettor_live_store`, with three backends and a chooser that **refuses**
rather than degrading:

| `BETTOR_LIVE_STATE` | result |
|---|---|
| unset / `postgres` | **the default.** `sportsassets-db`, which the target service already holds a connection string for |
| `file` | **REFUSED** unless `BETTOR_LIVE_STATE_DISK=1` declares the path a mounted disk |
| `memory` | **REFUSED** unless `BETTOR_LIVE_ALLOW_EPHEMERAL=1` chooses it deliberately |

If no durable store can be had, `main()` returns
`{"started": false, "why": "NO_DURABLE_STORE"}` and the loop does not
run. A run whose evidence dies on the next deploy produced nothing.

### Three new tables, and only those

```
bettor_live_journal   append-only decision and settlement records
bettor_live_cursor    one row per market: dedup position AND the
                      settlement schedule
bettor_live_ledger    one row per lane: the shadow ledger snapshot
```

**Lane is part of every primary key** — `polymarket-us/institutional/
decision-only` — so retail/institutional and preprod/production
blending is a constraint violation rather than a matter of remembering
a `WHERE`.

No accounting table is written. `TestNoOtherTableIsTouched` parses the
store's source, extracts the SQL string literals (skipping docstrings,
which name the forbidden tables on purpose) and fails if any table
outside the three appears.

**The worker creates its own schema.** The workers never run
migrations — `start.sh` applies them and that is the *API's*
entrypoint, so a worker release that waited for an API deploy would not
be isolated. `migrations/093_bettor_live_observation.sql` carries the
same DDL for the canonical record, and
`test_the_migration_holds_the_same_ddl_character_for_character` fails
the build if they drift.

### Bounds

| what | bound | mechanism |
|---|---|---|
| journal rows | `BETTOR_LIVE_JOURNAL_MAX_ROWS` (250,000) | pruned oldest-first every 300 s |
| cursor rows | `BETTOR_LIVE_CURSOR_RETENTION_DAYS` (7) | pruned by age |
| cursors in memory | 5,000 | terminal-first eviction; evicting a non-terminal cursor is **counted by name** |
| outbox | 20,000 records | overflow counted as `record_dropped_outbox_full` |
| **recovery time** | **O(cursors), not O(journal)** | recovery reads the cursor table and one ledger row; **the journal is never replayed** |
| write loss, flushes succeeding | one flush interval (2 s) | batched outbox |
| write loss, **SIGTERM** | **whatever is uncommitted at that instant** | see below |
| write loss, **database outage** | **grows until the outbox overflows, then records DROP** | counted, and the window is marked incomplete |

### The guarantee, stated accurately

"At most two seconds" assumed every flush succeeds. It does not hold
during an outage, and `durability_report()` says so rather than
implying otherwise. It reports **oldest uncommitted record age**,
**uncommitted count**, **flush failures**, **records dropped**,
**seconds since the last commit**, and marks measurement windows
`windows_incomplete` from the first drop to the last — because a rate
computed over a window with an unrecorded gap is wrong in a direction
nobody can see.

**A lost acknowledgment cannot inflate the count.** A commit can
succeed and its acknowledgment be lost; a dropped connection after
`COMMIT` looks exactly like a failed write. Every record carries a
**content-derived key** and the insert is `ON CONFLICT (lane,
record_key) DO NOTHING`, so the retry is a no-op. Demonstrated against
a real server: replaying a 120-record batch leaves **120 rows, not
240**.

**On SIGTERM the final flush does NOT run.** Measured, not assumed:
`workers/all.py` installs no SIGTERM handler, and Python's default
disposition terminates the process without running `finally` blocks
(verified in this container — exit 143, the `finally` never ran). A
Render restart or redeploy arrives as SIGTERM, so **whatever is
uncommitted at that instant is lost.**

**That is not "two seconds".** Two seconds is the healthy case, where
the previous flush succeeded. The actual bound is
`uncommitted_records` and `oldest_uncommitted_age_s` in the last
report before the restart — under a database outage those grow until
the outbox overflows, at which point records are DROPPED and the
window is marked incomplete. Read the number; do not assume the
interval.

The `finally` covers **cancellation** and the kill-switch exit, not
SIGTERM. The one-line remedy — a SIGTERM handler in `all.py` —
changes shutdown behaviour for all 25 registered loops and is **not**
in this release.

**The schema is verified, not assumed.** `CREATE TABLE IF NOT EXISTS`
is a no-op against a table that already exists with an older shape, so
`PgStore.start()` checks the columns it needs and **refuses to run**
naming the missing ones rather than failing at the first write.

### Demonstrated, not asserted

`scripts/bettor_durability_proof.py`, against a **real PostgreSQL
server**:

```
1. a worker process writes 40 records, 7 cursors and a ledger,
   then kills itself with SIGKILL          -> child signal -9
2. a SECOND process recovers                  journal 40, cursors 7,
                                              ledger cash 1234.5,
                                              m000 still RESOLVED
3. it refuses the observation it already      duplicate_observation_refused 1
   decided, and decides a newer one           decided 1, NO_TRADE
4. THE SAME RUN on a file store, with the
   directory deleted between the phases:      restart recovers 7 cursors
                                              REDEPLOY recovers 0
5. journal 5,040 rows -> pruned to 1,000; recovery over 5,040 rows
   took 0.0023 s
```

Phase 4 is the point. Both writes were fsynced. `fsync` guarantees the
bytes reached the device; it guarantees nothing about whether the
device is attached to the next container.

## 3. THE DISK OPTION, PREPARED AND NOT APPLIED

`render.yaml` now carries the exact block, commented, on
`sportsassets-workers`:

```yaml
disk:
  name: bettor-live-data
  mountPath: /var/bettor-data
  sizeGB: 1
```

with, in the worker's environment:

```
BETTOR_LIVE_STATE=file
BETTOR_LIVE_STATE_DIR=/var/bettor-data
BETTOR_LIVE_STATE_DISK=1
```

**Operational consequences, which is why it is not the default:**

* a Render service with a disk **cannot run more than one instance**,
  and its deploys **stop the old instance before starting the new
  one**. That changes the deploy behaviour of **all 25 loops in this
  process**, not just the new one.
* a disk **cannot be shrunk**, the same permanent commitment the
  database's `diskSizeGB` already carries.
* without `BETTOR_LIVE_STATE_DISK=1` the worker **refuses to start** on
  a file backend, because an undeclared path is the `/var/tmp` mistake
  again.

Postgres needs none of that: no service change, no new credential, no
new dependency, and no change to how the other seventeen loops deploy.

## 4. THE STARTUP PATH, RUN THROUGH THE ENTRY POINT

The old harness reported 16 decisions on books handed straight to the
loop. **The universe rule rejects all 16.** It demonstrated downstream
processing, not discovery and not selection.

`scripts/bettor_startup_path.py` drives `bettor_live_loop.main()` —
the function `workers/all.py` calls — with a paginated listing built
from **250 real venue rows** and a transport replaying **their own
captured books and ladders**:

```
1  PAGINATED DISCOVERY      3 listing calls (100, 100, 50 rows)
                            250 considered -> 18 SELECTED
                            excluded: SPREAD_BELOW_MIN_TICKS 172,
                                      ASK_ABOVE_MAX_PRICE 43,
                                      TRADED_VOLUME_BELOW_MIN 16,
                                      NOT_OPEN 1
2  SUBSCRIPTION             18 subscribed == 18 selected,
                            18 CONFIRMED_BY_DATA, 0 failed
3  EMPTY UNIVERSE           refuses to start, why=EMPTY_UNIVERSE,
                            with the histogram. NOTHING RELAXED.
4  CAPTURED CLOCK UNTOUCHED  18/18 STALE_AT_VENUE_CLOCK, 0 decisions
5  CLOCK REBASED            18 decided, all NO_TRADE, freshness
                            median 1.0011 s, 0 rejected, 0 orders
6  EVIDENCE CLASS           every record REPLAY_DECISION
```

### It found a real defect

`main()` built `leg_of={s: None for s in slugs}`, so **every
observation was rejected as `NO_OUTCOME_IDENTITY`**. The deployed
worker would have run for an hour producing nothing but refusals about
its own wiring. Section 5 showed `rejected: {"NO_OUTCOME_IDENTITY":
18}` and `by_action: {}`.

Fixed by carrying the outcome leg from the listing row through
`assess`/`select` into `main()`. **The selection rule is unchanged** —
the leg is carried, never filtered on; adding a leg test would be a new
rule under an old version. `selected_without_outcome_leg` is reported
at startup so the case is visible before the loop runs rather than
inferred from a refusal histogram an hour later.

### And a labelling hole

`decide_slug` hard-coded `evidence_class = PROSPECTIVE_SHADOW`, so a
replayed book would have been journalled as live observation. The
evidence class now belongs to the **transport**:
`ms.MarketStream.evidence_class` is `PROSPECTIVE_SHADOW`; a replaying
transport overrides it to `REPLAY_DECISION`.

## 5. SETTLEMENT SCHEDULING — CORRECTED TWICE

**First defect.** Reading "the 25 oldest observed" cannot make
progress: a contract that never resolves is permanently the oldest.

**Second and third defects, found in review of the first fix**, and
both worse than what they replaced:

* the first fix **retired `RESOLVED_DERIVED` as terminal**, so outcome
  collection closed on an *inference from converged prices* and the
  venue's own settlement endpoint could never confirm or contradict
  it;
* it advanced **one attempt counter on every result, `PENDING`
  included**, and abandoned the contract at twelve — but a market may
  legitimately stay open far longer than twelve checks.

### What it does now

| status | terminal? | schedule |
|---|---|---|
| `NULL` (never attempted) | no | **sorts first**, always |
| `PENDING` | **no** | event timing where the venue gives one (`event_at + 300 s`), else capped backoff 600 s → 6 h. **Never retires.** |
| `RESOLVED_DERIVED` | **no** | preserved in `settle_derived_outcome` / `settle_derived_at`, **never** in the authoritative `settle_outcome`; re-checked on its own cadence, 900 s → 6 h, **forever** |
| `UNREADABLE` / `UNMATCHED` | no | failure backoff 600 s → 6 h |
| `READ_FAILURE_ESCALATED` | **no** | after 6 **consecutive** failures: visible, still queued, longest interval, **back of the queue** |
| **`RESOLVED`** | **YES** | outcome collection complete. Nothing else is terminal. |

**Two counters, not one.** `settle_attempts` records every read;
`settle_failures` counts **consecutive** failed reads and is cleared by
any successful one. A `PENDING` answer is a successful read.

**There is no `ABANDONED` status any more**, and a test asserts the
constant does not exist.

### Retention and the cache

* The seven-day prune deletes **only `RESOLVED`** cursors. It
  previously ignored settlement status, which deleted exactly the
  markets that had been open longest — the obligations retention
  exists for. It reports `cursors_retained_unresolved`.
* The in-memory cursor map is a **cache**. The queue is read from the
  store (`store.due_for_settlement`), so an eviction is a cache miss,
  not lost work — and an **unflushed** cursor is never evicted,
  because that would lose the update rather than the cache entry.
* Found while fixing that: **`FileStore.flush` wrote the cursors it
  was handed as the whole file**, so every flush of a dirty subset
  deleted every contract that had not changed in that batch.

Pinned by test: a pending population cannot hold the batch; a newly
observed contract is read next pass; every contract is attempted over
64 scheduler passes; a hundred consecutive `PENDING` reads retire
nothing; a derived outcome stays queued and never writes the
authoritative field; an escalated contract stays queued but sorts last;
the queue survives an emptied cache; and a one-cursor flush does not
delete the others.

## 6. TWO MORE DEFECTS FIXED ALONG THE WAY

**Blocking I/O on the shared event loop.** `_record` opened the
journal and `fsync`ed it *inline inside `decide_slug`*, which `main()`
calls directly — on the event loop 24 sibling worker loops share.
The previous docstring claimed it ran in a thread. It did not. Records
now go to a bounded outbox and are flushed in batches;
`test_deciding_does_no_io_at_all` asserts the journal is still empty
after a decision.

**The shutdown flush was the batch most likely to be lost.**
`workers/all.py` stops a loop by cancelling it, and a `finally` that
awaits inside an already-cancelled task has its first `await` re-raise
`CancelledError`. `_final_flush` uncancels for the duration of the
flush; the `CancelledError` that brought us there still propagates.
`test_cancellation_still_writes_the_last_batch` pins it.

## 7. WHAT PUBLIC OBSERVATION CAN MEASURE — AND THE CAPTURE CANNOT

The review is right that these are not fill-conditioned and should not
wait on a funded pilot:

| needs our order | needs only public observation |
|---|---|
| `p_fill` | market activity |
| queue position | hypothetical quote markout |
| realized inventory duration | opportunity persistence |
| realized P&L | spread and depth at the touch |
| adverse selection | counter-differenced volume |

`bettor_market_measures` implements the right-hand column, with
evidence classes on every result.

**And then measured that the research capture cannot feed any of them**
(`research/bettor_counter_truth.sql`, run
[35646334184](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35646334184)):

| | |
|---|---|
| markets with a numeric traded-volume counter | **620** |
| observations of them, over 24.24 h | **623** |
| **markets observed EXACTLY ONCE** | **617** |
| **consecutive observation pairs in the whole table** | **3** |
| their summed separation | **0.9 seconds** |

The capture is a **coverage sampler, not a time series**. It answers
"what did the venue look like across many markets at one moment" and
cannot answer "what did this market do next". Every measure above
needs the same market observed more than once.

`scripts/bettor_market_measures_run.py` runs all three and reports
`NOT_IDENTIFIED` with the reason for each — never zero, because a zero
would read as "the markets do not trade" when it means "we did not look
twice". Its section 5 runs the same measures over a journal **the
worker itself produced**: 108 records, 18 markets, 90 consecutive
pairs, and activity becomes `PUBLIC_COUNTER_DELTA`. Markout and
persistence still say `NOT_IDENTIFIED` there, for reasons that are
properties of that short replay and are stated.

**The markout is reported as a decomposition**, because the single
number is easy to misread:

```
markout = half_spread + drift
```

A quote sits half a spread from the mid, so **both sides show a
positive markout in a drifting market**. Only `drift` is the adverse
component. `mean_drift_usd_per_share` is reported beside the markout
for exactly that reason, and the result is never labelled adverse
selection — that is this quantity *conditional on being filled*, and
that conditioning needs an order of ours.

## 8. THE DAILY CAPACITY FIGURE IS WITHDRAWN

**$396,361/day — and its refreshed value $400,660 — is withdrawn. No
replacement is asserted.**

The derivation was `sum over markets of max(stats_shares_traded) ×
max(mid)`. Three independent reasons it is not a day's volume:

1. **the counter is never differenced**, so its level measures the
   market's life, not any window;
2. **617 of 620 markets were observed once**, so it cannot be
   differenced even in principle on this data — the entire table holds
   three consecutive pairs totalling 0.9 seconds;
3. **shares were priced at `max(mid)`**, not at the price prevailing
   when they traded, so the dollar figure has no consistent unit basis.

**It is not an upper bound either.** A lifetime counter can exceed a
day, and a `max()` over mids can sit on either side of the traded
price. The earlier correction called it "an upper bound on one day's
activity"; that was still too strong.

A daily figure will come from **counter differences over an established
interval**, which is what the worker's journal produces — every
decision record now carries `stats_shares_traded` for that reason.

## 9. PREPRODUCTION ACCESS IS UNVERIFIED, NOT PROVEN

Corrected. `pmx-preprod` run 24 succeeded on **2026-09-10**; runs 25
and 26 fail at `SECRET_MISSING: PMX_PREPROD_PRIVATE_KEY_B64`.

That establishes **historical** access on 2026-09-10 and nothing about
today. **Current access is UNVERIFIED** and stays so until an
authentication succeeds. No credential was recovered from a log and
none was moved into CI.

## 10. VERIFICATION AT THIS SHA

| | |
|---|---|
| `pytest` (9 affected files) | **342 passed** with a real PostgreSQL; **336 passed / 6 skipped** without one |
| `scripts/bettor_durability_proof.py` | exit 0 — real server, real SIGKILL, real second process |
| `scripts/bettor_startup_path.py` | exit 0 — discovery → selection → subscription → decisions |
| `scripts/bettor_market_measures_run.py` | exit 0 |
| `scripts/bettor_live_harness.py` | exit 0 |

The six Postgres tests are **skipped** when `BETTOR_TEST_PG_DSN` names
no server, which is the case in this repository's CI. That is why
`bettor_durability_proof.py` exists: it runs the same code against a
real server and kills a process between the write and the read.

### Pre-existing failures this release does not cause

Six test files assert that the newest file in `backend/migrations/` is
`064_`. It has been `092_` for some time, so **all six already fail at
`3349219`** — run there in a clean worktree to be sure:

| file | at `3349219` | on this branch |
|---|---|---|
| `test_c10_nfl_spreads` | 1 failed, 31 passed | 1 failed, 31 passed |
| `test_e12_flow_only` | 1 failed, 29 passed | 1 failed, 29 passed |
| `test_e12b_witness` | 1 failed, 28 passed | 1 failed, 28 passed |
| `test_e18_rest_life` | 1 failed, 20 passed | 1 failed, 20 passed |
| `test_e5_frozen_exits` | 1 failed, 30 passed | 1 failed, 30 passed |
| `test_fill_t2_record` | 1 failed, 16 passed | 1 failed, 16 passed |

Adding `093` changes the value in the assertion message and nothing
else. **They are not repaired here**: they belong to other lanes and
re-pinning them is an unrelated change.

The three test files that import `workers/all.py` — where the
registration lives — pass: `test_workers_survive_a_dead_db`,
`test_obs_safety`, `test_obs_config_independence`, **59 passed**.

## 11. THE ACTIVATION DECISION

One decision: **merge this branch and deploy `sportsassets-workers`.**

### Before

| | |
|---|---|
| credentials | `PMUS_KEY_ID` (36) and `PMUS_SECRET_KEY` (88) are present on `sportsassets-workers` itself. Names and lengths only; nothing read, nothing moved. |
| database | `sportsassets-db` is already attached. The worker creates its own three tables on start. |
| service change | **none** |
| API deploy | **not required** — migration 093 is the canonical record, not a prerequisite |

### Configuration

| variable | default | effect |
|---|---|---|
| `BETTOR_LIVE_LOOP` | `on` | `off` stops the loop — **but it is read from the process's own environment, so Render applies it by RESTARTING the service.** See ACTIVATION.md §4. |
| `BETTOR_LIVE_STATE` | `postgres` | `file` needs `BETTOR_LIVE_STATE_DISK=1`; `memory` needs `BETTOR_LIVE_ALLOW_EPHEMERAL=1` |
| `BETTOR_LIVE_MAX_CONTRACTS` | **`0`** | sizes a **simulated** execution only |
| `BETTOR_LIVE_DISCOVERY_PAGES` | `6` | 6 × 500 = 3,000 markets measured; ≤100 subscribed |
| `BETTOR_LIVE_JOURNAL_MAX_ROWS` | `250000` | the disk bound |
| `BETTOR_LIVE_CURSOR_RETENTION_DAYS` | `7` | cursor age bound |

### Resource footprint

| | |
|---|---|
| threads | **+1** (the socket), daemon, stopped in a `finally` |
| memory | bounded: 2,000-record ring, 5,000-touch ring, 20,000-record outbox, ≤5,000 cursors, ≤100 cached books |
| event loop | no blocking I/O; flushes are batched and awaited |
| venue reads | 1 websocket; 1 listing / 900 s; ≤25 settlement REST calls / 600 s |
| database | one batched INSERT per 2 s, one UPSERT per ledger save per 60 s, one prune per 300 s |
| other workers | unchanged. `all.py` supervises each loop independently; `BOOT_STAGGER_S` shifts each subsequent start by 0.75 s. **No disk, so no change to deploy behaviour.** |

The risk is memory — `sportsassets-workers` was OOM-killed thirteen
times in one evening at 2 GiB — which is why every collection is
bounded and every bound is tested.

### After

1. `bettor_live_loop: store {...}` names the backend and what it
   survives, then `recovered {...}`
2. `N markets subscribed of M considered over P pages`
3. within 60 s: `stream.connected: true`, `subscriptions.confirmed > 0`
4. `by_action` **expected `{"NO_TRADE": ...}`**
5. `durability.records_written` rising, `persist_failures` **0**
6. `freshness.median` — **the first measurement of the live path's
   latency.** Everything reported so far is a replay's.

### Rollback

| severity | action |
|---|---|
| immediate | `BETTOR_LIVE_LOOP=off` — Render restarts the service; the NEW process reads the flag and returns. The old process is SIGTERMed and its `finally` does **not** run. |
| full | revert the `all.py` commit |

**Triggers:** any execution with `MAX_CONTRACTS=0`; `persist_failures`
rising; worker RSS above its pre-deploy band; any sibling loop's
heartbeat lagging; `bettor_live_journal` growing past its cap.

## 12. STILL OUT OF REACH

| # | item | blocked by |
|---|---|---|
| 1 | live decision-only observation | **this merge — one decision** |
| 2 | account identity, activity, resting orders | PMUS secrets absent in CI, *or* #1 |
| 3 | execution lifecycle in preproduction | `PMX_PREPROD_*` unpopulated; current access **UNVERIFIED** |
| 4 | `p_fill`, realized duration, adverse selection | **no BETTOR order has ever rested** — the funded pilot |
| 5 | activity, markout, persistence on live data | #1. The instrument exists; the data does not yet. |
| 6 | does completing a pair release capital | `NOT_IDENTIFIED` on institutional; #2 or preproduction |
| 7 | a daily capacity figure | #1, then counter differences over an established interval |

**#4 is the only genuine circularity.** #5 and #7 were previously
folded into it and are not: they need observation, which is #1.
