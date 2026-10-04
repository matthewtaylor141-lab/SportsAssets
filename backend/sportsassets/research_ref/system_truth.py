"""BETTOR System Truth / readiness reference."""
from __future__ import annotations

HARD_GATES = (
    "canonical_intent_coverage",
    "current_management_coverage",
    "ledger_reconciled",
    "market_data_freshness",
    "investment_lane_reachability",
    "exact_sha_ci",
    "paper_live_logic_parity",
    "approved_policy_version",
    "approved_live_book_rule",
    "settlement_compatibility",
    "no_unknown_live_order_state",
)
SOFT_DIMENSIONS = (
    "probability_calibration",
    "execution_calibration",
    "profitability_evidence",
    "opportunity_score_calibration",
    "agent_quality",
)

def evaluate(hard: dict, soft: dict | None = None) -> dict:
    soft = soft or {}
    failed = [k for k in HARD_GATES if hard.get(k) is False]
    unavailable = [k for k in HARD_GATES if k in hard and hard.get(k) is None]
    missing = [k for k in HARD_GATES if k not in hard]
    not_ready = bool(failed or unavailable or missing)
    measured = {k: soft.get(k) for k in SOFT_DIMENSIONS
                if isinstance(soft.get(k), (int, float))}
    soft_score = sum(measured.values()) / len(measured) if measured else None
    return {
        "status": "NOT_READY" if not_ready else "HARD_GATES_CLEAR",
        "failed_hard_gates": failed,
        "unavailable_hard_gates": unavailable,
        "missing_hard_gates": missing,
        "soft_score": soft_score,
        "soft_dimensions": {k: soft.get(k) for k in SOFT_DIMENSIONS},
        "principle": "hard gates are conjunctive; no weighted average can hide a zero",
        "authority": "READ_ONLY_REFERENCE",
    }
