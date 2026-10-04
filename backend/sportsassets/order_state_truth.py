"""ORDER STATE TRUTH: ONE PURE MAPPING FROM EVERY RECORDED ORDER STATE TO THE
NINE CANONICAL STATES, AND THE PROTECTION ARITHMETIC THAT FOLLOWS FROM IT.

Owner requirement (2026-10-04, P0): "A resting sell/hedge/order is NOT
protection." Every surface distinguishes

    PROPOSED   decided or planned, NOT yet at the venue / simulator
    SUBMITTED  sent, not yet acknowledged as resting or filled
    RESTING    on the book, NOTHING filled
    PARTIAL    part filled, the remainder still rests
    FILLED     completely filled
    CANCEL_PENDING  a cancel is requested but not confirmed: the order MAY
               STILL FILL. Its unfilled remainder is standing (never
               protection); any part already filled counts as filled
    CANCELLED  cancelled; any unfilled remainder is gone
    REJECTED   refused (by the venue, the simulator or our own rule)
    EXPIRED    expired; any unfilled remainder is gone

and ONE more, explicit, never folded into any of the nine:

    UNKNOWN    the recorded state is not one this module knows, or the
               submission outcome is genuinely unknown. UNKNOWN is never
               FILLED and contributes nothing to filled protection.

THE SINGLE SOURCE OF TRUTH. Every reader that shows an order state or a
protection quantity maps raw states through `order_state` (or the
convenience `canonical_order_state`) and computes protection through
`protection_summary`. A cancel that is requested but not confirmed is its
own state, CANCEL_PENDING (it can still fill); a mirror row EXCLUDED by our
own rule before it was ever sent maps to REJECTED with sub_state
EXCLUDED_BEFORE_SUBMISSION.

ONLY FILLED QUANTITY IS PROTECTION. The quantity a protective order has
FILLED (from the record's own filled quantity -- never its ordered quantity,
never inferred from its state) is protection; its unfilled remainder is a
STANDING order: an obligation that may still fill, shown beside protection
and never added to it. A floor that depends on a standing order filling is a
CONDITIONAL floor, labelled IF_FILLED, and is never the realized floor.

PURE. Imports nothing from this package; no I/O, no clock.
"""
from __future__ import annotations

VERSION = "ORDER_STATE_TRUTH_V1"

PROPOSED, SUBMITTED, RESTING, PARTIAL = (
    "PROPOSED", "SUBMITTED", "RESTING", "PARTIAL")
FILLED, CANCELLED, REJECTED, EXPIRED = (
    "FILLED", "CANCELLED", "REJECTED", "EXPIRED")
CANCEL_PENDING = "CANCEL_PENDING"
UNKNOWN = "UNKNOWN"

#: The nine canonical states, in lifecycle order (the owner's list).
CANONICAL_STATES = (PROPOSED, SUBMITTED, RESTING, PARTIAL, FILLED,
                    CANCEL_PENDING, CANCELLED, REJECTED, EXPIRED)
#: Every value `order_state` can return.
ALL_STATES = CANONICAL_STATES + (UNKNOWN,)
#: On the book: may still fill without a new decision (a requested but
#: unconfirmed cancel included).
STANDING_STATES = (RESTING, PARTIAL, CANCEL_PENDING)
#: Not (yet) on the book, or not known to be.
PENDING_STATES = (PROPOSED, SUBMITTED, UNKNOWN)
LIVE_STATES = PENDING_STATES + STANDING_STATES
TERMINAL_STATES = (FILLED, CANCELLED, REJECTED, EXPIRED)
#: States whose RECORDED filled quantity is a fill that happened. UNKNOWN is
#: excluded on purpose (its record is not trusted), REJECTED cannot have
#: filled, RESTING has by definition filled nothing (a RESTING row carrying
#: a fill is mapped to PARTIAL first), PROPOSED / SUBMITTED have not reached
#: a book.
FILL_BEARING_STATES = (PARTIAL, FILLED, CANCEL_PENDING, CANCELLED,
                       EXPIRED)

SUB_EXCLUDED = "EXCLUDED_BEFORE_SUBMISSION"
SUB_ABANDONED = "ABANDONED_BEFORE_SEND"
SUB_OUTCOME_UNKNOWN = "SUBMISSION_OUTCOME_UNKNOWN"
SUB_UNMAPPED = "UNMAPPED_RAW_STATE"
SUB_UNKNOWN_SOURCE = "UNKNOWN_ORDER_SOURCE"
SUB_RESTING_WITH_FILL = "RECORDED_FILL_ON_A_RESTING_ROW"

