"""THE TRADING FLOOR'S STATES ARE DERIVED FROM REAL ROWS (GET /api/command/floor).

  §1 PURE. derive_state: NOT_DEPLOYED before anything; no heartbeat or an
     old one is STALE (with its age); a run in progress inside the window is
     WORKING_ON (Karen CHALLENGING, Audrey / Xavier REVIEWING); a recorded
     output inside ACTIVE_WINDOW_S takes its hint; agent_status WAITING_* /
     BLOCKED / FAILED is WAITING; otherwise IDLE. merge_edges collapses rows
     per (from, to, kind) with counts and evidence, and drops self-edges and
     unknown actors (a person is not a desk).
  §2 FIXTURE STATE. On a scratch database (rolled back): Derek mid-run,
     Karen just challenged Derek, Audrey waiting on evidence, Xavier's
     heartbeat stale, the Chief Allocator idle after its last shadow run,
     and Eddie / Scout NOT_DEPLOYED because migration 217 is absent -- each
     with its real timestamps, and the Karen -> Derek edge carrying the
     challenge id. The workspace detail lists the challenge as given (Karen)
     and received + queued (Derek).
"""
from __future__ import annotations

import json
import os
import time

import pytest

from sportsassets.api import command_floor as FL

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
NOW = 1_800_000_000.0


def _state(agent="DEREK", **kw):
    args = dict(now=NOW, deployed=True, deploy_why=None,
                heartbeat_at=NOW - 30, stale_s=900.0, status={},
                signals=[])
    args.update(kw)
    return FL.derive_state(agent, **args)


# ── §1 pure ──────────────────────────────────────────────────────────

def test_not_deployed_wins_over_everything():
    s = _state("EDDIE", deployed=False,
               deploy_why="MIGRATION_217_NOT_APPLIED",
               status={"state": "EVALUATING"})
    assert s["state"] == "NOT_DEPLOYED"
    assert "MIGRATION_217_NOT_APPLIED" in s["detail"]


def test_no_heartbeat_and_old_heartbeat_are_stale_with_their_age():
    s = _state(heartbeat_at=None)
    assert s["state"] == "STALE" and s["heartbeat_age_s"] is None
    s = _state(heartbeat_at=NOW - 2000, status={
        "state": "EVALUATING", "last_run_started_at": NOW - 10})
    assert s["state"] == "STALE" and s["heartbeat_age_s"] == 2000.0


def test_the_stale_bound_follows_the_recorded_cadence():
    assert FL.stale_after_s(None) == FL.STALE_FLOOR_S
    assert FL.stale_after_s({"target_interval_s": 120}) == FL.STALE_FLOOR_S
    assert FL.stale_after_s(json.dumps({"review_interval_s": 1200})) == 3600
    assert FL.stale_after_s({"note": "x", "bad_s": -4}) == FL.STALE_FLOOR_S


def test_a_run_in_progress_is_working_and_role_specific():
    run = {"state": "EVALUATING", "activity": "COLLECTION_CYCLE_STARTED",
           "last_run_started_at": NOW - 20,
           "last_run_finished_at": NOW - 200}
    s = _state("DEREK", status=run)
    assert s["state"] == "WORKING_ON"
    assert s["activity_basis"] == "RUN_IN_PROGRESS"
    assert s["detail"] == "COLLECTION_CYCLE_STARTED"
    assert _state("KAREN", status=run)["state"] == "CHALLENGING"
    assert _state("AUDREY", status=run)["state"] == "REVIEWING"
    assert _state("XAVIER", status=run)["state"] == "REVIEWING"
    # a finished run, or a run started long ago, is not in progress
    done = dict(run, last_run_finished_at=NOW - 5)
    assert _state("DEREK", status=done)["state"] == "IDLE"
    old = dict(run, last_run_started_at=NOW - 5000, last_run_finished_at=None)
    assert _state("DEREK", status=old, stale_s=900.0)["state"] == "IDLE"


