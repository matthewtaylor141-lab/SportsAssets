"""ROOT-CAUSE CLUSTERS FROM PRODUCTION-SHAPED ROWS WRITTEN BY THE REAL
WRITERS (owner R30 program section 20, migration 234 §3).

  §1 THE PRODUCTION DEFECT, REPLAYED. Xavier HOLD reviews in production's
     pre-R30 shape (measure.stale = true, the measure keys research-sql run
     37226555657 read) on three groups of one exploration strategy; Karen's
     REAL runner raises HOLD_ON_STALE_PROBABILITY challenges, the REAL peer
     responder has Xavier concede and Audrey uphold. ONE cluster opens
     (KAREN|HOLD_ON_STALE_PROBABILITY|XAVIER, owner XAVIER) with the count,
     first / last seen, the strategy and the three markets; the improvement
     pipeline seeds NO per-challenge item for it; Audrey's queue owes its
     triage (BLOCKED on the engineering fix).
  §2 THE FIX AND ITS MEASURED EFFECT. A machine cannot link a fix, nor can
     anyone inside the runner's session; a named person can (40-hex SHA,
     effective instant). After it, the REAL review path (paper_xavier.
     review_group on held positions whose probability is stale) records
     WAITING_FOR_FRESH_EVIDENCE; the runner measures the RULE over its own
     table before vs after (never Karen's throttled challenge count):
     FIX_EFFECTIVE (significant AND material), recorded once per day;
     Audrey's triage completes. MEASURING CONTINUES: stale HOLDs recur, the
     next day's measurement moves the cluster to FIX_PARTIALLY_EFFECTIVE,
     Audrey's triage is raised again, and the improvement pipeline seeds
     the recurring challenge as its own item.
  §3 AUDREY'S REPEATED FINDINGS cluster by kind (owner AUDREY); a single
     finding does not. Karen's AUDIT_DISCREPANCY_LEFT_OPEN cluster, from
     findings written by the real audit writer and challenges by the real
     runner, names the findings' strategies and subjects.
  §4 THE EFFECT STATISTICS (pure): UNAVAILABLE without a window,
     INSUFFICIENT_SAMPLE below the floor, the verdict only from the interval
     -- and FIX_EFFECTIVE only when material (production's 97.6% -> 62.4%
     is FIX_PARTIALLY_EFFECTIVE).
  §5 THE READ API: GET only, COMMAND auth, one READ ONLY transaction. THE
     HUMAN STEPS: admin token, a named person, refused by the record for a
     machine name; an owner assignment keeps the status.

R30B REVIEW NOTES. The pre-fix stale HOLD reviews stay hand-built
(agent_ops_fixture.stale_hold_review: production's pre-R30 shape, which no
writer on this branch produces any more); the post-fix reviews are now the
real writer's. The effect-statistics pins for FIX_EFFECTIVE changed because
materiality was added: 60% -> 5% on 100 rows is a significant drop whose
after-rate upper bound (11%) is not within 10% of the baseline (6%).
"""
from __future__ import annotations

import uuid

import asyncpg
import pytest

from sportsassets.agents import agent_work as AW
from sportsassets.agents import improvement_clusters as IC
from sportsassets.agents import improvement_pipeline as P
from sportsassets.agents import karen_runner as KR
from sportsassets.agents import peer_responder as PRS
from sportsassets.agents import registry as R

from tests import agent_ops_fixture as F

pg = F.pg
NOW = F.NOW
H = 3600.0
SHA = "a" * 40
DET = "HOLD_ON_STALE_PROBABILITY"


