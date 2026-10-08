from __future__ import annotations
from .models import TwinEvidence

def twin_gate(e:TwinEvidence, *, min_agreement:float=0.95,
              max_optimistic_false_fill_rate:float=0.01)->dict:
    blockers=[]
    if e.compared<=0:
        return {"green":False,"agreement":None,"blockers":("NO_TWIN_COMPARISONS",)}
    agreement=e.matched/e.compared
    false_rate=e.optimistic_false_fills/e.compared
    if agreement<min_agreement: blockers.append("FILL_AGREEMENT_BELOW_TARGET")
    if false_rate>max_optimistic_false_fill_rate: blockers.append("OPTIMISTIC_FALSE_FILL_RATE_TOO_HIGH")
    if e.lookahead_violations!=0: blockers.append("LOOKAHEAD_VIOLATIONS_PRESENT")
    return {"green":not blockers,"agreement":agreement,
            "optimistic_false_fill_rate":false_rate,
            "lookahead_violations":e.lookahead_violations,
            "mismatch_reasons":dict(e.mismatch_reasons),
            "blockers":tuple(blockers)}
