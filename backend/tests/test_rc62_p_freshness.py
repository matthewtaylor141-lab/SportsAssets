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
      any 60 s, the denominator stays 192 -> 184 / 192 (the tenth open
      member is read at +9 s: 183 at +9 s, 184 at +10 s). On b3f1b0cd the 8
      closed markets take 8 of the 12 reads first: 178 / 192 at +60 s, 183
      at +65 s, 184 at +66 s (its tenth open read at +65 s). With the
      owner's snapshot-only read on, also 184 (it adds nothing here: the 8
      left are closed markets).
  §2  the read plan: a market whose own book read says it has ENDED
      (active_refresh.ENDED_STATES: expired, terminated, settled, resolved)
      is held out of the plan for an hour, then read again last -- named,
      NOT current, in the denominator, released at once by newer stream
      evidence; any other not-open market (CLOSED, the enum's 0, and the
      match-and-close auction included: independent review rev2) keeps its
      900 s retry and is read after every member that can be current. A
      market read CLOSED that the venue then opens is read again and
      current (on c07830f2 it was read once and never again). The budget is
      unchanged.
  §3  the paper runtime's book reads count current in the coverage pass
      and the census only as the frozen window counts them (and the
      held-position rule, on every state it names): the newest error-free
      read inside the bound, whose own state does not say the market is
      not open (on b3f1b0cd ANY row inside 300 s counted:
      PAPER_DISCOVERY_READ_DEFERRED_DURING_VENUE_HOLD rows -- reads never
      made -- and MARKET_STATE_EXPIRED reads included; research-sql
      37951157946 Q3b: 42 + 10 such member-snapshot credits in 24 h).
  §4  (independent review rev1) the frozen window's members are refreshed
      for the whole window: a quiet member that leaves the live registry
      list mid-hour (priority 10 -> 20) is still read before each read
      lapses and coded R in every sample, inside the same budget. On
      b3f1b0cd it is read once, before it leaves, and coded N for the rest
      of the window (production window 15:00Z on the RC6.1 plane,
      research-sql 37959672993: 8 such members, ~3.8 a sample).
  §5  (independent review rev2) which list the budget serves first when it
      binds -- the owner's decision, LIVE_FIRST by default: with 70 quiet
      live + 15 quiet frozen-only members the live members are as current
      as with no frozen-only member (60.0; c07830f2: about 49.5), and with
      30 + 15 every member of both lists is current. BY_LAPSE (c07830f2)
      and BOTH_FIRST are measured beside it; the budget is the same.

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
    "INSTRUMENT_STATE_EXPIRED",        # ended: the reference data's word
    "INSTRUMENT_STATE_SUSPENDED",      # transient: still never read first
    "INSTRUMENT_STATE_CLOSED",         # not ended (review rev2): read last
])
def test_the_scorecard_instant_reaches_184_of_192_inside_a_minute(
        monkeypatch, closed_state):
    """The 05:18:59Z instant through the RC6.1 freshness task for 60 s.
    The 10 open members are current, the 8 closed markets are not, the
    denominator is 192: 159 stream + 25 REST reads = 184 (183 needed), the
    tenth open member read at +9 s. On b3f1b0cd the closed markets are read
    first and 4 of the 10 are current at +60 s (178; 184 only at +66 s); on
    732cc0c6 no freshness task exists at all."""
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
    if closed_state in AR.ENDED_STATES:
        assert read_closed == []                       # held: it has ended
    else:
        # due again (its 900 s retry has run out): read, with what the
        # minute's budget leaves after the open members (12 - 10)
        assert read_closed and set(read_closed) <= set(closed)
        assert len(venue.reads) == 12
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
    assert ref.held_terminal("cand-soon", now=later)
    assert ref.outcome_of("cand-soon") == AR.R_REFRESH_NOT_OPEN
    # (independent review rev2) never without a limit: RETRY_ENDED_S after
    # its read it is due again, after every member that may be current
    assert AR.RETRY_ENDED_S == 3600.0
    due, counts = ref.plan(m, now=now + AR.RETRY_ENDED_S - 1.0, bound=BOUND)
    assert "cand-soon" not in due
    due, counts = ref.plan(m, now=now + AR.RETRY_ENDED_S, bound=BOUND)
    assert due[-1] == "cand-soon" and AR.R_REFRESH_HELD_TERMINAL not in counts
    assert not ref.held_terminal("cand-soon", now=now + AR.RETRY_ENDED_S)


@pytest.mark.parametrize("state", sorted(AR.ENDED_STATES))
def test_every_ended_state_holds_for_its_hour(state):
    clock, m, books, ref = _quiet()
    ref.record("cand-old", H.rest_book("cand-old", state=state), at=clock.t)
    due, counts = ref.plan(m, now=clock.t + 1000.0, bound=BOUND)
    assert "cand-old" not in due and counts[AR.R_REFRESH_HELD_TERMINAL] == 1
    due, counts = ref.plan(m, now=clock.t + AR.RETRY_ENDED_S, bound=BOUND)
    assert "cand-old" in due


