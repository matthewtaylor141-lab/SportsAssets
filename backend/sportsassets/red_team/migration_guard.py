from __future__ import annotations

def migration_guard(*, applied_fingerprint:str, repo_fingerprint:str,
                    fresh_db_passed:bool, forward_only:bool)->dict:
    blockers=[]
    if applied_fingerprint!=repo_fingerprint: blockers.append("APPLIED_MIGRATION_FINGERPRINT_DIFFERS")
    if not fresh_db_passed: blockers.append("FRESH_DB_MIGRATION_TEST_FAILED")
    if not forward_only: blockers.append("APPLIED_HISTORY_EDITED_IN_PLACE")
    return {"green":not blockers,"blockers":tuple(blockers)}
