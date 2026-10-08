from decimal import Decimal
from bettor_red_team.models import (
    VenueHealth, PositionTruth, ClaimExposure, ReleaseEvidence,
    TwinEvidence, ReadinessInput
)
from bettor_red_team.claim_exposure import aggregate_claim_exposure, exposure_gate
from bettor_red_team.two_leg_sentinel import (
    PairState, DISCOVERED, REVALIDATED, ARMED, PARTIAL, MATCHED, LOCKED,
    REPAIR_REQUIRED, FROZEN, revalidate, arm, fill, lock, guarantee_label
)
from bettor_red_team.truth_quorum import truth_quorum
from bettor_red_team.profit_breaker import ResidualObservation, mechanism_breaker
from bettor_red_team.freshness_guard import isolated_venue_health, pair_market_data_gate
from bettor_red_team.release_guard import release_gate
from bettor_red_team.fee_guard import FeeEvidence, fee_gate
from bettor_red_team.settlement_guard import settlement_certificate_gate
from bettor_red_team.digital_twin_gate import twin_gate
from bettor_red_team.sample_integrity import independent_sample_gate, multiple_testing_gate
from bettor_red_team.capacity_guard import CapacityPoint, capacity_frontier, deployment_cap
from bettor_red_team.ui_truth import metric_truth_gate
from bettor_red_team.karen_value import karen_incremental_value
from bettor_red_team.attribution import reconcile_attribution
from bettor_red_team.credential_guard import credential_slot_gate
from bettor_red_team.stream_guard import stream_currency_gate
from bettor_red_team.migration_guard import migration_guard
from bettor_red_team.readiness import readiness_gate

def test_01_aliases_collapse_to_one_claim():
    rows=[
      ClaimExposure("NYY_WIN","E1","fp","KALSHI","NYY_YES",Decimal("5"),Decimal("250"),"A"),
      ClaimExposure("NYY_WIN","E1","fp","KALSHI","TB_NO",Decimal("5"),Decimal("225"),"A"),
      ClaimExposure("NYY_WIN","E1","fp","POLYMARKET_US","NYY_YES",Decimal("5"),Decimal("235"),"A"),
    ]
    x=aggregate_claim_exposure(rows)["NYY_WIN"]
    assert x["alias_count"]==3 and x["signed_notional"]==Decimal("710")

def test_02_claim_limit_counts_all_aliases():
    rows=[
      ClaimExposure("NYY_WIN","E1","fp","KALSHI","NYY_YES",1,60,"A"),
      ClaimExposure("NYY_WIN","E1","fp","POLYMARKET_US","NYY_YES",1,60,"A"),
    ]
    assert not exposure_gate(rows,max_abs_notional_by_claim=100,max_abs_notional_by_event=500)["eligible"]

def test_03_event_limit_counts_multiple_claims():
    rows=[
      ClaimExposure("A","E1","a","K","1",1,80,"a"),
      ClaimExposure("B","E1","b","P","2",1,80,"b"),
    ]
    assert "EVENT_LIMIT:E1" in exposure_gate(rows,max_abs_notional_by_claim=100,max_abs_notional_by_event=150)["blockers"]

def test_04_pair_cannot_arm_before_revalidation():
    s=PairState(DISCOVERED,10,max_unmatched_loss=Decimal("2"))
    assert arm(s).state==FROZEN

def test_05_pair_revalidation_fails_on_stale_books():
    s=PairState(DISCOVERED,10)
    assert revalidate(s,books_current=False,settlement_current=True,economics_positive=True).reason=="BOOKS_NOT_CURRENT"

def test_06_pair_revalidation_fails_on_changed_settlement():
    s=PairState(DISCOVERED,10)
    assert revalidate(s,books_current=True,settlement_current=False,economics_positive=True).reason=="SETTLEMENT_NOT_CURRENT"

def test_07_pair_revalidation_fails_on_negative_economics():
    s=PairState(DISCOVERED,10)
    assert revalidate(s,books_current=True,settlement_current=True,economics_positive=False).reason=="ECONOMICS_NOT_POSITIVE"

def test_08_pair_partial_fill_is_not_execution_locked():
    s=arm(revalidate(PairState(DISCOVERED,10,max_unmatched_loss=Decimal("10")),
                     books_current=True,settlement_current=True,economics_positive=True))
    s=fill(s,"A",5,Decimal("2"),worst_case_unmatched_loss_per_contract=Decimal("1"))
    assert s.state==PARTIAL and guarantee_label(s)=="NOT_EXECUTION_LOCKED"

