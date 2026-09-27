#!/bin/bash
# THE FROZEN-CHECKOUT GATE. Safe cleanup, monitored environment, invalid runs
# that produce NO usable number.
#
# WHY THIS EXISTS AT ALL. On 2026-09-27 PostgreSQL died mid-comparison because
# 43 stale checkouts had filled the disk. Every database test then failed and the
# output read exactly like a large code regression. THAT is the failure this
# script is built around: an invalid environment that produces a NUMBER instead
# of an error. A gate that can do that is worse than no gate, because the number
# gets quoted.
#
# THREE RULES, AND THE FIRST TWO ARE ABOUT NOT DESTROYING EVIDENCE.
#
#   1  NEVER DELETE AN ACTIVE RUN OR A BASELINE. "Keep the newest two" is not a
#      safety rule -- a concurrent baseline run can be older than two newer
#      directories and would be deleted underneath itself. So every checkout
#      carries a LOCK naming its PID, a live lock is never touched whatever its
#      age, and the baseline directory is protected by name.
#   2  PRESERVE BEFORE PRUNING. Results and identity lists are copied into the
#      repository's evidence directory before anything is removed.
#   3  AN INVALID ENVIRONMENT PRODUCES AN INVALID GATE. The database is sampled
#      THROUGHOUT the run, not once at the end; migrations are checked for
#      failure rather than silently tolerated; and when either is wrong the
#      script removes any identity list it may have written and exits non-zero,
#      so there is nothing to mistake for a result.
#
#   usage: frozen_gate.sh <sha> <dir> <db> <log>
#   env:   MIN_FREE_MB=6000  KEEP_CHECKOUTS=2  PROTECT_DIRS="/tmp/gate_base ..."
#          SAMPLE_EVERY_S=15
set -uo pipefail

SHA="${1:?sha}"; DIR="${2:?dir}"; DB="${3:?db}"; LOG="${4:?log}"
REPO="${REPO:-/home/user/SportsAssets}"
MIN_FREE_MB=${MIN_FREE_MB:-6000}
KEEP_CHECKOUTS=${KEEP_CHECKOUTS:-2}
SAMPLE_EVERY_S=${SAMPLE_EVERY_S:-15}
PROTECT_DIRS="${PROTECT_DIRS:-}"
EVIDENCE="$REPO/research/evidence/gate"
LOCK="$DIR/.gate_lock"
HEALTH="$LOG.health"

void() {                        # an invalid gate leaves NOTHING usable behind
  rm -f "$LOG.ids"
  echo "GATE VOID: $*" >&2
  echo "GATE VOID: $*" >> "$HEALTH" 2>/dev/null || true
  exit "${2:-91}"
}
fail() { echo "GATE PREFLIGHT FAILED: $*" >&2; exit 90; }

