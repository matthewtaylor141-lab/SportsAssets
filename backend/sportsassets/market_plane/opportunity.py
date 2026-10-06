"""Immutable canonical opportunity object consumed by agents."""
from __future__ import annotations
import hashlib,json
VERSION="CANONICAL_OPPORTUNITY_V1"


def build(*, contract:dict, entities:dict, book:dict|None, probability:dict|None,
          settlement:dict|None, execution:dict|None, portfolio:dict|None)->dict:
    payload={"contract":contract,"entities":entities,"book":book,
             "probability":probability,"settlement":settlement,
             "execution":execution,"portfolio":portfolio,"version":VERSION}
    gaps=[]
    for k in ("contract","entities","book","settlement"):
        if not payload.get(k): gaps.append("MISSING_"+k.upper())
    payload["gaps"]=gaps
    payload["ready_for_agent_evaluation"]=not gaps
    payload["content_sha256"]=hashlib.sha256(json.dumps(payload,sort_keys=True,
        separators=(",",":"),default=str).encode()).hexdigest()
    return payload
