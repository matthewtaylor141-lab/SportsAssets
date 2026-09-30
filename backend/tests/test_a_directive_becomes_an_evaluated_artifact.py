"""A MANAGEMENT DIRECTIVE BECOMES AN INSPECTABLE, EVALUATED ARTIFACT.

The chain, through the production paths:

  1. AUTHENTICATED DIRECTIVE. "Cut losses from marginal entries without
     increasing capital limits." is sent to Audrey's chat route with the
     operator session (a read credential is refused and writes nothing). It
     becomes a durable LOSS_REDUCTION directive with Derek's and Xavier's
     DIRECTIVE_IMPROVEMENT tasks (deterministic mode: no provider key).
  2. ASSIGNED WORK. The scheduled improvement pass (`improvement.run_due`)
     has each assigned agent take its task up. Derek opens his own
     IMPROVEMENT task on the class he owns (DEREK_ENTRY_POLICY_THRESHOLD),
     tighten-only because the objective is loss reduction. Xavier has no
     registered replay for directive work: his task WAITS and names why.
  3. EVALUATION. The same pass replays Derek's variants on recorded entry
     valuations (training / fixture-level holdout), selects on training,
     tries once on the holdout, and judges by the class's registered rule.
     Proposer DEREK, evaluator the deterministic replay; it stops at
     APPROVAL_READY -- nothing is activated.
  4. ARTIFACT. `tools/improvement_sandbox.py --candidate-id` (a subprocess,
     as CI runs it) builds the evaluated candidate's exact parameter as a
     commit on improve/<task> from THIS repository's HEAD: a one-line diff of
     derek_policy.MIN_GROSS_EDGE_PP, the tests it ran, and evaluation.json.
     The artifact (branch@sha, diff, test results) is attached to the SAME
     candidate that holds the evaluation. Nothing is pushed; the branch is
     deleted afterwards.
  5. The directive's task follows the outcome, the directive reads
     IN_PROGRESS through its route, and the candidate detail shows params,
     evaluation, artifact and tests together.

THE EVALUATION EVIDENCE IS SYNTHETIC: recorded-valuation rows seeded as
inputs (5.5 pp entries that lost, 12 pp entries that won). Everything the
test proves -- directive, tasks, candidates, trials, evaluation, artifact --
is written by production code, never inserted here.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from sportsassets.agents import directives as D
from sportsassets.agents import improvement as IMP
from tests import _audrey_chat_fixture as F
from tests import audrey_helpers as AH

pg = F.pg
BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TOOL = BACKEND / "tools" / "improvement_sandbox.py"
DAY = 86400.0
TEXT = "Cut losses from marginal entries without increasing capital limits."
NOW = F.T0 + DAY


def _names(holdout: bool, n: int, prefix: str) -> list:
    out, i = [], 0
    while len(out) < n:
        f = "%sdirart-%s-%d" % (AH.PFX, prefix, i)
        if IMP.assign_holdout(f, salt=IMP.DEREK_HOLDOUT_SALT,
                              percent=IMP.DEREK_HOLDOUT_PERCENT) == holdout:
            out.append(f)
        i += 1
    return out


async def _seed(c, *, tb, n=40):
    """Per fixture: a 5.5 pp entry that LOST and a 12 pp entry that WON.
    Today's 5 pp threshold takes both; 6 pp takes only the winner."""
    for i, f in enumerate(_names(False, n, "t") + _names(True, n, "h")):
        dec = (tb + DAY + i * 60) if i >= n else (tb - 5 * DAY + i * 60)
        await AH.valuation(c, fixture=f, decided=dec, p=0.555, price=0.5,
                           cost=0.5, edge=0.0, outcome=0,
                           outcome_at=dec + 3600)
        await AH.valuation(c, fixture=f, decided=dec + 1, p=0.62, price=0.5,
                           cost=0.5, edge=0.1, outcome=1,
                           outcome_at=dec + 3600)


async def _purge_improvement(c):
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM improvement_trials WHERE task_id LIKE "
                        " 'imp-task-dir-%'")
        await c.execute("DELETE FROM improvement_candidates WHERE task_id "
                        " LIKE 'imp-task-dir-%'")
        if await c.fetchval("SELECT to_regclass('improvement_events')"):
            await c.execute("DELETE FROM improvement_events WHERE task_id "
                            " LIKE 'imp-task-dir-%'")
        await c.execute("DELETE FROM agent_task_events WHERE task_id LIKE "
                        " 'imp-task-dir-%'")
        await c.execute("DELETE FROM agent_tasks WHERE task_id LIKE "
                        " 'imp-task-dir-%'")


@pytest.fixture()
def world():
    if not F.DSN:
        pytest.skip("needs RN1X_TEST_DSN")

    async def _setup():
        c = await F.connect()
        try:
            await F.ensure_schema(c)
            await F.purge(c)
            await AH.purge(c)
            await _purge_improvement(c)
            await _seed(c, tb=NOW - 14 * DAY)
        finally:
            await c.close()
    F.run(_setup())
    yield

    async def _teardown():
        c = await F.connect()
        try:
            await _purge_improvement(c)
            await AH.purge(c)
            await F.teardown(c)
        finally:
            await c.close()
    F.run(_teardown())


