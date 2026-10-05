"""CAPITAL-CRITICAL: THE PROFITABILITY OS COMPONENTS ARE HONEST, PURE
COMPUTATIONS OVER RECORDED ROWS (sportsassets/pos_os).

One block per component, over synthetic rows (no database):
  * an unread input is UNAVAILABLE with its name -- never zeros;
  * a metric the rows cannot support is null with a named reason;
  * counterfactual rows never enter a realized figure;
  * a recommendation is never applied (`applied: false`) and a verdict
    promotes nothing;
  * a component that raises is its own UNAVAILABLE section and the rest of
    the page still renders.
"""
from __future__ import annotations

import pytest

from sportsassets.pos_os import assemble as A
from sportsassets.pos_os import autonomy as AU
from sportsassets.pos_os import capital as CAP
from sportsassets.pos_os import champion as CH
from sportsassets.pos_os import common as C
from sportsassets.pos_os import correlation as CO
from sportsassets.pos_os import drift as DR
from sportsassets.pos_os import execution as EX
from sportsassets.pos_os import existing as EXI
from sportsassets.pos_os import frontier as FR
from sportsassets.pos_os import governance as GV
from sportsassets.pos_os import release_twin as RT
from sportsassets.pos_os import sentinel as SE

NOW = 1_790_400_000.0
H, D = 3600.0, 86400.0
INV = "PINNACLE_COMPLETED_GAME_PAPER"
CHAL = "DEREK_ENTRY_POLICY_V2"
BENCH = "PINNACLE_ONLY_PAPER_BENCHMARK"


def pos(i, *, strategy=INV, sleeve="INVESTMENT", net=10.0, cap=100.0,
        ch=50.0, state="CLOSED", book="PAPER", released=None, **kw):
    d = {"book": book, "position_key": "paperpos:a:g%s:%s:LONG" % (i, i),
         "group_id": "g%s" % i, "us_market_slug": "m%s" % i,
         "holding_side": "LONG", "strategy": strategy, "sleeve": sleeve,
         "state": state, "net_profit_usd": net if state != "OPEN" else None,
         "capital_committed_usd": cap, "capital_hours": ch,
         "released_at": NOW - 2 * D if released is None else released,
         "opened_at": NOW - 3 * D}
    d.update(kw)
    return d


def empty_inputs():
    names = ["positions", "fills", "orders", "decisions", "settlements",
             "capacity", "books", "regime", "attribution", "twin",
             "model_tournament", "experiments", "improvements",
             "activations", "incidents", "archer_outcomes", "loops"]
    return dict({n: [] for n in names}, integrity={}, _errors={})


# ── the page contract ────────────────────────────────────────────────

def test_every_section_is_unavailable_with_a_reason_when_nothing_was_read():
    secs = A.build({"_errors": {}}, now=NOW)
    assert set(secs) == set(A.NAMES) and len(A.NAMES) == 15
    for name, s in secs.items():
        assert s["status"] == C.UNAVAILABLE, name
        assert s["why"] and C.R_INPUT_NOT_READ in s["why"], name
        assert s["data"] is None, name
        assert s["audit"] in (C.EXISTS, C.BUILT)
        assert s["implemented_by"]


def test_read_inputs_with_no_rows_are_empty_with_a_reason_not_zero():
    secs = A.build(empty_inputs(), now=NOW)
    for name, s in secs.items():
        assert s["status"] == C.EMPTY, (name, s["status"], s["why"])
        assert s["why"], name


def test_a_component_that_raises_is_its_own_unavailable_section(monkeypatch):
    def boom(inputs, *, now):
        raise RuntimeError("synthetic failure")
    sec = list(A.SECTIONS)
    sec[0] = (sec[0][0], boom, sec[0][2], sec[0][3])
    monkeypatch.setattr(A, "SECTIONS", tuple(sec))
    got = A.build(empty_inputs(), now=NOW)
    assert got["champion_challenger"]["status"] == C.UNAVAILABLE
    assert C.R_COMPONENT_RAISED in got["champion_challenger"]["why"]
    assert got["capacity_frontier"]["status"] == C.EMPTY


def test_existing_and_built_components_are_named():
    audits = {n: a for n, _, a, _ in A.SECTIONS}
    assert {n for n, a in audits.items() if a == C.EXISTS} == {
        "regime_detection", "post_trade_attribution", "counterfactual_twin"}


# ── champion / challenger ────────────────────────────────────────────

