#!/usr/bin/env python3
"""GATHER THE PREFLIGHT EVIDENCE. Read-only. Submits nothing.

WHAT THIS EXISTS BECAUSE OF. `/api/calibration/preflight` consumes the
ticket and the check flags the CALLER supplies, and reads cash. It does
not go and find the venue facts, so "produce a ticket" still meant
somebody typing in a market id, a price, a tick and a fee schedule. That
is asking a person to invent the inputs a safety check is supposed to
verify -- and a preflight whose evidence was typed in by the operator it
protects is not a check at all.

This command fetches them:

    account state        cash, and whether it was readable
    open-order state     the resting orders on the candidate market
    market identity      the venue's own slug and outcome naming
    book                 best bid / best ask at a stamped instant
    tick + min quantity  the venue's own trading rules for that market
    fee terms            the schedule, or a named refusal
    timestamps           every read carries the moment it was taken

and then builds the proposed ticket and the exact blockers. NOTHING
UNREADABLE IS FILLED IN: a field the venue did not give us stays None
and becomes a named blocker, because a preflight that guesses is worse
than one that refuses.

SUBMISSION IS NOT REACHABLE FROM HERE. This module imports no order
creation path; `--submit` is not a flag; and a test asserts the absence.

IT RUNS WHERE THE VENUE IS REACHABLE. The container this was written in
has no egress to the venue, so it is a command for an authorised runtime
rather than something to be faked locally. It asks for no secret: it
uses the same configured credentials every other read in this process
uses.

VENUE READS ARE COORDINATED WITH PROTECTED RESEARCH. `--require-idle-
domain` refuses to read at all while a beta48 collector holds the venue
concurrency slot, so evidence gathering cannot contaminate a capture.
"""
from __future__ import annotations

import argparse
import json
import time

from . import calibration as cal

B_ACCOUNT = "ACCOUNT_STATE_UNREADABLE"
B_OPEN_ORDERS = "OPEN_ORDER_STATE_UNREADABLE"
B_MARKET = "MARKET_IDENTITY_UNREADABLE"
B_BOOK = "BOOK_UNREADABLE"
B_TICK = "TICK_NOT_PUBLISHED"
B_MIN_QTY = "MINIMUM_QUANTITY_NOT_PUBLISHED"
B_FEES = "FEE_TERMS_NOT_OBTAINED"
B_RESEARCH = "PROTECTED_RESEARCH_WINDOW_ACTIVE"

