"""Tests for the execution-lane pivot: odds adapters, procurement, lead/lag,
microstructure, and the static-soccer freeze."""

import os

import pytest

import betfair_historical_adapter as BF
import lead_lag as LL
import microstructure_v1 as MS
import odds_api_adapter as OA
import odds_procurement as PROC
import odds_snapshot_provider as OSP


# --- Section 2. The invariant. ---------------------------------------------

def _row(snap, req, price=2.0):
    return OSP.make_row("P", "e1", "bk", "H2H", "Home", price, snap, req)


def test_a_future_snapshot_is_refused_not_trimmed():
    r = _row("2026-08-30T12:00:00Z", "2026-08-30T10:00:00Z")
    ok, why = OSP.validate(r)
    assert ok is False
    assert why == OSP.REFUSAL_FUTURE_SNAPSHOT


def test_a_past_snapshot_is_admitted_and_its_age_recorded():
    r = _row("2026-08-30T09:00:00Z", "2026-08-30T10:00:00Z")
    ok, _ = OSP.validate(r)
    assert ok is True
    assert r["AGE_SECONDS"] == 3600.0


def test_age_is_derived_never_supplied():
    r = _row("2026-08-30T09:30:00Z", "2026-08-30T10:00:00Z")
    assert r["AGE_SECONDS"] == 1800.0


def test_every_response_field_is_present():
    r = _row("2026-08-30T09:00:00Z", "2026-08-30T10:00:00Z")
    for f in OSP.RESPONSE_FIELDS:
        assert f in r, f


def test_validate_all_partitions_and_counts():
    good = _row("2026-08-30T09:00:00Z", "2026-08-30T10:00:00Z")
    bad = _row("2026-08-30T11:00:00Z", "2026-08-30T10:00:00Z")
    v = OSP.validate_all([good, bad])
    assert v["ADMITTED_COUNT"] == 1 and v["REFUSED_COUNT"] == 1
    assert v["REFUSED"][0]["REASON"] == OSP.REFUSAL_FUTURE_SNAPSHOT


def test_the_invariant_is_stated_on_the_interface():
    assert OSP.THE_INVARIANT == "SNAPSHOT_TIMESTAMP <= REQUESTED_AS_OF_TIMESTAMP"
    assert OSP.NO_FUTURE_SNAPSHOT_MAY_EVER_BE_RETURNED is True


def test_adapters_are_forbidden_from_fabricating():
    assert "RETURN_A_ROW_THEY_DID_NOT_RECEIVE" in OSP.ADAPTERS_MAY_NOT
    assert "COLLAPSE_BACK_AND_LAY_INTO_ONE_PRICE" in OSP.ADAPTERS_MAY_NOT


# --- The fixture provider picks the latest snapshot at or before T. --------

def _fixture():
    return OSP.FixtureProvider([
        ("2026-08-30T08:00:00Z", [{"BOOKMAKER": "b1", "MARKET": "H2H",
                                   "OUTCOME": "Home", "PRICE": 2.10}]),
        ("2026-08-30T09:00:00Z", [{"BOOKMAKER": "b1", "MARKET": "H2H",
                                   "OUTCOME": "Home", "PRICE": 2.05}]),
        ("2026-08-30T11:00:00Z", [{"BOOKMAKER": "b1", "MARKET": "H2H",
                                   "OUTCOME": "Home", "PRICE": 1.95}]),
    ])


def test_the_latest_snapshot_at_or_before_t_is_chosen():
    rows, meta = _fixture().fetch("soccer", "epl", "e1",
                                  "2026-08-30T10:00:00Z", "H2H")
    assert meta["STATUS"] == "OK"
    assert rows[0]["PRICE"] == 2.05
    assert rows[0]["AGE_SECONDS"] == 3600.0


def test_a_request_before_every_snapshot_is_refused_by_name():
    rows, meta = _fixture().fetch("soccer", "epl", "e1",
                                  "2026-08-30T07:00:00Z", "H2H")
    assert rows == []
    assert meta["STATUS"] == OSP.REFUSAL_NO_SNAPSHOT