def test_a_challenger_that_beats_the_champion_is_named_and_promotes_nothing():
    rows = [pos(i, net=5.0 + (i % 3)) for i in range(12)]
    rows += [pos(100 + i, strategy=CHAL, net=30.0 + (i % 3))
             for i in range(12)]
    rows += [pos(200 + i, strategy=BENCH, sleeve="BENCHMARK", net=-20.0)
             for i in range(3)]
    rows += [pos(300 + i, book="COUNTERFACTUAL", net=9999.0)
             for i in range(20)]
    got = CH.strategy_tournament(rows)
    assert got["promotes"] is False
    # both INVESTMENT: the higher lower bound is champion
    assert got["champion"] == CHAL
    v = {c["strategy"]: c for c in got["challengers"]}
    assert v[INV]["verdict"] == "CHAMPION_HOLDS"
    assert v[BENCH]["verdict"] == C.INSUFFICIENT
    # counterfactual rows never enter
    assert sum(s["closed_positions"] for s in got["strategies"]) == 27


def test_no_investment_strategy_means_no_champion_named_why():
    rows = [pos(i, strategy=BENCH, sleeve="BENCHMARK") for i in range(15)]
    got = CH.strategy_tournament(rows)
    assert got["champion"] is None and "NO_INVESTMENT" in got["champion_why"]


# ── execution-cost learning and the league ──────────────────────────

def _book(bid, offer):
    return {"bids": [{"px": {"value": "%.2f" % bid}, "qty": "100"}],
            "offers": [{"px": {"value": "%.2f" % offer}, "qty": "100"}]}


def fill(i, *, side="LONG", direction="BUY", price=0.52, qty=100.0,
         b0=(0.48, 0.52), marks=((0.46, 0.50), (0.43, 0.47), None),
         order_type="MARKETABLE", tif="IOC", strategy=INV, fee=0.4):
    f = {"fill_id": "f%d" % i, "strategy": strategy, "holding_side": side,
         "direction": direction, "price": price, "qty": qty, "fee_usd": fee,
         "gross_usd": price * qty, "order_type": order_type,
         "time_in_force": tif, "filled_at": NOW - D,
         "group_id": "g%d" % i, "us_market_slug": "m%d" % i}
    if b0:
        bk = _book(*b0)
        f["b0_bids"], f["b0_offers"] = bk["bids"], bk["offers"]
    for k, m in enumerate(marks):
        if m:
            bk = _book(*m)
            f["m%d_bids" % k], f["m%d_offers" % k] = bk["bids"], bk["offers"]
    return f


def test_a_fill_is_costed_against_recorded_marks_only():
    c = EX.fill_costs(fill(1))
    assert c["mid_at_fill"] == pytest.approx(0.50)
    assert c["spread_usd"] == pytest.approx(2.0)          # (0.52-0.50)*100
    assert c["adverse_usd_60"] == pytest.approx(2.0)      # 0.50 -> 0.48
    assert c["adverse_usd_300"] == pytest.approx(5.0)     # 0.50 -> 0.45
    assert c["adverse_usd_1800"] is None
    assert c["unmeasured"]["adverse_1800"] == C.R_NO_MARK_AFTER_FILL
    # SHORT holds the complement: mid 1 - 0.50, a fall in YES is favourable
    s = EX.fill_costs(fill(2, side="SHORT", price=0.52))
    assert s["mid_at_fill"] == pytest.approx(0.50)
    assert s["adverse_usd_300"] == pytest.approx(-5.0)
    n = EX.fill_costs(fill(3, b0=None))
    assert n["spread_usd"] is None
    assert n["unmeasured"]["spread"] == C.R_NO_MID_AT_FILL


def test_learning_shrinks_a_thin_record_toward_the_pool():
    fills = [EX.fill_costs(fill(i)) for i in range(20)]
    fills += [EX.fill_costs(fill(100, strategy=CHAL,
                                 marks=((0.48, 0.52), (0.60, 0.64), None)))]
    got = EX.learn(fills)
    rows = {r["strategy"]: r for r in got["by_strategy_style"]}
    thin = rows[CHAL]["adverse"]["300s"]
    assert thin["n"] == 1 and thin["status"] == C.INSUFFICIENT
    assert thin["raw_pp"] == pytest.approx(-0.12)
    # pulled toward the (positive) pool, away from its own one fill
    assert thin["raw_pp"] < thin["shrunk_pp"] < thin["pool_pp"]
    assert rows[INV]["adverse"]["300s"]["status"] == C.MEASURED


