"""CAPITAL-CRITICAL (SHADOW): THE CALIBRATION ENGINE SCORES CORRECTLY, SAYS
NULL WHEN IT CANNOT, AND NEVER LETS AN OVERLAY TOUCH PRODUCTION.

  §1 the numbers: Brier, log loss, Wilson interval and bootstrap interval
     against hand-computed values; deterministic
  §2 nothing measured -> null with a reason, never 0 (also in the DB CHECK)
  §3 the independent unit and the outcome join (valuation provenance,
     paper settlement, voids and exceptional settlements counted not scored)
  §4 segments: sport, league, market, probability band, live state,
     time-to-start, liquidity band
  §5 the frozen forward OOS protocol: a fit recovers a known
     miscalibration; NOT_VALIDATED below the forward sample; VALIDATED /
     FAILED decided only on post-freeze predictions; the register is
     immutable and can never be production-applied
  §6 the read over synthetic rows (rolled back)
"""
from __future__ import annotations

import math
import random
import time

import asyncpg
import pytest

from sportsassets import bettor_source_calibration as SC
from sportsassets.intel import calibration as CAL
from sportsassets.intel import store as ST

try:
    from tests import intel_fixture as F
    from tests import paper_harness as H
except ImportError:                                             # pragma: no cover
    import intel_fixture as F
    import paper_harness as H

pg = pytest.mark.skipif(not H.DSN, reason="needs RN1X_TEST_DSN")


# ── §1 the numbers ───────────────────────────────────────────────────

def test_brier_and_log_loss_match_hand_computation():
    pairs = [(0.8, 1), (0.3, 0), (0.6, 0)]
    assert SC.brier(pairs) == pytest.approx((0.04 + 0.09 + 0.36) / 3)
    expect = -(math.log(0.8) + math.log(0.7) + math.log(0.4)) / 3
    assert SC.log_loss(pairs) == pytest.approx(expect)
    assert SC.log_loss(pairs) == pytest.approx(0.498703, abs=1e-6)
    # a confident miss is a large finite penalty, not infinity
    assert SC.log_loss([(1.0, 0)]) == pytest.approx(-math.log(1e-6))


def test_wilson_interval_matches_the_textbook_values():
    lo, hi = SC.wilson_interval(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)
    lo, hi = SC.wilson_interval(0, 10)
    assert lo == 0.0 and round(hi, 4) == 0.2775
    lo, hi = SC.wilson_interval(10, 10)
    assert round(lo, 4) == 0.7225 and hi == pytest.approx(1.0)
    assert SC.wilson_interval(0, 0) == (None, None)


def test_bootstrap_is_deterministic_and_brackets_the_estimate():
    rng = random.Random(7)
    pairs = [(p, int(rng.random() < p)) for p in
             (rng.uniform(0.1, 0.9) for _ in range(200))]
    a = SC.bootstrap_ci(pairs, SC.brier)
    b = SC.bootstrap_ci(pairs, SC.brier)
    assert a == b
    assert a[0] < SC.brier(pairs) < a[1]
    assert SC.bootstrap_ci(pairs[:1], SC.brier) == (None, None)


def test_score_reports_point_estimates_and_intervals():
    pairs = [(0.7, 1)] * 7 + [(0.7, 0)] * 3
    s = CAL.score(pairs)
    assert s["n"] == 10
    assert s["brier"] == pytest.approx(0.21)
    assert s["observed_frequency"] == pytest.approx(0.7)
    assert s["mean_probability"] == pytest.approx(0.7)
    assert s["frequency_ci_low"] < 0.7 < s["frequency_ci_high"]
    assert s["brier_ci_low"] <= s["brier"] <= s["brier_ci_high"]
    assert s["small_sample"] is True
    assert s["reliability"]["table"][0]["gap"] == pytest.approx(0.0)
    assert s["ci_method"].startswith("PERCENTILE_BOOTSTRAP")


