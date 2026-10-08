from __future__ import annotations
from .models import ReleaseEvidence

def release_gate(e:ReleaseEvidence)->dict:
    blockers=[]
    if not e.is_descendant_of_base: blockers.append("RELEASE_NOT_DESCENDANT_OF_ACCEPTED_BASE")
    if e.tested_sha!=e.release_sha: blockers.append("RELEASE_SHA_DIFFERS_FROM_TESTED_SHA")
    if e.release_sha!=e.deployed_sha: blockers.append("DEPLOYED_SHA_DIFFERS_FROM_RELEASE_SHA")
    if not e.backend_tests_green: blockers.append("BACKEND_TESTS_NOT_GREEN")
    if not e.capital_critical_green: blockers.append("CAPITAL_CRITICAL_NOT_GREEN")
    if not e.commit_guard_green: blockers.append("COMMIT_GUARD_NOT_GREEN")
    if not e.engine_diagnostic_green: blockers.append("ENGINE_DIAGNOSTIC_NOT_GREEN")
    if not e.migration_fingerprint_match: blockers.append("MIGRATION_FINGERPRINT_MISMATCH")
    return {"green":not blockers,"blockers":tuple(blockers),
            "tested_sha":e.tested_sha,"release_sha":e.release_sha,
            "deployed_sha":e.deployed_sha,"accepted_base_sha":e.accepted_base_sha}