def test_the_league_ranks_policies_on_realized_cost_per_filled_dollar():
    fs = [EX.fill_costs(fill(i)) for i in range(6)]
    fs += [EX.fill_costs(fill(10 + i, order_type="RESTING", tif="GTD",
                              price=0.49, fee=0.0,
                              marks=((0.48, 0.52), (0.48, 0.52), None)))
           for i in range(6)]
    fs += [EX.fill_costs(fill(30, order_type="LIMIT", tif="FOK"))]
    orders = [{"direction": "BUY", "order_type": "MARKETABLE",
               "time_in_force": "IOC", "state": "FILLED", "qty": 100,
               "filled_qty": 100},
              {"direction": "BUY", "order_type": "RESTING",
               "time_in_force": "GTD", "state": "EXPIRED", "qty": 100,
               "filled_qty": 0}]
    got = EX.league(fs, orders)
    assert got["ranking"] == ["BUY/RESTING/GTD", "BUY/MARKETABLE/IOC"]
    by = {p["policy"]: p for p in got["policies"]}
    assert by["BUY/LIMIT/FOK"]["status"] == C.INSUFFICIENT
    assert by["BUY/RESTING/GTD"]["fill_rate_qty"] == 0.0
    taker = by["BUY/MARKETABLE/IOC"]
    # (2 spread + 0.4 fee + 5 adverse) / 52 gross
    assert taker["realized_cost_per_filled_dollar"] == pytest.approx(
        7.4 / 52.0, abs=1e-6)
    assert got["chooses_nothing"] is True


# ── capital-hour optimizer and the expected-profit clock ────────────

def test_the_optimizer_recommends_capital_hours_and_applies_nothing():
    rows = [pos(i, net=8.0 + i % 2, ch=40.0) for i in range(12)]
    rows += [pos(100 + i, strategy=CHAL, net=-9.0 - i % 2, ch=40.0)
             for i in range(12)]
    rows += [pos(200 + i, strategy=BENCH, sleeve="BENCHMARK", net=50.0)
             for i in range(12)]
    rows += [pos(300 + i, strategy="NEW", net=1.0) for i in range(2)]
    got = CAP.optimizer(rows)
    assert got["applied"] is False
    assert got["authority"] == "RECOMMENDATION_ONLY"
    by = {r["strategy"]: r for r in got["strategies"]}
    assert by[INV]["recommendation"] == "FAVOUR"
    assert by[CHAL]["recommendation"] == "REDUCE_CAPITAL_HOURS"
    assert by[BENCH]["recommendation"] == "NOT_ALLOCATED_RESEARCH_SLEEVE"
    assert by["NEW"]["recommendation"] == "HOLD_INSUFFICIENT_EVIDENCE"
    inv_share = sum(r["current_capital_hour_share"] for r in by.values()
                    if r["sleeve"] == "INVESTMENT")
    assert by[INV]["recommended_capital_hour_share"] == pytest.approx(
        inv_share, abs=1e-6)


def test_the_clock_has_honest_uncertainty_and_counts_unmeasured():
    op = [pos(i, state="OPEN", bought_qty=100.0, probability=0.6,
              expected_net_profit_usd=10.0, open_cost_basis_usd=50.0,
              expected_release_at=NOW + 10 * H) for i in range(3)]
    op.append(pos(9, state="OPEN", bought_qty=100.0, probability=None,
                  expected_net_profit_usd=None, open_cost_basis_usd=40.0,
                  expected_release_at=NOW + H))
    closed = [pos(20, net=6.0, expected_net_profit_usd=10.0,
                  released=NOW - 2 * H)]
    got = CAP.clock(op + closed, now=NOW)
    inv = got["by_sleeve"]["INVESTMENT"]
    assert inv["accrual_usd_per_hour"] == pytest.approx(3.0)
    w = inv["released_within"]["24h"]
    assert w["expected_usd"] == pytest.approx(30.0)
    sd = (3 * 100 ** 2 * 0.6 * 0.4) ** 0.5
    assert w["band90_usd"][0] == pytest.approx(30 - 1.645 * sd, abs=1e-4)
    assert inv["released_within"]["1h"]["expected_usd"] == 0.0
    assert got["unmeasured_positions"] == 1
    assert got["unmeasured_cost_basis_usd"] == pytest.approx(40.0)
    assert got["unmeasured"][0]["why"] == "NO_EXPECTED_NET_PROFIT"
    assert got["honesty"]["realized_over_expected"] == pytest.approx(0.6)
    assert got["trailing_realized"]["24h"]["realized_usd"] == 6.0
    assert got["by_sleeve"]["TRAINING"]["accrual_usd_per_hour"] is None