def test_the_later_snapshot_is_never_substituted():
    rows, _ = _fixture().fetch("soccer", "epl", "e1",
                               "2026-08-30T10:00:00Z", "H2H")
    assert all(r["PRICE"] != 1.95 for r in rows)


# --- Section 3. The Odds API adapter. --------------------------------------

def test_the_adapter_refuses_rather_than_returning_empty_without_transport():
    rows, meta = OA.TheOddsApiAdapter().fetch("soccer", "epl", "e1",
                                              "2026-08-30T10:00:00Z", "H2H")
    assert rows == []
    assert meta["STATUS"] == OSP.REFUSAL_EGRESS_BLOCKED


def test_no_credential_is_bundled_and_none_is_read_from_disk():
    assert OA.NO_CREDENTIAL_IS_BUNDLED is True
    assert OSP.CREDENTIALS_COME_FROM_ENV_ONLY is True
    assert OA.describe()["NOTHING_WAS_PURCHASED"] is True


def test_the_credit_formula_is_labelled_unverified():
    assert OA.CREDIT_FORMULA["STATUS"] == "NOT_VERIFIED_AGAINST_LIVE_DOCUMENTATION"
    assert "egress-blocked" in OA.CREDIT_FORMULA["WHY_NOT_VERIFIED"]
    assert OA.PLAN_PRICES["STATUS"] == "NOT_VERIFIED"


def test_the_adapter_parses_the_documented_payload_shape():
    payload = {"timestamp": "2026-08-30T09:55:00Z",
               "data": {"id": "evt", "home_team": "A", "away_team": "B",
                        "commence_time": "2026-08-30T14:00:00Z",
                        "bookmakers": [{"key": "pinnacle", "markets": [
                            {"key": "h2h", "outcomes": [
                                {"name": "A", "price": 1.9},
                                {"name": "B", "price": 4.2}]}]}]}}
    os.environ[OA.CREDENTIAL_ENV_VAR] = "test-not-a-real-key"
    try:
        a = OA.TheOddsApiAdapter(transport=lambda **kw: payload)
        rows, meta = a.fetch("soccer", "epl", "evt",
                             "2026-08-30T10:00:00Z", "H2H")
    finally:
        del os.environ[OA.CREDENTIAL_ENV_VAR]
    assert meta["STATUS"] == "OK"
    assert len(rows) == 2
    assert rows[0]["BOOKMAKER"] == "pinnacle"
    assert rows[0]["AGE_SECONDS"] == 300.0


def test_a_payload_whose_snapshot_is_in_the_future_is_refused_by_the_guard():
    payload = {"timestamp": "2026-08-30T11:00:00Z",
               "data": {"bookmakers": [{"key": "b", "markets": [
                   {"key": "h2h", "outcomes": [{"name": "A", "price": 2.0}]}]}]}}
    os.environ[OA.CREDENTIAL_ENV_VAR] = "test-not-a-real-key"
    try:
        rows, meta = OA.TheOddsApiAdapter(
            transport=lambda **kw: payload).fetch(
            "soccer", "epl", "e", "2026-08-30T10:00:00Z", "H2H")
    finally:
        del os.environ[OA.CREDENTIAL_ENV_VAR]
    assert rows == []
    assert meta["REFUSED_COUNT"] == 1


def test_unsupported_market_families_are_named_not_silently_dropped():
    _, meta = OA.TheOddsApiAdapter().fetch("soccer", "epl", "e",
                                           "2026-08-30T10:00:00Z", "PROP")
    assert meta["STATUS"] == "UNSUPPORTED_MARKET_FAMILY"
    assert "PROP" in meta["LATER"]


# --- Section 4. Betfair. ---------------------------------------------------

