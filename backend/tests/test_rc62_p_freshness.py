"""PRIORITY FRESHNESS AT THE SCORECARD INSTANT (RC6.2 lane p-freshness;
market_plane.active_refresh, market_plane.populate, workers.universal_
market_plane).

Production (pm-acceptance 37888018192, release 732cc0c6): the scorecard's
priority_members_fresh is 174 / 192 (0.9062; 0.95 needs 183), measured at
the coverage pass of 2026-10-09 05:18:59Z. The census taken at that instant
(research-sql 37943847206, P1 / P1b) names the 18 members not current:

  10  quiet, OPEN, PREGAME candidates whose plane book read had gone past
      the 300 s bound and was not repeated (732cc0c6 read one book per
      minutes-long pass: 1.89-2.80 reads a minute against a budget of 12)
   8  candidates whose market the venue had closed (events 3.3-6.8 h old;
      the plane's own REST book read said not open; 7 of 8 now read
      INSTRUMENT_STATE_EXPIRED in the venue's reference data). All 8 were
      DUE again at that instant (the 900 s not-open retry had run out; the
      read pass then: due 18), and 6 of them had never had a stream book,
      so in b3f1b0cd's order (earliest lapse first, never-current first)
      they were read BEFORE the 10 open members.

  §1  the instant replayed through the RC6.1 freshness task for 60 s, with
      the venue answering every book read as it did (10 open, 8 not open):
      the 10 become current, the 8 do not, never more than 12 book reads in
      any 60 s, the denominator stays 192 -> 184 / 192 (all 10 current by
      +9 s). On b3f1b0cd the 8 closed markets take 8 of the 12 reads first
      and 4 of the 10 are current at +60 s (178 / 192; all 10 by +65 s).
      With the owner's snapshot-only read on,
      also 184 (it adds nothing here: the 8 left are closed markets).
  §2  the read plan: a market whose own book read states a TERMINAL state
      (freshness_window.TERMINAL_STATES, the held-position rule's) is held
      out of the plan while it stays a member -- named, NOT current, in the
      denominator, released by newer stream evidence; a transient not-open
      market keeps its 900 s retry and is read after every member that can
      be current. The budget is unchanged.
  §3  the paper runtime's book reads count current in the coverage pass
      and the census only as the frozen window counts them (and the
      held-position rule, on every state it names): the newest error-free
      read inside the bound, whose own state does not say the market is
      not open (on b3f1b0cd ANY row inside 300 s counted:
      PAPER_DISCOVERY_READ_DEFERRED_DURING_VENUE_HOLD rows -- reads never
      made -- and MARKET_STATE_EXPIRED reads included; research-sql
      37951157946 Q3b: 42 + 10 such member-snapshot credits in 24 h).

The replay's 174 already-current members are SYNTHETIC stand-ins with the
production counts (159 on the stream, 15 through a REST read); the 18 carry
their production contract ids, event starts and stream book ages. It is a
test of the plane's code, not production evidence.
"""
from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import importlib.util
import os
import pathlib

import pytest

from sportsassets import institutional_stream as IS
from sportsassets.market_plane import active_refresh as AR
from sportsassets.market_plane import freshness_window as FW
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import snapshot_refresh as SR
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
HERE = pathlib.Path(__file__).resolve().parent
UTC = _dt.timezone.utc
BOUND = W.FRESH_SLA_S
OPEN = "INSTRUMENT_STATE_OPEN"


