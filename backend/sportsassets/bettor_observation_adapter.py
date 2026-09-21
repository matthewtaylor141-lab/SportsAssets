"""Captured observation rows -> normalized contracts the engine can decide on.

WHAT A CAPTURED ROW ACTUALLY IS, established by query rather than assumed:

    no_ask / no_bid      the literal string 'NOT_IDENTIFIED' on 100% of
                         1,393 rows over 7 days
    yes_ask              a real price on 820 of them
    outcome_leg          'no' (1,045), 'over' (158), 'yes' (8), and
                         otherwise a player or team name

So ONE ROW IS ONE OUTCOME LEG, not one market with four quotes. The
`yes_*` columns carry THAT LEG'S OWN book -- they are not the YES side of
anything -- and the complement columns have never been populated because
the sibling instrument is a separate read the collector does not make.

WHAT THIS MEANS FOR PAIRING. Two legs of the same event are NOT
automatically complements. An event can carry many markets (moneyline,
totals, spreads, player props), and two rows sharing an event_id and a
time bucket may be different contracts entirely. Sharing a bucket is a
necessary condition and nowhere near a sufficient one. Pairing here
therefore requires the SAME CONTRACT -- same market_id -- and two
distinct outcome legs of it, and refuses everything else by name.

AND THE INSTITUTIONAL SEMANTICS GOVERN. The demo venue's
`holds_both_legs_independently = SUPPORTED` is a fixture for exercising
code paths. The real institutional account's capability is UNKNOWN, and
UNKNOWN blocks. Normalizing a real observation must not quietly borrow
the demo's permissions, so this adapter stamps every record with the
venue and account class it was normalized FOR, and the decision engine
reads the capability from the registry for that pair.

PROVENANCE SURVIVES. Each normalized record says where its complement
price came from -- OBSERVED (the sibling's own book was read), DERIVED
(computed as 1 - own, which is an identity and blocked), or ABSENT --
and which fee schedule applied. A record that loses its provenance can
be decided on wrongly in a way nothing downstream can detect.

NOTHING IS FABRICATED. A row missing a price yields a REJECTED record
with a reason, never an invented quote. Rejections are returned, counted
and reported, because a normalizer that silently drops what it cannot
parse reports a clean run over an arbitrary subset.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

from . import bettor_decision_engine as de
from . import bettor_venue_contract as vc

ADAPTER_VERSION = "BETTOR_OBSERVATION_ADAPTER_V1"

SENTINEL = "NOT_IDENTIFIED"

ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"

# Reasons, enumerated so a report can count them rather than grep prose.
R_NO_MARKET_ID = "NO_MARKET_IDENTITY"
R_NO_OUTCOME = "NO_OUTCOME_IDENTITY"
R_NO_TIMESTAMP = "NO_SOURCE_TIMESTAMP"
R_NO_AGE = "NO_BOOK_AGE"
R_PRICE_SENTINEL = "PRICE_IS_NOT_IDENTIFIED_SENTINEL"
R_PRICE_UNPARSABLE = "PRICE_UNPARSABLE"
R_PRICE_OUT_OF_RANGE = "PRICE_OUTSIDE_0_1"
R_CROSSED = "BOOK_CROSSED_OR_LOCKED"
R_NO_STATE = "NO_MARKET_STATE"
R_NO_DEPTH = "NO_DEPTH_REPORTED"
R_UNREADABLE = "COLLECTOR_MARKED_UNREADABLE"


@dataclass
class NormalizedObservation:
    """One outcome leg of one contract, at one instant, with provenance."""
    status: str
    observation_id: str | None = None
    market_id: str | None = None
    event_id: str | None = None
    outcome_leg: str | None = None
    observed_at: str | None = None
    source_timestamp: str | None = None
    age_s: float | None = None
    venue_state: str | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    depth_source: str = SENTINEL
    complement_source: str = vc.ABSENT
    complement_market_id: str | None = None
    venue: str = "polymarket-us"
    account_class: str = "institutional"
    fee_source: str = "UNVERIFIED"
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _num(v):
    """A number, or None. The sentinel is None, never zero."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v) if math.isfinite(float(v)) else None
    s = str(v).strip()
    if not s or s == SENTINEL or s.lower() in ("none", "null", "nan"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def normalize(row: dict, *, venue: str = "polymarket-us",
              account_class: str = "institutional",
              fee_source: str = "UNVERIFIED") -> NormalizedObservation:
    """One captured row -> one normalized record, accepted or rejected."""
    reasons: list = []
    g = row.get

    market_id = g("market_id") or g("MARKET_ID")
    if not market_id or market_id == SENTINEL:
        reasons.append(R_NO_MARKET_ID)
    outcome = g("outcome_leg") or g("OUTCOME_LEG")
    if not outcome or outcome == SENTINEL:
        reasons.append(R_NO_OUTCOME)

    src_ts = g("book_source_ts") or g("BOOK_SOURCE_TIMESTAMP")
    if not src_ts or src_ts == SENTINEL:
        reasons.append(R_NO_TIMESTAMP)
    age = _num(g("book_age_s") or g("BOOK_AGE_S"))
    if age is None:
        reasons.append(R_NO_AGE)

    state = g("venue_state") or g("VENUE_STATE")
    if not state or state == SENTINEL:
        reasons.append(R_NO_STATE)

    readability = g("book_readability_status") or g("BOOK_READABILITY_STATUS")
    if readability and readability not in ("READABLE", "OK", None):
        reasons.append(R_UNREADABLE)

    # THE LEG'S OWN BOOK. The yes_* columns are this leg's quotes; they
    # are not the YES side of a two-sided market.
    raw_bid = g("yes_bid") or g("YES_BID")
    raw_ask = g("yes_ask") or g("YES_ASK")
    bid, ask = _num(raw_bid), _num(raw_ask)
    for raw, parsed, name in ((raw_bid, bid, "bid"), (raw_ask, ask, "ask")):
        if parsed is None:
            if str(raw).strip() == SENTINEL:
                reasons.append("%s_%s" % (R_PRICE_SENTINEL, name.upper()))
            else:
                reasons.append("%s_%s" % (R_PRICE_UNPARSABLE, name.upper()))
        elif not 0.0 < parsed < 1.0:
            reasons.append("%s_%s" % (R_PRICE_OUT_OF_RANGE, name.upper()))
    if bid is not None and ask is not None and ask <= bid:
        reasons.append(R_CROSSED)

    # DEPTH. The capture schema records no size column, so depth is
    # ABSENT -- and absent depth is not infinite depth. A record with no
    # depth can still be decided on; it simply cannot produce an
    # executable size, and the engine already refuses on that.
    depth_source = "ABSENT_IN_CAPTURE_SCHEMA"

    # COMPLEMENT. Never derived. bettor_state_capture is explicit that
    # deriving 1 - YES would assert a no-arbitrage identity Class C
    # already refuted, and the columns confirm the sibling was not read.
    comp_bid = _num(g("no_bid") or g("NO_BID"))
    comp_ask = _num(g("no_ask") or g("NO_ASK"))
    complement = vc.OBSERVED if (comp_bid is not None
                                 or comp_ask is not None) else vc.ABSENT

    rec = NormalizedObservation(
        status=REJECTED if reasons else ACCEPTED,
        observation_id=g("observation_id") or g("OBSERVATION_ID"),
        market_id=market_id if market_id != SENTINEL else None,
        event_id=(g("event_id") or g("EVENT_ID")),
        outcome_leg=outcome if outcome != SENTINEL else None,
        observed_at=str(g("observed_at") or g("OBSERVED_AT") or ""),
        source_timestamp=src_ts if src_ts != SENTINEL else None,
        age_s=age, venue_state=state if state != SENTINEL else None,
        bid=bid, ask=ask, depth_source=depth_source,
        complement_source=complement, venue=venue,
        account_class=account_class, fee_source=fee_source,
        reasons=reasons)
    return rec


def pair_legs(records: list) -> dict:
    """Group accepted legs into genuine complements of the SAME contract.

    SHARING AN EVENT OR A BUCKET IS NOT ENOUGH, and this is the check
    that keeps a totals line from being paired against a moneyline. Two
    legs are complements only when they carry the SAME market_id and two
    DISTINCT outcome legs of it.

    Returns the pairs it could form and, separately, every leg it could
    not pair and why -- because an unpaired leg is a finding, not a gap
    to be filled by loosening the rule.
    """
    by_market: dict = {}
    for r in records:
        if r.status != ACCEPTED or not r.market_id:
            continue
        by_market.setdefault(r.market_id, []).append(r)

    pairs, unpaired = [], []
    for mid, legs in sorted(by_market.items()):
        distinct = {l.outcome_leg for l in legs}
        if len(legs) == 1:
            unpaired.append({"market_id": mid, "legs": 1,
                             "why": "only one leg of this contract was "
                                    "observed; the sibling was never read"})
        elif len(distinct) < 2:
            unpaired.append({"market_id": mid, "legs": len(legs),
                             "why": ("%d rows but only %d distinct outcome "
                                     "leg(s); these are repeats of one "
                                     "side, not complements"
                                     % (len(legs), len(distinct)))})
        else:
            ordered = sorted(legs, key=lambda l: str(l.outcome_leg))[:2]
            pairs.append({"market_id": mid,
                          "legs": [l.outcome_leg for l in ordered],
                          "observations": [l.observation_id for l in ordered]})
    return {"pairs": pairs, "unpaired": unpaired}


def to_book(rec: NormalizedObservation) -> de.Book:
    """A normalized record as the decision engine's Book.

    Depth is zero because the capture schema has none, which means the
    engine will refuse for NO_EXECUTABLE_DEPTH rather than size a trade
    against a quantity nobody recorded. That refusal is correct and it
    is the honest state of this dataset.
    """
    return de.Book(
        market_id=rec.market_id or "UNKNOWN",
        yes_bid=rec.bid, yes_ask=rec.ask,
        yes_bid_size=0.0, yes_ask_size=0.0,
        no_bid=None, no_ask=None, no_bid_size=0.0, no_ask_size=0.0,
        age_s=rec.age_s, venue_state=rec.venue_state,
        complement_source=rec.complement_source)


def report(records: list) -> dict:
    """Accepted, rejected, and every reason counted."""
    counts: dict = {}
    for r in records:
        for reason in r.reasons:
            counts[reason] = counts.get(reason, 0) + 1
    accepted = [r for r in records if r.status == ACCEPTED]
    return {
        "adapter": ADAPTER_VERSION,
        "rows": len(records),
        "accepted": len(accepted),
        "rejected": len(records) - len(accepted),
        "rejection_reasons": dict(sorted(counts.items(),
                                         key=lambda kv: -kv[1])),
        "complement_sources": {
            s: sum(1 for r in records if r.complement_source == s)
            for s in (vc.OBSERVED, vc.DERIVED, vc.ABSENT)},
        "note": ("one row is one OUTCOME LEG of one contract, not a "
                 "two-sided market. Pairing requires the same market_id "
                 "and two distinct legs; sharing an event or bucket is "
                 "not sufficient."),
    }
