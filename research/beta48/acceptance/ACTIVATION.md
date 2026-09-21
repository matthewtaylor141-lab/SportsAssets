# THE ACTIVATION REQUEST

| | |
|---|---|
| **destination** | `claude/session-njaewf` @ **`0442d576`** |
| **prepared merge** | `claude/bettor-observation-activation` |
| **prepared rollback** | `claude/bettor-observation-deregister` @ **`ca68bc5f`** |
| release branch | `claude/bettor-observation-release` |

*(A document cannot hold its own hash. Confirm the prepared tip with
`git rev-parse origin/claude/bettor-observation-activation` before
pushing; §1 gives the check that must pass.)*

`OBSERVATION_RELEASE.md` is what is in the release and why. **This is
the action, its preflight, and its acceptance.**

---

## 0. THE OPERATIONAL RECORD, CORRECTED

I reported in every message before the last one that *"nothing from
this branch is running; production remains `3349219`."* **That was
wrong.** It was a read I could not take until `render-ops` was
repaired, and once taken it says the opposite.

### What deployed, and when

`render-ops action=deploys service=sportsassets-workers`
([run 1108](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35659196327))
— **twelve deploys to the worker on 2026-09-21**, every one from
`claude/session-njaewf`:

| window (UTC) | commit | state |
|---|---|---|
| 19:40:45 → 19:41:11 | **`0442d57`** | **live** |
| 19:32:47 → 19:33:21 | `f8ee4eb` | deactivated |
| 19:28:00 → 19:28:49 | `9454a30` | deactivated |
| 19:17:49 → 19:18:13 | `c87d1f4` | deactivated |
| 19:16:56 → 19:17:21 | `8109205` | deactivated |
| 19:13:21 → 19:14:14 | `20bdd34` | deactivated |
| 18:58:37 → 18:59:21 | `805eabb` | deactivated |
| 18:58:06 → 18:58:55 | `cfbc5b1` | deactivated |
| 18:55:27 → 18:56:15 | `caaea8a` | deactivated |
| 18:54:22 → 18:54:46 | `abdeb3d` | deactivated |
| 18:52:30 → 18:53:39 | `6b62dfc` | deactivated |
| 18:52:19 → 18:53:08 | `0247270` | deactivated |

Those images carried the **execution gate**, the `live_executor` lane
tags, the `pmus` authorization calls and the capture pacing change.
`sportsassets-api` tracks the same branch and deployed alongside.

### Whether anything changed in orders or controls

**Not inferred from the absence of a `LOOPS` registration — asked
directly** (`research/bettor_deploy_telemetry.sql`,
[run 226](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35660873523)):

| plane | reading |
|---|---|
| `ai_trades`, last 36 h | **0 rows.** No hour has a row, no status, no filled notional |
| `engine_fills` | columns enumerated; no row written in the window |
| `live_trading_paused` | **`true`** |
| `mirror_live` | **`false`**, tripped `wrong_sign_trip` on 2026-09-08 |
| heartbeats, last 36 h | 18 services, 15 `ok` |

**Nothing was ordered and no control moved.** That is a measurement of
the order and control planes over the window the deploys span, not an
argument from what was or was not registered.

**What I cannot claim:** these reads cover *our* tables. They are not a
venue-account audit and do not rule out effects on planes I did not
read. What they establish is that across twelve deploys carrying
order-path code, the order plane stayed empty and the two authoritative
switches held.

## 1. PREFLIGHT — DESTINATION AND FAST-FORWARD

Immediately before writing this:

```
destination  origin/claude/session-njaewf                   0442d576
prepared     origin/claude/bettor-observation-activation    (see above)

git merge-base --is-ancestor session-njaewf activation  →  YES
commits behind destination                              →  0
commits ahead of destination                            →  15
```

**The prepared merge contains the current destination tip.** The push
is a plain fast-forward.

**Re-run this check immediately before pushing.** If `behind` is not
`0`, the branch advanced: merge the new tip into
`claude/bettor-observation-activation`, re-run the ten test files, and
re-confirm before pushing.

```bash
git fetch origin
git merge-base --is-ancestor origin/claude/session-njaewf \
    origin/claude/bettor-observation-activation && echo FAST-FORWARD-OK
git rev-list --count \
    origin/claude/bettor-observation-activation..origin/claude/session-njaewf   # must be 0
```