#: (independent review rev2) the window's TERMINAL words that do NOT say the
#: market has ended: CLOSED is the enum's 0, stated on active programmes not
#: yet open; the match-and-close auction is a phase
NOT_ENDED_TERMINAL = sorted(FW.TERMINAL_STATES - AR.ENDED_STATES)


def test_the_ended_states_are_the_windows_terminal_states_less_closed():
    assert AR.ENDED_STATES < FW.TERMINAL_STATES
    assert NOT_ENDED_TERMINAL == [
        "CLOSED", "INSTRUMENT_STATE_CLOSED",
        "INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION", "MARKET_STATE_CLOSED",
        "MARKET_STATE_MATCH_AND_CLOSE_AUCTION"]
    # every state the institutional enum names that ends a market is held
    assert {"INSTRUMENT_STATE_EXPIRED", "INSTRUMENT_STATE_TERMINATED"} <= \
        AR.ENDED_STATES


@pytest.mark.parametrize("state", NOT_ENDED_TERMINAL)
def test_a_closed_or_auction_state_is_never_held_and_keeps_its_retry(state):
    """Review rev2 finding 1: on c07830f2 every TERMINAL_STATES word held
    the member with no limit. A CLOSED / auction read waits the 900 s
    not-open retry, then is due again -- after every member that may be
    current."""
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-old", H.rest_book("cand-old", state=state), at=now)
    assert not ref.held_terminal("cand-old", now=now + 1.0)
    due, counts = ref.plan(m, now=now + AR.RETRY_NOT_OPEN_S - 1.0,
                           bound=BOUND)
    assert "cand-old" not in due and AR.R_REFRESH_HELD_TERMINAL not in counts
    assert counts[AR.R_REFRESH_RETRY_WAIT] == 1
    due, counts = ref.plan(m, now=now + AR.RETRY_NOT_OPEN_S, bound=BOUND)
    assert due[-1] == "cand-old" and AR.R_REFRESH_HELD_TERMINAL not in counts


class _OpensLater:
    """The venue's REST book for one member: `before` until `opens_at`,
    then OPEN. Every read is recorded (instant, state answered)."""

    def __init__(self, clock, before, opens_at):
        self.clock, self.before, self.opens_at = clock, before, opens_at
        self.reads: list = []

    def read(self, name, symbol=""):
        assert name == "book"
        st = OPEN if self.clock.t >= self.opens_at else self.before
        self.reads.append((self.clock.t, st))
        return H.rest_book(symbol, at=self.clock.t, state=st)


@pytest.mark.parametrize("before,retry", [
    ("INSTRUMENT_STATE_CLOSED", AR.RETRY_NOT_OPEN_S),
    ("MARKET_STATE_CLOSED", AR.RETRY_NOT_OPEN_S),
    ("INSTRUMENT_STATE_MATCH_AND_CLOSE_AUCTION", AR.RETRY_NOT_OPEN_S),
    ("INSTRUMENT_STATE_EXPIRED", AR.RETRY_ENDED_S),
])
def test_a_market_read_not_open_that_then_opens_is_read_again_and_current(
        monkeypatch, before, retry):
    """Independent review rev2, finding 1 (probe_rev2 P2), through the
    plane's REAL freshness task for two hours. A quiet candidate with no
    stream book on this connection is read `before`; the venue opens it 10
    minutes later and the stream sends nothing. On c07830f2 every
    freshness_window.TERMINAL_STATES word -- CLOSED, the enum's 0, stated on
    active programmes not yet open, included -- held it with no limit: one
    read in two hours and never current again. Here a CLOSED / auction read
    is retried after 900 s (b3f1b0cd's retry), an ENDED one after an hour;
    the retry reads it OPEN and it is current from then on, re-read before
    each read lapses, never more than 12 reads in any 60 s."""
    clock = H.Clock(H.T0)
    s = "cand-not-open-then-open"
    m, books = H.plane(clock, [s])          # no stream book: never streamed
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    venue = _OpensLater(clock, before, opens_at=H.T0 + 600.0)
    state = _drive(monkeypatch, clock, m, books, ref, venue,
                   [H.member(s, 10, H.T0 + 86400.0)], seconds=7200)
    reads = venue.reads
    assert state["freshness_task"]["errors"] == 0
    _no_window_over_budget([t for t, _st in reads])
    assert reads[0] == (H.T0, before)                # read at once
    assert len(reads) >= 2, ("read once, never again: %r" % reads)
    # the retry: not before its wait, within a second of it
    assert retry <= reads[1][0] - reads[0][0] <= retry + 1.0
    assert reads[1][1] == OPEN
    # then current for the rest of the two hours, re-read inside the bound
    again = [t for t, _st in reads[1:]]
    assert all(b - a <= BOUND - AR.REFRESH_LEAD_S + 1.0
               for a, b in zip(again, again[1:]))
    assert clock.t - again[-1] <= BOUND
    assert s in ref.current(m, now=clock.t, bound=BOUND)
    assert len(reads) >= (7200 - retry) // BOUND