def test_a_large_segment_uses_the_bounded_normal_interval():
    pairs = [(0.6, i % 2) for i in range(5000)]
    s = CAL.score(pairs)
    assert s["ci_method"].startswith("NORMAL_ON_PER_EVENT_LOSSES")
    assert s["brier_ci_low"] < s["brier"] < s["brier_ci_high"]


# ── §2 null, never zero ──────────────────────────────────────────────

def test_an_empty_segment_is_null_with_a_reason_never_zero():
    s = CAL.score([])
    for k in ("brier", "log_loss", "observed_frequency", "brier_ci_low",
              "frequency_ci_low", "reliability"):
        assert s[k] is None, k
        assert s["unmeasured"][k] == "NO_RESOLVED_PREDICTIONS_IN_SEGMENT"
    one = CAL.score([(0.6, 1)])
    assert one["brier"] == pytest.approx(0.16)
    assert one["brier_ci_low"] is None
    assert one["unmeasured"]["brier_ci_low"] == (
        "ONE_OBSERVATION_HAS_NO_SAMPLING_DISTRIBUTION")


def test_drift_is_null_when_a_side_is_empty():
    now = 2_000_000_000.0
    recs = [CAL._record(source="VALUATION_PROBABILITY", rid=i, unit="u%d" % i,
                        p=0.6, outcome=1, cls=SC.RESOLVED, basis="x",
                        predicted_at=now - 3600, sport="s", league="l",
                        market="m", game_start=None, liquidity_usd=None)
            for i in range(5)]
    d = CAL.drift(recs, now=now)["VALUATION_PROBABILITY"]
    assert d["recent_brier"] == pytest.approx(0.16)
    assert d["baseline_brier"] is None and d["brier_change"] is None
    assert d["unmeasured"]["brier_change"] == (
        "ONE_SIDE_OF_THE_COMPARISON_IS_EMPTY")


# ── §3 the unit and the outcome join ─────────────────────────────────

def _val(i, ek, p, at, outcome=None, basis="VENUE_SETTLEMENT_PRICE"):
    return {"id": i, "event_key": ek, "probability": p,
            "observed_at_epoch": at, "outcome_known": outcome is not None,
            "outcome": outcome, "outcome_basis": basis if outcome is not None
            else None, "buy_intent": "ORDER_INTENT_BUY_LONG",
            "sport_family": "baseball", "market": "h2h"}


def test_one_prediction_per_fixture_the_first_priced_one():
    rows = [_val(1, "A", None, 100.0), _val(2, "A", 0.55, 200.0, 1),
            _val(3, "A", 0.70, 300.0, 1), _val(4, "B", 0.40, 50.0, 0)]
    recs = CAL.independent([CAL.normalize_valuation(r) for r in rows])
    got = {r["unit"]: (r["id"], r["p"]) for r in recs}
    assert got == {"A": (2, 0.55), "B": (4, 0.40)}


def test_unverified_provenance_is_not_scored():
    r = CAL.normalize_valuation(_val(1, "A", 0.6, 1.0, 1, basis=None))
    assert r["class"] == SC.UNVERIFIED and r["outcome"] is None
    assert CAL.pairs_of([r]) == []


def test_decision_outcomes_from_valuation_then_settlement():
    dec = {"decision_id": "d", "p_pinnacle": 0.6, "decided_at": 1.0,
           "label": {"event_key": "E"}}
    verified = _val(1, "E", 0.6, 1.0, 0)
    assert CAL.decision_outcome(dec, verified, {"outcome": "WON"})[:2] == (
        0, SC.RESOLVED)
    for outcome, want in (("WON", (1, SC.RESOLVED)), ("LOST", (0, SC.RESOLVED)),
                          ("VOID_REFUND", (None, SC.VOID)),
                          ("SETTLED_AT_VENUE_PRICE",
                           (None, CAL.EXCEPTIONAL))):
        got = CAL.decision_outcome(dec, None, {"outcome": outcome,
                                               "evidence_source": "X"})
        assert got[:2] == want, outcome
    assert CAL.decision_outcome(dec, None, None)[1] == SC.UNRESOLVED


