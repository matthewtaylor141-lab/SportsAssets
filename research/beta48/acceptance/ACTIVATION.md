# THE ACTIVATION REQUEST

Prepared merge: **`claude/bettor-observation-activation` @ `31ff849`**
Release branch: **`claude/bettor-observation-release`**
`OBSERVATION_RELEASE.md` is what is in the release and why. **This is
the action, its measured scope, the baseline, and the acceptance
procedure.**

---

## 0. A CORRECTION I HAVE TO LEAD WITH

I have said in every report this session that *"nothing from this
branch is running; production remains `3349219`."* **That was wrong,
and the measurement that shows it is a read I could not take until
`render-ops` was repaired.**

`render-ops action=deploys service=sportsassets-workers`
([run 1108](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35659196327)):

```
2026-09-21T19:40:45Z -> 19:41:11Z   live   0442d57   new_commit
2026-09-21T19:32:47Z -> 19:33:21Z   deactivated   f8ee4eb
2026-09-21T19:28:00Z -> 19:28:49Z   deactivated   9454a30
... ten more, all from claude/session-njaewf
```

`sportsassets-workers` is **live on `0442d57`** — the tip of
`claude/session-njaewf` — deployed at 19:41 today. `render-ops
action=services` (run 1105) shows why: **every SportsAssets service
tracks `claude/session-njaewf` with `autoDeploy=yes`.** The 68-commit
package, including the execution gate, has been deploying on every
push all session.

Three things follow, and they change the action:

1. **The only thing not running is the registration.** `all.py` on the
   live branch never added `("bettor_live", bettor_live_loop.main)`.
   Everything else — the stream, the engine, the adapters — is already
   in the image.
2. **Deploying the release branch in isolation would be a ROLLBACK.**
   It is based on `3349219`, which is now 68 commits behind what is
   live. Pointing the worker at it would remove `execution_gate.py`
   and the `_bind_execution_gate()` call from the live order path.
   **I am not proposing that.**
3. **The isolation still holds, and is now measurable as a marginal
   diff** rather than argued from a base commit.

## 1. THE ACTION

**Push the prepared merge to the branch the services track.**

```bash
git fetch origin
git push origin origin/claude/bettor-observation-activation:claude/session-njaewf
```

`claude/bettor-observation-activation` (`31ff849`) is
`claude/session-njaewf` with `claude/bettor-observation-release`
merged into it, conflicts resolved and the result verified. Pushing it
is a fast-forward of the tracked branch; Render's auto-deploy does the
rest. **That push is the activation.** Nothing else is required.

### The marginal change it deploys

```
git diff --stat origin/claude/session-njaewf..31ff849
→ 24 files changed, 7,238 insertions, 410 deletions
```

**One existing production file changes behaviour:**

| file | Δ | what |
|---|---|---|
| **`workers/all.py`** | **+25 / −6** | one import, one `LOOPS` entry, the comment |
| `render.yaml` | +34 | **comment only** — the disk block, not applied |

Everything else is new files, or the release's corrected versions of
files only this worker uses. **`execution_gate.py` and
`_bind_execution_gate()` are untouched and still present** — verified
on the merged tree.

### The seven merge conflicts, and how each was resolved

All seven are `add/add`: files that exist on both branches because the
release branch re-derived them from `3349219`.

| file | resolution |
|---|---|
| `bettor_market_stream.py`, `bettor_settlement_ingest.py`, `bettor_universe.py`, `workers/bettor_live_loop.py`, `tests/test_bettor_live_loop.py`, `scripts/bettor_live_harness.py` | **the release branch's version** — it carries every correction from the last three reviews |
| `tests/test_bettor_live_read.py` | **the live branch's version.** The release dropped `TestTheDecisionOnlyWorker` because `workers/bettor_prospective` was not in the isolated release; on the merged branch that module still exists, so its tests stay. |

## 2. VERIFIED SERVICE DEPLOYMENT SCOPE

Read from the Render API, not from blueprint defaults
([run 1105](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35656892281)):

| service | kind | branch | autoDeploy | suspended | redeploys? |
|---|---|---|---|---|---|
| **`sportsassets-workers`** | worker | `claude/session-njaewf` | **yes** | no | **YES — the intended one** |
| **`sportsassets-api`** | web, standard | `claude/session-njaewf` | **yes** | no | **YES** |
| `edge-shadow` | worker | `claude/session-njaewf` | yes | **SUSPENDED** | **NO** |
| `bettortoken-api` | web, pro_plus | `main` | yes | no | **NO** |
| `sportsassets-db` | Postgres 18, basic_4gb, 25 GB | — | — | available | — |

**Corrections to what I wrote before this read:** I said `edge-shadow`
would restart — it is suspended and will not. I said the blueprint's
default implied auto-deploy — it is now measured, `autoDeploy=yes` on
all four.

