from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class RegimeEvidence:
    sport: str
    family: str
    regime: str
    independent_events: int
    calibration_error: float | None
    lower_bound_net_ev: float | None
    freshness_ok: bool
    settlement_ok: bool
    evidence_complete: bool = True

@dataclass(frozen=True)
class RegimeDecision:
    authority: str
    reason: str
    segment: tuple[str,str,str]

def decide_regime(
    e: RegimeEvidence,
    *,
    minimum_events: int = 100,
    max_calibration_error: float = 0.05,
) -> RegimeDecision:
    seg=(e.sport,e.family,e.regime)
    if not e.evidence_complete:
        return RegimeDecision("ABSTAIN","UNMEASURED_OR_INCOMPLETE_EVIDENCE",seg)
    if not e.freshness_ok:
        return RegimeDecision("ABSTAIN","FRESHNESS_NOT_PROVEN",seg)
    if not e.settlement_ok:
        return RegimeDecision("ABSTAIN","SETTLEMENT_IDENTITY_NOT_PROVEN",seg)
    if e.independent_events < minimum_events:
        return RegimeDecision("SHADOW_ONLY","INSUFFICIENT_INDEPENDENT_EVENTS",seg)
    if e.calibration_error is None or e.calibration_error > max_calibration_error:
        return RegimeDecision("SHADOW_ONLY","CALIBRATION_NOT_PROVEN",seg)
    if e.lower_bound_net_ev is None or e.lower_bound_net_ev <= 0:
        return RegimeDecision("CASH","POSITIVE_EXECUTABLE_EV_NOT_PROVEN",seg)
    return RegimeDecision("ELIGIBLE_FOR_EXISTING_GATED_PATH","POSITIVE_REGIME_EVIDENCE",seg)
