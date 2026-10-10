"""WEBSOCKET-TRIGGERED PAPER EVALUATION, END TO END ON REAL POSTGRES.

The full-cycle proof research/pinnapi-reactive-20261002/CLAUDE-APPLY.md step 5
asks for, through production code from the feed frame to the paper decision:

  snapshot -> the REAL periodic `ext_pinnacle_loop.cycle()` registers the
  discovery seed (`pinnapi_reactive.register`, called by the collector) ->
  a changed WS frame applied to the REAL `pinnapi_feed.FeedCache` ->
  `FeedCache.on_change` -> the REAL `pinnapi_reactive.Scheduler` started by
  `pinnapi_reactive.start(pool, cycle=loop.cycle)` -> STARTED audit row ->
  the REAL `cycle(conn, stream_seed=...)` (venue-native identity, rules,
  fixture scope, venue book, reference re-validation, persistence) -> the
  REAL in-cycle paper hook (`_paper_valuation` -> paper_runtime) -> a paper
  decision on THAT valuation -> COMPLETED audit row with its valuation IDs.

NO SECOND PERIODIC CYCLE is run after discovery; every reactive evaluation is
started by a feed frame.

WHAT IS SUBSTITUTED, AT ITS TRANSPORT BOUNDARY ONLY (the seams the existing
harnesses use -- tests/test_venue_native_identity_matches_the_measured_fixture
`substitute` / `seed_venue` / `Game`, tests/test_completed_game_collector_path
`_wire`): the odds provider's catalogue/odds/scores fetches, the league
schedule, the venue client's listing/book calls, venue pacing, and the paper
market-data transport. ONE synthetic venue book (`_Venue`) answers both the
collector's book read and the paper book read, so the two can never disagree
about what the venue showed. The WS frames are the shapes of
tests/test_pinnapi_primary_source.seed / tests/test_pinnapi_reactive.tick,
stamped with the current clock (those helpers pin a fixed future instant,
which a real-clock cycle correctly refuses as FEED_QUOTE_CHANGE_TIME_IN_THE_
FUTURE). No book-currency mechanism is supplied: the venue read refuses
VENUE_BOOK_CURRENCY_NOT_ESTABLISHED exactly as in production, and the
valuation is the collector's CALIBRATION_ONLY record that the paper policies
decide on with their own book read. ALL PRICES ARE SYNTHETIC. Scratch paper
accounts only; no funded switch, no venue order.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import pathlib
import random
import time
import types

import pytest

from sportsassets import bettor_external_shadow as EXT
from sportsassets import bettor_paper_guard as G
from sportsassets import bettor_paper_session as S
from sportsassets import pinnapi_feed as F
from sportsassets import pinnapi_feed_runtime as FR
from sportsassets import pinnapi_primary as P
from sportsassets import pinnapi_reactive as R
from sportsassets import pmus
from sportsassets import venue_pace
from sportsassets.agents import paper_benchmark as PB
from sportsassets.agents import paper_derek as PD
from sportsassets.agents import paper_runtime as PR
from sportsassets.agents import runtime as RT
from sportsassets.workers import ext_pinnacle_loop as loop

from tests import paper_harness as H
from tests import paper_live_fixture as PL
from tests import test_venue_native_identity_matches_the_measured_fixture as VN

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")

BACKEND = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_194 = BACKEND / "migrations" / "194_pinnapi_reactive_attempts.sql"
SPORT_ID = 6                      # PinnAPI baseball
RUNTIME_ID = "rt-reactive-integration"
TERMINAL = ("COMPLETED", "REFUSED", "TIMEOUT", "ERROR", "CANCELLED")
FUNDED_EXTRA = ("rn1x_positions", "rn1x_orders", "rn1x_fills",
                "rn1x_decisions", "derek_entry_decisions")

#: The venue's recorded MLB wording (tests/test_completed_game_collector_path
#: LISTED_PROSE), with this SYNTHETIC fixture's teams substituted.
VENUE_PROSE = (
    "This market will settle to the winner of the Colorado Rockies vs Miami "
    "Marlins MLB game scheduled for the listed date. Extra innings are "
    "included if played. If the game is delayed, postponed, or suspended "
    "and not rescheduled to a date within two weeks of the originally "
    "scheduled date, the market will settle to the last fair market price. "
    "Outcome sourced from MLB.")

#: WS prices (AMERICAN). Home Marlins -150 / Rockies +130 de-vigs to ~0.58
#: for the home side; the discovery payload's own Pinnacle (2.20 / 1.72,
#: ~0.44) shows no edge against the 0.50 executable price.
WS_EDGE = {"home": -150, "away": 130}


def _iso(t):
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ── ONE SYNTHETIC VENUE: the collector's client AND the paper transport ──

class _Venue:
    """The venue's listing, book and market surfaces (what `pmus._get_client`
    hands the collector) and the paper market-data transport, over ONE book
    state. `age_s` is how old the venue's book is when it is read; `on_book`
    runs inside the collector's (threaded) venue book read, i.e. AFTER the
    reactive evaluation started and BEFORE the reference is re-validated."""

    def __init__(self, slug):
        self.slug = slug
        self.bids = [(0.50, 400)]          # the home (SHORT) row pays 1 - bid
        self.offers = [(0.52, 400)]
        self.age_s = 1.0
        self.market_state = None           # the book's venue market state
        self.on_book = None
        self.calls = {"list": 0, "book": 0, "paper": 0}

    # pmus client: client.markets.<call>
    @property
    def markets(self):
        return self

    def list(self, params=None):
        self.calls["list"] += 1
        want = list((params or {}).get("slug") or [])
        return {"markets": [{"slug": s, "description": VENUE_PROSE,
                             "marketType": "MARKET_TYPE_SPORTS",
                             "sportsMarketType": VN.SPORTS_TYPE,
                             "sportsMarketTypeV2":
                                 "SPORTS_MARKET_TYPE_MONEYLINE",
                             "orderPriceMinTickSize": "0.01"}
                            for s in want if s == self.slug]}

    def book(self, slug):
        self.calls["book"] += 1
        if self.on_book is not None:
            self.on_book()
        if slug != self.slug:
            return {}

        def lvl(p, q):
            return {"px": {"value": "%.2f" % p, "currency": "USD"},
                    "qty": str(q)}
        return {"marketData": {
            "offers": [lvl(p, q) for p, q in self.offers],
            "bids": [lvl(p, q) for p, q in self.bids],
            "transactTime": _dt.datetime.fromtimestamp(
                time.time() - self.age_s, _dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"}}

    def retrieve_by_slug(self, slug):
        return {"market": {"slug": slug, "marketSides": []}}

    # paper transport: transport(slug) -> {marketData, observed_at}
    def __call__(self, slug):
        self.calls["paper"] += 1
        if slug != self.slug:
            return {"marketData": None, "error": "NO_BOOK_FIXTURE",
                    "observed_at": time.time()}
        md = H.md(bids=self.bids, offers=self.offers)
        if self.market_state is not None:
            md["state"] = self.market_state
        return {"marketData": md, "observed_at": time.time() - self.age_s}


# ── THE FEED: real FeedCache, frames in the shapes of the unit tests ────

def _market(prices):
    return {"key": F.FULL_GAME_MONEYLINE_KEY, "type": "moneyline",
            "period": 0, "status": "open",
            "prices": [{"designation": "home", "price": prices["home"]},
                       {"designation": "away", "price": prices["away"]}]}


def _feed(game, eid):
    """`test_pinnapi_primary_source.seed` with moved=False, on the real
    clock: a granted, resynchronised epoch whose snapshot carries the event
    (age UNKNOWN until a change is observed)."""
    c = F.FeedCache()
    ep = c.new_connection([("prematch", SPORT_ID)])
    now = time.time()
    event = {"id": eid, "startTime": game.commence, "isLive": False,
             "participants": [{"name": VN.HOME, "alignment": "home"},
                              {"name": VN.AWAY, "alignment": "away"}],
             "markets": [_market({"home": 120, "away": -140})]}
    c.apply({"type": "snapshot", "stream": "prematch", "sport_id": SPORT_ID,
             "ts": (now - 60) * 1000, "events": [event]}, epoch=ep,
            received_ms=(now - 60) * 1000 + 5)
    assert c.authority.synced
    return c


def tick(cache, eid, prices, *, lag_s=0.2):
    """`test_pinnapi_reactive.tick`: one prematch_markets frame, provider
    stamp `lag_s` before our receipt, received NOW."""
    now = time.time()
    return cache.apply({"type": "prematch_markets", "matchup_id": eid,
                        "data": [_market(prices)], "sport_id": SPORT_ID,
                        "ts": (now - lag_s) * 1000},
                       epoch=cache.authority.epoch, received_ms=now * 1000)


# ── THE ENVIRONMENT ──────────────────────────────────────────────────

async def _ensure_schema(conn):
    from sportsassets.workers import premap as pm
    # the harness's way of putting a migration it needs in place
    # (test_derek_enters_on_conservative_agreement._ensure_schema)
    await conn.execute(MIGRATION_194.read_text())
    await pm._ensure_table(conn)
    await conn.execute("CREATE TABLE IF NOT EXISTS ingestion_state "
                       "(key TEXT PRIMARY KEY, value TEXT)")


async def _clean(conn, game, eid):
    await VN.clean(conn, game)
    async with conn.transaction():
        # paper_decisions keep their valuation id (PL.purge_everything)
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM external_valuations WHERE us_market_slug=$1",
            game.us_slug)
        # the venue event's fixed event key (migration 261, append-only):
        # the synthetic game's venue event slug is dated, so a key fixed by
        # one proof would otherwise be inherited by the next proof that day
        if await conn.fetchval(
                "SELECT to_regclass('venue_fixture_event_keys')") is not None:
            await conn.execute(
                "DELETE FROM venue_fixture_event_keys "
                " WHERE venue_event_slug=$1", game.event_slug)
    await conn.execute(
        "DELETE FROM pinnapi_reactive_attempts WHERE event_id=$1", str(eid))


async def _counts(conn) -> dict:
    out = dict(await H.funded_table_counts(conn))
    for t in FUNDED_EXTRA:
        if await conn.fetchval("SELECT to_regclass($1)", t) is not None:
            out[t] = await conn.fetchval('SELECT count(*) FROM "%s"' % t)
    return out


#: THE DISCOVERY QUOTE SITS INSIDE THE VENUE SPREAD (P0 incident, inc-edge).
#: Each valuation now also values the OTHER side of its contract, so the
#: discovery cycle's the-odds-api quote is decided on both sides. Its
#: synthetic prices (2.20 / 1.72, p(home) ~ 0.436 on this module's book of
#: bid 0.50 / offer 0.52) left the other side 4.5 pp under its offer and it
#: entered at discovery -- correctly, by the unchanged rules -- which is not
#: what these proofs are about: the reactive WS change. At 2.00 / 1.92
#: (p(home) ~ 0.49) neither side of the contract clears the threshold at
#: discovery, exactly as the priced side alone never did; every WS-path
#: assertion below is unchanged.
DISCOVERY_PRICES = (2.00, 1.92)


def _inside_the_spread(ev):
    for b in ev.get("bookmakers") or []:
        for m in b.get("markets") or []:
            outs = m.get("outcomes") or []
            if len(outs) == 2:
                outs[0]["price"], outs[1]["price"] = DISCOVERY_PRICES
    return ev


class Env:
    pass


@pytest.fixture
async def env(monkeypatch, new_strategies_off):
    """Production paper selection (completed-game policy ON, strict OFF,
    maker/exploration OFF), PAPER_SESSION on, the reactive switch on, a real
    asyncpg pool, a real feed cache owned by this process's runtime state."""
    e = Env()
    e.t0 = time.time()
    e.conn = await H.connect()
    e.pool = None
    e.task = None
    e.game = VN.Game()
    e.eid = random.randint(10**8, 2 * 10**8)
    await _ensure_schema(e.conn)
    await _clean(e.conn, e.game, e.eid)
    await VN.seed_venue(e.conn, e.game)

    e.acct = await PL.new_account(e.conn, "reactive", now=e.t0)
    e.venue = _Venue(e.game.us_slug)
    from unittest.mock import AsyncMock
    monkeypatch.setattr(PR.L, "selected_account", AsyncMock(return_value=e.acct["account_id"]))
    monkeypatch.setitem(PR._CLIENT, "client",
                        G.PaperMarketDataClient(e.venue))
    monkeypatch.setattr(RT, "paper_pass_hook",
                        lambda **kw: {"scheduled": False})
    # (integration) no background entry-fill read on the process-wide db
    # pool: an ENTER through the real cycle starts `schedule_entry_fill` by
    # default, which opened db's global pool on this test's loop and left it
    # for a later db.close_pool to trip on ("Event loop is closed"; the k6b
    # proof that uses this harness set it -- located with a teardown spy over
    # the suite prefix). Stubbed exactly like the paper-pass hook above.
    monkeypatch.setattr(PR, "schedule_entry_fill",
                        lambda ids, **kw: {"scheduled": False})
    PD._CONTEXT_CACHE.clear()
    PB._CONTEXT_CACHE.clear()
    monkeypatch.setenv(S.ENV_FLAG, "on")
    monkeypatch.setenv(PB.ENV_FLAG, "on")
    monkeypatch.setenv("PINNAPI_REACTIVE_PAPER", "on")
    e.controls = {}
    for key, on in ((PB.CG_POLICY["control_key"], True),
                    (PB.CONTROL_KEY, False)):
        e.controls[key] = await e.conn.fetchval(
            "SELECT enabled FROM paper_control WHERE control_key=$1", key)
        await e.conn.execute("UPDATE paper_control SET enabled=$2 WHERE "
                             " control_key=$1", key, on)

    # provider + venue at their transport boundaries (VN.substitute), with
    # this file's venue in place of its static client
    e.fetches = []
    e.discovery = lambda now: [_inside_the_spread(
        VN.odds_event(e.game, "rx%d" % e.eid, at=now))]
    VN.substitute(monkeypatch, slugs=[e.game.us_slug],
                  odds_by_sport={"baseball_mlb": lambda now: e.discovery(now)})
    real_fetch = loop.fetch_odds

    async def counted_fetch(sport_key, **kw):
        e.fetches.append(sport_key)
        return await real_fetch(sport_key, **kw)
    monkeypatch.setattr(loop, "fetch_odds", counted_fetch)
    monkeypatch.setattr(pmus, "_get_client", lambda: e.venue)
    monkeypatch.setattr(venue_pace, "pace", lambda *a, **k: 0.0)
    loop.rules_cache_reset()

    # the feed owner this process holds (pinnapi_feed_runtime._STATE)
    e.cache = _feed(e.game, e.eid)
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(cache=e.cache))
    monkeypatch.setitem(FR._STATE, "runtime_id", RUNTIME_ID)
    monkeypatch.setattr(R, "ACTIVE", None)
    try:
        yield e
    finally:
        try:
            await R.stop(e.task)
        finally:
            if e.pool is not None:
                await e.pool.close()
            loop.rules_cache_reset()
            for key, was in e.controls.items():
                if was is not None:
                    await e.conn.execute(
                        "UPDATE paper_control SET enabled=$2 WHERE "
                        " control_key=$1", key, was)
            await PL.drop_today_run(e.conn, e.t0)
            await _clean(e.conn, e.game, e.eid)
            await e.conn.close()


