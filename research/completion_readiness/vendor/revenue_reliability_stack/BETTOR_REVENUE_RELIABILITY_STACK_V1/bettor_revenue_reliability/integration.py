from __future__ import annotations
from dataclasses import asdict
from .current_contract import AGENTS, LIFECYCLE_STATES, validate_contract
from .certification import AgentEvidence, certify_agent, certification_does_not_expand_authority
from .tournament import CandidateMetrics, run_tournament
from .regime import RegimeEvidence, decide_regime
from .reliability import StrategyEvidence, optimize_reliable_portfolio
from .daily_contract import StrategyReadiness, build_daily_brief

def run_shadow_cycle(
    *,
    agent_evidence: list[AgentEvidence],
    candidates: list[CandidateMetrics],
    regime_evidence: list[RegimeEvidence],
    strategies: list[StrategyEvidence],
    bankroll_usd: float,
    correlation=None,
) -> dict:
    """One additive research/PAPER-SHADOW management cycle.

    This function intentionally never creates an order, changes a lifecycle
    row, raises a risk cap, or grants authority. It produces evidence-only
    recommendations that Claude can wire into the existing governed path.
    """
    validate_contract()

    certifications={}
    for ev in agent_evidence:
        cert=certify_agent(ev)
        authority=AGENTS.get(ev.agent_id).current_authority if ev.agent_id in AGENTS else ev.current_authority
        certifications[ev.agent_id]={
            "certification":asdict(cert),
            "authority":certification_does_not_expand_authority(cert,authority),
        }

    tournament=run_tournament(candidates,incumbent="CASH")
    regimes=[asdict(decide_regime(r)) for r in regime_evidence]
    plan=optimize_reliable_portfolio(
        strategies,bankroll=bankroll_usd,correlation=correlation
    )
    readiness=[
        StrategyReadiness(
            strategy=s.name,
            lifecycle=s.current_lifecycle,
            independent_events=s.independent_events,
            lower_bound_daily_profit=s.lower_bound_daily_profit,
            positive_capacity_usd=s.capacity_usd if s.lower_bound_daily_profit>0 else 0.0,
            capital_hour_profit=s.capital_hour_profit,
            primary_blocker=plan.reasons.get(s.name) if plan.reasons.get(s.name)!="ELIGIBLE" else None,
        )
        for s in strategies
    ]
    brief=build_daily_brief(
        bankroll_usd,readiness,plan.allocations,plan.expected_daily_profit
    )

    return {
        "mode":"RESEARCH_PAPER_SHADOW_ONLY",
        "authority_changed":False,
        "certifications":certifications,
        "tournament":asdict(tournament),
        "regimes":regimes,
        "portfolio_plan":asdict(plan),
        "daily_revenue_readiness":asdict(brief),
    }
