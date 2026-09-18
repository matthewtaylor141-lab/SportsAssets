#!/usr/bin/env python3
"""REHEARSE THE ACTUAL EVIDENCE COMMAND, from the workflow's own text.

WHAT THIS IS FOR. The gather step's shell is where three different
outcomes have to be told apart -- a clear proposal, a blocked proposal,
and a failed command -- and the version it replaced collapsed all three
into `|| echo` and a green step. A test of the Python function proves
nothing about that shell.

So this reads the `run:` text of the step OUT OF THE WORKFLOW FILE,
substitutes the GitHub expressions, and executes it under bash. The
command it runs is the command the runner runs.

WHAT IS MOCKED AND WHAT IS NOT.

    MOCKED   the venue SDK transport (account.balances, markets.*,
             orders.list, orders.preview) and the asyncpg pool.
    REAL     calibration_evidence's CLI and argument parsing, the fee
             arithmetic, the coordination check, and
             calibration_store.load -- which is a COROUTINE, awaited
             through the real `_durable_session`. The defect this is
             here to catch is exactly that: the previous version
             returned the coroutine object, never touched a database,
             and would have sized a ticket against a truthy object.

NOTHING REACHES A VENUE OR A DATABASE. The shim raises if anything tries
to open a socket.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
WORKFLOW = ROOT / ".github" / "workflows" / "calibration-evidence.yml"
STEP = "Gather the evidence and propose the ticket"

MARKET = "aec-atp-sin-alc-2026-09-18"

INPUTS = {
    "market": MARKET,
    "outcome_side": "LONG",
    "max_exit_orders": "1",
    "partial_fills": "true",
    "exit_price_limit": "0.35",
    "entry_cancellation_deadline": "2026-09-18T21:00:00Z",
    "residual_inventory_fallback": "hold to settlement; no re-entry",
    "hold_remainder_through_settlement": "true",
}
RUN_ID = "77777"


def step_command():
    """The step's `run:` text, from the workflow file."""
    import yaml
    doc = yaml.safe_load(WORKFLOW.read_text())
    for job in doc["jobs"].values():
        for s in job.get("steps", ()):
            if s.get("name") == STEP:
                return s["run"], s.get("working-directory")
    raise SystemExit("STEP_NOT_FOUND: %s" % STEP)


def substitute(text):
    """Evaluate the GitHub expressions the runner would have evaluated."""
    for key, val in INPUTS.items():
        text = text.replace("${{ github.event.inputs.%s }}" % key, val)
    text = text.replace("${{ github.run_id }}", RUN_ID)
    # the two conditional flag expressions
    text = re.sub(
        r"\$\{\{ github\.event\.inputs\.partial_fills == 'true' "
        r"&& '--partial-fills' \|\| '' \}\}",
        "--partial-fills" if INPUTS["partial_fills"] == "true" else "", text)
    text = re.sub(
        r"\$\{\{ github\.event\.inputs\.hold_remainder_through_settlement "
        r"== 'true' && '--hold-remainder-through-settlement' \|\| '' \}\}",
        "--hold-remainder-through-settlement"
        if INPUTS["hold_remainder_through_settlement"] == "true" else "", text)
    leftover = re.findall(r"\$\{\{[^}]*\}\}", text)
    if leftover:
        raise SystemExit("UNSUBSTITUTED_EXPRESSIONS: %s" % leftover)
    return text


