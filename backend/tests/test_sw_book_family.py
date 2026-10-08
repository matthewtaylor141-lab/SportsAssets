"""THE BOOK FAMILY OF SOFTWARE FIRST LOSSES, CLOSED BY CAUSE (red-team closeout).

PRODUCTION (release 08828d04, coverage first-loss census, ping readbacks
37552591368 .. 37718053655, 2026-10-07 00:33Z .. 2026-10-08 02:29Z):

  FEED_MARKET_NOT_IN_CURRENT_STATE (NORMALIZED, 3 events in EVERY 1 h census
  for 26 h: the same three NCAAF games of 2026-10-10)
      the feed matched the fixture and holds its record, the record holds no
      full-game money line, and the metered payload carried no Pinnacle h2h.
      When the record's own current list carries other full-game markets
      (or closes the full game) the two sources agree Pinnacle lists no
      money line for a game it prices:
      PINNAPI_PRIMARY_FIXTURE_LISTS_NO_FULL_GAME_MONEYLINE (EXTERNAL), the
      payload's absence beside it. A record holding nothing else, an
      unparsed record, a Pinnacle h2h in the payload or a native seed keeps
      FEED_MARKET_NOT_IN_CURRENT_STATE (SOFTWARE).
  THE_OBSERVED_BOOK_WAS_UNREADABLE_OR_EMPTY / BOOK_READ_DID_NOT_FINISH_...
      the shared paper market-data owner (221ce6b9) refuses a discovery read
      during the venue's Retry-After hold, or one its queue / coalesced
      leader cannot serve inside the deadline, BEFORE any request; those
      were recorded as a book read and found unreadable, and the one bounded
      retry that exists for a cooldown-cut read was never scheduled.
  FEED_OWNERSHIP_NOT_HELD (MODEL, paper decisions)
      the paper-pass backstop decided valuations priced by a PREVIOUS feed
      runtime (every restart), each a certain FEED_OWNERSHIP_NOT_HELD. They
      are left undecided and recorded instead.

No freshness limit, tolerance, retry bound, gate or threshold changes; the
tests below pin that too.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import os
import time

import pytest

from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_paper_guard as G
from sportsassets import coverage_first_loss as CFL
from sportsassets import paper_market_data as PMD
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as feed
from sportsassets import pinnapi_primary as P
from sportsassets import refusal_taxonomy as RT
from sportsassets import refusal_taxonomy_table as TT
from sportsassets.agents import intelligence_reports as IR
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN", "")
pg = pytest.mark.skipif(not DSN, reason="needs a migrated database")

T = _dt.datetime(2026, 10, 10, 20, 15, tzinfo=_dt.UTC).timestamp()
AT = T - 2.5 * 86400          # when production read it: 2.5 days out
FOOTBALL = P.SPORTS["football"]


def iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.UTC).isoformat() \
        .replace("+00:00", "Z")


# ═════════════════════════════════════════════════════════════════════
# 0 · NOTHING MOVED
# ═════════════════════════════════════════════════════════════════════

def test_no_limit_tolerance_or_retry_bound_moved():
    assert loop.PINNACLE_MAX_AGE_S == 30.0
    assert P.START_TOLERANCE_S == 90 * 60
    assert PB.BOOK_RETRY_MAX_WAIT_S == 20.0
    assert PB.BOOK_RETRY_MARGIN_S == 2.0
    assert PD.BOOK_READ_RESERVE_S == 1.5
    # the codes this closes stay ours where their evidence does not hold
    for c in (F.R_UNKNOWN_MARKET, F.R_NO_AUTHORITY, PD.R_NO_BOOK,
              PD.R_BOOK_DEADLINE):
        assert CFL.classify(c)["class"] == RT.SOFTWARE, c


def test_the_new_codes_are_classified_and_staged():
    c = loop.R_FIXTURE_LISTS_NO_MONEYLINE
    assert RT.classify(c)["classified"]
    assert ext.EVALUABILITY_OF[c] == ext.EXTERNAL_DEPENDENCY
    assert CFL.classify(c)["class"] == CFL.EXTERNAL
    assert ext.STAGE_OF[c] == "1_PROBABILITY"
    # staged where FEED_MARKET_NOT_IN_CURRENT_STATE was on these rows
    assert CFL.chain_stage(c, lane_stage=loop.no_pinnacle_stage([c]),
                           mapped=False) == "NORMALIZED"
    assert c in IR.PRE_VALUATION_CODES
    r = PD.R_PREVIOUS_RUNTIME
    assert TT.TABLE[r][0] == TT.S, "a restart is ours, never EXTERNAL"
    assert r not in ext.EVALUABILITY_OF or \
        ext.EVALUABILITY_OF[r] != ext.EXTERNAL_DEPENDENCY


# ═════════════════════════════════════════════════════════════════════
# 1 · FEED_MARKET_NOT_IN_CURRENT_STATE: WHAT THE FIXTURE'S RECORD LISTS
# ═════════════════════════════════════════════════════════════════════

SPREAD = {"key": "s;0;s;-27.5", "type": "spread", "period": 0,
          "status": "open",
          "prices": [{"designation": "home", "price": -110, "points": -27.5},
                     {"designation": "away", "price": -110, "points": 27.5}]}
TOTAL = {"key": "s;0;ou;55.5", "type": "total", "period": 0,
         "status": "open",
         "prices": [{"designation": "over", "price": -108, "points": 55.5},
                    {"designation": "under", "price": -112,
                     "points": 55.5}]}
HALF_ML = {"key": "s;1;m", "type": "moneyline", "period": 1,
           "status": "open",
           "prices": [{"designation": "home", "price": -900},
                      {"designation": "away", "price": 600}]}
MONEY_LINE = {"key": "s;0;m", "type": "moneyline", "period": 0,
              "status": "open",
              "prices": [{"designation": "home", "price": -5000},
                         {"designation": "away", "price": 1800}]}


def _feed(markets, *, home="Ohio State Buckeyes",
          away="Maryland Terrapins", extra=None):
    """A granted, synced cache holding ONE prematch NCAAF fixture whose
    snapshot record lists `markets`."""
    c = F.FeedCache()
    e = c.new_connection([("prematch", FOOTBALL)])
    ev = dict({"id": 1637351111, "startTime": iso(T), "isLive": False,
               "participants": [{"name": home, "alignment": "home"},
                                {"name": away, "alignment": "away"}],
               "markets": list(markets)}, **(extra or {}))
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": FOOTBALL,
             "ts": (AT - 60) * 1000, "events": [ev]}, epoch=e,
            received_ms=(AT - 60) * 1000 + 5)
    assert c.authority.synced
    return c


def _ncaaf(*, books=None, native=None):
    """The metered provider's event, as production carried it: no Pinnacle
    book (another bookmaker only)."""
    ev = {"id": "031164b8d704599d8023a3e104f9a6c0",
          "sport_key": "americanfootball_ncaaf",
          "home_team": "Ohio State Buckeyes",
          "away_team": "Maryland Terrapins", "commence_time": iso(T),
          "bookmakers": ([{"key": "draftkings", "markets": []}]
                         if books is None else books)}
    if native is not None:
        ev["pinnapi_native"] = native
    return ev


def _select(cache, event):
    why: dict = {}
    got = P.select(cache, event, loop.pinnacle_h2h(event, received_at=AT),
                   family="football", sharp_books=set(loop.SHARP_BOOKS),
                   at=AT, runtime_id="r1", explain=why)
    return got, why


def test_a_priced_fixture_whose_list_has_no_money_line_two_sources_agree():
    """Production shape: Ohio State v Maryland 2026-10-10, 2.5 days out.
    The feed holds the fixture with a spread and a total (and a first-half
    money line) but no full-game money line; the payload has no Pinnacle
    book."""
    c = _feed([SPREAD, TOTAL, HALF_ML])
    ev = _ncaaf()
    got, why = _select(c, ev)
    assert got is None and why["reason"] == F.R_UNKNOWN_MARKET
    ml = why["provenance"]["market_list"]
    assert ml["record_held"] is True and ml["money_line_held"] is False
    assert ml["other_full_game_open_markets"] == 2   # the half is not one
    assert ml["other_full_game_open_by_type"] == {"spread": 1, "total": 1}
    codes = loop.no_pinnacle_codes(why, ev)
    assert codes == [loop.R_FIXTURE_LISTS_NO_MONEYLINE,
                     loop.R_PAYLOAD_HAS_NO_PINNACLE]
    evid = loop.fixture_lists_no_moneyline(why, ev)
    assert evid["basis"] == \
        "OTHER_FULL_GAME_MARKETS_LISTED_WITHOUT_A_MONEY_LINE"
    assert evid["quote_event_id"] == 1637351111
    # staged and classed by its first code, as the ledger row is
    assert loop.no_pinnacle_stage(codes) == "1_PROBABILITY"
    assert CFL.classify(codes[0])["class"] == CFL.EXTERNAL


def test_a_pinnacle_book_without_h2h_is_the_same_second_source():
    c = _feed([SPREAD])
    ev = _ncaaf(books=[{"key": "pinnacle", "markets": [
        {"key": "spreads", "outcomes": []}]}])
    got, why = _select(c, ev)
    assert got is None
    assert loop.no_pinnacle_codes(why, ev) == [
        loop.R_FIXTURE_LISTS_NO_MONEYLINE, loop.R_PINNACLE_HAS_NO_H2H]


def test_a_record_holding_nothing_else_stays_ours():
    """The record holds NO market at all: we may have missed its list, so
    nothing says Pinnacle withheld the money line -- SOFTWARE, as before."""
    c = _feed([])
    ev = _ncaaf()
    _, why = _select(c, ev)
    assert why["reason"] == F.R_UNKNOWN_MARKET
    assert why["provenance"]["market_list"][
        "other_full_game_open_markets"] == 0
    assert loop.fixture_lists_no_moneyline(why, ev) is None
    codes = loop.no_pinnacle_codes(why, ev)
    assert codes == [F.R_UNKNOWN_MARKET, loop.R_PAYLOAD_HAS_NO_PINNACLE]
    assert CFL.classify(codes[0])["class"] == RT.SOFTWARE


def test_only_a_half_market_listed_stays_ours():
    c = _feed([HALF_ML])
    _, why = _select(c, _ncaaf())
    assert loop.fixture_lists_no_moneyline(why, _ncaaf()) is None


def test_a_pinnacle_h2h_in_the_payload_or_a_seed_stays_ours():
    c = _feed([SPREAD, TOTAL])
    pin = [{"key": "pinnacle", "last_update": iso(AT - 5),
            "markets": [{"key": "h2h", "outcomes": [
                {"name": "Ohio State Buckeyes", "price": 1.02},
                {"name": "Maryland Terrapins", "price": 19.0}]}]}]
    ev = _ncaaf(books=pin)
    got, why = _select(c, ev)
    assert got is not None              # the metered fallback carries it
    assert loop.fixture_lists_no_moneyline(why, ev) is None
    seed = _ncaaf(native={"fixture_id": 1637351111})
    got, why = _select(c, seed)
    assert loop.fixture_lists_no_moneyline(why, seed) is None
    assert loop.no_pinnacle_codes(why, seed)[0] == F.R_UNKNOWN_MARKET


def test_an_unparsed_last_record_keeps_its_own_refusal():
    c = _feed([SPREAD, TOTAL])
    c.apply({"type": "prematch_markets", "sport_id": FOOTBALL,
             "matchup_id": 1637351111, "ts": (AT - 30) * 1000,
             "data": [{"key": "s;0;s;-27.5"}]},          # no `type`
            epoch=c.authority.epoch, received_ms=(AT - 30) * 1000)
    _, why = _select(c, _ncaaf())
    assert why["reason"] == F.R_LAST_RECORD_UNPARSED
    assert loop.fixture_lists_no_moneyline(why, _ncaaf()) is None


def test_a_held_money_line_is_still_read_and_the_rule_still_applies():
    """When the record lists the money line the read proceeds exactly as
    before: first sight in a snapshot has no change time."""
    c = _feed([SPREAD, MONEY_LINE])
    _, why = _select(c, _ncaaf())
    assert why["reason"] == F.R_NO_CHANGE_TIME
    assert loop.fixture_lists_no_moneyline(why, _ncaaf()) is None


def test_a_closed_full_game_is_its_own_basis():
    """A live record whose own periods close the full game: Pinnacle has
    closed every full-game market of it (the game is over or suspended)."""
    c = F.FeedCache()
    e = c.new_connection([("live", FOOTBALL)])
    rec = {"id": 1637599999, "startTime": iso(AT - 3 * 3600),
           "isLive": True, "units": "Regular",
           "participants": [{"name": "Ohio State Buckeyes",
                             "alignment": "home"},
                            {"name": "Maryland Terrapins",
                             "alignment": "away"}],
           "markets": [dict(MONEY_LINE)]}
    c.apply({"type": "snapshot", "stream": "live", "sport_id": FOOTBALL,
             "ts": (AT - 60) * 1000, "events": [rec]}, epoch=e,
            received_ms=(AT - 60) * 1000)
    c.apply({"type": "live", "sport_id": FOOTBALL, "op": "upd",
             "ts": (AT - 10) * 1000,
             "rec": {"id": 1637599999,
                     "periods": [{"period": 0, "status": "settled"}]}},
            epoch=e, received_ms=(AT - 10) * 1000)
    ev = dict(_ncaaf(), commence_time=iso(AT - 3 * 3600))
    _, why = _select(c, ev)
    assert why["reason"] == F.R_UNKNOWN_MARKET
    evid = loop.fixture_lists_no_moneyline(why, ev)
    assert evid is not None and evid["basis"] == "FULL_GAME_PERIOD_CLOSED"
    assert loop.no_pinnacle_codes(why, ev)[0] == \
        loop.R_FIXTURE_LISTS_NO_MONEYLINE


def test_a_money_line_held_under_another_key_stays_ours():
    """A full-game moneyline-typed market under a key the selector does
    not read is a key-mapping question of ours, never Pinnacle's absence."""
    odd = dict(MONEY_LINE, key="s;0;m;alt")
    c = _feed([SPREAD, odd])
    _, why = _select(c, _ncaaf())
    assert why["reason"] == F.R_UNKNOWN_MARKET
    assert why["provenance"]["market_list"][
        "full_game_moneyline_under_other_keys"] == 1
    assert loop.fixture_lists_no_moneyline(why, _ncaaf()) is None
    assert loop.no_pinnacle_codes(why, _ncaaf())[0] == F.R_UNKNOWN_MARKET


