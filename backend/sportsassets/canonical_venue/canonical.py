from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
import hashlib, json
from collections import defaultdict
from .models import CanonicalEvent, VenueInstrument, PROVEN

def _p(v) -> str:
    return str(Decimal(v).normalize())

def validate_instrument(event: CanonicalEvent, i: VenueInstrument) -> list[str]:
    r=[]
    if not event.event_key or i.event_key != event.event_key:
        r.append("EVENT_IDENTITY_UNPROVEN")
    if not event.exhaustive:
        r.append("OUTCOME_SPACE_NOT_PROVEN_EXHAUSTIVE")
    if i.mapping_status != PROVEN:
        r.append("MAPPING_NOT_PROVEN")
    if i.settlement_status != PROVEN:
        r.append("SETTLEMENT_NOT_PROVEN")
    if i.rules_status != PROVEN:
        r.append("RULES_NOT_PROVEN")
    if not i.book_is_explicit:
        r.append("NON_EXPLICIT_BOOK")
    missing=[o for o in event.outcomes if o not in i.payoff]
    extra=[o for o in i.payoff if o not in set(event.outcomes)]
    if missing: r.append("PAYOFF_MISSING:"+",".join(sorted(missing)))
    if extra: r.append("PAYOFF_EXTRA:"+",".join(sorted(extra)))
    for o in event.outcomes:
        if o in i.payoff:
            try: p=Decimal(i.payoff[o])
            except Exception:
                r.append("PAYOFF_INVALID:"+o); continue
            if p < 0 or p > 1:
                r.append("PAYOFF_OUT_OF_RANGE:"+o)
    return r

def claim_fingerprint(event: CanonicalEvent, i: VenueInstrument) -> str:
    """Economic claim identity = canonical event + family/period + full payoff.

    Venue, market id, side and title are deliberately excluded. If Yankees YES
    and Rays NO pay identically in every exhaustive event state, they have the
    same claim fingerprint.
    """
    blockers=validate_instrument(event,i)
    if blockers:
        raise ValueError("claim not provable: "+ ";".join(blockers))
    payload={
        "event_key":event.event_key,
        "family":i.family,
        "period":i.period,
        "outcomes":list(event.outcomes),
        "payoff":[[o,_p(i.payoff[o])] for o in event.outcomes],
    }
    raw=json.dumps(payload,separators=(",",":"),sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True)
class ClaimClass:
    fingerprint: str
    event_key: str
    payoff: tuple[tuple[str,str], ...]
    instruments: tuple[VenueInstrument, ...]

def equivalence_classes(event: CanonicalEvent, instruments: list[VenueInstrument]) -> tuple[list[ClaimClass],dict]:
    groups=defaultdict(list)
    refused={}
    for i in instruments:
        b=validate_instrument(event,i)
        if b:
            refused[str(i.key)]=b
            continue
        groups[claim_fingerprint(event,i)].append(i)
    out=[]
    for fp,xs in groups.items():
        rep=xs[0]
        out.append(ClaimClass(fp,event.event_key,
                              tuple((o,_p(rep.payoff[o])) for o in event.outcomes),
                              tuple(xs)))
    return sorted(out,key=lambda c:c.fingerprint),refused

def are_equivalent(event: CanonicalEvent, a: VenueInstrument, b: VenueInstrument) -> bool:
    try:
        return claim_fingerprint(event,a)==claim_fingerprint(event,b)
    except ValueError:
        return False

def are_complements(event: CanonicalEvent, a: VenueInstrument, b: VenueInstrument,
                    payout: Decimal=Decimal("1")) -> bool:
    """True only when full payoff vectors sum to constant payout in every state."""
    if validate_instrument(event,a) or validate_instrument(event,b):
        return False
    for o in event.outcomes:
        if Decimal(a.payoff[o])+Decimal(b.payoff[o]) != payout:
            return False
    return True