MEANING = {
    PROPOSED: "decided or planned, NOT yet at the venue / simulator",
    SUBMITTED: "sent, not yet acknowledged as resting or filled",
    RESTING: ("resting on the book, NOTHING filled. A resting order is not a "
              "fill; resting protection is not protection until it fills"),
    PARTIAL: ("part filled; only the filled part counts, the remainder still "
              "rests"),
    FILLED: "completely filled",
    CANCEL_PENDING: ("cancel requested, not yet confirmed: it MAY STILL FILL. "
                     "Its unfilled remainder is NOT protection; only any part "
                     "already filled counts"),
    CANCELLED: "cancelled; the unfilled remainder is gone",
    REJECTED: "refused by the venue, the simulator or our own rule",
    EXPIRED: "expired; the unfilled remainder is gone",
    UNKNOWN: ("state unknown (unmapped, or the submission outcome is not "
              "known): never treated as filled"),
}

# ── THE SOURCES (the tables whose state machines are mapped) ──────────
SRC_PAPER = "paper_orders"
SRC_MIRROR = "execmirror_orders"
SRC_KALSHI = "kalshi_live_intents"
SRC_FUNDED = "bettor_funded_intents"
SRC_DECISION = "paper_decisions"

_PAPER = {
    "PENDING_SIMULATION": (SUBMITTED, None),
    "RESTING": (RESTING, None),
    "PARTIALLY_FILLED": (PARTIAL, None),
    "FILLED": (FILLED, None),
    "EXPIRED": (EXPIRED, None),
    "CANCEL_PENDING": (CANCEL_PENDING, None),
    "CANCELED": (CANCELLED, None),
    "REJECTED": (REJECTED, None)}
#: execmirror_orders and kalshi_live_intents share one state machine
#: (migrations 192 / 196, kalshi_orders.TRANSITIONS).
_MIRROR = {
    "PLANNED": (PROPOSED, None),
    "SUBMITTING": (SUBMITTED, None),
    "UNKNOWN": (UNKNOWN, SUB_OUTCOME_UNKNOWN),
    "OPEN": (RESTING, None),
    "PARTIALLY_FILLED": (PARTIAL, None),
    "FILLED": (FILLED, None),
    "CANCEL_REQUESTED": (CANCEL_PENDING, None),
    "CANCELLED": (CANCELLED, None),
    "EXPIRED": (EXPIRED, None),
    "REJECTED": (REJECTED, None),
    "EXCLUDED": (REJECTED, SUB_EXCLUDED)}
#: bettor_funded_intents (migration 125's CHECK).
_FUNDED = {
    "INTENT_RECORDED": (PROPOSED, None),
    "SEND_ATTEMPTED": (SUBMITTED, None),
    "ACKNOWLEDGED": (RESTING, None),
    "PARTIALLY_FILLED": (PARTIAL, None),
    "FILLED": (FILLED, None),
    "CANCELLED": (CANCELLED, None),
    "REJECTED": (REJECTED, None),
    "ABANDONED": (REJECTED, SUB_ABANDONED),
    "UNRESOLVED": (UNKNOWN, SUB_OUTCOME_UNKNOWN)}
#: A Derek ENTER decision with no order yet (position_rooms synthesises it).
_DECISION = {"DECIDED_ENTER_NOT_ORDERED": (PROPOSED, None)}

RAW_MAPS = {SRC_PAPER: _PAPER, SRC_MIRROR: _MIRROR, SRC_KALSHI: _MIRROR,
            SRC_FUNDED: _FUNDED, SRC_DECISION: _DECISION}


def source_key(source) -> str | None:
    """The table a record came from: the first word of a reader's source
    label ("paper_decisions (Derek ENTER, no order yet)" -> paper_decisions).
    None when it names no mapped table."""
    s = str(source or "").strip().split(" ")[0].split("#")[0]
    return s if s in RAW_MAPS else None