async def _start_and_discover(e):
    """The reactive worker started the way `ext_pinnacle_loop.run` starts it,
    then ONE periodic cycle -- the only one -- registers the discovery seed."""
    import asyncpg
    e.pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)
    e.task = R.start(e.pool, cycle=loop.cycle)
    assert e.task is not None, "the reactive switch did not start the worker"
    assert e.cache.on_change == R.ACTIVE.changed
    out = await loop.cycle(e.conn)
    assert out.get("ran") is True, out.get("why")
    assert e.eid in R.ACTIVE.seeds, (dict(R.ACTIVE.counts), out.get("refusals"))
    seed = R.ACTIVE.seeds[e.eid]
    assert seed["sport_key"] == "baseball_mlb" and seed["family"] == "baseball"
    assert e.fetches, "the periodic cycle never asked the provider"
    # nothing reactive happened yet: the snapshot alone has no change time
    assert not R.ACTIVE.pending
    assert await _attempts(e) == []
    e.fetches_after_discovery = len(e.fetches)
    return out


async def _attempts(e) -> list:
    return [dict(r, detail=H.j(r["detail"])) for r in await e.conn.fetch(
        "SELECT attempt_id, state, detail, created_at, updated_at "
        "  FROM pinnapi_reactive_attempts WHERE event_id=$1 "
        " ORDER BY created_at, attempt_id", str(e.eid))]


