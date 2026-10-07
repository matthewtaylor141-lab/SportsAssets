from __future__ import annotations
from decimal import Decimal
from .models import PositionTruth

REQUIRED_SOURCES=("INTERNAL_LEDGER","VENUE_POSITIONS","VENUE_BALANCE",
                  "MARKET_DATA","AUDREY_RECONCILIATION")

def truth_quorum(rows: list[PositionTruth], *, max_age_s: float, now: float,
                 tolerance_qty: Decimal=Decimal("0"),
                 tolerance_value: Decimal=Decimal("0.01")) -> dict:
    """Require agreement across independent truth sources before readiness.

    Missing source is not zero. Stale source is not current truth.
    """
    by={}
    blockers=[]
    for r in rows:
        if now-r.as_of > max_age_s:
            blockers.append(f"STALE_SOURCE:{r.source}")
            continue
        by.setdefault(r.source,[]).append(r)
    for source in REQUIRED_SOURCES:
        if source not in by:
            blockers.append(f"MISSING_SOURCE:{source}")

    # Position agreement only for sources that carry claim rows.
    position_sources=[s for s in ("INTERNAL_LEDGER","VENUE_POSITIONS","AUDREY_RECONCILIATION")
                      if s in by]
    claims={}
    for s in position_sources:
        for r in by[s]:
            claims.setdefault(r.claim_key,{})[s]=r

    for claim,srcs in claims.items():
        if "INTERNAL_LEDGER" in srcs and "VENUE_POSITIONS" in srcs:
            if abs(Decimal(srcs["INTERNAL_LEDGER"].qty)-Decimal(srcs["VENUE_POSITIONS"].qty)) > tolerance_qty:
                blockers.append(f"POSITION_QTY_MISMATCH:{claim}")
            a=srcs["INTERNAL_LEDGER"].value
            b=srcs["VENUE_POSITIONS"].value
            if a is not None and b is not None and abs(Decimal(a)-Decimal(b)) > tolerance_value:
                blockers.append(f"POSITION_VALUE_MISMATCH:{claim}")
    return {"green":not blockers,"blockers":tuple(sorted(set(blockers))),
            "sources":tuple(sorted(by)),"claims":claims}
