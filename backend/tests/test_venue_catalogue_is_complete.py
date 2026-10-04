"""THE VENUE CATALOGUE HOLDS THE FULL TRADABLE BOARD, NOT A SAMPLE OF IT
(R30A P0 incident -- coverage -> trade starvation -- stream inc-catalogue).

The owner: "Audit and repair now for: pagination, result limits,
market-per-event limits, first-page-only behavior, first-market-only behavior,
sport filters, league filters, active/open-state filters, live vs pregame
filtering, duplicate keys accidentally collapsing distinct markets."

Every venue payload below has the shape the venue's public gateway serves
(fetch-docs runs 37233823157 / 37233829391, `GET /v1/events`: `live`,
`period`, `category`, `marketCounts.numMarkets`, `status`, markets inline with
`marketSides` carrying `identifier`, `description` and the explicit `long`
marker) and the sportsMarketType vocabulary production holds (research-sql
run 37232541526 V2). The writer under test is the real one --
`workers/premap.refresh` -- against the real Postgres schema (migration 249),
inside one transaction that is rolled back.

  §1 pagination (pure PageWalk): multi-page boards end on their short page; a
     venue page-size cap below the request is detected, not mistaken for the
     end (the old rule read it as first-page-only); a board that shifts under
     the walk is caught by the overlap; a budget that runs out while pages are
     full is TRUNCATED, by name.
  §2 classification and state (pure): the families and the live / pregame
     state, the venue's word before the schedule's.
  §3 keys (pure): two sides of one market that normalise to the same text are
     both kept; a key reused by another market is refused, never overwritten.
  §4 THE WRITER END TO END on Postgres: every market of every event (several
     per event), two fixtures with the same title kept apart, live and pregame
     both retained with their state, the futures on both sides of the window
     read, the politics past +96 h and a stale game left out by name, the
     receipt appended and reconciled, every request behind venue_pace.
  §5 a 429 stops the sweep's next request and applies the venue_pace circuit.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from sportsassets import venue_catalogue as vc
from sportsassets.workers import premap

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")


# ── production-shaped venue payloads ─────────────────────────────────────

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _side(ident, desc, long, team=None):
    s = {"id": "s-%s-%s" % (ident, desc), "identifier": ident,
         "description": desc, "long": long, "tradable": True,
         "marketSideType": "MARKET_SIDE_TYPE_INSTRUMENT"}
    if team:
        s["team"] = team
    return s


def _two_sided(slug, question, smt, start, a, b, *, closed=False):
    """The venue's two-sided market: BOTH sides carry the market slug as
    identifier and are told apart only by the explicit long/short marker
    (production K2a: 20,033 such markets, every one 2 rows / 2 intents)."""
    return {"id": "m-" + slug, "slug": slug, "question": question,
            "sportsMarketType": smt, "gameStartTime": _iso(start),
            "closed": closed, "active": not closed,
            "status": "MARKET_STATUS_CLOSED" if closed
            else "MARKET_STATUS_OPEN",
            "marketSides": [_side(slug, a, True), _side(slug, b, False)]}


def _event(slug, title, start, markets, *, live=None, period="NS",
           category="sports", count=None, end=None, full_markets=None,
           **extra):
    ev = {"id": "e-" + slug, "slug": slug, "title": title,
          "startTime": _iso(start), "active": True, "closed": False,
          "archived": False, "category": category, "period": period,
          "markets": markets,
          "marketCounts": {"numMarkets": len(markets) if count is None
                           else count, "numSpreadsAndTotalsMarkets": 0}}
    if live is not None:
        ev["live"] = live
    if end is not None:
        # the EVENT's own end date (fetch-docs 37233829391: 2026-10-20 on the
        # NL Champion event whose markets expire 2026-11-06)
        ev["endDate"] = _iso(end)
    if full_markets is not None:
        # what events.retrieve_by_slug returns for this event (the listing
        # carries only `markets`); never served by the list read
        ev["_full_markets"] = full_markets
    ev.update(extra)
    return ev


def _nfl(slug_core, title, start, *, live, period):
    """One NFL game as the venue lists it: the moneyline, three alternate
    spreads (asc: the side describes itself by the line, the marker names
    it), a game total and a player prop -- six markets, twelve sides."""
    d = slug_core
    ml = _two_sided("aec-" + d, "Who will win %s?" % title,
                    "football_team_full_game_winner", start,
                    "Chiefs", "Raiders")
    spreads = []
    for line in ("3pt5", "6pt5", "9pt5"):
        s = "asc-%s-kc-pos-%s" % (d, line)
        v = line.replace("pt", ".") + "0"
        spreads.append(_two_sided(
            s, "Will the Chiefs cover -%s vs the Raiders?" % v[:-1],
            "football_team_full_game_spread", start, v, v))
    total = _two_sided("tsc-%s-47pt5" % d, "%s: O/U 47.5" % title,
                       "football_team_full_game_total", start,
                       "Over 47.5", "Under 47.5")
    prop = _two_sided("astatc-%s-kelce-rec-yds-80pt5" % d,
                      "Travis Kelce receiving yards O/U 80.5",
                      "football_player_receiving_yards", start,
                      "Over 80.5", "Under 80.5")
    return _event("nfl-" + d.split("nfl-", 1)[-1], title, start,
                  [ml] + spreads + [total, prop], live=live, period=period)


def _soccer(slug, title, start, codes, *, live=False, period="NS"):
    """A three-way soccer winner: one market per outcome, each a yes/no
    pair (the atc family)."""
    mk = [_two_sided("atc-%s-%s" % (slug, c), "Will %s win?" % c,
                     "soccer_team_full_time_winner", start, "Yes", "No")
          for c in codes]
    return _event(slug, title, start, mk, live=live, period=period)


def _future(slug, title, start, teams, *, end, category="sports"):
    return _event(slug, title, start, [
        _two_sided("tec-%s-%s" % (slug, t), title, "futures", start,
                   "Yes", "No") for t in teams],
        live=False, period="NS", category=category, end=end)


def _filler(i, start):
    slug = "wtt-p%03d-q%03d-%s" % (i, i, _iso(start)[:10])
    return _event(slug, "Player %d vs. Player Q%d" % (i, i), start, [
        _two_sided("aec-" + slug, "Who will win Player %d vs Q%d?" % (i, i),
                   "table_tennis_match_winner", start,
                   "Player %d" % i, "Player Q%d" % i)],
        live=False, period="NS")


def build_board():
    """The venue's calendar on both sides of the sweep's window."""
    window, ahead, earlier = [], [], []
    # IN THE WINDOW: a live NFL game, a pregame one, two fixtures sharing one
    # title on different days, a tennis match with set markets, an event with
    # one closed market, an event with every market closed, an event whose
    # inline list is shorter than the venue's own count (the detail read
    # brings the rest), a market whose two sides normalise to the same text,
    # and 230 fillers (three pages)
    window.append(_nfl("nfl-kc-lv-2026-10-04", "KC Chiefs vs. LV Raiders",
                       NOW - timedelta(hours=1), live=True, period="Q2"))
    window.append(_nfl("nfl-den-sf-2026-10-05", "DEN Broncos vs. SF 49ers",
                       NOW + timedelta(hours=20), live=False, period="NS"))
    window.append(_soccer("mls-cla-clb-2026-10-04", "Club A vs. Club B",
                          NOW + timedelta(hours=2), ("cla", "clb", "draw")))
    window.append(_soccer("mls-cla-clb-2026-10-06", "Club A vs. Club B",
                          NOW + timedelta(hours=50), ("cla", "clb", "draw")))
    t0 = NOW + timedelta(hours=5)
    window.append(_event("atp-abc-xyz-2026-10-05", "A. Abc vs. X. Xyz", t0, [
        _two_sided("aec-atp-abc-xyz-2026-10-05", "Who will win Abc vs Xyz?",
                   "tennis_match_winner", t0, "Abc", "Xyz"),
        _two_sided("astatc-atp-abc-xyz-2026-10-05-set1", "Set 1 winner",
                   "tennis_set_1_winner", t0, "Abc", "Xyz")],
        live=False, period="NS"))
    t1 = NOW + timedelta(hours=8)
    window.append(_event("nhl-uta-nyr-2026-10-04", "UTA vs. NYR", t1, [
        _two_sided("aec-nhl-uta-nyr-2026-10-04", "Who will win UTA vs NYR?",
                   "hockey_team_full_game_winner", t1, "Utah", "Rangers"),
        _two_sided("tsc-nhl-uta-nyr-2026-10-04-5pt5", "UTA vs NYR O/U 5.5",
                   "hockey_team_full_game_total", t1, "Over 5.5", "Under 5.5",
                   closed=True)], live=False, period="NS"))
    window.append(_event("nba-old-one-2026-10-04", "Old vs. One",
                         NOW + timedelta(hours=3), [
        _two_sided("aec-nba-old-one-2026-10-04", "Who wins?",
                   "basketball_team_full_game_winner",
                   NOW + timedelta(hours=3), "Old", "One", closed=True)],
        live=False, period="NS"))
    t6 = NOW + timedelta(hours=6)
    sd_ml = _two_sided("aec-mlb-sd-mil-2026-10-04", "Who will win SD vs MIL?",
                       "baseball_team_full_game_winner", t6,
                       "Padres", "Brewers")
    sd_more = [_two_sided("tsc-mlb-sd-mil-2026-10-04-%dpt5" % n,
                          "SD vs MIL O/U %d.5" % n,
                          "baseball_team_full_game_total", t6,
                          "Over %d.5" % n, "Under %d.5" % n)
               for n in (6, 7, 8)]
    sd_more.append(_two_sided("asc-mlb-sd-mil-2026-10-04-sd-1pt5",
                              "Padres -1.5", "baseball_team_full_game_spread",
                              t6, "1.50", "1.50"))
    window.append(_event("mlb-sd-mil-2026-10-04", "SD Padres vs. MIL Brewers",
                         t6, [sd_ml], live=False, period="NS", count=5,
                         full_markets=[sd_ml] + sd_more))
    t2 = NOW + timedelta(hours=9)
    weird = {"id": "m-tsc-cs2-aa-bb-2026-10-04-maps", "slug":
             "tsc-cs2-aa-bb-2026-10-04-maps", "question": "Total maps",
             "sportsMarketType": "esports_series_total_maps",
             "gameStartTime": _iso(t2), "status": "MARKET_STATUS_OPEN",
             "marketSides": [
                 _side("tsc-cs2-aa-bb-2026-10-04-maps", "Total 2.5", True),
                 _side("tsc-cs2-aa-bb-2026-10-04-maps", "total-2.5", False)]}
    window.append(_event("cs2-aa-bb-2026-10-04", "AA vs. BB", t2, [weird],
                         live=False, period="NS"))
    for i in range(230):
        window.append(_filler(i, NOW + timedelta(hours=10, minutes=i)))
    # AHEAD (past +96 h): a sports future -- read; the midterms (politics,
    # sportsMarketType `election`) -- not; a soccer fixture filed under a
    # category word nobody observed, whose markets name the sport -- read; a
    # listing under an unrecognised word with no sports evidence -- read and
    # counted by its word
    ws = NOW + timedelta(days=20)
    ahead.append(_future("mlb-wschamp-2026-10-31", "World Series Champion",
                         ws, ("lad", "nyy", "sd", "cle"),
                         end=NOW + timedelta(days=28)))
    mid = NOW + timedelta(days=30)
    ahead.append(_event("usho-midterms-2026-11-03", "U.S House Midterm Winner",
                        mid, [_two_sided("paccc-usho-midterms-2026-11-03-dem",
                                         "U.S House Midterm Winner",
                                         "election", mid, "Yes", "No")],
                        period="NS", category="politics",
                        tags=[{"slug": "politics"}, {"slug": "midterms"}],
                        end=mid + timedelta(hours=23, minutes=59)))
    nx = NOW + timedelta(days=6)
    ahead.append(_soccer("epl-ars-che-2026-10-10", "Arsenal vs. Chelsea", nx,
                         ("ars", "che", "draw")))
    ahead[-1]["category"] = "football"
    tv = NOW + timedelta(days=8)
    ahead.append(_future("trv-quiz-2026-10-12", "Quiz Night Winner", tv,
                         ("a", "b"), end=tv + timedelta(days=1),
                         category="trivia"))
    # STARTED EARLIER (before -12 h): the NL champion future (four weeks back,
    # its event ends in 16 days) -- kept; a live T20 -- kept; a multi-day test
    # between sessions (not live, its own end five days on) -- kept; and what
    # the pass must NOT re-admit: a finished soccer game 13 h after kickoff
    # (period FT), a game 30 h old the venue does not say is live, the stale
    # KBO game (FT, 75 h), a series-winner future past its event's end, and a
    # same-day weather market (category weather, type futures)
    nl = NOW - timedelta(days=27)
    earlier.append(_future("mlb-nlchamp-2026-09-27", "National League Champion",
                           nl, ("lad", "mil", "sd", "atl"),
                           end=NOW + timedelta(days=16)))
    ck = NOW - timedelta(hours=50)
    earlier.append(_event("t20i-eng-ind-2026-10-02", "England vs. India", ck, [
        _two_sided("aec-t20i-eng-ind-2026-10-02", "Who will win ENG vs IND?",
                   "cricket_match_winner", ck, "England", "India")],
        live=True, period="D3"))
    tst = NOW - timedelta(hours=52)
    earlier.append(_event("test-aus-nz-2026-10-02", "Australia vs. New Zealand",
                          tst, [_two_sided("aec-test-aus-nz-2026-10-02",
                                           "Who will win AUS vs NZ?",
                                           "cricket_match_winner", tst,
                                           "Australia", "New Zealand")],
                          live=False, period="STUMPS",
                          end=NOW + timedelta(hours=60)))
    ft = NOW - timedelta(hours=13)
    earlier.append(_soccer("isthp-ave-sta-2026-10-03", "Aveley vs. St Albans",
                           ft, ("ave", "sta", "draw"), live=False, period="FT"))
    gone = NOW - timedelta(hours=30)
    earlier.append(_event("acb-rma-bar-2026-10-03", "Madrid vs. Barcelona",
                          gone, [_two_sided("aec-acb-rma-bar-2026-10-03",
                                            "Who will win RMA vs BAR?",
                                            "basketball_team_full_game_winner",
                                            gone, "Madrid", "Barcelona")],
                          live=False, period=""))
    old = NOW - timedelta(hours=75)
    earlier.append(_event("kbo-kia-lg-2026-10-01", "KIA vs. LG", old, [
        _two_sided("aec-kbo-kia-lg-2026-10-01", "Who will win KIA vs LG?",
                   "baseball_team_full_game_winner", old, "KIA", "LG")],
        live=False, period="FT"))
    sw = NOW - timedelta(days=3)
    earlier.append(_future("mlb-alds-cws-cle-2026-10-01-w",
                           "ALDS Winner: CHI White Sox vs CLE Guardians", sw,
                           ("cws", "cle"), end=NOW - timedelta(hours=1)))
    wx = NOW - timedelta(hours=14)
    earlier.append(_future("temp-nychigh-2026-10-04",
                           "Highest temperature in NYC on October 4?", wx,
                           ("80", "81"), end=NOW + timedelta(hours=10),
                           category="weather"))
    return window, ahead, earlier


class FakeVenue:
    """events.list over the board, the way the venue answers it: the
    start-time window, active / closed, then offset + limit over a stable
    order -- and an optional page-size cap, as a venue may impose -- plus
    events.retrieve_by_slug (the event with every market)."""

    def __init__(self, events, *, page_cap=None, fail_on=None, delay_s=0.0):
        self.events = sorted(events, key=lambda e: e["id"])
        self.page_cap = page_cap
        self.calls = []
        self.fail_on = fail_on          # predicate(q) -> exception or None
        self.delay_s = delay_s
        venue = self

        class _E:
            def list(self, q):
                return venue.list(q)

            def retrieve_by_slug(self, slug):
                return venue.by_slug(slug)

        class _M:
            def list(self, q):
                venue.calls.append(("markets", dict(q)))
                return {"markets": []}

        self.client = type("C", (), {"events": _E(), "markets": _M()})()

    @staticmethod
    def _served(e, *, full=False):
        e = json.loads(json.dumps(e))
        fm = e.pop("_full_markets", None)
        if full and fm is not None:
            e["markets"] = fm
        return e

    def by_slug(self, slug):
        self.calls.append(("detail", slug))
        for e in self.events:
            if e["slug"] == slug:
                return {"event": self._served(e, full=True)}
        raise RuntimeError("404")

    def list(self, q):
        self.calls.append(("events", dict(q)))
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail_on is not None:
            exc = self.fail_on(q)
            if exc is not None:
                raise exc
        lo = q.get("startTimeMin")
        hi = q.get("startTimeMax")

        def _in(e):
            t = e["startTime"]
            return (lo is None or t >= lo) and (hi is None or t <= hi)

        rows = [e for e in self.events if _in(e)]
        off = int(q.get("offset") or 0)
        n = int(q.get("limit") or 100)
        if self.page_cap:
            n = min(n, self.page_cap)
        return {"events": [self._served(e) for e in rows[off:off + n]]}

    def requests(self):
        """Every request the venue received (list and detail reads)."""
        return [c for c in self.calls if c[0] in ("events", "detail")]


# ── §1 pagination ────────────────────────────────────────────────────────

def _drive(walk, board, *, size, cap=None, first=True):
    """Drive a PageWalk over a list the way refresh does."""
    got = []

    def page(off):
        n = min(size, cap) if cap else size
        return board[off:off + n]

    if first:
        got += walk.first(page(0))
    while (off := walk.next_offset()) is not None:
        got += walk.accept(page(off))
    return got


def _ev(i):
    return {"slug": "ev-%04d" % i}


def test_a_multi_page_board_is_read_to_its_short_last_page():
    board = [_ev(i) for i in range(237)]
    walk = vc.PageWalk(limit=100, max_requests=50)
    got = _drive(walk, board, size=100)
    assert [e["slug"] for e in got] == [e["slug"] for e in board]
    r = walk.receipt()
    assert r["stopped"] == vc.STOP_SHORT_PAGE and r["natural_end"]
    assert r["truncated"] is False and r["events_unique"] == 237
    assert r["complete"] is True and r["shift_suspected"] == 0
    # the overlap re-reads five per page and never writes them twice
    assert r["duplicates_across_pages"] == r["events_received"] - 237 > 0


def test_a_venue_page_cap_below_the_request_is_not_read_as_the_end():
    """THE FIRST-PAGE-ONLY CLASS. The old loop stopped on any page shorter
    than it asked for; a venue serving 40 events per page to a request for
    100 would have ended every sweep after page one, reporting
    truncated=False. The walk confirms a short first page with one more read
    and continues at the venue's size."""
    board = [_ev(i) for i in range(173)]

    def legacy_rule():
        out, off = [], 0
        for _ in range(50):
            got = board[off:off + 40]          # the venue's cap
            if not got:
                break
            out += got
            off += len(got)
            if len(got) < 100:                 # premap's old stop rule
                break
        return out

    assert len(legacy_rule()) == 40, "the defect, reproduced: one page"
    walk = vc.PageWalk(limit=100, max_requests=50)
    got = _drive(walk, board, size=100, cap=40)
    assert len({e["slug"] for e in got}) == 173
    r = walk.receipt()
    assert r["venue_page_cap"] == 40 and r["natural_end"]