def test_newer_stream_evidence_releases_the_hold():
    clock, m, books, ref = _quiet()
    now = clock.t
    ref.record("cand-soon", H.rest_book(
        "cand-soon", state="INSTRUMENT_STATE_TERMINATED"), at=now)
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
    assert not ref.held_terminal("cand-soon", now=now + 11.0)
    assert "state" not in ref.entries["cand-soon"]
    # a failed read (after the stream released the hold) replaces the
    # outcome: the member is retried on the failed read's own wait, never
    # held on the older word
    ref.record("cand-later", H.rest_book(
        "cand-later", state="INSTRUMENT_STATE_EXPIRED"), at=now)
    ref.record("cand-later", {"status": 503}, at=now + 10.0)
    assert ref.outcome_of("cand-later") == AR.R_REFRESH_NOT_200
    assert not ref.held_terminal("cand-later", now=now + 11.0)


def test_a_not_open_market_is_read_after_every_member_that_can_be_current():
    """Same tier, both never current (lapse first of all): the member whose
    last read said not open (its retry due) goes after the one that may be
    open, and (review rev2) one whose last read said the market has ended,
    due again after its hour, after those. Tier still comes first: a HELD
    not-open market before a CANDIDATE."""
    clock = H.Clock(H.T0)
    syms = ["held-closed", "cand-closed-retry", "cand-never-read",
            "cand-lapsed", "cand-ended-retry", "cand-closed-word"]
    m, books = H.plane(clock, syms)
    H.update(books, "cand-lapsed", H.T0)
    clock.t = H.T0 + 4000.0
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member("held-closed", 0, H.T0 + 9000),
                     H.member("cand-ended-retry", 10, H.T0 + 30),
                     H.member("cand-closed-retry", 10, H.T0 + 60),
                     H.member("cand-closed-word", 10, H.T0 + 61),
                     H.member("cand-never-read", 10, H.T0 + 9000),
                     H.member("cand-lapsed", 10, H.T0 + 9000)], now=clock.t)
    for s in ("held-closed", "cand-closed-retry"):
        ref.record(s, H.rest_book(s, state="INSTRUMENT_STATE_HALTED"),
                   at=clock.t - AR.RETRY_NOT_OPEN_S - 1.0)
    ref.record("cand-closed-word", H.rest_book(
        "cand-closed-word", state="INSTRUMENT_STATE_CLOSED"),
        at=clock.t - AR.RETRY_NOT_OPEN_S - 2.0)
    ref.record("cand-ended-retry", H.rest_book(
        "cand-ended-retry", state="INSTRUMENT_STATE_EXPIRED"),
        at=clock.t - AR.RETRY_ENDED_S - 1.0)
    due, _c = ref.plan(m, now=clock.t, bound=BOUND)
    assert due == ["held-closed", "cand-never-read", "cand-lapsed",
                   "cand-closed-retry", "cand-closed-word",
                   "cand-ended-retry"]


def test_a_snapshot_read_holds_only_on_the_books_own_terminal_state():
    clock, m, books, ref = _quiet()
    now = clock.t
    j = {"outcome": AR.R_REFRESH_NOT_OPEN, "state": "INSTRUMENT_STATE_EXPIRED",
         "state_from": SR.STATE_FROM_UPDATE, "status": "SNAPSHOT"}
    ref.record_snapshot("cand-soon", j, at=now)
    assert ref.held_terminal("cand-soon", now=now + 1.0)
    # a stateless book judged on a fallback state is not the book's own word
    ref.record_snapshot("cand-later", dict(
        j, state_from=SR.STATE_FROM_FALLBACK), at=now)
    assert not ref.held_terminal("cand-later", now=now + 1.0)
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
        "retry_s": AR.RETRY_ENDED_S, "members": ["cand-soon"]}
    assert d["list_order"] == AR.LIST_LIVE_FIRST
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
    assert AR.ENDED_STATES <= FW.TERMINAL_STATES


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
    300 s does not say the market is not open. On b3f1b0cd all six counted
    (each has a row inside 300 s; an error row -- a read never made --
    included): REST_RECOVERY 6, where 2 is right.
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


