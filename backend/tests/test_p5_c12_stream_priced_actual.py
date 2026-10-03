"""P5 C12 END TO END: THE ACTUAL LANE IS PRICED FROM THE ONE STREAM OBSERVATION.

Through the real reactive path (fresh PinnAPI WS change -> the real collector
cycle -> the V3 completed-game decision -> the execution intent -> the
ACTUAL lane) against a FAKE retail venue. The paper book is the REST
fixture; the resident institutional stream book is a `ResidentBooks` fed
directly, read through `live_book_evidence.READER`. The rule is approved for
the test (production approves none) and `paper_benchmark.BOOK_CURRENCY`
stays the production NOT_ESTABLISHED, so the REST path can never be
admitted: only a stream-priced decision can reach the venue.

  §1  a CURRENT stream book of the exactly mapped contract: the intent's
      admission facts take book, IOC limit, depth, fees and EV from that one
      observation (its id and age recorded), P5 is ESTABLISHED with C12
      passed, and Venue.place is called ONCE at the stream's wire price --
      not the REST one; the paper order is still priced from REST
  §2  no stream book (no mapper): REST_PAPER_BOOK stands, C12 refuses,
      Venue.place ZERO times
  §3  a stream book that is stale, crossed, in a gap, or for a different
      symbol refuses; a stream price that does not clear the edge refuses;
      Venue.place ZERO times in every case
"""
from __future__ import annotations

import asyncio
import json
import time


from sportsassets import actual_admission as AA
from sportsassets import bettor_paper_simulator as SIM
from sportsassets import institutional_stream as IS
from sportsassets import live_book_currency as LBC
from sportsassets import live_book_evidence as LBE
from sportsassets.agents import paper_benchmark as PB

try:
    from tests import test_pinnapi_reactive_integration as RI
    from tests.test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from tests.test_execution_intent_fanout import (_arm_actual, _disarm,
                                                    _wait)
    from tests import admission_fixture as AF
except ImportError:                                             # pragma: no cover
    import test_pinnapi_reactive_integration as RI  # type: ignore
    from test_pinnapi_reactive_integration import env, pg  # noqa: F401
    from test_execution_intent_fanout import _arm_actual, _disarm, _wait
    import admission_fixture as AF

OTHER = "aec-mlb-other-sym-2026-10-04"


def _world(e, monkeypatch):
    """OPEN market, compatible settlement, P5 approved for the test. The REST
    book's currency stays the production NOT_ESTABLISHED."""
    monkeypatch.setattr(AA, "APPROVED_LIVE_BOOK_RULES",
                        frozenset({AF.TEST_RULE, LBC.RULE_ID}))
    assert PB.BOOK_CURRENCY["verdict"] == "NOT_ESTABLISHED"
    e.venue.market_state = "MARKET_STATE_OPEN"
    from sportsassets.workers import ext_pinnacle_loop as LOOP
    real = LOOP._settlement_compatibility

    def compatible(srule):
        return dict(real(srule), compatibility="COMPATIBLE",
                    overall_established=True, blockers=[], attest_unmet=[])
    monkeypatch.setattr(LOOP, "_settlement_compatibility", compatible)


def stream_books(slug, *, bids=((490, 300000),), offers=((510, 300000),),
                 age_s=0.1, state="INSTRUMENT_STATE_OPEN"):
    """A resident stream book for `slug` (priceScale 1000, qty scale 100)."""
    b = IS.ResidentBooks()
    b.set_state(IS.S_IDLE, "test")
    b.set_instrument(slug, {"symbol": slug, "priceScale": "1000",
                            "fractionalQtyScale": "100",
                            "state": "INSTRUMENT_STATE_OPEN"})
    b.want([slug])
    b.on_connected("grpc-c12")
    at = time.time() - age_s
    b.on_update({"symbol": slug, "bids": list(bids), "offers": list(offers),
                 "state": state, "transact_time": at}, received_at=at)
    return b