def test_a_recent_real_output_takes_its_hint_an_old_one_does_not():
    sig = [{"at": NOW - 100, "hint": "REVIEWING", "label": "Reviewed g1",
            "ref": {"kind": "xavier_management_assessments", "id": "a1"}},
           {"at": NOW - 40, "hint": "REVIEWING", "label": "Reviewed g2",
            "ref": {"kind": "xavier_management_assessments", "id": "a2"}}]
    s = _state("XAVIER", signals=sig)
    assert s["state"] == "REVIEWING" and s["detail"] == "Reviewed g2"
    assert s["basis"] == [{"kind": "xavier_management_assessments",
                           "id": "a2"}]
    assert s["activity_basis"] == "RECENT_OUTPUT"
    old = [dict(sig[0], at=NOW - FL.ACTIVE_WINDOW_S - 1)]
    assert _state("XAVIER", signals=old)["state"] == "IDLE"
    future = [dict(sig[0], at=NOW + 50)]
    assert _state("XAVIER", signals=future)["state"] == "IDLE"


def test_waiting_statuses_are_waiting_and_say_on_what():
    for st in FL.WAITING_STATUSES:
        s = _state(status={"state": st, "activity": "NO_FRESH_PRICE"})
        assert s["state"] == "WAITING" and "NO_FRESH_PRICE" in s["detail"]
    s = _state(status={"state": "FAILED", "activity": "HOOK_RAISED:x",
                       "last_error": "TimeoutError"})
    assert "TimeoutError" in s["detail"]


def test_every_state_is_one_of_the_seven():
    assert set(FL.STATES) == {"WORKING_ON", "REVIEWING", "CHALLENGING",
                              "WAITING", "IDLE", "STALE", "NOT_DEPLOYED"}


def test_edges_merge_per_pair_and_drop_people_and_self_edges():
    raw = [{"from": "KAREN", "to": "DEREK", "kind": "CHALLENGE_RAISED",
            "at": NOW - 30, "evidence": {"kind": "karen_challenges",
                                         "id": "c1"}, "summary": "s1"},
           {"from": "KAREN", "to": "DEREK", "kind": "CHALLENGE_RAISED",
            "at": NOW - 10, "evidence": {"kind": "karen_challenges",
                                         "id": "c2"}, "summary": "s2"},
           {"from": None, "to": "DEREK", "kind": "CHALLENGE_RESOLVED",
            "at": NOW - 5},
           {"from": "AUDREY", "to": "AUDREY", "kind": "LOOP_X",
            "at": NOW - 5}]
    got = FL.merge_edges(raw)
    assert len(got) == 1
    e = got[0]
    assert e["count"] == 2 and e["at"] == NOW - 10
    assert e["first_at"] == NOW - 30 and e["summary"] == "s2"
    assert [x["id"] for x in e["evidence"]] == ["c1", "c2"]
    assert FL._seat_agent("owner") is None
    assert FL._seat_agent("audrey") == "AUDREY"


def test_the_seats_are_in_candidate_review_order_with_boundaries():
    assert [s["agent"] for s in FL.SEATS] == [
        "DEREK", "KAREN", "SCOUT", "EDDIE", "CHIEF_ALLOCATOR", "AUDREY",
        "XAVIER"]
    for s in FL.SEATS:
        assert s["may"] and s["may_not"], s["agent"]
        assert s["workspace"] == "/" + s["slug"]
    assert FL.SEAT_BY_AGENT["KAREN"]["authority_level"] == \
        "NONE_ZERO_AUTHORITY"


# ── §2 the fixture state ─────────────────────────────────────────────

D1 = {"kind": "agent_decisions", "id": "adr:floor-fixture-1"}


