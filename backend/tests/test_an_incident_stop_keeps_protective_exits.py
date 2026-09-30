"""THE INCIDENT STOP: ACQUISITIONS STOP, PROTECTIVE EXITS CONTINUE.

The incident procedure never rolls back to a build with the exit switch off
while inventory is held. It revokes the authorization and pauses the account
(no redeploy), which refuses every entry and hedge, and leaves exits and
reductions to the exit switch alone. This proves, through the scheduled
cycle with only the venue transport substituted, that in the EXIT-ONLY switch
state -- FUNDED_EXIT_SUBMISSION_ENABLED True, FUNDED_SUBMISSION_ENABLED and
REAL_ORDER_SUBMISSION_ENABLED False -- with the authorization revoked and the
account paused:
  * a profitable full exit is SENT (the adapter receives it);
  * a new entry is refused and nothing is created for it.
Market data is SYNTHETIC.
"""
from __future__ import annotations

import json
import os

import pytest

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_funded_activation as FA
from sportsassets import bettor_funded_execution as FX
from sportsassets import bettor_funded_management as FM
from tests.test_the_funded_lifecycle_is_complete import (  # noqa: E402
    ACCT, EVENT, PAYS_ON, SLUG, VENUE, _clean, _entry, _level, _live,
    _probability, _seed, _stopped, _transport)

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


@pg
@pytest.mark.asyncio
async def test_exits_are_sent_and_acquisitions_refused_in_the_incident_state(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets.workers import ext_pinnacle_loop as L
    conn = await asyncpg.connect(DSN)
    try:
        await _clean(conn)
        # THE INCIDENT STATE: authorization revoked, account paused
        await _seed(conn, revoked=True, paused=True)
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,$2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$2::jsonb", FA.ACCOUNT_KEY,
            json.dumps({"account_id": ACCT, "venue": VENUE,
                        "approved": True}))
        await _entry(conn, qty=10, price=0.60)
        await _probability(conn, p=0.55)
        # THE EXIT-ONLY SWITCH STATE
        monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", True)
        assert FX.FUNDED_SUBMISSION_ENABLED is False
        assert EX.REAL_ORDER_SUBMISSION_ENABLED is False
        pmus, sent, _ = _transport(monkeypatch, bids=[_level(0.75, 400)])
        monkeypatch.delenv("EDGE_ODDS_API_KEY", raising=False)
        monkeypatch.setattr(L, "_running", lambda c: _stopped())
        monkeypatch.setattr(
            L, "book_currency_evidence",
            lambda slug=None: {"subscription": _live(), "revalidation": None})
        await L.cycle(conn)
        creates = [p for k, p in sent if k == "create"]
        assert creates, "the protective exit was not sent"
        assert creates[0]["intent"] == "ORDER_INTENT_SELL_LONG"
        n_exit = await conn.fetchval(
            "SELECT count(*) FROM bettor_funded_intents WHERE "
            "account_id=$1 AND kind='EXIT'", ACCT)
        assert n_exit == 1
        # AND A NEW ACQUISITION IS REFUSED, whatever else is permitted
        before = len([1 for k, _ in sent if k == "create"])
        entry = await FX.submit_for_decision(
            conn, {"admissible": True, "us_market_slug": SLUG + "-other",
                   "event_key": EVENT + "-other", "order_intent": FX.LONG,
                   "payout_event": PAYS_ON,
                   "execution_plan": {"execution": {
                       "size": 5, "vwap": 0.62, "limit_price": 0.62}}},
            account_id=ACCT, venue=VENUE)
        assert entry["ok"] is False
        assert len([1 for k, _ in sent if k == "create"]) == before
    finally:
        await _clean(conn)
        await conn.close()
