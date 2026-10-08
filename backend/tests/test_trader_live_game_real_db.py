"""LIVE GAME STATE V1 IS DISPLAY ONLY -- PROVEN ON A REAL DATABASE.

BETTOR Live Game State V1 (migration 316) attaches a provider score to the
existing authenticated Trader response. Its package proved that with doubles
(apply_rows over a synthetic snapshot) and shipped seven real-PostgreSQL
tests that run only with SCORE_TEST_DSN, which no CI job sets. These proofs
run in backend-tests and capital-critical on the migrated database
(RN1X_TEST_DSN), through the real ledger writers and the real read model:

  * the package's seven Postgres proofs, ported unchanged in substance, on
    the INSTALLED migration file in an isolated schema
  * BETTOR_DISPLAY_SCORES on vs off: the Trader response is identical except
    positions[].game, score_feed and the snapshot digest -- on a position
    whose mark is current, whose packet is complete and which holds a
    resting protection, so "identical" compares real prices, packets and
    orders, not Nones; every guarded field, changed, is caught (the
    comparison is not vacuous); off is byte-identical to no enrichment
  * a FINAL score, collected by the real collector through the real
    canonical adapter, settles nothing: no ledger, order, fill, settlement,
    review or funded row is written, the position stays open and ACTIVE
  * a fresh score does not complete a stale management packet, and does not
    make a stale book current
  * research/lgs_held_fixture_census.sql (the production census) reaches
    the collector's own verdict for every seeded shape, including the two
    production shapes of 2026-10-08 (RC4 packet, pm-acceptance run
    37738089957: the held population was an NFL spread with a us_premap row
    and no venue_fixture_metadata row, and soccer whose fixture metadata, if
    any, is organiser-sourced UEFA 'UNL' with orientation SAME/SWAPPED --
    both CANONICAL_SCORE_FIXTURE_FIELDS_MISSING)

A skip here is not a pass: this file is on the capital-critical list.
"""
from __future__ import annotations

import ast
import asyncio
import collections
import copy
import json
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import asyncpg
import pytest

from sportsassets.api import trader_readmodel as TR
from sportsassets.live_game_state import core as C
from sportsassets.live_game_state import integration as I
from sportsassets.live_game_state.collector import Collector
from sportsassets.live_game_state.integration import enrich_snapshot
from sportsassets.live_game_state.providers import Reply, ScoreClient
from sportsassets.live_game_state.storage import PostgresStore
from tests import paper_harness as H
from tests._bettor_live_game_state_test_helpers import NOW, fixture, raw
from tests.test_trader_mode_real_db import _ctx, _fill, _pid, _review

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
T = H.T0
BACKEND = pathlib.Path(__file__).resolve().parents[1]
DISPLAY_MIGRATION = BACKEND / "migrations" / "316_trader_live_game_display.sql"
CENSUS_SQL = BACKEND.parent / "research" / "lgs_held_fixture_census.sql"
FRESHNESS_SQL = BACKEND.parent / "research" / "lgs_display_score_freshness.sql"
HOME, AWAY = "Boston Red Sox", "New York Yankees"


# ------------------------------------------------- the package's seven proofs
# Ported from the package's tests/postgres_integration.py (SCORE_TEST_DSN) to
# the repository's RN1X_TEST_DSN so CI runs them. The schema is the INSTALLED
# migration 316 (byte-identical to the package's schema.sql), in a random
# schema that is dropped afterwards.

@pytest.fixture
async def display_schema():
    schema = "test_lgs_" + uuid.uuid4().hex[:16]
    conn = await asyncpg.connect(H.DSN)
    await conn.execute("CREATE SCHEMA " + schema)
    await conn.execute("SET search_path TO " + schema)
    await conn.execute(DISPLAY_MIGRATION.read_text())

    async def setup(c):
        await c.execute("SET search_path TO " + schema)
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=3, setup=setup)
    try:
        yield conn, pool, PostgresStore(pool)
    finally:
        await pool.close()
        await conn.execute("DROP SCHEMA " + schema + " CASCADE")
        await conn.close()