@pg
async def test_fixture_rows_drive_every_desk_state():
    import asyncpg

    from sportsassets.agents import karen as K
    from sportsassets.agents import registry as R

    # the scenario's clock sits two hours past the newest activity row any
    # other test left behind (wall time, or a future-dated fixture): those
    # rows fall outside the floor's 5-minute activity window and 1-hour edge
    # window, so only this scenario's rows (all placed relative to `now`)
    # drive the desks
    conn = await asyncpg.connect(DSN)
    newest = time.time()
    for table, col in (("paper_audrey_findings", "found_at"),
                       ("audrey_audit_reports", "computed_at"),
                       ("coverage_collapse_alerts", "detected_at"),
                       ("karen_challenges", "challenged_at"),
                       ("karen_challenges", "responded_at"),
                       ("karen_challenges", "resolved_at"),
                       ("agent_decisions", "decided_at")):
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table):
            v = await conn.fetchval(
                "SELECT extract(epoch FROM max(%s)) FROM %s" % (col, table))
            if v is not None:
                newest = max(newest, float(v))
    now = newest + 7200
    tx = conn.transaction()
    await tx.start()
    try:
        await R.ensure_identities(conn)
        # this scenario owns these desks' status rows: a status another test
        # left behind (e.g. a fixed-clock run that "finished" in the future)
        # would correctly read as no run in progress
        await conn.execute(
            "DELETE FROM agent_status WHERE agent_id = ANY($1::text[])",
            ["DEREK", "XAVIER", "AUDREY", "KAREN"])
        await conn.execute(
            "INSERT INTO agent_decisions (decision_ref, agent_id, kind, "
            " decided_at) VALUES ($1,'DEREK','TEST',to_timestamp($2))",
            D1["id"], now - 600)
        await R.heartbeat(conn, "DEREK", state="EVALUATING",
                          activity="COLLECTION_CYCLE_STARTED", now=now - 20,
                          run={"started_at": now - 20})
        await R.heartbeat(conn, "XAVIER", state="IDLE", now=now - 5000)
        await R.heartbeat(conn, "AUDREY", state="WAITING_FOR_EVIDENCE",
                          activity="NO_FINAL_REPORT_DAY", now=now - 15)
        await R.heartbeat(conn, "KAREN", state="IDLE", now=now - 30,
                          cadence={"target_interval_s": 120})
        got = await K.open_challenge(
            conn, target_agent="DEREK", target_kind="agent_decisions",
            target_id=D1["id"], detector="DECISION_WITHOUT_EVIDENCE",
            claim="the decision cites no evidence", severity="HIGH",
            evidence_refs=[D1], record_at=now - 600, at=now - 60)
        assert got["ok"], got
        cid = got["challenge_id"]
        for t in ("intel_allocations", "intel_snapshots", "intel_runs"):
            await conn.execute("DELETE FROM %s" % t)
        for comp in ("CYCLE", "ALLOCATOR"):
            await conn.execute(
                "INSERT INTO intel_runs (run_id, component, started_at, "
                " finished_at, status, summary, version) VALUES "
                " ('floor-run-1', $1, to_timestamp($2), to_timestamp($3), "
                " 'OK', $4::jsonb, 'TEST')", comp, now - 420, now - 400,
                json.dumps({"candidates": 3, "allocated_usd": 125.5}))

        floor = await FL.build_floor(conn, now=now)
        by = {a["agent"]: a for a in floor["agents"]}
        assert list(by) == [s["agent"] for s in FL.SEATS]

        d = by["DEREK"]
        assert d["state"] == "WORKING_ON"
        assert d["activity_basis"] == "RUN_IN_PROGRESS"
        assert d["state_detail"] == "COLLECTION_CYCLE_STARTED"
        assert abs(d["heartbeat"]["age_s"] - 20) < 2
        assert d["challenges"]["open_against"] == 1

        k = by["KAREN"]
        assert k["state"] == "CHALLENGING"
        assert k["focus"]["id"] == cid and k["focus"]["target"] == "DEREK"
        assert k["challenges"]["raised_open"] == 1
        mon = {m["label"]: m for m in k["monitor"]}
        assert mon["Open challenges"]["value"] == 1
        assert mon["Open challenges"]["source"] == "karen_challenges"

        assert by["AUDREY"]["state"] == "WAITING"
        assert "NO_FINAL_REPORT_DAY" in by["AUDREY"]["state_detail"]

        x = by["XAVIER"]
        assert x["state"] == "STALE" and x["heartbeat"]["age_s"] > 4000
        labels = [m["label"] for m in x["monitor"]]
        assert "Managed positions · PAPER" in labels
        # never summed: ACTUAL is venue by venue, each venue's connection
        # read from its own control row (C28 venue independence)
        assert "Managed positions · ACTUAL · POLYMARKET US" in labels
        assert "Managed positions · ACTUAL · KALSHI" in labels
        assert "Managed positions · ACTUAL" not in labels
        mon = {m["label"]: m for m in x["monitor"]}
        k = mon["Managed positions · ACTUAL · KALSHI"]
        if k["value"] is None:
            assert "NOT_CONNECTED" in (k["why"] or "") or k["why"]

        al = by["CHIEF_ALLOCATOR"]
        assert al["state"] == "IDLE", al["state_detail"]
        assert al["last_output"]["id"] == "floor-run-1"
        assert "$125.50" in al["last_output"]["summary"]
        assert al["heartbeat"]["source"].startswith("intel_runs")

        for pos in ("EDDIE", "SCOUT"):
            if not await conn.fetchval(
                    "SELECT to_regclass('eddie_execution_estimates')"):
                assert by[pos]["state"] == "NOT_DEPLOYED", pos
                assert by[pos]["deploy_why"] == "MIGRATION_217_NOT_APPLIED"
                assert by[pos]["monitor"] == []

        edge = next(e for e in floor["edges"]
                    if (e["from"], e["to"]) == ("KAREN", "DEREK"))
        assert edge["kind"] == "CHALLENGE_RAISED" and edge["count"] == 1
        assert edge["evidence"][0] == {
            "kind": "karen_challenges", "id": cid,
            "href": "/api/command/karen/challenges/%s" % cid}
        assert floor["counts"]["NOT_DEPLOYED"] >= 0
        assert sum(floor["counts"].values()) == 7
        assert floor["read_only"] is True

        kd = await FL.build_agent_detail(conn, "karen", now=now)
        assert [c["challenge_id"] for c in kd["challenges"]["given"]] == [cid]
        dd = await FL.build_agent_detail(conn, "derek", now=now)
        assert [c["challenge_id"] for c in
                dd["challenges"]["received"]] == [cid]
        assert any(q["id"] == cid and q["from"] == "KAREN"
                   for q in dd["queue"])
        assert any(t["from"] == "KAREN" for t in dd["timeline"])
        assert await FL.build_agent_detail(conn, "nobody", now=now) is None
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_feed_and_opportunities_are_recorded_rows():
    import asyncpg

    now = time.time()
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        for t in ("intel_allocations", "intel_snapshots", "intel_runs"):
            await conn.execute("DELETE FROM %s" % t)
        floor = await FL.build_floor(conn, now=now)
        assert floor["opportunities"] == []
        assert floor["sections"]["opportunities.intel_allocations"] == {
            "status": "EMPTY", "why": "NO_ALLOCATOR_RUN"}
        n = await conn.fetchval("SELECT count(*) FROM paper_decisions")
        assert len(floor["feed"]) == min(12, n)
        for row in floor["feed"]:
            assert row["book"] == "PAPER" and row["kind"] == "paper_decisions"
        await conn.execute(
            "INSERT INTO intel_runs (run_id, component, started_at, "
            " finished_at, status, summary, version) VALUES ('fr2', "
            " 'ALLOCATOR', to_timestamp($1), to_timestamp($1), 'OK', "
            " '{}'::jsonb, 'T')", now - 50)
        await conn.execute(
            "INSERT INTO intel_allocations (run_id, candidate_id, "
            " computed_at, rank, candidate_kind, us_market_slug, score, "
            " shadow_weight, shadow_usd, reasons) VALUES ('fr2', 'c1', "
            " to_timestamp($1), 1, 'NEW_DECISION', 'mlb-x', 0.04, 0.1, "
            " 100, '[]'::jsonb)", now - 50)
        floor = await FL.build_floor(conn, now=now)
        assert [o["id"] for o in floor["opportunities"]] == ["c1"]
        assert floor["opportunities"][0]["label"] == "SHADOW"
        assert floor["opportunities"][0]["shadow_usd"] == 100.0
    finally:
        await tx.rollback()
        await conn.close()