# ── capacity frontier ────────────────────────────────────────────────

def test_the_frontier_counts_depth_limited_sizes_as_earning_nothing():
    def cand(net_at, limited_from):
        grid = []
        for size in (100.0, 500.0, 1000.0):
            if size >= limited_from:
                grid.append({"size_usd": size,
                             "status": "EXCEEDS_VISIBLE_DEPTH",
                             "expected_net_profit_usd": None})
            else:
                grid.append({"size_usd": size, "status": "MEASURED",
                             "expected_net_profit_usd": net_at(size)})
        return {"strategy": INV, "status": "MEASURED", "edge_at_size": grid,
                "executable_capacity_usd": 400.0}
    rows = [cand(lambda s: s * 0.05, 1e9) for _ in range(2)]
    rows += [cand(lambda s: s * 0.05, 500.0) for _ in range(2)]
    rows.append({"strategy": INV, "status": "UNAVAILABLE",
                 "edge_at_size": None})
    got = FR.frontier(rows)
    st = got["strategies"][0]
    assert st["candidates"] == 4 and st["status"] == C.MEASURED
    c = {p["size_usd"]: p for p in st["curve"]}
    assert c[1000.0]["depth_limited"] == 2
    assert c[1000.0]["depth_adjusted_net_usd"] == pytest.approx(25.0)
    assert c[500.0]["depth_adjusted_net_usd"] == pytest.approx(12.5)
    assert st["optimum_size_usd"] == 1000.0
    assert got["label"] == "EXPECTED_CONDITIONAL_ON_FILL_AT_RECORDED_BOOK"


# ── data-quality sentinel ────────────────────────────────────────────

def test_the_sentinel_judges_thresholds_and_never_passes_an_unread_check():
    inputs = empty_inputs()
    inputs["integrity"] = {"fills": 100, "fills_without_book": 30,
                           "fills_out_of_range": 0, "fills_bad_fee": 0,
                           "fills_book_older_than_60s": 6, "decisions": 50,
                           "enters_without_probability": 1,
                           "decisions_without_strategy": 0, "orders": 10,
                           "orders_past_expiry_not_terminal": 0,
                           "orders_overfilled": 0}
    inputs["settlements"] = [
        {"settlement_id": "s1", "position_key": "k", "outcome": "WON",
         "payout_per_contract": 1.0},
        {"settlement_id": "s2", "position_key": "k", "outcome": "LOST",
         "payout_per_contract": 0.0},
        {"settlement_id": "s3", "position_key": "j", "outcome": "WON",
         "payout_per_contract": 1.0},
        {"settlement_id": "s4", "position_key": "j", "outcome": "LOST",
         "payout_per_contract": 0.0, "supersedes": "s3"}]
    inputs["books"] = [
        {"bids": _book(0.55, 0.50)["bids"], "offers": _book(0.55, 0.50)[
            "offers"]},
        {"bids": _book(0.48, 0.52)["bids"], "offers": _book(0.48, 0.52)[
            "offers"]}]
    inputs["positions"] = None
    got = SE.build(inputs, now=NOW)
    assert got["status"] == C.OK and got["data"]["overall"] == SE.FAIL
    ck = {c["check"]: c for c in got["data"]["checks"]}
    assert ck["fills_without_recorded_book"]["status"] == SE.FAIL
    assert ck["fills_book_evidence_older_than_60s"]["status"] == SE.WARN
    assert ck["enter_decisions_without_probability"]["status"] == SE.FAIL
    assert ck["fills_price_or_qty_out_of_range"]["status"] == SE.PASS
    assert ck["settlements_conflicting"]["numerator"] == 1
    assert ck["settlements_conflicting"]["examples"] == ["k"]
    assert ck["books_crossed"]["numerator"] == 1
    assert ck["positions_without_strategy"]["status"] == C.UNAVAILABLE
    assert "positions_without_strategy" in got["data"][
        "unavailable_checks"]
    assert got["data"]["blocks_nothing"] is True


# ── experiment governance ────────────────────────────────────────────