async def _wait_terminal(e, n=1, timeout=45.0) -> list:
    end = time.time() + timeout
    while time.time() < end:
        rows = await _attempts(e)
        if (len([r for r in rows if r["state"] in TERMINAL]) >= n
                and not any(r["state"] == "STARTED" for r in rows)
                and not R.ACTIVE.pending):
            return rows
        await asyncio.sleep(0.1)
    raise AssertionError("no terminal attempt within %ss: %r counts=%r" % (
        timeout, await _attempts(e), dict(R.ACTIVE.counts)))


async def _valuation(e, vid):
    return await e.conn.fetchrow(
        "SELECT * FROM external_valuations WHERE id=$1", vid)


async def _priced(e, vids) -> list:
    """THE PRICED OUTCOME'S valuation ids, in order. P0 incident (inc-edge):
    each evaluation now ALSO records the other side of the same contract --
    the complement, 1 - p over the complete set, CALIBRATION_ONLY -- so an
    attempt carries two valuation ids where these proofs were written for
    one. The proofs below are about the priced side; the complement is
    asserted where an attempt is first read
    (test_a_changed_frame_evaluates_through_the_real_cycle_and_paper_hook)."""
    return [r["id"] for r in await e.conn.fetch(
        "SELECT id FROM external_valuations WHERE id = ANY($1::bigint[]) "
        "   AND NOT coalesce(payout_is_complement, false) ORDER BY id",
        list(vids))]


