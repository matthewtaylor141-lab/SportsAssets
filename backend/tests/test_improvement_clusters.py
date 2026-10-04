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
     effective instant). After it, reviews record WAITING_FOR_FRESH_EVIDENCE
     instead of a stale HOLD; the runner measures the RULE over its own
     table before vs after (never Karen's throttled challenge count):
     FIX_EFFECTIVE, recorded once per day; Audrey's triage completes.
  §3 AUDREY'S REPEATED FINDINGS cluster by kind (owner AUDREY); a single
     finding does not.
  §4 THE EFFECT STATISTICS (pure): UNAVAILABLE without a window,
     INSUFFICIENT_SAMPLE below the floor, the verdict only from the interval.
  §5 THE READ API: GET only, COMMAND auth, one READ ONLY transaction.
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
        # after the fix the reviews record WAITING, never a stale HOLD
        for p in pos:
            for i in range(13):
                await F.stale_hold_review(
                    conn, a, group_id=p["group_id"],
                    at=NOW + 600.0 + i * 1200.0,
                    recommendation="WAITING_FOR_FRESH_EVIDENCE")
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
        # the record is append-only
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute("UPDATE improvement_clusters SET title='x'")
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


def test_the_effect_statistics():
    u = IC.proportion_effect((0, 0), (0, 10))
    assert u["status"] == "UNAVAILABLE" and u["why"] == IC.R_NO_BASELINE
    u = IC.proportion_effect((5, 10), (0, 0))
    assert u["why"] == IC.R_NO_POST and u["verdict"] is None
    s = IC.proportion_effect((9, 10), (0, 10))
    assert s["status"] == "INSUFFICIENT_SAMPLE" and s["verdict"] is None
    e = IC.proportion_effect((60, 100), (5, 100))
    assert e["status"] == "MEASURED" and e["verdict"] == IC.S_EFFECTIVE
    assert e["ci95"][1] < 0
    n = IC.proportion_effect((10, 100), (40, 100))
    assert n["verdict"] == IC.S_NOT_EFFECTIVE and n["ci95"][0] >= 0
    i = IC.proportion_effect((20, 100), (18, 100))
    assert i["status"] == "MEASURED" and i["verdict"] is None
    r = IC.rate_effect((40, 7.0), (2, 7.0))
    assert r["verdict"] == IC.S_EFFECTIVE and r["basis"] == \
        "CLUSTER_MEMBERS_PER_DAY"
    assert IC.rate_effect((3, 7.0), (2, 7.0))["status"] == \
        "INSUFFICIENT_SAMPLE"
    assert IC.rate_effect((3, 0.0), (2, 7.0))["status"] == "UNAVAILABLE"
    lo, mid, hi = IC.windows(NOW, NOW + 3 * 86400)
    assert mid - lo == hi - mid == 3 * 86400
    lo, mid, hi = IC.windows(NOW, NOW + 30 * 86400)
    assert mid - lo == IC.BASELINE_MAX_S
    assert IC.MAIN_PAPER_ACCOUNT == AW.MAIN_PAPER_ACCOUNT


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
