"""R30A P0 INCIDENT -- PINNAPI SCOPE, FEED CLOCK AND IN-PLAY FIXTURES.

Production-shaped inputs throughout: PinnAPI's own documentation lines
(tests/fixtures/pinnapi_ws_subscription_docs_2026_10_04.json), the raw
Pinnacle record shapes the bounded ws_sample captured (2026-10-01: a live
child 1637543257 whose parentId is the prematch matchup 1637360364; a
'Both Teams To Score?' special carrying the s;0;m key and the game's own
participant names; a tennis '(Games)' child), and the production scope row
migration 193 wrote.

  §1 SCOPE (RC1). Every PinnAPI sport at both Pinnacle and the venue is
     subscribed -- the six ids read off the documentation table by NAME,
     never typed in -- with no four-id truncation; any other id is refused by
     name; migration 260 replaces 193's row (kept, restorable), is
     idempotent, and the owner subscribes all six on its one socket.
  §2 FEED CLOCK (RC4). The 30 s rule is unchanged and still measured from
     the last observed CHANGE: a snapshot never dates a price, confirmations
     are carried and counted but never make a quote fresh; a key that opens
     between two authoritative lists is an observed change; a change in a
     frame with no provider stamp is dated by our observation of it,
     labelled, with the source time left empty.
  §3 IN PLAY (RC3). A live-phase child prices its prematch parent's
     fixture; specials and derived-count children never do; a live game
     whose parent left the cache is its own fixture; the primary selector,
     the census view and the reactive scheduler all follow the fixture.
"""
from __future__ import annotations

import asyncio
import copy
import json
import pathlib
import re
from datetime import datetime, timezone

import asyncpg
import pytest

from sportsassets import pinnapi_census as C
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as R
from sportsassets import pinnapi_owner as O
from sportsassets import pinnapi_primary as P
from sportsassets import pinnapi_reactive as RX

from tests import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"
MIG = pathlib.Path(__file__).resolve().parents[1] / "migrations"
DOCS = json.loads((FIX / "pinnapi_ws_subscription_docs_2026_10_04.json")
                  .read_text())
UP = (MIG / "260_pinnapi_feed_scope_all_sports.sql").read_text()
DOWN = (MIG / "rollback" / "260_pinnapi_feed_scope_all_sports.down.sql"
        ).read_text()
ROW_193 = {"why": "migration 193: soccer added for held paper positions",
           "streams": ["live", "prematch"], "sport_ids": [6, 1]}


def _doc_ids() -> dict:
    """{sport name's first word, lower-case: id} from the documented table."""
    out = {}
    for line in DOCS["excerpt"]["sport_ids_table"]:
        m = re.match(r"^\| (\d+) \| ([^|]+) \|$", line)
        if m:
            out[m.group(2).split()[0].strip().lower()] = int(m.group(1))
    return out


# ═════════════════════════════════════════════════════════════════════
# §1 SCOPE
# ═════════════════════════════════════════════════════════════════════

def test_the_six_scope_ids_are_pinnapis_own_documented_ids():
    ids = _doc_ids()
    assert len(ids) == 12, "the documented table lists 12 sports"
    six = {ids[n] for n in ("soccer", "tennis", "basketball", "hockey",
                            "football", "baseball")}
    assert set(R.SCOPE_SPORTS) == six
    for name, sid in ids.items():
        assert R.PINNAPI_SPORT_IDS[sid].lower().startswith(name)
    assert R.ALLOWED_SPORTS == set(ids.values())
    # the primary selector and the census name the same ids
    assert {P.SPORTS[f] for f in ("soccer", "tennis", "basketball", "hockey",
                                  "football", "baseball")} == six
    for prefix in ("soccer", "tennis", "basketball", "hockey", "football",
                   "baseball"):
        assert C.sport_id_of(prefix + "_team_full_game_winner") == \
            ids[prefix]
    # the documented limits the scope is inside: one socket, sport-level
    # subscriptions uncapped, only EVENT ids capped (200 per stream)
    rules = " ".join(DOCS["excerpt"]["subscribe_rules"])
    assert "One WebSocket connection per account" in rules
    assert "200 event_ids per connection per stream" in rules
    assert "its sport_id matches a sport-level subscription" in rules
    assert DOCS["sha256"].startswith("162705de")