def test_a_single_short_page_costs_one_confirmation_read_and_ends_there():
    board = [_ev(i) for i in range(12)]
    walk = vc.PageWalk(limit=100, max_requests=50)
    got = _drive(walk, board, size=100)
    assert len(got) == 12
    r = walk.receipt()
    assert r["requests"] == 2 and r["stopped"] == vc.STOP_EMPTY_PAGE


def _walk_with_deletions(overlap, deleted, n=250):
    board = [_ev(i) for i in range(n)]
    walk = vc.PageWalk(limit=100, max_requests=50, overlap=overlap)
    got = walk.first(board[0:100])
    del board[10:10 + deleted]             # games close ahead of the cursor
    while (off := walk.next_offset()) is not None:
        got += walk.accept(board[off:off + 100])
    return {e["slug"] for e in got}, walk


def test_an_event_that_shifts_across_a_page_boundary_is_caught():
    """Games close while the sweep walks the board. Three deletions ahead of
    the cursor between page 1 and page 2 shift every later event back three
    positions; without an overlap the three events that slid across the
    boundary are never read."""
    seen0, _ = _walk_with_deletions(0, 3)
    missing = {"ev-%04d" % i for i in range(250)} - {
        "ev-0010", "ev-0011", "ev-0012"} - seen0
    assert missing == {"ev-0100", "ev-0101", "ev-0102"}, missing
    seen5, w5 = _walk_with_deletions(vc.PAGE_OVERLAP, 3)
    assert {"ev-0100", "ev-0101", "ev-0102"} <= seen5
    r = w5.receipt()
    assert r["overlap_catches"] == 3 and r["rewind_reads"] == 0