# ── §4 segments ──────────────────────────────────────────────────────

def test_segments_cover_every_dimension_with_sample_sizes():
    gs = 1_000_000.0
    pm = {"team_league": "MLB", "sports_type": "baseball", "game_start": gs}
    rows = [dict(_val(i, "F%d" % i, 0.15 + 0.07 * i, gs - 7200 + 1200 * i,
                      i % 2), execution_estimate={"depth_usd": 50 * 10 ** (
                          i % 4)}) for i in range(10)]
    recs = CAL.independent([CAL.normalize_valuation(r, pm) for r in rows])
    segs = CAL.segments(recs)
    kinds = {s["segment_kind"] for s in segs
             if s["source"] == "VALUATION_PROBABILITY"}
    assert kinds == set(CAL.SEGMENT_KINDS)
    by = {(s["segment_kind"], s["segment_value"]): s for s in segs
          if s["source"] == "VALUATION_PROBABILITY"}
    assert by[("ALL", "ALL")]["n"] == 10
    assert by[("league", "MLB")]["n"] == 10
    assert by[("live_state", "LIVE")]["n"] + by[("live_state", "PREGAME")][
        "n"] == 10
    assert ("time_to_start", "PRE_1H_6H") in by
    assert {k[1] for k in by if k[0] == "liquidity_band"} >= {
        "LT_100", "100_1K", "1K_10K", "GE_10K"}
    dec_all = [s for s in segs if s["source"] == "DECISION_P_PINNACLE"
               and s["segment_kind"] == "ALL"][0]
    assert dec_all["n"] == 0 and dec_all["brier"] is None


def test_uncertainty_for_reads_the_probability_band():
    recs = [CAL._record(source="DECISION_P_PINNACLE", rid=i, unit="u%d" % i,
                        p=0.65, outcome=int(i < 6), cls=SC.RESOLVED,
                        basis="b", predicted_at=1.0, sport="s", league="l",
                        market="m", game_start=None, liquidity_usd=None)
            for i in range(10)]
    rep = CAL.report(recs, now=10.0)
    u = CAL.uncertainty_for(rep, p=0.62)
    lo, hi = SC.wilson_interval(6, 10)
    assert u["n"] == 10 and u["band"] == "0.6-0.8"
    assert u["half_width"] == pytest.approx((hi - lo) / 2, abs=1e-6)
    assert u["miscalibration"] == pytest.approx(0.05)
    none = CAL.uncertainty_for(rep, p=0.1)
    assert none["half_width"] is None and none["n"] == 0
    assert "PROBABILITY_BAND" in none["unmeasured"]["half_width"]


# ── §5 the overlay protocol ──────────────────────────────────────────

def _miscalibrated(n, seed, a=0.4, b=0.6):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p = rng.uniform(0.05, 0.95)
        q = CAL.apply_overlay({"a": a, "b": b}, p)
        out.append((p, int(rng.random() < q)))
    return out


def test_a_fit_recovers_a_known_miscalibration():
    params = CAL.fit_overlay(_miscalibrated(4000, 1))
    assert params["a"] == pytest.approx(0.4, abs=0.15)
    assert params["b"] == pytest.approx(0.6, abs=0.12)
    assert CAL.fit_overlay(_miscalibrated(CAL.MIN_FIT_N - 1, 2)) is None


def test_oos_status_is_not_validated_below_the_forward_sample():
    params = {"a": 0.4, "b": 0.6}
    r = CAL.oos_test(params, _miscalibrated(CAL.MIN_OOS_N - 1, 3))
    assert r["status"] == "NOT_VALIDATED"
    assert "BELOW" in r["why"]
    assert CAL.oos_test(params, [])["status"] == "NOT_VALIDATED"