def test_scope_subscribes_all_six_without_the_four_id_truncation():
    row = {"sport_ids": [1, 2, 3, 4, 5, 6], "streams": ["live", "prematch"]}
    got = R.scope_of(row)
    # before R30A: [1, 2, 3, 4] -- football and baseball silently cut
    assert got == {"sport_ids": [1, 2, 3, 4, 5, 6],
                   "streams": ["live", "prematch"]}
    assert R.scope_of(ROW_193)["sport_ids"] == [1, 6]
    assert R.scope_of(json.dumps(row))["sport_ids"] == [1, 2, 3, 4, 5, 6]


def test_scope_refuses_unmeasured_and_undocumented_ids_by_name():
    got = R.scope_of({"sport_ids": list(range(1, 13)) + [99, "x", True, 6],
                      "streams": ["live", "bogus"]})
    assert got["sport_ids"] == [1, 2, 3, 4, 5, 6]
    assert got["streams"] == ["live"]
    ref = got["refused"]
    for sid in range(7, 13):
        assert ref[str(sid)] == R.R_SCOPE_UNMEASURED
    assert ref["99"] == R.R_SCOPE_UNDOCUMENTED
    assert ref["x"] == R.R_SCOPE_NOT_AN_ID
    assert ref["True"] == R.R_SCOPE_NOT_AN_ID
    # absent or malformed: the unchanged default
    assert R.scope_of(None) == R.DEFAULT_SCOPE
    assert R.scope_of("not json") == R.DEFAULT_SCOPE


def test_the_owner_subscribes_every_scoped_sport_on_its_one_socket():
    sc = R.scope_of({"sport_ids": [1, 2, 3, 4, 5, 6],
                     "streams": ["live", "prematch"]})
    owner = O.FeedOwner(F.FeedCache(), sport_ids=sc["sport_ids"],
                        streams=sc["streams"], lease_factory=None,
                        connect=None)
    subs = owner.subscriptions()
    assert len(subs) == 12
    assert {sp for _, sp in subs} == {1, 2, 3, 4, 5, 6}
    # resync waits for a snapshot of every (stream, sport)
    cache = owner.cache
    ep = cache.new_connection(subs)
    for i, (stream, sp) in enumerate(subs):
        assert not cache.authority.synced
        cache.apply({"type": "snapshot", "stream": stream, "sport_id": sp,
                     "ts": 1_000 + i, "events": []}, epoch=ep,
                    received_ms=1_000 + i)
    assert cache.authority.synced
    # the census reads the catalogue rows of all six sports
    sql = C.catalogue_sql(sport_ids=set(sc["sport_ids"]))
    for prefix in ("soccer", "tennis", "basketball", "hockey", "football",
                   "baseball"):
        assert "LIKE '%s%%'" % prefix in sql


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *_):
                return False
        return Ctx()


