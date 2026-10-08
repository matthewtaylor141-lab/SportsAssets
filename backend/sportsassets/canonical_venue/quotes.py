from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from typing import Iterable
from .models import Book, VenueInstrument, FeeFn
from .canonical import ClaimClass

ZERO=Decimal("0")
ONE=Decimal("1")

def kalshi_taker_fee(count: int, price: Decimal) -> Decimal:
    """Matches current BETTOR kalshi_orders.fee_for taker formula.

    ceil(0.07 * C * p * (1-p) * 100) / 100
    """
    c=Decimal(int(count)); p=Decimal(price)
    raw=Decimal("0.07")*c*p*(ONE-p)
    return (raw*100).to_integral_value(rounding=ROUND_CEILING)/100

def zero_fee(count: int, price: Decimal) -> Decimal:
    return ZERO

@dataclass(frozen=True)
class FillCost:
    key: tuple[str,str,str]
    requested: int
    filled: int
    principal: Decimal
    fee: Decimal
    other_cost: Decimal
    all_in: Decimal
    vwap: Decimal | None
    effective_per_contract: Decimal | None
    levels: tuple[tuple[Decimal,int],...]
    eligible: bool
    reason: str | None

def _clean_levels(asks):
    out=[]
    for p,q in asks or ():
        p=Decimal(p); q=int(q)
        if ZERO < p < ONE and q>0:
            out.append((p,q))
    return sorted(out,key=lambda z:z[0])

def acquisition_cost(
    instrument: VenueInstrument,
    book: Book,
    qty: int,
    *,
    now: float,
    fee_fn: FeeFn,
    other_cost_per_contract: Decimal=ZERO,
) -> FillCost:
    """Cost to ACQUIRE a canonical claim through one explicit venue instrument.

    No YES/NO price is synthesized. An explicit NO book is an independently
    executable candidate and is compared with every economically equivalent
    YES/NO instrument.
    """
    qty=int(qty)
    if qty<=0:
        raise ValueError("qty must be positive")
    if book.key != instrument.key:
        return FillCost(instrument.key,qty,0,ZERO,ZERO,ZERO,ZERO,None,None,(),False,"BOOK_CONTRACT_MISMATCH")
    if not book.explicit:
        return FillCost(instrument.key,qty,0,ZERO,ZERO,ZERO,ZERO,None,None,(),False,"BOOK_NOT_EXPLICIT")
    if now < book.observed_at:
        return FillCost(instrument.key,qty,0,ZERO,ZERO,ZERO,ZERO,None,None,(),False,"BOOK_TIME_IN_FUTURE")
    if now-book.observed_at > book.max_age_s:
        return FillCost(instrument.key,qty,0,ZERO,ZERO,ZERO,ZERO,None,None,(),False,"STALE_BOOK")

    rem=qty; principal=ZERO; fee=ZERO; used=[]
    for price,avail in _clean_levels(book.asks):
        if rem<=0: break
        take=min(rem,avail)
        if take<=0: continue
        used.append((price,take))
        principal += price*take
        # Conservative and aligned with Adriana current semantics:
        # fee evaluated for each consumed price level.
        fee += Decimal(fee_fn(take,price))
        rem-=take
    filled=qty-rem
    if filled != qty:
        return FillCost(instrument.key,qty,filled,principal,fee,ZERO,principal+fee,None,None,tuple(used),False,"INSUFFICIENT_DEPTH")
    other=Decimal(other_cost_per_contract)*qty
    total=principal+fee+other
    return FillCost(
        instrument.key,qty,filled,principal,fee,other,total,
        principal/qty,total/qty,tuple(used),True,None
    )

@dataclass(frozen=True)
class Route:
    claim_fingerprint: str
    qty: int
    all_in: Decimal
    effective_per_contract: Decimal
    allocations: tuple[dict,...]
    topology: str

