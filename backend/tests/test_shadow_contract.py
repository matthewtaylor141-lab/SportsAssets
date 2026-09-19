"""THE SHADOW CONTRACT: what it refuses to claim.

Shadow trading's only value is that its records are honest about what
was known and when. A shadow lane that flatters itself is worse than no
shadow lane, because management would act on it. These tests are the
four refusals, exercised:

  1. a price touched is not a fill
  2. simulated and observed economics never sum
  3. a decision does not execute at its own book
  4. YES and NO are different positions

NOTHING HERE OPENS A SOCKET or touches the database.
"""
from __future__ import annotations

import pytest

from sportsassets import shadow as sh


# ── the standing labels ──────────────────────────────────────────────

class TestTheLaneIsShadowByConstruction:

    def test_no_real_order_submission(self):
        assert sh.SHADOW_MODE is True
        assert sh.REAL_ORDER_SUBMISSION_ENABLED is False
        assert sh.CAPITAL_AT_RISK == 0

    def test_the_disclosure_says_all_three_things(self):
        d = sh.DISCLOSURE.upper()
        assert "SHADOW" in d and "NO REAL CAPITAL" in d
        assert "SIMULATED" in d or "COUNTERFACTUAL" in d

    def test_every_decision_carries_the_disclosure(self):
        rec = sh.decision_record(
            shadowDecisionId="d1", symbol="s", outcomeLeg="YES",
            modelVersion="m", policyVersion="p", evidenceSource="src",
            decisionTs="2026-09-19T17:00:00Z", proposedAction=sh.NO_TRADE)
        assert rec["disclosure"] == sh.DISCLOSURE
        assert rec["capitalAtRisk"] == 0
        assert rec["realOrderSubmissionEnabled"] is False


# ── 1. A PRICE TOUCHED IS NOT A FILL ─────────────────────────────────

class TestAPassiveOrderIsNotFilledByTheMarketMovingThroughIt:

    def test_without_a_queue_model_the_fill_is_not_identified(self):
        got = sh.passive_outcome(quote_markout=0.03)
        assert got["passiveFillStatus"] == sh.NOT_IDENTIFIED
        assert got["actualBettorFill"] is None
        assert got["queueModelEstimatedFill"] is None
        assert "not evidence that we were filled" in got["why"]

    def test_a_markout_is_research_not_money(self):
        got = sh.passive_outcome(quote_markout=0.05)
        assert got["counterfactualQuoteMarkout"] == 0.05
        assert got["executionClass"] == sh.PASSIVE_COUNTERFACTUAL_MARKOUT
        assert got["executionClass"] in sh.RESEARCH_CLASSES
        assert got["executionClass"] not in sh.REALIZABLE_CLASSES

    def test_a_queue_model_must_declare_itself_decision_grade(self):
        # a model that merely produces a number does not get counted
        vague = sh.passive_outcome(queue_model={"estimatedFillQty": 500})
        assert vague["passiveFillStatus"] == sh.NOT_IDENTIFIED
        assert vague["queueModelEstimatedFill"] is None

        graded = sh.passive_outcome(
            queue_model={"estimatedFillQty": 500, "decisionGrade": True})
        assert graded["queueModelEstimatedFill"] == 500
        assert graded["executionClass"] == sh.PASSIVE_QUEUE_MODEL_ESTIMATE

    def test_even_a_graded_estimate_is_not_an_actual_fill(self):
        graded = sh.passive_outcome(
            queue_model={"estimatedFillQty": 500, "decisionGrade": True})
        assert graded["actualBettorFill"] is None

    def test_an_unidentified_passive_fill_is_not_counted_as_pnl(self):
        rows = [{"executionClass": sh.PASSIVE_COUNTERFACTUAL_MARKOUT,
                 "passiveFillStatus": sh.NOT_IDENTIFIED, "pnl": 999.0}]
        got = sh.economics(rows)
        assert got["byExecutionClass"][
            sh.PASSIVE_COUNTERFACTUAL_MARKOUT]["pnl"] == 0.0
        assert got["unidentifiedRows"] == 1


# ── 2. SIMULATED AND OBSERVED ECONOMICS NEVER SUM ────────────────────