@pg
async def test_migration_260_replaces_193s_row_keeps_it_and_rolls_back():
    conn = await asyncpg.connect(H.DSN)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           R.SCOPE_KEY)
        await conn.execute("INSERT INTO ingestion_state (key, value) "
                           "VALUES ($1, $2::jsonb)", R.SCOPE_KEY,
                           json.dumps(ROW_193))
        await conn.execute(UP)
        v = json.loads(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", R.SCOPE_KEY))
        assert v["sport_ids"] == [1, 2, 3, 4, 5, 6]
        assert v["replaced"] == ROW_193
        # the runtime reads it through its own bounded read
        sc = await R.scope(_Pool(conn))
        assert sc == {"sport_ids": [1, 2, 3, 4, 5, 6],
                      "streams": ["live", "prematch"]}
        # idempotent: a re-run keeps the ORIGINAL replaced row
        await conn.execute(UP)
        v2 = json.loads(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", R.SCOPE_KEY))
        assert v2 == v
        # rollback restores 193's row exactly
        await conn.execute(DOWN)
        back = json.loads(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", R.SCOPE_KEY))
        assert back == ROW_193
        # on a database with no scope row: 260 inserts, rollback removes
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           R.SCOPE_KEY)
        await conn.execute(UP)
        v3 = json.loads(await conn.fetchval(
            "SELECT value FROM ingestion_state WHERE key = $1", R.SCOPE_KEY))
        assert v3["sport_ids"] == [1, 2, 3, 4, 5, 6] and "replaced" not in v3
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key = $1",
            R.SCOPE_KEY) == 0
        # an operator's later scope is never touched by the rollback
        await conn.execute("INSERT INTO ingestion_state (key, value) "
                           "VALUES ($1, '{\"sport_ids\": [6]}'::jsonb)",
                           R.SCOPE_KEY)
        await conn.execute(DOWN)
        assert await conn.fetchval(
            "SELECT count(*) FROM ingestion_state WHERE key = $1",
            R.SCOPE_KEY) == 1
    finally:
        await tx.rollback()
        await conn.close()


# ═════════════════════════════════════════════════════════════════════
# §2 FEED CLOCK
# ═════════════════════════════════════════════════════════════════════

ML = {"key": "s;0;m", "type": "moneyline", "period": 0, "status": "open",
      "prices": [{"designation": "home", "price": -120},
                 {"designation": "away", "price": 105}]}
TOTAL = {"key": "s;0;ou;47.5", "type": "total", "period": 0,
         "status": "open",
         "prices": [{"designation": "over", "price": -108, "points": 47.5},
                    {"designation": "under", "price": -112,
                     "points": 47.5}]}


def _moved(m, home):
    m = copy.deepcopy(m)
    m["prices"][0]["price"] = home
    return m


def test_a_snapshot_still_never_dates_a_price_and_confirmation_is_no_age():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 5)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": 1_000_000, "events": [{"id": 9, "version": 7,
                                          "markets": [ML]}]},
            epoch=ep, received_ms=1_000_020)
    r = c.read(9, "s;0;m", evaluated_ms=1_000_500)
    assert r["reason"] == F.R_NO_CHANGE_TIME
    # an authoritative list re-delivering the SAME price, and matchup frames
    # at the same version: confirmed, counted -- and still no age
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_005_000, "data": [dict(ML, version=7)]}, epoch=ep,
            received_ms=1_005_030)
    c.apply({"type": "prematch_matchups", "sport_id": 5, "ts": 1_010_000,
             "data": [{"id": 9, "version": 7}]}, epoch=ep,
            received_ms=1_010_010)
    r = c.read(9, "s;0;m", evaluated_ms=1_010_500)
    assert r["reason"] == F.R_NO_CHANGE_TIME, "never a guessed age"
    p = r["provenance"]
    assert p["confirmed_ms"] == 1_010_000
    assert p["confirmed_by"] == F.C_MATCHUP_VERSION
    assert p["confirmation_is_a_decision_input"] is False
    assert p["source_change_ms"] is None and p["change_ms"] is None
    cen = c.census(now_ms=1_010_500)
    assert cen["fresh_now"] == 0
    assert cen["fresh_now_if_measured_from_confirmation"] == {
        "markets": 1, "status": "COUNTERFACTUAL_NOT_A_DECISION_INPUT"}
    assert cen["freshness_basis"] == F.FRESHNESS_BASIS
    # the switch the WIP carried is gone: nothing can re-base the rule
    assert not hasattr(c, "set_freshness_basis")
    assert not hasattr(F, "BASIS_CONFIRMED")