def test_09_pair_unmatched_loss_cap_forces_repair():
    s=arm(revalidate(PairState(DISCOVERED,10,max_unmatched_loss=Decimal("2")),
                     books_current=True,settlement_current=True,economics_positive=True))
    s=fill(s,"A",5,Decimal("2"),worst_case_unmatched_loss_per_contract=Decimal("1"))
    assert s.state==REPAIR_REQUIRED

def test_10_pair_only_locks_after_both_full():
    s=arm(revalidate(PairState(DISCOVERED,2,max_unmatched_loss=Decimal("10")),
                     books_current=True,settlement_current=True,economics_positive=True))
    s=fill(s,"A",2,Decimal("1"),worst_case_unmatched_loss_per_contract=Decimal("1"))
    s=fill(s,"B",2,Decimal("1"),worst_case_unmatched_loss_per_contract=Decimal("1"))
    assert s.state==MATCHED
    s=lock(s)
    assert s.state==LOCKED and s.execution_locked

def test_11_overfill_freezes_pair():
    s=arm(revalidate(PairState(DISCOVERED,2,max_unmatched_loss=Decimal("100")),
                     books_current=True,settlement_current=True,economics_positive=True))
    assert fill(s,"A",3,Decimal("1"),worst_case_unmatched_loss_per_contract=Decimal("1")).reason=="OVERFILL"

def _quorum_rows(now=100):
    return [
      PositionTruth("INTERNAL_LEDGER","C",Decimal("5"),Decimal("2.5"),now),
      PositionTruth("VENUE_POSITIONS","C",Decimal("5"),Decimal("2.5"),now),
      PositionTruth("VENUE_BALANCE","BAL",Decimal("1"),None,now),
      PositionTruth("MARKET_DATA","MD",Decimal("1"),None,now),
      PositionTruth("AUDREY_RECONCILIATION","C",Decimal("5"),Decimal("2.5"),now),
    ]

def test_12_truth_quorum_requires_all_sources():
    rows=_quorum_rows()[:-1]
    q=truth_quorum(rows,max_age_s=10,now=100)
    assert not q["green"] and "MISSING_SOURCE:AUDREY_RECONCILIATION" in q["blockers"]

def test_13_truth_quorum_rejects_position_mismatch():
    rows=_quorum_rows()
    rows[1]=PositionTruth("VENUE_POSITIONS","C",Decimal("4"),Decimal("2.5"),100)
    assert not truth_quorum(rows,max_age_s=10,now=100)["green"]

def test_14_truth_quorum_rejects_stale_source():
    rows=_quorum_rows()
    rows[0]=PositionTruth("INTERNAL_LEDGER","C",Decimal("5"),Decimal("2.5"),0)
    assert "STALE_SOURCE:INTERNAL_LEDGER" in truth_quorum(rows,max_age_s=10,now=100)["blockers"]

def test_15_truth_quorum_green_when_sources_agree():
    assert truth_quorum(_quorum_rows(),max_age_s=10,now=100)["green"]

def test_16_profit_breaker_insufficient_sample_stays_shadow():
    rows=[ResidualObservation("ARB",1,1.1) for _ in range(10)]
    assert mechanism_breaker(rows,min_n=30)["ARB"]["status"]=="SHADOW_ONLY"

def test_17_profit_breaker_disables_consistently_negative_residual():
    rows=[ResidualObservation("DIRECTIONAL",1,0) for _ in range(40)]
    assert mechanism_breaker(rows,min_n=30)["DIRECTIONAL"]["status"]=="DISABLED"

def test_18_profit_breaker_can_leave_other_sleeves_eligible():
    rows=[ResidualObservation("ARB",1,1.2) for _ in range(40)] + \
         [ResidualObservation("DIRECTIONAL",1,0) for _ in range(40)]
    r=mechanism_breaker(rows,min_n=30)
    assert r["ARB"]["status"]=="ELIGIBLE" and r["DIRECTIONAL"]["status"]=="DISABLED"

def test_19_venue_health_is_not_blended():
    r=isolated_venue_health([
      VenueHealth("POLYMARKET_US",99,100,0,0,False,100,"PMX_GRPC"),
      VenueHealth("KALSHI",50,100,50,0,False,100,"KALSHI_BOOKS"),
    ],min_rate=.95,max_stale=0,max_gaps=0)
    assert r["POLYMARKET_US"]["green"] and not r["KALSHI"]["green"]

