"""QUOTE_STALE_ON_ARRIVAL IS NO LONGER SELF-INFLICTED (P0 incident, inc-edge).

THE MEASURED DEFECT (production 2026-10-04): NFL ~600 QUOTE_STALE_ON_ARRIVAL
rows a day, ~1,034 candidate-rows a day across leagues whose PROVIDER lag was
inside 30 s at receipt, refused because `received_at` is stamped once per
sport fetch and the candidates behind it wait on paced venue reads (~5-6 s per
queue position). The provider handed the quote over in time; our own queue
made it stale.

THE REPAIR: an on-demand provider refresh for exactly that case
(`ADAPTIVE_ODDS_REFETCH_RULE`). Proved here through the REAL scheduled cycle
(`ext_pinnacle_loop.cycle`) over the production-shaped single-event fixture,
with the provider's HTTP substituted:

  * provider fresh at receipt, stale on arrival through our delay -> one
    refresh, the candidate is judged on the provider's current quote (its
    own last_update), no QUOTE_STALE_ON_ARRIVAL, the valuation persisted;
  * provider ALREADY stale at receipt -> never refreshed (a coverage fact),
    refused QUOTE_STALE_ON_ARRIVAL by name as before;
  * a refresh that is still over the limit, or the per-sport bound reached
    -> refused by name; the 30 s rule, its clock and its instant unchanged.

No venue order: the fixture's venue counts sends and the proof asserts none.
"""
from __future__ import annotations

import datetime as _dt
import os
import time

import pytest

from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")


async def _connect():
    import asyncpg
    return await asyncpg.connect(DSN)


def _event(F, *, stamp_epoch):
    stamp = _dt.datetime.fromtimestamp(stamp_epoch, _dt.timezone.utc
                                       ).strftime("%Y-%m-%dT%H:%M:%SZ")
    ev = F.odds_event(time.time())
    for bk in ev["bookmakers"]:
        bk["last_update"] = stamp
        for m in bk["markets"]:
            if "last_update" in m:
                m["last_update"] = stamp
    return ev


def _provider(monkeypatch, F, answers):
    """The odds provider's HTTP, substituted: each call answers the next
    (received_offset_s, stamp_offset_s) relative to the call instant."""
    calls = []

    async def fake_odds(sport_key, *, api_key, timeout=20.0):
        now = time.time()
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": now,
                    "credits_used": "1", "credits_remaining": "9"}
        rec_off, stamp_off = answers[min(len(calls), len(answers) - 1)]
        calls.append({"at": now, "received_at": now + rec_off,
                      "stamp": now + stamp_off})
        return {"ok": True, "events": [_event(F, stamp_epoch=now + stamp_off)],
                "received_at": now + rec_off,
                "credits_used": str(len(calls)), "credits_remaining": "9"}
    monkeypatch.setattr(loop, "fetch_odds", fake_odds)
    return calls