async def _decisions(e, vids) -> list:
    return [dict(r) for r in await e.conn.fetch(
        "SELECT * FROM paper_decisions WHERE session_id=$1 "
        "   AND valuation_id = ANY($2::bigint[]) ORDER BY valuation_id",
        e.acct["session_id"], list(vids))]


async def _enters_on_contract(e) -> int:
    return await e.conn.fetchval(
        "SELECT count(*) FROM paper_decisions d JOIN external_valuations v "
        "  ON v.id = d.valuation_id WHERE d.session_id=$1 "
        "   AND v.us_market_slug=$2 AND d.verdict='ENTER'",
        e.acct["session_id"], e.game.us_slug)


def _cg(decisions):
    return [d for d in decisions if d["strategy"] == PB.CG_STRATEGY]


# ═════════════════════════════════════════════════════════════════════
# 1 · HAPPY PATH
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_a_changed_frame_evaluates_through_the_real_cycle_and_paper_hook(
        env):
    e = env
    await _start_and_discover(e)
    funded_before = await _counts(e.conn)

    tick(e.cache, e.eid, WS_EDGE)                 # the WS change
    assert len(R.ACTIVE.pending) == 1             # queued by on_change alone
    rows = await _wait_terminal(e)

    assert [r["state"] for r in rows] == ["COMPLETED"], rows
    a = rows[0]["detail"]
    assert a["result"]["state"] == "WS_PAPER_EVALUATED", a["result"]
    vids = await _priced(e, a["valuation_ids"])
    assert len(vids) == 1, a
    assert a["result"]["valuation_ids"] == a["valuation_ids"]
    # ...and the OTHER side of the same contract, valued from the same read
    # (P0 incident, inc-edge): one complement row beside the priced one
    comp = [r for r in [await _valuation(e, x) for x in a["valuation_ids"]]
            if r["payout_is_complement"]]
    assert len(comp) == 1 and len(a["valuation_ids"]) == 2, a
    assert comp[0]["record_purpose"] == "CALIBRATION_ONLY"
    assert comp[0]["us_market_slug"] == e.game.us_slug
    assert (a["received_at"] <= a["queued_at"] <= a["evaluation_started_at"]
            <= a["finished_at"]), a

    v = await _valuation(e, vids[0])
    assert v["provider"] == P.PROVIDER
    assert v["us_market_slug"] == e.game.us_slug
    ref = H.j(v["settlement_comparison"])["reference_input"]
    assert ref["provider"] == P.PROVIDER
    assert ref["evaluation_trigger"]["attempt_id"] == rows[0]["attempt_id"]
    assert ref["decision_check"]["ok"] is True, ref["decision_check"]
    assert ref["feed_event_id"] == e.eid
    assert ref["raw_odds"] == WS_EDGE
    assert v["probability"] is not None

    ds = _cg(await _decisions(e, vids))
    assert len(ds) == 1, await _decisions(e, vids)
    d = ds[0]
    assert d["valuation_id"] == vids[0]
    assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
    assert d["book_obs_id"] is not None
    assert e.venue.calls["paper"] >= 1 and e.venue.calls["book"] >= 2
    # THE LATENCY CHAIN, FROM RECORDED TIMESTAMPS: WS receipt -> worker
    # start -> persisted decision -> attempt finished; and the writer that
    # recorded the attempt is this process's feed owner
    decided = d["decided_at"].timestamp()
    assert (a["received_at"] <= a["evaluation_started_at"] <= decided
            <= a["finished_at"]), (a, decided)
    import os
    assert a["writer"]["pid"] == os.getpid(), a["writer"]
    assert a["writer"]["runtime_id"] == RUNTIME_ID, a["writer"]

    # paper only: no funded or inventory write; no provider fetch
    assert await _counts(e.conn) == funded_before
    assert len(e.fetches) == e.fetches_after_discovery
    assert R.ACTIVE.counts["COMPLETED"] == 1