def test_oos_validates_a_true_correction_and_fails_a_harmful_one():
    good = CAL.oos_test({"a": 0.4, "b": 0.6}, _miscalibrated(1500, 4))
    assert good["status"] == "VALIDATED_SHADOW_ONLY"
    assert good["log_loss_improvement_ci_low"] > 0
    assert good["production_probabilities_modified"] is False
    calibrated = _miscalibrated(1500, 5, a=0.0, b=1.0)
    bad = CAL.oos_test({"a": 1.0, "b": 2.0}, calibrated)
    assert bad["status"] == "FAILED_OOS"


def test_the_plan_tests_only_predictions_made_after_the_freeze():
    frozen = 5000.0
    pairs = _miscalibrated(600, 6)
    recs = []
    for i, (p, o) in enumerate(pairs):
        at = frozen - 100 + i          # the first 100 predate the freeze
        recs.append(CAL._record(source="VALUATION_PROBABILITY", rid=i,
                                unit="u%d" % i, p=p, outcome=o,
                                cls=SC.RESOLVED, basis="b", predicted_at=at,
                                sport="s", league="l", market="m",
                                game_start=None, liquidity_usd=None))
    ov = {"overlay_id": "o1", "source": "VALUATION_PROBABILITY",
          "status": "NOT_VALIDATED", "frozen_at": frozen,
          "params": {"a": 0.4, "b": 0.6}}
    acts = CAL.overlay_plan(recs, [ov], now=frozen + 10_000)
    ev = [a for a in acts if a["op"] == "EVALUATE"]
    assert len(ev) == 1 and ev[0]["result"]["oos_n"] == 499
    # no new FIT for a source with a pending overlay
    assert not [a for a in acts if a["op"] == "FIT"
                and a["source"] == "VALUATION_PROBABILITY"]


def test_with_no_overlay_the_plan_fits_once_enough_resolved_exist():
    recs = [CAL._record(source="DECISION_P_PINNACLE", rid=i, unit="u%d" % i,
                        p=p, outcome=o, cls=SC.RESOLVED, basis="b",
                        predicted_at=float(i), sport="s", league="l",
                        market="m", game_start=None, liquidity_usd=None)
            for i, (p, o) in enumerate(_miscalibrated(300, 8))]
    acts = CAL.overlay_plan(recs, [], now=1000.0)
    fit = [a for a in acts if a["op"] == "FIT"]
    assert len(fit) == 1 and fit[0]["fit_n"] == 300
    assert fit[0]["fit_window_end"] <= 1000.0
    assert CAL.PROTOCOL["production_probabilities_modified"] is False


@pg
async def test_the_overlay_register_is_frozen_and_never_applied():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        acts = [{"op": "FIT", "source": "DECISION_P_PINNACLE",
                 "params": {"a": 0.1, "b": 0.9}, "fit_n": 150,
                 "fit_window_end": now - 10}]
        await ST.apply_overlay_actions(conn, acts, now=now,
                                       protocol=CAL.PROTOCOL,
                                       method=CAL.OVERLAY_METHOD)
        oid = acts[0]["overlay_id"]
        row = await conn.fetchrow(
            "SELECT status, production_applied FROM "
            " intel_calibration_overlays WHERE overlay_id=$1", oid)
        assert row["status"] == "NOT_VALIDATED"
        assert row["production_applied"] is False
        for sql in (
                "UPDATE intel_calibration_overlays SET params='{\"a\":0,"
                "\"b\":1}' WHERE overlay_id=$1",
                "UPDATE intel_calibration_overlays SET frozen_at=now() + "
                "interval '1 day' WHERE overlay_id=$1",
                "UPDATE intel_calibration_overlays SET production_applied="
                "true WHERE overlay_id=$1",
                "DELETE FROM intel_calibration_overlays WHERE overlay_id=$1"):
            sp = conn.transaction()
            await sp.start()
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute(sql, oid)
            await sp.rollback()
        await ST.apply_overlay_actions(
            conn, [{"op": "EVALUATE", "overlay_id": oid,
                    "result": {"status": "FAILED_OOS", "oos_n": 250}}],
            now=now + 1, protocol=CAL.PROTOCOL, method=CAL.OVERLAY_METHOD)
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute(
                "UPDATE intel_calibration_overlays SET status="
                "'VALIDATED_SHADOW_ONLY' WHERE overlay_id=$1", oid)
        await sp.rollback()
        # the empty-segment CHECK: a segment with n = 0 cannot carry a 0
        sp = conn.transaction()
        await sp.start()
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO intel_calibration_segments (run_id, "
                " computed_at, source, segment_kind, segment_value, n, brier)"
                " VALUES ('r', now(), 'DECISION_P_PINNACLE', 'ALL', 'ALL', 0,"
                " 0)")
        await sp.rollback()
    finally:
        await tr.rollback()
        await conn.close()