@pg
async def test_write_and_deduplicate(display_schema):
    conn, _, store = display_schema
    a = await store.write(fixture(), raw(), now=NOW)
    b = await store.write(fixture(), raw(), now=NOW)
    assert a == b
    assert await conn.fetchval(
        "SELECT count(*) FROM trader_display_score_observations") == 1


@pg
async def test_binding_is_append_only(display_schema):
    conn, _, store = display_schema
    await store.write(fixture(), raw(), now=NOW)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await conn.execute("UPDATE trader_display_score_bindings SET payload='{}'")


@pg
async def test_observation_is_append_only(display_schema):
    conn, _, store = display_schema
    await store.write(fixture(), raw(), now=NOW)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError):
        await conn.execute("DELETE FROM trader_display_score_observations")


@pg
async def test_error_does_not_refresh_payload(display_schema):
    conn, _, store = display_schema
    await store.write(fixture(), raw(), now=NOW)
    await store.issue(fixture(), "HTTP_429", now=NOW + 10)
    r = json.loads(await conn.fetchval(
        "SELECT payload::text FROM trader_display_score_latest"))
    assert r["received_at"] == NOW


@pg
async def test_invalid_clock_not_persisted(display_schema):
    conn, _, store = display_schema
    with pytest.raises(ValueError):
        await store.write(fixture(), raw(received_at=NOW + 100), now=NOW)
    assert await conn.fetchval(
        "SELECT count(*) FROM trader_display_score_observations") == 0


@pg
async def test_real_db_enrichment_and_timeout_restore(display_schema, monkeypatch):
    _, pool, store = display_schema
    await store.write(fixture(), raw(), now=NOW)
    s = {"snapshot_id": "old", "positions": [
        {"venue": "POLYMARKET_US", "event_id": fixture().event_id,
         "state": "ACTIVE", "qty": 5, "orders": []}]}
    monkeypatch.setenv("BETTOR_DISPLAY_SCORES", "on")
    async with pool.acquire() as c:
        async with c.transaction(readonly=True, isolation="repeatable_read"):
            await c.execute("SET LOCAL statement_timeout=8000")
            r = await enrich_snapshot(c, s, now=NOW)
            assert await c.fetchval("SHOW statement_timeout") == "8s"
    assert r["positions"][0]["game"]["status"] == "CURRENT"
    assert r["positions"][0]["state"] == "ACTIVE"


@pg
async def test_concurrent_duplicate_writes_serialized(display_schema):
    conn, _, store = display_schema
    await asyncio.gather(*(store.write(fixture(), raw(), now=NOW) for _ in range(3)))
    assert await conn.fetchval(
        "SELECT count(*) FROM trader_display_score_observations") == 1


# ------------------------------------------------------------ seeding helpers

def _espn_board(*, start, state="STATUS_IN_PROGRESS", home_score="4",
                away_score="3", eid="401999777"):
    """A synthetic ESPN-shaped MLB scoreboard (TEST DATA, not a live game)."""
    iso = datetime.fromtimestamp(start, timezone.utc).isoformat()
    final = state.startswith("STATUS_FINAL")
    return {"leagues": [{"slug": "mlb"}], "events": [{
        "id": eid, "date": iso, "competitions": [{
            "id": eid, "date": iso, "competitors": [
                {"homeAway": "home", "score": home_score,
                 "team": {"id": "2", "displayName": HOME, "abbreviation": "BOS"}},
                {"homeAway": "away", "score": away_score,
                 "team": {"id": "10", "displayName": AWAY, "abbreviation": "NYY"}}],
            "status": {"period": 9 if final else 7, "clock": 0,
                       "displayClock": "0:00",
                       "type": {"name": state, "state": "post" if final else "in",
                                "shortDetail": "Final" if final else "Top 7th"}}}]}]}


