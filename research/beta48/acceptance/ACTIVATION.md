# THE DEPLOYMENT ACTION

Branch **`claude/bettor-observation-release`**. Based on production
**`3349219`**. Decision-only.

`OBSERVATION_RELEASE.md` is what is in the release and why.
**This document is the action, its true scope, and how to verify it.**

---

## 1. THE ACTION

**Merge `claude/bettor-observation-release` into the deploy branch.**

That is the whole action. No service change, no `render.yaml` change,
no new credential, no API deploy required as a prerequisite, and no
manual step before or after it except the canary in §5.

| | |
|---|---|
| what starts | one supervised loop, `("bettor_live", bettor_live_loop.main)`, last among the venue readers |
| what it does | streams ≤100 selected markets, decides on every book update, writes every decision **and every refusal** to its own three Postgres tables |
| what it cannot do | submit an order. `MAX_CONTRACTS` defaults to `0`; no order call is reachable from its import closure, asserted over the source |
| how to stop it | `BETTOR_LIVE_LOOP=off` — see §4, which is not what it sounds like |

## 2. THE SOURCE DIFF IS ISOLATED. THE DEPLOYMENT IS NOT.

The review asked this precisely, and the honest answer is that merging
**does** redeploy other services.

### What the blueprint says

```
sportsassets-api      rootDir: .            dockerfilePath: ./backend/Dockerfile
sportsassets-workers  rootDir: .            dockerfilePath: ./backend/Dockerfile
edge-shadow           rootDir: edge-engine  dockerfilePath: ./Dockerfile
```

**No service declares `buildFilter`, `ignoredPaths` or `autoDeploy`.**
Render's documented default for a Blueprint service is auto-deploy on
every push to the tracked branch, and `rootDir` scopes the *build
context*, not the *trigger*. So a merge redeploys **all three
services**, not only the worker.

### What each of those redeploys actually does

| service | effect | risk |
|---|---|---|
| **`sportsassets-workers`** | **the intended one.** New image, worker restarts, `bettor_live` starts. | §6 |
| **`sportsassets-api`** | new image containing the new modules, **all inert** — verified: no module under `sportsassets/api/` imports any of them. `start.sh` applies migrations, so **093 runs here**, additive and idempotent. Web service with a health check → rolling deploy. | low, and named |
| **`edge-shadow`** | `rootDir: edge-engine` is unchanged by this branch, so the image is byte-identical. It still restarts. It has a mounted disk, so its deploy is stop-then-start. | restart only |

**Migration 093 is applied by the API deploy, not the worker.** The
worker also creates the same three tables itself, with the same DDL
character-for-character, so it does not wait on the API — and if the
tables exist with an older shape, `PgStore.start()` refuses to run and
names the missing columns rather than failing at the first write.

### If scoped deploys are wanted

Not applied here, and not part of this release — it changes deploy
behaviour for every service:

```yaml
    buildFilter:
      paths:
        - backend/**
        - render.yaml
      ignoredPaths:
        - research/**
        - scripts/**
        - docs/**
```

That is the owner's call and a separate change.

### What I could NOT verify, and why

