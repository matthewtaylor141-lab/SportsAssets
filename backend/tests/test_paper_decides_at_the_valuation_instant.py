"""DEREK'S PAPER DECISION IS FORMED AT THE VALUATION INSTANT, INSIDE THE CYCLE.

The 30 s Pinnacle rule is unchanged; what this proves is WHERE it is applied.
Through the REAL collection cycle (`ext_pinnacle_loop.cycle`, the Derek
harness of test_derek_enters_on_conservative_agreement: provider, resolver,
venue quote and rules substituted at their boundaries, SYNTHETIC), with
PAPER_SESSION=on:

  * every valuation the cycle writes gets its paper decision in the cycle,
    right after it is persisted (decided_via IN_CYCLE_AT_THE_VALUATION_
    INSTANT), with the Pinnacle age at that instant recorded -- the measured
    distribution is reported and every age is inside the 30 s rule, so no
    decision refuses PROBABILITY_EVIDENCE_STALE because of our pipeline;
  * the same valuation judged by the paper pass a minute later (the old
    design, kept as a labelled backstop) refuses STALE, and says it was the
    backstop and how late it was -- the artifact this placement removes;
  * with PAPER_SESSION unset the cycle's hook does nothing at all.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_paper_session as S
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_derek_enters_on_conservative_agreement as DT

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def _wire(monkeypatch, acct, transport):
    monkeypatch.setattr(PR, "DEFAULT_ACCOUNT_ID", acct["account_id"])
    monkeypatch.setitem(PR._CLIENT, "client", PL.client(transport))
    scheduled = []

    def hook(**kw):
        scheduled.append(kw)
        return {"scheduled": False, "why": "RECORDED_BY_THE_TEST"}
    monkeypatch.setattr(RT, "paper_pass_hook", hook)
    PD._CONTEXT_CACHE.clear()
    return scheduled


@pg
async def test_the_cycle_decides_each_valuation_at_its_own_instant(monkeypatch):
    conn = await H.connect()
    t_start = time.time()
    try:
        await DT._seed(conn)
        acct = await PL.new_account(conn, "incycle", now=t_start)
        t = PL.Transport(t_start)
        t.set(DT.US_SLUG, offers=[(0.50, 5000)], bids=[(0.48, 5000)])
        _wire(monkeypatch, acct, t)
        monkeypatch.setenv(S.ENV_FLAG, "on")
        DT._stub(monkeypatch, p_home=0.60, stamp_age_s=2.0)
        out = await loop.cycle(conn)
        assert out.get("written", 0) >= 1, out.get("refusals")
        rows = await conn.fetch(
            "SELECT d.*, v.decided_at AS v_at FROM paper_decisions d "
            "  JOIN external_valuations v ON v.id = d.valuation_id "
            " WHERE d.session_id=$1 ORDER BY d.decided_at",
            acct["session_id"])
        assert rows, "the in-cycle hook recorded no paper decision"
        ages = []
        for r in rows:
            pin = H.j(r["pinnacle"])
            assert pin["decided_via"] == PD.DECIDED_VIA_CYCLE
            assert pin["age_s"] is not None
            ages.append(pin["age_s"])
            assert pin["decision_lag_after_valuation_s"] < 5.0
            assert r["refusal"] != "PROBABILITY_EVIDENCE_STALE"
        # THE MEASURED DISTRIBUTION OF PINNACLE AGE AT THE PAPER DECISION
        dist = {"n": len(ages), "min_s": min(ages), "max_s": max(ages)}
        print("PINNACLE_AGE_AT_PAPER_DECISION", dist)
        assert dist["max_s"] <= float(loop.PINNACLE_MAX_AGE_S)
        # THE OLD PLACEMENT, MEASURED: the same valuation judged by the
        # paper pass one minute later is STALE, and says why.
        late = await PL.new_account(conn, "latepass", now=t_start)
        vid = rows[0]["valuation_id"]
        row = await conn.fetchrow("SELECT * FROM external_valuations "
                                  " WHERE id=$1", vid)
        ctx = {"session": {"session_id": late["session_id"],
                           "config": late["config"]},
               "session_id": late["session_id"],
               "account_id": late["account_id"], "config": late["config"],
               "market_data": PL.client(t), "books_read": 0,
               "now": time.time() + 60.0, "deadline": time.monotonic() + 30,
               "first_fills": [], "fills": 0}
        ctx["clock"] = lambda: ctx["now"]
        rec = await PD.decide_one(conn, ctx, dict(row))
        assert rec["refusal"] == "PROBABILITY_EVIDENCE_STALE"
        d = await conn.fetchrow("SELECT pinnacle FROM paper_decisions WHERE "
                                " decision_id=$1", rec["decision_id"])
        pin = H.j(d["pinnacle"])
        assert pin["decided_via"] == PD.DECIDED_VIA_PASS
        assert pin["decision_lag_after_valuation_s"] >= 60.0
        assert pin["age_s"] > float(loop.PINNACLE_MAX_AGE_S)
    finally:
        await PL.drop_today_run(conn, t_start)
        await DT._cleanup(conn)
        await conn.close()


@pg
async def test_with_the_flag_unset_the_cycle_hook_does_nothing(monkeypatch):
    monkeypatch.delenv(S.ENV_FLAG, raising=False)

    class _NoConn:
        def __getattr__(self, name):
            raise AssertionError("the hook touched the database")
    await loop._paper_valuation(_NoConn(), 123)
    got = await PR.decide_valuation(_NoConn(), valuation_id=123)
    assert got == {"decided": False, "why": S.R_ENV_OFF}
