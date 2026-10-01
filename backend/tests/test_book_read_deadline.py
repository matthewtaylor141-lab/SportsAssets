"""THE BOOK READ IS BOUNDED BY THE CANDIDATE'S OWN FRESHNESS BUDGET.

The defect (research/derek_entry_lane_investigation_2026-10-01.md, Q2): the
per-candidate venue book read had no deadline. Each 10 s timeout added ~10 s
to every later candidate in the batch -- on 2026-09-30, 257 of 448
QUOTE_STALE_ON_ARRIVAL refusals were our own processing delay -- and on a
timeout the per-read accounting (gate wait vs venue) was discarded.

The 30 s Pinnacle rule is not touched by anything here; these tests only pin
that a read is given, and honours, the time that rule leaves.

THE 11 s READ. The stubbed venue blocks on an Event with an 11 s timeout. The
test releases it once its measurements are taken, so `asyncio.run` (which
joins the worker thread on exit) does not spend the 11 s for nothing; the
measured behaviour is the read's, before release.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time

import httpx
import pytest

from sportsassets import pmus
from sportsassets import venue_cooldown_store as VCS
from sportsassets import venue_http_observer as VHO
from sportsassets import venue_pace as VP
from sportsassets import venue_request_gate as GRT
from sportsassets import venue_sdk
from sportsassets.workers import ext_pinnacle_loop as loop

LONG = "ORDER_INTENT_BUY_LONG"
SLOW = "atc-unl-gre-ger-2026-10-04-gre"
FAST = "atc-unl-wal-den-2026-10-04-wal"
#: A book the snapshot parses: ask 0.63 x 900.
BOOK = {"bids": [{"px": {"value": "0.6000"}, "qty": "500"}],
        "offers": [{"px": {"value": "0.6300"}, "qty": "900"}],
        "transactTime": "2026-10-01T02:45:00Z"}
#: Time the scheduler/threads may add on a loaded box (a gate is running).
SLACK_S = 0.75


class _Conn:
    pass


@pytest.fixture(autouse=True)
def _clean():
    GRT.clear_hold()
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    VHO.reset_attempts()
    VHO.reset()
    VCS.reset_pending()
    yield
    GRT.clear_hold()
    VP._penalty_until = 0.0
    VP._penalty_until_epoch = 0.0
    VHO.reset_attempts()
    VHO.reset()
    VCS.reset_pending()


def test_an_11s_read_does_not_delay_the_next_candidate_beyond_its_budget(
        monkeypatch):
    """Candidate A (2 s of budget after the margin) hits an 11 s read.

    Before: A's read ran to VENUE_TIMEOUT_S (10 s) and B, priced 20 s ago,
    reached its read at age ~30 s -- stale by our own delay. Now A is refused
    by name inside its own budget and B is read inside its own.
    """
    release = threading.Event()
    calls = []

    def read(slug):
        b = loop._BOOK_READ_BUDGET.get() or {}
        calls.append({"slug": slug, "at": time.time(),
                      "deadline_epoch_s": b.get("deadline_epoch_s")})
        if slug == SLOW:
            release.wait(11.0)
            return {"marketData": BOOK}
        return {"marketData": BOOK}

    monkeypatch.setattr(loop, "_read_book_blocking", read)

    async def batch():
        try:
            t0 = time.time()
            pe_a = t0 - 27.0          # 3 s to the 30 s rule
            pe_b = t0 - 20.0          # 10 s to the 30 s rule
            a = await loop.venue_quote(
                _Conn(), us_slug=SLOW, intent=LONG, now=time.time(),
                freshness_deadline_epoch_s=pe_a + loop.PINNACLE_MAX_AGE_S)
            a_s = time.time() - t0
            b_age_at_read = time.time() - pe_b
            b = await loop.venue_quote(
                _Conn(), us_slug=FAST, intent=LONG, now=time.time(),
                freshness_deadline_epoch_s=pe_b + loop.PINNACLE_MAX_AGE_S)
            return a, a_s, b, b_age_at_read, pe_a, pe_b
        finally:
            release.set()

    a, a_s, b, b_age, pe_a, pe_b = asyncio.run(batch())

    # A: refused BY NAME, within its own budget (2 s), not after 10 or 11 s.
    budget_a = 30.0 - 27.0 - loop.BOOK_READ_DEADLINE_MARGIN_S
    assert a["refusal"] == loop.R_BOOK_READ_DEADLINE, a
    assert a_s <= budget_a + SLACK_S, a_s
    assert a_s < loop.VENUE_TIMEOUT_S
    assert a["read_timing"]["deadline_s"] == pytest.approx(budget_a, abs=0.2)
    assert a["read_timing"]["timeout_s"] == pytest.approx(budget_a, abs=0.2)
    # The read itself was told its deadline (the gate refuses by it).
    assert calls[0]["deadline_epoch_s"] == pytest.approx(
        pe_a + 30.0 - loop.BOOK_READ_DEADLINE_MARGIN_S)

    # B: reached inside its own budget, and actually read.
    assert b_age < 30.0 - loop.BOOK_READ_DEADLINE_MARGIN_S, b_age
    assert [c["slug"] for c in calls] == [SLOW, FAST]
    assert calls[1]["deadline_epoch_s"] == pytest.approx(
        pe_b + 30.0 - loop.BOOK_READ_DEADLINE_MARGIN_S)
    assert b.get("refusal") not in (loop.R_BOOK_READ_DEADLINE,
                                    loop.R_VENUE_READ_FAILED,
                                    loop.R_VENUE_READ_ERROR), b


def test_under_the_margin_the_candidate_is_refused_by_name_without_a_read(
        monkeypatch):
    calls = []
    monkeypatch.setattr(loop, "_read_book_blocking",
                        lambda slug: calls.append(slug) or {"marketData": BOOK})
    deadline = time.time() + loop.BOOK_READ_DEADLINE_MARGIN_S * 0.5

    out = asyncio.run(loop.venue_quote(
        _Conn(), us_slug=SLOW, intent=LONG,
        freshness_deadline_epoch_s=deadline))

    assert calls == [], "no read may be issued once the budget is gone"
    assert out["refusal"] == loop.R_BOOK_READ_DEADLINE
    assert out["read_issued"] is False
    assert out["read_timing"]["deadline_s"] < 0
    assert out["diagnostic"]["stage"] == "BOOK_READ_BUDGET"
    assert out["diagnostic"]["read_timing"] == out["read_timing"]


def test_without_a_deadline_the_read_is_unchanged(monkeypatch):
    """Callers that pass no freshness deadline (the hedge and observation
    paths) keep the 10 s bound and the old refusal name."""
    def boom(_slug):
        raise RuntimeError("no route to venue")

    monkeypatch.setattr(loop, "_read_book_blocking", boom)
    out = asyncio.run(loop.venue_quote(_Conn(), us_slug=SLOW, intent=LONG))
    assert out["refusal"] == loop.R_VENUE_READ_FAILED
    assert out["read_timing"]["timeout_s"] == loop.VENUE_TIMEOUT_S
    assert out["read_timing"]["deadline_s"] is None


# ── THE REAL READ CHAIN: _read_book_blocking -> pmus -> SDK -> our gate ──

_FAKE_KEY_ID = "00000000-0000-0000-0000-000000000000"
_FAKE_SECRET = base64.b64encode(bytes(32)).decode()


def _install(handler, monkeypatch):
    from polymarket_us import PolymarketUS

    client = PolymarketUS(gateway_base_url="https://gateway.test",
                          api_base_url="https://api.test",
                          key_id=_FAKE_KEY_ID, secret_key=_FAKE_SECRET,
                          **venue_sdk.client_kwargs())
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    got = pmus._install_request_gate(client)
    assert got["installed"] is True, got
    monkeypatch.setattr(pmus, "_client", client, raising=False)


def test_the_timing_record_survives_a_timeout(monkeypatch):
    """Gate holds 1 s, then the venue takes 11 s; the budget is 4 s.

    The await gives up at the budget -- and the record of where those 4 s
    went (1 s in our gate, the rest at the venue) is in the refusal and in
    the diagnostic the heartbeat's `venue_errors` carries.
    """
    release = threading.Event()
    dispatched = []

    def handler(request):
        dispatched.append(time.time())
        release.wait(11.0)
        return httpx.Response(200, json={"marketData": BOOK},
                              request=request)

    _install(handler, monkeypatch)

    async def one():
        try:
            t0 = time.time()
            GRT.hold_until(until_epoch_s=t0 + 1.0, reason="TEST_HOLD")
            out = await loop.venue_quote(
                _Conn(), us_slug=SLOW, intent=LONG,
                freshness_deadline_epoch_s=(
                    t0 + 4.0 + loop.BOOK_READ_DEADLINE_MARGIN_S))
            return out, time.time() - t0
        finally:
            release.set()

    out, took = asyncio.run(one())

    assert out["refusal"] == loop.R_BOOK_READ_DEADLINE, out
    assert took <= 4.0 + SLACK_S, took
    t = out["read_timing"]
    assert t["read_id"]
    assert t["deadline_s"] == pytest.approx(4.0, abs=0.2)
    assert t["gate_wait_s"] == pytest.approx(1.0, abs=0.3)
    assert t["venue_s"] == pytest.approx(3.0, abs=0.5)
    assert t["dispatched"] == 1 and len(dispatched) == 1
    assert out["diagnostic"]["read_timing"] == t
    assert out["diagnostic"]["stage"] == "BOOK_READ_AWAIT"


def test_a_gate_hold_past_the_budget_is_refused_by_name_with_no_request(
        monkeypatch):
    """The gate used to hold an undeadlined read for up to 20 s and surface
    as an anonymous TimeoutError. With the deadline it refuses at once."""
    dispatched = []

    def handler(request):
        dispatched.append(time.time())
        return httpx.Response(200, json={"marketData": BOOK},
                              request=request)

    _install(handler, monkeypatch)
    GRT.hold_until(until_epoch_s=time.time() + 15.0, reason="TEST_HOLD")

    t0 = time.time()
    out = asyncio.run(loop.venue_quote(
        _Conn(), us_slug=SLOW, intent=LONG,
        freshness_deadline_epoch_s=time.time() + 6.0))
    took = time.time() - t0

    assert dispatched == []
    assert took < 1.0, took
    assert out["refusal"] == loop.R_BOOK_READ_DEADLINE
    assert out["gate_refusal"] == GRT.R_COOLDOWN_EXCEEDS_DEADLINE
    assert out["read_timing"]["dispatched"] == 0
    assert out["read_timing"]["gate_refusals"][0]["refusal"] == \
        GRT.R_COOLDOWN_EXCEEDS_DEADLINE