def test_20_cross_venue_pair_requires_both_healthy():
    r={"POLYMARKET_US":{"green":True},"KALSHI":{"green":False}}
    assert not pair_market_data_gate(r,"POLYMARKET_US","KALSHI")["green"]

def test_21_release_must_descend_from_accepted_base():
    e=ReleaseEvidence("a","a","a","base",False,True,True,True,True,True)
    assert "RELEASE_NOT_DESCENDANT_OF_ACCEPTED_BASE" in release_gate(e)["blockers"]

def test_22_release_tested_sha_must_equal_release_sha():
    e=ReleaseEvidence("a","b","b","base",True,True,True,True,True,True)
    assert "RELEASE_SHA_DIFFERS_FROM_TESTED_SHA" in release_gate(e)["blockers"]

def test_23_deployed_sha_must_equal_release_sha():
    e=ReleaseEvidence("a","a","b","base",True,True,True,True,True,True)
    assert "DEPLOYED_SHA_DIFFERS_FROM_RELEASE_SHA" in release_gate(e)["blockers"]

def test_24_all_four_release_gates_required():
    e=ReleaseEvidence("a","a","a","base",True,True,False,True,True,True)
    assert not release_gate(e)["green"]

def test_25_migration_fingerprint_is_part_of_release_gate():
    e=ReleaseEvidence("a","a","a","base",True,True,True,True,True,False)
    assert "MIGRATION_FINGERPRINT_MISMATCH" in release_gate(e)["blockers"]

def test_26_unknown_fee_is_ineligible():
    e=FeeEvidence("KALSHI","M",False,None,None,100,False,False)
    assert not fee_gate(e,now=100)["green"]

def test_27_unknown_maker_fee_does_not_block_known_taker():
    e=FeeEvidence("KALSHI","M",True,"sched","v1",90,False,True)
    g=fee_gate(e,now=100)
    assert g["green"] and g["taker_eligible"] and not g["maker_eligible"]

def test_28_changed_settlement_rules_invalidate_certificate():
    g=settlement_certificate_gate(
      decision_rules_fingerprint="a",current_rules_fingerprint="b",
      outcome_space_exhaustive=True,unknown_states=[],unequal_states=[],
      source_agreement=True)
    assert "RULES_CHANGED_SINCE_CERTIFICATION" in g["blockers"]

def test_29_unknown_settlement_state_blocks():
    g=settlement_certificate_gate(
      decision_rules_fingerprint="a",current_rules_fingerprint="a",
      outcome_space_exhaustive=True,unknown_states=["VOID"],unequal_states=[],
      source_agreement=True)
    assert not g["green"]

def test_30_different_resolution_source_blocks():
    g=settlement_certificate_gate(
      decision_rules_fingerprint="a",current_rules_fingerprint="a",
      outcome_space_exhaustive=True,unknown_states=[],unequal_states=[],
      source_agreement=False)
    assert "RESOLUTION_SOURCE_DIFFERS" in g["blockers"]

def test_31_twin_70_percent_agreement_fails():
    g=twin_gate(TwinEvidence(1000,701,200,0,{"IOC":200}))
    assert not g["green"]

def test_32_twin_lookahead_always_fails():
    g=twin_gate(TwinEvidence(100,100,0,1,{}))
    assert "LOOKAHEAD_VIOLATIONS_PRESENT" in g["blockers"]

def test_33_twin_can_certify_above_threshold():
    g=twin_gate(TwinEvidence(1000,990,5,0,{}))
    assert g["green"]

def test_34_repeated_rows_do_not_replace_independent_events():
    g=independent_sample_gate(
      decision_rows=13000,independent_events=195,
      train_events={"a"},test_events={"b"},holdout_events={"c"},
      holdout_open_count=1,min_independent_events=200)
    assert not g["green"] and g["row_to_event_ratio"]>60

def test_35_event_leakage_is_rejected():
    g=independent_sample_gate(
      decision_rows=100,independent_events=100,
      train_events={"a"},test_events={"a"},holdout_events={"c"},
      holdout_open_count=1,min_independent_events=50)
    assert "TRAIN_TEST_EVENT_LEAKAGE" in g["blockers"]