# ── 1 · PRESERVE, BEFORE ANYTHING IS REMOVED ────────────────────────
mkdir -p "$EVIDENCE"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
for f in /tmp/*.ids /tmp/*.health; do
  [ -e "$f" ] || continue
  cp -n "$f" "$EVIDENCE/$(basename "$f").$STAMP" 2>/dev/null || true
done

# ── 2 · SAFE PRUNING ────────────────────────────────────────────────
#
# A directory is disposable only if ALL of these hold:
#   * it is not this run's directory;
#   * it is not named in PROTECT_DIRS (the baseline);
#   * it holds no lock, or its lock names a PID that is gone;
#   * it is outside the newest KEEP_CHECKOUTS.
is_locked_live() {
  local lk="$1/.gate_lock"
  [ -f "$lk" ] || return 1
  local pid
  pid=$(head -1 "$lk" 2>/dev/null | tr -dc '0-9')
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}
protected() {
  local d="$1"
  [ "$d" = "$DIR" ] && return 0
  for p in $PROTECT_DIRS; do [ "$d" = "$p" ] && return 0; done
  is_locked_live "$d" && return 0
  return 1
}

KEPT=0
PRUNED=0
while IFS= read -r d; do
  [ -d "$d" ] || continue
  if protected "$d"; then
    echo "keeping (active, baseline or this run): $d"
    continue
  fi
  if [ "$KEPT" -lt "$KEEP_CHECKOUTS" ]; then
    KEPT=$((KEPT + 1)); continue
  fi
  rm -rf "$d" && PRUNED=$((PRUNED + 1))
done < <(ls -1dt /tmp/gate_* 2>/dev/null)
echo "pruned=$PRUNED kept_newest=$KEPT protected_by_lock_or_name=yes"

# Scratch databases from previous gates, same care: never this run's, and never
# one a live backend is connected to.
for d in $(psql -lqt 2>/dev/null | cut -d'|' -f1 | tr -d ' ' \
           | grep -E '^gate_' | head -60); do
  [ "$d" = "$DB" ] && continue
  busy=$(psql -qtA -c \
    "SELECT count(*) FROM pg_stat_activity WHERE datname='$d'" 2>/dev/null \
    | tr -dc '0-9')
  [ "${busy:-1}" != "0" ] && { echo "keeping busy database: $d"; continue; }
  dropdb --if-exists "$d" >/dev/null 2>&1 || true
done

# ── 3 · PREFLIGHT. IT REFUSES RATHER THAN DEGRADES ──────────────────
FREE_MB=$(df -Pm / | awk 'NR==2{print $4}')
echo "preflight: free=${FREE_MB}MB required=${MIN_FREE_MB}MB"
[ "${FREE_MB:-0}" -ge "$MIN_FREE_MB" ] \
  || fail "only ${FREE_MB}MB free. A run that exhausts the disk kills PostgreSQL and reports a count instead of an error"

if ! pg_isready -q; then
  echo "preflight: PostgreSQL is down, attempting one restart"
  pg_ctlcluster 16 main start >/dev/null 2>&1 || true
  pg_isready -q || fail "PostgreSQL is not accepting connections"
fi
git -C "$REPO" cat-file -e "${SHA}^{commit}" 2>/dev/null \
  || fail "$SHA is not a commit in this repository"
PGV=$(psql -qtA -c 'SHOW server_version' 2>/dev/null | tr -d ' ')
echo "preflight: ok. postgres=$PGV sha=$(git -C "$REPO" rev-parse --short "$SHA")"

# ── 4 · THE FROZEN CHECKOUT, LOCKED WHILE IT RUNS ───────────────────
rm -rf "$DIR"; mkdir -p "$DIR"
printf '%s\n%s\n' "$$" "$(date -u +%FT%TZ) $SHA" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT
git -C "$REPO" archive "$SHA" | tar -x -C "$DIR" || fail "archive failed"
git -C "$REPO" rev-parse "$SHA" > "$DIR/.gate_sha"

dropdb --if-exists "$DB" >/dev/null 2>&1
createdb "$DB" || fail "createdb $DB"

# MIGRATION FAILURE IS AN ENVIRONMENT FAILURE, NOT A TEST RESULT. The previous
# version passed ON_ERROR_STOP=0 and sent every error to /dev/null, so a
# migration that rolled back -- exactly the PostgreSQL 18 defect this repository
# already hit once -- produced a database missing objects and a suite full of
# plausible-looking failures. Errors are captured and counted now.
MIGLOG="$LOG.migrations"
: > "$MIGLOG"
MIG_ERRORS=0
for f in "$DIR"/backend/migrations/*.sql; do
  if ! psql -q -d "$DB" -v ON_ERROR_STOP=1 -f "$f" >>"$MIGLOG" 2>&1; then
    echo "MIGRATION FAILED: $(basename "$f")" | tee -a "$MIGLOG"
    MIG_ERRORS=$((MIG_ERRORS + 1))
  fi
done
if [ "$MIG_ERRORS" -gt 0 ]; then
  void "$MIG_ERRORS migration(s) failed; see $MIGLOG. A suite run against an
  incomplete schema measures the schema, not the release" 92
fi

# ── 5 · MONITOR THE ENVIRONMENT WHILE THE SUITE RUNS ────────────────
#
# Liveness checked only at the end cannot distinguish "the database was down for
# four minutes in the middle" from "it was up the whole time": both end up.
: > "$HEALTH"
(
  while :; do
    ts=$(date -u +%FT%TZ)
    free=$(df -Pm / | awk 'NR==2{print $4}')
    if pg_isready -q; then up=up; else up=DOWN; fi
    printf '%s pg=%s free_mb=%s\n' "$ts" "$up" "$free" >> "$HEALTH"
    sleep "$SAMPLE_EVERY_S"
  done
) & MONITOR=$!
trap 'kill "$MONITOR" 2>/dev/null; rm -f "$LOCK"' EXIT

cd "$DIR/backend" || fail "no backend in the checkout"
RN1X_TEST_DSN="postgresql://$(whoami)@/$DB" \
DATABASE_URL="postgresql://$(whoami)@/$DB" \
ADMIN_TOKEN=t \
timeout 3000 python3 -m pytest tests -q -p no:randomly \
    --timeout=120 --timeout-method=signal > "$LOG" 2>&1
RC=$?
kill "$MONITOR" 2>/dev/null

# ── 6 · WAS THE ENVIRONMENT VALID FOR THE WHOLE RUN? ────────────────
DOWN_SAMPLES=$(grep -c 'pg=DOWN' "$HEALTH" 2>/dev/null || echo 0)
MIN_FREE_SEEN=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^free_mb=/){split($i,a,"=");
                 if(m==""||a[2]<m) m=a[2]}} END{print (m==""?"?":m)}' "$HEALTH")
echo "exit=$RC sha=$(cat "$DIR/.gate_sha") db=$DB"
echo "health: pg_down_samples=$DOWN_SAMPLES min_free_mb=$MIN_FREE_SEEN samples=$(wc -l < "$HEALTH")"

[ "${DOWN_SAMPLES:-0}" -eq 0 ] \
  || void "PostgreSQL was unreachable in $DOWN_SAMPLES sample(s) DURING the run.
  The failure list measures the environment, not the release" 91
pg_isready -q \
  || void "PostgreSQL is not reachable after the run" 91
[ "${MIN_FREE_SEEN}" = "?" ] || [ "${MIN_FREE_SEEN:-0}" -ge 500 ] \
  || void "free space fell to ${MIN_FREE_SEEN}MB during the run" 91
if [ "$RC" -eq 124 ]; then
  void "the suite hit its overall timeout; the failure list is truncated" 93
fi

# ── 7 · ONLY NOW IS THERE A RESULT ──────────────────────────────────
grep -E "^(FAILED|ERROR) " "$LOG" | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' \
    | sort -u > "$LOG.ids"
cp -f "$LOG.ids" "$EVIDENCE/$(basename "$LOG").ids.$STAMP" 2>/dev/null || true
cp -f "$HEALTH" "$EVIDENCE/$(basename "$HEALTH").$STAMP" 2>/dev/null || true
tail -1 "$LOG"
echo "identities: $(wc -l < "$LOG.ids")"
echo "VALID: environment held for the whole run"
