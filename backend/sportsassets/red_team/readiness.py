from __future__ import annotations
from .models import ReadinessInput, CAPITAL_CANDIDATE, PAPER_SHADOW_ONLY

def readiness_gate(i:ReadinessInput)->dict:
    checks={
      "release":i.release_ok,
      "runtime":i.runtime_ok,
      "market_data":i.market_data_ok,
      "held_freshness":i.held_freshness_ok,
      "priority_freshness":i.priority_freshness_ok,
      "settlement":i.settlement_ok,
      "digital_twin":i.twin_ok,
      "reconciliation":i.reconciliation_ok,
      "positive_edge":i.positive_edge_ok,
      "capacity":i.capacity_ok,
      "canonical_exposure":i.claim_exposure_ok,
      "profit_breakers":i.profit_breakers_ok,
      "live_authority_shadow":i.live_authority_still_shadow,
      "historical_paper_immutable":i.historical_paper_immutable,
    }
    blockers=tuple(k for k,v in checks.items() if not v)
    return {"status":CAPITAL_CANDIDATE if not blockers else PAPER_SHADOW_ONLY,
            "checks":checks,"blockers":blockers}
