import json
from pathlib import Path
from pm_acceptance.harness import evaluate

SPEC=json.loads((Path(__file__).parents[1]/"acceptance_spec.json").read_text())

def base():
    return {
      "accepted_base_sha":"base","tested_sha":"abc","release_sha":"abc","deployed_sha":"abc",
      "is_descendant_of_accepted_base":True,
      "backend_tests":True,"capital_critical":True,"commit_guard":True,"engine_diagnostic":True,
      "migration_integrity":True,"truth_quorum":True,"canary":True,"credential_classes":True,
      "historical_paper_immutable":True,"live_authority_shadow":True,
      "no_oom_minutes":120,"worker_rss_fraction":.60,
      "shared_worker_starts_ump":False,"dedicated_market_plane_present":True,
      "held_fresh":100,"held_required":100,"priority_fresh":190,"priority_required":200,
      "software_red_count":0,
      "pmx_primary_source":"PMX_GRPC","pmx_grpc_fresh_count":100,
      "kalshi_market_data_current":True,"kalshi_mapped_markets":200,
      "venue_health_isolated":True,"settlement_proven":True,
      "twin_compared":1000,"twin_matched":980,"twin_optimistic_false_fills":5,"twin_lookahead_violations":0,
      "xavier_complete_rate":.99,
      "forward_edge_lower_bound":.01,
      "proven_positive_capacity":100000,
      "profitability_governor":"ELIGIBLE",
      "mechanism_breakers_green":True,
      "paper_pnl":1000,
    }

def test_all_green_is_green_capital_candidate():
    r=evaluate(base(),SPEC)
    assert r["pm_state"]=="GREEN" and r["capital_status"]=="CAPITAL_CANDIDATE"

def test_sha_mismatch_is_red():
    e=base(); e["deployed_sha"]="zzz"
    r=evaluate(e,SPEC)
    assert r["pm_state"]=="RED" and "exact_sha" in r["critical_failures"]

def test_non_descendant_release_is_red():
    e=base(); e["is_descendant_of_accepted_base"]=False
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_oom_is_red():
    e=base(); e["no_oom_minutes"]=10
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_high_rss_is_red():
    e=base(); e["worker_rss_fraction"]=.90
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_shared_worker_ump_is_red():
    e=base(); e["shared_worker_starts_ump"]=True
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_held_stale_is_red_even_when_priority_green():
    e=base(); e["held_fresh"]=94
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_software_red_is_red():
    e=base(); e["software_red_count"]=1
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_truth_quorum_failure_is_red():
    e=base(); e["truth_quorum"]=False
    assert evaluate(e,SPEC)["pm_state"]=="RED"

def test_pmx_rest_only_is_yellow_when_integrity_green():
    e=base(); e["pmx_primary_source"]="REST"
    r=evaluate(e,SPEC)
    assert r["pm_state"]=="YELLOW" and "pmx_grpc_primary" in r["economic_or_evidence_gaps"]

def test_kalshi_not_current_is_yellow():
    e=base(); e["kalshi_market_data_current"]=False
    assert evaluate(e,SPEC)["pm_state"]=="YELLOW"

def test_uncertified_twin_is_yellow():
    e=base(); e["twin_matched"]=700
    assert evaluate(e,SPEC)["pm_state"]=="YELLOW"

def test_cash_governor_is_yellow_not_green():
    e=base(); e["profitability_governor"]="CASH"
    r=evaluate(e,SPEC)
    assert r["pm_state"]=="YELLOW" and r["capital_status"]=="PAPER_SHADOW_ONLY"

def test_negative_forward_edge_is_yellow():
    e=base(); e["forward_edge_lower_bound"]=-.01
    assert evaluate(e,SPEC)["pm_state"]=="YELLOW"

def test_zero_capacity_is_yellow():
    e=base(); e["proven_positive_capacity"]=0
    assert evaluate(e,SPEC)["pm_state"]=="YELLOW"

def test_xavier_below_95_is_yellow():
    e=base(); e["xavier_complete_rate"]=.94
    assert evaluate(e,SPEC)["pm_state"]=="YELLOW"