def _display_fixture(event, start):
    return C.Fixture("POLYMARKET_US", event, "MLB", HOME, AWAY, start,
                     "SYNTHETIC_TEST_FIXTURE")


def _bound_score(f, *, received, state="STATUS_IN_PROGRESS"):
    [g] = C.parse_espn(_espn_board(start=f.start_at, state=state), league="MLB",
                       received_at=received, observed_at=received,
                       payload_hash="TEST_PAYLOAD_HASH")
    return C.bind_game(f, g)


async def _premap(conn, slug, event, *, start, team_league=None):
    await conn.execute(
        "INSERT INTO us_premap (identifier, event_slug, market_slug, side_norm,"
        " game_start, team_league) VALUES ($1, $2, $1, 'yes', to_timestamp($3), $4)",
        slug, event, start, team_league)


async def _fixture_metadata(conn, event, *, competition, home=HOME, away=AWAY,
                            start=None, orientation=None, raw_meta=None):
    await conn.execute(
        "INSERT INTO venue_fixture_metadata (venue, venue_fixture_key,"
        " sport_family, competition, home_team, away_team, orientation,"
        " scheduled_kickoff, source, source_url, retrieved_at, reader_version,"
        " raw) VALUES ('PMUS', 'event:' || $1, 'test', $2, $3, $4, $5,"
        " to_timestamp($6), 'TEST_FIXTURE_SYNTHETIC', 'test://synthetic', now(),"
        " 'TEST', $7::jsonb)",
        event, competition, home, away, orientation, start,
        json.dumps(raw_meta) if raw_meta is not None else None)


async def _protected_position(conn, tag):
    """A real PAPER position: filled, protected (resting), freshly marked,
    reviewed with a complete packet at T + 7."""
    a = await H.new_account(conn, tag)
    g = "paper_g_%s_lgs" % a["account_id"][-10:]
    s = "%s:lgs" % a["account_id"]
    await _fill(conn, a, slug=s, group=g)
    [prot] = await H.protect(conn, _ctx(a, T + 5), g, at=T + 5)
    await H.observe(conn, s, T + 6, offers=[(0.42, 50)], bids=[(0.41, 50)])
    pid = _pid(a, g, s, "LONG")
    await _review(conn, a, group=g, at=T + 7, position_key=pid,
                  protection_order=prot)
    return a, g, s, pid, prot


async def _read(pool, account_id, now, *, display):
    with patch.dict(os.environ, {"BETTOR_DISPLAY_SCORES": display}):
        return await TR.read(pool, account_id=account_id, now=now)


def _trading_view(snapshot):
    """Everything but the three things display enrichment may change."""
    v = copy.deepcopy(snapshot)
    v.pop("score_feed", None)
    v.pop("snapshot_id", None)
    for p in v.get("positions") or []:
        p.pop("game", None)
    return v


#: the fields the comparison must see: prices, probabilities, P&L,
#: protection, orders, quantities, state, counts and authority
GUARDED = (("positions", 0, "qty"), ("positions", 0, "quote", "bid"),
           ("positions", 0, "quote", "current"),
           ("positions", 0, "packet", "complete"),
           ("positions", 0, "packet", "missing"),
           ("positions", 0, "orders", 0, "limit_price"),
           ("positions", 0, "orders", 0, "state"),
           ("positions", 0, "unrealized_usd"), ("positions", 0, "realized_usd"),
           ("positions", 0, "cost_basis_usd"), ("positions", 0, "state"),
           ("positions", 0, "proposal"), ("positions", 0, "review"),
           ("positions", 0, "event_id"), ("counts", "complete_packets"),
           ("counts", "current_marks"), ("execution_authority",),
           ("authority",), ("total_position_count",))


def _set(d, path, value):
    for k in path[:-1]:
        d = d[k]
    d[path[-1]] = value


