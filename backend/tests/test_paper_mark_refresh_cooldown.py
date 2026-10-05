"""CAPITAL-CRITICAL: THE HELD-MARK REFRESH WAITS FOR THE VENUE INSTEAD OF
BURNING ITS RUN ON REFUSALS, AND USES A PUSHED BOOK ONLY AT THE SLA.

The production defect (2026-10-05, runs 1-21): the authenticated book
endpoint answered 429 with Retry-After 7-10 s, the request gate refused every
read whose 8 s deadline could not outlast the hold, and each run spent its 90
reads in under a second with 0-1 OK. Over a seeded paper book (SYNTHETIC data
in a scratch test database; fake venue, fake gate, fake sleep -- no network,
no order):

  * a hold that fits inside the run is SLEPT OUT and the reads then succeed;
  * a hold longer than MAX_COOLDOWN_WAIT_S (or than the run has left) stops
    the REST lane: no venue request, no refusal row, every remaining due
    market recorded SKIPPED_VENUE_COOLDOWN and classified FEED_GAP with that
    reason -- never fresh;
  * a pushed (stream) book that passes the stream's own eligibility at the
    SLA bounds is recorded with ITS receipt instant and needs no request; a
    market with no eligible pushed book falls to the REST lane; an
    ineligible book (stale source, stale receipt, old connection) is never
    used; a pushed book no newer than the last successful read is not
    recorded again.
"""
from __future__ import annotations

import pytest

from sportsassets import bettor_paper_freshness as PMF
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_simulator as SIM
from sportsassets.agents import paper_mark_refresh as PMR

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
STRAT = "PINNACLE_COMPLETED_GAME_PAPER"


class _Clock:
    def __init__(self, t):
        self.t = float(t)

    def __call__(self):
        return self.t


class _Venue:
    def __init__(self, clock):
        self.clock = clock
        self.calls = []

    def __call__(self, slug):
        self.calls.append(slug)
        px = 0.40 + 0.01 * (len(self.calls) % 3)
        return {"marketData": H.md(bids=[(px, 100)], offers=[(px + 0.02, 100)]),
                "observed_at": self.clock()}


class _Gate:
    """Blocking for `hold` seconds until the fake sleep has slept it out."""

    def __init__(self, hold, clock, *, reason="VENUE_429_ON_BOOK_READ"):
        self.until = clock() + float(hold)
        self.clock = clock
        self.reason = reason
        self.checks = 0

    def __call__(self):
        self.checks += 1
        left = self.until - self.clock()
        return {"blocking": left > 0, "seconds_left": max(0.0, left),
                "reason": self.reason}


def _sleeper(clock, log):
    async def _sleep(s):
        log.append(s)
        clock.t += float(s)
    return _sleep


async def _seed(conn, a, n, *, at=H.T0, tag="c"):
    slugs = []
    for i in range(n):
        slug = "%s:%s%03d" % (a["account_id"], tag, i)
        o = H.order(a, key="%s%d" % (tag, i), qty=10, limit=0.50, slug=slug,
                    at=at, fixture="fx-%s-%d" % (tag, i),
                    group_id="paper_g_%s_%s%d" % (a["account_id"][-8:], tag,
                                                  i))
        o["strategy"] = STRAT
        got = await L.submit_order(conn, o, fee_fn=H.zero_fee, now=at)
        assert got["ok"], got
        await H.observe(conn, slug, at + 3, offers=[(0.50, 10)],
                        bids=[(0.48, 10)])
        f = await SIM.simulate_order(conn, got["order"]["order_id"],
                                     now=at + 4, fee_fn=H.zero_fee)
        assert f.get("filled_qty") or f.get("state") == "FILLED", f
        slugs.append(slug)
    return slugs


def _no_recent(*_a, **_k):
    return None


@pg
async def test_a_short_venue_hold_is_waited_out_and_the_reads_succeed():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "cdshort")
        slugs = await _seed(conn, a, 12)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        gate = _Gate(9.0, clk)
        slept = []
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                gate=gate, sleep=_sleeper(clk, slept),
                                stream=None)
        assert got.get("error") is None, got
        # the hold (Retry-After 9 s) was slept once, then every read went out
        assert len(slept) == 1 and 9.0 <= slept[0] <= 9.1
        assert got["cooldown_waited_s"] >= 9.0
        assert got["read_attempted"] == 12 and got["read_ok"] == 12
        assert got["skipped_cooldown"] == 0 and got["skipped_budget"] == 0
        assert sorted(v.calls) == sorted(slugs)
        rows = await PMF.classify_positions(conn, a["account_id"],
                                            now=clk.t + 1)
        s = PMF.summarize(rows)
        assert s["fresh_rate"] == 1.0
    finally:
        await conn.close()


