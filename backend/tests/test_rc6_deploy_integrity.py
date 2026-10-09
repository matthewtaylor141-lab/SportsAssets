"""MIGRATION_INTEGRITY AND RELEASE: THE HALF THE SERVING API CANNOT HOLD,
HELD BY THE JUDGE AS ATTESTED EVIDENCE (RC6, 2026-10-08).

Production evidence, pm-acceptance 37836393458 on release 69a8a07e:

  * MIGRATION_INTEGRITY UNKNOWN, FRESH_DB_RESULT_NOT_IN_THIS_PROCESS, with
    applied 224 = repo 224, applied fingerprint f442188e... = repo
    fingerprint, nothing edited in place -- the scorecard's Deployment
    infrastructure row failed at 5/6 on that one unit. The API cannot build
    an empty database; capital-critical builds one on the exact SHA with
    production's runner and kept nothing of it but its conclusion.
  * RELEASE UNKNOWN, NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA: receipts are
    POSTed only on post_receipt == "on" (C4), although the run had read
    every fact a receipt carries.

What these tests hold:

  * capital-critical writes a fresh-database receipt (tools/
    fresh_db_receipt.py build) whose fingerprint is EXACTLY the API
    control's fingerprint -- proven against a real migrated PostgreSQL by
    running the workflow step as written; a failed build is a FAILED
    receipt, never none; the receipt is attested and kept.
  * pm-acceptance downloads THAT receipt from the capital-critical run the
    CI row reads, verifies its attestation and records it (run as written
    with a fake gh); it judges nothing.
  * the scorecard binds it: absent / unverified / another SHA, workflow or
    run / self-inconsistent = UNPROVEN (failed); a FAILED build or a
    fingerprint that differs from the running API = RED (failed); PROVEN
    with the API's sole FRESH_DB_RESULT_NOT_IN_THIS_PROCESS = GREEN. The
    judge never overrules an API finding.
  * the release receipt the sender would POST is built on every run and
    judged by the API's own rules (pinned against the endpoint itself);
    the RELEASE unit passes only on a GREEN verdict for the release SHA: a
    non-GREEN release never reads GREEN, and nothing is POSTed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
from urllib.parse import urlparse

import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CC_WF = ROOT / ".github" / "workflows" / "capital-critical.yml"
PM_WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"
DSN = os.environ.get("RN1X_TEST_DSN", "")


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        name, BACKEND / "tools" / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FD = _tool("fresh_db_receipt")
RV = _tool("release_verdict")
SC = _tool("scorecard_14")
EP = _tool("evidence_packet")

#: release 69a8a07e and its capital-critical gate run (pm-acceptance
#: 37836393458 gates.json)
REL = "69a8a07e5335864bd3d70f7160494aed13305fcc"
BASE = "a91be09f125f0ab0d1f29a0d0866b5cb6f5765fd"
OTHER = "08828d04766017e520ffd4a34740162a2c792d36"
GATE_RUN = 37803641023
REPO = "matthewtaylor141-lab/SportsAssets"
MAP = {"001_init.sql": "a" * 64, "002_more.sql": "b" * 64,
       "315_red_team_closeout.sql": "c" * 64}


def _w(acc, name, obj):
    (acc / name).write_text(json.dumps(obj))


def _mig(fp, *, status="UNKNOWN", blockers=("FRESH_DB_RESULT_NOT_IN_"
                                             "THIS_PROCESS",), n=None,
         **over):
    """The production MIGRATION_INTEGRITY control (37836393458 shape)."""
    ev = {"applied": len(MAP) if n is None else n,
          "repo": len(MAP) if n is None else n, "edited_in_place": [],
          "applied_not_in_build": [], "not_yet_applied": [],
          "applied_fingerprint": fp, "repo_fingerprint": fp,
          "fresh_db": "UNPROVEN_HERE"}
    ev.update(over)
    return {"control": "MIGRATION_INTEGRITY", "status": status,
            "blockers": list(blockers), "evidence": ev}


def _release_ctl(*, status="UNKNOWN",
                 blockers=("NO_RELEASE_RECEIPT_FOR_THE_RUNNING_SHA",),
                 running=REL):
    return {"control": "RELEASE", "status": status,
            "blockers": list(blockers),
            "evidence": {"running_sha": running, "receipt": {}}}


def _acc(tmp_path, *, mig=None, release=None, running=REL, receipt="OK",
         prov=None, gate_run=GATE_RUN, workers=REL, gates_green=True,
         descendant=True, release_branch=REL):
    """A packet shaped like pm-acceptance 37836393458 (69a8a07e)."""
    acc = tmp_path / "acc"
    acc.mkdir(parents=True)
    fp = SC._fingerprint(MAP)
    _w(acc, "red_team.json", {"status": "OK", "data": {"readiness": {
        "implementation_sha": running,
        "read_timings": {"migrations": {"ok": True},
                         "release_receipt": {"ok": True}},
        "controls": {"MIGRATION_INTEGRITY": mig or _mig(fp),
                     "RELEASE": release or _release_ctl(running=running)}}}})
    runs = {k: {"id": i, "status": "completed",
                "conclusion": "success" if gates_green else "failure",
                "head_sha": REL}
            for k, i in (("backend_tests", 37803641044),
                         ("capital_critical", gate_run),
                         ("commit_guard", 37803641037),
                         ("engine_diagnostic", 37803641160))}
    _w(acc, "gates.json", {"backend_tests_green": gates_green,
                           "capital_critical_green": gates_green,
                           "commit_guard_green": gates_green,
                           "engine_diagnostic_green": gates_green,
                           "runs": runs})
    _w(acc, "lineage.json", {"accepted_base_sha": BASE, "sha": REL,
                             "descendant_of_base": descendant,
                             "release_branch": "claude/release-api",
                             "release_sha": release_branch})
    _w(acc, "render.json", {
        "sportsassets-api": {"live_commit": running},
        "sportsassets-workers": {"live_commit": workers},
        "sportsassets-market-plane": {"live_commit": REL}})
    _w(acc, "canary.json", {"boots": {"workers_boot": {"commit_sha": REL}}})
    _w(acc, "venues.json", {"status": "OK", "data": {"health": {
        "KALSHI_HEALTH": {"mechanism": {"plane": {
            "present": True, "commit": REL, "mode": "UMP_AND_KALSHI_WS",
            "process_locked": True, "age_s": 33.2}}}}}})
    if receipt is not None:
        rec = FD.build(dict(MAP), dict(MAP), sha=REL, run_id=str(GATE_RUN),
                       run_attempt="1", build_outcome="success",
                       server_version="16.10") if receipt == "OK" \
            else receipt
        p = tmp_path / "fresh-db-receipt.json"
        p.write_text(json.dumps(rec))
        run = {"id": GATE_RUN, "path": ".github/workflows/capital-critical.yml",
               "repository": {"full_name": REPO}, "head_sha": REL,
               "event": "push", "status": "completed",
               "conclusion": "success"}
        run.update(prov or {})
        doc = FD.readback(run_id=str(run.pop("_run_id", GATE_RUN)), run=run,
                          receipt_path=str(p),
                          attestation_verified=run.pop("_att", True))
        _w(acc, "fresh_db.json", doc)
    return acc


def _cat(out, name):
    return next(c for c in out["categories"] if c["category"] == name)


def _unit(out, cat, unit):
    return next(u for u in _cat(out, cat)["units"] if u["unit"] == unit)


def _score(acc):
    return SC.score(str(acc), release_sha=REL)


# ── 1. the receipt's fingerprint IS the API control's fingerprint ─────────

def test_the_receipt_fingerprints_exactly_as_the_api_control_does():
    from sportsassets.redteam import controls as C
    from sportsassets.scripts import migrate as M
    tree = FD.tree_migrations(BACKEND / "migrations")
    assert tree == C.repo_migrations() and len(tree) > 200
    assert all(tree[p.name] == M.content_sha(p.read_text())
               for p in (BACKEND / "migrations").glob("*.sql"))
    api = C.migrations(dict(tree), tree, fresh_db_passed=None)["evidence"]
    assert FD.fingerprint(tree) == api["applied_fingerprint"] \
        == api["repo_fingerprint"] == SC._fingerprint(tree)
    # one changed content hash moves it, in all three implementations
    moved = dict(tree, **{sorted(tree)[0]: "0" * 64})
    assert FD.fingerprint(moved) == SC._fingerprint(moved) == \
        C.migrations(moved, moved, fresh_db_passed=None)["evidence"][
            "applied_fingerprint"] != FD.fingerprint(tree)


def test_a_complete_fresh_build_is_passed_with_its_full_map():
    r = FD.build(dict(MAP), dict(MAP), sha=REL, run_id="7",
                 build_outcome="success", server_version="16.10")
    assert r["result"] == FD.PASSED and r["reasons"] == []
    assert r["migrations_applied"] == r["migrations_in_tree"] == 3
    assert r["fingerprint"] == r["tree_fingerprint"] == FD.fingerprint(MAP)
    assert r["applied"] == MAP and r["version"] == FD.VERSION
    assert r["runner"] == "python -m sportsassets.scripts.migrate"


@pytest.mark.parametrize("applied,outcome,needle", [
    (dict(MAP), "failure", FD.R_BUILD_STEP_FAILED + ":failure"),
    (dict(MAP), None, FD.R_BUILD_STEP_FAILED + ":None"),
    ({k: v for k, v in MAP.items() if k != "002_more.sql"}, "success",
     FD.R_NOT_APPLIED + ":002_more.sql"),
    (dict(MAP, **{"999_x.sql": "d" * 64}), "success",
     FD.R_APPLIED_NOT_IN_TREE + ":999_x.sql"),
    (dict(MAP, **{"001_init.sql": "e" * 64}), "success",
     FD.R_CONTENT_DIFFERS + ":001_init.sql"),
    (None, "success", FD.R_APPLIED_UNREADABLE),
    ({"001_init.sql": 5}, "success", FD.R_APPLIED_UNREADABLE),
])
def test_a_failed_or_partial_build_is_a_failed_receipt_never_none(
        applied, outcome, needle):
    r = FD.build(applied, dict(MAP), sha=REL, build_outcome=outcome)
    assert r["result"] == FD.FAILED and needle in r["reasons"]


def test_a_receipt_without_a_full_sha_or_a_tree_is_failed():
    assert FD.R_NOT_A_FULL_SHA in FD.build(
        dict(MAP), dict(MAP), sha="69a8a07", build_outcome="success")[
            "reasons"]
    assert FD.R_TREE_HAS_NO_MIGRATIONS in FD.build(
        {}, {}, sha=REL, build_outcome="success")["reasons"]


def _cc_steps():
    return yaml.safe_load(CC_WF.read_text())["jobs"]["suite"]["steps"]


def _cc_step(prefix):
    hits = [s for s in _cc_steps() if s.get("name", "").startswith(prefix)]
    assert len(hits) == 1, prefix
    return hits[0]


@pytest.fixture()
def scratch_db():
    """An EMPTY database on the test server (RN1X_TEST_DSN: capital-
    critical's own PostgreSQL 16 in CI), dropped afterwards."""
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    import asyncio
    import uuid

    import asyncpg
    name = "rc6_fresh_" + uuid.uuid4().hex[:10]

    async def admin(sql):
        c = await asyncpg.connect(DSN, timeout=10)
        try:
            await c.execute(sql)
        finally:
            await c.close()
    asyncio.run(admin('CREATE DATABASE "%s"' % name))
    try:
        yield DSN.rsplit("/", 1)[0] + "/" + name
    finally:
        asyncio.run(admin('DROP DATABASE IF EXISTS "%s" WITH (FORCE)'
                          % name))


def _receipt_step(tmp_path, dsn, outcome):
    """The workflow's receipt step, run by bash as written, pointed at
    `dsn` instead of the job's capital_critical database."""
    u = urlparse(dsn)
    script = _cc_step("The fresh-database receipt")["run"]
    conn = "-h 127.0.0.1 -U postgres -d capital_critical"
    assert script.count(conn) == 2
    script = script.replace(conn, "-h %s -p %s -U %s -d %s" % (
        u.hostname, u.port or 5432, u.username, u.path.lstrip("/")))
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "python").symlink_to(sys.executable)
    out_file = tmp_path / "gh_output"
    env = {"PATH": "%s:%s" % (shim, os.environ["PATH"]),
           "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path),
           "GITHUB_OUTPUT": str(out_file), "GITHUB_SHA": REL,
           "GITHUB_RUN_ID": str(GATE_RUN), "GITHUB_RUN_ATTEMPT": "1",
           "BUILD_OUTCOME": outcome}
    if u.password:
        env["PGPASSWORD"] = u.password
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                        "-c", script], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert out_file.read_text().strip() == "receipt=%s" % (
        tmp_path / "fresh-db-receipt.json")
    return json.loads((tmp_path / "fresh-db-receipt.json").read_text()), r


