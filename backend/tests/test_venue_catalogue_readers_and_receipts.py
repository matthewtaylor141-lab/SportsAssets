"""THE CATALOGUE'S READERS SEE ALL OF IT, AND EVERY REFRESH LEAVES A RECEIPT
(R30A P0 incident, stream inc-catalogue; migration 249).

  §1 THE BOARDS (ext_pinnacle_loop._board_sql). research-sql run 37233672878
     K5a: the real soccer board held 47 league tokens and `LIMIT 30` dropped
     17, among them the mapped `mls` (row 38) and `uslc` (row 44); the titles
     carried for fixture confirmation were the first 12 alphabetically while
     `unl` listed 25. On a production-shaped catalogue in Postgres the board
     now returns every token, every fixture title, and names any cut.
  §2 THE DESK (pmus._desk_sweep / event_board). Production logged
     `pages=14 events=1400/1400` on every sweep of an 18-page board, and one
     failed sweep replaced 1,400 events with 200. The bound now says when it
     binds, a failed sweep never replaces a fuller recent board, and an event
     past the bound is read by slug instead of through a filter the venue
     ignores.
  §3 MIGRATION 249: idempotent; the receipts are append-only and their
     arithmetic is a CHECK; the listing-state columns accept only the module's
     own vocabulary; the rollback refuses while a receipt exists and drops
     cleanly when none does.
"""
from __future__ import annotations

import json
import os
import pathlib
import threading
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from sportsassets import pmus
from sportsassets import venue_catalogue as vc
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
UP = (MIG / "249_venue_catalogue_completeness.sql").read_text()
DOWN = (MIG / "rollback" / "249_venue_catalogue_completeness.down.sql").read_text()


async def _tx():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    await conn.execute("DELETE FROM us_premap")
    return conn, tx


async def _expect(conn, exc, sql, *args):
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(exc):
            await conn.execute(sql, *args)
    finally:
        await sp.rollback()


ROW = ("INSERT INTO us_premap (identifier, event_slug, event_title, "
       "market_slug, question, kind, line, side_norm, event_keys, intent, "
       "sports_type, game_start, updated_at) VALUES ($1,$2,$3,$1,'q','side',"
       "'',$4,ARRAY[]::text[],$5,$6,$7, now())")

#: the production board of 2026-10-04 20:51Z (K5a): token -> real events
BOARD_K5A = {
    "ncaaws": 74, "unl": 25, "u21eq": 24, "intf": 14, "ncaams": 13,
    "arg2": 11, "ngnpfl": 9, "brb": 8, "gtasc": 8, "isthp": 8, "lpa": 8,
    "lco": 7, "bra": 6, "cnl": 6, "ghpl": 6, "ven2": 6, "lal2": 5,
    "bolcup": 4, "par1": 4, "par2": 4, "uru1": 4, "nwsl": 3, "sercc": 3,
    "u19f": 3, "vkl": 3, "ligaf": 2, "lng": 2, "mne2": 2, "serca": 2,
    "sercb": 2, "afcq": 1, "cpach": 1, "fbl": 1, "ilaln": 1, "irlp": 1,
    "lexp": 1, "minw": 1, "mls": 1, "nmcup": 1, "nor1": 1, "pl1": 1,
    "slr": 1, "svk2": 1, "uslc": 1, "uslcp": 1, "uzb1": 1, "wsl": 1}


async def _seed_board(conn):
    start = datetime.now(timezone.utc) + timedelta(hours=6)
    for token, n in BOARD_K5A.items():
        for i in range(n):
            ev = "%s-h%02d-a%02d-2026-10-05" % (token, i, i)
            title = "Home %s %02d vs. Away %s %02d" % (token, i, token, i)
            for side, intent in (("yes", "ORDER_INTENT_BUY_LONG"),
                                 ("no", "ORDER_INTENT_BUY_SHORT")):
                await conn.execute(ROW, "atc-%s-h" % ev, ev, title, side,
                                   intent, "soccer_team_full_time_winner",
                                   start)


# ── §1 the boards ────────────────────────────────────────────────────────

