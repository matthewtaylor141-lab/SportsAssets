"""THE COMMAND CENTRE'S I/O SIDE. Reads evidence; decides nothing.

The read model in `sportsassets.bettor_command_center` is pure: records
in, five views out. This module is the half that touches the world --
the Postgres journal, the two ingestion_state rows, the JUnit artifacts
on disk and the deployed SHA -- and it exists separately so that every
state the page can reach is reachable in a test by handing `build()` a
list of dicts.

WHAT IT WILL NOT DO. It opens no socket, writes no row, flips no
control, spends no allowance and reads no credential. Every statement
below is a SELECT, and `test_bettor_command_center.py` reads this file's
source and fails the build if a mutating verb appears in it.

A FAILED READ IS NOT AN EMPTY RUN. Anything that cannot produce the
real records raises `RetrievalIncomplete`, which the route turns into
503. The page then says UNAVAILABLE and keeps its previous snapshot
labelled stale. It never renders a well-formed page of zeros, because a
page of zeros during an outage is the one failure that actually
misleads somebody.

Run:  python -m pytest backend/tests/test_bettor_command_center.py
"""
from __future__ import annotations

import json
import os
import time
import xml.etree.ElementTree as ET

from .. import bettor_command_center as CC

# The journal is keyed by run id; the run row names which one is ours.
JOURNAL_TABLE = "bettor_incentive_journal"

# A whole ET-date of frames is tens of thousands of rows. The page needs
# counts, extremes and per-market presence -- not every ladder -- so the
# heavy work is done in SQL and only the shaped rows come back.
LADDER_SAMPLE = 400