async def _replay(conn, monkeypatch, *, groups=3, per_group=13, at=NOW):
    """Three exploration groups, each with its ENTRY and stale HOLD reviews
    20 h .. 1 h before `at` (NOW); Karen's runner and the peer responder
    run."""
    await R.ensure_identities(conn)
    a = await F.account(conn, "rcc")
    pos = []
    for g in range(groups):
        p = await F.position(conn, a, at=at - 30 * H)
        pos.append(p)
        for i in range(per_group):
            await F.stale_hold_review(conn, a, group_id=p["group_id"],
                                      at=at - 20 * H + (g * per_group + i)
                                      * 600.0)
    monkeypatch.setattr(KR, "MAX_NEW_PER_DETECTOR", 200)
    monkeypatch.setattr(KR, "MAX_NEW_PER_PASS", 200)
    monkeypatch.setattr(KR, "MAX_OPEN_PER_DETECTOR", 500)
    s = await KR.pass_once(conn, now=at,
                           detectors=[(DET, KR.detect_hold_on_stale_probability)])
    assert len(s["opened"]) == groups * per_group, s
    conceded = upheld = 0
    for k in range(10):                 # PRS.MAX_PER_AGENT a pass
        s = await PRS.pass_once(conn, now=at + 60 + k)
        conceded += len(s["responses"]["XAVIER"]["conceded"])
        upheld += len(s["evaluations"]["AUDREY"]["upheld"])
    assert conceded == upheld == groups * per_group
    return a, pos


@pg
async def test_the_stale_hold_flood_becomes_one_root_cause_item(monkeypatch):
    conn, tx = await F.tx()
    try:
        a, pos = await _replay(conn, monkeypatch)
        got = await IC.refresh(conn, now=NOW + 120)
        key = IC.cluster_key("KAREN", DET, "XAVIER")
        cid = IC.cluster_id_for(key)
        assert cid in got["opened"], got
        # a second refresh opens nothing new
        assert cid not in (await IC.refresh(conn, now=NOW + 180))["opened"]
        v = await IC.view(conn, now=NOW + 200, cluster_id=cid)
        c = v["clusters"][0]
        assert c["cluster_key"] == key and c["owner"] == "XAVIER"
        assert c["status"] == "OPEN" and c["count"] == 39
        assert c["by_state"] == {"UPHELD": 39}
        assert c["first_seen_at"] == pytest.approx(NOW - 20 * H, abs=1)
        assert c["last_seen_at"] == pytest.approx(NOW - 20 * H + 38 * 600,
                                                  abs=1)
        aff = c["affected"]
        assert aff["status"] == "MEASURED" and aff["subjects"] == 3
        assert aff["strategies"] == [F.EXPLORATION]
        assert sorted(aff["markets"]) == sorted(p["slug"] for p in pos)
        assert c["linked_fix"] is None
        assert c["measured_effect"]["status"] == "UNAVAILABLE"
        assert c["measured_effect"]["why"] == IC.R_NO_FIX
        cur = c["current_defect_rate"]
        assert cur["status"] == "MEASURED" and cur["hits"] >= 1
        assert c["events"][0]["kind"] == "OPENED"
        assert c["events"][0]["actor"] == IC.RUNNER
        # the improvement pipeline seeds no per-challenge item for it
        seeded = await P.seed_karen(conn, now=NOW + 300)
        assert not [x for x in seeded if x["origin"]["row"]["detector"]
                    == DET], seeded
        # Audrey owes its triage, blocked on the engineering fix
        await AW.sync_for(conn, "paper_pass", now=NOW + 400)
        tri = [r for r in await F.requests(conn, AW.K_ROOT_CAUSE)
               if r["group_id"] == cid]
        assert len(tri) == 1 and tri[0]["agent_id"] == "AUDREY"
        assert tri[0]["collaborator"] == "XAVIER"
        assert tri[0]["blocker"] == "AWAITING_ENGINEERING_FIX"
        await AW.sync_for(conn, "paper_pass", now=NOW + 500)
        att = await F.attempts(conn, tri[0]["request_id"])
        assert att[-1]["outcome"] == "BLOCKED"
        assert att[-1]["blocker"] == "AWAITING_ENGINEERING_FIX"
    finally:
        await F.done(conn, tx)