def test_market_list_counts_only_open_full_game_markets_of_this_epoch():
    c = _feed([SPREAD, dict(TOTAL, status="closed"), HALF_ML])
    ml = c.market_list(1637351111)
    assert ml["other_full_game_open_markets"] == 1
    assert ml["authority_synced"] is True and ml["record_unparsed"] is False
    assert c.market_list(42)["record_held"] is False


# ═════════════════════════════════════════════════════════════════════
# 2 · THE BOOK: A READ OUR OWN OWNER NEVER SENT IS A CUT READ
# ═════════════════════════════════════════════════════════════════════

def test_the_owner_refusals_are_pinned_to_their_source():
    assert PD.OWNER_CUT_REFUSALS == {PMD.R_QUEUE_DEADLINE,
                                     PMD.R_DISCOVERY_DEFERRED,
                                     PMD.R_COALESCE_DEADLINE}
    # each is already a named, SOFTWARE venue-book code in the taxonomy
    for c in PD.OWNER_CUT_REFUSALS:
        assert TT.TABLE[c][0] == TT.S and TT.TABLE[c][2] == "VENUE_BOOK"


def _holding_owner(seconds_left=4.0):
    """The real owner, with the venue's Retry-After hold in force."""
    def boom(*a, **k):
        raise AssertionError("no venue request while the hold is in force")
    return PMD.Owner(transport=boom, recent=lambda *a, **k: None,
                     gate=lambda: {"blocking": True,
                                   "seconds_left": seconds_left,
                                   "reason": "VENUE_429_ON_BOOK_READ"})


