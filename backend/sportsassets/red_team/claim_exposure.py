from __future__ import annotations
from collections import defaultdict
from decimal import Decimal
from .models import ClaimExposure

def aggregate_claim_exposure(rows: list[ClaimExposure]) -> dict:
    """Collapse venue aliases into one economic claim exposure.

    Equivalent contracts must never look diversified merely because they use
    different venues/sides/tickers.
    """
    claims=defaultdict(lambda: {"qty":Decimal("0"),"notional":Decimal("0"),
                               "aliases":set(),"events":set()})
    for r in rows:
        k=(r.claim_key,r.payoff_fingerprint)
        d=claims[k]
        d["qty"]+=Decimal(r.qty)
        d["notional"]+=Decimal(r.signed_notional)
        d["aliases"].add((r.venue,r.instrument_id,r.alias_group))
        d["events"].add(r.event_key)
    out={}
    for (claim,fp),d in claims.items():
        out[claim]={
            "payoff_fingerprint":fp,
            "qty":d["qty"],
            "signed_notional":d["notional"],
            "alias_count":len(d["aliases"]),
            "aliases":tuple(sorted(d["aliases"])),
            "event_keys":tuple(sorted(d["events"])),
        }
    return out

def exposure_gate(rows: list[ClaimExposure], *, max_abs_notional_by_claim: Decimal,
                  max_abs_notional_by_event: Decimal) -> dict:
    claims=aggregate_claim_exposure(rows)
    blockers=[]
    for claim,d in claims.items():
        if abs(d["signed_notional"]) > Decimal(max_abs_notional_by_claim):
            blockers.append(f"CLAIM_LIMIT:{claim}")
    by_event=defaultdict(Decimal)
    for r in rows:
        by_event[r.event_key]+=Decimal(r.signed_notional)
    for event,n in by_event.items():
        if abs(n) > Decimal(max_abs_notional_by_event):
            blockers.append(f"EVENT_LIMIT:{event}")
    return {"eligible":not blockers,"blockers":tuple(sorted(blockers)),
            "claims":claims,
            "event_notional":dict(by_event)}
