"""CAPITAL-CRITICAL: EVERY PROFITABILITY ITEM IS PRODUCTION-ACTIVE IN PAPER
ENTRY AND MANAGEMENT DECISIONS (bettor_paper_profitability_bind, migration 309).

Runs the PRODUCTION gates: PROFITABILITY_BIND_ENFORCED and
CAPITAL_AUTHORITY_ENFORCED below (the suite's seeded passthroughs in
tests/conftest.py do not apply here).

  §1 pure: calibration never inflates and falls back to the market price;
     descriptors / regimes; capacity frontier; capital-hour; correlation;
     churn; the absolute-positive champion rule; residual haircuts are
     never credits; learned execution costs only add.
  §2 THE LEDGER (bettor_paper_ledger.submit_order, every strategy): an
     uncalibrated entry is CASH; a calibrated one enters at the BOUND
     (smaller) size with the bind on its SUBMITTED event; capacity,
     capital-hour, correlation, churn and turnover refuse or shrink; the
     learned markout and the residual haircut lower the EV.
  §3 THE AUTHORITY: the champion rule and the regime authority refuse by
     name and route to a shadow counterfactual.
  §4 THE DECISION GATE (agents.paper_derek.capital_gate, Derek and the
     benchmarks): the same bind; the ledger re-derives the same size.
  §5 XAVIER: HOLD / EXIT / REDUCE on the same calibrated, haircut economics.
  §6 THE FITS AND THE CASH DECISION run in the paper pass.
  §7 THE ACCEPTANCE READ: unrealized / exposure never null, the $500,000
     management epoch reconciled to the cent, per-strategy bind state.
  §8 bookkeeping: refusals classified, tables append-only, no authority
     raised, the proof is capital-critical.
"""
from __future__ import annotations

import ast
import json
import pathlib

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as B
from sportsassets import bettor_strategy_lifecycle as LC

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                           # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

PROFITABILITY_BIND_ENFORCED = True
CAPITAL_AUTHORITY_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = 1_791_500_000.0
HOUR = 3600.0
CG = "PINNACLE_COMPLETED_GAME_PAPER"
EXPLORE = "PINNACLE_EXPLORATION_PAPER"
DEREK = "DEREK_ENTRY_POLICY_V2"
FEE = H.flat_fee(0.01)
ML = "baseball_team_full_game_winner"
CELL_PRE = B.cell_key("baseball", B.MONEYLINE, B.PRE_1H_24H)


def fee(q, px):
    return 0.01 * q


# ── §1 pure ──────────────────────────────────────────────────────────

def _cal(n=60, p=0.60, rate=0.60, regime=B.PRE_1H_24H):
    ones = int(round(n * rate))
    obs = [{"sport": "baseball", "family": B.MONEYLINE, "regime": regime,
            "p": p, "y": 1.0 if i < ones else 0.0} for i in range(n)]
    return B.fit_calibration(obs)


def test_calibration_never_inflates_and_no_data_is_the_market():
    # no model: the market price itself -> no edge
    c = B.calibrate(None, sport="baseball", family=B.MONEYLINE,
                    regime=B.PRE_1H_24H, p_raw=0.60, market=0.40)
    assert c["p_used"] == 0.40 and c["status"] == B.CAL_NO_DATA
    # an UNDER-confident model (observed 0.80 at p 0.60) is never raised
    m = _cal(rate=0.80)
    c = B.calibrate(m, sport="baseball", family=B.MONEYLINE,
                    regime=B.PRE_1H_24H, p_raw=0.60, market=0.40)
    assert c["p_calibrated"] > 0.60 and c["p_used"] == 0.60
    assert c["inflated"] is False
    # an OVER-confident one (observed 0.40 at p 0.60) is pulled down
    m = _cal(rate=0.40)
    c = B.calibrate(m, sport="baseball", family=B.MONEYLINE,
                    regime=B.PRE_1H_24H, p_raw=0.60, market=0.40)
    assert c["p_used"] < 0.45 and c["status"] == B.CAL_MEASURED
    # a thin cell shrinks harder toward the market than a measured one
    thin = B.calibrate(_cal(n=10), sport="baseball", family=B.MONEYLINE,
                       regime=B.PRE_1H_24H, p_raw=0.60, market=0.40)
    full = B.calibrate(_cal(n=300), sport="baseball", family=B.MONEYLINE,
                       regime=B.PRE_1H_24H, p_raw=0.60, market=0.40)
    assert thin["status"] == B.CAL_INSUFFICIENT
    assert 0.40 < thin["p_used"] < full["p_used"] <= 0.60
    # keyed by regime: an IN_PLAY fit says nothing about PRE_GAME
    c = B.calibrate(_cal(regime=B.IN_PLAY), sport="baseball",
                    family=B.MONEYLINE, regime=B.PRE_1H_24H, p_raw=0.60,
                    market=0.40)
    assert c["p_used"] == 0.40


def test_descriptors_and_regimes():
    assert B.family_of(ML) == B.MONEYLINE
    assert B.family_of("baseball_team_full_game_spread") == B.SPREAD
    assert B.family_of("baseball_team_total_runs") == B.TEAM_TOTAL
    assert B.family_of("football_team_full_game_total") == B.TOTAL
    assert B.family_of(None) == B.UNKNOWN
    assert B.sport_of(ML) == "baseball"
    assert B.sport_of(None, "nfl-kc-buf-2026") == "nfl"
    assert B.regime_of(None, NOW) == B.REGIME_UNKNOWN
    assert B.regime_of(NOW - 1, NOW) == B.IN_PLAY
    assert B.regime_of(NOW + 600, NOW) == B.PRE_LT_1H
    assert B.regime_of(NOW + 3 * HOUR, NOW) == B.PRE_1H_24H
    assert B.regime_of(NOW + 30 * HOUR, NOW) == B.PRE_GT_24H
    assert B.expected_hold_hours(sport="baseball", game_start=NOW + 3 * HOUR,
                                 at=NOW) == pytest.approx(3 + 3.25)
    assert B.expected_hold_hours(sport="x", game_start=None, at=NOW) is None