def test_a_shift_larger_than_the_overlap_is_detected_and_re_read():
    """THE OVERLAP'S BOUND (adversarial review: eight deletions between two
    pages lost three events while the receipt said COMPLETE). A page that
    re-reads none of the previous page's tail is a shift of the overlap or
    more: the walk re-reads one page back and resumes."""
    seen, walk = _walk_with_deletions(vc.PAGE_OVERLAP, 8)
    survivors = {"ev-%04d" % i for i in range(250)} - {
        "ev-%04d" % i for i in range(10, 18)}
    assert survivors <= seen, sorted(survivors - seen)
    r = walk.receipt()
    assert r["shift_suspected"] == 1 and r["rewind_reads"] == 1
    assert r["rewind_catches"] == 3 and r["shift_unrecovered"] == 0
    assert r["complete"] is True


def test_a_shift_the_rewind_cannot_reach_leaves_the_pass_incomplete():
    """150 already-read events close between pages 2 and 3: 145 unread
    events slide back past the cursor, more than one page -- the rewind
    reaches 100 of them and cannot reach the rest, and says so."""
    board = [_ev(i) for i in range(600)]
    walk = vc.PageWalk(limit=100, max_requests=50)
    got = walk.first(board[0:100])
    off = walk.next_offset()
    got += walk.accept(board[off:off + 100])
    del board[20:170]
    while (off := walk.next_offset()) is not None:
        got += walk.accept(board[off:off + 100])
    seen = {e["slug"] for e in got}
    assert "ev-0195" not in seen and "ev-0250" in seen
    r = walk.receipt()
    assert r["natural_end"] is True
    assert r["shift_unrecovered"] >= 1 and r["complete"] is False
    t = vc.CompletenessTally(lane="full")
    t.set_pass(vc.PASS_WINDOW, r)
    assert t.complete() is False and t.outcome() == "FAILED"   # nothing tallied
    t.event_dropped({"slug": "x"}, vc.D_EVENT_CLOSED)
    assert t.outcome() == "PARTIAL"


def test_a_budget_that_runs_out_on_full_pages_is_truncated_by_name():
    board = [_ev(i) for i in range(1000)]
    walk = vc.PageWalk(limit=100, max_requests=3)
    _drive(walk, board, size=100)
    r = walk.receipt()
    assert r["stopped"] == vc.STOP_BUDGET and r["truncated"] is True
    assert r["natural_end"] is False and r["requests"] == 3


