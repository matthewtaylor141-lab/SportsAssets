from __future__ import annotations
from dataclasses import dataclass, asdict
from decimal import Decimal
from itertools import combinations
from .models import CanonicalEvent, VenueInstrument, Book, FeeFn
from .canonical import equivalence_classes, are_complements
from .quotes import best_split_route

@dataclass(frozen=True)
class ArbOpportunity:
    event_key: str
    claim_a: str
    claim_b: str
    qty: int
    payout: Decimal
    all_in_cost: Decimal
    guaranteed_profit: Decimal
    guaranteed_roi: Decimal
    topology: str
    leg_a_route: dict
    leg_b_route: dict
    verdict: str

def _rep(c):
    return c.instruments[0]

def scan_complement_arbitrage(
    event: CanonicalEvent,
    instruments: list[VenueInstrument],
    books: dict,
    *,
    now: float,
    fee_by_venue: dict[str,FeeFn],
    other_cost_per_contract_by_venue: dict[str,Decimal]|None=None,
    max_qty: int=100,
    max_opt_qty: int=2000,
    per_set_payout: Decimal=Decimal("1"),
) -> tuple[list[ArbOpportunity],dict]:
    """Scan same-venue AND cross-venue complementary structures.

    A pair is discovered from payoff vectors, never titles. Each leg is an
    economic CLAIM CLASS. The router may acquire a leg through Yankees YES,
    Rays NO, or an equivalent Polymarket/Kalshi contract—whichever has the
    lowest fee-aware all-in cost at that size.

    Same-venue arb is therefore first class, not a special case.
    """
    classes,refused=equivalence_classes(event,instruments)
    opps=[]
    considered=0
    for a,b in combinations(classes,2):
        if not are_complements(event,_rep(a),_rep(b),per_set_payout):
            continue
        considered+=1
        best=None
        for q in range(1,int(max_qty)+1):
            ra=best_split_route(a,books,q,now=now,fee_by_venue=fee_by_venue,
                                other_cost_per_contract_by_venue=other_cost_per_contract_by_venue,
                                max_opt_qty=max_opt_qty)
            rb=best_split_route(b,books,q,now=now,fee_by_venue=fee_by_venue,
                                other_cost_per_contract_by_venue=other_cost_per_contract_by_venue,
                                max_opt_qty=max_opt_qty)
            if ra is None or rb is None:
                continue
            cost=ra.all_in+rb.all_in
            payout=per_set_payout*q
            profit=payout-cost
            if profit<=0:
                continue
            venues={x["venue"] for x in ra.allocations}|{x["venue"] for x in rb.allocations}
            topology="SAME_VENUE" if len(venues)==1 else "CROSS_VENUE"
            cand=ArbOpportunity(
                event.event_key,a.fingerprint,b.fingerprint,q,payout,cost,profit,
                profit/cost if cost>0 else Decimal("0"),topology,
                {"topology":ra.topology,"allocations":ra.allocations,"all_in":str(ra.all_in)},
                {"topology":rb.topology,"allocations":rb.allocations,"all_in":str(rb.all_in)},
                "GUARANTEED_AFTER_COSTS",
            )
            if best is None or cand.guaranteed_profit>best.guaranteed_profit:
                best=cand
        if best is not None:
            opps.append(best)
    opps.sort(key=lambda x:x.guaranteed_profit,reverse=True)
    return opps,{
        "event_key":event.event_key,
        "claim_classes":len(classes),
        "complement_pairs_considered":considered,
        "opportunities":len(opps),
        "mapping_refusals":refused,
        "authority":"SHADOW_ONLY_NO_ORDER_AUTHORITY",
    }

def best_acquisition_for_claim(
    event: CanonicalEvent,
    instruments: list[VenueInstrument],
    target: VenueInstrument,
    books: dict,
    *,
    qty:int,
    now:float,
    fee_by_venue:dict[str,FeeFn],
    other_cost_per_contract_by_venue=None,
):
    """Route a desired economic claim to its cheapest equivalent instrument."""
    classes,_=equivalence_classes(event,instruments)
    from .canonical import claim_fingerprint
    fp=claim_fingerprint(event,target)
    cl=next((c for c in classes if c.fingerprint==fp),None)
    if cl is None: return None
    return best_split_route(cl,books,qty,now=now,fee_by_venue=fee_by_venue,
                            other_cost_per_contract_by_venue=other_cost_per_contract_by_venue)