def test_capacity_frontier_stops_at_the_last_positive_marginal_contract():
    fills = [[0.40, 100], [0.45, 100], [0.55, 100]]
    got = B.capacity_frontier(fills=fills, p_used=0.52, fee_fn=fee,
                              adverse_per_contract=0.0,
                              haircut_per_contract=0.0)
    assert got["qty"] == 200 and got["stopped_at"]["price"] == 0.55
    # displayed depth caps it at MAX_DEPTH_FRACTION
    got = B.capacity_frontier(fills=fills, p_used=0.52, fee_fn=fee,
                              adverse_per_contract=0.0,
                              haircut_per_contract=0.0, depth=300)
    assert got["qty"] == 150 == int(300 * B.MAX_DEPTH_FRACTION)


def test_capital_hour_correlation_and_churn_only_shrink_or_refuse():
    lo = B.capital_hour(ev_usd=1.0, capital_usd=1000.0, hold_hours=720.0)
    assert lo["status"] == "BELOW_FLOOR" and lo["factor"] == 0.0
    mid = B.capital_hour(ev_usd=1.0, capital_usd=100.0, hold_hours=20.0)
    assert 0 < mid["factor"] < 1
    hi = B.capital_hour(ev_usd=10.0, capital_usd=40.0, hold_hours=6.0)
    assert hi["factor"] == 1.0                          # never above 1
    assert B.correlation_factor(0)["factor"] == 1.0
    assert B.correlation_factor(1)["factor"] == 0.5
    assert B.correlation_factor(B.MAX_CORRELATED_SAME_FIXTURE)["refused"]
    v = B.churn_verdict(ev_per_contract=0.10, prior_ev_per_contract=0.09,
                        kind="RECENT_REFUSAL")
    assert v["materially_improved"] is False
    v = B.churn_verdict(ev_per_contract=0.13, prior_ev_per_contract=0.09,
                        kind="RECENT_REFUSAL")
    assert v["materially_improved"] is True
    v = B.churn_verdict(ev_per_contract=0.005, prior_ev_per_contract=None,
                        kind="RECENT_REFUSAL")
    assert v["materially_improved"] is False
    # after an exit, no recorded entry EV: nothing to improve on
    v = B.churn_verdict(ev_per_contract=0.50, prior_ev_per_contract=None,
                        kind="CONTRACT_EXIT", require_prior=True)
    assert v["materially_improved"] is False


def test_champion_is_absolute_not_relative():
    n = CA.MIN_FORWARD_OBSERVATIONS
    ok = CA.forward_verdict([1.0] * n, [])
    assert B.champion_verdict(ok)["champion"] is True
    # net positive (POSITIVE verdict) but a CI that spans zero
    wide = CA.forward_verdict([10.0] * 11 + [-11.0] * 9, [])
    assert wide["verdict"] == CA.POSITIVE and wide["pnl_ci95_low"] < 0
    ch = B.champion_verdict(wide)
    assert ch["champion"] is False
    assert ch["refusal"] == B.R_NOT_ABSOLUTE_CHAMPION
    assert B.champion_verdict(CA.forward_verdict([], []))["champion"] is False


def test_residual_haircut_is_never_a_credit_and_execution_only_adds():
    rows = [{"strategy": CG, "sport": "baseball", "family": B.MONEYLINE,
             "expected_ev_usd": 10.0, "realized_pnl_usd": 4.0, "qty": 100,
             "source": "PAPER"} for _ in range(10)]
    m = B.fit_residuals(rows)
    h = B.residual_haircut(m, strategy=CG, sport="baseball",
                           family=B.MONEYLINE)
    assert h["haircut_per_contract"] == pytest.approx(
        0.06 * 10 / (10 + B.K_RESIDUAL))
    good = B.fit_residuals([dict(r, realized_pnl_usd=50.0) for r in rows])
    assert B.residual_haircut(good, strategy=CG, sport="baseball",
                              family=B.MONEYLINE)["haircut_per_contract"] \
        == 0.0
    ex = B.fit_execution(
        [{"strategy": CG, "style": B.TAKER, "qty": 10,
          "markout_per_contract": -0.05} for _ in range(10)],
        [{"strategy": CG, "style": B.TAKER, "qty": 10, "filled_qty": 10}])
    t = B.execution_terms(ex, strategy=CG, style=B.TAKER)
    assert t["learned_adverse_per_contract"] == 0.0     # a gain is not a credit
    assert 0.5 < t["fill_probability"] < 1.0
    assert B.execution_terms(None, strategy=CG, style=B.MAKER)[
        "fill_probability"] == B.FILL_PRIOR[B.MAKER]


def test_bind_economics_never_exceeds_the_policy_size():
    desc = {"sport": "baseball", "family": B.MONEYLINE,
            "regime": B.PRE_1H_24H, "expected_hold_hours": 6.25}
    ev = {"p": 0.60, "fills": [[0.40, 100.0]], "best_price": 0.40,
          "limit": 0.40, "adverse_selection_usd": 0.0,
          "levels": [{"price": 0.40, "qty": 10000}]}
    got = B.bind_economics(evidence=ev, qty_in=100, fee_fn=fee, desc=desc,
                           calibration=_cal(n=300), execution=None,
                           residuals=None, strategy=CG,
                           order_type="MARKETABLE", n_correlated=0)
    assert got["refusal"] is None and 1 <= got["qty"] <= 100
    assert got["calibration"]["p_used"] <= 0.60
    capped = B.bind_economics(evidence=ev, qty_in=100, fee_fn=fee, desc=desc,
                              calibration=_cal(n=300), execution=None,
                              residuals=None, strategy=CG,
                              order_type="MARKETABLE", n_correlated=0,
                              qty_cap=7)
    assert capped["qty"] == 7