**The live Render configuration is unreadable from here.**
`render-ops.yml` is **512,388 bytes**; GitHub's workflow-file ceiling
is **512,000**. It is **388 bytes over**, so every dispatch returns
`startup_failure` — reproduced on this branch
([run 35654491993](https://github.com/matthewtaylor141-lab/SportsAssets/actions/runs/35654491993)),
and it is the same size at `3349219`, so this **predates the release
and is not caused by it**.

Consequence: the auto-deploy state above is from the blueprint and
Render's documented default, **not from the API**. The remedy is to
remove ~400 bytes from that file; it is operational tooling, not this
release, and §5 gives the dashboard equivalent of every read the
canary wants.

## 3. WHAT IS CREATED IN PRODUCTION

Three new tables on `sportsassets-db`, all new, all this worker's:

```
bettor_live_journal   append-only decisions and settlements
                      UNIQUE (lane, record_key)  <- the retry guard
bettor_live_cursor    one row per market: dedup position AND the
                      settlement schedule
bettor_live_ledger    one row per lane: the shadow ledger snapshot
```

`lane` is part of **every** primary key, fixed at
`polymarket-us/institutional/decision-only`, so blending retail with
institutional or preproduction with production is a constraint
violation rather than a code review.

**No accounting table is written.** Not `ai_trades`, not
`bettor_state_*`, not `copy_*`. A test parses the store's SQL string
literals — skipping docstrings, which name the forbidden tables on
purpose — and fails if any other table appears.

**Disk growth is bounded**: the journal is pruned to
`BETTOR_LIVE_JOURNAL_MAX_ROWS` (250,000) every 300 s; cursors age out
at 7 days **only when `RESOLVED`**. Write rate: one batched INSERT per
2 s, one ledger UPSERT per 60 s, one prune per 300 s.

## 4. THE KILL SWITCH IS NOT AN INSTANT PROCESS KILL

`BETTOR_LIVE_LOOP=off` is read by `enabled()` at the top of each loop
iteration — **from the process's own environment, which does not
change while the process runs.** Setting it in Render therefore takes
effect the way Render applies any environment change: **by restarting
the service.**

The restart, measured rather than assumed:

1. Render sends **SIGTERM** to the worker process.
2. `workers/all.py` installs **no SIGTERM handler**, and Python's
   default disposition terminates without running `finally` blocks —
   verified in this container: **exit 143, the `finally` never ran.**
3. So `bettor_live_loop`'s final flush does **not** run. **Up to one
   flush interval (2 s) of decisions is lost.**
4. The new process starts, reads `BETTOR_LIVE_LOOP=off`, logs
   `disabled by BETTOR_LIVE_LOOP=off` and returns. Every sibling loop
   starts normally.

**Verify this during the canary** (§5 step 4), do not assume it.
Expect the journal row count to rise and never fall across the
restart, with a small gap at the seam whose size is
`oldest_uncommitted_age_s` in the last report before it.

Faster than a restart, if the loop must stop *now*: it is one of
nineteen supervised tasks in a shared process, so there is no way to
stop it alone without restarting the service. **Reverting the `all.py`
commit** is the full rollback.

## 5. THE CANARY

`scripts/bettor_canary.py --minutes 20`, run **once** against the
worker's own tables after roughly twenty minutes of collection, then
again after the restart in step 4. Short, then continuous collection.

| # | check | pass condition |
|---|---|---|
| 1 | **live socket, valid books** | records exist, ≥1 market, every record carries a venue clock, and **`evidence_class` is `PROSPECTIVE_SHADOW`** — a replay says `REPLAY_DECISION` and fails this check by construction |
| 2 | **decisions committed to Postgres** | rows by status and action, **0 duplicate record keys** |
| 3 | **outcome checks progressing** | contracts attempted or due; **`awaiting_authoritative` is reported separately from `authoritative`**, because a derived outcome is not a completed one |
| 4 | **no real order** | 0 records claiming an execution, 0 with a non-zero size, over **all** time |
| 5 | **sibling workers** | `NOT_ESTABLISHED` from these tables — see below |
| 6 | **stop and recovery retained records** | row count **rises and never falls** across a restart; ledger present |

A check that cannot be answered reports `NOT_ESTABLISHED` with the
reason. It never passes on silence.

### Step 5 needs the Render reads, which render-ops cannot serve

Until `render-ops.yml` is under the ceiling, these are dashboard reads
on `sportsassets-workers`:

* **Metrics → memory per instance**, against its pre-deploy band. This
  service was OOM-killed at 2 GiB **thirteen times in one evening**;
  RSS is the number that matters.
* **Events** — any restart or OOM since the deploy.
* **Logs** — every sibling loop's heartbeat still arriving, and
  `bettor_live_loop:` lines showing `by_action {"NO_TRADE": …}`,
  `durability.persist_failures 0`, `freshness.median`.

### Rollback triggers

Any execution recorded with `MAX_CONTRACTS=0`; `persist_failures`
rising; `windows_incomplete` true; worker RSS above its pre-deploy
band; any sibling heartbeat lagging; `bettor_live_journal` growing
past its cap.

## 6. RESOURCE FOOTPRINT

| | |
|---|---|
| threads | **+1** (the socket), daemon |
| memory | bounded: 2,000-record ring, 5,000-touch ring, 20,000-record outbox, ≤5,000 cached cursors, ≤100 cached books |
| event loop | no blocking I/O — flushes are batched and awaited |
| venue | 1 websocket; 1 listing / 900 s; ≤25 settlement REST calls / 600 s |
| database | 1 batched INSERT / 2 s, 1 UPSERT / 60 s, 1 prune / 300 s |
| other workers | unchanged. `all.py` supervises each loop independently; `BOOT_STAGGER_S` shifts each start by 0.75 s. **No disk, so no change to any service's deploy mode.** |

## 7. WHAT ACCEPTING THIS DOES AND DOES NOT MEAN

**Accepting the canary accepts the observation infrastructure and
nothing else.**

Every decision this worker takes is `NO_TRADE`, by construction. The
blockers are the same five on every record — `COMPLEMENT_ABSENT`,
`FV_BETTOR_INDEPENDENT_NOT_VALIDATED`,
`MERGE_NOT_OBSERVED_ON_INSTITUTIONAL`, `NO_PAIRED_INVENTORY`,
`P_FILL_NOT_IDENTIFIED` — and a green canary means the instrument
records them faithfully, not that any of them has been resolved.

**A canary that passes is not a profitability result and is not
evidence of one.** The script says so in three places, and the report
of the run must say so too. No profitability result is required to
accept this infrastructure, and none is offered.

Real trading stays disabled. The funded pilot remains a separate
authorization.

## 8. VERIFICATION AT THIS SHA

| | |
|---|---|
| `pytest`, 9 affected files | **373 passed** with a real PostgreSQL; **364 passed / 9 skipped** without |
| `bettor_durability_proof.py` | exit 0 — real server, real SIGKILL, real second process, lost-ack replay, simulated outage |
| `bettor_startup_path.py` | exit 0 — paginated discovery → selection → subscription → decisions |
| `bettor_market_measures_run.py` | exit 0 |
| `bettor_live_harness.py` | exit 0 |
| `bettor_canary.py` | runs; **fails check 1 in this container, correctly** — no live socket exists here, so no record is `PROSPECTIVE_SHADOW` |

Six unrelated test files fail on a stale assertion that the newest
migration is `064_`. **All six fail identically at `3349219`**,
verified in a clean worktree there. Not repaired here: they belong to
other lanes.

## 9. STILL OUT OF REACH

| # | item | blocked by |
|---|---|---|
| 1 | live decision-only observation | **this merge** |
| 2 | account identity, activity, resting orders | PMUS secrets absent in CI, *or* #1 |
| 3 | execution lifecycle in preproduction | `PMX_PREPROD_*` unpopulated; current access **UNVERIFIED** |
| 4 | `p_fill`, realized duration, adverse selection | **no BETTOR order has ever rested** — the funded pilot |
| 5 | activity, markout, persistence on live data | #1 |
| 6 | does completing a pair release capital | `NOT_IDENTIFIED` on institutional; #2 or preproduction |
| 7 | a daily capacity figure | #1, then counter differences over an established interval |
| 8 | Render reads through `render-ops` | it is 388 bytes over GitHub's ceiling; predates this release |

**#4 is the only genuine circularity.**
