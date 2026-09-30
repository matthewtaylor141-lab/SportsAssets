"""PROOF 12 -- SHUTTING THE ENTRY SIDE DOWN PRESERVES THE AUTHORIZED EXIT AND
RECOVERY PATH.

Entry-side shutdown takes every form here at once: the entry lane stopped
(`_running` False), Derek's entry policy refusing, raising or not installed,
and Derek's `after_cycle` raising. Through `_funded_attempt` (the one real
entry path) the Derek gate is BINDING -- nothing is read or sent unless it
says ENTER. Through `cycle()` and the servicing task's own pass, with
production suppliers and the venue transport substituted (XH harness), the
authorized exit is still dispatched and recovery still answers an unresolved
claim. ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_xavier as XV
from sportsassets.agents import runtime as AR
from sportsassets.workers import ext_pinnacle_loop as L

from tests import agents_core_harness as H
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

T0 = 1_790_300_000.0


@pytest.fixture(autouse=True)
def _fresh_worker_state(monkeypatch):
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_EXEC_LOCK", {"lock": None, "loop": None})


def _entry_path_recorders(monkeypatch):
    """The connector and the account read, recorded (the lane is bound)."""
    reads, sends = [], []

    async def _bound(conn, key):
        return {"account_id": "acct-ag12", "venue": "PMUS"}

    async def _read():
        reads.append(1)
        return {"ok": True}

    async def _submit(conn, rec, **kw):
        sends.append(rec)
        return {"ok": False, "refusal": "FUNDED_SUBMISSION_DISABLED"}
    monkeypatch.setattr(FA, "_state", _bound)
    monkeypatch.setattr(L, "venue_account_exposure", _read)
    monkeypatch.setattr(FX, "submit_for_decision", _submit)
    return reads, sends


# ════════════════════════════════════════════════════════════════════
# THE DEREK GATE IS BINDING ON THE ONE REAL ENTRY PATH
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_the_derek_gate_is_binding_on_the_funded_entry_path(monkeypatch):
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        reads, sends = _entry_path_recorders(monkeypatch)
        rec = {"edge": 0.1, "us_market_slug": "aec-x", "event_key": "e",
               "order_intent": FX.LONG}
        # 1 · THE POLICY SAYS SOMETHING OTHER THAN ENTER
        calls: list = []
        H.enter_gate(monkeypatch, verdict="SKIP", calls=calls)
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == AR.R_DEREK_GATE_REFUSED, got
        assert got["nothing_was_sent"] is True
        assert got["derek_gate"]["verdict"] == "SKIP"
        assert calls and calls[0]["rec"] is rec and calls[0]["now"] == T0
        assert reads == [] and sends == []
        assert not L._execution_lock().locked()
        # 2 · THE POLICY RAISES
        async def _raises(conn_, rec_, *, now):
            raise ValueError("policy broke (test)")
        H.install(monkeypatch, "derek_policy",
                  gate_for_funded_entry=_raises)
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == AR.R_DEREK_GATE_UNAVAILABLE, got
        assert got["derek_gate"]["error_type"] == "ValueError"
        st = await H.status(conn, "DEREK")
        assert st["state"] == "FAILED" and "ValueError" in st["last_error"]
        # 3 · THE POLICY HANGS PAST ITS BOUND
        async def _hangs(conn_, rec_, *, now):
            await asyncio.sleep(30)
        H.install(monkeypatch, "derek_policy", gate_for_funded_entry=_hangs)
        monkeypatch.setattr(AR, "GATE_TIMEOUT_S", 0.05)
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == AR.R_DEREK_GATE_UNAVAILABLE
        assert got["derek_gate"]["error_type"] == "TimeoutError"
        # 4 · THE POLICY IS NOT INSTALLED AT ALL
        H.uninstall(monkeypatch, "derek_policy")
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == AR.R_DEREK_GATE_UNAVAILABLE, got
        assert got["derek_gate"]["installed"] is False
        st = await H.status(conn, "DEREK")
        assert st["state"] == "WAITING_FOR_PROVIDER", st
        assert "ModuleNotFoundError" in st["last_error"]
        # 5 · THE AGENTS PACKAGE ITSELF UNIMPORTABLE: still refused, by name
        def _broken():
            raise ImportError("agents package missing (test)")
        monkeypatch.setattr(L, "_agents_runtime", _broken)
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == L.R_DEREK_GATE_UNAVAILABLE
        assert L.R_DEREK_GATE_UNAVAILABLE == AR.R_DEREK_GATE_UNAVAILABLE
        monkeypatch.undo()
        # NOTHING WAS EVER READ OR SENT
        assert reads == [] and sends == []

        # 6 · AND ENTER IS WHAT OPENS IT (the gate is not vacuous)
        reads, sends = _entry_path_recorders(monkeypatch)
        H.enter_gate(monkeypatch, verdict="ENTER")
        got = await L._funded_attempt(conn, rec, now=T0)
        assert got["refusal"] == "FUNDED_SUBMISSION_DISABLED"
        assert reads == [1] and len(sends) == 1
    finally:
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_with_the_real_connector_a_refusing_gate_writes_no_intent(
        monkeypatch):
    """The bound account through the real writers and the REAL connector:
    the gate's refusal comes first and no intent row is written; with ENTER
    the connector's own gates answer (both switches are off in this test)."""
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.clean(conn)
        await XH.authorize(conn)
        from tests import test_the_funded_book_is_durable as DUR
        pmus_sent: list = []
        from sportsassets import pmus
        monkeypatch.setattr(pmus, "_get_client", lambda: pmus_sent.append(1))
        assert FX.FUNDED_SUBMISSION_ENABLED is False
        H.enter_gate(monkeypatch, verdict="SKIP")
        got = await L._funded_attempt(conn, DUR._decision(), now=time.time())
        assert got["refusal"] == AR.R_DEREK_GATE_REFUSED, got
        H.enter_gate(monkeypatch, verdict="ENTER")
        got = await L._funded_attempt(conn, DUR._decision(), now=time.time())
        assert got["refusal"] != AR.R_DEREK_GATE_REFUSED, got
        assert got.get("derek_gate") is None
        assert await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1",
            XH.ACCT) == 0
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# WITH THE ENTRY SIDE SHUT, THE EXIT IS STILL SENT AND RECOVERY STILL RUNS
# ════════════════════════════════════════════════════════════════════