def best_single_route(
    claim: ClaimClass,
    books: dict[tuple[str,str,str],Book],
    qty: int,
    *,
    now: float,
    fee_by_venue: dict[str,FeeFn],
    other_cost_per_contract_by_venue: dict[str,Decimal]|None=None,
) -> Route | None:
    other=other_cost_per_contract_by_venue or {}
    candidates=[]
    for i in claim.instruments:
        b=books.get(i.key)
        fn=fee_by_venue.get(i.venue)
        if b is None or fn is None: continue
        c=acquisition_cost(i,b,qty,now=now,fee_fn=fn,
                           other_cost_per_contract=Decimal(other.get(i.venue,ZERO)))
        if c.eligible:
            candidates.append((c,i))
    if not candidates: return None
    candidates.sort(key=lambda x:(x[0].all_in,x[1].venue,x[1].instrument_id,x[1].side))
    c,i=candidates[0]
    return Route(claim.fingerprint,qty,c.all_in,c.all_in/qty,
                 ({"venue":i.venue,"instrument_id":i.instrument_id,"market_id":i.market_id,
                   "side":i.side,"qty":qty,"all_in":str(c.all_in),
                   "effective_per_contract":str(c.effective_per_contract),
                   "levels":[(str(p),q) for p,q in c.levels]},),
                 "SINGLE_INSTRUMENT")

def best_split_route(
    claim: ClaimClass,
    books: dict[tuple[str,str,str],Book],
    qty: int,
    *,
    now: float,
    fee_by_venue: dict[str,FeeFn],
    other_cost_per_contract_by_venue: dict[str,Decimal]|None=None,
    max_opt_qty: int=2000,
) -> Route | None:
    """Exact fee-aware allocation across equivalent instruments.

    It minimizes TOTAL all-in acquisition cost, not displayed price. That
    matters because Kalshi fees round per order and depth differs across
    Yankees YES / Rays NO / Polymarket Yankees YES, etc.

    Dynamic programming is intentionally bounded. Quantities above max_opt_qty
    are refused here rather than silently approximated.
    """
    qty=int(qty)
    if qty<=0 or qty>int(max_opt_qty):
        return None
    other=other_cost_per_contract_by_venue or {}
    options=[]
    for i in claim.instruments:
        b=books.get(i.key); fn=fee_by_venue.get(i.venue)
        if b is None or fn is None: continue
        curve=[None]*(qty+1)
        curve[0]=(ZERO,None)
        for q in range(1,qty+1):
            c=acquisition_cost(i,b,q,now=now,fee_fn=fn,
                               other_cost_per_contract=Decimal(other.get(i.venue,ZERO)))
            if c.eligible: curve[q]=(c.all_in,c)
        options.append((i,curve))
    if not options: return None

    INF=None
    dp=[INF]*(qty+1); dp[0]=(ZERO,[])
    for idx,(inst,curve) in enumerate(options):
        ndp=list(dp)
        for prevq in range(qty+1):
            if dp[prevq] is None: continue
            base,allocs=dp[prevq]
            for take in range(1,qty-prevq+1):
                cc=curve[take]
                if cc is None: break
                total=base+cc[0]
                nq=prevq+take
                cand=(total,allocs+[(idx,take,cc[1])])
                if ndp[nq] is None or total<ndp[nq][0]:
                    ndp[nq]=cand
        dp=ndp
    if dp[qty] is None: return None
    total,allocs=dp[qty]
    rendered=[]
    venues=set()
    for idx,take,c in allocs:
        i=options[idx][0]; venues.add(i.venue)
        rendered.append({
            "venue":i.venue,"instrument_id":i.instrument_id,"market_id":i.market_id,
            "side":i.side,"qty":take,"all_in":str(c.all_in),
            "effective_per_contract":str(c.effective_per_contract),
            "levels":[(str(p),q) for p,q in c.levels],
        })
    topology=("CROSS_VENUE_SPLIT" if len(venues)>1 else
              "SAME_VENUE_SPLIT" if len(rendered)>1 else "SINGLE_INSTRUMENT")
    return Route(claim.fingerprint,qty,total,total/qty,tuple(rendered),topology)
