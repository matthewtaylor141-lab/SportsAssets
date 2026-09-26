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

from sportsassets import bettor_entry_execution as EX
from sportsassets import bettor_entry_execution as entryx
from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_pinnacle_devig as devig
from sportsassets import bettor_settlement_terms as ST
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

CONDITION = "c-entry-sea-hou"
SLUG = "sea-hou-entry"
US_SLUG = "aec-mlb-sea-hou-2026-09-24-hou"
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


def _event(price=1.36, stamp_age_s=2.0, at=None):
    """An MLB h2h event priced so the de-vig beats the venue's ask.

    Two-way, which is what a complete MLB h2h set is: a three-outcome
    payload would be refused by the de-vig, correctly.
    """
    stamp = (_fresh_iso(stamp_age_s) if at is None else
             datetime.fromtimestamp(at - stamp_age_s, tz=timezone.utc)
             .strftime("%Y-%m-%dT%H:%M:%SZ"))
    # THE CONTRACT'S SELECTION IS THE HOME TEAM -- `pinnacle_h2h` names it
    # and `resolve_venue_identity` prices that side -- so the home side is
    # the favourite here. Getting this backwards priced p(underdog) 0.28
    # against an ask of 0.62 and refused with NO_OBSERVED_DEPTH_INSIDE_THE
    # _BREAK_EVEN_LIMIT, which was the engine being right.
    prices = [{"name": "Houston Astros", "price": price},
              {"name": "Seattle Mariners", "price": 3.55}]
    return {"id": EVENT_KEY, "home_team": "Houston Astros",
            "away_team": "Seattle Mariners",
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
    # ── THE VENUE'S CATALOGUE, because the PERIOD CHECK READS IT ──────
    #
    # `us_premap` is created by the copy lane's own bootstrap rather than
    # by a migration, which is why 031 and 055 fail on a fresh database.
    # The entry lane's period check now reads this table for the
    # catalogue's own kind, event and side, and for how many contracts the
    # venue publishes for the event -- so a test database without it
    # refuses every identity, correctly and unhelpfully.
    #
    # THE DDL IS THE AUTHORITATIVE ONE, copied from
    # `workers/premap.py:ensure_schema`, and only the columns the period
    # query actually selects are relied on. Two rows are inserted, because
    # a two-participant match publishes two contracts and the sibling
    # count is what tells a fixture from a trophy.
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS us_premap (
            identifier text PRIMARY KEY,
            event_slug text,
            event_title text,
            market_slug text,
            question text,
            kind text,
            line text,
            side_norm text,
            event_keys text[],
            intent text,
            signed text,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    await conn.execute("DELETE FROM us_premap WHERE event_slug = $1",
                       "mlb-sea-hou-2026-09-24")
    for side in ("hou", "sea"):
        await conn.execute(
            # `event_title` too: v4's participant test reads it, and a
            # seed that omits the field the rule reads asserts nothing.
            "INSERT INTO us_premap (identifier, event_slug, market_slug, "
            "kind, side_norm, question, event_title, intent) "
            "VALUES ($1,$2,$3,'side',$4,$5,$6,'ORDER_INTENT_BUY_LONG') "
            "ON CONFLICT (identifier) DO NOTHING",
            "aec-mlb-sea-hou-2026-09-24-%s" % side,
            "mlb-sea-hou-2026-09-24",
            "aec-mlb-sea-hou-2026-09-24-%s" % side,
            side, "Will %s win?" % side,
            "Seattle Mariners vs. Houston Astros")
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
    await conn.execute(open(
        "migrations/118_outcome_join_provenance_and_audit.sql").read())
    # 119 PUTS THE VENUE IDENTITY ON THE POSITION. Without it the entry
    # writer cannot record which contract and which side it opened, and the
    # settlement consumer is back to guessing from the condition alone.
    await conn.execute(open(
        "migrations/119_positions_carry_their_venue_identity.sql").read())
    # 120 RECORDS THE VENUE. `bettor_venue_position_model.model_for`
    # refuses an unknown venue rather than defaulting, and the settlement
    # replay can only honour that refusal if the venue is on the row.
    await conn.execute(open(
        "migrations/120_positions_record_their_venue.sql").read())
    # 122 MUST COME AFTER 117, AND OMITTING IT REVERTED THE SCHEMA.
    #
    # THE DEFECT THIS CLOSES. 117 above re-adds `rn1x_provenance_declared`
    # with THREE permitted origins; 122 widens it to four by adding
    # UNCALIBRATED_RESEARCH_SHADOW, the provenance an entry created under
    # the unfunded research waiver carries. This fixture applied 117 and
    # not 122, so every test built on it ran against a schema that FORBIDS
    # that provenance -- and the uncalibrated research lane's write failed
    # with `CheckViolationError` on `rn1x_provenance_declared` after the
    # entry had already been ADMITTED. Production is unaffected:
    # `scripts/migrate.py` applies 122 after 117 and keys on filename, so
    # the deployed schema permits all four. The fault was this fixture's
    # alone, and it made the research lane's lifecycle untestable.
    await conn.execute(open(
        "migrations/122_uncalibrated_research_shadow_provenance.sql").read())
    await conn.execute("CREATE TABLE IF NOT EXISTS ingestion_state "
                       "(key TEXT PRIMARY KEY, value TEXT)")
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
        "ON CONFLICT (key) DO UPDATE SET value = 'true'", loop.CONTROL_KEY)
    # THIS FIXTURE'S TEAMS ARE ITS OWN. An earlier version closed any other
    # MLB market naming the same two clubs, to keep the mapping
    # unambiguous -- and that silently closed the ACCEPTANCE suite's
    # market, breaking thirty of its tests whenever the two ran together.
    # A teardown that reaches outside its own fixture is a worse fault than
    # the ambiguity it was treating, so the pair below is one no other test
    # uses and nothing is closed.
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, "
        "sport, closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO UPDATE SET sport = 'MLB', "
        "closed = FALSE, resolved = FALSE, updated_at = now()",
        CONDITION, "Will Seattle Mariners beat Houston Astros?",
        "Seattle Mariners vs. Houston Astros", SLUG)
    # THE AUTHORITATIVE SCOPE AND EVENT STATE, persisted exactly as the
    # acquisition route writes it. Without this row the scope gate refuses
    # and no settlement comparison can read COMPATIBLE.
    await conn.execute(
        "INSERT INTO fixture_metadata (condition_id, phase, game_format, "
        "scheduled_innings, play_has_begun, event_state_raw, "
        "start_evidence, game_pk, official_date, home_team, away_team, "
        "source, source_url, retrieved_at, reader_version) "
        "VALUES ($1,$2,$3,9,false,'Pre-Game',$4,824298,'2026-09-24',"
        "'Houston Astros','Seattle Mariners','MLB_STATS_API',"
        "'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-09-24',"
        "now(),'test') "
        "ON CONFLICT (condition_id) DO UPDATE SET phase = EXCLUDED.phase, "
        "game_format = EXCLUDED.game_format, play_has_begun = FALSE, "
        "retrieved_at = now()",
        CONDITION, ST.PHASE_REGULAR, ST.FMT_NINE, ST.SE_ACTUAL_REPORTED)
    # THE GLOBAL CATALOGUE'S OUTCOMES AND THEIR INDICES. The payout event
    # is bound by matching its name against these, never derived from the
    # venue's order intent -- so they have to exist for an entry to be
    # held at all. Index 0 is the home side here, which is the selection
    # a BUY_LONG acquires, so the cross-check agrees.
    await conn.execute(
        "INSERT INTO market_tokens (token_id, condition_id, outcome, "
        "outcome_index) VALUES ($1,$2,$3,$4),($5,$2,$6,$7) "
        "ON CONFLICT (token_id) DO UPDATE SET outcome = EXCLUDED.outcome, "
        "outcome_index = EXCLUDED.outcome_index",
        "tok-hou", CONDITION, "Houston Astros", 0,
        "tok-sea", "Seattle Mariners", 1)
    await conn.execute("DELETE FROM external_valuations "
                       "WHERE experiment_id = $1", ext.EXPERIMENT_ID)
    # OUTCOMES FIRST: `rn1x_outcomes` references the position, and the
    # lifecycle test leaves one behind. A settlement row is meant to
    # outlive the decision that preceded it, which is why the foreign key
    # is there -- so the teardown removes it explicitly rather than the
    # schema being loosened to make cleanup easy.
    await conn.execute(
        "DELETE FROM rn1x_outcomes WHERE position_id IN (SELECT position_id "
        "FROM rn1x_positions WHERE policy = $1)", inv.POLICY)
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