**Never force this activation.** If the push is rejected as
non-fast-forward, that rejection is correct and the answer is to
incorporate the new tip, not to override it.

## 2. THE ACTION

```bash
git fetch origin
git push origin origin/claude/bettor-observation-activation:claude/session-njaewf
```

Render's auto-deploy does the rest. **That push is the activation.**

### The marginal change

```
git diff --stat origin/claude/session-njaewf..origin/claude/bettor-observation-activation
→ 25 files changed, 7,578 insertions, 410 deletions
```

| file | Δ | what |
|---|---|---|
| **`workers/all.py`** | **+25 / −6** | one import, one `LOOPS` entry, the comment |
| `render.yaml` | +34 | **comment only** — the disk block, not applied |

Everything else is new files or the release's corrected versions of
files only this worker uses. Verified on the merged tree:
`execution_gate.py` present, `_bind_execution_gate` ×2, `bettor_live`
registered ×1.

### Services affected — read, not inferred

[Run 1105](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35656892281):

| service | branch | autoDeploy | suspended | redeploys? |
|---|---|---|---|---|
| **`sportsassets-workers`** | `claude/session-njaewf` | yes | no | **YES** |
| **`sportsassets-api`** | `claude/session-njaewf` | yes | no | **YES** |
| `edge-shadow` | `claude/session-njaewf` | yes | **SUSPENDED** | **NO** |
| `bettortoken-api` | `main` | yes | no | **NO** |

## 3. ROLLBACK — NO FORCE PUSH

The force-push line is **removed**. Rewriting the destination branch
would discard the history every other service deploys from.

### First stop — the flag

```
render-ops action=env-set service=sportsassets-workers \
           arg=BETTOR_LIVE_LOOP=off confirm=DO
```

No deploy of its own. Render applies an environment change by
restarting the service (§4).

**Then verify the restarted process actually left the loop disabled:**

```
render-ops action=logs service=sportsassets-workers arg=10
#   expect: "bettor_live_loop: disabled by BETTOR_LIVE_LOOP=off"
#   and NO  "bettor_live_loop: boot <hex>" line after the restart

python3 scripts/bettor_canary.py --minutes 10 --checkpoint checkpoint.json
#   expect: NO new boot_id in the journal after the restart.
#   A new boot_id writing records means the flag did NOT take.
```

A restart that produced a new `boot_id` *and* new records is the flag
failing, and it is visible in exactly that comparison.

### Second stop — the forward revert

`claude/bettor-observation-deregister` (`ca68bc5f`) is prepared: it
comments the `LOOPS` entry out and drops the unused import. **12
insertions, 16 deletions, in `workers/all.py` alone.**

```bash
git push origin origin/claude/bettor-observation-deregister:claude/session-njaewf
```

A normal push, forward. The execution gate is untouched, the loop's
modules and its three tables stay, every record already written stays,
and no unrelated change is reverted.

### Third stop

```
render-ops action=suspend service=sportsassets-workers confirm=DO
```

Stops all 25 loops. Reserve it for the case where the worker itself is
the problem.

## 4. THE KILL SWITCH IS A RESTART

`BETTOR_LIVE_LOOP` is read from the process's own environment, which
does not change while the process runs. `render-ops` says so in its own
output: *"Render redeploys the service on an env change."*

1. Render sends **SIGTERM**.
2. `workers/all.py` installs **no SIGTERM handler**; Python's default
   disposition terminates without running `finally` blocks — verified:
   **exit 143, the `finally` never ran.**
3. The final flush does **not** run. **Whatever is uncommitted at that
   instant is lost** — read `uncommitted_records` and
   `oldest_uncommitted_age_s`, do not assume two seconds.
4. The new process reads `off`, logs it, returns. The other 24 loops
   start normally.

## 5. TRADING CONTROLS, AS THEY STAND — CONFIGURED vs READ

**Read, not changed.** Nothing in this preflight altered any of them.