def test_every_probe_rung_is_a_counted_request():
    walk = vc.PageWalk(limit=100, max_requests=10)
    walk.probe_failed()
    walk.probe_rejected()
    walk.first([_ev(i) for i in range(20)])
    while (off := walk.next_offset()) is not None:
        walk.accept([])
    r = walk.receipt()
    assert r["requests"] == 4 and r["pages_with_events"] == 1
    assert r["probe_requests_failed"] == 1 and r["probe_requests_rejected"] == 1


def test_a_deadline_stops_the_walk_as_truncated():
    clock = [0.0]
    walk = vc.PageWalk(limit=100, max_requests=50, deadline=5.0,
                       clock=lambda: clock[0])
    walk.first([_ev(i) for i in range(100)])
    clock[0] = 6.0
    assert walk.next_offset() is None
    r = walk.receipt()
    assert r["stopped"] == vc.STOP_WALL_TIME and r["truncated"] is True


def test_calendar_slices_tile_the_range_nearest_first():
    s = vc.calendar_slices(96.0, 400 * 24.0, vc.AHEAD_SLICE_BOUNDS_H)
    assert s[0] == (96.0, 14 * 24.0) and s[-1][1] == 400 * 24.0
    assert all(a[1] == b[0] for a, b in zip(s, s[1:]))
    e = vc.calendar_slices(12.0, 200 * 24.0, vc.EARLIER_SLICE_BOUNDS_H)
    assert e[0] == (12.0, 36.0) and e[-1] == (30 * 24.0, 200 * 24.0)


def test_a_pass_rolled_up_from_slices_names_what_it_did_not_read():
    nat = {"stopped": vc.STOP_SHORT_PAGE, "requests": 2, "pages_with_events": 2}
    cut = {"stopped": vc.STOP_BUDGET, "requests": 3, "pages_with_events": 3,
           "truncated": True}
    unread = [{"hours_from_now": [-4800.0, -720.0], "why": vc.STOP_BUDGET}]
    r = vc.rollup_slices([nat, cut], not_read=unread)
    assert r["stopped"] == vc.STOP_BUDGET and r["truncated"] is True
    assert r["requests"] == 5 and r["slices_not_read"] == unread
    late = vc.rollup_slices([nat], not_read=[{"why": vc.STOP_WALL_TIME}])
    assert late["stopped"] == vc.STOP_WALL_TIME and late["truncated"] is True
    ok = vc.rollup_slices([nat, dict(nat, stopped=vc.STOP_EMPTY_PAGE)])
    assert ok["complete"] is True and ok["stopped"] == vc.STOP_EMPTY_PAGE


# ── §2 classification and state ──────────────────────────────────────────

@pytest.mark.parametrize("smt,fam", [
    # every family checked against the 215 types production held on
    # 2026-10-04 (research-sql run 37238634518 R1); a sample of each, and the
    # three whole-match markets the old substring rule filed as periods
    ("football_team_full_game_winner", vc.F_WINNER),
    ("hockey_team_regulation_winner", vc.F_WINNER),
    ("football_team_full_game_spread", vc.F_SPREAD),
    ("tennis_match_sets_spread", vc.F_SPREAD),
    ("esports_series_map_handicap", vc.F_SPREAD),
    ("esports_series_game_handicap", vc.F_SPREAD),
    ("snooker_frame_handicap", vc.F_SPREAD),
    ("tennis_set_handicap", vc.F_SPREAD),
    ("football_team_full_game_total", vc.F_TOTAL),
    ("esports_series_total_maps", vc.F_TOTAL),
    ("table_tennis_match_total_sets", vc.F_TOTAL),
    ("football_team_points_full_game_total", vc.F_TEAM_TOTAL),
    ("hockey_team_total_goals", vc.F_TEAM_TOTAL),
    ("baseball_team_total_runs", vc.F_TEAM_TOTAL),
    ("football_team_first_half_total", vc.F_PERIOD),
    ("hockey_team_first_period_winner", vc.F_PERIOD),
    ("tennis_set_1_winner", vc.F_PERIOD),
    ("table_tennis_set_3_winner", vc.F_PERIOD),
    ("baseball_team_inning3_winner", vc.F_PERIOD),
    ("baseball_team_first_five_total", vc.F_PERIOD),
    ("esports_map_winner_1", vc.F_PERIOD),
    ("esports_map_total_rounds_2", vc.F_PERIOD),
    ("esports_game_total_kills_3", vc.F_PERIOD),
    ("football_player_receiving_yards", vc.F_PLAYER_PROP),
    ("soccer_game_exact_score", vc.F_MULTI_OUTCOME),
    ("football_game_exact_margin", vc.F_MULTI_OUTCOME),
    ("futures", vc.F_FUTURES),
    ("election", vc.F_YES_NO),
    ("soccer_game_btts", vc.F_YES_NO),
    ("baseball_game_extra_innings", vc.F_YES_NO),
    ("football_game_race_to_points", vc.F_OTHER),
    (None, vc.F_NO_TYPE),
])
def test_the_family_is_read_off_the_venues_own_type(smt, fam):
    assert vc.family_of(smt) == fam


def test_the_venues_live_word_comes_before_the_schedule():
    now = time.time()
    past, future = now - 3600, now + 3600
    ev = lambda **k: dict({"startTime": _iso(datetime.fromtimestamp(
        k.pop("start"), timezone.utc))}, **k)
    assert vc.listing_state(ev(start=past, live=True), now) == (
        vc.S_LIVE, vc.SRC_VENUE_LIVE_FLAG)
    assert vc.listing_state(ev(start=future, live=False, period="NS"), now) == (
        vc.S_PREGAME, vc.SRC_VENUE_LIVE_FLAG)
    assert vc.listing_state(ev(start=past, live=False, period="HT"), now) == (
        vc.S_NOT_LIVE, vc.SRC_VENUE_LIVE_FLAG)
    # a FINISHED game the venue has not closed is ENDED by its period word,
    # never "not live" (adversarial review: period FT was stored NOT_LIVE)
    assert vc.listing_state(ev(start=past, live=False, period="FT"), now) == (
        vc.S_ENDED, vc.SRC_VENUE_PERIOD)
    assert vc.listing_state(ev(start=past, ended=True, live=True), now) == (
        vc.S_ENDED, vc.SRC_VENUE_ENDED_FLAG)
    # `live` is not on every event: the politics events carried a period
    # word and no `live` key (fetch-docs 37233823157)
    assert vc.listing_state(ev(start=future, period="NS"), now) == (
        vc.S_PREGAME, vc.SRC_VENUE_PERIOD)
    assert vc.listing_state(ev(start=future), now) == (
        vc.S_PREGAME, vc.SRC_SCHEDULE)
    assert vc.listing_state(ev(start=past), now) == (
        vc.S_STARTED, vc.SRC_SCHEDULE)
    assert vc.listing_state({}, now) == (vc.S_UNKNOWN, vc.SRC_NONE)


def test_the_category_rule_denies_only_what_is_known_not_to_be_sport():
    pol = {"slug": "usho", "category": "politics", "tags": [{"slug": "politics"}],
           "markets": [{"sportsMarketType": "election"}]}
    assert vc.event_drop_reason(pol, pass_name=vc.PASS_AHEAD) == \
        vc.D_EVENT_OUT_OF_SCOPE_CATEGORY
    # the window keeps every category it always kept
    assert vc.event_drop_reason(pol, pass_name=vc.PASS_WINDOW) is None
    # a sport is never dropped on a guessed word: sports evidence wins
    for ev in ({"slug": "x", "category": "sports"},
               {"slug": "x", "category": "esports"},
               {"slug": "x", "category": None},
               {"slug": "x", "category": "soccer",
                "markets": [{"sportsMarketType": "soccer_team_full_time_winner"}]},
               {"slug": "x", "category": "politics",
                "tags": [{"slug": "leagues", "subtags": [{"slug": "sports"}]}]},
               {"slug": "x", "category": "trivia",
                "markets": [{"sportsMarketType": "futures"}]}):
        assert vc.event_drop_reason(ev, pass_name=vc.PASS_AHEAD) is None, ev
    assert vc.category_verdict({"category": "trivia"}) == "UNRECOGNISED"
    # a weather market is a `futures` type (research-sql 37236398336 B1b):
    # the type names no sport, the word is a known non-sports one
    wx = {"slug": "temp-nychigh", "category": "weather",
          "markets": [{"sportsMarketType": "futures"}]}
    assert vc.event_drop_reason(wx, pass_name=vc.PASS_STARTED_EARLIER) == \
        vc.D_EVENT_OUT_OF_SCOPE_CATEGORY


