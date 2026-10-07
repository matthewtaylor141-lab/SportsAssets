from __future__ import annotations
from dataclasses import dataclass, replace
from decimal import Decimal

DISCOVERED="DISCOVERED"
REVALIDATED="REVALIDATED"
ARMED="ARMED"
PARTIAL="PARTIAL"
MATCHED="MATCHED"
LOCKED="LOCKED"
REPAIR_REQUIRED="REPAIR_REQUIRED"
FROZEN="FROZEN"

@dataclass(frozen=True)
class PairState:
    state: str
    target_qty: int
    a_filled: int=0
    b_filled: int=0
    a_cost: Decimal=Decimal("0")
    b_cost: Decimal=Decimal("0")
    max_unmatched_loss: Decimal=Decimal("0")
    guaranteed_payout_per_set: Decimal=Decimal("1")
    reason: str|None=None

    @property
    def matched_qty(self)->int:
        return min(self.a_filled,self.b_filled)

    @property
    def unmatched_qty(self)->int:
        return abs(self.a_filled-self.b_filled)

    @property
    def execution_locked(self)->bool:
        return self.state==LOCKED and self.a_filled==self.b_filled==self.target_qty

def revalidate(s: PairState, *, books_current: bool, settlement_current: bool,
               economics_positive: bool) -> PairState:
    if s.state!=DISCOVERED:
        return replace(s,state=FROZEN,reason="INVALID_REVALIDATION_STATE")
    if not books_current:
        return replace(s,state=FROZEN,reason="BOOKS_NOT_CURRENT")
    if not settlement_current:
        return replace(s,state=FROZEN,reason="SETTLEMENT_NOT_CURRENT")
    if not economics_positive:
        return replace(s,state=FROZEN,reason="ECONOMICS_NOT_POSITIVE")
    return replace(s,state=REVALIDATED)

def arm(s: PairState)->PairState:
    if s.state!=REVALIDATED:
        return replace(s,state=FROZEN,reason="PAIR_NOT_REVALIDATED")
    return replace(s,state=ARMED)

def fill(s: PairState, leg: str, qty: int, cost: Decimal,
         *, worst_case_unmatched_loss_per_contract: Decimal) -> PairState:
    if s.state not in (ARMED,PARTIAL):
        return replace(s,state=FROZEN,reason="FILL_IN_INVALID_STATE")
    if qty<=0:
        return replace(s,state=FROZEN,reason="INVALID_FILL_QTY")
    a,b=s.a_filled,s.b_filled
    ac,bc=s.a_cost,s.b_cost
    if leg=="A":
        a+=qty; ac+=Decimal(cost)
    elif leg=="B":
        b+=qty; bc+=Decimal(cost)
    else:
        return replace(s,state=FROZEN,reason="UNKNOWN_LEG")
    if a>s.target_qty or b>s.target_qty:
        return PairState(FROZEN,s.target_qty,a,b,ac,bc,s.max_unmatched_loss,
                         s.guaranteed_payout_per_set,"OVERFILL")
    unmatched=abs(a-b)
    loss=Decimal(unmatched)*Decimal(worst_case_unmatched_loss_per_contract)
    if loss>s.max_unmatched_loss:
        return PairState(REPAIR_REQUIRED,s.target_qty,a,b,ac,bc,s.max_unmatched_loss,
                         s.guaranteed_payout_per_set,"UNMATCHED_LOSS_CAP_EXCEEDED")
    state=MATCHED if a==b==s.target_qty else PARTIAL
    return PairState(state,s.target_qty,a,b,ac,bc,s.max_unmatched_loss,
                     s.guaranteed_payout_per_set,None)

def lock(s: PairState)->PairState:
    if s.state!=MATCHED or s.a_filled!=s.b_filled or s.a_filled!=s.target_qty:
        return replace(s,state=FROZEN,reason="PAIR_NOT_FULLY_MATCHED")
    return replace(s,state=LOCKED,reason=None)

def guarantee_label(s: PairState)->str:
    return "EXECUTION_LOCKED" if s.execution_locked else "NOT_EXECUTION_LOCKED"
