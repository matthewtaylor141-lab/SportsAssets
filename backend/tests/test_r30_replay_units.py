"""THE REPLAY'S PURE RULES (no database), one per red-team finding.

  §1 QUALIFIED = LEGALLY ALLOCATABLE. A hard-rule refusal (stale probability,
     entries switched off, already held, ...) is never a qualified
     alternative; a refusal for capital only is; an opportunity whose
     economics were never recorded is ECONOMICS_UNRECORDED, not
     "not qualified" (a missing net is not a non-positive net).
  §2 THE CAPITAL-HOUR ALLOCATOR is the reference's tranche allocator: the
     tape hurdle and the hedge reserve are passed, an opportunity is cut into
     tranches (book-walk levels, else USD steps), so a top-ranked opportunity
     larger than the idle cash still gets the idle cash, a below-hurdle one
     gets nothing, and deeper book levels lose to better alternatives.
  §3 IDENTICAL HARD RAILS: per-order cap, usable idle after the hedge
     reserve, per-market / per-fixture headroom from point-in-time exposure,
     the concurrent-group slot; the caps effective at the clock (the policy
     recorded on the decision, else the session's); an unmeasured configured
     rail makes the benchmark UNAVAILABLE.
  §4 MANAGEMENT: the action CARRIED OUT includes the adapter outcome (PROTECT
     with no uncommitted inventory placed nothing and still matches); every
     divergent branch is pinned (priced sale, protect-not-priceable,
     protective fills on a divergent path, missing evidence state, truncated
     reviews, a recorded intent that differs).
  §5 CAPITAL HELD TO THE HORIZON: an open position occupies its open cost
     basis until the horizon, so it carries its share of the missed EV.
  §6 CASH vs RAILS: only cash refusals are CASH_UNAVAILABLE_BY_PRIOR_
     ALLOCATION; the per-order / concentration / group rails are their own
     class.
  §7 HYPOTHETICALS are summed apart from realized results.
"""
from __future__ import annotations

import pytest

from sportsassets import canonical_intent as CI
from sportsassets.intel import attribution_v2 as V2
from sportsassets.replay import counterfactuals as CF
from sportsassets.replay import events as EV
from sportsassets.replay import pit as P
from sportsassets.replay import reconstruct as RC

INV = "PINNACLE_COMPLETED_GAME_PAPER"


def _econ(qty, vwap, fees, net):
    return {"acquisition": {"qty": qty, "acquisition_cost_usd": qty * vwap,
                            "vwap": vwap, "fees_usd": fees,
                            "expected_net_profit_usd": net}}


def _dec(verdict="ENTER", refusal=None, econ=None, **kw):
    d = {"decision_id": kw.pop("decision_id", "d"), "verdict": verdict,
         "refusal": refusal, "refusals": [refusal] if refusal else [],
         "p_pinnacle": kw.pop("p", 0.62), "limit_price": 0.56,
         "proposed_qty": 100.0, "economics": econ if econ is not None
         else _econ(100, 0.554, 0.7, 6.0), "strategy": INV}
    d.update(kw)
    return d


# ── §1 qualified = legally allocatable ───────────────────────────────

def test_a_hard_rule_refusal_is_never_a_qualified_alternative():
    q = lambda d: RC.qualify(d, RC.econ_view(d))["state"]   # noqa: E731
    assert q(_dec()) == RC.QUALIFIED
    for code in ("PROBABILITY_EVIDENCE_STALE", "STRATEGY_ENTRIES_DISABLED",
                 "THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT",
                 "SETTLEMENT_NOT_SUPPORTED", "BELOW_MIN_GROSS_EDGE"):
        d = _dec("REFUSE", code)
        assert q(d) == RC.NOT_QUALIFIED, code
        assert RC.qualify(d, RC.econ_view(d))["why"].startswith(
            "HARD_RULE_REFUSAL: %s" % code)
    # a refusal for capital only: another allocation could have funded it
    assert q(_dec("REFUSE", "INSUFFICIENT_AVAILABLE_PAPER_CASH")) == \
        RC.QUALIFIED
    # a measured non-positive net is NOT_QUALIFIED (admissible, measured)
    neg = _dec(econ=_econ(100, 0.62, 0.7, -0.7))
    assert RC.qualify(neg, RC.econ_view(neg)) == {
        "state": RC.NOT_QUALIFIED, "admissible": True,
        "why": "NON_POSITIVE_EXECUTABLE_NET_AFTER_FEES (measured)"}