# ═════════════════════════════════════════════════════════════════════
# §4 the frozen window's members are refreshed for the whole window
# ═════════════════════════════════════════════════════════════════════
#
# Independent review rev1 of 3a86235d (production window 15:00Z on the RC6.1
# plane, research-sql 37959672993): the refresh read only the LIVE registry
# list (priority <= 10, or a working order), while the frozen window
# measures the membership frozen at its first sample for the whole hour. A
# quiet member that left the live list mid-hour (aec-autbl-obe-kap-2026-10-10
# went to priority 20, VENUE_ACTIVE) lost its refresh record, was never read
# again and was coded N in 47 of the hour's 53 samples, with book reads at
# 6.3-9.0 a minute. On b3f1b0cd the leaver below is read once, before it
# leaves, and is N from the next member re-read to the end of the window.

#: a window start no other test freezes (the events table is append-only)
WS4 = FW.window_start_of(1_800_000_000.0)
LEAVER, STAYS, STREAMED = "fz-leaver-2026-10-10", "fz-stays-2026-10-10", \
    "fz-streamed-2026-10-10"


def _frozen_win(members, ws=WS4, at=None):
    return dict(FW.freeze(members, window_start=ws,
                          now=ws + 5.0 if at is None else at, sla_s=BOUND),
                loaded=False)


def _fm(cid, tier="CANDIDATE", start=None):
    return {"contract_id": cid, "tier": tier, "venue": FW.VENUE_PMUS,
            "family": FW.F_MONEYLINE, "period": "FULL_EVENT",
            "event_start": start, "event_id": None, "line": None,
            "orders": tier == FW.TIER_ORDER}


def test_the_frozen_members_join_the_live_list_with_their_frozen_tier():
    now = WS4 + 600.0
    ref = AR.ActiveRefresh()
    ref.set_members([H.member(STAYS, 10, now + 3600)], now=now)
    win = _frozen_win([_fm(LEAVER, start=now + 7200),
                       _fm(STAYS, "HELD_POSITION", start=now + 3600),
                       _fm("fz-order", "WORKING_ORDER", start=None)])
    assert ref.set_frozen(win, now=now) is True
    assert ref.set_frozen(win, now=now + 1.0) is False     # same window
    tiers = {m[0]: m[1] for m in ref.members}
    # the live member keeps its place and takes the more urgent frozen tier;
    # the frozen-only members follow, in the window's (contract id) order,
    # with their frozen tiers
    assert [m[0] for m in ref.members] == [STAYS, LEAVER, "fz-order"]
    assert tiers == {STAYS: AR.TIER_HELD, LEAVER: AR.TIER_CANDIDATE,
                     "fz-order": AR.TIER_ORDER}
    assert ref.members_frozen_only == 2
    # a live tier more urgent than the frozen one stands
    ref.set_members([H.member(LEAVER, 0, now + 7200)], now=now + 30.0)
    assert {m[0]: m[1] for m in ref.members}[LEAVER] == AR.TIER_HELD
    # a member re-read off the live list keeps its record while frozen
    ref.record(LEAVER, H.rest_book(LEAVER, at=now), at=now)
    ref.set_members([], now=now + 60.0)
    assert LEAVER in ref.entries and len(ref.members) == 3
    d = ref.digest(now=now + 60.0, bound=BOUND)
    assert d["members"] == 3 and d["members_live"] == 0
    assert d["members_frozen_only"] == 3 and d["frozen_window_start"] == WS4
    # past the window and the carry of its last sample: the live list alone
    # (and a record of a member in neither is dropped, as before)
    late = WS4 + FW.WINDOW_S + FW.CARRY_S
    assert ref.set_frozen(win, now=late) is True
    assert ref.members == [] and ref.entries == {}
    # no window held: the live list alone, exactly as on b3f1b0cd
    ref2 = AR.ActiveRefresh()
    assert ref2.set_frozen(None, now=now) is False
    ref2.set_members([H.member(STAYS, 10, now + 3600)], now=now)
    assert [m[0] for m in ref2.members] == [STAYS]


def test_the_frozen_list_is_bounded_and_never_trimmed_by_the_live_one():
    now = WS4 + 60.0
    ref = AR.ActiveRefresh()
    live = [H.member("live-%05d" % i, 10, None)
            for i in range(AR.MAX_TRACKED + 10)]
    ref.set_members(live, now=now)
    win = _frozen_win([_fm("frozen-%05d" % i) for i in range(30)])
    ref.set_frozen(win, now=now)
    assert len(ref._live) == AR.MAX_TRACKED
    assert len(ref.members) == AR.MAX_TRACKED + 30
    assert ref.members_frozen_only == 30


