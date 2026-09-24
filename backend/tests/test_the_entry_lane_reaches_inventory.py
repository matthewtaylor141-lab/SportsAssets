"""AUTONOMOUS ENTRY, END TO END, THROUGH THE REAL CYCLE.

Writing a BUY valuation is not autonomous entry. These tests drive
`ext_pinnacle_loop.cycle()` -- the function armed at API startup -- with
only the PROVIDER and the VENUE stubbed at their transport boundaries,
and assert that an admitted decision becomes a position, a decision, a
modelled order, a fill with a fee, residual inventory and reconciled
accounting in the SAME four tables the managed position lives in.

WHAT IS AND IS NOT CONTROLLED HERE. The odds payload, the venue ladder,
the venue's published rules prose and the fixture metadata are supplied,
because a test cannot wait for a real market to offer an edge. Everything
between them is production code: the mapping, the de-vig, the settlement
comparison, the scope gate, the execution estimate, the frozen sizing
policy, the risk engine with its predeclared limits, the entry gate, and
the four writes. The one thing deliberately supplied that production does
NOT have is a CALIBRATION ROW for the external source -- because without
it the MODEL_TRUST_DRIFT gate blocks, which is the true production state
and is asserted here as its own test.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from sportsassets import bettor_entry_execution as entryx
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as ST
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

CONDITION = "c-entry-ari-col"
SLUG = "ari-col-entry"
US_SLUG = "aec-mlb-ari-col-2026-09-24-ari"
EVENT_KEY = "evt-entry-1"

#: Venue prose that agrees with Pinnacle's captured pre-game baseball
#: terms on every applicable terminal condition -- including the
#: bottom-half home-lead scoring exception, which is the detail that
#: separates a real match from two documents that both say "void".
VENUE_PROSE = (
    "This market settles on the final result of the game, including "
    "any extra innings. A game completed in regulation settles on "
    "the final score. If the game is called (ended) after at least "
    "five innings the market settles on the score at the end of the "
    "last completed inning, unless it is called in the bottom half "
    "and the home team has taken the lead, in which case the actual "
    "score is used. If the game is stopped before five innings the "
    "market is void and stakes are returned. If the game is "
    "suspended and resumed within the window it settles on the "
    "final score. If the game is suspended more than the window it "
    "settles on the score at the end of the last completed inning. "
    "If the game is abandoned or postponed and never completed the "
    "market is void and stakes are returned.")


def _fresh_iso(age_s=2.0):
    return (datetime.now(timezone.utc)
            - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event():
    """An MLB h2h event priced so the de-vig beats the venue's ask.

    Two-way, which is what a complete MLB h2h set is: a three-outcome
    payload would be refused by the de-vig, correctly.
    """
    stamp = _fresh_iso()
    # THE CONTRACT'S SELECTION IS THE HOME TEAM -- `pinnacle_h2h` names it
    # and `resolve_venue_identity` prices that side -- so the home side is
    # the favourite here. Getting this backwards priced p(underdog) 0.28
    # against an ask of 0.62 and refused with NO_OBSERVED_DEPTH_INSIDE_THE
    # _BREAK_EVEN_LIMIT, which was the engine being right.
    prices = [{"name": "Colorado Rockies", "price": 1.36},
              {"name": "Arizona Diamondbacks", "price": 3.55}]
    return {"id": EVENT_KEY, "home_team": "Colorado Rockies",
            "away_team": "Arizona Diamondbacks",
            "commence_time": "2026-09-24T19:10:00Z",
            "bookmakers": [
                {"key": "pinnacle", "last_update": stamp,
                 "markets": [{"key": "h2h", "last_update": stamp,
                              "outcomes": prices}]},
                {"key": "smarkets", "last_update": stamp,
                 "markets": [{"key": "h2h", "outcomes": prices}]},
            ]}


#: A ladder cheap enough that the break-even limit admits it, and thin
#: enough that coverage is genuinely BELOW 1 -- so p_fill is measured
#: rather than reaching 1.0 by construction.
LADDER = {
    "ok": True, "side_consumed": "ASK", "pays_on": "THE_PRICED_OUTCOME",
    "best_acquisition_price": 0.62, "best_api_price": 0.62,
    "displayed_depth": 900.0, "levels_published": 3, "levels_read": 3,
    "levels": [{"acquisition_price": 0.62, "qty": 400.0},
               {"acquisition_price": 0.64, "qty": 300.0},
               {"acquisition_price": 0.66, "qty": 200.0}],
}


async def _seed(conn):
    await conn.execute(open("migrations/103_external_valuations.sql").read())
    await conn.execute(open(
        "migrations/105_external_valuations_one_per_observation.sql").read())
    await conn.execute(open(
        "migrations/116_one_entry_position_per_exposure.sql").read())
    # 117 CARRIES THE CALIBRATION TABLE AND THE EVIDENCE COLUMNS. They were
    # appended to 116 after 116 had already been applied in production,
    # where the runner keys on FILENAME and skips a name it has seen -- so
    # they never ran there while a local replay of the whole file showed
    # green. This test applies both, which is what production now does.
    await conn.execute(open(
        "migrations/117_entry_lane_evidence_and_calibration.sql").read())
    await conn.execute("CREATE TABLE IF NOT EXISTS ingestion_state "
                       "(key TEXT PRIMARY KEY, value TEXT)")
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
        "ON CONFLICT (key) DO UPDATE SET value = 'true'", loop.CONTROL_KEY)
    # ONE MARKET FOR THIS FIXTURE, AND ONLY ONE. A second row naming the
    # same two teams makes the mapping AMBIGUOUS and the loop refuses every
    # candidate -- correctly -- so the cycle reports `evaluated: 0` and the
    # test reads as "the lane did nothing". That happened: a row left behind
    # by an unrelated probe. The seed now owns the fixture.
    await conn.execute(
        "UPDATE markets SET closed = TRUE WHERE condition_id <> $1 "
        "AND sport = 'MLB' AND NOT closed "
        "AND event_title ILIKE '%Arizona%' AND event_title ILIKE '%Colorado%'",
        CONDITION)
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, "
        "sport, closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO UPDATE SET sport = 'MLB', "
        "closed = FALSE, resolved = FALSE, updated_at = now()",
        CONDITION, "Will Arizona Diamondbacks beat Colorado Rockies?",
        "Arizona Diamondbacks vs. Colorado Rockies", SLUG)
    # THE AUTHORITATIVE SCOPE AND EVENT STATE, persisted exactly as the
    # acquisition route writes it. Without this row the scope gate refuses
    # and no settlement comparison can read COMPATIBLE.
    await conn.execute(
        "INSERT INTO fixture_metadata (condition_id, phase, game_format, "
        "scheduled_innings, play_has_begun, event_state_raw, "
        "start_evidence, game_pk, official_date, home_team, away_team, "
        "source, source_url, retrieved_at, reader_version) "
        "VALUES ($1,$2,$3,9,false,'Pre-Game',$4,824298,'2026-09-24',"
        "'Colorado Rockies','Arizona Diamondbacks','MLB_STATS_API',"
        "'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-09-24',"
        "now(),'test') "
        "ON CONFLICT (condition_id) DO UPDATE SET phase = EXCLUDED.phase, "
        "game_format = EXCLUDED.game_format, play_has_begun = FALSE, "
        "retrieved_at = now()",
        CONDITION, ST.PHASE_REGULAR, ST.FMT_NINE, ST.SE_ACTUAL_REPORTED)
    await conn.execute("DELETE FROM external_valuations "
                       "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
    await conn.execute(
        "DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id FROM "
        "rn1x_orders WHERE position_id IN (SELECT position_id FROM "
        "rn1x_positions WHERE policy = $1))", inv.POLICY)
    await conn.execute(
        "DELETE FROM rn1x_orders WHERE position_id IN (SELECT position_id "
        "FROM rn1x_positions WHERE policy = $1)", inv.POLICY)
    await conn.execute(
        "DELETE FROM rn1x_decisions WHERE position_id IN (SELECT "
        "position_id FROM rn1x_positions WHERE policy = $1)", inv.POLICY)
    await conn.execute("DELETE FROM rn1x_positions WHERE policy = $1",
                       inv.POLICY)
    await conn.execute("DELETE FROM external_source_calibration "
                       "WHERE source_version = $1", devig.VERSION)


async def _calibrate(conn):
    """Supply the ONE thing production does not have.

    A real measurement of the external source against resolved outcomes.
    The row is what the gate reads, so this test states plainly what it
    is assuming rather than bypassing the gate.
    """
    await conn.execute(
        "INSERT INTO external_source_calibration (source_version, "
        "measured_at, window_start, window_end, sample_size, metric, "
        "score, tolerance, within_tolerance, measured_by, provenance) "
        "VALUES ($1, now(), now() - interval '30 days', now(), 412, "
        "'BRIER', 0.2104, 0.2400, TRUE, 'CONTROLLED_INTEGRATION_TEST', "
        "'{\"note\": \"supplied by a test, not measured in production\"}') "
        "ON CONFLICT (source_version, measured_at) DO NOTHING",
        devig.VERSION)


def _stub(monkeypatch, *, ladder=LADDER, prose=VENUE_PROSE):
    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

    async def fake_fetch(sport_key, *, api_key, timeout=20.0):
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": time.time(),
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True, "events": [_event()],
                "received_at": time.time(),
                "credits_used": "1", "credits_remaining": "9"}

    monkeypatch.setattr(loop, "fetch_odds", fake_fetch)

    from sportsassets.workers import premap as _pm

    async def fake_resolve(conn_, title, event_title, outcome, slug, **kw):
        return {"market_slug": US_SLUG,
                "intent": "ORDER_INTENT_BUY_LONG"}

    monkeypatch.setattr(_pm, "resolve", fake_resolve)

    async def fake_quote(conn_, *, us_slug, intent, now, size=None):
        return {"ok": True, "ask": 0.62, "api_price": 0.62,
                "acquisition_price": 0.62, "side_consumed": "ASK",
                "pays_on": "THE_PRICED_OUTCOME", "intent": intent,
                "levels_read": 3, "depth": 900.0, "sized": None,
                "acquisition_ladder": ladder,
                "age_s": 3.0, "age_basis": "VENUE_TRANSACT_TIME",
                "bid": None, "read_at": now, "slug": us_slug}

    monkeypatch.setattr(loop, "venue_quote", fake_quote)

    # THE VENUE'S PUBLISHED PROSE, at the transport boundary the loop
    # actually crosses. The comparison itself is production code.
    def fake_rules(slug):
        return {"ok": True, "rules_text": prose, "slug": slug,
                "source": "pmus:/markets?slug=<slug>:rules_text"}

    monkeypatch.setattr(loop, "_read_rules_blocking", fake_rules)


# ── the production state: the calibration gate blocks ────────────────

@pg
@pytest.mark.asyncio
async def test_without_a_calibration_row_the_entry_is_refused_by_name(
        monkeypatch):
    """THE CURRENT PRODUCTION ANSWER, asserted rather than described.

    Everything else clears -- the settlement comparison is COMPATIBLE,
    the scope is admitted, both clocks are fresh, the ladder covers a
    real size inside break-even and every rail passes -- and the entry is
    still refused, because the external source's calibration has never
    been measured. That is the lane's standing blocker and it must not be
    clearable by anything in a worker.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        _stub(monkeypatch)
        out = await loop.cycle(conn)
        assert out["ran"] is True, out
        assert out["source_calibration"]["measured"] is False
        assert out["evaluated"] == 1, out
        assert out.get("entries") == [], out.get("entries")
        assert "ADMITTED" not in out["refusals"], out["refusals"]
        from sportsassets import bettor_entry_gate as gate
        assert out["refusals"].get(gate.R_RISK_BLOCKED), out["refusals"]
        # AND IT IS THE CALIBRATION GATE, not a rail. Every rail passed on
        # measured exposure; what blocked is the one condition no
        # measurement exists for.
        assert entryx.state_from_evidence(
            freshness={"fresh": True},
            settlement={"compatibility": "COMPATIBLE"},
            probability=0.72, calibration=None)["blocking"] == [
                "MODEL_TRUST_DRIFT"]
        # NO INVENTORY WAS CREATED.
        held = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
            inv.POLICY)
        assert held == 0
        # AND THE VALUATION ROW IS STILL THERE, with its refusal.
        row = await conn.fetchrow(
            "SELECT decision, admissible, refusals FROM external_valuations "
            "WHERE experiment_id = $1 AND condition_id = $2",
            ext.EXPERIMENT_ID, CONDITION)
        assert row["decision"] == "NO_TRADE"
        assert row["admissible"] is False
    finally:
        await conn.close()