class TestTheBooksAreKeptApart:

    def test_there_is_no_grand_total(self):
        got = sh.economics([])
        for key in got:
            assert "total" not in key.lower(), key
        assert "byExecutionClass" in got

    def test_each_class_keeps_its_own_money(self):
        rows = [
            {"executionClass": sh.MARKETABLE_RECONSTRUCTED,
             "status": sh.FILLED, "pnl": 10.0},
            {"executionClass": sh.PASSIVE_QUEUE_MODEL_ESTIMATE,
             "status": sh.FILLED, "pnl": 100.0},
        ]
        got = sh.economics(rows)["byExecutionClass"]
        assert got[sh.MARKETABLE_RECONSTRUCTED]["pnl"] == 10.0
        assert got[sh.PASSIVE_QUEUE_MODEL_ESTIMATE]["pnl"] == 100.0

    def test_actual_fill_pnl_is_none_while_no_order_exists(self):
        got = sh.economics([])
        assert got["actualFillPnl"] is None
        assert got["realizedFromRealOrders"] is None

    def test_an_actual_fill_carrying_money_is_refused(self):
        """The failure this catches: shadow P&L quietly relabelled as
        realized once someone adds a real order path."""
        with pytest.raises(sh.ShadowRefusal):
            sh.economics([{"executionClass": sh.ACTUAL_FILL,
                           "status": sh.FILLED, "pnl": 1.0}])

    def test_money_without_a_class_is_refused(self):
        with pytest.raises(sh.ShadowRefusal):
            sh.economics([{"pnl": 5.0, "status": sh.FILLED}])
        with pytest.raises(sh.ShadowRefusal):
            sh.economics([{"executionClass": "SOMETHING_NEW", "pnl": 5.0}])

    def test_only_actual_fills_could_ever_be_realizable(self):
        assert sh.REALIZABLE_CLASSES == {sh.ACTUAL_FILL}
        assert sh.MARKETABLE_RECONSTRUCTED not in sh.REALIZABLE_CLASSES


# ── 3. A DECISION DOES NOT EXECUTE AT ITS OWN BOOK ───────────────────

class TestLatency:

    def test_the_components_are_never_blended_away(self):
        got = sh.latency(data_ms=12, compute_ms=8, execution_ms=130)
        assert got["dataLatencyMs"] == 12
        assert got["decisionComputeMs"] == 8
        assert got["executionLatencyMs"] == 130
        assert got["totalDecisionToArrivalMs"] == 150
        assert got["latencyBasis"] == sh.OBSERVED

    def test_a_partial_measurement_reports_no_total(self):
        """Summing the parts you have understates the latency by exactly
        the part you did not measure."""
        got = sh.latency(data_ms=12, execution_ms=130)
        assert got["totalDecisionToArrivalMs"] is None
        assert got["executionLatencyMs"] == 130

    def test_a_scenario_is_labelled_a_scenario(self):
        got = sh.latency(data_ms=12, compute_ms=8, scenario_ms=250)
        assert got["latencyBasis"] == sh.SCENARIO
        assert got["latencyScenarioMs"] == 250
        assert got["appliedLatencyMs"] == 250
        assert got["totalDecisionToArrivalMs"] == 270

    def test_an_invented_latency_is_refused(self):
        """The grid is PREDECLARED so nobody picks the flattering value
        after seeing the result."""
        with pytest.raises(sh.ShadowRefusal):
            sh.latency(scenario_ms=7)

    def test_the_declared_grid_is_the_one_management_named(self):
        assert sh.LATENCY_SCENARIOS_MS == (50, 100, 250, 500, 1000)

    def test_no_latency_at_all_is_not_identified(self):
        got = sh.latency()
        assert got["latencyBasis"] == sh.NOT_IDENTIFIED
        assert got["appliedLatencyMs"] is None


