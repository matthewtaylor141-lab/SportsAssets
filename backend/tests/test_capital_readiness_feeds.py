"""CAPITAL-CRITICAL: THE CAPITAL READINESS LAB FEEDS (RESEARCH /
SHADOW_NO_AUTHORITY).

capital_readiness/feeds.py reads existing BETTOR evidence;
capital_readiness/observer.py turns it into Shadow Court, agent economics,
scale twin and readiness rows through runner.py only (migration 310,
append-only). These proofs pin:

  * court rows from seeded 309 evaluations always carry CASH and use only
    values (and models) frozen at the decision;
  * an outcome is a NEW court row, never an update;
  * agent observations are idempotent and role-metric specific, with the
    right signs for DEREK (discovery), XAVIER (management) and the
    capital-authority refusal gate (prevented loss);
  * the scale twin never exceeds measured capacity and records nothing
    without it;
  * readiness gates fail closed and RED forces $0;
  * the observer writes to the four 310 tables and nothing else;
  * the package and its API carry no execution / authority token;
  * GET /api/command/capital-readiness serves championship / court /
    scale / readiness and is GET-only.

ALL DATA IS SYNTHETIC, under fresh paper test accounts, inside transactions
the tests roll back (310 rows are append-only, so the GET proof commits a
few uniquely keyed ones).
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import uuid

import pytest

from sportsassets.capital_readiness import feeds as F
from sportsassets.capital_readiness import observer as O
from sportsassets.capital_readiness import readiness as R
from sportsassets.capital_readiness import runner as RUN
from sportsassets.capital_readiness import scale_twin as ST

try:
    from tests import intel_fixture as IF
    from tests import paper_harness as H
except ImportError:                                           # pragma: no cover
    import intel_fixture as IF
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
# far from every other suite's instants: no foreign row enters a window
NOW = 1_950_000_000.0
HOUR = 3600.0
DEREK = "DEREK_ENTRY_POLICY_V2"
SIM = "PAPER_SIM_V1"


def run(coro):
    return asyncio.run(coro)


def uid(tag="x"):
    return "%s%s" % (tag, uuid.uuid4().hex[:10])


async def _ok(conn, ctx):
    return {"value": True, "reason": None, "evidence": {"stub": True}}


GREEN = {g: _ok for g in R.HARD_GATES}


async def _session():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    acct = await H.new_account(conn, "crl", now=NOW - 30 * 86400)
    return conn, tr, acct


async def model(conn, acct, kind, payload, *, at, n=0):
    return await conn.fetchval(
        "INSERT INTO paper_profitability_models (account_id, kind, version, "
        " observations, payload, fitted_at) VALUES ($1,$2,'TEST',$3,"
        " $4::jsonb, to_timestamp($5)) RETURNING model_id",
        acct["account_id"], kind, n, json.dumps(payload), float(at))


def cal_payload(n=300, brier=0.20):
    bins = {"2": {"n": n // 2, "mean_p": 0.40, "observed": 0.41},
            "4": {"n": n - n // 2, "mean_p": 0.60, "observed": 0.60}}
    return {"cells": {"baseball|MONEYLINE|PRE_GAME_1H_TO_24H": {
        "n": n, "brier": brier, "bins": bins}}}


def exe_payload(fills=100, strategy=DEREK):
    return {"by_strategy_style": {"%s|TAKER" % strategy: {
        "strategy": strategy, "style": "TAKER", "fills": fills}}}


async def evaluation(conn, acct, *, at, verdict="ENTER", slug=None,
                     side="LONG", qty_in=10, qty_out=None, p=0.60,
                     market=0.40, ev=1.5, pf=0.9, hold=3.0, cap_qty=50,
                     order_key=None, decision_id=None, strategy=DEREK,
                     stage="LEDGER"):
    slug = slug or uid("crl-mkt-")
    if qty_out is None:
        qty_out = qty_in if verdict == "ENTER" else 0
    detail = {"bind": {"qty": qty_in, "qty_in": qty_in},
              "capacity": {"qty": cap_qty}}
    eid = await conn.fetchval(
        "INSERT INTO paper_profitability_evaluations (account_id, strategy, "
        " stage, decision_id, order_key, us_market_slug, holding_side, "
        " fixture, sport, market_family, regime, p_raw, p_used, "
        " market_price, qty_in, qty_out, all_in_ev_usd, ev_per_capital_hour,"
        " expected_hold_hours, residual_haircut_per_contract, "
        " learned_adverse_per_contract, fill_probability, verdict, refusal,"
        " detail, evaluated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'baseball',"
        " 'MONEYLINE','PRE_GAME_1H_TO_24H',$9,$9,$10,$11,$12,$13,0.001,$14,"
        " 0,0.01,$15,$16,$17,$18::jsonb,to_timestamp($19)) RETURNING eval_id",
        acct["account_id"], strategy, stage, decision_id, order_key, slug,
        side, "fx-" + slug, p, market, qty_in, qty_out, ev, hold, pf,
        verdict, None if verdict == "ENTER" else "CASH_WAIT_TEST_REFUSAL",
        json.dumps(detail), float(at))
    return eid, slug


async def order(conn, acct, *, group_id, slug, role="ENTRY", direction="BUY",
                side="LONG", qty, price, at, strategy=DEREK, key=None):
    oid = "paperord:" + uid()
    await conn.execute(
        "INSERT INTO paper_orders (order_id, idempotency_key, account_id, "
        " session_id, group_id, role, direction, holding_side, intent, "
        " us_market_slug, fixture, label, order_type, time_in_force, "
        " allow_partial, qty, limit_price, wire_price, filled_qty, state, "
        " decided_at, eligible_at, expires_at, simulator_version, strategy, "
        " terminal_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
        " '{}'::jsonb,'MARKETABLE','IOC',true,$12,$13,$13,$12,'FILLED',"
        " to_timestamp($14),to_timestamp($14),to_timestamp($14 + 90),$15,$16,"
        " to_timestamp($14 + 2))",
        oid, key or oid, acct["account_id"], acct["session_id"], group_id,
        role, direction, side, IF.intent_of(direction, side), slug,
        "fx-" + slug, qty, price, float(at), SIM, strategy)
    return oid


async def fill(conn, acct, *, order_id, group_id, slug, role="ENTRY",
               direction="BUY", side="LONG", qty, price, fee=0.0, at,
               strategy=DEREK):
    fid = "paperfill:" + uid()
    await conn.execute(
        "INSERT INTO paper_fills (fill_id, idempotency_key, order_id, "
        " account_id, session_id, group_id, role, direction, holding_side, "
        " us_market_slug, fixture, label, qty, price, wire_price, fee_usd, "
        " gross_usd, filled_at, basis, simulator_version, strategy) VALUES "
        " ($1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'{}'::jsonb,$11,$12,$12,$13,"
        " $14,to_timestamp($15),'DEPTH_WALK_WITHIN_LIMIT',$16,$17)",
        fid, order_id, acct["account_id"], acct["session_id"], group_id,
        role, direction, side, slug, "fx-" + slug, qty, price, fee,
        round(qty * price, 6), float(at), SIM, strategy)
    return fid


async def counterfactual(conn, acct, *, pnl, at, outcome="LOST",
                         strategy=DEREK, order_key=None, slug=None):
    slug = slug or uid("crl-cf-")
    key = uid("shadow:")
    sid = await conn.fetchval(
        "INSERT INTO paper_shadow_counterfactuals (shadow_key, account_id, "
        " strategy, order_key, source, capital_refusal, us_market_slug, "
        " holding_side, decided_at, eligible_at, expires_at, "
        " decision_to_execution_delay_s, p, limit_price, qty, levels, fills,"
        " cost_usd, fees_usd, adverse_selection_usd, total_executable_ev_usd,"
        " evidence) VALUES ($1,$2,$3,$4,'LEDGER_CAPITAL_AUTHORITY',"
        " 'CASH_WAIT_FORWARD_ECONOMICS_UNKNOWN',$5,'LONG',to_timestamp($6),"
        " to_timestamp($6),to_timestamp($6+60),0,0.6,0.5,10,'[]'::jsonb,"
        " '[]'::jsonb,5,0.1,0.1,0.8,'{}'::jsonb) RETURNING shadow_id",
        key, acct["account_id"], strategy, order_key, slug, float(at))
    await conn.execute(
        "INSERT INTO paper_shadow_counterfactual_outcomes (shadow_id, "
        " outcome, payout_per_contract, execution_basis, filled_qty, "
        " exec_cost_usd, exec_fees_usd, counterfactual_pnl_usd, evidence, "
        " settled_at) VALUES ($1,$2,0,'TEST',10,5,0.1,$3,'{}'::jsonb,"
        " to_timestamp($4))", sid, outcome, pnl, float(at) + HOUR)
    return key


async def variant(conn, acct, eid, *, name="MAKER", at, slug,
                  pnl=None, ev=1.0):
    vid = await conn.fetchval(
        "INSERT INTO paper_counterfactual_variants (account_id, strategy, "
        " eval_id, stage, verdict, us_market_slug, holding_side, variant, "
        " style, qty, vwap, p_used, fill_probability, cost_usd, fees_usd, "
        " expected_ev_usd, decided_at) VALUES ($1,$2,$3,'LEDGER','ENTER',$4,"
        " 'LONG',$5,'MAKER',10,0.38,0.6,0.3,3.8,0,$6,to_timestamp($7)) "
        "RETURNING variant_id", acct["account_id"], DEREK, eid, slug, name,
        ev, float(at))
    if pnl is not None:
        await conn.execute(
            "INSERT INTO paper_counterfactual_variant_outcomes (variant_id, "
            " outcome, payout_per_contract, counterfactual_pnl_usd, "
            " settled_at) VALUES ($1,'WON',1,$2,to_timestamp($3))", vid, pnl,
            float(at) + 4 * HOUR)
    return vid


async def court_rows(conn, decision_id):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM capital_readiness_shadow_court WHERE decision_id = $1"
        " ORDER BY court_id", decision_id)]


async def agent_rows(conn, subject):
    return [dict(r) for r in await conn.fetch(
        "SELECT * FROM capital_readiness_agent_economics "
        " WHERE subject_id = $1 ORDER BY observation_id", subject)]


# ═════════════════════════════════════════════════════════════════════
# 1 · THE DECISION SHADOW COURT
# ═════════════════════════════════════════════════════════════════════

@pg
def test_court_rows_include_cash_and_use_decision_time_values_only():
    async def go():
        conn, tr, acct = await _session()
        try:
            t = NOW - 2 * HOUR
            early = await model(conn, acct, "CALIBRATION", cal_payload(),
                                at=t - HOUR)
            await model(conn, acct, "EXECUTION", exe_payload(), at=t - HOUR)
            # a LATER fit (after the decision) must never be read
            late = await model(conn, acct, "CALIBRATION",
                               cal_payload(n=5000, brier=0.0), at=t + 60)
            eid, slug = await evaluation(conn, acct, at=t, ev=1.5, pf=0.9)
            await variant(conn, acct, eid, at=t, slug=slug)
            cid, _ = await evaluation(conn, acct, at=t, verdict="CASH",
                                      ev=-0.2)
            got = await O.observe(conn, now=NOW, account_id=acct["account_id"],
                                  readers=GREEN)
            assert got["court_judged"] == 2
            rows = await court_rows(conn, "ppe:%d" % eid)
            assert len(rows) == 1 and rows[0]["result"] is None
            alts = json.loads(rows[0]["alternatives"])
            names = [a["name"] for a in alts]
            assert "CASH" in names and "ENTER" in names
            assert "VARIANT:MAKER" in names          # 311 variant ledger
            enter = next(a for a in alts if a["name"] == "ENTER")
            fz = enter["frozen_at_decision"]
            assert fz["calibration_model_id"] == early != late
            assert fz["basis"] == "VALUES_RECORDED_AT_THE_DECISION_ONLY"
            assert abs(enter["expected_net_usd"] - 1.35) < 1e-9
            assert enter["expected_hold_hours"] == 3.0
            assert rows[0]["chosen"] == "ENTER"
            cash_rows = await court_rows(conn, "ppe:%d" % cid)
            assert cash_rows[0]["chosen"] == "CASH"
            assert cash_rows[0]["shadow_winner"] == "CASH"
            assert any(a["name"] == "CASH" for a in json.loads(
                cash_rows[0]["alternatives"]))
            # judged once per evaluation
            again = await O.observe(conn, now=NOW + 60,
                                    account_id=acct["account_id"],
                                    readers=GREEN)
            assert again["court_judged"] == 0
            assert len(await court_rows(conn, "ppe:%d" % eid)) == 1
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


@pg
def test_outcome_scoring_appends_a_new_court_row_never_an_update():
    async def go():
        conn, tr, acct = await _session()
        try:
            t = NOW - 5 * HOUR
            g = "paper_group_" + uid()
            slug = uid("crl-mkt-")
            key = uid("ok:")
            oid = await order(conn, acct, group_id=g, slug=slug, qty=10,
                              price=0.40, at=t, key=key)
            await fill(conn, acct, order_id=oid, group_id=g, slug=slug,
                       qty=10, price=0.40, fee=0.1, at=t + 2)
            eid, _ = await evaluation(conn, acct, at=t, slug=slug,
                                      order_key=key)
            await variant(conn, acct, eid, at=t, slug=slug, pnl=6.2)
            await O.observe(conn, now=NOW, account_id=acct["account_id"],
                            readers=GREEN)
            first = await court_rows(conn, "ppe:%d" % eid)
            assert len(first) == 1 and first[0]["result"] is None
            await IF.settle(conn, acct, group_id=g, slug=slug, qty=10,
                            outcome="WON", payout_per_contract=1.0,
                            at=t + 4 * HOUR)
            got = await O.observe(conn, now=NOW + 60,
                                  account_id=acct["account_id"],
                                  readers=GREEN)
            assert got["court_scored"] == 1
            rows = await court_rows(conn, "ppe:%d" % eid)
            assert len(rows) == 2
            assert rows[0] == first[0]               # untouched
            res = json.loads(rows[1]["result"])
            assert res["basis"] == "PAPER_ENTRY_FILLS_HELD_TO_SETTLEMENT"
            # 10 x 1.00 - (4.00 + 0.10)
            assert abs(res["chosen_realized_usd"] - 5.9) < 1e-6
            assert res["realized_by_alternative"]["VARIANT:MAKER"] == 6.2
            assert rows[1]["scored_at"] is not None
            # scored once
            await O.observe(conn, now=NOW + 120,
                            account_id=acct["account_id"], readers=GREEN)
            assert len(await court_rows(conn, "ppe:%d" % eid)) == 2
            # and the table itself refuses an update
            with pytest.raises(Exception, match="append-only"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE capital_readiness_shadow_court SET "
                        " disagreement = NOT disagreement WHERE court_id=$1",
                        rows[0]["court_id"])
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


# ═════════════════════════════════════════════════════════════════════
# 2 · AGENT ECONOMICS
# ═════════════════════════════════════════════════════════════════════

async def _seed_agents(conn, acct):
    t = NOW - 10 * HOUR
    g = "paper_group_" + uid()
    slug = uid("crl-mkt-")
    key = uid("ok:")
    oid = await order(conn, acct, group_id=g, slug=slug, qty=10, price=0.45,
                      at=t, key=key)
    await fill(conn, acct, order_id=oid, group_id=g, slug=slug, qty=10,
               price=0.45, fee=0.1, at=t + 2)
    # the bind recorded the market at 0.40 at the decision
    await evaluation(conn, acct, at=t, slug=slug, order_key=key, market=0.40)
    xo = await order(conn, acct, group_id=g, slug=slug, role="EXIT",
                     direction="SELL", qty=4, price=0.70, at=t + HOUR)
    await fill(conn, acct, order_id=xo, group_id=g, slug=slug, role="EXIT",
               direction="SELL", qty=4, price=0.70, fee=0.04, at=t + HOUR)
    await IF.settle(conn, acct, group_id=g, slug=slug, qty=6, outcome="LOST",
                    payout_per_contract=0.0, at=t + 5 * HOUR)
    bad = await counterfactual(conn, acct, pnl=-5.0, at=t)
    good = await counterfactual(conn, acct, pnl=3.0, at=t, outcome="WON")
    pk = F.position_key(acct["account_id"], g, slug, "LONG")
    return {"pk": pk, "exit": xo, "bad": bad, "good": good}


@pg
def test_derek_xavier_and_refusal_gate_alpha_signs_on_settlements():
    async def go():
        conn, tr, acct = await _session()
        try:
            s = await _seed_agents(conn, acct)
            await O.observe(conn, now=NOW, account_id=acct["account_id"],
                            readers=GREEN)
            d = await agent_rows(conn, s["pk"])
            assert [r["agent"] for r in d] == ["DEREK"]
            # 10 x (0 - 0.40): the market at the decision, not the fill
            assert float(d[0]["economic_alpha_usd"]) == pytest.approx(-4.0)
            x = await agent_rows(conn, s["exit"])
            assert [r["agent"] for r in x] == ["XAVIER"]
            # sold 4 at 0.70 less 0.04 fees vs holding to a LOST settlement
            assert float(x[0]["economic_alpha_usd"]) == pytest.approx(2.76)
            b = await agent_rows(conn, s["bad"])
            assert b[0]["agent"] == F.REFUSAL_GATE_AGENT
            assert float(b[0]["economic_alpha_usd"]) == pytest.approx(5.0)
            gd = await agent_rows(conn, s["good"])
            assert float(gd[0]["economic_alpha_usd"]) == pytest.approx(-3.0)
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


@pg
def test_agent_observations_are_idempotent_and_role_metric_specific():
    async def go():
        conn, tr, acct = await _session()
        try:
            s = await _seed_agents(conn, acct)
            for k in range(3):
                await O.observe(conn, now=NOW + k * 60,
                                account_id=acct["account_id"], readers=GREEN)
            metrics = {}
            for subj in (s["pk"], s["exit"], s["bad"], s["good"]):
                rows = await agent_rows(conn, subj)
                assert len(rows) == 1, subj           # idempotent
                metrics[rows[0]["agent"]] = rows[0]["role_metric"]
            assert metrics == {"DEREK": "discovery_alpha_usd",
                               "XAVIER": "management_alpha_usd",
                               F.REFUSAL_GATE_AGENT: "prevented_loss_usd"}
            # unmeasurable agents are reported, never written
            obs = await F.agent_observations(conn, acct["account_id"],
                                             now=NOW)
            for a in ("ARCHER", "AUDREY", "KAREN", "ALLIE", "ADRIANA",
                      "SCOUT"):
                assert obs["agents"][a]["status"] == "UNAVAILABLE"
                assert all(o["agent"] != a for o in obs["observations"])
            # the championship reads the agent's own metric
            rows = await F.stored_agent_rows(conn)
            ch = F.championship_input([r for r in rows if r["subject_id"]
                                       in (s["pk"], s["exit"])])
            assert {"agent": "DEREK", "metric": "discovery_alpha_usd",
                    "discovery_alpha_usd": -4.0} in ch
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


# ═════════════════════════════════════════════════════════════════════
# 3 · THE SCALE TWIN
# ═════════════════════════════════════════════════════════════════════

@pg
def test_scale_twin_never_above_measured_capacity_and_nothing_without_it():
    async def go():
        conn, tr, acct = await _session()
        try:
            scope = "ACCOUNT:%s:%ds" % (acct["account_id"],
                                        int(F.SCALE_WINDOW_S))
            # ENTER evidence but NO measured capacity: nothing recorded
            await evaluation(conn, acct, at=NOW - HOUR, cap_qty=None)
            got = await O.observe(conn, now=NOW,
                                  account_id=acct["account_id"],
                                  readers=GREEN)
            assert got["scale"]["status"] == "UNAVAILABLE"
            assert got["scale"]["why"] == \
                "NO_MEASURED_EXECUTABLE_CAPACITY_IN_WINDOW"
            assert got["scale"]["rows_recorded"] == 0
            assert not await conn.fetchval(
                "SELECT count(*) FROM capital_readiness_scale_trials "
                " WHERE scope_key = $1", scope)
            run_row = await conn.fetchrow(
                "SELECT payload FROM capital_readiness_runs "
                " ORDER BY run_id DESC LIMIT 1")
            sec = json.loads(run_row["payload"])["sections"]["scale_twin"]
            assert sec["status"] == "UNAVAILABLE" and sec["why"]
            # a strong edge with a measured frontier: capped at capacity
            for _ in range(40):
                await evaluation(conn, acct, at=NOW - HOUR, qty_in=100,
                                 p=0.95, ev=30.0, pf=1.0, cap_qty=20000,
                                 slug=uid("crl-cap-"))
            inp = await F.scale_inputs(conn, acct["account_id"], now=NOW + 60)
            assert inp["status"] == "OK"
            cap = inp["args"]["capacity_usd"]
            got = await O.observe(conn, now=NOW + 60,
                                  account_id=acct["account_id"],
                                  readers=GREEN)
            assert got["scale"]["status"] == "OK"
            assert got["scale"]["recommended_capital_usd"] <= cap
            rows = await conn.fetch(
                "SELECT capital_usd FROM capital_readiness_scale_trials "
                " WHERE scope_key = $1", scope)
            assert rows and all(float(r["capital_usd"]) <= cap + 1e-6
                                for r in rows)
            # unchanged inputs are not re-recorded
            n = len(rows)
            await O.observe(conn, now=NOW + 60,
                            account_id=acct["account_id"], readers=GREEN)
            assert await conn.fetchval(
                "SELECT count(*) FROM capital_readiness_scale_trials "
                " WHERE scope_key = $1", scope) == n
        finally:
            await tr.rollback()
            await conn.close()
    run(go())

    # pure: the twin itself never measures a rung above capacity
    rep = ST.evaluate(base_capital_usd=1000, base_expected_ev_usd=100,
                      capacity_usd=7000, uncertainty_sigma_usd=1)
    assert rep["recommended_capital_usd"] <= 7000
    assert all(r["status"] != "MEASURED" for r in rep["rows"]
               if r["capital_usd"] > 7000)


# ═════════════════════════════════════════════════════════════════════
# 4 · READINESS
# ═════════════════════════════════════════════════════════════════════

@pg
def test_readiness_gates_fail_closed_on_unreadable_sources_and_red_forces_zero():
    async def boom(conn, ctx):
        raise RuntimeError("source unreadable")

    async def go():
        conn, tr, acct = await _session()
        try:
            readers = dict(GREEN, freshness_gte_95=boom,
                           production_canary_clean=boom)
            gates = await F.gates(conn, account_id=acct["account_id"],
                                  now=NOW, readers=readers)
            assert gates["freshness_gte_95"]["value"] is False
            assert gates["freshness_gte_95"]["reason"] == "UNREADABLE"
            assert "source unreadable" in \
                gates["freshness_gte_95"]["evidence"]["why"]
            assert gates["production_canary_clean"]["value"] is False
            assert gates["small_live_shadow"]["value"] is True   # stub
            # the REAL readers on a scratch account: each a bool, the CI
            # gate False without an exact-SHA attestation
            real = await F.gates(conn, account_id=acct["account_id"],
                                 now=NOW, source_sha=None)
            assert set(real) == set(R.HARD_GATES)
            assert all(isinstance(v["value"], bool) for v in real.values())
            assert real["ci_exact_sha_green"]["value"] is False
            assert real["ci_exact_sha_green"]["reason"] == \
                "NO_EXACT_SHA_CI_ATTESTATION_IN_PRODUCTION"
            assert real["freshness_gte_95"]["value"] is False
            # a strong measured scale twin, but a RED gate => $0
            for _ in range(40):
                await evaluation(conn, acct, at=NOW - HOUR, qty_in=100,
                                 p=0.95, ev=30.0, pf=1.0, cap_qty=20000,
                                 slug=uid("crl-cap-"))
            got = await O.observe(conn, now=NOW,
                                  account_id=acct["account_id"],
                                  readers=readers)
            assert got["scale"]["recommended_capital_usd"] > 0
            assert got["readiness_status"] == "RED"
            assert got["recommended_capital_usd"] == 0
            assert "freshness_gte_95" in got["blocking_gates"]
            r = await conn.fetchrow(
                "SELECT * FROM capital_readiness_runs ORDER BY run_id DESC "
                "LIMIT 1")
            assert r["readiness_status"] == "RED"
            assert float(r["recommended_capital_usd"]) == 0
            p = json.loads(r["payload"])
            assert p["live_authority_granted"] is False
            assert p["gate_evidence"]["freshness_gte_95"]["reason"] == \
                "UNREADABLE"
            for k in ("agent_championship", "scale_twin", "shadow_court",
                      "alpha_decay", "portfolio_twin",
                      "adversarial_committee"):
                assert k in p["sections"], k
            assert p["sections"]["portfolio_twin"]["status"] == "UNAVAILABLE"
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


@pg
def test_observer_writes_only_to_the_four_310_tables():
    async def counts(conn):
        names = [r["relname"] for r in await conn.fetch(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
            "  ON n.oid = c.relnamespace WHERE c.relkind = 'r' "
            "   AND n.nspname = 'public' ORDER BY 1")]
        return {t: await conn.fetchval('SELECT count(*) FROM "%s"' % t)
                for t in names}

    async def go():
        conn, tr, acct = await _session()
        try:
            await _seed_agents(conn, acct)
            await model(conn, acct, "CALIBRATION", cal_payload(),
                        at=NOW - 20 * HOUR)
            await evaluation(conn, acct, at=NOW - HOUR, cap_qty=20)
            before = await counts(conn)
            # the REAL gate readers: none may write
            await O.observe(conn, now=NOW, account_id=acct["account_id"],
                            source_sha="0" * 40)
            after = await counts(conn)
            changed = {t for t in after if after[t] != before.get(t)}
            assert changed <= set(O.TABLES), changed
            assert "capital_readiness_runs" in changed
            assert "capital_readiness_shadow_court" in changed
            assert "capital_readiness_agent_economics" in changed
        finally:
            await tr.rollback()
            await conn.close()
    run(go())


# ═════════════════════════════════════════════════════════════════════
# 5 · AUTHORITY AUDIT AND THE GET ENDPOINT
# ═════════════════════════════════════════════════════════════════════

FORBIDDEN = ("bettor_funded_execution", "bettor_entry_execution",
             "submit_order(", "cancel_order(", "place_order(", "write:orders",
             "LIVE_TRADING_ENABLED", "requests.post(", "httpx.post(",
             "aiohttp.ClientSession")


def test_static_authority_audit_over_the_package_and_its_api():
    files = sorted((PKG / "capital_readiness").glob("*.py")) + [
        PKG / "api" / "command_capital_readiness.py"]
    assert any(f.name == "feeds.py" for f in files)
    assert any(f.name == "observer.py" for f in files)
    for f in files:
        src = f.read_text()
        for tok in FORBIDDEN:
            assert tok not in src, (f.name, tok)
        # no SQL write outside runner.py, and none but INSERT there
        if f.name != "runner.py":
            assert not re.search(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|"
                                 r"DELETE\s+FROM|TRUNCATE)\b", src, re.I), \
                f.name
    runner = (PKG / "capital_readiness" / "runner.py").read_text()
    assert set(re.findall(r"INSERT INTO (\w+)", runner)) == set(O.TABLES)
    # the observer persists only through runner.py
    obs = (PKG / "capital_readiness" / "observer.py").read_text()
    assert "conn.execute(\"INSERT" not in obs
    # the observer is scheduled from the API lifespan with a kill switch,
    # never from a decision path
    app = (PKG / "api" / "app.py").read_text()
    life = app[app.index("async def lifespan"):app.index("app = FastAPI(")]
    assert "_CRL.run(_cap_pool)" in life and "readiness_task" in \
        life.split("tasks = [t for t in (")[1][:600]
    assert O.ENV_KILL == "CAPITAL_READINESS_OBSERVER"
    for p in ("bettor_paper_ledger.py", "agents/paper_derek.py",
              "bettor_capital_authority.py",
              "bettor_paper_profitability_bind.py"):
        assert "capital_readiness" not in (PKG / p).read_text(), p


def test_observer_kill_switch(monkeypatch):
    monkeypatch.setenv("CAPITAL_READINESS_OBSERVER", "off")
    assert O.enabled() is False
    monkeypatch.setenv("CAPITAL_READINESS_OBSERVER", "on")
    assert O.enabled() is True
    monkeypatch.delenv("CAPITAL_READINESS_OBSERVER")
    assert O.enabled() is True


@pg
def test_get_endpoint_returns_championship_court_scale_readiness_get_only(
        monkeypatch):
    import asyncpg
    from sportsassets.api import command_capital_readiness as CR

    for route in CR.router.routes:
        assert set(route.methods) <= {"GET", "HEAD"}, route.path

    async def go():
        pool = await asyncpg.create_pool(H.DSN, min_size=1, max_size=2)

        async def _pool():
            return pool
        monkeypatch.setattr(CR, "_pool", _pool)
        try:
            async with pool.acquire() as conn:
                tag = uid("get-")
                court = {"decision_id": "test:" + tag, "chosen": "ENTER",
                         "shadow_winner": "CASH", "disagreement": True,
                         "alternatives": [{"name": "ENTER"},
                                          {"name": "CASH"}]}
                await RUN.record_court(conn, court, decided_at=NOW)
                await RUN.record_agent_observation(
                    conn, agent="XAVIER", role_metric="management_alpha_usd",
                    subject_id=tag, economic_alpha_usd=1.25,
                    observed_at=NOW)
                twin = ST.evaluate(base_capital_usd=1000,
                                   base_expected_ev_usd=50,
                                   capacity_usd=5000,
                                   uncertainty_sigma_usd=1)
                await RUN.record_scale(conn, "TEST:" + tag, twin,
                                       {"test": True}, computed_at=NOW + 1e6)
                v = R.score(gates={g: False for g in R.HARD_GATES})
                await RUN.record_readiness(conn, v, computed_at=NOW + 1e6)
            out = await CR.capital_readiness_latest()
        finally:
            await pool.close()
        assert out["label"] == "RESEARCH"
        assert out["authority"] == "SHADOW_NO_AUTHORITY"
        assert out["status"] == "OK"
        assert out["readiness"]["readiness_status"] == "RED"
        assert out["data"] == out["readiness"]
        assert "XAVIER" in out["agent_championship"]["agents"]
        sc = out["shadow_court"]
        assert sc["count"] >= 1 and 0 <= sc["disagreement_rate"] <= 1
        assert sc["winners"].get("CASH", 0) >= 1
        assert out["scale_twin"]["status"] == "OK"
        assert all(r["scope_key"] == out["scale_twin"]["rows"][0]["scope_key"]
                   for r in out["scale_twin"]["rows"])
    run(go())


def test_this_proof_is_capital_critical():
    lst = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_capital_readiness_feeds.py" in lst.split()
