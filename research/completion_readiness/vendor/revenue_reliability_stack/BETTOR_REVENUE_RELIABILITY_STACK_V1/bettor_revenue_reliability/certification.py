from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Iterable
import math

VALID_LICENSES = ("LICENSED", "PROBATION", "SHADOW_ONLY", "SUSPENDED")

@dataclass(frozen=True)
class AgentEvidence:
    agent_id: str
    independent_events: int
    expected_value_usd: float
    realized_value_usd: float
    lower_bound_value_per_event: float
    calibration_error: float | None = None
    reconciliation_error_usd: float | None = None
    false_block_rate: float | None = None
    evidence_complete: bool = True
    current_authority: str = ""

@dataclass(frozen=True)
class Certification:
    agent_id: str
    license: str
    reason: str
    evidence: dict

def certify_agent(
    ev: AgentEvidence,
    *,
    minimum_events: int = 100,
    max_calibration_error: float = 0.05,
    max_reconciliation_error_usd: float = 1e-6,
    max_false_block_rate: float = 0.20,
) -> Certification:
    reasons=[]
    if not ev.evidence_complete:
        reasons.append("EVIDENCE_INCOMPLETE")
    if ev.independent_events < minimum_events:
        reasons.append("INSUFFICIENT_INDEPENDENT_EVENTS")
    if ev.lower_bound_value_per_event <= 0:
        reasons.append("POSITIVE_LOWER_BOUND_NOT_PROVEN")
    if ev.calibration_error is not None and ev.calibration_error > max_calibration_error:
        reasons.append("CALIBRATION_ERROR_TOO_HIGH")
    if ev.reconciliation_error_usd is not None and abs(ev.reconciliation_error_usd) > max_reconciliation_error_usd:
        reasons.append("RECONCILIATION_ERROR_TOO_HIGH")
    if ev.false_block_rate is not None and ev.false_block_rate > max_false_block_rate:
        reasons.append("FALSE_BLOCK_RATE_TOO_HIGH")

    if "RECONCILIATION_ERROR_TOO_HIGH" in reasons or (
        not ev.evidence_complete and ev.independent_events >= minimum_events
    ):
        license_="SUSPENDED"
    elif reasons:
        license_="SHADOW_ONLY" if ev.independent_events < minimum_events else "PROBATION"
    else:
        license_="LICENSED"

    return Certification(
        agent_id=ev.agent_id,
        license=license_,
        reason=";".join(reasons) if reasons else "POSITIVE_INDEPENDENT_VALUE_PROVEN",
        evidence=asdict(ev),
    )

def certification_does_not_expand_authority(cert: Certification, current_authority: str) -> dict:
    """Certification is an economic status only; never an authority grant."""
    return {
        "agent_id": cert.agent_id,
        "license": cert.license,
        "current_authority": current_authority,
        "authority_changed": False,
        "capital_authority_granted": False,
    }
