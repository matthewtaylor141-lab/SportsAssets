"""TWO LANES, AND THE WALL BETWEEN THEM.

The BETTOR lane's entire claim is that it did not look at RN1. That
claim is worth nothing unless it is enforced, so these tests are mostly
about what the wall refuses:

  * RN1 evidence reaching the independent lane
  * a feature whose provenance nobody declared
  * an independent belief appearing in the RN1 lane
  * P_BETTOR quietly becoming P_MARKET or zero
  * a load-bearing EV term quietly becoming zero
  * the two lanes being added into one headline number

NOTHING HERE OPENS A SOCKET or touches the database.
"""
from __future__ import annotations

import pytest

from sportsassets import shadow as sh
from sportsassets import shadow_lanes as sl

CLEAN = {"spread_bps": sl.PROV_SPREAD, "book_depth": sl.PROV_DEPTH}
BASE = dict(shadowDecisionId="d1", symbol="s", outcomeLeg="YES",
            modelVersion="m", policyVersion="p",
            decisionTs="2026-09-19T17:00:00Z")


# ── 7. LINEAGE ───────────────────────────────────────────────────────

class TestRN1CannotReachTheIndependentLane:

    @pytest.mark.parametrize("prov", [
        sl.PROV_RN1_ACTION, sl.PROV_RN1_ACCOUNT_IDENTITY,
        sl.PROV_RN1_FUTURE_ACTION, sl.PROV_RN1_DERIVED_TARGET,
        sl.PROV_RN1_MIRROR_DECISION])
    def test_every_named_rn1_provenance_is_refused(self, prov):
        with pytest.raises(sl.LineageViolation):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW, {"f": prov})

    def test_the_refusal_says_what_the_lane_is_for(self):
        with pytest.raises(sl.LineageViolation) as exc:
            sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                              {"f": sl.PROV_RN1_ACTION})
        assert "did not look at RN1" in str(exc.value)

    def test_clean_provenance_passes(self):
        got = sl.assert_lineage(sl.BETTOR_EV_SHADOW, CLEAN)
        assert got["rn1FeaturesUsed"] is False
        assert got["rn1Features"] == []

    def test_a_registered_rn1_specialist_is_the_only_exception(self):
        spec = sl.register_specialist("s1", "FILL", "n", "v",
                                      sl.BETTOR_EV_SHADOW,
                                      rn1_specialist=True)
        got = sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                                {"f": sl.PROV_RN1_ACTION}, specialist=spec)
        # and the fact TRAVELS, so nothing is reclassified as
        # independent afterwards
        assert got["rn1FeaturesUsed"] is True
        assert got["rn1Features"] == ["f"]

    def test_an_unregistered_specialist_does_not_unlock_it(self):
        spec = sl.register_specialist("s1", "FILL", "n", "v",
                                      sl.BETTOR_EV_SHADOW)
        with pytest.raises(sl.LineageViolation):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                              {"f": sl.PROV_RN1_ACTION}, specialist=spec)

    def test_the_rn1_lane_may_use_rn1_evidence(self):
        got = sl.assert_lineage(sl.RN1_SHADOW, {"f": sl.PROV_RN1_ACTION})
        assert got["rn1FeaturesUsed"] is True


class TestAmbiguityFailsClosed:

    def test_an_undeclared_provenance_is_refused(self):
        with pytest.raises(sl.LineageAmbiguous):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW, {"mystery": None})

    def test_an_unrecognised_provenance_is_refused(self):
        with pytest.raises(sl.LineageAmbiguous):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                              {"f": "SOMETHING_PLAUSIBLE"})

    def test_no_declaration_at_all_is_refused(self):
        """An empty declaration and a clean one are different claims."""
        with pytest.raises(sl.LineageAmbiguous):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW, None)

    def test_an_empty_dict_is_a_clean_declaration_not_an_absent_one(self):
        got = sl.assert_lineage(sl.BETTOR_EV_SHADOW, {})
        assert got["rn1FeaturesUsed"] is False

    def test_provenance_is_declared_not_sniffed_from_the_name(self):
        """A name-sniffing rule misses `signal_17` and catches
        `rn1_free_indicator`. The declaration is what counts."""
        got = sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                                {"rn1_free_indicator": sl.PROV_L2})
        assert got["rn1FeaturesUsed"] is False
        with pytest.raises(sl.LineageViolation):
            sl.assert_lineage(sl.BETTOR_EV_SHADOW,
                              {"signal_17": sl.PROV_RN1_DERIVED_TARGET})


# ── 4. THREE PROBABILITY OBJECTS ─────────────────────────────────────

