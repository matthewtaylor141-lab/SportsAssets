"""THE RUNTIME CALLER. Does the loop actually supply the entry inputs?

`bettor_external_shadow.evaluate` accepting the new inputs in a test
proves nothing about production. What has to be true is that the LOOP --
the thing armed at API startup -- builds them from real reads and hands
them to the real gate. These tests drive `cycle()` itself, with the
provider and the venue stubbed at their transport boundaries and
everything in between left alone.
"""

from __future__ import annotations

import os
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_venue_mapping as vmap
from sportsassets import bettor_venue_settlement as vset
from sportsassets.workers import ext_pinnacle_loop as loop


# ── the mapping, which is where a wrong answer is most dangerous ─────

VENUE_MARKETS = [
    {"condition_id": "c-lfc-mci", "title": "Will Liverpool beat Manchester City?",
     "event_title": "Liverpool vs. Manchester City", "slug": "lfc-mci",
     "closed": False, "resolved": False},
    {"condition_id": "c-mufc-tot", "title": "Will Manchester United beat Tottenham?",
     "event_title": "Manchester United vs. Tottenham", "slug": "mufc-tot",
     "closed": False, "resolved": False},
    {"condition_id": "c-closed", "title": "Will Fulham beat Hull City?",
     "event_title": "Fulham vs. Hull City", "slug": "ful-hul",
     "closed": True, "resolved": False},
    {"condition_id": "c-seg", "title": "Will Arsenal lead Chelsea at half time?",
     "event_title": "Arsenal vs. Chelsea 1st Half", "slug": "ars-che-1h",
     "closed": False, "resolved": False},
]


def test_the_two_manchester_clubs_do_not_map_to_each_other():
    """The reason this module exists. `edge/venues/mapper.norm_team`
    strips 'city' and 'united' as noise, which makes these one name."""
    a = vmap.map_event(home="Liverpool", away="Manchester City",
                       markets=VENUE_MARKETS)
    b = vmap.map_event(home="Manchester United", away="Tottenham",
                       markets=VENUE_MARKETS)
    assert a["mapped"] and a["condition_id"] == "c-lfc-mci"
    assert b["mapped"] and b["condition_id"] == "c-mufc-tot"
    assert a["condition_id"] != b["condition_id"]
    # And the normalisation really does keep them apart.
    assert vmap.norm_name("Manchester City") != \
        vmap.norm_name("Manchester United")


def test_a_closed_market_is_refused_by_its_own_name():
    out = vmap.map_event(home="Fulham", away="Hull City",
                         markets=VENUE_MARKETS)
    assert not out["mapped"]
    assert vmap.R_CLOSED in out["refusals"]
    assert out["blocked_closed"] == 1


def test_a_segment_contract_is_never_priced_off_the_full_game():
    out = vmap.map_event(home="Arsenal", away="Chelsea",
                         markets=VENUE_MARKETS)
    assert not out["mapped"]
    assert vmap.R_SEGMENT in out["refusals"]


def test_an_unknown_fixture_is_refused_rather_than_near_matched():
    out = vmap.map_event(home="Real Madrid", away="Barcelona",
                         markets=VENUE_MARKETS)
    assert not out["mapped"]
    assert vmap.R_NO_CONTRACT in out["refusals"]


def test_two_matching_markets_are_ambiguous_not_first_wins():
    dupe = VENUE_MARKETS + [dict(VENUE_MARKETS[0], condition_id="c-dupe")]
    out = vmap.map_event(home="Liverpool", away="Manchester City",
                         markets=dupe)
    assert not out["mapped"]
    assert vmap.R_AMBIGUOUS in out["refusals"]
    assert out["candidates"] == 2


# ── the settlement rule is not assumed ───────────────────────────────

def test_the_venue_settlement_rule_is_not_established_and_says_so():
    got = vset.agrees(sport_family="soccer")
    assert got["agrees"] is None, "an unknown must not read as a match"
    assert got["refusal"] == vset.R_NOT_ESTABLISHED
    assert got["book_rule"] == "REGULATION_90_PLUS_STOPPAGE_NO_EXTRA_TIME"
    assert got["venue_rule"] is None
    assert "how_to_establish" in got


def test_the_two_sports_do_not_share_one_settlement_rule():
    assert vset.BOOK_SETTLEMENT["soccer"] != vset.BOOK_SETTLEMENT["baseball"]


