"""R30A INCIDENT REPAIR: A PAPER ENTRY IS FILLED ON A BOOK, NOT KILLED BY OUR
OWN REFUSAL TO READ ONE.

Production, 7 days to 2026-10-04 20:54Z (research-sql run 37233864395):
100 of the 168 paper entry orders expired as THE_OBSERVED_BOOK_WAS_UNREADABLE
-- the first "observation" in their window was an errored read (our venue
request gate declining to wait out a cooldown past an exhausted pass
deadline, or the pass deadline itself), and the simulator released the order
on it. These tests pin the repaired behaviour:

  * the simulator skips errored reads (evidence of nothing) and fills on the
    FIRST READABLE book in [eligible_at, expires_at]; with only errored reads
    it expires at the TTL as NO_READABLE_BOOK_OBSERVED_BEFORE_THE_ORDER_
    EXPIRED -- never a fill, the limit / delay / TTL / walk unchanged;
  * a read made for a pending entry must be RECEIVED at or after its eligible
    instant (the 6 s shared-read cache may not answer with an older receipt);
  * the books step does not read an entry before it is eligible;
  * step_after_delay does not record an errored read when the pass has no
    time left;
  * the dedicated entry-fill read waits for the eligible instant, reads once
    and simulates the order on that book;
  * a production-shaped replay (order paperord:6add79e6c58fa041e7ec62c1, its
    recorded book obs 28790, 24.55 s after eligibility) now fills;
  * no economic or risk threshold moved.
"""
from __future__ import annotations

import time

import pytest

from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_session as S
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


def SL(a, n):
    return "%s:%s" % (a["account_id"], n)


async def _errored(conn, slug, at, err="VENUE_COOLDOWN_EXCEEDS_THE_DECISION_DEADLINE",
                   basis="ENTRY_AFTER_DELAY"):
    got = await SIM.record_book(conn, slug=slug, read={
        "marketData": None, "error": err, "observed_at": at},
        source="PAPER_MARKET_DATA_CLIENT", read_basis=basis)
    return got["obs_id"]


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


# ── the simulator ───────────────────────────────────────────────────
@pg
async def test_an_errored_first_read_does_not_kill_the_order_the_next_readable_book_fills_it():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "errfirst")
        s = SL(a, "m")
        o = H.order(a, key="e1", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        await _errored(conn, s, H.T0 + 3.0)
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 4.0,
                                     fee_fn=H.zero_fee)
        # BEFORE R30A: EXPIRED / THE_OBSERVED_BOOK_WAS_UNREADABLE here.
        assert r["pending"] and r["state"] == "PENDING_SIMULATION"
        assert r["refusal"] == SIM.R_NO_BOOK_YET
        assert r["unreadable_reads_in_window"] == 1
        await H.observe(conn, s, H.T0 + 20.0, offers=[(0.50, 60), (0.49, 0)])
        await H.observe(conn, s, H.T0 + 21.0, offers=[(0.30, 1000)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 22.0,
                                     fee_fn=H.zero_fee)
        # the FIRST readable book in the window is the one used (not the
        # cheaper later one), its displayed depth only, IOC remainder off
        assert r["filled_qty"] == 60.0 and r["state"] == "CANCELED"
        f = await conn.fetchrow("SELECT * FROM paper_fills WHERE order_id=$1",
                                oid)
        assert float(f["price"]) == 0.50
        assert L._epoch(f["book_observed_at"]) == pytest.approx(H.T0 + 20.0)
    finally:
        await conn.close()


@pg
async def test_only_errored_reads_in_the_window_expire_at_the_ttl_with_a_precise_reason():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "erronly")
        s = SL(a, "m")
        o = H.order(a, key="e2", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=30.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        await _errored(conn, s, H.T0 + 3.0)
        await _errored(conn, s, H.T0 + 10.0,
                       err="PAPER_BOOK_READ_DEADLINE_EXCEEDED",
                       basis="OPEN_ORDER_OR_POSITION")
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 20.0,
                                     fee_fn=H.zero_fee)
        assert r["pending"]
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 31.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED"
        assert r["refusal"] == SIM.R_NO_READABLE_BOOK_IN_WINDOW
        assert r["unreadable_reads_in_window"] == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_fills WHERE order_id=$1", oid) == 0
        row = await conn.fetchrow("SELECT terminal_reason FROM paper_orders "
                                  " WHERE order_id=$1", oid)
        assert row["terminal_reason"] == SIM.R_NO_READABLE_BOOK_IN_WINDOW
        b = await L.balances(conn, a["account_id"], now=H.T0 + 32)
        assert b["reserved_usd"] == 0.0 and b["cash_usd"] == 500000.0
        # no read at all: the original reason stands
        o3 = H.order(a, key="e3", qty=10, limit=0.50, slug=SL(a, "n"),
                     at=H.T0, delay=2.0, ttl=30.0)
        oid3 = (await L.submit_order(conn, o3, fee_fn=H.zero_fee,
                                     now=H.T0))["order"]["order_id"]
        r = await SIM.simulate_order(conn, oid3, now=H.T0 + 31.0,
                                     fee_fn=H.zero_fee)
        assert r["refusal"] == SIM.R_NO_BOOK_IN_WINDOW
    finally:
        await conn.close()