| control | where | configured | how the process actually reads it | effect now |
|---|---|---|---|---|
| **`live_trading_paused`** | DB `ingestion_state` | **`true`** | `execution_gate.authorize()` on **every** submission; **absence counts as paused**; no fresh read ⇒ DENY | **PAUSED — submissions denied** |
| **`mirror_live`** | DB `ingestion_state` | **`false`** | read by the mirror lane before exposure can increase | mirror order submission off; tripped `wrong_sign_trip` 2026-09-08 |
| `LIVE_COPY_HALT` | env, worker | `'0'` | `os.getenv` per call, `live_executor.py:1737` | halt not engaged — the pause above is what binds |
| `LIVE_TRADING_ENABLED` | env, worker | **`'1'`** | via `cfg.live_trading_enabled`, **one site**, `live_executor.py:2624`; also `active_venue` in the gate | venue routing enabled — **the gate denies first** |
| `WHALE_EXIT_ENABLED` | env, worker | `'0'` | **module constant at import**, `whale_exits.py:71` | disabled; **only a restart can change it** |
| `AI_TRADER_ENABLED` | env | `'false'` | — | — |
| `UNDERDOG_ENABLED`, `SA_COPY_EXIT`, `COPY_PROBE_ENABLED` | env | **not set** | code defaults apply | — |
| `LIVE_ALLOW_SHORT` | env, worker | `'on'` | `os.getenv` per call, `live_executor.py:9294/9311` | permissive **only** past the gate |
| **execution gate** | code | `await _bind_execution_gate()` **before any loop starts** | binds a loop and a pool so the kill switch can be read | **bound; unbound ⇒ every submission denied** |

**The honest reading:** `LIVE_TRADING_ENABLED='1'` and
`LIVE_ALLOW_SHORT='on'` are permissive settings. They are not what is
holding trading off. What holds it off is `live_trading_paused=true`,
checked at the gate that every submission passes through, with absence
treated as paused. That is the standing state and this release does
not change it.

## 6. SHARED MIGRATION STARTUP — THE RACE, MEASURED AND FIXED

`start.sh` runs the API's migration runner (which applies **093**)
while the worker boots and calls `PgStore.start()` — **in the same
deployment.**

**`CREATE TABLE IF NOT EXISTS` is not concurrency-safe.** Measured
here against a real PostgreSQL: twelve sessions running the same DDL
at once gave **2 successes and 10 failures** —
`DuplicateTableError`, `UniqueViolationError` on
`pg_type_typname_nsp_index`, `DuplicateObjectError`. It checks the
catalog *before* taking its lock.

Unfixed, the worker's store would have raised, `main()` would have
reported `STORE_START_FAILED`, and **the observation run simply would
not have happened on that deploy.**

### The fix

* **One advisory lock**, key `930930093`. The worker takes it in
  `start()`; migration 093 takes it as its **first statement**, inside
  the transaction `migrate.py` already wraps each migration in.
* **Bounded waits:** `lock_timeout` 10 s, 5 attempts, 7.5 s of
  backoff — a stuck migration fails the boot rather than hanging it.
* **Verify regardless.** The other party can finish between our
  catalog check and our lock, so after *any* failure the schema is
  checked and a complete schema is accepted however it arrived.
* **Explicit failure path:** `SCHEMA_NOT_READY`, naming the missing
  columns and the lock key. `main()` turns it into
  `STORE_START_REFUSED` and does not start the loop.

Tested against a real server: ten concurrent `start()` calls all
succeed; the migration file applied by two simulated runners
concurrently with a worker `start()` leaves all three `ok`; and a
schema that can never be completed refuses by name after exactly
`SCHEMA_ATTEMPTS`.

### API health is part of acceptance

The API redeploy applies 093 and gains inert modules — no module under
`sportsassets/api/` imports any of them. Migrations are **best-effort**
at boot (`|| echo "migrate failed — serving anyway"`), so a failed
migration does not stop the API serving; it also means 093 may land on
the next boot instead, which is why the worker creates the schema
itself.

**Acceptance requires confirming the API came back:**

```
render-ops action=logs service=sportsassets-api arg=10
#   expect: "migrations complete"  (or the best-effort message, which
#           is survivable because the worker creates the schema too)
#   expect: uvicorn serving, health checks passing
render-ops action=events service=sportsassets-api
#   expect: a successful deploy, no crash loop
```

I could not read `/healthz` directly from here — egress to that host
is blocked by the proxy (HTTP 403 on CONNECT). It is an operator step.

## 7. PRE-DEPLOYMENT BASELINE

### Memory — `sportsassets-workers`, instance `…-jvhl6`

[Run 1106](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35658953668),
124 one-minute samples, 19:42 → 21:45 UTC:

| | MB |
|---|---|
| min (cold boot) | 254 |
| **p50** | **921** |
| p90 | 1,057 |
| **p95** | **1,108** |
| p99 | 1,511 |
| max | 1,531 |
| plan limit | **2,048** |

