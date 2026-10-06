"""P1 FIRST-LOSS CENSUS: ONE SLOW VENUE READ NO LONGER STALLS A FETCH.

Production (2026-10-06, research-sql run 37477575060 section 3b): the
metered NCAAF quotes arrived 15.19 s old; queue position 1's paced venue
read took ~17 s, so positions 2..50 of the same fetch reached their turn at
32.7-39.5 s and were refused QUOTE_STALE_ON_ARRIVAL. A read that cannot
finish before its OWN candidate's probability deadline (the provider change
instant + the unchanged 30 s rule) yields nothing a decision could use.

Pinned here: the read is bounded by that deadline -- not started past it,
awaited no longer than it, never dispatched past it even after the pacer's
queue -- and refused by name; the 30 s rule and the pacer are unchanged; a
read with no deadline behaves exactly as before.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import refusal_taxonomy as RT
from sportsassets import venue_request_gate as grt
from sportsassets.workers import ext_pinnacle_loop as L


def test_the_deadline_is_the_provider_instant_plus_the_unchanged_rule():
    assert L.PINNACLE_MAX_AGE_S == 30.0
    assert L.probability_deadline(1000.0) == 1030.0
    for bad in (None, "x", float("nan")):
        assert L.probability_deadline(bad) is None
    assert L._GATE_DEADLINE_PASSED == grt.R_DEADLINE_PASSED
    assert L._GATE_COOLDOWN_PAST_DEADLINE == grt.R_COOLDOWN_EXCEEDS_DEADLINE
    c = L.R_READ_PAST_PROBABILITY_DEADLINE
    assert RT.classify(c)["classified"]
    assert ext.STAGE_OF[c] == "2_FRESHNESS"
    assert ext.EVALUABILITY_OF[c] == ext.DECIDED
    # never relabelled a venue failure, and no calibration record claims a
    # book that was never read
    assert c not in L.CALIBRATION_ONLY_AFTER_READ_FAILURE


@pytest.mark.asyncio
async def test_a_read_past_its_deadline_is_not_started(monkeypatch):
    calls = []
    monkeypatch.setattr(L, "_read_book_blocking",
                        lambda slug: calls.append(slug) or {})
    got = await L.venue_quote(None, us_slug="aec-cfb-a-b-2026-10-10",
                              intent="ORDER_INTENT_BUY_LONG",
                              deadline_epoch_s=time.time() - 1.0)
    assert got["ok"] is False
    assert got["refusal"] == L.R_READ_PAST_PROBABILITY_DEADLINE
    assert got["bound"] == "NOT_STARTED" and calls == []
    assert "marketData" not in got and "ask" not in got


@pytest.mark.asyncio
async def test_a_slow_read_is_abandoned_at_the_deadline_not_at_the_timeout(
        monkeypatch):
    def slow(slug):
        time.sleep(1.5)
        return {"marketData": None}
    monkeypatch.setattr(L, "_read_book_blocking", slow)
    t0 = time.monotonic()
    got = await L.venue_quote(None, us_slug="aec-cfb-a-b-2026-10-10",
                              intent="ORDER_INTENT_BUY_LONG",
                              deadline_epoch_s=time.time() + 0.3)
    spent = time.monotonic() - t0
    assert got["refusal"] == L.R_READ_PAST_PROBABILITY_DEADLINE
    assert got["bound"] == "AWAIT_ABANDONED_AT_THE_DEADLINE"
    assert spent < 1.0 < L.VENUE_TIMEOUT_S


@pytest.mark.asyncio
async def test_the_collector_context_carries_the_deadline_to_the_reader(
        monkeypatch):
    seen = {}

    def reader(slug):
        seen["deadline"] = L._READ_DEADLINE.get()
        return {"error": grt.R_DEADLINE_PASSED,
                "refused_by": "OUR_REQUEST_GATE"}
    monkeypatch.setattr(L, "_read_book_blocking", reader)
    dl = time.time() + 5.0
    tok = L._READ_DEADLINE.set(dl)
    try:
        got = await L.venue_quote(None, us_slug="aec-nba-a-b-2026-10-10",
                                  intent="ORDER_INTENT_BUY_LONG")
    finally:
        L._READ_DEADLINE.reset(tok)
    assert seen["deadline"] == dl
    assert got["refusal"] == L.R_READ_PAST_PROBABILITY_DEADLINE
    assert got["bound"].startswith("REFUSED_BY_OUR_GATE")
    # outside the collector's call the variable is unset: no bound
    assert L._READ_DEADLINE.get() is None


@pytest.mark.asyncio
async def test_without_a_deadline_the_read_is_unchanged(monkeypatch):
    monkeypatch.setattr(L, "_read_book_blocking",
                        lambda slug: {"error": "429 rate limited"})
    got = await L.venue_quote(None, us_slug="aec-nba-a-b-2026-10-10",
                              intent="ORDER_INTENT_BUY_LONG")
    assert got["refusal"] == L.R_VENUE_READ_ERROR
    # a gate refusal with no deadline set stays the gate's own refusal
    monkeypatch.setattr(L, "_read_book_blocking", lambda slug: {
        "error": grt.R_DEADLINE_PASSED, "refused_by": "OUR_REQUEST_GATE"})
    got = await L.venue_quote(None, us_slug="aec-nba-a-b-2026-10-10",
                              intent="ORDER_INTENT_BUY_LONG")
    assert got["refusal"] == L.R_VENUE_READ_ERROR


def test_the_gate_refuses_a_request_whose_deadline_passed_in_the_queue():
    rid = grt.begin_read(slug="s", deadline_epoch_s=time.time() + 60)
    try:
        grt.check_deadline_after_pacing(read_id=rid)        # inside: sent
        with pytest.raises(grt.VenueGateRefusal) as e:
            grt.check_deadline_after_pacing(read_id=rid,
                                            now=time.time() + 61)
        assert e.value.refusal == grt.R_DEADLINE_PASSED
        assert e.value.detail["stage"] == "AFTER_THE_PACER_QUEUE"
        assert grt.read_state(rid)["gate_refusals"][-1]["stage"] == \
            "AFTER_THE_PACER_QUEUE"
    finally:
        grt.end_read(rid)
    # a read with no deadline is never refused here
    rid = grt.begin_read(slug="s")
    try:
        grt.check_deadline_after_pacing(read_id=rid, now=time.time() + 1e6)
    finally:
        grt.end_read(rid)
    grt.check_deadline_after_pacing(read_id=None)


def test_the_transport_rechecks_after_pacing_and_sends_nothing():
    httpx = pytest.importorskip("httpx")
    sent = []

    class Inner(httpx.BaseTransport):
        def handle_request(self, request):
            sent.append(request)
            return httpx.Response(200)

    rid = grt.begin_read(slug="s", deadline_epoch_s=time.time() + 0.05)
    grt.bind_read(rid)
    try:
        t = grt.PacedTransport(Inner(), pace=lambda: time.sleep(0.2))
        with pytest.raises(grt.VenueGateRefusal):
            t.handle_request(httpx.Request("GET", "https://x/v1/book"))
        assert sent == []
        assert grt.attempts_for_read(rid) == 0
    finally:
        grt.end_read(rid)
        grt.bind_read(None)
