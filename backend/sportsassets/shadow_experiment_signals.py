"""THE CANDIDATE HYPOTHESES, AND WHAT EACH ONE ACTUALLY NEEDS.

Owner directive 2026-09-19 21:2xZ §3, verbatim: "Use ONLY candidate
models / hypotheses that actually exist in the registered BETTOR
research stack. Do not invent models to create activity."

THE STATE OF THAT STACK WHEN THIS WAS WRITTEN. There was no registry
and no candidate model in it. `model_outputs` is a column on
bettor_opportunities that every writer passes as None; no microprice,
order-flow, direction-score, relative-value or disagreement model
existed anywhere in the tree. So §3 read literally against the code
yielded the empty set, and §15 ("start as soon as the first frozen
candidate is ready") could never fire.

WHAT THIS MODULE DOES ABOUT THAT, and what it refuses to do. It is the
first entry in that registry, and it is built the only way that does not
violate §3's actual intent: every hypothesis below is computed from
evidence THE COLLECTOR ALREADY CAPTURES, under the provenance
vocabulary shadow_lanes.py already declares. Nothing is inferred from a
feature we do not have. Where a hypothesis the directive named needs a
feature that is not captured, it is DECLARED AND LEFT UNARMED rather
than approximated -- an OFI computed without order flow would not be a
weak OFI, it would be a different quantity wearing the name, and the
leaderboard would then rank a fiction.

    SHORT_HORIZON_DIRECTION_SCORE  ARMED   -- mid series; captured
    RELATIVE_VALUE_SIGNAL          ARMED*  -- both legs of a binary;
                                              captured once the
                                              complement is collected
    MICROPRICE_SIGNAL              UNARMED -- needs touch SIZES
    OFI_SIGNAL                     UNARMED -- needs size deltas
    EXTERNAL_DISAGREEMENT_SIGNAL   UNARMED -- needs an external feed

THE CONTROL IS NOT AN AFTERTHOUGHT (§9). Every armed candidate is
declared beside a frozen null whose direction rule ignores the signal
entirely. "We need to learn: DID THE MODEL ADD VALUE? not merely: DID
THE MARKET GO UP?" A candidate that beats nothing is not measured by
its own P&L; it is measured against the control that traded the same
markets at the same instants on no information.

EVERY FUNCTION HERE IS PURE AND RETURNS NOT_IDENTIFIED RATHER THAN A
GUESS. A signal that silently returns 0.0 when its input is absent is
indistinguishable from a signal that genuinely says "flat", and the
experiment would then be measuring the frequency of missing data.
"""

from __future__ import annotations

from . import shadow as sh
from . import shadow_lanes as lanes

NOT_IDENTIFIED = sh.NOT_IDENTIFIED

LONG = "LONG"
SHORT = "SHORT"
FLAT = "FLAT"
DIRECTIONS = (LONG, SHORT, FLAT)


def _f(value):
    """A float, or None -- never a silent zero."""
    if value is None or value == NOT_IDENTIFIED:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _unidentified(name: str, why: str) -> dict:
    return {"signal": name, "value": None, "status": NOT_IDENTIFIED,
            "direction": None, "actionable": False, "why": why}


# ── M1: SHORT_HORIZON_DIRECTION_SCORE ────────────────────────────────

M1 = "SHORT_HORIZON_DIRECTION_SCORE"
M1_PROVENANCE = lanes.PROV_SHORT_HORIZON_PRICE

# Frozen before the first outcome (§4). These are the numbers the
# declaration hashes, so changing one produces a new experiment rather
# than quietly re-tuning a running one.
M1_LOOKBACK_SAMPLES = 5
M1_MIN_SAMPLES = 3
M1_ENTRY_THRESHOLD = 0.01          # one cent of mid drift
M1_MAX_SPREAD_RELATIVE = 0.25      # refuse to read direction off a book
                                   # so wide that the mid is a fiction


def short_horizon_direction(mid_series) -> dict:
    """Signed drift of the mid over the recent captured samples.

    THIS IS DELIBERATELY THE SIMPLEST HONEST THING. It is a first
    prospective candidate whose purpose is to be FALSIFIED cheaply, not
    to be good: if a naive momentum read on this venue's books has no
    edge, that is a real finding, and it is one we can only get by
    committing to it prospectively.

    `mid_series` is oldest-first, as the collector wrote it. A series
    with gaps is still a series -- the timestamps travel with the trade
    -- but fewer than M1_MIN_SAMPLES real mids is not a measurement.
    """
    mids = [m for m in (_f(v) for v in (mid_series or [])) if m is not None]
    mids = mids[-M1_LOOKBACK_SAMPLES:]
    if len(mids) < M1_MIN_SAMPLES:
        return _unidentified(
            M1, "fewer than %d readable mids in the lookback"
                % M1_MIN_SAMPLES)

    first, last = mids[0], mids[-1]
    if first <= 0:
        return _unidentified(M1, "the oldest mid is not a positive price")

    drift = last - first
    if abs(drift) < M1_ENTRY_THRESHOLD:
        direction, actionable = FLAT, False
    else:
        direction, actionable = (LONG if drift > 0 else SHORT), True

    return {"signal": M1, "value": drift, "status": "MEASURED",
            "direction": direction, "actionable": actionable,
            "samples": len(mids), "first": first, "last": last,
            "why": None if actionable else
            "drift %.4f is inside the frozen %.4f threshold"
            % (drift, M1_ENTRY_THRESHOLD)}


