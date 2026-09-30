"""PROOF 21 -- A FAILING AGENT HOOK IS RECORDED AS THAT AGENT'S FAILURE, AND
DETERMINISTIC MANAGEMENT, RECOVERY AND SETTLEMENT STILL RUN IN THE SAME PASS.

Stand-ins for the other streams' modules raise, hang or are absent; the
real scheduled orchestration (`cycle()` without the servicing task, which
services in-cycle AND runs the slow half; and the servicing task's own pass)
runs with production suppliers and the venue transport substituted (XH
harness). Each agent's status names the exception type; the pass's own
result is what it would have been without the agents. ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets.agents import runtime as AR
from sportsassets.workers import ext_pinnacle_loop as L

from tests import agents_core_harness as H
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _fresh_worker_state(monkeypatch):
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_EXEC_LOCK", {"lock": None, "loop": None})


def _every_hook_fails(monkeypatch, calls):
    async def after_cycle(conn, *, cycle, now):
        calls.append("derek")
        raise RuntimeError("derek blew up (test)")

    async def run_due(conn, *, now):
        calls.append("audrey")
        raise ValueError("audrey blew up (test)")

    async def after_review(conn, *, review, now):
        calls.append("ladder")
        raise KeyError("ladder blew up (test)")
    H.install(monkeypatch, "derek", after_cycle=after_cycle)
    H.install(monkeypatch, "audrey_audit", run_due=run_due)
    H.install(monkeypatch, "xavier_ladder", after_review=after_review)
    H.uninstall(monkeypatch, "improvement")          # not deployed
    H.enter_gate(monkeypatch, verdict="SKIP")


@pg
@pytest.mark.asyncio
async def test_failing_and_missing_hooks_leave_management_running_and_are_named(
        monkeypatch):
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.start(conn, p=0.30, price=0.60, approve_model=False)
        venue = XH.Venue(books=XH.books(held_bids=[(0.45, 4), (0.42, 3),
                                                   (0.10, 400)]),
                         holdings={XH.HELD: (10.0, 6.0)})
        XH.substitute(monkeypatch, venue)
        calls: list = []
        _every_hook_fails(monkeypatch, calls)
        out = await XH.run_cycle(conn)
        assert calls == ["ladder", "audrey", "derek"], calls
        # DETERMINISTIC MANAGEMENT RAN: the REDUCE was decided and sent
        svc = out["funded_servicing"]
        assert svc["ok"] is True, svc
        assert XH.step_of(out)["decision"]["action"] == "REDUCE"
        rec = (await XH.xavier_records(conn))[0]
        XH.assert_sent_the_persisted_plan(venue, rec)
        # ... and recovery, investigations, settlement re-reads and the
        # learning pass ran in the same pass
        assert svc["pair_cycle"]["xavier_claim_recovery"]["ok"] is True
        for k in ("investigations", "settlement_rechecks", "learning"):
            assert k in svc, k
        assert out["xavier_review"] is not None
        # EACH AGENT'S STATUS NAMES WHAT FAILED
        d = await H.status(conn, "DEREK")
        assert d["state"] == "FAILED" and "RuntimeError" in d["last_error"]
        assert d["activity"].endswith("derek.after_cycle")
        a = await H.status(conn, "AUDREY")
        assert a["state"] == "FAILED", a
        assert "ValueError" in a["last_error"]
        assert "ModuleNotFoundError" in a["last_error"]
        deps = AR.R._j(a["dependencies"])
        assert deps["sportsassets.agents.improvement.run_due"][
            "installed"] is False
        x = await H.status(conn, "XAVIER")
        assert x["state"] == "DECISION_RECORDED", x
        xdeps = AR.R._j(x["dependencies"])
        assert xdeps["xavier_ladder"]["error_type"] == "KeyError"
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_settlement_still_closes_the_position_while_every_hook_fails(
        monkeypatch):
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.start(conn, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=[(0.52, 400)]),
                         holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue,
                      settlements={XH.HELD: XH._settled(1.0)})
        calls: list = []
        _every_hook_fails(monkeypatch, calls)
        res = await L._service_once(conn, now=time.time(),
                                    source=L.SOURCE_SERVICING_TASK, slow=True)
        assert res["ran"] is True and res["funded_service"]["ok"] is True
        row = await conn.fetchrow(
            "SELECT closed_reason FROM bettor_funded_intents WHERE "
            " intent_id=$1", XH.HELD_ID)
        assert row["closed_reason"] == "SETTLED_BY_THE_VENUE"
        assert H.order_calls(venue.sent) == []
        a = await H.status(conn, "AUDREY")
        assert a["state"] == "FAILED" and "ValueError" in a["last_error"]
        # THE SETTLED POSITION IS NO LONGER OWNED INVENTORY. Xavier still
        # writes a record for what the settlement left owed (its
        # obligations), and says so -- with nothing held.
        res2 = await L._service_once(conn, now=time.time(),
                                     source=L.SOURCE_SERVICING_TASK,
                                     slow=False)
        assert res2["ran"] is True
        x = await H.status(conn, "XAVIER")
        assert x["state"] == "DECISION_RECORDED", x
        assert res2["agents"]["xavier"]["owned_open_positions"] == 0
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_empty_servicing_pass_is_idle_not_position_management(
        monkeypatch):
    """A bound account holding nothing: the scheduler ran and there was no
    inventory to manage. Xavier is IDLE with SCHEDULER_RAN_NO_OWNED_INVENTORY
    -- never DECISION_RECORDED or anything that reads as management."""
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.clean(conn)
        await XH.authorize(conn)
        venue = XH.Venue(books={}, holdings={})
        XH.substitute(monkeypatch, venue)
        res = await L._service_once(conn, now=time.time(),
                                    source=L.SOURCE_SERVICING_TASK,
                                    slow=False)
        assert res["ran"] is True and res["funded_service"]["ok"] is True
        x = await H.status(conn, "XAVIER")
        assert (x["state"], x["activity"]) == ("IDLE",
                                               AR.A_NO_OWNED_INVENTORY), x
        assert res["agents"]["xavier"]["owned_open_positions"] == 0
        assert res["agents"]["xavier"]["decisions_recorded"] == 0
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_hanging_slow_hook_is_bounded_and_never_holds_the_execution_lock(
        monkeypatch):
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        seen_locked: list = []

        async def run_due(conn_, *, now):
            seen_locked.append(L._execution_lock().locked())
            await asyncio.sleep(30)
        H.install(monkeypatch, "audrey_audit", run_due=run_due)
        H.install(monkeypatch, "improvement", run_due=run_due)
        monkeypatch.setattr(AR, "SLOW_HOOK_TIMEOUT_S", 0.05)

        async def _service(conn_, **kw):
            return None                       # funded lane not configured
        res = await L._service_once(conn, now=time.time(),
                                    source=L.SOURCE_SERVICING_TASK,
                                    slow=True, service=lambda: _service(conn))
        assert res["ran"] is True
        assert seen_locked == [False, False]
        a = await H.status(conn, "AUDREY")
        assert a["state"] == "FAILED" and "TimeoutError" in a["last_error"]
        x = await H.status(conn, "XAVIER")
        assert (x["state"], x["activity"]) == (
            "IDLE", AR.A_FUNDED_LANE_NOT_CONFIGURED)
    finally:
        await H.clean_agents(conn)
        await conn.close()


@pytest.mark.asyncio
async def test_a_hook_that_raises_through_every_layer_never_reaches_the_worker(
        monkeypatch):
    """No database at all: the agent runtime itself failing is contained by
    the worker's wrappers, and the cycle's own result is returned."""
    def _broken():
        raise ImportError("agents runtime missing (test)")
    monkeypatch.setattr(L, "_agents_runtime", _broken)

    class _Conn:
        async def execute(self, *a):
            return "OK"

        async def fetchval(self, *a):
            return None

    async def _stopped(conn):
        return False, "STOPPED_BY_TEST"

    async def _svc(conn, **kw):
        return {"ok": True, "pair_cycle": {"xavier": []}}
    monkeypatch.setattr(L, "_running", _stopped)
    monkeypatch.setattr(L, "_funded_service", _svc)

    async def _review(conn, *, now):
        return {"ok": True}
    monkeypatch.setattr(L, "_xavier_daily_review", _review)
    out = await L.cycle(_Conn())
    assert out["state"] == "STOPPED"
    assert out["funded_servicing"]["ok"] is True
    assert "ImportError" in out["agents"]["error"]
