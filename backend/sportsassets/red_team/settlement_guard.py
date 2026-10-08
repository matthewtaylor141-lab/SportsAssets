from __future__ import annotations

def settlement_certificate_gate(*, decision_rules_fingerprint:str|None,
                                current_rules_fingerprint:str|None,
                                outcome_space_exhaustive:bool,
                                unknown_states:list[str]|tuple[str,...],
                                unequal_states:list[str]|tuple[str,...],
                                source_agreement:bool)->dict:
    blockers=[]
    if not outcome_space_exhaustive: blockers.append("OUTCOME_SPACE_NOT_EXHAUSTIVE")
    if decision_rules_fingerprint is None: blockers.append("DECISION_RULES_FINGERPRINT_MISSING")
    if current_rules_fingerprint is None: blockers.append("CURRENT_RULES_FINGERPRINT_MISSING")
    if decision_rules_fingerprint and current_rules_fingerprint and decision_rules_fingerprint!=current_rules_fingerprint:
        blockers.append("RULES_CHANGED_SINCE_CERTIFICATION")
    if unknown_states: blockers.append("SETTLEMENT_STATES_UNKNOWN:"+",".join(sorted(unknown_states)))
    if unequal_states: blockers.append("SETTLEMENT_STATES_UNEQUAL:"+",".join(sorted(unequal_states)))
    if not source_agreement: blockers.append("RESOLUTION_SOURCE_DIFFERS")
    return {"green":not blockers,"blockers":tuple(blockers)}