def _at(h):
    return _iso(datetime.fromtimestamp(time.time() + h * 3600, timezone.utc))


@pytest.mark.parametrize("hours_after_start", [13, 30, 47])
def test_a_finished_game_12_to_48_hours_old_is_not_re_admitted(hours_after_start):
    """THE 48-HOUR RE-ADMISSION, CLOSED (adversarial review: a soccer winner
    with live false and period FT was KEPT at 13 h, 30 h and 47 h and stored
    NOT_LIVE). The pass exists for still-running futures and live or
    multi-day events, and a finished game is neither."""
    now = time.time()
    ev = {"slug": "g", "startTime": _at(-hours_after_start), "live": False,
          "period": "FT"}
    game = {"sportsMarketType": "soccer_team_full_time_winner"}
    assert vc.market_drop_reason(ev, game, pass_name=vc.PASS_STARTED_EARLIER,
                                 now=now) == vc.D_MARKET_GAME_FINISHED
    quiet = dict(ev, period="")
    assert vc.market_drop_reason(quiet, game, pass_name=vc.PASS_STARTED_EARLIER,
                                 now=now) == vc.D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW
    assert vc.event_reason_for_markets([vc.D_MARKET_GAME_FINISHED]) == \
        vc.D_EVENT_FINISHED


def test_what_started_earlier_keeps_and_why():
    now = time.time()
    game = {"sportsMarketType": "cricket_match_winner"}
    fut = {"sportsMarketType": "futures"}
    se = vc.PASS_STARTED_EARLIER
    # a live long match
    live = {"slug": "c", "startTime": _at(-50), "live": True}
    assert vc.market_drop_reason(live, game, pass_name=se, now=now) is None
    # a multi-day test BETWEEN SESSIONS: not live, its own end still ahead,
    # scheduled over days (adversarial review: dropped at stumps before)
    stumps = {"slug": "t", "startTime": _at(-52), "live": False,
              "period": "STUMPS", "endDate": _at(60)}
    assert vc.market_drop_reason(stumps, game, pass_name=se, now=now) is None
    # a one-day event whose end is still ahead is not multi-day
    oneday = dict(stumps, startTime=_at(-13), endDate=_at(10))
    assert vc.market_drop_reason(oneday, game, pass_name=se, now=now) == \
        vc.D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW
    # a future while its event's own end is ahead (or unstated) -- and not
    # after it
    nl = {"slug": "f", "startTime": _at(-27 * 24), "endDate": _at(16 * 24)}
    assert vc.market_drop_reason(nl, fut, pass_name=se, now=now) is None
    assert vc.market_drop_reason({"slug": "f", "startTime": _at(-900)}, fut,
                                 pass_name=se, now=now) is None
    over = dict(nl, endDate=_at(-1))
    assert vc.market_drop_reason(over, fut, pass_name=se, now=now) == \
        vc.D_MARKET_FUTURE_PAST_END
    # a market's own endDate is its expiry and is never read as the event's
    assert vc.event_end({"markets": [{"endDate": _at(900)}]}) is None
    # a suspended in-play market is KEPT on every pass (a live market for a moment)
    assert vc.market_drop_reason(live, dict(game, status="MARKET_STATUS_SUSPENDED"),
                                 pass_name=vc.PASS_WINDOW, now=now) is None


# ── §3 keys ──────────────────────────────────────────────────────────────

def test_two_sides_of_one_market_with_one_text_are_both_kept():
    g = vc.SideKeyGuard()
    a = {"identifier": "tsc-x", "side_norm": "total 2 5", "market_slug": "tsc-x",
         "intent": "ORDER_INTENT_BUY_LONG"}
    b = dict(a, intent="ORDER_INTENT_BUY_SHORT")
    r1, f1 = g.admit(a)
    assert r1 is a and f1 is None
    r2, f2 = g.admit(b)
    assert r2["side_norm"] == "total 2 5 [short]"
    assert f2["rewrite"]["side_norm"] == "total 2 5 [long]"
    assert f2["delete_side_norm"] == "total 2 5"
    assert g.receipt()["qualified_pairs"] == 1


def test_a_key_reused_by_another_market_is_refused_not_overwritten():
    g = vc.SideKeyGuard()
    a = {"identifier": "x", "side_norm": "yes", "market_slug": "m1",
         "intent": "ORDER_INTENT_BUY_LONG"}
    assert g.admit(a)[0] is a
    r, fix = g.admit(dict(a, market_slug="m2"))
    assert r is None and fix is None
    assert g.receipt()["refused_cross_market"] == 1
    # the venue listing one side twice is one row, nothing lost
    assert g.admit(dict(a))[0] is None and g.receipt()["refused_cross_market"] == 1


def test_the_tally_reconciles_and_stays_bounded():
    t = vc.CompletenessTally(lane="full")
    for i in range(vc.MAX_RECEIPT_CELLS + 40):
        t.market_seen(sport="s", league="l%d" % i, family=vc.F_WINNER)
        t.market_kept(sport="s", league="l%d" % i, family=vc.F_WINNER, sides=2)
    t.market_seen(sport="s", league="z", family=vc.F_TOTAL)
    t.market_dropped(sport="s", league="z", family=vc.F_TOTAL,
                     reason=vc.D_MARKET_CLOSED)
    t.extra_request("event_detail", 3)
    t.set_pass(vc.PASS_WINDOW, {"requests": 5, "pages_with_events": 4,
                                "stopped": vc.STOP_SHORT_PAGE})
    r = t.receipt()
    assert r["markets"]["seen"] == r["markets"]["kept"] + r["markets"]["dropped"]
    assert r["requests"] == 8            # the detail reads are requests too
    cells = r["by_sport_league_family"]
    assert len(cells) == vc.MAX_RECEIPT_CELLS + 1 and "_other" in cells
    assert sum(c["seen"] for c in cells.values()) == r["markets"]["seen"]


# ── §4 the writer end to end on Postgres ─────────────────────────────────

class _ConnPool:
    """premap's pool interface over ONE connection inside a transaction."""

    def __init__(self, conn):
        self.conn = conn

    async def execute(self, sql, *a):
        return await self.conn.execute(sql, *a)

    async def fetchval(self, sql, *a):
        return await self.conn.fetchval(sql, *a)

    async def fetch(self, sql, *a):
        return await self.conn.fetch(sql, *a)


def _wire(monkeypatch, conn, venue):
    claims = []
    from sportsassets import venue_pace

    real_pace = venue_pace.pace
    monkeypatch.setattr(venue_pace, "pace",
                        lambda gap=venue_pace.MIN_GAP_S, **k: (
                            claims.append(gap), real_pace(0.0))[1])
    monkeypatch.setattr(premap.pmus, "_get_client", lambda: venue.client)
    monkeypatch.setattr(premap, "get_pool",
                        lambda: asyncio.sleep(0, result=_ConnPool(conn)))
    monkeypatch.setattr(premap, "LIST_PACING_S", 0.0)
    return claims


async def _run_refresh(monkeypatch, conn, venue, **kw):
    claims = _wire(monkeypatch, conn, venue)
    summary = await premap.refresh(**kw)
    return summary, claims


async def _run_both(monkeypatch, conn, venue):
    """The full sweep, then the calendar lane -- what _full_loop runs."""
    claims = _wire(monkeypatch, conn, venue)
    full = await premap.refresh()
    n_full = len(claims)
    cal = await premap.calendar_refresh()
    return full, cal, claims, n_full