class _RegistryConn:
    """The plane's database for one window, in memory: the registry rows the
    two member lists read (freshness_window.PRIORITY_SQL to freeze,
    active_refresh.MEMBERS_SQL every 30 s) and the append-only events table
    (the window, the samples). No PAPER tables: none is read."""

    def __init__(self, rows):
        self.reg = {r["contract_id"]: dict(r) for r in rows}
        self.events: dict = {}

    async def fetchval(self, sql, *args):
        if "to_regclass" in sql:
            return False
        assert sql == FW.WINDOW_READ_SQL, sql
        return self.events.get(args[0])

    async def execute(self, sql, *args):
        assert sql.lstrip().startswith("INSERT INTO market_plane_events"), sql
        self.events.setdefault(args[0], args[3])     # ON CONFLICT DO NOTHING

    async def fetch(self, sql, *args):
        rows = [dict(r) for r in self.reg.values() if r["active"]
                and r["priority"] <= args[0]]
        if sql == FW.PRIORITY_SQL:
            return sorted(rows, key=lambda r: r["contract_id"])
        assert sql == AR.MEMBERS_SQL, sql
        return sorted(rows, key=lambda r: (r["priority"], r["event_start"],
                                           r["contract_id"]))[:args[1]]


class _ConnPool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class A:
            async def __aenter__(self_):
                return c

            async def __aexit__(self_, *a):
                return False
        return A()


class _PgPool(_ConnPool):
    """One connection (the test's transaction); the freshness task is the
    only user here and never holds it twice at once."""


async def _window_run(monkeypatch, pool, *, start, seconds, leave_at, leave):
    """The plane's REAL freshness task (snapshot read off, as in
    production) with the REAL frozen-window sampler, one tick a second for
    `seconds`: the REST book read answers OPEN for every member, the stream
    sends STREAMED a book every tick and nothing for the two quiet members
    (LEAVER's last stream book the older, so it lapsed first and is read
    first). At `leave_at` seconds `leave()` takes LEAVER off the live list.
    Returns (refresher, book reads [(t, symbol)])."""
    clock = H.Clock(start)
    m, books = H.plane(clock, [LEAVER, STAYS, STREAMED])
    H.update(books, LEAVER, start - BOUND - 200.0)       # quiet: past the bound
    H.update(books, STAYS, start - BOUND - 100.0)
    H.update(books, STREAMED, start)
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    reads: list = []

    class Venue:
        def read(self, name, symbol=""):
            assert name == "book"
            reads.append((clock.t, symbol))
            return H.rest_book(symbol, at=clock.t)
    real_step = FW.step
    ticks = [0]

    async def get_pool():
        return pool

    async def fw_step(*a, **kw):
        got = await real_step(*a, **kw)
        ticks[0] += 1
        clock.t += 1.0
        H.update(books, STREAMED, clock.t)
        books.on_heartbeat()
        if ticks[0] == leave_at:
            await leave()
        if ticks[0] >= seconds:
            raise asyncio.CancelledError()
        return got
    monkeypatch.setattr(W, "get_pool", get_pool)
    monkeypatch.setattr(W.FW, "step", fw_step)
    state: dict = {}
    with pytest.raises(asyncio.CancelledError):
        await W.freshness_loop(ref, m, Venue(), state=state,
                               client_lock=asyncio.Lock(), bound=BOUND,
                               tick_s=0.0, clock=clock)
    assert state["freshness_task"]["errors"] == 0, state["freshness_task"]
    return ref, reads


def _samples(payloads):
    import json
    got = [json.loads(p) if isinstance(p, str) else p for p in payloads]
    win = [p for p in got if "members" in p]
    smp = sorted((p for p in got if "codes" in p),
                 key=lambda p: p["verified_at"])
    assert len(win) == 1
    order = [r[0] for r in win[0]["members"]]
    return order, smp


def _assert_served(order, smp, reads, *, start, leave_at, seconds):
    i = order.index(LEAVER)
    codes = [p["codes"][i] for p in smp]
    after = [t for t, s in reads if s == LEAVER and t >= start + leave_at]
    # the budget is the same: never more than 12 book reads in any 60 s
    _no_window_over_budget([t for t, _s in reads])
    assert after, ("the member that left the live list was never read "
                   "again (b3f1b0cd): %d reads before it left, window codes "
                   "%s" % (sum(1 for _t, s in reads if s == LEAVER),
                           "".join(codes)))
    # read again before each read lapses, for the whole window
    assert len(after) >= (seconds - leave_at) // (BOUND - AR.REFRESH_LEAD_S)
    # R in every sample of the window: it never lapses
    assert set(codes) == {FW.C_REFRESH}, "".join(codes)
    # the member that stayed is served exactly as before, and the streamed
    # one needs no read
    j = order.index(STAYS)
    assert smp[0]["codes"][j] == FW.C_NOT              # read on the next tick
    assert {p["codes"][j] for p in smp[1:]} == {FW.C_REFRESH}
    assert not [s for _t, s in reads if s == STREAMED]
    # every sample: the whole frozen membership, nothing dropped
    assert {p["n"] for p in smp} == {3}