def test_a_discovery_read_deferred_by_the_owner_is_a_cut_read():
    owner = _holding_owner()
    got = owner.read("aec-cfb-ucf-okst-2026-10-10",
                     deadline_epoch_s=time.time() + 5)
    assert got["error"] == PMD.R_DISCOVERY_DEFERRED
    assert got["refused_by"] == "PAPER_MARKET_DATA_OWNER"
    assert PD.book_deadline_refusal(got) is True
    # through the paper client, exactly as a decision reads it
    client = G.PaperMarketDataClient(
        lambda slug, deadline_epoch_s=None: owner.read(
            slug, deadline_epoch_s=deadline_epoch_s))
    ctx = {"market_data": client, "deadline": time.monotonic() + 6.0}
    got = asyncio.run(PD.read_book_within_deadline(ctx, "aec-x"))
    assert PD.book_deadline_refusal(got) is True
    # and the source names it a cut read, not a failed one
    assert PB.book_source({"got": got, "obs": {"error": got["error"]}}) == \
        "READ_CUT_BY_DEADLINE_OR_COOLDOWN"


def test_a_queue_or_coalesce_deadline_is_a_cut_read():
    for err in (PMD.R_QUEUE_DEADLINE, PMD.R_COALESCE_DEADLINE):
        assert PD.book_deadline_refusal(
            {"marketData": None, "error": err}) is True


