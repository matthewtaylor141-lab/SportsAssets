"""PROOF 23 -- DISCOVERY, MANAGEMENT, CHAT-CREATED TASKS AND LEARNING, RUN AT
ONCE, PRESERVE ONE EXECUTION AUTHORITY.

At the same time, each on its own connection:
  * DISCOVERY -- the real `cycle()` (no servicing task alive, so it services
    in-cycle under the execution lock, then runs Derek's hook);
  * MANAGEMENT -- the servicing task's own pass (`_service_once`);
  * ENTRY -- `_funded_attempt`, the one real entry path, with Derek's gate
    saying ENTER;
  * CHAT -- the same task created twice from two connections;
  * LEARNING -- Audrey's audit and improvement hooks (stand-ins that read the
    database, create a task, and note whether the execution lock is held).

The venue transport is substituted (XH harness, demonstration (a): the hedge
acquisition is the one order the book should produce).

PROVEN: exactly one servicing pass runs (the other is refused BUSY, not
queued); one DISPATCH_CLAIMED event and one venue create in total; the entry
submission happens only while no servicing pass is in progress, under the
same lock; the learning hooks never hold the execution lock; the chat task
exists once with one CREATED event; a later pass sends nothing more.
ALL DATA SYNTHETIC.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from sportsassets import bettor_funded_execution as FX
from sportsassets.agents import registry as R
from sportsassets.workers import ext_pinnacle_loop as L

from tests import agents_core_harness as H
from tests import test_xavier_manages_positions_through_the_scheduled_path as XH

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pytest.fixture(autouse=True)
def _fresh_worker_state(monkeypatch):
    monkeypatch.setattr(L, "_SERVICING", L._servicing_state())
    monkeypatch.setattr(L, "_EXEC_LOCK", {"lock": None, "loop": None})


@pg
@pytest.mark.asyncio
async def test_discovery_management_chat_and_learning_at_once_keep_one_execution_authority(
        monkeypatch):
    c_cycle, c_task, c_entry, c_chat, c_learn = [
        await XH._connect() for _ in range(5)]
    try:
        await H.clean_agents(c_cycle)
        await XH.start(c_cycle, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=XH.PROFIT_LADDER,
                                        hedge_bid=0.55),
                         holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue)
        H.enter_gate(monkeypatch, verdict="ENTER")

        # ── WHO IS INSIDE A SERVICING PASS, AND FOR HOW LONG ──────────
        in_service = {"now": 0, "max": 0, "passes": []}
        real_service = L._funded_service

        async def _observed_service(conn, **kw):
            in_service["now"] += 1
            in_service["max"] = max(in_service["max"], in_service["now"])
            in_service["passes"].append(id(conn))
            try:
                await asyncio.sleep(0.2)     # every contender arrives now
                return await real_service(conn, **kw)
            finally:
                in_service["now"] -= 1
        monkeypatch.setattr(L, "_funded_service", _observed_service)

        # ── THE ENTRY SUBMISSION, OBSERVED (acquisitions pass through) ─
        real_submit = FX.submit_for_decision
        entry_seen: list = []

        async def _submit(conn, rec, **kw):
            if (rec or {}).get("ag23_entry"):
                entry_seen.append({"servicing_passes_running":
                                   in_service["now"],
                                   "lock_held": L._execution_lock().locked()})
                return {"ok": False, "refusal": "ENTRY_OBSERVED_NOT_SENT"}
            return await real_submit(conn, rec, **kw)
        monkeypatch.setattr(FX, "submit_for_decision", _submit)

        # ── LEARNING: reads, a task, and the lock's state ──────────────
        learning: list = []

        async def audit_run_due(conn, *, now):
            learning.append(("audit", L._execution_lock().locked()))
            await conn.fetch("SELECT * FROM bettor_xavier_decisions "
                             " WHERE account_id=$1", XH.ACCT)
            return {"ran": True, "recorded": 1}

        async def improvement_run_due(conn, *, now):
            learning.append(("improvement", L._execution_lock().locked()))
            got = await R.create_task(
                conn, assignee=R.AUDREY, created_by="AUDREY",
                kind="EVALUATE_CANDIDATE", title="replay", spec={},
                task_id="task-ag23-improve", now=now)
            return {"ran": True, "task": got.get("task_id")}
        H.install(monkeypatch, "audrey_audit", run_due=audit_run_due)
        H.install(monkeypatch, "improvement", run_due=improvement_run_due)

        async def _chat(conn):
            return await R.create_task(
                conn, assignee="xavier", created_by="OWNER_VIA_CHAT",
                kind="REVIEW_POSITION", title="look at the Red Sox position",
                spec={"intent_id": XH.HELD_ID}, directive_id="dir-ag23",
                task_id="task-ag23-chat", now=time.time())

        entry_rec = dict(ag23_entry=True,
                         edge=0.1, us_market_slug=XH.HELD,
                         event_key=XH.EVENT, order_intent=FX.LONG)
        got = await asyncio.gather(
            L.cycle(c_cycle),
            L._service_once(c_task, now=time.time(),
                            source=L.SOURCE_SERVICING_TASK),
            L._funded_attempt(c_entry, entry_rec, now=time.time()),
            _chat(c_chat), _chat(c_learn))
        cyc, task_pass, entry, chat1, chat2 = got

        # ONE SERVICING PASS RAN; THE OTHER WAS REFUSED, NOT QUEUED
        assert in_service["max"] == 1, in_service
        assert len(in_service["passes"]) == 1, in_service
        cycle_busy = (cyc["funded_servicing"] or {}).get("refusal") == \
            L.R_EXECUTION_AUTHORITY_BUSY
        task_busy = task_pass["ran"] is False
        assert cycle_busy != task_busy, (cyc["funded_servicing"], task_pass)
        # ONE CLAIM AND ONE ORDER IN TOTAL
        assert await c_cycle.fetchval(
            "SELECT count(*) FROM bettor_xavier_execution_events WHERE "
            " account_id=$1 AND event_kind='DISPATCH_CLAIMED'", XH.ACCT) == 1
        assert len(venue.creates_sent()) == 1, venue.sent
        # THE ENTRY WAITED FOR THE AUTHORITY AND SUBMITTED UNDER IT
        assert entry["refusal"] == "ENTRY_OBSERVED_NOT_SENT", entry
        assert entry_seen == [{"servicing_passes_running": 0,
                               "lock_held": True}], entry_seen
        # LEARNING RAN ONCE, NEVER HOLDING THE LOCK
        assert sorted(learning) == [("audit", False),
                                    ("improvement", False)], learning
        # THE CHAT TASK EXISTS ONCE
        assert sorted([chat1["created"], chat2["created"]]) == [False, True]
        assert await c_chat.fetchval(
            "SELECT count(*) FROM agent_task_events WHERE "
            " task_id='task-ag23-chat' AND kind='CREATED'") == 1
        assert await c_chat.fetchval(
            "SELECT count(*) FROM agent_tasks WHERE task_id LIKE "
            " 'task-ag23-%'") == 2
        # THE AGENTS' STATES ARE TRUE OF WHAT HAPPENED
        assert (await H.status(c_cycle, "XAVIER"))["state"] in (
            "DECISION_RECORDED", "RECOVERING")
        assert (await H.status(c_cycle, "AUDREY"))["state"] == \
            "DECISION_RECORDED"

        # ── A LATER PASS (THE NEXT MINUTE) SENDS NOTHING MORE ──────────
        again = await L._service_once(c_task, now=time.time() + 60,
                                      source=L.SOURCE_SERVICING_TASK)
        assert again["ran"] is True
        assert len(venue.creates_sent()) == 1, venue.sent
        assert await c_cycle.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            " AND leg_role='HEDGE'", XH.ACCT) == 1
    finally:
        await XH.clean(c_cycle)
        await H.clean_agents(c_cycle)
        for c in (c_cycle, c_task, c_entry, c_chat, c_learn):
            await c.close()


@pg
@pytest.mark.asyncio
async def test_two_servicing_passes_on_two_connections_claim_one_decision_once(
        monkeypatch):
    """The per-group layer beneath the lock: even if two processes' passes
    overlapped (each with its OWN in-process lock), the group advisory lock
    and the dispatch claim let at most one order out."""
    a, b = await XH._connect(), await XH._connect()
    try:
        await H.clean_agents(a)
        await XH.start(a, p=0.55)
        venue = XH.Venue(books=XH.books(held_bids=XH.PROFIT_LADDER,
                                        hedge_bid=0.55),
                         holdings={XH.HELD: (10.0, 5.0)})
        XH.substitute(monkeypatch, venue)
        real_service = L._funded_service

        async def _slow(conn, **kw):
            await asyncio.sleep(0.2)
            return await real_service(conn, **kw)
        monkeypatch.setattr(L, "_funded_service", _slow)

        async def _as_another_process(conn):
            # a SEPARATE execution lock, as a second process would have
            L._EXEC_LOCK.update(lock=None, loop=None)
            return await L._funded_service(conn, now=time.time(),
                                           review_interval_s=60.0,
                                           run_learning=False)
        ra, rb = await asyncio.gather(_as_another_process(a),
                                      _as_another_process(b))
        # EXACTLY ONE ORDER: one review held the group, the other was refused
        # by the group lock (or found the hedge already held) and sent nothing.
        assert len(venue.creates_sent()) == 1, venue.sent
        assert await a.fetchval(
            "SELECT count(*) FROM bettor_xavier_execution_events WHERE "
            " account_id=$1 AND event_kind='DISPATCH_CLAIMED'", XH.ACCT) == 1
        assert await a.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE account_id=$1 "
            " AND leg_role='HEDGE'", XH.ACCT) == 1
        refusals = [XH.step_of({"funded_servicing": r}).get("refusal")
                    for r in (ra, rb)]
        from sportsassets import bettor_xavier as XV
        assert sorted(refusals, key=str) == sorted(
            [None, XV.R_GROUP_REVIEW_IN_PROGRESS], key=str), refusals
    finally:
        await XH.clean(a)
        await H.clean_agents(a)
        await a.close()
        await b.close()