**What the API redeploy does:** it is already running this branch, so
it gains only the release's new files — all inert, no module under
`sportsassets/api/` imports any of them — and `start.sh` applies
**migration 093**, which is additive and idempotent. It is a web
service with a health check, so Render rolls it.

## 3. COMMANDS

Every one of these is a `render-ops` dispatch, working again as of
[run 1105](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35656892281).

### Deploy

```
git push origin origin/claude/bettor-observation-activation:claude/session-njaewf
render-ops  action=deploys  service=sportsassets-workers          # follow it
```

### Monitor

```
render-ops  action=logs     service=sportsassets-workers  arg=30
render-ops  action=metrics  service=sportsassets-workers  arg=120
render-ops  action=events   service=sportsassets-workers
python3 scripts/bettor_canary.py --minutes 20 --evidence ops.json
```

In the logs, the lines that matter:

```
bettor_live_loop: boot <16 hex>, store {...}, recovered {...}
bettor_live_loop: N markets subscribed of M considered over P pages
bettor_live_loop: {"by_action": {"NO_TRADE": ...}, "durability": {...}}
```

### Rollback

| severity | command | effect |
|---|---|---|
| **stop the loop** | `render-ops action=env-set service=sportsassets-workers arg=BETTOR_LIVE_LOOP=off confirm=DO` | **Render RESTARTS the service.** The new process reads the flag and the loop returns. §4. |
| **full revert** | `git push --force-with-lease origin 0442d57:claude/session-njaewf` then `render-ops action=deploy service=sportsassets-workers confirm=DO` | back to exactly what is live now |
| **stop everything** | `render-ops action=suspend service=sportsassets-workers confirm=DO` | stops all 25 loops |

## 4. THE KILL SWITCH IS A RESTART, MEASURED

`BETTOR_LIVE_LOOP` is read from the process's own environment, which
does not change while the process runs. Render applies an environment
change by restarting the service — `render-ops` says so itself:
*"(Render redeploys the service on an env change; read 'deploys' to
follow it)"*.

1. Render sends **SIGTERM**.
2. `workers/all.py` installs **no SIGTERM handler**, and Python's
   default disposition terminates without running `finally` blocks —
   verified here: **exit 143, the `finally` never ran.**
3. So the final flush does **not** run. **Whatever is uncommitted at
   that instant is lost.**
4. The new process starts, reads `off`, logs `disabled by
   BETTOR_LIVE_LOOP=off`, returns. The other 24 loops start normally.

**The loss is not "two seconds".** Two seconds is the healthy case,
where the last flush succeeded. The actual bound is
`uncommitted_records` and `oldest_uncommitted_age_s` in the last
report before the restart; under a database outage they grow until the
outbox overflows, and then records are DROPPED and the window is
marked `windows_incomplete`. **Read the number.**

## 5. PRE-DEPLOYMENT BASELINE

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
| **plan limit** | **2,048** |
| headroom at p95 | 940 |

Last 60 minutes: p50 **963**, p95 **1,070**.

**This is the number that matters.** The service was OOM-killed at
2 GiB thirteen times in one evening; it now sits at p95 1,108 MB with
940 MB of headroom, and p99 already touches 1,511. The canary's
`--evidence` gate allows **+15%** on p95 — i.e. a post-deploy p95
above **1,274 MB fails**.

### CPU

Same window: p50 ≈ 0.25 cores, peaks ≈ 0.8. Not a constraint.

### Loops

**24 registered before this change, 25 after** — the new one is
`bettor_live`, registered last among the venue readers. (I have been
saying "eighteen"; that was wrong and is corrected throughout.)

### Instance history

A boot at ~19:42 UTC (`…-j68gh` → `…-jvhl6`), consistent with the
19:41 deploy of `0442d57`. No OOM in the window.

### Heartbeats

`render-ops action=sql arg=hourly` currently **fails**: `ERROR:
division by zero`, in a mirror-lane statement of that query, exit 1
([run 1107](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35659068384)).
Pre-existing and unrelated to this release, but it means the heartbeat
baseline must come from `action=logs`, not from that query. Recorded
here so the next person does not rediscover it.

## 6. THE 20-MINUTE CANARY

