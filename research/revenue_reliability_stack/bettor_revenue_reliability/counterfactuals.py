from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class CounterfactualScore:
    agent_id: str
    baseline: str
    actual_value_usd: float
    baseline_value_usd: float
    incremental_value_usd: float
    status: str
    reason: str

def score_increment(agent_id: str, baseline: str, actual_value_usd: float, baseline_value_usd: float) -> CounterfactualScore:
    inc=float(actual_value_usd)-float(baseline_value_usd)
    return CounterfactualScore(
        agent_id=agent_id,
        baseline=baseline,
        actual_value_usd=float(actual_value_usd),
        baseline_value_usd=float(baseline_value_usd),
        incremental_value_usd=inc,
        status="POSITIVE_VALUE_ADD" if inc>0 else "NO_POSITIVE_VALUE_ADD",
        reason="ACTUAL_BEAT_PREDECLARED_COUNTERFACTUAL" if inc>0 else "ACTUAL_DID_NOT_BEAT_PREDECLARED_COUNTERFACTUAL",
    )

def xavier_management_alpha(actual_pnl: float, hold_to_settlement_pnl: float, immediate_exit_pnl: float) -> dict:
    """Uses Xavier's existing frozen counterfactual design from migration 206."""
    vs_hold=score_increment("XAVIER","HOLD_TO_SETTLEMENT",actual_pnl,hold_to_settlement_pnl)
    vs_exit=score_increment("XAVIER","IMMEDIATE_EXIT",actual_pnl,immediate_exit_pnl)
    conservative=min(vs_hold.incremental_value_usd,vs_exit.incremental_value_usd)
    return {
        "vs_hold":vs_hold,
        "vs_immediate_exit":vs_exit,
        "conservative_incremental_value_usd":conservative,
        "positive_against_both":conservative>0,
    }

def archer_execution_alpha(realized_execution_value: float, feasible_benchmark_value: float) -> CounterfactualScore:
    return score_increment("ARCHER","BEST_FEASIBLE_EXECUTION_BENCHMARK",realized_execution_value,feasible_benchmark_value)

def allie_allocation_alpha(realized_portfolio_value: float, equal_weight_value: float) -> CounterfactualScore:
    return score_increment("CHIEF_ALLOCATOR","EQUAL_WEIGHT_POSITIVE_ELIGIBLE_SET",realized_portfolio_value,equal_weight_value)

def derek_entry_alpha(realized_entry_value: float, market_prior_baseline_value: float) -> CounterfactualScore:
    return score_increment("DEREK","MARKET_PRIOR_OR_CASH_BASELINE",realized_entry_value,market_prior_baseline_value)

def karen_challenge_value(saved_loss_usd: float, false_block_opportunity_cost_usd: float) -> CounterfactualScore:
    return score_increment("KAREN","NO_CHALLENGE",saved_loss_usd,-abs(false_block_opportunity_cost_usd))

def audrey_reconciliation_score(unexplained_residual_usd: float, tolerance_usd: float=1e-6) -> dict:
    return {
        "agent_id":"AUDREY",
        "unexplained_residual_usd":float(unexplained_residual_usd),
        "tolerance_usd":float(tolerance_usd),
        "reconciled":abs(float(unexplained_residual_usd))<=float(tolerance_usd),
    }