def test_the_capital_critical_step_over_a_fresh_build_by_productions_runner(
        tmp_path, scratch_db):
    """An empty database built by `python -m sportsassets.scripts.migrate`
    (as the build step does), then the receipt step as written: PASSED,
    every file of the tree, and the fingerprint the API's own control
    computes over the same rows."""
    import asyncio

    import asyncpg
    from sportsassets.redteam import controls as C
    built = subprocess.run(
        [sys.executable, "-m", "sportsassets.scripts.migrate"], cwd=BACKEND,
        env=dict(os.environ, DATABASE_URL=scratch_db), capture_output=True,
        text=True, timeout=600)
    assert built.returncode == 0, built.stderr[-2000:]
    rec, r = _receipt_step(tmp_path, scratch_db, "success")

    async def applied():
        c = await asyncpg.connect(scratch_db)
        try:
            return await C.applied_migrations(c)
        finally:
            await c.close()
    db = asyncio.run(applied())
    api = C.migrations(db, C.repo_migrations(), fresh_db_passed=None)
    assert rec["result"] == FD.PASSED, rec["reasons"]
    assert rec["applied"] == db == C.repo_migrations()
    assert rec["migrations_applied"] == rec["migrations_in_tree"] == len(db)
    assert rec["fingerprint"] == api["evidence"]["applied_fingerprint"] \
        == api["evidence"]["repo_fingerprint"] == rec["tree_fingerprint"]
    assert rec["sha"] == REL and rec["run_id"] == str(GATE_RUN)
    assert rec["server_version"] and rec["server_version"][0].isdigit()
    assert '"result": "PASSED"' in r.stdout


