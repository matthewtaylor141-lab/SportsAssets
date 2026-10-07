"""Canonical source arbitration: PMX gRPC -> retail push -> REST recovery."""
from __future__ import annotations

from .models import SOURCE_PRIORITY

VERSION = "CANONICAL_BOOK_ARBITRATION_V1"


def choose(reads: dict, *, now: float, max_age_s: float,
           require_certified_primary: bool = True) -> dict:
    rejected=[]
    for source in SOURCE_PRIORITY:
        r = dict(reads.get(source) or {})
        if not r:
            rejected.append({"source":source,"why":"ABSENT"}); continue
        if not r.get("ok", True):
            rejected.append({"source":source,"why":r.get("refusal") or "NOT_OK"}); continue
        at = r.get("venue_at") or r.get("observed_at") or r.get("received_at")
        if at is None:
            rejected.append({"source":source,"why":"NO_TIMESTAMP"}); continue
        age=max(0.0,float(now)-float(at))
        if age > max_age_s:
            rejected.append({"source":source,"why":"STALE","age_s":age}); continue
        if source == SOURCE_PRIORITY[0] and require_certified_primary and not r.get("certified", False):
            rejected.append({"source":source,"why":"PRIMARY_NOT_CERTIFIED"}); continue
        return {"ok":True,"source":source,"book":r,"age_s":age,
                "fallback":source != SOURCE_PRIORITY[0],"rejected":rejected,
                "version":VERSION}
    return {"ok":False,"source":None,"book":None,"rejected":rejected,
            "refusal":"NO_FRESH_CERTIFIED_BOOK_SOURCE","version":VERSION}
