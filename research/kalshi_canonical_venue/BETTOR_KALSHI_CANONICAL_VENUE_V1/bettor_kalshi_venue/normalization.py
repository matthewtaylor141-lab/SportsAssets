from __future__ import annotations
from decimal import Decimal
from .models import CanonicalEvent, VenueInstrument, PROVEN

def binary_proposition_instrument(
    *,
    venue: str,
    instrument_id: str,
    market_id: str,
    side: str,
    event: CanonicalEvent,
    family: str,
    period: str,
    subject_outcomes: set[str],
    nonstandard_payoff: dict[str, Decimal],
    rules_status: str=PROVEN,
    mapping_status: str=PROVEN,
    settlement_status: str=PROVEN,
    orderable_id: str|None=None,
    sport: str|None=None,
    metadata=None,
) -> VenueInstrument:
    """Build explicit YES/NO payoff vectors from a PROVEN proposition.

    `subject_outcomes` is the set of regular canonical event outcomes for which
    the venue proposition is true. This is not inferred from a title.

    Example (two-way MLB):
      Yankees market YES subject_outcomes={"NYY"}
      Rays market NO    subject_outcomes={"TB"} and side="NO"
    Both produce NYY=1, TB=0. If VOID/POSTPONED payouts also agree, they
    become the same canonical economic claim.

    Example (three-way soccer):
      Chelsea NO pays on ARS and DRAW; it will NOT equal Arsenal YES.
    """
    ss=str(side).upper()
    if ss not in ("YES","NO"):
        raise ValueError("side must be YES or NO")
    payoff={}
    nonstd=set(nonstandard_payoff)
    for o in event.outcomes:
        if o in nonstd:
            payoff[o]=Decimal(nonstandard_payoff[o])
            continue
        yes = Decimal("1") if o in subject_outcomes else Decimal("0")
        payoff[o]=yes if ss=="YES" else Decimal("1")-yes
    return VenueInstrument(
        venue=venue,instrument_id=instrument_id,market_id=market_id,side=ss,
        event_key=event.event_key,family=family,period=period,
        subject="|".join(sorted(subject_outcomes)),
        payoff=payoff,rules_status=rules_status,mapping_status=mapping_status,
        settlement_status=settlement_status,book_is_explicit=True,
        orderable_id=orderable_id or instrument_id,sport=sport,
        metadata=dict(metadata or {}),
    )