def _num(v) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def order_state(raw_state, *, source, qty=None, filled_qty=None) -> dict:
    """THE canonical state of one recorded order, with the quantities that
    follow from it. Pure; never raises.

      state            one of ALL_STATES (UNKNOWN for anything unmapped)
      sub_state        CANCEL_PENDING / EXCLUDED_BEFORE_SUBMISSION / ... or None
      filled_qty       the quantity that COUNTS as filled: the record's own
                       filled quantity in a fill-bearing state, else 0
      standing_qty     the unfilled remainder while it may still fill
                       (RESTING / PARTIAL / CANCEL_PENDING), else 0
      pending_qty      the unfilled remainder of a PROPOSED / SUBMITTED /
                       UNKNOWN order, else 0
      can_still_fill   standing or pending
    """
    raw = None if raw_state is None else str(raw_state)
    src = source_key(source)
    if src is None:
        state, sub = UNKNOWN, SUB_UNKNOWN_SOURCE
    else:
        state, sub = RAW_MAPS[src].get(raw or "", (UNKNOWN, SUB_UNMAPPED))
    q = _num(qty)
    f = _num(filled_qty)
    f = max(0.0, f) if f is not None else 0.0
    if state == RESTING and f > 0:
        # the record shows a fill: never present it as nothing-filled, and
        # count only the filled part
        state, sub = PARTIAL, sub or SUB_RESTING_WITH_FILL
    counted = f if state in FILL_BEARING_STATES else 0.0
    if state == FILLED and q is not None and f > q:
        counted = q
    rem = max(0.0, q - f) if q is not None else None
    standing = (rem or 0.0) if state in STANDING_STATES else 0.0
    pending = (rem or 0.0) if state in PENDING_STATES else 0.0
    return {"state": state, "sub_state": sub, "raw_state": raw,
            "source": src or (None if source is None else str(source)),
            "meaning": MEANING[state],
            "qty": q, "recorded_filled_qty": f,
            "filled_qty": round(counted, 6),
            "standing_qty": round(standing, 6),
            "pending_qty": round(pending, 6),
            "can_still_fill": state in LIVE_STATES,
            "is_fill": state == FILLED,
            "unknown_state_fill_excluded": (state == UNKNOWN and f > 0)}


def canonical_order_state(raw_state, *, source, filled_qty=None) -> str:
    """Just the canonical state (see `order_state`)."""
    return order_state(raw_state, source=source,
                       filled_qty=filled_qty)["state"]


def raw_states(source, states) -> list:
    """Every raw state of `source` that can map to one of `states` (for a
    SQL `state = ANY(...)` filter). A RESTING row carrying a fill reads as
    PARTIAL, so a raw resting state is listed under both."""
    m = RAW_MAPS.get(source_key(source) or "", {})
    want = set(states)
    out = []
    for raw, (st, sub) in m.items():
        cands = {st}
        if st == RESTING:
            cands.add(PARTIAL)
        if cands & want:
            out.append(raw)
    return sorted(out)


# ═════════════════════════════════════════════════════════════════════
# PROTECTION: only filled quantity is protection
# ═════════════════════════════════════════════════════════════════════

#: Order roles whose purpose is to protect a held position: the standing
#: protective sale of the held side and the hedge buy of its complement.
PROTECTIVE_ROLES = ("STANDING_PROTECTION", "HEDGE")
IF_FILLED = "IF_FILLED"
RULE = ("only FILLED quantity is protection: a RESTING, PARTIAL or "
        "CANCEL_PENDING order's unfilled remainder is a standing order (it "
        "may still fill) and is "
        "never counted as protection; a floor that needs a standing order "
        "to fill is CONDITIONAL (IF_FILLED) and never the realized floor")
POSITION_BASIS = ("position quantity = contracts held now on the protected "
                  "legs + contracts already SOLD by filled protective sales "
                  "(those sales came out of the position); unprotected "
                  "quantity = position quantity - filled protection quantity")


def is_protective(order: dict) -> bool:
    return str(order.get("role") or "") in PROTECTIVE_ROLES


def _st(o: dict) -> dict:
    """An order's state truth: from its raw state when it carries one,
    else from an already-canonical `state`."""
    if o.get("raw_state") is not None and source_key(o.get("source")):
        return order_state(o.get("raw_state"), source=o.get("source"),
                           qty=o.get("qty"), filled_qty=o.get("filled_qty"))
    st = str(o.get("state") or "")
    st = st if st in ALL_STATES else UNKNOWN
    q, f = _num(o.get("qty")), max(0.0, _num(o.get("filled_qty")) or 0.0)
    if st == RESTING and f > 0:
        st = PARTIAL
    rem = max(0.0, q - f) if q is not None else 0.0
    counted = f if st in FILL_BEARING_STATES else 0.0
    if st == FILLED and q is not None and f > q:
        counted = q
    return {"state": st, "sub_state": o.get("sub_state"),
            "filled_qty": round(counted, 6),
            "standing_qty": round(rem if st in STANDING_STATES else 0.0, 6),
            "pending_qty": round(rem if st in PENDING_STATES else 0.0, 6),
            "unknown_state_fill_excluded": st == UNKNOWN and f > 0}


def fmt_qty(v) -> str:
    n = _num(v)
    if n is None:
        return "UNAVAILABLE"
    return ("{:,.0f}".format(n) if abs(n - round(n)) < 1e-9
            else "{:,.2f}".format(n))


