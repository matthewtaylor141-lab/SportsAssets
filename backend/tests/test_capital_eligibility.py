"""CAPITAL-ELIGIBLE ENTER ON THE PAPER PATH (bettor_capital_eligibility).

  §1 DEPTH: no executable depth => no capital-eligible ENTER; partial depth
     caps the size; depth outside the limit is not executable.
  §2 IDENTITY AND SETTLEMENT: unresolved identity or terms => refused.
  §3 TOTAL EXECUTABLE EV: fees, adverse selection (limit bound or measured),
     incentives only with recorded terms; EV <= 0 => CASH_WAIT with $0.
  §4 SIZE ONLY SHRINKS: the lifecycle factor never raises the size.
  §5 PARENT / CHILD PLAN: children bounded by visible depth; PAPER only.
  §6 VENUES: Polymarket-only while the Kalshi read path is BLOCKED; never
     compared without established payoff equivalence.
  §7 AUTHORITY: the module imports no venue / order / funded module.
  §8 THE PAPER PATH: Derek's and the benchmark's decide_one consult the gate
     before an ENTER is recorded.
ALL DATA HERE IS SYNTHETIC TEST DATA.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from sportsassets import bettor_capital_eligibility as CE

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "sportsassets"

IDENT = {"us_market_slug": "test-mkt", "payout_event": "HOME_WINS",
         "fixture": "fx-1", "holding_side": "LONG"}
OK_SETTLE = {"compatibility": "COMPATIBLE"}


def zero_fee(q, px):
    return 0.0


def flat_fee(per):
    return lambda q, px: (q * per, "FLAT")


def ev(**kw):
    base = dict(p=0.60, levels=[{"price": 0.50, "qty": 100}], qty=100,
                limit=0.50, fee_fn=zero_fee, settlement=OK_SETTLE,
                identity=IDENT)
    base.update(kw)
    return CE.evaluate(**base)


# ── §1 depth ─────────────────────────────────────────────────────────

def test_no_executable_depth_is_never_capital_eligible():
    for levels in ([], [{"price": 0.55, "qty": 100}],
                   [{"price": 0.50, "qty": 0}], None):
        got = ev(levels=levels)
        assert got["capital_eligible"] is False
        assert CE.R_CE_NO_EXECUTABLE_DEPTH in got["refusals"]
        assert got["allocation_usd"] == 0.0 and got["qty"] == 0
        assert got["decision"] == CE.CASH_WAIT


def test_partial_depth_caps_the_size_to_the_executable_depth():
    got = ev(levels=[{"price": 0.48, "qty": 30}, {"price": 0.50, "qty": 7.6},
                     {"price": 0.52, "qty": 1000}], qty=100)
    assert got["capital_eligible"] is True
    assert got["qty"] == 37 and got["depth_capped"] is True
    assert got["executable_depth"] == pytest.approx(37.6)
    assert got["filled_qty"] == 37


# ── §2 identity and settlement ───────────────────────────────────────

@pytest.mark.parametrize("st", [None, {}, {"compatibility": "UNKNOWN"},
                                {"compatibility": "INCOMPATIBLE"},
                                {"compatibility": None}])
def test_unresolved_settlement_terms_refuse(st):
    got = ev(settlement=st)
    assert not got["capital_eligible"]
    assert CE.R_CE_SETTLEMENT_UNRESOLVED in got["refusals"]


@pytest.mark.parametrize("drop", sorted(IDENT))
def test_unresolved_identity_refuses(drop):
    got = ev(identity=dict(IDENT, **{drop: None}))
    assert not got["capital_eligible"]
    assert CE.R_CE_IDENTITY_UNRESOLVED in got["refusals"]


# ── §3 total executable EV ───────────────────────────────────────────

def test_non_positive_executable_ev_allocates_zero_and_cash_wait_wins():
    got = ev(p=0.50)                         # zero edge, zero fees
    assert got["decision"] == CE.CASH_WAIT
    assert got["refusals"] == [CE.R_CE_CASH_WAIT_EV_NOT_POSITIVE]
    assert got["allocation_usd"] == 0.0 and got["qty"] == 0
    assert got["total_executable_ev_usd"] == pytest.approx(0.0)
    # fees that consume a positive gross edge
    got = ev(p=0.52, fee_fn=flat_fee(0.03))
    assert got["decision"] == CE.CASH_WAIT
    assert got["fees_usd"] == pytest.approx(3.0)


def test_adverse_selection_is_the_limit_bound_unless_measured():
    levels = [{"price": 0.40, "qty": 50}, {"price": 0.50, "qty": 50}]
    got = ev(p=0.60, levels=levels, qty=100, limit=0.50)
    # vwap 0.45; bound = (0.50 - 0.45) x 100 = 5.0
    assert got["adverse_selection_basis"] == CE.AS_BASIS_LIMIT_BOUND
    assert got["adverse_selection_usd"] == pytest.approx(5.0)
    assert got["slippage_vs_top_usd"] == pytest.approx(5.0)
    # 100 x 0.60 - 45 - 0 - 5 = 10
    assert got["total_executable_ev_usd"] == pytest.approx(10.0)
    m = ev(p=0.60, levels=levels, qty=100, limit=0.50,
           adverse_selection_per_contract=0.01)
    assert m["adverse_selection_basis"] == CE.AS_BASIS_MEASURED
    assert m["total_executable_ev_usd"] == pytest.approx(14.0)
    # the bound can turn a positive VWAP edge into CASH_WAIT
    w = ev(p=0.48, levels=levels, qty=100, limit=0.50)
    assert w["decision"] == CE.CASH_WAIT


def test_incentives_count_only_when_their_terms_are_recorded():
    base = ev(p=0.50)
    assert base["decision"] == CE.CASH_WAIT
    unrecorded = ev(p=0.50, incentives=[
        {"kind": "REBATE", "usd_per_contract": 0.01},
        {"kind": "REWARD", "usd_per_contract": 0.01, "terms_recorded": True},
        {"kind": "REWARD", "usd_per_contract": 0.01, "terms_ref": "x"}])
    assert unrecorded["incentives_credited_usd"] == 0.0
    assert len(unrecorded["incentives_not_credited"]) == 3
    assert unrecorded["decision"] == CE.CASH_WAIT
    rec = ev(p=0.50, incentives=[{"kind": "REBATE", "usd_per_contract": 0.01,
                                  "terms_recorded": True,
                                  "terms_ref": "manifest:abc"}])
    assert rec["incentives_credited_usd"] == pytest.approx(1.0)
    assert rec["decision"] == CE.ENTER


def test_fees_are_required_not_defaulted():
    got = ev(fee_fn=None)
    assert CE.R_CE_FEES_NOT_IDENTIFIED in got["refusals"]
    got = ev(fee_fn=lambda q, px: (None, "x"))
    assert got["refusals"] == [CE.R_CE_FEES_NOT_IDENTIFIED]


def test_dollars_per_capital_hour_travels_with_the_dollars():
    got = ev(expected_hold_hours=2.0)
    assert got["total_executable_ev_usd"] == pytest.approx(10.0)
    assert got["ev_per_capital_hour"] == pytest.approx(10.0 / (50.0 * 2.0))
    assert ev()["ev_per_capital_hour"] is None
    assert ev()["ev_per_capital_hour_status"] == \
        "UNAVAILABLE_NO_HOLD_ESTIMATE"


def test_an_exception_is_a_refusal_never_a_raise():
    def boom(q, px):
        raise RuntimeError("x")
    got = ev(fee_fn=boom)
    assert got["capital_eligible"] is False
    assert got["refusals"] == [CE.R_CE_GATE_ERROR]


# ── §4 size only shrinks ─────────────────────────────────────────────

@pytest.mark.parametrize("factor", [0.0, 0.25, 0.5, 1.0, 2.0, 100.0, -1.0,
                                    None])
def test_the_size_factor_never_raises_the_size(factor):
    got = ev(size_factor=factor)
    assert got["qty"] <= 100
    if factor == 0.5:
        assert got["qty"] == 50


# ── §5 the parent / child plan ───────────────────────────────────────

def test_children_are_bounded_by_visible_depth_and_paper_only():
    levels = [{"price": 0.50, "qty": 12.7}, {"price": 0.51, "qty": 5},
              {"price": 0.53, "qty": 400}, {"price": 0.40, "qty": 3}]
    plan = CE.child_order_plan(levels, parent_qty=1000, limit=0.51)
    assert plan["paper_simulation_only"] is True
    assert [c["qty"] for c in plan["children"]] == [3, 12, 5]
    for c in plan["children"]:
        assert c["qty"] <= c["visible_qty_at_level"]
        assert c["limit_price"] <= 0.51
    assert plan["planned_qty"] == 20 and plan["unplanned_qty"] == 980
    small = CE.child_order_plan(levels, parent_qty=4, limit=0.53)
    assert small["planned_qty"] == 4
    capped = CE.child_order_plan(levels, parent_qty=1000, limit=0.53,
                                 max_children=1)
    assert len(capped["children"]) == 1


# ── §6 venues ────────────────────────────────────────────────────────

def test_kalshi_read_path_is_blocked_by_name_and_routing_is_polymarket():
    st = CE.kalshi_read_path_status(env={})
    assert st["status"] == "BLOCKED" and st["routing"] == "POLYMARKET_US_ONLY"
    assert st["module"].endswith("KalshiClient.orderbook")
    assert any("KALSHI_ENV_UNSET" in b for b in st["blockers"])
    assert any("NO_PRODUCTION_CALLER" in b for b in st["blockers"])
    st2 = CE.kalshi_read_path_status(env={"KALSHI_ENV": "prod"})
    assert st2["status"] == "BLOCKED"
    # even an ESTABLISHED, cheaper Kalshi quote is not used
    r = CE.route_venue(polymarket={"all_in_cost_per_contract": 0.55},
                       kalshi={"all_in_cost_per_contract": 0.40},
                       mapping_verdict="ESTABLISHED")
    assert r["venue"] == CE.POLYMARKET_US
    assert CE.R_CE_KALSHI_READ_PATH_BLOCKED in r["kalshi_refusals"]


def test_no_cross_venue_comparison_without_payoff_equivalence():
    avail = {"status": "AVAILABLE"}
    r = CE.route_venue(polymarket={"all_in_cost_per_contract": 0.55},
                       kalshi={"all_in_cost_per_contract": 0.40},
                       mapping_verdict="NOT_ESTABLISHED", read_path=avail)
    assert r["venue"] == CE.POLYMARKET_US
    assert CE.R_CE_VENUE_NOT_EQUIVALENT in r["kalshi_refusals"]
    # the rule itself: equivalent payoffs AND a working read path AND cheaper
    r = CE.route_venue(polymarket={"all_in_cost_per_contract": 0.55},
                       kalshi={"all_in_cost_per_contract": 0.40},
                       mapping_verdict="ESTABLISHED", read_path=avail)
    assert r["venue"] == CE.KALSHI
    r = CE.route_venue(polymarket={"all_in_cost_per_contract": 0.40},
                       kalshi={"all_in_cost_per_contract": 0.40},
                       mapping_verdict="ESTABLISHED", read_path=avail)
    assert r["venue"] == CE.POLYMARKET_US


def test_no_production_module_calls_the_kalshi_orderbook_yet():
    """The blocker is real: if a production caller appears, this test fails
    and kalshi_read_path_status must be revisited."""
    callers = []
    for path in PKG.rglob("*.py"):
        if path.name == "kalshi_venue.py":
            continue
        if ".orderbook(" in path.read_text(encoding="utf-8"):
            callers.append(path.relative_to(PKG).as_posix())
    assert callers == [], callers


# ── §7 authority ─────────────────────────────────────────────────────

FORBIDDEN = ("venue", "kalshi", "execmirror", "live_executor", "funded",
             "smalllive", "submit", "live_parity", "pmus")


def _imports(path):
    out = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            out.add(node.module or "")
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_the_gate_imports_no_venue_order_or_funded_module():
    for imp in _imports(PKG / "bettor_capital_eligibility.py"):
        leaf = imp.rsplit(".", 1)[-1].lower()
        assert not any(f in leaf for f in FORBIDDEN), imp


def test_this_proof_is_registered_capital_critical():
    listed = (ROOT / "tools" / "capital_critical_tests.txt").read_text()
    assert "tests/test_capital_eligibility.py" in listed.splitlines()


# ── §8 the paper path consults the gate ──────────────────────────────

def test_derek_and_the_benchmark_consult_the_gate_before_recording():
    for rel in ("agents/paper_derek.py", "agents/paper_benchmark.py"):
        src = (PKG / rel).read_text()
        i_gate = src.index("capital_gate(\n") if rel.endswith("benchmark.py") \
            else src.index("ce = await capital_gate(")
        i_verdict = src.index("verdict = DP.ENTER if not refusals",
                              i_gate - 4000)
        assert i_gate < i_verdict, rel
        assert src.index("INSERT INTO paper_decisions") > i_verdict