@pg
async def test_the_ws_quotes_proved_prematch_label_is_its_recorded_context(
        env):
    """(RC6.2, p-coverage) THROUGH THE REAL CYCLE: the PinnAPI reader proved
    this quote's stream (prematch) against the event's own isLive flag, and
    the venue-native fixture has no condition key, so no fixture context
    exists. The entry lane now records the provider's label as the quote
    context (bettor_settlement_terms.book_context_for's authoritative
    source) instead of none. On b3f1b0cd quote_context was None and the
    settlement blockers named QUOTE_CONTEXT_NOT_ESTABLISHED."""
    e = env
    await _start_and_discover(e)
    tick(e.cache, e.eid, WS_EDGE)
    rows = await _wait_terminal(e)
    vids = await _priced(e, rows[0]["detail"]["valuation_ids"])
    v = await _valuation(e, vids[0])
    sc = H.j(v["settlement_comparison"])
    assert sc["reference_input"]["stream"] == "prematch"
    assert sc["quote_context"] == "PRE_GAME"
    assert sc["quote_context_basis"] == "PROVIDER_QUOTE_LABEL"
    assert sc["quote_provider_label"] == "PRE_GAME"
    assert sc["quote_context_refusal"] is None
    assert not any("QUOTE_CONTEXT_NOT_ESTABLISHED" in b
                   for b in sc.get("blockers") or [])


# ═════════════════════════════════════════════════════════════════════
# 1b · PINNAPI IS THE SOLE PROBABILITY AUTHORITY (V3, owner 2026-10-03)
# ═════════════════════════════════════════════════════════════════════

def _single_source(e):
    """The discovery payload carries Pinnacle ALONE, as production's usually
    does by the time a WS change arrives: the PinnAPI quote's own outcome
    count is 1 and nothing else is offered as a second book."""
    def only_pinnacle(now):
        ev = _inside_the_spread(VN.odds_event(e.game, "rx%d" % e.eid, at=now))
        ev["bookmakers"] = [b for b in ev["bookmakers"]
                            if b["key"] == "pinnacle"]
        return [ev]
    e.discovery = only_pinnacle


@pg
async def test_a_fresh_single_source_pinnapi_valuation_reaches_paper_enter(env):
    e = env
    _single_source(e)
    rows = await _one_reactive(e)
    assert [r["state"] for r in rows] == ["COMPLETED"], rows
    vids = await _priced(e, rows[0]["detail"]["valuation_ids"])
    assert len(vids) == 1, rows[0]["detail"]
    v = await _valuation(e, vids[0])
    # THE ROW STATES THE FACTS: PinnAPI, one book, the general floor unmet
    assert v["provider"] == P.PROVIDER and v["outcome_books"] == 1
    assert EXT.R_THIN_OUTCOME in [str(c) for c in (v["refusals"] or [])]
    ds = _cg(await _decisions(e, vids))
    assert len(ds) == 1, ds
    d = ds[0]
    assert d["verdict"] == "ENTER", (d["refusal"], d["refusals"])
    assert d["policy_version"] == PB.CG_VERSION
    auth = H.j(d["pinnacle"])["probability_authority"]
    assert auth["basis"] == PB.PINNAPI_SOLE and auth["outcome_books"] == 1
    assert auth["evidence"] == "SINGLE_SOURCE_PINNAPI"
    # execution still had to be established on the venue's own book
    econ = H.j(d["economics"])
    assert d["book_obs_id"] is not None and econ["proposed_qty"] > 0
    assert econ["book_age_s"] <= PB.BOOK_MAX_AGE_S
    assert await _enters_on_contract(e) == 1


@pg
async def test_a_stale_single_source_pinnapi_change_never_enters(env):
    e = env
    _single_source(e)
    await _start_and_discover(e)
    funded_before = await _counts(e.conn)
    # the provider's own change stamp is older than the Pinnacle limit
    tick(e.cache, e.eid, WS_EDGE, lag_s=loop.PINNACLE_MAX_AGE_S + 60.0)
    await asyncio.sleep(1.0)
    rows = await _attempts(e)
    if rows:
        rows = await _wait_terminal(e)
        vids = [vid for r in rows for vid in (r["detail"].get("valuation_ids") or [])]
        for d in _cg(await _decisions(e, vids)):
            assert d["verdict"] == "REFUSE", (d["refusal"], d["refusals"])
    else:                       # refused before an attempt: an unusable change
        assert R.ACTIVE.counts["UNUSABLE_CHANGE"] >= 1, dict(R.ACTIVE.counts)
    assert await _enters_on_contract(e) == 0
    assert await _counts(e.conn) == funded_before


@pg
async def test_a_mis_mapped_pinnapi_event_is_never_evaluated(env):
    e = env
    # the discovery names a DIFFERENT fixture's teams: no confirmed mapping
    e.discovery = lambda now: [VN.odds_event(e.game, "rx%d" % e.eid, at=now,
                                             home="Nowhere Nine",
                                             away="Elsewhere Eleven")]
    import asyncpg
    e.pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=4)
    e.task = R.start(e.pool, cycle=loop.cycle)
    assert e.task is not None
    out = await loop.cycle(e.conn)
    assert out.get("ran") is True, out.get("why")
    assert e.eid not in R.ACTIVE.seeds
    tick(e.cache, e.eid, WS_EDGE)
    await asyncio.sleep(1.0)
    assert await _attempts(e) == []
    assert R.ACTIVE.counts["NO_CONFIRMED_DISCOVERY"] >= 1, dict(R.ACTIVE.counts)
    assert await _enters_on_contract(e) == 0


# ═════════════════════════════════════════════════════════════════════
# 2 · REFUSALS ON THE REACTIVE PATH
# ═════════════════════════════════════════════════════════════════════

