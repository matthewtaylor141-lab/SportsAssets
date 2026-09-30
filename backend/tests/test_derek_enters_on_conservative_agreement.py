"""DEREK'S ENTRY POLICY, PROVEN THROUGH THE SCHEDULED CYCLE (owner proofs 1, 2).

PROOF 1. A Yankees moneyline at $0.50 whose de-vigged Pinnacle probability is
0.60 AND whose APPROVED internal model clears the threshold reaches a Derek
ENTER decision -- through `derek_policy.gate_for_funded_entry` on the record
`_funded_attempt` receives from the real `ext_pinnacle_loop.cycle()`, and
through `derek.after_cycle` alone -- with the gross edge in probability points,
the fees from `bettor_funded_book.fee_for`, the net EV and the return on
deployed capital all exact.

PROOF 2. Exactly 5 pp enters; 4.99 pp refuses BELOW_MIN_GROSS_EDGE; an edge
the fees erase refuses NET_EV_NOT_POSITIVE_AFTER_FEES; Pinnacle clearing while
the model does not refuses ESTIMATES_DISAGREE...; no approved model refuses
NO_APPROVED_INTERNAL_MODEL; a stale quote (the lane's own freshness rule, a
controlled clock) refuses PROBABILITY_EVIDENCE_STALE; unsupported settlement
refuses SETTLEMENT_NOT_SUPPORTED.

WHAT IS CONTROLLED, AND WHAT IS SYNTHETIC. As in
test_the_entry_lane_reaches_inventory: the odds payload, the venue ladder, the
venue's rules prose and the fixture metadata are supplied at their transport
boundaries; everything between them is production code. The internal model is
SYNTHETIC TEST EVIDENCE: valuation rows generated from a stated rule (P(win) at
price 0.30 / 0.50 / 0.70 = 0.48 / 0.76 / 0.96, 25 fixtures per price -- strong
enough that the model beats both the raw venue price and its base rate beyond
the clustered-jackknife uncertainty, which KEY_ENTRY_PAYOUT's promotion now
requires; the earlier 0.35 / 0.60 / 0.85 on 20 fixtures per price did not),
decided by `derek.after_cycle`
(which writes the decision-time vector), labelled by the outcome columns the
production join writes, fit by `bettor_funded_model.fit_from_records`,
registered, evaluated prospectively and promoted ONLY by `promote` with a named
approver -- never a hand-inserted APPROVED row.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import time
from pathlib import Path

import pytest

from sportsassets import bettor_entry_inventory as inv
from sportsassets import bettor_external_shadow as ext
from sportsassets import bettor_funded_book as FB
from sportsassets import bettor_funded_model as FM
from sportsassets import bettor_settlement_terms as ST
from sportsassets.agents import derek as D
from sportsassets.agents import derek_policy as DP
from sportsassets.workers import ext_pinnacle_loop as loop

DSN = os.environ.get("RN1X_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="RN1X_TEST_DSN is not set")

BACKEND = Path(__file__).resolve().parents[1]
CONDITION = "c-derek-bos-nyy"
SLUG = "bos-nyy-derek"
EVENT_SLUG = "mlb-bos-nyy-2026-10-01"
US_SLUG = "aec-mlb-bos-nyy-2026-10-01-nyy"
EVENT_KEY = "evt-derek-nyy-1"
TITLE = "Boston Red Sox vs. New York Yankees"
SYN = "derek-test-syn-"
APPROVER = "owner@test (SYNTHETIC APPROVAL OF SYNTHETIC EVIDENCE)"

#: The venue prose that agrees with Pinnacle's pre-game baseball terms
#: (the same text the entry-lane harness uses).
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
INCOMPATIBLE_PROSE = (
    "This market settles on the final result of the game, including any "
    "extra innings. If the game is called (ended) after at least five "
    "innings the market is void and stakes are returned. If the game is "
    "abandoned or postponed and never completed the market is void and "
    "stakes are returned.")

#: ONE LEVEL AT $0.50, deep enough that the whole sized quantity is bought at
#: exactly the owner's price.
LADDER = {
    "ok": True, "side_consumed": "ASK", "pays_on": "THE_PRICED_OUTCOME",
    "best_acquisition_price": 0.50, "best_api_price": 0.50,
    "displayed_depth": 5000.0, "levels_published": 1, "levels_read": 1,
    "levels": [{"acquisition_price": 0.50, "qty": 5000.0}],
}


def _iso(epoch):
    return _dt.datetime.fromtimestamp(epoch, tz=_dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(p_home: float, *, stamp_at: float):
    """A two-way MLB h2h payload whose power de-vig gives exactly `p_home`
    for the home side (zero overround, so k = 1)."""
    prices = [{"name": "New York Yankees", "price": 1.0 / p_home},
              {"name": "Boston Red Sox", "price": 1.0 / (1.0 - p_home)}]
    stamp = _iso(stamp_at)
    return {"id": EVENT_KEY, "home_team": "New York Yankees",
            "away_team": "Boston Red Sox",
            "commence_time": "2026-10-01T23:05:00Z",
            "bookmakers": [
                {"key": "pinnacle", "last_update": stamp,
                 "markets": [{"key": "h2h", "last_update": stamp,
                              "outcomes": prices}]},
                {"key": "smarkets", "last_update": stamp,
                 "markets": [{"key": "h2h", "outcomes": prices}]}]}


async def _ensure_schema(conn):
    await conn.execute((BACKEND / "migrations" / "153_derek.sql").read_text())
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS us_premap (
            identifier text PRIMARY KEY, event_slug text, event_title text,
            market_slug text, question text, kind text, line text,
            side_norm text, event_keys text[], intent text, signed text,
            sports_type text, updated_at timestamptz NOT NULL DEFAULT now())
    """)
    await conn.execute("CREATE TABLE IF NOT EXISTS ingestion_state "
                       "(key TEXT PRIMARY KEY, value TEXT)")