@pg
async def test_the_limit_the_delay_and_the_ttl_are_unchanged():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "errlimit")
        s = SL(a, "m")
        o = H.order(a, key="l1", qty=100, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=30.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        # a readable book BEFORE eligibility is never used
        await H.observe(conn, s, H.T0 + 1.0, offers=[(0.40, 1000)])
        await _errored(conn, s, H.T0 + 2.5)
        # the first readable book in the window is above the limit
        await H.observe(conn, s, H.T0 + 5.0, offers=[(0.55, 1000)])
        await H.observe(conn, s, H.T0 + 6.0, offers=[(0.45, 1000)])
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 7.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED"
        assert r["refusal"] == SIM.R_NOTHING_WITHIN_LIMIT
        # a readable book after the TTL is never used
        o2 = H.order(a, key="l2", qty=100, limit=0.50, slug=SL(a, "q"),
                     at=H.T0, delay=2.0, ttl=30.0)
        oid2 = (await L.submit_order(conn, o2, fee_fn=H.zero_fee,
                                     now=H.T0))["order"]["order_id"]
        await _errored(conn, SL(a, "q"), H.T0 + 3.0)
        await H.observe(conn, SL(a, "q"), H.T0 + 31.0, offers=[(0.40, 1000)])
        r = await SIM.simulate_order(conn, oid2, now=H.T0 + 32.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "EXPIRED" and not r.get("fills")
        assert r["refusal"] == SIM.R_NO_READABLE_BOOK_IN_WINDOW
    finally:
        await conn.close()


#: PRODUCTION-SHAPED: order paperord:6add79e6c58fa041e7ec62c1
#: (PINNACLE_COMPLETED_GAME_PAPER, BUY LONG 1666 @ limit 0.58) expired on an
#: errored first read; its window later held readable book obs 28790,
#: 24.55 s after eligibility (research-sql run 37233864395, E4). The levels
#: below are that row's first four offers / three bids, verbatim.
PROD_OFFERS = [
    {"px": {"value": "0.5800", "currency": "USD"}, "qty": "51461.8700"},
    {"px": {"value": "0.5900", "currency": "USD"}, "qty": "1124.3700"},
    {"px": {"value": "0.6000", "currency": "USD"}, "qty": "71245.9700"},
    {"px": {"value": "0.6100", "currency": "USD"}, "qty": "34506.0000"}]
PROD_BIDS = [
    {"px": {"value": "0.5700", "currency": "USD"}, "qty": "70900.0800"},
    {"px": {"value": "0.5600", "currency": "USD"}, "qty": "29893.0000"},
    {"px": {"value": "0.5500", "currency": "USD"}, "qty": "1897.5100"}]


@pg
async def test_production_shaped_replay_of_an_order_the_old_rule_expired():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "prodreplay")
        s = SL(a, "phi-atl")
        o = H.order(a, key="p1", qty=1666, limit=0.58, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        await _errored(conn, s, H.T0 + 2.0 + 3.0)
        await SIM.record_book(conn, slug=s, read={
            "marketData": {"offers": PROD_OFFERS, "bids": PROD_BIDS},
            "observed_at": H.T0 + 2.0 + 24.55},
            source="PAPER_MARKET_DATA_CLIENT", read_basis="OPEN_ORDER_OR_POSITION")
        r = await SIM.simulate_order(conn, oid, now=H.T0 + 30.0,
                                     fee_fn=H.zero_fee)
        assert r["state"] == "FILLED" and r["filled_qty"] == 1666.0
        f = await conn.fetchrow("SELECT price, qty FROM paper_fills "
                                " WHERE order_id=$1", oid)
        assert float(f["price"]) == 0.58 and float(f["qty"]) == 1666.0
    finally:
        await conn.close()


# ── the read must postdate the eligible instant ─────────────────────
def test_a_shared_read_received_before_not_before_does_not_answer(monkeypatch):
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    shared = {"marketData": {"offers": [], "bids": []}, "observed_at": 100.0,
              "shared_read": True}
    fresh = {"marketData": {"offers": [], "bids": []}, "observed_at": 200.0}
    calls = []
    monkeypatch.setattr(LOOP, "recent_book",
                        lambda slug, max_age_s: dict(shared))

    def blocking(slug, deadline_epoch_s=None):
        calls.append((slug, deadline_epoch_s))
        return dict(fresh)
    monkeypatch.setattr(LOOP, "_read_book_blocking", blocking)
    assert G._default_transport("s")["observed_at"] == 100.0
    assert G._default_transport("s", not_before_epoch=50.0)[
        "observed_at"] == 100.0
    got = G._default_transport("s", not_before_epoch=150.0,
                               deadline_epoch_s=999.0)
    assert got["observed_at"] == 200.0 and calls == [("s", 999.0)]


async def test_the_client_passes_not_before_only_to_a_transport_that_takes_it():
    seen = {}

    def modern(slug, *, deadline_epoch_s=None, not_before_epoch=None):
        seen.update(slug=slug, nb=not_before_epoch, dl=deadline_epoch_s)
        return {"marketData": {}, "observed_at": 1.0}

    def legacy(slug):
        seen.update(legacy=slug)
        return {"marketData": {}, "observed_at": 1.0}
    await G.PaperMarketDataClient(modern).read_book(
        "x", not_before_epoch=42.0, deadline_epoch_s=9.0, timeout_s=5.0)
    assert seen["nb"] == 42.0 and seen["dl"] == 9.0
    await G.PaperMarketDataClient(legacy).read_book("y",
                                                    not_before_epoch=42.0)
    assert seen["legacy"] == "y"
    assert G._accepts(G._default_transport, "not_before_epoch")
    assert G._accepts_deadline(G._default_transport)


# ── the pass ────────────────────────────────────────────────────────
class _NBTransport:
    """A transport that records the not_before it was given and answers at
    a controlled receipt instant."""

    def __init__(self, t, md):
        self.t = float(t)
        self.md = md
        self.calls = []

    def __call__(self, slug, *, deadline_epoch_s=None, not_before_epoch=None):
        self.calls.append((slug, not_before_epoch))
        return {"marketData": dict(self.md), "observed_at": self.t}


def _ctx(a, *, md, at, deadline_s=30.0):
    return {"account_id": a["account_id"], "session_id": a["session_id"],
            "config": a["config"], "market_data": md, "books_read": 0,
            "deadline": time.monotonic() + deadline_s, "now": at,
            "clock": _Clock(at), "first_fills": [], "fills": 0,
            "pending_entries": [], "sleep": _nosleep}


async def _nosleep(_s):
    return None


@pg
async def test_the_books_step_does_not_read_an_entry_before_it_is_eligible():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "booksstep")
        s = SL(a, "m")
        o = H.order(a, key="b1", qty=10, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        await L.submit_order(conn, o, fee_fn=H.zero_fee, now=H.T0)
        t = _NBTransport(H.T0 + 0.5, H.md(offers=[(0.50, 100)]))
        ctx = _ctx(a, md=G.PaperMarketDataClient(t), at=H.T0 + 0.5)
        got = await PR.step_books(conn, ctx)
        assert t.calls == [] and got["deferred_until_eligible"] == 1
        # once eligible it is read first, and told its eligible instant
        t.t = H.T0 + 3.0
        ctx = _ctx(a, md=G.PaperMarketDataClient(t), at=H.T0 + 3.0)
        got = await PR.step_books(conn, ctx)
        assert t.calls and t.calls[0][0] == s
        assert t.calls[0][1] == pytest.approx(H.T0 + 2.0)
        assert got["deferred_until_eligible"] == 0
    finally:
        await conn.close()


@pg
async def test_after_delay_records_no_errored_read_when_the_pass_has_no_time_left():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "nolefttime")
        s = SL(a, "m")
        o = H.order(a, key="n1", qty=10, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        t = _NBTransport(H.T0 + 2.5, H.md(offers=[(0.50, 100)]))
        ctx = _ctx(a, md=G.PaperMarketDataClient(t), at=H.T0 + 2.5,
                   deadline_s=0.5)
        got = await PD.step_after_delay(conn, ctx)
        assert t.calls == [] and got["books"] == 0
        assert got["deferred_past_the_pass_deadline"] >= 1
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_book_observations "
            " WHERE us_market_slug=$1", s) == 0
        st = await conn.fetchval("SELECT state FROM paper_orders "
                                 " WHERE order_id=$1", oid)
        assert st == "PENDING_SIMULATION"
        # with time, it waits to eligibility, reads with not_before, fills
        ctx = _ctx(a, md=G.PaperMarketDataClient(t), at=H.T0 + 1.0)
        got = await PD.step_after_delay(conn, ctx)
        assert t.calls == [(s, pytest.approx(H.T0 + 2.0))]
        assert got["results"][0]["state"] == "FILLED"
    finally:
        await conn.close()


@pg
async def test_the_entry_fill_read_waits_for_eligibility_reads_once_and_fills():
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "entryfill")
        s = SL(a, "m")
        o = H.order(a, key="f1", qty=10, limit=0.50, slug=s, at=H.T0,
                    delay=2.0, ttl=90.0)
        oid = (await L.submit_order(conn, o, fee_fn=H.zero_fee,
                                    now=H.T0))["order"]["order_id"]
        slept = []
        clk = _Clock(H.T0 + 0.2)

        async def sleep(sec):
            slept.append(sec)
            clk.t += sec
        t = _NBTransport(H.T0 + 2.3, H.md(offers=[(0.50, 100)]))
        got = await PR.entry_fill(conn, [oid],
                                  market_data=G.PaperMarketDataClient(t),
                                  clock=clk, sleep=sleep, fee_fn=H.zero_fee)
        assert slept == [pytest.approx(1.8)]
        assert t.calls == [(s, pytest.approx(H.T0 + 2.0))]
        assert got["read"] == 1 and got["errors"] == 0
        assert got["results"][0]["state"] == "FILLED"
        b = await conn.fetchrow(
            "SELECT read_basis, error FROM paper_book_observations "
            " WHERE us_market_slug=$1", s)
        assert b["read_basis"] == PR.ENTRY_FILL_READ_BASIS
        assert b["error"] is None
        # idempotent: a second run finds nothing pending and reads nothing
        again = await PR.entry_fill(conn, [oid],
                                    market_data=G.PaperMarketDataClient(t),
                                    clock=clk, sleep=sleep)
        assert again["read"] == 0 and len(t.calls) == 1
    finally:
        await conn.close()