async def _cleanup(conn):
    """Remove everything this module created.

    ITS ROWS ARE ITS OWN. An open MLB market left in `markets` becomes a
    CANDIDATE for every other suite's pool query, and an outcome row left
    in `rn1x_outcomes` makes another suite's teardown fail on a foreign
    key. Both happened, and together they turned thirty-four unrelated
    tests red. Deleting the market cascades to its tokens.
    """
    for sql, args in (
            ("DELETE FROM rn1x_outcomes WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE policy = $1)",
             (inv.POLICY,)),
            ("DELETE FROM rn1x_fills WHERE order_id IN (SELECT order_id "
             "FROM rn1x_orders WHERE position_id IN (SELECT position_id "
             "FROM rn1x_positions WHERE policy = $1))", (inv.POLICY,)),
            ("DELETE FROM rn1x_orders WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE policy = $1)",
             (inv.POLICY,)),
            ("DELETE FROM rn1x_decisions WHERE position_id IN (SELECT "
             "position_id FROM rn1x_positions WHERE policy = $1)",
             (inv.POLICY,)),
            ("DELETE FROM rn1x_positions WHERE policy = $1",
             (inv.POLICY,)),
            ("DELETE FROM external_valuations WHERE condition_id = $1",
             (CONDITION,)),
            ("DELETE FROM fixture_metadata WHERE condition_id = $1",
             (CONDITION,)),
            ("DELETE FROM markets WHERE condition_id = $1", (CONDITION,)),
            ("DELETE FROM external_source_calibration WHERE measured_by = $1",
             ("CONTROLLED_INTEGRATION_TEST",)),
    ):
        try:
            await conn.execute(sql, *args)
        except Exception:                                      # noqa: BLE001
            pass


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


