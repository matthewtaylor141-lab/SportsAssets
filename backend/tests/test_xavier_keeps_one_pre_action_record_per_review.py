"""XAVIER'S RECORD: one per position per review, written before dispatch,
never rewritten except to record once what dispatch did (migration 148)."""
from __future__ import annotations

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


async def test_a_review_is_recorded_once_and_its_dispatch_once():
    async with _conn() as c:
        kw = _kw()
        got = await X.record_decision(c, **kw)
        assert got["ok"] is True and got["already"] is False, got
        again = await X.record_decision(c, **kw)
        assert again["ok"] is True and again["already"] is True
        d = await X.record_dispatch(c, xavier_decision_id=got[
            "xavier_decision_id"], result={"sent": False})
        assert d["written"] is True
        d2 = await X.record_dispatch(c, xavier_decision_id=got[
            "xavier_decision_id"], result={"sent": True})
        assert d2["written"] is False
        latest = await X.latest_decisions(c, account_id=ACCT)
        assert latest["positions"][0]["alternatives"][1]["blocker"] == \
            "NO_BID"
        assert latest["positions"][0]["dispatch_result"] == {"sent": False}


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