@pg
async def test_the_soccer_board_returns_every_token_and_every_title():
    conn, tx = await _tx()
    try:
        await _seed_board(conn)
        got = await loop.venue_soccer_competitions(conn)
        tokens = [t for t, _ in got["board"]]
        assert len(tokens) == len(BOARD_K5A) == 47
        # the two MAPPED competitions LIMIT 30 used to drop
        assert {"mls", "uslc"} <= set(tokens)
        cands = {c["our_token"] for c in loop.candidates_from_board(
            got["board"], got["titles"], got["title_days"])}
        assert {"mls", "uslc"} <= cands
        # every Nations League fixture travels with the candidate, not 12
        assert len(got["titles"]["unl"]) == 25
        assert len(got["title_days"]["unl"]) == 25
        assert got["board_truncated"] is False and got["titles_truncated"] == []
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_a_board_that_fills_a_bound_says_so():
    conn, tx = await _tx()
    try:
        await _seed_board(conn)
        sql = loop._board_sql("soccer", 12, 30)          # the OLD bounds
        rows = await conn.fetch(sql)
        b = loop.board_bounds(rows, token_limit=30, title_limit=12)
        assert len(rows) == 30 and b["board_truncated"] is True
        cut = {c["token"]: c for c in b["titles_truncated"]}
        assert cut["unl"] == {"token": "unl", "titles": 25, "carried": 12}
        assert {"ncaaws", "unl", "u21eq", "intf", "ncaams"} == set(cut)
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_two_fixtures_with_one_title_keep_both_dates():
    """ADVERSARIAL REVIEW: `per[title] = day` kept the LAST date, so the
    first meeting of two clubs listed twice under one title was erased from
    the fixture confirmation."""
    conn, tx = await _tx()
    try:
        d1 = datetime.now(timezone.utc) + timedelta(hours=6)
        d2 = datetime.now(timezone.utc) + timedelta(hours=54)
        for ev, at in (("mls-cla-clb-1", d1), ("mls-cla-clb-2", d2)):
            for side, intent in (("yes", "ORDER_INTENT_BUY_LONG"),
                                 ("no", "ORDER_INTENT_BUY_SHORT")):
                await conn.execute(ROW, "atc-%s-cla" % ev, ev,
                                   "Club A vs. Club B", side, intent,
                                   "soccer_team_full_time_winner", at)
        got = await loop.venue_soccer_competitions(conn)
        days = got["title_days"]["mls"]["Club A vs. Club B"]
        assert days == sorted([d1.strftime("%Y-%m-%d"), d2.strftime("%Y-%m-%d")])
        assert got["same_title_fixtures"] == [
            {"token": "mls", "title": "Club A vs. Club B", "days": days}]
        # the confirmation matches a provider fixture on EITHER date
        for when in (d1, d2):
            r = loop.confirm_mapping_by_fixtures(
                provider_events=[{"home_team": "Club A", "away_team": "Club B",
                                  "commence_time": when.strftime(
                                      "%Y-%m-%dT%H:%M:%SZ")}],
                venue_event_titles=got["titles"]["mls"],
                venue_event_days=got["title_days"]["mls"])
            assert r["ok"] is True, r
    finally:
        await tx.rollback()
        await conn.close()


def test_a_single_date_stays_a_string():
    assert loop.title_days_of(["A vs B\u00012026-10-05"]) == {
        "A vs B": "2026-10-05"}
    assert loop.title_days_of(["A vs B\u00012026-10-05",
                               "A vs B\u00012026-10-05"]) == {
        "A vs B": "2026-10-05"}


def test_a_board_row_without_its_title_count_is_unmeasured_not_complete():
    b = loop.board_bounds([{"token": "mls", "events": 1, "titles": ["A vs B"]}])
    assert b["titles_count_unmeasured"] == ["mls"]
    assert b["titles_truncated"] == []


# ── §2 the desk ──────────────────────────────────────────────────────────

def _desk_event(i):
    return {"slug": "mlb-a%d-b%d-2026-10-05" % (i, i), "title": "A%d vs B%d" % (i, i),
            "markets": [{"question": "A%d vs B%d" % (i, i), "marketSides": [
                {"identifier": "aec-mlb-a%d-b%d" % (i, i), "description": "A",
                 "price": 0.5},
                {"identifier": "aec-mlb-a%d-b%d" % (i, i), "description": "B",
                 "price": 0.5}]}]}


