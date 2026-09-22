# Activation request — deploy the schema repair, then one replacement probe

One request covering both steps, under the limits already approved. Nothing
below asks for a new limit, a new capability or a schema change.

## The SHA

**Verified code SHA: `cf77a0fa842e013a33b643f750fa2373c9826aab`** on
`claude/bettor-none-pool-fix`. This is the commit the image was built from and
the check was run against.

**Deploy the branch tip.** `render-ops deploy` deploys the latest commit of the
branch, and a document cannot contain its own hash — so this request names the
verified SHA here and the exact tip hash is given with it in the accompanying
report. Any commit after `cf77a0f` on this branch is documentation only:
`git diff cf77a0f <tip> -- backend/` is **empty**, and `backend/` is the only
tree `backend/Dockerfile` builds from. Whichever of the two is deployed, the
image is byte-identical. Confirm that diff is empty before deploying.

- `0` behind / `3` ahead of the deployed tip `fd39a0a9` on
  `claude/session-njaewf`, which is an **ancestor** — so it fast-forwards.
  Never force.
- **`backend/` is byte-identical to `4b2c7bdd`**, the SHA the check was asked
  against — `git diff 4b2c7bdd cf77a0fa842e013a33b643f750fa2373c9826aab -- backend/`
  is empty, and `backend/` is the only tree the image ships from.
  The two commits on top add, outside it: the preserved probe row, the
  incident wording correction, a read-only `render-ops sql obs-schema`, the
  image-check evidence, and two harness files in `scripts/`
  (`bettor_image_migrate.py`, `bettor_image_startup_check.py`, +527 lines).
  `backend/Dockerfile` copies no `scripts/`, so neither reaches the image —
  verified in the built image: `/app/scripts` does not exist and
  `import scripts.*` raises `ModuleNotFoundError`.

### The whole runtime diff from what is deployed

```
backend/sportsassets/bettor_live_control.py | 27 ++++++++--   (2 lines of SQL)
backend/tests/test_bettor_live_control.py   | 84 +++++++++++++++
backend/tests/test_bettor_live_loop.py      | 18 +++++++
```

Two `updated_at = now()` clauses removed from the reservation UPDATE, a
constant documenting why, and the tests that stop it recurring. That is all.

## Why this is safe to deploy

### The check you asked for, against the deployment artifact

| | |
|---|---|
| image | built from `backend/Dockerfile`, **unmodified** |
| image id | `sha256:488d66b13137eccce76e6b273afc018e4a7179ddc132ed7bb5f95e808639091a` |
| database | built inside the image by `sportsassets.scripts.migrate`, **all 91 migrations** |
| entry point | `bettor_live_loop.main()` **with no arguments** |
| replaced | the venue transport only — `pmus._get_client`, and `MarketStream._run` |
| result | **28 of 28 checks passed**, exit 0 |

Full transcript and method: `research/beta48/acceptance/image_check/`.

### Schema comparison — production read directly, not assumed

`render-ops sql obs-schema`, run **35704135528**, read-only:

| | production | disposable, after 91 migrations |
|---|---|---|
| `ingestion_state` | `key text NOT NULL`, `value jsonb NOT NULL` | identical |
| further columns | none | none |
| migrations applied | 91, `001_init.sql` → `093_bettor_live_observation.sql` | identical |

**No production schema alteration is needed or requested.** The repair stops
the code writing a column that does not exist.

### What the startup check demonstrated

- a listing attempt **reserved before** the request — 1 reserved, 1 dispatched
- 35 distinct-market slots reserved = 35 markets read
- 35 BBO attempts reserved = 35 requests issued; **no refund path**
- ceilings held: 35/40 distinct, 35/160 attempts, 1/18 listing
- 28 markets selected and subscribed; 644 recorded frames ingested
- **644 decisions persisted** to `bettor_live_journal`
- `MAX_CONTRACTS=0`: **zero** orders
- `obs-stop` → `STOPPED_BY_CONTROL`; zero further venue requests, zero
  further reservations; classified as a control reason, so the worker polls
  at 30 s rather than climbing the acquisition ladder
- deadline passed → `DEADLINE_PASSED`; zero requests, counters unmoved,
  control auto-disarmed. With the control put back **on** and the deadline
  still past, a reservation is still refused `RESERVATION_DEADLINE_PASSED` —
  the deadline gate holding on its own

### Stated plainly, so nothing here is overread

- The **listing envelope is constructed**, not recorded: the capture corpus
  holds `/bbo` and `/book` only. The BBO leg is verbatim; the listing leg is
  not.
- Only `transactTime` was restamped, to clear the 10 s freshness gate. Every
  price, quantity, ladder level, state and counter is as the venue sent it.
  Every record is labelled `REPLAY_DECISION`, never `PROSPECTIVE_SHADOW`.
- **This was not a live venue connection.** No probe has established one.
  That is what the requested probe is for.

## The preserved probe

`f99cc7d9-535d-41ee-9780-7b2d6f67035a` and its three zero counters are
copied verbatim to `research/beta48/acceptance/incidents/probe-f99cc7d9.json`
before anything replaces the row. It is **not** reset, replenished or
extended. A replacement probe takes a new identity.

## What is requested

1. **Deploy the branch tip (verified code SHA `cf77a0f`) while stopped.**
   Recheck destination ancestry and
   `obs-state=false` first; fast-forward, never force. Then confirm both
   services' running SHA, API health, effective limits, and that the database
   control is holding observation idle.
2. **Arm once, run once.** One fresh `probe_id`, read back with its zero
   counters, caps and absolute deadline before `obs-run`. The previously
   approved limits, unchanged:

   | | |
   |---|---|
   | distinct markets | 40 |
   | BBO attempts, retries included | 160 |
   | listing attempts, retries included | 18 |
   | BBO pacing | 0.25 req/s, one in flight |
   | WebSocket connections | 1 |
   | frames captured | 25, total received reported separately |
   | absolute deadline | 1,800 s, unchanged across restarts |
   | orders | zero; `MAX_CONTRACTS=0` |

   No re-arming, no replenishment, no extension. Server backoff honoured; a
   `Retry-After` past the deadline ends the probe. If no market qualifies,
   stop and report it without changing selection rules.
3. **Demonstrate stopping.** Once useful live evidence arrives, `obs-stop`;
   acquisition within 58 s, stream closed within 90 s.
4. **Report** ACCEPTED / INCOMPLETE / STOPPED-ROLLED BACK with actual request
   counts, selected markets, socket outcome, frames received, fresh persisted
   decisions and stop latency.

## Rollback

Forward deregistration `claude/bettor-deregister-cf77a0f` at
**`f7345128c184dca7774d4a617ab21cbe5d71ad4b`** — 0 behind / 1 ahead of
`cf77a0f`, with `fd39a0a9` an
ancestor of both, so it fast-forwards whether or not the repair deployed. It
comments out the one registration line and its import; the supervisor cannot
start a loop it is not handed. 24 other loops untouched, no trading control
touched. Ancestry to be rechecked against the then-current destination before
use.

For an ordinary stop, `render-ops sql obs-stop confirm=DO` is faster and
needs no deploy.

## Boundaries, unchanged

Decision-only, zero real-order capability. No funded trading, no trading-flag
change, no credential movement, no accounting change, no expansion of
deployment authority. `live_trading_paused` stays `true`. This authorizes the
deployment and one observation experiment — not continuous collection and not
a funded pilot.
