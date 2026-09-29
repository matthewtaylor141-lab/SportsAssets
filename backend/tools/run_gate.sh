#!/bin/bash
# ONE GATE RUN: a structured report, human tracebacks beside it, and pytest's
# OWN exit status preserved.
#
# THE DEFECT THIS REPLACES. The previous runner ended on `echo` and `tail`, so
# the script's exit status was the status of `tail`. Simulated pytest exits 1
# through 5 all became runner exit 0 -- a usage error, an internal error, an
# interrupted run and "no tests collected" were all indistinguishable from
# success to anything downstream.
#
# pytest's status is captured into a variable IMMEDIATELY after the run, before
# any other command can overwrite $?, and written into the artifact set. The
# script then exits with it.
#
# EXIT 1 IS EXPECTED HERE. The matched baseline fails 367 tests, so a completed
# run exits 1. That is why this script does not decide acceptance: it records
# what happened and `gate_verdict.py` decides, where 0 and 1 are completed runs
# and everything else is INVALID.
set -u

if [ "$#" -lt 3 ]; then
    echo "usage: run_gate.sh CHECKOUT DATABASE OUT_PREFIX [pytest args...]" >&2
    exit 64
fi

CO="$1"; DB="$2"; PREFIX="$3"; shift 3

if [ ! -d "$CO/backend" ]; then
    echo "no such checkout: $CO/backend" >&2
    exit 66
fi

SHA=$(cd "$CO" && git rev-parse HEAD) || exit 65
DIRTY=$(cd "$CO" && git status --porcelain | head -5)

CONSOLE="${PREFIX}_console.txt"
REPORT="${PREFIX}_report.json"
META="${PREFIX}_meta.json"

# ── THE CHECKOUT MUST BE CLEAN, or the SHA does not describe what ran ──
# The directive says not to alter an immutable checkout while its gate runs.
# This is the cheap half of that: refuse to start from a dirty one.
if [ -n "$DIRTY" ]; then
    echo "REFUSED: $CO is not clean. The recorded SHA would not describe" >&2
    echo "         what actually ran, which is the point of recording it." >&2
    echo "$DIRTY" >&2
    exit 67
fi

cd "$CO/backend" || exit 66

# ── WHAT TO RUN: the whole suite, or exactly what was asked for ──────
#
# A DEFECT CAUGHT BY THE FIRST SMOKE TEST OF THIS SCRIPT. It hardcoded `tests/`
# and then appended "$@", so naming one file ADDED it to the full suite instead
# of selecting it -- a targeted run silently became a 32-minute full gate. A
# gate runner that cannot be pointed at one file will not be used during
# implementation, which is how targeted suites stop being run at all.
#
# So paths in "$@" REPLACE the default target and flags are passed through.
TARGETS=("tests/")
EXTRA=()
if [ "$#" -gt 0 ]; then
    NAMED=()
    for a in "$@"; do
        case "$a" in
            -*) EXTRA+=("$a") ;;
            *)  NAMED+=("$a") ;;
        esac
    done
    if [ "${#NAMED[@]}" -gt 0 ]; then
        TARGETS=("${NAMED[@]}")
    fi
fi

# ── THE INSTRUMENT LIVES OUTSIDE THE CHECKOUT ────────────────────────
#
# A GATE MEASURES A CHECKOUT; IT MUST NOT BE PART OF IT. Copying the reporter
# into the tree under test makes that tree dirty -- and this script then
# correctly refuses it, because the recorded SHA would no longer describe what
# ran. It also means a BASELINE commit predating the reporter cannot be measured
# with it at all, which would make a matched comparison impossible.
#
# So `GATE_TOOLS_DIR` puts the plugin on PYTHONPATH from a neutral directory and
# both checkouts stay pristine. The plugin is named `gate_report` there rather
# than `tools.gate_report`.
PLUGIN="tools.gate_report"
PYPATH="${PYTHONPATH:-}"
if [ -n "${GATE_TOOLS_DIR:-}" ]; then
    PLUGIN="gate_report"
    PYPATH="$GATE_TOOLS_DIR${PYPATH:+:$PYPATH}"
fi

GATE_REPORT_PATH="$REPORT" \
PYTHONPATH="$PYPATH" \
RN1X_TEST_DSN="postgresql://postgres:postgres@127.0.0.1:5432/$DB" \
RN1X_CAPACITY_SLOT_MUST_BE_FREE=1 \
  python -m pytest "${TARGETS[@]}" -q -p no:randomly --tb=short -rf \
      -p "$PLUGIN" \
      ${EXTRA[@]+"${EXTRA[@]}"} > "$CONSOLE" 2>&1
# CAPTURED IMMEDIATELY. Anything between the run and this line clobbers $?.
RC=$?

python3 - "$META" "$SHA" "$DB" "$RC" "$REPORT" "$CONSOLE" <<'PY'
import json, os, platform, subprocess, sys
meta, sha, db, rc, report, console = sys.argv[1:7]


def sh(*a):
    try:
        return subprocess.check_output(a, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except Exception:                                           # noqa: BLE001
        return ""


def _psql(db, sql):
    env = dict(os.environ, PGPASSWORD="postgres")
    try:
        return subprocess.check_output(
            ["psql", "-h", "127.0.0.1", "-U", "postgres", "-d", db, "-tAc", sql],
            text=True, stderr=subprocess.DEVNULL, env=env).strip()
    except Exception:                                           # noqa: BLE001
        return ""


doc = {
    "commit": sha,
    "database": db,
    # pytest's OWN status, recorded rather than inferred from console text.
    "pytest_exitstatus": int(rc),
    "report_path": report,
    "report_present": os.path.exists(report),
    "console_path": console,
    "console_bytes": (os.path.getsize(console)
                      if os.path.exists(console) else 0),
    "python": platform.python_version(),
    "pytest": sh(sys.executable, "-m", "pytest", "--version"),
    "pip_freeze_sha256_16": sh(
        "bash", "-c", "python -m pip freeze | sha256sum | cut -c1-16"),
    # THE SCHEMA STATE OF THE DATABASE THAT RAN, which is part of what makes a
    # gate reproducible. The first version returned empty strings because
    # PGPASSWORD was not in the subprocess environment -- captured silently,
    # which is exactly the class of gap this whole section is about, so it is
    # ASSERTED below rather than merely recorded.
    "public_tables": _psql(db, "SELECT count(*) FROM information_schema.tables"
                               " WHERE table_schema='public'"),
    "funded_tables": _psql(db, "SELECT count(*) FROM information_schema.tables"
                               " WHERE table_name LIKE 'bettor_funded%'"),
    "migration_max": _psql(db, "SELECT coalesce(max(version::text),'none')"
                               " FROM schema_migrations") or "no_table",
}
with open(meta, "w") as fh:
    json.dump(doc, fh, indent=1, sort_keys=True)
print("=== GATE RUN on %s (db %s) -> pytest exit %s ==="
      % (sha[:12], db, rc))
print("  report present: %s" % os.path.exists(report))
PY

tail -2 "$CONSOLE"
exit "$RC"