class _Desk429(Exception):
    def __init__(self):
        super().__init__("429 Too Many Requests")
        self.status_code = 429
        self.response = type("R", (), {"status_code": 429,
                                       "headers": {"retry-after": "9"}})()


class _DeskEvents:
    def __init__(self, n, *, fail_at=None, page_cap=None, fail_exc=None,
                 fail_probe=None):
        self.board = [_desk_event(i) for i in range(n)]
        self.fail_at = fail_at
        self.page_cap = page_cap
        self.fail_exc = fail_exc or (lambda: RuntimeError("gateway 502"))
        self.fail_probe = fail_probe
        self.calls = []

    def list(self, q):
        self.calls.append(dict(q))
        off = int(q.get("offset") or 0)
        if q.get("offset") is None and self.fail_probe is not None:
            exc = self.fail_probe(q)
            if exc is not None:
                raise exc
        if self.fail_at is not None and off >= self.fail_at:
            raise self.fail_exc()
        n = min(100, self.page_cap or 100)
        return {"events": self.board[off:off + n]}


def _wire_desk(monkeypatch, events, *, cache=None):
    from sportsassets import venue_pace
    claims = []
    monkeypatch.setattr(pmus, "_get_client",
                        lambda: type("C", (), {"events": events})())
    monkeypatch.setattr(pmus, "_desk_cache", cache or {
        "ts": 0.0, "events": [], "blind_at": 0.0, "warned_at": 0.0})
    monkeypatch.setattr(pmus, "_desk_sweep_lock", threading.Lock())
    monkeypatch.setattr(pmus, "_DESK_PACE_S", 0.0)
    monkeypatch.setattr(venue_pace, "pace",
                        lambda gap=0.35, **k: claims.append(gap) or 0.0)
    return claims


def test_the_desk_names_its_truncation(monkeypatch):
    """Eighteen pages listed, the budget reads fourteen: the receipt says
    TRUNCATED -- the endpoint used to publish truncated False over it. The
    probe page is the walk's first page now (one request fewer)."""
    ev = _DeskEvents(1738)
    claims = _wire_desk(monkeypatch, ev)
    monkeypatch.setattr(pmus, "_DESK_MAX_PAGES", 14)
    got = pmus.list_desk_events()
    # the same 15 requests as before (probe + 14 pages) now read 1,430
    assert len(got) == 1430
    r = pmus._desk_cache["receipt"]
    assert r["truncated"] is True and r["stopped"] == vc.STOP_BUDGET
    assert r["pages"] == 15 and r["max_pages_after_probe"] == 14
    # every request paced, every request receipted
    assert len(claims) == len(ev.calls) == r["requests"] == 15


def test_the_desk_budget_reads_the_whole_board_when_it_covers_it(monkeypatch):
    ev = _DeskEvents(1738)
    _wire_desk(monkeypatch, ev)
    monkeypatch.setattr(pmus, "_DESK_MAX_PAGES", 30)
    got = pmus.list_desk_events()
    assert len(got) == 1738
    r = pmus._desk_cache["receipt"]
    assert r["truncated"] is False and r["stopped"] == vc.STOP_SHORT_PAGE


def test_a_venue_page_cap_does_not_make_the_desk_first_page_only(monkeypatch):
    """ADVERSARIAL REVIEW: the desk ended on any page shorter than 100. With
    a venue cap of 40 it read 40 of 1,738 events and reported truncated
    False. It now confirms a short first page and pages at the venue's size."""
    ev = _DeskEvents(500, page_cap=40)
    _wire_desk(monkeypatch, ev)
    monkeypatch.setattr(pmus, "_DESK_MAX_PAGES", 30)
    got = pmus.list_desk_events()
    assert len(got) == 500
    r = pmus._desk_cache["receipt"]
    assert r["venue_page_cap"] == 40 and r["truncated"] is False


