"""CAPITAL-CRITICAL: THE HELD WATCH TARGETS EXACTLY THE FIXTURES THE HELD
READS PRICE -- LINE CONTRACTS AND ENTRY-PROVEN FIXTURES INCLUDED.

Production (research-sql run 37840684877, complete-packet Xavier reviews
since 2026-10-07 04:37Z): the held NFL spread asc-nfl-tb-dal-2026-10-08-
pos-9pt5 was read current from the in-process PinnAPI cache 267 times
(CURRENT_BLEND_HELD_CACHE, EXACT_STRUCTURED_NAMES), yet the held watch
(pinnapi_held.refresh -> pinnapi_feed_runtime.held_event_id) refused it
HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE: it was never a priority
target, so no provider change or provider-stamped confirmation ever
triggered its review -- only the 60 s backstop, 2-7 complete reviews an
hour. Four other groups were read current ONLY through the entry-proven
fixture (ENTRY_PROVEN_PROVIDER_FIXTURE), which the watch never consulted.

Pinned here (the real FeedCache, census and held reads; fake catalogue
connections answer the catalogue reads; one real-Postgres proof that the
entry-fixture read runs against the real schema):
  * a held line contract whose family is proven resolves to the SAME
    fixture the held line read prices, and becomes a watch target whose
    money-line change triggers its review;
  * the entry-proven fixture resolves a held contract exact names do not;
  * an unproven line family, a non-line type and an unscoped sport still
    refuse by name; nothing about the 30 s rule moves (the review reads the
    held contract's own quote).
"""
from __future__ import annotations

import asyncio
import time
import types

import pytest

from sportsassets import bettor_market_family as MF
from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_held as PH

from tests import paper_harness as H
from tests.test_r30a_line_market_family import FID, nfl_cache
from tests.test_xavier_held_line_read import _event_rows, _row

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

SPREAD = "football_team_full_game_spread"
SLUG = "asc-nfl-det-car-2026-10-04-pos-10pt5"


class _Catalogue:
    """The reads the held watch makes: the held slugs, the entry-proven
    fixtures, then held_event_id's two catalogue reads -- answered from the
    given rows (an explicit fake input; no database)."""

    def __init__(self, row, event_rows, *, entry=None):
        self.row, self.event_rows, self.entry = row, event_rows, entry
        self.sql: list = []

    async def fetchrow(self, sql, slug):
        self.sql.append(sql)
        return None if self.row is None else dict(self.row)

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        if "external_valuations" in sql:        # HELD_ENTRY_FIXTURES_SQL
            return ([] if self.entry is None else
                    [{"slug": self.row["identifier"],
                      "entry_event_key": self.entry}])
        if "paper_fills" in sql:                # HELD_SLUGS_SQL
            return [{"slug": self.row["identifier"], "kind": "PAPER"}]
        return [dict(r) for r in self.event_rows]


def _own(monkeypatch, cache, sport_ids=(5,)):
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(
        cache=cache, sport_ids=sorted(sport_ids)))


def test_a_held_spread_resolves_to_the_fixture_its_held_read_prices(
        monkeypatch):
    cache = nfl_cache()
    _own(monkeypatch, cache)
    row = _row(SPREAD, slug=SLUG)
    rows = _event_rows(row)
    # the held read prices it (unchanged) ...
    q = FR.held_line_quote(row, event_rows=rows,
                           payout_event="Detroit Lions -10.5",
                           payout_is_complement=False, at=time.time(),
                           max_age_s=30.0, sport_ids=[5], synced=True,
                           view=C.feed_event_view(cache), cache=cache,
                           entry_line=-10.5)
    assert q["ok"] and q["feed_event_id"] == FID, q
    # ... and the watch resolves the SAME fixture. BASE: (None,
    # HELD_VENUE_TYPE_NOT_PROVED_FULL_GAME_MONEYLINE) -- never a target
    got = asyncio.run(FR.held_event_id(_Catalogue(row, rows), SLUG))
    assert got == (FID, None)


def test_the_held_spread_is_a_watch_target_whose_change_triggers_its_review(
        monkeypatch):
    cache = nfl_cache()
    _own(monkeypatch, cache)
    row = _row(SPREAD, slug=SLUG)
    watch = PH.HeldWatch(clock=time.time)
    heard: list = []
    PH.add_listener(lambda fid, slugs: heard.append((fid, slugs)),
                    watch=watch)
    out = asyncio.run(PH.refresh(_Catalogue(row, _event_rows(row)),
                                 watch=watch))
    assert out["ok"], out
    assert watch.is_held(FID) and watch.slug_event == {SLUG: FID}
    assert watch.unmatched == {}
    # the fixture's money line moves on the feed: the held spread's review
    # is triggered at once (the review then reads the SPREAD's own quote)
    PH.install(cache, watch=watch)
    ml = cache.quotes[(FID, F.FULL_GAME_MONEYLINE_KEY)]
    moved = [{"designation": d, "price": (p + 5 if p > 0 else p - 5)}
             for d, p in ml.prices.items()]
    cache.apply({"type": "prematch_markets", "matchup_id": FID,
                 "sport_id": 5, "ts": time.time() * 1000.0,
                 "data": [{"key": F.FULL_GAME_MONEYLINE_KEY,
                           "type": "moneyline", "period": 0,
                           "status": "open", "prices": moved}]},
                epoch=cache.authority.epoch, received_ms=time.time() * 1000)
    assert heard and heard[-1] == (FID, [SLUG])
    assert watch.changed_at(SLUG) is not None