def install_stream(monkeypatch, books, *, symbol=None, read_symbol=None):
    """An exact mapper (to `symbol`, default the slug) and a reader of
    `books` (for `read_symbol` whatever was asked, when given)."""
    monkeypatch.setattr(LBE, "IDENTITY_MAPPER", lambda slug, intent: {
        "status": "EXACT", "symbol": symbol or slug})
    monkeypatch.setattr(LBE, "READER", lambda s, now=None: books.current(
        read_symbol or s, now=now))


async def run_one(e, monkeypatch, *, setup=None):
    """One WS change -> one ENTER decision -> its intent (and the lane)."""
    RI._single_source(e)
    _world(e, monkeypatch)
    e.venue.bids = [(0.50, 2500)]
    await RI._start_and_discover(e)
    await _arm_actual(e, monkeypatch)
    if setup is not None:
        setup()
    RI.tick(e.cache, e.eid, RI.WS_EDGE)

    async def intent():
        return await e.conn.fetchrow(
            "SELECT i.* FROM execution_intents i JOIN paper_decisions d "
            " ON d.decision_id = i.decision_id WHERE d.session_id = $1 "
            " AND d.strategy = $2", e.acct["session_id"], PB.CG_STRATEGY)
    assert await _wait(intent, timeout=10.0), "no execution intent"
    it = dict(await intent())
    if it["live_eligible"]:
        await _wait(lambda: _done(e, it["intent_id"]), timeout=8.0)
        it = dict(await e.conn.fetchrow(
            "SELECT * FROM execution_intents WHERE intent_id = $1",
            it["intent_id"]))
    else:
        await asyncio.sleep(0.3)
    ev = it["evidence"]
    it["evidence"] = json.loads(ev) if isinstance(ev, str) else ev
    return it


async def _done(e, iid):
    st = await e.conn.fetchval(
        "SELECT actual_state FROM execution_intents WHERE intent_id = $1", iid)
    return st not in ("DISPATCHED", "SUBMITTING")


def facts_of(it):
    return it["evidence"]["admission_facts"]


async def no_order(e, it):
    assert e.retail.placed == []                     # Venue.place: ZERO
    assert await e.conn.fetchval(
        "SELECT count(*) FROM execmirror_orders WHERE execution_intent_id=$1",
        it["intent_id"]) == 0


# ── §1 a current stream book: priced from it, P5 established, ONE order ─

@pg
async def test_c12_is_proven_and_the_order_is_priced_from_the_stream(
        env, monkeypatch):
    e = env
    books = stream_books(e.game.us_slug)
    try:
        it = await run_one(e, monkeypatch,
                           setup=lambda: install_stream(monkeypatch, books))
        f = facts_of(it)
        bc = f["book"]["book_currency"]
        assert bc["rule"] == LBC.RULE_ID
        assert bc["verdict"] == LBC.ESTABLISHED, bc
        assert bc["failed_components"] == []
        assert f["book"]["source"] == PB.STREAM_BOOK_SOURCE
        assert str(f["book"]["obs_id"]).startswith("stream:grpc-c12:1:")
        assert f["book"]["age_at_decision_s"] <= LBC.MAX_RECEIPT_AGE_S
        assert f["book"]["market_state"] == "INSTRUMENT_STATE_OPEN"
        ev = it["evidence"]
        assert ev["actual_price_source"] == PB.STREAM_BOOK_SOURCE
        assert ev["stream_observation"]["obs_id"] == f["book"]["obs_id"]
        # price, depth, fees and EV are ONE observation's: the stream ladder
        stream_md = LBE.stream_observation(
            books.current(e.game.us_slug), symbol=e.game.us_slug,
            now=time.time())["market_data"]
        side = it["holding_side"]
        s_lv = SIM.levels_for(stream_md, direction="BUY", holding_side=side)
        r_wire = ev["paper_rest_pricing"]["wire"]
        assert float(f["price"]["wire"]) == float(s_lv["levels"][0]["wire"])
        assert float(f["price"]["wire"]) != float(r_wire)   # never mixed
        assert float(it["wire_price"]) == float(f["price"]["wire"])
        assert f["slippage"]["max_price"] == f["price"]["wire"]
        assert f["fees"]["known"] is True
        assert f["economics"]["net_ev_positive"] is True
        assert it["book_obs_id"] is None
        assert abs(it["book_observed_at"].timestamp()
                   - ev["stream_observation"]["observed_at"]) < 1e-3
        assert it["live_eligible"] is True, it["live_eligibility"]
        assert len(e.retail.placed) == 1
        assert float(e.retail.placed[0]["price"]["value"]) == \
            float(it["wire_price"])
        # the paper decision is still the REST book's
        d = await e.conn.fetchrow(
            "SELECT limit_price, book_obs_id FROM paper_decisions WHERE "
            " decision_id = $1", it["decision_id"])
        assert d["book_obs_id"] == ev["paper_rest_pricing"]["book_obs_id"]
        assert float(d["limit_price"]) == float(
            ev["paper_rest_pricing"]["limit"])
    finally:
        await _disarm(e)