def test_a_failed_sweep_does_not_replace_a_fuller_recent_board(monkeypatch):
    import time as _t
    full = [_desk_event(i) for i in range(1400)]
    ev = _DeskEvents(1738, fail_at=200)
    _wire_desk(monkeypatch, ev, cache={
        "ts": _t.time() - 130.0, "events": full, "blind_at": 0.0,
        "warned_at": 0.0, "receipt": {"stopped": vc.STOP_BUDGET}})
    got = pmus.list_desk_events()
    assert len(got) == 1400, "the partial must not replace it"
    assert pmus._desk_cache["events"] is full
    rej = pmus._desk_cache["receipt_rejected"]
    assert rej["partial"] is True and rej["kept_previous_board"] == 1400
    assert rej["stopped"] == vc.STOP_ERROR
    # AND THE NEXT CALLER DOES NOT START ANOTHER SWEEP (adversarial review:
    # the rejected partial never stamped the cache, so every desk call for up
    # to ten minutes swept again)
    n = len(ev.calls)
    assert pmus.list_desk_events() is full
    assert len(ev.calls) == n
    assert pmus._desk_cache["retry_at"] > _t.time()


def test_a_429_on_the_desk_applies_the_circuit_and_a_not_before(monkeypatch):
    import time as _t
    from sportsassets import venue_pace
    applied = []
    monkeypatch.setattr(venue_pace, "penalize_observed",
                        lambda **k: applied.append(k) or {})
    ev = _DeskEvents(1738, fail_probe=lambda q: _Desk429())
    claims = _wire_desk(monkeypatch, ev)
    assert pmus.list_desk_events() == []
    assert applied and applied[0]["retry_after_s"] == 9.0
    # the ladder stopped at the 429: one request, paced and receipted
    assert len(ev.calls) == 1 and len(claims) == 1
    assert pmus._desk_cache["receipt_blind"]["stopped"] == vc.STOP_RATE_LIMITED
    assert pmus._desk_cache["retry_at"] > _t.time()
    pmus.list_desk_events()
    assert len(ev.calls) == 1, "no new sweep before the not-before time"


def test_an_event_past_the_desk_budget_is_read_by_its_slug(monkeypatch):
    """markets.list's eventSlug filter is ignored by the venue (PREMAP-GT):
    the old read returned a generic page and the slug check emptied it."""
    slug = "nfl-kc-lv-2026-10-04"

    class _Events:
        def retrieve_by_slug(self, s):
            assert s == slug
            return {"event": {"slug": slug, "markets": [
                {"slug": "aec-" + slug, "question": "Who will win?",
                 "marketSides": [
                     {"identifier": "aec-" + slug, "description": "Chiefs",
                      "price": 0.6},
                     {"identifier": "aec-" + slug, "description": "Raiders",
                      "price": 0.4}]},
                {"slug": "tsc-" + slug + "-47pt5", "question": "O/U 47.5",
                 "marketSides": [
                     {"identifier": "tsc-" + slug + "-47pt5",
                      "description": "Over 47.5", "price": 0.5},
                     {"identifier": "tsc-" + slug + "-47pt5",
                      "description": "Under 47.5", "price": 0.5}]}]}}

    class _Markets:
        def list(self, q):                       # the generic page
            return {"markets": [{"slug": "aec-other", "eventSlug": "other",
                                 "marketSides": []}]}

    monkeypatch.setattr(pmus, "_get_client", lambda: type(
        "C", (), {"events": _Events(), "markets": _Markets()})())
    rows = pmus.event_board(slug)
    assert len(rows) == 4
    assert {r["us_slug"] for r in rows} == {"aec-" + slug,
                                             "tsc-" + slug + "-47pt5"}


def test_event_board_is_paced_and_stops_on_a_429(monkeypatch):
    from sportsassets import venue_pace
    claims = []
    monkeypatch.setattr(venue_pace, "pace",
                        lambda gap=0.35, **k: claims.append(gap) or 0.0)
    monkeypatch.setattr(venue_pace, "penalize_observed", lambda **k: {})
    calls = []

    class _Events:
        def retrieve_by_slug(self, s):
            calls.append("slug")
            raise _Desk429()

    class _Markets:
        def list(self, q):
            calls.append("markets")
            return {"markets": []}

    monkeypatch.setattr(pmus, "_get_client", lambda: type(
        "C", (), {"events": _Events(), "markets": _Markets()})())
    assert pmus.event_board("ev-x") == []
    assert calls == ["slug"] and len(claims) == 1