def test_a_member_that_leaves_the_live_list_mid_window_is_still_refreshed(
        monkeypatch):
    """REAL freshness task + REAL frozen-window sampler, 30 minutes inside
    one window. The quiet LEAVER is frozen into the window at its first
    sample and leaves the live list (registry priority 10 -> 20) 2 minutes
    in. b3f1b0cd reads it once (before it leaves) and codes it N from the
    next member re-read to the end; here it is read before each read
    lapses and coded R in every sample, inside the same budget."""
    start, seconds, leave_at = WS4 + 5.0, 1800, 120
    conn = _RegistryConn([
        {"contract_id": s, "venue": FW.VENUE_PMUS, "priority": 10,
         "active": True, "market_type": "soccer_team_full_time_winner",
         "family": "WINNER", "period": "FULL_EVENT", "event_id": "ev-" + s,
         "line": None, "event_start": _dt.datetime.fromtimestamp(
             start + 86400.0, UTC)}
        for s in (LEAVER, STAYS, STREAMED)])

    async def leave():
        conn.reg[LEAVER]["priority"] = 20                # VENUE_ACTIVE

    ref, reads = run(_window_run(monkeypatch, _ConnPool(conn), start=start,
                                 seconds=seconds, leave_at=leave_at,
                                 leave=leave))
    order, smp = _samples(conn.events.values())
    assert len(smp) == seconds // 60 and LEAVER in order
    _assert_served(order, smp, reads, start=start, leave_at=leave_at,
                   seconds=seconds)
    assert {m[0] for m in ref._live} == {STAYS, STREAMED}
    assert ref.digest(now=start + seconds, bound=BOUND)[
        "members_frozen_only"] == 1


@pg
def test_against_postgres_a_member_that_leaves_mid_window_is_still_refreshed(
        monkeypatch):
    """The same 30 minutes against Postgres (a rolled-back transaction): the
    window frozen by freshness_window.load_or_freeze's own SQL, the live
    list read by MEMBERS_WITH_ORDERS_SQL (paper_orders exists), the leaver
    taken off it by an UPDATE of its registry priority to 20."""
    import asyncpg
    start, seconds, leave_at = WS4 + 5.0, 1800, 120

    async def go():
        c = await asyncpg.connect(DSN)
        tr = c.transaction()
        await tr.start()
        try:
            await c.execute("UPDATE market_plane_registry SET active=false")
            await c.execute(
                "UPDATE paper_orders SET state = 'CANCELED', terminal_at = "
                " now(), terminal_reason = 'TEST_ISOLATION' "
                " WHERE state = ANY($1::text[])", list(FW.OPEN_ORDER_STATES))
            for s in (LEAVER, STAYS, STREAMED):
                await c.execute(
                    "INSERT INTO market_plane_registry (contract_id, venue, "
                    " active, desired_subscription, updated_at, priority, "
                    " required_reason, market_type, family, period, event_id,"
                    " event_start, last_seen_at) VALUES ($1, 'POLYMARKET_US',"
                    " true, true, now(), 10, 'EVALUATED_CANDIDATE', "
                    " 'soccer_team_full_time_winner', 'WINNER', 'FULL_EVENT',"
                    " 'ev-1', to_timestamp($2), now()) ON CONFLICT "
                    " (contract_id) DO UPDATE SET active = true, priority = 10,"
                    " event_start = excluded.event_start", s,
                    start + 86400.0)

            async def leave():
                await c.execute("UPDATE market_plane_registry SET priority ="
                                " 20, required_reason = 'VENUE_ACTIVE' "
                                " WHERE contract_id = $1", LEAVER)
            ref, reads = await _window_run(
                monkeypatch, _PgPool(c), start=start, seconds=seconds,
                leave_at=leave_at, leave=leave)
            rows = [r["payload"] for r in await c.fetch(
                "SELECT payload FROM market_plane_events WHERE event_key = $1"
                " OR (kind = 'FRESHNESS_SAMPLE' AND (payload->>"
                "'window_start')::float8 = $2)", "fwin:%d" % int(WS4), WS4)]
            return ref, reads, rows
        finally:
            await tr.rollback()
            await c.close()
    ref, reads, rows = asyncio.run(go())
    order, smp = _samples(rows)
    assert order == sorted([LEAVER, STAYS, STREAMED])
    assert len(smp) == seconds // 60
    _assert_served(order, smp, reads, start=start, leave_at=leave_at,
                   seconds=seconds)
    # the live list (MEMBERS_WITH_ORDERS_SQL) no longer holds it
    assert {m[0] for m in ref._live} == {STAYS, STREAMED}