def _chat(client, headers, message, cookies=None):
    body = {"message": message, "request_id": F.rid("dirart"),
            "conversation_id": "dirart-conv"}
    client.cookies.clear()
    for k, v in (cookies or {}).items():
        client.cookies.set(k, v)
    r = client.post("/api/command/agents/audrey/chat", json=body,
                    headers=headers)
    client.cookies.clear()
    assert r.status_code == 200, r.text
    return r.json()


async def _q(sql, *args):
    c = await F.connect()
    try:
        return [dict(r) for r in await c.fetch(sql, *args)]
    finally:
        await c.close()


async def _call(fn, *a, **kw):
    c = await F.connect()
    try:
        return await fn(c, *a, **kw)
    finally:
        await c.close()


def _git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], check=True,
                          capture_output=True, text=True).stdout


@pg
def test_a_directive_becomes_an_evaluated_committed_candidate(
        world, monkeypatch, tmp_path):
    from sportsassets.agents import audrey_chat as AC
    F.no_network(monkeypatch)
    clock = F.Clock(F.T0)
    client = F.build_client(monkeypatch, clock)

    # ── 1 · AUTHENTICATED DIRECTIVE ────────────────────────────────────
    got = _chat(client, F.desk_headers(), TEXT)
    assert got["status"] == AC.S_REQUIRES_OPERATOR       # read: refused
    got = _chat(client, {}, TEXT, cookies=F.operator_cookie())
    assert got["status"] == AC.S_DIRECTIVE, got
    d = got["directive"]
    assert d["requested_by_role"] == "operator"
    assert d["objective_kind"] == "LOSS_REDUCTION"
    assert d["change_class"] == D.POLICY_CANDIDATE
    assert d["status"] == D.ACTIVE
    did = d["directive_id"]
    dtask = D.task_id_for(did, "DEREK")
    xtask = D.task_id_for(did, "XAVIER")
    assert d["task_ids"] == [dtask, xtask]

    # ── 2 + 3 · THE SCHEDULED PASS: WORK TAKEN UP AND EVALUATED ────────
    out = F.run(_call(IMP.run_due, now=NOW))
    assert out.get("ok") is True, out
    wid = IMP.directive_work_task_id(dtask)
    taken = out["directive_work"]["taken_up"]
    assert [t["task_id"] for t in taken] == [dtask]
    assert taken[0]["improvement_task_id"] == wid
    assert taken[0]["change_class"] == "DEREK_ENTRY_POLICY_THRESHOLD"
    assert taken[0]["variants"] == [0.06, 0.07]            # tighten only
    assert xtask in out["directive_work"]["waiting"]

    w = F.run(_call(IMP.read_task, wid))
    assert w["kind"] == IMP.TASK_KIND and w["assignee"] == "DEREK"
    assert w["created_by"] == "DEREK" and w["directive_id"] == did
    assert w["spec"]["directive_task_id"] == dtask
    assert w["status"] == "APPROVAL_READY", w

    res = [a for a in out["advanced"] if a.get("task_id") == wid][0]
    assert res["verdict"] == IMP.V_PASS, res
    cid = res["candidate_id"]
    cand = F.run(_call(IMP.candidate, cid))
    assert cand["params"] == {"min_gross_edge_pp": 0.06}
    assert cand["state"] == "APPROVAL_READY"
    assert cand["proposed_by"] == "DEREK"
    assert cand["evaluated_by"] == IMP.EVALUATOR_REPLAY
    ev = cand["evaluation"]
    assert ev["verdict"] == IMP.V_PASS and ev["segment"] == "HOLDOUT"
    hm = ev["metrics"]
    # on the holdout: 6 pp keeps every winner and drops every loser
    assert hm["fixtures"] >= 30 and hm["fixtures_in_both"] == 0
    assert hm["fixture_mean_pnl_delta_per_contract"] > 0
    assert hm["fixture_mean_pnl_per_contract"] > \
        hm["baseline"]["fixture_mean_pnl_per_contract"]
    trials = F.run(_q("SELECT segment, verdict FROM improvement_trials "
                      " WHERE task_id=$1 ORDER BY segment, verdict", wid))
    assert {"segment": "HOLDOUT", "verdict": IMP.V_PASS} in trials
    assert sum(t["segment"] == "TRAINING" for t in trials) == 2
    # nothing was activated
    assert F.run(_q("SELECT 1 FROM agent_policy_versions WHERE "
                    " policy_key='DEREK_ENTRY_POLICY' AND state='ACTIVE'")) \
        == []
    # Derek's directive task follows; Xavier's waits and names why
    dt = F.run(_call(IMP.read_task, dtask))
    assert dt["status"] == "APPROVAL_READY", dt
    xt = F.run(_call(IMP.read_task, xtask))
    assert xt["status"] == "WAITING"
    xev = F.run(_q("SELECT kind, detail FROM agent_task_events WHERE "
                   " task_id=$1 ORDER BY at, kind", xtask))
    assert any(e["kind"] == "WAITING_FOR_AN_EVALUATOR" and
               IMP.R_NO_DIRECTIVE_EVALUATOR in str(e["detail"]) for e in xev)

    # ── 4 · THE ARTIFACT: A COMMIT OF EXACTLY THE EVALUATED PARAMETER ──
    base = _git("rev-parse", "HEAD").strip()
    branch = "improve/%s" % wid
    before = (BACKEND / "sportsassets" / "agents" / "derek_policy.py"
              ).read_text()
    env = {k: v for k, v in os.environ.items() if k != "RENDER"}
    try:
        p = subprocess.run(
            [sys.executable, str(TOOL), "--task-id", wid, "--candidate-id",
             cid, "--dsn", F.DSN, "--repo", str(REPO), "--base", base,
             "--workdir", str(tmp_path), "--now", str(NOW + 60)],
            capture_output=True, text=True, env=env, timeout=600)
        rep = json.loads(p.stdout)
        assert p.returncode == 0, rep
        sha = _git("rev-parse", branch).strip()
        assert rep["commit"] == sha and rep["base_commit"] == base
        diff = _git("diff", base, branch, "--",
                    "backend/sportsassets/agents/derek_policy.py")
        assert "-MIN_GROSS_EDGE_PP = 0.05  # versioned default" in diff
        assert "+MIN_GROSS_EDGE_PP = 0.06  # versioned default" in diff
        tr = rep["test_results"]
        assert tr["passed"] is True, tr
        assert tr["counts"]["passed"] >= 2
        rdir = "backend/research/improvements/%s/" % wid.replace("-", "_")
        files = set(_git("show", "--name-only", "--format=", branch).split())
        assert "backend/sportsassets/agents/derek_policy.py" in files
        assert any(f.endswith("/evaluation.json") for f in files), files
        evj = [f for f in files if f.endswith("/evaluation.json")][0]
        committed = json.loads(_git("show", "%s:%s" % (branch, evj)))
        assert committed["evaluated_candidate"]["candidate_id"] == cid
        assert committed["evaluated_candidate"]["evaluation"]["verdict"] == \
            IMP.V_PASS
        assert committed["replay"]["new"]["fixture_mean_pnl_per_contract"] \
            > committed["replay"]["baseline"]["fixture_mean_pnl_per_contract"]
        assert rdir.split("/")[-2] in evj
        # recorded on the SAME candidate that holds the evaluation
        assert rep["recorded"]["ok"] is True, rep["recorded"]
        cand = F.run(_call(IMP.candidate, cid))
        assert cand["artifact_ref"] == "%s@%s" % (branch, sha)
        assert "+MIN_GROSS_EDGE_PP = 0.06" in cand["diff"]
        assert cand["test_results"]["passed"] is True
        assert cand["state"] == "APPROVAL_READY"
        assert cand["evaluation"]["verdict"] == IMP.V_PASS   # unchanged
        # a second artifact for the same candidate is refused
        again = F.run(_call(IMP.attach_artifact, cid, diff="x",
                            artifact_ref="other@0", base_commit=base,
                            test_results={}, report={}, now=NOW + 120))
        assert again["refusal"] == "THE_ARTIFACT_IS_ALREADY_RECORDED"
        # the checkout running this test is untouched; nothing was pushed
        assert (BACKEND / "sportsassets" / "agents" / "derek_policy.py"
                ).read_text() == before
    finally:
        subprocess.run(["git", "-C", str(REPO), "branch", "-D", branch],
                       capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "worktree", "prune"],
                       capture_output=True)

    # ── 5 · THE DIRECTIVE AND THE CANDIDATE, AS MANAGEMENT READS THEM ──
    mon = F.run(_call(D.monitor, now=NOW + 180))
    assert any(t["directive_id"] == did and t["to"] == D.IN_PROGRESS
               for t in mon["transitions"]), mon
    r = client.get("/api/command/agents/audrey/directives/%s" % did,
                   headers=F.desk_headers())
    assert r.status_code == 200
    assert r.json()["directive"]["status"] == D.IN_PROGRESS
    detail = F.run(_call(IMP.candidate_detail, cid))
    assert detail is not None
    blob = json.dumps(detail, default=str)
    for want in (cid, wid, "min_gross_edge_pp", branch, sha):
        assert want in blob, want
    dev = F.run(_q("SELECT kind FROM agent_task_events WHERE task_id=$1 "
                   " ORDER BY at, kind", dtask))
    assert [e["kind"] for e in dev].count("WORK_TAKEN_UP") == 1
    assert "IMPROVEMENT_OUTCOME" in [e["kind"] for e in dev]

    # ── IDEMPOTENT: the next pass takes nothing up again ───────────────
    out2 = F.run(_call(IMP.run_due, now=NOW + 3600))
    assert out2["directive_work"]["taken_up"] == []
    n = F.run(_q("SELECT count(*) AS n FROM agent_tasks WHERE task_id=$1",
                 wid))[0]["n"]
    assert n == 1