def _helpers():
    spec = importlib.util.spec_from_file_location(
        "_rc62_refresh_helpers", HERE / "test_rc6_priority_active_refresh.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _helpers()


def run(coro):
    return asyncio.run(coro)


def _at(s):
    return _dt.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(
        tzinfo=UTC).timestamp()


# ═════════════════════════════════════════════════════════════════════
# §1 the scorecard instant, replayed through the freshness task
# ═════════════════════════════════════════════════════════════════════

#: the coverage pass the scorecard read: 2026-10-09 05:18:59.078222Z
INSTANT = 1791523139.078222
#: research-sql 37943847206 P1b: (contract id, stream book age s, event start)
OPEN10 = [
    ("aec-cfb-cmich-ohio-2026-10-10", 331, "2026-10-10 19:30"),
    ("aec-cfb-uconn-templ-2026-10-10", 437, "2026-10-10 19:45"),
    ("aec-cfb-uab-mphs-2026-10-10", 331, "2026-10-10 23:00"),
    ("aec-cfb-airf-nill-2026-10-10", 332, "2026-10-10 23:30"),
    ("atc-brb-juv-ber-2026-10-11-juv", 655, "2026-10-11 19:00"),
    ("atc-bra-bah-mir-2026-10-11-bah", 430, "2026-10-11 22:30"),
    ("atc-lmx-pum-caz-2026-10-11-pum", 519, "2026-10-12 01:15"),
    ("atc-brb-fec-crb-2026-10-12-fec", 1967, "2026-10-12 19:00"),
    ("atc-brb-cri-afc-2026-10-12-cri", 764, "2026-10-12 22:30"),
    ("atc-brb-bot-csc-2026-10-12-bot", 3839, "2026-10-13 00:00")]
#: None: no stream book on the connection (AWAITING_FIRST_SNAPSHOT)
CLOSED8 = [
    ("atc-brb-csc-cri-2026-10-08-csc", None, "2026-10-08 22:30"),
    ("aec-cfb-msrst-wkent-2026-10-08", None, "2026-10-08 23:00"),
    ("aec-cfb-smho-librty-2026-10-08", None, "2026-10-08 23:00"),
    ("aec-cfb-sala-arkst-2026-10-08", None, "2026-10-08 23:30"),
    ("aec-cfb-sfl-utsa-2026-10-08", None, "2026-10-08 23:30"),
    ("aec-mlb-cle-cws-2026-10-08", 5419, "2026-10-09 00:00"),
    ("aec-nfl-tb-dal-2026-10-08", None, "2026-10-09 00:15"),
    ("aec-nhl-tor-veg-2026-10-08", 1260, "2026-10-09 02:00")]
N_STREAM, N_READ = 159, 15


class _Venue:
    """The venue's REST book, as it answered at the instant: an OPEN book
    for the open members, `closed_state` for the closed ones. Every call is
    recorded with the clock's instant; a member that needs no read never
    reaches it."""

    def __init__(self, clock, open_syms, closed_syms, closed_state):
        self.clock, self.open, self.closed = clock, set(open_syms), set(
            closed_syms)
        self.closed_state, self.reads = closed_state, []

    def read(self, name, symbol=""):
        assert name == "book"
        assert symbol in self.open or symbol in self.closed, symbol
        self.reads.append((self.clock.t, symbol))
        return H.rest_book(symbol, at=self.clock.t, state=(
            OPEN if symbol in self.open else self.closed_state))


def _instant(closed_state):
    """The plane at the instant: the books (a REAL subscribe-all Manager),
    the refresher with the state its reads left, the member rows."""
    clock = H.Clock(INSTANT)
    t = INSTANT
    stream = ["rc62-stream-current-%03d" % i for i in range(N_STREAM)]
    read = ["rc62-read-current-%02d" % i for i in range(N_READ)]
    opn = [s for s, _a, _st in OPEN10]
    closed = [s for s, _a, _st in CLOSED8]
    syms = stream + read + opn + closed
    m, books = H.plane(clock, syms)
    for s in stream:
        H.update(books, s, t - 30.0)
    for s in read:
        H.update(books, s, t - 600.0)
    for s, age, _st in OPEN10 + CLOSED8:
        if age is not None:
            H.update(books, s, t - float(age))
    books.on_heartbeat()
    rows = ([H.member(s, 10, t + 3600.0 * (1 + i % 48))
             for i, s in enumerate(stream + read)]
            + [H.member(s, 10, _at(st)) for s, _a, st in OPEN10 + CLOSED8])
    ref = AR.ActiveRefresh()
    ref.set_members(rows, now=t)
    for i, s in enumerate(read):
        # current through a plane read; none nears the bound inside 60 s
        ref.record(s, H.rest_book(s), at=t - 10.0 * i)
    for i, s in enumerate(opn):
        # a CURRENT read that has since gone past the bound
        ref.record(s, H.rest_book(s), at=t - BOUND - 7.0 * (i + 1))
    for i, s in enumerate(closed):
        # the venue's word at the last read, 900 s and more ago: not open
        ref.record(s, H.rest_book(s, state=closed_state),
                   at=t - AR.RETRY_NOT_OPEN_S - 1.0 - i)
    return clock, m, books, ref, rows, stream, read, opn, closed


def _drive(monkeypatch, clock, m, books, ref, venue, rows, *, seconds=60,
           snapper=None):
    """The plane's REAL freshness task (workers.universal_market_plane.
    freshness_loop) for `seconds` ticks of one second each; the member list
    is re-read from `rows` every 30 s as the task re-reads the registry. The
    frozen-window sampler's step (tested in test_rc6_d1_freshness_window.py)
    is the tick boundary here: it advances the clock one second and the
    stream beats."""
    pool = H._Pool(list(rows))
    ticks = [0]

    async def get_pool():
        return pool

    async def fw_step(_pool, _mgr, _ref, _fw, *, now, sla_s):
        ticks[0] += 1
        clock.t += 1.0
        books.on_heartbeat()
        if ticks[0] >= seconds:
            raise asyncio.CancelledError()
    monkeypatch.setattr(W, "get_pool", get_pool)
    monkeypatch.setattr(W.FW, "step", fw_step)
    state: dict = {}

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await W.freshness_loop(
                ref, m, venue, state=state, client_lock=asyncio.Lock(),
                bound=BOUND, tick_s=0.0, clock=clock, snapper=snapper,
                token_fn=(lambda: "rc62-test-token-NOT-REAL"))
    run(go())
    return state


def _census(m, ref, rows, now):
    fresh = W.fresh_symbols(m, now=now)
    refreshed = ref.current(m, now=now, bound=BOUND)
    cen = run(W.priority_census(H._CensusConn(rows), m, fresh=fresh,
                                now=now, refreshed=refreshed, refresher=ref))
    return fresh, refreshed, cen


def _no_window_over_budget(starts):
    for i, s in enumerate(starts):
        assert sum(1 for x in starts[i:] if x - s < 60.0) <= 12
        if i:
            assert s - starts[i - 1] >= AR.MIN_GAP_S


@pytest.mark.parametrize("closed_state", [
    "INSTRUMENT_STATE_EXPIRED",        # terminal: the reference data's word
    "INSTRUMENT_STATE_SUSPENDED",      # transient: still never read first
])
def test_the_scorecard_instant_reaches_184_of_192_inside_a_minute(
        monkeypatch, closed_state):
    """The 05:18:59Z instant through the RC6.1 freshness task for 60 s.
    The 10 open members are current, the 8 closed markets are not, the
    denominator is 192: 159 stream + 25 REST reads = 184 (183 needed). On
    b3f1b0cd the closed markets are read first and 4 of the 10 are current
    at +60 s; on 732cc0c6 no freshness task exists at all."""
    clock, m, books, ref, rows, stream, read, opn, closed = _instant(
        closed_state)
    fresh0, ref0, cen0 = _census(m, ref, rows, INSTANT)
    # the instant as production measured it: 159 + 15 current, 18 not
    assert len(fresh0) == N_STREAM and len(ref0) == N_READ
    assert cen0["members"] == 192 and cen0["not_current"] == 18
    venue = _Venue(clock, opn, closed, closed_state)
    state = _drive(monkeypatch, clock, m, books, ref, venue, rows)
    now = clock.t
    assert now == INSTANT + 60.0
    starts = [t for t, _s in venue.reads]
    _no_window_over_budget(starts)
    read_open = [s for _t, s in venue.reads if s in set(opn)]
    read_closed = [s for _t, s in venue.reads if s in set(closed)]
    assert sorted(read_open) == sorted(opn)            # each read once
    # the budget reaches the markets that can be current first
    assert all(t_o < t_c for t_o, s_o in venue.reads if s_o in set(opn)
               for t_c, s_c in venue.reads if s_c in set(closed))
    if closed_state in FW.TERMINAL_STATES:
        assert read_closed == []                       # held: it has ended
    fresh, refreshed, cen = _census(m, ref, rows, now)
    assert len(fresh) == N_STREAM
    assert set(opn) <= set(refreshed) and not set(closed) & set(refreshed)
    assert cen["members"] == 192                       # the denominator
    assert cen["not_current"] == 8
    assert {s["contract_id"] for s in cen["sample"]} == set(closed)
    num = len(fresh) + len(refreshed)
    assert num == 184 and num / 192 >= 0.95
    assert state["freshness_task"]["errors"] == 0
    assert state["freshness_task"]["reads"] == len(venue.reads)


def test_the_snapshot_read_adds_nothing_at_this_instant(monkeypatch):
    """The owner's decision, measured on the same instant: with the
    snapshot-only read ON (one call, every member due within its lead), the
    10 open members are current through the snapshot, no REST read is made,
    and the count is the same 184: the 8 left are closed markets. The
    venue's snapshot answers are in-process; no channel is opened."""
    closed_state = "INSTRUMENT_STATE_EXPIRED"
    clock, m, books, ref, rows, stream, read, opn, closed = _instant(
        closed_state)
    asked: list = []

    def caller(token, syms):
        asked.append(list(syms))
        at = clock.t
        ups = [({"symbol": s, "bids": [(400, 100)], "offers": [(410, 100)],
                 "state": OPEN if s in set(opn) else closed_state,
                 "transact_time": _dt.datetime.fromtimestamp(at, UTC)}, at)
               for s in syms]
        return {"status": "ENDED", "updates": ups, "heartbeats": 0, "ms": 5.0}

    class _Snap(SR.SnapshotRefresh):
        async def step(self, ref_, mgr, **kw):
            return await super().step(ref_, mgr, caller=caller, **kw)
    venue = _Venue(clock, opn, closed, closed_state)
    _drive(monkeypatch, clock, m, books, ref, venue, rows, snapper=_Snap())
    assert len(asked) == 1 and set(opn) <= set(asked[0])
    assert venue.reads == []
    fresh, refreshed, cen = _census(m, ref, rows, clock.t)
    assert cen["members"] == 192 and cen["not_current"] == 8
    assert len(fresh) + len(refreshed) == 184
    assert {s for s in refreshed if ref.origin_of(s) == "SNAPSHOT"} == set(
        opn)


# ═════════════════════════════════════════════════════════════════════
# §2 the read plan: a market the venue says has ended
# ═════════════════════════════════════════════════════════════════════

def _quiet():
    clock, m, books = H._quiet_plane()
    ref = AR.ActiveRefresh()
    ref.set_members(H._members(clock.t), now=clock.t)
    return clock, m, books, ref


def test_a_market_whose_book_read_says_it_has_ended_is_held_out_of_the_plan():
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    ref.record("cand-later", H.rest_book(
        "cand-later", state="INSTRUMENT_STATE_SUSPENDED"), at=now)
    # long after the not-open retry: the ended market is still not read; the
    # suspended one is due again
    later = now + AR.RETRY_NOT_OPEN_S + 1.0
    books.on_heartbeat()
    due, counts = ref.plan(m, now=later, bound=BOUND)
    assert "cand-soon" not in due and "cand-later" in due
    assert counts[AR.R_REFRESH_HELD_TERMINAL] == 1
    # named, never current, its record kept (the member stays a member)
    assert ref.current(m, now=later, bound=BOUND) == {}
    assert ref.entries["cand-soon"]["state"] == "INSTRUMENT_STATE_EXPIRED"
    assert ref.held_terminal("cand-soon")
    assert ref.outcome_of("cand-soon") == AR.R_REFRESH_NOT_OPEN
    # a whole day later, still held
    due, counts = ref.plan(m, now=now + 86400.0, bound=BOUND)
    assert "cand-soon" not in due


@pytest.mark.parametrize("state", sorted(
    s for s in FW.TERMINAL_STATES if s.startswith("INSTRUMENT_STATE_")))
def test_every_terminal_instrument_state_holds(state):
    clock, m, books, ref = _quiet()
    ref.record("cand-old", H.rest_book("cand-old", state=state), at=clock.t)
    due, counts = ref.plan(m, now=clock.t + 1000.0, bound=BOUND)
    assert "cand-old" not in due and counts[AR.R_REFRESH_HELD_TERMINAL] == 1


def test_newer_stream_evidence_releases_the_hold():
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_CLOSED"), at=now)
    # the stream later sends the market OPEN, then goes quiet again (after
    # the not-open read's own retry wait, which is unchanged)
    clock.t = now + AR.RETRY_NOT_OPEN_S
    H.update(books, "cand-soon", clock.t)
    clock.t = now + AR.RETRY_NOT_OPEN_S + BOUND + 1.0
    books.on_heartbeat()
    assert m.current("cand-soon", now=clock.t, max_snapshot_age_s=BOUND)[
        "refusal"] == IS.R_SNAPSHOT_OLD
    due, counts = ref.plan(m, now=clock.t, bound=BOUND)
    assert "cand-soon" in due and AR.R_REFRESH_HELD_TERMINAL not in counts
    # read again and still ended: held again
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_EXPIRED"), at=clock.t)
    due, counts = ref.plan(m, now=clock.t + 1000.0, bound=BOUND)
    assert "cand-soon" not in due and counts[AR.R_REFRESH_HELD_TERMINAL] == 1


def test_an_open_read_or_a_failed_read_never_holds():
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    # a later read of the same member that is CURRENT clears the state
    ref.record("cand-soon", H.rest_book("cand-soon"), at=now + 10.0)
    assert not ref.held_terminal("cand-soon")
    assert "state" not in ref.entries["cand-soon"]
    # a failed read (after the stream released the hold) replaces the
    # outcome: the member is retried on the failed read's own wait, never
    # held on the older word
    ref.record("cand-later", H.rest_book(
        "cand-later", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    ref.record("cand-later", {"status": 503}, at=now + 10.0)
    assert ref.outcome_of("cand-later") == AR.R_REFRESH_NOT_200
    assert not ref.held_terminal("cand-later")


def test_a_not_open_market_is_read_after_every_member_that_can_be_current():
    """Same tier, both never current (lapse first of all): the member whose
    last read said not open (its retry due) goes after the one that may be
    open. Tier still comes first: a HELD not-open market before a
    CANDIDATE."""
    clock = H.Clock(H.T0)
    syms = ["held-closed", "cand-closed-retry", "cand-never-read",
            "cand-lapsed"]
    m, books = H.plane(clock, syms)
    H.update(books, "cand-lapsed", H.T0)
    clock.t = H.T0 + 1000.0
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member("held-closed", 0, H.T0 + 9000),
                     H.member("cand-closed-retry", 10, H.T0 + 60),
                     H.member("cand-never-read", 10, H.T0 + 9000),
                     H.member("cand-lapsed", 10, H.T0 + 9000)], now=clock.t)
    for s in ("held-closed", "cand-closed-retry"):
        ref.record(s, H.rest_book(s, state="INSTRUMENT_STATE_HALTED"),
                   at=clock.t - AR.RETRY_NOT_OPEN_S - 1.0)
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    assert due == ["held-closed", "cand-never-read", "cand-lapsed",
                   "cand-closed-retry"]


def test_a_snapshot_read_holds_only_on_the_books_own_terminal_state():
    clock, m, books, ref = _quiet()
    now = clock.t
    j = {"outcome": AR.R_REFRESH_NOT_OPEN, "state": "INSTRUMENT_STATE_EXPIRED",
         "state_from": SR.STATE_FROM_UPDATE, "status": "SNAPSHOT"}
    ref.record_snapshot("cand-soon", j, at=now)
    assert ref.held_terminal("cand-soon")
    # a stateless book judged on a fallback state is not the book's own word
    ref.record_snapshot("cand-later", dict(
        j, state_from=SR.STATE_FROM_FALLBACK), at=now)
    assert not ref.held_terminal("cand-later")
    due, counts = ref.plan(m, now=now + AR.RETRY_NOT_OPEN_S + 1.0,
                           bound=BOUND)
    assert "cand-soon" not in due and "cand-later" in due


def test_the_digest_and_the_census_name_the_held_members():
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    ref.record("cand-later", H.rest_book(
        "cand-later", state="INSTRUMENT_STATE_SUSPENDED"), at=now)
    d = ref.digest(now=now, bound=BOUND, mgr=m)
    assert d["totals"]["not_open_by_state"] == {
        "INSTRUMENT_STATE_EXPIRED": 1, "INSTRUMENT_STATE_SUSPENDED": 1}
    assert d["held_market_terminal"] == {
        "n": 1, "by_state": {"INSTRUMENT_STATE_EXPIRED": 1},
        "members": ["cand-soon"]}
    # the budget is the venue's figure, unchanged
    assert d["budget"]["per_min"] == 12 and d["bound_s"] == BOUND
    rows = H._members(now)
    cen = run(W.priority_census(H._CensusConn(rows), m, fresh=set(),
                                now=now + 400.0, refreshed={},
                                refresher=ref))
    # every member is still counted; the held one is named, not current
    assert cen["members"] == len(rows)
    assert cen["refresh_held_market_terminal"] == 1
    assert "cand-soon" in {s["contract_id"] for s in cen["sample"]}


def test_a_held_member_stays_in_the_window_denominator_and_not_current():
    """The frozen window keeps a held member eligible: X only while the
    venue's not-open word is inside the bound (unchanged), then N -- never
    removed from the population."""
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    mem = {"contract_id": "cand-soon", "venue": "POLYMARKET_US",
           "tier": "CANDIDATE"}
    code, why, _a, _b = FW.classify(mem, mgr=m, refreshed={},
                                    entries=ref.entries, paper={},
                                    kalshi={}, now=now + 100.0, sla_s=BOUND)
    assert code == FW.C_EXTERNAL
    code, why, _a, _b = FW.classify(mem, mgr=m, refreshed={},
                                    entries=ref.entries, paper={},
                                    kalshi={}, now=now + 1000.0, sla_s=BOUND)
    assert code == FW.C_NOT and "ACTIVE_REFRESH_MARKET_NOT_OPEN" in why


def test_the_hold_is_classified_in_the_refusal_taxonomy():
    from sportsassets import refusal_taxonomy_table as TT
    assert TT.TABLE[AR.R_REFRESH_HELD_TERMINAL] == ("SOFTWARE", TT.DATA,
                                                    "INGESTION")
    assert AR.TERMINAL_STATES is FW.TERMINAL_STATES


# ═════════════════════════════════════════════════════════════════════
# §3 a paper book read counts current only as the frozen window counts it
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("state,current", [
    ("MARKET_STATE_OPEN", True), ("INSTRUMENT_STATE_OPEN", True),
    (None, True),                       # the held rule's FRESH: no state word
    ("MARKET_STATE_EXPIRED", False), ("MARKET_STATE_CLOSED", False),
    ("INSTRUMENT_STATE_EXPIRED", False), ("MARKET_STATE_HALTED", False),
    ("MARKET_STATE_SUSPENDED", False), ("INSTRUMENT_STATE_PREOPEN", False)])
def test_the_paper_read_rule_is_the_windows_and_the_held_rules(state,
                                                               current):
    """The coverage pass counts a paper read exactly as the frozen window
    classes it (P when current, X when the venue's own state says not open);
    the held-position rule agrees on every state it names (its sets carry
    the retail MARKET_STATE_* words, not the institutional enum's)."""
    from sportsassets import bettor_paper_freshness as PF
    assert POP.paper_book_counts(state) is current
    ext, _why = FW.external_from(state, read_at=990.0, now=1000.0,
                                 sla_s=BOUND, source="PAPER_REST")
    assert ext is (not current)
    if not str(state or "").startswith("INSTRUMENT_STATE_"):
        got = PF.classify(now=1000.0, holding_side="LONG", last_ok={
            "obs_id": 1, "at": 990.0, "market_state": state, "bids": [],
            "offers": []})
        assert (got["class"] != PF.EXTERNAL_UNAVAILABLE) is current


def test_the_coverage_pass_counts_no_paper_read_of_a_closed_market(
        monkeypatch):
    """The coverage pass over the in-memory tables: a paper read inside the
    bound whose state says the market is not open is NOT REST_RECOVERY (on
    b3f1b0cd it was); every other paper read keeps its credit; the
    denominator is unchanged."""
    MB = H._mem_bound_module()
    monkeypatch.setattr(POP, "external_codes", lambda: {MB.EXT})
    cs, ev, fresh = MB.universe(230)
    pri = [c for c, r in sorted(cs.items()) if r["active"]
           and r["priority"] <= POP.P_CANDIDATE and c not in fresh
           and c in ev["rest"]]
    assert len(pri) >= 3
    closed, halted = pri[0], pri[1]
    for v in ev["rest"].values():
        v["market_state"] = "MARKET_STATE_OPEN"
    base, _c = run(MB._cov(POP.coverage_pass, cs, ev, fresh))
    ev2 = copy.deepcopy(ev)
    ev2["rest"][closed]["market_state"] = "MARKET_STATE_EXPIRED"
    ev2["rest"][halted]["market_state"] = "INSTRUMENT_STATE_HALTED"
    got, _c = run(MB._cov(POP.coverage_pass, cs, ev2, fresh))
    b, g = base["freshness_tiers"]["PRIORITY"], got["freshness_tiers"][
        "PRIORITY"]
    assert g["total"] == b["total"]                    # the denominator
    assert g["REST_RECOVERY"] == b["REST_RECOVERY"] - 2
    assert g["NONE"] == b["NONE"] + 2


class _PaperCensusConn(H._CensusConn):
    def __init__(self, rows, paper):
        super().__init__(rows)
        self.paper = paper

    async def fetch(self, sql, *args):
        if "FROM market_plane_registry" in sql:
            return await super().fetch(sql, *args)
        assert "paper_book_observations" in sql
        return [dict(slug=k, **v) for k, v in self.paper.items()]


def test_the_census_names_a_member_whose_only_paper_read_is_not_open():
    clock = H.Clock(H.T0)
    syms = ["p-open", "p-closed", "p-old"]
    m, books = H.plane(clock, syms)
    for s in syms:
        H.update(books, s, H.T0)
    clock.t = H.T0 + 400.0
    books.on_heartbeat()
    rows = [H.member(s, 10, H.T0 + 3600) for s in syms]
    paper = {"p-open": {"age": 20.0, "market_state": "MARKET_STATE_OPEN"},
             "p-closed": {"age": 20.0,
                          "market_state": "MARKET_STATE_EXPIRED"},
             "p-old": {"age": 900.0, "market_state": "MARKET_STATE_OPEN"}}
    cen = run(W.priority_census(_PaperCensusConn(rows, paper), m,
                                fresh=set(), now=clock.t))
    assert cen["members"] == 3 and cen["not_current"] == 2
    by = {s["contract_id"]: s for s in cen["sample"]}
    assert set(by) == {"p-closed", "p-old"}
    assert by["p-closed"]["rest_age_s"] == 20.0
    k = [k for k in cen["by_tier_reason_rest_phase"] if "|REST_" in k]
    assert any("|REST_MARKET_NOT_OPEN|" in x for x in k), k
    assert any("|REST_OLDER_THAN_300S|" in x for x in k), k


@pg
def test_against_postgres_only_the_newest_error_free_open_read_counts():
    """Real Postgres, a rolled-back transaction: six priority members with
    the paper runtime's reads as production records them. The coverage pass
    and the census count exactly the two whose newest error-free read inside
    300 s does not say the market is not open. On b3f1b0cd all five with a
    row inside 300 s counted (an error row -- a read never made -- included).
    """
    import asyncpg
    slugs = {
        "rc62-pfre-ok": [(-20, None, "MARKET_STATE_OPEN")],
        "rc62-pfre-ok-inst": [(-40, None, "INSTRUMENT_STATE_OPEN")],
        "rc62-pfre-error-only": [
            (-30, "PAPER_DISCOVERY_READ_DEFERRED_DURING_VENUE_HOLD", None)],
        "rc62-pfre-expired": [(-25, None, "MARKET_STATE_EXPIRED")],
        "rc62-pfre-halted-newer": [(-200, None, "MARKET_STATE_OPEN"),
                                   (-50, None, "MARKET_STATE_HALTED")],
        "rc62-pfre-old": [(-400, None, "MARKET_STATE_OPEN"),
                          (-10, "PAPER_BOOK_READ_DEADLINE_EXCEEDED", None)]}

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            for s, obs in slugs.items():
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " active, desired_subscription, updated_at, priority, "
                    " required_reason, event_start, last_seen_at) VALUES "
                    " ($1, 'POLYMARKET_US', true, true, now(), 10, "
                    " 'EVALUATED_CANDIDATE', now() + interval '20 hours', "
                    " now()) ON CONFLICT (contract_id) DO UPDATE SET "
                    " active = true, priority = 10", s)
                for dt_, err, st in obs:
                    await c.execute(
                        "INSERT INTO paper_book_observations (us_market_slug,"
                        " observed_at, source, bids, offers, market_state, "
                        " error, read_basis) VALUES ($1, now() + make_interval"
                        "(secs => $2), 'PAPER_MARKET_DATA_CLIENT', "
                        " '[]'::jsonb, '[]'::jsonb, $3, $4, 'TEST')",
                        s, float(dt_), st, err)
            now = await c.fetchval("SELECT extract(epoch FROM now())::float8")
            cov = await POP.coverage_pass(c, fresh_symbols=set(), now=now)
            cen = await W.priority_census(c, None, fresh=set(), now=now)
            return cov, cen
        finally:
            await tr.rollback()
            await c.close()
    cov, cen = run(go())
    pr = cov["freshness_tiers"]["PRIORITY"]
    assert pr["total"] == 6
    assert pr["REST_RECOVERY"] == 2 and pr["NONE"] == 4
    assert cen["members"] == 6 and cen["not_current"] == 4
    assert {s["contract_id"] for s in cen["sample"]} == {
        "rc62-pfre-error-only", "rc62-pfre-expired",
        "rc62-pfre-halted-newer", "rc62-pfre-old"}