def test_a_build_that_never_ran_still_writes_a_failed_receipt(
        tmp_path, scratch_db):
    """The build step failed before the runner recorded anything: the step
    still writes a receipt, FAILED, naming why -- never no receipt."""
    rec, _ = _receipt_step(tmp_path, scratch_db, "failure")
    assert rec["result"] == FD.FAILED
    assert FD.R_BUILD_STEP_FAILED + ":failure" in rec["reasons"]
    assert FD.R_APPLIED_UNREADABLE in rec["reasons"]
    assert rec["migrations_applied"] == 0


def test_capital_critical_writes_attests_and_keeps_the_receipt():
    doc = yaml.safe_load(CC_WF.read_text())
    steps = _cc_steps()
    names = [s.get("name", "") for s in steps]
    build = _cc_step("Build the database with production's migration runner")
    rec = _cc_step("The fresh-database receipt")
    att = _cc_step("Attest the fresh-database receipt")
    up = next(s for s in steps
              if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    # after the build, before the suite touches the database; a FAILED
    # build still writes its receipt
    assert build["id"] == "freshdb"
    assert names.index(build["name"]) < names.index(rec["name"]) < \
        names.index(att["name"]) < names.index("Run the whole suite")
    assert rec["if"] == "always() && steps.freshdb.outcome != 'skipped'"
    assert rec["env"]["BUILD_OUTCOME"] == "${{ steps.freshdb.outcome }}"
    assert "python tools/fresh_db_receipt.py build" in rec["run"]
    assert '--sha "$GITHUB_SHA"' in rec["run"]
    assert "FROM schema_migrations" in rec["run"]
    assert rec["run"].rstrip().endswith("exit 0")
    # attested; an attestation outage never turns the suite's verdict and
    # is UNPROVEN where the receipt is read
    assert att["uses"].startswith("actions/attest-build-provenance@")
    assert att["with"]["subject-path"] == \
        "${{ steps.receipt.outputs.receipt }}"
    assert att["continue-on-error"] is True
    assert doc["permissions"] == {"contents": "read", "id-token": "write",
                                  "attestations": "write"}
    assert up["if"] == "always()"
    assert "${{ runner.temp }}/fresh-db-receipt.json" in up["with"]["path"]
    assert up["with"]["name"] == "capital-critical-${{ github.sha }}"
    # nothing reaches production: no secret is read anywhere in the job
    assert "secrets." not in CC_WF.read_text()


# ── 2. pm-acceptance reads the gate run's receipt and judges nothing ──────

FAKE_GH = r'''#!/usr/bin/env bash
case "$1 $2" in
  "api repos/"*) echo "$*" >> "$FAKE_GH_DIR/calls"; cat "$FAKE_GH_DIR/run.json" ;;
  "run download")
     echo "$*" >> "$FAKE_GH_DIR/calls"
     shift 2; d=""
     while [ $# -gt 0 ]; do [ "$1" = "--dir" ] && d="$2"; shift; done
     [ -d "$FAKE_GH_DIR/art" ] && cp "$FAKE_GH_DIR"/art/* "$d/" ;;
  "attestation verify") echo "$*" >> "$FAKE_GH_DIR/calls"; exit "${FAKE_GH_VERIFY_RC:-0}" ;;
  *) echo "fake gh: unexpected $*" >&2; exit 2 ;;
esac
'''


def _pm_steps():
    return yaml.safe_load(PM_WF.read_text())["jobs"]["accept"]["steps"]


def _pm_step(prefix):
    hits = [s for s in _pm_steps() if s.get("name", "").startswith(prefix)]
    assert len(hits) == 1, prefix
    return hits[0]


def _readback_run(tmp_path, *, receipt=True, verify_rc="0", gates=True):
    work = tmp_path / "work"
    (work / "judge").mkdir(parents=True)
    (work / "judge" / "backend").symlink_to(BACKEND)
    (work / "acc").mkdir()
    if gates:
        _w(work / "acc", "gates.json", {"runs": {"capital_critical": {
            "id": GATE_RUN, "head_sha": REL, "conclusion": "success"}}})
    gh = tmp_path / "ghdir"
    gh.mkdir()
    (gh / "run.json").write_text(json.dumps({
        "id": GATE_RUN, "path": ".github/workflows/capital-critical.yml",
        "repository": {"full_name": REPO}, "head_sha": REL,
        "event": "push", "status": "completed", "conclusion": "success"}))
    if receipt:
        (gh / "art").mkdir()
        (gh / "art" / "fresh-db-receipt.json").write_text(json.dumps(
            FD.build(dict(MAP), dict(MAP), sha=REL, run_id=str(GATE_RUN),
                     build_outcome="success")))
        (gh / "art" / "capital-critical-verdict.json").write_text("{}")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH)
    (bindir / "gh").chmod(0o755)
    st = _pm_step("The fresh-database receipt")
    env = {"PATH": "%s:%s" % (bindir, os.environ["PATH"]), "SHA": REL,
           "REPO": REPO, "GH_TOKEN": "t", "HOME": str(tmp_path),
           "RUNNER_TEMP": str(tmp_path / "rt"), "FAKE_GH_DIR": str(gh),
           "FAKE_GH_VERIFY_RC": verify_rc}
    (tmp_path / "rt").mkdir()
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                        "-c", st["run"]], cwd=work, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    calls = (gh / "calls").read_text() if (gh / "calls").exists() else ""
    return json.loads((work / "acc" / "fresh_db.json").read_text()), calls


