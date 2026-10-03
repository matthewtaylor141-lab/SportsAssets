"""THE AUTONOMOUS IMPROVEMENT DRIVER: ordinary deficits -> Audrey's evidence
-> the proposer's hypothesis -> a DIFFERENT agent's peer challenge -> a
PAPER_ONLY experiment registration. NEVER further: no candidate, no
evaluation, no release eligibility, nothing in production changes; the
collaboration loop's database guards stay in force.

ALL DATA IS SYNTHETIC TEST DATA, inside a transaction that is rolled back.
"""
from __future__ import annotations

import inspect
import json

import asyncpg
import pytest

from sportsassets.agents import collaboration_loop as CL
from sportsassets.agents import coverage_integrity as C
from sportsassets.agents import improvement_driver as D
from tests import paper_harness as H
from tests import paper_ops_seed as SEED
from tests import test_coverage_integrity_funnel as COVT

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
NOW = COVT.NOW


def test_the_driver_cannot_reach_past_experiment_registration():
    src = inspect.getsource(D)
    for forbidden in ("record_candidate", "record_evaluation",
                      "mark_release_eligible", "paper_control",
                      "agent_policy_versions", "submit_order"):
        assert forbidden not in src, forbidden
    assert D.MAX_STAGE == CL.BOUNDED_EXPERIMENT
    for kind, (p, c) in D.ROLES.items():
        assert p != c and {p, c} <= set(CL.AGENTS), kind
    assert CL.check_stopping_rule(D.STOPPING_RULE) is None


async def _production_snapshot(conn) -> dict:
    out = {}
    for t in ("paper_control", "paper_improvement_proposals",
              "paper_policy_parameter_heads", "execmirror_control",
              "kalshi_smalllive_control"):
        if await conn.fetchval("SELECT to_regclass($1)", t):
            out[t] = await conn.fetchval(
                "SELECT md5(coalesce(string_agg(t::text, '|' ORDER BY "
                " t::text), '')) FROM %s t" % t)
    return out


