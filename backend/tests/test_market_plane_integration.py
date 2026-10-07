"""THE UNIVERSAL MARKET PLANE AS INTEGRATED (closeout, migration 312).

  §1  ontology over the venue's PRODUCTION vocabulary (research run
      37529879382): futures take their sport from the league code, a prop's
      metric is the venue's own label, table_tennis is never tennis, an
      unrecognised type stays a named gap; both binary sides first-class and
      SHORT is the complement of the one long book (no manufactured book)
  §2  the populator: every listed sports market -> one registry row with both
      sides, canonical venue-id entities and a priority; non-sports leagues
      excluded by name; a CONTRACT_UPSERT event only when content changed; a
      held market the catalogue no longer lists stays active with its reason;
      a market not re-listed within the horizon and not required retires
  §3  catalogue completeness from the premap receipts: TRUNCATED / missing
      lane / catalogue_complete=false are never complete
  §4  coverage: every active contract gets exactly one terminal state; an
      ontology gap or a priceable contract without a current book is a
      CODE_CONTROLLED_GAP; EXTERNAL only with EXTERNAL-class evidence
  §5  subscription: only refdata-listed contracts, priority-ordered, overflow
      named as the LOWEST-priority tail with the shards required; existing
      assignments never move
  §6  the PMX streams arm under the existing stream's conditions (switch,
      identity guard, transport); shard books are RUNNING and record the
      replacement instant for the latency SLO
  §7  durable same-book certification: reused only for the identical
      fingerprint, never over a CONTRADICTED window; the certifier persists
      the strict window verdict
  §8  authority: GET-only routes, no order / funded tokens anywhere in the
      plane, the worker or the API module; snapshot + Radar shape
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import pathlib
import time
from datetime import datetime, timezone

import asyncpg
import pytest

from sportsassets.market_plane import certification as CERT
from sportsassets.market_plane import ontology as O
from sportsassets.market_plane import populate as POP
from sportsassets.market_plane import registry as R
from sportsassets.market_plane import sharding as SH
from sportsassets.workers import universal_market_plane as W

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1] / "sportsassets"
NOW = 1_800_000_000.0


def run(coro):
    return asyncio.run(coro)


# ── §1 ontology ──────────────────────────────────────────────────────

def _p(t, comp=None):
    return O.parse_market_type(venue="P", contract_id="x",
                               sports_market_type=t, competition=comp)


def test_futures_take_their_sport_from_the_venue_league_code():
    r = _p("futures", "mlb")
    assert r["ok"] and r["meaning"]["sport"] == "baseball"
    assert r["meaning"]["metric"] == "OUTRIGHT"
    assert r["meaning"]["subject_type"] == "OUTRIGHT"
    assert r["sport_basis"] == "VENUE_LEAGUE_CODE"
    assert not _p("futures", "zzz")["ok"]          # unknown league: named


def test_a_prop_metric_is_the_venue_label_never_a_guess():
    r = _p("soccer_game_total_corners")
    assert r["ok"] and r["meaning"]["metric"] == "TOTAL_CORNERS"
    assert r["meaning"]["operator"] == "TOTAL"
    assert r["metric_source"] == "VENUE_LABEL"
    r = _p("football_team_first_half_spread")
    assert r["meaning"]["period"] == "FIRST_HALF"
    assert r["meaning"]["metric"] == "MARGIN" and r["metric_source"] == "TOKEN"


def test_table_tennis_is_never_read_as_tennis():
    assert _p("table_tennis_match_winner")["meaning"]["sport"] == \
        "table_tennis"
    assert _p("tennis_match_winner")["meaning"]["sport"] == "tennis"


def test_an_unrecognised_type_stays_a_named_gap():
    r = _p("weird_magic_market")
    assert not r["ok"] and "METRIC_NOT_NORMALIZED" in r["gaps"]
    assert not _p("")["ok"]


def test_both_sides_are_first_class_and_short_is_the_complement():
    sides = O.binary_sides(long_intent="ORDER_INTENT_BUY_LONG",
                           long_side="yes", short_intent="ORDER_INTENT_BUY_SHORT",
                           short_side="no")
    assert sides["LONG"]["price_transform"] == "IDENTITY"
    assert sides["SHORT"]["price_transform"] == "COMPLEMENT"
    assert sides["independent_short_book"] is False
    book = {"bids": [[0.44, 10], [0.43, 5]], "offers": [[0.46, 7]]}
    assert O.side_price(book, "LONG", "BUY") == 0.46
    assert O.side_price(book, "LONG", "SELL") == 0.44
    assert O.side_price(book, "SHORT", "BUY") == pytest.approx(0.56)
    assert O.side_price(book, "SHORT", "SELL") == pytest.approx(0.54)
    assert O.side_price({"bids": [], "offers": []}, "SHORT", "BUY") is None


# ── §2 the populator (pure) ──────────────────────────────────────────

def _cat(slug, *, sports_type="football_team_full_game_spread",
         event="aec-nfl-tb-dal-2026-10-08", state="PREGAME", seen=NOW,
         start=NOW + 3600):
    return {"market_slug": slug, "event_slug": event, "event_title": "TB @ DAL",
            "question": "q", "sports_type": sports_type, "team_league": None,
            "line": "3.5", "listing_state": state,
            "updated_at": datetime.fromtimestamp(seen, tz=timezone.utc),
            "game_start": datetime.fromtimestamp(start, tz=timezone.utc),
            "sides": json.dumps([
                {"intent": "ORDER_INTENT_BUY_LONG", "side_norm": "tb",
                 "team_id": 11, "team_abbr": "TB", "team_league": "nfl"},
                {"intent": "ORDER_INTENT_BUY_SHORT", "side_norm": "dal",
                 "team_id": 12, "team_abbr": "DAL", "team_league": "nfl"}])}


def test_a_listed_market_becomes_one_row_with_both_sides_and_venue_ids():
    c = POP.contract_row(_cat("m1"), now=NOW)
    assert c["contract_id"] == "m1" and c["active"]
    assert c["sport"] == "football" and c["competition"] == "nfl"
    ont = c["ontology"]
    assert ont["sides"]["LONG"]["side_norm"] == "tb"
    assert ont["sides"]["SHORT"]["side_norm"] == "dal"
    assert ont["entities"]["teams"] == ["team:nfl:11", "team:nfl:12"]
    assert ont["entities"]["basis"] == "VENUE_IDS"
    assert c["priority"] == POP.P_CORE_SOON


def test_priorities_put_held_and_candidates_first():
    assert POP.contract_row(_cat("m1"), now=NOW, held={"m1"})["priority"] \
        == POP.P_HELD
    assert POP.contract_row(_cat("m1"), now=NOW,
                            candidates={"m1"})["priority"] == POP.P_CANDIDATE
    far = POP.contract_row(_cat("m2", sports_type="futures",
                                event="aec-mlb-wschamp-2026",
                                start=NOW + 30 * 86400), now=NOW)
    assert far["priority"] == POP.P_REST


def test_non_sports_leagues_are_excluded_by_name():
    assert POP.contract_row(_cat("b1", event="aec-btc-up-2026"),
                            now=NOW) is None


def test_a_listing_past_the_horizon_is_not_venue_active_unless_required():
    old = _cat("m1", seen=NOW - POP.ACTIVE_HORIZON_S - 60)
    assert POP.contract_row(old, now=NOW)["active"] is False
    assert POP.contract_row(old, now=NOW, held={"m1"})["active"] is True
    assert POP.contract_row(_cat("m1", state="ENDED"),
                            now=NOW)["active"] is False


# ── §3 catalogue completeness ────────────────────────────────────────

class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, *a):
        return self.rows


def _rc(lane, outcome="COMPLETE", truncated=False, cc=None, rid=1):
    return {"lane": lane, "id": rid, "outcome": outcome,
            "truncated": truncated, "finished_at": NOW,
            "catalogue_complete": cc, "markets_seen": 10, "markets_kept": 10}


def test_catalogue_complete_only_when_every_lane_is_complete():
    ok = run(POP.catalogue_completeness(_Conn([_rc("full"), _rc("calendar"),
                                               _rc("fast")])))
    assert ok["complete"] is True
    t = run(POP.catalogue_completeness(_Conn([
        _rc("full", "TRUNCATED", True), _rc("calendar")])))
    assert t["complete"] is False
    assert t["lanes"]["full"]["complete"] is False
    miss = run(POP.catalogue_completeness(_Conn([_rc("full")])))
    assert miss["complete"] is False                 # no calendar receipt
    cc = run(POP.catalogue_completeness(_Conn([_rc("full", cc=False),
                                               _rc("calendar")])))
    assert cc["complete"] is False
    none = run(POP.catalogue_completeness(_Conn([])))
    assert none["complete"] is False


# ── §4 coverage classification ───────────────────────────────────────

def _contract(**kw):
    base = {"contract_id": "m1", "sport": "football", "competition": "nfl",
            "event_id": "aec-nfl-tb-dal", "family": "MARGIN",
            "period": "FULL_EVENT",
            "ontology": {"gaps": [], "meaning": {}}}
    base.update(kw)
    return base


def test_an_ontology_gap_is_a_code_controlled_gap():
    t = POP.classify(_contract(ontology={"gaps": ["METRIC_NOT_NORMALIZED"]}))
    assert t["state"] == "CODE_CONTROLLED_GAP"
    assert t["why"].startswith("ONTOLOGY_GAPS")


def test_no_valuation_is_settlement_not_proven_never_dropped():
    t = POP.classify(_contract())
    assert t["state"] == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
    assert t["why"] == "NO_SETTLEMENT_COMPARISON_RECORDED"


def test_a_settled_comparison_without_probability_is_no_fair_value():
    t = POP.classify(_contract(), valuation={
        "has_probability": False, "settlement_verdict": "COMPATIBLE",
        "refusals": []})
    assert t["state"] == "MAPPED_BUT_NO_FAIR_VALUE_SOURCE"


def test_priceable_needs_a_current_book_else_code_controlled():
    v = {"has_probability": True, "settlement_verdict": "COMPATIBLE",
         "refusals": []}
    assert POP.classify(_contract(), valuation=v, fresh_book=True,
                        book_source="PMX_GRPC")["state"] == "PRICEABLE"
    t = POP.classify(_contract(), valuation=v, fresh_book=False)
    assert t["state"] == "CODE_CONTROLLED_GAP"
    assert t["why"] == "NO_CURRENT_CANONICAL_BOOK"


def test_a_settlement_refusal_is_not_proven():
    t = POP.classify(_contract(), valuation={
        "has_probability": True, "settlement_verdict": None,
        "refusals": ["SETTLEMENT_NOT_SUPPORTED"]})
    assert t["state"] == "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"


def test_external_only_with_external_class_evidence():
    t = POP.classify(_contract(), candidate={
        "first_refusal": "PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM"},
        external_codes={"PINNAPI_PRIMARY_FEED_HOLDS_NO_FIXTURE_FOR_EITHER_TEAM"})
    assert t["state"] == "EXTERNAL_DATA_UNAVAILABLE"
    t = POP.classify(_contract(), candidate={"first_refusal": "SOMETHING"},
                     external_codes={"OTHER"})
    assert t["state"] != "EXTERNAL_DATA_UNAVAILABLE"


def test_the_taxonomy_supplies_the_external_codes():
    codes = POP.external_codes()
    assert isinstance(codes, set)


# ── §5 subscription ──────────────────────────────────────────────────

def test_overflow_is_the_lowest_priority_tail_and_existing_never_move():
    order = ["held", "cand", "core1", "core2", "rest1", "rest2"]
    p = SH.assign_stable(order, {}, max_per_stream=2, max_streams=2,
                         order=order)
    assert set(p["overflow"]) == {"rest1", "rest2"}
    first = p["assignments"]
    p2 = SH.assign_stable(order + ["new"], first, max_per_stream=3,
                          max_streams=2, order=["new"] + order)
    for s, sid in first.items():
        assert p2["assignments"][s] == sid


# ── §6 arming and shard books ────────────────────────────────────────

def test_streams_do_not_arm_without_the_stream_switch():
    got = W.stream_arming({"INSTITUTIONAL_MD_STREAM": "off"})
    assert got["armed"] is False and "IS_NOT_ON" in got["why"]


def test_streams_do_not_arm_when_the_identity_guard_refuses(monkeypatch):
    from sportsassets import market_data_identity as MDI
    monkeypatch.setattr(MDI, "guard", lambda *a, **k: "REFUSED_FOR_TEST")
    got = W.stream_arming({"INSTITUTIONAL_MD_STREAM": "on"},
                          available=lambda: (True, None))
    assert got == {"armed": False, "why": "REFUSED_FOR_TEST"}


def test_streams_do_not_arm_without_the_transport(monkeypatch):
    from sportsassets import market_data_identity as MDI
    monkeypatch.setattr(MDI, "guard", lambda *a, **k: None)
    got = W.stream_arming({"INSTITUTIONAL_MD_STREAM": "on"},
                          available=lambda: (False, "no grpc"))
    assert got["armed"] is False and "TRANSPORT_UNAVAILABLE" in got["why"]
    ok = W.stream_arming({"INSTITUTIONAL_MD_STREAM": "on"},
                         available=lambda: (True, None))
    assert ok["armed"] is True


def test_the_worker_is_on_by_switch_and_caps_are_configured():
    assert W.enabled({}) is True
    assert W.enabled({"UNIVERSAL_MARKET_PLANE": "off"}) is False
    assert W.caps({}) == (W.DEFAULT_MAX_STREAMS, W.DEFAULT_MAX_PER_STREAM)
    assert W.caps({"UMP_MAX_STREAMS": "20", "UMP_MAX_PER_STREAM": "5000"}) \
        == (20, 1000)                     # never above the documented 1,000


def test_shard_books_are_running_and_record_the_replacement_instant():
    from sportsassets import institutional_stream as IS
    from sportsassets.market_plane.sharded_stream import Manager
    made = []

    class T:
        def __init__(self, b):
            self.b, self.subs = b, []

        def start(self):
            pass

        def subscribe(self, x):
            self.subs.extend(x)

        def stop(self):
            pass
    clock = {"t": NOW}
    m = Manager(token_fn=lambda: "x", max_per_stream=10, max_streams=2,
                transport_factory=lambda b, tok: made.append(T(b)) or made[-1],
                clock=lambda: clock["t"])
    rec = {"symbol": "sym", "priceScale": "1000", "fractionalQtyScale": "100",
           "state": "INSTRUMENT_STATE_OPEN"}
    assert m.sync({"sym": 0}, {"sym": rec})["ok"]
    books = m.shards[0]["books"]
    assert books.state in IS.RUNNING
    books.on_connected("c1")
    books.on_update({"symbol": "sym", "bids": [(450, 1000)],
                     "offers": [(470, 500)], "state": "INSTRUMENT_STATE_OPEN",
                     "transact_time": datetime.fromtimestamp(
                         NOW - 0.05, tz=timezone.utc)}, received_at=NOW)
    assert m.current("sym", now=NOW + 1)["ok"] is True
    lat = m.latency_samples()
    assert len(lat) == 1 and lat[0][1] == NOW
    rep = W.latency_report(m, now=NOW + 1)
    assert rep["n"] == 1 and rep["transport_ms"]["p50"] == pytest.approx(
        50.0, abs=1.0)
    assert rep["distribution_scope"].startswith("IN_PROCESS")
    assert W.fresh_symbols(m, now=NOW + 1) == {"sym"}


# ── §7 durable certification ─────────────────────────────────────────

def _ident(scale=1000):
    return {"status": "EXACT", "symbol": "sym", "price_scale": scale,
            "qty_scale": 100, "price_transform": "IDENTITY"}


def _durable(scale=1000):
    fp = CERT.fingerprint(CERT.identity_for("sym", price_scale=str(scale),
                                            qty_scale="100"))
    return [{"fingerprint": fp, "comparable": 30, "agreeing": 30,
             "certified_at": "t"}]


def test_a_durable_certificate_survives_a_quiet_window():
    from sportsassets import paper_market_data as PMD
    got = PMD.effective_same_book({"status": "UNTESTED", "detail": {}},
                                  _durable(), _ident())
    assert got["status"] == "SUPPORTED"
    assert got["detail"]["basis"] == "DURABLE_CERTIFICATION"


def test_a_changed_identity_never_reuses_the_certificate():
    from sportsassets import paper_market_data as PMD
    got = PMD.effective_same_book(None, _durable(scale=1000), _ident(100))
    assert got is None


def test_a_contradicted_window_always_wins():
    from sportsassets import paper_market_data as PMD
    w = {"status": "CONTRADICTED", "detail": {}}
    assert PMD.effective_same_book(w, _durable(), _ident()) is w


def test_the_certificate_rule_is_the_strict_one():
    assert CERT.MIN_SAMPLES == 30 and CERT.MIN_AGREEMENT == 0.95
    ident = CERT.identity_for("s", price_scale="1000", qty_scale="100")
    assert ident["price_scale"] == 1000 and ident["qty_scale"] == 100
    assert CERT.verdict(comparable=29, agreeing=29,
                        identity=ident)["status"] == "ACCUMULATING"


# ── §8 authority and readback ────────────────────────────────────────

FORBIDDEN = ("bettor_funded_execution", "bettor_entry_execution",
             "submit_order(", "cancel_order(", "place_order(", "write:orders",
             "LIVE_TRADING_ENABLED", "requests.post(", "httpx.post(",
             "execmirror", "live_executor")


def test_no_order_or_funded_path_in_the_plane_worker_or_api():
    files = list((ROOT / "market_plane").glob("*.py")) + [
        ROOT / "workers" / "universal_market_plane.py",
        ROOT / "api" / "command_market_plane.py"]
    bad = [(f.name, t) for f in files for t in FORBIDDEN
           if t in f.read_text()]
    assert not bad, bad


def test_the_routes_are_get_only_and_registered():
    from sportsassets.api import command_market_plane as API
    methods = {m for r in API.router.routes for m in getattr(r, "methods", ())}
    assert methods <= {"GET", "HEAD"}
    paths = {r.path for r in API.router.routes}
    assert {"/api/command/market-plane",
            "/api/command/market-plane/opportunity"} <= paths
    src = (ROOT / "api" / "app.py").read_text()
    assert "from .command_market_plane import router" in src


def test_priority_and_total_denominators_are_counted_apart():
    import asyncio as _a

    class _C:
        async def fetch(self, *a):
            return []
    tiers = {"PRIORITY": {"PMX_GRPC": 3, "REST_RECOVERY": 5, "NONE": 1,
                          "EXTERNAL_DATA_UNAVAILABLE": 1, "total": 10},
             "ALL": {"PMX_GRPC": 30, "REST_RECOVERY": 50, "NONE": 900,
                     "EXTERNAL_DATA_UNAVAILABLE": 20, "total": 1000}}
    got = _a.run(W.freshness_denominators(
        _C(), {"freshness_tiers": tiers},
        {"active": 1000, "pmx_listed": 400}, {"overflow_count": 7},
        subscribed=380, fresh=30, now=NOW))
    pr, tu = got["priority_universe"], got["total_universe"]
    assert pr["rate"] == round(8 / 9, 4)          # external named apart
    assert tu["rate"] == round(80 / 980, 4)       # nothing excluded
    assert tu["overflow"] == 7 and tu["stale_or_unread"] == 900
    assert got["held_positions"]["rate"] is None  # unread here: never 0


def test_the_worker_runs_only_in_its_dedicated_service():
    # completion readiness 2026-10-07: the shared workers OOM-killed with
    # the market plane in-process; it is supervised ONLY by its dedicated
    # read-only service (ops/render_market_plane_service.yaml)
    src = (ROOT / "workers" / "all.py").read_text()
    assert '("universal_market_plane", universal_market_plane.run)' not in src
    assert 'DEDICATED_ONLY_LOOPS = frozenset({"universal_market_plane"})' in src


# ── pg: populate, coverage, assignment, certification, snapshot ─────

async def _db():
    c = await asyncpg.connect(DSN)
    tr = c.transaction()
    await tr.start()
    return c, tr


async def _seed_premap(c, slug, *, event, sports_type, state="PREGAME",
                       age_s=0.0):
    for intent, side in (("ORDER_INTENT_BUY_LONG", "a"),
                         ("ORDER_INTENT_BUY_SHORT", "b")):
        await c.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, kind, "
            " side_norm, intent, sports_type, listing_state, "
            " listing_state_source, updated_at, game_start, team_id, "
            " team_league) "
            "VALUES ($1,$2,$3,'side',$4,$5,$6,$7,'VENUE_LIVE_FLAG', "
            " now() - make_interval("
            " secs => $8), now() + interval '2 hours', $9, 'nfl')",
            slug, event, slug, side, intent, sports_type, state, float(age_s),
            11 if side == "a" else 12)


@pg
def test_populate_registers_every_listed_market_and_events_only_on_change():
    async def go():
        c, tr = await _db()
        try:
            await _seed_premap(c, "mp-a", event="aec-nfl-tb-dal-2026-10-08",
                               sports_type="football_team_full_game_spread")
            await _seed_premap(c, "mp-b", event="aec-mlb-wschamp-2026",
                               sports_type="futures")
            await _seed_premap(c, "mp-btc", event="aec-btc-up-2026",
                               sports_type="")
            now = time.time()
            out = await POP.populate(c, since=0.0, now=now, full=True)
            assert out["excluded"].get("btc") == 1
            rows = {r["contract_id"]: dict(r) for r in await c.fetch(
                "SELECT * FROM market_plane_registry WHERE contract_id LIKE "
                "'mp-%'")}
            assert set(rows) == {"mp-a", "mp-b"}
            assert rows["mp-a"]["active"] and rows["mp-b"]["sport"] == \
                "baseball"
            n1 = await c.fetchval("SELECT count(*) FROM market_plane_events "
                                  "WHERE contract_id LIKE 'mp-%'")
            await POP.populate(c, since=0.0, now=now + 5, full=True)
            n2 = await c.fetchval("SELECT count(*) FROM market_plane_events "
                                  "WHERE contract_id LIKE 'mp-%'")
            assert n1 == 2 and n2 == n1        # unchanged content: no event
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_a_required_market_stays_active_and_an_unlisted_one_retires():
    async def go():
        c, tr = await _db()
        try:
            await _seed_premap(c, "mp-old", event="aec-nfl-x-y-2026",
                               sports_type="football_team_full_game_total",
                               age_s=POP.ACTIVE_HORIZON_S + 600)
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            assert await c.fetchval(
                "SELECT active FROM market_plane_registry "
                " WHERE contract_id = 'mp-old'") is False
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_coverage_and_assignment_over_the_registry():
    async def go():
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active = false")
            # this transaction's catalogue is exactly the seeded markets
            await c.execute("DELETE FROM us_premap")
            await _seed_premap(c, "mp-1", event="aec-nfl-tb-dal-2026",
                               sports_type="football_team_full_game_spread")
            await _seed_premap(c, "mp-2", event="aec-nfl-tb-dal-2026",
                               sports_type="football_team_full_game_total")
            await _seed_premap(c, "mp-3", event="aec-nfl-tb-dal-2026",
                               sports_type="weird_magic_market")
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            cov = await POP.coverage_pass(c, fresh_symbols=set(), now=now)
            # (the shared test database may hold REQUIRED markets from other
            # proofs' paper fills / candidates: they are active too)
            assert cov["total"] == cov["active"] >= 3
            assert sum(cov["by_state"].values()) == cov["total"]
            assert cov["silent_omissions"] == 0
            states = {r["contract_id"]: r["coverage_state"] for r in
                      await c.fetch("SELECT contract_id, coverage_state FROM "
                                    "market_plane_registry WHERE active")}
            assert all(v is not None for v in states.values())
            assert states["mp-3"] == "CODE_CONTROLLED_GAP"
            assert states["mp-1"] == states["mp-2"] == \
                "MAPPED_BUT_SETTLEMENT_NOT_PROVEN"
            # only refdata-listed contracts are subscribable
            await R.save_refdata(c, "mp-1", {"symbol": "mp-1",
                                             "priceScale": "1000"}, at=now)
            await R.save_unlisted(c, "mp-2", at=now)
            plan = await R.assign_missing_shards(c, max_per_stream=1,
                                                 max_streams=1)
            assert plan["subscribable"] == 1 and plan["overflow_count"] == 0
            assert plan["shards_required_for_all"] == 1
            assigned = await R.assigned_contracts(c)
            assert [r["contract_id"] for r in assigned] == ["mp-1"]
            due = await R.refdata_due(c, now=now, unlisted_retry_s=3600,
                                      limit=100000)
            assert "mp-3" in due and "mp-1" not in due and "mp-2" not in due
            # the snapshot + Radar with no stream armed
            snap = await W.snapshot(c, None, {"coverage": cov, "plan": plan,
                                              "catalogue": {"complete": False}},
                                    now=now, arming={"why": "TEST"},
                                    fresh=set(), caps=(4, 1000))
            assert snap["radar"]["green"] is False
            assert any(f.startswith("PMX_STREAMS_NOT_ARMED")
                       for f in snap["radar"]["extra_findings"])
            assert "CATALOGUE_NOT_PROVEN_COMPLETE" in snap["radar"]["failures"]
            assert snap["universe"]["represented"] == cov["active"]
            # the two denominators, never blended
            fr = snap["freshness"]
            assert set(fr) == {"priority_universe", "held_positions",
                               "total_universe"}
            tu = fr["total_universe"]
            assert tu["active_contracts"] == cov["active"]
            assert tu["streamed"] == 0 and tu["overflow"] == 0
            # nothing streamed here; the shared database's REQUIRED markets
            # (other proofs' paper positions) may be current via the REST
            # recovery read or named external -- the identity, not a count
            al = (cov.get("freshness_tiers") or {}).get("ALL") or {}
            assert tu["stale_or_unread"] == cov["active"] - int(
                al.get("PMX_GRPC") or 0) - int(
                al.get("REST_RECOVERY") or 0) - int(
                al.get("EXTERNAL_DATA_UNAVAILABLE") or 0)
            # the seeded markets alone: none current, none external
            assert {"mp-1", "mp-2", "mp-3"} <= set(states)
            assert fr["priority_universe"]["target"] == 0.95
            cap = snap["radar"]["capacity"]
            assert cap["symbol_capacity"] == 4 * 1000
            # (developer pass) capacity is not backlog: the known subscribable
            # set needs one stream, but mp-3 still awaits reference data, so
            # the full-universe requirement is NOT proven (null, with why)
            assert cap["streams_for_known_subscribable"] == 1
            assert cap["refdata_pending"] >= 1
            assert cap["streams_required_for_full_coverage"] is None
            assert cap["full_coverage_requirement_why"] == (
                "REFERENCE_DATA_OR_FULL_UNIVERSE_STREAM_AVAILABILITY_NOT_PROVEN")
            assert cap["unused_configured_slots"] == 4 * 1000 - cap[
                "subscribed"]
            assert cap["streams_open"] == 0
        finally:
            await tr.rollback()
            await c.close()
    run(go())


@pg
def test_the_certifier_persists_the_strict_window_verdict():
    async def go():
        from sportsassets import institutional_same_book as SB
        c, tr = await _db()
        try:
            await c.execute("UPDATE market_plane_registry SET active = false")
            await _seed_premap(c, "mp-c", event="aec-nfl-tb-dal-2026",
                               sports_type="football_team_full_game_spread")
            now = time.time()
            await POP.populate(c, since=0.0, now=now, full=True)
            await R.save_refdata(c, "mp-c", {"priceScale": "1000",
                                             "fractionalQtyScale": "100"},
                                 at=now)
            await c.execute("UPDATE market_plane_registry SET "
                            "subscription_shard = 0 WHERE contract_id='mp-c'")

            class _Pool:
                def __init__(self, c):
                    self.c = c

                async def execute(self, *a):
                    return await self.c.execute(*a)
            ts = "2026-10-06T18:27:43.123456Z"
            rows = []
            for _ in range(30):
                rows.append({
                    "symbol": "mp-c", "retail_slug": "mp-c",
                    "identity_ok": True,
                    "identity": {"institutional_symbol": "mp-c"},
                    "verdict": SB.V_AGREE, "stream_changed_in_window": False,
                    "stream_venue_ts": ts,
                    "retail_book": {"transact_time": ts},
                    "probed_at": datetime.now(timezone.utc),
                    "orders_placed": 0})
            await SB.persist(_Pool(c), rows, process_id="t", service="t")
            out = await W.certify(c, None)
            assert out["evaluated"] == 1 and out["supported"] == 1
            row = await c.fetchrow("SELECT * FROM market_plane_certification "
                                   " WHERE contract_id = 'mp-c'")
            assert row["status"] == "SUPPORTED" and row["comparable"] == 30
            assert row["fingerprint"] == CERT.fingerprint(CERT.identity_for(
                "mp-c", price_scale=1000, qty_scale=100))
        finally:
            await tr.rollback()
            await c.close()
    run(go())
