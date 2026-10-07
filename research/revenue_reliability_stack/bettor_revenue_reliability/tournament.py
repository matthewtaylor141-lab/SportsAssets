from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
import math
import numpy as np

@dataclass(frozen=True)
class CandidateMetrics:
    name: str
    independent_events: int
    mean_net_per_event: float
    lower_bound_net_per_event: float
    brier: float | None = None
    log_loss: float | None = None
    calibration_error: float | None = None
    drawdown_usd: float = 0.0
    capacity_usd: float = 0.0
    capital_hour_profit: float | None = None
    evidence_complete: bool = True

@dataclass(frozen=True)
class TournamentDecision:
    incumbent: str
    selected: str
    status: str
    reason: str
    rankings: list[dict]

def _rank_score(c: CandidateMetrics) -> tuple:
    # Positive lower bound and capital-hour economics dominate. Lower
    # calibration/Brier/log-loss are tie-breakers, never substitutes for profit.
    return (
        c.lower_bound_net_per_event,
        c.capital_hour_profit if c.capital_hour_profit is not None else -math.inf,
        -c.drawdown_usd,
        -(c.calibration_error if c.calibration_error is not None else math.inf),
        -(c.brier if c.brier is not None else math.inf),
        -(c.log_loss if c.log_loss is not None else math.inf),
    )

def run_tournament(
    candidates: Sequence[CandidateMetrics],
    *,
    incumbent: str = "CASH",
    minimum_events: int = 100,
    require_absolute_positive: bool = True,
) -> TournamentDecision:
    rankings=[]
    eligible=[]
    for c in candidates:
        blockers=[]
        if not c.evidence_complete: blockers.append("EVIDENCE_INCOMPLETE")
        if c.independent_events < minimum_events: blockers.append("INSUFFICIENT_EVENTS")
        if require_absolute_positive and c.lower_bound_net_per_event <= 0:
            blockers.append("ABSOLUTE_POSITIVE_LOWER_BOUND_NOT_PROVEN")
        rankings.append({
            "name":c.name,
            "eligible":not blockers,
            "blockers":blockers,
            "lower_bound_net_per_event":c.lower_bound_net_per_event,
            "mean_net_per_event":c.mean_net_per_event,
            "independent_events":c.independent_events,
        })
        if not blockers:
            eligible.append(c)

    if not eligible:
        return TournamentDecision(
            incumbent=incumbent,
            selected="CASH",
            status="NO_PROMOTION",
            reason="NO_CANDIDATE_BEATS_CASH_ON_ABSOLUTE_FORWARD_ECONOMICS",
            rankings=rankings,
        )

    best=max(eligible,key=_rank_score)
    return TournamentDecision(
        incumbent=incumbent,
        selected=best.name,
        status="CHALLENGER_SELECTED" if incumbent=="CASH" else "PROMOTION_CANDIDATE",
        reason="BEST_ELIGIBLE_ABSOLUTE_POSITIVE_FORWARD_CANDIDATE",
        rankings=rankings,
    )