NOTHING_IS_INVENTED = (
    "a field the venue did not give us stays null and becomes a named "
    "blocker. The operator is never asked to supply a fact the check "
    "exists to verify")


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read(fn, *a, **kw):
    """One read, with its own timestamp and its own failure."""
    at = _now()
    try:
        return {"ok": True, "at": at, "value": fn(*a, **kw)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "at": at, "value": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


def gather(market_id, readers, quantity=None):
    """Every venue fact the preflight needs, each stamped, none invented.

    `readers` is a dict of callables so this is testable without a venue
    and so an authorised runtime can supply whichever verified reads it
    has.
    """
    ev = {
        "marketId": market_id,
        "gatheredAt": _now(),
        "nothingIsInvented": NOTHING_IS_INVENTED,
        "account": _read(readers["account"]),
        "openOrders": _read(readers["open_orders"], market_id),
        "market": _read(readers["market"], market_id),
        "book": _read(readers["book"], market_id),
        "rules": _read(readers["rules"], market_id),
        "fees": _read(readers["fees"], market_id),
    }
    blockers = []
    if not ev["account"]["ok"]:
        blockers.append(B_ACCOUNT)
    if not ev["openOrders"]["ok"]:
        blockers.append(B_OPEN_ORDERS)
    if not ev["market"]["ok"] or not (ev["market"]["value"] or {}).get("slug"):
        blockers.append(B_MARKET)
    book = ev["book"]["value"] if ev["book"]["ok"] else None
    if not book or book.get("ask") is None:
        blockers.append(B_BOOK)
    rules = ev["rules"]["value"] if ev["rules"]["ok"] else {}
    if not (rules or {}).get("tick"):
        blockers.append(B_TICK)
    if (rules or {}).get("minQuantity") is None:
        blockers.append(B_MIN_QTY)
    fees = ev["fees"]["value"] if ev["fees"]["ok"] else None
    if not fees or fees.get("entry") is None or fees.get("exit") is None:
        blockers.append(B_FEES)
    ev["evidenceBlockers"] = sorted(set(blockers))
    ev["evidenceComplete"] = not blockers
    return ev


def propose(evidence, session, quantity=None, order_type="LIMIT_GTC_POST_ONLY",
            inventory_plan=None, operator_stop="POST /api/calibration/stop"):
    """Build the ticket the evidence supports, and its exact blockers.

    THE SIZE IS DERIVED, NOT CHOSEN. The largest whole quantity whose
    all-in cost fits BOTH the $5 per-lifecycle cap and what the session
    has left. If that is below the venue's minimum the market is skipped
    -- the limit is never raised to fit a market.
    """
    if not evidence.get("evidenceComplete"):
        return {"ticket": None, "blockers": list(evidence["evidenceBlockers"]),
                "submittable": False,
                "why": "the venue facts are incomplete; nothing is filled in"}

    rules = evidence["rules"]["value"]
    fees = evidence["fees"]["value"]
    ask = float(evidence["book"]["value"]["ask"])
    tick = float(rules["tick"])
    min_qty = int(rules["minQuantity"])
    # SIZED IN INTEGER CENTS. Both the cap and the venue's prices are
    # cent-denominated, and binary floats are not: 4.40 // 0.40 is 10.0,
    # not 11, so a float sizing quietly leaves a whole share of the
    # approved allowance unused. Cents give the true largest quantity
    # that fits, and the all-in check below is still what enforces the
    # cap -- this only stops the arithmetic from being wrong in the
    # tidy-looking direction.
    room = min(cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE, cal.remaining(session))
    room_c = int(round(room * 100))
    fees_c = int(round(float(fees["entry"]) * 100)) + \
        int(round(float(fees["exit"]) * 100))
    ask_c = int(round(ask * 100))
    qty = max(0, (room_c - fees_c) // ask_c) if ask_c > 0 else 0
    if quantity is not None:
        qty = int(quantity)

    ticket = {
        "venue": evidence["market"]["value"].get("venue", "polymarket-us"),
        "account": evidence["account"]["value"].get("account", "NOT IDENTIFIED"),
        "marketId": evidence["market"]["value"]["slug"],
        "outcome": evidence["market"]["value"].get("outcome", "NOT IDENTIFIED"),
        "side": "BUY",
        "orderType": order_type,
        "clientOrderId": "CAL-%s" % evidence["gatheredAt"].replace(":", "")
                                                          .replace("-", ""),
        "expiry": evidence["market"]["value"].get("expiry", "NOT IDENTIFIED"),
        "price": ask,
        "quantity": qty,
        "tick": tick,
        "venueMinQuantity": min_qty,
        "entryFeeReserve": float(fees["entry"]),
        "exitFeeReserve": float(fees["exit"]),
        "feeModel": fees.get("model", "NOT IDENTIFIED"),
        "inventoryPlan": inventory_plan or "hold to settlement; no re-entry",
        "operatorStop": operator_stop,
        "stateFresh": True,
        "evidenceAsOf": evidence["gatheredAt"],
    }
    why = cal.refusals(ticket, session,
                       {"available": evidence["account"]["value"].get("cash")})
    return {"ticket": ticket, "blockers": why, "submittable": False,
            "allInCost": cal.all_in_cost(qty, ask, ticket["entryFeeReserve"],
                                         ticket["exitFeeReserve"]) if qty > 0
            else None,
            "why": "a ticket is a PROPOSAL; submission needs a human "
                   "approval and is disabled in this release"}


def _cli():                                                   # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", required=True)
    ap.add_argument("--quantity", type=int, default=None)
    ap.add_argument("--require-idle-domain", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    raise SystemExit(
        "CALIBRATION_EVIDENCE_NEEDS_AN_AUTHORISED_RUNTIME: this command "
        "reads the venue, and the readers are wired by the runtime that "
        "has credentials. It is deliberately not runnable from a "
        "container with no venue egress, because a local run would have "
        "to fake the very facts it exists to obtain. market=%s out=%s "
        "require_idle_domain=%s" % (a.market, a.out, a.require_idle_domain))


if __name__ == "__main__":                                    # pragma: no cover
    _cli()