SHIM = '''
"""Mocked TRANSPORTS only. The store and the CLI are the real ones."""
import asyncio, os, socket, sys

SCENARIO = os.environ["REHEARSAL_SCENARIO"]

# NOTHING OPENS A SOCKET.
def _no_sockets(*a, **k):
    raise AssertionError("REHEARSAL_TRIED_TO_OPEN_A_SOCKET")
socket.socket.connect = _no_sockets

from sportsassets import pmus, calibration_store, calibration_domain

MARKET = "%(market)s"

class _Markets:
    def retrieve_by_slug(self, slug):
        if SCENARIO == "venue_unreadable":
            raise RuntimeError("venue 503")
        return {"market": {
            "slug": MARKET, "title": "Sinner vs Alcaraz",
            "closeTime": "2026-09-18T23:00:00Z",
            "orderPriceMinTickSize": "0.01", "minimumTradeQty": 1,
            "marketSides": [
                {"long": True, "identifier": "sin",
                 "description": "Sinner to win"},
                {"long": False, "identifier": "sin",
                 "description": "Sinner not to win"}]}}
    def bbo(self, slug):
        return {"marketData": {"bestBid": 0.39, "bestAsk": 0.40}}

class _Account:
    def balances(self):
        return {"balances": [{"currency": "USD",
                              "currentBalance": {"value": "5000.00"},
                              "buyingPower": {"value": "7500.00"}}]}

class _Orders:
    def list(self, params=None):
        return {"orders": []}
    def preview(self, req):
        if SCENARIO == "fee_disagreement":
            return {"order": {"expectedFee": {"value": "9.99",
                                              "currency": "USD"},
                              "executionRole": "TAKER"}}
        from sportsassets import calibration_fees as cf
        r = req["request"]
        qty = r["quantity"]
        px = r["price"]["value"]
        fee = cf.expected_fee(px, qty, role=cf.ROLE_TAKER)["FEE"]
        return {"order": {"expectedFee": {"value": str(fee),
                                          "currency": "USD"},
                          "executionRole": "TAKER"}}

class _Client:
    markets = _Markets(); account = _Account(); orders = _Orders()

pmus._get_client = lambda: _Client()

# THE REAL COROUTINE, over a mocked pool. `load` is not replaced.
class _Pool:
    async def fetchrow(self, sql, *a):
        if "FROM calibration_sessions" in sql:
            if SCENARIO == "db_down":
                raise RuntimeError("connection refused")
            return {"session_id": "MICRO-EXEC-CAL-1",
                    "experiment": "calibration", "authorised_by": "matt",
                    "max_all_in_usd": 5.00, "max_spend_usd": 100.00,
                    "max_open": 1, "spent_usd": 0.0, "stopped": False,
                    "stopped_at": None, "stopped_by": None,
                    "stop_reason": None}
        return None
    async def fetch(self, sql, *a):
        return []
async def _pool():
    return _Pool()
calibration_store.get_pool = _pool

# Coordination: a real census shape over a mocked fetch.
def _fetch(url):
    wf = url.split("/workflows/")[1].split("/runs")[0]
    if SCENARIO == "domain_busy" and wf == "run85-phase2-capture":
        return {"workflow_runs": [{"id": 1, "name": "run85-phase2-capture",
                                   "status": "in_progress"}],
                "total_count": 1}
    if wf == "calibration-evidence":
        return {"workflow_runs": [{"id": int(os.environ["GITHUB_RUN_ID"]),
                                   "name": "calibration-evidence",
                                   "status": "in_progress"}],
                "total_count": 1}
    return {"workflow_runs": [], "total_count": 0}

_real = calibration_domain.coordination

def _coord(**kw):
    if SCENARIO == "crash":
        # AN UNHANDLED EXCEPTION mid-command. Python exits 1 for this,
        # which is the SAME code as a blocked proposal -- so the exit
        # code alone cannot tell them apart, and the step's report check
        # is what does.
        raise MemoryError("the process died")
    return _real(fetch=_fetch, repo="o/r", root=%(root)r,
                 **{k: v for k, v in kw.items() if k == "require_reservation"})

calibration_domain.coordination = _coord

if SCENARIO == "malformed_report":
    # TRUNCATED ON THE WAY OUT, the way a write that dies mid-flush
    # leaves a file. Patched on `json` itself rather than on
    # calibration_evidence: `python -m pkg.mod` runs the module as
    # __main__, a SECOND module object, so patching the imported copy
    # would not reach the one the command actually executes.
    import json as _json
    _json.dumps = lambda *a, **k: '{"marketId": "x"'

'''


