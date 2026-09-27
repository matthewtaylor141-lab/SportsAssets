#!/bin/bash
# THE FROZEN-CHECKOUT GATE, WITH A PREFLIGHT AND BOUNDED RETENTION.
#
# WHY THE PREFLIGHT EXISTS, AND IT IS NOT HYGIENE. On 2026-09-27 PostgreSQL died
# mid-comparison because the filesystem was 98% full -- 43 stale gate checkouts
# from previous runs, about 13 GB. Every database-backed test then failed, the
# output looked exactly like a large code regression, and I spent a diagnosis
# cycle on it. A release comparison must not share a disk with its own history,
# and a comparison whose environment fails halfway produces a NUMBER rather than
# an error, which is the dangerous failure: a count that means nothing.
#
# SO THE ORDER IS: preflight, preserve, prune, run.
#
#   preflight  refuse to start without headroom and a live database
#   preserve   the recorded baseline and every *.ids result are copied into the
#              repository's evidence directory BEFORE anything is deleted
#   prune      keep the N newest checkouts and drop the rest
#   run        only then
#
#   usage: gate_run8.sh <sha> <dir> <db> <log>
set -uo pipefail

SHA="$1"; DIR="$2"; DB="$3"; LOG="$4"
REPO=/home/user/SportsAssets
MIN_FREE_MB=${MIN_FREE_MB:-6000}
KEEP_CHECKOUTS=${KEEP_CHECKOUTS:-2}
EVIDENCE="$REPO/research/evidence/gate"

fail() { echo "GATE PREFLIGHT FAILED: $*" >&2; exit 90; }

# ── 1 · PRESERVE BEFORE PRUNING ─────────────────────────────────────
# Evidence is copied out first. A cleanup that runs before a save is how a
# comparison loses the thing it was run to produce.
mkdir -p "$EVIDENCE"
for f in /tmp/*.ids; do
  [ -e "$f" ] || continue
  cp -n "$f" "$EVIDENCE/$(basename "$f").$(date -u +%Y%m%dT%H%M%SZ)" 2>/dev/null || true
done

# ── 2 · BOUNDED RETENTION ───────────────────────────────────────────
mapfile -t OLD < <(ls -1dt /tmp/gate_* 2>/dev/null | tail -n +$((KEEP_CHECKOUTS + 1)))
if [ "${#OLD[@]}" -gt 0 ]; then
  echo "pruning ${#OLD[@]} stale checkout(s), keeping $KEEP_CHECKOUTS"
  rm -rf "${OLD[@]}"
fi
# Scratch databases from previous gates, same rule.
for d in $(psql -lqt 2>/dev/null | cut -d'|' -f1 | tr -d ' ' \
           | grep -E '^gate_' | head -40); do
  [ "$d" = "$DB" ] && continue
  dropdb --if-exists "$d" >/dev/null 2>&1 || true
done

# ── 3 · PREFLIGHT, AND IT REFUSES RATHER THAN DEGRADES ──────────────
FREE_MB=$(df -Pm / | awk 'NR==2{print $4}')
echo "preflight: free=${FREE_MB}MB required=${MIN_FREE_MB}MB"
[ "${FREE_MB:-0}" -ge "$MIN_FREE_MB" ] \
  || fail "only ${FREE_MB}MB free; a run that exhausts the disk kills PostgreSQL
  and reports a count instead of an error"

if ! pg_isready -q; then
  echo "preflight: PostgreSQL is down, attempting one restart"
  pg_ctlcluster 16 main start >/dev/null 2>&1 || true
  pg_isready -q || fail "PostgreSQL is not accepting connections"
fi
echo "preflight: database is up"

git -C "$REPO" cat-file -e "${SHA}^{commit}" 2>/dev/null \
  || fail "$SHA is not a commit in this repository"

# ── 4 · THE FROZEN CHECKOUT ─────────────────────────────────────────
rm -rf "$DIR"; mkdir -p "$DIR"
git -C "$REPO" archive "$SHA" | tar -x -C "$DIR" || fail "archive failed"
git -C "$REPO" rev-parse "$SHA" > "$DIR/.gate_sha"

dropdb --if-exists "$DB" >/dev/null 2>&1
createdb "$DB" || fail "createdb $DB"
for f in "$DIR"/backend/migrations/*.sql; do
  psql -q -d "$DB" -v ON_ERROR_STOP=0 -f "$f" >/dev/null 2>&1
done

# ── 5 · RUN, AND CHECK THE ENVIRONMENT SURVIVED ─────────────────────
cd "$DIR/backend" || fail "no backend in the checkout"
RN1X_TEST_DSN="postgresql://$(whoami)@/$DB" \
DATABASE_URL="postgresql://$(whoami)@/$DB" \
ADMIN_TOKEN=t \
timeout 3000 python3 -m pytest tests -q -p no:randomly \
    --timeout=120 --timeout-method=signal > "$LOG" 2>&1
RC=$?

POST_FREE=$(df -Pm / | awk 'NR==2{print $4}')
echo "exit=$RC sha=$(cat "$DIR/.gate_sha") db=$DB free_after=${POST_FREE}MB"
if ! pg_isready -q; then
  echo "RESULT IS VOID: PostgreSQL died during the run. The failure list below" \
       "measures the environment, not the release." >&2
  exit 91
fi
if [ "${POST_FREE:-0}" -lt 500 ]; then
  echo "RESULT IS SUSPECT: only ${POST_FREE}MB free after the run." >&2
fi

grep -E "^(FAILED|ERROR) " "$LOG" | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' \
    | sort -u > "$LOG.ids"
tail -1 "$LOG"
echo "identities: $(wc -l < "$LOG.ids")"