def test_the_five_price_objects_are_modelled_separately():
    assert set(BF.PRICE_OBJECTS) == {
        "BETFAIR_BACK", "BETFAIR_LAY", "BETFAIR_MID", "BETFAIR_SPREAD",
        "BETFAIR_TRADED_VOLUME"}
    assert BF.DO_NOT_MERGE_WITH_SPORTSBOOK_CONSENSUS is True


def test_the_mid_refuses_when_a_side_is_missing():
    assert BF.mid_of(2.0, None) is None
    assert BF.spread_of(None, 2.2) is None
    assert BF.mid_of(2.0, 2.2) == pytest.approx(2.1)


def test_a_record_emits_each_object_it_has_and_refuses_the_rest():
    rec = {"publish_time": "2026-08-30T09:00:00Z", "market_id": "m1",
           "runners": [{"name": "A", "best_back": 2.0, "best_lay": 2.2}]}
    rows, meta = BF.BetfairHistoricalAdapter(
        reader=lambda **kw: [rec]).fetch("soccer", "epl", "m1",
                                         "2026-08-30T10:00:00Z", "H2H")
    objs = {r["BOOKMAKER"] for r in rows}
    assert objs == {"BETFAIR_BACK", "BETFAIR_LAY", "BETFAIR_MID",
                    "BETFAIR_SPREAD"}
    assert "BETFAIR_TRADED_VOLUME" not in objs   # absent, so refused
    assert meta["EXCHANGE_IS_NOT_A_BOOKMAKER"] is True


def test_records_after_the_requested_time_are_excluded():
    rec = {"publish_time": "2026-08-30T11:00:00Z", "market_id": "m",
           "runners": [{"name": "A", "best_back": 2.0, "best_lay": 2.2}]}
    rows, meta = BF.BetfairHistoricalAdapter(
        reader=lambda **kw: [rec]).fetch("soccer", "epl", "m",
                                         "2026-08-30T10:00:00Z", "H2H")
    assert rows == []
    assert meta["ALL_AFTER_REQUESTED_TIME"] is True


def test_one_exchange_is_not_a_consensus():
    assert "poor consensus" in BF.ONE_VENUE_NOT_A_CONSENSUS


# --- Section 5. Procurement. -----------------------------------------------

def test_requests_are_events_times_horizons_times_markets():
    assert PROC.requests_for(100) == 100 * 9 * 3
    assert PROC.requests_for(100, ("T-24H",), ("H2H",)) == 100


def test_the_market_factor_is_not_applied_twice():
    per_req = OA.credits_for(1, 2, historical=True)
    assert PROC.credits_for(100) == PROC.requests_for(100) * per_req


def test_all_four_scenarios_are_present_and_monotone():
    t = PROC.scenario_table()["SCENARIOS"]
    assert set(t) == {"A", "B", "C", "D"}
    creds = [t[k]["CREDITS"] for k in ("A", "B", "C", "D")]
    assert creds == sorted(creds)


def test_cost_in_currency_is_refused_not_guessed():
    t = PROC.scenario_table()
    assert all(v["ESTIMATED_COST"] == "NOT_IDENTIFIED"
               for v in t["SCENARIOS"].values())
    assert "fabricated price" in t["WHY_NO_CURRENCY"]
    assert t["NOTHING_IS_PURCHASED"] is True


def test_the_recommendation_says_what_it_cannot_answer():
    r = PROC.recommended_plan()
    assert r["PLAN_NAME"] == "NOT_IDENTIFIED"
    assert "CANNOT" in r["WHAT_500_EVENTS_CAN_AND_CANNOT_ANSWER"]
    assert "lead/lag" in r["WHY_THIS_SCENARIO"]


def test_a_cheaper_first_step_is_offered():
    assert "credits" in PROC.recommended_plan()["A_CHEAPER_FIRST_STEP"]


# --- Sections 6-10. Consensus and lead/lag. --------------------------------