async def _account_rows(conn, account_id):
    """Every row the trading ledger keeps for one account, plus the funded
    tables: what a settlement, order or fill would have to write."""
    out = {}
    for t in ("paper_fills", "paper_orders", "paper_settlements_by_key",
              "paper_ledger", "paper_xavier_reviews"):
        if t == "paper_settlements_by_key":
            out[t] = await conn.fetchval(
                "SELECT count(*) FROM paper_settlements WHERE position_key LIKE $1",
                "paperpos:" + account_id + ":%")
        else:
            out[t] = await conn.fetchval(
                "SELECT count(*) FROM %s WHERE account_id=$1" % t, account_id)
    out["funded"] = await H.funded_table_counts(conn)
    from sportsassets.open_position_canon import CANONICAL_OPEN_POSITIONS_SQL
    out["open"] = sorted(
        (r["us_market_slug"], r["holding_side"], float(r["open_qty"]))
        for r in await conn.fetch(
            "SELECT * FROM (" + CANONICAL_OPEN_POSITIONS_SQL + ") o "
            "WHERE account_id=$1", account_id))
    return out


# --------------------------------------------------------- the ON / OFF proof

@pg
async def test_enrichment_changes_only_game_score_feed_and_the_digest():
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a, g, s, pid, prot = await _protected_position(conn, "lgs1")
        event = "lgs-test-%s" % uuid.uuid4().hex[:12]
        await _premap(conn, s, event, start=T - 3600)
        f = _display_fixture(event, T - 3600)
        await PostgresStore(pool).write(f, _bound_score(f, received=T + 6), now=T + 8)

        off = await _read(pool, a["account_id"], T + 8, display="off")
        on = await _read(pool, a["account_id"], T + 8, display="on")

        # NOT VACUOUS: a marked, protected, complete position, and the score
        # really is attached
        [p_off], [p_on] = off["positions"], on["positions"]
        assert p_off["position_id"] == pid and p_off["event_id"] == event
        assert p_off["quote"]["current"] and p_off["quote"]["bid"] == 0.41
        assert p_off["packet"]["complete"], p_off["packet"]
        assert [o["order_id"] for o in p_off["orders"]] == [prot]
        assert p_off["orders"][0]["limit_price"] is not None
        assert p_off["state"] == "ACTIVE" and p_off["qty"] == 100.0
        assert p_on["game"]["status"] == "CURRENT", p_on["game"]
        assert p_on["game"]["schema"] == "bettor.live_game.v1"
        assert p_on["game"]["execution_authority"] is False
        assert p_on["game"]["home_score"] == 4 and p_on["game"]["away_score"] == 3
        assert p_off["game"]["status"] == "UNAVAILABLE"
        assert on["score_feed"]["status"] == "OK"
        assert on["score_feed"]["counts"]["current"] == 1
        assert on["snapshot_id"] != off["snapshot_id"]

        # THE PROOF: identical except game / score_feed / snapshot digest
        assert _trading_view(on) == _trading_view(off)

        # and the comparison sees every field it guards
        for path in GUARDED:
            changed = copy.deepcopy(on)
            _set(changed, path, "CHANGED_BY_THE_NEGATIVE_CONTROL")
            assert _trading_view(changed) != _trading_view(off), path
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_off_is_byte_identical_to_no_enrichment_at_all(monkeypatch):
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a, *_ = await _protected_position(conn, "lgs2")
        off = await _read(pool, a["account_id"], T + 8, display="off")

        async def untouched(conn, snapshot, *, now):
            return snapshot
        monkeypatch.setattr(TR, "enrich_live_game_snapshot", untouched)
        none = await _read(pool, a["account_id"], T + 8, display="on")
        assert off == none
        assert off["score_feed"] == none["score_feed"]
        assert "schema" not in off["score_feed"]   # the legacy feed, as before
    finally:
        await pool.close()
        await conn.close()


# ----------------------------------------------- a FINAL score settles nothing

class _Board:
    """A provider transport serving one synthetic ESPN scoreboard. No network."""

    def __init__(self, body, clock):
        self.body, self.clock, self.calls = body, clock, []

    async def get(self, url, *, params=None):
        self.calls.append((url, dict(params or {})))
        now = self.clock()
        return Reply(self.body, 200, now, now, "TEST_HASH", {})

    async def close(self):
        pass