def test_every_other_failure_keeps_its_meaning():
    # a venue error, a raised client, a foreign ticker: not cut reads
    for err in ("APIStatusError", "ConnectError", PMD.R_FOREIGN_VENUE,
                "NO_MARKET_DATA"):
        assert PD.book_deadline_refusal({"marketData": None,
                                         "error": err}) is False
    # an absent side on a SUCCESSFUL read stays the read-failure code; a
    # valid empty side is the ECONOMIC one (unchanged)
    assert PD.no_book_refusal({"error": None, "market_data": {"bids": []}},
                              {"book_was": None, "levels": []}) == \
        PD.R_NO_BOOK
    assert PD.no_book_refusal(
        {"error": None, "market_data": {"offers": []}},
        {"book_was": "VALID_BUT_EMPTY", "levels": [],
         "excluded_off_cent_grid": 0}) == PD.R_SIDE_EMPTY_ON_A_READ_BOOK
    # the gate's own refusal and our timeout are cut reads, as before
    assert PD.book_deadline_refusal({"refused_by": "OUR_REQUEST_GATE",
                                     "error": "X"}) is True
    assert PD.book_deadline_refusal(
        {"error": G.R_BOOK_READ_DEADLINE}) is True


def test_the_owner_deferral_earns_the_one_bounded_retry():
    owner = _holding_owner(seconds_left=4.0)
    got = owner.read("aec-x", deadline_epoch_s=time.time() + 5)
    pin = {"age_s": 10.0, "limit_s": 30.0}
    r = PB.book_retry_plan({"book_retry_ok": True}, got, pin)
    assert r["retry"] is True and r["cooldown_s"] == 4.0
    assert r["after_s"] == pytest.approx(4.25)
    # the same bounds: no budget, a stale projection, a long hold
    assert not PB.book_retry_plan({}, got, pin)["retry"]
    assert PB.book_retry_plan({"book_retry_ok": True}, got,
                              {"age_s": 25.0, "limit_s": 30.0})["why"] == \
        "PINNACLE_WOULD_BE_STALE_AFTER_THE_COOLDOWN"
    long = _holding_owner(seconds_left=40.0).read(
        "aec-x", deadline_epoch_s=time.time() + 5)
    assert PB.book_retry_plan({"book_retry_ok": True}, long, pin)["why"] \
        == "COOLDOWN_LONGER_THAN_THE_RETRY_CAP"