async def _tx():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    # a clean catalogue inside the transaction, so the counts below are the
    # sweep's alone
    await conn.execute("DELETE FROM us_premap")
    return conn, tx


@pg
async def test_the_writer_keeps_every_tradable_listing_with_its_state(monkeypatch):
    window, ahead, earlier = build_board()
    venue = FakeVenue(window + ahead + earlier)
    conn, tx = await _tx()
    try:
        full, cal, claims, n_full = await _run_both(monkeypatch, conn, venue)
        rows = await conn.fetch(
            "SELECT identifier, side_norm, intent, event_slug, market_slug, "
            "sports_type, listing_state, listing_state_source, listing_pass "
            "FROM us_premap")
        by_market: dict = {}
        for r in rows:
            by_market.setdefault(r["market_slug"], []).append(r)
        by_event: dict = {}
        for r in rows:
            by_event.setdefault(r["event_slug"], []).append(r)

        # EVERY MARKET OF THE EVENT, not the first: the live NFL game's six
        # markets, twelve sides, each with its own intent
        kc = by_event["nfl-kc-lv-2026-10-04"]
        assert len({r["market_slug"] for r in kc}) == 6 and len(kc) == 12
        for m in {r["market_slug"] for r in kc}:
            assert sorted(r["intent"] for r in by_market[m]) == [
                "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"], m
        # the three alternate spreads stay three markets (asc yes/no rows)
        assert len([m for m in by_market if m.startswith("asc-nfl-kc-lv")]) == 3
        # LIVE AND PREGAME BOTH RETAINED, with the venue's word
        assert {(r["listing_state"], r["listing_state_source"]) for r in kc} \
            == {(vc.S_LIVE, vc.SRC_VENUE_LIVE_FLAG)}
        den = by_event["nfl-den-sf-2026-10-05"]
        assert len(den) == 12 and {r["listing_state"] for r in den} == {
            vc.S_PREGAME}
        # TWO FIXTURES, ONE TITLE: both kept, apart
        assert len(by_event["mls-cla-clb-2026-10-04"]) == 6
        assert len(by_event["mls-cla-clb-2026-10-06"]) == 6
        # the closed market out, its open sibling in; the all-closed event out
        assert "aec-nhl-uta-nyr-2026-10-04" in by_market
        assert "tsc-nhl-uta-nyr-2026-10-04-5pt5" not in by_market
        assert "aec-nba-old-one-2026-10-04" not in by_market
        # THE PER-EVENT CAP, REPAIRED: the listing carried 1 of the venue's 5
        # markets; the detail read brought the other 4, every one written
        sd = {r["market_slug"] for r in by_event["mlb-sd-mil-2026-10-04"]}
        assert len(sd) == 5 and ("detail", "mlb-sd-mil-2026-10-04") in venue.calls
        # THE SIDES THAT NORMALISE ALIKE: both kept, each named by its marker
        weird = sorted(r["side_norm"] for r in by_market[
            "tsc-cs2-aa-bb-2026-10-04-maps"])
        assert weird == ["total 2 5 [long]", "total 2 5 [short]"], weird
        assert sum(1 for m in by_market if m.startswith("aec-wtt-")) == 230

        # THE CALENDAR ON BOTH SIDES OF THE WINDOW (the calendar lane)
        ws = by_event["mlb-wschamp-2026-10-31"]
        assert len({r["market_slug"] for r in ws}) == 4
        assert {r["listing_pass"] for r in ws} == {vc.PASS_AHEAD}
        assert len(by_event["epl-ars-che-2026-10-10"]) == 6     # category "football"
        assert len(by_event["trv-quiz-2026-10-12"]) == 4        # unrecognised word, kept
        nl = by_event["mlb-nlchamp-2026-09-27"]
        assert len({r["market_slug"] for r in nl}) == 4
        assert {r["listing_pass"] for r in nl} == {vc.PASS_STARTED_EARLIER}
        ck = by_event["t20i-eng-ind-2026-10-02"]
        assert len(ck) == 2 and {r["listing_state"] for r in ck} == {vc.S_LIVE}
        tst = by_event["test-aus-nz-2026-10-02"]            # between sessions
        assert len(tst) == 2 and {(r["listing_state"], r["listing_state_source"])
                                  for r in tst} == {(vc.S_NOT_LIVE,
                                                     vc.SRC_VENUE_LIVE_FLAG)}
        # left out, by name, never silently -- and no finished game re-admitted
        for gone in ("usho-midterms-2026-11-03", "kbo-kia-lg-2026-10-01",
                     "isthp-ave-sta-2026-10-03", "acb-rma-bar-2026-10-03",
                     "mlb-alds-cws-cle-2026-10-01-w", "temp-nychigh-2026-10-04"):
            assert gone not in by_event, gone
        # every identifier is one the venue wrote on a side
        venue_idents = {s["identifier"] for e in window + ahead + earlier
                        for m in (e.get("_full_markets") or e["markets"])
                        for s in m["marketSides"]}
        assert {r["identifier"] for r in rows} <= venue_idents

        # THE FULL LANE'S RECEIPT: the window only
        rec = full["completeness"]
        assert rec["outcome"] == "COMPLETE" and rec["complete"] is True
        assert full["truncated"] is False and full["mode"] == "events"
        assert full["calendar_eligible"] is True
        assert set(rec["passes"]) == {vc.PASS_WINDOW}
        assert rec["passes"][vc.PASS_WINDOW]["pages_with_events"] >= 3
        for k in ("events", "markets"):
            assert rec[k]["seen"] == rec[k]["kept"] + rec[k]["dropped"]
        assert rec["events"]["dropped_by_reason"] == {vc.D_EVENT_NO_OPEN_MARKET: 1}
        assert rec["markets"]["dropped_by_reason"] == {vc.D_MARKET_CLOSED: 2}
        mc = rec["venue_market_counts"]
        assert mc["events_inline_fewer_than_venue_count"] == 1
        assert mc["markets_missing_inline"] == 4
        assert mc["markets_recovered_by_detail"] == 4 and mc["detail_reads"] == 1
        assert mc["events_still_short_after_detail"] == 0
        assert rec["requests_outside_page_walks"] == {"event_detail": 1}
        assert full["side_keys"]["qualified_pairs"] == 1
        # EVERY REQUEST BEHIND THE PROCESS-WIDE GATE, every one receipted
        assert n_full == rec["requests"]

        # THE CALENDAR LANE'S RECEIPT
        crec = cal["completeness"]
        assert cal["lane"] == "calendar" and crec["lane"] == "calendar"
        assert set(crec["passes"]) == {vc.PASS_AHEAD, vc.PASS_STARTED_EARLIER}
        assert crec["outcome"] == "COMPLETE", crec["passes"]
        for k in ("events", "markets"):
            assert crec[k]["seen"] == crec[k]["kept"] + crec[k]["dropped"]
        ed = crec["events"]["dropped_by_reason"]
        assert ed == {vc.D_EVENT_OUT_OF_SCOPE_CATEGORY: 2,     # midterms, weather
                      vc.D_EVENT_FINISHED: 2,                  # Aveley FT, KBO FT
                      vc.D_EVENT_NOT_LIVE_BEFORE_WINDOW: 1,    # the 30 h game
                      vc.D_EVENT_PAST_END: 1}, ed               # the ALDS future
        assert crec["notes"]["category_dropped:politics"] == 1
        assert crec["notes"]["category_dropped:weather"] == 1
        assert crec["notes"]["category_unrecognised_kept:trivia"] == 1
        md = crec["markets"]["dropped_by_reason"]
        assert md == {vc.D_MARKET_GAME_FINISHED: 4,
                      vc.D_MARKET_GAME_NOT_LIVE_BEFORE_WINDOW: 1,
                      vc.D_MARKET_FUTURE_PAST_END: 2}, md
        assert crec["states"][vc.S_LIVE] == 1                  # the T20
        # each STARTED_EARLIER keep rule counted by sport (markets)
        n = crec["notes"]
        assert n["started_earlier_kept:FUTURE_EVENT_STILL_RUNNING:futures"] == 4
        assert n["started_earlier_kept:VENUE_FLAGS_LIVE:cricket"] == 1
        assert n["started_earlier_kept:MULTI_DAY_SCHEDULE:cricket"] == 1
        # nearest slice first, every slice named, the durations measured
        ah = crec["passes"][vc.PASS_AHEAD]
        assert ah["slices"][0]["hours_from_now"] == [96.0, 14 * 24.0]
        assert "duration_s" in ah and ah["slices_not_read"] == []
        se = crec["passes"][vc.PASS_STARTED_EARLIER]
        assert se["slices"][0]["hours_from_now"] == [-36.0, -12.0]
        assert len(claims) - n_full == crec["requests"]
        assert crec["requests"] == len(venue.requests()) - n_full

        # both receipts are in the append-only history, reconciled by its CHECKs
        hist = await conn.fetch(
            "SELECT lane, outcome, events_seen, sides_written, truncated "
            "FROM venue_catalogue_receipts ORDER BY id")
        assert [(h["lane"], h["outcome"]) for h in hist] == [
            ("full", "COMPLETE"), ("calendar", "COMPLETE")]
        assert hist[0]["events_seen"] == rec["events"]["seen"]
        assert sum(h["sides_written"] for h in hist) == len(rows)
        st = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = 'premap_last'")
        st = json.loads(st) if isinstance(st, str) else st
        assert st["completeness"]["outcome"] == "COMPLETE"
        cst = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = 'premap_last_calendar'")
        cst = json.loads(cst) if isinstance(cst, str) else cst
        assert cst["lane"] == "calendar" and cst["truncated"] is False
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_the_fast_lane_keeps_its_old_authority_and_a_compact_receipt(
        monkeypatch):
    window, ahead, earlier = build_board()
    venue = FakeVenue(window + ahead + earlier)
    conn, tx = await _tx()
    try:
        summary, claims = await _run_refresh(
            monkeypatch, conn, venue, back_h=3.0, fwd_h=14.0, max_pages=25,
            prune=False, windowed_only=True, state_key="premap_last_fast")
        assert summary["lane"] == "fast" and summary["calendar_eligible"] is False
        assert set(summary["completeness"]["passes"]) == {vc.PASS_FAST}
        assert "by_sport_league_family" not in summary["completeness"]
        passes = {r["listing_pass"] for r in await conn.fetch(
            "SELECT DISTINCT listing_pass FROM us_premap")}
        assert passes == {vc.PASS_FAST}
        # every request a windowed one: no unbounded rung, no calendar pass
        assert all("startTimeMin" in q for k, q in venue.calls if k == "events")
        assert len(claims) == summary["completeness"]["requests"]
        # no detail read every 180 s: the short event is counted, the full
        # lane repairs it
        assert not [c for c in venue.calls if c[0] == "detail"]
        assert summary["completeness"]["venue_market_counts"][
            "detail_reads_skipped_budget"] == 1
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_venue_page_cap_does_not_shrink_the_catalogue(monkeypatch):
    """The same board served 40 events per page: every listing still lands."""
    window, ahead, earlier = build_board()
    full = FakeVenue(window + ahead + earlier)
    capped = FakeVenue(window + ahead + earlier, page_cap=40)
    counts = []
    for venue in (full, capped):
        conn, tx = await _tx()
        try:
            s, c, _, _ = await _run_both(monkeypatch, conn, venue)
            counts.append((await conn.fetchval("SELECT count(*) FROM us_premap"),
                           s["completeness"]["passes"][vc.PASS_WINDOW]))
        finally:
            await tx.rollback()
            await conn.close()
    assert counts[0][0] == counts[1][0] > 0
    assert counts[1][1]["venue_page_cap"] == 40