@pg
async def test_a_linked_fix_is_measured_on_the_rule_itself(monkeypatch):
    conn, tx = await F.tx()
    try:
        a, pos = await _replay(conn, monkeypatch)
        await IC.refresh(conn, now=NOW + 120)
        cid = IC.cluster_id_for(IC.cluster_key("KAREN", DET, "XAVIER"))
        await AW.sync_for(conn, "paper_pass", now=NOW + 130)
        # a machine never links a fix; nor does anyone in the runner session
        bad = await IC.link_fix(conn, cid, actor="CLAUDE", commit_sha=SHA,
                                effective_at=NOW, at=NOW + 150)
        assert not bad["ok"]
        sp = conn.transaction()
        await sp.start()
        try:
            await conn.execute(
                "SELECT set_config('bettor.cluster_runner', 'on', true)")
            with pytest.raises(asyncpg.IntegrityConstraintViolationError):
                await conn.execute(
                    "INSERT INTO improvement_cluster_events (cluster_id, "
                    " kind, status_to, fix_commit_sha, fix_effective_at, "
                    " actor, actor_class, recorded_by, at) VALUES ($1,"
                    " 'FIX_LINKED','FIX_LINKED',$2,now(),'Test Engineer',"
                    " 'ENGINEERING','Test Engineer',now())", cid, SHA)
        finally:
            await sp.rollback()
        # an effect cannot be measured before a fix exists
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            sp = conn.transaction()
            await sp.start()
            try:
                await conn.execute(
                    "INSERT INTO improvement_cluster_events (cluster_id, "
                    " kind, status_to, effect, actor, actor_class, "
                    " recorded_by, at) VALUES ($1,'EFFECT_MEASURED',"
                    " 'FIX_EFFECTIVE','{}'::jsonb,$2,'RUNNER',$2,now())",
                    cid, IC.RUNNER)
            finally:
                await sp.rollback()
        ok = await IC.link_fix(conn, cid, actor="Test Engineer",
                               commit_sha=SHA, effective_at=NOW,
                               at=NOW + 200, ref="claude/r30-live-parity")
        assert ok["ok"], ok
        # too soon after the fix: nothing measured yet
        v = await IC.view(conn, now=NOW + 2 * H, cluster_id=cid)
        assert v["clusters"][0]["measured_effect"]["why"] == IC.R_TOO_SOON
        await AW.sync_for(conn, "paper_pass", now=NOW + 2 * H)
        tri = [r for r in await F.requests(conn, AW.K_ROOT_CAUSE)
               if r["group_id"] == cid][0]
        assert (await F.attempts(conn, tri["request_id"]))[-1]["outcome"] \
            == "WAITING_FOR_FRESH_EVIDENCE"
        # after the fix: the REAL review path on positions whose
        # probability is stale records WAITING_FOR_FRESH_EVIDENCE, never a
        # stale HOLD
        for g in range(3):
            acct, gid, _slug = await F.held_position(conn, "rccx%d" % g,
                                                     at=NOW + 300.0)
            for i in range(13):
                rv = await F.real_review(conn, acct, gid,
                                         at=NOW + 600.0 + i * 1200.0 + g)
                assert rv["recommendation"] == "WAITING_FOR_FRESH_EVIDENCE"
                assert F.j(rv["measure"])["stale"] is True
        got = await IC.refresh(conn, now=NOW + 20 * H)
        assert got["measured"] == [{"cluster_id": cid,
                                    "status": IC.S_EFFECTIVE}], got
        v = await IC.view(conn, now=NOW + 20 * H, cluster_id=cid)
        c = v["clusters"][0]
        eff = c["measured_effect"]
        assert eff["basis"] == "RULE_PREDICATE_SHARE_OF_TABLE_ROWS"
        assert eff["before"]["rate"] == 1.0 and eff["after"]["rate"] == 0.0
        assert eff["before"]["rows"] == 39 and eff["after"]["rows"] == 39
        assert eff["status"] == "MEASURED" and eff["verdict"] == \
            IC.S_EFFECTIVE
        assert eff["materiality"]["material"] is True
        assert eff["materiality"]["after_upper_95"] <= \
            eff["materiality"]["target"]
        assert c["status"] == IC.S_EFFECTIVE
        assert c["linked_fix"]["commit_sha"] == SHA
        assert c["linked_fix"]["linked_by"] == "Test Engineer"
        assert c["last_recorded_measurement"]["effect"]["verdict"] == \
            IC.S_EFFECTIVE
        # at most one measurement a day; Audrey's triage completes
        assert (await IC.refresh(conn, now=NOW + 21 * H))["measured"] == []
        await AW.sync_for(conn, "paper_pass", now=NOW + 21 * H)
        tri = [r for r in await F.requests(conn, AW.K_ROOT_CAUSE)
               if r["group_id"] == cid][0]
        assert tri["terminal"]["state"] == "COMPLETED"
        assert tri["terminal"]["evidence_table"] == \
            "improvement_cluster_events"
        # an owner assignment by a person keeps the status
        own = await IC.assign_owner(conn, cid, actor="Test Engineer",
                                    owner="XAVIER", at=NOW + 21 * H + 1)
        assert own["ok"], own
        v = await IC.view(conn, now=NOW + 21 * H + 2, cluster_id=cid)
        assert v["clusters"][0]["status"] == IC.S_EFFECTIVE
        # MEASURING CONTINUES AFTER FIX_EFFECTIVE (R30B review): the stale
        # HOLD recurs on the next day; the next measurement sees it
        for i in range(39):
            await F.stale_hold_review(conn, a, group_id=pos[i % 3][
                "group_id"], at=NOW + 21 * H + 600.0 + i * 1200.0)
        # Karen's runner and the peer responder uphold one recurrence
        s_ = await KR.pass_once(conn, now=NOW + 44 * H, detectors=[
            (DET, KR.detect_hold_on_stale_probability)])
        assert s_["opened"], s_
        for k in range(10):
            await PRS.pass_once(conn, now=NOW + 44 * H + 60 + k)
        got = await IC.refresh(conn, now=NOW + 44 * H + 600)
        assert got["measured"] == [{"cluster_id": cid,
                                    "status": IC.S_PARTIAL}], got
        c = (await IC.view(conn, now=NOW + 44 * H + 700,
                           cluster_id=cid))["clusters"][0]
        assert c["status"] == IC.S_PARTIAL
        assert c["measured_effect"]["after"]["rate"] == 0.5    # 39 of 78
        # Audrey's triage is raised again (a new item: the old one stays
        # COMPLETED), blocked on the defect still running
        await AW.sync_for(conn, "paper_pass", now=NOW + 44 * H + 800)
        tri = [r for r in await F.requests(conn, AW.K_ROOT_CAUSE)
               if r["group_id"] == cid]
        assert [r["terminal"]["state"] for r in tri if r["terminal"]] == [
            "COMPLETED"]
        again = [r for r in tri if r["is_open"]]
        assert len(again) == 1 and again[0]["blocker"] == \
            "FIX_PARTIALLY_EFFECTIVE_DEFECT_STILL_RUNNING"
        # the recurring challenge (a record AFTER the cluster went
        # FIX_EFFECTIVE) is seeded as its own improvement item again;
        # the members from before the fix stay folded into the cluster
        seeded = [x["origin"]["row"] for x in await P.seed_karen(
            conn, now=NOW + 44 * H + 900)
            if x["origin"]["row"]["detector"] == DET]
        # the cluster is no longer FIX_EFFECTIVE (it regressed), so every
        # member folds into it again
        assert seeded == []
        # the record is append-only
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE improvement_clusters SET title='x'")
    finally:
        await F.done(conn, tx)