def test_missing_economics_are_unrecorded_not_a_measured_no():
    # the red-team's direct check: p and d recorded, economics {} -> net None
    ev = RC.econ_view({"p_blended": 0.6, "limit_price": 0.5,
                       "proposed_qty": 10, "economics": {}})
    assert ev["net"] is None and ev["known"] is False
    assert ev["why_unknown"] == "DECISION_TIME_EXECUTABLE_NET_NOT_RECORDED"
    d = _dec(econ={})
    assert RC.qualify(d, RC.econ_view(d))["state"] == RC.UNRECORDED
    # and the benchmarks are UNAVAILABLE, never 0.0
    out = RC.allocation_benchmarks(
        None, d, RC.econ_view(d), RC.qualify(d, RC.econ_view(d)),
        {"status": "MEASURED", "alternatives": [],
         "unrecorded_alternatives": 0}, rails=_rails(), eddie={},
        allie={"status": "UNAVAILABLE", "why": "x"}, hours=2.0,
        hurdle={"value": 0.0}, clock=0.0, scope="s")
    for k in ("EQUAL_ALLOCATION", "ROI_ONLY_RANKING", "CAPITAL_HOUR_RANKING"):
        assert out[k]["usd"] is None
        assert out[k]["why"].startswith(
            "DECISION_TIME_EXECUTABLE_NET_NOT_RECORDED")


# ── §2 the capital-hour tranche allocator ────────────────────────────

class _Ctx:
    params = dict(RC.DEFAULT_PARAMS)

    def hours_to_release(self, row, clock, scope):
        return (2.0, "test")


def _rails(cash=1000.0, available=100.0, **caps):
    c = {k: None for k in RC.CAP_KEYS}
    c["hedge_reserve_fraction"] = 0.0
    c.update(caps)
    return RC.Rails(
        caps=c, source="TEST",
        ledger={"cash_usd": cash, "available_usd": available, "basis": "t"},
        book={"by_market": {}, "by_fixture": {}, "position_groups": set()},
        orders={"by_market": {}, "by_fixture": {}, "groups": set()})


def _ev(capital, net, p=0.6, d=0.5, levels=None):
    qty = (capital / d)
    return {"p": p, "d": d, "qty": qty, "cost": capital, "fees": 0.0,
            "net": net, "capital_required": capital, "known": True,
            "capital_per_contract": capital / qty, "r": (p - d) / d,
            "roi": net / capital, "levels": levels, "positive": net > 0}


def _bench(ev, *, rails, hurdle, alts=()):
    tape = {"status": "MEASURED", "unrecorded_alternatives": 0,
            "alternatives": [{"r": {"decision_id": a, "us_market_slug": a,
                                    "fixture": "f-" + a}, "e": e}
                             for a, e in alts]}
    return RC.allocation_benchmarks(
        _Ctx(), {"decision_id": "me", "us_market_slug": "m-me",
                 "fixture": "f-me"}, ev, {"state": RC.QUALIFIED},
        tape, rails=rails, eddie={}, allie={"status": "UNAVAILABLE",
                                            "why": "t"},
        hours=2.0, hurdle=hurdle, clock=0.0, scope="s")


