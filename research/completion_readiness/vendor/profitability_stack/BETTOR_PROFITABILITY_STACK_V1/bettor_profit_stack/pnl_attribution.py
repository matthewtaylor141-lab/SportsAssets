from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import defaultdict
import math

@dataclass(frozen=True)
class AttributionInput:
    decision_id: str
    strategy: str
    sport: str
    family: str
    regime: str
    venue: str
    qty: float

    # Probability / outcome
    market_prior_p: float
    model_p: float
    calibrated_p: float
    outcome: float

    # Entry economics per contract
    expected_fill_price: float
    actual_fill_price: float
    expected_execution_cost: float
    actual_execution_cost: float

    # Management economics per contract
    expected_management_value: float
    realized_management_value: float

    # Settlement economics per contract
    expected_settlement_adjustment: float
    realized_settlement_adjustment: float

@dataclass(frozen=True)
class Attribution:
    decision_id: str
    expected_profit: float
    realized_profit: float
    residual: float
    forecast_residual: float
    execution_residual: float
    management_residual: float
    settlement_residual: float
    outcome_variance: float
    reconciliation_error: float
    dimensions: dict

def attribute(inp: AttributionInput) -> Attribution:
    q=float(inp.qty)

    # Expected profit uses calibrated probability and expected execution.
    expected_per_contract = (
        inp.calibrated_p
        - inp.expected_fill_price
        - inp.expected_execution_cost
        + inp.expected_management_value
        + inp.expected_settlement_adjustment
    )

    # Realized profit is the actual binary payoff less actual execution plus
    # realized management/settlement adjustments.
    realized_per_contract = (
        inp.outcome
        - inp.actual_fill_price
        - inp.actual_execution_cost
        + inp.realized_management_value
        + inp.realized_settlement_adjustment
    )

    # Attribution ladder:
    # market prior -> model -> calibrated -> realized outcome
    # We separately expose model-vs-market and calibration through forecast
    # residual aggregate; outcome variance captures realized outcome minus
    # calibrated expectation.
    forecast_residual = (inp.calibrated_p - inp.market_prior_p) * q
    outcome_variance = (inp.outcome - inp.calibrated_p) * q

    execution_residual = (
        (inp.expected_fill_price - inp.actual_fill_price)
        + (inp.expected_execution_cost - inp.actual_execution_cost)
    ) * q
    management_residual = (inp.realized_management_value - inp.expected_management_value) * q
    settlement_residual = (inp.realized_settlement_adjustment - inp.expected_settlement_adjustment) * q

    expected = expected_per_contract*q
    realized = realized_per_contract*q
    residual = realized-expected

    # The full residual relative to expected calibrated profit should be:
    # outcome variance + execution + management + settlement.
    # Forecast residual is separately useful vs the market prior and is not
    # added into this reconciliation identity.
    reconciled = outcome_variance+execution_residual+management_residual+settlement_residual
    err = residual-reconciled

    return Attribution(
        decision_id=inp.decision_id,
        expected_profit=expected,
        realized_profit=realized,
        residual=residual,
        forecast_residual=forecast_residual,
        execution_residual=execution_residual,
        management_residual=management_residual,
        settlement_residual=settlement_residual,
        outcome_variance=outcome_variance,
        reconciliation_error=err,
        dimensions={
            "strategy":inp.strategy,"sport":inp.sport,"family":inp.family,
            "regime":inp.regime,"venue":inp.venue
        },
    )

def aggregate(attributions, dimension: str) -> list[dict]:
    groups=defaultdict(list)
    for a in attributions:
        groups[a.dimensions.get(dimension,"UNKNOWN")].append(a)
    rows=[]
    for key,vals in sorted(groups.items()):
        rows.append({
            dimension:key,
            "n":len(vals),
            "expected_profit":sum(v.expected_profit for v in vals),
            "realized_profit":sum(v.realized_profit for v in vals),
            "residual":sum(v.residual for v in vals),
            "forecast_residual":sum(v.forecast_residual for v in vals),
            "execution_residual":sum(v.execution_residual for v in vals),
            "management_residual":sum(v.management_residual for v in vals),
            "settlement_residual":sum(v.settlement_residual for v in vals),
            "outcome_variance":sum(v.outcome_variance for v in vals),
            "reconciliation_error":sum(v.reconciliation_error for v in vals),
        })
    return rows

def require_reconciled(a: Attribution, tolerance: float = 1e-9):
    if abs(a.reconciliation_error)>tolerance:
        raise ValueError(f"attribution does not reconcile: {a.reconciliation_error}")
    return True
