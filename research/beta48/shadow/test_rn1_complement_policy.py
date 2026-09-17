"""Tests for the complement-policy training table and the basis>1 correlates."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rn1_complement_policy as rcp  # noqa: E402
import whale_pairs as WP  # noqa: E402


def fill(tid, leg, price, size, ts, side="BUY", cond="c1"):
    # `outcome_index` is what identifies the leg -- the venue's own 0/1 token
    # index. `asset` is the token id and is not what the pairing reads.
    return {"trade_id": tid, "condition_id": cond,
            "outcome_index": 0 if leg == "A" else 1,
            "asset": "token-" + leg,
            "price": str(price), "size": str(size), "ts": ts, "side": side}


T = ["2026-08-01T10:00:%02dZ" % s for s in range(0, 60, 5)]


# ---------------------------------------------------------------------------
# the row unit
# ---------------------------------------------------------------------------


def test_only_a_fill_that_increases_matched_quantity_makes_a_row():
    # Two buys of the SAME leg pair nothing; the third, on the other leg, does.
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "A", "0.45", 100, T[1]),
                           fill(3, "B", "0.50", 60, T[2])])
    assert len(rows) == 1
    assert rows[0]["TRADE_ID"] == 3
    assert rows[0]["NEWLY_MATCHED_QTY"] == "60"


def test_the_incremental_basis_is_this_price_plus_the_other_legs_unit_cost():
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "B", "0.50", 100, T[1])])
    r = rows[0]
    assert r["OPPOSITE_BASIS_BEFORE"] == "0.40"
    assert r["FILL_PRICE"] == "0.50"
    assert r["INCREMENTAL_PAIR_BASIS"] == "0.90"
    assert r["INCREMENTAL_LOCK_PER_SHARE"] == "0.10"
    assert r["Y_BASIS_ABOVE_ONE"] == 0


def test_a_pair_costing_more_than_one_is_flagged_as_a_locked_loss():
    rows = rcp.build_rows([fill(1, "A", "0.60", 100, T[0]),
                           fill(2, "B", "0.55", 100, T[1])])
    r = rows[0]
    assert r["INCREMENTAL_PAIR_BASIS"] == "1.15"
    assert r["Y_BASIS_ABOVE_ONE"] == 1
    assert r["INCREMENTAL_LOCK_PER_SHARE"] == "-0.15"
    assert r["Y_INCREMENTAL_LOCKED_PNL"] == "-15.00"


def test_a_second_complement_purchase_pairs_only_what_is_newly_matched():
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "B", "0.50", 40, T[1]),
                           fill(3, "B", "0.52", 40, T[2])])
    assert [r["NEWLY_MATCHED_QTY"] for r in rows] == ["40", "40"]
    assert rows[1]["MATCHED_QTY_BEFORE"] == "40"


def test_a_sell_is_skipped_not_folded_into_a_buy():
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "B", "0.50", 100, T[1], side="SELL")])
    assert rows == []


def test_the_rows_carry_their_scope_and_inference_label():
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "B", "0.50", 100, T[1])])
    r = rows[0]
    assert r["SCOPE"] == "PIPELINE_VALIDATION_SELECTED_SUBSET"
    assert r["INFERENCE_LABEL"] == "NOT_VALID_FOR_RN1_POPULATION_INFERENCE"
    assert r["ACCOUNTING_CONVENTION"] == \
        "INCREMENTAL_COMPLEMENT_DECISION_ECONOMICS"


def test_the_timings_are_measured_from_the_streams_own_clock():
    rows = rcp.build_rows([fill(1, "A", "0.40", 100, T[0]),
                           fill(2, "A", "0.41", 100, T[1]),
                           fill(3, "B", "0.50", 100, T[2])])
    assert rows[0]["SECONDS_SINCE_FIRST_FILL"] == 10.0
    assert rows[0]["SECONDS_SINCE_PREVIOUS_FILL"] == 5.0
    assert rows[0]["FILLS_BEFORE"] == 2


# ---------------------------------------------------------------------------
# the as-of guard
# ---------------------------------------------------------------------------


def test_no_feature_moves_when_the_stream_is_truncated_at_its_own_decision():
    fills = [fill(1, "A", "0.40", 100, T[0]),
             fill(2, "B", "0.50", 50, T[1]),
             fill(3, "A", "0.30", 200, T[2]),
             fill(4, "B", "0.62", 150, T[3]),
             fill(5, "A", "0.20", 500, T[4])]
    rep = rcp.assert_as_of(fills)
    assert rep["AS_OF_HOLDS"] is True
    assert rep["LEAK_COUNT"] == 0
    assert rep["ROWS_CHECKED"] == 2


def test_the_guard_actually_catches_a_leak():
    # Deliberately corrupt the builder so a later fill reaches an earlier row,
    # and confirm the guard notices rather than passing quietly.
    fills = [fill(1, "A", "0.40", 100, T[0]),
             fill(2, "B", "0.50", 50, T[1]),
             fill(3, "B", "0.90", 50, T[2])]
    real = rcp.build_rows

    def leaky(f, condition_id=None):
        rows = real(f, condition_id)
        if rows:
            # the last fill's price, written onto every row
            rows[0] = dict(rows[0], FILL_PRICE=str(f[-1].get("price")))
        return rows

    rcp.build_rows = leaky
    try:
        rep = rcp.assert_as_of(fills)
    finally:
        rcp.build_rows = real
    assert rep["AS_OF_HOLDS"] is False
    assert any(x.get("FEATURE") == "FILL_PRICE" for x in rep["LEAKS"])


def test_nothing_from_settlement_is_listed_as_a_feature():
    assert "SETTLED_YES" not in rcp.FEATURES
    assert not any("SETTLE" in k or "OUTCOME" in k for k in rcp.FEATURES)
    illegal = " ".join(rcp.ILLEGAL_AS_FEATURES)
    assert "settlement outcome" in illegal
    assert "later than the decision fill" in illegal


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------


def _world():
    return {
        "c1": [fill(1, "A", "0.40", 100, T[0], cond="c1"),
               fill(2, "B", "0.50", 100, T[1], cond="c1")],
        "c2": [fill(3, "A", "0.60", 100, T[0], cond="c2"),
               fill(4, "B", "0.55", 100, T[1], cond="c2")],
        "c3": [fill(5, "A", "0.30", 100, T[0], cond="c3")],
    }


def test_the_table_counts_what_it_skipped_and_why():
    t = rcp.build_table(_world())
    assert t["ROW_COUNT"] == 2
    assert t["CONDITIONS_WITH_ROWS"] == 2
    assert t["SKIPPED"]["NO_COMPLEMENT_ACQUISITION"] == 1
    assert t["BASIS_ABOVE_ONE_ROWS"] == 1
    assert abs(t["BASIS_ABOVE_ONE_RATE"] - 0.5) < 1e-12


def test_incomplete_coverage_conditions_are_excluded_and_counted():
    t = rcp.build_table(_world(), complete_conditions={"c1"})
    assert t["ROW_COUNT"] == 1
    assert t["SKIPPED"]["INCOMPLETE_COVERAGE"] == 2


def test_a_null_condition_is_counted_not_bucketed_somewhere():
    w = _world()
    w[None] = [fill(9, "A", "0.4", 10, T[0], cond=None)]
    t = rcp.build_table(w)
    assert t["SKIPPED"]["NULL_CONDITION_ID"] == 1


def test_the_table_carries_the_selection_constants_that_must_travel():
    t = rcp.build_table(_world())
    c = t["SELECTION_CONSTANTS"]
    assert c["SELECTION_FINDING_STATUS"] == "FROZEN"
    assert c["DO_NOT_TRAIN_OR_VALIDATE_BETTOR_POLICY_FROM_THIS_SUBSET"] is True
    assert t["INFERENCE_LABEL"] == WP.SUBSET_INFERENCE_LABEL


# ---------------------------------------------------------------------------
# the correlates
# ---------------------------------------------------------------------------


def _corr_rows(n=200):
    rows = []
    for i in range(n):
        above = i % 2
        rows.append({
            "Y_BASIS_ABOVE_ONE": above,
            "FILL_PRICE": "0.70" if above else "0.30",
            "OPPOSITE_BASIS_BEFORE": "0.50",
            "FILLS_BEFORE": 10 + (i % 5),
            "SECONDS_SINCE_FIRST_FILL": 100.0 + 800.0 * above + (i % 7),
            "OPPOSITE_QTY_BEFORE": "1000",
            "SAME_QTY_BEFORE": "1000",
            "MATCHED_QTY_BEFORE": "1000",
            "UNMATCHED_OPPOSITE_BEFORE": "0",
            "SECONDS_SINCE_PREVIOUS_FILL": 30.0,
            "FILL_SIZE": "100",
            "NEWLY_MATCHED_QTY": "100",
        })
    return rows


def test_a_component_of_the_target_is_flagged_and_kept_out_of_large_effects():
    rep = rcp.basis_above_one_correlates(_corr_rows())
    assert rep["FEATURES"]["FILL_PRICE"]["EFFECT"] == "LARGE"
    assert rep["FEATURES"]["FILL_PRICE"]["MECHANICAL_COMPONENT_OF_TARGET"]
    assert "FILL_PRICE" not in rep["LARGE_EFFECTS"]
    assert "FILL_PRICE" in rep["MECHANICAL_COMPONENTS_OF_THE_TARGET"]
    # a genuine non-component effect still gets through
    assert "SECONDS_SINCE_FIRST_FILL" in rep["LARGE_EFFECTS"]


def test_a_feature_with_no_variation_reports_a_negligible_effect():
    rep = rcp.basis_above_one_correlates(_corr_rows())
    assert rep["FEATURES"]["FILL_SIZE"]["EFFECT"] == "NEGLIGIBLE"


def test_too_few_rows_is_said_rather_than_computed():
    rep = rcp.basis_above_one_correlates(
        [{"Y_BASIS_ABOVE_ONE": 1, "FILL_PRICE": "0.9"}])
    assert rep["FEATURES"]["FILL_PRICE"]["STATUS"] == "TOO_FEW_ROWS"


def test_no_motive_is_assigned_anywhere():
    rep = rcp.basis_above_one_correlates(_corr_rows())
    assert rep["MOTIVE_ASSIGNED"] is False
    assert rcp.MOTIVE_ASSIGNED is False
    for word in ("Hedging", "stop-loss", "inventory", "mistake"):
        assert word in rcp.WHY_NO_MOTIVE
    assert "do not identify a cause" in rep["ASSOCIATION_IS_NOT_CAUSE"]


def test_the_correlates_carry_the_scope_and_the_selection_constants():
    rep = rcp.basis_above_one_correlates(_corr_rows())
    assert rep["SCOPE"] == "PIPELINE_VALIDATION_SELECTED_SUBSET"
    assert rep["INFERENCE_LABEL"] == "NOT_VALID_FOR_RN1_POPULATION_INFERENCE"
    assert rep["SELECTION_CONSTANTS"]["RN1_U0_TRADES_AT_CUTOFF"] == 962509


# ---------------------------------------------------------------------------
# complement semantics (section 4)
# ---------------------------------------------------------------------------


def test_a_two_token_market_paying_one_and_zero_verifies_in_its_own_state():
    row = {"resolved": True, "payouts": ["1.0", "0.0"],
           "tokens": [{"outcome": "Yes"}, {"outcome": "No"}]}
    status, detail = WP.check_complement(row)
    assert status == WP.COMPLEMENT_OBSERVED_STATUS_VERIFIED
    assert detail["PAYOUT_SUM"] == "1.0"


def test_a_half_half_void_still_sums_to_one_and_verifies():
    row = {"resolved": True, "payouts": ["0.5", "0.5"],
           "tokens": [{"outcome": "Yes"}, {"outcome": "No"}]}
    assert WP.check_complement(row)[0] == WP.COMPLEMENT_OBSERVED_STATUS_VERIFIED


def test_payouts_that_do_not_sum_to_one_are_a_violation_not_a_rounding_note():
    row = {"resolved": True, "payouts": ["1.0", "1.0"],
           "tokens": [{"outcome": "Yes"}, {"outcome": "No"}]}
    status, detail = WP.check_complement(row)
    assert status == WP.COMPLEMENT_OBSERVED_STATUS_VIOLATED
    assert "categorically wrong" in detail["WHAT_IT_MEANS"]


def test_a_three_outcome_market_is_not_a_binary_complement():
    row = {"resolved": True, "payouts": ["1.0", "0.0", "0.0"],
           "tokens": [{"outcome": "H"}, {"outcome": "D"}, {"outcome": "A"}]}
    status, detail = WP.check_complement(row)
    assert status == WP.COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE
    assert detail["REASON"] == "NOT_A_TWO_TOKEN_MARKET"


def test_an_unresolved_or_unreadable_market_refuses_rather_than_passing():
    assert WP.check_complement({"resolved": False})[0] == \
        WP.COMPLEMENT_OBSERVED_STATUS_UNCHECKABLE
    assert WP.check_complement(
        {"resolved": True, "payouts": ["x", "y"],
         "tokens": [{}, {}]})[1]["REASON"] == "PAYOUT_NOT_NUMERIC"
    assert WP.check_complement(
        {"resolved": True, "payouts": ["1.0"],
         "tokens": [{}, {}]})[1]["REASON"] == \
        "PAYOUT_LENGTH_DOES_NOT_MATCH_TOKEN_COUNT"


def test_the_all_states_guarantee_stays_unverified_however_many_states_pass():
    rows = [{"resolved": True, "payouts": ["1.0", "0.0"],
             "tokens": [{}, {}]} for _ in range(5000)]
    rep = WP.complement_report(rows)
    assert rep["VERIFIED_IN_OBSERVED_STATE"] == 5000
    assert rep["VIOLATED_IN_OBSERVED_STATE"] == 0
    assert rep["OBSERVED_STATE_PASS_RATE"] == 1.0
    # and still:
    assert rep["COMPLEMENT_GUARANTEE_STATUS"] == "NOT_VERIFIED"
    assert "one terminal state" in rep["WHY_NOT_VERIFIED"]


def test_a_single_violation_is_surfaced_with_its_market():
    rows = [{"resolved": True, "payouts": ["1.0", "0.0"], "tokens": [{}, {}]},
            {"resolved": True, "payouts": ["0.0", "0.0"], "tokens": [{}, {}],
             "condition_id": "0xbad", "market_slug": "bad-slug"}]
    rep = WP.complement_report(rows)
    assert rep["VIOLATED_IN_OBSERVED_STATE"] == 1
    assert rep["VIOLATIONS"][0]["MARKET_SLUG"] == "bad-slug"