def test_capital_hour_ranking_is_the_reference_tranche_allocator():
    ev = _ev(500.0, 50.0)                    # ppch 50 / (500 x 2) = 0.05
    # the red-team probe: idle $100 < capital $500. The opportunity ranked
    # first gets the idle cash (it got $0 as one full-size tranche)
    out = _bench(ev, rails=_rails(available=100.0), hurdle={"value": 1e-6})
    assert out["EQUAL_ALLOCATION"]["usd"] == pytest.approx(100.0)
    assert out["ROI_ONLY_RANKING"]["usd"] == pytest.approx(100.0)
    assert out["CAPITAL_HOUR_RANKING"]["usd"] == pytest.approx(100.0)
    assert out["CAPITAL_HOUR_RANKING"]["tranches_chosen"] == 2
    # the tape hurdle IS applied: above the opportunity's ppch -> nothing
    hi = _bench(ev, rails=_rails(available=100.0), hurdle={"value": 0.06})
    assert hi["CAPITAL_HOUR_RANKING"]["usd"] == 0
    assert hi["CAPITAL_HOUR_RANKING"]["rejections"] == [
        "BELOW_MARGINAL_HURDLE"]
    # no hurdle -> UNAVAILABLE, never a hurdle of 0
    na = _bench(ev, rails=_rails(available=100.0),
                hurdle={"value": None, "why": "HURDLE_TAPE_SAMPLE_3_BELOW_10"})
    assert na["CAPITAL_HOUR_RANKING"]["usd"] is None
    assert na["CAPITAL_HOUR_RANKING"]["why"].startswith("HURDLE_UNAVAILABLE")
    # the hedge reserve is the allocator's reserve: cash 1000 x 5% = 50 kept
    rs = _bench(ev, rails=_rails(available=100.0,
                                 hedge_reserve_fraction=0.05),
                hurdle={"value": 1e-6})
    assert rs["CAPITAL_HOUR_RANKING"]["usd"] == pytest.approx(50.0)
    assert rs["EQUAL_ALLOCATION"]["usd"] == pytest.approx(50.0)


def test_deeper_book_levels_lose_to_a_better_alternative():
    # me: level 1 = 100 @ 0.50 (net 10 on $50: ppch 0.1), level 2 =
    # 100 @ 0.58 (net 2 on $58: ppch 0.017); the alternative earns 0.025
    me = _ev(108.0, 12.0, levels=[(100.0, 0.50, 0.0), (100.0, 0.58, 0.0)])
    alt = _ev(100.0, 5.0)
    out = _bench(me, rails=_rails(available=150.0), hurdle={"value": 1e-6},
                 alts=[("alt", alt)])
    # ROI-only ranks by whole-opportunity ROI (me 0.111 > alt 0.05)
    assert out["ROI_ONLY_RANKING"]["usd"] == pytest.approx(108.0)
    # the marginal allocator takes me's first level, then the alternative,
    # and has no room left for me's expensive second level
    assert out["CAPITAL_HOUR_RANKING"]["usd"] == pytest.approx(50.0)
    assert "BOOK_WALK_LEVELS" in out["CAPITAL_HOUR_RANKING"]["basis"]


# ── §3 identical hard rails ──────────────────────────────────────────

def test_every_hard_rail_is_applied_from_point_in_time_inputs():
    caps = {"per_order_cap_usd": 30.0, "per_market_cap_usd": 100.0,
            "per_fixture_cap_usd": 150.0, "hedge_reserve_fraction": 0.1,
            "max_concurrent_groups": 2}
    book = {"by_market": {"m1": 80.0}, "by_fixture": {"f1": 140.0},
            "position_groups": {"g1"}}
    orders = {"by_market": {"m1": 5.0}, "by_fixture": {}, "groups": {"g2"}}
    r = RC.Rails(caps=caps, source="t", ledger={"cash_usd": 1000.0,
                                                "available_usd": 900.0},
                 book=book, orders=orders)
    got = r.for_opportunity("m1", "f1")
    assert got["hard_rail_usd"] == 30.0
    assert got["idle_capital_usd"] == pytest.approx(800.0)  # 900 - 10% x 1000
    assert got["market_headroom_usd"] == pytest.approx(15.0)  # 100 - 80 - 5
    assert got["fixture_headroom_usd"] == pytest.approx(10.0)
    assert got["group_slot_usd"] == 0.0                     # 2 groups open
    assert r.cap_for("m1", "f1", 1000.0) == 0.0
    r3 = RC.Rails(caps=dict(caps, max_concurrent_groups=3), source="t",
                  ledger={"cash_usd": 1000.0, "available_usd": 900.0},
                  book=book, orders=orders)
    assert r3.cap_for("m1", "f1", 1000.0) == pytest.approx(10.0)
    # every benchmark (and Allie) is clamped by exactly these
    k, used, why = V2.clamp_to_rails(1000.0, r3.for_opportunity("m1", "f1"))
    assert k == pytest.approx(10.0) and why is None
    assert set(used) == {"hard_rail_usd", "idle_capital_usd",
                         "market_headroom_usd", "fixture_headroom_usd"}
    # a configured rail whose input is unmeasured: UNAVAILABLE, not fewer
    # rails
    bad = RC.Rails(caps=caps, source="t", ledger=None, book=None, orders=None,
                   why="RAIL_INPUT_UNMEASURED: OPEN_ORDERS_READ_TRUNCATED")
    k, used, why = V2.clamp_to_rails(1000.0, bad.for_opportunity("m1", "f1"))
    assert k is None and why.startswith("HARD_RAIL_UNMEASURED")
    # nothing allocated stays nothing whatever the rails
    assert V2.clamp_to_rails(0.0, bad.for_opportunity("m1", "f1"))[0] == 0.0


