"""THE RECORDS EDDIE, SCOUT AND THE CANDIDATE-REVIEW WORKFLOW MAY CITE
(migration 217).

Registered into collaboration_loop.EVIDENCE_KINDS on import, so a finding,
a challenge or a workflow step citing one is checked to exist like any other
evidence reference. Fixed table names and key expressions only. Read only.
"""
from __future__ import annotations

from . import collaboration_loop as CL
from . import karen_evidence as _KE  # noqa: F401  (intel_allocations etc.)

POS_KINDS = {
    "eddie_execution_estimates": ("eddie_execution_estimates", "estimate_id"),
    "eddie_execution_outcomes": ("eddie_execution_outcomes", "outcome_id"),
    "scout_sources": ("scout_sources", "source_id"),
    "scout_features": ("scout_features", "feature_id"),
    "scout_feature_observations": ("scout_feature_observations",
                                   "observation_id"),
    "scout_feature_tournaments": ("scout_feature_tournaments",
                                  "tournament_id"),
    "pos_candidate_reviews": ("pos_candidate_reviews", "review_id"),
    "paper_book_observations": ("paper_book_observations", "obs_id"),
    "paper_fills": ("paper_fills", "fill_id"),
    "fixture_metadata": ("fixture_metadata", "condition_id"),
    "intel_audrey_risk_checks": ("intel_audrey_risk_checks",
                                 "(run_id || '|' || book || '|' || metric)"),
    "intel_runs": ("intel_runs", "run_id"),
}


def register() -> None:
    for k, v in POS_KINDS.items():
        CL.EVIDENCE_KINDS.setdefault(k, v)


register()