@pg
async def test_a_final_score_settles_nothing(monkeypatch):
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    client = None
    try:
        a, g, s, pid, prot = await _protected_position(conn, "lgs3")
        event = "lgs-final-%s" % uuid.uuid4().hex[:12]
        start = T - 4 * 3600
        await _premap(conn, s, event, start=start, team_league="mlb")
        await _fixture_metadata(conn, event, competition="MLB")
        before = await _account_rows(conn, a["account_id"])
        off = await _read(pool, a["account_id"], T + 8, display="off")

        # the REAL collector, through the REAL canonical adapter
        store = PostgresStore(pool)
        fixtures, census = await store.fixtures(account_id=a["account_id"])
        assert [f.event_id for f in fixtures] == [event], census
        clock = lambda: T + 8                                   # noqa: E731
        board = _Board(_espn_board(start=start, state="STATUS_FINAL",
                                   home_score="6", away_score="2"), clock)
        client = ScoreClient(board, clock=clock)
        report = await Collector(client, store, clock=clock).cycle(
            fixtures, census=census)
        assert report["successful"] == 1, report
        assert report["source_counts"] == {"ESPN": 1, "THE_ODDS_API": 0}
        assert report["execution_authority"] is False
        assert [u for u, _ in board.calls] == [
            "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"]

        on = await _read(pool, a["account_id"], T + 8, display="on")
        [p_on], [p_off] = on["positions"], off["positions"]
        assert p_on["game"]["status"] == "CURRENT"
        assert p_on["game"]["game_status"] == "FINAL"
        assert (p_on["game"]["home_score"], p_on["game"]["away_score"]) == (6, 2)
        # the position is still open, ACTIVE, unsettled and unchanged
        assert p_on["state"] == p_off["state"] == "ACTIVE"
        assert p_on["qty"] == p_off["qty"] == 100.0
        assert _trading_view(on) == _trading_view(off)
        # and NOTHING in the trading ledger moved: no settlement, order, fill,
        # ledger entry, review or funded row; the canonical position is open
        after = await _account_rows(conn, a["account_id"])
        assert after == before
        assert after["paper_settlements_by_key"] == 0
        assert after["open"] == [(s, "LONG", 100.0)]
    finally:
        if client:
            await client.close()
        await pool.close()
        await conn.close()


# ------------------------- a fresh score does not complete a stale packet

@pg
async def test_a_fresh_score_does_not_complete_a_stale_packet_or_mark():
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a, g, s, pid, prot = await _protected_position(conn, "lgs4")
        event = "lgs-stale-%s" % uuid.uuid4().hex[:12]
        await _premap(conn, s, event, start=T - 3600)
        f = _display_fixture(event, T - 3600)
        store = PostgresStore(pool)

        # 31 s after the review the probability has expired; the score is 2 s old
        late = T + 7 + 31
        await store.write(f, _bound_score(f, received=late - 2), now=late)
        off = await _read(pool, a["account_id"], late, display="off")
        on = await _read(pool, a["account_id"], late, display="on")
        [p] = on["positions"]
        assert p["game"]["status"] == "CURRENT"
        assert p["packet"]["complete"] is False
        assert "NO_FRESH_PROBABILITY" in p["packet"]["missing"]
        assert p["packet"]["current_recommendation"] is None
        assert on["counts"]["complete_packets"] == 0
        assert _trading_view(on) == _trading_view(off)

        # 400 s: the book read is past its 300 s limit; a 2 s old score does
        # not make the mark current or the unrealised P&L known
        later = T + 400
        await store.write(f, _bound_score(f, received=later - 2), now=later)
        off = await _read(pool, a["account_id"], later, display="off")
        on = await _read(pool, a["account_id"], later, display="on")
        [p] = on["positions"]
        assert p["game"]["status"] == "CURRENT"
        assert p["quote"]["current"] is False
        assert p["quote"]["why"] == "BOOK_READ_OLDER_THAN_300S"
        assert p["unrealized_usd"] is None
        assert "NO_CURRENT_EXECUTABLE_BOOK" in p["packet"]["missing"]
        assert on["counts"]["current_marks"] == 0
        assert _trading_view(on) == _trading_view(off)
    finally:
        await pool.close()
        await conn.close()


