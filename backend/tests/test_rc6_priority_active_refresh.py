"""THE PLANE RE-PROVES A QUIET PRIORITY MEMBER WITH THE VENUE'S REST BOOK
(RC6, the freshness lane; market_plane.active_refresh).

Production (pm-acceptance 37836393458, release 69a8a07e): the scorecard's
"Data freshness and latency" binds on priority_members_fresh 121/192
(0.630); the plane's 2026-10-08 20:07Z snapshot read 114 PMX + 1 REST of
186, and its census named 56 of the 72 misses
STREAM:SNAPSHOT_OLDER_THAN_THE_BOUND on quiet PREGAME candidates
(atc-fnl-han-pri-2026-10-10-han, rest_age_s 19,702) while the subscribe-all
stream was healthy: it re-sends a book only when the book changes.

  §1  judge: CURRENT only for a 200 book of THIS symbol with the venue clock,
      OPEN and not crossed; every other read is a named outcome
  §2  the budget: never more than the venue's 12 GetOrderBook reads in any
      minute, never two inside a second; a 429 holds every read
  §3  the plan, against a REAL subscribe-all Manager and its ResidentBooks:
      only members the stream refuses for snapshot currency, HELD first,
      then CANDIDATE by event start; retry waits
  §4  what counts and for how long: the plane's bound from the receipt,
      never extended; newer stream evidence about the market wins
  §5  one pass of the refresh: one read, deferrals named, the budget held
      over ten simulated minutes, a 429 stops the reads for its hold
  §6  the coverage pass counts a refresh as REST_RECOVERY labelled
      PLANE_ACTIVE_REFRESH; without it the output is RC5's exactly
  §7  the census: refreshed members current, every other miss names its
      refresh outcome, the QUIET_VALID counterfactual is reported and never
      counted
  §8  the REAL run loop against Postgres: a quiet priority member is current
      in the snapshot through one allow-listed book read (on 1c874c1f it
      stays not current)
  §9  memory: one small record per member, dropped when the member leaves
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import json
import os
import pathlib
import threading
import time

import pytest

from sportsassets import institutional_stream as IS
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane.sharded_stream import Manager
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
UTC = _dt.timezone.utc
T0 = 1_791_490_000.0
BOUND = W.FRESH_SLA_S
OPEN = "INSTRUMENT_STATE_OPEN"
HERE = pathlib.Path(__file__).resolve().parent


def AR():
    # imported where used: a base without the module fails each test on its
    # behaviour, not the whole file at collection
    from sportsassets.market_plane import active_refresh
    return active_refresh


def run(coro):
    return asyncio.run(coro)


def dt(epoch):
    return _dt.datetime.fromtimestamp(epoch, UTC)


def iso(epoch):
    return dt(epoch).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


def rest_book(sym, *, at=T0, state=OPEN, bids=(("40", "100"),),
              offers=(("41", "250"),), status=200):
    return {"read": "book", "status": status, "ms": 74.9,
            "body": {"symbol": sym, "state": state, "transactTime": iso(at),
                     "bids": [{"px": p, "qty": q} for p, q in bids],
                     "offers": [{"px": p, "qty": q} for p, q in offers]}}


class Clock:
    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        return self.t


class _T:
    """A subscribe-all transport that never opens a socket."""

    def __init__(self, b, tok, **kw):
        self.b, self.kw = b, kw
        self.subscription_mode = IS.MODE_SUBSCRIBE_ALL

    def start(self):
        pass

    def subscribe(self, x):
        pass

    def stop(self):
        pass


def plane(clock, syms):
    """A REAL subscribe-all Manager holding `syms` with their scales, its
    one connection open."""
    m = Manager(token_fn=lambda: "x", max_per_stream=1000, max_streams=1,
                subscribe_all=True, clock=clock,
                transport_factory=lambda b, tok, **kw: _T(b, tok, **kw))
    m.sync({s: 0 for s in syms}, {s: {
        "symbol": s, "priceScale": "1000", "fractionalQtyScale": "100",
        "state": OPEN} for s in syms})
    books = m.shards[0]["books"]
    books.on_connected("conn-1")
    return m, books


def update(books, s, at, *, state=OPEN, px=(400, 410)):
    books.on_update({"symbol": s, "bids": [(px[0], 100)],
                     "offers": [(px[1], 100)], "state": state,
                     "transact_time": dt(at)}, received_at=at)


def member(sym, priority, start):
    return {"contract_id": sym, "priority": priority,
            "event_start": None if start is None else dt(start)}


# ═════════════════════════════════════════════════════════════════════
# §1 judge
# ═════════════════════════════════════════════════════════════════════

def test_a_200_book_of_the_member_with_the_venue_clock_open_is_current():
    j = AR().judge("atc-fnl-han-pri-2026-10-10-han",
                   rest_book("atc-fnl-han-pri-2026-10-10-han"))
    assert j["outcome"] == AR().CURRENT
    assert j["levels"] == [1, 1] and abs(j["venue_ts"] - T0) < 1e-3
    # an empty side is omitted by the venue's JSON: still a book
    b = rest_book("s1", bids=(), offers=())
    del b["body"]["bids"], b["body"]["offers"]
    assert AR().judge("s1", b)["outcome"] == AR().CURRENT


@pytest.mark.parametrize("row,why", [
    ({"status": 429, "body": {"code": 8}}, "R_REFRESH_HTTP_429"),
    ({"status": None, "transportError": "ReadTimeout"},
     "R_REFRESH_TRANSPORT"),
    ({"status": None, "verdict": "REJECTED"}, "R_REFRESH_NO_TOKEN"),
    ({"status": 404, "body": {"code": 5}}, "R_REFRESH_NOT_200"),
    ({"status": 200, "body": ["not", "a", "book"]}, "R_REFRESH_NOT_A_BOOK"),
    ({"status": 200, "body": {"symbol": "s1", "bids": "x"}},
     "R_REFRESH_NOT_A_BOOK"),
])
def test_every_unusable_read_is_a_named_outcome(row, why):
    assert AR().judge("s1", row)["outcome"] == getattr(AR(), why)


def test_a_book_that_cannot_be_shown_current_is_never_current():
    A = AR()
    other = rest_book("another-symbol")
    assert A.judge("s1", other)["outcome"] == A.R_REFRESH_SYMBOL
    no_ts = rest_book("s1")
    del no_ts["body"]["transactTime"]
    assert A.judge("s1", no_ts)["outcome"] == A.R_REFRESH_NO_VENUE_TS
    no_state = rest_book("s1")
    del no_state["body"]["state"]
    assert A.judge("s1", no_state)["outcome"] == A.R_REFRESH_STATE_UNKNOWN
    closed = rest_book("s1", state="INSTRUMENT_STATE_CLOSED")
    assert A.judge("s1", closed)["outcome"] == A.R_REFRESH_NOT_OPEN
    crossed = rest_book("s1", bids=(("41", "1"),), offers=(("41", "1"),))
    assert A.judge("s1", crossed)["outcome"] == A.R_REFRESH_CROSSED


def test_the_refreshable_refusals_are_snapshot_currency_only():
    A = AR()
    assert A.REFRESHABLE == {
        IS.R_SNAPSHOT_OLD, IS.R_SNAPSHOT_PENDING, IS.R_GAP_CONNECTION,
        IS.R_GAP_CLOCK, IS.R_SILENT, IS.R_NO_CONNECTION, IS.R_NO_VENUE_TS}
    # evidence about the MARKET is never refreshed past
    for r in (IS.R_NOT_OPEN, IS.R_BOOK_HIDDEN, IS.R_CROSSED,
              IS.R_REFUSED_SYMBOL, IS.R_NO_SCALE, IS.R_STATE_UNKNOWN,
              IS.R_NOT_REQUESTED, IS.R_REFUSED_KEY, IS.R_GAVE_UP):
        assert r not in A.REFRESHABLE


# ═════════════════════════════════════════════════════════════════════
# §2 the budget
# ═════════════════════════════════════════════════════════════════════

def test_the_budget_is_the_venues_getorderbook_figure_never_above():
    A = AR()
    assert A.BOOK_READS_PER_MIN_MAX == 12          # GetOrderBook 12/min
    assert A.per_min({}) == 12
    assert A.per_min({"UMP_BOOK_REFRESH_PER_MIN": "99"}) == 12
    assert A.per_min({"UMP_BOOK_REFRESH_PER_MIN": "4"}) == 4
    assert A.per_min({"UMP_BOOK_REFRESH_PER_MIN": "0"}) == 1
    assert A.per_min({"UMP_BOOK_REFRESH_PER_MIN": "x"}) == 12
    assert A.ActiveRefresh(per_minute=50).per_min == 12
    # the refdata budget is a different endpoint's and is not touched
    from sportsassets.market_plane import refdata_universe as RU
    assert (RU.CALLS_PER_MIN_DEFAULT, RU.CALLS_PER_MIN_MAX) == (5, 6)
    assert A.enabled({}) and not A.enabled({"UMP_ACTIVE_REFRESH": "off"})


def test_no_sixty_second_window_ever_holds_more_than_the_budget():
    A = AR()
    ref = A.ActiveRefresh(per_minute=12)
    starts, t = [], T0
    while t < T0 + 600:
        ok, _why = ref.slot(t)
        if ok:
            ref.start(t)
            starts.append(t)
        t += 0.25
    for i, s in enumerate(starts):
        assert sum(1 for x in starts[i:] if x - s < 60.0) <= 12
        if i:
            assert s - starts[i - 1] >= A.MIN_GAP_S
    assert len(starts) >= 119          # the budget is used, not starved


def test_a_429_holds_every_read_for_its_backoff():
    A = AR()
    ref = A.ActiveRefresh()
    ref.start(T0)
    ref.record("s1", {"status": 429, "body": {"code": 8}}, at=T0 + 0.1)
    assert ref.slot(T0 + 59.0) == (False, A.R_REFRESH_HOLD_429)
    assert ref.slot(T0 + 60.2) == (True, None)
    assert ref.totals["by_outcome"] == {A.R_REFRESH_HTTP_429: 1}


# ═════════════════════════════════════════════════════════════════════
# §3 the plan (a REAL subscribe-all Manager)
# ═════════════════════════════════════════════════════════════════════

SYMS = ["held-1", "cand-inplay", "cand-soon", "cand-later", "cand-old",
        "cand-nostart", "cand-fresh", "cand-closed"]


def _members(now):
    return [member("cand-later", 10, now + 5 * 3600),
            member("cand-nostart", 10, None),
            member("cand-old", 10, now - 6 * 3600),
            member("cand-soon", 10, now + 3600),
            member("cand-fresh", 10, now + 600),
            member("cand-closed", 10, now + 600),
            member("not-in-the-books", 10, now + 60),
            member("cand-inplay", 10, now - 1800),
            member("held-1", 0, now + 3 * 3600)]


def _quiet_plane():
    """Every member last updated at T0; at T0 + 301 the stream (alive) has
    sent nothing new -- except cand-fresh, updated now, and cand-closed,
    whose newest update says the market closed."""
    clock = Clock(T0)
    m, books = plane(clock, SYMS)
    for s in SYMS:
        update(books, s, T0)
    clock.t = T0 + 301.0
    update(books, "cand-fresh", clock.t)
    update(books, "cand-closed", clock.t, state="INSTRUMENT_STATE_CLOSED")
    books.on_heartbeat()
    return clock, m, books


def test_only_snapshot_currency_misses_are_due_held_first_then_by_start():
    A = AR()
    clock, m, _books = _quiet_plane()
    now = clock.t
    assert m.current("cand-soon", now=now, max_snapshot_age_s=BOUND)[
        "refusal"] == IS.R_SNAPSHOT_OLD
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    due, counts = ref.plan(m, now=now, bound=BOUND)
    assert due == ["held-1", "cand-inplay", "cand-soon", "cand-later",
                   "cand-old", "cand-nostart"]
    assert counts == {"STREAM_CURRENT": 1,
                      "STREAM_REFUSAL_NOT_REFRESHABLE": 1,
                      "NOT_HELD_BY_THE_PLANE_BOOKS": 1}


def test_a_refreshed_member_is_re_read_only_near_the_bound():
    A = AR()
    clock, m, books = _quiet_plane()
    now = clock.t
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    ref.record("held-1", rest_book("held-1"), at=now)
    due, counts = ref.plan(m, now=now + 284.0, bound=BOUND)
    assert "held-1" not in due and counts["REFRESH_CURRENT"] == 1
    due, counts = ref.plan(m, now=now + 286.0, bound=BOUND)
    assert due[0] == "held-1"
    assert counts["REFRESH_CURRENT_DUE_FOR_RE_READ"] == 1


def test_a_failed_read_waits_to_retry_and_a_closed_market_waits_longer():
    A = AR()
    clock, m, books = _quiet_plane()
    now = clock.t
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    ref.record("held-1", {"status": 503, "body": None}, at=now)
    ref.record("cand-soon", rest_book("cand-soon",
                                      state="INSTRUMENT_STATE_SUSPENDED"),
               at=now)
    due, counts = ref.plan(m, now=now + 59.0, bound=BOUND)
    assert "held-1" not in due and "cand-soon" not in due
    assert counts[A.R_REFRESH_RETRY_WAIT] == 2
    due, _c = ref.plan(m, now=now + 61.0, bound=BOUND)
    assert due[0] == "held-1" and "cand-soon" not in due
    due, _c = ref.plan(m, now=now + 901.0, bound=BOUND)
    assert "cand-soon" in due


# ═════════════════════════════════════════════════════════════════════
# §4 what counts, and for how long
# ═════════════════════════════════════════════════════════════════════

def test_a_refresh_counts_for_the_bound_from_its_receipt_never_longer():
    A = AR()
    clock, m, books = _quiet_plane()
    now = clock.t
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    ref.record("cand-soon", rest_book("cand-soon"), at=now)
    ref.record("cand-later", {"status": 503}, at=now)
    for dt_ in (0.0, 150.0, 300.0):
        books.on_heartbeat()
        assert ref.current(m, now=now + dt_, bound=BOUND) == {
            "cand-soon": now}
    assert ref.current(m, now=now + 300.5, bound=BOUND) == {}
    # the bound is the caller's (the plane's 300 s), never widened here
    assert W.FRESH_SLA_S == 300.0 == IS.HELD_MARK_MAX_SNAPSHOT_AGE_S


def test_newer_stream_evidence_about_the_market_wins_over_a_refresh():
    A = AR()
    clock, m, books = _quiet_plane()
    now = clock.t
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    ref.record("cand-soon", rest_book("cand-soon"), at=now)
    ref.record("cand-later", rest_book("cand-later"), at=now)
    clock.t = now + 10.0
    # the stream says cand-soon's market closed; cand-later moved (stream
    # current again: counted as the stream's, not the refresh's)
    update(books, "cand-soon", clock.t, state="INSTRUMENT_STATE_CLOSED")
    update(books, "cand-later", clock.t)
    assert ref.current(m, now=clock.t, bound=BOUND) == {}


def test_a_newer_rest_word_about_the_market_ends_an_earlier_current_read():
    A = AR()
    clock, m, books = _quiet_plane()
    now = clock.t
    ref = A.ActiveRefresh()
    ref.set_members(_members(now), now=now)
    for s in ("cand-soon", "cand-later", "cand-old"):
        ref.record(s, rest_book(s), at=now)
    # re-reads near the bound: a failed read says nothing about the book (the
    # earlier read stands to its own bound); a closed market ends it now
    ref.record("cand-later", {"status": 503}, at=now + 290.0)
    ref.record("cand-old", rest_book("cand-old", state="INSTRUMENT_STATE_"
                                     "CLOSED"), at=now + 290.0)
    clock.t = now + 295.0
    books.on_heartbeat()
    assert m.current("cand-old", now=clock.t, max_snapshot_age_s=BOUND)[
        "refusal"] == IS.R_SNAPSHOT_OLD
    assert ref.current(m, now=clock.t, bound=BOUND) == {
        "cand-soon": now, "cand-later": now}


# ═════════════════════════════════════════════════════════════════════
# §5 one pass of the refresh
# ═════════════════════════════════════════════════════════════════════

class _Pool:
    def __init__(self, rows):
        self.rows, self.fetches = rows, []

    def acquire(self):
        pool = self

        class A:
            async def __aenter__(self_):
                return pool

            async def __aexit__(self_, *a):
                return False
        return A()

    async def fetch(self, sql, *args):
        self.fetches.append((sql, args))
        assert "FROM market_plane_registry" in sql and "priority <= $1" in sql
        assert args[0] == POP.P_CANDIDATE
        return self.rows


def test_one_pass_reads_one_book_in_order_and_names_the_deferrals():
    A = AR()
    clock, m, books = _quiet_plane()
    pool = _Pool(_members(clock.t))
    ref = A.ActiveRefresh()
    reads = []

    def read(s):
        reads.append(s)
        clock.t += 0.075
        return rest_book(s)
    got = run(A.step(pool, None, ref, m, bound=BOUND, clock=clock,
                     read=read))
    assert reads == ["held-1"]
    assert got["due"] == 6 and got["read"] == 1 and got["deferred"] == 5
    assert got["deferred_why"] == "MAX_READS_PER_PASS"
    assert got["reads"][0]["outcome"] == A.CURRENT
    # half a second later the gap holds; a second later the next in order
    clock.t += 0.5
    got = run(A.step(pool, None, ref, m, bound=BOUND, clock=clock,
                     read=read))
    assert got["read"] == 0 and got["deferred_why"] == A.R_REFRESH_BUDGET
    clock.t += 1.0
    run(A.step(pool, None, ref, m, bound=BOUND, clock=clock, read=read))
    assert reads == ["held-1", "cand-inplay"]
    assert len(pool.fetches) == 1          # members re-read every 30 s only
    d = ref.digest(now=clock.t, bound=BOUND, mgr=m)
    assert d["current_via_refresh"] == 2
    assert d["current_via_refresh_by_tier"] == {"HELD": 1, "CANDIDATE": 1}
    assert d["budget"]["per_min"] == 12 and d["totals"]["reads"] == 2


@pytest.mark.parametrize("n,expect_current", [(40, (40, 40)),
                                              (80, (55, 60))])
def test_ten_minutes_of_passes_never_exceed_the_budget(n, expect_current):
    """40 quiet members fit the budget (12 a minute x 300 s = 60 at once)
    and are all kept current; 80 do not, and the budget is never exceeded
    to make them: at most 60 are current at once."""
    A = AR()
    clock = Clock(T0)
    syms = ["quiet-%02d" % i for i in range(n)]
    m, books = plane(clock, syms)
    for s in syms:
        update(books, s, T0)
    clock.t = T0 + 400.0
    pool = _Pool([member(s, 10, T0 + 3600 + i) for i, s in enumerate(syms)])
    ref = A.ActiveRefresh()
    starts, per = [], {}

    def read(s):
        starts.append(clock.t)
        per.setdefault(s, []).append(clock.t)
        return rest_book(s)
    end = clock.t + 600.0
    while clock.t < end:
        books.on_heartbeat()
        run(A.step(pool, None, ref, m, bound=BOUND, clock=clock, read=read))
        clock.t += 2.0                     # the plane's pass interval
    for i, s in enumerate(starts):
        assert sum(1 for x in starts[i:] if x - s < 60.0) <= 12
    assert len(starts) <= 121
    if n == 80:
        assert len(starts) >= 119          # saturated: the whole budget
    # a member is re-read only once its refresh nears the bound
    for s, ts in per.items():
        assert all(b - a >= BOUND - A.REFRESH_LEAD_S
                   for a, b in zip(ts, ts[1:])), (s, ts)
    assert ref.totals["current"] == len(starts)
    lo, hi = expect_current
    assert lo <= len(ref.current(m, now=clock.t, bound=BOUND)) <= hi


def test_a_429_stops_the_reads_for_its_hold():
    A = AR()
    clock, m, books = _quiet_plane()
    pool = _Pool(_members(clock.t))
    ref = A.ActiveRefresh()
    reads = []

    def read(s):
        reads.append(s)
        return {"status": 429, "body": {"code": 8}}
    got = run(A.step(pool, None, ref, m, bound=BOUND, clock=clock,
                     read=read))
    assert got["reads"][0]["outcome"] == A.R_REFRESH_HTTP_429
    for _ in range(29):
        clock.t += 2.0
        books.on_heartbeat()
        got = run(A.step(pool, None, ref, m, bound=BOUND, clock=clock,
                         read=read))
        assert got["read"] == 0
    assert got["deferred_why"] == A.R_REFRESH_HOLD_429 and reads == ["held-1"]


def test_a_member_list_read_failure_keeps_the_last_list():
    A = AR()
    clock, m, books = _quiet_plane()
    ref = A.ActiveRefresh()
    ref.set_members(_members(clock.t), now=clock.t - 31.0)

    class Bad(_Pool):
        async def fetch(self, sql, *args):
            raise RuntimeError("db away")
    got = run(A.step(Bad([]), None, ref, m, bound=BOUND, clock=clock,
                     read=lambda s: rest_book(s)))
    assert got["members_error"] == "MEMBERS_READ_FAILED:RuntimeError"
    assert got["read"] == 1 and len(ref.members) == 9


# ═════════════════════════════════════════════════════════════════════
# §6 the coverage pass
# ═════════════════════════════════════════════════════════════════════

def _mem_bound_module():
    spec = importlib.util.spec_from_file_location(
        "_rc6_mem_bound", HERE / "test_market_plane_memory_bound.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_coverage_pass_counts_a_refresh_as_labelled_rest_recovery(
        monkeypatch):
    MB = _mem_bound_module()
    monkeypatch.setattr(POP, "external_codes", lambda: {MB.EXT})
    cs, ev, fresh = MB.universe(230)
    pri = [c for c, r in sorted(cs.items()) if r["active"]
           and r["priority"] <= POP.P_CANDIDATE]
    quiet = [c for c in pri if c not in fresh and c not in ev["rest"]][:6]
    in_rest = [c for c in pri if c in ev["rest"] and c not in fresh][:2]
    in_fresh = [c for c in pri if c in fresh][:2]
    stale = quiet.pop()
    refreshed = {c: MB.NOW - 30.0 for c in quiet + in_rest + in_fresh}
    refreshed[stale] = MB.NOW - 300.5          # outside the bound

    base, _bc = run(MB._cov(POP.coverage_pass, cs, ev, fresh))
    got, _gc = run(MB._cov(POP.coverage_pass, cs, ev, fresh,
                           refreshed=refreshed))
    assert "rest_recovery_by_origin" not in base      # RC5's output exactly
    b, g = base["freshness_tiers"]["PRIORITY"], got["freshness_tiers"][
        "PRIORITY"]
    assert g["total"] == b["total"]
    assert g["REST_RECOVERY"] == b["REST_RECOVERY"] + len(quiet)
    assert g["NONE"] == b["NONE"] - len(quiet)
    assert g["PMX_GRPC"] == b["PMX_GRPC"]          # the stream keeps its own
    org = got["rest_recovery_by_origin"]["PRIORITY"]
    # the paper observation keeps its credit; the refresh adds only its own
    assert org == {"PAPER_BOOK_OBSERVATION": b["REST_RECOVERY"],
                   "PLANE_ACTIVE_REFRESH": len(quiet)}
    assert got["source_counts"]["REST_RECOVERY"] == base["source_counts"][
        "REST_RECOVERY"] + len(quiet)
    A = AR()
    assert (A.ORIGIN_PAPER, A.ORIGIN_PLANE) == tuple(org)


def test_the_snapshot_denominators_carry_the_origin_and_the_digest():
    class _C:
        async def fetch(self, *a):
            return []
    tiers = {"PRIORITY": {"PMX_GRPC": 114, "REST_RECOVERY": 61, "NONE": 11,
                          "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 186},
             "ALL": {"PMX_GRPC": 30, "REST_RECOVERY": 61, "NONE": 900,
                     "EXTERNAL_DATA_UNAVAILABLE": 0, "total": 991}}
    origins = {"PRIORITY": {"PAPER_BOOK_OBSERVATION": 1,
                            "PLANE_ACTIVE_REFRESH": 60},
               "ALL": {"PAPER_BOOK_OBSERVATION": 1,
                       "PLANE_ACTIVE_REFRESH": 60}}
    digest = {"version": "PRIORITY_ACTIVE_REFRESH_V1", "enabled": True}
    got = run(W.freshness_denominators(
        _C(), {"freshness_tiers": tiers, "rest_recovery_by_origin": origins},
        {"active": 991, "pmx_listed": 400}, {"overflow_count": 0},
        subscribed=380, fresh=30, now=T0, refresh=digest))
    pr = got["priority_universe"]
    assert pr["current_rest_fallback"] == 61
    assert pr["current_rest_fallback_by_origin"] == origins["PRIORITY"]
    assert pr["rate"] == round(175 / 186, 4) and pr["active_refresh"] == digest


# ═════════════════════════════════════════════════════════════════════
# §7 the census
# ═════════════════════════════════════════════════════════════════════

class _CensusConn:
    def __init__(self, rows, rest=None):
        self.rows, self.rest = rows, rest or {}

    async def fetch(self, sql, *args):
        if "FROM market_plane_registry" in sql:
            return [dict(r, required_reason="EVALUATED_CANDIDATE",
                         refdata_state="PMX_LISTED") for r in self.rows]
        assert "paper_book_observations" in sql
        return [{"slug": k, "age": v} for k, v in self.rest.items()]


def test_the_census_counts_refreshed_members_and_names_every_other_miss():
    A = AR()
    clock = Clock(T0)
    syms = ["m-refreshed", "m-unread", "m-429", "m-acked"]
    m, books = plane(clock, syms)
    books.on_ack(added=["m-acked"])
    for s in syms:
        update(books, s, T0)
    clock.t = T0 + 400.0
    books.on_heartbeat()
    rows = [member(s, 10, T0 + 3600) for s in syms]
    ref = A.ActiveRefresh()
    ref.set_members(rows, now=clock.t)
    ref.record("m-refreshed", rest_book("m-refreshed"), at=clock.t - 5)
    ref.record("m-429", {"status": 429}, at=clock.t - 5)
    refreshed = ref.current(m, now=clock.t, bound=BOUND)
    assert set(refreshed) == {"m-refreshed"}
    cen = run(W.priority_census(_CensusConn(rows, rest={"m-unread": 9000.0}),
                                m, fresh=set(), now=clock.t,
                                refreshed=refreshed, refresher=ref))
    assert cen["members"] == 4 and cen["current_via_refresh"] == 1
    assert cen["not_current"] == 3
    assert cen["by_refresh_outcome"] == {"NOT_YET_READ": 2,
                                         A.R_REFRESH_HTTP_429: 1}
    assert {s["contract_id"]: s["refresh"] for s in cen["sample"]} == {
        "m-unread": "NOT_YET_READ", "m-429": A.R_REFRESH_HTTP_429,
        "m-acked": "NOT_YET_READ"}
    assert all(s["why"] == "STREAM:" + IS.R_SNAPSHOT_OLD
               for s in cen["sample"])
    q = cen["quiet_valid_counterfactual"]
    assert q["stream_quiet_on_the_live_connection"] == 3
    assert q["of_which_symbol_acked_on_this_connection"] == 1
    assert q["snapshot_age_s"] == {"n": 3, "p50": 400.0, "max": 400.0}
    assert q["counted"] is False
    # the parity bridge carries stream books only: none here
    assert cen["pmx_books"] == {}


def test_without_a_refresher_the_census_is_unchanged_in_what_it_counts():
    clock = Clock(T0)
    m, books = plane(clock, ["a", "b"])
    for s in ("a", "b"):
        update(books, s, T0)
    clock.t = T0 + 400.0
    books.on_heartbeat()
    rows = [member(s, 10, T0 + 3600) for s in ("a", "b")]
    cen = run(W.priority_census(_CensusConn(rows), m, fresh=set(),
                                now=clock.t))
    assert cen["not_current"] == 2 and cen["current_via_refresh"] == 0
    assert cen["by_refresh_outcome"] == {}
    assert [s["refresh"] for s in cen["sample"]] == [None, None]


# ═════════════════════════════════════════════════════════════════════
# §8 the REAL run loop against Postgres
# ═════════════════════════════════════════════════════════════════════

QUIET = "atc-rc6-quiet-pri-2026-10-10-qui"


class _QuietTransport:
    """The subscribe-all stream as production showed it: connected, alive
    (heartbeats), and the member's last full update 400 s old."""

    def __init__(self, books, tok, **kw):
        self.books, self.kw = books, kw
        self.subscription_mode = IS.MODE_SUBSCRIBE_ALL
        self._stop = threading.Event()

    def start(self):
        self.books.on_connected("rc6-quiet")

        def beat():
            while not self._stop.wait(0.2):
                self.books.on_heartbeat()
        threading.Thread(target=beat, daemon=True).start()

    def subscribe(self, syms):
        for s in syms:
            at = time.time() - 400.0
            self.books.on_update({"symbol": s, "bids": [(400, 100)],
                                  "offers": [(410, 100)], "state": OPEN,
                                  "transact_time": dt(at)}, received_at=at)
        self.books.on_heartbeat()

    def stop(self):
        self._stop.set()


