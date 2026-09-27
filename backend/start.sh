#!/bin/sh
# API entrypoint: apply migrations, then serve.
# PORT is injected by the host (Render); defaults to 8000 for local runs.
#
# Migrations are best-effort at boot: a sick database (connection
# exhaustion after a crash-loop, a restart in progress) must not stop the
# API from COMING UP — a serving API can answer health checks, serve
# cached data, and retry the DB; a dead one just 502s the whole product.
# (2026-08-03: hours of continuous 502s because `set -e` + a failing
# migrate killed every boot attempt while Postgres was saturated.)
set +e
# glibc gives each worker thread its own malloc arena by default, and an
# arena never returns freed pages to the OS — so every large JSON parse in
# asyncio.to_thread ratchets RSS upward until the container hits its memory
# limit (observed 2026-08-03: 995 MB -> 1.3+ GB baseline, OOM kills at 2 GB).
# Two arenas is the standard fix for threaded CPython services.
export MALLOC_ARENA_MAX=2
# A FAILED MIGRATION DOES NOT STOP THE SERVICE AND DOES NOT PASS UNNOTICED.
#
# Serving anyway is deliberate (see above) and it is not the whole answer. On
# 2026-09-27 migration 126 rolled back on production's PostgreSQL 18, this line
# printed its one-liner, and the API came up with funded code expecting columns
# the database did not have. Availability was right; a funded submission against
# that schema would not have been, because the order would have been sent and
# the fill would have had nowhere to go.
#
# So the consequence is now stated where an operator reads it, and it is
# ENFORCED in code rather than trusted here: `bettor_funded_schema.require`
# blocks every funded submission path and the command centre reports the funded
# capability BLOCKED until the schema is actually present. Nothing else is
# withheld.
if ! python -m sportsassets.scripts.migrate; then
  echo "MIGRATE FAILED — SERVING ANYWAY (health, diagnostics and every"
  echo "  non-funded route stay up, which is how this gets diagnosed)."
  echo "  FUNDED EXECUTION IS BLOCKED while the funded schema is absent:"
  echo "  bettor_funded_schema.require refuses submit_for_decision,"
  echo "  submit_exit and cancel_outstanding, and the command centre's"
  echo "  funded section reports capability BLOCKED with the missing"
  echo "  migrations, columns and functions named."
  echo "  Migrations are retried on the next boot; this process does not"
  echo "  apply them on demand."
fi
exec uvicorn sportsassets.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
