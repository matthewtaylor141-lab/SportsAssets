"""P1: PINNAPI_PRIMARY_INPUT_CHANGED -- WHICH PART IS OURS.

PRODUCTION (latest release): 98 paper decisions in ~16 min over ~39 in-play
markets refused PINNAPI_PRIMARY_INPUT_CHANGED, across the completed-game,
exploration and Derek strategies.

WHAT IS NOT THE CAUSE, PINNED HERE: identity churn. A new socket epoch clears
the cache, so a quote on it is first sight and reads AGE UNKNOWN until a frame
really changes it; an epoch bump with identical prices never reaches the
substance comparison (and that comparison is NOT loosened).

WHAT IS CORRECT AND STAYS: in play the price moves every few seconds, and a
valuation is never decided on a price the feed no longer holds -- a genuine
price change, a fixture change, a phase change and the 30 s rule each still
refuse, by their own names.

WHAT WAS OURS, FIXED: the sequencing waste. One reactive job decides a
valuation with each strategy in turn (each awaiting its own venue read), then
the other side from the same quote; a book retry and the pass backstop decide
the same old valuation later. Meanwhile the feed's newer price is already
queued on the reactive scheduler. A valuation whose price a strictly newer,
fresh price of the same market / record / fixture replaced, while that newer
price is itself being valued, is now SUPERSEDED: not decided by any strategy,
recorded as a DEFERRED hook row (never a paper decision, so no first loss),
and skipped by the backstop.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import types
from datetime import datetime, timezone

import pytest

from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as feed
from sportsassets import pinnapi_primary as P
from sportsassets import pinnapi_reactive as reactive
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_explore as PEX
from sportsassets.agents import paper_maker as PMK
from sportsassets.agents import paper_runtime as PR

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")

AT = 1791050000.0
KEY = F.FULL_GAME_MONEYLINE_KEY
TEAMS = ("Atlanta Braves", "Los Angeles Dodgers")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def market(home=-120, away=110):
    return {"key": KEY, "type": "moneyline", "period": 0, "status": "open",
            "prices": [{"designation": "home", "price": home},
                       {"designation": "away", "price": away}]}


def world(*, live=True):
    """A cache holding fixture 123 (baseball), whose money line CHANGED at
    AT-2 (so it reads fresh at AT), and the discovery event naming it."""
    stream = "live" if live else "prematch"
    start = AT - 1800 if live else AT + 3600
    ev = {"id": 123, "startTime": iso(start), "isLive": live,
          "participants": [{"name": TEAMS[0], "alignment": "home"},
                           {"name": TEAMS[1], "alignment": "away"}],
          "markets": [market(-125, 110)]}
    c = F.FeedCache()
    e = c.new_connection([(stream, 6)])
    c.apply({"type": "snapshot", "stream": stream, "sport_id": 6,
             "ts": (AT - 60) * 1000, "events": [ev]}, epoch=e,
            received_ms=(AT - 60) * 1000 + 5)
    move(c, -120, 110, at=AT - 2, live=live)
    discovery = {"id": "discovery-123", "home_team": TEAMS[0],
                 "away_team": TEAMS[1], "commence_time": iso(start),
                 "sport_key": "baseball_mlb", "bookmakers": []}
    return c, discovery


def move(c, home, away, *, at, live=True):
    m = market(home, away)
    frame = ({"type": "live", "op": "upd", "rec": {"id": 123, "markets": [m]}}
             if live else
             {"type": "prematch_markets", "matchup_id": 123, "data": [m]})
    c.apply(dict(frame, sport_id=6, ts=at * 1000), epoch=c.authority.epoch,
            received_ms=at * 1000 + 5)


def select(c, discovery, at=AT):
    q = P.select(c, discovery, None, family="baseball",
                 sharp_books={"pinnacle"}, at=at, runtime_id="r1")
    assert q is not None and q["reference_input"]["provider"] == P.PROVIDER
    # the provenance as the paper decision reads it: through the persisted
    # row's JSON, exactly as `derek_policy.candidate_from_row` hands it on
    return json.loads(json.dumps(q, default=str))


def check(c, q, at):
    return P.validate(c, q, at=at, max_age_s=30.0, runtime_id="r1")


def sup(c, q, at):
    return P.supersession(c, q, at=at, max_age_s=30.0, runtime_id="r1")


# ── the refusals that stay ───────────────────────────────────────────────

def test_a_genuine_price_change_still_refuses_input_changed():
    c, d = world()
    q = select(c, d)
    assert check(c, q, AT + 1)["ok"] is True
    move(c, -140, 125, at=AT + 3)
    got = check(c, q, AT + 4)
    assert got == {"ok": False, "reason": "PINNAPI_PRIMARY_INPUT_CHANGED"}
    # the supersession names the newer price; it values nothing itself
    s = sup(c, q, AT + 4)
    assert s["reason"] == P.R_SUPERSEDED
    assert s["previous_change_ms"] == (AT - 2) * 1000
    assert s["newer_change_ms"] == (AT + 3) * 1000
    assert s["newer_raw_odds"] == {"home": -140.0, "away": 125.0}
    assert s["previous_raw_odds"] == {"home": -120.0, "away": 110.0}
    assert check(c, q, AT + 4)["ok"] is False      # still refused


def test_a_price_change_with_the_same_change_instant_still_refuses():
    c, d = world()
    q = select(c, d)
    quote = c.quotes[(123, KEY)]
    quote.prices = {"home": -150.0, "away": 130.0}  # same clocks, new prices
    assert check(c, q, AT + 1)["reason"] == "PINNAPI_PRIMARY_INPUT_CHANGED"
    assert sup(c, q, AT + 1) is None                # not strictly newer


def test_epoch_churn_with_identical_substance_is_not_an_input_change():
    """A reconnect (new epoch) re-delivers the IDENTICAL price: the cache was
    cleared, the quote is first sight, and the read says AGE UNKNOWN -- the
    epoch comparison is never what refuses, and nothing is superseded."""
    c, d = world()
    q = select(c, d)
    old_epoch = q["reference_input"]["epoch"]
    e = c.new_connection([("live", 6)])
    assert e == old_epoch + 1
    ev = {"id": 123, "startTime": iso(AT - 1800), "isLive": True,
          "participants": [{"name": TEAMS[0], "alignment": "home"},
                           {"name": TEAMS[1], "alignment": "away"}],
          "markets": [market(-120, 110)]}
    c.apply({"type": "snapshot", "stream": "live", "sport_id": 6,
             "ts": (AT + 1) * 1000, "events": [ev]}, epoch=e,
            received_ms=(AT + 1) * 1000 + 5)
    assert c.quotes[(123, KEY)].prices == {"home": -120.0, "away": 110.0}
    got = check(c, q, AT + 2)
    assert got["ok"] is False
    assert got["reason"] == F.R_NO_CHANGE_TIME != "PINNAPI_PRIMARY_INPUT_CHANGED"
    assert sup(c, q, AT + 2) is None


def test_a_reconfirmed_identical_price_on_the_same_epoch_still_validates():
    c, d = world()
    q = select(c, d)
    move(c, -120, 110, at=AT + 1)                   # same price re-sent
    assert check(c, q, AT + 2)["ok"] is True
    assert sup(c, q, AT + 2) is None


def test_a_phase_change_keeps_its_refusal_and_is_never_superseded():
    c, d = world(live=False)
    q = select(c, d)
    # the game goes in play: a live-phase child now prices fixture 123
    child = {"id": 456, "parentId": 123, "isLive": True, "units": "Regular",
             "startTime": iso(AT + 3600),
             "participants": [{"name": TEAMS[0], "alignment": "home"},
                              {"name": TEAMS[1], "alignment": "away"}],
             "markets": [market(-140, 125)]}
    c.apply({"type": "live", "op": "ins", "rec": child, "sport_id": 6,
             "ts": (AT + 2) * 1000}, epoch=c.authority.epoch,
            received_ms=(AT + 2) * 1000 + 5)
    assert c.fixture_quote_id(123) == (456, None)
    assert check(c, q, AT + 3)["reason"] == "PINNAPI_PRIMARY_INPUT_CHANGED"
    assert sup(c, q, AT + 3) is None


def test_a_fixture_change_keeps_its_refusal_and_is_never_superseded():
    c, d = world()
    q = select(c, d)
    q["reference_input"]["feed_event_id"] = 999
    assert check(c, q, AT + 1)["ok"] is False
    assert sup(c, q, AT + 1) is None


def test_the_30_second_rule_is_intact():
    c, d = world()
    q = select(c, d)
    assert check(c, q, AT - 2 + 30)["ok"] is True
    got = check(c, q, AT - 2 + 30.5)
    assert got["ok"] is False and got["reason"] == F.R_STALE
    # a newer price that is itself older than 30 s supersedes nothing
    move(c, -140, 125, at=AT + 3)
    late = AT + 3 + 31
    assert check(c, q, late)["reason"] == F.R_STALE
    assert sup(c, q, late) is None


def test_a_line_or_legacy_reference_is_never_superseded_here():
    c, d = world()
    q = select(c, d)
    move(c, -140, 125, at=AT + 3)
    legacy = {"reference_input": dict(q["reference_input"],
                                      provider=P.LEGACY_PROVIDER)}
    line = {"reference_input": dict(q["reference_input"],
                                    version="PINNAPI_LINE_V1")}
    assert sup(c, legacy, AT + 4) is None
    assert sup(c, line, AT + 4) is None


# ── the scheduler says what it will value ────────────────────────────────

def _scheduler(c, *, clock):
    s = reactive.Scheduler(c, None, None, clock=clock,
                           held=types.SimpleNamespace(is_held=lambda e: False))
    s.seeds[123] = dict(event={}, sport_key="baseball_mlb", family="baseball",
                        received_at=AT, registered_at=AT)
    c.on_change = s.changed
    return s


def test_the_scheduler_will_value_only_the_queued_or_running_version():
    c, d = world()
    s = _scheduler(c, clock=lambda: AT + 4)
    move(c, -140, 125, at=AT + 3)
    newest = P.version_key(c.quotes[(123, KEY)])
    assert newest == reactive.version_of(c.quotes[(123, KEY)])
    assert s.will_value(123, newest) is True
    assert s.will_value(123, (newest[0], newest[1] - 1, newest[2])) is False
    assert s.will_value(999, newest) is False
    eid, tick = s.next_job()
    assert s.will_value(123, newest) is False       # dequeued, not running
    s.running = (eid, tick["version"])
    assert s.will_value(123, newest) is True        # in flight
    s.closed = True
    assert s.will_value(123, newest) is False


# ── the paper hook: superseded valuations are not decided ────────────────

class FakeConn:
    def __init__(self, newer=None):
        self.newer, self.executed = newer, []

    async def fetchval(self, sql, *args):
        assert "FROM external_valuations" in sql
        return self.newer

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


def _row(q, vid=7001):
    return {"id": vid, "experiment_id": PB.EXPERIMENT_ID,
            "us_market_slug": "aec-mlb-atl-lad-2026-10-06",
            "buy_intent": "BUY_YES",
            "settlement_comparison": json.dumps(
                {"reference_input": q["reference_input"]})}


def _ctx(at):
    return {"clock": lambda: at, "now": at, "session_id": "lane-ic-sess",
            "account_id": "lane-ic-acct", "decided_via": "IN_CYCLE",
            "config": {"entry": {"pinnacle_max_age_s": 30.0}}}


@pytest.fixture
def live_feed(monkeypatch):
    c, d = world()
    monkeypatch.setattr(feed, "_STATE", {
        "owner": types.SimpleNamespace(cache=c), "runtime_id": "r1"})
    monkeypatch.setattr(reactive, "ACTIVE", None)
    return c, d


@pytest.fixture
def strategies(monkeypatch):
    calls = []

    def fake(name):
        async def decide(conn, c, row, **kw):
            calls.append(name)
            return {"decision_id": "d-%s" % name, "verdict": "REFUSE",
                    "refusal": "PINNAPI_PRIMARY_INPUT_CHANGED"}
        return decide

    monkeypatch.setattr(PB, "decide_for_hook", fake(PB.CG_STRATEGY))
    monkeypatch.setattr(PMK, "decide_for_hook", fake(PB.MAKER_STRATEGY))
    monkeypatch.setattr(PEX, "decide_for_hook", fake(PB.EXPLORE_STRATEGY))

    async def no_attempt(*a, **k):
        return None
    monkeypatch.setattr(PB, "record_attempt", no_attempt)
    return calls


def _decide(conn, ctx, row):
    return asyncio.run(PR._decide_paper_strategies(
        conn, ctx, row, vid=row["id"], strategies=None,
        via="IN_CYCLE_AT_THE_VALUATION_INSTANT", attempt_no=1,
        schedule_retry=lambda **k: {"scheduled": False}))


def test_a_superseded_valuation_is_not_decided_by_any_strategy(
        live_feed, strategies, monkeypatch):
    c, d = live_feed
    q = select(c, d)
    s = _scheduler(c, clock=lambda: AT + 4)
    monkeypatch.setattr(reactive, "ACTIVE", s)
    move(c, -140, 125, at=AT + 3)                   # queued by the feed
    conn = FakeConn()
    before = dict(PR.SUPERSEDED_COUNTS)
    out = _decide(conn, _ctx(AT + 4), _row(q))
    assert strategies == []                         # nobody decided it
    for strat in (PB.CG_STRATEGY, PB.MAKER_STRATEGY, PB.EXPLORE_STRATEGY):
        assert out[strat]["superseded"] is True
        assert out[strat]["why"] == P.R_SUPERSEDED
        assert out[strat]["superseded_by"]["by"] == "QUEUED_CHANGE"
        assert "decision_id" not in out[strat]
    rows = [a for sql, a in conn.executed if "paper_hook_failures" in sql]
    assert [a[3] for a in rows] == [PB.CG_STRATEGY, PB.MAKER_STRATEGY,
                                    PB.EXPLORE_STRATEGY]
    assert all(a[4] == P.R_SUPERSEDED for a in rows)
    assert json.loads(rows[0][5])["superseded"] is True
    assert PR.SUPERSEDED_COUNTS["by_queued_change"] == \
        before["by_queued_change"] + 3


def test_without_a_newer_valuation_on_its_way_the_refusal_is_recorded(
        live_feed, strategies, monkeypatch):
    """No scheduler queue and no newer valuation row: nothing says the newer
    price will be valued, so every strategy decides and refuses as before."""
    c, d = live_feed
    q = select(c, d)
    move(c, -140, 125, at=AT + 3)
    conn = FakeConn(newer=None)
    out = _decide(conn, _ctx(AT + 4), _row(q))
    assert strategies == [PB.CG_STRATEGY, PB.MAKER_STRATEGY,
                          PB.EXPLORE_STRATEGY]
    assert all(out[k]["refusal"] == "PINNAPI_PRIMARY_INPUT_CHANGED"
               for k in strategies)
    assert not [1 for sql, _ in conn.executed if "paper_hook_failures" in sql]


def test_a_newer_valuation_of_the_same_contract_supersedes(live_feed):
    c, d = live_feed
    q = select(c, d)
    move(c, -140, 125, at=AT + 3)
    got = asyncio.run(PR.superseded_by(FakeConn(newer=7002), _ctx(AT + 4),
                                       _row(q)))
    assert got["by"] == "NEWER_VALUATION" and got["newer_valuation_id"] == 7002


def test_a_current_valuation_is_never_superseded(live_feed):
    c, d = live_feed
    q = select(c, d)
    got = asyncio.run(PR.superseded_by(FakeConn(newer=7002), _ctx(AT + 1),
                                       _row(q)))
    assert got is None                              # its price is current


def test_a_stale_or_phase_changed_valuation_is_never_superseded(live_feed):
    c, d = live_feed
    q = select(c, d)
    move(c, -140, 125, at=AT + 3)
    stale = asyncio.run(PR.superseded_by(FakeConn(newer=7002),
                                         _ctx(AT + 3 + 31), _row(q)))
    assert stale is None


def test_the_supersession_code_is_classified_software_not_external():
    assert PR.R_SUPERSEDED == P.R_SUPERSEDED
    cls, fam, stage = TT.TABLE[P.R_SUPERSEDED]
    assert cls == TT.S and fam == TT.FRESH
    assert TT.TABLE["PINNAPI_PRIMARY_INPUT_CHANGED"][0] == TT.S


def test_the_backstop_sql_skips_what_the_hook_superseded():
    for sql in (PB.CANDIDATES_SQL, PD.CANDIDATES_SQL):
        assert "paper_hook_failures" in sql
        assert P.R_SUPERSEDED in sql


# ── against the real schema ──────────────────────────────────────────────

async def _pg_superseded_round_trip():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            c, d = world()
            q = select(c, d)
            ref = q["reference_input"]
            vid = await conn.fetchval(
                "INSERT INTO external_valuations (experiment_id, version, "
                " source_class, provider, book, devig_method, venue, "
                " contract_selection, sport_family, market, raw_odds, "
                " outcomes_priced, expected_outcomes, decision, admissible, "
                " us_market_slug, buy_intent, settlement_comparison, "
                " decided_at) VALUES ($1,'V','EXTERNAL_BOOKMAKER_VALUATION',"
                " $2,'pinnacle','power','PMUS','Atlanta Braves','baseball',"
                " 'h2h','{}'::jsonb,2,2,'NO_TRADE',false,"
                " 'aec-mlb-lane-ic','BUY_YES',$3::jsonb,to_timestamp($4))"
                " RETURNING id", PB.EXPERIMENT_ID, P.PROVIDER,
                json.dumps({"reference_input": ref}), AT)
            row = dict(await conn.fetchrow(
                "SELECT * FROM external_valuations WHERE id = $1", vid))
            s = _scheduler(c, clock=lambda: AT + 4)
            old = (feed._STATE, reactive.ACTIVE)
            feed._STATE = {"owner": types.SimpleNamespace(cache=c),
                           "runtime_id": "r1"}
            reactive.ACTIVE = s
            try:
                move(c, -140, 125, at=AT + 3)
                ctx = _ctx(AT + 4)
                got = await PR.superseded_by(conn, ctx, row)
                assert got is not None and got["by"] == "QUEUED_CHANGE"
                res = PR._superseded_result(got)
                await PR._record_superseded(conn, ctx=ctx, valuation_id=vid,
                                            strategy=PB.CG_STRATEGY, res=res)
            finally:
                feed._STATE, reactive.ACTIVE = old
            h = await conn.fetchrow(
                "SELECT outcome, error, detail FROM paper_hook_failures "
                " WHERE valuation_id = $1", vid)
            assert h["outcome"] == "DEFERRED" and h["error"] == P.R_SUPERSEDED
            assert json.loads(h["detail"])["superseded"] is True
            assert await conn.fetchval(
                "SELECT count(*) FROM paper_decisions WHERE valuation_id = $1",
                vid) == 0                           # no decision, no loss
            ids = lambda rows: {r["id"] for r in rows}   # noqa: E731
            # (PAPER-1) the candidates statements take the reading's age
            # limit and the selection instant as well; this row has no
            # observed_at, so its freshness column is true and the
            # selection is exactly as before
            cg = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, ctx["session_id"], 50,
                                  PB.CG_STRATEGY, 30.0, AT + 4)
            ex = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, ctx["session_id"], 50,
                                  PB.EXPLORE_STRATEGY, 30.0, AT + 4)
            other = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                     AT - 60, AT + 60, "another-session", 50,
                                     PB.CG_STRATEGY, 30.0, AT + 4)
            dk = await conn.fetch(PD.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, ctx["session_id"], 50,
                                  30.0, AT + 4)
            assert vid not in ids(cg)               # the backstop skips it
            assert vid in ids(ex) and vid in ids(other) and vid in ids(dk)
            newer = await conn.fetchval(
                PR.NEWER_VALUATION_SQL, PB.EXPERIMENT_ID, "aec-mlb-lane-ic",
                "BUY_YES", vid)
            assert newer is None
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@pg
def test_pg_a_superseded_valuation_is_recorded_and_the_backstop_skips_it():
    asyncio.run(_pg_superseded_round_trip())
