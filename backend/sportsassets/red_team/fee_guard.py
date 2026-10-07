from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class FeeEvidence:
    venue:str
    market_key:str
    fee_known:bool
    schedule_id:str|None
    schedule_version:str|None
    effective_at:float|None
    maker_known:bool
    taker_known:bool

def fee_gate(e:FeeEvidence, *, now:float)->dict:
    blockers=[]
    if not e.fee_known: blockers.append("FEE_UNKNOWN")
    if not e.schedule_id: blockers.append("FEE_SCHEDULE_ID_MISSING")
    if not e.schedule_version: blockers.append("FEE_SCHEDULE_VERSION_MISSING")
    if e.effective_at is None or e.effective_at>now: blockers.append("FEE_EFFECTIVE_TIME_INVALID")
    if not e.taker_known: blockers.append("TAKER_FEE_UNKNOWN")
    return {"green":not blockers,"blockers":tuple(blockers),
            "maker_eligible":bool(e.maker_known and not blockers),
            "taker_eligible":bool(e.taker_known and not blockers)}