# ── the positive branch, with the measurement supplied ───────────────

@pg
@pytest.mark.asyncio
async def test_an_admitted_entry_becomes_a_position_order_fill_and_basis(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch)
        out = await loop.cycle(conn)
        assert out["ran"] is True, out
        assert out["source_calibration"]["measured"] is True
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        assert out["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1, \
            out["refusals"]
        assert len(out["entries"]) == 1, out["entries"]
        ent = out["entries"][0]
        assert ent["written"] is True, ent
        assert out["order_submitted"] is False

        pid = ent["position_id"]
        pos = await conn.fetchrow(
            "SELECT policy, entry_kind, source_trade_id, source_account, "
            "seed_qty::float8 AS q, seed_price::float8 AS p, "
            "seed_basis_usd::float8 AS b, decision_basis, provenance, "
            "condition_id, outcome_index FROM rn1x_positions "
            "WHERE position_id = $1", pid)
        assert pos is not None, "an admitted entry must hold a position"
        assert pos["policy"] == inv.POLICY
        assert pos["entry_kind"] == inv.ENTRY_KIND
        assert pos["source_trade_id"] is None, (
            "this position is not seeded from an observed trade")
        assert pos["source_account"] == inv.NO_SOURCE_ACCOUNT
        assert pos["condition_id"] == CONDITION
        assert pos["q"] > 0 and 0 < pos["p"] < 1

        order = await conn.fetchrow(
            "SELECT liquidity, side, state, is_modelled, fill_basis, "
            "qty::float8 AS q, filled_qty::float8 AS f, "
            "limit_price::float8 AS lp FROM rn1x_orders "
            "WHERE position_id = $1", pid)
        assert order["side"] == "BUY"
        # MARKETABLE, NOT RESTING. This order crossed the spread, so its
        # liquidity is TAKER and its fill basis is the reconstructor's,
        # never PRINT_THROUGH_WITH_QUEUE_SHARE_V1.
        assert order["liquidity"] == "TAKER"
        assert order["fill_basis"] == inv.FILL_BASIS
        assert order["is_modelled"] is True
        # THE ORDER IS FOR WHAT THE BUDGET AFFORDED INSIDE BREAK-EVEN, and
        # it fills completely: the intent is a DOLLAR amount, so the
        # shortfall is money that could not be spent, not contracts that
        # went unfilled. The state says which of the two happened.
        assert order["q"] == pytest.approx(order["f"])
        assert order["state"] == "FILLED_LIQUIDITY_LIMITED"

        fill = await conn.fetchrow(
            "SELECT qty::float8 AS q, price::float8 AS p, "
            "fee_usd::float8 AS fee, evidence_id, queue_share::float8 AS qs, "
            "fill_basis, is_modelled FROM rn1x_fills WHERE order_id = $1",
            ent["order_id"])
        assert fill is not None, "an entry with no fill is a half-entry"
        assert fill["fee"] > 0, "a free fill is the defect fees exist for"
        assert fill["qs"] == 0.0, "a crossing order joins no queue"
        assert fill["evidence_id"].startswith("venue_book:")
        assert fill["is_modelled"] is True

        # ACCOUNTING: basis includes fees, residual is the whole position.
        acct = ent["accounting"]
        assert acct["invariant_ok"] is True
        assert acct["cost_basis_usd"] == pytest.approx(
            acct["filled_qty"] * acct["vwap"] + acct["fees_usd"], rel=1e-6)
        assert pos["b"] == pytest.approx(acct["cost_basis_usd"], rel=1e-6)
        assert acct["residual_qty"] == pytest.approx(acct["filled_qty"])
        assert acct["realized_pnl_usd"] == 0.0
        # THE FROZEN POLICY'S THREE FIGURES, and only the executed one
        # enters P&L. The ladder held $558 of the $1,000 intended.
        assert acct["intended_notional_usd"] == 1000.0
        assert acct["unfilled_notional_usd"] > 0
        assert acct["executed_notional_usd"] < acct["intended_notional_usd"]
        assert acct["only_executed_notional_enters_pnl"] is True

        # THE DECISION CARRIES THE PROVENANCE, unpromoted.
        dec = await conn.fetchrow(
            "SELECT selected_action, ev_basis, input_labels::text AS lbl, "
            "conditional_on_our_fill::text AS cond, selected_qty::float8 AS q "
            "FROM rn1x_decisions WHERE position_id = $1", pid)
        assert dec["selected_action"] == "BUY"
        assert "EXTERNAL_BOOKMAKER_VALUATION" in dec["ev_basis"]
        assert '"qualified_model": false' in dec["lbl"]
        assert '"probability_validated": false' in dec["lbl"]
        assert entryx.MARKETABLE_BASIS in dec["cond"]
        assert '"execution_secured": false' in dec["cond"]

        # AND THE VALUATION ROW CARRIES THE WHOLE DECISION, so production
        # can be asked what the fill estimate was, what size the policy
        # chose, which rail passed and what the settlement comparison
        # found -- none of which was readable before migration 116.
        val = await conn.fetchrow(
            "SELECT execution_estimate, risk_verdict, exposure_observed, "
            "settlement_comparison FROM external_valuations "
            "WHERE experiment_id = $1 AND condition_id = $2",
            ext.EXPERIMENT_ID, CONDITION)
        import json as _json
        ee = _json.loads(val["execution_estimate"])
        assert ee["basis"] == entryx.MARKETABLE_BASIS
        assert 0 < ee["p_fill"] <= 1
        assert ee["sizing"]["sizingPolicyVersion"]
        rv = _json.loads(val["risk_verdict"])
        assert rv["permitted"] is True
        assert rv["limitsSha"] == entryx.LIMITS_SHA
        assert rv["railsNotPassed"] == []
        ex = _json.loads(val["exposure_observed"])
        assert ex["observed"]["MAX_CAPITAL_DEPLOYED"] > 0
        sc = _json.loads(val["settlement_comparison"])
        assert sc["compatibility"] == "COMPATIBLE"
        assert sc["scope_phase"] == ST.PHASE_REGULAR
        assert sc["fixture_source"] == "MLB_STATS_API"
        assert sc["fixture_retrieved_at"]
        assert sc["venue_rules_read"] is True
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_second_cycle_does_not_create_a_second_position(monkeypatch):
    """Duplicate protection, exercised rather than asserted from the
    schema. The same exposure evaluated twice holds one position."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch)
        first = await loop.cycle(conn)
        assert first["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1
        second = await loop.cycle(conn)
        assert second["ran"] is True
        held = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
            inv.POLICY)
        assert held == 1, "one exposure, one position"
        orders = await conn.fetchval(
            "SELECT count(*) FROM rn1x_orders WHERE position_id IN "
            "(SELECT position_id FROM rn1x_positions WHERE policy = $1)",
            inv.POLICY)
        assert orders == 1, "and one order, not one per cycle"
        # The second cycle's own report says which it was.
        assert second["evaluated"] >= 0
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_incompatible_settlement_rule_refuses_the_entry(monkeypatch):
    """The gate that matters most: a venue that VOIDS a called game where
    the book GRADES it pays differently on the same fixture, and the
    probability cannot price that contract however good the edge looks."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch, prose=(
            "This market settles on the final result of the game, "
            "including any extra innings. If the game is called (ended) "
            "after at least five innings the market is void and stakes "
            "are returned. If the game is abandoned or postponed and "
            "never completed the market is void and stakes are returned."))
        out = await loop.cycle(conn)
        assert "ADMITTED" not in out["refusals"], out["refusals"]
        held = await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy = $1",
            inv.POLICY)
        assert held == 0, "an incompatible payout rule creates no inventory"
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_unreadable_ladder_refuses_by_its_own_name(monkeypatch):
    """No depth means no execution estimate, and the refusal says which of
    the two problems it was rather than one blanket unknown."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch, ladder={"ok": False, "levels": [],
                                   "refusal": "SIDE_EMPTY"})
        out = await loop.cycle(conn)
        assert out["refusals"].get(entryx.R_NO_LADDER), out["refusals"]
        assert "ADMITTED" not in out["refusals"]
    finally:
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_a_ladder_priced_beyond_break_even_is_not_an_unknown(
        monkeypatch):
    """A book that is simply too expensive is a different finding from a
    book nobody could read, and collapsing them would hide an edge that
    never existed behind a data problem that did not happen."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch, ladder=dict(
            LADDER, best_acquisition_price=0.95,
            levels=[{"acquisition_price": 0.95, "qty": 500.0}]))
        out = await loop.cycle(conn)
        assert out["refusals"].get(entryx.R_NOTHING_INSIDE_LIMIT), \
            out["refusals"]
        assert entryx.R_NO_LADDER not in out["refusals"]
    finally:
        await conn.close()