# ── helpers ──────────────────────────────────────────────────────────

async def _tx():
    conn = await H.connect()
    tr = conn.transaction()
    await tr.start()
    return conn, tr


async def _done(conn, tr):
    await tr.rollback()
    await conn.close()


async def premap(conn, slug, *, game_start, sports_type=ML):
    await conn.execute(
        "INSERT INTO us_premap (identifier, market_slug, event_slug, "
        " side_norm, game_start, sports_type, team_name) VALUES ($1,$2,$3,"
        " 'HOME',to_timestamp($4),$5,'Test Home')",
        "pbind-" + F.uid(), slug, "mlb-test-" + slug[-6:], float(game_start),
        sports_type)


async def calibrate_account(conn, acct, *, n=300, rate=0.60,
                            regime=B.PRE_1H_24H):
    return await B.record_model(conn, account_id=acct, kind="CALIBRATION",
                                payload=_cal(n=n, rate=rate, regime=regime),
                                at=NOW - 60)


def _settled(outcome="WON", payout=1.0):
    async def fn(conn, slug, side):
        return {"outcome": outcome, "payout_per_contract": payout,
                "evidence": {"test": True}}
    return fn


async def seed_forward(conn, a, strategy, *, n=None, game_in_h=3.0,
                       outcomes=None):
    """n settled filled shadows of `strategy` decided in the regime of a
    game `game_in_h` hours after the decision."""
    n = n or CA.MIN_FORWARD_OBSERVATIONS
    outcomes = outcomes or ["WON"] * n
    for i in range(n):
        slug = "%s:fwd:%s:%d" % (a["account_id"], strategy[-6:], i)
        dec = NOW - 900 + i
        await premap(conn, slug, game_start=dec + game_in_h * HOUR)
        ce = CA.evaluate_executable(
            p=0.6, levels=[{"price": 0.40, "qty": 100}], qty=100, limit=0.40,
            fee_fn=FEE, at=dec, settlement={"compatibility": "COMPATIBLE"},
            identity={"us_market_slug": slug, "payout_event": "HOME",
                      "fixture": "fx-" + slug, "holding_side": "LONG"})
        ev = CA.capital_evidence(ce, p=0.6, limit=0.40, basis="TEST",
                                 levels=[{"price": 0.40, "qty": 100}])
        got = await CA.record_shadow(
            conn, account_id=a["account_id"], strategy=strategy,
            decision_id="dec:" + slug, order_key=None,
            source=CA.SRC_DECISION, capital_refusal=CA.R_FORWARD_UNKNOWN,
            lifecycle_state=LC.ACTIVE_CHALLENGER, slug=slug,
            holding_side="LONG", fixture="fx-" + slug, payout_event="HOME",
            decided_at=dec, delay_s=2.0, expires_at=dec + 90,
            simulator_version="test", evidence=ev)
        assert got["recorded"], got
        out = outcomes[i]
        await CA.settle_shadows(
            conn, now=NOW - 100, account_id=a["account_id"], limit=1,
            settlement_fn=_settled(out, 1.0 if out == "WON" else 0.0))


def _evidence(*, slug, p=0.60, levels=None, qty=100, limit=0.40):
    levels = levels or [{"price": limit, "qty": qty}]
    ce = CA.evaluate_executable(
        p=p, levels=levels, qty=qty, limit=limit, fee_fn=FEE, at=NOW,
        settlement={"compatibility": "COMPATIBLE"},
        identity={"us_market_slug": slug, "payout_event": "HOME",
                  "fixture": "fx-" + slug, "holding_side": "LONG"})
    return CA.capital_evidence(ce, p=p, limit=limit, threshold_edge_pp=0.5,
                               basis="TEST", levels=levels)


def _order(a, *, key, strategy=CG, qty=100, limit=0.40, p=0.60, levels=None,
           at=NOW, slug=None, fixture=None):
    slug = slug or "%s:%s" % (a["account_id"], key)
    o = H.order(a, key=key, qty=qty, limit=limit, at=at, slug=slug,
                group_id="paper_g_%s_%s" % (a["account_id"][-6:], key),
                fixture=fixture or ("fx-" + slug))
    o["strategy"] = strategy
    o["capital_evidence"] = _evidence(slug=slug, p=p, levels=levels,
                                      qty=qty, limit=limit)
    return o


async def ready(conn, tag, *, strategy=CG, calibrate=True):
    a = await H.new_account(conn, tag, now=NOW - 3600)
    if calibrate:
        await calibrate_account(conn, a["account_id"])
    await seed_forward(conn, a, strategy)
    return a


async def counts(conn, acct):
    return {k: await conn.fetchval(sql, acct) for k, sql in {
        "orders": "SELECT count(*) FROM paper_orders WHERE account_id=$1",
        "shadows": "SELECT count(*) FROM paper_shadow_counterfactuals "
                   " WHERE account_id=$1",
        "census": "SELECT count(*) FROM paper_entry_refusal_census "
                  " WHERE account_id=$1",
        "evals": "SELECT count(*) FROM paper_profitability_evaluations "
                 " WHERE account_id=$1"}.items()}


# ── §2 the ledger ────────────────────────────────────────────────────

@pg
async def test_uncalibrated_entry_is_cash_at_the_ledger_with_a_record():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbuc", calibrate=False)
        acct = a["account_id"]
        o = _order(a, key="u1")
        await premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        before = await counts(conn, acct)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"] is False
        # raw EV is positive; the calibrated (market-anchored) EV is not
        assert o["capital_evidence"]["total_executable_ev_usd"] > 0
        assert got["refusal"] == B.R_CALIBRATED_EV_NOT_POSITIVE
        assert got["under_lock"] is True
        after = await counts(conn, acct)
        assert after["orders"] == before["orders"]
        assert after["shadows"] == before["shadows"]     # economics, no shadow
        assert after["census"] == before["census"] + 1
        r = await conn.fetchrow(
            "SELECT * FROM paper_profitability_evaluations WHERE "
            " account_id=$1 ORDER BY eval_id DESC LIMIT 1", acct)
        assert r["verdict"] == "CASH" and r["stage"] == "LEDGER"
        assert r["refusal"] == B.R_CALIBRATED_EV_NOT_POSITIVE
        assert r["regime"] == B.PRE_1H_24H and r["sport"] == "baseball"
        assert float(r["p_used"]) <= float(r["p_raw"])
        assert float(r["qty_out"]) == 0
    finally:
        await _done(conn, tr)