# ── §2 no stream book: REST stands, C12 refuses, nothing placed ──────────

@pg
async def test_c12_refuses_without_a_stream_book(env, monkeypatch):
    e = env
    try:
        it = await run_one(e, monkeypatch, setup=lambda: monkeypatch.setattr(
            LBE, "IDENTITY_MAPPER", None))
        f = facts_of(it)
        assert "actual_price_source" not in it["evidence"]
        assert f["book"]["book_currency"] == PB.BOOK_CURRENCY
        live = f["book"]["live_book_currency"]
        assert live["verdict"] == "NOT_ESTABLISHED"
        assert "C12_PRICED_FROM_THIS_BOOK" in live["failed_components"]
        assert it["live_eligible"] is False
        assert it["actual_refusal"] == AA.R_BOOK_CURRENCY
        await no_order(e, it)
    finally:
        await _disarm(e)


# ── §3 every unusable stream book refuses ───────────────────────────────

def _refused_case(name):
    async def case(env, monkeypatch):
        e = env
        slug = e.game.us_slug
        if name == "stale":
            books, sym = stream_books(slug, age_s=5.0), None
        elif name == "crossed":
            books, sym = stream_books(slug, bids=((520, 300000),),
                                      offers=((510, 300000),)), None
        elif name == "gap":
            books, sym = stream_books(slug), None
            books.on_disconnected("test: connection lost")
        elif name == "other_symbol":
            # the mapping is the slug's; the book read is another symbol's
            books, sym = stream_books(OTHER), None
        elif name == "mapped_to_other":
            # a mapper that names another symbol is never priced from
            books, sym = stream_books(OTHER), OTHER
        elif name == "no_edge":
            books, sym = stream_books(slug, bids=((10, 300000),),
                                      offers=((990, 300000),)), None
        try:
            it = await run_one(e, monkeypatch, setup=lambda: install_stream(
                monkeypatch, books, symbol=sym,
                read_symbol=OTHER if name == "other_symbol" else None))
            f = facts_of(it)
            assert it["live_eligible"] is False, (name, it["live_eligibility"])
            await no_order(e, it)
            bc = f["book"]["book_currency"]
            if name == "stale":
                assert bc["verdict"] == LBC.STALE
                assert "C8_RECEIPT_AGE" in bc["failed_components"]
                assert it["actual_refusal"] == AA.R_BOOK_CURRENCY
            elif name == "crossed":
                assert bc["verdict"] == LBC.REFUSED
                assert "C12_PRICED_FROM_THIS_BOOK" in bc["failed_components"]
                assert "actual_price_source" not in it["evidence"]
            elif name == "gap":
                assert bc["verdict"] == LBC.GAP
                assert "actual_price_source" not in it["evidence"]
            elif name == "other_symbol":
                assert bc["verdict"] == LBC.REFUSED
                assert "C1_IDENTITY_EXACT" in bc["failed_components"]
                assert "actual_price_source" not in it["evidence"]
            elif name == "mapped_to_other":
                assert "C12_PRICED_FROM_THIS_BOOK" in bc["failed_components"]
                assert "actual_price_source" not in it["evidence"]
            elif name == "no_edge":
                assert it["evidence"]["actual_price_source"] == \
                    PB.STREAM_BOOK_SOURCE + "_NO_ORDER"
                assert it["evidence"]["actual_pricing_refusals"]
                assert f["price"]["wire"] is None
                assert it["actual_refusal"] in (AA.R_PRICE, AA.R_DEPTH,
                                                AA.R_NET_EV, AA.R_FEES)
        finally:
            await _disarm(e)
    case.__name__ = "test_a_%s_stream_book_refuses_and_places_nothing" % name
    return pg(case)