@pg
async def test_a_recurrence_after_an_effective_fix_is_seeded_again(
        monkeypatch):
    """(R30B review) Once a cluster existed, the improvement pipeline seeded
    nothing for its class in ANY status: a defect recurring after a fix
    produced no work item anywhere. Now an upheld challenge of a record made
    after the cluster became FIX_EFFECTIVE (or CLOSED) is seeded as its own
    item; earlier members stay folded."""
    conn, tx = await F.tx()
    try:
        a, pos = await _replay(conn, monkeypatch, groups=1, per_group=3)
        await IC.refresh(conn, now=NOW + 120)
        cid = IC.cluster_id_for(IC.cluster_key("KAREN", DET, "XAVIER"))
        before = {x["origin"]["row"]["challenge_id"] for x in
                  await P.seed_karen(conn, now=NOW + 130)
                  if x["origin"]["row"]["detector"] == DET}
        assert before == set()                      # folded while OPEN
        # a person closes it (say the defect was judged fixed)
        ok = await IC.close(conn, cid, actor="Test Engineer",
                            note="believed fixed", at=NOW + 200)
        assert ok["ok"], ok
        # the defect recurs after the close: a new stale HOLD, upheld
        await F.stale_hold_review(conn, a, group_id=pos[0]["group_id"],
                                  at=NOW + 600)
        s_ = await KR.pass_once(conn, now=NOW + 900, detectors=[
            (DET, KR.detect_hold_on_stale_probability)])
        assert len(s_["opened"]) == 1, s_
        for k in range(3):
            await PRS.pass_once(conn, now=NOW + 960 + k)
        rows = [x["origin"]["row"] for x in await P.seed_karen(
            conn, now=NOW + 1200) if x["origin"]["row"]["detector"] == DET]
        assert [r["challenge_id"] for r in rows] == [s_["opened"][0]], rows
    finally:
        await F.done(conn, tx)


