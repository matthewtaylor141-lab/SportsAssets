"""DEPLOYMENT INTEGRITY AND EVALUATOR PINNING (RC6 lane E, owner directive
2E): release lineage, upgrade path, rollback readiness, deployment health
and the grading-logic diff, from the files pm-acceptance collects.

Production evidence, pm-acceptance 37836393458 on release 69a8a07e (tree
a88f876f == implementation 2f72a2c1's tree; single parent 93dc6f41, an
interim release that never ran; Render's previous deploy on api, workers
AND market plane is 7fd4574e; 69a8a07e adds migration 316 to 7fd4574e's
223): none of these facts was checked by the judge -- the scorecard's
Deployment row asked only "is every service on the release" and "do the
migrations fingerprint". These tests hold:

  * release_lineage passes only with the implementation SHA given, its
    tree == the release tree, a single-parent release commit, the release
    branch AT the release, and the four gates green on BOTH SHAs (the
    lineage step run as written over a real git repository);
  * the rollback target is what RAN (Render's deploy history, all three
    services agreeing), not the git parent; rollback_ready re-judges every
    fact (services on the release, one target, ancestry, its gates, the
    commands, schema compatibility) and never trusts the tool's status;
  * upgrade_path is PROVEN by identical migration sets (content hashes) or
    by capital-critical's attested receipt FROM the rollback target's
    migration set; a FAILED receipt is RED even then; absent = UNPROVEN;
  * the grading diff runs the PINNED prior evaluator (2fadc8dc, from git)
    over a copy of the same packet and lists every unit whose verdict
    differs, declared or UNDECLARED -- never hidden;
  * the packet carries pinning, lineage, upgrade, rollback, the diff and
    per-service deployment health at declared paths.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest
import yaml

BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
PM_WF = ROOT / ".github" / "workflows" / "pm-acceptance.yml"
CC_WF = ROOT / ".github" / "workflows" / "capital-critical.yml"


def _tool(name):
    spec = importlib.util.spec_from_file_location(
        "rc6e_" + name, BACKEND / "tools" / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = _tool("scorecard_14")
RB = _tool("rollback_readiness")
UP = _tool("upgrade_path_receipt")
GD = _tool("grading_diff")
EP = _tool("evidence_packet")
FD = _tool("fresh_db_receipt")

REL = "69a8a07e5335864bd3d70f7160494aed13305fcc"
IMPL = "2f72a2c1e7f237d5fb0849d971b2a38c680bb58c"
PREV = "7fd4574e9ac8b95c355035a5bd4a9927d01c29ea"
PARENT = "93dc6f41d6e1d2ba25169f9d29d092093091bf02"
BASE = "a91be09f125f0ab0d1f29a0d0866b5cb6f5765fd"
TREE = "a88f876f71370c7296d8eb92556d5ede42a90d34"
GATE_RUN = 37803641023
REPO = "matthewtaylor141-lab/SportsAssets"
MIG = {"001_init.sql": "a" * 64, "002_more.sql": "b" * 64}


def _w(d, name, obj):
    (d / name).write_text(json.dumps(obj))


def _gates(sha, *, green=True, first=37803641000):
    return {"runs": {k: {"id": first + i, "status": "completed",
                         "conclusion": "success" if green else "failure",
                         "head_sha": sha}
                     for i, k in enumerate(SC.GATES)}}


def _deploys(live, prev, *, extra=(), history=True):
    """Render's deploy list, newest first, as pm-acceptance stores it: a
    failed build and a redeploy of the live commit sit between the live
    deploy and the previous release (neither is a previous release)."""
    rows = [{"deploy": {"id": "dep-live", "status": "live",
                        "commit": {"id": live}, "finishedAt": "t2"}}]
    rows += [{"deploy": {"id": "dep-failed", "status": "build_failed",
                         "commit": {"id": BASE}, "finishedAt": "t1c"}}]
    rows += [{"deploy": {"id": "dep-redeploy", "status": "deactivated",
                         "commit": {"id": live}, "finishedAt": "t1b"}}]
    rows += list(extra)
    if prev:
        rows.append({"deploy": {"id": "dep-prev", "status": "deactivated",
                                "commit": {"id": prev}, "finishedAt": "t1"}})
    if history:
        rows.append({"deploy": {"id": "dep-old", "status": "deactivated",
                                "commit": {"id": BASE}, "finishedAt": "t0"}})
    return rows


def _migdir(path, files):
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name).write_text(text)
    return path


def _packet(tmp_path, *, impl=IMPL, itree=TREE, rtree=TREE,
            parents=(PARENT,), branch=REL, impl_green=True, rel_green=True,
            prev=PREV, plane_prev=PREV, target_green=True, ancestor=True,
            target_files=None, release_files=None, upgrade=None,
            render_live=REL, history=True):
    """A packet shaped like pm-acceptance 37836393458 (69a8a07e) with the
    V2 files the lane E steps write."""
    acc = tmp_path / "acc"
    acc.mkdir(parents=True)
    _w(acc, "lineage.json", {
        "accepted_base_sha": BASE, "sha": REL, "descendant_of_base": True,
        "release_branch": "claude/release-api", "release_sha": branch,
        "implementation_sha": impl, "implementation_tree": itree,
        "release_tree": rtree, "trees_equal": bool(itree and itree == rtree),
        "release_parents": list(parents)})
    g = _gates(REL, green=rel_green)
    g["runs"]["capital_critical"]["id"] = GATE_RUN
    _w(acc, "gates.json", g)
    if impl:
        _w(acc, "gates_implementation.json", _gates(impl, green=impl_green))
    _w(acc, "render.json", {s: {"live_commit": render_live}
                            for s in SC.SERVICES})
    for s in SC.SERVICES:
        _w(acc, "deploys_%s.json" % s, _deploys(
            render_live, plane_prev if s == "sportsassets-market-plane"
            else prev, history=history))
    _w(acc, "gates_rollback_target.json", _gates(prev, green=target_green))
    files = {"001_init.sql": "create table a();\n",
             "002_more.sql": "create table b();\n"}
    tdir = _migdir(tmp_path / "target_mig", target_files or files)
    rdir = _migdir(tmp_path / "release_mig", release_files or files)
    rb = RB.build(acc, sha=REL, target_migrations=tdir,
                  release_migrations=rdir, target_is_ancestor=ancestor)
    _w(acc, "rollback.json", rb)
    if upgrade is not None:
        _w(acc, "upgrade_path.json", upgrade)
    return acc


def _unit(acc, unit):
    out = SC.score(str(acc), release_sha=REL)
    cat = next(c for c in out["categories"]
               if c["category"] == "Deployment infrastructure")
    return next(u for u in cat["units"] if u["unit"] == unit)


# ── 1. release lineage ───────────────────────────────────────────────────

def test_the_rc5_lineage_with_both_gate_sets_green_passes(tmp_path):
    u = _unit(_packet(tmp_path), "release_lineage")
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["implementation_tree"] == u["detail"]["release_tree"]


@pytest.mark.parametrize("kw,needle", [
    (dict(impl=None), "LINEAGE_IMPLEMENTATION_SHA_ABSENT"),
    (dict(itree="b" * 40), "LINEAGE_IMPLEMENTATION_TREE_DIFFERS_FROM_RELEASE_TREE"),
    (dict(itree=None), "LINEAGE_IMPLEMENTATION_TREE_DIFFERS_FROM_RELEASE_TREE"),
    (dict(parents=(PARENT, IMPL)), "LINEAGE_RELEASE_COMMIT_NOT_SINGLE_PARENT:2"),
    (dict(parents=()), "LINEAGE_RELEASE_COMMIT_NOT_SINGLE_PARENT:0"),
    # 2026-10-07: deployed by commit id while claude/release-api lagged
    (dict(branch=BASE), "LINEAGE_RELEASE_BRANCH_NOT_AT_THE_RELEASE_SHA"),
    (dict(impl_green=False),
     "LINEAGE_IMPLEMENTATION_GATE_NOT_GREEN:backend_tests=failure"),
    (dict(rel_green=False), "LINEAGE_RELEASE_GATE_NOT_GREEN:capital_critical=failure"),
])
def test_a_broken_lineage_fails_with_its_reason(tmp_path, kw, needle):
    u = _unit(_packet(tmp_path, **kw), "release_lineage")
    assert u["passed"] is False and needle in u["detail"]["reasons"], \
        u["detail"]


def test_implementation_gates_never_read_are_named(tmp_path):
    acc = _packet(tmp_path)
    (acc / "gates_implementation.json").unlink()
    u = _unit(acc, "release_lineage")
    assert "LINEAGE_IMPLEMENTATION_GATES_UNREAD" in u["detail"]["reasons"]


def test_the_rc5_packet_lineage_without_v2_fields_fails(tmp_path):
    """pm-acceptance 37836393458's lineage.json predates these fields."""
    acc = tmp_path / "acc"
    acc.mkdir()
    _w(acc, "lineage.json", {"accepted_base_sha": BASE, "sha": REL,
                             "descendant_of_base": True,
                             "release_branch": "claude/release-api",
                             "release_sha": REL})
    u = _unit(acc, "release_lineage")
    assert u["passed"] is False and u["class"] == "FAIL"
    assert "LINEAGE_IMPLEMENTATION_SHA_ABSENT" in u["detail"]["reasons"]