Last 60 min: p50 **963**, p95 **1,070**. The canary's evidence gate
allows **+15%** on p95 — a post-deploy p95 above **1,274 MB fails**.
This service was OOM-killed at 2 GiB thirteen times in one evening.

### CPU

p50 ≈ 0.25 cores, peaks ≈ 0.8. Not a constraint.

### Heartbeats — 18 services, 15 `ok`

From `service_heartbeats`, last 36 h, newest first: `poller`,
`mirror_live`, `shadow_experimental` *(already_sealed)*,
`chain_listener`, `dispatcher`, `mirror_shadow` *(degraded)*,
`institutional_md`, `workers_memory`, `underdog`, `shadow_rn1`
*(venue_unreadable)*, `metadata`, `shadow_bettor`
*(venue_unreadable)*, `copy_sweep`, `reconciler`, `analytics`
*(running)*, `retention`, `roster`, `whale_exits` *(last beat 12:05,
consistent with `WHALE_EXIT_ENABLED=0`)*.

**Use 18 as `loops_expected` in the evidence file**, not 25: six of the
25 registered loops do not write heartbeats, and `bettor_live` will not
either. Four of the eighteen are in a non-`ok` state **before** this
change; that is the baseline, not a regression to be attributed to it.

### Loops

**24 registered before, 25 after.**

## 8. THE 20-MINUTE CANARY

```bash
# T+0
git push origin origin/claude/bettor-observation-activation:claude/session-njaewf

# T+2   both services back?
render-ops action=logs service=sportsassets-workers arg=5
render-ops action=logs service=sportsassets-api     arg=5
#   worker: "bettor_live_loop: boot <hex>, store {...backend: postgres...}"
#           "bettor_live_loop: N markets subscribed of M considered"
#   api:    migrations line, uvicorn serving

# T+20
render-ops action=metrics service=sportsassets-workers arg=30
render-ops action=events  service=sportsassets-workers
render-ops action=logs    service=sportsassets-workers arg=20
cat > ops.json <<'EOF'
{"rss_mb_p95": <metrics>, "rss_mb_baseline_p95": 1108,
 "restarts_since_deploy": 1, "oom_since_deploy": 0,
 "loops_heartbeating": <count>, "loops_expected": 18,
 "collected_at": "<utc>", "source": "render-ops run <id>"}
EOF
python3 scripts/bettor_canary.py --checkpoint-write checkpoint.json
python3 scripts/bettor_canary.py --minutes 20 --evidence ops.json

# T+25   the restart comparison
render-ops action=restart service=sportsassets-workers confirm=DO

# T+30
render-ops action=metrics service=sportsassets-workers arg=15    # refresh ops.json
python3 scripts/bettor_canary.py --minutes 20 \
        --checkpoint checkpoint.json --evidence ops.json
echo "exit $?"     # 0 accepted | 1 failed | 3 ACCEPTANCE INCOMPLETE
```

| # | check | passes only if |
|---|---|---|
| 1 | live socket, **fresh** books | ≥10 decisions fresh on **both** clocks (source ≤10 s, receipt ≤5 s), every decision carrying both, ≥1 market, `evidence_class = PROSPECTIVE_SHADOW` |
| 2 | committed to Postgres | rows present, **0 duplicate record keys** |
| 3 | outcome checks **ran** | ≥1 contract **attempted** while work was owed. Nothing owed → `NOT_ESTABLISHED` |
| 4 | no execution **in our journal** | 0 executed, 0 sized. **Our journal; not a venue-account audit** |
| 5 | siblings unaffected | from `--evidence`: 0 OOM, heartbeats ≥ 18, p95 ≤ 1,274 MB |
| 6 | **restart** retained records | a **new `boot_id`**, every checkpointed `record_key` present, counts not going backwards |

**`NOT_ESTABLISHED` is not a pass.** Exit 3 means incomplete.

## 9. VERIFICATION

| | |
|---|---|
| `pytest`, 10 files, **merged tree** | **410 passed** against a real PostgreSQL |
| `pytest`, release branch | 401 passed with PostgreSQL |
| `bettor_durability_proof.py` | exit 0 — SIGKILL, second process, lost-ack replay, outage, prune |
| `bettor_startup_path.py` | exit 0 — paginated discovery → selection → subscription → decisions |
| `bettor_market_measures_run.py`, `bettor_live_harness.py` | exit 0 |
| `bettor_canary.py` | 24 acceptance tests; exits 3 here, correctly |
| merged tree | gate present, `_bind_execution_gate` ×2, `bettor_live` ×1 |

