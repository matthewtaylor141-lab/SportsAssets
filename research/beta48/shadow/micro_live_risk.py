#!/usr/bin/env python3
"""THE RISK GATES. Fifteen of them, and an unset limit is not an absent limit.

THE DESIGN DECISION THAT MATTERS MOST HERE. None of these limits has a value,
and this module does not invent one. A dollar figure chosen by the system that
the figure is supposed to restrain is not a control. Every limit therefore
starts at AUTHORIZATION_STATUS = NOT_SET, and a NOT_SET limit BLOCKS live
submission exactly as a breached limit does.

That is deliberate and it is the opposite of the usual default. A missing limit
is normally treated as "no constraint"; here it is treated as "no permission".
The dry run still reports what WOULD have passed or failed once the numbers are
supplied, so the shape of the decision is visible today and only the magnitudes
wait on authorization.

This module contacts nothing and can place no order.
"""
import json
from decimal import Decimal as D

NOT_SET = "NOT_SET"
NOT_IDENTIFIED = "NOT_IDENTIFIED"

# name -> (unit, what it bounds)
GATES = {
    "MAX_ORDER_NOTIONAL": ("USD", "the largest single order"),
    "MAX_EVENT_EXPOSURE": ("USD", "all markets on one contest, together"),
    "MAX_MARKET_EXPOSURE": ("USD", "one market"),
    "MAX_TOTAL_OPEN_INVENTORY": ("USD", "everything held at once"),
    "MAX_ONE_SIDED_INVENTORY": ("USD", "unhedged directional exposure"),
    "MAX_SIMULTANEOUS_POSITIONS": ("COUNT", "positions open at once"),
    "MAX_DAILY_LOSS": ("USD", "realized plus unrealized, rolling 24 h"),
    "MAX_SESSION_LOSS": ("USD", "this session"),
    "MAX_UNREALIZED_DRAWDOWN": ("USD", "peak-to-trough on open inventory"),
    "MAX_INVENTORY_AGE": ("SECONDS", "how long a position may sit"),
    "MAX_SLIPPAGE": ("USD_PER_CONTRACT", "worse than the decision price"),
    "MAX_BOOK_STALENESS": ("SECONDS", "age of the book we decided on"),
    "MIN_EXPECTED_EV": ("USD", "below this, not worth the risk"),
    "MIN_EV_MARGIN_OVER_UNCERTAINTY": (
        "RATIO", "how far EV must clear its own error bar"),
    "MIN_LIQUIDITY_DEPTH": ("CONTRACTS", "executable size at our price"),
}

MAXIMA = tuple(g for g in GATES if g.startswith("MAX_"))
MINIMA = tuple(g for g in GATES if g.startswith("MIN_"))

WHY_NOT_SET_BLOCKS = (
    "a limit with no authorized value is not an unlimited limit; it is a "
    "control nobody has agreed to yet, and an unagreed control cannot permit")
LIMITS_ARE_NOT_SELF_ASSIGNED = (
    "the system does not choose the numbers that restrain the system")


def unset_limits():
    """The starting position: every limit declared, none authorized."""
    return {g: {"VALUE": NOT_SET, "UNIT": GATES[g][0],
                "BOUNDS": GATES[g][1],
                "AUTHORIZATION_STATUS": NOT_SET} for g in GATES}


def _d(x):
    if x in (None, NOT_SET, NOT_IDENTIFIED, ""):
        return None
    try:
        return D(str(x))
    except Exception:                                         # noqa: BLE001
        return None