def test_pm_acceptance_reads_the_gate_runs_receipt_as_written(tmp_path):
    doc, calls = _readback_run(tmp_path)
    assert doc["reason"] is None and doc["receipt"]["result"] == FD.PASSED
    p = doc["provenance"]
    assert p == {"run_id": str(GATE_RUN),
                 "workflow_path": ".github/workflows/capital-critical.yml",
                 "repository": REPO, "head_sha": REL, "event": "push",
                 "status": "completed", "conclusion": "success",
                 "signer_workflow": ".github/workflows/capital-critical.yml",
                 "attestation_verified": True,
                 "receipt_sha256": p["receipt_sha256"]}
    assert re.fullmatch(r"[0-9a-f]{64}", p["receipt_sha256"])
    # THE run the CI row reads, ITS artifact for the SHA, verified as
    # signed by capital-critical.yml in this repository
    assert "api repos/%s/actions/runs/%d" % (REPO, GATE_RUN) in calls
    assert ("run download %d --repo %s --name capital-critical-%s --dir"
            % (GATE_RUN, REPO, REL)) in calls
    assert ("attestation verify %s/rt/freshdb/art/fresh-db-receipt.json "
            "--repo %s --signer-workflow %s/.github/workflows/"
            "capital-critical.yml" % (tmp_path, REPO, REPO)) in calls


def test_an_unverified_or_absent_receipt_is_recorded_with_its_reason(
        tmp_path):
    doc, _ = _readback_run(tmp_path / "a", verify_rc="1")
    assert doc["provenance"]["attestation_verified"] is False
    assert doc["receipt"]["result"] == FD.PASSED      # recorded, not judged
    doc, _ = _readback_run(tmp_path / "b", receipt=False)
    assert doc["reason"] == FD.R_RECEIPT_NOT_IN_ARTIFACT
    assert doc["receipt"] is None
    doc, calls = _readback_run(tmp_path / "c", gates=False)
    assert doc["reason"] == FD.R_NO_GATE_RUN and calls == ""


