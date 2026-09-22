# The deployment-image check

Asked for because three probes in a row were stopped by the same cause:
**our code and fixtures did not match the production interfaces.** Every
local proof had passed, because every local proof supplied the seam it was
meant to test. This check removes that possibility by using the deployment
artifact itself.

Reproduce:

```
docker build -f backend/Dockerfile -t bettor-verify .        # from the repo root
createdb bettor_image_check
docker run --rm --network=host -w /app -e DATABASE_URL=... \
  -v $PWD/scripts/bettor_image_migrate.py:/app/m.py:ro bettor-verify python m.py
docker run --rm --network=host -w /app -e DATABASE_URL=... \
  -e PMUS_KEY_ID=not-a-credential -e PMUS_SECRET_KEY=not-a-credential \
  -e MAX_CONTRACTS=0 \
  -v $PWD/scripts/bettor_image_startup_check.py:/app/c.py:ro \
  -v $PWD/research/beta48/acceptance:/recorded:ro bettor-verify python c.py
```

## What is real, and what is replaced

| | |
|---|---|
| the image | `backend/Dockerfile`, **unmodified** — `git diff` clean |
| the working directory | `/app`, so `import sportsassets` resolves to `/app/sportsassets`, the same copy `sh start.sh` and `python -m sportsassets.workers.all` resolve |
| the database | built by `sportsassets.scripts.migrate` **inside the image**, all 91 migrations |
| the entry point | `bettor_live_loop.main()` **with no arguments**, as `workers/all.py` invokes it |
| settings, `get_pool`, every reservation, every control read | production code, untouched |
| the BBO bodies | **verbatim** `marketData` from real HTTP 200 responses |
| the book frames | the `/book` half of the same recorded pairs, pushed through the production `_on_market_data` |
| **replaced** | `pmus._get_client`, and `MarketStream._run` — the WebSocket thread only |

`book_at`, the freshness gates, the epoch invalidation, `subscribe`, `prune`
and the frame-capture path are the **production class**, inherited untouched.
`ReplayStream` subclasses `ms.MarketStream` and overrides exactly one method.

## Two things stated plainly so they are not overread

1. **The listing envelope is constructed, not recorded.** The capture corpus
   holds `/v1/markets/{slug}/bbo` and `/v1/markets/{slug}/book` only — it
   never recorded the collection endpoint. The listing response is therefore
   built in `markets.list` shape from real slugs out of the corpus plus the
   fields the SDK's `MarketDetail` declares. The BBO leg is recorded; the
   listing leg is not.
2. **Only `transactTime` is restamped**, to the replay clock, so the frames
   clear `MAX_SOURCE_AGE_S = 10 s`. Every price, quantity, ladder level,
   state and counter is exactly as the venue sent it. Every persisted record
   is labelled `REPLAY_DECISION`, never `PROSPECTIVE_SHADOW` — the
   production class's own rule, asserted.

**This is not a live venue connection.** No probe has established one yet.

## Schema comparison

Read from production with `render-ops sql obs-schema` (run 35704135528,
read-only, no `confirm=DO`) and from the disposable database after the
migration:

| | production | disposable |
|---|---|---|
| `ingestion_state` col 1 | `key text NOT NULL` | `key text NOT NULL` |
| `ingestion_state` col 2 | `value jsonb NOT NULL` | `value jsonb NOT NULL` |
| further columns | none | none |
| `schema_migrations` applied | 91 | 91 |
| first / last | `001_init.sql` / `093_bettor_live_observation.sql` | identical |

There is no `updated_at` on this table in either, and no migration adds one:
of the 91 files, only `001_init.sql` names `ingestion_state`, and it contains
no later `ALTER`. **No production schema change is needed.** The repair stops
the code writing a column that does not exist.

## One finding outside the repair, reported not fixed

`031_us_premap_signed.sql` runs `ALTER TABLE us_premap ADD COLUMN ...`, and
**no migration creates `us_premap`.** It is created at runtime by
`workers/premap.py:_ensure_table`. A database built from the migration set
alone therefore stops at migration 31. Production is unaffected — the worker
created the table long before — but the migration set is not self-sufficient,
and a genuinely fresh deployment would hit this.

`scripts/bettor_image_migrate.py` resolves it by calling **that worker's own
DDL function** and resuming; it does not hand-write a schema. Left as a
finding: this is a narrow repair, not licence to change the migration set.

## Result

Every check passed. See `startup_check_output.txt` for the full transcript.

- listing attempt **reserved before** the request; 1 reserved, 1 dispatched
- 35 distinct-market slots reserved = 35 markets read
- 35 BBO attempts reserved = 35 requests issued
- ceilings held: 35/40 distinct, 35/160 attempts, 1/18 listing
- 28 markets selected and subscribed; 644 recorded frames ingested
- **644 decisions persisted** to `bettor_live_journal`, all `DECIDED`, all
  `REPLAY_DECISION`
- `MAX_CONTRACTS=0`: no order submitted
- `obs-stop` → `STOPPED_BY_CONTROL`, **zero** further venue requests, **zero**
  further reservations, and classified as a control reason so the worker
  polls at 30 s instead of climbing the acquisition ladder
- deadline passed → `DEADLINE_PASSED`, zero further requests, counters
  unmoved, the control auto-disarmed, and the database goes on refusing.
  With the control put back **on** and the deadline still past, the
  reservation is refused `RESERVATION_DEADLINE_PASSED` — the deadline gate
  holding on its own, not riding on the control.