# ═════════════════════════════════════════════════════════════════════
# 3 · FEED_OWNERSHIP_NOT_HELD: A VALUATION OF A PREVIOUS FEED RUNTIME
# ═════════════════════════════════════════════════════════════════════

def _row(runtime_id, provider=P.PROVIDER, vid=1):
    return {"id": vid, "settlement_comparison": json.dumps(
        {"reference_input": {"provider": provider, "version": P.VERSION,
                             "runtime_id": runtime_id}})}


@pytest.fixture
def runtime(monkeypatch):
    def set_(rid):
        monkeypatch.setattr(feed, "_STATE", {"owner": None,
                                             "runtime_id": rid})
    return set_


def test_a_previous_runtime_valuation_is_named(runtime):
    runtime("new")
    got = PD.previous_feed_runtime(_row("old"))
    assert got == {"why": PD.R_PREVIOUS_RUNTIME,
                   "valuation_runtime_id": "old",
                   "current_runtime_id": "new",
                   "certain_refusal": F.R_NO_AUTHORITY}


def test_the_current_runtime_no_feed_here_or_a_legacy_row_is_decided(
        runtime):
    runtime("new")
    assert PD.previous_feed_runtime(_row("new")) is None
    assert PD.previous_feed_runtime(_row("old", provider=P.LEGACY_PROVIDER)) \
        is None
    assert PD.previous_feed_runtime(_row(None)) is None
    assert PD.previous_feed_runtime({"id": 1}) is None
    runtime(None)                       # no feed in this process
    assert PD.previous_feed_runtime(_row("old")) is None