class TestMarketableFillsWalkTheObservedBook:

    BOOK = {"asks": [{"price": 0.47, "qty": 100},
                     {"price": 0.48, "qty": 200},
                     {"price": 0.50, "qty": 500}],
            "bids": [{"price": 0.46, "qty": 150},
                     {"price": 0.45, "qty": 400}]}

    def test_a_buy_lifts_asks_cheapest_first(self):
        got = sh.marketable_fill(sh.BUY, 250, 0.50, self.BOOK)
        assert got["status"] == sh.FILLED
        assert got["shadowFilledQty"] == 250
        # 100@.47 + 150@.48
        assert round(got["vwap"], 6) == round((100 * .47 + 150 * .48) / 250, 6)

    def test_a_sell_hits_bids_richest_first(self):
        got = sh.marketable_fill(sh.SELL, 200, 0.45, self.BOOK)
        assert got["status"] == sh.FILLED
        assert round(got["vwap"], 6) == round((150 * .46 + 50 * .45) / 200, 6)

    def test_it_never_fills_more_than_the_observed_depth(self):
        got = sh.marketable_fill(sh.BUY, 10_000, 0.60, self.BOOK)
        assert got["shadowFilledQty"] == 800      # 100+200+500
        assert got["unfilledQty"] == 9_200
        assert got["status"] == sh.PARTIAL

    def test_it_never_fills_at_a_price_that_is_gone(self):
        """The decision wanted .47; at arrival only .50 is left inside a
        .48 limit, so the fill stops rather than pretending."""
        thin = {"asks": [{"price": 0.50, "qty": 500}]}
        got = sh.marketable_fill(sh.BUY, 100, 0.48, thin, decision_price=0.47)
        assert got["status"] == sh.UNFILLED
        assert got["shadowFilledQty"] == 0.0

    def test_an_absent_book_is_not_identified_not_a_guess(self):
        for absent in (None, {}, {"asks": []}, {"asks": None}):
            got = sh.marketable_fill(sh.BUY, 100, 0.50, absent)
            assert got["status"] == sh.NOT_IDENTIFIED
            assert got["shadowFilledQty"] is None
            assert "interpolation" in got["why"]

    def test_slippage_is_positive_when_it_hurt_either_side(self):
        buy = sh.marketable_fill(sh.BUY, 250, 0.50, self.BOOK,
                                 decision_price=0.47)
        assert buy["slippage"] > 0          # paid more than intended
        sell = sh.marketable_fill(sh.SELL, 200, 0.45, self.BOOK,
                                  decision_price=0.46)
        assert sell["slippage"] > 0         # received less than intended

    def test_a_fill_is_always_marked_reconstructed(self):
        got = sh.marketable_fill(sh.BUY, 50, 0.50, self.BOOK)
        assert got["executionClass"] == sh.MARKETABLE_RECONSTRUCTED
        assert got["executionClass"] not in sh.REALIZABLE_CLASSES


# ── 4. YES AND NO ARE DIFFERENT POSITIONS ────────────────────────────

class TestPairingKeepsLegsApart:

    def test_the_pair_locks_a_number_and_reports_it(self):
        got = sh.pair_economics(leg_shares=100, leg_avg_cost=0.47,
                                complement_price=0.51)
        assert got["pairCost"] == pytest.approx(51.0)
        assert got["lockedValue"] == pytest.approx(100.0)
        assert got["lockedPnl"] == pytest.approx(100 - 47 - 51)

    def test_a_loss_lock_is_reported_not_suppressed(self):
        """'A loss-lock is not automatically an error.' It is recorded
        with its number and the policy explains itself."""
        got = sh.pair_economics(leg_shares=100, leg_avg_cost=0.60,
                                complement_price=0.55)
        assert got["lockedPnl"] < 0
        assert got["status"] == "IDENTIFIED"

    def test_an_unestablished_term_stays_unknown(self):
        got = sh.pair_economics(leg_shares=100, leg_avg_cost=None,
                                complement_price=0.51)
        assert got["status"] == sh.NOT_IDENTIFIED
        assert got["lockedPnl"] is None
        assert "leg_avg_cost" in got["why"]