class RetrievalIncomplete(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__("%s: %s" % (reason, detail))
        self.reason = reason
        self.detail = detail


# ── where the evidence artifacts live ────────────────────────────────
#
# THE PRODUCTION IMAGE DOES NOT CARRY THEM. `backend/Dockerfile` copies
# `research/beta48/shadow`, the top-level JSON and exactly one
# acceptance file (incentive_manifest.json) -- deliberately, so that
# 4.9 MB of patches and suite XML stays out of a production image. So
# in the deployed API the test and economic artifacts are ABSENT, and
# every figure that depends on them renders UNKNOWN with that reason
# named. It does not render zero and it does not render a stale number
# from somewhere else.

def evidence_root() -> str:
    return os.environ.get("BETTOR_EVIDENCE_ROOT") or "research/beta48/acceptance"


def _artifact(name: str):
    path = os.path.join(evidence_root(), name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), path
    except Exception:                                          # noqa: BLE001
        return None, path


# ── JUnit ────────────────────────────────────────────────────────────

def parse_junit(path: str, *, name, kind, sha, environment,
                superseded_by=None) -> dict:
    """One JUnit file to one suite row.

    COMPLETION IS READ, NOT ASSUMED. pytest writes the XML at the end of
    a run, so a file that parses is a run that finished -- but a file
    that is absent, truncated or malformed is an INCOMPLETE run, and
    `suite_row` withholds its counts rather than showing the prefix that
    happened to survive.
    """
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:                                   # noqa: BLE001
        return CC.suite_row(name=name, kind=kind, sha=sha,
                            environment=environment, at=None, complete=False,
                            artifact=path, superseded_by=superseded_by,
                            failure_ids=[])
    suites = ([root] if root.tag == "testsuite"
              else list(root.iter("testsuite")))
    tests = failures = errors = skipped = 0
    at = None
    failure_ids, failure_detail = [], []
    for s in suites:
        tests += int(s.get("tests") or 0)
        failures += int(s.get("failures") or 0)
        errors += int(s.get("errors") or 0)
        skipped += int(s.get("skipped") or 0)
        at = at or s.get("timestamp")
        for case in s.iter("testcase"):
            bad = (list(case.iter("failure")) + list(case.iter("error")))
            if not bad:
                continue
            ident = "%s::%s" % (case.get("classname") or "",
                                case.get("name") or "")
            failure_ids.append(ident)
            msg = (bad[0].get("message") or "").strip()
            failure_detail.append({"id": ident, "message": msg[:400],
                                   "time": case.get("time")})
    row = CC.suite_row(name=name, kind=kind, sha=sha,
                       environment=environment, at=at, complete=True,
                       passed=tests - failures - errors - skipped,
                       failed=failures + errors, skipped=skipped,
                       failure_ids=failure_ids, artifact=path,
                       superseded_by=superseded_by)
    row["failures"] = failure_detail
    row["total"] = tests
    return row


# THE SUITES, DECLARED. Each one names what it actually exercised --
# unit code, a transport double, a recorded tape or the real venue --
# because "264 passed" says nothing without that.
SUITE_MANIFEST = [
    {"file": "release_tests.xml", "name": "release suite",
     "kind": CC.K_UNIT, "sha": "d630d3d",
     "environment": "CI container, python 3.12, no venue"},
    {"file": "repair_tests.xml", "name": "repair suite",
     "kind": CC.K_UNIT, "sha": "195ee01",
     "environment": "CI container, python 3.12, no venue",
     "superseded_by": "release suite @ d630d3d"},
    {"file": "gate_tests_against_defective_code.xml",
     "name": "gate, run against DELIBERATELY DEFECTIVE code",
     "kind": CC.K_SIM, "sha": "195ee01 + injected defects",
     "environment": "CI container; the 13 failures are the POINT -- this "
                    "run proves the gate fails when the code is wrong"},
]


def load_suites() -> list:
    rows = []
    for spec in SUITE_MANIFEST:
        path = os.path.join(evidence_root(), spec["file"])
        rows.append(parse_junit(
            path, name=spec["name"], kind=spec["kind"], sha=spec["sha"],
            environment=spec["environment"],
            superseded_by=spec.get("superseded_by")))
    return rows


def load_artifacts() -> dict:
    ev, ev_path = _artifact("evaluation.json")
    opp, opp_path = _artifact("incentive_opportunity.json")
    man, man_path = _artifact("incentive_manifest.json")
    return {"evaluation": ev, "opportunity": opp, "manifest": man,
            "paths": {"evaluation": ev_path, "opportunity": opp_path,
                      "manifest": man_path},
            "present": {"evaluation": ev is not None,
                        "opportunity": opp is not None,
                        "manifest": man is not None},
            "why_absent": "the production image carries only "
                          "incentive_manifest.json from acceptance/; the "
                          "rest are evidence artifacts kept out of a "
                          "production image on purpose"}


# ── the database side ────────────────────────────────────────────────

async def read_control(pool) -> tuple:
    from .. import bettor_live_control as CTL
    try:
        state = await CTL.read_control(pool)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("CONTROL_UNREADABLE",
                                  type(exc).__name__) from exc
    if state.get("why") in (CTL.W_UNREADABLE, CTL.W_MALFORMED):
        raise RetrievalIncomplete(state["why"], str(state.get("detail") or ""))
    return (not CTL.is_closed(state)), state


async def read_probe(pool) -> dict | None:
    from .. import bettor_live_control as CTL
    try:
        raw = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", CTL.BUDGET_KEY)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("PROBE_ROW_UNREADABLE",
                                  type(exc).__name__) from exc
    if raw is None:
        return None
    try:
        return json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("PROBE_ROW_MALFORMED",
                                  type(exc).__name__) from exc


async def read_run_row(pool) -> dict | None:
    from .. import bettor_incentive_state as ST
    got = await ST.read_run(pool)
    if got.get("why") in (ST.S_UNREADABLE, ST.S_MALFORMED):
        raise RetrievalIncomplete(got["why"], str(got.get("detail") or ""))
    return got.get("run")