# -------------------------- the production census is the collector's verdict

def _census_statements(account_id):
    text = CENSUS_SQL.read_text()
    body = "\n".join(re.sub(r"--.*$", "", ln) for ln in text.splitlines()
                     if not ln.lstrip().startswith("\\echo"))
    stmts = [x.strip() for x in re.split(r";\s*(?:\n|$)", body) if x.strip()]
    out = []
    for st in stmts:
        if "paper_fills" in st:
            assert st.count("'paper_acct_main'") == 1, st[:200]
            st = st.replace("'paper_acct_main'", "'%s'" % account_id)
        out.append(st)
    return out


@pg
async def test_the_census_reaches_the_collectors_own_verdict():
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a = await H.new_account(conn, "lgscen")
        acct = a["account_id"]
        tag = uuid.uuid4().hex[:10]
        start = T - 3600

        def ev(name):
            return "lgs-cen-%s-%s" % (tag, name)
        shapes = {}
        n = 0

        async def held(name):
            nonlocal n
            n += 1
            slug = "%s:%s" % (acct, name)
            await _fill(conn, a, slug=slug, group="paper_g_%s_%s" % (tag, name),
                        key="e%d" % n, at=T + n)
            return slug
        # no catalogue row at all
        shapes[await held("none")] = "CANONICAL_VENUE_EVENT_MISSING"
        # PRODUCTION SHAPE 1: an NFL market, premap row, no fixture row
        s = await held("nfl")
        await _premap(conn, s, ev("nfl"), start=start, team_league="nfl")
        shapes[s] = "CANONICAL_SCORE_FIXTURE_FIELDS_MISSING"
        # PRODUCTION SHAPE 2: organiser-sourced UEFA fixture metadata
        s = await held("unl")
        await _premap(conn, s, ev("unl"), start=start)
        await _fixture_metadata(conn, ev("unl"), competition="UNL",
                                home="France", away="Italy", orientation="SAME")
        shapes[s] = "CANONICAL_SCORE_FIXTURE_FIELDS_MISSING"
        # established, twice on one event (one fixture)
        for name in ("mlb", "mlb2"):
            s = await held(name)
            await _premap(conn, s, ev("mlb"), start=start)
            shapes[s] = "ESTABLISHED"
        await _fixture_metadata(conn, ev("mlb"), competition="MLB")
        # orientation the adapter does not accept
        s = await held("swap")
        await _premap(conn, s, ev("swap"), start=start)
        await _fixture_metadata(conn, ev("swap"), competition="NFL",
                                home="Dallas Cowboys", away="Tampa Bay Buccaneers",
                                orientation="SWAPPED")
        shapes[s] = "CANONICAL_HOME_AWAY_UNPROVEN"
        # one team named twice
        s = await held("same")
        await _premap(conn, s, ev("same"), start=start)
        await _fixture_metadata(conn, ev("same"), competition="NBA",
                                home="Boston Celtics", away="Boston Celtics")
        shapes[s] = "CANONICAL_PARTICIPANTS_INVALID"
        # a game number outside 1..9
        s = await held("gn")
        await _premap(conn, s, ev("gn"), start=start)
        await _fixture_metadata(conn, ev("gn"), competition="MLB",
                                raw_meta={"game_number": 12})
        shapes[s] = "GAME_NUMBER_INVALID"
        # two held markets of one event that disagree on the start
        for name, st in (("c1", start), ("c2", start + 1800)):
            s = await held(name)
            await _premap(conn, s, ev("conf"), start=st)
            shapes[s] = "CONFLICTING_CANONICAL_FIXTURE_ROWS"
        await _fixture_metadata(conn, ev("conf"), competition="NHL",
                                home="Boston Bruins", away="New York Rangers")

        # the collector's adapter
        fixtures, census = await PostgresStore(pool).fixtures(account_id=acct)
        # the census file, every statement, on the same database
        stmts = _census_statements(acct)
        assert len(stmts) == 5
        results = [await conn.fetch(st) for st in stmts]
        population, detail, by_league = results[0][0], results[1], results[2]

        got = {r["us_market_slug"]: r["collector_verdict"] for r in detail}
        assert got == shapes
        assert population["held_markets"] == len(shapes)
        assert census["held_market_rows"] == len(shapes)
        # established: the same events, the same identity
        assert sorted(f.event_id for f in fixtures) == [ev("mlb")]
        assert sorted({r["event_id"] for r in detail
                       if r["collector_verdict"] == "ESTABLISHED"}) == [ev("mlb")]
        [mlb] = [r for r in detail if r["us_market_slug"].endswith(":mlb")]
        [fx] = fixtures
        assert (mlb["collector_league"], mlb["collector_home"],
                mlb["collector_away"]) == (fx.league, fx.home, fx.away)
        assert mlb["collector_start"].timestamp() == fx.start_at
        assert mlb["primary_source_request"].startswith(
            "ESPN baseball/mlb scoreboard dates=")
        # refusals: the same names; the conflict is one detection in the
        # collector (the second row) and both markets of the event here
        sql_reasons = collections.Counter(
            v for v in got.values()
            if v not in ("ESTABLISHED", "CONFLICTING_CANONICAL_FIXTURE_ROWS"))
        py_reasons = dict(census["missing_by_reason"])
        assert py_reasons.pop("CONFLICTING_CANONICAL_FIXTURE_ROWS") == 1
        assert py_reasons == dict(sql_reasons)
        # the league x verdict roll-up accounts for every market once
        assert sum(r["markets"] for r in by_league) == len(shapes)
        assert {r["league"] for r in by_league
                if r["collector_verdict"] == "ESTABLISHED"} == {"MLB"}
        # the production-shape diagnostics name what is missing
        [nfl] = [r for r in detail if r["us_market_slug"].endswith(":nfl")]
        assert nfl["premap_row"] and not nfl["fixture_metadata_row"]
        assert nfl["diag_venue_team_league"] == "nfl"
        assert nfl["collector_league"] is None
    finally:
        await pool.close()
        await conn.close()