def test_a_caller_refusal_vetoes_admission():
    """Otherwise the refusal would be decorative: recorded on the row and
    ignored by the decision."""
    rec = ext.evaluate(
        contract={"venue": "V", "condition_id": "c", "selection": "A",
                  "sport_family": "soccer", "market": "h2h",
                  "period": "FULL_GAME", "line": None,
                  "settlement_rule": "R", "event_key": "e"},
        quote={"book": "pinnacle",
               "outcomes": {"A": 1.5, "B": 3.0, "C": 4.0},
               "observed_at": 1000.0, "received_at": 1000.0,
               "event_key": "e",
               "period": "FULL_GAME", "line": None,
               "settlement_rule": "R"},
        market_state={"ask": 0.05, "depth": 500.0, "readable": True},
        execution_estimate={"p_fill": 0.9, "basis": "TEST", "crossing": True},
        size=1.0, risk={"permitted": True}, fee_fn=lambda qty, price: 0.0,
        now=1001.0, outcome_books=4, armed=True,
        extra_refusals=["VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED"])
    assert rec["admissible"] is False
    assert rec["decision"] == "NO_TRADE"
    assert "VENUE_SETTLEMENT_RULE_NOT_ESTABLISHED" in rec["refusals"]
    # The record is still COMPLETE: a refusal must not blank the evidence.
    assert rec["probability"] is not None
    assert rec["executable_price"] == 0.05
    assert rec["estimated_edge_per_contract"] is not None


def test_without_caller_refusals_the_same_inputs_clear():
    """The control. Without this, the test above would pass against a
    function that refuses everything."""
    rec = ext.evaluate(
        contract={"venue": "V", "condition_id": "c", "selection": "A",
                  "sport_family": "soccer", "market": "h2h",
                  "period": "FULL_GAME", "line": None,
                  "settlement_rule": "R", "event_key": "e"},
        quote={"book": "pinnacle",
               "outcomes": {"A": 1.5, "B": 3.0, "C": 4.0},
               "observed_at": 1000.0, "received_at": 1000.0,
               "event_key": "e",
               "period": "FULL_GAME", "line": None,
               "settlement_rule": "R"},
        market_state={"ask": 0.05, "depth": 500.0, "readable": True},
        execution_estimate={"p_fill": 0.9, "basis": "TEST", "crossing": True},
        size=1.0, risk={"permitted": True}, fee_fn=lambda qty, price: 0.0,
        now=1001.0, outcome_books=4, armed=True)
    assert rec["admissible"] is True
    assert rec["decision"] == "BUY"
    assert rec["order_submitted"] is False


# ── no funded path exists ────────────────────────────────────────────

def test_the_loop_imports_no_order_submission_path():
    """A database CHECK on order_submitted does not stop a network
    request. What stops one is that no submit function is reachable."""
    import inspect

    src = inspect.getsource(loop)
    for bad in ("post_order", "place_order", "submit_order", "create_order",
                "live_executor", "OrderArgs", "sign_order", "api_creds"):
        assert bad not in src, "%s must not appear in the loop" % bad
    # The venue module it does import is used only through its READER.
    assert "book_read" in src
    assert "pmus.book_read" in src


def test_the_loop_is_bounded():
    assert loop.MAX_PER_CYCLE <= 40
    assert loop.CYCLE_S >= 300, "the cadence is the provider budget"
    assert loop.VENUE_TIMEOUT_S <= 30


def test_the_three_venue_read_failures_have_three_names():
    """The 02:08:18Z production cycle mapped two events and then said
    `NO_CONTEMPORANEOUS_VENUE_QUOTE 2`, which does not say which thing
    was missing. Depth and staleness already had their own names, so the
    remaining three branches get theirs: a market row with no slug, a
    read that raised, and a venue error response.
    """
    import asyncio as _a

    codes = {loop.R_NO_SLUG, loop.R_VENUE_READ_FAILED,
             loop.R_VENUE_READ_ERROR, loop.R_NO_DEPTH,
             loop.R_VENUE_QUOTE_STALE, loop.R_NO_VENUE_QUOTE}
    assert len(codes) == 6, "each refusal must be distinguishable"

    class _Conn:
        def __init__(self, slug):
            self._slug = slug

        async def fetchval(self, *_a, **_k):
            return self._slug

    # 1 · no slug on the market row
    out = _a.run(loop.venue_quote(_Conn(None), condition_id="c",
                                  outcome_index=0, now=0.0))
    assert out["refusal"] == loop.R_NO_SLUG

    # 2 · the read raises
    def _boom(_slug):
        raise RuntimeError("no route to venue")

    orig = loop._read_book_blocking
    loop._read_book_blocking = _boom
    try:
        out = _a.run(loop.venue_quote(_Conn("slug-1"), condition_id="c",
                                      outcome_index=0, now=0.0))
    finally:
        loop._read_book_blocking = orig
    assert out["refusal"] == loop.R_VENUE_READ_FAILED
    assert out["exception"] == "RuntimeError"

    # 3 · the venue answers with an error
    loop._read_book_blocking = lambda _slug: {"error": "429 rate limited"}
    try:
        out = _a.run(loop.venue_quote(_Conn("slug-1"), condition_id="c",
                                      outcome_index=0, now=0.0))
    finally:
        loop._read_book_blocking = orig
    assert out["refusal"] == loop.R_VENUE_READ_ERROR
    assert "429" in out["venue_error"]