def test_the_caps_effective_at_the_clock():
    cfg = {"risk": {"per_order_cap_usd": 5000.0, "per_market_cap_usd": 1e4,
                    "per_fixture_cap_usd": 1.5e4,
                    "hedge_reserve_fraction": 0.2,
                    "max_concurrent_groups": 150}}
    pol = {"version": "PAPER_CAPITAL_1000_AVERAGE_TARGET_V1",
           "per_order_cap_usd": None, "per_market_cap_usd": None,
           "per_fixture_cap_usd": None, "max_concurrent_groups": None,
           "hedge_reserve_fraction": 0.0}
    from sportsassets import bettor_paper_limits as LIMITS
    assert RC.OWNER_POLICY_ACCOUNT == LIMITS.ACCOUNT_ID    # the restatement
    main = RC.OWNER_POLICY_ACCOUNT
    # the capital policy RECORDED ON THE DECISION wins (a versioned policy)
    caps, src = RC.caps_at({"account_id": main,
                            "provenance": {"capital_policy": pol}}, cfg)
    assert caps["per_order_cap_usd"] is None and \
        caps["hedge_reserve_fraction"] == 0.0
    assert src.startswith("DECISION_PROVENANCE_CAPITAL_POLICY")
    # a pre-policy decision of the main account (provenance, no policy)
    caps, src = RC.caps_at({"account_id": main, "provenance": {"x": 1}}, cfg)
    assert caps["per_order_cap_usd"] == 5000.0
    # the main account with no provenance cannot say which applied
    caps, src = RC.caps_at({"account_id": main, "provenance": None}, cfg)
    assert caps is None and src.startswith(
        "CAPITAL_POLICY_AT_THE_CLOCK_NOT_RECORDED")
    # another account: the session's caps; no visible session: UNAVAILABLE
    caps, _ = RC.caps_at({"account_id": "paper_test_x"}, cfg)
    assert caps["max_concurrent_groups"] == 150
    caps, src = RC.caps_at({"account_id": "paper_test_x"}, None)
    assert caps is None and src == \
        "SESSION_CONFIGURATION_NOT_VISIBLE_AT_THE_CLOCK"


# ── §4 management: actual vs canonical ───────────────────────────────

def _review(rid, *, chosen, fresh, taken, why=None, live=(), cand=None,
            evidence=True):
    meas = ({"evidence_state": "FRESH_CURRENT_PROBABILITY" if fresh
             else "STALE_ENTRY_TIME_PROBABILITY", "p": 0.66,
             "stale": not fresh} if evidence else {})
    return {"review_id": rid, "reviewed_at": 100.0,
            "selection": {"selected": chosen, "mechanical_selection": chosen},
            "measure": meas,
            "alternatives": {"candidates": [cand] if cand else []},
            "standing": {"live_orders": list(live), "protective_price": {
                "ok": True, "price": 0.57}},
            "exposure": {"open_qty": 100.0},
            "action": dict({"taken": taken}, **({"why": why} if why else {}))}


def _mg(reviews, *, sells=(), truncated=False, realized=31.7, hold=43.9,
        recorded=None):
    rec = {"outcome": {"reviews": reviews, "reviews_truncated": truncated,
                       "mgmt_intents": recorded or {}}}
    pos = {"entry": [{"qty": 100.0}], "q": 100.0, "payoff": 1.0,
           "sells": list(sells)}
    return CF.management(rec, pos, realized, hold)


