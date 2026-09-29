"""XAVIER'S RECORD: one per position per review, written before dispatch and
never rewritten; what execution then did is an append-only, idempotent
history of events bound to the decision and its plan (migration 148)."""
from __future__ import annotations

import asyncio
import contextlib
import os
import time

import asyncpg
import pytest

from sportsassets import bettor_xavier as X

DSN = os.environ.get("RN1X_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="needs a migrated database")
ACCT = "acct-xavier-record"


@contextlib.asynccontextmanager
async def _conn():
    c = await asyncpg.connect(DSN)
    try:
        yield c
    finally:
        await c.close()


async def _purge(c):
    if not await X.has_schema(c):
        pytest.skip("migration 148 is not in this database")
    async with c.transaction():
        await c.execute("SET LOCAL session_replication_role = replica")
        await c.execute("DELETE FROM bettor_xavier_execution_events "
                        " WHERE account_id=$1", ACCT)
        await c.execute("DELETE FROM bettor_xavier_decisions "
                        " WHERE account_id=$1", ACCT)


@pytest.fixture(autouse=True)
async def _clean():
    if DSN:
        async with _conn() as c:
            await _purge(c)
    yield
    if DSN:
        async with _conn() as c:
            await _purge(c)


def _kw(**over):
    base = dict(account_id=ACCT, venue="PMUS_TEST", intent_id="int-x-1",
                decided_at=time.time(), responsibility_state=X.HELD,
                execution_eligibility=X.E_HOLD,
                alternatives=[{"action": "HOLD", "value_usd": 1.0},
                              {"action": "DIRECT_EXIT",
                               "blocker": "NO_BID"}],
                reasoning={"why": "HOLD is worth more than every executable "
                                  "alternative"},
                expected_economics={"expected_net_usd": 1.0},
                residual_exposure={"held_qty": 10, "unpaired_qty": 10},
                evidence={"probability_source": "PINNACLE_DEVIG_V1"},
                obligations=[], chosen_action="HOLD")
    base.update(over)
    return base


async def test_a_review_is_recorded_once_and_never_rewritten():
    async with _conn() as c:
        kw = _kw()
        got = await X.record_decision(c, **kw)
        assert got["ok"] is True and got["already"] is False, got
        again = await X.record_decision(c, **kw)
        assert again["ok"] is True and again["already"] is True
        latest = await X.latest_decisions(c, account_id=ACCT)
        row = latest["positions"][0]
        assert row["alternatives"][1]["blocker"] == "NO_BID"
        assert "dispatch_result" not in row
        assert row["execution"]["status"] == X.X_NOT_CLAIMED
        # HOLD has no plan and so nothing can be claimed against it
        cl = await X.claim_dispatch(c, xavier_decision_id=got[
            "xavier_decision_id"], plan_digest="p-any")
        assert cl["claimed"] is False
        assert cl["refusal"] == X.R_NOT_THE_WINNING_PLAN


def _exit_kw(**over):
    return _kw(chosen_action="DIRECT_EXIT", chosen_plan_digest="plan-A",
               decision_id="dec:int-x-1:1", execution_eligibility=X.E_DISPATCHED,
               portfolio_group_id="grp-x-1", **over)


async def test_the_claim_prevents_a_second_submission_of_the_decision_or_plan():
    async with _conn() as c:
        got = await X.record_decision(c, **_exit_kw())
        xid = got["xavier_decision_id"]
        wrong = await X.claim_dispatch(c, xavier_decision_id=xid,
                                       plan_digest="plan-B")
        assert wrong["claimed"] is False
        assert wrong["refusal"] == X.R_NOT_THE_WINNING_PLAN
        first = await X.claim_dispatch(c, xavier_decision_id=xid,
                                       plan_digest="plan-A",
                                       order_intent_id="exit-1")
        assert first["claimed"] is True, first
        replay = await X.claim_dispatch(c, xavier_decision_id=xid,
                                        plan_digest="plan-A")
        assert replay["claimed"] is False
        assert replay["refusal"] == X.R_ALREADY_CLAIMED
        # a DIFFERENT review of the same position that chose the SAME plan
        other = await X.record_decision(c, **_exit_kw(
            decided_at=time.time() + 5))
        dup = await X.claim_dispatch(c, xavier_decision_id=other[
            "xavier_decision_id"], plan_digest="plan-A")
        assert dup["claimed"] is False
        assert dup["refusal"] == X.R_PLAN_ALREADY_CLAIMED
        # the database itself refuses a second claim, whatever the key
        with pytest.raises(asyncpg.exceptions.UniqueViolationError):
            await c.execute(
                "INSERT INTO bettor_xavier_execution_events (idempotency_key, "
                " fact_sha, xavier_decision_id, decision_id, plan_digest, "
                " position_intent_id, account_id, venue, event_kind, source, "
                " occurred_at) VALUES ('k-other','s',$1,'dec:int-x-1:1',"
                " 'plan-A','int-x-1',$2,'PMUS_TEST','DISPATCH_CLAIMED',"
                " 'DISPATCHER', now())", xid, ACCT)


async def test_concurrent_claims_admit_exactly_one_sender():
    async with _conn() as c:
        got = await X.record_decision(c, **_exit_kw())
        xid = got["xavier_decision_id"]
    conns = [await asyncpg.connect(DSN) for _ in range(6)]
    try:
        res = await asyncio.gather(*(X.claim_dispatch(
            k, xavier_decision_id=xid, plan_digest="plan-A") for k in conns))
    finally:
        for k in conns:
            await k.close()
    assert sum(1 for r in res if r["claimed"]) == 1, res
    assert all(r["refusal"] == X.R_ALREADY_CLAIMED
               for r in res if not r["claimed"])


async def test_execution_is_an_append_only_idempotent_history():
    async with _conn() as c:
        got = await X.record_decision(c, **_exit_kw())
        xid = got["xavier_decision_id"]
        # nothing can be recorded as sent before the claim -- writer and DB
        pre = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_ACK, source="SEND_RESPONSE",
            venue_order_id="vo-1")
        assert pre["refusal"] == X.R_NO_CLAIM
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await c.execute(
                "INSERT INTO bettor_xavier_execution_events (idempotency_key, "
                " fact_sha, xavier_decision_id, plan_digest, "
                " position_intent_id, account_id, venue, venue_order_id, "
                " event_kind, source, occurred_at) VALUES ('k-pre','s',$1,"
                " 'plan-A','int-x-1',$2,'PMUS_TEST','vo-1','ACKNOWLEDGED',"
                " 'SEND_RESPONSE', now())", xid, ACCT)
        assert (await X.claim_dispatch(c, xavier_decision_id=xid,
                                       plan_digest="plan-A",
                                       order_intent_id="exit-1"))["claimed"]
        st = await X.execution_state(c, xavier_decision_id=xid)
        # claimed with nothing after it: the send may or may not have left
        assert st["status"] == X.X_CLAIMED_OUTCOME_UNRECORDED
        ack = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_ACK, source="SEND_RESPONSE",
            venue_order_id="vo-1")
        assert ack["appended"] is True
        f4 = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_FILL, source="SEND_RESPONSE",
            venue_order_id="vo-1", cumulative_filled_qty=4,
            avg_fill_price_cents=61)
        assert f4["appended"] is True
        # the same fill seen again by another reader appends nothing
        f4b = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_FILL,
            source="ORDER_STATUS_READ", venue_order_id="vo-1",
            cumulative_filled_qty=4)
        assert f4b["appended"] is False and f4b["already"] is True
        # the same key asserting a different fact is refused, never merged
        clash = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_FILL,
            source="ORDER_STATUS_READ", venue_order_id="vo-1",
            cumulative_filled_qty=5, idempotency_key=f4["idempotency_key"])
        assert clash["refusal"] == X.R_CONFLICTING_RETRY
        # a LATER partial fill is recorded after the first: nothing blocks it
        f7 = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_FILL,
            source="FILLS_LEDGER", venue_order_id="vo-1",
            cumulative_filled_qty=7)
        assert f7["appended"] is True
        # a stale read arriving late does not lower the displayed quantity
        await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_FILL,
            source="RECOVERY_READ", venue_order_id="vo-1",
            cumulative_filled_qty=6)
        mid = await X.execution_state(c, xavier_decision_id=xid)
        assert mid["filled_qty"] == 7.0 and mid["status"] == X.X_WORKING
        term = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_CANCELLED,
            source="ORDER_STATUS_READ", venue_order_id="vo-1",
            terminal_status="CANCELLED_REMAINDER")
        assert term["appended"] is True
        # a correction supersedes a fact without rewriting it
        corr = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_CORRECTION,
            source="OPERATOR_RECONCILIATION", venue_order_id="vo-1",
            cumulative_filled_qty=6.5, supersedes_event_id=f7["event_id"])
        assert corr["appended"] is True, corr
        latest = await X.latest_decisions(c, account_id=ACCT)
        ex = latest["positions"][0]["execution"]
        assert ex["filled_qty"] == 6.5
        assert ex["status"] == "TERMINAL:CANCELLED_REMAINDER"
        assert ex["venue_order_ids"] == ["vo-1"]
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await c.execute("UPDATE bettor_xavier_execution_events SET "
                            " cumulative_filled_qty=10 WHERE event_id=$1",
                            f7["event_id"])
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await c.execute("DELETE FROM bettor_xavier_execution_events "
                            " WHERE event_id=$1", f7["event_id"])
        # the decision itself is still exactly what was recorded
        again = await X.history(c, intent_id="int-x-1")
        assert again["decisions"][0]["chosen_plan_digest"] == "plan-A"