async def _purge_decisions(conn):
    async with conn.transaction():
        await conn.execute("SET LOCAL session_replication_role = replica")
        await conn.execute(
            "DELETE FROM derek_entry_decisions WHERE us_market_slug LIKE $1 "
            "   OR us_market_slug = $2", SYN + "%", US_SLUG)


async def _cleanup(conn):
    await _purge_decisions(conn)
    for sql, args in (
            ("DELETE FROM bettor_funded_models WHERE model_key = $1",
             (FM.KEY_ENTRY_PAYOUT,)),
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
            ("DELETE FROM rn1x_positions WHERE policy = $1", (inv.POLICY,)),
            ("DELETE FROM external_valuations WHERE us_market_slug LIKE $1 "
             "   OR condition_id = $2 OR condition_id LIKE $1",
             (SYN + "%", CONDITION)),
            ("DELETE FROM fixture_metadata WHERE condition_id = $1",
             (CONDITION,)),
            ("DELETE FROM markets WHERE condition_id = $1", (CONDITION,)),
            ("DELETE FROM us_premap WHERE event_slug = $1", (EVENT_SLUG,)),
            ("DELETE FROM external_source_calibration WHERE measured_by = $1",
             ("CONTROLLED_INTEGRATION_TEST",))):
        try:
            await conn.execute(sql, *args)
        except Exception:                                      # noqa: BLE001
            pass