def _shut_the_entry_side(monkeypatch):
    H.enter_gate(monkeypatch, verdict="SHUT")

    async def after_cycle(conn, *, cycle, now):
        raise RuntimeError("derek is down (test)")
    H.install(monkeypatch, "derek", after_cycle=after_cycle)
    # (XH.substitute also stops the entry lane: `_running` is False.)


async def _reduce_scenario(conn, monkeypatch):
    """XH demonstration (c): held 10 at 0.60, forward 0.30; a REDUCE of 7 is
    the authorized, dispatched management action."""
    await H.clean_agents(conn)
    await XH.start(conn, p=0.30, price=0.60, approve_model=False)
    venue = XH.Venue(books=XH.books(held_bids=[(0.45, 4), (0.42, 3),
                                               (0.10, 400)]),
                     holdings={XH.HELD: (10.0, 6.0)})
    XH.substitute(monkeypatch, venue)
    _shut_the_entry_side(monkeypatch)
    return venue


@pg
@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["COLLECTION_CYCLE", "SERVICING_TASK"])
async def test_with_the_entry_side_shut_the_authorized_exit_is_still_sent(
        monkeypatch, path):
    conn = await XH._connect()
    try:
        venue = await _reduce_scenario(conn, monkeypatch)
        if path == "COLLECTION_CYCLE":
            out = await XH.run_cycle(conn)
            assert out["state"] == "STOPPED"
            svc = out["funded_servicing"]
            assert out["agents"]["hook"]["error_type"] == "RuntimeError"
        else:
            res = await L._service_once(conn, now=time.time(),
                                        source=L.SOURCE_SERVICING_TASK)
            svc = res["funded_service"]
        assert svc["ok"] is True, svc
        step = XH.step_of({"funded_servicing": svc})
        assert step["decision"]["action"] == "REDUCE", step
        rec = (await XH.xavier_records(conn))[0]
        plan, c = XH.assert_sent_the_persisted_plan(venue, rec)
        assert c["intent"] == "ORDER_INTENT_SELL_LONG"
        assert int(c["quantity"]) == 7
        # RECOVERY, INVESTIGATIONS AND SETTLEMENT RE-READS RAN IN THE PASS
        assert svc["pair_cycle"]["xavier_claim_recovery"]["ok"] is True
        assert "investigations" in svc and "settlement_rechecks" in svc
        # ONLY BUYS ARE ENTRIES: nothing opened
        assert all(x["intent"] == "ORDER_INTENT_SELL_LONG"
                   for x in venue.creates_sent())
        st = await H.status(conn, "XAVIER")
        assert st["state"] == "DECISION_RECORDED", st
        if path == "COLLECTION_CYCLE":
            d = await H.status(conn, "DEREK")
            assert d["state"] == "FAILED" and "RuntimeError" in d["last_error"]
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_with_the_entry_side_shut_an_unanswered_claim_is_still_recovered(
        monkeypatch):
    """A dispatch claim with nothing after it (the process died between the
    claim and the send). With the entry side shut, the servicing pass's
    recovery still answers it from the book: no intent names it, so it is
    NOT_SENT -- and nothing is sent in its place."""
    conn = await XH._connect()
    try:
        await H.clean_agents(conn)
        await XH.start(conn, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=[(0.52, 400)],
                                        hedge_bid=0.20),
                         holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue)
        _shut_the_entry_side(monkeypatch)
        rec = await XV.record_decision(
            conn, account_id=XH.ACCT, venue=XH.VENUE, intent_id=XH.HELD_ID,
            decided_at=time.time() - 60, responsibility_state=XV.HELD,
            execution_eligibility=XV.E_DISPATCHED, alternatives=[],
            reasoning={}, expected_economics={}, residual_exposure={},
            evidence={}, obligations=[], chosen_action="EXIT",
            chosen_plan_digest="plan-orphan-ag12", decision_id="dec:ag12",
            portfolio_group_id="grp:" + XH.HELD_ID)
        xid = rec["xavier_decision_id"]
        assert (await XV.claim_dispatch(conn, xavier_decision_id=xid,
                                        plan_digest="plan-orphan-ag12"))[
            "claimed"]
        out = await XH.run_cycle(conn)
        rc = out["funded_servicing"]["pair_cycle"]["xavier_claim_recovery"]
        assert xid in [r["xavier_decision_id"] for r in rc["recovered"]], rc
        assert (await XV.execution_state(conn, xavier_decision_id=xid))[
            "status"] == XV.X_NOT_SENT
        assert H.order_calls(venue.sent) == [], venue.sent
        d = await H.status(conn, "DEREK")
        assert d["state"] == "FAILED"
    finally:
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()