@pg
async def test_calibrated_entry_enters_at_the_bound_size():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbok")
        acct = a["account_id"]
        o = _order(a, key="k1", qty=100,
                   levels=[{"price": 0.40, "qty": 100}])
        await premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
        # the capacity frontier: half of the 100 displayed contracts
        assert float(got["order"]["qty"]) == 50
        ev = H.j(await conn.fetchval(
            "SELECT detail FROM paper_order_events WHERE order_id=$1 "
            "   AND kind='SUBMITTED'", got["order"]["order_id"]))
        ca = ev["capital_authority"]
        assert ca["forward_verdict"] == CA.POSITIVE
        assert ca["champion"] is True
        assert ca["regime"] == B.PRE_1H_24H
        assert ca["regime_verdict"] == CA.POSITIVE
        pb = ca["profitability_bind"]
        assert pb["qty"] == 50 and pb["qty_in"] == 100
        assert pb["p_used"] < pb["p_raw"] == 0.60
        assert pb["capacity_factor"] == 0.5
        assert pb["all_in_ev_usd"] > 0
        # the reservation is the bound size's, never the policy's
        assert float(got["order"]["reserved_usd"]) == pytest.approx(
            float(L.reservation_for(L.D(50), L.D(0.40), at=NOW,
                                    fee_fn=FEE)))
        r = await conn.fetchrow(
            "SELECT * FROM paper_profitability_evaluations WHERE "
            " account_id=$1 AND order_key=$2", acct, o["idempotency_key"])
        assert r["verdict"] == "ENTER" and float(r["qty_out"]) == 50
    finally:
        await _done(conn, tr)


@pg
async def test_capital_hour_refuses_a_slow_entry():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbch")
        # 200 days to the start: far below the EV per capital-hour floor
        await calibrate_account(conn, a["account_id"], regime=B.PRE_GT_24H)
        o = _order(a, key="c1", p=0.60)
        await premap(conn, o["us_market_slug"],
                     game_start=NOW + 200 * 86400.0)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] == B.R_CAPITAL_HOUR_BELOW_FLOOR
        assert got["profitability_bind"]["ev_per_capital_hour"] < \
            B.MIN_EV_PER_CAPITAL_HOUR
    finally:
        await _done(conn, tr)


@pg
async def test_correlated_fixture_exposure_halves_then_refuses():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbco")
        fx = "fx-shared-" + F.uid()
        sizes = []
        for i in range(B.MAX_CORRELATED_SAME_FIXTURE + 1):
            o = _order(a, key="f%d" % i, qty=40, fixture=fx,
                       levels=[{"price": 0.40, "qty": 1000}])
            await premap(conn, o["us_market_slug"],
                         game_start=NOW + 3 * HOUR)
            got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + i)
            sizes.append(float(got["order"]["qty"]) if got.get("ok")
                         else got["refusal"])
        assert sizes[:3] == [40.0, 20.0, 10.0]
        assert sizes[3] == B.R_CORRELATED_EXPOSURE
    finally:
        await _done(conn, tr)


@pg
async def test_churn_refuses_a_reentry_without_material_ev_gain():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbcr")
        acct = a["account_id"]
        slug = "%s:churn" % acct
        await premap(conn, slug, game_start=NOW + 3 * HOUR)
        # a CASH evaluation of the contract (here: the regime authority's
        # ... any recorded CASH): record one directly from a refused entry
        o = _order(a, key="r1", slug=slug, p=0.45)        # EV not positive
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                                  B.R_ALL_IN_EV_NOT_POSITIVE)
        # the same contract minutes later with a marginal improvement
        o = _order(a, key="r2", slug=slug, p=0.47)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + 60)
        assert got["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                                  B.R_ALL_IN_EV_NOT_POSITIVE,
                                  B.R_CHURN_RECENT_REFUSAL)
        o = _order(a, key="r3", slug=slug, p=0.60)        # positive EV now
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + 120)
        # prior EV per contract was <= 0: +1c is material -> passes churn
        assert got["ok"], got
        # an EXIT of the contract, then a re-entry inside the cooldown
        slug2 = "%s:exit" % acct
        await premap(conn, slug2, game_start=NOW + 3 * HOUR)
        g = "paper_group_" + F.uid()
        F_STRAT = F.STRATEGY
        oid = await F.order(conn, a, group_id=g, slug=slug2, qty=10,
                            price=0.40, at=NOW - 600)
        await F.fill(conn, a, order_id=oid, group_id=g, slug=slug2, qty=10,
                     price=0.40, at=NOW - 598)
        so = await F.order(conn, a, group_id=g, slug=slug2, qty=10,
                           price=0.45, at=NOW - 300, role="EXIT",
                           direction="SELL")
        await F.fill(conn, a, order_id=so, group_id=g, slug=slug2,
                     role="EXIT", direction="SELL", qty=10, price=0.45,
                     at=NOW - 298)
        await seed_forward(conn, a, F_STRAT)
        o = _order(a, key="x1", slug=slug2, strategy=F_STRAT,
                   fixture="fx-other-" + F.uid())
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + 200)
        assert got.get("refusal") == B.R_CHURN_RECENT_EXIT, got
        assert got["churn"]["kind"] == "CONTRACT_EXIT"
    finally:
        await _done(conn, tr)


