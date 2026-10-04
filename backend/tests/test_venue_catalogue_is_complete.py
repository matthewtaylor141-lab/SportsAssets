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
           category="sports", count=None, **extra):
    ev = {"id": "e-" + slug, "slug": slug, "title": title,
          "startTime": _iso(start), "active": True, "closed": False,
          "archived": False, "category": category, "period": period,
          "markets": markets,
          "marketCounts": {"numMarkets": len(markets) if count is None
                           else count, "numSpreadsAndTotalsMarkets": 0}}
    if live is not None:
        ev["live"] = live
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


def _soccer(slug, title, start, codes):
    """A three-way soccer winner: one market per outcome, each a yes/no
    pair (the atc family)."""
    mk = [_two_sided("atc-%s-%s" % (slug, c), "Will %s win?" % c,
                     "soccer_team_full_time_winner", start, "Yes", "No")
          for c in codes]
    return _event(slug, title, start, mk, live=False, period="NS")


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
    # inline list is shorter than the venue's own count, a market whose two
    # sides normalise to the same text, and 230 fillers (three pages)
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
    window.append(_event("mlb-sd-mil-2026-10-04", "SD Padres vs. MIL Brewers",
                         NOW + timedelta(hours=6), [
        _two_sided("aec-mlb-sd-mil-2026-10-04", "Who will win SD vs MIL?",
                   "baseball_team_full_game_winner", NOW + timedelta(hours=6),
                   "Padres", "Brewers")], live=False, period="NS", count=5))
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
    # AHEAD (past +96 h): a sports future -- read -- and the midterms -- not
    ws = NOW + timedelta(days=20)
    ahead.append(_event("mlb-wschamp-2026-10-31", "World Series Champion", ws, [
        _two_sided("tec-mlb-wschamp-2026-10-31-%s" % t,
                   "World Series Champion", "futures", ws, "Yes", "No")
        for t in ("lad", "nyy", "sd", "cle")], live=False, period="NS"))
    mid = NOW + timedelta(days=30)
    ahead.append(_event("usho-midterms-2026-11-03", "U.S House Midterm Winner",
                        mid, [_two_sided("paccc-usho-midterms-2026-11-03-dem",
                                         "U.S House Midterm Winner",
                                         "election", mid, "Yes", "No")],
                        live=False, period="NS", category="politics"))
    # STARTED EARLIER (before -12 h): the NL champion future (four weeks back,
    # still open), a live multi-day cricket test, and a game the venue has not
    # closed three days after its start
    nl = NOW - timedelta(days=27)
    earlier.append(_event("mlb-nlchamp-2026-09-27", "National League Champion",
                          nl, [_two_sided("tec-mlb-nlchamp-2026-09-27-%s" % t,
                                          "National League Champion",
                                          "futures", nl, "Yes", "No")
                               for t in ("lad", "mil", "sd", "atl")],
                          live=False, period="NS"))
    ck = NOW - timedelta(hours=50)
    earlier.append(_event("t20i-eng-ind-2026-10-02", "England vs. India", ck, [
        _two_sided("aec-t20i-eng-ind-2026-10-02", "Who will win ENG vs IND?",
                   "cricket_match_winner", ck, "England", "India")],
        live=True, period="D3"))
    old = NOW - timedelta(hours=75)
    earlier.append(_event("kbo-kia-lg-2026-10-01", "KIA vs. LG", old, [
        _two_sided("aec-kbo-kia-lg-2026-10-01", "Who will win KIA vs LG?",
                   "baseball_team_full_game_winner", old, "KIA", "LG")],
        live=False, period="FT"))
    return window, ahead, earlier


class FakeVenue:
    """events.list over the board, the way the venue answers it: the
    start-time window, active / closed, then offset + limit over a stable
    order -- and an optional page-size cap, as a venue may impose."""

    def __init__(self, events, *, page_cap=None, fail_on=None):
        self.events = sorted(events, key=lambda e: e["id"])
        self.page_cap = page_cap
        self.calls = []
        self.fail_on = fail_on          # predicate(q) -> exception or None
        venue = self

        class _E:
            def list(self, q):
                return venue.list(q)

        class _M:
            def list(self, q):
                venue.calls.append(("markets", dict(q)))
                return {"markets": []}

        self.client = type("C", (), {"events": _E(), "markets": _M()})()

    def list(self, q):
        self.calls.append(("events", dict(q)))
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
        return {"events": [json.loads(json.dumps(e)) for e in rows[off:off + n]]}


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


