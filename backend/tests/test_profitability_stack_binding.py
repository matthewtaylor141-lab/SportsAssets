"""CAPITAL-CRITICAL: EVERY PROFITABILITY ITEM BINDS IN THE PAPER DECISION
CHAIN (bettor_paper_profitability_bind + bettor_paper_profitability_stack,
migrations 309 / 311), AND THE FORWARD SCOREBOARD READS IT.

Runs the PRODUCTION gates (PROFITABILITY_BIND_ENFORCED and
CAPITAL_AUTHORITY_ENFORCED below: the suite's seeded passthroughs in
tests/conftest.py do not apply here). One or more named tests per item; each
proves the BINDING effect -- the paper order's size is lowered, the entry is
refused / CASH, a quarantine is written, a management action is refused --
not merely that a number is reported.

  item -> code path that binds it
   1 calibration x price region   bind.calibrate (region cells shrunk to the
                                   parent) <- bind._bind_core <- entry_bind
   2 executable EV on depth       bind.all_in on the walked fills
   3 maker / taker learned cost   bind.execution_terms(style)
   4 fees                         bind.all_in fee per walked level
   5 spread                       bind.book_inputs -> management_cost
   6 slippage                     walked cost + capacity_frontier
   7 adverse selection            max(IOC bound, learned markout)
   8 management / exit cost       bind.management_cost (MANAGEMENT model)
   9 settlement difference        bind.settlement_terms caps p
  10 freshness decay              bind.freshness haircut / refusal
  11 capital-hour                 bind.capital_hour
  12 capacity frontier            bind.capacity_frontier (depth fraction)
  13 marginal-EV sizing           capacity_frontier with every cost
  14 correlation                  bind.correlation_factor
  15 scenario concentration       bind.scenario_cap <- scenario_exposure
  16 NO_TRADE / REDUCE / CASH     refusal -> CASH evaluation, cash_step,
                                   lifecycle REDUCED_SIZE factor
  17 churn / reprice deadband     bind.churn_check (REPRICE)
  18 management deadband          paper_xavier.alternatives (min improvement
                                   from management_economics)
  19 absolute champion            bind.champion_verdict <- authority_extra
  20 no best-losing promotion     champion_verdict / LC.promotion_evidence
  21 automatic quarantine         stack.evaluate_quarantine -> LC.record ->
                                   lifecycle entry gate refuses
  22 counterfactual ledger        bind.record_variants + stack.settle_variants
  23 variants                     bind.counterfactual_variants
  24 attribution                  bind.attribution (sums to the EV)
  25 residual learning            bind.residual_haircut
  26 P&L forecast                 stack.pnl_forecast
   S scoreboard                   stack.scoreboard / scoreboard_read, GET
                                   /api/command/paper/profitability-scoreboard
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from sportsassets import bettor_capital_authority as CA
from sportsassets import bettor_paper_ledger as L
from sportsassets import bettor_paper_profitability_bind as B
from sportsassets import bettor_paper_profitability_stack as S
from sportsassets import bettor_settlement_difference_policy as SDP
from sportsassets import bettor_strategy_lifecycle as LC

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
    from tests import test_profitability_bind as PB
except ImportError:                                           # pragma: no cover
    import intel_fixture as F
    import paper_harness as H
    import test_profitability_bind as PB

PROFITABILITY_BIND_ENFORCED = True
CAPITAL_AUTHORITY_ENFORCED = True

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")
ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"
NOW = PB.NOW
HOUR = 3600.0
CG = PB.CG
FEE = PB.FEE
DESC = {"sport": "baseball", "family": B.MONEYLINE, "regime": B.PRE_1H_24H,
        "expected_hold_hours": 6.25}


def fee(q, px):
    return 0.01 * q


def _ev(p=0.60, fills=((0.40, 100.0),), depth=10000, adverse=0.0, **kw):
    fills = [list(f) for f in fills]
    return dict({"p": p, "fills": fills, "best_price": fills[0][0],
                 "limit": max(f[0] for f in fills),
                 "adverse_selection_usd": adverse,
                 "levels": [{"price": f[0], "qty": depth} for f in fills]},
                **kw)


def bind(ev=None, *, qty_in=100, cal=None, execution=None, residuals=None,
         management=None, inputs=None, at=NOW, scenario=None, n_corr=0,
         order_type="MARKETABLE", fee_fn=fee, desc=None):
    return B.bind_economics(
        evidence=ev or _ev(), qty_in=qty_in, fee_fn=fee_fn,
        desc=desc or DESC,
        calibration=PB._cal(n=300) if cal is None else cal,
        execution=execution, residuals=residuals, strategy=CG,
        order_type=order_type, n_correlated=n_corr, management=management,
        inputs=inputs, at=at, scenario=scenario)


# ── 1 calibration by sport x family x regime x PRICE REGION ──────────

def _region_obs():
    obs = []
    for i in range(300):         # the 0.35-0.65 region: accurate
        obs.append({"sport": "baseball", "family": B.MONEYLINE,
                    "regime": B.PRE_1H_24H, "p": 0.60, "price": 0.40,
                    "y": 1.0 if i < 180 else 0.0})
    for i in range(100):         # the longshot region: badly overconfident
        obs.append({"sport": "baseball", "family": B.MONEYLINE,
                    "regime": B.PRE_1H_24H, "p": 0.60, "price": 0.10,
                    "y": 1.0 if i < 10 else 0.0})
    return obs


def test_item01_price_region_is_a_calibration_dimension_and_lowers_size():
    m = B.fit_calibration(_region_obs())
    assert m["region_key"] == "sport|family|regime|price_region"
    assert B.region_key("baseball", B.MONEYLINE, B.PRE_1H_24H, "0.00-0.15") \
        in m["region_cells"]
    no_region = dict(m, region_cells={})
    kw = dict(sport="baseball", family=B.MONEYLINE, regime=B.PRE_1H_24H,
              p_raw=0.60)
    acc = B.calibrate(m, market=0.40, **kw)
    acc0 = B.calibrate(no_region, market=0.40, **kw)
    lng = B.calibrate(m, market=0.10, **kw)
    lng0 = B.calibrate(no_region, market=0.10, **kw)
    assert acc["price_region"] == "0.35-0.65"
    # the accurate region keeps more of p; the overconfident one far less
    assert acc["p_used"] > acc0["p_used"]
    assert lng["p_used"] < lng0["p_used"]
    # BINDING: at a longshot price the region pulls the bound EV down
    ev = _ev(p=0.60, fills=((0.10, 100.0),))
    with_r = bind(ev, cal=m)
    without = bind(ev, cal=no_region)
    assert with_r["calibration"]["p_used"] < without["calibration"]["p_used"]
    assert (with_r.get("ev_per_contract") or -1) < \
        (without.get("ev_per_contract") or 0)


def test_item01_sparse_price_region_shrinks_to_the_coarser_cell():
    obs = _region_obs()[:300] + [
        {"sport": "baseball", "family": B.MONEYLINE, "regime": B.PRE_1H_24H,
         "p": 0.60, "price": 0.10, "y": 0.0} for _ in range(2)]
    m = B.fit_calibration(obs)
    c = B.calibrate(m, sport="baseball", family=B.MONEYLINE,
                    regime=B.PRE_1H_24H, p_raw=0.60, market=0.10)
    assert c["n_region_bin"] == 2
    assert c["region_weight"] == pytest.approx(2 / (2 + B.K_PRICE_REGION))
    # two observations move the bias only by their weight
    assert abs(c["bin_bias"] - c["parent_bin_bias"]) <= \
        c["region_weight"] * 1.0 + 1e-9
    assert c["p_used"] <= 0.60


# ── 2 executable EV from depth / fill, not the midpoint ──────────────

def test_item02_ev_is_on_the_walked_depth_not_the_midpoint():
    ev = _ev(p=0.60, fills=((0.40, 50.0), (0.58, 20.0)))
    b = bind(ev, qty_in=70, inputs={"held_mid": 0.39, "spread": 0.02})
    # the walk stops where the marginal contract stops paying
    assert b["refusal"] is None
    assert b["qty"] == 50
    assert b["capacity"]["stopped_at"]["price"] == 0.58
    # the cost is the walked VWAP at the bound size, never mid x qty
    assert b["all_in"]["cost_usd"] == pytest.approx(50 * 0.40)
    pol = b["all_in_at_policy_size"]
    assert pol["cost_usd"] == pytest.approx(50 * 0.40 + 20 * 0.58)
    assert pol["ev_given_fill_usd"] < 70 * (0.60 - 0.39)
    assert pol["ev_given_fill_usd"] < b["all_in"]["ev_given_fill_usd"]
    # a book whose depth is all above p is CASH whatever its midpoint
    thin = bind(_ev(p=0.60, fills=((0.40, 5.0), (0.58, 100.0))),
                inputs={"held_mid": 0.39})
    assert thin["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                               B.R_ALL_IN_EV_NOT_POSITIVE)


# ── 3 maker vs taker learned execution cost ──────────────────────────

def test_item03_maker_and_taker_carry_their_own_learned_costs():
    exm = B.fit_execution(
        [{"strategy": CG, "style": B.MAKER, "qty": 10,
          "markout_per_contract": 0.06} for _ in range(40)]
        + [{"strategy": CG, "style": B.TAKER, "qty": 10,
            "markout_per_contract": 0.0} for _ in range(40)],
        [{"strategy": CG, "style": B.MAKER, "qty": 10, "filled_qty": 1}
         for _ in range(40)])
    tk = bind(execution=exm, order_type="MARKETABLE", qty_in=20)
    mk = bind(execution=exm, order_type="RESTING", qty_in=20)
    assert tk["execution"]["style"] == B.TAKER
    assert mk["execution"]["style"] == B.MAKER
    assert mk["execution"]["charged_adverse_per_contract"] > \
        tk["execution"]["charged_adverse_per_contract"] + 0.03
    assert mk["execution"]["fill_probability"] < \
        tk["execution"]["fill_probability"]
    # BINDING: the maker's expected EV and its capital-hour size factor drop
    assert mk["all_in_at_policy_size"]["expected_ev_usd"] < \
        tk["all_in_at_policy_size"]["expected_ev_usd"]


# ── 4 fees ───────────────────────────────────────────────────────────

def test_item04_fees_lower_the_ev_and_refuse_when_they_eat_the_edge():
    cheap = bind(fee_fn=lambda q, px: 0.01 * q)
    dear = bind(fee_fn=lambda q, px: 0.08 * q)
    assert dear["all_in_at_policy_size"]["fees_usd"] > \
        cheap["all_in_at_policy_size"]["fees_usd"]
    assert dear.get("ev_per_contract", 0) < cheap["ev_per_contract"]
    gone = bind(fee_fn=lambda q, px: 0.30 * q)
    assert gone["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                               B.R_ALL_IN_EV_NOT_POSITIVE)
    assert gone["qty"] == 0


# ── 5 spread ─────────────────────────────────────────────────────────

def test_item05_the_observed_spread_is_charged_and_binds():
    tight = bind(inputs={"spread": 0.01, "held_bid": 0.395})
    wide = bind(inputs={"spread": 0.30, "held_bid": 0.25})
    assert tight["management"]["spread_basis"] == "OBSERVED_BOOK"
    assert wide["management"]["charged_per_contract"] > \
        tight["management"]["charged_per_contract"]
    assert wide["ev_per_contract"] < tight["ev_per_contract"]
    huge = bind(inputs={"spread": 0.95, "held_bid": 0.01},
                cal=PB._cal(n=300, p=0.60, rate=0.60))
    # a half-spread exit cost of 0.475 at a 0.5 exit rate eats the edge
    assert huge["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                               B.R_ALL_IN_EV_NOT_POSITIVE)
    # no readable book: the conservative default spread, never zero
    none = bind(inputs={})
    assert none["management"]["spread_basis"] == "DEFAULT_SPREAD"
    assert none["management"]["spread"] == B.DEFAULT_SPREAD


@pg
async def test_item05_entry_bind_reads_the_spread_from_the_observed_book():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "pssp")
        o = PB._order(a, key="sp1", qty=10,
                      levels=[{"price": 0.40, "qty": 1000}])
        await PB.premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        await H.observe(conn, o["us_market_slug"], NOW - 5,
                        bids=((0.20, 100),), offers=((0.40, 100),))
        b = await B.entry_bind(
            conn, account_id=a["account_id"], strategy=CG,
            evidence=o["capital_evidence"], qty_in=10,
            slug=o["us_market_slug"], side="LONG", fixture=o["fixture"],
            order_type="MARKETABLE", at=NOW, fee_fn=FEE)
        assert b["management"]["spread_basis"] == "OBSERVED_BOOK"
        assert b["management"]["spread"] == pytest.approx(0.20)
        assert b["bind_inputs"]["held_mid"] == pytest.approx(0.30)
    finally:
        await PB._done(conn, tr)


# ── 6 slippage ───────────────────────────────────────────────────────

def test_item06_slippage_is_in_the_walked_cost_and_attributed():
    ev = _ev(p=0.60, fills=((0.40, 10.0), (0.42, 10.0), (0.44, 10.0)))
    b = bind(ev, qty_in=30, inputs={"held_mid": 0.39, "spread": 0.02})
    fin = b["all_in"]
    q = fin["filled_qty"]
    assert fin["cost_usd"] > q * 0.40              # deeper levels cost more
    assert b["attribution"]["terms"]["slippage_usd"] == pytest.approx(
        -(fin["cost_usd"] - q * 0.40))
    assert b["attribution"]["terms"]["slippage_usd"] < 0


# ── 7 adverse selection ──────────────────────────────────────────────

def test_item07_adverse_selection_charges_the_larger_of_bound_and_learned():
    ex = B.fit_execution([{"strategy": CG, "style": B.TAKER, "qty": 10,
                           "markout_per_contract": 0.05}
                          for _ in range(60)], [])
    base = bind(ev=_ev(adverse=0.5))              # 0.005 per contract bound
    learned = bind(ev=_ev(adverse=0.5), execution=ex)
    assert base["execution"]["charged_adverse_per_contract"] == \
        pytest.approx(0.005)
    assert learned["execution"]["charged_adverse_per_contract"] > 0.04
    assert learned["ev_per_contract"] < base["ev_per_contract"] - 0.03


# ── 8 management / exit cost ─────────────────────────────────────────

def test_item08_learned_management_cost_is_charged_and_binds():
    rows = ([{"strategy": CG, "exited": True,
              "exit_cost_per_contract": 0.10} for _ in range(40)]
            + [{"strategy": CG, "exited": False} for _ in range(10)])
    m = B.fit_management(rows)
    r = m["by_strategy"][CG]
    assert r["exit_rate"] > 0.7 and r["exit_cost_per_contract"] == 0.10
    prior = bind()
    learned = bind(management=m)
    assert learned["management"]["basis"] == "LEARNED"
    assert learned["management"]["charged_per_contract"] == pytest.approx(
        r["exit_rate"] * 0.10, abs=1e-6)
    assert learned["ev_per_contract"] < prior["ev_per_contract"] - 0.02
    heavy = B.fit_management([{"strategy": CG, "exited": True,
                               "exit_cost_per_contract": 0.40}
                              for _ in range(200)])
    assert bind(management=heavy)["refusal"] in (
        B.R_CALIBRATED_EV_NOT_POSITIVE, B.R_ALL_IN_EV_NOT_POSITIVE)


# ── 9 settlement difference ──────────────────────────────────────────

def _priced(p, q=0.0881):
    return {"compatibility": SDP.SETTLEMENT_PRICED, "policy_id": SDP.POLICY_ID,
            "version": SDP.VERSION, "p": p, "q_hi": q}


def test_item09_priced_settlement_difference_caps_p_and_refuses():
    plain = bind()
    capped = bind(inputs={"settlement": _priced(0.52)})
    assert capped["settlement"]["applies"] is True
    assert capped["calibration"]["p_used"] <= 0.52
    assert capped["calibration"]["p_used"] < plain["calibration"]["p_used"]
    assert capped["ev_per_contract"] < plain["ev_per_contract"]
    assert capped["attribution"]["terms"]["settlement_difference_usd"] < 0
    gone = bind(inputs={"settlement": _priced(0.41)})
    assert gone["refusal"] in (B.R_CALIBRATED_EV_NOT_POSITIVE,
                               B.R_ALL_IN_EV_NOT_POSITIVE)
    # COMPATIBLE terms: no cap, no cost
    ok = bind(inputs={"settlement": {"compatibility": "COMPATIBLE"}})
    assert ok["settlement"]["applies"] is False
    assert ok["calibration"]["p_used"] == plain["calibration"]["p_used"]


# ── 10 freshness decay ───────────────────────────────────────────────

def test_item10_freshness_haircut_grows_with_age_and_refuses_beyond_bound():
    f0 = bind(inputs={"evaluated_at": NOW, "p_observed_at": NOW})
    f15 = bind(inputs={"evaluated_at": NOW, "p_observed_at": NOW - 15})
    f29 = bind(inputs={"evaluated_at": NOW, "p_observed_at": NOW - 29,
                       "book_observed_at": NOW - 2})
    assert f0["freshness"]["haircut_per_contract"] == 0.0
    assert f15["freshness"]["haircut_per_contract"] == pytest.approx(
        B.FRESHNESS_MAX_HAIRCUT * 0.5)
    assert f29["freshness"]["haircut_per_contract"] > \
        f15["freshness"]["haircut_per_contract"]
    assert f0["ev_per_contract"] > f15["ev_per_contract"] > \
        f29["ev_per_contract"]
    old = bind(inputs={"evaluated_at": NOW, "p_observed_at": NOW - 31})
    assert old["refusal"] == B.R_FRESHNESS_BEYOND_BOUND and old["qty"] == 0
    # an old BOOK alone takes the maximum haircut, not a refusal
    book = bind(inputs={"evaluated_at": NOW, "book_observed_at": NOW - 300})
    assert book["refusal"] is None
    assert book["freshness"]["haircut_per_contract"] == \
        B.FRESHNESS_MAX_HAIRCUT


def test_item10_the_ledger_rederives_freshness_at_the_decision_instant():
    ins = {"evaluated_at": NOW, "p_observed_at": NOW - 10}
    ev = _ev(bind_inputs=ins)
    # the ledger runs 20 s later: the carried decision instant is used
    later = bind(ev, at=NOW + 20, inputs={"p_observed_at": None})
    assert later["freshness"]["p_age_s"] == pytest.approx(10.0)
    assert later["refusal"] is None


# ── 11 capital-hour opportunity cost ─────────────────────────────────

def test_item11_capital_hour_refuses_slow_capital_and_shrinks_marginal():
    slow = bind(desc=dict(DESC, expected_hold_hours=24 * 400.0))
    assert slow["refusal"] == B.R_CAPITAL_HOUR_BELOW_FLOOR
    mid = bind(desc=dict(DESC, expected_hold_hours=24 * 30.0), qty_in=50)
    assert 0 < mid["capital_hour"]["factor"] < 1
    assert mid["qty"] < 50


# ── 12 capacity frontier ─────────────────────────────────────────────

def test_item12_capacity_frontier_caps_at_the_displayed_depth_fraction():
    b = bind(_ev(depth=60), qty_in=100)
    assert b["capacity"]["depth_cap_qty"] == 30
    assert b["qty"] == 30 and b["capacity_factor"] == 0.3


# ── 13 marginal-EV sizing ────────────────────────────────────────────

def test_item13_the_marginal_contract_carries_every_cost():
    ev = _ev(p=0.60, fills=((0.40, 10.0), (0.53, 10.0), (0.60, 10.0)))
    fresh = bind(ev, qty_in=30, inputs={"spread": 0.02})
    stale = bind(ev, qty_in=30, inputs={"spread": 0.02,
                                        "evaluated_at": NOW,
                                        "book_observed_at": NOW - 300})
    # the freshness haircut alone moves the frontier inward
    assert stale["capacity"]["positive_marginal_qty"] <= \
        fresh["capacity"]["positive_marginal_qty"]
    assert fresh["capacity"]["stopped_at"] is not None
    got = B.capacity_frontier(
        fills=[[0.40, 10], [0.50, 10]], p_used=0.55, fee_fn=fee,
        adverse_per_contract=0.0, haircut_per_contract=0.0)
    cut = B.capacity_frontier(
        fills=[[0.40, 10], [0.50, 10]], p_used=0.55, fee_fn=fee,
        adverse_per_contract=0.0, haircut_per_contract=0.0,
        extra_per_contract={"management": 0.05})
    assert got["qty"] == 20 and cut["qty"] == 10


# ── 14 correlation-aware sizing ──────────────────────────────────────

def test_item14_correlated_exposure_halves_then_refuses():
    q0 = bind(qty_in=40)["qty"]
    q1 = bind(qty_in=40, n_corr=1)["qty"]
    assert q1 == q0 // 2
    r = bind(qty_in=40, n_corr=B.MAX_CORRELATED_SAME_FIXTURE)
    assert r["refusal"] == B.R_CORRELATED_EXPOSURE


# ── 15 scenario concentration ────────────────────────────────────────

def test_item15_scenario_concentration_only_lowers_size():
    # the cap is the OWNER's own per-fixture rail, passed on the scenario
    CAP = 3000.0
    free = bind(qty_in=100, scenario={"exposure_usd": 0.0, "cap_usd": CAP})
    near = bind(qty_in=100, scenario={"exposure_usd": CAP - 10.0,
                                      "cap_usd": CAP})
    full = bind(qty_in=100, scenario={"exposure_usd": CAP, "cap_usd": CAP})
    assert free["qty"] >= near["qty"] >= 1
    assert near["qty"] < free["qty"]
    assert near["scenario"]["qty_cap"] == near["qty"]
    assert full["refusal"] == B.R_SCENARIO_CONCENTRATION


def test_item15_no_invented_event_cap_when_the_owner_sets_none():
    # owner, 2026-10-06: no arbitrary event cap; existing rails authoritative
    assert B.SCENARIO_MAX_EXPOSURE_USD is None
    free = bind(qty_in=100, scenario={"exposure_usd": 0.0})
    huge = bind(qty_in=100, scenario={"exposure_usd": 1e7})
    assert huge["qty"] == free["qty"] and huge.get("refusal") is None
    got = B.scenario_cap({"exposure_usd": 1e7}, capital_per_contract=1.0)
    assert got["qty_cap"] is None and got["cap_usd"] is None
    assert got["basis"] == "MEASURED_NO_OWNER_EVENT_CAP"
    assert got["exposure_usd"] == 1e7                 # measured, recorded


def test_item15_the_owner_rail_is_the_main_accounts_policy():
    from sportsassets import bettor_paper_limits as LIMITS
    assert LIMITS.effective_caps({}, LIMITS.ACCOUNT_ID, "ENTRY").get(
        "per_fixture_cap_usd") is None


@pg
async def test_item15_scenario_exposure_across_strategies_shrinks_at_ledger(
        monkeypatch):
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "psscn")
        monkeypatch.setattr(B, "SCENARIO_MAX_EXPOSURE_USD", 20.0)
        fx = "fx-scn-" + F.uid()
        o = PB._order(a, key="s1", qty=40, fixture=fx,
                      levels=[{"price": 0.40, "qty": 1000}])
        await PB.premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
        first = float(got["order"]["qty"])
        assert first * 0.41 <= 20.0 + 1e-6          # capped by the scenario
        o2 = PB._order(a, key="s2", qty=40, fixture=fx,
                       levels=[{"price": 0.40, "qty": 1000}])
        await PB.premap(conn, o2["us_market_slug"],
                        game_start=NOW + 3 * HOUR)
        got2 = await L.submit_order(conn, o2, fee_fn=FEE, now=NOW + 1)
        if got2.get("ok"):
            assert float(got2["order"]["qty"]) < first
        else:
            assert got2["refusal"] in (B.R_SCENARIO_CONCENTRATION,
                                       B.R_SIZE_BELOW_ONE)
    finally:
        await PB._done(conn, tr)


# ── 16 NO_TRADE / REDUCE / CASH authority ────────────────────────────

@pg
async def test_item16_nothing_clears_so_cash_is_the_recorded_decision():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "pscash")
        acct = a["account_id"]
        o = PB._order(a, key="c1", p=0.45)              # nothing clears
        await PB.premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"] is False
        r = await conn.fetchrow(
            "SELECT verdict, qty_out FROM paper_profitability_evaluations "
            " WHERE account_id=$1 ORDER BY eval_id DESC LIMIT 1", acct)
        assert r["verdict"] == "CASH" and float(r["qty_out"]) == 0
        await F.decision(conn, a, at=NOW + 1, p=0.41, verdict="REFUSE")
        cs = await B.cash_step(conn, {"account_id": acct, "now": NOW,
                                      "clock": lambda: NOW + 5})
        assert cs["recorded"] == 1
    finally:
        await PB._done(conn, tr)
    # REDUCE: the lifecycle's reduced state only shrinks
    assert LC.decision_size_factor(LC.REDUCED_SIZE) == LC.REDUCED_SIZE_FACTOR
    assert LC.REDUCED_SIZE_FACTOR < 1.0


# ── 17 churn / reprice deadband ──────────────────────────────────────

@pg
async def test_item17_a_reprice_inside_the_deadband_needs_a_material_gain():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "psrep")
        acct = a["account_id"]
        slug = "%s:rep" % acct
        await PB.premap(conn, slug, game_start=NOW + 3 * HOUR)
        o = PB._order(a, key="r1", slug=slug, qty=10,
                      levels=[{"price": 0.40, "qty": 1000}])
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"], got
        # a re-price of the same contract 2 minutes later, same economics
        o2 = PB._order(a, key="r2", slug=slug, qty=10,
                       levels=[{"price": 0.40, "qty": 1000}])
        got2 = await L.submit_order(conn, o2, fee_fn=FEE, now=NOW + 120)
        assert got2["refusal"] == B.R_CHURN_REPRICE_DEADBAND, got2
        assert got2["churn"]["kind"] == "REPRICE"
        # the order's own re-derivation (same key) is never the deadband
        b = await B.entry_bind(
            conn, account_id=acct, strategy=CG,
            evidence=o["capital_evidence"], qty_in=10, slug=slug,
            side="LONG", fixture=o["fixture"], order_type="MARKETABLE",
            at=NOW + 1, fee_fn=FEE, order_key=o["idempotency_key"])
        assert (b.get("churn") or {}).get("refusal") != \
            B.R_CHURN_REPRICE_DEADBAND
        # past the deadband the same economics pass the churn check
        b = await B.entry_bind(
            conn, account_id=acct, strategy=CG,
            evidence=o2["capital_evidence"], qty_in=10, slug=slug,
            side="LONG", fixture=o2["fixture"], order_type="MARKETABLE",
            at=NOW + B.REPRICE_DEADBAND_S + 5, fee_fn=FEE, order_key="r3")
        assert (b.get("churn") or {}).get("refusal") != \
            B.R_CHURN_REPRICE_DEADBAND
    finally:
        await PB._done(conn, tr)


# ── 18 minimum expected improvement before a management action ───────

def test_item18_a_marginal_exit_is_refused_and_hold_stands():
    from sportsassets.agents import paper_xavier as PX
    pos = {"open_qty": 10, "cost_basis_usd": 4.0}
    levels = [{"price": 0.62, "qty": 100, "wire": 0.62}]
    # EXIT: 10 x 0.62 - 0.10 fee = 6.10 vs HOLD 10 x 0.60 = 6.00
    free = PX.alternatives(pos=pos, levels=levels, p=0.60, fee_fn=FEE,
                           at=NOW)
    assert "EXIT" in {c["action"] for c in free["candidates"]}
    bound = PX.alternatives(
        pos=pos, levels=levels, p=0.60, fee_fn=FEE, at=NOW,
        economics={"min_improvement_per_contract_usd": 0.02})
    acts = {c["action"] for c in bound["candidates"]}
    assert "EXIT" not in acts and "HOLD" in acts
    blk = [c for c in bound["not_rankable"] if c["action"] == "EXIT"][0]
    assert blk["blocker"] == PX.B_BELOW_MIN_IMPROVEMENT
    # a material improvement still ranks
    rich = [{"price": 0.70, "qty": 100, "wire": 0.70}]
    ok = PX.alternatives(pos=pos, levels=rich, p=0.60, fee_fn=FEE, at=NOW,
                         economics={"min_improvement_per_contract_usd": 0.02})
    assert "EXIT" in {c["action"] for c in ok["candidates"]}
    src = (PKG / "agents" / "paper_xavier.py").read_text()
    assert '"min_improvement_per_contract_usd")}' in src


@pg
async def test_item18_management_economics_carries_the_deadband():
    conn, tr = await PB._tx()
    try:
        a = await H.new_account(conn, "psmd", now=NOW - 3600)
        slug = "%s:md" % a["account_id"]
        await PB.premap(conn, slug, game_start=NOW - HOUR)
        m = await B.management_economics(
            conn, account_id=a["account_id"],
            pos={"us_market_slug": slug, "holding_side": "LONG",
                 "strategy": CG}, p_raw=0.6, at=NOW)
        assert m["min_improvement_per_contract_usd"] == \
            B.MIN_MANAGEMENT_IMPROVEMENT_USD > 0
    finally:
        await PB._done(conn, tr)


# ── 19 absolute-positive champion ────────────────────────────────────

def test_item19_capital_needs_an_absolutely_positive_forward_ci():
    n = CA.MIN_FORWARD_OBSERVATIONS
    assert B.champion_verdict(CA.forward_verdict([1.0] * n, []))["champion"]
    wide = CA.forward_verdict([10.0] * 11 + [-11.0] * 9, [])
    assert B.champion_verdict(wide)["refusal"] == B.R_NOT_ABSOLUTE_CHAMPION
    assert B.R_NOT_ABSOLUTE_CHAMPION in CA.NO_CAPITAL_AUTHORITY


# ── 20 no "best losing strategy" promotion ───────────────────────────

def test_item20_the_best_of_losing_strategies_is_never_promoted():
    n = CA.MIN_FORWARD_OBSERVATIONS
    losers = [CA.forward_verdict([-x] * n, []) for x in (0.5, 1.0, 2.0)]
    best = max(losers, key=lambda f: f["net_pnl_usd"])
    assert B.champion_verdict(best)["champion"] is False
    fwd = {"closed_positions": 40, "pnl_ci95_low": -0.1,
           "dollars_per_capital_hour": -0.01}
    assert LC.promotion_evidence(fwd)["ok"] is False
    # and nothing in the stack promotes: it only writes QUARANTINED
    src = (PKG / "bettor_paper_profitability_stack.py").read_text()
    assert "to_state=LC.QUARANTINED" in src
    assert "transition(" not in src and "ACTIVE_CHAMPION" not in src


# ── 21 automatic strategy quarantine ─────────────────────────────────

def test_item21_triggers_fire_on_each_deterioration():
    res = B.fit_residuals([{"strategy": CG, "sport": "baseball",
                            "family": B.MONEYLINE, "expected_ev_usd": 1.0,
                            "realized_pnl_usd": -1.0, "qty": 10,
                            "source": "PAPER"} for _ in range(40)])
    got = S.quarantine_triggers(CG, calibration_rows=[], residual_model=res,
                                execution_model=None)
    assert [g["rule_id"] for g in got] == [S.RULE_RESIDUAL_QUARANTINE]
    ex = B.fit_execution([{"strategy": CG, "style": B.TAKER, "qty": 10,
                           "markout_per_contract": 0.08}
                          for _ in range(30)], [])
    got = S.quarantine_triggers(CG, calibration_rows=[], residual_model=None,
                                execution_model=ex)
    assert [g["rule_id"] for g in got] == [S.RULE_EXECUTION_QUARANTINE]
    rows = [{"p": 0.9, "market": 0.5, "y": 0.0 if i % 2 else 1.0}
            for i in range(60)]
    got = S.quarantine_triggers(CG, calibration_rows=rows,
                                residual_model=None, execution_model=None)
    assert [g["rule_id"] for g in got] == [S.RULE_CALIBRATION_QUARANTINE]
    good = [{"p": 0.5, "market": 0.5, "y": 0.0 if i % 2 else 1.0}
            for i in range(60)]
    assert S.quarantine_triggers(CG, calibration_rows=good,
                                 residual_model=None,
                                 execution_model=None) == []


@pg
async def test_item21_quarantine_is_written_and_refuses_new_entries():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "psqr")
        acct = a["account_id"]
        await B.record_model(conn, account_id=acct, kind="RESIDUAL",
                             payload=B.fit_residuals(
                                 [{"strategy": CG, "sport": "baseball",
                                   "family": B.MONEYLINE,
                                   "expected_ev_usd": 1.0,
                                   "realized_pnl_usd": -1.0, "qty": 10,
                                   "source": "PAPER"} for _ in range(40)]),
                             at=NOW - 30)
        got = await S.evaluate_quarantine(conn, account_id=acct, now=NOW,
                                          strategies=[CG])
        assert got["quarantined"][0]["strategy"] == CG
        cur = await LC.current_state(conn, acct, CG)
        assert cur["state"] == LC.QUARANTINED
        assert cur["rule_id"] == S.RULE_RESIDUAL_QUARANTINE
        ev = await conn.fetchrow(
            "SELECT actor, to_state FROM paper_strategy_lifecycle_events "
            " WHERE account_id=$1 AND strategy=$2 ORDER BY event_id DESC "
            " LIMIT 1", acct, CG)
        assert ev["actor"] == LC.AUTOMATIC_ACTOR
        # BINDING: the next entry is refused under the account lock
        o = PB._order(a, key="q1", qty=10,
                      levels=[{"price": 0.40, "qty": 1000}])
        await PB.premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        sub = await L.submit_order(conn, o, fee_fn=FEE, now=NOW + 1)
        assert sub["ok"] is False
        assert sub["refusal"] == LC.R_LIFECYCLE_QUARANTINED
        # idempotent: already quarantined, nothing more is written
        again = await S.evaluate_quarantine(conn, account_id=acct,
                                            now=NOW + 2, strategies=[CG])
        assert again["quarantined"] == []
    finally:
        await PB._done(conn, tr)


# ── 22 counterfactual ledger for refused trades ──────────────────────

@pg
async def test_item22_refused_trades_get_counterfactual_rows_that_settle():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "pscf")
        acct = a["account_id"]
        slug = "%s:cf" % acct
        await PB.premap(conn, slug, game_start=NOW + 3 * HOUR)
        o = PB._order(a, key="cf1", slug=slug, p=0.43)   # refused
        got = await L.submit_order(conn, o, fee_fn=FEE, now=NOW)
        assert got["ok"] is False
        rows = await conn.fetch(
            "SELECT variant, verdict, refusal, qty FROM "
            " paper_counterfactual_variants WHERE account_id=$1 "
            "   AND us_market_slug=$2", acct, slug)
        names = {r["variant"] for r in rows}
        assert names == set(B.VARIANTS)
        assert {r["verdict"] for r in rows} == {"CASH"}
        assert all(r["refusal"] for r in rows)
        await F.valuation(conn, experiment_id="pscf", at=NOW, p=0.6,
                          slug=slug, outcome=1)
        st = await S.settle_variants(conn, account_id=acct, now=NOW + 10)
        assert st["settled"] >= 4
        out = await conn.fetch(
            "SELECT v.variant, o.outcome, o.counterfactual_pnl_usd FROM "
            " paper_counterfactual_variant_outcomes o JOIN "
            " paper_counterfactual_variants v USING (variant_id) "
            " WHERE v.account_id=$1", acct)
        pol = [r for r in out if r["variant"] == "POLICY_SIZE"][0]
        assert pol["outcome"] == "WON"
        assert float(pol["counterfactual_pnl_usd"]) > 0
        for sql in ("UPDATE paper_counterfactual_variants SET qty=0",
                    "DELETE FROM paper_counterfactual_variant_outcomes"):
            with pytest.raises(Exception):
                async with conn.transaction():
                    await conn.execute(sql)
    finally:
        await PB._done(conn, tr)


# ── 23 maker / taker / size / exit variants ──────────────────────────

def test_item23_every_variant_is_valued_on_the_same_economics():
    b = bind(qty_in=40, inputs={"spread": 0.04})
    vs = {v["variant"]: v for v in b["variants"]}
    assert set(vs) == set(B.VARIANTS)
    assert vs["AS_BOUND"]["qty"] == b["qty"]
    assert vs["POLICY_SIZE"]["qty"] == 40 and vs["HALF_SIZE"]["qty"] == 20
    assert vs["MAKER"]["style"] == B.MAKER
    assert vs["MAKER"]["vwap"] == pytest.approx(0.39)
    assert vs["MAKER"]["fill_probability"] == B.FILL_PRIOR[B.MAKER]
    assert vs["EARLY_EXIT"]["expected_ev_usd"] < \
        vs["HOLD_TO_SETTLEMENT"]["expected_ev_usd"]
    refused = bind(_ev(p=0.41))
    rv = {v["variant"]: v for v in refused["variants"]}
    assert rv["AS_BOUND"]["qty"] == 0 and rv["POLICY_SIZE"]["qty"] == 100


# ── 24 subsystem profit attribution ──────────────────────────────────

def test_item24_attribution_terms_sum_to_the_expected_ev():
    ex = B.fit_execution([{"strategy": CG, "style": B.TAKER, "qty": 10,
                           "markout_per_contract": 0.02}
                          for _ in range(30)], [])
    b = bind(_ev(fills=((0.40, 10.0), (0.42, 30.0))), qty_in=40,
             execution=ex,
             inputs={"held_mid": 0.39, "spread": 0.02,
                     "settlement": _priced(0.58), "evaluated_at": NOW,
                     "p_observed_at": NOW - 6})
    at = b["attribution"]
    assert at["sum_usd"] == pytest.approx(b["all_in"]["ev_given_fill_usd"],
                                          abs=1e-6)
    t = at["terms"]
    for k in ("probability_edge_usd", "calibration_adjustment_usd",
              "settlement_difference_usd", "spread_usd", "slippage_usd",
              "fees_usd", "adverse_selection_usd", "freshness_usd",
              "management_usd"):
        assert k in t, k
    assert t["probability_edge_usd"] > 0 and t["fees_usd"] < 0
    assert t["spread_usd"] == pytest.approx(-b["all_in"]["filled_qty"] * 0.01)
    tot = S.attribution_totals([at, at], realized_pnl_usd=1.0,
                               expected_on_closed_usd=3.0)
    assert tot["expected_total_usd"] == pytest.approx(2 * at["sum_usd"],
                                                      abs=1e-5)
    assert tot["outcome_residual_usd"] == -2.0


# ── 25 expected-vs-realized residual learning ────────────────────────

def test_item25_negative_residuals_become_a_binding_haircut():
    res = B.fit_residuals([{"strategy": CG, "sport": "baseball",
                            "family": B.MONEYLINE, "expected_ev_usd": 1.0,
                            "realized_pnl_usd": 0.0, "qty": 10,
                            "source": "PAPER"} for _ in range(40)])
    base = bind(qty_in=40)
    cut = bind(qty_in=40, residuals=res)
    assert cut["residual"]["haircut_per_contract"] > 0.05
    assert cut["ev_per_contract"] < base["ev_per_contract"] - 0.05
    good = B.fit_residuals([{"strategy": CG, "sport": "baseball",
                             "family": B.MONEYLINE, "expected_ev_usd": 1.0,
                             "realized_pnl_usd": 9.0, "qty": 10,
                             "source": "PAPER"} for _ in range(40)])
    assert bind(qty_in=40, residuals=good)["ev_per_contract"] == \
        base["ev_per_contract"]


# ── 26 probabilistic P&L forecast ────────────────────────────────────

def test_item26_forecast_percentiles_are_ordered_and_deterministic():
    ops = [{"qty": 10, "cost_basis_usd": 4.0, "p": 0.6} for _ in range(30)]
    fw = [{"qty": 10, "cost_usd": 4.0, "fees_usd": 0.1, "p": 0.6,
           "fill_probability": 0.5} for _ in range(10)]
    f = S.pnl_forecast(ops, fw)
    assert f["p5_usd"] < f["p50_usd"] < f["p95_usd"]
    assert f == S.pnl_forecast(ops, fw)                # same seed, same
    assert f["expected_usd"] == pytest.approx(
        30 * (6.0 - 4.0) + 10 * 0.5 * (6.0 - 4.1))
    assert abs(f["mean_usd"] - f["expected_usd"]) < 5.0
    certain = S.pnl_forecast([{"qty": 10, "cost_basis_usd": 4.0, "p": 1.0}])
    assert certain["p5_usd"] == certain["p95_usd"] == 6.0
    assert S.pnl_forecast([])["p50_usd"] == 0.0


# ── S the forward scoreboard ─────────────────────────────────────────

def test_scoreboard_insufficient_forward_sample_is_deferred_with_size():
    sb = S.scoreboard({"closed_pnls_since_cutover": [1.0, -0.5, 2.0],
                       "independent_trades": 3,
                       "independent_settled_trades": 3,
                       "expected_ev_usd": 1.2, "cutover": NOW - 86400,
                       "historical_realized_pnl_usd": -5000.0})
    assert sb["verdict"] == S.DEFERRED
    assert sb["required_sample_size"] >= S.MIN_FORWARD_SAMPLE
    assert str(sb["required_sample_size"]) in sb["why"]
    assert sb["realized_pnl_since_cutover_usd"] == 2.5
    assert sb["historical_realized_pnl_usd"] == -5000.0
    assert sb["max_drawdown_usd"] == 0.5
    assert sb["promotes_nothing"] is True
    for k in ("sample", "expected_after_cost_ev_usd", "ev_per_capital_hour",
              "calibration", "expected_vs_realized_residual_usd",
              "turnover", "capital_deployed_usd", "forecast"):
        assert k in sb, k
    f = sb["forecast"]
    assert f["p5_usd"] <= f["p50_usd"] <= f["p95_usd"]


def test_scoreboard_classifies_a_sufficient_sample():
    pos = S.scoreboard({"closed_pnls_since_cutover": [1.0, 2.0] * 100,
                        "independent_settled_trades": 200})
    assert pos["verdict"] == S.FORWARD_POSITIVE
    neg = S.scoreboard({"closed_pnls_since_cutover": [-1.0, -2.0] * 100,
                        "independent_settled_trades": 200})
    assert neg["verdict"] == S.FORWARD_NEGATIVE
    assert S.required_sample([0.1, -0.1] * 10) == S.MIN_FORWARD_SAMPLE
    assert S.required_sample([1.0, 3.0] * 10) == S.MIN_FORWARD_SAMPLE
    noisy = [5.0, -4.0] * 20
    assert S.required_sample(noisy) > S.MIN_FORWARD_SAMPLE
    cm = S.calibration_metrics([{"p": 1.0, "y": 1.0}, {"p": 0.0, "y": 0.0}])
    assert cm["brier"] == 0.0 and cm["ece"] == 0.0


@pg
async def test_scoreboard_read_counts_the_forward_sample_since_cutover():
    conn, tr = await PB._tx()
    try:
        a = await PB.ready(conn, "pssb")
        acct = a["account_id"]
        o = PB._order(a, key="b1", qty=10,
                      levels=[{"price": 0.40, "qty": 1000}])
        await PB.premap(conn, o["us_market_slug"], game_start=NOW + 3 * HOUR)
        assert (await L.submit_order(conn, o, fee_fn=FEE, now=NOW))["ok"]
        o2 = PB._order(a, key="b2", p=0.45)
        await PB.premap(conn, o2["us_market_slug"],
                        game_start=NOW + 3 * HOUR)
        await L.submit_order(conn, o2, fee_fn=FEE, now=NOW + 1)
        sb = await S.scoreboard_read(conn, account_id=acct, now=NOW + 60,
                                     cutover=NOW - 10)
        assert sb["sample"]["independent_opportunities"] == 2
        assert sb["sample"]["independent_trades"] == 1
        assert sb["expected_after_cost_ev_usd"] > 0
        assert sb["verdict"] == S.DEFERRED
        assert sb["required_sample_size"] >= S.MIN_FORWARD_SAMPLE
        assert sb["forecast"]["forward_entries"] == 1
        assert "POLICY_SIZE|CASH" in sb["counterfactuals"]
        # before the cutover: nothing forward
        early = await S.scoreboard_read(conn, account_id=acct, now=NOW + 60,
                                        cutover=NOW + 30)
        assert early["sample"]["independent_trades"] == 0
    finally:
        await PB._done(conn, tr)


def test_scoreboard_route_is_get_only_registered_and_read_only():
    from fastapi.testclient import TestClient

    from sportsassets.api import app as APP
    from sportsassets.api import command_profitability_scoreboard as R
    assert R.PATH == "/api/command/paper/profitability-scoreboard"
    paths, stack = {}, list(APP.app.routes)
    while stack:
        r = stack.pop()
        if hasattr(r, "original_router"):
            stack.extend(r.original_router.routes)
        elif getattr(r, "path", "") == R.PATH:
            paths[r.path] = set(getattr(r, "methods", set()) or set())
    assert paths and all(m <= {"GET", "HEAD"} for m in paths.values())
    client = TestClient(APP.app, raise_server_exceptions=False)
    assert client.get(R.PATH).status_code == 401
    for verb in (client.post, client.put, client.delete, client.patch):
        assert verb(R.PATH).status_code in (401, 405)
    write = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[a-z_]+\s+SET|DELETE\s+"
                       r"FROM|TRUNCATE|ALTER\s+TABLE|DROP\s+TABLE)", re.I)
    for rel in ("api/command_profitability_scoreboard.py",):
        src = (PKG / rel).read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not write.search(node.value), node.value[:60]
        assert "readonly=True" in src and "submit_order" not in src
    ping = (ROOT.parent / ".github" / "workflows" / "ping.yml").read_text()
    assert ("get profitability_scoreboard "
            "/api/command/paper/profitability-scoreboard") in ping


async def test_an_unreadable_scoreboard_is_unavailable_never_zero(
        monkeypatch):
    from sportsassets.api import command_profitability_scoreboard as R

    async def pool():
        raise RuntimeError("db down")
    monkeypatch.setattr(R, "_pool", pool)
    R._CACHE.clear()
    got = await R.paper_profitability_scoreboard()
    assert got["status"] == "UNAVAILABLE" and got["data"] is None


@pg
async def test_the_scoreboard_route_reads_ok_in_a_read_only_transaction():
    from sportsassets.api import command_profitability_scoreboard as R
    conn = await H.connect()
    try:
        got = await R.read(conn, account_id="paper_t_none", now=NOW,
                           cutover=NOW - 60)
        assert got["status"] == "OK", got
        assert got["data"]["verdict"] == S.DEFERRED
    finally:
        await conn.close()


# ── bookkeeping ──────────────────────────────────────────────────────

def test_new_refusals_are_classified_and_nothing_raises_authority():
    from sportsassets import refusal_taxonomy_table as TT
    from sportsassets.agents import paper_xavier as PX
    for c in (B.R_SCENARIO_CONCENTRATION, B.R_FRESHNESS_BEYOND_BOUND,
              B.R_CHURN_REPRICE_DEADBAND, PX.B_BELOW_MIN_IMPROVEMENT):
        assert c in TT.TABLE, c
    assert set((B.R_SCENARIO_CONCENTRATION, B.R_FRESHNESS_BEYOND_BOUND,
                B.R_CHURN_REPRICE_DEADBAND)) <= set(B.REFUSALS)
    assert not set(B.REFUSALS) & set(CA.NO_CAPITAL_AUTHORITY)
    # only shrinks / refuses: every constant the stack adds lowers size
    assert 0 < B.FRESHNESS_MAX_HAIRCUT and 0 <= B.EXIT_RATE_PRIOR <= 1
    assert B.SCENARIO_MAX_EXPOSURE_USD is None      # no invented cap
    src = (PKG / "bettor_paper_profitability_stack.py").read_text()
    for word in ("per_order_cap_usd", "per_market_cap_usd",
                 "INSERT INTO paper_orders", "INSERT INTO paper_ledger",
                 "submit_order", "UPDATE ", "DELETE FROM"):
        assert word not in src, word
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            m = node.module or ""
            assert not any(k in m for k in (
                "funded", "execution_gate", "entry_execution", "pmus",
                "kalshi", "live_executor", "execmirror", "smalllive")), m


def test_the_stack_steps_run_in_the_paper_pass():
    from sportsassets.agents import paper_runtime as PR
    names = [n for n, _ in PR.default_steps()]
    assert names.index("profitability_fit") < \
        names.index("profitability_quarantine") < names.index("derek")
    assert "counterfactual_settlement" in names


def test_migration_311_has_its_rollback_and_is_tracked():
    from sportsassets.api import command_release as R
    mig = ROOT / "migrations" / "311_paper_profitability_stack.sql"
    assert mig.is_file()
    assert (ROOT / "migrations" / "rollback" /
            "311_paper_profitability_stack.down.sql").is_file()
    # tracked: inside the release range (311 and every later migration)
    assert R.TRACKED_FROM <= 311 <= R.TRACKED_TO
    assert "MANAGEMENT" in mig.read_text()


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_profitability_stack_binding.py" in listed.splitlines()