def test_governance_names_peeking_and_ungoverned_promotions():
    exps = [
        {"experiment_id": "ok", "registered_at": NOW - 10 * D,
         "start_at": NOW - 9 * D, "stop_at": NOW + D, "min_sample": 100,
         "primary_metric": {"name": "net", "direction": "UP"},
         "stopping_rule": {"early_stop_for_efficacy": False, "analyses": 1},
         "result": None},
        {"experiment_id": "peek", "registered_at": NOW - 10 * D,
         "start_at": NOW - 11 * D, "stop_at": NOW + D, "min_sample": 100,
         "status_changed_at": NOW - D,
         "primary_metric": {"name": "net"},
         "stopping_rule": {"early_stop_for_efficacy": True, "analyses": 3},
         "result": {"verdict": "SUPPORTED",
                    "arms": {"CONTROL": {"n": 10}, "TREATMENT": {"n": 12}}}}]
    imps = [
        {"kind": "CANDIDATE", "candidate_id": "c1", "state": "RELEASED",
         "hypothesis": "", "success_metrics": [], "evaluated_at": NOW - D,
         "approved_at": NOW - 2 * D},
        {"kind": "CANDIDATE", "candidate_id": "c2", "state": "APPROVED",
         "hypothesis": "h", "success_metrics": ["m"],
         "evaluated_at": NOW - 3 * D, "approved_at": NOW - 2 * D},
        {"kind": "TRIAL", "trial_id": "t1", "created_at": NOW - 2 * D,
         "evaluation_boundary_at": NOW - D},
        {"kind": "RELEASE", "release_id": "r1", "candidate_id": "c1",
         "released_at": NOW - 1.5 * D},
        {"kind": "RELEASE", "release_id": "r2", "candidate_id": "zz",
         "released_at": NOW}]
    acts = [{"activation_id": "a1", "kind": "ACTIVATE", "evaluation_id": None},
            {"activation_id": "a2", "kind": "SHIPPED_DEFAULT"}]
    got = GV.build({"experiments": exps, "improvements": imps,
                    "activations": acts, "_errors": {}}, now=NOW)
    codes = {(v["subject"], v["violation"]) for v in
             got["data"]["violations"]}
    assert ("ok", "NO_PRIMARY_METRIC") not in codes
    for c in ("REGISTERED_AFTER_START", "NO_PRIMARY_METRIC",
              "EARLY_EFFICACY_STOP_ALLOWED", "MULTIPLE_LOOKS_WITHOUT_SPENDING",
              "CONCLUSIVE_BEFORE_STOPPING_RULE"):
        assert ("peek", c) in codes, c
    assert ("c1", "PROMOTED_WITHOUT_PREDECLARATION") in codes
    assert ("c1", "APPROVED_BEFORE_EVALUATION") in codes
    assert not any(s == "c2" for s, _ in codes)
    assert ("t1", "SCORED_BEFORE_WINDOW_CLOSED") in codes
    assert ("r1", "RELEASED_BEFORE_EVALUATION") in codes
    assert ("r2", "RELEASED_WITHOUT_CANDIDATE") in codes
    assert ("a1", "ACTIVATED_WITHOUT_EVALUATION") in codes
    assert got["data"]["verdict"] == "VIOLATIONS"
    clean = GV.build({"experiments": exps[:1], "improvements": [],
                      "activations": None, "_errors": {}}, now=NOW)
    assert clean["data"]["verdict"] == "COMPLIANT"
    assert clean["data"]["unread_inputs"] == ["activations"]


# ── drift ────────────────────────────────────────────────────────────

def test_drift_detects_a_shift_and_says_insufficient_when_thin():
    decs = [{"p_pinnacle": 0.5 + 0.001 * (i % 10),
             "decided_at": NOW - 20 * D + i * 60} for i in range(60)]
    decs += [{"p_pinnacle": 0.85, "decided_at": NOW - D + i * 60}
             for i in range(40)]
    rows, sets = [], []
    for i in range(30):
        recent = i >= 15
        k = "paperpos:a:g%d:m%d:LONG" % (i, i)
        won = (i % 2 == 0) if not recent else False
        rows.append(pos(i, probability=0.6, position_key=k,
                        released=(NOW - D if recent else NOW - 15 * D),
                        predicted_edge_per_dollar=0.05,
                        realized_edge_per_dollar=(0.05 + 0.01 * (i % 3))
                        if not recent else -0.5 - 0.01 * (i % 3)))
        sets.append({"settlement_id": "s%d" % i, "position_key": k,
                     "outcome": "WON" if won else "LOST"})
    got = DR.drift(rows, sets, decs, now=NOW)
    assert got["probability_psi"]["verdict"] == "MAJOR"
    inv = got["scopes"]["INVESTMENT"]
    assert inv["calibration_bias"]["verdict"] == "DRIFT_DETECTED"
    assert inv["economic_gap"]["verdict"] == "DRIFT_DETECTED"
    assert got["overall"] == "DRIFT_DETECTED" and got["changes"] == "NOTHING"
    thin = DR.drift(rows[:4], sets[:4], decs[:5], now=NOW)
    assert thin["overall"] == C.INSUFFICIENT