@pg
async def test_a_long_hold_stops_the_rest_lane_and_records_every_skip():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "cdlong")
        slugs = await _seed(conn, a, 10)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        gate = _Gate(60.0, clk)
        slept = []
        before = await conn.fetchval(
            "SELECT count(*) FROM paper_book_observations "
            " WHERE us_market_slug = ANY($1::text[])", slugs)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                gate=gate, sleep=_sleeper(clk, slept),
                                stream=None)
        assert got.get("error") is None, got
        # no request, no sleep, no refusal row: the venue said wait 60 s
        assert v.calls == [] and slept == []
        assert got["read_attempted"] == 0
        assert got["skipped_cooldown"] == 10 and got["skipped_budget"] == 10
        after = await conn.fetchval(
            "SELECT count(*) FROM paper_book_observations "
            " WHERE us_market_slug = ANY($1::text[])", slugs)
        assert after == before
        oc = got["outcomes"]
        assert {x["outcome"] for x in oc.values()} == {PMR.O_SKIP_COOLDOWN}
        assert all("VENUE_429_ON_BOOK_READ" in x["why"] for x in oc.values())
        # recorded in the run row, so the classifier explains every gap
        run = await conn.fetchrow(
            "SELECT * FROM paper_mark_refresh_runs WHERE run_id = $1",
            got["run_id"])
        assert run["skipped_budget"] == 10 and run["read_attempted"] == 0
        rows = await PMF.classify_positions(conn, a["account_id"],
                                            now=now + 1)
        assert {r["class"] for r in rows} == {PMF.FEED_GAP}
        assert all(r["reason"].startswith(PMR.O_SKIP_COOLDOWN) for r in rows)
        assert PMF.summarize(rows)["fresh_rate"] == 0.0
    finally:
        await conn.close()


@pg
async def test_a_hold_longer_than_the_run_has_left_stops_the_lane():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "cdroom")
        await _seed(conn, a, 4)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        slept = []
        # 9 s is inside MAX_COOLDOWN_WAIT_S but the run has 10 s in all:
        # a wait plus a full read (8 s + reserve) does not fit
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                run_budget_s=10.0, gate=_Gate(9.0, clk),
                                sleep=_sleeper(clk, slept), stream=None)
        assert v.calls == [] and slept == []
        assert got["skipped_cooldown"] == 4
    finally:
        await conn.close()


class _Stream:
    """A pushed-book source: `books` maps slug -> receipt age (s), or None
    for "no eligible book"."""

    def __init__(self, books, *, clock):
        self.books = dict(books)
        self.clock = clock
        self.wanted = []

    def want(self, slugs):
        self.wanted.append(sorted(slugs))
        return {"queued": len(slugs)}

    def book(self, slug, *, now):
        age = self.books.get(slug)
        if age is None:
            return None
        return {"marketData": H.md(bids=[(0.44, 50)], offers=[(0.46, 50)]),
                "observed_at": float(now) - float(age),
                "stream_receipt_age_s": float(age)}


@pg
async def test_pushed_books_are_recorded_with_their_receipt_and_need_no_read():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "cdstream")
        slugs = await _seed(conn, a, 6)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        # four markets have an eligible pushed book; two have none
        st = _Stream({s: 4.0 for s in slugs[:4]}, clock=clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                gate=lambda: {"blocking": False},
                                sleep=_sleeper(clk, []), stream=st)
        assert got.get("error") is None, got
        assert got["stream"] == "RUNNING"
        # every held market was asked for on the subscription
        assert st.wanted == [sorted(slugs)]
        assert got["stream_books"] == 4 and got["harvested"] == 4
        # the two without a pushed book went to the venue, nothing else did
        assert sorted(v.calls) == sorted(slugs[4:])
        oc = got["outcomes"]
        for s in slugs[:4]:
            assert oc[s]["outcome"] == PMR.O_STREAM
            row = await conn.fetchrow(
                "SELECT observed_at, read_basis, source, error FROM "
                " paper_book_observations WHERE obs_id = $1", oc[s]["obs_id"])
            assert row["read_basis"] == PMR.STREAM_BASIS
            assert row["source"] == PMR.STREAM_SOURCE
            assert row["error"] is None
            # ITS receipt instant, not the run's
            assert abs(row["observed_at"].timestamp() - (now - 4.0)) < 1e-3
        rows = await PMF.classify_positions(conn, a["account_id"],
                                            now=now + 1)
        assert PMF.summarize(rows)["fresh_rate"] == 1.0
    finally:
        await conn.close()


