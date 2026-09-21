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

import json
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
# yes_depth.ask/.bid are CUMULATIVE ACROSS levelsCaptured LEVELS, not the
# quantity at the quoted price. Measured on a real row (bsv_4d941e01...,
# 2026-09-21): yes_depth.ask = 4903.69 while the ladder's level 0 holds
# 17.00 at the quoted 0.7200 -- 288x. The remaining 4886 sit at 0.73 to
# 0.76, so the cumulative figure misstates the PRICE as well as the size.
# A row with the cumulative number and no ladder cannot be sized and is
# rejected rather than sized from the wrong quantity.
R_DEPTH_LADDER_ABSENT = "DEPTH_IS_CUMULATIVE_ONLY_NO_LADDER"
R_DEPTH_PRICE_MISMATCH = "DEPTH_LADDER_TOP_PRICE_DISAGREES_WITH_QUOTE"
R_UNREADABLE = "COLLECTOR_MARKED_UNREADABLE"
R_STALE = "BOOK_OLDER_THAN_DECISION_BOUND"


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
    # EXECUTABLE size at the quoted price -- the ladder's top level.
    bid_size: float = 0.0
    ask_size: float = 0.0
    # The five-level SUM. Kept because it bounds what the book holds in
    # total, and kept SEPARATE because an order sized against it would
    # be sized at a price the venue is not showing.
    cumulative_bid_size: float = 0.0
    cumulative_ask_size: float = 0.0
    depth_levels: int = 0
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


def _as_obj(v):
    """A dict/list from a JSONB column, whether it arrived parsed or not.

    psycopg returns JSONB already decoded; a JSON export file hands back
    a string. Both reach this adapter and only one of them was handled.
    """
    if v in (None, SENTINEL, "null"):
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return None
    return None