def test_basketball_and_hockey_are_not_requested_at_all():
    """Measured: NBA 0/41 and NHL 0/33 carry no Pinnacle quote on this
    plan. Asking anyway spends credits to be refused."""
    keys = [k for k, _ in loop.SPORTS]
    assert not any("basketball" in k or "icehockey" in k for k in keys)


# ── one whole cycle against the real schema ──────────────────────────

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

def _fresh_iso(offset_s: float = -2.0) -> str:
    """A quote stamped a couple of seconds ago, in the provider's format.

    Hard-coding an ISO timestamp made this fixture age past the 30 s
    freshness rule as soon as the clock moved on, so the source refused it
    with QUOTE_STALE -- correctly. The fixture has to be fresh for the
    same reason a real quote does.
    """
    from datetime import datetime, timedelta, timezone

    at = datetime.now(timezone.utc) + timedelta(seconds=offset_s)
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(stamp=None):
    stamp = stamp or _fresh_iso()
    return {
        "id": "evt-1", "home_team": "Liverpool",
        "away_team": "Manchester City",
        "commence_time": "2026-09-24T18:00:00Z",
        "bookmakers": [
            {"key": "pinnacle", "last_update": stamp,
             "markets": [{"key": "h2h", "last_update": stamp,
                          "outcomes": [
                              {"name": "Liverpool", "price": 2.0},
                              {"name": "Manchester City", "price": 3.6},
                              {"name": "Draw", "price": 3.5}]}]},
            {"key": "smarkets", "last_update": stamp,
             "markets": [{"key": "h2h",
                          "outcomes": [
                              {"name": "Liverpool", "price": 2.02},
                              {"name": "Manchester City", "price": 3.5},
                              {"name": "Draw", "price": 3.4}]}]},
        ],
    }