async def read_journal(pool, run_id: str) -> list:
    """Every non-ladder record, plus a bounded, SHAPED ladder view.

    WHY NOT `read_run()`. That helper returns every row with its full
    bids/offers payload -- a day of twelve markets is tens of thousands
    of rows and tens of megabytes, and pulling it into an API process to
    count it would make the page a load-generator against the same
    database the collector is writing to. The counts, extremes and
    per-market presence are computed in SQL instead, and the ladders
    that come back carry only what the views read: `at`, `slug`,
    `boot_id`, `epoch` and whether depth was present.

    THE DEPTH TEST IS DONE IN SQL AND IT IS THE SAME TEST the pure
    function applies: a frame has depth when its bids or offers array
    is non-empty. A frame that arrived carrying neither is a frame that
    told us nothing about the book, and it stays counted apart.
    """
    try:
        meta = await pool.fetch(
            "SELECT boot_id, at, kind, epoch, slug, payload FROM "
            + JOURNAL_TABLE +
            " WHERE run_id = $1 AND kind <> 'LADDER' ORDER BY at, id",
            run_id)
        ladders = await pool.fetch(
            "SELECT boot_id, at, epoch, slug,"
            " (jsonb_array_length(COALESCE(payload->'bids','[]'::jsonb))"
            "  + jsonb_array_length(COALESCE(payload->'offers','[]'::jsonb)))"
            "   AS levels"
            " FROM " + JOURNAL_TABLE +
            " WHERE run_id = $1 AND kind = 'LADDER' ORDER BY at, id",
            run_id)
    except Exception as exc:                                   # noqa: BLE001
        raise RetrievalIncomplete("JOURNAL_UNREADABLE",
                                  type(exc).__name__) from exc

    out = []
    for r in meta:
        pay = r["payload"]
        out.append({"boot_id": r["boot_id"], "at": float(r["at"]),
                    "kind": r["kind"], "epoch": r["epoch"], "slug": r["slug"],
                    "payload": json.loads(pay)
                    if isinstance(pay, (str, bytes)) else pay})
    for r in ladders:
        n = int(r["levels"] or 0)
        out.append({"boot_id": r["boot_id"], "at": float(r["at"]),
                    "kind": "LADDER", "epoch": r["epoch"], "slug": r["slug"],
                    # Shaped, not truncated: the views ask only whether
                    # depth was persisted, and this answers exactly that.
                    "payload": {"bids": [1] * n, "offers": [],
                                "levels": n}})
    out.sort(key=lambda r: r["at"])
    return out


def deployed_identity() -> dict:
    """The SHA this process is actually running, if anything recorded it."""
    for var in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_COMMIT",
                "BETTOR_DEPLOYED_SHA"):
        val = os.environ.get(var)
        if val:
            return {"sha": val, "source": "environment %s" % var}
    return {"sha": None, "source": "environment",
            "why": "no deployment stamped a commit into this process's "
                   "environment; the running SHA is UNKNOWN rather than "
                   "guessed from the working tree, which is not what is "
                   "deployed"}


def allowlist_from(artifacts: dict) -> list:
    man = (artifacts or {}).get("manifest") or {}
    return list(((man.get("freeze_preview") or {}).get("slugs")) or [])


async def snapshot(pool=None, *, now=None) -> dict:
    """Read everything, then build. Raises RetrievalIncomplete."""
    if pool is None:
        try:
            from ..db import get_pool
            pool = await get_pool()
        except Exception as exc:                               # noqa: BLE001
            raise RetrievalIncomplete("NO_DATABASE_POOL",
                                      type(exc).__name__) from exc

    control, control_state = await read_control(pool)
    probe = await read_probe(pool)
    run_row = await read_run_row(pool)
    artifacts = load_artifacts()

    records = []
    if run_row and run_row.get("run_id"):
        records = await read_journal(pool, run_row["run_id"])

    payload = CC.build(records=records, control=control, probe=probe,
                       run_row=run_row,
                       allowlist=allowlist_from(artifacts),
                       suites=load_suites(), artifacts=artifacts,
                       deployed=deployed_identity(),
                       now=now if now is not None else time.time())
    payload["views"]["live_operation"]["control_row"] = {
        "why": control_state.get("why"),
        "read_at": CC.iso(control_state.get("read_at")),
        "source": "ingestion_state.%s"
                  % control_state.get("key", "bettor_live_observation"),
    }
    payload["evidence_availability"] = {
        "artifacts": artifacts["present"],
        "root": evidence_root(),
        "why_absent": artifacts["why_absent"],
    }
    return payload