@pg
async def test_with_no_detail_budget_the_short_event_is_counted_not_guessed(
        monkeypatch):
    window, _, _ = build_board()
    venue = FakeVenue(window)
    monkeypatch.setattr(premap, "DETAIL_READS_PER_REFRESH", 0)
    conn, tx = await _tx()
    try:
        s, claims = await _run_refresh(monkeypatch, conn, venue)
        mc = s["completeness"]["venue_market_counts"]
        assert mc["events_still_short_after_detail"] == 1
        assert mc["markets_still_missing_after_detail"] == 4
        assert mc["detail_reads_skipped_budget"] == 1
        assert not [c for c in venue.calls if c[0] == "detail"]
        assert len(claims) == s["completeness"]["requests"]
    finally:
        await tx.rollback()
        await conn.close()


# ── §5 one bad listing, and a database that refuses every write ─────────

def _breaking_rows(monkeypatch, bad_slug):
    real = premap._market_rows

    def rows(ev, m):
        if (ev.get("slug") or ev.get("eventSlug")) == bad_slug:
            raise ValueError("malformed listing %s" % bad_slug)
        return real(ev, m)

    monkeypatch.setattr(premap, "_market_rows", rows)


@pg
@pytest.mark.parametrize("bad_slug,lane", [
    ("nfl-den-sf-2026-10-05", "full"),
    ("mlb-wschamp-2026-10-31", "calendar"),
    ("mlb-nlchamp-2026-09-27", "calendar")])
async def test_a_malformed_listing_in_any_pass_still_ends_in_a_receipt(
        monkeypatch, bad_slug, lane):
    """ADVERSARIAL REVIEW: a listing that failed to write in AHEAD or
    STARTED_EARLIER escaped refresh() -- no summary, no receipt, no prune,
    premap_last stuck on a progress mode -- and one in the window left a
    receipt that failed its own markets_reconcile CHECK."""
    window, ahead, earlier = build_board()
    venue = FakeVenue(window + ahead + earlier)
    _breaking_rows(monkeypatch, bad_slug)
    conn, tx = await _tx()
    try:
        # a row unseen for two days: the full lane's prune must still run
        await conn.execute(
            "INSERT INTO us_premap (identifier, side_norm, updated_at) "
            "VALUES ('stale-row', 'x', now() - interval '2 days')")
        full, cal, claims, n_full = await _run_both(monkeypatch, conn, venue)
        s = full if lane == "full" else cal
        rec = s["completeness"]
        assert s["receipt_history"]["appended"] is True, s["receipt_history"]
        assert rec["write_failures"]["events"] == 1
        assert rec["write_failures"]["examples"][0]["event"] == bad_slug
        assert rec["outcome"] == "PARTIAL" and rec["complete"] is False
        assert rec["events"]["dropped_by_reason"].get(vc.D_EVENT_WRITE_FAILED) == 1
        assert rec["markets"]["dropped_by_reason"].get(
            vc.D_MARKET_ROW_BUILD_FAILED) >= 1
        for k in ("events", "markets"):
            assert rec[k]["seen"] == rec[k]["kept"] + rec[k]["dropped"]
        # the rest of the board was still written, the prune still ran
        assert await conn.fetchval(
            "SELECT count(*) FROM us_premap WHERE event_slug = $1",
            bad_slug) == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM us_premap WHERE identifier = 'stale-row'") == 0
        assert full["prune_err"] is None
        last = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1",
            "premap_last" if lane == "full" else "premap_last_calendar")
        last = json.loads(last) if isinstance(last, str) else last
        assert not str(last["mode"]).startswith("events/"), last["mode"]
        assert "completeness" in last
    finally:
        await tx.rollback()
        await conn.close()