def test_a_key_opening_between_two_authoritative_lists_is_an_observed_change():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 5)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 5,
             "ts": 1_000_000, "events": [{"id": 9, "markets": [ML]}]},
            epoch=ep, received_ms=1_000_020)
    # the FIRST authoritative list after a reconnect: a new key there is
    # still first sight (nothing earlier on this epoch says when it opened)
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_002_000, "data": [ML, TOTAL]}, epoch=ep,
            received_ms=1_002_040)
    assert c.read(9, TOTAL["key"], evaluated_ms=1_002_100)["reason"] == \
        F.R_NO_CHANGE_TIME
    # a SECOND list carrying a key the first did not: it opened between them
    alt = dict(TOTAL, key="s;0;ou;48.5", prices=[
        {"designation": "over", "price": 110, "points": 48.5},
        {"designation": "under", "price": -130, "points": 48.5}])
    c.apply({"type": "prematch_markets", "sport_id": 5, "matchup_id": 9,
             "ts": 1_020_000, "data": [ML, TOTAL, alt]}, epoch=ep,
            received_ms=1_020_050)
    r = c.read(9, alt["key"], evaluated_ms=1_030_000)
    assert r["ok"] is True
    assert r["provenance"]["source_change_ms"] == 1_020_000
    assert r["provenance"]["change_clock"] == F.CHANGE_CLOCK_PROVIDER
    assert r["provenance"]["observed_change_ms"] == 1_020_050
    assert c.counts["new_key_observed_as_change"] == 1
    # the 30 s rule is unchanged: 30.0 s passes, 30.001 s does not
    assert c.read(9, alt["key"], evaluated_ms=1_050_000)["ok"] is True
    assert c.read(9, alt["key"], evaluated_ms=1_050_001)["reason"] == \
        F.R_STALE
    # the keys the earlier list carried keep no change time
    assert c.read(9, TOTAL["key"], evaluated_ms=1_020_100)["reason"] == \
        F.R_NO_CHANGE_TIME


def test_an_unstamped_change_is_dated_by_our_observation_never_as_source():
    c = F.FeedCache()
    seen = []
    c.on_change = seen.append
    ep = c.new_connection([("live", 4)])
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 4,
             "ts": 2_000_000, "events": [{"id": 5, "markets": [ML]}]},
            epoch=ep, received_ms=2_000_010)
    # the provider forwards the change with no envelope stamp
    c.apply({"type": "live", "sport_id": 4, "op": "upd",
             "rec": {"id": 5, "markets": [_moved(ML, -140)]}}, epoch=ep,
            received_ms=2_004_000)
    r = c.read(5, "s;0;m", evaluated_ms=2_010_000)
    assert r["ok"] is True
    p = r["provenance"]
    assert p["source_change_ms"] is None, "never filled from our clock"
    assert p["observed_change_ms"] == 2_004_000
    assert p["change_clock"] == F.CHANGE_CLOCK_LOCAL
    assert p["change_ms"] == 2_004_000 and p["quote_age_s"] == 6.0
    # the same 30 s rule, from the observation
    assert c.read(5, "s;0;m", evaluated_ms=2_034_001)["reason"] == F.R_STALE
    # the change was announced (a stamped change would have been too)
    assert len(seen) == 1 and seen[0].change_ms == 2_004_000
    # an unchanged unstamped re-send keeps the observation, never re-dates
    c.apply({"type": "live", "sport_id": 4, "op": "upd",
             "rec": {"id": 5, "markets": [_moved(ML, -140)]}}, epoch=ep,
            received_ms=2_020_000)
    assert c.read(5, "s;0;m", evaluated_ms=2_020_100)["provenance"][
        "change_ms"] == 2_004_000
    assert c.census(now_ms=2_020_100)[
        "markets_change_dated_by_local_observation"] == 1
    # an unstamped SNAPSHOT never dates anything
    c2 = F.FeedCache()
    ep2 = c2.new_connection([("live", 4)])
    c2.apply({"type": "snapshot", "stream": "live", "sport_id": 4,
              "events": [{"id": 5, "markets": [ML]}]}, epoch=ep2,
             received_ms=3_000_000)
    assert c2.read(5, "s;0;m", evaluated_ms=3_000_100)["reason"] == \
        F.R_NO_CHANGE_TIME


def test_a_moved_line_under_the_same_key_is_a_change():
    tt = {"key": "s;0;tt;home", "type": "team_total", "period": 0,
          "side": "home", "status": "open",
          "prices": [{"designation": "over", "price": -110, "points": 24.5},
                     {"designation": "under", "price": -110,
                      "points": 24.5}]}
    c = F.FeedCache()
    ep = c.new_connection([("live", 5)])
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 5,
             "ts": 1_000, "events": [{"id": 3, "markets": [tt]}]}, epoch=ep,
            received_ms=1_010)
    moved = copy.deepcopy(tt)
    for pr in moved["prices"]:
        pr["points"] = 23.5
    c.apply({"type": "live", "sport_id": 5, "op": "upd", "ts": 9_000,
             "rec": {"id": 3, "markets": [moved]}}, epoch=ep,
            received_ms=9_010)
    r = c.read(3, "s;0;tt;home", evaluated_ms=10_000)
    assert r["ok"] and r["quote"].points == {"over": 23.5, "under": 23.5}
    assert r["provenance"]["source_change_ms"] == 9_000