def test_an_event_that_shifts_across_a_page_boundary_is_caught():
    """Games close while the sweep walks the board. Three deletions ahead of
    the cursor between page 1 and page 2 shift every later event back three
    positions; without an overlap the three events that slid across the
    boundary are never read."""
    def walk_with(overlap):
        board = [_ev(i) for i in range(250)]
        walk = vc.PageWalk(limit=100, max_requests=50, overlap=overlap)
        got = walk.first(board[0:100])
        del board[10:13]                       # three games close
        while (off := walk.next_offset()) is not None:
            got += walk.accept(board[off:off + 100])
        return {e["slug"] for e in got}, walk

    seen0, _ = walk_with(0)
    missing = {"ev-%04d" % i for i in range(250)} - {
        "ev-0010", "ev-0011", "ev-0012"} - seen0
    assert missing == {"ev-0100", "ev-0101", "ev-0102"}, missing
    seen5, w5 = walk_with(vc.PAGE_OVERLAP)
    assert {"ev-0100", "ev-0101", "ev-0102"} <= seen5
    assert w5.receipt()["overlap_catches"] == 3


def test_a_budget_that_runs_out_on_full_pages_is_truncated_by_name():
    board = [_ev(i) for i in range(1000)]
    walk = vc.PageWalk(limit=100, max_requests=3)
    _drive(walk, board, size=100)
    r = walk.receipt()
    assert r["stopped"] == vc.STOP_BUDGET and r["truncated"] is True
    assert r["natural_end"] is False and r["requests"] == 3


# ── §2 classification and state ──────────────────────────────────────────

