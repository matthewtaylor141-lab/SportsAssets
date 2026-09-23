"""WHAT KIND OF EVENT THIS IS: an entry, an addition, a completion, or
something we cannot tell.

WHY IT MATTERS AND WHY IT WAS MISSING. Nothing in this codebase
distinguished one RN1 purchase from another, so every buy read as a new
entry. Under that reading an account that entered once and added four
times looks like five positions, its average cost is wrong, its pair
completion is invisible, and a seeded management experiment would hand
our policy five separate books where the account had one.

THE CLASSIFICATION IS DERIVED FROM THE ACCOUNT'S OWN RUNNING POSITION,
reconstructed from its observed fills on that condition in source-time
order. Nothing else: no price heuristics, no size thresholds, no
inference from what the market did afterwards.

    INITIAL_ENTRY     flat on BOTH legs, then a buy
    ADDITION          already long THIS leg, buy more of it
    PAIR_COMPLETION   already long ONE leg, buy the OTHER leg
    REDUCTION         sell part of a leg
    EXIT              sell a leg to zero
    UNKNOWN           prior inventory cannot be established

UNKNOWN IS A FIRST-CLASS ANSWER AND THE MOST IMPORTANT ONE HERE. Our
`trades` history is what an on-chain listener happened to capture from
the moment it started watching that account. If an account sells more
than we ever saw it buy, it held inventory we never observed -- and from
that moment the reconstructed position is a lower bound, not the
position. Every later event on that condition is then UNKNOWN.

    THE ALTERNATIVE IS WORSE. Clamping the running position at zero and
    carrying on produces a confident classification built on a position
    we know to be wrong, and the seeded experiment would then assign
    inventory that never existed. The directive is explicit: classify as
    UNKNOWN rather than invent a position.

THE THREE CLOCKS ARE KEPT SEPARATE, because collapsing them is how
look-ahead enters without anyone deciding to allow it:

    source_ts     the venue's / chain's own instant for the fill
    detected_ts   when our pipeline first saw it
    decision_ts   when our policy acted on it

`decision_ts` is never earlier than `detected_ts`: we cannot decide on
what we have not yet seen, and a replay that decided at `source_ts`
would be granting itself the detection latency for free.
"""

from __future__ import annotations

from dataclasses import dataclass, field

VERSION = "BETTOR_ENTRY_KIND_V1"

INITIAL_ENTRY = "INITIAL_ENTRY"
ADDITION = "ADDITION"
PAIR_COMPLETION = "PAIR_COMPLETION"
REDUCTION = "REDUCTION"
EXIT = "EXIT"
UNKNOWN = "UNKNOWN"

# Why a row came back UNKNOWN. Enumerated so a census can count them
# rather than parse prose.
U_OVERSOLD = "PRIOR_INVENTORY_NOT_OBSERVED_ACCOUNT_SOLD_MORE_THAN_IT_BOUGHT"
U_FIRST_IS_SELL = "FIRST_OBSERVED_EVENT_ON_THIS_CONDITION_IS_A_SELL"
U_NO_LEG = "OUTCOME_INDEX_MISSING_SO_LEGS_CANNOT_BE_SEPARATED"
U_TAINTED = "AN_EARLIER_EVENT_ON_THIS_CONDITION_WAS_UNKNOWN"
U_WINDOW_EDGE = "FIRST_FILL_SITS_AT_THE_EDGE_OF_OUR_OBSERVATION_WINDOW"

# How close to the edge of our observation window is too close to trust.
# A condition whose first observed fill is within this of the account's
# first observed fill ANYWHERE may have opened before we were watching.
EDGE_S = 60.0


@dataclass
class Fill:
    """One observed fill by the tracked account."""
    source_ts: float
    detected_ts: float
    outcome_index: int | None
    side: str                     # BUY | SELL
    size: float
    price: float
    trade_id: int | None = None


@dataclass
class Classified:
    kind: str
    why: str
    unknown_reason: str | None = None
    # The running position BEFORE this fill, per leg, as reconstructed.
    position_before: dict = field(default_factory=dict)
    position_after: dict = field(default_factory=dict)
    # Is the reconstruction trustworthy at this point?
    position_is_a_lower_bound: bool = False
    source_ts: float | None = None
    detected_ts: float | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "why": self.why,
            "unknown_reason": self.unknown_reason,
            "position_before": dict(self.position_before),
            "position_after": dict(self.position_after),
            "position_is_a_lower_bound": self.position_is_a_lower_bound,
            "source_ts": self.source_ts,
            "detected_ts": self.detected_ts,
        }


