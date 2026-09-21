# RELEASE AND ACTIVATION PACKAGE

**Code SHA: `9454a305c1ad025dc1e5eaadc16f756f8112e9e2`**, branch
`claude/session-njaewf`. Every executable line this package activates
exists at that commit. This document is the sole change in the commit
that adds it, so the branch tip differs from `9454a30` by documentation
only:

```
git diff --stat 9454a30..HEAD
→ research/beta48/acceptance/RELEASE_AND_ACTIVATION.md | (docs only)
```

Stated this way because a release note cannot contain its own SHA, and
the previous version pretended otherwise: it was pinned to `20bdd342`
while being delivered at `c87d1f4`, two commits ahead. Verify the pin by
running the diff above rather than by trusting this line.

Restrictions unchanged: `mirror_live=false`, no orders, no capital
activation, production `3349219`, pilot unactivated.

**NOTHING FROM THIS BRANCH IS RUNNING.** `sportsassets-workers` runs
`python -m sportsassets.workers.all` from production `3349219`.

---

## 1. "ONE LINE AND MERGE" WAS WRONG, AND BY HOW MUCH

The activation review's central objection. Measured:

```
git diff --stat 3349219..HEAD -- backend/sportsassets/ render.yaml
→ 22 files changed, 8,293 insertions, 8 deletions   (68 commits)
```

**A one-line registration in `all.py` does not make this a one-line
release.** Merging to deploy the worker deploys everything below.

### 1.1 Production behaviour that WOULD change

| file | Δ | what it does in production |
|---|---|---|
| **`pmus.py`** | +28 | **`submit_fok` and `close_position` now consult the execution gate and RAISE on denial.** Every order this system can place arrives at one of these. |
| **`live_executor.py`** | +42/−8 | lane tags on copy / whale_exit / manual; **gates a second CLOB submission path** (`post_order` on its own `py_clob_client`) that `pmus`-level gating never covered |
| **`workers/all.py`** | +27 | **`await _bind_execution_gate()` before any loop starts.** Until it binds, **every submission in the process is denied.** |
| `workers/bettor_state.py` | +15 | capture pacing |
| `api/app.py` | +17 | gate binding in the API process |
| `execution_gate.py` | +571 | new module |

**The execution gate is a restriction, not a capability** — its failure
mode is refusal. But a deploy that cannot read the kill switch **halts
the mirror's order flow**, and that is a production consequence of
merging this branch, not of adding the BETTOR worker.

### 1.2 New modules, inert until called

`bettor_decision_engine` (866), `bettor_shadow_loop` (1279),
`bettor_market_stream` (578), `bettor_observation_adapter` (493),
`bettor_live_read` (519), `bettor_maker_economics` (426),
`bettor_live_loop` (443), `bettor_fee_schedule` (382),
`bettor_admission` (536), `bettor_capacity_harness` (438),
`bettor_settlement_ingest` (211), `bettor_venue_contract` (186),
`bettor_universe` (179), `bettor_prospective` (265),
`venue_reconcile` (461), `api/reconcile_read` (339).

**No production module imports any of them.** Verified by `grep` over
`backend/sportsassets`. They deploy as dead weight until `all.py`
registers one.

### 1.3 Therefore: THREE releases, not one

| # | contents | risk | approval |
|---|---|---|---|
| **R1** | the **execution gate**: `execution_gate.py`, `pmus.py`, `live_executor.py`, `api/app.py`, `all.py` binding | **touches the live order path** | its own, on its own evidence |
| **R2** | the inert BETTOR modules + tests | none — nothing imports them | routine |
| **R3** | **one line** registering `bettor_live_loop` in `LOOPS` | decision-only | the activation decision |

**R3 is genuinely one line. R1 is not, and I previously described the
whole thing as R3.** Splitting them is a recommendation, not something
I have done: reordering 68 commits is itself a change and is the
owner's call.

## 2. THE ACTIVATION ACTION (R3)