def _git(repo, *args, env=None):
    return subprocess.run(["git", "-C", str(repo)] + list(args), check=True,
                          capture_output=True, text=True,
                          env=env).stdout.strip()


FAKE_GH_BRANCH = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_DIR/calls"
case "$1 $2" in
  "api repos/"*)
     case "$2" in
       *branches/claude/release-api*) cat "$FAKE_GH_DIR/branch" ;;
       *actions/runs?head_sha=*) cat "$FAKE_GH_DIR/runs.json" ;;
       *) echo '{}' ;;
     esac ;;
  *) echo "fake gh: unexpected $*" >&2; exit 2 ;;
esac
'''


def _repo(tmp_path):
    """A real repository: an accepted base, an implementation commit on its
    own line, and a release commit with the implementation's tree whose
    single parent is the base (how release commits are made)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               HOME=str(tmp_path))
    _git(repo, "init", "-q", "-b", "main", env=env)
    (repo / "backend" / "migrations").mkdir(parents=True)
    (repo / "backend" / "migrations" / "001_init.sql").write_text("x;\n")
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", "base", env=env)
    base = _git(repo, "rev-parse", "HEAD", env=env)
    (repo / "backend" / "migrations" / "002_more.sql").write_text("y;\n")
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", "impl", env=env)
    impl = _git(repo, "rev-parse", "HEAD", env=env)
    rel = _git(repo, "commit-tree", impl + "^{tree}", "-p", base, "-m",
               "Release: x", env=env)
    merge = _git(repo, "commit-tree", impl + "^{tree}", "-p", base, "-p",
                 impl, "-m", "merge", env=env)
    return repo, base, impl, rel, merge, env


def _run_step(prefix, cwd, env):
    st = next(s for s in yaml.safe_load(PM_WF.read_text())["jobs"][
        "accept"]["steps"] if s.get("name", "").startswith(prefix))
    r = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail",
                        "-c", st["run"]], cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    return r


def _fake_gh(tmp_path, *, branch, runs=None):
    gh = tmp_path / "ghdir"
    gh.mkdir(exist_ok=True)
    (gh / "branch").write_text(branch + "\n")
    (gh / "runs.json").write_text(json.dumps(runs or {"workflow_runs": []}))
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text(FAKE_GH_BRANCH)
    (bindir / "gh").chmod(0o755)
    return gh, bindir


@pytest.mark.parametrize("which", ["release", "merge", "no_impl"])
def test_the_lineage_step_as_written_reads_trees_and_parents(tmp_path, which):
    repo, base, impl, rel, merge, env = _repo(tmp_path)
    sha = merge if which == "merge" else rel
    gh, bindir = _fake_gh(tmp_path, branch=sha)
    env = dict(env, PATH="%s:%s" % (bindir, os.environ["PATH"]), SHA=sha,
               ACCEPTED_BASE=base, REPO=REPO, GH_TOKEN="t",
               FAKE_GH_DIR=str(gh),
               IMPL_SHA="" if which == "no_impl" else impl)
    _run_step("Lineage (", repo, env)
    lin = json.loads((repo / "acc" / "lineage.json").read_text())
    assert lin["sha"] == sha and lin["release_sha"] == sha
    assert lin["descendant_of_base"] is True
    assert lin["release_tree"] == _git(repo, "rev-parse", sha + "^{tree}")
    if which == "no_impl":
        assert lin["implementation_sha"] is None
        assert lin["implementation_tree"] is None
        assert lin["trees_equal"] is False
    else:
        assert lin["implementation_sha"] == impl
        assert lin["implementation_tree"] == lin["release_tree"]
        assert lin["trees_equal"] is True
    assert lin["release_parents"] == ([base, impl] if which == "merge"
                                      else [base])


# ── 2. rollback readiness ────────────────────────────────────────────────

def test_the_target_is_what_ran_not_the_git_parent(tmp_path):
    acc = _packet(tmp_path)
    rb = json.loads((acc / "rollback.json").read_text())
    assert rb["target_sha"] == PREV and rb["target_sha"] != PARENT
    for s in SC.SERVICES:
        # the redeploy of the live commit is skipped, the older one is not
        assert rb["services"][s]["previous_commit"] == PREV
        assert rb["services"][s]["previous_deploy_id"] == "dep-prev"
    assert rb["migrations"]["identical"] is True
    assert rb["status"] == RB.READY and rb["deploys_nothing"] is True
    assert all(PREV in rb["commands"][s] for s in SC.SERVICES)
    assert "deploy-api-commit" in rb["commands"]["sportsassets-api"]
    assert "workers-commit-deploy" in rb["commands"]["sportsassets-workers"]
    assert "market-plane.yml" in rb["commands"]["sportsassets-market-plane"]
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is True, u["detail"]
    assert u["detail"]["schema"] == "COMPATIBLE_IDENTICAL_SCHEMA"
    assert u["detail"]["procedure"] == "docs/closeout/ROLLBACK_PROCEDURE.md"
    assert (ROOT / u["detail"]["procedure"]).is_file()


@pytest.mark.parametrize("kw,needle", [
    (dict(plane_prev=BASE), "ROLLBACK_TARGET_UNKNOWN"),
    (dict(prev=None, plane_prev=None, history=False),
     "ROLLBACK_TARGET_UNKNOWN"),
    (dict(target_green=False), "ROLLBACK_TARGET_GATE_NOT_GREEN"),
    (dict(ancestor=False), "ROLLBACK_TARGET_NOT_AN_ANCESTOR_OF_THE_RELEASE"),
    (dict(ancestor=None), "ROLLBACK_TARGET_NOT_AN_ANCESTOR_OF_THE_RELEASE"),
    (dict(render_live=PREV), "ROLLBACK_READINESS_NOT_FOR_THE_RELEASE_SHA"),
    # a migration added and no upgrade receipt: compatibility UNPROVEN
    (dict(release_files={"001_init.sql": "create table a();\n",
                         "002_more.sql": "create table b();\n",
                         "003_new.sql": "alter table a add c int;\n"}),
     "ROLLBACK_SCHEMA_COMPATIBILITY_UNPROVEN"),
])
def test_a_rollback_that_is_not_ready_names_why(tmp_path, kw, needle):
    acc = _packet(tmp_path, **kw)
    if kw.get("render_live") == PREV:
        # the services are not on the release: the record is for PREV
        (acc / "rollback.json").write_text(json.dumps(RB.build(
            acc, sha=PREV, target_migrations=tmp_path / "target_mig",
            release_migrations=tmp_path / "release_mig",
            target_is_ancestor=True)))
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False
    assert any(needle in r for r in u["detail"]["reasons"]), u["detail"]


def test_the_tools_own_status_is_not_trusted(tmp_path):
    acc = _packet(tmp_path, target_green=False)
    rb = json.loads((acc / "rollback.json").read_text())
    rb["status"], rb["reasons"] = RB.READY, []
    _w(acc, "rollback.json", rb)
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False
    assert any("ROLLBACK_TARGET_GATE_NOT_GREEN" in r
               for r in u["detail"]["reasons"])


def test_no_rollback_record_is_read_unavailable(tmp_path):
    acc = _packet(tmp_path)
    (acc / "rollback.json").unlink()
    for unit in ("rollback_ready", "upgrade_path"):
        u = _unit(acc, unit)
        assert u["class"] == "READ_UNAVAILABLE" and u["passed"] is False


def test_the_rc5_deploy_history_names_7fd4574e_on_every_service(tmp_path):
    """The RC5 packet's own deploy files (shape and order as Render returned
    them): target 7fd4574e, and 69a8a07e adds exactly migration 316."""
    acc = tmp_path / "acc"
    acc.mkdir()
    for s in SC.SERVICES:
        _w(acc, "deploys_%s.json" % s, _deploys(REL, PREV))
    _w(acc, "render.json", {s: {"live_commit": REL} for s in SC.SERVICES})
    tgt, svcs, reasons = RB.target(acc)
    assert tgt == PREV and reasons == []
    assert RB.main(["target", str(acc)]) == 0


# ── 3. upgrade path ──────────────────────────────────────────────────────

def _receipt(*, result="PASSED", base_fp=None, sha=REL, run_id=GATE_RUN,
             compat="COMPATIBLE", reasons=(), representative="COMPLETE"):
    return {"version": UP.VERSION, "sha": sha, "run_id": str(run_id),
            "result": result, "reasons": list(reasons),
            "representative": representative,
            "base": {"sha": PREV, "migrations_fingerprint": base_fp},
            "new_migrations": ["003_new.sql"], "seeded": {"coverage": 0.79},
            "rollback_compatibility": {"verdict": compat, "blocking": [
                "ROLLBACK_COLUMN_DROPPED:a.c"] if compat != "COMPATIBLE"
                else [], "unproven": []}}


def _readback(rec, *, att=True, path=".github/workflows/capital-critical.yml",
              head=REL, run_id=GATE_RUN):
    return {"version": UP.READBACK_VERSION, "reason": None,
            "provenance": {"run_id": str(run_id), "workflow_path": path,
                           "head_sha": head, "attestation_verified": att},
            "receipt": rec}


NEW = {"001_init.sql": "create table a();\n",
       "002_more.sql": "create table b();\n",
       "003_new.sql": "alter table a add c int;\n"}


def test_identical_migration_sets_prove_the_upgrade_without_a_database(
        tmp_path):
    u = _unit(_packet(tmp_path), "upgrade_path")
    assert u["passed"] is True
    assert u["detail"]["evidence"] == SC.IDENTICAL


def test_a_new_migration_needs_the_receipt_from_the_rollback_target(
        tmp_path):
    acc = _packet(tmp_path, release_files=NEW)
    u = _unit(acc, "upgrade_path")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert "UPGRADE_PATH_READBACK_ABSENT" in u["detail"]["reasons"]
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    _w(acc, "upgrade_path.json", _readback(_receipt(base_fp=tfp)))
    u = _unit(acc, "upgrade_path")
    assert u["passed"] is True and u["detail"]["status"] == SC.PROVEN
    rb = _unit(acc, "rollback_ready")
    assert rb["passed"] is True
    assert rb["detail"]["schema"] == "COMPATIBLE_BY_UPGRADE_RECEIPT"


@pytest.mark.parametrize("mut,needle", [
    (dict(att=False), "UPGRADE_PATH_RECEIPT_ATTESTATION_NOT_VERIFIED"),
    (dict(path=".github/workflows/backend-tests.yml"),
     "UPGRADE_PATH_RECEIPT_NOT_FROM_CAPITAL_CRITICAL"),
    (dict(head=PREV), "UPGRADE_PATH_RECEIPT_NOT_FOR_THE_RELEASE_SHA"),
    (dict(run_id=1), "UPGRADE_PATH_RECEIPT_NOT_FROM_THE_GATE_RUN"),
    (dict(base="other"), "UPGRADE_PATH_BASE_IS_NOT_THE_ROLLBACK_TARGET"),
    (dict(representative="INCOMPLETE"),
     "UPGRADE_PATH_ROWS_NOT_REPRESENTATIVE"),
    (dict(version="UPGRADE_PATH_RECEIPT_V0"), "UPGRADE_PATH_RECEIPT_MALFORMED"),
])
def test_an_unverifiable_upgrade_receipt_is_unproven(tmp_path, mut, needle):
    acc = _packet(tmp_path, release_files=NEW)
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    rec = _receipt(base_fp="9" * 64 if mut.get("base") else tfp,
                   representative=mut.get("representative", "COMPLETE"))
    if "version" in mut:
        rec["version"] = mut["version"]
    _w(acc, "upgrade_path.json", _readback(rec, **{
        k: v for k, v in mut.items()
        if k in ("att", "path", "head", "run_id")}))
    u = _unit(acc, "upgrade_path")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert needle in u["detail"]["reasons"], u["detail"]


def test_a_failed_upgrade_is_red_even_with_identical_sets(tmp_path):
    acc = _packet(tmp_path)
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    _w(acc, "upgrade_path.json", _readback(_receipt(
        result="FAILED", base_fp=tfp,
        reasons=["UPGRADE_ROWS_LOST:paper_positions:1->0"])))
    u = _unit(acc, "upgrade_path")
    assert u["passed"] is False and u["detail"]["status"] == SC.RED
    assert "UPGRADE_ROWS_LOST:paper_positions:1->0" in u["detail"]["reasons"]
    assert _unit(acc, "rollback_ready")["passed"] is False


@pytest.mark.parametrize("reasons", [
    ["UPGRADE_NOT_A_FULL_SHA", "UPGRADE_BASE_TREE_UNREADABLE"],
    ["UPGRADE_BASE_BUILD_FAILED:rc=1:applied=0/223"],
    ["UPGRADE_RUN_CRASHED:OSError:x"]])
def test_a_base_that_was_never_built_is_unproven_not_a_finding(
        tmp_path, reasons):
    acc = _packet(tmp_path, release_files=NEW)
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    _w(acc, "upgrade_path.json", _readback(_receipt(
        result="FAILED", base_fp=tfp, reasons=reasons)))
    u = _unit(acc, "upgrade_path")
    assert u["passed"] is False and u["detail"]["status"] == SC.UNPROVEN
    assert u["detail"]["reasons"][0] == SC.R_UPGRADE_BASE_UNBUILT
    # one release-side reason beside them makes it a finding again
    _w(acc, "upgrade_path.json", _readback(_receipt(
        result="FAILED", base_fp=tfp,
        reasons=reasons + ["UPGRADE_MIGRATION_FAILED:003_new.sql"])))
    assert _unit(acc, "upgrade_path")["detail"]["status"] == SC.RED


def test_the_base_side_reasons_are_the_receipts_own():
    assert set(SC.UPGRADE_BASE_SIDE) == {
        UP.R_NOT_A_FULL_SHA, UP.R_BASE_TREE_UNREADABLE,
        UP.R_BASE_BUILD_FAILED, UP.R_RUN_CRASHED}


def test_a_blocked_schema_names_what_blocks_the_rollback(tmp_path):
    acc = _packet(tmp_path, release_files=NEW)
    tfp = json.loads((acc / "rollback.json").read_text())[
        "migrations"]["target"]["fingerprint"]
    _w(acc, "upgrade_path.json", _readback(_receipt(
        base_fp=tfp, compat="NOT_PROVEN_COMPATIBLE")))
    assert _unit(acc, "upgrade_path")["passed"] is True
    u = _unit(acc, "rollback_ready")
    assert u["passed"] is False and u["detail"]["schema"] == "BLOCKED"
    assert "ROLLBACK_SCHEMA_BLOCKED:ROLLBACK_COLUMN_DROPPED:a.c" in \
        u["detail"]["reasons"]


def test_the_deployment_row_needs_all_nine_units(tmp_path):
    """V2 adds three requirements and removes none: the six RC6 units plus
    lineage, upgrade path and rollback."""
    out = SC.score(str(_packet(tmp_path)), release_sha=REL)
    dep = next(c for c in out["categories"]
               if c["category"] == "Deployment infrastructure")
    names = [u["unit"] for u in dep["units"]]
    assert names == ["sportsassets-api_on_release",
                     "sportsassets-workers_on_release",
                     "sportsassets-market-plane_on_release",
                     "workers_boot_on_release",
                     "market_plane_heartbeat_on_release",
                     "migration_integrity", "release_lineage",
                     "upgrade_path", "rollback_ready"]
    assert dep["components"][0]["denominator"] == 9


# ── 4. the grading-logic diff ────────────────────────────────────────────

def _sc(units, changes=(), passing=0):
    cats = {}
    for (cat, unit), (passed, cls, detail) in units.items():
        cats.setdefault(cat, []).append({"unit": unit, "passed": passed,
                                         "class": cls, "detail": detail})
    return {"version": "X", "passing": passing,
            "grading_changes": list(changes),
            "categories": [{"category": c, "readiness": None,
                            "passes": False, "components": [],
                            "units": u} for c, u in cats.items()]}


def test_every_verdict_difference_is_listed_declared_or_not():
    prior = _sc({("K", "cred"): (False, "FAIL", "agg"),
                 ("K", "same"): (True, "PASS", 1),
                 ("D", "gone"): (True, "PASS", None),
                 ("R", "lowered"): (True, "PASS", None),
                 ("R", "re"): (False, "FAIL", None)})
    cur = _sc({("K", "cred"): (True, "PASS", "own"),
               ("K", "same"): (True, "PASS", 2),
               ("D", "new"): (False, "FAIL", None),
               ("R", "lowered"): (False, "UNMEASURED",
                                  "UNMEASURED:INPUT_NAMES_ANOTHER_RELEASE:x"),
               ("R", "re"): (False, "UNMEASURED", "y")},
              changes=[{"id": "C1", "units": [["K", "cred"]], "reason": "r1"},
                       {"id": "P", "units": [["*", "*"]],
                        "when_current_detail_contains":
                        "INPUT_NAMES_ANOTHER_RELEASE", "reason": "pin"}])
    rep = GD.diff(prior, cur)
    got = {(d["category"], d["unit"]): d for d in rep["differences"]}
    assert got[("K", "cred")]["change"] == GD.LIFTED
    assert got[("K", "cred")]["declared"] == ["C1"]
    assert got[("D", "new")]["change"] == GD.ADDED
    assert got[("D", "gone")]["change"] == GD.REMOVED
    assert got[("R", "lowered")]["change"] == GD.LOWERED
    assert got[("R", "lowered")]["declared"] == ["P"]
    assert got[("R", "re")]["change"] == GD.RECLASSIFIED
    assert got[("R", "re")]["reason"] == "UNDECLARED"
    assert ("K", "same") not in got
    assert rep["detail_only_changes"] == ["K / same"]
    assert rep["verdict"] == GD.UNDECLARED
    assert set(rep["undeclared"]) == {"D / new", "D / gone", "R / re"}
    assert rep["lifted"] == ["K / cred"] and rep["lifted_undeclared"] == []


def test_no_difference_and_no_prior_are_named():
    s = _sc({("A", "u"): (True, "PASS", None)})
    assert GD.diff(s, s)["verdict"] == GD.NO_DIFFERENCE
    assert GD.diff(None, s)["verdict"] == GD.PRIOR_UNAVAILABLE


def _prior_source(tmp_path):
    p = tmp_path / "prior_scorecard_14.py"
    r = subprocess.run(["git", "-C", str(ROOT), "show",
                        "%s:backend/tools/scorecard_14.py" %
                        SC.PRIOR_EVALUATOR_SHA], capture_output=True)
    if r.returncode != 0:
        pytest.skip("the pinned prior evaluator's commit is not in this "
                    "clone (capital-critical checks out full history)")
    p.write_bytes(r.stdout)
    return p


def test_the_pinned_prior_evaluator_rescores_the_same_packet(tmp_path):
    """2fadc8dc (the RC5 judge) and this evaluator over one packet: the
    Kalshi credential unit is LIFTED by the declared scope change, the
    three Deployment units are ADDED, and nothing is UNDECLARED."""
    prior = _prior_source(tmp_path)
    acc = _packet(tmp_path)
    _w(acc, "red_team.json", {"status": "OK", "data": {"readiness": {
        "implementation_sha": REL, "controls": {"CREDENTIAL_CLASSES": {
            "status": "RED", "blockers": [
                "CREDENTIAL_CLASS_MISMATCH:PMUS:POLYMARKET_EXCHANGE_RSA_M2M"],
            "evidence": {"expected": {"PMUS": "x", "KALSHI": "y", "PMX": "z"},
                         "approved": {"KALSHI": ["KALSHI_ED25519_API_KEY"]},
                         "by_process": {"api": {"KALSHI":
                                                "KALSHI_ED25519_API_KEY"}},
                         "verdicts": {"KALSHI": "MATCHES"},
                         "not_provisioned": []}}}}}})
    assert SC.main([str(acc), "--release-sha", REL,
                    "--evaluator-sha", "e" * 40]) == 0
    assert GD.main(["run", "--acc", str(acc), "--release-sha", REL,
                    "--prior-source", str(prior),
                    "--prior-sha", SC.PRIOR_EVALUATOR_SHA,
                    "--out", str(acc / "grading_diff.json")]) == 0
    rep = json.loads((acc / "grading_diff.json").read_text())
    assert rep["prior"]["error"] is None
    assert rep["prior"]["version"] == "SCORECARD_14_V1"
    assert rep["current"]["version"] == "SCORECARD_14_V2"
    assert rep["current"]["sha"] == "e" * 40
    got = {(d["category"], d["unit"]): d["change"]
           for d in rep["differences"]}
    assert got[("Kalshi integration", "credential_class_control")] == \
        GD.LIFTED
    for u in ("release_lineage", "upgrade_path", "rollback_ready"):
        assert got[("Deployment infrastructure", u)] == GD.ADDED
    assert rep["undeclared"] == [] and rep["verdict"] == GD.DECLARED
    assert (acc / "scorecard_14_prior.json").is_file()
    # the prior ran on a COPY: nothing it wrote landed in the packet
    assert json.loads((acc / "scorecard_14.json").read_text())[
        "version"] == "SCORECARD_14_V2"


def test_a_prior_that_cannot_run_is_reported_not_hidden(tmp_path):
    acc = _packet(tmp_path)
    SC.main([str(acc), "--release-sha", REL])
    bad = tmp_path / "bad.py"
    bad.write_text("raise SystemExit(3)\n")
    rep, prior = GD.build(acc=acc, release_sha=REL, prior_source=bad,
                          prior_sha="0" * 40)
    assert prior is None and rep["verdict"] == GD.PRIOR_UNAVAILABLE
    assert rep["prior"]["error"].startswith("PRIOR_EVALUATOR_FAILED:rc=3")
    rep, _ = GD.build(acc=acc, release_sha=REL, prior_source=None,
                      prior_sha=None)
    assert rep["prior"]["error"] == "PRIOR_SOURCE_ABSENT"


def test_every_change_declared_since_the_prior_names_real_units():
    out = SC.score("/nonexistent", release_sha=REL)
    units = {(c["category"], u["unit"]) for c in out["categories"]
             for u in c["units"]}
    for g in SC.GRADING_CHANGES:
        assert g["id"] and g["reason"] and g["units"]
        for cat, unit in g["units"]:
            assert (cat, unit) == ("*", "*") or cat in SC.CATEGORIES, g
            if (cat, unit) != ("*", "*") and cat != "Red-team safeguards":
                assert (cat, unit) in units, (cat, unit)


# ── 5. the workflows, as written ─────────────────────────────────────────

def _pm_steps():
    return yaml.safe_load(PM_WF.read_text())["jobs"]["accept"]["steps"]


def _pm_step(prefix):
    hits = [s for s in _pm_steps() if s.get("name", "").startswith(prefix)]
    assert len(hits) == 1, prefix
    return hits[0]


def test_the_prior_evaluator_is_pinned_by_input_and_run_on_the_same_packet():
    doc = yaml.safe_load(PM_WF.read_text())
    inp = doc[True]["workflow_dispatch"]["inputs"]["prior_evaluator_sha"]
    assert inp["default"] == SC.PRIOR_EVALUATOR_SHA
    val = _pm_step("Validate the SHA")
    assert val["env"]["PRIOR_EVALUATOR_SHA"] == \
        "${{ inputs.prior_evaluator_sha }}"
    assert "prior_evaluator_sha is not a full 40-hex sha" in val["run"]
    st = _pm_step("Production evidence packet")
    run = st["run"]
    assert st["env"]["PRIOR_EVALUATOR_SHA"] == \
        "${{ inputs.prior_evaluator_sha }}"
    # the prior's file at that commit, by git; run by the JUDGE's diff tool
    assert 'git show "$PRIOR_EVALUATOR_SHA:backend/tools/scorecard_14.py"' \
        in run
    i_sc = run.index("python3 -I judge/backend/tools/scorecard_14.py")
    i_gd = run.index("python3 -I judge/backend/tools/grading_diff.py run")
    i_ep = run.index("python3 -I judge/backend/tools/evidence_packet.py")
    assert i_sc < i_gd < i_ep        # both before the manifest is hashed
    assert '--evaluator-sha "$JUDGE_SHA"' in run
    assert '--implementation-sha "$IMPL_SHA"' in run
    assert "--out acc/grading_diff.json" in run
    # the production frontend's build record: a public GET, no token
    fe = [ln for ln in run.splitlines() if "command.bettortoken.com/build.json"
          in ln and "curl" in ln]
    assert len(fe) == 1 and "ADMIN_TOKEN" not in fe[0] \
        and "X-Admin-Token" not in fe[0]
    assert "acc/frontend_preview_identity.json" in run
    # still only judge/ checkouts: the release and the judge
    co = [s["with"]["ref"] for s in _pm_steps()
          if str(s.get("uses", "")).startswith("actions/checkout")]
    assert co == ["${{ inputs.sha }}", "${{ github.sha }}"]


def test_the_new_steps_read_only_and_sit_in_order():
    names = [s.get("name", "") for s in _pm_steps()]

    def at(prefix):
        return next(i for i, n in enumerate(names) if n.startswith(prefix))
    assert at("Exact-SHA gate conclusions") < at("The upgrade-path receipt") \
        < at("Production readbacks")
    assert at("Render services") < at("Rollback readiness") < \
        at("Production evidence packet")
    for prefix in ("The upgrade-path receipt", "Rollback readiness"):
        st = _pm_step(prefix)
        assert "continue-on-error" not in st
        assert "secrets." not in json.dumps(st)
        assert "confirm=DO" not in st["run"]
    gates = _pm_step("Exact-SHA gate conclusions")
    assert gates["env"]["IMPL_SHA"] == "${{ inputs.implementation_sha }}"
    assert "> acc/gates_implementation.json" in gates["run"]


FAKE_GH_UP = r'''#!/usr/bin/env bash
echo "$*" >> "$FAKE_GH_DIR/calls"
case "$1 $2" in
  "api repos/"*)
     case "$2" in
       *actions/runs?head_sha=*) cat "$FAKE_GH_DIR/runs.json" ;;
       *) cat "$FAKE_GH_DIR/run.json" ;;
     esac ;;
  "run download")
     shift 2; d=""
     while [ $# -gt 0 ]; do [ "$1" = "--dir" ] && d="$2"; shift; done
     [ -d "$FAKE_GH_DIR/art" ] && cp "$FAKE_GH_DIR"/art/* "$d/" ;;
  "attestation verify") exit "${FAKE_GH_VERIFY_RC:-0}" ;;
  *) echo "fake gh: unexpected $*" >&2; exit 2 ;;
esac
'''


def _work(tmp_path, *, receipt=None, runs=None):
    work = tmp_path / "work"
    (work / "judge").mkdir(parents=True)
    (work / "judge" / "backend").symlink_to(BACKEND)
    (work / "acc").mkdir()
    gh = tmp_path / "ghdir"
    gh.mkdir()
    (gh / "run.json").write_text(json.dumps({
        "id": GATE_RUN, "path": ".github/workflows/capital-critical.yml",
        "repository": {"full_name": REPO}, "head_sha": REL,
        "event": "push", "status": "completed", "conclusion": "success"}))
    (gh / "runs.json").write_text(json.dumps(runs or {"workflow_runs": []}))
    if receipt is not None:
        (gh / "art").mkdir()
        (gh / "art" / "upgrade-path-receipt.json").write_text(
            json.dumps(receipt))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(FAKE_GH_UP)
    (bindir / "gh").chmod(0o755)
    (tmp_path / "rt").mkdir()
    env = {"PATH": "%s:%s" % (bindir, os.environ["PATH"]), "SHA": REL,
           "REPO": REPO, "GH_TOKEN": "t", "HOME": str(tmp_path),
           "RUNNER_TEMP": str(tmp_path / "rt"), "FAKE_GH_DIR": str(gh)}
    return work, gh, env


def test_pm_acceptance_reads_the_gate_runs_upgrade_receipt(tmp_path):
    work, gh, env = _work(tmp_path, receipt=_receipt(base_fp="f" * 64))
    _w(work / "acc", "gates.json", {"runs": {"capital_critical": {
        "id": GATE_RUN, "head_sha": REL}}})
    _run_step("The upgrade-path receipt", work, env)
    doc = json.loads((work / "acc" / "upgrade_path.json").read_text())
    assert doc["reason"] is None and doc["receipt"]["result"] == "PASSED"
    p = doc["provenance"]
    assert p["run_id"] == str(GATE_RUN) and p["attestation_verified"] is True
    assert p["workflow_path"] == ".github/workflows/capital-critical.yml"
    calls = (gh / "calls").read_text()
    assert "run download %d --repo %s --name capital-critical-%s" % (
        GATE_RUN, REPO, REL) in calls
    assert "--signer-workflow %s/.github/workflows/capital-critical.yml" % \
        REPO in calls
    # absent: recorded with its reason, never a pass
    work2, _, env2 = _work(tmp_path / "b")
    _w(work2 / "acc", "gates.json", {"runs": {"capital_critical": {
        "id": GATE_RUN}}})
    _run_step("The upgrade-path receipt", work2, env2)
    doc = json.loads((work2 / "acc" / "upgrade_path.json").read_text())
    assert doc["reason"] == UP.R_RECEIPT_NOT_IN_ARTIFACT
    assert doc["receipt"] is None


def test_the_rollback_step_as_written_over_a_real_repository(tmp_path):
    """Render names the previous commit; git says it is an ancestor and
    gives its migrations; GitHub gives its gates: rollback.json READY with
    identical sets (the release adds none)."""
    repo, base, impl, rel, merge, genv = _repo(tmp_path)
    # the 'previous release' is the base line; the release is rel (whose
    # tree adds 002): a migration added, so compatibility needs a receipt
    work, gh, env = _work(tmp_path / "w")
    for p in ("acc", "judge"):
        os.rename(work / p, repo / p)
    runs = {"workflow_runs": [
        {"name": n, "id": i, "status": "completed", "conclusion": "success",
         "created_at": "2026-10-08T00:00:00Z", "head_sha": base,
         "event": "push", "html_url": "u"}
        for i, n in enumerate(("backend-tests", "capital-critical",
                               "commit-guard", "engine-diagnostic"))]}
    (pathlib.Path(env["FAKE_GH_DIR"]) / "runs.json").write_text(
        json.dumps(runs))
    acc = repo / "acc"
    for s in SC.SERVICES:
        _w(acc, "deploys_%s.json" % s, _deploys(rel, base))
    _w(acc, "render.json", {s: {"live_commit": rel} for s in SC.SERVICES})
    env = dict(env, SHA=rel)
    env.update({k: genv[k] for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                      "GIT_COMMITTER_NAME",
                                      "GIT_COMMITTER_EMAIL")})
    _run_step("Rollback readiness", repo, env)
    rb = json.loads((acc / "rollback.json").read_text())
    assert rb["target_sha"] == base
    assert rb["target_is_ancestor_of_release"] is True
    assert rb["target_gates"]["runs"]["capital_critical"]["head_sha"] == base
    assert rb["migrations"]["added"] == ["002_more.sql"]
    assert rb["migrations"]["target"]["count"] == 1
    assert rb["migrations"]["release"]["count"] == 2
    assert rb["status"] == RB.SCHEMA_BY_RECEIPT and rb["reasons"] == []
    assert "api repos/%s/actions/runs?head_sha=%s" % (REPO, base) in \
        (pathlib.Path(env["FAKE_GH_DIR"]) / "calls").read_text()


def test_capital_critical_writes_attests_and_keeps_the_upgrade_receipt():
    doc = yaml.safe_load(CC_WF.read_text())
    steps = doc["jobs"]["suite"]["steps"]
    names = [s.get("name", "") for s in steps]
    up = next(s for s in steps
              if s.get("name", "").startswith("The upgrade-path receipt"))
    att = next(s for s in steps
               if s.get("name", "").startswith("Attest the upgrade-path"))
    assert names.index("The fresh-database receipt (what production's "
                       "runner built, fingerprinted as the API "
                       "fingerprints production)") < names.index(up["name"]) \
        < names.index(att["name"]) < names.index("Run the whole suite")
    assert up["id"] == "upgrade"
    assert up["if"] == "always() && steps.freshdb.outcome != 'skipped'"
    run = up["run"]
    assert "python tools/upgrade_path_receipt.py run" in run
    assert '--sha "$GITHUB_SHA"' in run and "--base-backend" in run
    assert "git -C .. archive" in run
    assert 'git rev-parse -q --verify "$GITHUB_SHA^1"' in run
    assert "refs/remotes/origin/claude/release-api" in run
    assert run.rstrip().endswith("exit 0")
    assert att["with"]["subject-path"] == "${{ steps.upgrade.outputs.receipt }}"
    assert att["continue-on-error"] is True
    upl = next(s for s in steps if str(s.get("uses", "")).startswith(
        "actions/upload-artifact"))
    assert "${{ runner.temp }}/upgrade-path-receipt.json" in \
        upl["with"]["path"]
    assert doc[True]["workflow_dispatch"]["inputs"]["upgrade_base_sha"][
        "default"] == ""
    assert "secrets." not in CC_WF.read_text()


# ── 6. the packet ────────────────────────────────────────────────────────

def test_the_packet_reports_lane_e_at_declared_paths(tmp_path):
    acc = _packet(tmp_path)
    _w(acc, "red_team.json", {"status": "OK", "data": {"readiness": {
        "implementation_sha": REL, "controls": {}}}})
    _w(acc, "release.json", {"api": {"sha": REL}, "workers": {"sha": REL}})
    _w(acc, "canary.json", {"boots": {"api": {"commit_sha": REL},
                                      "workers_boot": {"commit_sha": REL}}})
    _w(acc, "venues.json", {"status": "OK", "data": {"health": {
        "KALSHI_HEALTH": {"mechanism": {"plane": {"commit": PREV}}}}}})
    _w(acc, "render_events_sportsassets-api.json",
       {"start": "s", "end": "e", "stopped": "SHORT_PAGE"})
    SC.main([str(acc), "--release-sha", REL, "--evaluator-sha", "e" * 40])
    _w(acc, "grading_diff.json", {"version": GD.VERSION,
                                  "verdict": GD.DECLARED})
    p = EP.build(acc, now=1.0)
    assert p["pinning"]["pinning.evaluator"]["value"]["sha"] == "e" * 40
    assert p["pinning"]["pinning.release_sha"]["value"] == REL
    assert p["grading_diff"]["verdict"]["value"] == GD.DECLARED
    assert p["lineage"]["trees_equal"]["value"] is True
    assert p["rollback"]["target_sha"]["value"] == PREV
    assert p["upgrade_path"]["receipt.result"]["value"] == EP.UNAVAILABLE
    dh = p["deployment_health"]
    assert dh["sportsassets-api"]["verdict"] == "RUNNING_ON_RELEASE"
    assert dh["sportsassets-workers"]["verdict"] == "RUNNING_ON_RELEASE"
    # the plane's own heartbeat names another commit than Render: visible
    assert dh["sportsassets-market-plane"]["verdict"] == "NOT_ON_RELEASE"
    assert dh["sportsassets-api"]["events_window"]["stopped"] == "SHORT_PAGE"
    assert dh["sportsassets-workers"]["events_window"]["value"] == \
        EP.UNAVAILABLE


# ── 7. reason codes are classified; the tools stay stdlib where judged ───

def test_every_lane_e_reason_code_is_classified():
    from sportsassets import refusal_taxonomy as RT
    from sportsassets import refusal_taxonomy_table as TT
    codes = {}
    for mod in (SC, RB, UP):
        for name, v in vars(mod).items():
            if name.startswith("R_") and isinstance(v, str):
                codes[RT.normalize(v)] = "%s:%s" % (mod.__name__, name)
    missing = {c: w for c, w in codes.items() if c not in TT.NOT_REFUSAL}
    assert not missing, missing


@pytest.mark.parametrize("tool,argv", [
    ("rollback_readiness.py", ["target", "/nonexistent"]),
    ("grading_diff.py", ["--help"]),
    ("upgrade_path_receipt.py", ["readback", "--help"]),
    ("scorecard_14.py", ["--help"]),
])
def test_the_judged_tools_run_on_a_bare_interpreter(tool, argv):
    """pm-acceptance runs them with `python3 -I` from judge/: no
    third-party import may be needed on those paths."""
    r = subprocess.run([sys.executable, "-I", str(BACKEND / "tools" / tool)]
                       + argv, capture_output=True, text=True, timeout=60,
                       env={"PATH": os.environ["PATH"]})
    assert r.returncode == 0, r.stderr[-1500:]