# ═════════════════════════════════════════════════════════════════════
# §5 which list the budget serves first when it binds (review rev2)
# ═════════════════════════════════════════════════════════════════════
#
# Independent review rev2 of c07830f2, finding 2: the members only the frozen
# window holds are outside the scorecard's instant denominator (the coverage
# pass's PRIORITY tier, registry priority <= 10) and share the same 12 reads
# a minute. In plain earliest-lapse order they take reads from the instant
# population whenever more members are quiet than the budget can hold
# (probe_rev2 P1: 70 quiet live + 15 quiet frozen-only members -> 49.6 live
# members current on average, 60.0 with no frozen window). The order is the
# owner's (active_refresh.list_order); LIVE_FIRST, the default, serves the
# live list first and the frozen-only members with every read it does not
# need. The budget is the same in every order.

WS5 = FW.window_start_of(1_800_007_200.0)
#: the real sampler, taken before any test patches it (a test below runs the
#: task twice inside one monkeypatch scope)
_REAL_FW_STEP = FW.step


def _capacity_rows(n_live, n_fo, start):
    rows = []
    for i in range(n_live + n_fo):
        s = ("cap-live-%03d" % i) if i < n_live else (
            "cap-fonly-%03d" % (i - n_live))
        rows.append({"contract_id": s, "venue": FW.VENUE_PMUS,
                     "priority": 10, "active": True,
                     "market_type": "soccer_team_full_time_winner",
                     "family": "WINNER", "period": "FULL_EVENT",
                     "event_id": "ev-" + s, "line": None,
                     "event_start": _dt.datetime.fromtimestamp(
                         start + 86400.0 + i, UTC)})
    return rows


def _capacity_run(monkeypatch, *, n_live, n_fo, order=None, minutes=30,
                  freeze_fo=True):
    """The plane's REAL freshness task and REAL frozen-window sampler for
    `minutes` inside one window. Every member is quiet (its last stream
    book past the bound, spread so no two lapse together) and the venue
    answers every book read OPEN. All n_live + n_fo members are at registry
    priority 10 when the window freezes; two minutes in the n_fo leave the
    live list (priority 20), so they are members only the frozen window
    holds (with freeze_fo False they are never members: the budget's
    baseline with the same n_live). Returns ([(live current,
    frozen-only current)] at each minute from minute 10, the refresher, the
    book read instants)."""
    start = WS5 + 5.0
    rows = _capacity_rows(n_live, n_fo, start)
    conn = _RegistryConn(rows)
    fo = [r["contract_id"] for r in rows[n_live:]]
    live = [r["contract_id"] for r in rows[:n_live]]
    if not freeze_fo:
        for s in fo:
            conn.reg[s]["priority"] = 20
    clock = H.Clock(start)
    m, books = H.plane(clock, live + fo)
    for i, s in enumerate(live + fo):
        H.update(books, s, start - BOUND - 10.0 - i)
    books.on_heartbeat()
    ref = AR.ActiveRefresh() if order is None else AR.ActiveRefresh(
        order=order)
    reads: list = []
    series: list = []

    class Venue:
        def read(self, name, symbol=""):
            assert name == "book"
            reads.append(clock.t)
            return H.rest_book(symbol, at=clock.t)
    real_step = _REAL_FW_STEP
    ticks = [0]

    async def get_pool():
        return _ConnPool(conn)

    async def fw_step(*a, **kw):
        got = await real_step(*a, **kw)
        ticks[0] += 1
        clock.t += 1.0
        books.on_heartbeat()
        if ticks[0] == 120:
            # after the window froze at its first sample (as in section 4)
            for s in fo:
                conn.reg[s]["priority"] = 20            # VENUE_ACTIVE
        if ticks[0] % 60 == 0 and ticks[0] >= 600:
            cur = ref.current(m, now=clock.t, bound=BOUND)
            series.append((sum(1 for s in live if s in cur),
                           sum(1 for s in fo if s in cur)))
        if ticks[0] >= minutes * 60:
            raise asyncio.CancelledError()
        return got
    monkeypatch.setattr(W, "get_pool", get_pool)
    monkeypatch.setattr(W.FW, "step", fw_step)
    state: dict = {}

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await W.freshness_loop(ref, m, Venue(), state=state,
                                   client_lock=asyncio.Lock(), bound=BOUND,
                                   tick_s=0.0, clock=clock)
    run(go())
    assert state["freshness_task"]["errors"] == 0, state["freshness_task"]
    _no_window_over_budget(reads)
    assert ref.members_frozen_only == (n_fo if freeze_fo else 0)
    return series, ref, reads


def _avg(xs):
    return sum(xs) / float(len(xs))


