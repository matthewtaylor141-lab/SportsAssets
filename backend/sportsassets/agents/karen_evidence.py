"""THE RECORDS KAREN MAY CITE BEYOND THE LOOP'S OWN (migration 212).

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

#: the Chief Allocator's shadow allocations (migration 208). The primary key
#: is (run_id, candidate_id) and every run rewrites every candidate, so the
#: evidence key is the STABLE candidate identity: a reference resolves when
#: any run holds that candidate. Absent table: resolves nothing.
ALLOCATOR_TABLE = "intel_allocations"
ALLOCATOR_KINDS = {ALLOCATOR_TABLE: (ALLOCATOR_TABLE, "candidate_id")}


def register() -> None:
    for k, v in {**GOVERNANCE_KINDS, **ALLOCATOR_KINDS}.items():
        CL.EVIDENCE_KINDS.setdefault(k, v)


register()
