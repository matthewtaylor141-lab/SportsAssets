"""THE RECORDS KAREN MAY CITE BEYOND THE LOOP'S OWN (migration 207a).

Registered into collaboration_loop.EVIDENCE_KINDS when Karen's module is
imported, so a challenge, a peer response or an evaluation citing one of
these is checked to exist exactly like any other evidence reference. Fixed
table names and key expressions only -- never a caller-supplied identifier.
Read only: nothing here writes any of these tables.
"""
from __future__ import annotations

from . import collaboration_loop as CL

#: governance records: composite keys are named by a fixed expression
#: ("<id>@<version>", "<agent>|<key>|<version>")
GOVERNANCE_KINDS = {
    "agent_policy_artifacts": ("agent_policy_artifacts",
                               "(policy_id || '@' || version)"),
    "live_rule_artifacts": ("live_rule_artifacts",
                            "(rule_id || '@' || version)"),
    "agent_policy_versions": ("agent_policy_versions",
                              "(agent_id || '|' || policy_key || '|' || "
                              "version)"),
    "improvement_candidates": ("improvement_candidates", "candidate_id"),
}

#: the shadow allocator's tables (migration 208, the intel workstream), by
#: every name it may land under. Their key is the table's own single-column
#: primary key, read from the catalogue (None); a table absent from this
#: database resolves nothing and its detector skips.
ALLOCATOR_TABLES = ("shadow_allocator_decisions", "shadow_allocations",
                    "chief_allocator_decisions", "allocator_shadow_decisions",
                    "shadow_allocator_allocations")
ALLOCATOR_KINDS = {t: (t, None) for t in ALLOCATOR_TABLES}


def register() -> None:
    for k, v in {**GOVERNANCE_KINDS, **ALLOCATOR_KINDS}.items():
        CL.EVIDENCE_KINDS.setdefault(k, v)


register()