def test_when_the_budget_binds_the_live_list_is_served_first(monkeypatch):
    """70 quiet live members + 15 quiet frozen-only members, 12 reads a
    minute (the budget holds about 57-60 at once). With the default order
    the live members are exactly as current as with no frozen-only member
    at all, and the frozen-only members take no read the live list needs.
    On c07830f2 (no list order) the live average is about 50 -- the
    frozen-only members took about 10 of the budget's places."""
    base, _r, _x = _capacity_run(monkeypatch, n_live=70, n_fo=15,
                                 freeze_fo=False)
    got, ref, reads = _capacity_run(monkeypatch, n_live=70, n_fo=15)
    assert ref.order == AR.LIST_LIVE_FIRST
    live_base = _avg([a for a, _b in base])
    live = _avg([a for a, _b in got])
    assert live_base >= 55.0                       # the budget binds
    assert live >= live_base - 0.5, (live, live_base, got)
    assert _avg([b for _a, b in got]) <= 0.5
    # the same budget: about 12 reads a minute, whichever list
    assert len(reads) <= 12 * 30


def test_when_the_budget_does_not_bind_the_frozen_only_members_are_served(
        monkeypatch):
    """30 quiet live + 15 quiet frozen-only members: 45 fit inside the
    budget, so with the default order every one of them is current at every
    minute from minute 10 -- the frozen window's leavers are still served
    for the whole window (review rev1, c07830f2)."""
    got, _ref, _x = _capacity_run(monkeypatch, n_live=30, n_fo=15)
    assert set(got) == {(30, 15)}, got


@pytest.mark.parametrize("order", [AR.LIST_BY_LAPSE, AR.LIST_BOTH_FIRST])
def test_the_owners_other_orders_trade_live_reads_for_frozen_ones(
        monkeypatch, order):
    """The owner's alternatives, measured on the same 70 + 15: BY_LAPSE
    (c07830f2 as reviewed) and BOTH_FIRST (members of both lists first, then
    live-only and frozen-only together; here every live member is also in
    the window, so it serves them like LIVE_FIRST) -- the budget the same in
    each."""
    base, _r, _x = _capacity_run(monkeypatch, n_live=70, n_fo=15,
                                 freeze_fo=False)
    got, ref, _x = _capacity_run(monkeypatch, n_live=70, n_fo=15, order=order)
    assert ref.order == order
    live_base = _avg([a for a, _b in base])
    live, fo = _avg([a for a, _b in got]), _avg([b for _a, b in got])
    if order == AR.LIST_BY_LAPSE:
        assert live <= live_base - 5.0 and fo >= 5.0, (live, fo, live_base)
    else:
        assert live >= live_base - 0.5 and fo <= 0.5, (live, fo, live_base)


def test_the_list_order_is_the_owners_switch_and_defaults_to_live_first():
    assert AR.list_order({}) == AR.LIST_LIVE_FIRST
    assert AR.list_order({AR.LIST_ORDER_ENV: "by_lapse"}) == AR.LIST_BY_LAPSE
    assert AR.list_order({AR.LIST_ORDER_ENV: " BOTH_FIRST "}) == \
        AR.LIST_BOTH_FIRST
    assert AR.list_order({AR.LIST_ORDER_ENV: "other"}) == AR.LIST_LIVE_FIRST
    assert AR.ActiveRefresh().order == AR.LIST_LIVE_FIRST
    assert AR.ActiveRefresh(order="nonsense").order == AR.LIST_LIVE_FIRST
    # the plan's ranks: LIVE_FIRST puts the frozen-only member after the
    # live one whatever its tier or lapse; BOTH_FIRST puts a live-only
    # member after the members of both lists; BY_LAPSE ranks none
    now = WS5 + 60.0
    win = _frozen_win([_fm("both"), _fm("fonly", "HELD_POSITION")], ws=WS5)
    for order, rank in ((AR.LIST_LIVE_FIRST, {"fonly": 1}),
                        (AR.LIST_BOTH_FIRST, {"fonly": 1, "lonly": 1}),
                        (AR.LIST_BY_LAPSE, {})):
        ref = AR.ActiveRefresh(order=order)
        ref.set_members([H.member("both", 10, None),
                         H.member("lonly", 10, None)], now=now)
        ref.set_frozen(win, now=now)
        assert ref.list_rank == rank, order
        assert {m[0] for m in ref.members} == {"both", "lonly", "fonly"}
    clock = H.Clock(now)
    m, books = H.plane(clock, ["both", "lonly", "fonly"])
    books.on_heartbeat()
    ref = AR.ActiveRefresh()
    ref.set_members([H.member("both", 10, None),
                     H.member("lonly", 10, None)], now=now)
    ref.set_frozen(win, now=now)
    due, _c = ref.plan(m, now=now, bound=BOUND)
    assert due[-1] == "fonly"                      # a HELD tier, still last
    d = ref.digest(now=now, bound=BOUND, mgr=m)
    assert d["list_order"] == AR.LIST_LIVE_FIRST
    assert d["current_via_refresh_frozen_only"] == 0