def test_the_entry_proven_fixture_resolves_what_exact_names_cannot(
        monkeypatch):
    cache = nfl_cache()
    _own(monkeypatch, cache)
    # venue rows that do not group to two teams (exact identity fails)
    lone = _row(SPREAD, slug=SLUG)
    cat = _Catalogue(lone, [lone])
    no_key = asyncio.run(FR.held_event_id(cat, SLUG))
    assert no_key[0] is None and no_key[1] in (
        C.S_NO_FEED_EVENT, "STRUCTURED_PARTICIPANTS_NOT_TWO")
    # BASE: held_event_id took no entry key at all (TypeError)
    keyed = asyncio.run(FR.held_event_id(
        cat, SLUG, entry_event_key="pinnapi:%s" % FID))
    assert keyed == (FID, None)
    # the held read anchors on the same fixture
    q = FR.held_line_quote(lone, event_rows=[lone],
                           payout_event="Detroit Lions -10.5",
                           payout_is_complement=False, at=time.time(),
                           max_age_s=30.0, sport_ids=[5], synced=True,
                           view=C.feed_event_view(cache), cache=cache,
                           entry_line=-10.5,
                           entry_event_key="pinnapi:%s" % FID)
    assert q["ok"] and q["identity_basis"] == FR.IDENTITY_ENTRY_FIXTURE
    # the watch reads the entry-proven fixture of the held contract
    watch = PH.HeldWatch(clock=time.time)
    out = asyncio.run(PH.refresh(
        _Catalogue(lone, [lone], entry="pinnapi:%s" % FID), watch=watch))
    assert out["ok"] and watch.is_held(FID), out
    # a key that is not a PinnAPI fixture is ignored, never guessed at
    other = PH.HeldWatch(clock=time.time)
    asyncio.run(PH.refresh(_Catalogue(lone, [lone], entry="e-%s" % SLUG),
                           watch=other))
    assert not other.is_held(FID)


def test_a_held_moneyline_takes_the_entry_proven_fixture_too(monkeypatch):
    cache = nfl_cache()
    _own(monkeypatch, cache)
    win = _row("football_team_full_game_winner",
               slug="aec-nfl-det-car-2026-10-04-det")
    assert asyncio.run(FR.held_event_id(_Catalogue(win, [win]), win[
        "identifier"]))[0] is None
    got = asyncio.run(FR.held_event_id(
        _Catalogue(win, [win]), win["identifier"],
        entry_event_key="pinnapi:%s" % FID))
    assert got == (FID, None)


def test_unproven_families_other_types_and_unscoped_sports_still_refuse(
        monkeypatch):
    cache = nfl_cache()
    _own(monkeypatch, cache)
    tennis = dict(_row("tennis_match_games_spread", slug="t-1"),
                  team_league="atp")
    got = asyncio.run(FR.held_event_id(_Catalogue(tennis, [tennis]), "t-1",
                                       entry_event_key="pinnapi:%s" % FID))
    assert got == (None, MF.family_status("tennis", MF.SPREAD)["refusal"])
    prop = dict(_row("football_player_passing_yards", slug="p-1"))
    assert asyncio.run(FR.held_event_id(_Catalogue(prop, [prop]), "p-1")) \
        == (None, FR.R_HELD_TYPE_UNPROVED)
    _own(monkeypatch, cache, sport_ids=(1, 6))
    row = _row(SPREAD, slug=SLUG)
    assert asyncio.run(FR.held_event_id(_Catalogue(row, _event_rows(row)),
                                        SLUG)) == (None, C.S_OUT_OF_SCOPE)


def test_a_start_the_venue_does_not_state_is_never_matched(monkeypatch):
    _own(monkeypatch, nfl_cache())
    row = dict(_row(SPREAD, slug=SLUG), game_start=None)
    assert asyncio.run(FR.held_event_id(_Catalogue(row, [row]), SLUG)) == \
        (None, FR.R_HELD_TIME_UNPROVED)


def test_the_entry_fixture_prefix_is_the_held_reads_own():
    assert FR.ENTRY_FIXTURE_PREFIX == "pinnapi:"
    assert "LIKE 'pinnapi:%'" in PH.HELD_ENTRY_FIXTURES_SQL


@pg
async def test_the_entry_fixture_read_runs_on_the_real_schema():
    from tests import paper_live_fixture as PL
    from sportsassets import bettor_paper_ledger as L
    from sportsassets import bettor_paper_simulator as SIM
    conn = await H.connect()
    try:
        a = await H.new_account(conn, "rc6held")
        slug = "rc6-held-%s" % a["account_id"][-10:]
        e = H.order(a, key="e", qty=10, limit=0.40, slug=slug, at=H.T0)
        e["decision_id"] = await PL.entry_identity(conn, a, slug=slug,
                                                   at=H.T0)
        ge = await L.submit_order(conn, e, fee_fn=H.zero_fee, now=H.T0)
        await H.observe(conn, slug, H.T0 + 3, offers=[(0.40, 100)],
                        bids=[(0.38, 100)])
        await SIM.simulate_order(conn, ge["order"]["order_id"],
                                 now=H.T0 + 4, fee_fn=H.zero_fee)
        vid = await conn.fetchval(
            "SELECT valuation_id FROM paper_decisions WHERE decision_id=$1",
            e["decision_id"])
        # the fixture's own entry key (as pinnapi discovery writes it)
        await conn.execute("UPDATE external_valuations SET event_key=$2 "
                           " WHERE id=$1", vid, "pinnapi:%s" % FID)
        got = await PH.held_entry_fixtures(conn)
        assert got.get(slug) == "pinnapi:%s" % FID
        # an entry key that names no PinnAPI fixture is not returned
        await conn.execute("UPDATE external_valuations SET event_key=$2 "
                           " WHERE id=$1", vid, "e-%s" % slug)
        assert slug not in await PH.held_entry_fixtures(conn)
    finally:
        await conn.close()