@pg
async def test_the_freshness_census_runs_and_matches_the_api_status():
    """research/lgs_display_score_freshness.sql renders core.display; on a
    current, a stale and an absent score it agrees with the API's status."""
    conn = await H.connect()
    pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)
    try:
        a = await H.new_account(conn, "lgsfr")
        acct, tag = a["account_id"], uuid.uuid4().hex[:10]
        now = datetime.now(timezone.utc).timestamp()
        store = PostgresStore(pool)
        want = {}
        for n, (name, age) in enumerate((("cur", 2), ("old", 60), ("none", None))):
            slug = "%s:%s" % (acct, name)
            await _fill(conn, a, slug=slug, group="paper_g_%s_%s" % (tag, name),
                        key="e%d" % n, at=T + n)
            event = "lgs-fr-%s-%s" % (tag, name)
            await _premap(conn, slug, event, start=now - 3600)
            if age is not None:
                f = _display_fixture(event, now - 3600)
                await store.write(f, _bound_score(f, received=now - age),
                                  now=now - age + 1)
            want[event] = {"cur": "CURRENT", "old": "STALE",
                           "none": "UNAVAILABLE"}[name]
        text = FRESHNESS_SQL.read_text()
        body = "\n".join(re.sub(r"--.*$", "", ln) for ln in text.splitlines()
                         if not ln.lstrip().startswith("\\echo"))
        stmts = [x.strip() for x in re.split(r";\s*(?:\n|$)", body) if x.strip()]
        assert len(stmts) == 4
        out = []
        for st in stmts:
            if "paper_fills" in st:
                assert st.count("'paper_acct_main'") == 1
                st = st.replace("'paper_acct_main'", "'%s'" % acct)
            out.append(await conn.fetch(st))
        got = {r["event_id"]: r["status"] for r in out[1]}
        assert got == want
        [old] = [r for r in out[1] if r["status"] == "STALE"]
        assert old["why"] == "SCORE_EVIDENCE_STALE"
        assert old["freshness_basis"] == "RETRIEVAL_ONLY"
        assert old["provider_current"] is None
        # the API agrees, read at the same instant
        snap = await _read(pool, acct, datetime.now(timezone.utc).timestamp(),
                           display="on")
        api = {p["event_id"]: p["game"]["status"] for p in snap["positions"]}
        assert api == want
    finally:
        await pool.close()
        await conn.close()