def rehearse(scenario, workdir, command, cwd):
    shim = workdir / "sitecustomize.py"
    shim.write_text(SHIM % {"market": MARKET, "root": str(ROOT)})

    env = dict(os.environ)
    env.update({
        "PYTHONPATH": "%s%s%s" % (workdir, os.pathsep, str(HERE)),
        "REHEARSAL_SCENARIO": scenario,
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_WORKFLOW": "calibration-evidence",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_TOKEN": "t",
        "PMUS_DOMAIN_RESERVATION": "pmus-public-read-global",
        "PMUS_KEY_ID": "ABCD1234EFGH",
        "PMUS_SECRET_KEY": "never-logged",
        "DATABASE_URL": "postgresql://rehearsal/none",
    })
    if scenario == "no_reservation":
        env.pop("PMUS_DOMAIN_RESERVATION")
    proc = subprocess.run(["bash", "-c", command], cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=300)
    report = None
    out = cwd / ("evidence_%s.json" % RUN_ID)
    if out.exists() and out.stat().st_size:
        try:
            report = json.loads(out.read_text())
        except ValueError:
            report = "MALFORMED"
        out.unlink()
    diag = cwd / ("diagnostic_%s.txt" % RUN_ID)
    diag_text = diag.read_text() if diag.exists() else ""
    if diag.exists():
        diag.unlink()
    return proc.returncode, proc.stdout, proc.stderr, report, diag_text


SCENARIOS = (
    ("clear", 0, "PROPOSAL_CLEAR",
     "every fact obtainable and the preview agrees: a clear proposal"),
    ("fee_disagreement", 0, "PROPOSAL_BLOCKED",
     "the venue's preview disagrees with the schedule: BLOCKED, and the "
     "step still succeeds because the command worked"),
    ("venue_unreadable", 0, "PROPOSAL_BLOCKED",
     "the market read fails: named blockers, a valid report, blocked"),
    ("domain_busy", 0, "PROPOSAL_BLOCKED",
     "run85 is executing: nothing is read and the proposal is blocked"),
    ("no_reservation", 0, "PROPOSAL_BLOCKED",
     "no execution reservation: nothing is read"),
    ("db_down", 0, "PROPOSAL_BLOCKED",
     "the durable budget is unreadable: refused, no invented session"),
    ("crash", 1, "FAILED",
     "an unhandled exception mid-command: Python exits 1, the SAME code "
     "as a blocked proposal, and the report check is what tells them "
     "apart -- no report written, so the step fails"),
    ("malformed_report", 1, "FAILED",
     "a report missing required keys: a FAILED COMMAND, not a blocked "
     "proposal dressed up as one"),
)


def main():
    raw, workdir_rel = step_command()
    command = substitute(raw)
    cwd = ROOT / (workdir_rel or ".")

    print("=" * 74)
    print("REHEARSING THE WORKFLOW'S OWN COMMAND TEXT")
    print("  workflow  %s" % WORKFLOW.relative_to(ROOT))
    print("  step      %s" % STEP)
    print("  cwd       %s" % cwd.relative_to(ROOT))
    print("=" * 74)

    ok = True
    for scenario, want_rc_class, want_marker, why in SCENARIOS:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="rehearse-ev-"))
        try:
            rc, out, err, report, diag = rehearse(scenario, tmp, command, cwd)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        if want_marker == "FAILED":
            good = rc != 0
            saw = "FAILED rc=%d" % rc
        else:
            good = rc == 0 and want_marker in out and isinstance(report, dict)
            saw = ("rc=%d %s report=%s" % (
                rc, want_marker if want_marker in out else "MARKER_MISSING",
                "yes" if isinstance(report, dict) else report))
        # NO SECRET IN THE DIAGNOSTIC OR THE LOG.
        leaked = [s for s in ("never-logged", "postgresql://rehearsal/none")
                  if s in (out + err + (diag or ""))]
        if leaked:
            good, saw = False, saw + " SECRET_LEAKED=%s" % leaked
        ok = ok and good
        print("%-20s %-28s %s" % (scenario, saw, "PASS" if good else "FAIL"))
        print("    %s" % why)
        if not good:
            print("    stdout: %s" % out.strip()[-600:])
            print("    stderr: %s" % err.strip()[-600:])

    print("=" * 74)
    print("EVIDENCE_COMMAND_REHEARSAL = %s" % ("PASS" if ok else "FAIL"))
    print("THE_COMMAND_REHEARSED_IS_THE_COMMAND_IN_THE_WORKFLOW = YES")
    print("VENUE_CONTACTED = NO   DATABASE_CONTACTED = NO   ORDERS = 0")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