# ── scenario / correlation ───────────────────────────────────────────

def test_exposure_is_bucketed_by_event_league_and_team():
    def lf(i, ev, league, team):
        return {"group_id": "g%d" % i, "us_market_slug": "m%d" % i,
                "direction": "BUY", "filled_at": NOW - D,
                "label": {"event_key": ev, "competition": league,
                          "participant": team, "home_team": "A",
                          "away_team": "B"}}
    op = [pos(i, state="OPEN", open_cost_basis_usd=100.0) for i in range(3)]
    op.append(pos(3, state="OPEN", open_cost_basis_usd=100.0,
                  us_market_slug="m3"))
    fills = [lf(0, "E1", "MLB", "A"), lf(1, "E1", "MLB", "B"),
             lf(2, "E2", "NFL", "C")]
    got = CO.exposure(op, fills)
    assert got["exposure_usd"] == 400.0
    ev = {b["bucket"]: b for b in got["dimensions"]["event"]["buckets"]}
    assert ev["E1"]["exposure_usd"] == 200.0
    assert ev["E1"]["both_sides_or_several_markets"] is True
    lg = got["dimensions"]["league"]
    assert lg["unknown_exposure_usd"] == 100.0
    assert lg["hhi"] == pytest.approx(0.5 ** 2 + 0.25 ** 2 + 0.25 ** 2)
    top = got["scenarios"][0]
    assert top["loss_upper_bound_usd"] == 200.0
    assert got["co_exposure"][0]["teams"] == ["A", "B"]


def test_realized_outcome_correlation_needs_enough_pairs():
    fills = [{"group_id": "g%d" % i, "us_market_slug": "m%d" % i,
              "direction": "BUY", "filled_at": NOW,
              "label": {"competition": "MLB" if i < 8 else "NFL"}}
             for i in range(16)]
    rows = [pos(i, net=(5.0 if i % 2 else -5.0) if i < 8 else 5.0,
                released=NOW - 2 * D) for i in range(16)]
    got = CO.realized(rows, fills)
    assert got["same_league_same_day"]["pairs"] == 28 + 28
    assert got["different_league_same_day"]["pairs"] == 64
    thin = CO.realized(rows[:3], fills)
    assert thin["same_league_same_day"]["status"] == C.INSUFFICIENT


# ── autonomy health ──────────────────────────────────────────────────

def test_autonomy_counts_loops_decisions_and_the_refusal_mix():
    loops = [{"loops": [{"name": "a", "status": "HEALTHY"},
                        {"name": "b", "status": "UNHEALTHY"},
                        {"name": "c", "status": "DISABLED"}],
              "summary": {"HEALTHY": 1}, "capital_critical_not_healthy":
                  ["b"]}]
    decs = [{"decided_at": NOW - 10 * 60, "verdict": "ENTER",
             "strategy": INV},
            {"decided_at": NOW - 2 * H, "verdict": "REFUSE",
             "refusal": "PROBABILITY_EVIDENCE_STALE", "strategy": INV},
            {"decided_at": NOW - 3 * H, "verdict": "REFUSE",
             "refusals": ["SKIP_EXECUTION"], "strategy": CHAL},
            {"decided_at": NOW - 3 * D, "verdict": "REFUSE",
             "refusal": "PROBABILITY_EVIDENCE_STALE", "strategy": INV}]
    got = AU.build(dict(loops=loops, decisions=decs, _errors={}), now=NOW)
    d = got["data"]
    assert d["loops"]["alive_share"] == 0.5
    assert d["loops"]["capital_critical_not_healthy"] == ["b"]
    assert d["decisions"]["last_1h"]["decisions"] == 1
    assert d["decisions"]["last_24h"]["decisions_per_hour"] == pytest.approx(
        3 / 24, abs=1e-6)
    rm = d["refusal_mix"]
    assert rm["refusals"] == 2 and rm["stale_rate"] == 0.5
    assert rm["share_by_class"] == {"ECONOMIC": 0.5, "SOFTWARE": 0.5}
    lost = AU.build(dict(loops=None, decisions=decs, _errors={}), now=NOW)
    assert lost["data"]["loops"]["status"] == C.UNAVAILABLE