```python
# backend/sportsassets/workers/all.py
 from . import (analytics, bettor_state, chain_listener, copy_sweep,
+               bettor_live_loop,
                dispatcher, edge_marks, institutional_md, ...)
 LOOPS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
     ...
+    ("bettor_live", bettor_live_loop.main),
 ]
```

**No `render.yaml` change.**

### Credentials on the EXACT target service

`render-ops env-keys sportsassets-workers` — key names and lengths
only, no value read, nothing moved:

| key | length |
|---|---|
| `PMUS_KEY_ID` | **36** |
| `PMUS_SECRET_KEY` | **88** |

Present on the deployment target itself, not inferred from
`sportsassets-api`. *(The service also carries the unprefixed `PMX_*`
production-exchange credentials. This worker does not read them and
must not: the preprod lane uses the `PMX_PREPROD_*` namespace precisely
so the two can never be confused.)*

### Configuration

| variable | default | effect |
|---|---|---|
| `BETTOR_LIVE_LOOP` | `on` | `off` stops the loop, no deploy needed |
| **`BETTOR_LIVE_STATE_DIR`** | **`/var/tmp/bettor`** | journal + ledger. **`main()` refuses to run if it is unusable.** |
| `BETTOR_LIVE_MAX_CONTRACTS` | **`0`** | sizes a **simulated** execution only |
| `BETTOR_LIVE_DISCOVERY_PAGES` | `6` | 6 × 500 = 3,000 markets measured |
| `BETTOR_LIVE_OPENING_CASH` | `0` | shadow ledger opening balance |
| `BETTOR_FEE_DATE` | today | which dated schedule applies |

**`/var/tmp` on Render is ephemeral — it survives a restart, not a
redeploy.** For evidence that must outlive a redeploy, point
`BETTOR_LIVE_STATE_DIR` at a persistent disk (as `edge-shadow` already
does) or accept that a redeploy starts a fresh journal. Recovery is
correct either way: an absent journal recovers zero lines and decides
everything afresh.

### Resource footprint

| | |
|---|---|
| threads | **+1** (the socket), daemon, stopped in a `finally` |
| memory | bounded: 2,000-record ring + 5,000-touch ring + ≤100 dedup keys + ≤100 cached books |
| event loop | journal writes and `report()` run via `asyncio.to_thread` |
| venue reads | 1 websocket; 1 discovery listing / 900 s; ≤25 settlement REST calls / 600 s |
| existing workers | `all.py` supervises each loop independently and restarts on crash; `BOOT_STAGGER_S` shifts every subsequent loop's start by 0.75 s |

**The relevant risk is memory.** `sportsassets-workers` was OOM-killed
thirteen times in one evening at 2 GiB, which is why every collection
here is bounded and why that is tested rather than asserted.

### Pre-merge validation

1. `pytest` the seven affected files — **277 passing at this SHA**
   *(full suite: 10,163 pass / 387 fail across 56 files, all
   pre-existing, none of them these seven)*
2. `python3 scripts/bettor_live_harness.py` — exit 0
3. `python3 scripts/bettor_shadow_demo.py` — exit 0
4. `python3 scripts/bettor_replay_real.py` — exit 0

### Post-deploy validation

1. `bettor_live_loop: recovered {...}` then `N markets subscribed of M considered`
2. within 60 s: `stream.connected: true`, `subscriptions.confirmed > 0`
3. `by_action` **expected `{"NO_TRADE": ...}`**
4. `durability.records_written` rising, `persist_failures` **0**
5. `freshness.median` — **the first measurement of the live path's
   latency**; the capture feed's median source-to-receipt is 549.6 s

**Rollback triggers:** any execution with `MAX_CONTRACTS=0`;
`persist_failures` > 0 and rising; worker RSS above its pre-deploy
band; any sibling loop's heartbeat lagging.

### Rollback

| severity | action |
|---|---|
| immediate | `render-ops env-set BETTOR_LIVE_LOOP=off confirm=DO` — the loop exits its `while`, `finally` stops the stream and saves the ledger |
| full | revert the `all.py` commit |