@pg
async def test_turnover_cap_per_hour():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbtc")
        last = None
        for i in range(B.MAX_ENTRIES_PER_HOUR + 1):
            o = _order(a, key="t%d" % i, qty=4,
                       levels=[{"price": 0.40, "qty": 1000}])
            await premap(conn, o["us_market_slug"],
                         game_start=NOW + 3 * HOUR)
            last = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + i)
        assert last["refusal"] == B.R_TURNOVER_CAP
    finally:
        await _done(conn, tr)


@pg
async def test_learned_markout_and_residual_haircut_lower_the_ev():
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pblr")
        acct = a["account_id"]
        o = _order(a, key="m0", qty=10, levels=[{"price": 0.40,
                                                  "qty": 1000}])
        await premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        base = await B.entry_bind(
            conn, account_id=acct, strategy=CG,
            evidence=o["capital_evidence"], qty_in=10,
            slug=o["us_market_slug"], side="LONG", fixture=o["fixture"],
            order_type="MARKETABLE", at=NOW, fee_fn=FEE)
        assert base["refusal"] is None
        ev0 = base["all_in"]["ev_given_fill_usd"]
        await B.record_model(conn, account_id=acct, kind="EXECUTION",
                             payload=B.fit_execution(
                                 [{"strategy": CG, "style": B.TAKER,
                                   "qty": 10, "markout_per_contract": 0.03}
                                  for _ in range(30)], []), at=NOW - 30)
        await B.record_model(conn, account_id=acct, kind="RESIDUAL",
                             payload=B.fit_residuals(
                                 [{"strategy": CG, "sport": "baseball",
                                   "family": B.MONEYLINE,
                                   "expected_ev_usd": 1.0,
                                   "realized_pnl_usd": 0.5, "qty": 10,
                                   "source": "SHADOW"}
                                  for _ in range(20)]), at=NOW - 30)
        b = await B.entry_bind(
            conn, account_id=acct, strategy=CG,
            evidence=o["capital_evidence"], qty_in=10,
            slug=o["us_market_slug"], side="LONG", fixture=o["fixture"],
            order_type="MARKETABLE", at=NOW, fee_fn=FEE)
        assert b["execution"]["charged_adverse_per_contract"] > 0.02
        assert b["residual"]["haircut_per_contract"] > 0.02
        assert b["refusal"] is None
        assert b["all_in"]["ev_given_fill_usd"] < ev0 - 10 * 0.04
        # strong enough, and the entry is CASH
        await B.record_model(conn, account_id=acct, kind="RESIDUAL",
                             payload=B.fit_residuals(
                                 [{"strategy": CG, "sport": "baseball",
                                   "family": B.MONEYLINE,
                                   "expected_ev_usd": 1.0,
                                   "realized_pnl_usd": -2.0, "qty": 10,
                                   "source": "PAPER"}
                                  for _ in range(200)]), at=NOW - 10)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] == B.R_CALIBRATED_EV_NOT_POSITIVE or \
            got["refusal"] == B.R_ALL_IN_EV_NOT_POSITIVE
    finally:
        await _done(conn, tr)


# ── §3 the authority: champion and regime, routed to shadow ──────────

@pg
async def test_regime_without_positive_forward_evidence_routes_to_shadow():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbrg", now=NOW - 3600)
        acct = a["account_id"]
        await calibrate_account(conn, acct, regime=B.PRE_LT_1H)
        # positive forward evidence, all of it PRE_GAME_1H_TO_24H
        await seed_forward(conn, a, CG, game_in_h=3.0)
        o = _order(a, key="g1", qty=20, levels=[{"price": 0.40,
                                                  "qty": 1000}])
        # this entry is 30 minutes before its start: another regime
        await premap(conn, o["us_market_slug"], game_start=NOW + 1800)
        before = await counts(conn, acct)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] == B.R_REGIME_NOT_POSITIVE
        assert got["refusal"] in CA.NO_CAPITAL_AUTHORITY
        after = await counts(conn, acct)
        assert after["orders"] == before["orders"]
        assert after["shadows"] == before["shadows"] + 1
        r = await conn.fetchrow(
            "SELECT * FROM paper_shadow_counterfactuals WHERE account_id=$1"
            "   AND order_key=$2", acct, o["idempotency_key"])
        assert r["capital_refusal"] == B.R_REGIME_NOT_POSITIVE
        # the shadow is recorded at the BOUND size
        assert float(r["qty"]) <= 20
        assert H.j(r["evidence"])["profitability_bind"]["regime"] == \
            B.PRE_LT_1H
        # an unknown regime is CASH / shadow
        ra = await B.regime_authority(conn, account_id=acct, strategy=CG,
                                      regime=B.REGIME_UNKNOWN, now=NOW)
        assert ra["refusal"] == B.R_REGIME_UNKNOWN
        assert B.R_REGIME_UNKNOWN in CA.NO_CAPITAL_AUTHORITY
    finally:
        await _done(conn, tr)


@pg
async def test_net_positive_but_not_absolutely_positive_is_refused():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbcp", now=NOW - 3600)
        acct = a["account_id"]
        await calibrate_account(conn, acct)
        n = CA.MIN_FORWARD_OBSERVATIONS
        await seed_forward(conn, a, CG,
                           outcomes=["WON"] * 11 + ["LOST"] * (n - 11))
        fe = await CA.forward_economics(conn, acct, CG, now=NOW)
        assert fe["verdict"] == CA.POSITIVE and fe["pnl_ci95_low"] < 0
        o = _order(a, key="h1", qty=20, levels=[{"price": 0.40,
                                                  "qty": 1000}])
        await premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["refusal"] == B.R_NOT_ABSOLUTE_CHAMPION
        assert (await counts(conn, acct))["orders"] == 0
        assert await conn.fetchval(
            "SELECT count(*) FROM paper_shadow_counterfactuals WHERE "
            " account_id=$1 AND capital_refusal=$2", acct,
            B.R_NOT_ABSOLUTE_CHAMPION) == 1
    finally:
        await _done(conn, tr)