def _top_level(levels):
    """(quantity, price) of the BEST level, or (None, None).

    The best level is the one the venue marks lowest -- `level` 0 or 1
    depending on the capture -- NOT the first element of the array. An
    array whose order is assumed rather than read is how a mid-book
    level gets treated as the touch.
    """
    if not isinstance(levels, list) or not levels:
        return None, None
    best, rank = None, None
    for i, lv in enumerate(levels):
        if not isinstance(lv, dict):
            continue
        r = _num(lv.get("level"))
        r = i if r is None else r
        if rank is None or r < rank:
            best, rank = lv, r
    if best is None:
        return None, None
    return _num(best.get("qty") or best.get("size")), _num(best.get("price"))


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
    elif age > de.MAX_BOOK_AGE_S:
        # REAL BOOK AGES ARE HOURS. The captured sample runs from 61s to
        # 25,912s, against a 10s decision bound. This is not a defect in
        # the adapter -- it is the state of the capture, and an engine
        # deciding on a seven-hour-old book would be deciding on history.
        reasons.append(R_STALE)

    # THE VENUE'S OWN VOCABULARY. Real rows carry MARKET_STATE_OPEN and
    # MARKET_STATE_EXPIRED, not OPEN/CLOSED. Normalizing to the engine's
    # vocabulary is this adapter's job; making the engine guess is not.
    raw_state = g("venue_state") or g("VENUE_STATE")
    state = None
    if not raw_state or raw_state == SENTINEL:
        reasons.append(R_NO_STATE)
    else:
        state = str(raw_state).replace("MARKET_STATE_", "").upper()
        if state not in ("OPEN", "ACTIVE"):
            reasons.append("MARKET_STATE_%s" % state)

    readability = g("book_readability_status") or g("BOOK_READABILITY_STATUS")
    if readability and not str(readability).startswith(("READABLE", "OK")):
        reasons.append("%s:%s" % (R_UNREADABLE, str(readability).split(":")[-1]))

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

    # DEPTH: TOP OF BOOK, NOT THE FIVE-LEVEL SUM.
    #
    # `yes_depth` carries {"ask", "bid", "levelsCaptured": 5}. I read
    # those two numbers as the executable size at the quote. They are
    # the SUM ACROSS ALL FIVE LEVELS. On the row measured above the
    # quoted ask is 0.7200 with 17 contracts behind it and yes_depth.ask
    # reports 4903.69 -- the other 4886 sit at 0.73, 0.74, 0.75 and
    # 0.76. An order sized from that number is not merely 288x too
    # large, it is priced at a level the venue never showed.
    #
    # So the executable size comes from `multi_level_depth`, whose top
    # level must AGREE WITH THE QUOTE, and the cumulative figure is
    # carried separately where nothing can mistake it for executable.
    raw_depth = g("yes_depth") or g("YES_DEPTH")
    raw_ladder = g("multi_level_depth") or g("MULTI_LEVEL_DEPTH")
    bid_size = ask_size = 0.0
    cum_bid = cum_ask = 0.0
    depth_levels = 0
    depth_source = "NOT_PRESENT_IN_ROW"

    parsed = _as_obj(raw_depth)
    if isinstance(parsed, dict) and "status" not in parsed:
        cum_bid = _num(parsed.get("bid") or parsed.get("bidSize")) or 0.0
        cum_ask = _num(parsed.get("ask") or parsed.get("askSize")) or 0.0
        depth_levels = int(_num(parsed.get("levelsCaptured")) or 0)
        depth_source = "CUMULATIVE_ONLY"

    ladder = _as_obj(raw_ladder)
    if isinstance(ladder, dict):
        top_ask, ask_px = _top_level(ladder.get("ask") or ladder.get("asks"))
        top_bid, bid_px = _top_level(ladder.get("bid") or ladder.get("bids"))
        if top_ask is not None or top_bid is not None:
            # THE LADDER MUST DESCRIBE THE QUOTE IT SITS UNDER. If its
            # top level is at a different price the two were captured at
            # different instants, and neither the size nor the price can
            # be trusted for sizing.
            mism = ((ask is not None and ask_px is not None
                     and abs(ask_px - ask) > 1e-9)
                    or (bid is not None and bid_px is not None
                        and abs(bid_px - bid) > 1e-9))
            if mism:
                reasons.append(R_DEPTH_PRICE_MISMATCH)
            else:
                ask_size = top_ask or 0.0
                bid_size = top_bid or 0.0
                depth_source = "TOP_OF_BOOK_FROM_LADDER"
                if not depth_levels:
                    depth_levels = int(_num(ladder.get("levels")) or 0)

    if depth_source == "NOT_PRESENT_IN_ROW":
        reasons.append(R_NO_DEPTH)
    elif depth_source == "CUMULATIVE_ONLY":
        reasons.append(R_DEPTH_LADDER_ABSENT)

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
        bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size,
        cumulative_bid_size=cum_bid, cumulative_ask_size=cum_ask,
        depth_levels=depth_levels, depth_source=depth_source,
        complement_source=complement, venue=venue,
        account_class=account_class, fee_source=fee_source,
        reasons=reasons)
    return rec


# How far apart two legs' source timestamps may be and still describe one
# instant. A pair built from quotes seconds apart is a pair of two
# different markets.
PAIR_ALIGNMENT_S = 2.0

# Outcome labels the venue uses for genuine two-sided contracts. A pair
# must be one FROM each side of one of these families -- not merely two
# strings that happen to differ.
COMPLEMENT_FAMILIES = (
    frozenset({"yes", "no"}),
    frozenset({"over", "under"}),
)


def _family_of(a: str, b: str):
    la, lb = str(a).strip().lower(), str(b).strip().lower()
    for fam in COMPLEMENT_FAMILIES:
        if {la, lb} == fam:
            return "/".join(sorted(fam))
    return None


