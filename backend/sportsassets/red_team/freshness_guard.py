from __future__ import annotations
from .models import VenueHealth

def venue_health_gate(health: VenueHealth, *, min_rate:float=0.95,
                      max_stale:int=0, max_gaps:int=0) -> dict:
    den=int(health.required_books)
    rate=None if den<=0 else float(health.current_books)/den
    blockers=[]
    if den<=0: blockers.append("NO_REQUIRED_BOOKS_DENOMINATOR")
    if rate is None or rate<min_rate: blockers.append("FRESHNESS_BELOW_TARGET")
    if health.stale_books>max_stale: blockers.append("STALE_BOOKS_PRESENT")
    if health.gaps>max_gaps: blockers.append("STREAM_GAPS_PRESENT")
    if health.rate_limited: blockers.append("RATE_LIMIT_ACTIVE")
    return {"venue":health.venue,"green":not blockers,"rate":rate,
            "numerator":health.current_books,"denominator":den,
            "blockers":tuple(blockers),"as_of":health.as_of,"source":health.source}

def isolated_venue_health(items:list[VenueHealth], **kw)->dict:
    """Never create a blended health status."""
    return {h.venue:venue_health_gate(h,**kw) for h in items}

def pair_market_data_gate(report:dict, venue_a:str, venue_b:str)->dict:
    a=report.get(venue_a); b=report.get(venue_b)
    blockers=[]
    if not a or not a.get("green"): blockers.append(f"{venue_a}_NOT_HEALTHY")
    if not b or not b.get("green"): blockers.append(f"{venue_b}_NOT_HEALTHY")
    return {"green":not blockers,"blockers":tuple(blockers)}
