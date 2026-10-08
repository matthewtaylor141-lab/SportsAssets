from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping

RED="RED"; YELLOW="YELLOW"; GREEN="GREEN"

CRITICAL=(
 "release_lineage","exact_sha","backend_tests","capital_critical","commit_guard",
 "engine_diagnostic","migration_integrity","no_oom","shared_worker_ump_isolated",
 "held_freshness","priority_freshness","truth_quorum","software_red_zero",
 "canary","credential_classes","historical_paper_immutable","live_authority_shadow"
)
ECONOMIC=(
 "pmx_grpc_primary","kalshi_market_data_live","venue_health_isolated",
 "settlement_proven","digital_twin_certified","xavier_complete",
 "positive_forward_edge","positive_capacity","profitability_governor_positive",
 "mechanism_breakers_green"
)

def bool_gate(name,value,why=None):
    return {"name":name,"pass":bool(value),"why":why if not value else None}

def derive_gates(e:Mapping[str,Any],spec:Mapping[str,Any])->dict:
    t=spec["thresholds"]
    tested=e.get("tested_sha"); release=e.get("release_sha"); deployed=e.get("deployed_sha")
    held_den=int(e.get("held_required",0) or 0)
    priority_den=int(e.get("priority_required",0) or 0)
    held_rate=None if held_den<=0 else float(e.get("held_fresh",0))/held_den
    priority_rate=None if priority_den<=0 else float(e.get("priority_fresh",0))/priority_den
    twin_comp=int(e.get("twin_compared",0) or 0)
    twin_agree=None if twin_comp<=0 else float(e.get("twin_matched",0))/twin_comp
    twin_false=None if twin_comp<=0 else float(e.get("twin_optimistic_false_fills",0))/twin_comp

    g={}
    g["release_lineage"]=bool_gate("release_lineage",e.get("is_descendant_of_accepted_base") is True,
                                    "candidate must descend from accepted production base")
    g["exact_sha"]=bool_gate("exact_sha",bool(tested and tested==release==deployed),
                              f"tested={tested} release={release} deployed={deployed}")
    for k in ("backend_tests","capital_critical","commit_guard","engine_diagnostic",
              "migration_integrity","truth_quorum","canary","credential_classes",
              "historical_paper_immutable","live_authority_shadow"):
        g[k]=bool_gate(k,e.get(k) is True)
    g["no_oom"]=bool_gate("no_oom",
        float(e.get("no_oom_minutes",0))>=float(t["min_no_oom_minutes"])
        and float(e.get("worker_rss_fraction",99))<float(t["max_worker_rss_fraction"]),
        f"no_oom_minutes={e.get('no_oom_minutes')} rss_fraction={e.get('worker_rss_fraction')}")
    g["shared_worker_ump_isolated"]=bool_gate(
        "shared_worker_ump_isolated",
        e.get("shared_worker_starts_ump") is False and e.get("dedicated_market_plane_present") is True)
    g["held_freshness"]=bool_gate("held_freshness",
        held_rate is not None and held_rate>=float(t["min_held_freshness"]),
        f"{e.get('held_fresh',0)}/{held_den}={held_rate}")
    g["priority_freshness"]=bool_gate("priority_freshness",
        priority_rate is not None and priority_rate>=float(t["min_priority_freshness"]),
        f"{e.get('priority_fresh',0)}/{priority_den}={priority_rate}")
    g["software_red_zero"]=bool_gate("software_red_zero",
        int(e.get("software_red_count",999))<=int(t["max_software_red_count"]),
        f"software_red_count={e.get('software_red_count')}")
    g["pmx_grpc_primary"]=bool_gate("pmx_grpc_primary",
        e.get("pmx_primary_source")=="PMX_GRPC" and int(e.get("pmx_grpc_fresh_count",0))>0)
    g["kalshi_market_data_live"]=bool_gate("kalshi_market_data_live",
        e.get("kalshi_market_data_current") is True and int(e.get("kalshi_mapped_markets",0))>0)
    g["venue_health_isolated"]=bool_gate("venue_health_isolated",
        e.get("venue_health_isolated") is True)
    g["settlement_proven"]=bool_gate("settlement_proven",
        e.get("settlement_proven") is True)
    g["digital_twin_certified"]=bool_gate("digital_twin_certified",
        twin_agree is not None and twin_agree>=float(t["min_twin_agreement"])
        and twin_false is not None and twin_false<=float(t["max_twin_optimistic_false_fill_rate"])
        and int(e.get("twin_lookahead_violations",999))==0,
        f"agreement={twin_agree} false_fill_rate={twin_false} lookahead={e.get('twin_lookahead_violations')}")
    g["xavier_complete"]=bool_gate("xavier_complete",
        float(e.get("xavier_complete_rate",0))>=float(t["min_xavier_complete"]),
        f"xavier_complete_rate={e.get('xavier_complete_rate')}")
    g["positive_forward_edge"]=bool_gate("positive_forward_edge",
        e.get("forward_edge_lower_bound") is not None and float(e.get("forward_edge_lower_bound"))>0)
    g["positive_capacity"]=bool_gate("positive_capacity",
        e.get("proven_positive_capacity") is not None and float(e.get("proven_positive_capacity"))>0)
    g["profitability_governor_positive"]=bool_gate("profitability_governor_positive",
        e.get("profitability_governor") not in (None,"CASH","DISABLE","SHADOW_ONLY"))
    g["mechanism_breakers_green"]=bool_gate("mechanism_breakers_green",
        e.get("mechanism_breakers_green") is True)
    return g

def evaluate(evidence:Mapping[str,Any],spec:Mapping[str,Any])->dict:
    gates=derive_gates(evidence,spec)
    critical_fail=[k for k in CRITICAL if not gates[k]["pass"]]
    economic_fail=[k for k in ECONOMIC if not gates[k]["pass"]]
    if critical_fail:
        state=RED
    elif economic_fail:
        state=YELLOW
    else:
        state=GREEN
    return {
      "pm_state":state,
      "capital_status":"CAPITAL_CANDIDATE" if state==GREEN else "PAPER_SHADOW_ONLY",
      "critical_failures":critical_fail,
      "economic_or_evidence_gaps":economic_fail,
      "gates":gates,
      "summary":{
        "tested_sha":evidence.get("tested_sha"),
        "release_sha":evidence.get("release_sha"),
        "deployed_sha":evidence.get("deployed_sha"),
        "profitability_governor":evidence.get("profitability_governor"),
        "paper_pnl":evidence.get("paper_pnl"),
        "proven_positive_capacity":evidence.get("proven_positive_capacity"),
      }
    }