class TestCashOutIsNotAssumedGood:

    def test_it_compares_the_alternatives_rather_than_preferring_one(self):
        got = sh.choose_exit({sh.HOLD: 0.04, sh.CASH_OUT: 0.01,
                              sh.PAIR: 0.02})
        assert got["chosen"] == sh.HOLD

    def test_an_unknown_ev_is_not_treated_as_zero(self):
        """A zero would beat every negative option on arithmetic alone."""
        got = sh.choose_exit({sh.HOLD: -0.02, sh.CASH_OUT: None})
        assert got["chosen"] == sh.HOLD
        assert sh.CASH_OUT in got["unknown"]

    def test_all_unknown_is_not_identified(self):
        got = sh.choose_exit({sh.HOLD: None, sh.CASH_OUT: None})
        assert got["status"] == sh.NOT_IDENTIFIED
        assert got["chosen"] is None
        assert "coin toss" in got["why"]

    def test_uncertainty_shades_every_alternative_the_same_way(self):
        got = sh.choose_exit({sh.HOLD: 0.03, sh.PAIR: 0.02},
                             uncertainty=0.05)
        assert got["uncertaintyApplied"] == 0.05
        assert got["conservative"][sh.HOLD] == pytest.approx(-0.02)

    def test_an_action_outside_the_exit_set_is_refused(self):
        with pytest.raises(sh.ShadowRefusal):
            sh.choose_exit({sh.BUY: 0.10})


# ── the record builder ───────────────────────────────────────────────

class TestADecisionMustSayWhatItKnewAndWhy:

    BASE = dict(shadowDecisionId="d1", symbol="s", outcomeLeg="YES",
                modelVersion="m", policyVersion="p", evidenceSource="src",
                decisionTs="2026-09-19T17:00:00Z")

    def test_evidence_source_is_required(self):
        fields = {**self.BASE, "proposedAction": sh.NO_TRADE}
        fields.pop("evidenceSource")
        with pytest.raises(sh.ShadowRefusal) as exc:
            sh.decision_record(**fields)
        assert "evidenceSource" in str(exc.value)

    def test_an_action_outside_the_vocabulary_is_refused(self):
        with pytest.raises(sh.ShadowRefusal):
            sh.decision_record(**self.BASE, proposedAction="YOLO")

    def test_an_acting_decision_must_say_why_it_dominated(self):
        with pytest.raises(sh.ShadowRefusal) as exc:
            sh.decision_record(**self.BASE, proposedAction=sh.BUY)
        assert "dominated" in str(exc.value)

    def test_no_trade_is_a_first_class_record(self):
        """NO_TRADE decisions are training evidence too and must be
        retained, so they are recordable without ceremony."""
        rec = sh.decision_record(**self.BASE, proposedAction=sh.NO_TRADE)
        assert rec["proposedAction"] == sh.NO_TRADE

    def test_the_vocabulary_is_the_one_management_specified(self):
        for action in ("NO_TRADE", "HOLD", "BUY", "SELL", "CASH_OUT",
                       "PAIR", "COMPLETE_COMPLEMENT", "CANCEL", "REPRICE",
                       "REDUCE", "HOLD_TO_SETTLEMENT"):
            assert action in sh.ACTIONS

    def test_every_action_maps_to_a_book_or_to_none(self):
        for action in sh.ACTIONS:
            cls = sh.execution_class_for(action)
            assert cls is None or cls in sh.EXECUTION_CLASSES
        assert sh.execution_class_for(sh.NO_TRADE) is None
        assert sh.execution_class_for(sh.BUY) == sh.MARKETABLE_RECONSTRUCTED
        assert sh.execution_class_for(sh.REPRICE) == \
            sh.PASSIVE_COUNTERFACTUAL_MARKOUT


class TestScoringHorizons:

    def test_five_seconds_needs_a_source_that_can_resolve_it(self):
        assert sh.horizon_observable("5S", source_interval_s=30)[
            "observable"] is False
        assert sh.horizon_observable("5S", source_interval_s=1)[
            "observable"] is True

    def test_an_unknown_source_rate_is_not_observable(self):
        got = sh.horizon_observable("30S")
        assert got["observable"] is False
        assert "not established" in got["why"]

    def test_settlement_is_always_observable(self):
        assert sh.horizon_observable("SETTLEMENT")["observable"] is True

    def test_the_horizons_are_the_ones_specified(self):
        assert sh.HORIZONS == ("5S", "30S", "60S", "300S", "SETTLEMENT")