# ════════════════════════════════════════════════════════════════════
# A DISCOVERY OUTAGE DOES NOT BLOCK XAVIER
# ════════════════════════════════════════════════════════════════════

@pg
@pytest.mark.asyncio
async def test_a_discovery_outage_does_not_block_xavier(monkeypatch):
    """Discovery is down: the odds credential is absent and the cycle is
    stuck inside its (blocked) observation pass, and Derek's hook hangs. The
    servicing task -- its own pool connections, the real `_servicing_loop` --
    reviews the held position pass after pass meanwhile, and Xavier's status
    says so on every pass."""
    import asyncpg

    conn = await XH._connect()
    pool = await asyncpg.create_pool(DSN, min_size=1, max_size=3)
    try:
        await H.clean_agents(conn)
        await XH.start(conn, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=[(0.52, 400)],
                                        hedge_bid=0.20),
                         holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue)
        observing, release = asyncio.Event(), asyncio.Event()

        async def _running(conn_):
            return True, None

        async def _table_ready(conn_):
            return True

        async def _stuck_observation(conn_, observable, *, now):
            observing.set()
            await release.wait()
            return {"ok": True, "observations_written": 0}

        async def _hangs(conn_, *, cycle, now):
            await asyncio.sleep(30)
        monkeypatch.setattr(L, "_running", _running)
        monkeypatch.setattr(L, "_table_ready", _table_ready)
        monkeypatch.setattr(L.ext, "credential_present",
                            lambda: {"present": False,
                                     "refusal": "ODDS_PROVIDER_DOWN_TEST"})
        monkeypatch.setattr(L, "_pair_observation_pass", _stuck_observation)
        monkeypatch.setattr(L, "_LAST_OBSERVATION_PASS", [0.0])
        H.install(monkeypatch, "derek", after_cycle=_hangs)
        monkeypatch.setattr(AR, "HOOK_TIMEOUT_S", 0.2)
        slept: list = []

        async def _sleep(d):
            slept.append(d)
            if len(slept) == 1:
                await observing.wait()
            await asyncio.sleep(0)
        servicing = asyncio.create_task(L._servicing_loop(
            pool, interval_s=L.SERVICING_INTERVAL_S, sleep=_sleep,
            max_passes=3))
        await asyncio.sleep(0)
        collecting = asyncio.create_task(L.cycle(conn))
        await asyncio.wait_for(servicing, timeout=120)
        # THREE PASSES WHILE DISCOVERY WAS DOWN AND STUCK
        assert observing.is_set() and not collecting.done()
        assert L._SERVICING["passes"] == 3, L._SERVICING
        x = await H.status(conn, "XAVIER")
        assert x["state"] == "DECISION_RECORDED" and x["runs"] == 3, x
        assert len(await XH.xavier_records(conn)) == 3
        release.set()
        out = await asyncio.wait_for(collecting, timeout=30)
        assert out["state"] == "BLOCKED"
        d = await H.status(conn, "DEREK")
        assert d["state"] == "FAILED" and "TimeoutError" in d["last_error"]
    finally:
        await pool.close()
        await XH.clean(conn)
        await H.clean_agents(conn)
        await conn.close()