def m1_gate(market_state: dict | None) -> str | None:
    """The one book condition M1's own rule refuses on.

    A wide book is not a bad model, it is an unreadable mid: the
    midpoint of a 30-cent spread is not a price anything traded at, so a
    direction read from it measures the spread rather than the market.
    """
    micro = market_state or {}
    rel = _f(micro.get("spreadRelative"))
    if rel is None:
        bid, ask = _f(micro.get("bid")), _f(micro.get("ask"))
        mid = _f(micro.get("mid"))
        rel = ((ask - bid) / mid) if (bid is not None and ask is not None
                                      and mid) else None
    if rel is None:
        return "SPREAD_NOT_IDENTIFIED"
    if rel > M1_MAX_SPREAD_RELATIVE:
        return "SPREAD_ABOVE_FROZEN_MAX"
    return None


# ── M2: MICROPRICE_SIGNAL (declared, NOT armed) ──────────────────────

M2 = "MICROPRICE_SIGNAL"
M2_PROVENANCE = lanes.PROV_MARKET_MICROSTRUCTURE
M2_REQUIRED = ("bidSize", "askSize")


def microprice(bid=None, ask=None, bid_size=None, ask_size=None) -> dict:
    """The size-weighted touch, and NOT_IDENTIFIED without sizes.

    WHY THIS IS UNARMED. The collector reads the venue's BBO feed, which
    it parses for bestBid and bestAsk and nothing else; no size reaches
    the row. A microprice computed with sizes assumed equal IS THE
    MIDPOINT -- it would be M1's input under a second name, and the
    leaderboard would show two experiments where there is one. So this
    returns NOT_IDENTIFIED until sizes are genuinely captured, and its
    declaration stays AWAITING_FEATURE.
    """
    b, a = _f(bid), _f(ask)
    bs, as_ = _f(bid_size), _f(ask_size)
    if b is None or a is None:
        return _unidentified(M2, "the touch is not readable")
    if bs is None or as_ is None:
        return _unidentified(
            M2, "touch sizes are not captured; an assumed-equal "
                "microprice is the midpoint under another name")
    total = bs + as_
    if total <= 0:
        return _unidentified(M2, "the touch has no size")
    value = (a * bs + b * as_) / total
    mid = (a + b) / 2.0
    tilt = value - mid
    return {"signal": M2, "value": value, "status": "MEASURED",
            "microprice": value, "mid": mid, "tilt": tilt,
            "direction": (LONG if tilt > 0 else SHORT if tilt < 0 else FLAT),
            "actionable": tilt != 0, "why": None}


# ── M3: OFI_SIGNAL (declared, NOT armed) ─────────────────────────────

M3 = "OFI_SIGNAL"
M3_PROVENANCE = lanes.PROV_ORDER_FLOW
M3_REQUIRED = ("bidSize", "askSize")


def order_flow_imbalance(previous=None, current=None) -> dict:
    """Cont's OFI over two consecutive touches.

    UNARMED FOR THE SAME REASON AS M2, and it matters more here: OFI is
    defined on CHANGES in resting size at the touch. With no sizes there
    is no OFI at all -- not a noisy one, none. Anything computed from
    prices alone and called OFI would be a price-change signal wearing
    an order-flow name, which is the exact failure §3 forbids.
    """
    for name, side in (("previous", previous), ("current", current)):
        if not isinstance(side, dict):
            return _unidentified(M3, "%s touch is absent" % name)
    pb, pa = _f(previous.get("bid")), _f(previous.get("ask"))
    cb, ca = _f(current.get("bid")), _f(current.get("ask"))
    pbs, pas = _f(previous.get("bidSize")), _f(previous.get("askSize"))
    cbs, cas = _f(current.get("bidSize")), _f(current.get("askSize"))
    if None in (pb, pa, cb, ca):
        return _unidentified(M3, "a touch price is not readable")
    if None in (pbs, pas, cbs, cas):
        return _unidentified(
            M3, "resting sizes are not captured; OFI is defined on "
                "changes in size and is not approximable from prices")

    # Standard construction: a bid that improved contributes its whole
    # new size, one that receded removes its old size, one that held
    # contributes the delta. The ask side enters with the sign flipped.
    if cb > pb:
        d_bid = cbs
    elif cb < pb:
        d_bid = -pbs
    else:
        d_bid = cbs - pbs
    if ca < pa:
        d_ask = cas
    elif ca > pa:
        d_ask = -pas
    else:
        d_ask = cas - pas

    value = d_bid - d_ask
    return {"signal": M3, "value": value, "status": "MEASURED",
            "direction": (LONG if value > 0 else SHORT if value < 0
                          else FLAT),
            "actionable": value != 0, "why": None}


