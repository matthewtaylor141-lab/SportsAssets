"""PROOF 19: AN IMPROVEMENT TASK PRODUCES A REAL CANDIDATE ARTIFACT.

`tools/improvement_sandbox.py` is run as a subprocess -- the way CI runs it
-- against (a) a temporary git repository built here and (b) THIS
repository, through `git worktree add` from the serving base commit into a
temporary directory. Each run produces an actual commit on
improve/<task>, with a parameter or code diff, the tests it ran and an
evaluation report, and records the candidate (branch@sha) in
improvement_candidates. The candidate is then reviewed in-process by an
evaluator that is not its proposer. Protected diffs, and a production
environment, are refused by name and leave no branch.

Nothing is pushed: the temporary repository has no remote, and the tool has
no remote operation.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

from sportsassets.agents import improvement as IMP
from tests import audrey_helpers as H

pg = H.pg
BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TOOL = BACKEND / "tools" / "improvement_sandbox.py"
T = 1963000000.0
BASE_COMMIT = "dff544c735906227cc93b51a6b14d33f081ae818"

sys.path.insert(0, str(BACKEND / "tools"))
import improvement_sandbox as SB  # noqa: E402


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                           "-c", "user.email=t@t", "-c",
                           "commit.gpgsign=false", *a], check=True,
                          capture_output=True, text=True).stdout


def _tmp_repo(tmp: pathlib.Path) -> pathlib.Path:
    repo = tmp / "repo"
    (repo / "backend" / "tests").mkdir(parents=True)
    (repo / "backend" / "collect_policy.py").write_text(
        "#: how many candidates one pass attempts\n"
        "CANDIDATES_PER_PASS = 3  # versioned default\n\n\n"
        "def per_pass():\n    return CANDIDATES_PER_PASS\n")
    (repo / "backend" / "tests" / "test_policy.py").write_text(
        "import importlib.util, pathlib\n"
        "P = pathlib.Path(__file__).resolve().parents[1] / "
        "'collect_policy.py'\n"
        "def _m():\n"
        "    s = importlib.util.spec_from_file_location('cp', P)\n"
        "    m = importlib.util.module_from_spec(s)\n"
        "    s.loader.exec_module(m)\n    return m\n"
        "def test_per_pass_is_the_constant():\n"
        "    m = _m()\n    assert m.per_pass() == m.CANDIDATES_PER_PASS\n")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                   check=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _run(*args, env=None):
    e = dict(os.environ)
    e.pop("RENDER", None)
    e.update(env or {})
    got = subprocess.run([sys.executable, str(TOOL), *args],
                         capture_output=True, text=True, env=e, timeout=600)
    try:
        body = json.loads(got.stdout)
    except ValueError:
        body = {"raw": got.stdout, "err": got.stderr}
    return got.returncode, body


@pytest.fixture()
async def db():
    if not H.DSN:
        pytest.skip("needs RN1X_TEST_DSN")
    async with H.connect() as c:
        if not await IMP.has_schema(c):
            pytest.skip("migration 155 is not in this database")
        await H.ensure_core_tables(c)
        await H.purge(c)
        yield c
        await H.purge(c)


SAMPLES = [{"offered": 8, "attempted": 3, "elapsed_s": 20.0,
            "budget_s": 120.0} for _ in range(10)]


# ═════════════════════════════════════════════════════════════════════
# 1 · A PARAMETER CANDIDATE FROM A TASK ID
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_proof19_a_task_becomes_a_committed_parameter_candidate(
        db, tmp_path):
    repo = _tmp_repo(tmp_path)
    tid = "audt-sbx-policy"
    await IMP.create_task(
        db, assignee="DEREK", created_by="AUDREY", kind=IMP.TASK_KIND,
        title="collection pass limit", task_id=tid, now=T, spec={
            "change_class": "COLLECTION_PASS_LIMIT",
            "hypothesis": "passes leave candidates for LIMIT_PER_PASS",
            "evidence": {"passes_with_candidates_left_for_limit": 10},
            "params": {"candidates_per_pass": 5},
            "target": {"path": "backend/collect_policy.py",
                       "constant": "CANDIDATES_PER_PASS"},
            "tests": ["tests/test_policy.py"], "test_cwd": "backend",
            "replay": {"samples": SAMPLES}})
    out = tmp_path / "report.json"
    rc, rep = _run("--task-id", tid, "--dsn", H.DSN, "--repo", str(repo),
                   "--base", "HEAD", "--out", str(out), "--now", str(T),
                   "--workdir", str(tmp_path))
    assert rc == 0, rep
    assert json.loads(out.read_text())["commit"] == rep["commit"]
    branch = "improve/audt-sbx-policy"
    assert rep["branch"] == branch
    # A REAL COMMIT ON ITS OWN BRANCH, CHILD OF THE BASE
    sha = _git(repo, "rev-parse", branch).strip()
    assert sha == rep["commit"]
    assert _git(repo, "rev-parse", branch + "^").strip() == \
        _git(repo, "rev-parse", "main").strip()
    diff = _git(repo, "diff", "main", branch)
    assert "-CANDIDATES_PER_PASS = 3  # versioned default" in diff
    assert "+CANDIDATES_PER_PASS = 5  # versioned default" in diff
    files = _git(repo, "show", "--name-only", "--format=", branch).split()
    rdir = "backend/research/improvements/audt_sbx_policy/"
    assert set(files) == {"backend/collect_policy.py",
                          rdir + "evaluation.json",
                          rdir + "policy_version.json",
                          rdir + "test_candidate_audt_sbx_policy.py"}
    committed = json.loads(_git(repo, "show",
                                branch + ":" + rdir + "evaluation.json"))
    for k in ("hypothesis", "evidence", "affected_behavior",
              "training_boundary", "evaluation_boundary", "success_metrics",
              "harm_metrics", "diff", "test_results", "release_scope",
              "rollback", "replay", "harm"):
        assert k in committed, k
    assert committed["release_scope"] == IMP.SCOPE_PREAUTH
    # THE TESTS RAN: the repository's own and the generated bounds tests
    tr = rep["test_results"]
    assert tr["passed"] is True and tr["counts"]["passed"] == 3
    # THE REPLAY, judged by the CLASS's harm metrics
    assert rep["replay"]["new"]["mean_additional_attempts_per_pass"] == 2.0
    assert rep["replay"]["new"]["evidence_category"] == IMP.SIMULATED
    assert rep["harm"]["projected_deadline_breach_fraction"][
        "breached"] is False
    # THE CHECKOUT WAS NOT TOUCHED, NOTHING WAS PUSHED
    assert "= 3" in (repo / "backend" / "collect_policy.py").read_text()
    assert _git(repo, "remote").strip() == ""
    assert _git(repo, "worktree", "list").count("\n") == 1
    # RECORDED IN improvement_candidates, the task moved on
    rec = rep["recorded"]
    assert rec["ok"] is True
    c = await IMP.candidate(db, rec["candidate_id"])
    assert c["artifact_ref"] == "%s@%s" % (branch, sha)
    assert c["state"] == "PROPOSED" and c["task_id"] == tid
    assert c["proposed_by"] == "improvement_sandbox:DEREK"
    assert c["params"] == {"candidates_per_pass": 5}
    assert "+CANDIDATES_PER_PASS = 5" in c["diff"]
    assert c["test_results"]["passed"] is True
    assert c["evidence"]["sandbox_report"]["commit"] == sha
    assert (await IMP.read_task(db, tid))["status"] == "CANDIDATE_READY"
    # the same task again: the branch exists, refused, nothing changes
    rc2, again = _run("--task-id", tid, "--dsn", H.DSN, "--repo", str(repo),
                      "--base", "HEAD", "--workdir", str(tmp_path))
    assert rc2 == 2 and again["refusal"] == SB.R_BRANCH_EXISTS


# ═════════════════════════════════════════════════════════════════════
# 2 · A CODE CANDIDATE FROM A UNIFIED DIFF, REVIEWED BY ANOTHER
# ═════════════════════════════════════════════════════════════════════

GOOD_DIFF = """diff --git a/backend/collect_policy.py b/backend/collect_policy.py
--- a/backend/collect_policy.py
+++ b/backend/collect_policy.py
@@ -4,3 +4,7 @@ CANDIDATES_PER_PASS = 3  # versioned default

 def per_pass():
     return CANDIDATES_PER_PASS