def test_a_receipt_that_is_not_json_is_named(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{not json")
    doc = FD.readback(run_id="5", run={}, receipt_path=str(p),
                      attestation_verified=True)
    assert doc["reason"] == FD.R_RECEIPT_NOT_JSON and doc["receipt"] is None


def test_the_readback_step_sits_between_the_gates_and_the_readbacks():
    names = [s.get("name", "") for s in _pm_steps()]
    st = _pm_step("The fresh-database receipt")
    i = names.index(st["name"])
    assert names.index("Exact-SHA gate conclusions (GitHub)") < i < \
        names.index("Production readbacks (ADMIN_TOKEN, read role)")
    run = st["run"]
    assert "jq -r '.runs.capital_critical.id // empty' acc/gates.json" in run
    assert 'python3 -I judge/backend/tools/fresh_db_receipt.py readback' \
        in run
    assert "continue-on-error" not in st and st["env"]["GH_TOKEN"] == \
        "${{ github.token }}"


# ── 3. the scorecard binds it: the Deployment unit and the Red-team unit ──

def test_the_rc5_packet_without_a_receipt_is_unproven_and_named(tmp_path):
    out = _score(_acc(tmp_path, receipt=None))
    for cat, unit in (("Deployment infrastructure", "migration_integrity"),
                      ("Red-team safeguards", "MIGRATION_INTEGRITY")):
        u = _unit(out, cat, unit)
        assert u["passed"] is False and u["class"] == "FAIL"
        assert u["detail"]["status"] == SC.UNPROVEN
        assert u["detail"]["blockers"] == [SC.R_FRESH_DB_READBACK_ABSENT]
        # the API's own reading stays visible beside the binding
        assert u["detail"]["api_status"] == "UNKNOWN"
        assert u["detail"]["api_blockers"] == [SC.API_FRESH_DB_ABSENT]
    assert _cat(out, "Deployment infrastructure")["passes"] is False


def test_an_attested_passed_receipt_matching_production_proves_it(tmp_path):
    """The RC5 state with capital-critical's receipt of the same SHA: the
    Deployment row is 6/6."""
    out = _score(_acc(tmp_path))
    dep = _cat(out, "Deployment infrastructure")
    assert dep["passes"] is True and dep["readiness"] == 1.0
    u = _unit(out, "Deployment infrastructure", "migration_integrity")
    assert u["detail"]["status"] == SC.GREEN
    assert u["detail"]["fresh_db"]["status"] == SC.PROVEN
    assert u["detail"]["fresh_db"]["run_id"] == str(GATE_RUN)
    assert u["detail"]["fresh_db"]["fingerprint"] == SC._fingerprint(MAP)
    rt = _unit(out, "Red-team safeguards", "MIGRATION_INTEGRITY")
    assert rt["passed"] is True and rt["detail"]["api_status"] == "UNKNOWN"


@pytest.mark.parametrize("prov,receipt,needle", [
    ({"_att": False}, "OK", SC.R_FRESH_DB_NOT_ATTESTED),
    ({"path": ".github/workflows/backend-tests.yml"}, "OK",
     SC.R_FRESH_DB_NOT_CAPITAL_CRITICAL),
    ({"head_sha": OTHER}, "OK", SC.R_FRESH_DB_NOT_THE_RELEASE),
    ({"_run_id": 1}, "OK", SC.R_FRESH_DB_NOT_THE_GATE_RUN),
    ({}, "OTHER_SHA", SC.R_FRESH_DB_NOT_THE_RELEASE),
    ({}, "OTHER_RUN", SC.R_FRESH_DB_NOT_THE_GATE_RUN),
    ({}, "FORGED_FINGERPRINT", SC.R_FRESH_DB_MALFORMED),
    ({}, "OLD_VERSION", SC.R_FRESH_DB_MALFORMED),
    ({}, "NOT_JSON", SC.R_FRESH_DB_RECEIPT_ABSENT + ":" +
     FD.R_RECEIPT_NOT_JSON),
])
def test_an_unverifiable_receipt_is_unproven_never_a_pass(
        tmp_path, prov, receipt, needle):
    good = FD.build(dict(MAP), dict(MAP), sha=REL, run_id=str(GATE_RUN),
                    build_outcome="success")
    rec = {"OK": "OK", "OTHER_SHA": dict(good, sha=OTHER),
           "OTHER_RUN": dict(good, run_id="1"),
           "FORGED_FINGERPRINT": dict(good, fingerprint="f" * 64),
           "OLD_VERSION": dict(good, version="FRESH_DB_RECEIPT_V0"),
           "NOT_JSON": "OK"}[receipt]
    acc = _acc(tmp_path, receipt=rec, prov=prov)
    if receipt == "NOT_JSON":
        p = tmp_path / "bad.json"
        p.write_text("{x")
        _w(acc, "fresh_db.json", FD.readback(
            run_id=str(GATE_RUN), run={}, receipt_path=str(p),
            attestation_verified=True))
    out = _score(acc)
    for cat, unit in (("Deployment infrastructure", "migration_integrity"),
                      ("Red-team safeguards", "MIGRATION_INTEGRITY")):
        u = _unit(out, cat, unit)
        assert u["passed"] is False, (cat, needle)
        assert u["detail"]["status"] == SC.UNPROVEN
        assert needle in u["detail"]["blockers"], u["detail"]


def test_a_running_api_on_another_sha_is_not_proven_by_this_receipt(
        tmp_path):
    out = _score(_acc(tmp_path, running=OTHER))
    u = _unit(out, "Deployment infrastructure", "migration_integrity")
    assert u["passed"] is False
    assert SC.R_RUNNING_NOT_THE_RELEASE in u["detail"]["blockers"]


@pytest.mark.parametrize("case", ["FINGERPRINT", "COUNT", "BUILD_FAILED"])
def test_a_mismatch_with_the_running_fingerprint_is_red(tmp_path, case):
    fp = SC._fingerprint(MAP)
    if case == "FINGERPRINT":
        mig = _mig("1" * 64)                 # production differs
        needle = SC.R_FRESH_DB_FINGERPRINT_DIFFERS
    elif case == "COUNT":
        mig = _mig(fp, n=224)
        needle = SC.R_FRESH_DB_COUNT_DIFFERS
    else:
        mig = _mig(fp)
        needle = SC.R_FRESH_DB_BUILD_FAILED
    rec = "OK" if case != "BUILD_FAILED" else FD.build(
        dict(MAP), dict(MAP), sha=REL, run_id=str(GATE_RUN),
        build_outcome="failure")
    out = _score(_acc(tmp_path, mig=mig, receipt=rec))
    for cat, unit in (("Deployment infrastructure", "migration_integrity"),
                      ("Red-team safeguards", "MIGRATION_INTEGRITY")):
        u = _unit(out, cat, unit)
        assert u["passed"] is False and u["detail"]["status"] == SC.RED
        assert needle in u["detail"]["blockers"]


def test_a_red_receipt_turns_even_an_api_green_red(tmp_path):
    """A posted release receipt makes the API read GREEN from the
    capital-critical conclusion alone; the fresh build's own receipt saying
    otherwise is a finding, and it wins."""
    fp = SC._fingerprint(MAP)
    out = _score(_acc(tmp_path, mig=_mig("2" * 64, status="GREEN",
                                         blockers=()),
                      receipt="OK"))
    u = _unit(out, "Deployment infrastructure", "migration_integrity")
    assert u["passed"] is False and u["detail"]["status"] == SC.RED
    # and an API GREEN with no receipt at all stays the API's GREEN
    out = _score(_acc(tmp_path / "x", mig=_mig(fp, status="GREEN",
                                               blockers=()), receipt=None))
    assert _unit(out, "Deployment infrastructure",
                 "migration_integrity")["passed"] is True


@pytest.mark.parametrize("status,blockers,over", [
    ("RED", ["APPLIED_HISTORY_EDITED_IN_PLACE"],
     {"edited_in_place": ["031_x.sql"]}),
    ("RED", ["APPLIED_MIGRATION_FINGERPRINT_DIFFERS"], {}),
    ("UNKNOWN", ["FRESH_DB_MIGRATION_TEST_FAILED"], {}),
    ("RED", ["FRESH_DB_RESULT_NOT_IN_THIS_PROCESS",
             "READ_UNAVAILABLE:migrations"], {}),
    ("UNKNOWN", [], {}),
    (None, ["FRESH_DB_RESULT_NOT_IN_THIS_PROCESS", "X"], {}),
])
def test_the_judge_never_overrules_an_api_finding(tmp_path, status,
                                                  blockers, over):
    fp = SC._fingerprint(MAP)
    out = _score(_acc(tmp_path, mig=_mig(fp, status=status,
                                         blockers=blockers, **over)))
    for cat, unit in (("Deployment infrastructure", "migration_integrity"),
                      ("Red-team safeguards", "MIGRATION_INTEGRITY")):
        u = _unit(out, cat, unit)
        assert u["passed"] is False, (status, blockers)
        assert u["detail"]["status"] in (SC.RED, SC.UNKNOWN)


def test_an_unreadable_red_team_readback_stays_read_unavailable(tmp_path):
    acc = _acc(tmp_path)
    _w(acc, "red_team.json", {"status": "UNAVAILABLE", "data": None})
    u = _unit(_score(acc), "Deployment infrastructure",
              "migration_integrity")
    assert u["class"] == "READ_UNAVAILABLE" and u["passed"] is False


# ── 4. the release receipt for the running SHA, and the RELEASE unit ──────

def test_the_rc5_release_reads_green_from_its_own_packet(tmp_path):
    acc = _acc(tmp_path)
    assert RV.main([str(acc), "--sha", REL]) == 0
    v = json.loads((acc / "release_verdict.json").read_text())
    assert v["status"] == RV.GREEN and v["posted"] is False
    assert v["running_api_sha"] == REL
    assert v["release_gate"] == {"green": True, "blockers": []}
    assert set(v["inputs_sha256"]) == set(RV.INPUTS)
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is True and u["detail"]["status"] == SC.GREEN
    assert u["detail"]["api_status"] == "UNKNOWN"
    assert u["detail"]["api_blockers"] == [SC.API_RELEASE_ABSENT]


NON_GREEN = {
    "gates_not_green": (dict(gates_green=False),
                        ["BACKEND_TESTS_NOT_GREEN", "CAPITAL_CRITICAL_NOT_"
                         "GREEN", "COMMIT_GUARD_NOT_GREEN",
                         "ENGINE_DIAGNOSTIC_NOT_GREEN"]),
    "not_descendant": (dict(descendant=False),
                       ["RELEASE_NOT_DESCENDANT_OF_ACCEPTED_BASE"]),
    # 2026-10-07: deployed by commit id while claude/release-api lagged
    "branch_left_behind": (dict(release_branch=BASE),
                           ["RELEASE_SHA_DIFFERS_FROM_TESTED_SHA",
                            "DEPLOYED_SHA_DIFFERS_FROM_RELEASE_SHA"]),
    "workers_elsewhere": (dict(workers=OTHER),
                          ["WORKERS_NOT_ON_RELEASE_SHA:08828d047660"]),
    "workers_unread": (dict(workers=None),
                       ["WORKERS_NOT_ON_RELEASE_SHA:UNKNOWN"]),
    "fingerprint_mismatch": (dict(mig=_mig("1" * 64, repo_fingerprint="2"
                                            * 64)),
                             ["MIGRATION_FINGERPRINT_MISMATCH"]),
    "edited_in_place": (dict(mig=_mig(None, edited_in_place=["031_x.sql"])),
                        ["MIGRATION_FINGERPRINT_MISMATCH"]),
}


@pytest.mark.parametrize("case", sorted(NON_GREEN))
def test_a_non_green_release_never_reads_green(tmp_path, case):
    kw, want = copy.deepcopy(NON_GREEN[case])
    if "mig" in kw and kw["mig"]["evidence"]["applied_fingerprint"] is None:
        kw["mig"]["evidence"]["applied_fingerprint"] = SC._fingerprint(MAP)
        kw["mig"]["evidence"]["repo_fingerprint"] = SC._fingerprint(MAP)
    acc = _acc(tmp_path, **kw)
    v = RV.build(acc, REL)
    assert v["status"] == RV.RED, v
    assert v["release_gate"]["blockers"] == want
    _w(acc, "release_verdict.json", v)
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.RED
    assert u["detail"]["blockers"] == want


@pytest.mark.parametrize("case", ["running_elsewhere", "red_team_unread",
                                  "short_sha"])
def test_a_body_the_api_would_refuse_is_unproven(tmp_path, case):
    acc = _acc(tmp_path, running=OTHER if case == "running_elsewhere"
               else REL)
    if case == "red_team_unread":
        _w(acc, "red_team.json", {"status": "UNAVAILABLE", "data": None})
    v = RV.build(acc, REL if case != "short_sha" else "69a8a07e")
    assert v["status"] == RV.REFUSED
    want = {"running_elsewhere": ["DEPLOYED_SHA_IS_NOT_THIS_API"],
            "red_team_unread": ["DEPLOYED_SHA_IS_NOT_THIS_API"],
            "short_sha": ["NOT_A_FULL_SHA:tested_sha",
                          "NOT_A_FULL_SHA:deployed_sha",
                          "DEPLOYED_SHA_IS_NOT_THIS_API"]}[case]
    assert v["refused"] == want
    _w(acc, "release_verdict.json", v)
    if case == "red_team_unread":
        return            # the RELEASE unit itself is READ_UNAVAILABLE then
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert u["detail"]["blockers"] == (
        [SC.R_RELEASE_VERDICT_NOT_THE_RELEASE] if case == "short_sha" else
        ["%s:%s" % (SC.R_RELEASE_VERDICT_REFUSED, r) for r in want])


@pytest.mark.parametrize("mutate,needle", [
    (lambda v: v.update(sha=OTHER), SC.R_RELEASE_VERDICT_NOT_THE_RELEASE),
    (lambda v: v.update(version="RELEASE_VERDICT_V0"),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v["release_gate"].update(blockers=["X"]),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v["body"].update(capital_critical_green=False),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v["body"].update(capital_critical_green="true"),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v["body"].update(workers_deployed_sha=OTHER),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v.update(running_api_sha=OTHER),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
    (lambda v: v["release_gate"].update(green=False),
     SC.R_RELEASE_VERDICT_INCONSISTENT),
])
def test_a_verdict_that_says_green_inconsistently_is_not_a_pass(
        tmp_path, mutate, needle):
    acc = _acc(tmp_path)
    v = RV.build(acc, REL)
    mutate(v)
    _w(acc, "release_verdict.json", v)
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert u["detail"]["blockers"] == [needle]


def test_no_verdict_is_unproven_and_an_api_red_is_never_lifted(tmp_path):
    acc = _acc(tmp_path)
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert u["detail"]["blockers"] == [SC.R_RELEASE_VERDICT_ABSENT]
    # the API holds a RED receipt for its SHA: a GREEN verdict here does
    # not lift it
    acc = _acc(tmp_path / "r", release=_release_ctl(
        status="RED", blockers=["WORKERS_NOT_ON_RELEASE_SHA:7fd4574e9ac8"]))
    _w(acc, "release_verdict.json", RV.build(acc, REL))
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.RED
    # the API's GREEN receipt with this run's verdict RED: RED
    acc = _acc(tmp_path / "g", release=_release_ctl(status="GREEN",
                                                    blockers=()),
               workers=OTHER)
    _w(acc, "release_verdict.json", RV.build(acc, REL))
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False and u["detail"]["status"] == SC.RED
    # an API GREEN about another running build is not this release's
    acc = _acc(tmp_path / "o", release=_release_ctl(
        status="GREEN", blockers=(), running=OTHER))
    u = _unit(_score(acc), "Red-team safeguards", "RELEASE")
    assert u["passed"] is False
    assert u["detail"]["blockers"] == [SC.R_RUNNING_NOT_THE_RELEASE]


def test_the_judge_rules_are_the_endpoints_rules():
    """validate() is command_red_team.validate_release rule for rule."""
    from sportsassets.api import command_red_team as CR
    good = {"accepted_base_sha": BASE, "tested_sha": REL,
            "release_sha": REL, "deployed_sha": REL,
            "descendant_of_base": True, "backend_tests_green": True,
            "capital_critical_green": True, "commit_guard_green": True,
            "engine_diagnostic_green": True}
    bodies = [good, dict(good, deployed_sha=OTHER),
              dict(good, tested_sha="69a8a07e"),
              dict(good, release_sha="X" * 40),
              {k: v for k, v in good.items() if k != "commit_guard_green"},
              dict(good, accepted_base_sha=None)]
    for b in bodies:
        for running in (REL, OTHER, "", "UNKNOWN"):
            assert RV.validate(b, running_sha=running) == \
                CR.validate_release(b, running_sha=running), (b, running)
    assert RV.REQUIRED == CR.REQUIRED


async def test_the_verdict_is_what_the_endpoint_records(monkeypatch,
                                                        tmp_path):
    """The SAME body through the real receipt endpoint (local database,
    append-only tables of migration 315): its release_gate is the verdict,
    for the green receipt and for each kind of red one."""
    if not DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    import asyncpg
    from sportsassets.api import command_red_team as CR
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=2)

    async def _p():
        return pool
    monkeypatch.setattr(CR, "_pool", _p)
    monkeypatch.setenv("RENDER_GIT_COMMIT", REL)
    try:
        for i, kw in enumerate([{}, dict(gates_green=False),
                                dict(workers=OTHER),
                                dict(release_branch=BASE),
                                dict(descendant=False)]):
            acc = _acc(tmp_path / str(i), **kw)
            v = RV.build(acc, REL)
            got = await CR.release_receipt(dict(v["body"]))
            assert got["release_gate"] == v["release_gate"], (kw, got)
            assert got["authority"] == "EVIDENCE_ONLY_NO_AUTHORITY"
    finally:
        await pool.close()


def test_the_verdict_body_is_the_body_the_sender_would_post(tmp_path):
    """The POST step's own jq, run by bash over the same files: the same
    body (pm_acceptance, filed separately, aside)."""
    acc = _acc(tmp_path)
    _w(acc, "pm_before.json", {"status": "OK", "data": {"pm_acceptance": {
        "pm_state": "RED"}}})
    run = _pm_step("POST the release receipt")["run"]
    cut = run.index("python3 -I judge/.github/pm-acceptance/post_receipt.py")
    script = run[:cut].replace('echo "::add-mask::$ADMIN_TOKEN"', "")
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                        "-c", script], cwd=tmp_path,
                       env={"PATH": os.environ["PATH"], "SHA": REL,
                            "HOME": str(tmp_path)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    posted = json.loads((acc / "receipt_body.json").read_text())
    assert posted.pop("pm_acceptance") == {"pm_state": "RED"}
    assert posted == RV.build(acc, REL)["body"]


def test_the_verdict_is_built_in_the_packet_step_and_never_posted():
    st = _pm_step("Production evidence packet")
    run = st["run"]
    i = run.index('python3 -I judge/backend/tools/release_verdict.py acc '
                  '--sha "$SHA"')
    assert i < run.index("python3 -I judge/backend/tools/scorecard_14.py")
    assert i < run.index("python3 -I judge/backend/tools/evidence_packet.py")
    # the only sender is unchanged: still the one step, still only on "on"
    post = _pm_step("POST the release receipt")
    assert post["if"] == "${{ inputs.post_receipt == 'on' }}"
    src = (BACKEND / "tools" / "release_verdict.py").read_text()
    for s in ("urllib", "http.client", "requests", "socket", "post_receipt"):
        assert "import %s" % s not in src and "from %s" % s not in src


def test_the_release_verdict_imports_only_the_apis_pure_gate():
    src = (BACKEND / "tools" / "release_verdict.py").read_text()
    imports = sorted(set(re.findall(r"^(?:from|import) (\S+)", src, re.M)))
    assert imports == ["__future__", "argparse", "hashlib", "json",
                       "pathlib", "re",
                       "sportsassets.red_team.models",
                       "sportsassets.red_team.release_guard", "sys"]
    # and those run on a bare interpreter (the runner's python3 -I)
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import runpy,sys; sys.argv=['x','--help'];"
                        "runpy.run_path(%r, run_name='__main__')" % str(
                            BACKEND / "tools" / "release_verdict.py")],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "--sha" in r.stdout, r.stderr


# ── 5. the packet reports both halves at declared paths ───────────────────

def test_the_packet_reports_both_halves_at_declared_paths(tmp_path):
    acc = _acc(tmp_path)
    RV.main([str(acc), "--sha", REL])
    p = EP.build(acc, now=1.0)
    fd = p["migrations"]["fresh_db"]
    assert fd["receipt.result"] == {"value": "PASSED",
                                    "source": "fresh_db.json",
                                    "path": "receipt.result"}
    assert fd["provenance.attestation_verified"]["value"] is True
    assert fd["provenance.run_id"]["value"] == str(GATE_RUN)
    assert fd["receipt.fingerprint"]["value"] == SC._fingerprint(MAP)
    assert "receipt.applied" not in fd           # the map stays in the file
    rv = p["release_verdict"]
    assert rv["status"]["value"] == "GREEN" and rv["posted"]["value"] is False
    assert rv["sha"]["path"] == "sha"


def test_absent_halves_are_read_unavailable_in_the_packet(tmp_path):
    p = EP.build(_acc(tmp_path, receipt=None), now=1.0)
    for v in list(p["migrations"]["fresh_db"].values()) + list(
            p["release_verdict"].values()):
        assert v["value"] == EP.UNAVAILABLE and v["reason"].endswith(
            "FILE_ABSENT")


# ── 6. every new reason code is classified ────────────────────────────────

def test_every_new_reason_code_is_classified():
    from sportsassets import refusal_taxonomy as RT
    from sportsassets import refusal_taxonomy_table as TT
    codes = {}
    for mod in (FD, SC):
        for name, v in vars(mod).items():
            if name.startswith("R_") and isinstance(v, str):
                codes[RT.normalize(v)] = "%s:%s" % (mod.__name__, name)
    assert len(codes) >= 25
    missing = {c: w for c, w in codes.items()
               if c not in TT.TABLE and c not in TT.NOT_REFUSAL
               and c not in TT.WRAPPERS}
    assert not missing, missing
    assert all(c in TT.NOT_REFUSAL for c in codes)


def test_the_receipt_tool_is_stdlib_only():
    src = (BACKEND / "tools" / "fresh_db_receipt.py").read_text()
    imports = sorted(set(re.findall(r"^(?:from|import) (\S+)", src, re.M)))
    assert imports == ["__future__", "argparse", "hashlib", "json",
                       "pathlib", "re", "sys"]
    assert hashlib.sha256(b"").hexdigest()      # (hashlib is the stdlib's)