def test_the_entry_fill_read_is_bounded():
    assert PR.ENTRY_FILL_CONCURRENCY <= 2
    assert PR.ENTRY_FILL_BUDGET_PER_HOUR <= 240
    assert PR.ENTRY_FILL_MAX_WAIT_S <= 30.0
    assert PR.schedule_entry_fill([])["why"] == "NO_ORDERS"
    # outside a running loop nothing is started
    assert PR.schedule_entry_fill(["paperord:x"])["scheduled"] is False


def test_no_economic_or_risk_threshold_moved():
    """The repair is plumbing only: the session's frozen execution model and
    every gate keep their values (2 s delay, 90 s marketable TTL, the 30 s
    Pinnacle rule, the per-order cap, the hedge reserve)."""
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    cfg = S.default_config()
    assert cfg["simulator"]["decision_to_execution_delay_s"] == 2.0
    assert cfg["simulator"]["marketable_ttl_s"] == 90.0
    assert cfg["entry"]["pinnacle_max_age_s"] == 30.0
    assert LOOP.PINNACLE_MAX_AGE_S == 30.0
    assert cfg["risk"]["per_order_cap_usd"] == 5000.0
    assert cfg["risk"]["hedge_reserve_fraction"] == 0.20
    assert cfg["cadence"]["max_book_reads_per_pass"] == 12
    assert cfg["cadence"]["max_decisions_per_pass"] == 40
    from sportsassets.agents import paper_benchmark as PB
    assert PB.CG_MIN_EDGE_PP_V2 == 0.5 and PB.BOOK_MAX_AGE_S == 10.0
