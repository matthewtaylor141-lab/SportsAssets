"""THE LEARNING LAYER'S PURE PARTS (migration 218): the model tournament's
training / forecasting / fixed-sample verdict, the edge-confidence
meta-model, the avoidance model, the agent variants, the experiment design
and analysis, and Karen's / Audrey's checks. No database.

ALL DATA HERE IS SYNTHETIC, generated from fixed seeds.
"""
from __future__ import annotations

import math
import random

import pytest

from sportsassets.poslearn import agents as AG
from sportsassets.poslearn import audrey_review as AUD
from sportsassets.poslearn import avoidance as AV
from sportsassets.poslearn import common as C
from sportsassets.poslearn import edge_confidence as EC
from sportsassets.poslearn import experiments as EX
from sportsassets.poslearn import features as FT
from sportsassets.poslearn import karen_review as KAR
from sportsassets.poslearn import logistic as LR
from sportsassets.poslearn import models as M
from sportsassets.poslearn import scoring as S

T0 = 1_790_000_000.0


def _rows(n, *, seed=7, sport="baseball", shrink=0.4, price_gap=0.02,
          fee=0.01, extra=None):
    """Synthetic resolved records: the raw p is OVERCONFIDENT (the truth is
    0.5 + shrink * (p - 0.5)), so a recalibration has something to find."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        p = (0.15, 0.3, 0.7, 0.85)[i % 4]
        q = 0.5 + shrink * (p - 0.5)
        o = 1 if rng.random() < q else 0
        price = q - price_gap
        f = {"logit_p": C.logit(p), "gross_edge": p - price,
             "net_edge": p - price - fee, "probability_band":
             FT.prob_band(p), "settlement_family": "UNKNOWN"}
        f.update(extra or {})
        out.append({"p": p, "p_reference": p, "o": o, "features": f,
                    "sport": sport, "league": "UNKNOWN", "market": "h2h",
                    "live_state": "PREGAME",
                    "sport_key": M.sport_key(sport, "UNKNOWN"),
                    "decided_at": T0 + i, "outcome_at": T0 + i + 3600,
                    "price": price, "fee": fee})
    return out


# ── common ──────────────────────────────────────────────────────────

def test_the_normal_helpers_and_the_bonferroni_adjustment():
    assert C.norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert C.norm_cdf(C.norm_ppf(0.3)) == pytest.approx(0.3, abs=1e-9)
    z1, z11 = C.adjusted_z(0.05, 1), C.adjusted_z(0.05, 11)
    assert z1 == pytest.approx(1.96, abs=1e-3)
    assert z11 > z1 and z11 == pytest.approx(C.norm_ppf(1 - 0.05 / 22))
    assert C.canonical({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_the_logistic_fit_recovers_a_known_relationship():
    rng = random.Random(3)
    rows, ys = [], []
    for _ in range(800):
        x = rng.uniform(-2, 2)
        rows.append({"x": x})
        ys.append(1 if rng.random() < C.sigmoid(0.5 + 1.5 * x) else 0)
    params = LR.fit(rows, ys, ["x"], ridge=1e-3)
    std = params["standardizer"]["x"]
    slope = params["coef"][1] / std[1]
    assert slope == pytest.approx(1.5, abs=0.3)
    lo = LR.predict(params, {"x": -2.0})
    hi = LR.predict(params, {"x": 2.0})
    assert lo["p"] < 0.2 < 0.85 < hi["p"]
    assert lo["p_lo"] < lo["p"] < lo["p_hi"]
    assert LR.predict(params, {})["missing"] == ["x"]
    assert LR.fit(rows[:3], ys[:3], ["x"]) is None


# ── 1 · the model tournament ────────────────────────────────────────

def test_the_declared_plan_and_its_family_size():
    subjects = [s for s, _, _ in M.SPECS]
    assert subjects[0] == M.CHAMPION == "M0_PINNAPI_RAW"
    for s in ("M1_PINNAPI_CALIBRATED", "M2_PINNAPI_PLUS_MICROSTRUCTURE",
              "M3_PINNAPI_PLUS_SCOUT", "M4_CROSS_MARKET_CONSENSUS"):
        assert s in subjects
    for sport in ("NFL", "MLB", "NCAAF", "NBA", "NHL", "SOCCER", "TENNIS"):
        assert "M5_%s_SPECIFIC" % sport in subjects
    assert M.FAMILY_SIZE == len(subjects) - 1 == 11
    placeholders = {s: p for s, _, p in M.SPECS if p}
    assert placeholders == {"M3_PINNAPI_PLUS_SCOUT": "AWAITING_FEATURES",
                            "M4_CROSS_MARKET_CONSENSUS": "AWAITING_SOURCE"}


def test_sport_keys():
    assert M.sport_key("baseball", "UNKNOWN") == "MLB"
    assert M.sport_key("football", "nfl") == "NFL"
    assert M.sport_key("football", "cfb") == "NCAAF"
    assert M.sport_key("basketball", "nba") == "NBA"
    assert M.sport_key("hockey", "UNKNOWN") == "NHL"
    assert M.sport_key("soccer", "epl") == "SOCCER"
    assert M.sport_key("tennis", "atp") == "TENNIS"
    assert M.sport_key("table_tennis", "x") == "OTHER"


def test_training_happens_only_on_outcomes_known_by_registration():
    rows = _rows(300)
    got = M.train("M1_PINNAPI_CALIBRATED", "CALIBRATED", rows,
                  now=T0 + 200 + 3600)
    # only the rows whose outcome_at <= now were usable
    assert got["trainable"] is False or got["n"] <= 201
    got = M.train("M1_PINNAPI_CALIBRATED", "CALIBRATED", rows,
                  now=T0 + 10 ** 6)
    assert got["trainable"] and got["n"] == 300
    assert got["window"]["end"] <= T0 + 10 ** 6
    # an overconfident raw p is shrunk toward 0.5
    assert got["params"]["method"] == "INTEL_FIT_OVERLAY"
    assert 0.5 < M.apply_recalibration(got["params"], 0.85) < 0.85
    small = M.train("M1_PINNAPI_CALIBRATED", "CALIBRATED", rows[:50],
                    now=T0 + 10 ** 6)
    assert small["trainable"] is False
    assert small["why"].startswith("INSUFFICIENT_TRAINING_DATA")


def test_a_diverging_overlay_fit_is_refused_and_a_damped_fit_used():
    """intel/calibration.fit_overlay (undamped Newton from a=0, b=1)
    diverges on extreme probabilities; the layer must not register that."""
    rng = random.Random(4)
    pairs = []
    for i in range(260):
        p = (0.03, 0.97)[i % 2]
        pairs.append((p, 1 if rng.random() < 0.5 + 0.4 * (p - 0.5) else 0))
    from sportsassets.intel import calibration as CAL
    raw = CAL.fit_overlay(pairs)
    got = M.recalibrate(pairs)
    if raw is not None and abs(raw["a"]) <= 20 and 0 < raw["b"] <= 20:
        assert got["method"] == "INTEL_FIT_OVERLAY"
    else:
        assert got["method"] == "DAMPED_IRLS_FALLBACK"
        assert "did not converge" in got["why"]
    hi = M.apply_recalibration(got, 0.97)
    lo = M.apply_recalibration(got, 0.03)
    assert 0.55 < hi < 0.8 and 0.2 < lo < 0.45


def test_placeholders_are_not_fitted_and_say_why():
    for subject, kind, ph in M.SPECS:
        if ph:
            got = M.train(subject, kind, _rows(400), now=T0 + 10 ** 6)
            assert got["trainable"] is False
            assert got["why"] == M.PLACEHOLDER_WHY[ph]
    assert "valuation_corroboration" in M.PLACEHOLDER_WHY["AWAITING_SOURCE"]


def test_the_document_declares_every_criterion_before_forward_data():
    got = M.train("M1_PINNAPI_CALIBRATED", "CALIBRATED", _rows(300),
                  now=T0 + 10 ** 6)
    doc = M.document("M1_PINNAPI_CALIBRATED", "CALIBRATED", got, version=1,
                     threshold_pp=0.5, threshold_basis="TEST")
    for k in ("training_window", "features", "parameters",
              "validation_method", "minimum_sample", "promotion_threshold",
              "failure_threshold", "hypothesis_family", "family_size"):
        assert k in doc, k
    assert doc["family_size"] == M.FAMILY_SIZE
    assert doc["promotion_threshold"]["z_adjusted"] == pytest.approx(
        C.adjusted_z(0.05, 11), abs=1e-6)
    champ = M.document(M.CHAMPION, "RAW", M.train(M.CHAMPION, "RAW", [],
                                                  now=T0),
                       version=1, threshold_pp=0.5, threshold_basis="TEST")
    assert champ["role"] == "CHAMPION"
    assert champ["training_window"] == "NOT_TRAINED"


def _opp(p, *, price=0.5, fee=0.01, sport="baseball", league="UNKNOWN",
         features=None):
    return {"opportunity_id": "o", "p_reference": p, "price": price,
            "fee": fee, "sport": sport, "league": league,
            "features": features or {"logit_p": C.logit(p)}}


def test_forecasts_and_named_abstentions():
    got = M.train("M5_MLB_SPECIFIC", "SPORT:MLB", _rows(300),
                  now=T0 + 10 ** 6)
    doc = M.document("M5_MLB_SPECIFIC", "SPORT:MLB", got, version=1,
                     threshold_pp=0.5, threshold_basis="TEST")
    inside = M.forecast(doc, _opp(0.8))
    assert inside["probability"] is not None and inside["action"] in (
        "ENTER", "PASS")
    outside = M.forecast(doc, _opp(0.8, sport="soccer"))
    assert outside["action"] == "ABSTAIN"
    assert outside["abstain_reason"] == "OUT_OF_SCOPE_SPORT_SOCCER"
    raw = M.document(M.CHAMPION, "RAW", {"features": ["p_reference"],
                                         "params": {}},
                     version=1, threshold_pp=0.5, threshold_basis="TEST")
    nofee = M.forecast(raw, _opp(0.8, fee=None))
    assert nofee["action"] == "SCORE" and nofee["probability"] == 0.8
    assert nofee["predicted_net_edge"] is None
    assert M.forecast(raw, _opp(0.8, price=0.7))["action"] == "ENTER"
    assert M.forecast(raw, _opp(0.703, price=0.7))["action"] == "PASS"


def _pairs_setup(n, *, better=True, seed=1):
    rng = random.Random(seed)
    opps, outs, ch, xf = {}, {}, {}, []
    for i in range(n):
        oid = "o%04d" % i
        p_raw = 0.97 if i % 2 else 0.03
        truth = 0.6 if i % 2 else 0.4
        y = 1 if rng.random() < truth else 0
        opps[oid] = {"opportunity_at": T0 + i, "price": 0.5, "fee": 0.01,
                     "p_reference": p_raw, "sport": "baseball"}
        outs[oid] = {"outcome_class": "RESOLVED", "o": y}
        ch[oid] = {"opportunity_id": oid, "probability": p_raw,
                   "action": "SCORE"}
        xf.append({"opportunity_id": oid, "action": "SCORE",
                   "probability": (truth if better else p_raw)})
    return opps, outs, ch, xf


def _chal(n_min, fam=11):
    return {"registration_id": "X@1", "registered_at": T0 - 1,
            "document": {"minimum_sample": n_min, "family_size": fam,
                         "role": "CHALLENGER"}}


def test_the_fixed_sample_verdict_is_decided_on_the_first_n_only():
    opps, outs, ch, xf = _pairs_setup(200)
    first = S.paired(_chal(100), ch, xf[:100], opps, outs)
    assert first["verdict"] == "CRITERIA_MET", first
    # later, contrary forward data cannot move a decided verdict
    for f in xf[100:]:
        f["probability"] = 0.999 if outs[f["opportunity_id"]]["o"] == 0 \
            else 0.001
    later = S.paired(_chal(100), ch, xf, opps, outs)
    assert later["verdict"] == "CRITERIA_MET"
    assert later["sample_ids_sha256"] == first["sample_ids_sha256"]
    short = S.paired(_chal(500), ch, xf, opps, outs)
    assert short["verdict"] == "INSUFFICIENT_SAMPLE"
    assert short["log_loss_improvement"] is None


def test_significance_at_005_is_not_enough_under_the_family_size():
    # a modest, DETERMINISTIC improvement (230 of 400 outcomes are 1): its
    # paired log-loss t is ~2.4 -- significant unadjusted, not at 0.05 / 11
    opps, outs, ch, xf = {}, {}, {}, []
    n = 400
    for i in range(n):
        oid = "m%04d" % i
        y = 1 if i < 230 else 0
        opps[oid] = {"opportunity_at": T0 + i, "price": 0.5, "fee": 0.0}
        outs[oid] = {"outcome_class": "RESOLVED", "o": y}
        ch[oid] = {"probability": 0.5}
        xf.append({"opportunity_id": oid, "action": "SCORE",
                   "probability": 0.53})
    lls = [S._ll(0.5, outs[f["opportunity_id"]]["o"])
           - S._ll(0.53, outs[f["opportunity_id"]]["o"]) for f in xf]
    _, lo1, _, _ = C.mean_ci(lls, 1.959964)
    _, lo11, _, _ = C.mean_ci(lls, C.adjusted_z(0.05, 11))
    assert lo1 > 0 > lo11
    assert S.paired(_chal(n, fam=1), ch, xf, opps, outs)["verdict"] == \
        "CRITERIA_MET"
    assert S.paired(_chal(n, fam=11), ch, xf, opps, outs)["verdict"] == \
        "NOT_MET"


def test_a_worse_challenger_fails_its_predeclared_threshold():
    opps, outs, ch, xf = _pairs_setup(200)
    for f in xf:
        f["probability"] = 0.98 if f["probability"] < 0.5 else 0.02
    assert S.paired(_chal(100), ch, xf, opps, outs)["verdict"] == "FAILED"


def test_the_model_report_counts_abstentions_in_coverage():
    opps, outs, ch, xf = _pairs_setup(10)
    xf[0] = {"opportunity_id": "o0000", "action": "ABSTAIN",
             "probability": None}
    rep = S.model_report({"registration_id": "X@1", "subject_id": "X",
                          "registered_at": T0 - 1, "status": "ACTIVE_FORWARD",
                          "document": {"role": "CHALLENGER"}}, xf, opps, outs)
    assert rep["offered"] == 10 and rep["forecast"] == 9
    assert rep["coverage"] == 0.9 and rep["abstained"] == 1
    assert rep["brier"] is not None and rep["entries"] == 0
    assert rep["realized_edge_mean"] is None
    assert rep["unmeasured"]["realized_edge_mean"] == "NO_RESOLVED_ENTRY"


# ── 2 · edge confidence ─────────────────────────────────────────────

def test_edge_confidence_is_trained_frozen_and_uncertain():
    rows = _rows(400, extra={"spread": 0.02})
    got = EC.train(rows, now=T0 + 10 ** 6)
    assert got["trainable"], got
    assert "spread" not in got["dropped"]
    assert "eddie_exec_uncertainty" in got["dropped"]
    doc = EC.document(got, version=1)
    assert doc["promotion_threshold"]["future_sizing_formula"][
        "status"] == "NOT_ACTIVE"
    hi = EC.forecast(doc, _opp(0.85, price=0.55, fee=0.01,
                               features=rows[3]["features"]))
    lo = EC.forecast(doc, _opp(0.15, price=0.55, fee=0.01,
                               features=rows[0]["features"]))
    assert 0 <= lo["edge_confidence"] < hi["edge_confidence"] <= 1
    assert hi["expected_net_edge_ci_low"] < hi["expected_net_edge"] < \
        hi["expected_net_edge_ci_high"]
    none = EC.forecast(doc, _opp(0.85, price=0.55, fee=None,
                                 features=rows[3]["features"]))
    assert none["edge_confidence"] is None
    assert none["unmeasured"]["edge_confidence"].startswith("NO_ALL_IN_COST")
    assert EC.train(rows[:50], now=T0 + 10 ** 6)["trainable"] is False


def test_edge_confidence_evaluation_is_null_until_measured():
    empty = EC.evaluate([])
    assert empty["brier_q"] is None and empty["n"] == 0
    assert "brier_q" in empty["unmeasured"]
    rows = [{"edge_confidence": 0.95, "probability": 0.7,
             "p_reference": 0.8, "price": 0.5, "fee": 0.01, "o": i % 2}
            for i in range(10)]
    got = EC.evaluate(rows)
    b = {x["bucket"]: x for x in got["by_edge_confidence_bucket"]}
    assert b["GE_0.9"]["n"] == 10
    assert b["LT_0.5"]["mean_realized_net_edge"] is None


# ── 3 · avoidance ───────────────────────────────────────────────────

def _avoid_rows():
    rows = []
    rng = random.Random(5)
    for i in range(400):
        sport = "soccer" if i % 2 else "baseball"
        p = 0.8
        price = 0.6
        # soccer entries lose far more often than predicted
        o = 1 if rng.random() < (0.3 if sport == "soccer" else 0.8) else 0
        rows.append({"p_reference": p, "price": price, "fee": 0.01, "o": o,
                     "sport": sport, "league": "UNKNOWN", "market": "h2h",
                     "live_state": "PREGAME", "outcome_at": T0 + i,
                     "decided_at": T0 + i - 100,
                     "features": {"probability_band": "0.8-1.0"}})
    return rows


def test_avoidance_learns_where_predicted_ev_failed():
    got = AV.train(_avoid_rows(), now=T0 + 10 ** 6, threshold_pp=0.5)
    assert got["trainable"]
    seg = got["params"]["segments"]["sport"]
    assert seg["soccer"]["flag"] == "AVOID"
    assert seg["baseball"]["flag"] == "OK"
    assert got["params"]["segments_tested"] >= 3
    doc = AV.document(got, version=1)
    bad = AV.forecast(doc, {"sport": "soccer", "league": "UNKNOWN",
                            "market": "h2h", "live_state": "PREGAME",
                            "features": {"probability_band": "0.8-1.0"}})
    assert bad["avoidance_level"] == "AVOID"
    assert any(r.startswith("sport=soccer: AVOID") for r in bad["reasons"])
    assert 0.9 < bad["avoidance_risk"] <= 1
    unknown = AV.forecast(doc, {"sport": "tennis", "league": "x",
                                "market": "spreads", "live_state": "LIVE",
                                "features": {"probability_band": "0.0-0.2",
                                             "regime": "Q",
                                             "settlement_family": "Z"}})
    assert unknown["avoidance_level"] in ("UNMEASURED", "NORMAL", "CAUTION")
    seg_vals = AV.segment_values({"features": {"price_move_1h": -0.05}})
    assert seg_vals["price_movement"] == "DOWN_LARGE"
    assert set(seg_vals) == set(AV.DIMENSIONS)


def test_avoidance_measurement_is_null_until_measurable():
    got = AV.evaluate([], threshold_pp=0.5)
    assert got["AVOID"]["precision"] is None
    assert got["AVOID"]["unmeasured"]["precision"] == "NO_ENTRY_WAS_BLOCKED"
    rows = [{"avoidance_level": "AVOID" if i < 4 else "NORMAL",
             "p_reference": 0.8, "price": 0.6, "fee": 0.01,
             "o": 0 if i in (0, 1, 2, 5) else 1} for i in range(10)]
    got = AV.evaluate(rows, threshold_pp=0.5)["AVOID"]
    assert got["blocked"] == 4 and got["good_trades_falsely_blocked"] == 1
    assert got["precision"] == 0.75 and got["recall"] == 0.75
    assert got["losses_avoided_per_contract"] == pytest.approx(3 * 0.61)
    assert got["incremental_economics_per_contract_total"] == pytest.approx(
        3 * 0.61 - 0.39)


# ── 4 · the agent tournament ────────────────────────────────────────

def test_the_variants_and_their_family():
    subs = AG.subjects()
    for a in ("XAVIER", "DEREK", "ALLOCATOR", "EDDIE"):
        for v in ("V1", "CHALLENGER_A", "CHALLENGER_B"):
            assert "%s_%s" % (a, v) in subs
    assert AG.FAMILY_SIZE == 8
    assert not any("MARCO" in s for s in subs)
    doc = AG.document("EDDIE_V1", version=1, threshold_pp=0.5,
                      threshold_basis="T", awaiting="AWAITING_INTERFACE")
    assert doc["placeholder"]["status"] == "AWAITING_INTERFACE"
    a = AG.document("DEREK_CHALLENGER_A", version=1, threshold_pp=0.5,
                    threshold_basis="T")
    assert a["parameters"]["threshold_pp"] == 1.0
    assert a["role"] == "CHALLENGER"


def test_derek_variants_and_the_shared_entry_set():
    v1 = AG.document("DEREK_V1", version=1, threshold_pp=0.5,
                     threshold_basis="T")
    b = AG.document("DEREK_CHALLENGER_B", version=1, threshold_pp=0.5,
                    threshold_basis="T")
    opp = _opp(0.7, price=0.6, fee=0.01)
    assert AG.derek(v1, opp)["action"] == "ENTER"
    assert AG.derek(b, opp, avoidance_level="AVOID")["action"] == "PASS"
    assert AG.derek(b, opp, avoidance_level="UNMEASURED")["action"] == \
        "ENTER"
    x = AG.document("XAVIER_CHALLENGER_A", version=1, threshold_pp=0.5,
                    threshold_basis="T")
    assert AG.xavier(x, _opp(0.5, price=0.6), threshold_pp=0.5)[
        "abstain_reason"] == "NOT_IN_THE_SHARED_ENTRY_SET"


def test_the_allocators_stay_inside_the_sleeve():
    batch = [dict(_opp(0.8, price=0.6, fee=0.01,
                       features={"liquidity_usd": 5000.0,
                                 "cal_half_width": 0.02}),
                  opportunity_id="o%d" % i) for i in range(40)]
    for subject in ("ALLOCATOR_V1", "ALLOCATOR_CHALLENGER_A",
                    "ALLOCATOR_CHALLENGER_B"):
        doc = AG.document(subject, version=1, threshold_pp=0.5,
                          threshold_basis="T")
        got = AG.allocate(doc, batch, ec_by_opp={"o1": 0.9},
                          threshold_pp=0.5)
        total = sum(v.get("shadow_usd") or 0 for v in got.values())
        assert total <= AG.SLEEVE_USD + 1e-6, subject
        assert all(v["action"] == "ALLOCATE" for v in got.values())
    b = AG.allocate(AG.document("ALLOCATOR_CHALLENGER_B", version=1,
                                threshold_pp=0.5, threshold_basis="T"),
                    batch, ec_by_opp={"o1": 0.9}, threshold_pp=0.5)
    assert b["o1"]["shadow_usd"] > 0 and b["o2"]["shadow_usd"] == 0
    assert b["o2"]["output"]["basis"] == \
        "EDGE_CONFIDENCE_UNAVAILABLE_DECLARED_ZERO"


def test_xavier_replays_the_recorded_plan_over_the_path():
    opp = _opp(0.8, price=0.5, fee=0.01)
    opp["features"]["holding_side"] = "LONG"
    path = {"books": [{"at": T0 + 10, "bids": [{"px": "0.55", "qty": "9"}],
                       "offers": [{"px": "0.57", "qty": "9"}]},
                      {"at": T0 + 20, "bids": [{"px": "0.62", "qty": "9"}],
                       "offers": [{"px": "0.64", "qty": "9"}]}],
            "quotes": [{"at": T0 + 15, "p": 0.45}]}
    v, at, obs = AG.xavier_exit("TAKE_PROFIT_PLAN", opp, path,
                                take_profit=0.10)
    assert v == 0.62 and at == T0 + 20 and obs
    v, at, _ = AG.xavier_exit("EXIT_ON_EDGE_LOSS_PLAN", opp, path,
                              take_profit=0.10)
    assert v == 0.55 and at == T0 + 15
    assert AG.xavier_exit("TAKE_PROFIT_PLAN", opp, {}, take_profit=0.1)[
        2] is False


def test_agent_metrics_and_the_paired_verdict():
    rows = [{"pnl": x, "capital": 0.5, "hours": 1.0, "fee": 0.01,
             "exec_loss": 0.01, "refused_good": False, "sport": "s",
             "at": T0 + i} for i, x in enumerate([1, -1, -1, 2, -3, 1])]
    s = AG.summarize(rows)
    assert s["net_economics_usd"] == -1 and s["max_drawdown_usd"] == 3
    assert s["turnover_usd"] == 3.0 and s["fees_usd"] == pytest.approx(0.06)
    assert AG.summarize([])["net_economics_usd"] is None
    pv = AG.paired_verdict([0.1] * 50 + [-5] * 50, min_sample=50,
                           family_size=8)
    assert pv["verdict"] in ("CRITERIA_MET", "NOT_MET")  # sd 0 -> lo = m
    assert AG.paired_verdict([0.1] * 10, min_sample=50,
                             family_size=8)["verdict"] == \
        "INSUFFICIENT_SAMPLE"


# ── 5 · experiments ─────────────────────────────────────────────────

def test_the_draw_is_seeded_and_matches_the_database_formula():
    d = EX.draw("seed1234", "e", "u")
    assert d == pytest.approx(0.7307971673308471, abs=1e-15)
    arms = [{"arm": "CONTROL", "weight": 0.5}, {"arm": "TREATMENT",
                                                "weight": 0.5}]
    assert EX.arm_for(0.49, arms) == "CONTROL"
    assert EX.arm_for(0.5, arms) == "TREATMENT"
    assert EX.draw("seed1234", "e", "u") == d


def test_the_design_is_complete_and_karen_blocks_a_bad_one():
    d = EX.design(EX.PLAN[0], policy_versions={"CONTROL": "DEREK_V1@1",
                                               "TREATMENT":
                                               "DEREK_CHALLENGER_A@1"},
                  now=T0)
    assert d["min_sample"] == 2 * EX.power_n_per_arm()
    assert d["start_at"] > T0 and d["stop_at"] > d["start_at"]
    assert d["stopping_rule"]["early_stop_for_efficacy"] is False
    ok = KAR.design_challenge(d, required_n_per_arm=EX.power_n_per_arm(),
                              declared_family_size=EX.FAMILY_SIZE)
    assert ok["outcome"] == "NOT_BLOCKED", ok
    bad = dict(d, min_sample=10, primary_metric={"name": "x"},
               failure_criteria={})
    got = KAR.design_challenge(bad, required_n_per_arm=EX.power_n_per_arm(),
                               declared_family_size=EX.FAMILY_SIZE,
                               other_seeds=[d["seed"]])
    assert got["outcome"] == "BLOCKED"
    assert {"MIN_SAMPLE_MEETS_POWER", "PRIMARY_METRIC_PREDECLARED",
            "FAILURE_CRITERIA", "SEED_NOT_SHARED"} <= set(got["blocked_by"])


def test_the_analysis_is_intention_to_treat():
    exp = {"alpha": 0.05, "family_size": 2, "min_sample": 4}
    asg = [{"unit_id": "u%d" % i, "arm": "CONTROL" if i % 2 else "TREATMENT"}
           for i in range(8)]
    outs = {"u%d" % i: {"realized_net_edge_per_unit": 0.0 if i % 2 else 0.3,
                        "entered": i % 2 == 0, "o": 1} for i in range(6)}
    got = EX.analyze(exp, asg, outs)
    assert got["assigned"] == 8
    assert got["missing_outcomes"] == {"CONTROL": 1, "TREATMENT": 1}
    assert got["arms"]["TREATMENT"]["n"] == 3
    assert got["difference"] == pytest.approx(0.3)


def test_audreys_randomization_audit_catches_a_tampered_arm():
    exp = {"experiment_id": "E", "seed": "seedseed1",
           "arms": [{"arm": "CONTROL", "weight": 0.5},
                    {"arm": "TREATMENT", "weight": 0.5}]}
    asg = []
    for i in range(200):
        d = EX.draw(exp["seed"], "E", "u%d" % i)
        asg.append({"unit_id": "u%d" % i, "draw": d,
                    "arm": EX.arm_for(d, exp["arms"])})
    cov = {a["unit_id"]: 0.5 for a in asg}
    assert AUD.randomization_audit(exp, asg, cov)["outcome"] == "PASS"
    asg[0] = dict(asg[0], arm="TREATMENT" if asg[0]["arm"] == "CONTROL"
                  else "CONTROL")
    got = AUD.randomization_audit(exp, asg, cov)
    assert got["outcome"] == "FAIL"
    assert got["findings"]["draw_or_arm_mismatches"] == ["u0"]


def test_karen_breaks_a_one_sport_or_one_half_result():
    good = [{"at": T0 + i, "diff": 0.05 + 0.01 * (i % 3), "sport":
             "a" if i % 2 else "b", "regime": "NORMAL"} for i in range(60)]
    doc = {"training_window": {"end": T0 - 10}}
    assert KAR.promotion_challenge(good, document=doc,
                                   registered_at=T0 - 1)["outcome"] == \
        "NOT_BLOCKED"
    one_sport = [dict(p, diff=(0.5 if p["sport"] == "a" else -0.1))
                 for p in good]
    got = KAR.promotion_challenge(one_sport, document=doc,
                                  registered_at=T0 - 1)
    assert "SPORT_LEAKAGE" in got["blocked_by"]
    halves = [dict(p, diff=(0.2 if i < 30 else -0.05))
              for i, p in enumerate(good)]
    got = KAR.promotion_challenge(halves, document=doc, registered_at=T0 - 1)
    assert "TIME_STABILITY" in got["blocked_by"]
    leak = KAR.promotion_challenge(good, document={"training_window": {
        "end": T0 + 999}}, registered_at=T0 - 1)
    assert "FORWARD_ONLY" in leak["blocked_by"]


# ── the feature contract ────────────────────────────────────────────

def test_every_brief_feature_is_listed_available_or_named_unavailable():
    names = {c[0] for c in FT.CATALOG}
    for want in ("PinnAPI probability", "Polymarket price", "gross edge",
                 "net edge", "spread", "depth", "liquidity", "time-to-event",
                 "live/pregame", "sport", "league", "probability band",
                 "PinnAPI volatility", "market-change frequency",
                 "execution latency", "cross-market disagreement",
                 "calibration history", "regime", "settlement confidence",
                 "Karen challenge state", "Scout feature state",
                 "historical edge reliability",
                 "EDDIE execution uncertainty", "price movement"):
        assert want in names, want
    status = {s["feature"]: s for s in FT.catalog_status([])}
    assert status["Scout feature state"]["status"] == "UNAVAILABLE"
    assert status["spread"]["status"] == "UNMEASURED"
    assert status["spread"]["available_share"] is None


def test_build_names_what_is_missing_and_stores_no_outcome():
    row = {"probability": 0.6, "executable_price": None,
           "calibration_only_evidence": {"compared_at_the_displayed_price": {
               "price": 0.55, "cost_per_contract": 0.01}},
           "buy_intent": "ORDER_INTENT_BUY_LONG", "sport_family": "baseball",
           "market": "h2h", "age_s": 3.0, "outcome": 1,
           "outcome_known": True}
    f, un, meta = FT.build(row, t=T0, quotes=[(T0 - 100, 0.58),
                                               (T0 + 100, 0.99)])
    assert meta["price_basis"] == "DISPLAYED_NOT_EXECUTABLE"
    assert f["net_edge"] == pytest.approx(0.04)
    assert f["price_move_1h"] == pytest.approx(0.02)   # the later quote
    assert un["spread"].startswith("NO_BOOK_OBSERVATION")
    assert un["scout_feature_state"].startswith("UNAVAILABLE")
    assert un["eddie_exec_uncertainty"] == "EDDIE_INTERFACE_ABSENT"
    for k in FT.FORBIDDEN_KEYS:
        assert k not in f
    assert not any(k in f for k in ("outcome", "outcome_known"))


def test_finite_helper():
    assert AG.finite(1.0) and not AG.finite(math.inf)
