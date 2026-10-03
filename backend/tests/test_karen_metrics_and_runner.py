"""KAREN'S METRICS AND HER SCHEDULED RUNNER.

METRICS: evidence grounding, valid defect discovery, challenge precision,
false-block rate, time-to-challenge and downstream improvement -- each with
its numerator and denominator; a metric with nothing to measure is null with
the reason, NEVER 0.

RUNNER: one pass heartbeats KAREN (agent_status, agent_runs, the service
heartbeat), runs the detectors (a raising detector is recorded by name and
the others still run), opens only grounded challenges (each cites records
that exist), is idempotent across passes, bounded, and reports FAILED only
when every detector failed. The loop never raises into its caller.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from sportsassets.agents import karen as K
from sportsassets.agents import karen_runner as KR
from sportsassets.agents import registry as R

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
T0 = 1_790_000_000.0
NAMES = ("evidence_grounding", "valid_defect_discovery",
         "challenge_precision", "false_block_rate", "time_to_challenge",
         "downstream_improvement")


def _c(cid, state="OPEN", **kw):
    return dict({"challenge_id": cid, "state": state,
                 "evidence_refs": [{"kind": "agent_decisions", "id": cid}],
                 "blocked": False, "false_block": None, "record_at": T0,
                 "challenged_at": T0 + 100}, **kw)


# ════════════════════════════════════════════════════════════════════
# METRICS (pure)
# ════════════════════════════════════════════════════════════════════

def test_with_nothing_to_measure_every_metric_is_null_never_zero():
    got = K.summarise_metrics([], set(), set())
    assert set(got["metrics"]) == set(NAMES)
    for name, m in got["metrics"].items():
        assert m["value"] is None and m["numerator"] is None, name
        assert m["measurable"] is False and m["why"], name
        assert m["definition"], name
        assert m["denominator"] in (0, None), name
    assert got["rule"] == "null means unmeasurable, never zero"


def test_open_challenges_measure_grounding_and_timing_but_not_outcomes():
    rows = [_c("a"), _c("b"), _c("c", evidence_refs=[
        {"kind": "agent_decisions", "id": "gone"}])]
    m = K.summarise_metrics(rows, {("agent_decisions", "a"),
                                   ("agent_decisions", "b")}, set())["metrics"]
    g = m["evidence_grounding"]
    assert (g["numerator"], g["denominator"]) == (2, 3)
    assert g["value"] == round(2 / 3, 6)
    t = m["time_to_challenge"]
    assert (t["numerator"], t["denominator"], t["value"]) == (300.0, 3, 100.0)
    assert t["median_s"] == 100.0 and t["unit"] == "seconds"
    # nothing has an outcome: discovery, precision and improvement are null
    for name in ("valid_defect_discovery", "challenge_precision",
                 "downstream_improvement", "false_block_rate"):
        assert m[name]["value"] is None and m[name]["why"], name
    assert m["valid_defect_discovery"]["why"] == \
        "NO_CHALLENGE_HAS_AN_OUTCOME_YET"


def test_outcomes_false_blocks_and_improvements_are_counted_as_defined():
    rows = [_c("u1", "UPHELD"), _c("u2", "UPHELD"), _c("r1", "REJECTED"),
            _c("w1", "WITHDRAWN"), _c("o1"),
            _c("b1", "REJECTED", blocked=True, false_block=True),
            _c("b2", "UPHELD", blocked=True, false_block=False),
            _c("b3", "UPHELD", blocked=True)]
    res = {("agent_decisions", r["challenge_id"]) for r in rows}
    m = K.summarise_metrics(rows, res, adopted={"u1"})["metrics"]
    d = m["valid_defect_discovery"]
    assert (d["value"], d["numerator"], d["denominator"]) == (4, 4, 8)
    p = m["challenge_precision"]
    # withdrawn counts AGAINST precision: 4 upheld / (4 + 2 rejected + 1 w)
    assert (p["numerator"], p["denominator"]) == (4, 7)
    assert p["value"] == round(4 / 7, 6)
    f = m["false_block_rate"]
    assert (f["numerator"], f["denominator"], f["value"]) == (1, 2, 0.5)
    assert f["blocking_challenges"] == 3 and f["blocking_unassessed"] == 1
    i = m["downstream_improvement"]
    assert (i["numerator"], i["denominator"], i["value"]) == (1, 4, 0.25)
    # a measured zero is a zero (not null): no upheld challenge improved
    m = K.summarise_metrics([_c("u", "UPHELD")], set(), set())["metrics"]
    assert m["downstream_improvement"]["value"] == 0.0
    assert m["evidence_grounding"]["value"] == 0.0


# ════════════════════════════════════════════════════════════════════
# THE RUNNER (database + fakes)
# ════════════════════════════════════════════════════════════════════

async def _tx():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    await R.ensure_identities(conn)
    return conn, tx


def _fake_detector(rows):
    async def det(conn, now, limit):
        return list(rows)[:limit]
    return det


async def _broken(conn, now, limit):
    raise RuntimeError("detector broke (test)")


@pg
@pytest.mark.asyncio
async def test_a_pass_heartbeats_isolates_failures_and_opens_grounded_challenges():
    conn, tx = await _tx()
    try:
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ('adr:k207-run','XAVIER','T',"
            " to_timestamp($1))", T0)
        cand = {"detector": "FAKE", "target_agent": "XAVIER",
                "target_kind": "agent_decisions", "target_id": "adr:k207-run",
                "severity": "LOW", "record_at": T0, "claim": "fake claim",
                "evidence_refs": [{"kind": "agent_decisions",
                                   "id": "adr:k207-run"}]}
        ungrounded = dict(cand, detector="FAKE_UNGROUNDED", evidence_refs=[
            {"kind": "agent_decisions", "id": "adr:not-there"}])
        dets = (("BROKEN", _broken), ("FAKE", _fake_detector([cand])),
                ("FAKE_UNGROUNDED", _fake_detector([ungrounded])))
        s = await KR.pass_once(conn, now=T0 + 600, detectors=dets)
        assert s["status"] == "CHALLENGES_OPENED", s
        assert len(s["opened"]) == 1 and s["authority"] == "NONE"
        assert s["detector_errors"] == {"BROKEN": "RuntimeError"}
        assert s["refused"]["FAKE_UNGROUNDED"] == K.R_UNGROUNDED
        st = await R.status_of(conn, R.KAREN)
        assert st["state"] == "DECISION_RECORDED"
        assert st["activity"] == "OPENED 1 CHALLENGE(S)"
        assert st["runs"] == 1 and st["errors"] == 1
        assert "BROKEN:RuntimeError" in st["last_error"]
        assert st["cadence"]["target_interval_s"] == KR.INTERVAL_S
        assert st["last_heartbeat_at"] >= T0 + 600
        run = await conn.fetchrow(
            "SELECT outcome, finished_at FROM agent_runs WHERE run_id=$1",
            s["run_id"])
        assert run["outcome"] == "CHALLENGES_OPENED" and run["finished_at"]
        hb = await conn.fetchrow("SELECT status, detail FROM "
                                 "service_heartbeats WHERE service=$1",
                                 KR.SERVICE)
        assert hb["status"] == "ok"
        c = (await K.challenge(conn, s["opened"][0]))["challenge"]
        assert c["time_to_challenge_s"] == 600.0
        # IDEMPOTENT: a second pass opens nothing new and says so
        s2 = await KR.pass_once(conn, now=T0 + 900, detectors=dets[1:])
        assert s2["opened"] == [] and s2["status"] == "NOTHING_TO_CHALLENGE"
        st = await R.status_of(conn, R.KAREN)
        assert st["state"] == "IDLE" and st["runs"] == 2
        # EVERY DETECTOR FAILED: FAILED, truthfully
        s3 = await KR.pass_once(conn, now=T0 + 1200,
                                detectors=(("BROKEN", _broken),))
        assert s3["status"] == "FAILED"
        assert (await R.status_of(conn, R.KAREN))["state"] == "FAILED"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_pass_is_bounded_per_detector_and_per_pass(monkeypatch):
    conn, tx = await _tx()
    try:
        refs = []
        for i in range(12):
            ref = "adr:k207-bound-%02d" % i
            refs.append(ref)
            await conn.execute(
                "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
                " decided_at) VALUES ($1,'DEREK','T',to_timestamp($2))",
                ref, T0)
        cands = [{"detector": "BOUND", "target_agent": "DEREK",
                  "target_kind": "agent_decisions", "target_id": r,
                  "severity": "LOW", "record_at": T0, "claim": "c",
                  "evidence_refs": [{"kind": "agent_decisions", "id": r}]}
                 for r in refs]

        async def many(conn, now, limit):
            return cands                      # ignores the limit on purpose
        s = await KR.pass_once(conn, now=T0 + 10, detectors=(("BOUND", many),))
        assert len(s["opened"]) == KR.MAX_NEW_PER_DETECTOR
        monkeypatch.setattr(KR, "MAX_OPEN_PER_DETECTOR", 3)
        s = await KR.pass_once(conn, now=T0 + 20, detectors=(("BOUND", many),))
        assert s["opened"] == []
        assert s["refused"]["BOUND"] == "OPEN_CHALLENGE_CAP_REACHED"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_real_detectors_challenge_real_records_with_their_ids():
    conn, tx = await _tx()
    try:
        now = T0 + 3 * 86400
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " verdict, decided_at) VALUES ('adr:k207-noev','DEREK','ENTRY',"
            " 'ENTER', to_timestamp($1))", now - 3600)
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at, evidence_refs) VALUES ('adr:k207-ev','DEREK','ENTRY',"
            " to_timestamp($1), '[{\"kind\":\"x\",\"id\":\"1\"}]')",
            now - 3600)
        s = await KR.pass_once(conn, now=now)
        assert s["detector_errors"] == {}, s
        rows = await K.challenges(conn, limit=100)
        mine = [r for r in rows if r["target_id"] == "adr:k207-noev"]
        assert len(mine) == 1
        c = mine[0]
        assert c["detector"] == "DECISION_WITHOUT_EVIDENCE"
        assert c["target_agent"] == "DEREK" and c["state"] == "OPEN"
        assert "adr:k207-noev" in c["claim"]
        assert not [r for r in rows if r["target_id"] == "adr:k207-ev"]
        # EVERY challenge the real detectors opened is grounded NOW
        met = (await K.metrics(conn))["metrics"]["evidence_grounding"]
        assert met["numerator"] == met["denominator"] >= 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_loop_detector_refutes_a_finding_resting_on_an_upheld_defect():
    from sportsassets.agents import collaboration_loop as CL
    conn, tx = await _tx()
    try:
        for ref, who in (("adr:k207-def", "DEREK"), ("adr:k207-h", "DEREK")):
            await conn.execute(
                "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
                " decided_at) VALUES ($1,$2,'T',to_timestamp($3))",
                ref, who, T0)
        cid = (await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id="adr:k207-def", detector="T", claim="defective",
            severity="HIGH", evidence_refs=[{"kind": "agent_decisions",
                                             "id": "adr:k207-def"}],
            record_at=T0, at=T0 + 1))["challenge_id"]
        await K.respond(conn, cid, agent="DEREK", stance="CONCEDE",
                        response="yes", at=T0 + 2)
        await K.resolve(conn, cid, resolver="AUDREY", outcome="UPHELD",
                        reason="confirmed", at=T0 + 3)
        f = await CL.open_finding(
            conn, proposer="DEREK", title="t", statement="s",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k207-def"}],
            evidence_window_end=T0, at=T0 + 10)
        await CL.record_hypothesis(
            conn, f["finding_id"], actor="DEREK", hypothesis="h",
            evidence_refs=[{"kind": "agent_decisions", "id": "adr:k207-h"}],
            at=T0 + 20)
        s = await KR.pass_once(conn, now=T0 + 30, detectors=(
            ("FINDING_RESTS_ON_UPHELD_DEFECT",
             KR.detect_finding_on_upheld_defect),))
        assert len(s["opened"]) == 1, s
        loop = await CL.finding(conn, f["finding_id"])
        st = loop["stages"][-1]
        assert (st["stage"], st["actor"], st["outcome"]) == (
            "PEER_CHALLENGE", "KAREN", "REFUTED")
        assert {"kind": "karen_challenges", "id": cid} in st["evidence_refs"]
        c = (await K.challenge(conn, s["opened"][0]))["challenge"]
        assert c["blocked"] is True and c["finding_id"] == f["finding_id"]
    finally:
        await tx.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_the_loop_never_raises_and_can_be_switched_off(monkeypatch):
    calls = []

    async def bad_pool():
        calls.append(1)
        raise ConnectionError("no database (test)")
    task = asyncio.create_task(KR.run(bad_pool, interval_s=0.01,
                                      first_delay_s=0))
    await asyncio.sleep(0.1)
    assert not task.done() and len(calls) >= 2      # failing, still looping
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monkeypatch.setenv("KAREN_RUNNER_ENABLED", "0")
    assert KR.enabled() is False
    calls.clear()
    await asyncio.wait_for(KR.run(bad_pool, first_delay_s=0), 1)
    assert calls == []
    monkeypatch.delenv("KAREN_RUNNER_ENABLED")
    assert KR.enabled() is True


def test_the_api_process_arms_the_runner_and_includes_the_router():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "sportsassets" /
           "api" / "app.py").read_text()
    assert "_KAREN.run(_cap_pool)" in src and "karen_task" in src
    assert "from .agents_karen import router" in src