# ═════════════════════════════════════════════════════════════════════
# §3 IN PLAY
# ═════════════════════════════════════════════════════════════════════

AT = 1_790_896_803.5


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


PARENT = {"id": 1637360364, "type": "matchup", "isLive": False,
          "startTime": "2026-10-01T21:30:00Z", "units": "Regular",
          "participants": [
              {"name": "Fuerte San Francisco", "alignment": "home"},
              {"name": "Luis Angel Firpo", "alignment": "away"}],
          "markets": [ML]}
CHILD = {"id": 1637543257, "type": "matchup", "status": "started",
         "startTime": "2026-10-01T21:30:00Z", "isLive": True,
         "parentId": 1637360364, "version": 1790896801, "units": "Regular",
         "participants": [
             {"name": "Fuerte San Francisco", "alignment": "home"},
             {"name": "Luis Angel Firpo", "alignment": "away"}],
         "markets": []}
SPECIAL = {"id": 1637550528, "type": "matchup", "isLive": True,
           "parentId": 1637543257, "special": "Both Teams To Score?",
           "units": "Regular", "startTime": "2026-10-01T21:30:00Z",
           "participants": [
               {"name": "Fuerte San Francisco", "alignment": "home"},
               {"name": "Luis Angel Firpo", "alignment": "away"}],
           "markets": [ML]}
GAMES = {"id": 1637453398, "type": "matchup", "isLive": True,
         "parentId": 1637360364, "units": "Games",
         "startTime": "2026-10-01T21:30:00Z",
         "participants": [
             {"name": "Fuerte San Francisco (Games)", "alignment": "home"},
             {"name": "Luis Angel Firpo (Games)", "alignment": "away"}],
         "markets": [ML]}
SOCCER_ML = {"key": "s;0;m", "type": "moneyline", "period": 0,
             "status": "open",
             "prices": [{"designation": "home", "price": 300},
                        {"designation": "away", "price": -150},
                        {"designation": "draw", "price": 260}]}


def _inplay_cache():
    c = F.FeedCache()
    ep = c.new_connection([("prematch", 1), ("live", 1)])
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": 1,
             "ts": (AT - 4000) * 1000,
             "events": [dict(PARENT, markets=[SOCCER_ML])]}, epoch=ep,
            received_ms=(AT - 4000) * 1000)
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 1,
             "ts": (AT - 30) * 1000, "events": [
                 dict(CHILD, markets=[SOCCER_ML]), SPECIAL, GAMES]},
            epoch=ep, received_ms=(AT - 30) * 1000)
    moved = _moved(SOCCER_ML, 320)
    c.apply({"type": "live", "sport_id": 1, "op": "upd",
             "ts": (AT - 2) * 1000,
             "rec": dict(CHILD, markets=[moved])}, epoch=ep,
            received_ms=(AT - 2) * 1000 + 40)
    return c, ep


def test_the_live_phase_child_prices_its_parents_fixture():
    c, _ = _inplay_cache()
    view, skipped = F.fixture_view(c.events)
    assert [(v["id"], v["quote_id"], v["basis"]) for v in view] == [
        (1637360364, 1637543257, F.B_LIVE_CHILD)]
    assert view[0]["live"] is True
    # the special (same names, the s;0;m key) and the '(Games)' child price
    # no fixture, each under its own name
    assert skipped[F.R_CHILD_OF_A_CHILD] == 1          # the special's parent
    assert skipped[F.R_CHILD_UNITS] == 1               # '(Games)'
    assert c.canonical_id(1637543257) == 1637360364
    assert c.fixture_quote_id(1637360364) == (1637543257, None)
    cv = C.feed_event_view(c)
    assert cv[1][0]["id"] == 1637360364 and cv[1][0]["quote_id"] == \
        1637543257