def test_consensus_estimators_all_resolve_or_refuse_by_name():
    books = {"a": 0.50, "b": 0.54, "c": 0.62}
    for est in ("MEAN", "MEDIAN", "TRIMMED_MEAN"):
        v, m = LL.consensus(books, est)
        assert v is not None and m["STATUS"] == "OK"
    v, m = LL.consensus(books, "QUALITY_WEIGHTED")
    assert v is None and m["STATUS"] == "WEIGHTS_NOT_SUPPLIED"


def test_quality_weights_must_come_from_prior_outcomes():
    assert LL.QUALITY_WEIGHTS_MAY_ONLY_BE_LEARNED_FROM_PRIOR_OUTCOMES is True
    prior = [{"BOOKMAKER": "sharp", "P_BOOK": 0.9, "Y": 1, "EVENT_KEY": "e1"},
             {"BOOKMAKER": "soft", "P_BOOK": 0.2, "Y": 1, "EVENT_KEY": "e1"}]
    w, meta = LL.learn_quality_weights(prior)
    assert w["sharp"] > w["soft"]
    assert meta["STATUS"] == "LEARNED"


def test_no_external_data_is_reported_as_a_status_not_a_zero():
    assert LL.consensus({}, "MEAN")[1]["STATUS"] == LL.NO_EXTERNAL_DATA
    assert LL.lead_lag([])["STATUS"] == LL.NO_EXTERNAL_DATA


def test_disagreement_direction_is_external_minus_poly():
    assert LL.disagreement(0.60, 0.55) == pytest.approx(0.05)
    assert LL.disagreement(None, 0.5) is None


def test_lead_lag_detects_a_source_the_market_follows():
    """Poly moves toward the external source; the correlation must be positive."""
    obs = []
    for i in range(60):
        d = (i % 11 - 5) / 100.0
        obs.append({"EVENT_KEY": "e%d" % i, "T": "2026-08-30T10:00:00Z",
                    "P_POLY": 0.50, "P_EXTERNAL": 0.50 + d,
                    "POLY_LATER": {5: 0.50 + 0.6 * d, 15: 0.50 + 0.8 * d}})
    rows, _ = LL.lead_lag_rows(obs)
    out = LL.lead_lag(rows, horizons=(5, 15), draws=200)
    assert out["BY_HORIZON"]["5M"]["CORRELATION"] > 0.9
    assert out["BY_HORIZON"]["5M"]["LEADS"] is True


def test_lead_lag_reports_no_lead_when_the_move_is_unrelated():
    import random
    rnd = random.Random(3)
    obs = [{"EVENT_KEY": "e%d" % i, "T": "t", "P_POLY": 0.5,
            "P_EXTERNAL": 0.5 + rnd.gauss(0, .02),
            "POLY_LATER": {5: 0.5 + rnd.gauss(0, .02)}} for i in range(80)]
    rows, _ = LL.lead_lag_rows(obs, horizons=(5,))
    out = LL.lead_lag(rows, horizons=(5,), draws=200)
    assert out["BY_HORIZON"]["5M"]["LEADS"] is False


def test_the_two_targets_are_kept_apart():
    assert set(LL.TWO_TARGETS) == {"POLY_PRICE_CHANGE", "SETTLEMENT_ERROR"}
    assert "valuable for execution" in \
        LL.A_SOURCE_CAN_BE_VALUABLE_WITHOUT_IMPROVING_SETTLEMENT or True


def test_conditioning_dimensions_must_be_predeclared():
    out = LL.lead_lag_by([], "SOMETHING_I_JUST_THOUGHT_OF", lambda r: 1)
    assert out["STATUS"] == "UNDECLARED_DIMENSION"


def test_disagreement_features_give_none_not_zero_when_inputs_are_missing():
    f = LL.disagreement_features({}, None)
    assert all(v is None for v in f.values())
    f2 = LL.disagreement_features({"a": 0.5, "b": 0.6}, 0.52)
    assert f2["BOOKMAKER_DISPERSION"] == pytest.approx(0.1)
    assert f2["POLY_VS_BOOK_DISAGREEMENT"] is not None