@pg
@pytest.mark.asyncio
async def test_a_coverage_collapse_becomes_a_registered_paper_experiment():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        before = await _production_snapshot(conn)
        await COVT._seed(conn)
        acct = await H.new_account(conn, "drv", now=NOW - 5 * 86400)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        cov = await C.run(conn, now=NOW, ctx=ctx, days=5)
        assert any(a["league"] == COVT.NCAAF for a in cov["alerts"])
        res = await D.run(conn, ctx=ctx, now=NOW + 60)
        assert res["ran"], res
        cd = [d for d in res["deficits"] if d["kind"] == "COVERAGE_COLLAPSE"
              and COVT.NCAAF in d["subject"]]
        assert len(cd) == 1, res
        d = cd[0]
        assert (d["proposer"], d["challenger"]) == ("DEREK", "AUDREY")
        assert d["stage"] == CL.BOUNDED_EXPERIMENT, d
        assert d["challenge_outcome"] == "SUSTAINED"
        got = await CL.finding(conn, d["agent_finding_id"])
        stages = [(s["stage"], s["actor"]) for s in got["stages"]]
        assert stages == [(CL.EVIDENCE, "DEREK"), (CL.HYPOTHESIS, "DEREK"),
                          (CL.PEER_CHALLENGE, "AUDREY"),
                          (CL.BOUNDED_EXPERIMENT, "DEREK")]
        exp = got["stages"][-1]
        assert exp["scope"] == "PAPER_ONLY"
        assert exp["metric"]["name"].startswith("coverage_ratio:%s"
                                                % COVT.NCAAF)
        assert exp["stopping_rule"] == D.STOPPING_RULE
        assert {s["production_effect"] for s in got["stages"]} == {"NONE"}
        # the evidence IS Audrey's coverage finding
        ref = got["finding"]["evidence_refs"][0]
        assert ref["kind"] == "paper_audrey_findings"
        assert await conn.fetchval(
            "SELECT kind FROM paper_audrey_findings WHERE finding_id=$1",
            ref["id"]) == "COVERAGE_COLLAPSE"
        row = await conn.fetchrow(
            "SELECT * FROM improvement_deficits WHERE agent_finding_id=$1",
            d["agent_finding_id"])
        assert row["stage_reached"] == CL.BOUNDED_EXPERIMENT
        assert row["production_effect"] == "NONE"
        # A REPLAY ADDS NOTHING: no second finding, no stage beyond 4
        again = await D.run(conn, ctx=ctx, now=NOW + 120)
        assert again["ran"]
        got2 = await CL.finding(conn, d["agent_finding_id"])
        assert len(got2["stages"]) == 4
        assert await conn.fetchval(
            "SELECT count(*) FROM agent_finding_stages WHERE stage IN "
            " ('CANDIDATE_IMPROVEMENT','INDEPENDENT_EVALUATION',"
            "  'RELEASE_ELIGIBILITY')") == 0
        # NOTHING IN PRODUCTION CHANGED
        assert await _production_snapshot(conn) == before
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_stale_reviews_are_proposed_by_xavier_and_challenged_by_audrey():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        t0 = H.T0 + 9_000_000.0
        acct = await H.new_account(conn, "drs", now=t0)
        await SEED.seed(conn, acct, t0=t0)
        now = t0 + 4 * 3600
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": now}
        found = await D.stale_review_deficits(
            conn, now=now, account_id=acct["account_id"])
        assert found and found[0]["evidence"]["stale"] >= 1
        res = await D.drive_one(conn, found[0], ctx=ctx, now=now)
        assert (res["proposer"], res["challenger"]) == ("XAVIER", "AUDREY")
        assert res["stage"] == CL.BOUNDED_EXPERIMENT, res
        got = await CL.finding(conn, res["agent_finding_id"])
        assert got["stages"][2]["actor"] == "AUDREY"
        assert got["stages"][2]["outcome"] == "SUSTAINED"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_refuted_deficit_is_closed_by_the_challenger():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        acct = await H.new_account(conn, "drr", now=NOW - 86400)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        # an alert whose snapshot does not exist: the challenger re-reads
        # the record and cannot confirm it
        deficit = {
            "kind": "COVERAGE_COLLAPSE",
            "subject": "test_league:ABSENT_DOWNSTREAM:mapped",
            "evidence": {"alert_id": "covalert:none", "tz": "UTC",
                         "day": "2026-09-01", "league": "test_league",
                         "alert_kind": "ABSENT_DOWNSTREAM",
                         "stage_from": "venue_discovered",
                         "stage_to": "mapped", "ratio": 0.0,
                         "baseline": None},
            "title": "Coverage collapse: TEST at mapped",
            "statement": "synthetic deficit with no backing snapshot",
            "hypothesis": "synthetic hypothesis",
            "metric": {"name": "coverage_ratio:test", "direction": "INCREASE",
                       "threshold": 0.5}}
        res = await D.drive_one(conn, deficit, ctx=ctx, now=NOW)
        assert res["challenge_outcome"] == "REFUTED"
        assert res["stage"] == CL.CLOSED
        got = await CL.finding(conn, res["agent_finding_id"])
        assert [s["stage"] for s in got["stages"]] == [
            CL.EVIDENCE, CL.HYPOTHESIS, CL.PEER_CHALLENGE, CL.CLOSED]
        assert got["stages"][-1]["actor"] == "AUDREY"
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_guards_stay_in_the_database():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        async def refused(sql, *args):
            sp = conn.transaction()
            await sp.start()
            try:
                with pytest.raises((asyncpg.CheckViolationError,
                                    asyncpg.RaiseError)):
                    await conn.execute(sql, *args)
            finally:
                await sp.rollback()
        base = ("INSERT INTO improvement_deficits (deficit_id, kind, subject,"
                " observed_at, evidence, proposer, challenger, stage_reached,"
                " updated_at) VALUES ($1,'STALE_REVIEWS','s',now(),$2::jsonb,"
                " $3,$4,$5,now())")
        # the challenger is never the proposer
        await refused(base, "d1", "{}", "XAVIER", "XAVIER", "EVIDENCE")
        # the driver's record can never reach a candidate or beyond
        await refused(base, "d2", "{}", "XAVIER", "AUDREY",
                      "CANDIDATE_IMPROVEMENT")
        await refused(base, "d3", "{}", "XAVIER", "AUDREY",
                      "RELEASE_ELIGIBILITY")
        # and carries no authority key
        await refused(base, "d4", json.dumps({"capital_usd": 5}), "XAVIER",
                      "AUDREY", "EVIDENCE")
        # the loop's own guard: an agent cannot challenge its own finding
        acct = await H.new_account(conn, "drg", now=NOW - 86400)
        ctx = {"session_id": acct["session_id"],
               "account_id": acct["account_id"], "now": NOW}
        from sportsassets.agents import paper_audrey as PA
        f = await PA.finding(conn, ctx, kind="IMPROVEMENT_DEFICIT_TEST",
                             subject="g", detail={}, severity="WARNING")
        refs = [{"kind": "paper_audrey_findings", "id": f["finding_id"]}]
        o = await CL.open_finding(conn, proposer="XAVIER", title="t",
                                  statement="s", evidence_refs=refs,
                                  evidence_window_end=NOW, at=NOW)
        await CL.record_hypothesis(conn, o["finding_id"], actor="XAVIER",
                                   hypothesis="h", evidence_refs=refs,
                                   at=NOW)
        bad = await CL.record_challenge(conn, o["finding_id"],
                                        actor="XAVIER", challenge="c",
                                        outcome="SUSTAINED",
                                        evidence_refs=refs, at=NOW)
        assert bad["refusal"] == CL.R_SELF_CHALLENGE
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_without_a_session_the_driver_does_nothing():
    conn = await asyncpg.connect(H.DSN)
    try:
        got = await D.run(conn, ctx=None, now=NOW)
        assert got["ran"] is False and got["why"] == D.R_NO_SESSION
        assert (await D.step(conn, {"account_id": "paper_test_x",
                                    "now": NOW}))["why"] == \
            "NOT_THE_MAIN_PAPER_ACCOUNT"
    finally:
        await conn.close()