## 3. WHY IT IS NOT ALREADY RUNNING

**Venue credentials exist in the runtime and not in CI.**

| | `PMUS_KEY_ID` | `PMUS_SECRET_KEY` | `PMX_PREPROD_*` |
|---|---|---|---|
| `sportsassets-workers` (target) | **36** | **88** | — |
| `sportsassets-api` | 36 | 88 | — |
| GitHub Actions | **empty** | **empty** | **empty** |

Two ways to exercise the loop live, both outside what I hold:
**deploy it** (§2, approval), or **move credentials into CI** (which
the directive forbids). I attempted neither.

## 4. WHAT RAN, AND WHAT IT OBSERVED

| run | what it was | result |
|---|---|---|
| `bettor_live_harness.py` | the **real** stream handler, eligibility, normalizer, engine, shadow ledger, settlement ingestion and **the worker's own `build()` and `recover()`**, on 16 real captured books | 16 → **16 NO_TRADE**, 5 blockers by name, freshness median ≈1.0 s, **16 durable records, 0 persist failures**, recovery replayed 16 lines and re-decided **0**, residual 0, **0 orders** |
| `pytest` (7 files) | 277 tests, **36 of them driving `main()` itself** | pass |
| `research-sql` ×6 | read-only replica queries | depth, volume, spreads, clocks, maturity |
| `bettor-capability-probe` ×2 | authenticated venue reads | 4 account reads `AuthenticationError`; public market read `ok` |
| `pmx-preprod` (whoami) | institutional preprod | failed at "Stage the key" |
| `render-ops env-keys` ×2 | key names and lengths only | §2 |

**The socket has not been opened.** Every harness record is
`REPLAY_DECISION`, never `PROSPECTIVE_SHADOW`. It does not establish
that the socket connects, that subscriptions are accepted, the venue's
real update rate, or whether a book message replaces or deltas.

## 5. PREPRODUCTION

Entitlement is **proven**: `pmx-preprod` **run 24 succeeded
2026-09-10**, returning positions and the USD balance. Runs 25–26 fail
at `SECRET_MISSING: PMX_PREPROD_PRIVATE_KEY_B64` — the credentials were
migrated from dispatch inputs to secret slots that were never
populated. **The missing thing is the secret, not the access**, and it
does not block §2.

## 6. CAPACITY

Method, window, deduplication, units and population are documented in
`CORRECTIONS_ACTIVATION.md` §5, including the caveat that
`stats_shares_traded` is very likely cumulative, making **$396,361 an
upper bound on one day's activity in the sampled universe rather than a
measurement of one day** — and not a ceiling on future activity.

| | round-trip model | settlement-only model |
|---|---|---|
| executed : position | 2 : 1 | **1 : 1** |
| capital for $500k executed, 1 turn/day | $250,000 | **$500,000** |

**Capital is no longer derived by halving.** Which model applies is
`NOT_IDENTIFIED`, because `capital_recycling(INSTITUTIONAL)` reports
`CAPITAL_RECYCLING_AVAILABLE = NOT_IDENTIFIED` — *not* `NO`.

## 7. ON THE CRITICAL PATH

| # | item | blocked by | unblocked by |
|---|---|---|---|
| 1 | live decision-only observation | the R3 merge — **and R1's gate rides with it unless split** | owner |
| 2 | account identity / activity / resting orders | PMUS secrets absent in CI, *or* #1 | owner |
| 3 | execution lifecycle in preprod | `PMX_PREPROD_*` unpopulated since 2026-09-19 | owner |
| 4 | `p_fill`, markout, holding period | no BETTOR order has ever rested | funded pilot |
| 5 | `holds_both_legs_independently`; complement identity | #2, plus no leg-level venue identifier in the capture | #2, then a capture change |
| 6 | **does completing a pair release capital** | `NOT_IDENTIFIED` on institutional | #2 / preprod |

**#4 is the genuine circularity.** #1, #2 and #3 are each one owner
action. #6 is newly separated from #1 — it had been wrongly reported as
a settled `NO`.
