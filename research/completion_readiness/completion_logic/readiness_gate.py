from __future__ import annotations
from dataclasses import dataclass, asdict

@dataclass(frozen=True)
class ReadinessEvidence:
    no_oom_minutes: float
    worker_rss_highwater_fraction: float
    priority_freshness_rate: float | None
    management_freshness_rate: float | None
    software_red_count: int
    xavier_packet_complete_rate: float | None
    canary_pass: bool
    probability_edge_lb: float | None
    forward_independent_events: int
    digital_twin_fill_agreement: float | None
    settlement_proven: bool
    capacity_proven: bool
    mirror_venue_confirmed: bool
    small_live_shadow: bool
    historical_paper_immutable: bool

@dataclass(frozen=True)
class ReadinessDecision:
    status: str
    blockers: tuple[str,...]
    evidence: dict

def evaluate_readiness(
    e: ReadinessEvidence,
    *,
    minimum_no_oom_minutes: float = 60.0,
    max_rss_fraction: float = 0.85,
    minimum_freshness: float = 0.95,
    minimum_packet_rate: float = 0.95,
    minimum_forward_events: int = 100,
    minimum_twin_agreement: float = 0.95,
) -> ReadinessDecision:
    blockers=[]
    if not e.small_live_shadow:
        blockers.append("SMALL_LIVE_NOT_SHADOW")
    if not e.historical_paper_immutable:
        blockers.append("HISTORICAL_PAPER_MUTABILITY")
    if e.no_oom_minutes < minimum_no_oom_minutes:
        blockers.append("RUNTIME_SOAK_INSUFFICIENT")
    if e.worker_rss_highwater_fraction >= max_rss_fraction:
        blockers.append("WORKER_MEMORY_HEADROOM_INSUFFICIENT")
    if e.priority_freshness_rate is None or e.priority_freshness_rate < minimum_freshness:
        blockers.append("PRIORITY_FRESHNESS_BELOW_TARGET")
    if e.management_freshness_rate is None or e.management_freshness_rate < minimum_freshness:
        blockers.append("MANAGEMENT_FRESHNESS_BELOW_TARGET")
    if e.software_red_count != 0:
        blockers.append("CODE_CONTROLLED_REDS_REMAIN")
    if e.xavier_packet_complete_rate is None or e.xavier_packet_complete_rate < minimum_packet_rate:
        blockers.append("XAVIER_PACKET_COMPLETENESS_BELOW_TARGET")
    if not e.canary_pass:
        blockers.append("PRODUCTION_CANARY_NOT_GREEN")
    if e.probability_edge_lb is None or e.probability_edge_lb <= 0:
        blockers.append("POSITIVE_PROBABILITY_EDGE_NOT_PROVEN")
    if e.forward_independent_events < minimum_forward_events:
        blockers.append("FORWARD_INDEPENDENT_SAMPLE_INSUFFICIENT")
    if e.digital_twin_fill_agreement is None or e.digital_twin_fill_agreement < minimum_twin_agreement:
        blockers.append("DIGITAL_TWIN_NOT_CERTIFIED")
    if not e.settlement_proven:
        blockers.append("SETTLEMENT_IDENTITY_NOT_PROVEN")
    if not e.capacity_proven:
        blockers.append("POSITIVE_CAPACITY_NOT_PROVEN")
    if not e.mirror_venue_confirmed:
        blockers.append("VENUE_POSITION_CONFIRMATION_NOT_PROVEN")
    return ReadinessDecision(
        "CAPITAL_CANDIDATE" if not blockers else "PAPER_SHADOW_ONLY",
        tuple(blockers),
        asdict(e),
    )