class TestTheThreeProbabilitiesStayDistinct:

    def test_absent_p_bettor_is_not_established_not_zero(self):
        got = sl.probabilities(p_market=0.48)
        assert got["pBettor"] is None
        assert got["pBettorStatus"] == sl.NOT_ESTABLISHED
        assert got["pBettor"] != got["pMarket"]

    def test_absent_p_fill_is_its_own_status(self):
        got = sl.probabilities(p_market=0.48, p_fill=None)
        assert got["pFillStatus"] == sl.NOT_IDENTIFIED

    def test_they_are_three_separate_keys(self):
        got = sl.probabilities(p_market=0.48, p_bettor=0.52, p_fill=0.3)
        assert (got["pMarket"], got["pBettor"], got["pFill"]) == \
            (0.48, 0.52, 0.3)

    def test_the_rn1_lane_refuses_an_independent_belief(self):
        with pytest.raises(sl.LaneViolation) as exc:
            sl.probabilities(p_market=0.48, p_bettor=0.52,
                             lane=sl.RN1_SHADOW)
        assert "wearing our name" in str(exc.value)

    def test_the_rn1_lane_still_reports_the_market(self):
        got = sl.probabilities(p_market=0.48, lane=sl.RN1_SHADOW)
        assert got["pMarket"] == 0.48
        assert got["pBettorStatus"] == sl.NOT_ESTABLISHED


# ── 5. ACTION EV ─────────────────────────────────────────────────────

FULL = {"valueAdvantage": 0.02, "arrivalPrice": -0.004,
        "slippage": -0.001, "feesRebates": -0.0005}


class TestActionEvNamesWhatItDoesNotKnow:

    def test_a_missing_load_bearing_term_is_not_identified(self):
        for drop in sl.LOAD_BEARING:
            comps = {k: v for k, v in FULL.items() if k != drop}
            got = sl.action_ev(sh.BUY, comps)
            assert got["status"] == sl.NOT_IDENTIFIED
            assert got["ev"] is None
            assert drop in got["missing"]

    def test_it_never_silently_zeroes(self):
        got = sl.action_ev(sh.BUY, {"valueAdvantage": 0.02})
        assert got["ev"] is None
        assert "would make the EV a wish" in got["why"]

    def test_a_complete_set_sums(self):
        got = sl.action_ev(sh.BUY, FULL)
        assert got["status"] == "IDENTIFIED"
        assert got["ev"] == pytest.approx(sum(FULL.values()))

    def test_a_passive_action_also_needs_fill_probability(self):
        got = sl.action_ev(sh.REPRICE, FULL)
        assert got["status"] == sl.NOT_IDENTIFIED
        assert "fillProbability" in got["missing"]
        ok = sl.action_ev(sh.REPRICE, {**FULL, "fillProbability": 0.4})
        assert ok["status"] == "IDENTIFIED"

    def test_no_trade_has_no_execution_to_price(self):
        got = sl.action_ev(sh.NO_TRADE, {})
        assert got["status"] == "NO_EXECUTION"
        assert got["missing"] == []

    def test_unnamed_components_are_refused(self):
        with pytest.raises(sh.ShadowRefusal):
            sl.action_ev(sh.BUY, {**FULL, "vibes": 0.5})

    def test_the_components_are_the_ones_specified(self):
        for name in ("valueAdvantage", "fillProbability", "arrivalPrice",
                     "spread", "depth", "slippage", "feesRebates",
                     "toxicity", "inventoryCost", "capitalTime",
                     "pairingOpportunity", "exitAlternatives",
                     "settlementValue"):
            assert name in sl.EV_COMPONENTS

    def test_unidentified_components_are_listed_not_hidden(self):
        got = sl.action_ev(sh.BUY, FULL)
        assert "toxicity" in got["notIdentified"]


# ── 6. SPECIALISTS ───────────────────────────────────────────────────

class TestSpecialistsAreInterfacesNotModels:

    def test_the_eleven_kinds_are_registered(self):
        for kind in ("SETTLEMENT_FAIR_VALUE", "SHORT_HORIZON_30S",
                     "SHORT_HORIZON_60S", "SHORT_HORIZON_300S",
                     "RELATIVE_VALUE", "PAIR_COMPLETION", "TOXICITY",
                     "FILL", "CASHOUT", "INVENTORY", "SPORT_SPECIFIC"):
            assert kind in sl.SPECIALIST_KINDS

    def test_registering_does_not_promote(self):
        spec = sl.register_specialist("s", "TOXICITY", "n", "v",
                                      sl.BETTOR_EV_SHADOW)
        assert spec["promoted"] is False

    def test_a_pre_promoted_registration_is_refused(self):
        with pytest.raises(sl.LaneViolation) as exc:
            sl.register_specialist("s", "TOXICITY", "n", "v",
                                   sl.BETTOR_EV_SHADOW, promoted=True)
        assert "prospective and gated" in str(exc.value)

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(sl.LaneViolation):
            sl.register_specialist("s", "VIBES", "n", "v", sl.RN1_SHADOW)


# ── 8. DISAGREEMENT ──────────────────────────────────────────────────

