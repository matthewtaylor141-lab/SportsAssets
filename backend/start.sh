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
python -m sportsassets.scripts.migrate || echo "migrate failed — serving anyway; will apply on next boot"
exec uvicorn sportsassets.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
