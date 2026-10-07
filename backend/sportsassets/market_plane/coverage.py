"""Universal coverage matrix: every active contract gets a terminal state."""
from __future__ import annotations
from .models import (PRICEABLE,MAPPED_NO_FAIR_VALUE,MAPPED_SETTLEMENT_UNKNOWN,
                     EXTERNAL_UNAVAILABLE,CODE_CONTROLLED_GAP,TERMINAL_STATES)
VERSION="MARKET_COVERAGE_MATRIX_V1"


def terminal(contract:dict)->dict:
    if contract.get("external_unavailable"):
        state=EXTERNAL_UNAVAILABLE; why=contract.get("external_unavailable")
    elif not contract.get("mapped"):
        state=CODE_CONTROLLED_GAP; why=contract.get("mapping_why") or "ACTIVE_CONTRACT_NOT_MAPPED"
    elif not contract.get("settlement_proven"):
        state=MAPPED_SETTLEMENT_UNKNOWN; why=contract.get("settlement_why") or "SETTLEMENT_NOT_PROVEN"
    elif not contract.get("fair_value_source"):
        state=MAPPED_NO_FAIR_VALUE; why=contract.get("fair_value_why") or "NO_FAIR_VALUE_SOURCE"
    elif not contract.get("fresh_book"):
        state=CODE_CONTROLLED_GAP; why=contract.get("book_why") or "NO_FRESH_CANONICAL_BOOK"
    else:
        state=PRICEABLE; why=None
    return {"contract_id":contract.get("contract_id"),"state":state,"why":why,
            "sport":contract.get("sport"),"competition":contract.get("competition"),
            "family":contract.get("family"),"period":contract.get("period"),
            "version":VERSION}


def matrix(contracts:list[dict])->dict:
    rows=[terminal(c) for c in contracts]
    by={s:0 for s in TERMINAL_STATES}
    for r in rows: by[r["state"]]+=1
    return {"total":len(rows),"by_state":by,"rows":rows,
            "silent_omissions":0,"version":VERSION}