```bash
# T+0   deploy
git push origin origin/claude/bettor-observation-activation:claude/session-njaewf

# T+2   the loop should have booted
render-ops action=logs service=sportsassets-workers arg=5
#   expect: "bettor_live_loop: boot <hex>, store {...backend: postgres...}"
#           "bettor_live_loop: N markets subscribed of M considered"

# T+20  collect operational evidence, then run the canary
render-ops action=metrics service=sportsassets-workers arg=30
render-ops action=events  service=sportsassets-workers
render-ops action=logs    service=sportsassets-workers arg=20
cat > ops.json <<'EOF'
{"rss_mb_p95": <from metrics>, "rss_mb_baseline_p95": 1108,
 "restarts_since_deploy": 1, "oom_since_deploy": 0,
 "loops_heartbeating": <count from logs>, "loops_expected": 25,
 "collected_at": "<utc>", "source": "render-ops run <id>"}
EOF

python3 scripts/bettor_canary.py --minutes 20 --evidence ops.json \
        --checkpoint-write checkpoint.json      # writes, then exits
python3 scripts/bettor_canary.py --minutes 20 --evidence ops.json

# T+25  the restart comparison
render-ops action=restart service=sportsassets-workers confirm=DO

# T+30  after a few minutes of fresh collection
render-ops action=metrics service=sportsassets-workers arg=15   # refresh ops.json
python3 scripts/bettor_canary.py --minutes 20 \
        --checkpoint checkpoint.json --evidence ops.json
echo "exit $?"     # 0 accepted | 1 failed | 3 ACCEPTANCE INCOMPLETE
```

### What each check requires

| # | check | passes only if |
|---|---|---|
| 1 | live socket, **fresh** books | ≥10 decisions fresh on **both** clocks (source ≤10 s, receipt ≤5 s), every decision carrying both, ≥1 market, and `evidence_class = PROSPECTIVE_SHADOW` |
| 2 | committed to Postgres | rows present, **0 duplicate record keys** |
| 3 | outcome checks **ran** | ≥1 contract **attempted** during the window while work was owed. Nothing owed → `NOT_ESTABLISHED`, never a pass |
| 4 | no execution **in our journal** | 0 executed, 0 sized. **Scope: our journal. Not a venue-account audit.** |
| 5 | siblings unaffected | from `--evidence`: 0 OOM, all 25 heartbeating, p95 ≤ baseline +15% |
| 6 | **restart** retained records | a **NEW `boot_id`**, every checkpointed `record_key` still present, row and cursor counts not going backwards |

**`NOT_ESTABLISHED` is not a pass.** Exit 3 means acceptance is
incomplete; supply the missing evidence and re-run.

### The restart comparison, precisely

The checkpoint records `journal_rows`, the newest 200 `record_key`s,
the set of `boot_id`s, cursor counts and the ledger's writer. After
the restart, a **new `boot_id` must appear** — a stream epoch
increments on a *reconnect* and is not evidence of a restart — and
every checkpointed key must still be there.

**Expect a small gap at the seam** (§4). Measure it; do not assume it.

## 7. WHAT ACCEPTANCE MEANS

Accepting this canary accepts the **observation infrastructure** and
nothing else. Every decision is `NO_TRADE` by construction, against the
same five blockers. **A green canary is not a profitability result and
is not evidence of one**, and no profitability result is required to
accept the infrastructure.

## 8. VERIFICATION

| | |
|---|---|
| `pytest` on the **merged** tree, 10 files | **406 passed** with a real PostgreSQL |
| `pytest` on the release branch, 10 files | 397 passed with PostgreSQL; 366 passed / 31 skipped without |
| `bettor_durability_proof.py` | exit 0 — real SIGKILL, real second process, lost-ack replay, simulated outage, prune |
| `bettor_startup_path.py` | exit 0 — paginated discovery → selection → subscription → decisions |
| `bettor_market_measures_run.py` | exit 0 |
| `bettor_live_harness.py` | exit 0 |
| `bettor_canary.py` | its own 24 acceptance tests pass; it correctly exits 3 here, where no live socket exists |
| merged tree | `execution_gate.py` present, `_bind_execution_gate` ×2, `bettor_live` registered ×1 |

## 9. THE REQUEST

**I am asking for one production action:**

> Push `claude/bettor-observation-activation` (`31ff849`) to
> `claude/session-njaewf`, which starts the decision-only observation
> loop on `sportsassets-workers` and redeploys `sportsassets-api` with
> inert modules and migration 093.

Real trading stays disabled: `MAX_CONTRACTS=0`, no order call
reachable from the loop's import closure, no accounting table written,
no credential moved, `mirror_live=false` and every standing trading
restriction unchanged. The execution gate that is live today stays
live. **The funded pilot remains a separate authorization and is not
part of this request.**

## 10. STILL OUT OF REACH

| # | item | blocked by |
|---|---|---|
| 1 | live decision-only observation | **this push** |
| 2 | account identity, activity, resting orders | PMUS secrets absent in CI, *or* #1 |
| 3 | execution lifecycle in preproduction | `PMX_PREPROD_*` unpopulated; current access **UNVERIFIED** |
| 4 | `p_fill`, realized duration, adverse selection | **no BETTOR order has ever rested** — the funded pilot |
| 5 | activity, markout, persistence on live data | #1 |
| 6 | does completing a pair release capital | `NOT_IDENTIFIED`; #2 or preproduction |
| 7 | a daily capacity figure | #1, then counter differences over an established interval |
| 8 | the `hourly` heartbeat query | pre-existing division by zero, unrelated to this release |

**#4 is the only genuine circularity.**