REDUCE = {"action": "REDUCE", "qty": 40.0, "fee_usd": 0.2,
          "walk": {"worst_price": 0.70, "worst_wire": 0.70}}


def test_protect_with_no_uncommitted_inventory_is_the_canonical_action():
    # paper_xavier._maintain_standing under PROTECT returns NONE /
    # NO_UNCOMMITTED_INVENTORY when every held contract is committed
    m = _mg([_review("r1", chosen="HOLD", fresh=False, taken="NONE",
                     why="NO_UNCOMMITTED_INVENTORY")])
    r = m["per_review"][0]
    assert r["canonical_action"] == CI.ACT_PROTECT and r["match"] is True
    assert r["executed_without_order"] is True and m["divergent"] == 0
    assert m["canonical"]["pnl_usd"] == pytest.approx(31.7)


def test_a_divergent_canonical_sale_is_priced_at_the_walk():
    # canonical REDUCE on fresh evidence; the action taken kept the standing
    # protection instead
    m = _mg([_review("r1", chosen="REDUCE", fresh=True, taken="KEEP_STANDING",
                     cand=REDUCE)])
    assert m["divergent"] == 1
    assert m["canonical"]["pnl_usd"] == pytest.approx(
        43.9 + 40.0 * (0.70 - 1.0) - 0.2)
    assert m["canonical"]["basis"].startswith("HOLD_TO_SETTLEMENT")


def test_every_unpriceable_divergence_is_unavailable_with_its_reason():
    # canonical PROTECT, but the review sold: the protective fill that would
    # have happened instead cannot be priced
    m = _mg([_review("r1", chosen="HOLD", fresh=False, taken="SUBMIT_EXIT")])
    assert m["canonical"]["pnl_usd"] is None
    assert m["canonical"]["why"].startswith(
        "PROTECTIVE_FILL_COUNTERFACTUAL_NOT_PRICEABLE")
    # a protective fill on a divergent path
    m = _mg([_review("r1", chosen="REDUCE", fresh=True, taken="KEEP_STANDING",
                     cand=REDUCE)],
            sells=[{"qty": 10.0, "price": 0.57, "fee_usd": 0.0,
                    "role": "STANDING_PROTECTION"}])
    assert m["canonical"]["why"].startswith(
        "PROTECTIVE_FILLS_ON_A_DIVERGENT_PATH_NOT_PRICEABLE")
    # a review written before the freshness contract
    m = _mg([_review("r1", chosen="HOLD", fresh=False, taken="KEEP_STANDING",
                     evidence=False)])
    assert m["canonical"]["why"].startswith(
        "1_REVIEWS_WITHOUT_A_RECONSTRUCTABLE_CANONICAL_ACTION")
    # the per-position review bound stopped the read
    m = _mg([_review("r1", chosen="HOLD", fresh=False,
                     taken="KEEP_STANDING")], truncated=True)
    assert m["canonical"]["why"].startswith("REVIEWS_TRUNCATED")
    # R30 recorded a canonical action that the replay does not reproduce
    m = _mg([_review("r1", chosen="HOLD", fresh=False,
                     taken="KEEP_STANDING")], recorded={"r1": CI.ACT_NONE})
    assert m["recorded_differs_from_replay"] == 1


# ── §5 capital held to the horizon ───────────────────────────────────