async def _one_reactive(e, *, prices=WS_EDGE):
    await _start_and_discover(e)
    funded_before = await _counts(e.conn)
    tick(e.cache, e.eid, prices)
    rows = await _wait_terminal(e)
    assert await _counts(e.conn) == funded_before
    assert len(e.fetches) == e.fetches_after_discovery
    return rows


@pg
async def test_a_stale_venue_book_never_enters(env):
    e = env
    e.venue.age_s = PB.BOOK_MAX_AGE_S + 50.0
    rows = await _one_reactive(e)
    a = rows[0]["detail"]
    assert rows[0]["state"] == "COMPLETED", rows
    ds = _cg(await _decisions(e, await _priced(e, a["valuation_ids"])))
    assert len(ds) == 1, ds
    assert ds[0]["verdict"] == "REFUSE"
    assert ds[0]["refusal"] == PB.R_BOOK_NOT_CURRENT, ds[0]["refusals"]
    econ = H.j(ds[0]["economics"])
    assert econ["book_age_s"] > PB.BOOK_MAX_AGE_S == econ["book_max_age_s"]
    assert econ["proposed_qty"] == 0
    assert await _enters_on_contract(e) == 0


@pg
async def test_insufficient_venue_depth_never_enters(env):
    e = env
    e.venue.bids = [(0.50, 0.4)]
    rows = await _one_reactive(e)
    a = rows[0]["detail"]
    ds = _cg(await _decisions(e, await _priced(e, a["valuation_ids"])))
    assert len(ds) == 1, ds
    assert ds[0]["verdict"] == "REFUSE"
    assert ds[0]["refusal"] == PB.R_NO_QTY, ds[0]["refusals"]
    econ = H.j(ds[0]["economics"])
    assert econ["book_age_s"] <= PB.BOOK_MAX_AGE_S         # fresh, but thin
    assert econ["depth_within_limit"] < 1 and econ["proposed_qty"] == 0
    assert await _enters_on_contract(e) == 0


@pg
async def test_an_adverse_executable_price_never_enters(env):
    e = env
    e.venue.bids = [(0.40, 400)]                  # home side now costs 0.60
    rows = await _one_reactive(e)
    a = rows[0]["detail"]
    ds = _cg(await _decisions(e, await _priced(e, a["valuation_ids"])))
    assert len(ds) == 1, ds
    assert ds[0]["verdict"] == "REFUSE"
    assert ds[0]["refusal"] == PB.R_EDGE, ds[0]["refusals"]
    econ = H.j(ds[0]["economics"])
    assert econ["book_age_s"] <= PB.BOOK_MAX_AGE_S
    assert econ["best_level_edge_pp"] < econ["threshold_edge_pp"]
    assert await _enters_on_contract(e) == 0


@pg
async def test_authority_revoked_after_notification_refuses_the_attempt(env):
    e = env
    await _start_and_discover(e)
    calls = dict(e.venue.calls)
    tick(e.cache, e.eid, WS_EDGE)
    assert len(R.ACTIVE.pending) == 1
    e.cache.lost("LEASE_LOST")                    # before the worker runs
    rows = await _wait_terminal(e)
    assert [r["state"] for r in rows] == ["REFUSED"], rows
    a = rows[0]["detail"]
    assert a["reason"] == "LEASE_LOST"
    assert "valuation_ids" not in a and "finished_at" not in a
    # no evaluation at all: no venue read, no paper read, no valuation
    assert e.venue.calls == calls
    assert await e.conn.fetchval(
        "SELECT count(*) FROM external_valuations WHERE us_market_slug=$1 "
        "   AND provider=$2", e.game.us_slug, P.PROVIDER) == 0
    assert await _enters_on_contract(e) == 0


def _on_the_loop(fn):
    """Run `fn` on the event loop from the collector's book-read thread and
    wait for it: the feed is only ever mutated on the loop, as in
    production (FeedCache.on_change -> Scheduler.changed sets an
    asyncio.Event)."""
    import threading
    aloop = asyncio.get_running_loop()

    def hook():
        done = threading.Event()

        def run():
            try:
                fn()
            finally:
                done.set()
        aloop.call_soon_threadsafe(run)
        assert done.wait(5)
    return hook


@pg
@pytest.mark.parametrize("change", ["revoked", "resynchronised"])
async def test_feed_epoch_changed_during_evaluation_removes_the_ws_probability(
        env, change):
    """The worker started on epoch 1; while the collector awaits the venue
    book, the lease is lost (revoked) or the socket reconnects and resyncs
    to the SAME prices on epoch 2. The re-validation after the awaited read
    (`validate_primary_pinnacle`) must refuse the epoch-1 inputs by name, and
    `stamp_record` must strip the probability, so the paper hook cannot
    enter on it."""
    e = env
    await _start_and_discover(e)
    fired = []

    def mutate():
        if fired:
            return
        fired.append(change)
        if change == "revoked":
            e.cache.lost("LEASE_LOST")
            return
        old = e.cache.events[e.eid]
        ep = e.cache.new_connection([("prematch", SPORT_ID)])
        now = time.time()
        e.cache.apply({"type": "snapshot", "stream": "prematch",
                       "sport_id": SPORT_ID, "ts": (now - 1) * 1000,
                       "events": [dict(old, markets=[_market(
                           {"home": 120, "away": -140})])]},
                      epoch=ep, received_ms=now * 1000 - 500)
        tick(e.cache, e.eid, WS_EDGE)             # same prices, epoch 2
    e.venue.on_book = _on_the_loop(mutate)
    tick(e.cache, e.eid, WS_EDGE)
    rows = await _wait_terminal(e, n=1 if change == "revoked" else 2)
    assert fired == [change]
    first = rows[0]
    assert first["state"] == "COMPLETED", rows
    assert first["detail"]["version"][0] == 1      # epoch-1 trigger
    vids = await _priced(e, first["detail"]["valuation_ids"])
    assert len(vids) == 1, first
    v = await _valuation(e, vids[0])
    ref = H.j(v["settlement_comparison"])["reference_input"]
    why = ("LEASE_LOST" if change == "revoked"
           else "PINNAPI_PRIMARY_INPUT_CHANGED")
    chk = ref["decision_check"]
    assert chk["ok"] is False and chk["reason"] == why, chk
    assert ref["evaluation_trigger"]["attempt_id"] == first["attempt_id"]
    assert v["probability"] is None and v["admissible"] is False
    assert v["refusals"][0] == why
    ds = _cg(await _decisions(e, vids))
    assert len(ds) == 1 and ds[0]["verdict"] != "ENTER", ds
    if change == "revoked":
        assert len(rows) == 1
        assert await _enters_on_contract(e) == 0
    else:
        # the epoch-2 change is a NEW version and gets its OWN attempt, on
        # epoch 2 -- an evaluation is never silently re-pointed at it
        second = rows[1]
        assert second["detail"]["version"][0] == 2, second
        assert second["attempt_id"] != first["attempt_id"]


