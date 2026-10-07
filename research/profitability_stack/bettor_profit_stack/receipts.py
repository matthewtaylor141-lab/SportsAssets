from __future__ import annotations
from dataclasses import asdict
from .common import Receipt, fingerprint
from .execution_truth import ExecutionChoice
from .structural_arb import ArbPortfolio
from .profitability_governor import EvidenceState, GraduationDecision
from .pnl_attribution import Attribution

def execution_receipt(candidate_id: str, choices: list[ExecutionChoice], selected: ExecutionChoice) -> Receipt:
    return Receipt("BETTOR_EXECUTION_TRUTH", "v1", {
        "candidate_id": candidate_id,
        "choices": [asdict(c) for c in choices],
        "selected": asdict(selected),
    })

def arb_receipt(scan_id: str, opportunities: list[ArbPortfolio]) -> Receipt:
    return Receipt("BETTOR_STRUCTURAL_ARB", "v1", {
        "scan_id": scan_id,
        "opportunities": [asdict(x) for x in opportunities],
    })

def governor_receipt(strategy: str, state: EvidenceState, decision: GraduationDecision) -> Receipt:
    return Receipt("BETTOR_PROFITABILITY_GOVERNOR", "v1", {
        "strategy": strategy,
        "evidence_state": asdict(state),
        "decision": asdict(decision),
    })

def pnl_receipt(epoch_id: str, rows: list[Attribution]) -> Receipt:
    return Receipt("BETTOR_PNL_ATTRIBUTION", "v1", {
        "epoch_id": epoch_id,
        "rows": [asdict(r) for r in rows],
    })