async def _seed(conn):
    await _ensure_schema(conn)
    await _cleanup(conn)
    for side in ("nyy", "bos"):
        await conn.execute(
            "INSERT INTO us_premap (identifier, event_slug, market_slug, kind, "
            " side_norm, question, event_title, sports_type, intent) "
            "VALUES ($1,$2,$1,'side',$3,$4,$5,"
            " 'baseball_team_full_game_winner','ORDER_INTENT_BUY_LONG') "
            "ON CONFLICT DO NOTHING",
            "aec-mlb-bos-nyy-2026-10-01-%s" % side, EVENT_SLUG, side,
            "Will %s win?" % side, TITLE)
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1,'true') "
        "ON CONFLICT (key) DO UPDATE SET value = 'true'", loop.CONTROL_KEY)
    await conn.execute(
        "INSERT INTO markets (condition_id, title, event_title, slug, sport, "
        " closed, resolved) VALUES ($1,$2,$3,$4,'MLB',false,false) "
        "ON CONFLICT (condition_id) DO UPDATE SET sport = 'MLB', "
        " closed = FALSE, resolved = FALSE, updated_at = now()",
        CONDITION, "Will Boston Red Sox beat New York Yankees?", TITLE, SLUG)
    await conn.execute(
        "INSERT INTO fixture_metadata (condition_id, phase, game_format, "
        " scheduled_innings, play_has_begun, event_state_raw, start_evidence, "
        " game_pk, official_date, home_team, away_team, source, source_url, "
        " retrieved_at, reader_version) "
        "VALUES ($1,$2,$3,9,false,'Pre-Game',$4,824777,'2026-10-01',"
        " 'New York Yankees','Boston Red Sox','MLB_STATS_API',"
        " 'https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-10-01',"
        " now(),'test') "
        "ON CONFLICT (condition_id) DO UPDATE SET phase = EXCLUDED.phase, "
        " game_format = EXCLUDED.game_format, play_has_begun = FALSE, "
        " retrieved_at = now()",
        CONDITION, ST.PHASE_REGULAR, ST.FMT_NINE, ST.SE_ACTUAL_REPORTED)
    await conn.execute(
        "INSERT INTO market_tokens (token_id, condition_id, outcome, "
        " outcome_index) VALUES ($1,$2,$3,$4),($5,$2,$6,$7) "
        "ON CONFLICT (token_id) DO UPDATE SET outcome = EXCLUDED.outcome, "
        " outcome_index = EXCLUDED.outcome_index",
        "tok-derek-nyy", CONDITION, "New York Yankees", 0,
        "tok-derek-bos", "Boston Red Sox", 1)
    # THE ONE THING PRODUCTION DOES NOT HAVE: a calibration measurement of the
    # external source (the MODEL_TRUST_DRIFT gate), labelled as a test's.
    from sportsassets import bettor_pinnacle_devig as devig
    from sportsassets import bettor_source_calibration as SCAL
    await conn.execute(
        "INSERT INTO external_source_calibration (source_version, "
        " measured_at, window_start, window_end, sample_size, metric, score, "
        " tolerance, within_tolerance, measured_by, provenance) "
        "VALUES ($1, now(), now() - interval '30 days', now(), 412, 'BRIER', "
        " 0.2104, 0.2400, TRUE, 'CONTROLLED_INTEGRATION_TEST', $2::jsonb) "
        "ON CONFLICT (source_version, measured_at) DO NOTHING",
        devig.VERSION, json.dumps({"evaluator": SCAL.VERSION,
                                   "supplied_by": "TEST_FIXTURE"}))


def _stub(monkeypatch, *, p_home=0.60, stamp_age_s=2.0, prose=VENUE_PROSE,
          ladder=LADDER):
    monkeypatch.setenv("EDGE_ODDS_API_KEY", "x" * 32)

    async def fake_fetch(sport_key, *, api_key, timeout=20.0):
        t = time.time()
        if sport_key != "baseball_mlb":
            return {"ok": True, "events": [], "received_at": t,
                    "credits_used": "1", "credits_remaining": "9"}
        return {"ok": True, "events": [_event(p_home,
                                              stamp_at=t - stamp_age_s)],
                "received_at": t, "credits_used": "1",
                "credits_remaining": "9"}

    monkeypatch.setattr(loop, "fetch_odds", fake_fetch)
    from sportsassets.workers import premap as _pm

    async def fake_resolve(conn_, title, event_title, outcome, slug, **kw):
        return {"market_slug": US_SLUG, "outcome": "nyy",
                "title": "Will the New York Yankees beat the Boston Red Sox?",
                "matched_by": "premap_identity", "score": 1.0,
                "intent": "ORDER_INTENT_BUY_LONG"}

    monkeypatch.setattr(_pm, "resolve", fake_resolve)

    async def fake_quote(conn_, *, us_slug, intent, now, size=None,
                         subscription=None, revalidation=None):
        # A LIVE SUBSCRIPTION (M1) whose last update for this market arrived
        # at the read: the mechanism that admits a book, stated.
        return {"ok": True, "ask": 0.50, "api_price": 0.50,
                "acquisition_price": 0.50, "side_consumed": "ASK",
                "pays_on": "THE_PRICED_OUTCOME", "intent": intent,
                "levels_read": 1, "depth": 5000.0, "sized": None,
                "acquisition_ladder": ladder, "venue_ts": now - 1.0,
                "age_s": 1.0, "age_basis": "VENUE_TRANSACT_TIME",
                "subscription": {"alive_at": now, "last_update_at": now},
                "bid": None, "read_at": now, "slug": us_slug}

    monkeypatch.setattr(loop, "venue_quote", fake_quote)

    def fake_rules(slug, *, now=None):
        return {"ok": True, "rules_text": prose, "slug": slug,
                "read_at": now, "from_cache": False,
                "source": "pmus:/markets?slug=<slug>:rules_text"}

    monkeypatch.setattr(loop, "_read_venue_rules_blocking", fake_rules)
    loop.rules_cache_reset()