def fmt_usd(v) -> str:
    n = _num(v)
    if n is None:
        return "UNAVAILABLE"
    return ("-$" if n < 0 else "$") + "{:,.2f}".format(abs(n))


def order_line(o: dict) -> str:
    """'RESTING SELL: 2,083 @ $0.51 / FILLED: 0' -- the standing (unfilled)
    quantity first, then what has filled; never one number for both."""
    t = _st(o)
    lim = _num(o.get("limit"))
    shown = (t["standing_qty"] if t["state"] in STANDING_STATES else
             t["pending_qty"] if t["state"] in PENDING_STATES else
             _num(o.get("qty")))
    return "%s %s: %s @ %s / FILLED: %s" % (
        t["state"],
        str(o.get("direction") or "?").upper(), fmt_qty(shown),
        "UNAVAILABLE" if lim is None else "$%.2f" % lim,
        fmt_qty(t["filled_qty"]))


def protection_summary(*, held_qty, orders: list,
                       conditional_floor_if_filled_usd=None,
                       realized_protection_usd=None,
                       realized_floor_usd=None) -> dict:
    """THE PROTECTION LEDGER OF ONE POSITION. Pure.

    held_qty  contracts held NOW on the protected (non-hedge) legs, from fills
    orders    the position's orders (dicts with role, direction, qty,
              filled_qty, limit and raw_state+source or a canonical state)
    The three dollar figures are the caller's (it owns the payout table);
    they are passed through with their labels so every surface prints the
    same line."""
    h = _num(held_qty)
    prot = [o for o in orders if is_protective(o)]
    rows, filled, standing, pending = [], 0.0, 0.0, 0.0
    sold_by_protection, excluded = 0.0, []
    for o in prot:
        t = _st(o)
        filled += t["filled_qty"]
        standing += t["standing_qty"]
        pending += t["pending_qty"]
        if str(o.get("direction") or "").upper() == "SELL":
            sold_by_protection += t["filled_qty"]
        if t["unknown_state_fill_excluded"]:
            excluded.append(o.get("order_ref"))
        rows.append({"order_ref": o.get("order_ref"), "role": o.get("role"),
                     "direction": o.get("direction"), "state": t["state"],
                     "sub_state": t["sub_state"],
                     "limit": _num(o.get("limit")), "qty": _num(o.get("qty")),
                     "filled_qty": t["filled_qty"],
                     "standing_qty": t["standing_qty"],
                     "pending_qty": t["pending_qty"],
                     "line": order_line(o)})
    position = None if h is None else round(h + sold_by_protection, 6)
    unprot = None if position is None else round(max(0.0, position - filled),
                                                 6)
    live_rows = [r for r in rows if r["state"] in LIVE_STATES
                 or r["filled_qty"] > 0]
    head = " + ".join(r["line"] for r in live_rows) or "NO PROTECTIVE ORDER"
    line = "%s / CONDITIONAL FLOOR IF FILLED: %s / REALIZED PROTECTION: %s " \
           "/ UNPROTECTED QTY: %s" % (
               head, fmt_usd(conditional_floor_if_filled_usd)
               if standing > 0 else "NONE (nothing standing)",
               fmt_usd(realized_protection_usd if realized_protection_usd
                       is not None else (0.0 if filled == 0 else None)),
               fmt_qty(unprot))
    return {
        "version": VERSION,
        "position_qty": position,
        "held_qty_now": None if h is None else round(h, 6),
        "filled_protection_qty": round(filled, 6),
        "standing_order_qty": round(standing, 6),
        "pending_order_qty": round(pending, 6),
        "unprotected_qty": unprot,
        "conditional_floor_if_filled_usd": (
            _num(conditional_floor_if_filled_usd) if standing > 0 else None),
        "conditional_floor_label": IF_FILLED,
        "conditional_floor_reason": (
            None if standing > 0 else "NO_STANDING_PROTECTIVE_ORDER"),
        "realized_protection_usd": (
            _num(realized_protection_usd) if realized_protection_usd
            is not None else (0.0 if filled == 0 else None)),
        "realized_floor_usd": _num(realized_floor_usd),
        "realized_floor_excludes": IF_FILLED,
        "unknown_state_fills_excluded": excluded,
        "orders": rows, "line": line,
        "rule": RULE, "position_basis": POSITION_BASIS}


def describe() -> dict:
    return {"version": VERSION, "states": list(CANONICAL_STATES),
            "explicit_unknown": UNKNOWN, "meaning": MEANING,
            "maps": {k: {r: {"state": s, "sub_state": sub}
                         for r, (s, sub) in m.items()}
                     for k, m in RAW_MAPS.items()},
            "protective_roles": list(PROTECTIVE_ROLES), "rule": RULE}