def _stub(monkeypatch, *, ladder=LADDER, prose=VENUE_PROSE,
          price=1.36, stamp_age_s=2.0, at=None):
    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

    async def fake_fetch(sport_key, *, api_key, timeout=20.0):
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": time.time(),
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True, "events": [_event(price, stamp_age_s, at)],
                "received_at": (at if at is not None else time.time()),
                "credits_used": "1", "credits_remaining": "9"}

    monkeypatch.setattr(loop, "fetch_odds", fake_fetch)

    from sportsassets.workers import premap as _pm

    async def fake_resolve(conn_, title, event_title, outcome, slug, **kw):
        # THE SHAPE `premap.resolve` ACTUALLY RETURNS, and this stub did
        # not. It returned `market_slug` and `intent` only, so
        # `resolve_venue_identity` -- which read `side_norm`, `identifier`
        # and `question` -- recorded three nulls and the test could not
        # have noticed, because the real resolver returns the side under
        # `outcome`, the identifier under `market_slug` and the question
        # under `title`. An unfaithful stub is how a silent field-name bug
        # lives in production while its test suite stays green.
        #
        # `outcome` also matters to the PERIOD check: the only token
        # allowed after the slug's date is the matched side, and US_SLUG
        # ends in `-hou`. Without a side there is nothing for that token
        # to equal, and the identity is correctly refused.
        return {"market_slug": US_SLUG,
                "outcome": "hou",
                "title": "Will the Houston Astros beat the Seattle Mariners?",
                "matched_by": "premap_identity",
                "score": 1.0,
                "intent": "ORDER_INTENT_BUY_LONG"}

    monkeypatch.setattr(_pm, "resolve", fake_resolve)

    async def fake_quote(conn_, *, us_slug, intent, now, size=None):
        return {"ok": True, "ask": 0.62, "api_price": 0.62,
                "acquisition_price": 0.62, "side_consumed": "ASK",
                "pays_on": "THE_PRICED_OUTCOME", "intent": intent,
                "levels_read": 3, "depth": 900.0, "sized": None,
                "acquisition_ladder": ladder,
                # THE VENUE'S OWN INSTANT. The freshness gate re-ages the
                # book at the DECISION instant rather than inheriting the
                # age it had at read time, so a stub that reports only
                # `age_s` leaves the venue clock unmeasured and the gate
                # blocks -- which is the correct refusal and is why this
                # has to be supplied.
                "venue_ts": now - 3.0,
                "age_s": 3.0, "age_basis": "VENUE_TRANSACT_TIME",
                "bid": None, "read_at": now, "slug": us_slug}

    monkeypatch.setattr(loop, "venue_quote", fake_quote)

    # THE VENUE'S PUBLISHED PROSE, at the transport boundary the loop
    # actually crosses. The comparison itself is production code.
    def fake_rules(slug, *, now=None):
        return {"ok": True, "rules_text": prose, "slug": slug,
                "read_at": now, "from_cache": False,
                "source": "pmus:/markets?slug=<slug>:rules_text"}

    monkeypatch.setattr(loop, "_read_venue_rules_blocking", fake_rules)
    # THE CACHE IS PER-PROCESS AND SURVIVES BETWEEN TESTS. A stub set in
    # one test would otherwise be shadowed by a cached answer from another.
    loop.rules_cache_reset()


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
        await _cleanup(conn)
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
        # THE EXACT INVARIANT IS OVER THE LEVELS. The vwap identity is
        # checked separately, to a tolerance that scales with the quantity
        # the average was divided by.
        assert acct["invariant_ok"] is True
        assert acct["vwap_identity_ok"] is True, acct["vwap_identity_residual"]
        assert acct["cost_basis_usd"] == pytest.approx(
            acct["levels_cost_usd"] + acct["fees_usd"], abs=1e-9)
        assert pos["b"] == pytest.approx(acct["cost_basis_usd"], rel=1e-6)
        assert acct["residual_qty"] == pytest.approx(acct["filled_qty"])
        assert acct["realized_pnl_usd"] == 0.0
        # THE FROZEN POLICY'S THREE FIGURES, and only the executed one
        # enters P&L. The ladder held $558 of what was asked for.
        #
        # THIS USED TO ASSERT `== 1000.0` AND IT NO LONGER CAN. Sizing now
        # asks the exposure rails for headroom BEFORE it spends, and a
        # $1,000 budget reserved at the break-even limit always breaches a
        # $1,000 rail by exactly the edge -- which is why every positive-
        # edge candidate in production failed all five rails on an empty
        # book. The intent is therefore reduced to what the rails allow.
        #
        # The frozen policy's own notional is UNCHANGED and still recorded,
        # and nothing about this fill moves: the ladder held $558, which is
        # inside both the standard intent and the reduced one.
        assert acct["intended_notional_usd"] < 1000.0
        assert acct["intended_notional_usd"] > acct["executed_notional_usd"]
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
            "settlement_comparison, probability::float8 AS pr, "
            "executable_price::float8 AS px, "
            "cost_per_contract::float8 AS cpc, "
            "estimated_edge_per_contract::float8 AS edge "
            "FROM external_valuations "
            "WHERE experiment_id = $1 AND condition_id = $2",
            ext.EXPERIMENT_ID, CONDITION)
        import json as _json
        ee = _json.loads(val["execution_estimate"])

        # ── THE THREE PRICES REACH THREE DIFFERENT CONSUMERS ─────────
        # This is the regression that made 159 of run 48's refusals
        # arithmetic rather than market fact. `_entry_plan` handed the
        # gate `limit_price`, which by construction IS
        # fair_value - fee, so the edge the gate computed was zero less
        # rounding and every candidate refused with
        # NO_ACTION_HAS_POSITIVE_NET_EDGE.
        #
        #   executable_price      what the walked quantity costs
        #   orders.limit_price    what the order was submitted at
        #   the reservation       the worst case, at the limit
        #
        # They are three DIFFERENT numbers here, and each is asserted
        # against its own consumer.
        assert val["px"] == pytest.approx(
            ee["acquisition_cost_per_contract"]), (
                "the economic comparison must use the walked cost")
        assert val["px"] == pytest.approx(ee["vwap"])
        assert ee["submitted_limit"] == pytest.approx(order["lp"]), (
            "the ORDER goes out at the break-even limit")
        assert ee["worst_case_cost_per_contract"] == pytest.approx(
            ee["submitted_limit"])
        assert val["px"] < ee["submitted_limit"], (
            "a walk strictly inside the limit is the whole point of the "
            "ladder being cheaper than break-even")
        # THE FEE IS THE ONE ACTUALLY WALKED, summed per level and
        # divided by the quantity filled -- not re-derived at one price.
        assert val["cpc"] == pytest.approx(
            ee["fee_per_contract_realised"], abs=1e-8)
        # AND THE EDGE IS THAT SUBTRACTION, POSITIVE, and equal to the
        # probability less what is paid less what it costs.
        assert val["edge"] == pytest.approx(
            val["pr"] - val["px"] - val["cpc"], abs=1e-9)
        assert val["edge"] > 0, val["edge"]
        # THE RESERVATION IS THE WORST CASE, deliberately, and is bigger
        # than the modelled cost of the same quantity.
        exp_detail = _json.loads(val["exposure_observed"])
        assert exp_detail["proposed_cost_basis"] == \
            "SIZE_TIMES_WORST_CASE_COST_PER_CONTRACT", exp_detail
        assert exp_detail["proposed_cost_usd"] > \
            ee["size"] * val["px"]
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
        await _cleanup(conn)
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
        await _cleanup(conn)
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
        await _cleanup(conn)
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
        await _cleanup(conn)
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
        await _cleanup(conn)
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
        # TWO CALL SITES: the normal path and the no-candidates path.
        # Management must not depend on the cohort having produced a new
        # fill, because "no new candidates" is the ordinary state.
        assert src.count("run_continuing_management") == 2, src.count(
            "run_continuing_management")
        helper = inspect.getsource(RS.run_continuing_management)
        assert helper.count("manage_open_positions") >= 1
        assert "_ext.EXPERIMENT_ID" in helper, (
            "the helper must manage the entry lane's experiment too")
    finally:
        await _cleanup(conn)
        await conn.close()


