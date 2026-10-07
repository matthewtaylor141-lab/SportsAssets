"""COMPLETION READINESS LOGIC, BOUND (completion readiness, 2026-10-07).

  §1  the backend copies of probability_authority / ev_authority /
      readiness_gate are the completion package's own, byte for byte, and
      the package's own tests pass against them
  §2  probability: the market prior is the incumbent; BETTOR deviates only on
      positive out-of-sample residual evidence; with no residual model in
      production the best authority is MARKET_PRIOR_ONLY; nothing is fitted
  §3  executable EV: lower-bound net executable EV <= 0 is CASH; a fee the
      schedule refuses excludes the decision, never prices it at zero; a
      residual authority with unmeasured uncertainty prices nothing
  §4  the repaired twin: IOC evaluated on the first readable book at/after
      eligibility, whole-cent levels, consumption once; the optimistic
      mismatch classes named; certification only on a FRESH window of at
      least TWIN_MIN_FRESH_ORDERS
  §5  the readiness gate is PAPER_SHADOW_ONLY until every input is green
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
from pathlib import Path

import pytest

from sportsassets.completion import ev_authority as EVA
from sportsassets.completion import evidence as EV
from sportsassets.completion import probability_authority as PA
from sportsassets.completion import readiness_gate as RG
from sportsassets.completion import twin as TW

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "research" / "completion_readiness" / "completion_logic"
BACK = ROOT / "backend" / "sportsassets" / "completion"


# ── §1 byte-identical copies ─────────────────────────────────────────

@pytest.mark.parametrize("name", ["probability_authority.py",
                                  "ev_authority.py", "readiness_gate.py"])
def test_the_backend_copy_is_the_package_module_byte_for_byte(name):
    a = hashlib.sha256((PKG / name).read_bytes()).hexdigest()
    b = hashlib.sha256((BACK / name).read_bytes()).hexdigest()
    assert a == b, name


def test_the_package_tests_pass_against_the_backend_copies(monkeypatch):
    import sys
    import types
    pkg = types.ModuleType("completion_logic")
    pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "completion_logic", pkg)
    for n, m in (("probability_authority", PA), ("ev_authority", EVA),
                 ("readiness_gate", RG)):
        monkeypatch.setitem(sys.modules, "completion_logic." + n, m)
        setattr(pkg, n, m)
    spec = importlib.util.spec_from_file_location(
        "_pkg_completion_tests",
        ROOT / "research" / "completion_readiness" / "tests" /
        "test_completion_logic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ran = 0
    for k in dir(mod):
        if k.startswith("test_") and callable(getattr(mod, k)):
            getattr(mod, k)()
            ran += 1
    assert ran >= 5


# ── §2 probability ───────────────────────────────────────────────────

def _prob_rows(n_events=120, bettor_better=False):
    rows = []
    for i in range(n_events):
        y = 1.0 if i % 2 == 0 else 0.0
        pm = 0.6 if y == 1.0 else 0.4          # an informative market
        p = (0.7 if y == 1.0 else 0.3) if bettor_better else 0.5
        rows.append({"bid": pm - 0.01, "ask": pm + 0.01, "side": "LONG",
                     "y_long": y, "p_used": p, "event_slug": "ev-%d" % i,
                     "slug": "m-%d" % i})
    return rows


def test_a_bettor_worse_than_the_market_gets_market_prior_only():
    out = EV.probability_block(_prob_rows())
    assert out["authority"] == "MARKET_PRIOR_ONLY"
    assert out["reason"] in ("CALIBRATION_NOT_PROVEN",
                             "BETTOR_DOES_NOT_BEAT_MARKET_PRIOR_OOS")
    assert out["bettor_logloss_minus_market"] > 0
    assert out["fitted_parameters"] == 0 and out["holdout_touched"] is False


def test_even_a_better_bettor_has_no_residual_authority_in_production():
    out = EV.probability_block(_prob_rows(bettor_better=True))
    assert out["bettor_logloss_minus_market"] < 0
    assert out["improvement_lower_bound"] > 0
    # no residual model exists in production: the incumbent stays
    assert out["authority"] == "MARKET_PRIOR_ONLY"
    assert out["residual_model"] == "NONE_IN_PRODUCTION"


def test_too_few_events_or_no_rows():
    assert EV.probability_block(_prob_rows(20))["reason"] == \
        "INSUFFICIENT_INDEPENDENT_EVENTS"
    assert EV.probability_block([])["authority"] == "ABSTAIN"


def test_short_side_orientation():
    r = {"bid": 0.40, "ask": 0.42, "side": "SHORT"}
    assert EV.orient(r["bid"], r["ask"], "SHORT") == pytest.approx((0.58, 0.60))
    assert EV.orient(0.5, 0.5, "LONG") is None


def test_cluster_bounds_are_by_event():
    b = EV.cluster_mean_bounds([1.0, 1.0, -1.0, -1.0], ["a", "a", "b", "b"])
    assert b["clusters"] == 2 and b["mean"] == 0.0 and b["se"] > 0
    assert EV.cluster_mean_bounds([1.0], ["a"])["lb"] is None


# ── §3 executable EV ─────────────────────────────────────────────────

def test_market_prior_pricing_is_cash_and_fees_are_never_zeroed():
    rows = [{"bid": 0.48, "ask": 0.52, "side": "LONG", "at": 0.0,
             "event_slug": "e%d" % i} for i in range(10)]
    out = EV.ev_block(rows, authority="MARKET_PRIOR_ONLY",
                      fee_fn=lambda p, at: 0.01)
    assert out["verdict"] == "CASH"
    assert out["mean_net_ev_per_contract"] == pytest.approx(-0.03)
    assert out["authority_granted"] is False
    none = EV.ev_block(rows, authority="MARKET_PRIOR_ONLY",
                       fee_fn=lambda p, at: None)
    assert none["decisions_priced"] == 0
    assert none["dropped"] == {"FEE_UNMEASURED": 10}
    assert none["verdict"] == "CASH"
    res = EV.ev_block(rows, authority="BETTOR_RESIDUAL_ALLOWED",
                      fee_fn=lambda p, at: 0.0)
    assert res["dropped"] == {"RESIDUAL_UNCERTAINTY_UNMEASURED": 10}
    assert EV.ev_block(rows, authority="ABSTAIN",
                       fee_fn=lambda p, at: 0.0)["verdict"] == "CASH"


def test_the_ev_authority_rule_itself():
    d = EVA.evaluate_all_in_ev(EVA.ExecutableEconomics(
        p_used=0.6, executable_price=0.5, fee_per_contract=0.02,
        probability_uncertainty_per_contract=0.05,
        execution_uncertainty_per_contract=0.04))
    assert d.verdict == "CASH" and d.lower_bound_ev_per_contract < 0
    d2 = EVA.evaluate_all_in_ev(EVA.ExecutableEconomics(
        p_used=0.6, executable_price=0.5))
    assert d2.verdict == "ELIGIBLE_FOR_EXISTING_GATED_PATH"


# ── §4 the repaired twin ─────────────────────────────────────────────

def _order(**kw):
    o = {"order_id": "o1", "tif": "IOC", "direction": "BUY", "side": "LONG",
         "slug": "m", "qty": 10.0, "limit": 0.50, "decided": 100.0,
         "eligible": 102.0, "expires": 190.0, "fills": [],
         "books": []}
    o.update(kw)
    return o


def _bk(at, bids=((0.45, 100),), asks=((0.50, 100),)):
    return {"at": at, "bids": [{"px": p, "qty": q} for p, q in bids],
            "asks": [{"px": p, "qty": q} for p, q in asks]}


def test_a_stale_pre_activation_quote_is_not_executable():
    o = _order(books=[_bk(99.0), _bk(103.0, asks=((0.55, 100),))])
    r = TW.replay_marketable(o)
    assert r["state"] == "EXPIRED" and r["qty"] == 0.0
    assert r["book_at"] == 103.0
    assert TW.diagnose_optimistic(o) == TW.M_STALE
    assert TW.classify(o, r) is None            # PAPER expired too


def test_no_book_in_the_window_expires_and_the_first_eligible_book_fills():
    assert TW.replay_marketable(_order(books=[_bk(99.0)]))["why"] == \
        TW.M_NO_BOOK
    o = _order(books=[_bk(99.0, asks=((0.60, 100),)), _bk(104.0)],
               fills=[{"qty": 10.0, "price": 0.5}])
    r = TW.replay_marketable(o)
    assert r["state"] == "FILLED" and r["vwap"] == pytest.approx(0.50)
    assert TW.diagnose_optimistic(o) == TW.M_EARLY_CANCEL
    assert TW.classify(o, r) is None


def test_off_cent_levels_and_short_side_cost():
    o = _order(side="SHORT", limit=0.67, qty=5.0,
               books=[_bk(103.0, bids=((0.335, 50), (0.33, 50)))])
    r = TW.replay_marketable(o)
    assert r["vwap"] == pytest.approx(0.67)     # 0.335 is off the cent grid
    name, lv = TW.ladder(o["books"][0], direction="BUY", side="SHORT")
    assert name == "bids" and [x[2] for x in lv] == [0.33]


def test_liquidity_is_consumed_once_across_orders():
    b = _bk(103.0, asks=((0.50, 10),))
    led: dict = {}
    r1 = TW.replay_marketable(_order(order_id="a", books=[b]), led)
    r2 = TW.replay_marketable(_order(order_id="b", books=[b]), led)
    assert r1["qty"] == 10.0 and r2["qty"] == 0.0
    fok = TW.replay_marketable(_order(tif="FOK", qty=20.0,
                                      books=[_bk(103.0)]), {})
    assert fok["qty"] == 20.0
    fok2 = TW.replay_marketable(_order(tif="FOK", qty=200.0,
                                       books=[_bk(103.0)]), {})
    assert fok2["qty"] == 0.0


def test_certification_needs_a_fresh_window_of_enough_orders():
    fresh_t = EV.TWIN_DIAGNOSIS_WINDOW_END + 10
    agree = [_order(order_id="o%d" % i, slug="m%d" % i, eligible=fresh_t,
                    expires=fresh_t + 90, books=[_bk(fresh_t + 1)],
                    fills=[{"qty": 10.0}])
             for i in range(EV.TWIN_MIN_FRESH_ORDERS)]
    stale = [_order(order_id="s%d" % i) for i in range(50)]
    few = EV.twin_block(agree[:10] + stale)
    assert few["status"] == "ACCUMULATING" and few["certified"] is False
    assert few["replayed"] == 10                # the stale ones never count
    ok = EV.twin_block(agree)
    assert ok["status"] == "CERTIFIED" and ok["fill_agreement_rate"] == 1.0
    assert ok["twin_pnl_reported"] is False
    bad = EV.twin_block([dict(o, fills=[]) for o in agree])
    assert bad["status"] == "BELOW_TARGET" and bad["certified"] is False
    assert bad["residual_mismatch_taxonomy"]


# ── §5 the readiness gate ────────────────────────────────────────────

def _green(**kw):
    base = dict(no_oom_minutes=90, worker_rss_highwater_fraction=0.6,
                priority_freshness_rate=0.97, management_freshness_rate=0.98,
                software_red_count=0, xavier_packet_complete_rate=0.99,
                canary_pass=True, probability_edge_lb=0.01,
                forward_independent_events=150,
                digital_twin_fill_agreement=0.97, settlement_proven=True,
                capacity_proven=True, mirror_venue_confirmed=True,
                small_live_shadow=True, historical_paper_immutable=True)
    base.update(kw)
    return RG.ReadinessEvidence(**base)


def test_the_gate_is_paper_shadow_only_until_every_input_is_green():
    assert RG.evaluate_readiness(_green()).status == "CAPITAL_CANDIDATE"
    for k, v in (("probability_edge_lb", None), ("canary_pass", False),
                 ("software_red_count", 1), ("no_oom_minutes", 59.0),
                 ("worker_rss_highwater_fraction", 0.85),
                 ("mirror_venue_confirmed", False)):
        d = RG.evaluate_readiness(_green(**{k: v}))
        assert d.status == "PAPER_SHADOW_ONLY" and d.blockers, k


def test_numbers_are_finite():
    assert math.isfinite(EV._ll(0.0, 1.0)) and math.isfinite(EV._ll(1.0, 0.0))