async def test_a_lost_acknowledgement_stays_unresolved_until_recovered():
    async with _conn() as c:
        got = await X.record_decision(c, **_exit_kw())
        xid = got["xavier_decision_id"]
        assert (await X.claim_dispatch(c, xavier_decision_id=xid,
                                       plan_digest="plan-A"))["claimed"]
        # the send raised: the compatibility writer maps it to UNKNOWN
        d = await X.record_dispatch(c, xavier_decision_id=xid,
                                    result={"unknown": True,
                                            "error": "TimeoutError"})
        assert d["ok"] is True and d["written"] is True
        un = await X.unresolved_claims(c, account_id=ACCT)
        assert [u["xavier_decision_id"] for u in un] == [xid]
        assert un[0]["status"] == X.X_UNRESOLVED
        # a restart replays the same claim: it must not be sendable again
        again = await X.claim_dispatch(c, xavier_decision_id=xid,
                                       plan_digest="plan-A")
        assert again["claimed"] is False
        rec = await X.record_execution_event(
            c, xavier_decision_id=xid, kind=X.K_RECOVERED,
            source="RECOVERY_READ", venue_order_id="vo-9",
            cumulative_filled_qty=10, terminal_status="FILLED")
        assert rec["appended"] is True
        st = await X.execution_state(c, xavier_decision_id=xid)
        assert st["status"] == "TERMINAL:FILLED" and st["filled_qty"] == 10
        assert await X.unresolved_claims(c, account_id=ACCT) == []