# ── §4 the decision gate ─────────────────────────────────────────────

def _cand(slug):
    return {"us_market_slug": slug, "payout_event": "HOME",
            "fixture": "fx-" + slug,
            "settlement": {"compatibility": "COMPATIBLE"}}


@pg
async def test_the_decision_gate_binds_and_the_ledger_rederives_the_size():
    from sportsassets.agents import paper_derek as PD
    conn, tr = await _tx()
    try:
        a = await ready(conn, "pbdg", strategy=DEREK)
        acct = a["account_id"]
        ctx = {"account_id": acct, "config": a["config"]}
        slug = "%s:dg" % acct
        await premap(conn, slug, game_start=NOW + 3 * HOUR)
        levels = [{"price": 0.40, "qty": 120}]
        ce = await PD.capital_gate(
            conn, ctx, strategy=DEREK, p=0.60, levels=levels,
            sized={"qty": 100, "limit": 0.40}, cand=_cand(slug),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:dg1",
            threshold_edge_pp=0.5)
        assert ce["capital_eligible"] is True, ce
        assert ce["qty"] == 60                     # depth 120 x 0.5
        assert ce["profitability_bind"]["qty_in"] == 100
        assert ce["capital_authority"]["champion"] is True
        ev = CA.capital_evidence(ce, p=0.60, limit=0.40,
                                 threshold_edge_pp=0.5, basis="TEST",
                                 levels=levels)
        assert ev["pre_bind"]["qty"] == 100
        o = H.order(a, key="dg1", qty=ce["qty"], limit=0.40, at=NOW,
                    slug=slug, fixture="fx-" + slug)
        o.update(strategy=DEREK, capital_evidence=ev,
                 decision_id="paperdec:dg1")
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
        assert float(got["order"]["qty"]) == 60    # never a second shrink
        stages = [r["stage"] for r in await conn.fetch(
            "SELECT stage FROM paper_profitability_evaluations WHERE "
            " account_id=$1 AND us_market_slug=$2 ORDER BY eval_id", acct,
            slug)]
        assert stages == ["DECISION", "LEDGER"]
        # uncalibrated: CASH at the decision, no shadow
        a2 = await H.new_account(conn, "pbdh", now=NOW - 3600)
        ctx2 = {"account_id": a2["account_id"], "config": a2["config"]}
        ce = await PD.capital_gate(
            conn, ctx2, strategy=DEREK, p=0.60, levels=levels,
            sized={"qty": 100, "limit": 0.40}, cand=_cand(slug),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:dg2")
        assert ce["refusals"] == [B.R_CALIBRATED_EV_NOT_POSITIVE]
        assert "shadow" not in ce
        # SHADOW_ONLY: the shadow is recorded at the BOUND size
        await LC.record(conn, account_id=acct, strategy=CG, from_state=None,
                        to_state=LC.SHADOW_ONLY, rule_id="TEST",
                        actor="person:test", evidence={"t": 1}, why="t",
                        at=NOW - 30)
        slug3 = "%s:dg3" % acct
        await premap(conn, slug3, game_start=NOW + 3 * HOUR)
        ce = await PD.capital_gate(
            conn, ctx, strategy=CG, p=0.60, levels=levels,
            sized={"qty": 100, "limit": 0.40}, cand=_cand(slug3),
            side="LONG", at=NOW, fee_fn=FEE, decision_id="paperdec:dg3")
        assert ce["refusals"] == [LC.R_LIFECYCLE_SHADOW_ONLY]
        assert ce["shadow"]["recorded"] is True
        assert ce["shadow_evaluation"]["qty"] == 60
    finally:
        await _done(conn, tr)


def test_every_entry_path_reaches_the_bind():
    der = (PKG / "agents" / "paper_derek.py").read_text()
    assert "PBIND.entry_bind(" in der and "context=(b or {})" in der
    led = (PKG / "bettor_paper_ledger.py").read_text()
    assert "CA.ledger_entry_authority(conn, o, at=at," in led
    assert "fee_fn=fee_fn)" in led
    ca = (PKG / "bettor_capital_authority.py").read_text()
    assert "PBIND.entry_bind(" in ca and "PBIND.authority_extra(" in ca
    # explore and maker submit through the ledger with their evidence
    for f in ("paper_explore.py", "paper_maker.py", "paper_benchmark.py"):
        src = (PKG / "agents" / f).read_text()
        assert "L.submit_order(" in src and "capital_evidence" in src, f


# ── §5 Xavier ────────────────────────────────────────────────────────

def test_xavier_alternatives_use_the_haircut_hold_value_and_exit_fees():
    from sportsassets.agents import paper_xavier as PX
    pos = {"open_qty": 10, "cost_basis_usd": 4.0}
    levels = [{"price": 0.55, "qty": 100, "wire": 0.55}]
    raw = PX.alternatives(pos=pos, levels=levels, p=0.60, fee_fn=FEE,
                          at=NOW)
    cut = PX.alternatives(pos=pos, levels=levels, p=0.60, fee_fn=FEE,
                          at=NOW, hold_haircut_per_contract=0.10)
    hold = {c["action"]: c for c in raw["candidates"]}
    hold_c = {c["action"]: c for c in cut["candidates"]}
    assert hold["HOLD"]["value_usd"] == pytest.approx(6.0)
    assert hold_c["HOLD"]["value_usd"] == pytest.approx(5.0)
    # the EXIT is the walked proceeds after the fee either way
    assert hold_c["EXIT"]["value_usd"] == pytest.approx(5.5 - 0.10)
    assert hold_c["EXIT"]["fees_usd"] == pytest.approx(0.10)
    # REDUCE's kept half is valued at the same haircut hold value
    assert hold_c["REDUCE"]["value_usd"] == pytest.approx(
        5 * 0.55 - 0.05 + 5 * 0.50)
    assert cut["economics"]["residual_haircut_per_contract"] == 0.10
    src = (PKG / "agents" / "paper_xavier.py").read_text()
    assert "PBIND.management_economics(" in src
    assert 'mecon.get("p_hold")' in src


@pg
async def test_management_economics_calibrates_the_hold_probability():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbxm", now=NOW - 3600)
        acct = a["account_id"]
        slug = "%s:xm" % acct
        await premap(conn, slug, game_start=NOW - HOUR)       # in play
        await H.observe(conn, slug, NOW - 10, bids=((0.50, 100),),
                        offers=((0.52, 100),))
        pos = {"us_market_slug": slug, "holding_side": "LONG",
               "strategy": CG}
        # no calibration: anchored to the held side's exit price
        m = await B.management_economics(conn, account_id=acct, pos=pos,
                                         p_raw=0.70, at=NOW)
        assert m["status"] == "BOUND" and m["p_hold"] == 0.50
        assert m["descriptor"]["regime"] == B.IN_PLAY
        # a measured, accurate in-play cell keeps most of the raw p
        await B.record_model(conn, account_id=acct, kind="CALIBRATION",
                             payload=_cal(n=600, p=0.70, rate=0.70,
                                          regime=B.IN_PLAY), at=NOW - 5)
        m = await B.management_economics(conn, account_id=acct, pos=pos,
                                         p_raw=0.70, at=NOW)
        assert 0.65 < m["p_hold"] <= 0.70
        # and a raw p below the market is never raised
        m = await B.management_economics(conn, account_id=acct, pos=pos,
                                         p_raw=0.30, at=NOW)
        assert m["p_hold"] <= 0.30
    finally:
        await _done(conn, tr)


# ── §6 the fits and the CASH decision ────────────────────────────────

@pg
async def test_the_fits_learn_from_settled_decisions_and_shadows():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbft", now=NOW - 86400)
        acct = a["account_id"]
        for i in range(12):
            d = await F.decision(conn, a, at=NOW - 7200 - i, p=0.6,
                                 verdict="REFUSE")
            await premap(conn, d["slug"], game_start=NOW - 3600)
            await F.valuation(conn, experiment_id="pbft", at=NOW - 7200,
                              p=0.6, slug=d["slug"],
                              outcome=1 if i < 7 else 0)
        await seed_forward(conn, a, CG, n=5)
        got = await B.fit_all(conn, account_id=acct, now=NOW)
        assert got["CALIBRATION"]["observations"] == 12
        assert got["RESIDUAL"]["observations"] == 5          # the shadows
        assert "model_id" in got["EXECUTION"]
        m = await B.latest_models(conn, acct)
        cell = m["CALIBRATION"]["cells"][B.cell_key(
            "baseball", B.MONEYLINE, B.PRE_1H_24H)]
        assert cell["n"] == 12 and cell["observed"] == pytest.approx(7 / 12)
        assert m["RESIDUAL"]["cells"]["%s|*|*" % CG]["shadow"] == 5
        # the step refits at most every FIT_EVERY_S
        B._LAST_FIT.pop(acct, None)
        s1 = await B.step(conn, {"account_id": acct, "now": NOW + 1})
        s2 = await B.step(conn, {"account_id": acct, "now": NOW + 2})
        assert s1["ran"] is True and s2["ran"] is False
    finally:
        await _done(conn, tr)


@pg
async def test_the_cash_decision_is_recorded_when_nothing_enters():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbcs", now=NOW - 3600)
        acct = a["account_id"]
        await F.decision(conn, a, at=NOW + 1, p=0.6, verdict="REFUSE")
        await F.decision(conn, a, at=NOW + 2, p=0.6, verdict="REFUSE")
        got = await B.cash_step(conn, {"account_id": acct, "now": NOW,
                                       "clock": lambda: NOW + 5})
        assert got["recorded"] == 1
        r = await conn.fetchrow(
            "SELECT * FROM paper_cash_decisions WHERE account_id=$1", acct)
        assert r["decision"] == "CASH" and r["strategy"] == F.STRATEGY
        assert r["decisions_evaluated"] == 2 and r["entries"] == 0
        assert H.j(r["binding_refusals"]) == {"TEST_REFUSAL": 2}
        # idempotent per pass; a strategy that entered records no CASH
        again = await B.cash_step(conn, {"account_id": acct, "now": NOW,
                                         "clock": lambda: NOW + 5})
        assert again["recorded"] == 0
        await F.decision(conn, a, at=NOW + 101, p=0.6, verdict="REFUSE")
        await F.order(conn, a, group_id="paper_group_" + F.uid(),
                      slug="s-" + F.uid(), qty=1, price=0.4, at=NOW + 102)
        got = await B.cash_step(conn, {"account_id": acct, "now": NOW + 100,
                                       "clock": lambda: NOW + 105})
        assert got["recorded"] == 0 and got["strategies_entered"] == 1
    finally:
        await _done(conn, tr)


def test_the_fit_and_cash_steps_run_in_the_paper_pass():
    from sportsassets.agents import paper_runtime as PR
    names = [n for n, _ in PR.default_steps()]
    assert names.index("profitability_fit") < names.index("derek")
    assert names.index("cash_fallback") > names.index("derek")
    assert names.index("cash_fallback") < names.index("simulate_after_delay")


# ── §7 the acceptance read ───────────────────────────────────────────

@pg
async def test_the_acceptance_read_marks_everything_and_reconciles(
        monkeypatch):
    from sportsassets import bettor_paper_simulator as SIM
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbac", now=NOW - 86400)
        acct = a["account_id"]

        async def _admit(conn, o, *, at, fee_fn=None):
            return {"refusal": None}
        # THIS proof is about the read, not the entry gate: three real
        # ledger positions (order -> simulated fill -> FILL ledger entry)
        monkeypatch.setattr(CA, "ledger_entry_authority", _admit)
        s1, s2, s3 = ("%s:m%d" % (acct, i) for i in range(3))
        at = NOW - 7200
        oids = []
        for s in (s1, s2, s3):
            o = H.order(a, key=s[-2:], qty=10, limit=0.40, slug=s, at=at,
                        fixture="fx-" + s)
            o["strategy"] = CG
            g = await L.submit_order(conn, o, fee_fn=FEE, now=at)
            assert g["ok"], g
            oids.append(g["order"]["order_id"])
        for s, oid in zip((s1, s2, s3), oids):
            await H.observe(conn, s, at + 3, offers=[(0.40, 100)])
            got = await SIM.simulate_order(conn, oid, now=at + 4, fee_fn=FEE)
            assert got["state"] == "FILLED", got
        # three open positions: a live book, an old executable exit only,
        # and no executable exit ever
        await H.observe(conn, s1, NOW - 30, bids=((0.45, 100),),
                        offers=((0.47, 100),))
        await H.observe(conn, s2, NOW - 5000, bids=((0.42, 100),),
                        offers=((0.44, 100),))
        await H.observe(conn, s2, NOW - 20, bids=(), offers=((0.44, 100),))
        await H.observe(conn, s3, NOW - 20, bids=(), offers=((0.44, 100),))
        raw = await L.balances(conn, acct, now=NOW)
        assert raw["unrealized_pnl_usd"] is None        # the defect
        v = await CA.acceptance_view(conn, account_id=acct, now=NOW)
        paper = v["paper"]
        assert paper["unrealized_pnl_usd"] is not None
        assert paper["open_exposure_usd"] is not None
        assert paper["unrealized_mark_basis"] == {
            CA.MB_LEDGER: 1, CA.MB_SETTLEMENT: 0, CA.MB_LAST_EXIT: 1,
            CA.MB_ZERO_FLOOR: 1}
        # 10 x (0.45 + 0.42 + 0) - 3 x 4.1 cost basis
        assert paper["unrealized_pnl_usd"] == pytest.approx(
            10 * 0.45 + 10 * 0.42 - 3 * 4.1)
        st = v["strategies"][CG]
        assert st["unrealized_paper_pnl_usd"] is not None
        assert st["open_exposure_usd"] == pytest.approx(3 * 4.1)
        m = paper["management"]
        assert m["opening_equity_usd"] == 500000.0
        assert m["epoch_start"] == "2026-10-05 00:00:00 America/New_York"
        assert m["null_fields"] == []
        assert m["residual_usd"] == 0.0
        assert m["reconciles_to_the_cent"] is True, json.dumps(m, default=str)
        assert m["equity_usd"] == pytest.approx(
            500000.0 + m["realized_pnl_usd"] + m["unrealized_pnl_usd"],
            abs=0.005)
        pb = st["profitability_bind"]
        assert set(pb["regime_authority"]) == set(B.REGIMES) | {
            B.REGIME_UNKNOWN}
        assert pb["champion"]["rule"] == \
            "ABSOLUTE_POSITIVE_FORWARD_NET_CI_LOW_GT_0"
        assert "cash_decisions_since_cutover" in pb
        assert v["profitability_bind"]["version"] == B.VERSION
    finally:
        await _done(conn, tr)


# ── §8 bookkeeping ───────────────────────────────────────────────────

def test_every_bind_refusal_is_classified_and_authority_refusals_shadow():
    from sportsassets import refusal_taxonomy_table as TT
    codes = {v for k, v in vars(B).items() if k.startswith("R_")}
    assert codes == set(B.ALL_REFUSALS)
    for c in codes:
        assert c in TT.TABLE, c
    assert set(B.AUTHORITY_REFUSALS) <= set(CA.NO_CAPITAL_AUTHORITY)
    assert not set(B.REFUSALS) & set(CA.NO_CAPITAL_AUTHORITY)


def test_the_bind_raises_no_cap_limit_or_authority():
    src = (PKG / "bettor_paper_profitability_bind.py").read_text()
    for word in ("per_order_cap_usd", "per_market_cap_usd", "transition(",
                 "LC.record(", "INSERT INTO paper_orders",
                 "INSERT INTO paper_ledger", "UPDATE ", "DELETE FROM"):
        assert word not in src, word
    tree = ast.parse(src)
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
        elif isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
    for m in mods:
        assert not any(k in m for k in (
            "pos_os", "funded", "execution_gate", "entry_execution", "pmus",
            "kalshi", "live_executor", "execmirror", "smalllive")), m
    assert B.MAX_DEPTH_FRACTION <= 1.0
    assert B.CORRELATION_SIZE_DECAY < 1.0


@pg
async def test_the_tables_are_append_only_and_labelled():
    conn, tr = await _tx()
    try:
        a = await H.new_account(conn, "pbao", now=NOW - 60)
        mid = await B.record_model(conn, account_id=a["account_id"],
                                   kind="CALIBRATION", payload=_cal(n=1),
                                   at=NOW)
        assert mid is not None
        for sql in ("UPDATE paper_profitability_models SET version='x'",
                    "DELETE FROM paper_profitability_models",
                    "TRUNCATE paper_profitability_evaluations",
                    "TRUNCATE paper_cash_decisions"):
            with pytest.raises(Exception):
                async with conn.transaction():
                    await conn.execute(sql)
        # an evaluation can never record a raised size or an inflated p
        for cols, vals in (("qty_in, qty_out", "1, 2"),
                           ("p_raw, p_used", "0.5, 0.6"),
                           ("capacity_factor", "1.5")):
            with pytest.raises(Exception):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO paper_profitability_evaluations "
                        "(account_id, strategy, stage, verdict, refusal, "
                        " evaluated_at, %s) VALUES ('a', 's', 'LEDGER', "
                        " 'CASH', 'X', now(), %s)" % (cols, vals))
    finally:
        await _done(conn, tr)


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_profitability_bind.py" in listed.splitlines()