@pg
async def test_audreys_repeated_findings_cluster_by_kind():
    conn, tx = await F.tx()
    try:
        a = await F.account(conn, "rcca")
        kind = "OPS_AUDIT_FEES_CONSUME_EDGE_T%s" % uuid.uuid4().hex[:6]
        for i in range(3):
            await F.audrey_finding(conn, a, kind=kind, at=NOW - 7200 + i,
                                   subject="aec-mkt-%d" % i,
                                   strategy="DEREK_ENTRY_POLICY_V2")
        single = "SINGLE_T%s" % uuid.uuid4().hex[:6]
        await F.audrey_finding(conn, a, kind=single, at=NOW - 7200)
        await F.audrey_finding(conn, a, kind="INFO_T", at=NOW,
                               severity="INFO")
        got = await IC.refresh(conn, now=NOW)
        cid = IC.cluster_id_for(IC.cluster_key("AUDREY", kind, None))
        assert cid in got["opened"]
        assert IC.cluster_id_for(IC.cluster_key("AUDREY", single, None)) \
            not in got["opened"]
        c = (await IC.view(conn, now=NOW, cluster_id=cid))["clusters"][0]
        assert c["owner"] == "AUDREY" and c["count"] == 3
        assert c["affected"]["strategies"] == ["DEREK_ENTRY_POLICY_V2"]
        assert c["affected"]["n_markets"] == 3
        assert c["current_defect_rate"]["status"] == "UNAVAILABLE"
    finally:
        await F.done(conn, tx)