def test_an_open_position_holds_its_capital_until_the_horizon():
    H = 3600.0

    def item(did, settled):
        pos = {"group_id": "g-" + did, "slug": did, "side": "LONG",
               "entry": [{"qty": 100.0, "price": 0.5, "fee_usd": 0.0,
                          "t": 0.0}], "sells": [], "own_settlement": (
                   {"payout_per_contract": 1.0, "settled_at": 10 * H,
                    "outcome": "WON"} if settled else None),
               "payoff": 1.0 if settled else None, "outcome": None,
               "payoff_basis": None}
        rec = {"decision": {"decision_id": did, "strategy": INV,
                            "decided_at": 0.0, "us_market_slug": did},
               "outcome": {"orders": []}, "ev": {"p": 0.6, "p_basis": "t",
                                                 "net": None},
               "horizon": 20 * H, "sleeve": "INVESTMENT"}
        eco = CF.economics(rec, pos)
        return {"rec": rec, "eval": {"economics": eco,
                                     "lost_opportunity": {}}}
    a, b = item("open", False), item("closed", True)
    eco = a["eval"]["economics"]["actual"]
    assert eco["state"] == "OPEN" and eco["open_at_horizon"]
    assert eco["capital_hours_to_horizon"] == pytest.approx(50.0 * 20)
    r = {"rec": {"decision": {"decision_id": "refused", "decided_at": 5 * H,
                              "us_market_slug": "x"}, "ev": {"net": 10.0},
                 "outcome": {"refusal": {"code": RC.R_CASH}},
                 "sleeve": "INVESTMENT"},
         "eval": {"economics": {"actual": {"segments": []}},
                  "lost_opportunity": {"classification":
                                       "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"}}}
    tape = {"rows": [], "unrecorded": [], "complete_until": 1e12}
    ea = EV.capital_extras(a, [a, b, r], tape, horizon=20 * H)
    eb = EV.capital_extras(b, [a, b, r], tape, horizon=20 * H)
    # both held $50 at t = 5 h: the missed EV is shared, the open one is no
    # longer charged nothing
    assert ea["missed_executable_ev_from_occupied_capital"][
        "attributed_usd"] == pytest.approx(5.0)
    assert eb["missed_executable_ev_from_occupied_capital"][
        "attributed_usd"] == pytest.approx(5.0)
    # a tape that stops before the release cannot count the marginal
    # opportunities: UNAVAILABLE, not "none"
    short = EV.capital_extras(a, [a, b, r], dict(tape, complete_until=H),
                              horizon=20 * H)
    assert short["marginal_opportunities_available"]["status"] == \
        "UNAVAILABLE"


# ── §6 cash vs rails ─────────────────────────────────────────────────

@pytest.mark.parametrize("code,cls", [
    ("INSUFFICIENT_AVAILABLE_PAPER_CASH", "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"),
    ("AN_ENTRY_MAY_NOT_SPEND_THE_HEDGE_RESERVE",
     "CASH_UNAVAILABLE_BY_PRIOR_ALLOCATION"),
    ("ABOVE_THE_PER_ORDER_CAP", "REFUSED_BY_A_HARD_RISK_RAIL"),
    ("ABOVE_THE_PER_MARKET_CONCENTRATION_CAP", "REFUSED_BY_A_HARD_RISK_RAIL"),
    ("ABOVE_THE_MAXIMUM_CONCURRENT_GROUPS", "REFUSED_BY_A_HARD_RISK_RAIL"),
    ("THIS_STRATEGY_ALREADY_HOLDS_THIS_CONTRACT",
     "ORDER_REFUSED_BY_PAPER_RISK")])
def test_only_cash_refusals_are_cash_held_by_prior_allocations(code, cls):
    rec = {"decision": {"verdict": "ENTER"},
           "outcome": {"orders": [], "refusal": {"code": code}}}
    assert CF.lost_opportunity(rec, {})["classification"] == cls


# ── §7 hypotheticals apart ───────────────────────────────────────────

def test_hypotheticals_are_never_added_to_realized_results():
    got = EV.sum_cf([CF.cf(10.0, 2.0), CF.cf(-25.4, 0.0, label=CF.HYPO),
                     CF.cf(3.0, 1.0)])
    assert got["pnl_usd"] == pytest.approx(13.0) and got["n"] == 2
    assert got["hypothetical"]["pnl_usd"] == pytest.approx(-25.4)
    assert got["hypothetical"]["label"] == CF.HYPO
    got = EV.sum_cf([CF.cf(10.0, 2.0), CF.cf(None, why="X: y")])
    assert got["pnl_usd"] is None and got["unavailable"] == {"X": 1}
    assert got["hypothetical"] is None


def test_a_truncated_snapshot_is_not_a_hindsight_violation():
    # the two failures mean different things: the component that needed an
    # incomplete snapshot is UNAVAILABLE; a hindsight violation stops the run
    assert not issubclass(P.SnapshotIncomplete, P.HindsightViolation)