@pytest.mark.parametrize("smt,fam", [
    ("football_team_full_game_winner", vc.F_WINNER),
    ("football_team_full_game_spread", vc.F_SPREAD),
    ("football_team_full_game_total", vc.F_TOTAL),
    ("football_team_points_full_game_total", vc.F_TEAM_TOTAL),
    ("baseball_team_total_runs", vc.F_TEAM_TOTAL),
    ("football_team_first_half_total", vc.F_PERIOD),
    ("hockey_team_first_period_winner", vc.F_PERIOD),
    ("tennis_set_1_winner", vc.F_PERIOD),
    ("football_player_receiving_yards", vc.F_PLAYER_PROP),
    ("soccer_game_exact_score", vc.F_MULTI_OUTCOME),
    ("football_game_exact_margin", vc.F_MULTI_OUTCOME),
    ("futures", vc.F_FUTURES),
    ("election", vc.F_YES_NO),
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
    assert vc.listing_state(ev(start=past, ended=True, live=True), now) == (
        vc.S_ENDED, vc.SRC_VENUE_ENDED_FLAG)
    assert vc.listing_state(ev(start=future), now) == (
        vc.S_PREGAME, vc.SRC_SCHEDULE)
    assert vc.listing_state(ev(start=past), now) == (
        vc.S_STARTED, vc.SRC_SCHEDULE)
    assert vc.listing_state({}, now) == (vc.S_UNKNOWN, vc.SRC_NONE)


def test_the_extra_passes_keep_sports_and_drop_stale_games_by_name():
    now = time.time()
    pol = {"slug": "usho", "category": "politics"}
    assert vc.event_drop_reason(pol, pass_name=vc.PASS_AHEAD) == \
        vc.D_EVENT_OUT_OF_SCOPE_CATEGORY
    # a sport is never dropped on a guessed word: esports and no category stay
    for cat in ("sports", "esports", None):
        assert vc.event_drop_reason({"slug": "x", "category": cat},
                                    pass_name=vc.PASS_AHEAD) is None
    # the window keeps every category it always kept
    assert vc.event_drop_reason(pol, pass_name=vc.PASS_WINDOW) is None
    old = {"slug": "g", "startTime": _iso(datetime.fromtimestamp(
        now - 75 * 3600, timezone.utc))}
    game = {"sportsMarketType": "baseball_team_full_game_winner"}
    fut = {"sportsMarketType": "futures"}
    assert vc.market_drop_reason(old, game, pass_name=vc.PASS_STARTED_EARLIER,
                                 now=now) == vc.D_MARKET_PAST_PLAYABLE_SPAN
    assert vc.market_drop_reason(old, fut, pass_name=vc.PASS_STARTED_EARLIER,
                                 now=now) is None
    assert vc.market_drop_reason(dict(old, live=True), game,
                                 pass_name=vc.PASS_STARTED_EARLIER,
                                 now=now) is None
    # a suspended in-play market is KEPT (a live market for a moment)
    assert vc.market_drop_reason(old, dict(game, status="MARKET_STATUS_SUSPENDED"),
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
    r = t.receipt()
    assert r["markets"]["seen"] == r["markets"]["kept"] + r["markets"]["dropped"]
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


async def _run_refresh(monkeypatch, conn, venue, **kw):
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
    summary = await premap.refresh(**kw)
    return summary, claims


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
        summary, claims = await _run_refresh(monkeypatch, conn, venue)
        rows = await conn.fetch(
            "SELECT identifier, side_norm, intent, event_slug, market_slug, "
            "sports_type, listing_state, listing_state_source, listing_pass "
            "FROM us_premap")
        by_market: dict = {}
        for r in rows:
            by_market.setdefault(r["market_slug"], []).append(r)

        # EVERY MARKET OF THE EVENT, not the first: the live NFL game's six
        # markets, twelve sides, each with its own intent
        kc = [r for r in rows if r["event_slug"] == "nfl-kc-lv-2026-10-04"]
        assert len({r["market_slug"] for r in kc}) == 6 and len(kc) == 12
        for m in {r["market_slug"] for r in kc}:
            assert sorted(r["intent"] for r in by_market[m]) == [
                "ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT"], m
        # the three alternate spreads stay three markets (asc yes/no rows)
        asc = [m for m in by_market if m.startswith("asc-nfl-kc-lv")]
        assert len(asc) == 3
        # LIVE AND PREGAME BOTH RETAINED, with the venue's word
        assert {(r["listing_state"], r["listing_state_source"]) for r in kc} \
            == {(vc.S_LIVE, vc.SRC_VENUE_LIVE_FLAG)}
        den = [r for r in rows if r["event_slug"] == "nfl-den-sf-2026-10-05"]
        assert len(den) == 12 and {r["listing_state"] for r in den} == {
            vc.S_PREGAME}
        # TWO FIXTURES, ONE TITLE: both kept, apart
        both = {r["event_slug"] for r in rows
                if r["event_slug"].startswith("mls-cla-clb-")}
        assert both == {"mls-cla-clb-2026-10-04", "mls-cla-clb-2026-10-06"}
        assert sum(1 for r in rows if r["event_slug"].startswith(
            "mls-cla-clb-")) == 12
        # the closed market out, its open sibling in; the all-closed event out
        assert "aec-nhl-uta-nyr-2026-10-04" in by_market
        assert "tsc-nhl-uta-nyr-2026-10-04-5pt5" not in by_market
        assert "aec-nba-old-one-2026-10-04" not in by_market
        # THE SIDES THAT NORMALISE ALIKE: both kept, each named by its marker
        weird = sorted(r["side_norm"] for r in by_market[
            "tsc-cs2-aa-bb-2026-10-04-maps"])
        assert weird == ["total 2 5 [long]", "total 2 5 [short]"], weird
        # THE CALENDAR ON BOTH SIDES OF THE WINDOW
        ws = [r for r in rows if r["event_slug"] == "mlb-wschamp-2026-10-31"]
        assert len({r["market_slug"] for r in ws}) == 4
        assert {r["listing_pass"] for r in ws} == {vc.PASS_AHEAD}
        nl = [r for r in rows if r["event_slug"] == "mlb-nlchamp-2026-09-27"]
        assert len({r["market_slug"] for r in nl}) == 4
        assert {r["listing_pass"] for r in nl} == {vc.PASS_STARTED_EARLIER}
        ck = [r for r in rows if r["event_slug"] == "t20i-eng-ind-2026-10-02"]
        assert len(ck) == 2 and {r["listing_state"] for r in ck} == {vc.S_LIVE}
        # left out, by name, never silently
        assert not [r for r in rows if r["event_slug"] in (
            "usho-midterms-2026-11-03", "kbo-kia-lg-2026-10-01")]
        # every identifier is one the venue wrote on a side
        venue_idents = {s["identifier"] for e in window + ahead + earlier
                        for m in e["markets"] for s in m["marketSides"]}
        assert {r["identifier"] for r in rows} <= venue_idents
        # the window filled three pages, the 230 fillers all present
        assert sum(1 for m in by_market if m.startswith("aec-wtt-")) == 230

        rec = summary["completeness"]
        assert rec["outcome"] == "COMPLETE" and rec["complete"] is True
        assert summary["truncated"] is False and summary["mode"] == "events"
        assert set(rec["passes"]) == {vc.PASS_WINDOW, vc.PASS_AHEAD,
                                      vc.PASS_STARTED_EARLIER}
        assert rec["passes"][vc.PASS_WINDOW]["pages_with_events"] >= 3
        assert rec["events"]["seen"] == rec["events"]["kept"] + \
            rec["events"]["dropped"]
        assert rec["markets"]["seen"] == rec["markets"]["kept"] + \
            rec["markets"]["dropped"]
        dropped = rec["events"]["dropped_by_reason"]
        assert dropped[vc.D_EVENT_OUT_OF_SCOPE_CATEGORY] == 1
        assert rec["notes"]["category_dropped:politics"] == 1
        assert dropped[vc.D_EVENT_NO_OPEN_MARKET] == 2   # NBA all-closed, KBO stale
        mdrop = rec["markets"]["dropped_by_reason"]
        assert mdrop[vc.D_MARKET_CLOSED] == 2
        assert mdrop[vc.D_MARKET_PAST_PLAYABLE_SPAN] == 1
        assert rec["venue_market_counts"][
            "events_inline_fewer_than_venue_count"] == 1
        assert rec["venue_market_counts"]["markets_missing_inline"] == 4
        assert rec["states"][vc.S_LIVE] == 2            # NFL + cricket
        assert rec["sides_written"] == len(rows)
        assert summary["side_keys"]["qualified_pairs"] == 1
        # EVERY REQUEST BEHIND THE PROCESS-WIDE GATE
        n_events_calls = sum(1 for c in venue.calls if c[0] == "events")
        assert len(claims) == n_events_calls == rec["requests"]
        # the receipt is in the append-only history, reconciled by its CHECKs
        h = await conn.fetchrow(
            "SELECT * FROM venue_catalogue_receipts ORDER BY id DESC LIMIT 1")
        assert h["lane"] == "full" and h["outcome"] == "COMPLETE"
        assert h["events_seen"] == rec["events"]["seen"]
        assert h["sides_written"] == len(rows) and h["truncated"] is False
        assert summary["receipt_history"]["appended"] is True
        st = await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = 'premap_last'")
        st = json.loads(st) if isinstance(st, str) else st
        assert st["completeness"]["outcome"] == "COMPLETE"
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
        summary, _ = await _run_refresh(
            monkeypatch, conn, venue, back_h=3.0, fwd_h=14.0, max_pages=25,
            prune=False, windowed_only=True, state_key="premap_last_fast")
        assert summary["lane"] == "fast" and summary["extra_passes"] == []
        assert set(summary["completeness"]["passes"]) == {vc.PASS_FAST}
        assert "by_sport_league_family" not in summary["completeness"]
        passes = {r["listing_pass"] for r in await conn.fetch(
            "SELECT DISTINCT listing_pass FROM us_premap")}
        assert passes == {vc.PASS_FAST}
        # every request a windowed one: no unbounded rung, no extra pass
        assert all("startTimeMin" in q for k, q in venue.calls if k == "events")
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
            s, _ = await _run_refresh(monkeypatch, conn, venue)
            counts.append((await conn.fetchval("SELECT count(*) FROM us_premap"),
                           s["completeness"]["passes"][vc.PASS_WINDOW]))
        finally:
            await tx.rollback()
            await conn.close()
    assert counts[0][0] == counts[1][0] > 0
    assert counts[1][1]["venue_page_cap"] == 40


# ── §5 a 429 ─────────────────────────────────────────────────────────────

class _Http429(Exception):
    def __init__(self):
        super().__init__("429 Too Many Requests")
        self.status_code = 429
        # httpx's Headers are case-insensitive; the venue_http_error reader
        # asks for the lower-case name
        self.response = type("R", (), {"status_code": 429,
                                       "headers": {"retry-after": "7"}})()


@pg
async def test_a_429_stops_the_next_request_and_applies_the_circuit(monkeypatch):
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
        summary, _ = await _run_refresh(monkeypatch, conn, venue)
        assert applied and applied[0]["retry_after_s"] == 7.0
        rec = summary["completeness"]
        assert rec["passes"][vc.PASS_AHEAD]["stopped"] == vc.STOP_RATE_LIMITED
        # the limiter said no: the next pass is not asked
        assert vc.PASS_STARTED_EARLIER not in rec["passes"]
        assert summary["mode"] == "events/partial" and summary["err"]
        assert rec["outcome"] == "PARTIAL"
        # the window's rows were kept
        assert await conn.fetchval("SELECT count(*) FROM us_premap") > 0
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_429_on_the_first_probe_never_falls_back_to_the_markets_list(
        monkeypatch):
    """The markets fallback is for a board that does not answer; a board that
    answered 429 gets no further request, not a different endpoint."""
    venue = FakeVenue([], fail_on=lambda q: _Http429())
    from sportsassets import venue_pace
    monkeypatch.setattr(venue_pace, "penalize_observed", lambda **k: {})
    conn, tx = await _tx()
    try:
        summary, _ = await _run_refresh(monkeypatch, conn, venue)
        assert summary["mode"] == "events/rate_limited"
        assert [c for c in venue.calls if c[0] == "markets"] == []
        assert len([c for c in venue.calls if c[0] == "events"]) == 1
        assert summary["completeness"]["outcome"] == "FAILED"
        assert summary["completeness"]["passes"][vc.PASS_WINDOW][
            "stopped"] == vc.STOP_RATE_LIMITED
    finally:
        await tx.rollback()
        await conn.close()