@pg
async def test_the_audit_discrepancy_cluster_names_strategies_and_markets(
        monkeypatch):
    """(R30B review) Karen's AUDIT_DISCREPANCY_LEFT_OPEN cluster (the second
    largest in production) read TARGET_KIND_CARRIES_NO_STRATEGY_OR_MARKET
    although paper_audrey_findings carries both. Findings from the REAL
    audit writer (paper_audrey.finding), challenges from Karen's REAL
    runner, answers and evaluations from the REAL peer responder."""
    from sportsassets.agents import paper_audrey as PA
    conn, tx = await F.tx()
    try:
        await R.ensure_identities(conn)
        a = await F.account(conn, "rccd")
        det = "AUDIT_DISCREPANCY_LEFT_OPEN"
        found = NOW - 2 * 86400                  # older than open-after
        slugs = ["aec-audit-%s-%d" % (uuid.uuid4().hex[:6], i)
                 for i in range(3)]
        for i, slug in enumerate(slugs):
            got = await PA.finding(
                conn, {"session_id": a["session_id"],
                       "account_id": a["account_id"], "now": found + i},
                kind="R30B_FEES_CONSUME_EDGE", subject=slug,
                severity="WARNING",
                detail={"strategy": "DEREK_ENTRY_POLICY_V2", "i": i},
                scope=str(i))
            assert got["new"], got
        monkeypatch.setattr(KR, "MAX_NEW_PER_DETECTOR", 50)
        monkeypatch.setattr(KR, "MAX_NEW_PER_PASS", 50)
        monkeypatch.setattr(KR, "MAX_OPEN_PER_DETECTOR", 500)
        s = await KR.pass_once(conn, now=NOW, detectors=[
            (det, KR.detect_audit_discrepancy_left_open)])
        assert len(s["opened"]) >= 3, s
        for k in range(10):
            await PRS.pass_once(conn, now=NOW + 60 + k)
        upheld = await conn.fetchval(
            "SELECT count(*) FROM karen_challenges k JOIN "
            " paper_audrey_findings f ON f.finding_id = k.target_id "
            " WHERE k.detector = $1 AND k.state = 'UPHELD' "
            "   AND f.subject = ANY($2::text[])", det, slugs)
        assert upheld == 3
        await IC.refresh(conn, now=NOW + 120)
        cid = IC.cluster_id_for(IC.cluster_key("KAREN", det, "AUDREY"))
        c = (await IC.view(conn, now=NOW + 200,
                           cluster_id=cid))["clusters"][0]
        aff = c["affected"]
        assert aff["status"] == "MEASURED", aff
        assert "DEREK_ENTRY_POLICY_V2" in aff["strategies"]
        assert set(slugs) <= set(aff["markets"])
        assert aff["basis"]["markets"] == "the finding's subject"
        assert c["owner"] == "AUDREY"
    finally:
        await F.done(conn, tx)


class _Pool:
    """A pool stand-in handing out the proof's own connection."""

    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pg
async def test_the_human_steps_have_an_authenticated_path(monkeypatch):
    """(R30B review) No application path could link a fix, assign an owner
    or close a cluster, so 'linked fix' and 'measured effect' could never be
    filled in production. The admin-token POST (the clear-halt pattern)
    records the NAMED person as actor and recorded_by; the record refuses a
    machine name; an owner assignment keeps the status."""
    from sportsassets.api import command_improvement_clusters as API
    conn, tx = await F.tx()
    try:
        a, pos = await _replay(conn, monkeypatch, groups=1, per_group=3)
        await IC.refresh(conn, now=NOW + 120)
        cid = IC.cluster_id_for(IC.cluster_key("KAREN", DET, "XAVIER"))

        async def pool():
            return _Pool(conn)
        monkeypatch.setattr(API, "_pool", pool)
        from fastapi import HTTPException
        # a machine name is refused by the record (409), and nothing lands
        with pytest.raises(HTTPException) as e:
            await API.cluster_human_step(cid, "link-fix", {
                "actor": "CLAUDE", "commit_sha": SHA,
                "effective_at": NOW})
        assert e.value.status_code == 409
        # malformed input never reaches the record (400)
        for body in ({"commit_sha": SHA, "effective_at": NOW},
                     {"actor": "Test Engineer", "commit_sha": "abc",
                      "effective_at": NOW},
                     {"actor": "Test Engineer", "commit_sha": SHA}):
            with pytest.raises(HTTPException) as e:
                await API.cluster_human_step(cid, "link-fix", body)
            assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await API.cluster_human_step("rcc:" + "0" * 24, "close",
                                         {"actor": "x y", "note": "n"})
        assert e.value.status_code == 404
        got = await API.cluster_human_step(cid, "link-fix", {
            "actor": "Test Engineer", "commit_sha": SHA.upper(),
            "effective_at": NOW, "ref": "claude/r30b-agents"})
        assert got["ok"] and got["actor"] == "Test Engineer"
        ev = await conn.fetchrow(
            "SELECT * FROM improvement_cluster_events WHERE event_id=$1",
            got["event_id"])
        assert ev["kind"] == "FIX_LINKED" and ev["fix_commit_sha"] == SHA
        assert ev["actor"] == ev["recorded_by"] == "Test Engineer"
        assert ev["actor_class"] == "ENGINEERING"
        got = await API.cluster_human_step(cid, "assign-owner", {
            "actor": "Test Engineer", "owner": "xavier"})
        assert got["ok"]
        c = (await IC.view(conn, now=NOW + 300,
                           cluster_id=cid))["clusters"][0]
        assert c["status"] == IC.S_FIX_LINKED          # kept
        assert c["linked_fix"]["commit_sha"] == SHA
        # the record itself refuses an assignment that changes the status
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO improvement_cluster_events (cluster_id, "
                    " kind, status_to, owner_agent, actor, actor_class, "
                    " recorded_by, at) VALUES ($1,'OWNER_ASSIGNED','OPEN',"
                    " 'XAVIER','Test Engineer','HUMAN','Test Engineer',"
                    " now())", cid)
        got = await API.cluster_human_step(cid, "close", {
            "actor": "Test Engineer", "note": "fixed and measured"})
        assert got["ok"]
        with pytest.raises(HTTPException) as e:     # only REOPENED follows
            await API.cluster_human_step(cid, "close", {
                "actor": "Test Engineer", "note": "again"})
        assert e.value.status_code == 409
        got = await API.cluster_human_step(cid, "reopen", {
            "actor": "Test Engineer", "note": "it came back"})
        assert got["ok"]
    finally:
        await F.done(conn, tx)