## 10. THE REQUEST

**One production action:**

> Push `claude/bettor-observation-activation` to
> `claude/session-njaewf`, as a fast-forward.

It starts the **decision-only observation worker** and creates its
**three storage tables** (`bettor_live_journal`, `bettor_live_cursor`,
`bettor_live_ledger`). It includes the **`sportsassets-api` restart
and migration 093**, because both services track that branch.

**Not included:** no funded pilot, no trading activation, no broader
release. `MAX_CONTRACTS=0`, no order call reachable from the loop's
import closure, no accounting table written, no credential moved.
`live_trading_paused=true` and `mirror_live=false` stay exactly as
they are, and the execution gate that is live today stays live.

**The next step after authorization is the live canary** — not another
demonstration and not a feature.

## 11. STILL OUT OF REACH

| # | item | blocked by |
|---|---|---|
| 1 | live decision-only observation | **this push** |
| 2 | account identity, activity, resting orders | PMUS secrets absent in CI, *or* #1 |
| 3 | execution lifecycle in preproduction | `PMX_PREPROD_*` unpopulated; access **UNVERIFIED** |
| 4 | `p_fill`, realized duration, adverse selection | **no BETTOR order has ever rested** — the funded pilot |
| 5 | activity, markout, persistence on live data | #1 |
| 6 | does completing a pair release capital | `NOT_IDENTIFIED`; #2 or preproduction |
| 7 | a daily capacity figure | #1, then counter differences over an established interval |
| 8 | the `hourly` heartbeat query | pre-existing division by zero, unrelated |

**#4 is the only genuine circularity.**

---

## 12. WHAT HAPPENED WHEN IT WENT LIVE — 2026-09-21

**Outcome: STOPPED / ROLLED BACK.** The activation deployed exactly as
planned. The observation loop then failed to select a universe, wrote
nothing, and has been deregistered.

### The push and the deploy

| | |
|---|---|
| destination before | `0442d576` |
| pushed | `e8b82a3e350aa1df563500bbcdfc031a73f5d022`, plain fast-forward, 0 behind / 18 ahead, **no force** |
| pushed at | 22:31:39Z |
| worker live on `e8b82a3` | 22:32:53.94Z |
| api live on `e8b82a3` | 22:33:02.80Z |
| api health | `GET /healthz 200 OK` at 22:41:16Z and 22:41:21Z |

### The defect: the universe is empty by construction

Every five seconds, from 22:32:53Z:

```
bettor_live_loop: listing hit the 6-page bound; the universe is drawn from a PREFIX
bettor_live_loop: discovery returned 0 eligible markets of 3000 considered;
                  not starting. Excluded: {"ONE_SIDED_BOOK": 3000}
loop bettor_live exited cleanly; restarting in 5s
```

`_discover` reads `client.markets.list(...)`, which returns
`GetMarketsResponse -> list[MarketDetail]`. `MarketDetail` is
`id, slug, title, outcome, description, active, closed, liquidity,
volume, eventSlug, team`. **No `bestBid`, no `bestAsk`, no
`sharesTraded`, no `state`.** `assess()` requires a bid, an ask and a
shares figure, so every row falls to `R_ONE_SIDED` — 3000 of 3000.

The venue carries bid and ask on a **different endpoint**:
`markets.bbo(slug) -> MarketBBO {bestBid, bestAsk, bidDepth, askDepth,
lastTradePx, sharesTraded, openInterest}`, **one market at a time**.

Reproduced locally against a `MarketDetail`-shaped row:
`reason ONE_SIDED_BOOK, bid None, ask None, included False`.

**Why the harness missed it.** `startup_rows_250.json` holds *captured
observations* — `yes_bid`, `yes_ask`, `stats_shares_traded`,
`venue_state` — spellings `assess()` also accepts. The harness proved
the selection rule against a payload shape production never delivers.

### Schema initialization DID succeed

`main()` calls `store.start()` **before** `_discover`, and returns
`STORE_START_REFUSED` on failure. The `EMPTY_UNIVERSE` log is only
reachable past a `PgStore.start()` that returned `ok` — and the
default backend is Postgres. So the advisory-locked DDL ran against
production Postgres and the three tables exist.