# ── the gap I had claimed was closed ─────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_the_entry_lanes_inventory_is_actually_re_evaluated(monkeypatch):
    """INVENTORY WITH NO MANAGEMENT IS THE FERRARI FAILURE'S SHAPE.

    The entry lane writes into the same four tables and
    `store.open_positions` filters on experiment_id ALONE -- no policy
    clause -- so the shape was right and I reported the gap as closed. It
    was not: `manage_open_positions` is invoked PER EXPERIMENT, with the
    challenger's id, and these positions carry a different one. They were
    written into the shared ledger and then re-evaluated by nothing.

    This test asks the question directly: does the store hand the entry
    lane's open position back when asked for THAT experiment, and does the
    challenger's cycle ask?
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1, \
            out["refusals"]

        from sportsassets import bettor_rn1x_store as store

        # 1 · THE STORE RETURNS IT for the entry lane's own experiment.
        mine = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        assert [p for p in mine if p["policy"] == inv.POLICY], mine

        # 2 · AND NOT for the challenger's, which is exactly why a second
        # call is needed rather than a wider query.
        from sportsassets.workers import rn1x_shadow as RS

        theirs = await store.open_positions(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID, limit=50)
        assert not [p for p in theirs if p["policy"] == inv.POLICY], \
            "the challenger's experiment must not see the entry lane's rows"

        # 3 · THE CYCLE ASKS FOR BOTH. Pinned on the source so a future
        # edit that drops the second call fails here rather than in six
        # weeks with unmanaged inventory.
        import inspect

        src = inspect.getsource(RS.cycle)
        assert "manage_open_positions" in src
        assert src.count("manage_open_positions") >= 2, (
            "the cycle must manage the entry lane's experiment as well as "
            "the challenger's")
        assert "_ext.EXPERIMENT_ID" in src
    finally:
        await conn.close()
