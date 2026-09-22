#!/usr/bin/env bash
# THE INTEGRATION RUN. Builds the deployment image, builds a disposable
# database with the repository's own migration runner, and drives the
# whole observation lifecycle through the supervisor's real entry point.
#
# Each phase is a SEPARATE `docker run`, so the restart phases (L2/L10)
# cross a genuine process boundary rather than a simulated one.
#
# Usage:  scripts/bettor_lifecycle_run.sh [DSN]
#
# Everything it proves is SIMULATED TRANSPORT. It is not a live venue
# connection and must not be reported as one.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DSN="${1:-postgresql://root:imgcheck@127.0.0.1:5432/bettor_lifecycle}"
DB="$(printf '%s' "$DSN" | sed 's#.*/##')"
IMG="bettor-lifecycle:$(cd "$REPO" && git rev-parse --short HEAD)"
OUT="${BETTOR_LIFECYCLE_OUT:-$REPO/research/beta48/acceptance/lifecycle}"
mkdir -p "$OUT"

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
run() { docker run --rm --network=host -w /app \
          -e DATABASE_URL="$DSN" \
          -e PMUS_KEY_ID=not-a-credential \
          -e PMUS_SECRET_KEY=not-a-credential \
          -e MAX_CONTRACTS=0 \
          -e BETTOR_LIVE_MAX_CONTRACTS=0 \
          -v "$REPO/scripts/bettor_lifecycle_integration.py:/app/lc.py:ro" \
          -v "$REPO/scripts/bettor_image_migrate.py:/app/mg.py:ro" \
          -v "$REPO/research/beta48/acceptance:/recorded:ro" \
          "$IMG" "$@"; }

say "1. build the production Dockerfile, unmodified"
( cd "$REPO" && git diff --quiet -- backend/Dockerfile ) \
  || { echo "REFUSING: backend/Dockerfile is modified"; exit 2; }
docker build --network=host -q -f "$REPO/backend/Dockerfile" -t "$IMG" "$REPO" \
  > "$OUT/build.txt" 2>&1 || { tail -30 "$OUT/build.txt"; exit 2; }
IMAGE_ID="$(docker image inspect "$IMG" --format '{{.Id}}')"
echo "image $IMG  $IMAGE_ID"
echo "$IMAGE_ID" > "$OUT/image_id.txt"

say "2. a disposable database, built by the repository's migration runner"
psql -U root -d postgres -q -c "DROP DATABASE IF EXISTS $DB" || exit 2
psql -U root -d postgres -q -c "CREATE DATABASE $DB"          || exit 2
run python mg.py > "$OUT/migrate.txt" 2>&1
grep -E "APPLIED_COUNT|APPLIED_FIRST|APPLIED_LAST|RUNTIME_SUPPLIED|^  [0-9] \|" \
     "$OUT/migrate.txt" || { tail -30 "$OUT/migrate.txt"; exit 2; }

say "3. the lifecycle"
FAILED=()
for P in L1 L3 L4 L5 L6 L7 L8 L9 L11; do
  printf '\n--- phase %s ---\n' "$P"
  run python lc.py --phase "$P" > "$OUT/$P.txt" 2>&1
  rc=$?
  grep -E "^\s+\[(PASS|FAIL)\]|^   [a-z].*:|all [0-9]+ checks|FAILED [0-9]+" \
       "$OUT/$P.txt" | sed 's/^/  /'
  [ $rc -eq 0 ] || { FAILED+=("$P"); echo "  PHASE $P EXIT $rc"; }
done

say "4. restart across a real process boundary (L2 + L10)"
run python lc.py --phase L2A > "$OUT/L2A.txt" 2>&1
rc=$?
grep -E "^\s+\[(PASS|FAIL)\]|PROCESS_1_STATE" "$OUT/L2A.txt" | sed 's/^/  /'
STATE="$(grep -o 'STATE_JSON:.*' "$OUT/L2A.txt" | sed 's/^STATE_JSON://')"
[ $rc -eq 0 ] && [ -n "$STATE" ] || { FAILED+=("L2A"); echo "  L2A EXIT $rc"; }
if [ -n "$STATE" ]; then
  echo "  --- a NEW container, a NEW interpreter, the same database ---"
  run python lc.py --phase L2B --state "$STATE" > "$OUT/L2B.txt" 2>&1
  rc=$?
  grep -E "^\s+\[(PASS|FAIL)\]|recovered_at_start" "$OUT/L2B.txt" | sed 's/^/  /'
  [ $rc -eq 0 ] || { FAILED+=("L2B"); echo "  L2B EXIT $rc"; }
fi

say "RESULT"
TOTAL=$(grep -ho '^\s*\[PASS\]' "$OUT"/L*.txt 2>/dev/null | wc -l)
BAD=$(grep -ho '^\s*\[FAIL\]' "$OUT"/L*.txt 2>/dev/null | wc -l)
echo "checks passed: $TOTAL    checks failed: $BAD"
if [ ${#FAILED[@]} -ne 0 ]; then
  echo "PHASES FAILED: ${FAILED[*]}"
  exit 1
fi
echo "THE FULL LIFECYCLE PASSES on image $IMAGE_ID"
echo "SIMULATED TRANSPORT. This does not establish venue connectivity."