def test_its_decision_would_have_been_the_certain_refusal(runtime):
    """What the pass used to record for it, by the unchanged recheck."""
    runtime("new")
    ref = json.loads(_row("old")["settlement_comparison"])["reference_input"]
    assert P.validate(object(), {"reference_input": ref}, at=AT,
                      runtime_id="new")["reason"] == F.R_NO_AUTHORITY


def test_both_backstop_queries_skip_what_was_recorded():
    for sql in (PB.CANDIDATES_SQL, PD.CANDIDATES_SQL):
        assert PD.R_PREVIOUS_RUNTIME in sql and P.R_SUPERSEDED in sql


class _Conn:
    """Records the hook-failure INSERT; answers the candidates query."""

    def __init__(self, rows):
        self.rows, self.inserts = rows, []

    async def fetch(self, sql, *a):
        return list(self.rows)

    async def fetchval(self, sql, *a):
        return True

    async def execute(self, sql, *a):
        self.inserts.append((sql, a))


def _ctx():
    return {"config": {"entry": {"valuation_lookback_s": 1800.0},
                       "cadence": {"max_decisions_per_pass": 50}},
            "now": AT, "session_id": "s1", "account_id": "a1",
            "deadline": time.monotonic() + 30.0}


def test_the_benchmark_pass_leaves_it_undecided_and_recorded(
        runtime, monkeypatch):
    runtime("new")

    async def enabled(conn, pol):
        return {"enabled": True}
    monkeypatch.setattr(PB, "enablement", enabled)
    decided = []

    async def decide(conn, ctx, row, pol):
        decided.append(row["id"])
        return {"decision_id": "d{}".format(row["id"]), "verdict": "REFUSE",
                "refusal": "BELOW_MIN_GROSS_EDGE"}

    async def attempt(*a, **k):
        return None
    monkeypatch.setattr(PB, "record_attempt", attempt)
    conn = _Conn([_row("old", vid=7), _row("new", vid=8)])
    out = asyncio.run(PB.step(conn, _ctx(), PB.CG_POLICY, decide=decide))
    assert decided == [8], "only the current runtime's valuation"
    assert out["previous_runtime"] == 1 and out["deferred"] == 1
    sql, args = conn.inserts[0]
    assert "paper_hook_failures" in sql and "'DEFERRED'" in sql
    assert args[2] == 7 and args[3] == PB.CG_STRATEGY
    assert args[4] == PD.R_PREVIOUS_RUNTIME
    assert json.loads(args[5])["valuation_runtime_id"] == "old"


