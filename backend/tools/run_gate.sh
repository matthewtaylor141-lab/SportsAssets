#!/bin/bash
# THE GATE, WITH TRACEBACKS. `--tb=no` was the defect: a failure that occurs
# once in six runs yields a node id and nothing else, so the one chance to
# diagnose it is thrown away by the flag rather than lost to the code. The
# tests already dump their own diagnostic state on assertion failure; this
# keeps it. `-rf` still gives the clean node-id list the comparator parses.
set -u
CO="$1"; DB="$2"; OUT="$3"
SHA=$(cd "$CO" && git rev-parse HEAD)
echo "$SHA" > "${OUT%.txt}_sha.txt"
cd "$CO/backend" || exit 1
RN1X_TEST_DSN="postgresql://postgres:postgres@127.0.0.1:5432/$DB" \
RN1X_CAPACITY_SLOT_MUST_BE_FREE=1 \
  python -m pytest tests/ -q -p no:randomly --tb=short -rf > "$OUT" 2>&1
echo "=== GATE DONE on $SHA ==="
tail -2 "$OUT"