def test_event_board_falls_back_to_the_list_read_when_the_lookup_fails(monkeypatch):
    class _Events:
        def retrieve_by_slug(self, s):
            raise RuntimeError("404")

    class _Markets:
        def list(self, q):
            return {"markets": [{"slug": "aec-x", "eventSlug": "ev-x",
                                 "question": "Q", "marketSides": [
                                     {"identifier": "aec-x", "description": "A"}]}]}

    monkeypatch.setattr(pmus, "_get_client", lambda: type(
        "C", (), {"events": _Events(), "markets": _Markets()})())
    assert [r["us_slug"] for r in pmus.event_board("ev-x")] == ["aec-x"]


# ── §3 migration 249 ─────────────────────────────────────────────────────

REC = ("INSERT INTO venue_catalogue_receipts (lane, started_at, finished_at, "
       "outcome, pages_read, requests, events_seen, events_kept, "
       "events_dropped, markets_seen, markets_kept, markets_dropped, "
       "sides_written, truncated, version, receipt) VALUES ($1, now(), now(), "
       "$2, $3, $4, $5, $6, $7, $8, $9, $10, 0, $11, 'v', $12::jsonb)")


@pg
async def test_249_is_idempotent_append_only_checked_and_guarded():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(UP)                                  # twice
        ok = json.dumps({"complete": True})
        await conn.execute(REC, "full", "COMPLETE", 3, 4, 10, 8, 2, 20, 18, 2,
                           False, ok)
        E = asyncpg.CheckViolationError
        # kept + dropped must equal seen
        await _expect(conn, E, REC, "full", "PARTIAL", 3, 4, 10, 8, 1, 20, 18,
                      2, False, ok)
        await _expect(conn, E, REC, "full", "PARTIAL", 3, 4, 10, 8, 2, 20, 17,
                      2, False, ok)
        # TRUNCATED is exactly a truncated refresh
        await _expect(conn, E, REC, "full", "TRUNCATED", 3, 4, 10, 8, 2, 20,
                      18, 2, False, ok)
        await _expect(conn, E, REC, "full", "PARTIAL", 3, 4, 10, 8, 2, 20, 18,
                      2, True, ok)
        # COMPLETE only when the receipt says so
        await _expect(conn, E, REC, "full", "COMPLETE", 3, 4, 10, 8, 2, 20,
                      18, 2, False, json.dumps({"complete": False}))
        # more pages than requests, an unknown lane or outcome
        await _expect(conn, E, REC, "full", "PARTIAL", 5, 4, 10, 8, 2, 20, 18,
                      2, False, ok)
        await _expect(conn, E, REC, "slow", "PARTIAL", 3, 4, 10, 8, 2, 20, 18,
                      2, False, ok)
        # the calendar lane has its own receipts
        await conn.execute(REC, "calendar", "PARTIAL", 3, 4, 10, 8, 2, 20, 18,
                           2, False, ok)
        await _expect(conn, E, REC, "full", "FINE", 3, 4, 10, 8, 2, 20, 18, 2,
                      False, ok)
        # append-only
        X = asyncpg.RaiseError
        await _expect(conn, X, "UPDATE venue_catalogue_receipts SET pages_read=0")
        await _expect(conn, X, "DELETE FROM venue_catalogue_receipts")
        await _expect(conn, X, "TRUNCATE venue_catalogue_receipts")
        # the listing columns take the module's vocabulary and nothing else
        base = ("INSERT INTO us_premap (identifier, side_norm, listing_state, "
                "listing_state_source, listing_pass) VALUES ($1,'yes',$2,$3,$4)")
        for st in vc.STATES:
            await conn.execute(base, "id-" + st, st, vc.SRC_SCHEDULE,
                               vc.PASS_WINDOW)
        for p in vc.PASSES:
            await conn.execute(base, "id-p-" + p, vc.S_PREGAME,
                               vc.SRC_VENUE_LIVE_FLAG, p)
        for src in vc.STATE_SOURCES:
            await conn.execute(base, "id-s-" + src, vc.S_UNKNOWN, src, None)
        await _expect(conn, E, base, "bad1", "LIVEISH", vc.SRC_SCHEDULE, None)
        await _expect(conn, E, base, "bad2", vc.S_LIVE, "GUESS", None)
        await _expect(conn, E, base, "bad3", vc.S_LIVE, None, None)
        await _expect(conn, E, base, "bad4", None, None, "SOMEWHERE")
        # the rollback refuses while a receipt exists
        await _expect(conn, asyncpg.RaiseError, DOWN)
    finally:
        await tx.rollback()
        await conn.close()


