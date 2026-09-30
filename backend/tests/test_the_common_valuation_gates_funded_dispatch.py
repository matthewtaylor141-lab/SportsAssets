"""A FUNDED ORDER GOES OUT ONLY ON A ROBUST, AGREEING ONE-MEASURE CHOICE.

The limits-constrained ranking (`FD.decide`) still chooses; the common
valuation (`bettor_common_valuation`) values every action on one distribution
over the held contract's own payouts with the void rate's range -- and, where
the contract's void payout is not established, over [0, 100] cents -- and the
dispatch goes out only if that valuation permits it AND picks the same fixed
action. Driven through `ext_pinnacle_loop.cycle()` with only the venue
transport and the held-leg read substituted. Market data is SYNTHETIC.
"""
from __future__ import annotations

import json
import os

import pytest

from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_management as FM
from sportsassets import bettor_funded_pair_cycle as PC
from tests.test_an_incident_stop_keeps_protective_exits import _venue_terms
from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, VENUE, _clean, _entry, _level, _live, _probability, _seed,
    _stopped, _transport)

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


async def _run(conn, monkeypatch, *, p, bid, terms):
    from sportsassets.workers import ext_pinnacle_loop as L
    await _clean(conn)
    await _seed(conn)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.ACCOUNT_KEY,
        json.dumps({"account_id": ACCT, "venue": VENUE, "approved": True}))
    await _entry(conn, qty=10, price=0.60)
    await _probability(conn, p=p)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
    pmus, sent, _ = _transport(monkeypatch, bids=[_level(bid, 400)])
    if terms:
        _venue_terms(monkeypatch)
    monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
    monkeypatch.setattr(L, "_running", lambda c: _stopped())
    monkeypatch.setattr(
        L, "book_currency_evidence",
        lambda slug=None: {"subscription": _live(), "revalidation": None})
    out = await L.cycle(conn)
    step = (out["funded_servicing"]["pair_cycle"].get("considered")
            or [{}])[0]
    return step, [q for k, q in sent if k == "create"]


@pg
@pytest.mark.asyncio
async def test_an_exit_robust_over_the_range_is_sent(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        step, creates = await _run(conn, monkeypatch, p=0.55, bid=0.75,
                                   terms=True)
        assert creates and creates[0]["intent"] == "ORDER_INTENT_SELL_LONG"
        assert step["funded_dispatch_gate"]["permitted"] is True
        cv = step["common_valuation"]
        assert cv["held_payouts_from"] == \
            "THE_HELD_CONTRACTS_OWN_SETTLEMENT_RULES"
        assert cv["void_range"] == [0.0, 1.0]       # no measured rate
        assert cv["winner"]["fixed_action"][:3] == \
            ["DIRECT_EXIT", "DIRECT_EXIT", 10.0]
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_exit_that_depends_on_the_void_rate_is_not_sent(monkeypatch):
    """p = 0.45, bid 0.49, void pays 50c: the exit beats HOLD if the fixture
    never voids and loses to it if it always does. The ranking selects the
    exit; real money does not follow."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        step, creates = await _run(conn, monkeypatch, p=0.45, bid=0.49,
                                   terms=True)
        assert step["decision"]["action"] == "EXIT"
        assert creates == []
        assert step["refusal"] == \
            "THE_SELECTED_ACTION_CHANGES_WITHIN_THE_VOID_RATES_UNCERTAINTY"
        assert step["common_valuation"]["selection_basis"] == \
            "CONDITIONAL_RESEARCH_VALUATION_NOT_FOR_FUNDED_DISPATCH"
    finally:
        await _clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_unestablished_void_payout_is_a_range_not_a_zero(
        monkeypatch):
    """Without the contract's terms the void payout is valued over [0, 100]
    cents. With the rate unmeasured too, even this exit is not robust (if the
    fixture voids and the void paid 100c, HOLD would win), so it is recorded as
    research and not sent."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        step, creates = await _run(conn, monkeypatch, p=0.55, bid=0.75,
                                   terms=False)
        assert creates == []
        cv = step["common_valuation"]
        assert cv["void_cents_range"] == [0.0, 100.0]
        assert cv["held_payouts_from"] == \
            "BINARY_PAYOUTS_VOID_PAYOUT_NOT_ESTABLISHED"
        assert step["funded_dispatch_gate"]["permitted"] is False
    finally:
        await _clean(conn)
        await conn.close()


def test_the_gate_refuses_when_the_two_rankings_disagree():
    verdict = {"selected": "REDUCE",
               "selected_candidate": {"action": "REDUCE", "qty": 4.0}}
    cv = {"ok": True, "funded_dispatch_permitted": True,
          "selection_basis": "ROBUST_ACROSS_THE_VOID_RATE_RANGE",
          "winner": {"fixed_action": ["DIRECT_EXIT", "DIRECT_EXIT", 10.0,
                                      None]}}
    g = PC.common_valuation_gate(verdict, cv)
    assert g["permitted"] is False and g["refusal"] == PC.R_CV_DISAGREES
    # same kind, different quantity: also a different fixed action
    cv2 = dict(cv, winner={"fixed_action": ["REDUCE", "REDUCE", 6.0, None]})
    assert PC.common_valuation_gate(verdict, cv2)["refusal"] == \
        PC.R_CV_DISAGREES
    cv3 = dict(cv, winner={"fixed_action": ["REDUCE", "REDUCE", 4.0, None]})
    assert PC.common_valuation_gate(verdict, cv3)["permitted"] is True
    # a hold needs no permit
    assert PC.common_valuation_gate({"selected": "HOLD"}, {})["permitted"]
