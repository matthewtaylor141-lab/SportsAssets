"""Immutable canonical opportunity object consumed by agents.

(settlement rule registry) `settlement_evidence` carries the market plane's
settlement state for the contract with its provenance -- rules fingerprint,
field, parser version, matched clauses, verification sources, conflicts and
the basis (DECISION_ATTEST / RULES_TERMS_COMPARISON / ...). It never fills
the `settlement` slot (the decision valuation's own comparison) and it can
only ADD a gap: a contract whose settlement state is not proven is not
ready for agent evaluation, whatever else it carries. Decision-time attest
remains the authority for trading.
"""
from __future__ import annotations
import hashlib,json
VERSION="CANONICAL_OPPORTUNITY_V1"
PROVEN_SETTLEMENT_STATES=("SETTLEMENT_PROVEN_COMPATIBLE",
                          "SETTLEMENT_PROVEN_DIFFERENT_BUT_PRICED")


def build(*, contract:dict, entities:dict, book:dict|None, probability:dict|None,
          settlement:dict|None, execution:dict|None, portfolio:dict|None,
          settlement_evidence:dict|None=None)->dict:
    payload={"contract":contract,"entities":entities,"book":book,
             "probability":probability,"settlement":settlement,
             "execution":execution,"portfolio":portfolio,"version":VERSION}
    if settlement_evidence is not None:
        payload["settlement_evidence"]=settlement_evidence
    gaps=[]
    for k in ("contract","entities","book","settlement"):
        if not payload.get(k): gaps.append("MISSING_"+k.upper())
    if settlement_evidence is not None:
        st=settlement_evidence.get("state")
        if st not in PROVEN_SETTLEMENT_STATES:
            gaps.append("SETTLEMENT_STATE:%s"%(st or "NOT_YET_CLASSIFIED"))
    payload["gaps"]=gaps
    payload["ready_for_agent_evaluation"]=not gaps
    payload["content_sha256"]=hashlib.sha256(json.dumps(payload,sort_keys=True,
        separators=(",",":"),default=str).encode()).hexdigest()
    return payload