def evaluate(limits, observed):
    """Check every gate. Returns a receipt; never raises on a breach.

    `observed` maps a gate name to the measured quantity it bounds. A gate with
    an authorized limit and no observation is NOT passed: it is UNEVALUATED,
    and unevaluated blocks, because a limit nobody measured against restrains
    nothing.
    """
    limits = dict(limits or unset_limits())
    observed = dict(observed or {})
    rows, breached, unset, unevaluated = [], [], [], []

    for g in sorted(GATES):
        spec = limits.get(g) or {}
        val = _d(spec.get("VALUE"))
        auth = spec.get("AUTHORIZATION_STATUS", NOT_SET)
        obs = _d(observed.get(g))
        row = {"GATE": g, "UNIT": GATES[g][0], "BOUNDS": GATES[g][1],
               "LIMIT": spec.get("VALUE", NOT_SET),
               "AUTHORIZATION_STATUS": auth,
               "OBSERVED": observed.get(g, NOT_IDENTIFIED)}
        if val is None or auth != "AUTHORIZED":
            row["VERDICT"] = "BLOCKED_LIMIT_NOT_SET"
            unset.append(g)
        elif obs is None:
            row["VERDICT"] = "BLOCKED_NOT_EVALUATED"
            unevaluated.append(g)
        elif g in MAXIMA and obs > val:
            row["VERDICT"] = "BREACH"
            breached.append(g)
        elif g in MINIMA and obs < val:
            row["VERDICT"] = "BREACH"
            breached.append(g)
        else:
            row["VERDICT"] = "PASS"
        rows.append(row)

    approved = not (breached or unset or unevaluated)
    return {
        "GATES_TOTAL": len(GATES),
        "GATES": rows,
        "PASSED": [r["GATE"] for r in rows if r["VERDICT"] == "PASS"],
        "BREACHED": breached,
        "LIMITS_NOT_SET": unset,
        "NOT_EVALUATED": unevaluated,
        "RISK_VERDICT": "RISK_APPROVED" if approved else "RISK_REJECTED",
        "LIVE_SUBMISSION": "BLOCKED" if not approved else "BLOCKED",
        "WHY_LIVE_SUBMISSION_STILL_BLOCKED": (
            "MICRO_LIVE_MODE = DRY_RUN_NO_SUBMIT; passing the risk gates is "
            "necessary and never sufficient"),
        "WHY_NOT_SET_BLOCKS": WHY_NOT_SET_BLOCKS,
        "LIMITS_ARE_NOT_SELF_ASSIGNED": LIMITS_ARE_NOT_SELF_ASSIGNED,
        "AN_UNEVALUATED_GATE_BLOCKS": True,
    }


def would_pass_if_authorized(limits, observed, hypothetical):
    """What the dry run shows today: the same evaluation with the proposed
    numbers substituted, labelled as hypothetical so it can never be mistaken
    for an authorization."""
    merged = {g: dict(unset_limits()[g]) for g in GATES}
    for g, v in (hypothetical or {}).items():
        if g in merged:
            merged[g] = {"VALUE": v, "UNIT": GATES[g][0],
                         "BOUNDS": GATES[g][1],
                         "AUTHORIZATION_STATUS": "AUTHORIZED"}
    r = evaluate(merged, observed)
    r.update({
        "THIS_IS_HYPOTHETICAL": True,
        "HYPOTHETICAL_LIMITS": dict(hypothetical or {}),
        "REAL_AUTHORIZATION_STATUS": NOT_SET,
        "LIVE_SUBMISSION": "BLOCKED",
        "WHY": ("this shows the shape of the decision once limits exist; it "
                "authorizes nothing and no limit here has been agreed"),
    })
    return r


def render(r):
    L = ["%-34s %-10s %-16s %-14s %s" % ("GATE", "UNIT", "LIMIT", "OBSERVED",
                                         "VERDICT")]
    for g in r["GATES"]:
        L.append("%-34s %-10s %-16s %-14s %s" % (
            g["GATE"], g["UNIT"], g["LIMIT"], g["OBSERVED"], g["VERDICT"]))
    L.append("")
    L.append("%-34s = %s" % ("RISK_VERDICT", r["RISK_VERDICT"]))
    L.append("%-34s = %s" % ("LIVE_SUBMISSION", r["LIVE_SUBMISSION"]))
    return "\n".join(L)


def to_json(r):
    return json.dumps(r, indent=1, sort_keys=True, default=str)