test_a_stale_stream_book_refuses_and_places_nothing = _refused_case("stale")
test_a_crossed_stream_book_refuses_and_places_nothing = _refused_case("crossed")
test_a_gap_stream_book_refuses_and_places_nothing = _refused_case("gap")
test_an_other_symbol_stream_book_refuses_and_places_nothing = \
    _refused_case("other_symbol")
test_a_stream_price_below_the_edge_refuses_and_places_nothing = \
    _refused_case("no_edge")
test_a_mapping_to_another_symbol_is_never_priced_from = \
    _refused_case("mapped_to_other")


# ── §4 stream OFF (today's production): the decision path is unchanged ──

@pg
async def test_with_the_stream_off_the_api_mapper_changes_nothing(
        env, monkeypatch):
    """The API lifespan installs institutional_api_stream.identity_mapper
    and calls start(); with INSTITUTIONAL_MD_STREAM unset nothing starts, the
    mapper holds no refdata and answers None, and the intent is exactly the
    REST path's: no new evidence key, the paper decision's own wire, obs id
    and receipt instant, the same live record as before the change."""
    from sportsassets import institutional_api_stream as IAS
    e = env
    IS.reset()
    IAS.reset()
    monkeypatch.delenv("INSTITUTIONAL_MD_STREAM", raising=False)
    try:
        out = await IAS.start(None, env={})
        assert out["started"] is False
        assert out["state"] == IS.S_DISABLED
        assert IS._TRANSPORT is None and IAS._STATE["task"] is None

        def setup():
            monkeypatch.setattr(LBE, "IDENTITY_MAPPER", IAS.identity_mapper)
            monkeypatch.setattr(LBE, "READER", None)
        it = await run_one(e, monkeypatch, setup=setup)
        ev = it["evidence"]
        for k in ("actual_price_source", "stream_observation",
                  "paper_rest_pricing", "actual_pricing_refusals"):
            assert k not in ev, k
        f = facts_of(it)
        assert f["book"]["book_currency"] == PB.BOOK_CURRENCY
        assert f["book"]["source"] != PB.STREAM_BOOK_SOURCE
        live = f["book"]["live_book_currency"]
        before = LBE.for_decision({}, {"us_market_slug": e.game.us_slug,
                                       "side": it["order_intent"]},
                                  obs=None, now=time.time())
        assert live["reason"] == before["reason"] == \
            LBC.R_IDENTITY_UNMAPPED
        assert live["failed_components"] == before["failed_components"]
        assert live["stream_read"] is False
        d = await e.conn.fetchrow(
            "SELECT limit_price, book_obs_id FROM paper_decisions WHERE "
            " decision_id = $1", it["decision_id"])
        assert it["book_obs_id"] == d["book_obs_id"] is not None
        assert float(it["limit_price"]) == float(d["limit_price"])
        assert it["actual_refusal"] == AA.R_BOOK_CURRENCY
        await no_order(e, it)
        # the stream was never asked for anything; nothing was requested
        assert IS.BOOKS.wanted() == [] and IAS.describe()["requested"] == []
        assert IAS.REFDATA == {}
    finally:
        await _disarm(e)
        IS.reset()
        IAS.reset()