@pg
async def test_249_rolls_back_cleanly_with_no_receipt_and_reapplies():
    conn = await asyncpg.connect(DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute(UP)
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT to_regclass('venue_catalogue_receipts')") is None
        cols = {r["column_name"] for r in await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'us_premap'")}
        assert not ({"listing_state", "listing_state_source",
                     "listing_pass"} & cols)
        assert "team_name" in cols and "signed" in cols       # 055 / 031 untouched
        await conn.execute(UP)
        assert await conn.fetchval(
            "SELECT to_regclass('venue_catalogue_receipts')") is not None
    finally:
        await tx.rollback()
        await conn.close()


def test_the_migration_states_exactly_the_modules_vocabulary():
    for words in (vc.STATES, vc.STATE_SOURCES, vc.PASSES):
        for w in words:
            assert "'%s'" % w in UP, w


# ── §4 the readers keep the horizon they always measured ─────────────────

@pg
async def test_the_census_and_the_board_keep_their_horizon_as_the_catalogue_grows():
    """The writer now also holds next week's slate and the futures (premap
    AHEAD). The census and the board measured the sweep's own window only
    because nothing past +96 h existed; the bound is now stated, so a
    far-future listing neither inflates NO_FEED_EVENT nor ranks a
    competition for the metered budget."""
    from sportsassets import pinnapi_census as C

    conn, tx = await _tx()
    try:
        near = datetime.now(timezone.utc) + timedelta(hours=6)
        far = datetime.now(timezone.utc) + timedelta(days=9)
        for ev, at in (("nfl-kc-lv-2026-10-05", near),
                       ("nfl-kc-lv-2026-10-12", far)):
            for side, intent in (("chiefs", "ORDER_INTENT_BUY_LONG"),
                                 ("raiders", "ORDER_INTENT_BUY_SHORT")):
                await conn.execute(ROW, "aec-" + ev, ev, "KC vs. LV", side,
                                   intent, "football_team_full_game_winner", at)
        got = await conn.fetch(C.catalogue_sql(sport_ids=[5]))
        assert {r["event_slug"] for r in got} == {"nfl-kc-lv-2026-10-05"}
        totals = await conn.fetch(C.catalogue_totals_sql())
        assert sum(int(r["n"]) for r in totals) == 2
        fb = await loop.venue_football_competitions(conn)
        assert fb["board"] == [("nfl", 1)]
        # the frozen bettor_state frame and the shadow universe sample the
        # population they always sampled: a future that started four weeks
        # ago (STARTED_EARLIER) and next week's game (AHEAD), both freshly
        # written, are not in it
        old = datetime.now(timezone.utc) - timedelta(days=27)
        await conn.execute(ROW, "tec-mlb-nlchamp-lad", "mlb-nlchamp-2026-09-27",
                           "National League Champion", "yes",
                           "ORDER_INTENT_BUY_LONG", "futures", old)
        from sportsassets import shadow_bettor as sb
        from sportsassets.workers import bettor_state as bs
        frame = {r["event_slug"] for r in await conn.fetch(bs.PREMAP_SQL, "7200")}
        assert frame == {"nfl-kc-lv-2026-10-05"}, frame
        uni = {r["event_slug"] for r in await conn.fetch(
            sb.UNIVERSE_SQL, "7200", 40)}
        assert uni == {"nfl-kc-lv-2026-10-05"}, uni
    finally:
        await tx.rollback()
        await conn.close()