# ── ITEM 2: THREE CASES, AND ONLY ONE OF THEM WRITES ─────────────────

@pg
@pytest.mark.asyncio
async def test_a_later_cycle_on_a_held_exposure_adds_no_executions(
        monkeypatch):
    """THE DUPLICATE-FILL DEFECT, REPRODUCED AND THEN REFUSED.

    The ids used to hang off `int(now)`, so a cycle a minute later minted a
    new order id and a new fill id -- both inserted -- while the position
    row hit ON CONFLICT DO NOTHING and kept its original seed_qty and
    basis. Executions accumulated against inventory that never grew.

    This is NOT two immediate calls: the second cycle carries a NEW
    PROVIDER OBSERVATION (a fresh `last_update`, a different price) and a
    decision instant a full cycle later, so neither timestamp collision
    nor the valuation table's own per-observation uniqueness can be what
    makes it pass.
    """
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch)
        first = await loop.cycle(conn)
        assert first["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1, \
            first["refusals"]
        ent = first["entries"][0]
        pid = ent["position_id"]

        async def counts():
            return dict(await conn.fetchrow(
                "SELECT (SELECT count(*) FROM rn1x_orders WHERE "
                "  position_id = $1) AS orders,"
                " (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o"
                "   ON o.order_id = f.order_id WHERE o.position_id = $1)"
                "   AS fills,"
                " (SELECT coalesce(sum(f.qty),0)::float8 FROM rn1x_fills f"
                "   JOIN rn1x_orders o ON o.order_id = f.order_id"
                "  WHERE o.position_id = $1) AS filled_qty,"
                " (SELECT coalesce(sum(f.fee_usd),0)::float8 FROM rn1x_fills f"
                "   JOIN rn1x_orders o ON o.order_id = f.order_id"
                "  WHERE o.position_id = $1) AS fees,"
                " (SELECT seed_qty::float8 FROM rn1x_positions WHERE"
                "   position_id = $1) AS seed_qty,"
                " (SELECT seed_basis_usd::float8 FROM rn1x_positions WHERE"
                "   position_id = $1) AS basis", pid))

        before = await counts()
        # EVERY QUANTITY TOGETHER, not one at a time: the defect was
        # precisely that fills moved while the position did not.
        assert before["filled_qty"] == pytest.approx(before["seed_qty"])
        assert before["fees"] > 0
        assert before["basis"] == pytest.approx(
            ent["accounting"]["cost_basis_usd"])

        # ── A NEW OBSERVATION, A CYCLE LATER ─────────────────────────
        #
        # THE WHOLE CLOCK MOVES, not just the decision instant. A cycle is
        # 900 s and a quote goes stale at 30, so a second cycle genuinely
        # carries a NEW provider observation -- and the fixture evidence
        # has to be re-acquired at that instant too, exactly as production
        # re-acquires it. Shifting only `now` would have refused on
        # QUOTE_STALE and proved nothing about duplicate fills.
        later = time.time() + loop.CYCLE_S
        await conn.execute(
            "UPDATE fixture_metadata SET retrieved_at = to_timestamp($2), "
            "play_has_begun = FALSE WHERE condition_id = $1",
            CONDITION, later)
        _stub(monkeypatch, price=1.34, stamp_age_s=1.0, at=later)
        monkeypatch.setattr(loop.time, "time", lambda: later)
        second = await loop.cycle(conn)
        # THE SECOND CYCLE DID REACH A DECISION -- otherwise the assertions
        # below would pass for want of a candidate rather than because the
        # write was refused.
        assert second["evaluated"] == 1, second["refusals"]

        after = await counts()
        assert after["orders"] == before["orders"], (
            "a new quote on a held exposure must not add an order")
        assert after["fills"] == before["fills"], (
            "nor a fill: that is the defect")
        assert after["filled_qty"] == pytest.approx(before["filled_qty"])
        assert after["fees"] == pytest.approx(before["fees"])
        assert after["seed_qty"] == pytest.approx(before["seed_qty"])
        assert after["basis"] == pytest.approx(before["basis"])
        # AND IT IS REFUSED BY NAME. Which name is itself informative:
        # with the first position now RESERVED in the book, the exposure
        # rails see the combined size and refuse at the risk gate before
        # the writer is reached. Either refusal is legitimate and both are
        # named; what must never happen is a write.
        from sportsassets import bettor_entry_gate as _gate

        named = set(second["refusals"]) & {inv.R_ALREADY_HELD,
                                           _gate.R_RISK_BLOCKED}
        assert named, second["refusals"]
        assert not [e for e in second["entries"] if e.get("written")]
        assert inv.ADD_SUPPORTED is False

    finally:
        await _cleanup(conn)
        await conn.close()


@pg
@pytest.mark.asyncio
async def test_an_exact_replay_is_named_a_replay_and_writes_nothing(
        monkeypatch):
    """The same observation twice is idempotent AND reported as a replay --
    not as a write that happened to change nothing."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        # NO CYCLE HERE. Running one would create the position first, and
        # the write below would then be correctly refused as an ADD -- a
        # different case from the one this test is about.
        rec = {"admissible": True, "experiment_id": ext.EXPERIMENT_ID,
               "observed_at": 1000.0, "received_at": 1000.0,
               "payout_event": "Houston Astros",
               "contract": {"condition_id": CONDITION,
                            "us_market_slug": US_SLUG,
                            "buy_intent": "ORDER_INTENT_BUY_LONG",
                            "event_key": EVENT_KEY},
               "execution_plan": {"execution": {
                   "size": 100.0, "vwap": 0.62, "submitted_limit": 0.70,
                   "intended_notional_usd": 1000.0,
                   "unfilled_notional_usd": 938.0,
                   "levels_taken": [{"price": 0.62, "qty": 100.0,
                                     "cost": 62.0}]}}}
        fee = (lambda qty, price, maker=False: 0.016 * float(qty))
        plan = inv.plan_entry(rec, now=2000.0, outcome_index=0, fee_fn=fee)
        assert plan["ok"], plan
        w1 = await inv.persist_entry(conn, plan)
        assert w1["written"] is True and w1["case"] == inv.CASE_NEW, w1
        n1 = await conn.fetchval(
            "SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id WHERE o.position_id = $1",
            w1["position_id"])
        # SAME observation, later decision instant: still a replay.
        plan2 = inv.plan_entry(rec, now=9000.0, outcome_index=0, fee_fn=fee)
        w2 = await inv.persist_entry(conn, plan2)
        assert w2["written"] is False
        assert w2["case"] == inv.CASE_REPLAY, w2
        n2 = await conn.fetchval(
            "SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o "
            "ON o.order_id = f.order_id WHERE o.position_id = $1",
            w1["position_id"])
        assert n2 == n1, "a replay writes nothing"
    finally:
        await _cleanup(conn)
        await conn.close()


# ── ITEM 3: THE RESERVATION COUNTEREXAMPLE ───────────────────────────

def test_two_individually_admissible_entries_cannot_both_clear_the_cap():
    """THE COUNTEREXAMPLE THE REVIEW ASKED FOR, pinned.

    Two $600 proposals are each admissible against an empty book. The
    combined-exposure rail is $1,000. Measured against a book that was
    read once per cycle, the second proposal never saw the first and both
    cleared. Reserved after the first creation, the second is refused.
    """
    first = EX.exposure_from_rows(
        [], condition_id="c1", event_key="e1",
        proposed_cost_usd=600.0, proposed_qty=1000.0, now=0.0)
    v1 = EX.verdict("TAKE_YES", observed=first["observed"],
                    state={k: True for k in risk_gates()})
    assert v1["permitted"] is True, v1["railsNotPassed"]

    # WITHOUT the reservation: the stale book is still empty.
    stale = EX.exposure_from_rows(
        [], condition_id="c2", event_key="e2",
        proposed_cost_usd=600.0, proposed_qty=1000.0, now=0.0)
    v_stale = EX.verdict("TAKE_YES", observed=stale["observed"],
                         state={k: True for k in risk_gates()})
    assert v_stale["permitted"] is True, (
        "this is the defect: measured against a book read before the first "
        "entry, the second one clears")

    # WITH it: the first position is in the book the second is measured on.
    reserved = EX.exposure_from_rows(
        [{"condition_id": "c1", "event_key": "e1", "cost_usd": 600.0,
          "qty": 1000.0, "opened_at": 0.0, "reserved_in_this_cycle": True}],
        condition_id="c2", event_key="e2",
        proposed_cost_usd=600.0, proposed_qty=1000.0, now=0.0)
    assert reserved["observed"]["MAX_CORRELATED_EXPOSURE"] == 1200.0
    v2 = EX.verdict("TAKE_YES", observed=reserved["observed"],
                    state={k: True for k in risk_gates()})
    assert v2["permitted"] is False
    assert "MAX_CORRELATED_EXPOSURE" in v2["railsNotPassed"]


def risk_gates():
    from sportsassets import bettor_risk_engine as risk
    return list(risk.STATE_GATES)

@pg
@pytest.mark.asyncio
async def test_the_writer_itself_refuses_a_second_observation_as_an_add():
    """The same discrimination at the WRITER's own level, with the risk
    gate out of the picture: a different provider observation on a held
    exposure is CASE_NEW_QUOTE and writes nothing."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        fee = (lambda qty, price, maker=False: 0.016 * float(qty))

        def _rec(observed_at, vwap):
            return {"admissible": True, "experiment_id": ext.EXPERIMENT_ID,
                    "observed_at": observed_at, "received_at": observed_at,
                    "payout_event": "Houston Astros",
                    "contract": {"condition_id": CONDITION,
                                 "us_market_slug": US_SLUG,
                                 "buy_intent": "ORDER_INTENT_BUY_LONG",
                                 "event_key": EVENT_KEY},
                    "execution_plan": {"execution": {
                        "size": 100.0, "vwap": vwap,
                        "submitted_limit": 0.70,
                        "intended_notional_usd": 1000.0,
                        "unfilled_notional_usd": 900.0,
                        "levels_taken": [{"price": vwap, "qty": 100.0,
                                          "cost": 100.0 * vwap}]}}}

        w1 = await inv.persist_entry(conn, inv.plan_entry(
            _rec(5000.0, 0.62), now=5001.0, outcome_index=0, fee_fn=fee))
        assert w1["written"] is True and w1["case"] == inv.CASE_NEW

        async def snap():
            return dict(await conn.fetchrow(
                "SELECT (SELECT count(*) FROM rn1x_orders WHERE"
                "  position_id=$1) AS orders,"
                " (SELECT count(*) FROM rn1x_fills f JOIN rn1x_orders o"
                "  ON o.order_id=f.order_id WHERE o.position_id=$1) AS fills,"
                " (SELECT seed_qty::float8 FROM rn1x_positions"
                "  WHERE position_id=$1) AS qty,"
                " (SELECT seed_basis_usd::float8 FROM rn1x_positions"
                "  WHERE position_id=$1) AS basis", w1["position_id"]))

        before = await snap()
        # A DIFFERENT OBSERVATION, an hour later, at a different price.
        w2 = await inv.persist_entry(conn, inv.plan_entry(
            _rec(8600.0, 0.64), now=8601.0, outcome_index=0, fee_fn=fee))
        assert w2["written"] is False
        assert w2["case"] == inv.CASE_NEW_QUOTE, w2
        assert w2["refusals"] == [inv.R_ALREADY_HELD]
        assert w2["existing_qty"] == pytest.approx(before["qty"])
        after = await snap()
        assert after == before, ("a second observation must change nothing: "
                                 "%r vs %r" % (after, before))
    finally:
        await _cleanup(conn)
        await conn.close()


# ── ITEM 1: THE WHOLE LIFECYCLE ON AN ENTRY-CREATED POSITION ─────────

@pg
@pytest.mark.asyncio
async def test_an_entry_created_position_runs_the_whole_lifecycle(
        monkeypatch):
    """ENTRY -> TWO SCHEDULED MANAGEMENT CYCLES -> RESTART -> SETTLEMENT.

    Not the acceptance harness's seeded position: one this lane created
    itself, with source_trade_id NULL and provenance
    AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW. The manager and the store
    must carry it as it is -- no fabricated RN1 trade, no replacement
    position, and the same position_id throughout.

    Management is driven through `rn1x_shadow.run_continuing_management`,
    which is what the scheduled cycle calls on BOTH paths, including the
    one where the cohort produced no new candidate.
    """
    asyncpg = pytest.importorskip("asyncpg")
    from sportsassets import bettor_rn1x_store as store
    from sportsassets.workers import rn1x_shadow as RS

    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _calibrate(conn)
        _stub(monkeypatch)
        made = await loop.cycle(conn)
        assert made["refusals"].get("ENTRY_INVENTORY_WRITTEN") == 1, \
            made["refusals"]
        pid = made["entries"][0]["position_id"]

        # ── THE POSITION AS THE LANE CREATED IT ──────────────────────
        pos = await conn.fetchrow(
            "SELECT source_trade_id, source_account, provenance, policy, "
            "seed_qty::float8 AS q FROM rn1x_positions WHERE position_id=$1",
            pid)
        assert pos["source_trade_id"] is None, (
            "an autonomous entry has no source trade and one must not be "
            "invented for it")
        assert pos["provenance"] == inv.PROVENANCE
        assert pos["policy"] == inv.POLICY

        # ── THE STORE CARRIES A NULL source_trade_id ─────────────────
        open_rows = await store.open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, limit=10)
        mine = [r for r in open_rows if r["position_id"] == pid]
        assert mine, "the store must return it as an ordinary open position"
        assert mine[0]["source_trade_id"] is None

        async def decisions():
            return await conn.fetchval(
                "SELECT count(*) FROM rn1x_decisions WHERE position_id=$1",
                pid)

        d0 = await decisions()
        assert d0 >= 1, "the entry itself recorded a decision"

        # ── TWO SCHEDULED MANAGEMENT CYCLES ──────────────────────────
        # Through the same entry point the scheduler uses. It must not
        # raise on a NULL source trade, and it must find THIS experiment.
        seen = []
        managed_results = []
        for _ in range(2):
            got = await RS.run_continuing_management(
                conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
            assert got["entry_lane"] is not None
            el = got["entry_lane"]
            assert "error" not in el, el.get("error")
            assert ext.EXPERIMENT_ID in got["experiments"]
            seen.append(el.get("examined"))
            managed_results.append(el)
        assert all(n and n >= 1 for n in seen), (
            "both cycles must have examined the entry lane's position: %r"
            % (seen,))
        # AND NOT MERELY EXAMINED: each cycle must have reached a recorded
        # outcome for it -- either a written management decision or a named
        # refusal. "examined, and nothing happened" is the state that hid
        # the TypeError through three cycles.
        for got in (seen_full := managed_results):
            res = [r for r in (got.get("results") or [])
                   if r.get("position_id") == pid]
            assert res, "the position must appear in the results"
            r = res[0]
            assert r.get("written") or r.get("error") or r.get("refused_at") \
                or r.get("no_inputs") or r.get("first_failing_link"), r
            assert not r.get("error"), r["error"]

        # THE SAME POSITION, NOT A REPLACEMENT.
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE policy=$1",
            inv.POLICY) == 1
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_positions WHERE position_id=$1",
            pid) == 1

        # ── RESTART RECOVERY ────────────────────────────────────────
        # A new connection is a new process for these purposes: nothing is
        # carried in memory, and the position is rebuilt from the ledger.
        await conn.close()
        conn = await asyncpg.connect(DSN)
        again = await RS.run_continuing_management(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
        el = again["entry_lane"]
        assert "error" not in el, el.get("error")
        assert el.get("examined") >= 1, (
            "after a restart the position must still be found and managed")
        loaded = await store.load_position(conn, pid)
        assert loaded["orders"], "its order survived the restart"
        assert loaded["fills"], "and its fills"

        # ── SETTLEMENT, BY PRODUCTION CODE ──────────────────────────
        # THE DEFECT THIS REPLACES, AND IT WAS MINE. This block used to
        # INSERT its own row into `rn1x_outcomes` and then assert that the
        # row it had just written said what it wanted -- a test that
        # Postgres stores what you put in it. Nothing in production
        # created an outcome for an entry-lane position.
        #
        # Now the ONLY thing supplied is the venue's response at the
        # transport boundary -- `client.markets.settlement(slug)`, the
        # authoritative endpoint, in the shape the SDK documents. Reading
        # it, mapping it through the verified venue-side identity,
        # computing the payout, closing the residual and writing the row
        # are all production code.
        acct = made["entries"][0]["accounting"]
        from sportsassets import bettor_entry_settlement as SETTLE

        _venue_calls = []

        class _Settlement:
            @staticmethod
            def settlement(slug):
                _venue_calls.append(slug)
                return {"marketSlug": slug,
                        "settlementPrice": {"value": "1",
                                            "currency": "USD"},
                        "settledAt": "2026-09-24T23:14:07Z"}

        class _Client:
            markets = _Settlement()

        from sportsassets import pmus as _pmus

        monkeypatch.setattr(_pmus, "_get_client", lambda: _Client())
        # NO FRESH BOOKMAKER ODDS. The odds fetch is made to fail outright:
        # a finished contract's value is the venue's settlement price, and
        # needing a live quote to settle a market that is over is the
        # defect that left a settled fixture carried as open inventory.
        async def _no_odds(*a, **k):
            raise AssertionError("settlement must not need fresh odds")

        monkeypatch.setattr(loop, "fetch_odds", _no_odds)

        done = await RS.run_continuing_management(
            conn, experiment_id=RS.CHALLENGER_EXPERIMENT_ID)
        s = done["settlement"]
        assert s["ran"] is True, s
        assert s["settled"] == 1, s
        assert _venue_calls == [US_SLUG], (
            "the venue's own settlement endpoint, asked once, for the "
            "contract this position holds: %r" % (_venue_calls,))
        res = [r for r in s["results"] if r["position_id"] == pid][0]
        assert res["status"] == SETTLE.S_SETTLED
        assert res["written"] is True
        assert res["side_map"] == loop.SIDE_LONG
        assert res["needed_fresh_odds"] is False
        assert res["settlement_read"] == "1"

        settled = await conn.fetchrow(
            "SELECT realized_cash_usd::float8 AS cash, net_usd::float8 AS "
            "net, residual_qty::float8 AS resid, fees_usd::float8 AS fees, "
            "outcome_basis, settled_at, payout_per_leg::text AS pay "
            "FROM rn1x_outcomes WHERE position_id = $1", pid)
        assert settled is not None, "production code created the outcome"
        assert settled["resid"] == 0.0, "settlement closes the residual"
        assert settled["outcome_basis"] == SETTLE.BASIS_SETTLED
        # THE VENUE'S OWN INSTANT, not ours.
        assert settled["settled_at"].isoformat().startswith("2026-09-24T23:14")
        # THE ACCOUNTING RECONCILES: a contract that pays 1 returns the
        # quantity in cash, and the net is that less what it cost.
        assert settled["cash"] == pytest.approx(acct["filled_qty"])
        assert settled["fees"] == pytest.approx(acct["fees_usd"], rel=1e-6)
        assert settled["net"] == pytest.approx(
            acct["filled_qty"] - acct["cost_basis_usd"], rel=1e-6)
        assert '"payout_per_contract": 1.0' in settled["pay"]

        # ── EXACTLY ONCE, ACROSS A RESTART ──────────────────────────
        # A second cycle -- and a new connection, which is a new process
        # for these purposes -- must not settle it again, must not rewrite
        # what it wrote, and must say so rather than silently no-op.
        written_at = await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1", pid)
        await conn.close()
        conn = await asyncpg.connect(DSN)
        twice = await SETTLE.settle_open_positions(
            conn, experiment_id=ext.EXPERIMENT_ID, policy=inv.POLICY,
            now=time.time(),
            read_resolution=lambda slug: pytest.fail(
                "a settled position must not be re-read"))
        assert twice["examined"] == 0, (
            "a settled position is no longer open, so the venue is not "
            "asked about it again at all")
        assert await conn.fetchval(
            "SELECT count(*) FROM rn1x_outcomes WHERE position_id=$1",
            pid) == 1
        assert await conn.fetchval(
            "SELECT written_at FROM rn1x_outcomes WHERE position_id=$1",
            pid) == written_at

        # AND THE DECISION THAT PRECEDED IT IS UNTOUCHED.
        assert await decisions() >= d0
        ev = await conn.fetchrow(
            "SELECT ev_at_decision_usd, ev_basis FROM rn1x_decisions "
            "WHERE position_id = $1 ORDER BY decision_ts LIMIT 1", pid)
        assert "EXTERNAL_BOOKMAKER_VALUATION" in ev["ev_basis"]

        # ── AND IT IS NO LONGER OPEN ─────────────────────────────────
        # The exposure rails must stop counting a settled position, which
        # is what lets the lane enter again.
        rows = await loop.open_shadow_book(conn, ext.EXPERIMENT_ID)
        exp = entryx.exposure_from_rows(
            rows, condition_id="other", event_key="other",
            proposed_cost_usd=10.0, proposed_qty=10.0, now=time.time())
        assert exp["settled_positions_excluded_from_exposure"] >= 1
        assert exp["observed"]["MAX_CAPITAL_DEPLOYED"] == pytest.approx(10.0)
    finally:
        # THIS TEST'S OUTCOME ROW IS ITS OWN TO REMOVE. `rn1x_outcomes`
        # references the position, so a row left here made an unrelated
        # suite's teardown fail on a foreign key -- thirty-four tests red
        # for a fixture that had nothing to do with them. A settlement row
        # is meant to outlive its decision; that is the schema working, so
        # the cleanup is explicit rather than the constraint being relaxed.
        await _cleanup(conn)
        await conn.close()