@pg
@pytest.mark.parametrize("where", ["lookup_keys", "listing_state"])
async def test_a_failure_outside_the_row_builder_is_isolated_too(monkeypatch,
                                                                where):
    """Any exception while one event is written -- its lookup keys (inside
    the event's guard) or its state (outside it, finished by difference) --
    is that event's failure alone, and the receipt still reconciles."""
    window, _, _ = build_board()
    venue = FakeVenue(window)
    bad = "nhl-uta-nyr-2026-10-04"
    if where == "lookup_keys":
        real = premap.event_keys_for

        def keys(title, slug=None):
            if slug == bad:
                raise KeyError("odd title")
            return real(title, slug)

        monkeypatch.setattr(premap, "event_keys_for", keys)
    else:
        real_state = vc.listing_state

        def state(ev, now, market=None):
            if ev.get("slug") == bad:
                raise TypeError("odd payload")
            return real_state(ev, now, market)

        monkeypatch.setattr(vc, "listing_state", state)
    conn, tx = await _tx()
    try:
        s, _ = await _run_refresh(monkeypatch, conn, venue)
        rec = s["completeness"]
        assert rec["write_failures"]["events"] == 1
        for k in ("events", "markets"):
            assert rec[k]["seen"] == rec[k]["kept"] + rec[k]["dropped"]
        assert s["receipt_history"]["appended"] is True
        assert s["mode"] == "events" and s["rows"] > 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_database_that_refuses_every_write_stops_the_pass_by_name(
        monkeypatch):
    window, _, _ = build_board()
    venue = FakeVenue(window)

    async def refuse(pool, r, keys):
        raise RuntimeError("could not write: disk full")

    monkeypatch.setattr(premap, "_upsert", refuse)
    conn, tx = await _tx()
    try:
        s, claims = await _run_refresh(monkeypatch, conn, venue)
        rec = s["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        assert w["stopped"] == vc.STOP_WRITE_FAILURES
        assert rec["write_failures"]["events"] == vc.MAX_CONSECUTIVE_EVENT_FAILURES
        assert rec["events"]["dropped_by_reason"][vc.D_EVENT_NOT_REACHED] > 0
        for k in ("events", "markets"):
            assert rec[k]["seen"] == rec[k]["kept"] + rec[k]["dropped"]
        # a board that answered is not a dead board: no markets fallback
        assert s["mode"] == "events/write_failed"
        assert not [c for c in venue.calls if c[0] == "markets"]
        assert s["receipt_history"]["appended"] is True
        assert s["calendar_eligible"] is False
    finally:
        await tx.rollback()
        await conn.close()


# ── §6 a 429, the lock, the wall-time bound ──────────────────────────────

class _Http429(Exception):
    def __init__(self):
        super().__init__("429 Too Many Requests")
        self.status_code = 429
        # httpx's Headers are case-insensitive; the venue_http_error reader
        # asks for the lower-case name
        self.response = type("R", (), {"status_code": 429,
                                       "headers": {"retry-after": "7"}})()


class _Http502(Exception):
    def __init__(self):
        super().__init__("502 Bad Gateway")
        self.status_code = 502
        self.response = type("R", (), {"status_code": 502, "headers": {}})()


@pg
async def test_a_429_stops_the_calendar_lane_and_applies_the_circuit(monkeypatch):
    window, ahead, earlier = build_board()
    min_ahead = _iso(NOW + timedelta(hours=96))[:13]
    venue = FakeVenue(window + ahead + earlier, fail_on=lambda q: (
        _Http429() if str(q.get("startTimeMin", "")).startswith(min_ahead)
        else None))
    applied = []
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "penalize_observed",
                        lambda **k: applied.append(k) or {})
    conn, tx = await _tx()
    try:
        full, cal, claims, n_full = await _run_both(monkeypatch, conn, venue)
        assert full["completeness"]["outcome"] == "COMPLETE"
        assert applied and applied[0]["retry_after_s"] == 7.0
        rec = cal["completeness"]
        assert rec["passes"][vc.PASS_AHEAD]["stopped"] == vc.STOP_RATE_LIMITED
        # the limiter said no: the next pass is not asked, and says so
        assert rec["passes"][vc.PASS_STARTED_EARLIER]["stopped"] == vc.STOP_NOT_RUN
        assert cal["mode"] == "calendar/partial" and cal["err"]
        # nothing was read before the limiter said no: FAILED, not PARTIAL
        assert rec["outcome"] == "FAILED"
        assert [x["why"] for x in rec["passes"][vc.PASS_AHEAD][
            "slices_not_read"]] == [vc.STOP_NOT_RUN, vc.STOP_NOT_RUN]
        # the 429'd request was sent and is receipted
        assert len(claims) - n_full == rec["requests"] == 1
        assert await conn.fetchval("SELECT count(*) FROM us_premap") > 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_429_on_the_first_probe_never_falls_back_to_the_markets_list(
        monkeypatch):
    """The markets fallback is for a board that does not answer; a board that
    answered 429 gets no further request, not a different endpoint -- and the
    receipt counts the request it did make (it said 0 before)."""
    venue = FakeVenue([], fail_on=lambda q: _Http429())
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "penalize_observed", lambda **k: {})
    conn, tx = await _tx()
    try:
        summary, claims = await _run_refresh(monkeypatch, conn, venue)
        assert summary["mode"] == "events/rate_limited"
        assert [c for c in venue.calls if c[0] == "markets"] == []
        events_calls = [c for c in venue.calls if c[0] == "events"]
        assert len(events_calls) == 1
        rec = summary["completeness"]
        assert rec["outcome"] == "FAILED"
        assert rec["passes"][vc.PASS_WINDOW]["stopped"] == vc.STOP_RATE_LIMITED
        assert rec["requests"] == len(claims) == len(events_calls) == 1
        assert summary["receipt_history"]["appended"] is True
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_failed_probe_rungs_are_receipted_requests(monkeypatch):
    """Rung 1 answers 502, rung 2 answers events: two requests sent before
    the first page, both claimed on the gate, both on the receipt (it said one
    fewer before)."""
    window, _, _ = build_board()
    venue = FakeVenue(window, fail_on=lambda q: (
        _Http502() if "startTimeMin" in q else None))
    conn, tx = await _tx()
    try:
        summary, claims = await _run_refresh(monkeypatch, conn, venue)
        rec = summary["completeness"]
        w = rec["passes"][vc.PASS_WINDOW]
        assert w["probe_requests_failed"] == 1
        sent = len([c for c in venue.calls if c[0] in ("events", "detail")])
        assert rec["requests"] == len(claims) == sent
        # the unbounded rung answered: the calendar lane is not asked
        assert summary["calendar_eligible"] is False
    finally:
        await tx.rollback()
        await conn.close()


async def test_the_calendar_lane_runs_with_the_sweep_lock_released(monkeypatch):
    """ADVERSARIAL REVIEW: inside _SWEEP_LOCK the calendar passes extended
    the time the 180 s fast lane skips. The full loop now releases the lock
    before the calendar lane."""
    seen = []

    async def fake_refresh(**kw):
        seen.append((kw.get("lane") or "full", premap._SWEEP_LOCK.locked()))
        return {"mode": "events", "calendar_eligible": True}

    class _Stop(Exception):
        pass

    async def stop_sleep(_s):
        raise _Stop()

    monkeypatch.setattr(premap, "refresh", fake_refresh)
    monkeypatch.setattr(premap, "CALENDAR_ENABLED", True)
    monkeypatch.setattr(premap.asyncio, "sleep", stop_sleep)
    with pytest.raises(_Stop):
        await premap._full_loop()
    assert seen == [("full", True), ("calendar", False)]


@pg
async def test_the_calendar_lane_is_bounded_in_wall_time(monkeypatch):
    _, ahead, earlier = build_board()
    venue = FakeVenue(ahead + earlier, delay_s=0.6)
    monkeypatch.setattr(premap, "CALENDAR_MAX_SECONDS", 1.0)
    conn, tx = await _tx()
    try:
        claims = _wire(monkeypatch, conn, venue)
        cal = await premap.calendar_refresh()
        rec = cal["completeness"]
        stops = {p: r["stopped"] for p, r in rec["passes"].items()}
        assert vc.STOP_WALL_TIME in stops.values(), stops
        assert cal["truncated"] is True and rec["outcome"] == "TRUNCATED"
        assert any(r.get("slices_not_read") for r in rec["passes"].values())
        assert len(claims) == rec["requests"]
        assert cal["receipt_history"]["appended"] is True
    finally:
        await tx.rollback()
        await conn.close()
