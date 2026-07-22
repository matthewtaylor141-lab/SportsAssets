#!/bin/sh
# API entrypoint: apply migrations, then serve.
# PORT is injected by the host (Render); defaults to 8000 for local runs.
set -e
python -m sportsassets.scripts.migrate
exec uvicorn sportsassets.api.app:app --host 0.0.0.0 --port "${PORT:-8000}"