def _wrap_funded_attempt(monkeypatch, captured: list, *, gate=True):
    """WHAT THE CORE STREAM WIRES: Derek's gate, called with the record
    `_funded_attempt` receives, BEFORE any submission. The original is then
    called unchanged (it returns None here: no funded account is bound)."""
    original = loop._funded_attempt

    async def wrapped(conn, rec, *, now):
        got = (await DP.gate_for_funded_entry(conn, rec, now=now)
               if gate else None)
        captured.append({"rec": copy.deepcopy(rec), "now": now,
                         "gate": got})
        return await original(conn, rec, now=now)

    monkeypatch.setattr(loop, "_funded_attempt", wrapped)


# ── THE SYNTHETIC APPROVED MODEL, THROUGH THE REGISTRY ───────────────────

RULE = {0.30: 12, 0.50: 19, 0.70: 24}      # wins out of 25 fixtures per price
PER_PRICE = 25


async def _synthetic_valuation(conn, *, n: int, tag: str, price: float,
                               decided_at: float) -> int:
    cid = "%s%s-%03d" % (SYN, tag, n)
    return await conn.fetchval(
        "INSERT INTO external_valuations (experiment_id, version, "
        " source_class, provider, book, devig_method, venue, condition_id, "
        " us_market_slug, contract_selection, sport_family, market, period, "
        " raw_odds, outcomes_priced, expected_outcomes, observed_at, "
        " received_at, probability, executable_price, cost_per_contract, "
        " decision, admissible, refusals, why, payout_event, buy_intent, "
        " record_purpose, decided_at, event_key) "
        "VALUES ($1,'PINNACLE_DEVIG_V1','EXTERNAL_BOOKMAKER_VALUATION',"
        " 'the-odds-api.com/v4','pinnacle','power','PMUS',$2,$2,"
        " 'SYNTHETIC HOME','baseball','h2h','FULL_GAME','{}'::jsonb,2,2,"
        " to_timestamp($3 - 5),to_timestamp($3 - 4),$4,$5,0.0175,'NO_TRADE',"
        " false, ARRAY['SYNTHETIC_TRAINING_RECORD'],'synthetic test evidence',"
        " 'SYNTHETIC HOME','ORDER_INTENT_BUY_LONG','ENTRY_DECISION',"
        " to_timestamp($3),$2) RETURNING id",
        ext.EXPERIMENT_ID, cid, float(decided_at), min(0.99, price + 0.1),
        float(price))


async def _cohort(conn, *, tag: str, start: float, resolve_at: float) -> list:
    """One valuation per fixture, decided by Derek's after-cycle pass (which
    writes each decision-time vector), then labelled by the columns the
    production outcome join writes."""
    ids, n = [], 0
    for price, wins in RULE.items():
        for i in range(PER_PRICE):
            vid = await _synthetic_valuation(conn, n=n, tag=tag, price=price,
                                             decided_at=start + n)
            ids.append((vid, 1 if i < wins else 0))
            n += 1
    got = await D.after_cycle(conn, cycle={"elapsed_s": 0.0},
                              now=start + n + 1)
    assert got["decisions_recorded"] == n, got
    for vid, won in ids:
        await conn.execute(
            "UPDATE external_valuations SET outcome_known = TRUE, outcome = $2,"
            " outcome_at = to_timestamp($3), "
            " outcome_basis = 'VENUE_SETTLEMENT_PRICE' WHERE id = $1",
            vid, won, float(resolve_at))
    return ids


async def _approve_entry_model(conn) -> dict:
    now = time.time()
    await _cohort(conn, tag="train", start=now - 3 * 86400,
                  resolve_at=now - 2 * 86400)
    cutoff = _dt.datetime.fromtimestamp(now - 86400, tz=_dt.timezone.utc)
    fitted = await FM.fit_from_records(
        conn, through=cutoff, model_key=FM.KEY_ENTRY_PAYOUT,
        source=FM.SOURCE_ENTRY_DECISIONS)
    assert fitted.get("ok"), fitted
    assert fitted["training_provenance"]["n_events"] == 3 * PER_PRICE
    reg = await FM.register(conn, model_id="derek-entry-test-v1",
                            model_version="derek-entry-test-v1",
                            fitted=fitted, fit_through=cutoff,
                            model_key=FM.KEY_ENTRY_PAYOUT)
    assert reg.get("ok"), reg
    # PROSPECTIVE EVIDENCE: decided and resolved AFTER the model was frozen.
    t = time.time() + 120.0
    await _cohort(conn, tag="pros", start=t, resolve_at=t + 3600.0)
    ev = await FM.evaluate(conn, model_id="derek-entry-test-v1")
    assert ev.get("ok"), ev
    prom = await FM.promote(conn, model_id="derek-entry-test-v1",
                            approved_by=APPROVER)
    assert prom.get("ok"), prom
    ap = await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT)
    assert ap["ok"] and ap["provenance_verified"], ap
    return ap


