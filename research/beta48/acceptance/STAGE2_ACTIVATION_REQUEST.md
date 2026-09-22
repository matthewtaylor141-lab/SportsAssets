# Activation request — deploy while stopped, then one bounded probe

One request covering both steps, under limits already approved. Nothing
below asks for a new limit, a new capability, a schema change, capital,
an order path or a credential movement.

## The SHA

**Verified code SHA: `cf77a0fa842e013a33b643f750fa2373c9826aab`** on
`claude/bettor-none-pool-fix`.

**Deploy the branch tip.** `render-ops deploy` takes the latest commit of
the branch, and a document cannot contain its own hash — so this names
the verified SHA and the report accompanying it gives the exact tip.
Every commit after `cf77a0f` touches only `scripts/`, `research/` and
`.github/`:

```
git diff cf77a0f <tip> -- backend/        # MUST be empty
```

`backend/` is the only tree `backend/Dockerfile` builds from, so the
image is byte-identical whichever is deployed. **Confirm that diff is
empty before deploying.**

### The whole runtime diff from what is deployed (`fd39a0a9`)

```
backend/sportsassets/bettor_live_control.py | 27 ++++++++--   (2 SQL lines)
backend/tests/test_bettor_live_control.py   | 84 +++++++++++++++
backend/tests/test_bettor_live_loop.py      | 18 +++++++
```

Two `updated_at = now()` clauses removed from the reservation UPDATE — a
column `ingestion_state` does not have — plus a constant documenting why
and the tests that stop it recurring. Everything else in this branch is
harnesses, evidence and documentation that the image does not ship.

`fd39a0a9` is an ancestor of the tip, so it **fast-forwards**. Never
force.

## Why this is safe to deploy

### The integration run

| | |
|---|---|
| image | `backend/Dockerfile`, **unmodified** (the runner refuses otherwise) |
| database | built **inside the image** by `sportsassets.scripts.migrate`, all 91 migrations |
| entry point | `bettor_live_loop.main()` **with no arguments** |
| replaced | the venue network boundary only — `pmus._get_client`, and `MarketStream._run` |
| result | **115 checks, 0 failures**, across eleven lifecycle properties |

Reproduce: `scripts/bettor_lifecycle_run.sh`. Each phase is a separate
`docker run`, so the restart phases cross a real process boundary.
Evidence: `research/beta48/acceptance/lifecycle/`.

Covered: disabled startup makes no venue request · identity and deadline
survive restart · reservations precede every request · failures, retries,
empty universe and crashes cannot replenish · listing → selection →
persisted decisions with the selection rule pinned · malformed fields
produce named refusals · a database outage fabricates no write · stop and
deadline close the transport · the deadline holds when the disarm write
is blocked · restart recovers evidence and obligations · resources are
bounded.

### Schema, read from production rather than assumed

`render-ops sql obs-schema` (read-only): `ingestion_state` is
`key text NOT NULL`, `value jsonb NOT NULL` — two columns, no
`updated_at`. 91 migrations applied, `001_init.sql` →
`093_bettor_live_observation.sql`, matching the repository exactly and
matching the disposable database column for column.

**No production schema alteration is needed or requested.**

### Regression

446 suite failures on this branch, **zero in any `bettor_live` file**.
The identical 446 ids at `fd39a0a9` and on this branch give
**byte-identical failure sets — 179 and 179**. The gap is test-order
pollution in the full run, not a code difference. **No regression.** The
179 pre-existing failures are in mirror/pmus/calibration/pnl modules and
are out of scope here.

### What this does NOT establish

- **Not a live venue connection.** Every lifecycle result is simulated
  transport over recorded REST bodies.
- **No recorded PMUS WebSocket frame exists anywhere in the corpus.**
  Frame format, sequencing, book-replacement semantics and `transactTime`
  semantics are `NOT_ESTABLISHED`. Only this probe can settle them.
- **No recorded `markets.list` response exists.** The listing envelope is
  built to the SDK's *declared* fields, and those declarations have been
  wrong once already.
- Nothing about profitability. The economic verdict is separate and is
  **NOT_IDENTIFIED for the maker classes and FALSIFIED for the taker
  pair**.

## The preserved probes

`12724d1e-fd2d-41b7-a62f-00c267e43880` and
`f99cc7d9-535d-41ee-9780-7b2d6f67035a`, both with zero counters, are
copied verbatim to `research/beta48/acceptance/incidents/`. Neither is
reset, replenished or extended. The probe runner archives the
then-current row again before arming, and refuses to continue if that
archive is empty.

## What is requested

### 1. Deploy the branch tip while stopped

Recheck destination ancestry and `obs-state = false`; confirm
`git diff cf77a0f <tip> -- backend/` is empty; fast-forward, never force.
Then confirm both services' running SHA, API health, effective limits,
and that the database control holds observation idle.

### 2. One bounded probe, as one operation

`.github/workflows/bettor-probe.yml`, dispatched once with
`expect_sha = <the deployed commit>` and `confirm = ARM`.

It performs the whole sequence itself: preflight (both SHAs, four
operator flags by value, credential **presence** only, observation
stopped, trading paused) → archive the previous probe → arm once → read
the row back → run → watch → stop → verify shutdown → preserve.

| limit | value |
|---|---|
| distinct markets | 40 |
| BBO attempts, retries included | 160 |
| listing attempts, retries included | 18 |
| BBO pacing | 0.25 req/s, one in flight |
| WebSocket connections | 1 |
| frames captured | 25, total messages received reported separately |
| absolute deadline | 1,800 s, unchanged across restarts |
| orders | zero; `MAX_CONTRACTS = 0` |

**Structural guarantees, not promises:** `obs-arm` appears exactly once
in the workflow; `obs-stop` runs in an `if: always()` step, so a
cancelled or failed run still disarms; a `concurrency` group of one makes
a second dispatch wait rather than arm over the first; the run refuses to
report success if the control is not `false` or the trading pause moved.

No re-arming, no replenishment, no extension, no automatic budget
expansion. Server backoff honoured; a `Retry-After` past the deadline
ends the probe. If no market qualifies, it stops and reports that without
changing selection rules.

### 3. Report

ACCEPTED / INCOMPLETE / STOPPED-ROLLED BACK, with actual request counts,
selected markets, socket outcome, frames captured **and** total messages
received, fresh persisted decisions, and stop latency.

## Rollback

Forward deregistration on `claude/bettor-deregister-<tip>`: it comments
out the one registration line and its import, so the supervisor cannot
start a loop it is not handed. AST-verified — 24 loops registered,
`bettor_live` not among them. No trading control touched. Ancestry to be
rechecked against the then-current destination before use.

For an ordinary stop, `render-ops sql obs-stop confirm=DO` is faster and
needs no deploy — and the probe workflow disarms itself anyway.

## Boundaries, unchanged

Decision-only, zero real-order capability. No funded trading, no
trading-flag change, no credential movement, no accounting change, no
expansion of deployment authority. `live_trading_paused` stays `true`.
This authorizes the deployment and one observation experiment — not
continuous collection, and not a funded pilot.

**No pilot is proposed in this package.** No policy passed the economic
evaluation, and weakening a criterion to produce one is the failure mode
this programme exists to avoid.