# ----------------------------------------- authority, flags and process scope

LGS = BACKEND / "sportsassets" / "live_game_state"
WORKER = BACKEND / "sportsassets" / "workers" / "trader_live_scores.py"
#: no execution, ledger-writing, settlement or venue-session module
FORBIDDEN = ("pmus", "execmirror", "live_executor", "kalshi_orders",
             "bettor_funded_execution", "pmx", "bettor_paper_ledger",
             "bettor_paper_simulator", "bettor_xavier_standing_orders",
             "paper_xavier", "bettor_live")


def _imports(path):
    tree = ast.parse(path.read_text())
    return {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}


def test_the_display_modules_hold_no_order_settlement_or_venue_authority():
    files = sorted(LGS.glob("*.py")) + [WORKER]
    assert len(files) == 7, files
    for path in files:
        for m in _imports(path):
            last = m.split(".")[-1]
            assert last not in FORBIDDEN, (path.name, m)
            assert "settle" not in m, (path.name, m)
    # every write the package makes is to its own display tables (its SQL is
    # upper-case; prose like "update latency" is not a statement)
    written = set()
    for path in files:
        src = path.read_text()
        for verb, table in re.findall(
                r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+)", src):
            assert table.startswith("trader_display_score_"), (path.name, verb, table)
            written.add(table)
    assert written == {"trader_display_score_bindings",
                       "trader_display_score_observations",
                       "trader_display_score_latest",
                       "trader_display_score_health"}, written


def test_the_api_read_path_never_calls_a_provider():
    """The Trader request performs one bounded DB read; ESPN / Odds calls
    live only in the dedicated collector."""
    mods = _imports(LGS / "integration.py")
    assert not any("providers" in m or "collector" in m or m == "httpx" for m in mods), mods
    rm = _imports(BACKEND / "sportsassets" / "api" / "trader_readmodel.py")
    assert {m for m in rm if "live_game_state" in m} == {"live_game_state.integration"}


def test_every_flag_defaults_off_and_off_makes_no_provider_call(monkeypatch):
    for k in ("BETTOR_DISPLAY_SCORES", "BETTOR_ODDS_SCORE_FALLBACK",
              "BETTOR_SCORE_SUMMARIES", "ODDS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert I.enabled() is False
    from sportsassets.workers import trader_live_scores as W

    def no_pool(*a, **kw):
        raise AssertionError("a disabled collector opened a database pool")
    monkeypatch.setattr(asyncpg, "create_pool", no_pool)
    assert asyncio.run(W.main()) is None
    # the metered fallback needs the explicit flag AND a key
    assert ScoreClient(object(), odds_key="k").odds_enabled is False
    assert ScoreClient(object(), odds_enabled=True).odds_enabled is False


def test_the_collector_is_a_dedicated_service_not_a_shared_worker_loop():
    allpy = (BACKEND / "sportsassets" / "workers" / "all.py").read_text()
    assert "trader_live_scores" not in allpy
    render = (BACKEND.parent / "render.yaml").read_text()
    assert "trader_live_scores" not in render
    assert "BETTOR_DISPLAY_SCORES" not in render