def test_two_live_phase_children_are_ambiguous_never_a_pick():
    c, ep = _inplay_cache()
    twin = dict(CHILD, id=1637543999, markets=[SOCCER_ML])
    c.apply({"type": "live", "sport_id": 1, "op": "add", "ts": AT * 1000,
             "rec": twin}, epoch=ep, received_ms=AT * 1000)
    view, skipped = F.fixture_view(c.events)
    assert view == []
    assert skipped[F.R_LIVE_PHASE_AMBIGUOUS] == 1
    assert c.fixture_quote_id(1637360364) == (None, F.R_LIVE_PHASE_AMBIGUOUS)


def test_a_live_game_whose_parent_left_the_cache_is_its_own_fixture():
    c, ep = _inplay_cache()
    # PinnAPI: a live "del" is "event removed by Pinnacle (kicked off ...)"
    c.apply({"type": "live", "sport_id": 1, "op": "del", "ts": AT * 1000,
             "rec": {"id": 1637360364}}, epoch=ep, received_ms=AT * 1000)
    view, skipped = F.fixture_view(c.events)
    assert [(v["id"], v["quote_id"], v["basis"]) for v in view] == [
        (1637543257, 1637543257, F.B_ORPHAN_LIVE)]
    # its special and derived children still price nothing
    assert sum(n for k, n in skipped.items()
               if k.endswith("|PARENT_NOT_HELD")) == 1   # '(Games)'


def test_the_primary_selector_prices_the_live_child_for_the_matched_fixture():
    c, ep = _inplay_cache()
    event = {"id": "pinnapi:1637360364", "home_team": "Fuerte San Francisco",
             "away_team": "Luis Angel Firpo",
             "commence_time": "2026-10-01T21:30:00Z", "bookmakers": []}
    got = P.select(c, event, None, family="soccer",
                   sharp_books=("pinnacle",), at=AT, max_age_s=30.0,
                   runtime_id="rt")
    assert got is not None
    ref = got["reference_input"]
    assert ref["feed_event_id"] == 1637360364
    assert ref["quote_event_id"] == 1637543257
    assert ref["stream"] == "live" and ref["change_clock"] == \
        F.CHANGE_CLOCK_PROVIDER
    assert got["observed_at"] == pytest.approx(AT - 2)
    assert P.validate(c, got, at=AT + 1, runtime_id="rt")["ok"] is True
    # the live record is replaced by another: validation refuses by name
    c.apply({"type": "live", "sport_id": 1, "op": "del",
             "ts": (AT + 2) * 1000, "rec": {"id": 1637543257}}, epoch=ep,
            received_ms=(AT + 2) * 1000)
    v = P.validate(c, got, at=AT + 3, runtime_id="rt")
    assert v["ok"] is False


def test_a_live_child_change_reaches_the_seed_of_its_fixture():
    c, ep = _inplay_cache()
    evaluated = []

    async def evaluate(job):
        evaluated.append(job)
        return {"ok": True}

    async def audit(_attempt):
        return None

    class NoHeld:
        def is_held(self, _eid):
            return False

    s = RX.Scheduler(c, evaluate, audit, clock=lambda: AT, held=NoHeld())
    event = {"id": "pinnapi:1637360364", "home_team": "Fuerte San Francisco",
             "away_team": "Luis Angel Firpo",
             "commence_time": "2026-10-01T21:30:00Z"}
    s.register(event, sport_key="pinnapi_soccer", family="soccer",
               received_at=AT)
    assert 1637360364 in s.seeds, "seeded on the FIXTURE"
    c.on_change = s.changed
    moved = _moved(SOCCER_ML, 340)
    c.apply({"type": "live", "sport_id": 1, "op": "upd",
             "ts": (AT - 1) * 1000, "rec": dict(CHILD, markets=[moved])},
            epoch=ep, received_ms=(AT - 1) * 1000 + 10)
    assert 1637360364 in s.pending
    assert s.counts["NO_CONFIRMED_DISCOVERY"] == 0

    async def one():
        task = asyncio.create_task(s.run())
        for _ in range(50):
            if evaluated:
                break
            await asyncio.sleep(0.01)
        s.closed = True
        s.wake.set()
        task.cancel()
    asyncio.run(one())
    assert len(evaluated) == 1
    assert evaluated[0]["trigger"]["version"][1] == (AT - 1) * 1000
