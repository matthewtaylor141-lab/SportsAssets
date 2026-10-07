from __future__ import annotations
from decimal import Decimal
from typing import Mapping

def norm(x):
    return None if x is None else str(x).strip().upper()

def event_match(a:dict,b:dict,start_tolerance_s:int=900)->tuple[bool,str]:
    for k in ("sport","league","home","away"):
        if norm(a.get(k))!=norm(b.get(k)):
            return False, "TEAM_MISMATCH" if k in ("home","away") else f"{k.upper()}_MISMATCH"
    # ISO lexical equality is enough for frozen cases; production must use aware datetimes.
    if a.get("start")!=b.get("start"):
        return False,"START_TIME_MISMATCH"
    return True,"ESTABLISHED"

def market_match(a:dict,b:dict)->tuple[bool,str]:
    if norm(a.get("family"))!=norm(b.get("family")):
        return False,"FAMILY_OR_SUBJECT_MISMATCH"
    if norm(a.get("period"))!=norm(b.get("period")):
        return False,"PERIOD_MISMATCH"
    if a.get("line")!=b.get("line"):
        return False,"LINE_MISMATCH"
    if norm(a.get("subject"))!=norm(b.get("subject")):
        return False,"FAMILY_OR_SUBJECT_MISMATCH"
    return True,"ESTABLISHED"

def payoff_equivalent(outcomes:list[str],a:Mapping,b:Mapping)->tuple[bool,str]:
    for o in outcomes:
        av=a.get(o); bv=b.get(o)
        if av is None or bv is None:
            return False,"UNKNOWN_SETTLEMENT_STATE"
        if Decimal(str(av))!=Decimal(str(bv)):
            return False,f"PAYOFF_DIFFERS:{o}"
    return True,"EQUIVALENT"

def payoff_complements(outcomes:list[str],a:Mapping,b:Mapping,payout=Decimal("1"))->bool:
    for o in outcomes:
        av=a.get(o); bv=b.get(o)
        if av is None or bv is None:
            return False
        if Decimal(str(av))+Decimal(str(bv))!=payout:
            return False
    return True

def best_route(routes:list[dict])->str|None:
    eligible=[]
    for r in routes:
        if r.get("fresh") is False:
            continue
        price=Decimal(str(r["display_price"]))
        fee=Decimal(str(r.get("fee",0)))
        slip=Decimal(str(r.get("slippage",0)))
        eligible.append((price+fee+slip,r["name"]))
    return min(eligible)[1] if eligible else None

def arb_floor(legs:list[dict],guaranteed_payout)->Decimal:
    return Decimal(str(guaranteed_payout))-sum(Decimal(str(x["all_in"])) for x in legs)