def classify_condition(fills: list, *, account_first_seen_ts=None) -> list:
    """Classify every observed fill on ONE condition, in source order.

    Returns one `Classified` per input fill, in the same order as the
    SORTED sequence -- the caller gets the order back so it cannot
    misalign results against an unsorted input.

    TAINT IS STICKY. Once the reconstruction is known to be a lower
    bound, it stays one: later arithmetic on a position we know to be
    incomplete cannot recover confidence. So every subsequent event on
    that condition is UNKNOWN, with U_TAINTED naming why rather than
    repeating the original cause as if it were freshly detected.
    """
    seq = sorted(fills, key=lambda f: (f.source_ts, f.trade_id or 0))
    pos: dict = {}
    out = []
    tainted = False
    first_seen = None

    for f in seq:
        before = dict(pos)
        oi = f.outcome_index

        if oi is None:
            tainted = True
            out.append(Classified(
                kind=UNKNOWN, unknown_reason=U_NO_LEG,
                why=("the fill carries no outcome_index, so it cannot be "
                     "attributed to a leg and no pair relationship can be "
                     "established"),
                position_before=before, position_after=dict(pos),
                position_is_a_lower_bound=True,
                source_ts=f.source_ts, detected_ts=f.detected_ts))
            continue

        held = pos.get(oi, 0.0)
        other_held = sum(v for k, v in pos.items() if k != oi and v > 0)

        if f.side == "SELL":
            if first_seen is None:
                # THE FIRST THING WE EVER SAW ON THIS CONDITION IS A
                # SELL. They were already long something we never
                # observed. This is the clearest form of the defect.
                tainted = True
                first_seen = f.source_ts
                out.append(Classified(
                    kind=UNKNOWN, unknown_reason=U_FIRST_IS_SELL,
                    why=("the first observed event on this condition is a "
                         "sell of %.4g, so inventory existed before our "
                         "observation began and its size and cost are not "
                         "recoverable" % f.size),
                    position_before=before, position_after=dict(pos),
                    position_is_a_lower_bound=True,
                    source_ts=f.source_ts, detected_ts=f.detected_ts))
                continue
            if f.size > held + 1e-9:
                tainted = True
                out.append(Classified(
                    kind=UNKNOWN, unknown_reason=U_OVERSOLD,
                    why=("a sell of %.4g against a reconstructed holding "
                         "of %.4g: the account held inventory we never "
                         "observed, so the position is a lower bound from "
                         "here on" % (f.size, held)),
                    position_before=before, position_after=dict(pos),
                    position_is_a_lower_bound=True,
                    source_ts=f.source_ts, detected_ts=f.detected_ts))
                continue
            pos[oi] = held - f.size
            if tainted:
                kind, reason = UNKNOWN, U_TAINTED
                why = ("a sell, but an earlier event on this condition was "
                       "UNKNOWN so the position it reduces is not trusted")
            elif pos[oi] <= 1e-9:
                kind, reason = EXIT, None
                why = "sell of %.4g closes the leg to zero" % f.size
            else:
                kind, reason = REDUCTION, None
                why = ("sell of %.4g leaves %.4g of the leg"
                       % (f.size, pos[oi]))
            out.append(Classified(
                kind=kind, unknown_reason=reason, why=why,
                position_before=before, position_after=dict(pos),
                position_is_a_lower_bound=tainted,
                source_ts=f.source_ts, detected_ts=f.detected_ts))
            continue

        # ── a BUY ─────────────────────────────────────────────────────
        if first_seen is None:
            first_seen = f.source_ts
            # EDGE OF THE OBSERVATION WINDOW. If this condition's first
            # fill sits at the very start of everything we ever saw from
            # this account, the position may have opened before we were
            # watching and "flat before this" is an assumption rather
            # than an observation.
            if (account_first_seen_ts is not None
                    and f.source_ts - account_first_seen_ts <= EDGE_S):
                tainted = True
                pos[oi] = held + f.size
                out.append(Classified(
                    kind=UNKNOWN, unknown_reason=U_WINDOW_EDGE,
                    why=("this is the account's first observed fill on "
                         "this condition AND it lands within %.0fs of the "
                         "first fill we ever saw from the account, so "
                         "'flat beforehand' would be an assumption about "
                         "the edge of our window, not an observation"
                         % EDGE_S),
                    position_before=before, position_after=dict(pos),
                    position_is_a_lower_bound=True,
                    source_ts=f.source_ts, detected_ts=f.detected_ts))
                continue

        pos[oi] = held + f.size
        if tainted:
            kind, reason = UNKNOWN, U_TAINTED
            why = ("a buy, but an earlier event on this condition was "
                   "UNKNOWN so what this adds to is not trusted")
        elif held > 1e-9:
            kind, reason = ADDITION, None
            why = ("already long %.4g of this leg; this buys %.4g more"
                   % (held, f.size))
        elif other_held > 1e-9:
            kind, reason = PAIR_COMPLETION, None
            why = ("long %.4g of the OTHER leg of this condition and flat "
                   "on this one, so this buy pairs against it"
                   % other_held)
        else:
            kind, reason = INITIAL_ENTRY, None
            why = "flat on both legs of this condition before this buy"
        out.append(Classified(
            kind=kind, unknown_reason=reason, why=why,
            position_before=before, position_after=dict(pos),
            position_is_a_lower_bound=tainted,
            source_ts=f.source_ts, detected_ts=f.detected_ts))

    return out


def census(classified: list) -> dict:
    """Counts by kind and by UNKNOWN reason.

    REPORTED, NOT SUPPRESSED. A high UNKNOWN rate is a finding about our
    observation coverage, and a classifier that quietly resolved those
    rows into entries would hide it.
    """
    by_kind: dict = {}
    by_reason: dict = {}
    for c in classified:
        by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        if c.unknown_reason:
            by_reason[c.unknown_reason] = by_reason.get(
                c.unknown_reason, 0) + 1
    n = len(classified) or 1
    return {"version": VERSION, "n": len(classified), "by_kind": by_kind,
            "by_unknown_reason": by_reason,
            "unknown_fraction": by_kind.get(UNKNOWN, 0) / n,
            "note": ("UNKNOWN is a statement about OUR observation "
                     "coverage, not about the account. It is reported "
                     "rather than resolved.")}