# ═════════════════════════════════════════════════════════════════════
# 3 · DUPLICATES AND BURSTS
# ═════════════════════════════════════════════════════════════════════

@pg
async def test_identical_frames_make_one_attempt(env):
    e = env
    await _start_and_discover(e)
    tick(e.cache, e.eid, WS_EDGE)
    # the same frame re-sent (same provider stamp, same prices)
    q = e.cache.quotes[(e.eid, F.FULL_GAME_MONEYLINE_KEY)]
    e.cache.apply({"type": "prematch_markets", "matchup_id": e.eid,
                   "data": [_market(WS_EDGE)], "sport_id": SPORT_ID,
                   "ts": q.frame_ts_ms}, epoch=e.cache.authority.epoch,
                  received_ms=time.time() * 1000)
    assert R.ACTIVE.counts["UNCHANGED"] == 1
    assert len(R.ACTIVE.pending) == 1
    await _wait_terminal(e)
    # and once more AFTER the evaluation: still the evaluated version
    e.cache.apply({"type": "prematch_markets", "matchup_id": e.eid,
                   "data": [_market(WS_EDGE)], "sport_id": SPORT_ID,
                   "ts": q.frame_ts_ms}, epoch=e.cache.authority.epoch,
                  received_ms=time.time() * 1000)
    assert R.ACTIVE.counts["UNCHANGED"] == 2
    assert not R.ACTIVE.pending
    await asyncio.sleep(0.5)
    rows = await _attempts(e)
    assert [r["state"] for r in rows] == ["COMPLETED"], rows
    assert len(await _priced(e, rows[0]["detail"]["valuation_ids"])) == 1
    assert await _enters_on_contract(e) == 1


async def _entry_orders(e) -> list:
    return [dict(r) for r in await e.conn.fetch(
        "SELECT order_id, decision_id, state FROM paper_orders "
        " WHERE account_id=$1 AND us_market_slug=$2 AND role='ENTRY'",
        e.acct["account_id"], e.game.us_slug)]


async def _burst_then_one_more(e):
    await _start_and_discover(e)
    for home in (-140, -145, -150):
        tick(e.cache, e.eid, {"home": home, "away": 130})
    assert len(R.ACTIVE.pending) == 1
    assert R.ACTIVE.counts["COALESCED"] == 2
    rows = await _wait_terminal(e)
    assert len(rows) == 1, rows
    v = await _valuation(e, (await _priced(e, rows[0]["detail"]["valuation_ids"]))[0])
    ref = H.j(v["settlement_comparison"])["reference_input"]
    assert ref["raw_odds"] == {"home": -150, "away": 130}   # the newest
    assert rows[0]["detail"]["version"][2] == [["away", 130], ["home", -150]]
    assert await _enters_on_contract(e) == 1
    assert len(await _entry_orders(e)) == 1
    # one more drained change: its own attempt and its own valuation
    tick(e.cache, e.eid, {"home": -155, "away": 130})
    rows = await _wait_terminal(e, n=2)
    assert len(rows) == 2 and rows[0]["attempt_id"] != rows[1]["attempt_id"]
    assert all(r["state"] == "COMPLETED" for r in rows), rows
    second = await _priced(e, rows[1]["detail"]["valuation_ids"])
    assert len(second) == 1 and second != await _priced(
        e, rows[0]["detail"]["valuation_ids"])
    return rows


def _owner_policy_on_scratch(monkeypatch, e):
    """THE PRODUCTION CAPITAL POLICY (bettor_paper_limits.uses_owner_policy:
    `paper_acct_main` only, the account production's reactive path decides
    on -- paper_runtime.DEFAULT_ACCOUNT_ID). No test may trade on that
    account, so its predicate is extended to THIS scratch account; nothing
    else about the policy is changed and no other account is broadened."""
    from sportsassets import bettor_paper_limits as LIMITS
    real = LIMITS.uses_owner_policy
    monkeypatch.setattr(LIMITS, "uses_owner_policy",
                        lambda a: a == e.acct["account_id"] or real(a))


async def _finding(e, decision_id):
    return await e.conn.fetchrow(
        "SELECT kind, detail FROM paper_audrey_findings "
        " WHERE session_id=$1 AND subject=$2",
        e.acct["session_id"], decision_id)