@pg
@pytest.mark.asyncio
async def test_one_cycle_writes_a_complete_refusal_record(monkeypatch):
    """The whole point of item 2: fresh odds -> mapping -> venue quote ->
    fees -> gate -> a persisted row. The row here is a REFUSAL, because
    the venue settlement rule is not established -- and it must still
    carry every field management needs to see why."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            open("migrations/103_external_valuations.sql").read())
        # 105 TOO. Without it there is no uniqueness, so the duplicate
        # test below would pass against an unmigrated database and prove
        # nothing.
        await conn.execute(open(
            "migrations/105_external_valuations_one_per_observation.sql"
        ).read())
        await conn.execute("DELETE FROM external_valuations "
                           "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_state "
            "(key TEXT PRIMARY KEY, value TEXT)")
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value = 'true'",
            loop.CONTROL_KEY)
        await conn.execute(
            "INSERT INTO markets (condition_id, title, event_title, slug, "
            "sport, closed, resolved) VALUES "
            # 'Soccer', the label `sports.classify` actually writes --
            # not the lowercase family name. Seeding the family name made
            # the candidate query return nothing, which is precisely the
            # production defect this vocabulary split caused.
            "('c-lfc-mci','Will Liverpool beat Manchester City?',"
            "'Liverpool vs. Manchester City','lfc-mci','Soccer',false,false)"
            # DO UPDATE, not DO NOTHING: a row left over from an earlier
            # run would keep its old sport label and the fixture would
            # silently test the wrong thing.
            " ON CONFLICT (condition_id) DO UPDATE SET "
            "sport = EXCLUDED.sport, closed = FALSE, resolved = FALSE")

        monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

        async def fake_fetch(sport_key, *, api_key, timeout=20.0):
            assert api_key, "the loop must pass the credential through"
            if sport_key != "soccer_epl":
                return {"ok": True, "events": [],
                        "received_at": time.time(),
                        "credits_used": "1", "credits_remaining": "9"}
            return {"ok": True, "events": [_event()],
                    "received_at": time.time(),
                    "credits_used": "1", "credits_remaining": "9"}

        monkeypatch.setattr(loop, "fetch_odds", fake_fetch)

        async def fake_quote(conn_, *, condition_id, outcome_index, now):
            return {"ok": True, "ask": 0.52, "depth": 800.0, "age_s": 3.0,
                    "age_basis": "VENUE_TRANSACT_TIME", "bid": None,
                    "read_at": now, "slug": "lfc-mci",
                    "outcome_index": outcome_index}

        monkeypatch.setattr(loop, "venue_quote", fake_quote)

        out = await loop.cycle(conn)
        assert out["ran"] is True, out
        assert out["state"] == "LIVE"
        assert out["evaluated"] == 1, out
        assert out["written"] == 1, out
        assert out["order_submitted"] is False
        # The SPECIFIC unmet rules, not one blanket unknown. Soccer h2h
        # leaves three unestablished and the census must name each.
        for code in (vset.R_DRAW_ASYMMETRIC, vset.R_OVERTIME_UNKNOWN,
                     vset.R_VOID_UNKNOWN):
            assert code in out["refusals"], (code, out["refusals"])

        row = await conn.fetchrow(
            "SELECT probability, executable_price, cost_per_contract, "
            "estimated_edge_per_contract, decision, admissible, refusals, "
            "condition_id, event_key, observed_at, received_at, "
            "outcome_books, overround, order_submitted "
            "FROM external_valuations WHERE experiment_id = $1",
            ext.EXPERIMENT_ID)
        assert row is not None, "the refusal must be persisted, not dropped"
        # EVERY FIELD THE DIRECTIVE LISTS, on a refused row.
        assert row["probability"] is not None
        assert float(row["executable_price"]) == pytest.approx(0.52)
        assert row["cost_per_contract"] is not None
        assert row["estimated_edge_per_contract"] is not None
        assert row["decision"] == "NO_TRADE"
        assert row["admissible"] is False
        assert vset.R_DRAW_ASYMMETRIC in list(row["refusals"]), \
            list(row["refusals"])
        assert vset.R_OVERTIME_UNKNOWN in list(row["refusals"])
        assert row["condition_id"] == "c-lfc-mci"
        assert row["event_key"] == "evt-1"
        assert row["observed_at"] is not None
        assert row["received_at"] is not None
        assert row["order_submitted"] is False
        assert int(row["outcome_books"]) == 2
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_second_cycle_does_not_double_count(monkeypatch):
    """Item 5 asks for a subsequent cycle processing new data without
    duplicate accounting. Re-evaluating the SAME quote must not create a
    second row for the same observation."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(open(
            "migrations/105_external_valuations_one_per_observation.sql"
        ).read())
        before = await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE experiment_id=$1",
            ext.EXPERIMENT_ID)
        monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

        async def fake_fetch(sport_key, *, api_key, timeout=20.0):
            if sport_key != "soccer_epl":
                return {"ok": True, "events": [],
                        "received_at": time.time()}
            return {"ok": True, "events": [_event()],
                        "received_at": time.time()}

        async def fake_quote(conn_, *, condition_id, outcome_index, now):
            return {"ok": True, "ask": 0.52, "depth": 800.0, "age_s": 3.0,
                    "age_basis": "VENUE_TRANSACT_TIME", "bid": None,
                    "read_at": now, "slug": "lfc-mci",
                    "outcome_index": outcome_index}

        monkeypatch.setattr(loop, "fetch_odds", fake_fetch)
        monkeypatch.setattr(loop, "venue_quote", fake_quote)
        await loop.cycle(conn)
        after = await conn.fetchval(
            "SELECT count(*) FROM external_valuations WHERE experiment_id=$1",
            ext.EXPERIMENT_ID)
        assert after == before, (
            "the same observation was counted twice: %s -> %s"
            % (before, after))
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_refusals_before_scoring_are_still_counted(monkeypatch):
    """THE GAP THE FIRST LIVE RUN EXPOSED.

    The census showed `evaluated 0` with an empty refusal list while every
    candidate had in fact been refused by name -- because six of the
    loop's eight refusal counters fire before a candidate is ever scored,
    so none of them produces a row. A cycle that refuses everything must
    not be indistinguishable from a cycle that did nothing.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS ingestion_state "
            "(key TEXT PRIMARY KEY, value TEXT)")
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
            "ON CONFLICT (key) DO UPDATE SET value = 'true'",
            loop.CONTROL_KEY)
        await conn.execute("DELETE FROM ingestion_state WHERE key = $1",
                           loop.HEARTBEAT_KEY)
        monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

        # An event whose teams match NO venue market: refused at mapping,
        # which is before scoring.
        unmapped = _event()
        unmapped["home_team"] = "Real Madrid"
        unmapped["away_team"] = "Barcelona"
        unmapped["bookmakers"][0]["markets"][0]["outcomes"] = [
            {"name": "Real Madrid", "price": 2.0},
            {"name": "Barcelona", "price": 3.6},
            {"name": "Draw", "price": 3.5}]

        async def fake_fetch(sport_key, *, api_key, timeout=20.0):
            if sport_key != "soccer_epl":
                return {"ok": True, "events": [], "received_at": time.time()}
            return {"ok": True, "events": [unmapped],
                    "received_at": time.time()}

        monkeypatch.setattr(loop, "fetch_odds", fake_fetch)
        out = await loop.cycle(conn)
        assert out["evaluated"] == 0, out
        assert out["written"] == 0, out
        # The cycle itself named the reason...
        assert vmap.R_NO_CONTRACT in out["refusals"], out["refusals"]

        # ...and the heartbeat PERSISTED it, which is the part that was
        # missing. Without this the command centre cannot tell management
        # why nothing was valued.
        import json

        raw = await conn.fetchval(
            "SELECT value::text FROM ingestion_state WHERE key = $1",
            loop.HEARTBEAT_KEY)
        assert raw, "the cycle wrote no heartbeat"
        hb = json.loads(raw)
        assert hb["evaluated"] == 0
        assert hb["refusals"].get(vmap.R_NO_CONTRACT) == 1, hb["refusals"]
        assert hb["state"] == "LIVE"
    finally:
        await conn.close()


def test_the_venue_error_text_is_kept_bounded_and_deduplicated():
    """Run 22 named the refusal `VENUE_BOOK_READ_RETURNED_ERROR 2`. The
    counter is right and still not an answer: an entitlement, a closed
    market and a rate limit need different actions from different people,
    and only the response distinguishes them. So the loop keeps a
    structured diagnostic -- contract, slug, endpoint, stage, code, HTTP
    status when the client gives one -- capped and deduplicated."""
    import inspect

    src = inspect.getsource(loop.cycle)
    assert "venue_errors" in src
    assert "MAX_VENUE_ERRORS" in src
    assert "seen_venue_errors" in src, "duplicates must not fill the cap"
    assert loop.MAX_VENUE_ERRORS <= 10, "a heartbeat is not a log"
    hb = inspect.getsource(loop._heartbeat)
    assert '"venue_errors"' in hb, "it must reach the tile, not just a local"


def test_the_diagnostic_names_the_request_not_just_the_exception():
    class _Httpish(Exception):
        status_code = 403

    d = loop._venue_diagnostic("nba-lal-bos-2026", _Httpish("Forbidden"),
                               stage="BOOK_READ")
    assert d["slug"] == "nba-lal-bos-2026"
    assert d["endpoint"] == "markets.book"
    assert d["stage"] == "BOOK_READ"
    assert d["status"] == 403, "an HTTP status is the actionable part"
    assert d["exception"] == "_Httpish"

    named = loop._venue_diagnostic("s", None, stage="BOOK_READ",
                                   code="NO_MARKET_DATA_IN_PAYLOAD",
                                   feed="book")
    assert named["code"] == "NO_MARKET_DATA_IN_PAYLOAD"
    assert named["exception"] is None


def test_a_diagnostic_cannot_carry_a_credential():
    """A diagnostic is worthless if it cannot be shown to anyone. An
    exception from an HTTP client can carry a signed URL, so every
    free-text field is sanitized before it is stored."""
    raw = ("GET https://venue.example/v1/markets/abc/book"
           "?signature=ZmFrZXNpZ25hdHVyZXZhbHVlMTIzNDU2Nzg5 -> 401; "
           "secret_key=ABCDEFGHIJKLMNOPQRSTUVWXYZ012345")
    out = loop._sanitize(raw)
    assert "ZmFrZXNpZ25hdHVyZXZhbHVl" not in out
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345" not in out
    assert "<query-removed>" in out or "<redacted>" in out
    assert "401" in out, "the status must survive the redaction"
    assert len(out) <= 240