async def _decision_for(conn, valuation_id):
    r = await conn.fetchrow(
        "SELECT * FROM derek_entry_decisions WHERE valuation_id = $1",
        valuation_id)
    return None if r is None else dict(r)


def _checks(row) -> dict:
    cs = row["checks"]
    cs = json.loads(cs) if isinstance(cs, str) else cs
    return {c["check"]: c for c in cs}


def _perturbed(rec, **fields):
    """The same admitted record with ONE economic input changed and no
    valuation link, so each boundary case is its own decision."""
    out = copy.deepcopy(rec)
    out.pop("valuation_row_id", None)
    out.update(fields)
    return out


# ═════════════════════════════════════════════════════════════════════════
# THE OWNER'S WORKED EXAMPLE (pure arithmetic, fee_for exact)
# ═════════════════════════════════════════════════════════════════════════

def test_the_owners_worked_example_in_explicit_units():
    at = 1790000000.0
    for qty in (1.0, 100.0):
        e = DP.economics(p_pinnacle=0.60, p_model=0.60, fills=[(0.50, qty)],
                         at=at)
        h = e["headline"]
        fee, _basis = FB.fee_for(qty, 0.50, at=at)
        assert h["gross_edge_pp"] == pytest.approx(0.10, abs=1e-12)
        assert h["gross_edge_percentage_points"] == pytest.approx(10.0)
        assert h["expected_gross_profit_usd"] == pytest.approx(0.10 * qty)
        assert h["expected_gross_return_on_cost"] == pytest.approx(0.20)
        assert e["fees_usd"] == pytest.approx(fee)
        assert h["expected_net_profit_usd"] == pytest.approx(0.10 * qty - fee)
        assert h["expected_net_roi"] == pytest.approx(
            (0.10 * qty - fee) / (0.50 * qty + fee))
        assert h["expected_net_roi"] < h["expected_gross_return_on_cost"]
    # THE THRESHOLD IS PROBABILITY POINTS, NOT RETURN: 5 pp at $0.90 is a
    # 5.6% gross return and still qualifies; 4.99 pp at $0.10 is a 49.9%
    # return and does not.
    assert DP.clears(DP.gross_edge(0.95, 0.90), 0.05)
    assert not DP.clears(DP.gross_edge(0.1499, 0.10), 0.05)


def test_the_float_boundary_is_handled_deliberately():
    # 0.60 - 0.55 is 0.04999999999999993 in binary floating point.
    assert (0.60 - 0.55) < 0.05
    assert DP.gross_edge(0.60, 0.55) == 0.05
    assert DP.clears(DP.gross_edge(0.60, 0.55), 0.05)
    assert DP.clears(0.05 - 5e-10, 0.05)          # inside the 1e-9 tolerance
    assert not DP.clears(0.0499, 0.05)
    assert not DP.clears(0.05 - 2e-9, 0.05)