+
+
+def refusal_memory_s():
+    return 6 * 3600
diff --git a/backend/tests/test_memory.py b/backend/tests/test_memory.py
new file mode 100644
--- /dev/null
+++ b/backend/tests/test_memory.py
@@ -0,0 +1,9 @@
+import importlib.util, pathlib
+P = pathlib.Path(__file__).resolve().parents[1] / 'collect_policy.py'
+def test_memory_is_six_hours():
+    s = importlib.util.spec_from_file_location('cp', P)
+    m = importlib.util.module_from_spec(s)
+    s.loader.exec_module(m)
+    assert m.refusal_memory_s() == 21600
+
+
"""


def _spec(tmp, tid, diff, **kw):
    p = tmp / ("%s.json" % tid)
    p.write_text(json.dumps(dict({
        "task_id": tid, "change_class": "CODE_CHANGE",
        "assigned_agent": "DEREK",
        "hypothesis": "a shorter refusal memory re-offers fixtures sooner",
        "evidence": {"skipped_recently_refused": 16}, "diff": diff,
        "tests": ["tests/test_policy.py", "tests/test_memory.py"],
        "test_cwd": "backend"}, **kw)))
    return p


@pg
async def test_proof19_a_code_diff_becomes_a_candidate_reviewed_by_another(
        db, tmp_path):
    repo = _tmp_repo(tmp_path)
    tid = "audt-sbx-code"
    await IMP.create_task(db, assignee="DEREK", created_by="AUDREY",
                          kind=IMP.TASK_KIND, title="refusal memory",
                          spec={"change_class": "CODE_CHANGE"},
                          task_id=tid, now=T)
    first = await IMP.run_due(db, now=T)
    assert first["advanced"][0]["waiting_on"] == IMP.NEEDS_SANDBOX
    rc, rep = _run("--spec", str(_spec(tmp_path, tid, GOOD_DIFF)),
                   "--dsn", H.DSN, "--repo", str(repo), "--base", "main",
                   "--now", str(T), "--workdir", str(tmp_path))
    assert rc == 0, rep
    diff = _git(repo, "diff", "main", rep["branch"])
    assert "+def refusal_memory_s():" in diff
    assert "backend/tests/test_memory.py" in diff
    assert rep["test_results"]["passed"] is True
    assert rep["test_results"]["counts"]["passed"] == 2
    assert rep["release_scope"] == IMP.SCOPE_APPROVAL
    cid = rep["recorded"]["candidate_id"]
    # THE REVIEW: an evaluator that is not the proposer; CODE never
    # releases unattended -- it stops at APPROVAL_READY
    out = await IMP.run_due(db, now=T + 60)
    assert out["advanced"][0]["reviewed"] == [
        {"candidate_id": cid, "verdict": IMP.V_PASS}]
    c = await IMP.candidate(db, cid)
    assert c["state"] == "APPROVAL_READY"
    assert c["evaluated_by"] == IMP.EVALUATOR_SANDBOX_REVIEW
    assert c["proposed_by"] == "improvement_sandbox:DEREK"
    assert (await IMP.read_task(db, tid))["status"] == "APPROVAL_READY"
    trials = await IMP.trials(db, candidate_id=cid)
    assert [t["segment"] for t in trials] == ["SANDBOX_TESTS"]
    assert await db.fetchval("SELECT count(*) FROM improvement_releases") == 0

    # A DIFF WHOSE OWN TEST FAILS: committed, recorded -- and REJECTED
    bad = GOOD_DIFF.replace("== 21600", "== 1")
    tid2 = "audt-sbx-code-bad"
    await IMP.create_task(db, assignee="DEREK", created_by="AUDREY",
                          kind=IMP.TASK_KIND, title="bad",
                          spec={"change_class": "CODE_CHANGE"},
                          task_id=tid2, now=T)
    rc, rep2 = _run("--spec", str(_spec(tmp_path, tid2, bad)), "--dsn",
                    H.DSN, "--repo", str(repo), "--base", "main",
                    "--workdir", str(tmp_path))
    assert rc == 0 and rep2["test_results"]["passed"] is False
    await IMP.run_due(db, now=T + 120)
    c2 = await IMP.candidate(db, rep2["recorded"]["candidate_id"])
    assert c2["state"] == "REJECTED"
    assert c2["evaluation"]["tests_passed"] is False


# ═════════════════════════════════════════════════════════════════════
# 3 · THIS REPOSITORY, FROM THE SERVING BASE
# ═════════════════════════════════════════════════════════════════════

def test_proof19_this_repository_from_the_serving_base(tmp_path):
    """From the serving base commit when this clone has it (a shallow CI
    clone may not: then from HEAD -- still this repository, still a
    worktree added into a temporary directory)."""
    have = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e",
                           BASE_COMMIT + "^{commit}"], capture_output=True)
    base = BASE_COMMIT if have.returncode == 0 else _git(
        REPO, "rev-parse", "HEAD").strip()
    path = "backend/sportsassets/bettor_pair_observations.py"
    src = _git(REPO, "show", "%s:%s" % (base, path))
    old = int(re.search(r"^CANDIDATES_PER_PASS = (\d+)", src,
                        re.MULTILINE).group(1))
    new = old + 1 if old < 10 else old - 1
    before = (REPO / path).read_text()
    tid = "audt-real-%d" % os.getpid()
    branch = SB.branch_for(tid)
    spec = tmp_path / "s.json"
    spec.write_text(json.dumps({
        "task_id": tid, "change_class": "COLLECTION_PASS_LIMIT",
        "hypothesis": "one more candidate per pass",
        "params": {"candidates_per_pass": new}, "replay": False}))
    try:
        rc, rep = _run("--spec", str(spec), "--repo", str(REPO), "--base",
                       base, "--no-record", "--workdir", str(tmp_path))
        assert rc == 0, rep
        assert rep["base_commit"] == base
        diff = _git(REPO, "diff", base, branch, "--", path)
        assert "-CANDIDATES_PER_PASS = %d" % old in diff
        assert "+CANDIDATES_PER_PASS = %d" % new in diff
        # the generated test imported the REAL module and passed
        assert rep["test_results"]["passed"] is True, rep["test_results"]
        assert rep["test_results"]["counts"]["passed"] == 2
        # the checkout running this test is untouched
        assert (REPO / path).read_text() == before
    finally:
        subprocess.run(["git", "-C", str(REPO), "branch", "-D", branch],
                       capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"],
                       capture_output=True)
    assert branch not in _git(REPO, "branch", "--list", branch)


# ═════════════════════════════════════════════════════════════════════
# 4 · REFUSALS
# ═════════════════════════════════════════════════════════════════════

def _d(path, minus="", plus=""):
    return ("--- a/%s\n+++ b/%s\n@@ -1 +1 @@\n%s%s" % (
        path, path, ("-%s\n" % minus) if minus else "",
        ("+%s\n" % plus) if plus else ""))


def test_protected_diffs_are_refused_by_name():
    cases = [
        (_d("backend/sportsassets/config.py", "a", "b"),
         SB.R_PROTECTED_PATH),
        (_d("backend/sportsassets/bettor_risk_engine.py", "a", "b"),
         SB.R_PROTECTED_PATH),
        (_d("backend/sportsassets/bettor_owner_authorization.py", "a", "b"),
         SB.R_PROTECTED_PATH),
        (_d("backend/tools/gate_verdict.py", "a", "b"), SB.R_PROTECTED_PATH),
        (_d("backend/sportsassets/agents/improvement.py", "a", "b"),
         SB.R_PROTECTED_PATH),
        (_d("backend/tools/capital_critical_tests.txt",
            "tests/test_admission_control.py"), SB.R_CRITICAL_REMOVAL),
        (_d("backend/sportsassets/bettor_funded_management.py",
            "FUNDED_EXIT_SUBMISSION_ENABLED = False",
            "FUNDED_EXIT_SUBMISSION_ENABLED = True"), SB.R_SWITCH),
        (_d("backend/sportsassets/bettor_pair_observations.py", "",
            "max_position_usd = 10_000"), SB.R_PROTECTED_KEY_IN_DIFF),
    ]
    for diff, want in cases:
        with pytest.raises(SB.Refused) as e:
            SB.check_diff(diff)
        assert e.value.refusal == want, diff
    # ADDING a capital-critical test is allowed
    ok = SB.check_diff(_d("backend/tools/capital_critical_tests.txt", "",
                          "tests/test_new.py"))
    assert ok["files"] == ["backend/tools/capital_critical_tests.txt"]


def test_a_protected_run_leaves_no_branch_and_production_is_refused(
        tmp_path):
    repo = _tmp_repo(tmp_path)
    spec = _spec(tmp_path, "audt-prot", _d(
        "backend/sportsassets/config.py", "a", "b"))
    rc, rep = _run("--spec", str(spec), "--repo", str(repo), "--base",
                   "main", "--no-record", "--workdir", str(tmp_path))
    assert rc == 2 and rep["refusal"] == SB.R_PROTECTED_PATH
    assert "improve/" not in _git(repo, "branch", "--list")
    spec2 = tmp_path / "k.json"
    spec2.write_text(json.dumps({
        "task_id": "audt-k", "change_class": "COLLECTION_PASS_LIMIT",
        "params": {"candidates_per_pass": 4},
        "touched_keys": ["account_authority"]}))
    rc, rep = _run("--spec", str(spec2), "--repo", str(repo), "--no-record")
    assert rc == 2 and rep["refusal"] == IMP.R_PROTECTED_KEY
    assert rep["detail"]["protected"] == ["account_authority"]
    spec3 = tmp_path / "b.json"
    spec3.write_text(json.dumps({
        "task_id": "audt-b", "change_class": "COLLECTION_PASS_LIMIT",
        "params": {"candidates_per_pass": 50}}))
    rc, rep = _run("--spec", str(spec3), "--repo", str(repo), "--no-record")
    assert rc == 2 and rep["refusal"] == IMP.R_OUT_OF_BOUNDS
    rc, rep = _run("--spec", str(spec2), "--repo", str(repo), "--no-record",
                   env={"RENDER": "true"})
    assert rc == 4 and rep["refusal"] == SB.R_PRODUCTION
    rc, rep = _run("--spec", str(spec2), "--repo", str(tmp_path / "nope"),
                   "--no-record")
    assert rc == 4 and rep["refusal"] == SB.R_NOT_A_REPO


def test_the_tool_has_no_remote_operation():
    src = TOOL.read_text()
    for word in ('"push"', "'push'", '"fetch"', '"pull"', "remote add",
                 "deploy("):
        assert word not in src
