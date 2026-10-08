"""FIRST-LOSS CENSUS (2026-10-08): AN ABANDONED VENUE READ IS NEVER SENT.

Production (research-sql 37412834338 T1; 37477575060 section 4b): the first
candidate of a metered fetch reached its paced book read with ~15 s to its
probability deadline; the await was abandoned at VENUE_TIMEOUT_S (10 s,
VENUE_BOOK_READ_FAILED / VENUE_TIMEOUT) and the next candidate arrived at
15.6 s -- QUOTE_STALE_ON_ARRIVAL for the rest of the fetch. The request gate
was told only the PROBABILITY deadline, so the abandoned reader thread went
on to sleep out the venue's 429 hold and DISPATCH the request seconds later,
unawaited, into an endpoint that answers 429 at ~0.23 req/s; and a hold that
outlasted the await (but not the deadline) was slept for the whole await.

Pinned here: the read's dispatch deadline is the instant its caller stops
awaiting it (READ_DISPATCH_BOUNDED_BY_THE_AWAIT_RULE) -- a hold longer than
the await is refused at once and named as OUR gate's refusal, never as the
probability deadline unless the hold outlasts that too; a read abandoned at
the await is never dispatched; a sooner probability deadline behaves exactly
as before. The 30 s rule, VENUE_TIMEOUT_S, the pacer and the holds are
unchanged.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from sportsassets import pmus
from sportsassets import venue_request_gate as grt
from sportsassets.workers import ext_pinnacle_loop as L

SLUG = "aec-cfb-tenn-ark-2026-10-10"
LONG = "ORDER_INTENT_BUY_LONG"


@pytest.fixture(autouse=True)
def _no_hold():
    grt.clear_hold()
    yield
    grt.clear_hold()


def test_the_limits_and_the_clock_are_unchanged():
    assert L.PINNACLE_MAX_AGE_S == 30.0
    assert L.VENUE_TIMEOUT_S == 10.0
    assert L.probability_deadline(1000.0) == 1030.0
    assert "no limit, clock or pacing" in \
        L.READ_DISPATCH_BOUNDED_BY_THE_AWAIT_RULE


def test_the_dispatch_bound_is_the_earlier_of_the_two_instants():
    # no probability deadline: the await bound
    b = L.read_dispatch_bound(1000.0, None)
    assert b == {"timeout_s": 10.0, "gate_deadline_epoch_s": 1010.0,
                 "probability_binds": False}
    # the probability deadline comes first: EXACTLY that deadline, as before
    b = L.read_dispatch_bound(1000.0, 1004.5)
    assert b == {"timeout_s": 4.5, "gate_deadline_epoch_s": 1004.5,
                 "probability_binds": True}
    # the probability deadline is beyond the await (the first candidate of a
    # metered fetch, ~15 s out): the gate gets the await bound, not 1015
    b = L.read_dispatch_bound(1000.0, 1015.0)
    assert b == {"timeout_s": 10.0, "gate_deadline_epoch_s": 1010.0,
                 "probability_binds": False}
    # equal: the await bound (it is the same instant)
    b = L.read_dispatch_bound(1000.0, 1010.0)
    assert b["gate_deadline_epoch_s"] == 1010.0
    assert b["probability_binds"] is False


@pytest.mark.asyncio
async def test_the_reader_is_handed_the_await_bound(monkeypatch):
    seen = []

    def reader(slug):
        seen.append(L._READ_DEADLINE.get())
        return {"error": "x"}
    monkeypatch.setattr(L, "_read_book_blocking", reader)
    t0 = time.time()
    await L.venue_quote(None, us_slug=SLUG, intent=LONG,
                        deadline_epoch_s=t0 + 15.0)
    await L.venue_quote(None, us_slug=SLUG, intent=LONG)
    for dl in seen:
        assert dl is not None
        assert t0 + L.VENUE_TIMEOUT_S - 0.5 <= dl <= time.time() \
            + L.VENUE_TIMEOUT_S
    # a sooner probability deadline is handed over unchanged
    dl = time.time() + 3.0
    await L.venue_quote(None, us_slug=SLUG, intent=LONG, deadline_epoch_s=dl)
    assert seen[-1] == dl
    assert L._READ_DEADLINE.get() is None


def _gate_stub(monkeypatch, sent, slept):
    """`pmus.book_read` standing in for the SDK call through our transport:
    the gate's own check, recording (never sleeping) any hold it would
    sleep out, then the dispatch."""
    class Client:
        _http = None
    monkeypatch.setattr(pmus, "_get_client", lambda: Client())

    def book_read(client, slug):
        grt.check_before_dispatch(read_id=grt.current_read(),
                                  sleep=slept.append)
        sent.append(slug)
        return {"marketData": None, "error": "UNREACHED_IN_THIS_TEST"}
    monkeypatch.setattr(pmus, "book_read", book_read)


@pytest.mark.asyncio
async def test_a_hold_longer_than_the_await_is_refused_at_once(monkeypatch):
    sent, slept = [], []
    _gate_stub(monkeypatch, sent, slept)
    now = time.time()
    # the venue's hold ends 12 s from now: past the 10 s await, inside the
    # candidate's probability deadline 20 s out
    grt.hold_until(until_epoch_s=now + 12.0, reason="VENUE_429_ON_BOOK_READ")
    t0 = time.monotonic()
    got = await L.venue_quote(None, us_slug=SLUG, intent=LONG,
                              deadline_epoch_s=now + 20.0)
    assert time.monotonic() - t0 < 2.0        # not the 10 s await
    assert slept == [] and sent == []         # nothing slept, nothing sent
    # OUR GATE's refusal, by name -- not the probability deadline (it had
    # not passed) and not a venue failure
    assert got["ok"] is False
    assert got["refusal"] == L.R_VENUE_READ_ERROR
    assert got["refused_by"] == "OUR_REQUEST_GATE"
    assert got["diagnostic"]["refusal"] == grt.R_COOLDOWN_EXCEEDS_DEADLINE
    assert L.venue_read_refusal(got) == L.VR_GATE_COOLDOWN


@pytest.mark.asyncio
async def test_a_hold_past_the_probability_deadline_keeps_its_name(
        monkeypatch):
    sent, slept = [], []
    _gate_stub(monkeypatch, sent, slept)
    now = time.time()
    grt.hold_until(until_epoch_s=now + 25.0, reason="VENUE_429_ON_BOOK_READ")
    got = await L.venue_quote(None, us_slug=SLUG, intent=LONG,
                              deadline_epoch_s=now + 20.0)
    assert slept == [] and sent == []
    assert got["refusal"] == L.R_READ_PAST_PROBABILITY_DEADLINE
    assert got["bound"] == ("REFUSED_BY_OUR_GATE:%s"
                            % grt.R_COOLDOWN_EXCEEDS_DEADLINE)


@pytest.mark.asyncio
async def test_a_hold_inside_the_await_is_still_waited_out(monkeypatch):
    sent, slept = [], []
    _gate_stub(monkeypatch, sent, slept)
    now = time.time()
    grt.hold_until(until_epoch_s=now + 7.0, reason="VENUE_429_ON_BOOK_READ")
    await L.venue_quote(None, us_slug=SLUG, intent=LONG,
                        deadline_epoch_s=now + 20.0)
    # the venue's 7 s window fits the await: honoured, then sent
    assert len(slept) == 1 and 6.0 < slept[0] <= 7.0
    assert sent == [SLUG]


@pytest.mark.asyncio
async def test_a_read_abandoned_at_the_await_is_never_dispatched(
        monkeypatch):
    httpx = pytest.importorskip("httpx")
    sent = []
    done = threading.Event()

    class Inner(httpx.BaseTransport):
        def handle_request(self, request):
            sent.append(str(request.url))
            return httpx.Response(200, json={})

    # the pacer's queue outlasts the (shortened) await
    transport = grt.PacedTransport(Inner(), pace=lambda: time.sleep(0.6))

    class Client:
        _http = None
    monkeypatch.setattr(pmus, "_get_client", lambda: Client())

    def book_read(client, slug):
        try:
            transport.handle_request(
                httpx.Request("GET", "https://x/v1/markets/%s/book" % slug))
            return {"marketData": {}}
        finally:
            done.set()
    monkeypatch.setattr(pmus, "book_read", book_read)
    monkeypatch.setattr(L, "VENUE_TIMEOUT_S", 0.3)
    got = await L.venue_quote(None, us_slug=SLUG, intent=LONG,
                              deadline_epoch_s=time.time() + 5.0)
    # abandoned at the await, named as before
    assert got["refusal"] == L.R_VENUE_READ_FAILED
    assert L.venue_read_refusal(got) == L.VR_TIMEOUT
    # ...and the orphaned thread, released by the pacer AFTER the await gave
    # up, sends nothing (before the rule it dispatched: the probability
    # deadline was still 4 s away)
    assert done.wait(5.0)
    assert sent == []


@pytest.mark.asyncio
async def test_the_observation_read_is_bounded_the_same_way(monkeypatch):
    seen = []

    def reader(slug):
        seen.append(L._READ_DEADLINE.get())
        return {"error": "x"}
    monkeypatch.setattr(L, "_read_book_blocking", reader)
    t0 = time.time()
    # the real observation read (conftest keeps it reachable by this name)
    got = await L._real_observation_quote(SLUG, LONG)
    assert got["refusal"] == L.R_VENUE_READ_ERROR
    assert seen and seen[0] is not None
    assert t0 + L.VENUE_TIMEOUT_S - 0.5 <= seen[0] <= time.time() \
        + L.VENUE_TIMEOUT_S
    assert L._READ_DEADLINE.get() is None