# ── release / incident twin ──────────────────────────────────────────

def test_a_release_is_replayed_before_and_after_on_recorded_rows_only():
    decs = []
    for i in range(20):
        decs.append({"strategy": INV, "policy_version": "V1",
                     "verdict": "ENTER" if i % 4 == 0 else "REFUSE",
                     "refusal": "EDGE_LOW", "recorded_ev_usd": 2.0,
                     "decided_at": NOW - 20 * H + i * 30 * 60})
    for i in range(20):
        decs.append({"strategy": INV, "policy_version": "V2",
                     "verdict": "ENTER" if i % 2 == 0 else "REFUSE",
                     "refusal": "EDGE_LOW", "recorded_ev_usd": 4.0,
                     "decided_at": NOW - 10 * H + i * 30 * 60})
    t = NOW - 10 * H
    positions = [pos(i, opened_at=t - 5 * H, net=1.0, ch=10.0)
                 for i in range(3)]
    positions += [pos(10 + i, opened_at=t + 2 * H, net=3.0, ch=10.0)
                  for i in range(3)]
    evs = RT.events(decs, [], [], [{"finding_id": "f", "kind": "X",
                                    "at": NOW - 30 * 60}])
    kinds = [e["kind"] for e in evs]
    assert kinds == ["INCIDENT", "POLICY_VERSION"]
    got = RT.build({"decisions": decs, "positions": positions,
                    "improvements": [], "activations": [], "incidents": [
                        {"finding_id": "f", "kind": "X",
                         "at": NOW - 30 * 60}], "_errors": {}}, now=NOW)
    reps = {r["kind"]: r for r in got["data"]["replays"]}
    pv = reps["POLICY_VERSION"]
    assert pv["status"] == C.MEASURED and pv["window_h"] == pytest.approx(10)
    assert pv["before"]["enter_rate"] == 0.25
    assert pv["after"]["enter_rate"] == 0.5
    assert pv["after_minus_before"]["mean_recorded_ev_usd"] == 2.0
    assert pv["after_minus_before"]["realized_net_usd"] == 6.0
    assert pv["label"] == "OBSERVATIONAL_NOT_CAUSAL"
    assert reps["INCIDENT"]["status"] == C.INSUFFICIENT
    assert got["data"]["re_executes_nothing"] is True


# ── the components that exist elsewhere ─────────────────────────────

def test_existing_components_are_read_with_counterfactual_kept_apart():
    paper = pos(1, net=5.0)
    cf = pos(2, book="COUNTERFACTUAL", net=-3.0,
             basis_position_key=paper["position_key"])
    got = EXI.counterfactual({"positions": [paper, cf], "twin": [],
                              "_errors": {}}, now=NOW)
    h = got["data"]["hold_to_settlement"]
    assert h["REALIZED"]["net_usd"] == 5.0
    assert h["COUNTERFACTUAL"]["net_usd"] == -3.0
    assert h["counterfactual_minus_realized_usd"] == -8.0
    assert got["data"]["summed_with_realized"] is False
    assert got["data"]["twin"]["status"] == C.EMPTY
    r = EXI.regime({"regime": [{"recommendation": "NORMAL",
                                "computed_at": NOW - 60, "signals": "[]",
                                "applied": False}], "_errors": {}}, now=NOW)
    assert r["status"] == C.OK and r["data"]["age_s"] == 60.0
    a = EXI.attribution({"attribution": [
        {"book": "PAPER", "strategy": INV, "model_edge_usd": 3.0,
         "execution_edge_usd": -1.0, "fees_usd": 0.5,
         "realized_pnl_usd": 2.0, "reconciles": True}], "_errors": {}},
        now=NOW)
    assert a["data"]["totals"]["PAPER"]["model_edge_usd"] == 3.0
    assert a["data"]["by_strategy"][INV]["fees_usd"] == 0.5