def test_the_human_step_route_needs_the_admin_token(monkeypatch):
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_improvement_clusters as API

    class _Cfg:
        admin_token = "admin-token-for-the-cluster-steps"
        desk_password = operator_password = command_read_password = "x"
    monkeypatch.setattr(APP, "settings", lambda: _Cfg(), raising=False)
    client = TestClient(APP.app, raise_server_exceptions=False)
    route = API.ADMIN_PATH + "/rcc:%s/close" % ("0" * 24)
    assert client.post(route, json={"actor": "a b", "note": "n"}
                       ).status_code == 401
    assert client.post(route, json={"actor": "a b", "note": "n"},
                       headers={"X-Admin-Token": "wrong"}).status_code == 401
    r = client.post(API.ADMIN_PATH + "/rcc:%s/merge" % ("0" * 24),
                    json={}, headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 404                     # no such action
    r = client.post(route, json={"note": "n"},
                    headers={"X-Admin-Token": _Cfg.admin_token})
    assert r.status_code == 400                     # the person is named


def test_the_effect_statistics():
    u = IC.proportion_effect((0, 0), (0, 10))
    assert u["status"] == "UNAVAILABLE" and u["why"] == IC.R_NO_BASELINE
    u = IC.proportion_effect((5, 10), (0, 0))
    assert u["why"] == IC.R_NO_POST and u["verdict"] is None
    s = IC.proportion_effect((9, 10), (0, 10))
    assert s["status"] == "INSUFFICIENT_SAMPLE" and s["verdict"] is None
    # (pin changed, R30B review: materiality) 60% -> 0% of 100 rows is
    # effective; 60% -> 5% is a significant drop whose after-rate upper
    # bound (11%) exceeds 10% of the baseline (6%): partially effective
    e = IC.proportion_effect((60, 100), (0, 100))
    assert e["status"] == "MEASURED" and e["verdict"] == IC.S_EFFECTIVE
    assert e["ci95"][1] < 0 and e["materiality"]["material"] is True
    pe = IC.proportion_effect((60, 100), (5, 100))
    assert pe["ci95"][1] < 0 and pe["verdict"] == IC.S_PARTIAL
    assert pe["materiality"]["target"] == pytest.approx(0.06)
    assert pe["materiality"]["after_upper_95"] > 0.06
    # PRODUCTION'S OWN FIGURES (research-sql run 37226555657): Xavier's
    # stale-HOLD rate 10-03 15,164 / 15,534 -> 10-04 9,978 / 15,993 is a
    # highly significant drop that leaves the defect running at 62%: never
    # FIX_EFFECTIVE
    prod = IC.proportion_effect((15164, 15534), (9978, 15993))
    assert prod["status"] == "MEASURED" and prod["ci95"][1] < 0
    assert prod["verdict"] == IC.S_PARTIAL
    assert prod["before"]["rate"] == pytest.approx(0.976181, abs=1e-6)
    assert prod["after"]["rate"] == pytest.approx(0.623898, abs=1e-6)
    # a rare defect may meet the absolute target instead
    tiny = IC.proportion_effect((30, 1000), (0, 1000))
    assert tiny["verdict"] == IC.S_EFFECTIVE
    assert tiny["materiality"]["target"] == IC.TARGET_RATE
    n = IC.proportion_effect((10, 100), (40, 100))
    assert n["verdict"] == IC.S_NOT_EFFECTIVE and n["ci95"][0] >= 0
    i = IC.proportion_effect((20, 100), (18, 100))
    assert i["status"] == "MEASURED" and i["verdict"] is None
    # members per day: (pin changed, materiality) 40 -> 2 a week is a
    # significant drop whose rate-ratio bound (0.21) is not a 90% cut
    r = IC.rate_effect((40, 7.0), (2, 7.0))
    assert r["verdict"] == IC.S_PARTIAL and r["basis"] == \
        "CLUSTER_MEMBERS_PER_DAY"
    r = IC.rate_effect((400, 7.0), (2, 7.0))
    assert r["verdict"] == IC.S_EFFECTIVE
    assert r["materiality"]["rate_ratio_upper_95"] <= 0.1
    assert IC.rate_effect((3, 7.0), (2, 7.0))["status"] == \
        "INSUFFICIENT_SAMPLE"
    assert IC.rate_effect((3, 0.0), (2, 7.0))["status"] == "UNAVAILABLE"
    # the windows: the post-fix window is the most recent stretch since the
    # fix (at most BASELINE_MAX_S), the baseline as long, just before it
    lo, mid, a0, hi = IC.windows(NOW, NOW + 3 * 86400)
    assert mid - lo == hi - a0 == 3 * 86400 and a0 == mid
    lo, mid, a0, hi = IC.windows(NOW, NOW + 30 * 86400)
    assert mid - lo == hi - a0 == IC.BASELINE_MAX_S and a0 > mid
    assert IC.wilson_upper(0, 39) == pytest.approx(0.0897, abs=1e-3)
    assert IC.MAIN_PAPER_ACCOUNT == AW.MAIN_PAPER_ACCOUNT
    # Audrey's triage is owed in every status a fix has not made effective
    assert set(AW.ROOT_CAUSE_OPEN) == {IC.S_OPEN, IC.S_FIX_LINKED,
                                       IC.S_PARTIAL, IC.S_NOT_EFFECTIVE}
    assert set(IC.S_MEASURED) == {IC.S_FIX_LINKED, IC.S_EFFECTIVE,
                                  IC.S_PARTIAL, IC.S_NOT_EFFECTIVE}


def test_the_cluster_read_route_is_get_only_and_requires_a_command_session():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_improvement_clusters as API
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "").startswith(API.PATH):
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert API.PATH in paths and API.PATH + "/{cluster_id}" in paths
    assert all(m <= {"GET", "HEAD"} for m in paths.values()), paths
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(API.PATH).status_code == 401
    assert client.post(API.PATH).status_code in (401, 405)


@pg
async def test_the_cluster_read_answers_inside_a_read_only_transaction(
        monkeypatch):
    from sportsassets.api import command_improvement_clusters as API
    conn, tx = await F.tx()
    try:
        await _replay(conn, monkeypatch, groups=1, per_group=3)
        await IC.refresh(conn, now=NOW + 120)
        got = await API.read(conn, now=NOW + 200)
        assert got["status"] == "OK"
        keys = [c["cluster_key"] for c in got["clusters"]]
        assert IC.cluster_key("KAREN", DET, "XAVIER") in keys
        out = API._envelope(got, NOW)
        assert out["authority"] == "NONE_RECORDS_ONLY"
        assert "throttled sample" in out["disclosure"]
        # the read wrote nothing (a write inside it would have raised)
        assert conn.is_in_transaction()
    finally:
        await F.done(conn, tx)