class TestTheDisagreementDataset:

    def test_not_yet_eligible_is_checked_before_everything(self):
        """A NO_TRADE for want of an EV is a different fact from a
        NO_TRADE after judging the opportunity."""
        got = sl.classify_disagreement(
            rn1_action=sh.BUY, bettor_action=sh.NO_TRADE,
            bettor_reason=sl.REASON_EV_NOT_ESTABLISHED)
        assert got["classification"] == sl.BETTOR_NOT_YET_ELIGIBLE
        assert got["classification"] != sl.RN1_ONLY

    def test_both_declining_is_first_class_evidence(self):
        got = sl.classify_disagreement(sh.NO_TRADE, sh.NO_TRADE)
        assert got["classification"] == sl.BOTH_NO_TRADE
        assert "retained" in got["why"]

    def test_one_sided_action(self):
        assert sl.classify_disagreement(sh.BUY, sh.NO_TRADE)[
            "classification"] == sl.RN1_ONLY
        assert sl.classify_disagreement(sh.NO_TRADE, sh.BUY)[
            "classification"] == sl.BETTOR_ONLY

    def test_opposite_directions_are_flagged(self):
        got = sl.classify_disagreement(sh.BUY, sh.SELL)
        assert got["classification"] == sl.DISAGREE_DIRECTION

    def test_same_direction_agrees(self):
        assert sl.classify_disagreement(sh.BUY, sh.BUY)[
            "classification"] == sl.BOTH_AGREE

    def test_the_six_classes_are_the_ones_specified(self):
        assert set(sl.DISAGREEMENT_CLASSES) == {
            "RN1_ONLY", "BETTOR_ONLY", "BOTH_AGREE", "DISAGREE_DIRECTION",
            "BOTH_NO_TRADE", "BETTOR_NOT_YET_ELIGIBLE"}


# ── 9. COMMAND NEVER GETS ONE HEADLINE NUMBER ────────────────────────

class TestTheLanesAreNeverAdded:

    def test_the_report_has_no_combined_pnl(self):
        got = sl.lane_report({
            sl.RN1_SHADOW: [{"executionClass": sh.MARKETABLE_RECONSTRUCTED,
                             "status": sh.FILLED, "pnl": 10.0}],
            sl.BETTOR_EV_SHADOW: []})
        assert got["combinedPnl"] is None
        assert set(got["byLane"]) == set(sl.LANES)

    def test_each_lane_keeps_its_own_economics(self):
        got = sl.lane_report({
            sl.RN1_SHADOW: [{"executionClass": sh.MARKETABLE_RECONSTRUCTED,
                             "status": sh.FILLED, "pnl": 10.0}],
            sl.BETTOR_EV_SHADOW: [
                {"executionClass": sh.MARKETABLE_RECONSTRUCTED,
                 "status": sh.FILLED, "pnl": 3.0}]})
        rn1 = got["byLane"][sl.RN1_SHADOW]["byExecutionClass"]
        bet = got["byLane"][sl.BETTOR_EV_SHADOW]["byExecutionClass"]
        assert rn1[sh.MARKETABLE_RECONSTRUCTED]["pnl"] == 10.0
        assert bet[sh.MARKETABLE_RECONSTRUCTED]["pnl"] == 3.0

    def test_an_unknown_lane_is_refused(self):
        with pytest.raises(sl.LaneViolation):
            sl.lane_report({"SOME_OTHER_LANE": []})

    def test_the_disclosure_travels_with_the_report(self):
        assert sl.lane_report({})["disclosure"] == sh.DISCLOSURE


# ── the two decision builders ────────────────────────────────────────

class TestTheLaneDecisionBuilders:

    def test_an_rn1_decision_holds_the_belief_fields_closed(self):
        rec = sl.rn1_lane_decision(**BASE, proposedAction=sh.BUY,
                                   reasonCodes=["RN1_BOUGHT"])
        assert rec["lane"] == sl.RN1_SHADOW
        assert rec["pBettorStatus"] == sl.NOT_ESTABLISHED
        assert rec["informationEv"] is None
        assert rec["rn1FeaturesUsed"] is True

    def test_a_bettor_decision_checks_lineage_before_anything_else(self):
        with pytest.raises(sl.LineageViolation):
            sl.bettor_lane_decision({"f": sl.PROV_RN1_ACTION}, **BASE,
                                    proposedAction=sh.BUY,
                                    reasonCodes=["X"])

    def test_a_clean_bettor_decision_records_its_lineage(self):
        rec = sl.bettor_lane_decision(CLEAN, **BASE,
                                      proposedAction=sh.BUY,
                                      reasonCodes=["EDGE"])
        assert rec["lane"] == sl.BETTOR_EV_SHADOW
        assert rec["featureLineage"] == CLEAN
        assert rec["rn1FeaturesUsed"] is False

    def test_not_yet_eligible_is_a_real_recorded_decision(self):
        """'Do not force BUY/SELL output.' The honest NO_TRADE is the
        expected shape until independent EV is earned."""
        rec = sl.not_yet_eligible(**BASE)
        assert rec["proposedAction"] == sh.NO_TRADE
        assert sl.REASON_EV_NOT_ESTABLISHED in rec["reasonCodes"]
        assert rec["pBettor"] is None
        assert rec["pBettorStatus"] == sl.NOT_ESTABLISHED
        assert rec["lane"] == sl.BETTOR_EV_SHADOW

    def test_the_two_lanes_are_the_two_management_named(self):
        assert sl.LANES == ("RN1_SHADOW", "BETTOR_EV_SHADOW")