**Nothing was observed and nothing was written.** `main()` returns
before `build()`, so no record was ever constructed.

### The first stop did not work

| time | action | result |
|---|---|---|
| 22:38:58Z | `env-set BETTOR_LIVE_LOOP=off` | HTTP 200, 3 chars stored |
| — | Render raised **no deploy** for the env change | still cycling at 22:43:10Z |
| 22:43:40.13Z | `action=restart confirm=DO` | **`server_restarted`** in the event log |
| 22:47:29Z | loop still logging the **discovery error** | **not** `disabled by BETTOR_LIVE_LOOP=off` |

The restart demonstrably happened and the restarted process still did
not read the new value. **§4's claim that an environment change is a
working kill switch on this service is withdrawn.** It is not a
demonstrated control and must not be relied on as the first stop until
someone shows it working.

A second defect the same reading exposed: `run_forever` in
`workers/all.py` is `while True`, so a clean return restarts the loop
after `RESTART_DELAY_SECONDS`. **Even a working kill switch would
leave a five-second start/disable/restart cycle**, not a quiet stop.
Deregistration is the only genuinely quiet state.

### The second stop, applied

`ca68bc5` is **not** a descendant of `e8b82a3` (1 ahead, 5 behind), so
pushing it would not have been a fast-forward and this activation is
never to be forced. It was applied **onto the current tip** instead:

| | |
|---|---|
| commit | `7a9947b65bc91e57b1dae7d23f7c05994356c488` |
| pushed at | 22:48:50Z, `e8b82a3..7a9947b`, fast-forward, **no force** |
| change | one `LOOPS` line commented out, one import dropped, two registration tests widened |
| tests | `test_bettor_live_loop.py` **61 passed** |
| untouched | `execution_gate.py` present, `_bind_execution_gate` still awaited before any loop; `live_trading_paused=true`; `mirror_live=false`; migration 093 and the three tables stay |

### What the canary established

| # | check | result |
|---|---|---|
| 1 | live socket, fresh books | **FAIL** — zero decisions; the loop never subscribed |
| 2 | committed to Postgres | **NOT ESTABLISHED** — tables created, no rows to commit |
| 3 | outcome checks ran | **NOT ESTABLISHED** — no contracts to check |
| 4 | no execution in our journal | trivially true; nothing ran |
| 5 | siblings unaffected | the other loops ran throughout; API served 200s |
| 6 | restart retained records | **NOT ESTABLISHED** — no records to retain |

The scripted 20-minute canary was **not run**: with zero fresh
decisions it exits 3 by construction, and running it would have
produced a number, not a finding.

### The load this cost

~73 discovery cycles between 22:32:53Z and the deregistration, each
six listing pages — roughly 440 listing requests at about 1.2/s, plus
one advisory-locked DDL transaction per cycle. Bounded, public
endpoints, no orders, now stopped.

### What has to change before this is re-registered

1. `_discover` must obtain a bid, an ask and a shares figure. The
   listing cannot supply them; `markets.bbo(slug)` can, per market.
   3000 candidates is 3000 calls, so a pre-filter is needed — but
   `MarketDetail.volume` is not `sharesTraded`, and substituting it
   would silently change the frozen selection rule. **That is a
   decision, not an implementation detail.**
2. The startup harness must be driven by the **listing payload
   shape**, not by captured observations, or it will keep passing
   while production selects nothing.
3. Either demonstrate the environment kill switch actually taking
   effect on this service, or accept deregistration as the only stop.

### The stop, verified

| | |
|---|---|
| worker live on `7a9947b` | 22:49:41.29Z |
| worker live on `c5ff8c3` | 22:50:42.96Z |
| api live on `c5ff8c3` | 22:50:46.99Z |
| last `bettor_live` log line of any kind | **22:51:08Z**, on the draining instance |
| `logs arg="22:51:20/22:53:00 bettor_live"` | **zero lines returned** |

The loop is gone from the running process, not merely quiet: the
filter matches `starting loop: bettor_live` and `disabled by ...` as
well as the discovery error, and it returned nothing across a
100-second window in which a five-second cycle would have produced
about twenty lines.

`execution_gate.py` is present on the deployed tip and
`_bind_execution_gate()` is still awaited before any loop starts.
`live_trading_paused` and `mirror_live` were never touched.