@pg
async def test_a_held_contract_under_the_owner_policy_records_a_named_refusal_not_a_second_enter(
        env, monkeypatch):
    """THE DUPLICATE-ENTER CASE, UNDER THE PRODUCTION ACCOUNT POLICY. Before
    the decision-level read (candidate 14 9c89f55), the second drained change
    recorded an ENTER decision whose order the account lock refused
    (Audrey PAPER_RISK_REFUSED_THE_ORDER / THIS_STRATEGY_ALREADY_HOLDS_THIS_
    CONTRACT): a recommendation that could never become an order, counted as
    ENTER activity. Now the decision itself is the named REFUSE; exactly one
    ENTER, one ENTRY order, and no refused-order finding on the contract."""
    from sportsassets import bettor_paper_ledger as L
    e = env
    _owner_policy_on_scratch(monkeypatch, e)
    rows = await _burst_then_one_more(e)
    orders = await _entry_orders(e)
    assert len(orders) == 1, orders
    d1 = _cg(await _decisions(e, await _priced(e, rows[0]["detail"]["valuation_ids"])))
    d2 = _cg(await _decisions(e, await _priced(e, rows[1]["detail"]["valuation_ids"])))
    assert [d["verdict"] for d in d1] == ["ENTER"], d1
    assert orders[0]["decision_id"] == d1[0]["decision_id"]
    assert len(d2) == 1
    assert d2[0]["verdict"] == "REFUSE", d2
    assert d2[0]["refusal"] == L.R_SAME_CONTRACT_HELD, d2
    assert await _finding(e, d2[0]["decision_id"]) is None
    assert await _enters_on_contract(e) == 1
    from sportsassets import bettor_paper_ops as OPS
    split = await OPS.enter_outcomes(e.conn, e.acct["account_id"],
                                     PB.CG_STRATEGY, 1)
    assert split == {"enter_recommended": 1, "enter_order_accepted": 1,
                     "enter_filled": 0, "enter_without_order": 0}, split


@pg
async def test_a_burst_coalesces_and_a_held_contract_gets_no_second_entry_order(
        env, monkeypatch):
    """THE ACCOUNT-LOCK GUARD STAYS THE AUTHORITY. The decision-level read is
    made to miss (as in a race where the first order lands between that read
    and the lock): the decision is an ENTER, and submit_order -- under the
    account lock, with the production same-contract rule -- refuses its
    order. Still exactly one ENTRY order; the refused submission is an
    Audrey finding naming the rule, never an order or a fill."""
    import inspect
    from sportsassets import bettor_paper_ledger as L
    e = env
    _owner_policy_on_scratch(monkeypatch, e)
    real_held = L.same_contract_held
    misses = []

    async def racing_read(*a, **kw):
        if inspect.stack()[1].function == "decide_one":
            misses.append(a)
            return []
        return await real_held(*a, **kw)
    monkeypatch.setattr(L, "same_contract_held", racing_read)
    rows = await _burst_then_one_more(e)
    assert misses, "the decision-level read was not reached"
    orders = await _entry_orders(e)
    assert len(orders) == 1, orders
    d2 = _cg(await _decisions(e, await _priced(e, rows[1]["detail"]["valuation_ids"])))
    assert len(d2) == 1 and d2[0]["verdict"] == "ENTER", d2
    assert not [o for o in orders if o["decision_id"] == d2[0]["decision_id"]]
    f = await _finding(e, d2[0]["decision_id"])
    assert f is not None and f["kind"] == PD.R_ORDER_REFUSED, f
    assert H.j(f["detail"])["refusal"] == L.R_SAME_CONTRACT_HELD
    assert H.j(f["detail"])["under_lock"] is True
    fills = await e.conn.fetchval(
        "SELECT count(*) FROM paper_fills f JOIN paper_orders o "
        "  ON o.order_id = f.order_id WHERE o.decision_id = $1",
        d2[0]["decision_id"])
    assert fills == 0
    # the status readers keep the recommendation, the accepted order and the
    # fill apart: the refused submission is counted as an ENTER without an
    # order, never as an order or a fill
    from sportsassets import bettor_paper_ops as OPS
    enters = await e.conn.fetchval(
        "SELECT count(*) FROM paper_decisions WHERE account_id=$1 "
        "   AND strategy=$2 AND verdict='ENTER'", e.acct["account_id"],
        PB.CG_STRATEGY)
    assert enters == 2
    split = await OPS.enter_outcomes(e.conn, e.acct["account_id"],
                                     PB.CG_STRATEGY, enters)
    assert split == {"enter_recommended": 2, "enter_order_accepted": 1,
                     "enter_filled": 0, "enter_without_order": 1}, split


# ═════════════════════════════════════════════════════════════════════
# 4 · THE SWITCH
# ═════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("value", [None, "off", ""])
async def test_switch_off_starts_nothing(monkeypatch, value):
    cache = F.FeedCache()
    monkeypatch.setitem(FR._STATE, "owner", types.SimpleNamespace(cache=cache))
    monkeypatch.setattr(R, "ACTIVE", None)
    monkeypatch.setenv(S.ENV_FLAG, "on")
    if value is None:
        monkeypatch.delenv("PINNAPI_REACTIVE_PAPER", raising=False)
    else:
        monkeypatch.setenv("PINNAPI_REACTIVE_PAPER", value)
    pool = types.SimpleNamespace(acquire=None)

    async def cycle(conn, *, stream_seed=None):
        raise AssertionError("never called")
    assert R.start(pool, cycle=cycle) is None
    assert cache.on_change is None
    assert R.ACTIVE is None
    R.register({"id": "x"}, sport_key="baseball_mlb", family="baseball",
               received_at=time.time())          # a no-op, never raises