# ── §6 the read ──────────────────────────────────────────────────────

@pg
async def test_the_read_joins_valuations_and_decisions_to_outcomes():
    conn = await asyncpg.connect(H.DSN)
    tr = conn.transaction()
    await tr.start()
    try:
        now = time.time()
        exp = F.uid("INTEL_TEST_EXP_")
        await F.valuation(conn, experiment_id=exp, at=now - 7200, p=0.7,
                          outcome=1, event_key="EV1")
        await F.valuation(conn, experiment_id=exp, at=now - 3600, p=0.8,
                          outcome=1, event_key="EV1")   # same fixture, later
        await F.valuation(conn, experiment_id=exp, at=now - 7000, p=0.3,
                          outcome=0, event_key="EV2")
        await F.valuation(conn, experiment_id=exp, at=now - 7000, p=0.5,
                          event_key="EV3")              # unresolved
        acct = await H.new_account(conn, "intelcal", now=now - 86400)
        d = await F.decision(conn, acct, at=now - 5000, p=0.6)
        g = await F.position(conn, acct, slug=d["slug"], qty=100, price=0.5,
                             at=now - 4990, decision_id=d["decision_id"],
                             event_key=d["event_key"])
        await F.settle(conn, acct, group_id=g, slug=d["slug"], qty=100,
                       outcome="LOST", payout_per_contract=0.0, at=now - 100)
        await F.decision(conn, acct, at=now - 4000, p=0.4, verdict="REFUSE")
        recs = await CAL.load_records(conn, now=now,
                                      account_id=acct["account_id"],
                                      experiment_id=exp)
        ind = CAL.independent(recs)
        vals = {r["unit"]: r for r in ind
                if r["source"] == "VALUATION_PROBABILITY"}
        assert vals["EV1"]["p"] == 0.7 and vals["EV1"]["outcome"] == 1
        assert vals["EV3"]["class"] == SC.UNRESOLVED
        decs = [r for r in ind if r["source"] == "DECISION_P_PINNACLE"]
        assert len(decs) == 2
        lost = [r for r in decs if r["id"] == d["decision_id"]][0]
        assert lost["outcome"] == 0 and lost["class"] == SC.RESOLVED
        assert lost["basis"].startswith("PAPER_SETTLEMENT")
        rep = CAL.report(ind, now=now)
        v = rep["overall"]["VALUATION_PROBABILITY"]
        assert v["n"] == 2
        assert v["brier"] == pytest.approx(((0.3) ** 2 + (0.3) ** 2) / 2)
        assert rep["overall"]["DECISION_P_PINNACLE"]["n"] == 1
        assert rep["production_probabilities_modified"] is False
        assert rep["label"] == "SHADOW"
    finally:
        await tr.rollback()
        await conn.close()