# ═════════════════════════════════════════════════════════════════════════
# PROOF 1
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_proof1_the_yankees_at_50c_on_qualified_60pct_reach_enter(
        monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        ap = await _approve_entry_model(conn)
        _stub(monkeypatch, p_home=0.60)
        captured: list = []
        _wrap_funded_attempt(monkeypatch, captured)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        assert out["order_submitted"] is False
        assert len(captured) == 1, captured
        gate = captured[0]["gate"]
        rec = captured[0]["rec"]
        assert gate["verdict"] == DP.ENTER, (gate.get("refusal"),
                                             gate.get("decision", {})
                                             .get("all_refusals"))
        assert gate["refusal"] is None and gate["recorded"] is True
        # THE EXECUTION AUTHORITY STILL STANDS IN THE WAY, BY NAME.
        blockers = {b["check"]: b for b in
                    gate["execution_authority_blockers"]}
        assert blockers[DP.C_SUBMISSION]["dependency"] == \
            DP.DEP_OWNER_DECISION
        assert gate["decision"]["sendable_now"] is False

        row = await _decision_for(conn, rec["valuation_row_id"])
        assert row is not None and row["verdict"] == DP.ENTER
        assert row["decided_by"] == DP.DECIDED_BY_GATE
        assert row["policy_version"] == DP.POLICY_VERSION
        assert row["pinnacle_p"] == pytest.approx(0.60, abs=1e-12)
        assert row["pinnacle_qualification"] == "FRESH"
        mp = row["model_p"]
        m = DP.model_estimate(ap, DP.candidate_from_rec(
            rec, now=captured[0]["now"]), at=captured[0]["now"])
        assert mp == pytest.approx(m["p"]) and mp >= 0.60, mp
        assert row["model_version"] == "derek-entry-test-v1"
        assert row["executable_price"] == pytest.approx(0.50)
        qty = float(row["qty"])
        assert qty >= 1 and qty == int(qty)
        fee, _ = FB.fee_for(qty, 0.50, at=captured[0]["now"])
        assert row["gross_edge_pp"] == pytest.approx(0.10, abs=1e-12)
        assert row["expected_gross_profit_usd"] == pytest.approx(0.10 * qty)
        assert row["fees_usd"] == pytest.approx(fee)
        assert row["expected_net_profit_usd"] == pytest.approx(
            0.10 * qty - fee)
        assert row["expected_net_roi"] == pytest.approx(
            (0.10 * qty - fee) / (0.50 * qty + fee))
        ev = json.loads(row["evidence"])
        assert ev["economics"]["headline"]["expected_gross_return_on_cost"] \
            == pytest.approx(0.20)
        assert ev["estimates"]["model"]["qualification"] == "FRESH"
        assert ev["combination_policy"] == DP.COMBINATION_POLICY
        # EVERY PRE-PURCHASE CHECK IS RECORDED, BY NAME, WITH A STATUS.
        cks = _checks(row)
        for name in DP.PRE_PURCHASE_CHECKS:
            assert name in cks, name
            assert cks[name]["status"] in (DP.PASS, DP.FAIL, DP.UNKNOWN)
        for c in cks.values():
            if c["blocks"] == DP.BLOCKS_POLICY:
                assert c["status"] == DP.PASS, c
        assert cks[DP.C_TICK]["evidence"]["limit_price"] == pytest.approx(0.5)
        lat = json.loads(row["latency"])
        assert lat["source_to_decision_s"] is not None
        assert lat["send"] == {"status": "EMPTY",
                               "why": "no funded order has been sent"}

        # AND THE AFTER-CYCLE PASS DOES NOT RE-DECIDE IT.
        res = await D.after_cycle(conn, cycle=out, now=time.time())
        assert res["already_recorded"] == 0 and \
            res["decisions_recorded"] == 0, res
        assert res["census"]["ok"] is True
        n = await conn.fetchval(
            "SELECT count(*) FROM derek_entry_decisions WHERE valuation_id "
            "= $1", rec["valuation_row_id"])
        assert n == 1

        # THE WORKSPACE SHOWS IT, WITH ITS EVIDENCE LINK.
        from sportsassets.api import agents_derek as A
        ws = await A.workspace(conn)
        assert ws["sections"]["decisions"]["status"] == "OK"
        # (the synthetic prospective cohort is dated after this decision, so
        # read past the workspace's 50 newest)
        dec = await A.decisions(conn, limit=500)
        mine = [d for d in dec["data"] if d["decision_id"] ==
                row["decision_id"]]
        assert mine and mine[0]["verdict"] == DP.ENTER
        assert any(e["href"].endswith(row["decision_id"])
                   for e in dec["evidence"])
        assert ws["sections"]["plans_fills"]["status"] == "EMPTY"
        assert ws["sections"]["plans_fills"]["why"] == \
            "no funded order has been sent"
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
async def test_proof1_after_cycle_alone_reaches_the_same_enter(monkeypatch):
    """No funded gate wired: the after-cycle pass judges the cycle's own row
    AS OF ITS DECISION INSTANT and records the same verdict.

    INTEGRATED WIRING: `cycle()` itself calls `derek.after_cycle` at its end
    (core hook), so the decision already exists when the test's own call
    runs; that second call must record NOTHING new (one decision per
    valuation per policy version) while the row carries the same verdict."""
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        await _approve_entry_model(conn)
        _stub(monkeypatch, p_home=0.60)
        captured: list = []
        _wrap_funded_attempt(monkeypatch, captured, gate=False)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        vid = captured[0]["rec"]["valuation_row_id"]
        row = await _decision_for(conn, vid)
        assert row is not None, "the cycle's own after_cycle hook recorded nothing"
        res = await D.after_cycle(conn, cycle=out, now=time.time())
        assert res["decisions_recorded"] == 0, res      # idempotent replay
        assert await _decision_for(conn, vid) == row
        assert row["decided_by"] == DP.DECIDED_BY_CYCLE
        assert row["verdict"] == DP.ENTER, (row["refusal"],
                                            json.loads(row["evidence"])
                                            ["all_refusals"])
        assert row["gross_edge_pp"] == pytest.approx(0.10, abs=1e-12)
        qty = float(row["qty"])
        fee, _ = FB.fee_for(qty, 0.50)
        assert row["expected_net_profit_usd"] == pytest.approx(
            0.10 * qty - fee)
    finally:
        await _cleanup(conn)
        await conn.close()


# ═════════════════════════════════════════════════════════════════════════
# PROOF 2
# ═════════════════════════════════════════════════════════════════════════

@pg
async def test_proof2_the_threshold_the_fees_and_the_agreement(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        ap = await _approve_entry_model(conn)
        _stub(monkeypatch, p_home=0.60)
        captured: list = []
        _wrap_funded_attempt(monkeypatch, captured)
        await loop.cycle(conn)
        rec, now = captured[0]["rec"], captured[0]["now"]

        # EXACTLY 5 pp ENTERS.
        got = await DP.gate_for_funded_entry(
            conn, _perturbed(rec, probability=0.55), now=now)
        assert got["verdict"] == DP.ENTER, got.get("refusal")
        hd = got["decision"]["economics"]["headline"]
        assert hd["gross_edge_pp"] == 0.05

        # 4.99 pp REFUSES, BY NAME.
        got = await DP.gate_for_funded_entry(
            conn, _perturbed(rec, probability=0.5499), now=now)
        assert (got["verdict"], got["refusal"]) == (DP.REFUSE, DP.R_BELOW)

        # AN EDGE THE FEES ERASE: 1 pp gross clears a 1 pp threshold (a
        # separately versioned override; the code default is untouched), and
        # fee_for at $0.50 is ~1.7 cents a contract.
        got = await DP.gate_for_funded_entry(
            conn, _perturbed(rec, probability=0.51), now=now,
            params={"min_gross_edge_pp": 0.01})
        assert (got["verdict"], got["refusal"]) == (DP.REFUSE, DP.R_NET)
        assert got["decision"]["economics"]["headline"][
            "expected_gross_profit_usd"] > 0
        assert DP.DEFAULT_PARAMS["min_gross_edge_pp"] == 0.05

        # THE SEPARATELY NAMED NET THRESHOLD.
        got = await DP.gate_for_funded_entry(
            conn, _perturbed(rec, probability=0.55), now=now,
            params={"min_net_ev_usd": 1e9})
        assert (got["verdict"], got["refusal"]) == (DP.REFUSE, DP.R_BELOW_NET)

        # DISAGREEMENT: Pinnacle clears and the model does not. The policy
        # function is pure in its model input, so the model's answer is
        # stated here (the registry read path is proof 1's).
        cand = DP.candidate_from_rec(rec, now=now)
        real = DP.model_estimate(ap, cand, at=now)
        low = dict(real, p=0.52)
        dec = DP.evaluate(cand, model=low, authority=[])
        assert (dec["verdict"], dec["refusal"]) == (DP.REFUSE, DP.R_DISAGREE)
        assert dec["economics"]["pinnacle"]["clears_min_gross_edge"] is True
        assert dec["economics"]["model"]["clears_min_gross_edge"] is False
        # NEVER AVERAGED: (0.60 + 0.52) / 2 = 0.56 would have cleared.
        assert DP.clears(DP.gross_edge((0.60 + 0.52) / 2, 0.50), 0.05)
        # And the same candidate with the real approved model enters.
        dec = DP.evaluate(cand, model=real, authority=[])
        assert dec["verdict"] == DP.ENTER, dec["all_refusals"]
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
async def test_proof2_no_approved_model_and_a_stale_quote_refuse(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        assert (await FM.approved(conn, model_key=FM.KEY_ENTRY_PAYOUT))[
            "refusal"] == FM.R_NO_APPROVED_MODEL
        # The Pinnacle stamp is 25 s old at the fetch: inside the lane's 30 s
        # rule when the cycle decides.
        _stub(monkeypatch, p_home=0.60, stamp_age_s=25.0)
        captured: list = []
        _wrap_funded_attempt(monkeypatch, captured)
        out = await loop.cycle(conn)
        assert out["refusals"].get("ADMITTED") == 1, out["refusals"]
        gate, rec, now = (captured[0]["gate"], captured[0]["rec"],
                          captured[0]["now"])
        # NO APPROVED MODEL: refused by name; Pinnacle alone is never enough.
        assert (gate["verdict"], gate["refusal"]) == (DP.REFUSE,
                                                      DP.R_NO_MODEL)
        pin = gate["decision"]["economics"]["pinnacle"]
        assert pin["clears_min_gross_edge"] is True
        assert gate["decision"]["estimates"]["model"]["p"] is None
        row = await _decision_for(conn, rec["valuation_row_id"])
        assert row["verdict"] == DP.REFUSE and row["refusal"] == DP.R_NO_MODEL
        assert row["model_p"] is None
        assert _checks(row)[DP.C_MODEL]["dependency"] == DP.DEP_ENGINEERING

        # STALE, BY THE LANE'S OWN RULE ON A CONTROLLED CLOCK: six seconds
        # later the provider's quote is past PINNACLE_MAX_AGE_S while the
        # venue book (subscription) is still established.
        later = now + 6.0
        got = await DP.gate_for_funded_entry(conn, _perturbed(rec), now=later)
        assert (got["verdict"], got["refusal"]) == (DP.REFUSE, DP.R_STALE)
        fr = got["decision"]["checks"]
        prob = [c for c in fr if c["check"] == DP.C_PROBABILITY][0]
        assert prob["status"] == DP.FAIL
        assert prob["evidence"]["freshness"]["pinnacle_qualification"] == \
            "STALE"
        assert prob["evidence"]["freshness"]["pinnacle_age_s"] > \
            loop.PINNACLE_MAX_AGE_S
        assert prob["evidence"]["freshness"]["basis"] == \
            "RE_EVALUATED_AT_THE_GATE_INSTANT"
        # AT THE DECISION INSTANT ITSELF IT WAS FRESH.
        at_now = await DP.gate_for_funded_entry(conn, _perturbed(rec),
                                                now=now)
        assert at_now["refusal"] == DP.R_NO_MODEL
    finally:
        await _cleanup(conn)
        await conn.close()


@pg
async def test_proof2_unsupported_settlement_refuses(monkeypatch):
    asyncpg = pytest.importorskip("asyncpg")
    conn = await asyncpg.connect(DSN)
    try:
        await _seed(conn)
        _stub(monkeypatch, p_home=0.60, prose=INCOMPATIBLE_PROSE)
        captured: list = []
        _wrap_funded_attempt(monkeypatch, captured)
        out = await loop.cycle(conn)
        assert "ADMITTED" not in out["refusals"], out["refusals"]
        assert captured == []           # nothing reached the funded path
        # The cycle's own after_cycle hook (core wiring) has recorded the
        # refusal; a repeat call records nothing new.
        res = await D.after_cycle(conn, cycle=out, now=time.time())
        assert res["decisions_recorded"] == 0, res
        vid = await conn.fetchval(
            "SELECT max(id) FROM external_valuations WHERE us_market_slug = $1"
            "   AND record_purpose = 'ENTRY_DECISION'", US_SLUG)
        row = await _decision_for(conn, vid)
        assert row["verdict"] == DP.REFUSE
        assert row["refusal"] == DP.R_SETTLEMENT, row["refusal"]
        c = _checks(row)[DP.C_SETTLEMENT]
        assert c["status"] == DP.FAIL and c["dependency"] == DP.DEP_EVIDENCE
    finally:
        await _cleanup(conn)
        await conn.close()


def test_a_gate_that_raises_refuses_with_the_exception_type():
    import asyncio

    class _Broken:
        async def fetchval(self, *a, **k):
            raise ConnectionError("gone")

        async def fetchrow(self, *a, **k):
            raise ConnectionError("gone")

        async def fetch(self, *a, **k):
            raise ConnectionError("gone")

        async def execute(self, *a, **k):
            raise ConnectionError("gone")

    got = asyncio.run(DP.gate_for_funded_entry(_Broken(), {"contract": {}},
                                               now=1790000000.0))
    assert got["verdict"] == DP.REFUSE
    assert got["refusal"].startswith((DP.R_RAISED, DP.R_NOT_RECORDED))
    assert got["refusal"].split(":")[-1] in ("ConnectionError",
                                             "TypeError", "KeyError",
                                             "AttributeError")