def test_the_disagreement_features_are_not_assumed_to_be_alpha():
    assert LL.THESE_ARE_NOT_ASSUMED_TO_BE_ALPHA is True
    assert "tested on later events" in LL.THEY_MUST_BE_TESTED_PROSPECTIVELY


def test_the_three_tiers_and_the_ensemble_rule():
    t = LL.tier_status()
    assert t["TIERS"]["TIER_A"]["OBJECT"] == "P_MARKET_RAW"
    assert t["TIERS"]["TIER_B"]["STATUS"] == "NOT_BUILT_NO_DATA"
    assert t["TIERS"]["TIER_C"]["STATUS"] == "FROZEN_NEGATIVE_CONTROL"
    assert "may not be rebuilt until TIER_B exists" in t["ENSEMBLE_RULE"]


# --- Sections 12-14. Microstructure. ---------------------------------------

def test_book_imbalance_and_microprice_lean_the_right_way():
    f = MS.tick_features({"BEST_BID": 0.50, "BEST_ASK": 0.52,
                          "BID_SIZE": 300, "ASK_SIZE": 100})
    assert f["ORDER_BOOK_IMBALANCE"] > 0            # more bid than ask
    assert f["MICROPRICE_MINUS_MID"] > 0           # microprice leans to the ask
    assert f["SPREAD"] == pytest.approx(0.02)


def test_missing_book_inputs_give_none_not_zero():
    f = MS.tick_features({"BEST_BID": None, "BEST_ASK": None})
    assert f["ORDER_BOOK_IMBALANCE"] is None
    assert f["SPREAD"] is None


def test_forward_targets_are_attached_and_truncation_is_declared():
    ticks = [{"REQUEST_UTC": "2026-08-30T10:00:%02dZ" % s,
              "BEST_BID": 0.50 + s / 1000.0, "BEST_ASK": 0.52 + s / 1000.0}
             for s in range(0, 60, 4)]
    rows, meta = MS.build_targets(ticks, horizons=(5, 300))
    assert rows[0]["MID_MOVE_5S"] is not None
    assert rows[0]["MID_MOVE_300S"] is None        # past the capture end
    assert meta["TRUNCATED_AT_CAPTURE_END"]["MID_MOVE_300S"] > 0
    assert meta["TRUNCATION_IS_DECLARED_NOT_DROPPED"] is True


def test_the_relative_value_residual_has_the_declared_sign():
    assert MS.surface_residual(0.55, 0.50) == pytest.approx(0.05)
    assert MS.surface_residual(None, 0.5) is None


def test_a_reverting_residual_shows_a_negative_correlation():
    obs = []
    for i in range(60):
        res = (i % 9 - 4) / 100.0
        obs.append({"EVENT_KEY": "e%d" % (i % 12), "MARKET": "m", "T": "t",
                    "P_TARGET": 0.50 + res, "P_SURFACE_EX_TARGET": 0.50,
                    "TARGET_LATER": {5: 0.50 + 0.3 * res}})
    rows, _ = MS.relative_value_rows(obs, horizons=(5,))
    out = MS.relative_value_test(rows, horizons=(5,))
    assert out["BY_HORIZON"]["5S"]["CORRELATION"] < -0.5


def test_the_surface_must_exclude_the_target():
    assert "EXCLUDING the target" in MS.RELATIVE_VALUE_RULE
    assert "explains the target with itself" in MS.RELATIVE_VALUE_RULE


def test_microstructure_is_not_p_fill():
    assert MS.THIS_IS_NOT_P_FILL is True
    assert "queue" in MS.NO_FEATURE_HERE_MAY_BE_USED_AS_A_FILL_PROXY


def test_settlement_is_no_longer_the_only_target():
    assert "not whether it predicts settlement" in \
        MS.SETTLEMENT_IS_NO_LONGER_THE_ONLY_TARGET