class _Client:
    reads: list = []

    def __init__(self, env=None, session=None):
        pass

    def token(self):
        return "rc6-test-token-NOT-REAL"

    def invalidate_token(self):
        pass

    def read_instruments(self, body):
        return {"read": "instruments", "status": 200, "ms": 1.0,
                "body": {"instruments": [], "eof": True}}

    def read(self, name, symbol=""):
        type(self).reads.append((name, symbol))
        from sportsassets import pmx_institutional as PMX
        method, url, _b = PMX.request_for(name, symbol)  # the allow-list
        assert (method, url) == ("GET", PMX.REST_BASE + "/v1/orderbook/"
                                 + symbol)
        return rest_book(symbol, at=time.time())


class _Keeper:
    def __init__(self, client):
        self.client = client

    def token(self):
        return self.client.token()

    def invalidate(self):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def digest(self):
        return {"mode": "TEST"}


@pg
def test_the_run_loop_makes_a_quiet_priority_member_current(monkeypatch):
    """The plane's REAL run loop, armed (the transport, the PMX client and
    the token keeper replaced at their edges), on a rolled-back connection:
    one quiet candidate the stream has not re-sent for 400 s. After one
    pass the snapshot counts it current through ONE allow-listed book read,
    labelled PLANE_ACTIVE_REFRESH. On 1c874c1f it stays not current."""
    import asyncpg
    from sportsassets import pmx_institutional as PMX
    from sportsassets.market_plane import sharded_stream as SS
    _Client.reads = []
    snaps, beats = [], []

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()

        class Pool:
            def acquire(self):
                class A:
                    async def __aenter__(self_):
                        return c

                    async def __aexit__(self_, *a):
                        return False
                return A()

        async def get_pool():
            return Pool()

        async def hb(service, status="ok", detail=None, con=None):
            beats.append((service, status, detail))
            if len(beats) >= 2:
                raise asyncio.CancelledError()
        real_snapshot = W.snapshot

        async def snapshot(*a, **kw):
            s = await real_snapshot(*a, **kw)
            snaps.append(json.loads(json.dumps(s, default=str)))
            return s

        async def required(conn):
            return set(), {QUIET}, True
        monkeypatch.setattr(W, "get_pool", get_pool)
        monkeypatch.setattr(W, "heartbeat", hb)
        monkeypatch.setattr(W, "snapshot", snapshot)
        monkeypatch.setattr(W, "INTERVAL_S", 0.0)
        monkeypatch.setattr(W, "stream_arming",
                            lambda *a, **k: {"armed": True, "why": None})
        monkeypatch.setattr(W, "TokenKeeper", _Keeper)
        monkeypatch.setattr(PMX, "Institutional", _Client)
        monkeypatch.setattr(W, "Manager", lambda **kw: SS.Manager(
            transport_factory=lambda b, tok, **k: _QuietTransport(b, tok,
                                                                  **k),
            **kw))
        monkeypatch.setattr(POP, "required_sets_read", required)
        monkeypatch.setenv("KALSHI_CATALOGUE", "off")
        monkeypatch.delenv("UMP_ACTIVE_REFRESH", raising=False)
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute("DELETE FROM us_premap")
            await c.execute(
                "INSERT INTO market_plane_registry (contract_id, venue, "
                " active, desired_subscription, updated_at, priority, "
                " required_reason, event_start, last_seen_at, refdata) "
                "VALUES ($1, 'POLYMARKET_US', true, true, now(), 10, "
                " 'EVALUATED_CANDIDATE', now() + interval '20 hours', now(),"
                " $2::jsonb) ON CONFLICT (contract_id) DO UPDATE SET "
                " active = true, priority = 10, refdata = excluded.refdata",
                QUIET, json.dumps({"symbol": QUIET, "priceScale": "1000",
                                   "fractionalQtyScale": "100",
                                   "state": OPEN}))
            with pytest.raises(asyncio.CancelledError):
                await W.run()
        finally:
            await tr.rollback()
            await c.close()
    run(go())
    assert beats and beats[0][1] != "error", beats[0]
    snap = snaps[0]
    pu = snap["freshness"]["priority_universe"]
    assert pu["denominator"] == 1 and pu["current_pmx_stream"] == 0
    assert pu["current_rest_fallback"] == 1 and pu["rate"] == 1.0
    assert pu["current_rest_fallback_by_origin"] == {
        "PAPER_BOOK_OBSERVATION": 0, "PLANE_ACTIVE_REFRESH": 1}
    cen = pu["census"]
    assert cen["current_via_refresh"] == 1 and cen["not_current"] == 0
    assert pu["active_refresh"]["totals"]["reads"] == 1
    assert pu["active_refresh"]["totals"]["current"] == 1
    # ONE read, the allow-listed GET book of the member, nothing else
    assert _Client.reads == [("book", QUIET)]
    # the stream's own book stays what the stream sent (never a REST book)
    assert snap["subscription"]["fresh"] == 0
    # the heartbeat carries this pass of the refresh
    assert beats[0][2]["refresh"]["read"] == 1


# ═════════════════════════════════════════════════════════════════════
# §9 memory
# ═════════════════════════════════════════════════════════════════════

def test_one_small_record_per_member_dropped_when_it_leaves():
    A = AR()
    ref = A.ActiveRefresh()
    rows = [member("mem-%04d" % i, 10, T0 + i) for i in range(500)]
    ref.set_members(rows, now=T0)
    for r in rows:
        ref.record(r["contract_id"], rest_book(r["contract_id"]), at=T0)
    e = ref.entries["mem-0001"]
    # no book levels are kept: counts and instants only
    assert set(e) <= {"ok_at", "tries", "tried_at", "outcome", "status",
                      "venue_ts", "levels"}
    assert all(not isinstance(v, (list, dict)) or k == "levels"
               for k, v in e.items())
    ref.set_members(rows[:10], now=T0 + 31)
    assert len(ref.entries) == 10 and len(ref.members) == 10
    big = [member("big-%05d" % i, 10, None) for i in range(A.MAX_TRACKED + 50)]
    ref.set_members(big, now=T0 + 62)
    assert len(ref.members) == A.MAX_TRACKED
    assert len(ref._starts) == 0 and ref._starts.maxlen == ref.per_min
