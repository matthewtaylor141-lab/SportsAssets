"""24/7 market-data completeness auditor (Radar)."""
from __future__ import annotations

VERSION="RADAR_AUDITOR_V1"


def audit(*, venue_active:int, registry_active:int, subscribed:int, fresh:int,
          coverage:dict, catalogue_complete:bool, shard_complete:bool,
          latency:dict|None=None)->dict:
    missing=max(0,int(venue_active)-int(registry_active))
    unmapped=(coverage.get("by_state") or {}).get("CODE_CONTROLLED_GAP",0)
    failures=[]
    if missing: failures.append("ACTIVE_CONTRACTS_MISSING_FROM_REGISTRY")
    if not catalogue_complete: failures.append("CATALOGUE_NOT_PROVEN_COMPLETE")
    if not shard_complete: failures.append("SUBSCRIPTION_CAPACITY_OVERFLOW")
    if subscribed < registry_active: failures.append("ACTIVE_REGISTRY_NOT_FULLY_SUBSCRIBED")
    if fresh < registry_active: failures.append("ACTIVE_REGISTRY_NOT_FULLY_FRESH")
    if unmapped: failures.append("CODE_CONTROLLED_COVERAGE_GAPS")
    if latency and not latency.get("green",False): failures.append("INTERNAL_LATENCY_SLO_RED")
    return {"green":not failures,"failures":failures,"venue_active":venue_active,
            "registry_active":registry_active,"missing":missing,"subscribed":subscribed,
            "fresh":fresh,"coverage":coverage.get("by_state"),"version":VERSION}