def pair_legs(records: list) -> dict:
    """Genuine complements of one contract, at one instant.

    THREE DEFECTS THIS REPLACES, all found by review.

    1. IT COULD PAIR TWO ROWS OF ONE LABEL. The old body sorted the legs
       by outcome and took the first two, so a contract observed twice
       on the 'no' side produced a 'pair' of no/no. Distinctness was
       checked on the SET of labels and then thrown away by the slice.

    2. IT NEVER CHECKED COMPLEMENT SEMANTICS. Two labels differing is
       not two labels complementing. 'over' and 'no' are both real
       outcome labels and are not each other's complement; a player
       name and 'no' are not a two-sided market. Pairing now requires
       both labels to come from one declared family.

    3. IT NEVER CHECKED TIME. Two quotes hours apart describe two
       different markets, and summing them is meaningless. Legs must
       share an instant within PAIR_ALIGNMENT_S of SOURCE timestamps --
       the venue's clock, not ours.

    Everything it cannot pair is returned with the reason, because an
    unpaired leg is a finding.

    NOTE ON THE 11 CONTRACTS. A venue-wide query found 11 contracts with
    two distinct outcome labels. That is a count of LABEL DIVERSITY and
    nothing more: none of them has been checked for complement family,
    timestamp alignment, or executable depth. They are candidates for
    verification, not verified executable pairs.
    """
    by_market: dict = {}
    for r in records:
        if r.status != ACCEPTED or not r.market_id:
            continue
        by_market.setdefault(r.market_id, []).append(r)

    pairs, unpaired = [], []
    for mid, legs in sorted(by_market.items()):
        best = None
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                a, b = legs[i], legs[j]
                fam = _family_of(a.outcome_leg, b.outcome_leg)
                if fam is None:
                    continue
                skew = _skew_s(a.source_timestamp, b.source_timestamp)
                if skew is None or skew > PAIR_ALIGNMENT_S:
                    continue
                if best is None or skew < best[0]:
                    best = (skew, a, b, fam)
        if best is not None:
            skew, a, b, fam = best
            pairs.append({
                "market_id": mid, "family": fam,
                "legs": [a.outcome_leg, b.outcome_leg],
                "observations": [a.observation_id, b.observation_id],
                "source_skew_s": round(skew, 4),
                "provenance": [a.complement_source, b.complement_source],
                "depth": [a.ask_size, b.ask_size],
                "executable": bool(a.ask_size > 0 and b.ask_size > 0),
            })
            continue

        labels = sorted({str(l.outcome_leg) for l in legs})
        if len(legs) == 1:
            why = "only one leg of this contract was observed"
        elif len(labels) < 2:
            why = ("%d rows but one label (%s): repeats of one side, not "
                   "complements" % (len(legs), labels[0]))
        elif not any(_family_of(x, y)
                     for x in labels for y in labels if x != y):
            why = ("labels %s are not a declared complement family; two "
                   "labels differing is not two labels complementing"
                   % labels)
        else:
            why = ("complement family present but source timestamps are "
                   "not within %.1fs" % PAIR_ALIGNMENT_S)
        unpaired.append({"market_id": mid, "legs": len(legs),
                         "labels": labels, "why": why})
    return {"pairs": pairs, "unpaired": unpaired,
            "alignment_bound_s": PAIR_ALIGNMENT_S,
            "note": ("a pair here is a CANDIDATE: same contract, declared "
                     "complement family, aligned source clocks. Executable "
                     "still requires depth on both legs.")}


def _skew_s(a: str | None, b: str | None) -> float | None:
    """Seconds between two venue SOURCE timestamps, or None."""
    from datetime import datetime

    def parse(x):
        if not x:
            return None
        t = str(x).replace("Z", "+00:00")
        if "." in t:                      # trim ns to us
            head, rest = t.split(".", 1)
            digits = "".join(c for c in rest if c.isdigit())[:6]
            tail = rest[len(digits):] if len(rest) > len(digits) else ""
            tz = tail if tail.startswith(("+", "-")) else "+00:00"
            t = "%s.%s%s" % (head, digits.ljust(6, "0"), tz)
        try:
            return datetime.fromisoformat(t)
        except ValueError:
            return None

    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return None
    return abs((pa - pb).total_seconds())


def to_book(rec: NormalizedObservation) -> de.Book:
    """A normalized record as the decision engine's Book.

    Depth comes from DISPLAYED_DEPTH_AT_T0 when the row carries it, and
    a row without it is REJECTED rather than given a zero -- zero depth
    and unrecorded depth are different facts and only one of them is
    about the market.
    """
    return de.Book(
        market_id=rec.market_id or "UNKNOWN",
        yes_bid=rec.bid, yes_ask=rec.ask,
        yes_bid_size=rec.bid_size, yes_ask_size=rec.ask_size,
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
    ages = sorted(r.age_s for r in records if r.age_s is not None)
    return {
        "book_age_s": ({"min": ages[0], "median": ages[len(ages) // 2],
                        "max": ages[-1],
                        "decision_bound_s": de.MAX_BOOK_AGE_S,
                        "within_bound": sum(1 for a in ages
                                            if a <= de.MAX_BOOK_AGE_S)}
                       if ages else None),
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