async def _run(monkeypatch, answers):
    from tests import _emptybook_fixture as F
    conn = await _connect()
    venue = F.Venue()
    handed: list = []
    await F.clean(conn)
    await F.seed(conn)
    real_currency = loop.book_currency_evidence
    F.substitute(monkeypatch, venue)
    # PRODUCTION'S STATE, not the funded-lifecycle fixture's assumptions: the
    # venue's book currency is NOT established (every collector record is
    # CALIBRATION_ONLY) and every submission switch is OFF.
    monkeypatch.setattr(loop, "book_currency_evidence", real_currency)
    from sportsassets import bettor_entry_execution as EX
    from sportsassets import bettor_funded_execution as FX
    from sportsassets import bettor_funded_management as FM
    monkeypatch.setattr(EX, "REAL_ORDER_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FX, "FUNDED_SUBMISSION_ENABLED", False)
    monkeypatch.setattr(FM, "FUNDED_EXIT_SUBMISSION_ENABLED", False)
    calls = _provider(monkeypatch, F, answers)

    async def paper_hook(conn_, valuation_id):
        handed.append(valuation_id)
    monkeypatch.setattr(loop, "_paper_valuation", paper_hook)
    out = await loop.cycle(conn)
    return conn, F, venue, out, calls, handed


@pg
@pytest.mark.asyncio
async def test_a_quote_our_delay_made_stale_is_refreshed_and_judged(
        monkeypatch):
    """Received 40 s ago with a 5 s provider lag (fresh at receipt), 45 s old
    at arrival: our delay. One refresh; the candidate is judged on the
    provider's current quote and recorded with ITS last_update."""
    conn, F, venue, out, calls, handed = await _run(
        monkeypatch, [(-40.0, -45.0), (0.0, -2.0)])
    try:
        assert out["ran"] is True, out.get("why")
        assert len(calls) == 2
        fr = out["odds_freshness"]
        assert fr["adaptive_refetch"] == {"fired": 1, "saved": 1,
                                          "still_stale": 0, "failed": 0,
                                          "capped": 0}
        assert loop.R_QUOTE_STALE_ON_ARRIVAL not in out["refusals"]
        assert out["latency"]["stale_on_arrival_due_to_our_processing"] == 0
        assert out["latency"]["limit_s"] == loop.PINNACLE_MAX_AGE_S == 30.0
        led = [e for e in out["mapped_candidate_ledger"]
               if e.get("us_market_slug") == F.US_SLUG]
        assert led and led[0]["first_refusal"] != \
            loop.R_QUOTE_STALE_ON_ARRIVAL
        row = await conn.fetchrow(
            "SELECT extract(epoch from observed_at) AS obs, "
            "extract(epoch from received_at) AS rcv FROM external_valuations "
            "WHERE condition_id=$1 AND NOT coalesce(payout_is_complement, "
            "false)", F.CONDITION)
        assert row is not None
        # the valuation carries the REFRESHED provider instant (whole
        # seconds, as the provider states it), not the stale one
        assert abs(float(row["obs"]) - calls[1]["stamp"]) < 1.0
        assert abs(float(row["rcv"]) - calls[1]["received_at"]) < 1e-3
        # the agents receive it (the priced side and its other side)
        assert len(handed) == 2
        assert out["calibration_only"]["recorded"] == 1
        assert out["calibration_only"]["complement_recorded"] == 1
        assert venue.creates_sent() == []
    finally:
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_quote_the_provider_delivered_stale_is_never_refreshed(
        monkeypatch):
    """45 s old already at receipt: the provider's lag, a coverage fact.
    No refresh; refused QUOTE_STALE_ON_ARRIVAL by name, as before."""
    conn, F, venue, out, calls, handed = await _run(
        monkeypatch, [(0.0, -45.0)])
    try:
        assert len(calls) == 1
        assert out["odds_freshness"]["adaptive_refetch"]["fired"] == 0
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["provider_stale_on_arrival"] == 1
        assert handed == []
    finally:
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_refresh_still_over_the_limit_is_refused_by_name(monkeypatch):
    """The refresh returns a quote that is again ours-stale: refused
    QUOTE_STALE_ON_ARRIVAL; the limit is not bent to admit it."""
    conn, F, venue, out, calls, handed = await _run(
        monkeypatch, [(-40.0, -45.0), (-40.0, -45.0)])
    try:
        assert len(calls) == 2
        fr = out["odds_freshness"]["adaptive_refetch"]
        assert fr["fired"] == 1 and fr["still_stale"] == 1
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert handed == []
    finally:
        await F.clean(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_the_per_sport_bound_holds(monkeypatch):
    monkeypatch.setattr(loop, "ADAPTIVE_ODDS_REFETCH_MAX_PER_SPORT", 0)
    conn, F, venue, out, calls, handed = await _run(
        monkeypatch, [(-40.0, -45.0), (0.0, -2.0)])
    try:
        assert len(calls) == 1
        fr = out["odds_freshness"]["adaptive_refetch"]
        assert fr["fired"] == 0 and fr["capped"] == 1
        assert out["refusals"].get(loop.R_QUOTE_STALE_ON_ARRIVAL) == 1
        assert out["latency"]["stale_on_arrival_due_to_our_processing"] == 1
    finally:
        await F.clean(conn)
        await conn.close()


def test_the_rule_is_stated_and_the_limit_is_unchanged():
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert loop.EVENTS_PER_ODDS_FETCH == loop.MAX_PER_CYCLE   # default kept
    assert "unchanged" in loop.ADAPTIVE_ODDS_REFETCH_RULE
    assert "never refreshed" in loop.ADAPTIVE_ODDS_REFETCH_RULE