async def test_a_send_that_bypassed_the_claim_is_refused_by_name():
    async with _conn() as c:
        got = await X.record_decision(c, **_exit_kw())
        xid = got["xavier_decision_id"]
        d = await X.record_dispatch(c, xavier_decision_id=xid,
                                    result={"sent": True,
                                            "venue_order_id": "vo-x"})
        assert d["ok"] is False and d["refusal"] == X.R_NO_CLAIM
        nothing = await X.record_dispatch(c, xavier_decision_id=xid,
                                          result={"sent": False})
        assert nothing["written"] is False


async def test_the_record_refuses_what_it_cannot_stand_behind():
    async with _conn() as c:
        bad = await X.record_decision(c, **_kw(responsibility_state="MAYBE"))
        assert bad["refusal"] == X.R_STATE
        noplan = await X.record_decision(c, **_kw(chosen_action="REDUCE"))
        assert noplan["refusal"] == X.R_PLAN_REQUIRED
        got = await X.record_decision(c, **_kw())
        xid = got["xavier_decision_id"]
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await c.execute("UPDATE bettor_xavier_decisions SET "
                            " chosen_action='EXIT' WHERE xavier_decision_id=$1",
                            xid)
        with pytest.raises(asyncpg.exceptions.RaiseError):
            await c.execute("DELETE FROM bettor_xavier_decisions "
                            " WHERE xavier_decision_id=$1", xid)
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await c.execute(
                "INSERT INTO bettor_xavier_decisions (xavier_decision_id, "
                " account_id, venue, intent_id, decided_at, xavier_version, "
                " responsibility_state, chosen_action, execution_eligibility) "
                " VALUES ('xav:t', $1, 'v', 'i', now(), 'X', 'HELD', 'EXIT', "
                " 'DISPATCHED')", ACCT)