@pg
async def test_an_old_pushed_book_is_not_recorded_again_nor_made_fresh():
    conn = await H.connect()
    try:
        PMR._HOURLY["reads"] = []
        a = await H.new_account(conn, "cdold")
        slugs = await _seed(conn, a, 2)
        now = H.T0 + 1000
        clk = _Clock(now)
        v = _Venue(clk)
        # the pushed book was received BEFORE the last successful read
        # (seeded at T0 + 3): it is no newer, so the REST lane reads instead
        st = _Stream({slugs[0]: now - (H.T0 + 1)}, clock=clk)
        got = await PMR.refresh(conn, account_id=a["account_id"],
                                market_data=G.PaperMarketDataClient(v),
                                now=now, clock=clk, recent=_no_recent,
                                gate=lambda: {"blocking": False},
                                sleep=_sleeper(clk, []), stream=st)
        assert got["stream_books"] == 0
        assert sorted(v.calls) == sorted(slugs)
    finally:
        await conn.close()


class _FakeStream:
    def __init__(self, out):
        self.out = out

    def book_at(self, slug, *, max_source_age_s, max_receipt_age_s):
        assert max_source_age_s == PMF.SLA_S
        assert max_receipt_age_s == PMF.SLA_S
        return dict(self.out, slug=slug)


class _FakeSub:
    def __init__(self, out):
        self.stream = _FakeStream(out)


class _FakeMsub:
    def __init__(self):
        self.asked = []

    def want(self, slugs):
        self.asked.append(list(slugs))
        return {"queued": len(slugs)}


def test_the_subscription_adapter_uses_only_eligible_books_at_the_sla():
    ok = {"eligible": True, "reason": "ELIGIBLE",
          "book": {"bids": [[0.41, 5]], "offers": [[0.43, 5]]},
          "venue_state": "MARKET_STATE_OPEN",
          "source_ts": "2026-10-05T22:00:00Z",
          "receipt_age_s": 12.5, "source_age_s": 20.0}
    msub = _FakeMsub()
    b = PMR._SubscriptionBooks(_FakeSub(ok), msub)
    got = b.book("m1", now=1000.0)
    assert got["observed_at"] == 1000.0 - 12.5
    assert got["marketData"]["bids"] == [[0.41, 5]]
    assert got["marketData"]["offers"] == [[0.43, 5]]
    assert got["marketData"]["state"] == "MARKET_STATE_OPEN"
    assert b.want(["m1", "m2"]) == {"queued": 2} and msub.asked == [["m1",
                                                                      "m2"]]
    for reason in ("STALE_SOURCE", "STALE_RECEIPT", "DISCONNECTED",
                   "NOT_OPEN", "NO_BOOK"):
        bad = dict(ok, eligible=False, reason=reason)
        assert PMR._SubscriptionBooks(_FakeSub(bad), msub).book(
            "m1", now=1000.0) is None


def test_no_subscription_in_this_process_means_no_stream():
    from sportsassets import bettor_market_subscription as MSUB
    MSUB.reset()
    assert PMR._default_stream() is None


def test_the_default_gate_reads_the_process_hold():
    from sportsassets import venue_request_gate as GRT
    import time
    GRT.clear_hold()
    try:
        assert PMR._default_gate()["blocking"] is False
        GRT.hold_until(until_epoch_s=time.time() + 30, reason="TEST_HOLD")
        g = PMR._default_gate()
        assert g["blocking"] is True and g["reason"] == "TEST_HOLD"
        assert 25 < g["seconds_left"] <= 30
    finally:
        GRT.clear_hold()