def test_36_holdout_may_only_be_opened_once():
    g=independent_sample_gate(
      decision_rows=100,independent_events=100,
      train_events={"a"},test_events={"b"},holdout_events={"c"},
      holdout_open_count=2,min_independent_events=50)
    assert "HOLDOUT_OPENED_MORE_THAN_ONCE" in g["blockers"]

def test_37_strategy_mining_after_holdout_is_blocked():
    g=multiple_testing_gate(candidates_tested=10,pre_registered=10,
                            selected_after_holdout=True,pbo_ok=True,dsr_ok=True)
    assert not g["green"]

def test_38_unregistered_candidates_are_blocked():
    g=multiple_testing_gate(candidates_tested=20,pre_registered=10,
                            selected_after_holdout=False,pbo_ok=True,dsr_ok=True)
    assert "UNREGISTERED_CANDIDATES_TESTED" in g["blockers"]

def test_39_capacity_requires_positive_lower_bound_and_fill_probability():
    pts=[CapacityPoint(10,Decimal(".01"),Decimal(".9"),Decimal("1"),Decimal("1")),
         CapacityPoint(20,Decimal("-.01"),Decimal(".9"),Decimal("2"),Decimal("-1"))]
    g=capacity_frontier(pts)
    assert g["max_positive_qty"]==10

def test_40_turnover_target_never_overrides_capacity():
    assert deployment_cap(requested_turnover=Decimal("500000"),
                          proven_positive_capacity=Decimal("82000"))==Decimal("82000")

def test_41_ui_green_metric_must_not_be_stale():
    g=metric_truth_gate({"value":1,"as_of":0,"source":"x","status":"GREEN"},now=100,max_age_s=10)
    assert not g["green"]

def test_42_ui_ratio_needs_denominator():
    g=metric_truth_gate({"value":.9,"as_of":100,"source":"x","status":"RED","numerator":9},now=100,max_age_s=10)
    assert "DENOMINATOR_EVIDENCE_INCOMPLETE" in g["blockers"]

def test_43_karen_value_subtracts_false_block_cost():
    assert karen_incremental_value(100,30)==Decimal("70")

def test_44_attribution_must_reconcile_to_realized():
    g=reconcile_attribution(realized_pnl=100,components={
      "selection":20,"execution":10,"allocation":10,"management":10,
      "settlement":0,"outcome_variance":50})
    assert g["green"]

def test_45_double_counted_attribution_fails():
    g=reconcile_attribution(realized_pnl=100,components={
      "selection":50,"execution":50,"allocation":50,"management":0,
      "settlement":0,"outcome_variance":0})
    assert not g["green"]

def test_46_credential_class_mismatch_is_blocked():
    g=credential_slot_gate({
      "PMX":"POLYMARKET_EXCHANGE_RSA_M2M",
      "PMUS":"POLYMARKET_EXCHANGE_RSA_M2M",
      "KALSHI":"KALSHI_RSA_API_KEY"})
    assert any(x.startswith("CREDENTIAL_CLASS_MISMATCH:PMUS") for x in g["blockers"])

def test_47_reconnect_without_snapshot_is_not_fresh():
    g=stream_currency_gate(connected=True,complete_snapshot_received=False,gap=False,
                           book_age_s=0,max_book_age_s=5,source="PMX_GRPC")
    assert not g["green"]

def test_48_stream_gap_is_not_fresh():
    g=stream_currency_gate(connected=True,complete_snapshot_received=True,gap=True,
                           book_age_s=0,max_book_age_s=5,source="PMX_GRPC")
    assert "STREAM_GAP" in g["blockers"]

def test_49_applied_migration_history_cannot_be_edited():
    g=migration_guard(applied_fingerprint="a",repo_fingerprint="a",
                      fresh_db_passed=True,forward_only=False)
    assert "APPLIED_HISTORY_EDITED_IN_PLACE" in g["blockers"]

def test_50_final_readiness_requires_every_interlock():
    g=readiness_gate(ReadinessInput(
      True,True,True,True,True,True,True,True,True,True,True,True,True,True))
    assert g["status"]=="CAPITAL_CANDIDATE"

def test_51_one_failed_interlock_keeps_paper_shadow_only():
    g=readiness_gate(ReadinessInput(
      True,True,True,True,True,True,True,True,False,True,True,True,True,True))
    assert g["status"]=="PAPER_SHADOW_ONLY" and "positive_edge" in g["blockers"]