def test_derek_s_pass_leaves_it_undecided_and_recorded(runtime, monkeypatch):
    runtime("new")

    async def context(conn, ctx):
        return {"model": {"ok": False}}
    monkeypatch.setattr(PD, "_context", context)
    decided = []

    async def decide_one(conn, ctx, row):
        decided.append(row["id"])
        return {"verdict": "REFUSE", "refusal": "X"}
    monkeypatch.setattr(PD, "decide_one", decide_one)
    conn = _Conn([_row("old", vid=7), _row("new", vid=8)])
    out = asyncio.run(PD.step(conn, _ctx()))
    assert decided == [8]
    assert out["previous_runtime"] == 1
    assert conn.inserts[0][1][3] == "DEREK_ENTRY_POLICY_V2"
    assert conn.inserts[0][1][4] == PD.R_PREVIOUS_RUNTIME


async def _pg_previous_runtime_round_trip():
    import asyncpg
    conn = await asyncpg.connect(DSN)
    try:
        tr = conn.transaction()
        await tr.start()
        try:
            vid = await conn.fetchval(
                "INSERT INTO external_valuations (experiment_id, version, "
                " source_class, provider, book, devig_method, venue, "
                " contract_selection, sport_family, market, raw_odds, "
                " outcomes_priced, expected_outcomes, decision, admissible, "
                " us_market_slug, buy_intent, settlement_comparison, "
                " decided_at) VALUES ($1,'V','EXTERNAL_BOOKMAKER_VALUATION',"
                " $2,'pinnacle','power','PMUS','Vanderbilt','football',"
                " 'h2h','{}'::jsonb,2,2,'NO_TRADE',false,"
                " 'aec-cfb-miss-vand-rt','BUY_YES',$3::jsonb,"
                " to_timestamp($4)) RETURNING id", PB.EXPERIMENT_ID,
                P.PROVIDER, json.dumps({"reference_input": {
                    "provider": P.PROVIDER, "version": P.VERSION,
                    "runtime_id": "old"}}), AT)
            row = dict(await conn.fetchrow(
                "SELECT * FROM external_valuations WHERE id = $1", vid))
            old = feed._STATE
            feed._STATE = {"owner": None, "runtime_id": "new"}
            try:
                prev = PD.previous_feed_runtime(row)
                assert prev is not None
                ctx = {"session_id": "rt-sess", "account_id": "rt-acct"}
                res = await PD.record_previous_runtime(
                    conn, ctx=ctx, valuation_id=vid,
                    strategy=PB.CG_STRATEGY, prev=prev)
                assert res["deferred"] is True
            finally:
                feed._STATE = old
            h = await conn.fetchrow(
                "SELECT stage, outcome, error, detail FROM "
                " paper_hook_failures WHERE valuation_id = $1", vid)
            assert (h["stage"], h["outcome"], h["error"]) == (
                "PAPER_PASS", "DEFERRED", PD.R_PREVIOUS_RUNTIME)
            assert json.loads(h["detail"])["current_runtime_id"] == "new"
            assert await conn.fetchval(
                "SELECT count(*) FROM paper_decisions WHERE valuation_id = $1",
                vid) == 0                           # no decision, no loss
            def ids(rows):
                return {r["id"] for r in rows}
            cg = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, "rt-sess", 50,
                                  PB.CG_STRATEGY)
            ex = await conn.fetch(PB.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, "rt-sess", 50,
                                  PB.EXPLORE_STRATEGY)
            dk = await conn.fetch(PD.CANDIDATES_SQL, PB.EXPERIMENT_ID,
                                  AT - 60, AT + 60, "rt-sess", 50)
            assert vid not in ids(cg)               # not re-selected
            assert vid in ids(ex) and vid in ids(dk)  # per strategy
        finally:
            await tr.rollback()
    finally:
        await conn.close()


@pg
def test_pg_a_previous_runtime_valuation_is_recorded_and_not_reselected():
    asyncio.run(_pg_previous_runtime_round_trip())
