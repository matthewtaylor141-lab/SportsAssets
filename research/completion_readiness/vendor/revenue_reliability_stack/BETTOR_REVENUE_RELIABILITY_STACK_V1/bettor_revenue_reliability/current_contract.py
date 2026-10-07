from __future__ import annotations
from dataclasses import dataclass

# These constants mirror the current BETTOR database contracts verified at
# tested closeout SHA 659edaf21c2deb1dcc032932f38a7ff1ec8456c2.
CURRENT_PRODUCTION_BASE_SHA = "659edaf21c2deb1dcc032932f38a7ff1ec8456c2"

LIFECYCLE_STATES = (
    "ACTIVE_CHAMPION",
    "ACTIVE_CHALLENGER",
    "REDUCED_SIZE",
    "SHADOW_ONLY",
    "QUARANTINED",
    "RETIRED",
)

NO_ENTRY_STATES = {"SHADOW_ONLY", "QUARANTINED", "RETIRED"}
TIGHTEN_ONLY_AUTOMATIC_STATES = {"REDUCED_SIZE", "SHADOW_ONLY", "QUARANTINED", "RETIRED"}

@dataclass(frozen=True)
class AgentContract:
    canonical_id: str
    display_name: str
    role: str
    current_authority: str
    economic_job: str
    can_grant_capital: bool = False

AGENTS = {
    "DEREK": AgentContract(
        "DEREK", "Derek", "Discovery & entry",
        "ENTRY_REQUEST_THROUGH_GATED_PATH",
        "Find positive executable opportunities and refuse non-positive entries.",
    ),
    "KAREN": AgentContract(
        "KAREN", "Karen", "Adversarial review",
        "CHALLENGE_ONLY_ZERO_AUTHORITY",
        "Challenge assumptions and measure false-block / saved-loss value.",
    ),
    "CHIEF_ALLOCATOR": AgentContract(
        "CHIEF_ALLOCATOR", "Allie", "Capital allocation",
        "SHADOW_WEIGHTS_ONLY",
        "Allocate only among independently eligible positive-capacity strategies.",
    ),
    "ARCHER": AgentContract(
        "ARCHER", "Archer", "Execution & microstructure",
        "SHADOW_ONLY",
        "Improve executable economics via MAKE / TAKE / WAIT / REFUSE evidence.",
    ),
    "XAVIER": AgentContract(
        "XAVIER", "Xavier", "Portfolio management",
        "MANAGEMENT_DISPATCH_THROUGH_CLAIM_PATH",
        "Improve realized management value versus frozen entry counterfactuals.",
    ),
    "AUDREY": AgentContract(
        "AUDREY", "Audrey", "Audit & reconciliation",
        "AUDIT_NO_ORDER_PATH",
        "Reconcile evidence, P&L and governed improvement records.",
    ),
    "SCOUT": AgentContract(
        "SCOUT", "Scout", "Research & signals",
        "RESEARCH_SHADOW_ONLY",
        "Improve evidence quality and research coverage without capital authority.",
    ),
    "ADRIANA": AgentContract(
        "ADRIANA", "Adriana", "Structural arbitrage",
        "SHADOW_ONLY",
        "Find settlement-compatible, all-in positive structural arbitrage.",
    ),
}

CURRENT_TABLES = {
    "strategy_lifecycle": "paper_strategy_lifecycle_current_v",
    "profitability_models": "paper_profitability_models",
    "profitability_evaluations": "paper_profitability_evaluations",
    "cash_decisions": "paper_cash_decisions",
    "shadow_counterfactuals": "paper_shadow_counterfactuals",
    "counterfactual_variants": "paper_counterfactual_variants",
    "counterfactual_outcomes": "paper_counterfactual_variant_outcomes",
    "score_tournament": "opportunity_score_tournament",
    "derek_decisions": "derek_entry_decisions",
    "karen_challenges": "karen_challenges",
    "xavier_theses": "xavier_entry_theses",
    "xavier_assessments": "xavier_management_assessments",
    "xavier_value_add": "xavier_value_add",
    "audrey_reports": "audrey_audit_reports",
    "improvement_candidates": "improvement_candidates",
    "improvement_trials": "improvement_trials",
}

def validate_contract() -> None:
    assert set(NO_ENTRY_STATES).issubset(LIFECYCLE_STATES)
    assert AGENTS["KAREN"].current_authority == "CHALLENGE_ONLY_ZERO_AUTHORITY"
    assert AGENTS["ARCHER"].current_authority == "SHADOW_ONLY"
    assert AGENTS["CHIEF_ALLOCATOR"].current_authority == "SHADOW_WEIGHTS_ONLY"
    assert all(not a.can_grant_capital for a in AGENTS.values())