# ── M4: RELATIVE_VALUE_SIGNAL ────────────────────────────────────────

M4 = "RELATIVE_VALUE_SIGNAL"
M4_PROVENANCE = lanes.PROV_CROSS_MARKET_RELATIVE_VALUE
M4_REQUIRED = ("legAsk", "complementAsk")

# A binary pair's two YES sides must cost about $1 together. Anything
# cheaper is a real dislocation; anything dearer is one the other way.
# Frozen before the first outcome.
M4_PAIR_BASIS = 1.0
M4_ENTRY_THRESHOLD = 0.02


def relative_value(leg_ask=None, complement_ask=None,
                   leg_bid=None, complement_bid=None) -> dict:
    """What the two legs of one binary cost together.

    THIS IS THE ONE CANDIDATE WHOSE EDGE IS NOT A PREDICTION. It does
    not forecast where the market goes; it reads a price relation that
    either holds or does not at the instant observed. That makes it the
    most falsifiable of the five, and the cheapest to be wrong about.

    It needs BOTH legs at one instant, which the collector captures only
    once the complement is collected beside the subject -- until then
    this is honestly NOT_IDENTIFIED rather than computed off a stale
    other side.
    """
    la, ca = _f(leg_ask), _f(complement_ask)
    if la is None or ca is None:
        return _unidentified(
            M4, "both legs' asks are required at one instant")
    pair_cost = la + ca
    dislocation = M4_PAIR_BASIS - pair_cost
    actionable = abs(dislocation) >= M4_ENTRY_THRESHOLD
    # Buying BOTH legs below basis locks value; the signal is on the
    # pair, so its direction is the pair's, not a single leg's.
    return {"signal": M4, "value": dislocation, "status": "MEASURED",
            "pairCost": pair_cost, "basis": M4_PAIR_BASIS,
            "legAsk": la, "complementAsk": ca,
            "direction": (LONG if dislocation > 0 else SHORT
                          if dislocation < 0 else FLAT),
            "actionable": actionable,
            "why": None if actionable else
            "pair cost %.4f is inside the frozen %.4f band"
            % (pair_cost, M4_ENTRY_THRESHOLD)}


# ── M5: EXTERNAL_DISAGREEMENT_SIGNAL (declared, NOT armed) ───────────

M5 = "EXTERNAL_DISAGREEMENT_SIGNAL"
M5_PROVENANCE = lanes.PROV_EXTERNAL_CONSENSUS
M5_REQUIRED = ("externalConsensusProbability",)


def external_disagreement(venue_mid=None, external_probability=None) -> dict:
    """Venue price against an exact-timestamp external consensus.

    UNARMED: `external_consensus` is a column no writer populates, and
    the provenance it would carry is EXACT_TIMESTAMP_EXTERNAL_CONSENSUS
    for a reason -- a consensus read at a different instant than the
    book is not a disagreement, it is a latency artefact. Arming this
    needs a feed whose timestamp we control, not a number we can find.
    """
    v, e = _f(venue_mid), _f(external_probability)
    if v is None:
        return _unidentified(M5, "the venue mid is not readable")
    if e is None:
        return _unidentified(
            M5, "no exact-timestamp external consensus is captured")
    gap = e - v
    return {"signal": M5, "value": gap, "status": "MEASURED",
            "venueMid": v, "externalProbability": e,
            "direction": (LONG if gap > 0 else SHORT if gap < 0 else FLAT),
            "actionable": gap != 0, "why": None}


# ── §9: the frozen null ──────────────────────────────────────────────

C0 = "NULL_CONTROL_NO_INFORMATION"


def null_control(mid_series=None, **_) -> dict:
    """The comparator that trades on no information at all.

    IT MUST BE DETERMINISTIC. A control that flipped a coin would make
    every comparison a question about the seed, and Math.random-style
    nondeterminism cannot be replayed from the ledger. So it takes a
    fixed side, and the question it answers is exact: over these same
    markets at these same instants, what did simply being long do?
    Anything the candidate earns above this line is the candidate's.
    """
    return {"signal": C0, "value": 0.0, "status": "MEASURED",
            "direction": LONG, "actionable": True,
            "why": "frozen null: always long, ignores every feature"}


SIGNALS = {
    M1: short_horizon_direction,
    M2: microprice,
    M3: order_flow_imbalance,
    M4: relative_value,
    M5: external_disagreement,
    C0: null_control,
}

PROVENANCES = {
    M1: M1_PROVENANCE, M2: M2_PROVENANCE, M3: M3_PROVENANCE,
    M4: M4_PROVENANCE, M5: M5_PROVENANCE,
    # The control reads no feature, so it declares the family it trades
    # in rather than one it consumes.
    C0: lanes.PROV_MARKET_MICROSTRUCTURE,
}
