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
python -m sportsassets.scripts.migrate || echo "migrate failed — serving anyway; will apply on next boot"
exec uvicorn sportsassets.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
