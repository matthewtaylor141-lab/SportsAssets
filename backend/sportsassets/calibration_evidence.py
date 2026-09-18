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

    account state        cash and the account's own identifier
    open-order state     the resting orders on the candidate market
    market identity      the venue's own slug, outcome naming and expiry
    book                 best bid / best ask at a stamped instant
    tick + min quantity  the venue's own trading rules for that market
    fee terms            the schedule and its provenance, or a refusal
    timestamps           every read carries the moment it was taken

and then builds the proposed ticket and the exact blockers.

FOUR THINGS THE FIRST VERSION GOT WRONG, ALL NOW FIXED.

  1. `_cli()` PARSED ITS ARGUMENTS AND THEN RAISED SystemExit. It read
     nothing, wired nothing and produced nothing. An authorised runtime
     cannot make a command work that never had a body. The command now
     wires the configured read-only readers, looks up the durable
     session, checks the research-isolation condition, gathers, proposes
     and writes the evidence out.
  2. FRESHNESS WAS THE LITERAL `True`. The ticket asserted
     `"stateFresh": True` regardless of when anything had been read. It
     is now COMPUTED from the per-read timestamps against a bound, and a
     read older than the bound -- or one whose timestamp will not parse
     -- is a named blocker.
  3. MISSING PROVENANCE BECAME THE STRING "NOT IDENTIFIED". A ticket
     carrying `"account": "NOT IDENTIFIED"` has a nonempty account
     field, which is exactly how an unknown gets past a check that only
     tests for presence. Account, outcome, expiry and fee model are
     BLOCKERS when the venue did not name them, and the ticket is not
     built at all.
  4. THE PROPOSAL PRICED AT THE ASK. For a post-only order that is the
     taker's price: it crosses the spread, so the venue refuses it, and
     pricing there at all is a request to be filled immediately rather
     than a passive quote. The passive rule is now stated, applied and
     tested against the venue's own book and tick: a post-only
     calibration entry JOINS THE BEST BID and never crosses. When the
     rule cannot be satisfied the command REFUSES; it does not fall back
     to an aggressive price to obtain a fill.

NOTHING UNREADABLE IS FILLED IN: a field the venue did not give us stays
None and becomes a named blocker, because a preflight that guesses is
worse than one that refuses.

SUBMISSION IS NOT REACHABLE FROM HERE. This module imports no order
creation path; `--submit` is not a flag; and a test asserts the absence.

IT RUNS WHERE THE VENUE IS REACHABLE. The readers use the same configured
credentials every other read in this process uses, and it asks for no
secret. Where a fact has NO verified read in this codebase -- the fee
schedule is the current example -- the reader raises `ReaderNotWired` and
the fact becomes a blocker. That is the honest state of the evidence, not
a placeholder standing in for it.

VENUE READS ARE COORDINATED WITH PROTECTED RESEARCH. `--require-idle-
domain` refuses to read at all while a beta48 collector holds or is
waiting for the venue concurrency slot, so evidence gathering cannot
contaminate a capture. An isolation state that cannot be established is
NOT idle.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time

from . import calibration as cal

# ── evidence blockers ────────────────────────────────────────────────
B_ACCOUNT = "ACCOUNT_STATE_UNREADABLE"
B_OPEN_ORDERS = "OPEN_ORDER_STATE_UNREADABLE"
B_MARKET = "MARKET_IDENTITY_UNREADABLE"
B_BOOK = "BOOK_UNREADABLE"
B_TICK = "TICK_NOT_PUBLISHED"
B_MIN_QTY = "MINIMUM_QUANTITY_NOT_PUBLISHED"
B_FEES = "FEE_TERMS_NOT_OBTAINED"
B_RESEARCH = "PROTECTED_RESEARCH_WINDOW_ACTIVE"

# ── provenance blockers: what "NOT IDENTIFIED" used to hide ──────────
B_ACCOUNT_ID = "ACCOUNT_NOT_IDENTIFIED"
B_OUTCOME = "OUTCOME_NOT_IDENTIFIED"
B_OUTCOME_SIDE = "OUTCOME_SIDE_NOT_IDENTIFIED"
B_EXPIRY = "EXPIRY_NOT_IDENTIFIED"
B_FEE_MODEL = "FEE_MODEL_PROVENANCE_NOT_OBTAINED"
B_EXIT_PRICE_LIMIT = "EXIT_PRICE_LIMIT_NOT_STATED"
B_CANCEL_DEADLINE = "ENTRY_CANCELLATION_DEADLINE_NOT_STATED"
B_RESIDUAL_PLAN = "RESIDUAL_INVENTORY_FALLBACK_NOT_STATED"
B_NO_CANDIDATE = "NO_CANDIDATE_IN_THE_SUPPORTED_UNIVERSE"
B_EXIT_POLICY_MISSING = "EXIT_POLICY_NOT_STATED"

# ── freshness blockers: what the literal True used to hide ───────────
B_STALE = "EVIDENCE_STALE"
B_CLOCK = "READ_TIMESTAMP_UNREADABLE"

# ── passive-pricing blockers ─────────────────────────────────────────
B_NO_BID = "NO_BEST_BID_TO_JOIN"
B_CROSSED_BOOK = "BOOK_CROSSED_OR_LOCKED"
B_PRICE_OFF_TICK = "PASSIVE_PRICE_NOT_ON_THE_TICK_GRID"
B_NOT_PASSIVE = "PRICE_WOULD_CROSS_THE_SPREAD"
B_ORDER_TYPE_NOT_PRICEABLE = "ORDER_TYPE_HAS_NO_PASSIVE_PRICE_RULE"

EXIT_SELLS_ONLY_WHAT_WE_OWN = (
    "an exit may sell only RECONCILED inventory actually owned. Not an "
    "expected fill, not an unreconciled one -- selling what we have not "
    "confirmed we hold is how a lost placement becomes a short")

NO_NEW_ENTRY_WHILE_UNRESOLVED = (
    "no new entry while any order, fill, cancellation or inventory "
    "remains unresolved. The single-lifecycle limit is spent on the "
    "unresolved one")


def exit_plan(policy):
    """The exit, declared in full before the entry, or a named blocker.

    A PERMITTED PARTIAL FILL DOES NOT AUTHORISE ANOTHER EXIT ORDER. With
    automaticReplacementOrders false, whatever the one permitted order
    does not sell is a remainder, and what happens to that remainder has
    to be stated on the ticket. Holding it through settlement is a
    decision, not a silence.
    """
    if not isinstance(policy, dict):
        return {"BLOCKER": B_EXIT_POLICY_MISSING}
    for field, blocker in (("exitPriceLimit", B_EXIT_PRICE_LIMIT),
                           ("entryCancellationDeadline", B_CANCEL_DEADLINE),
                           ("residualInventoryFallback", B_RESIDUAL_PLAN)):
        v = policy.get(field)
        if v in (None, "") or (field == "exitPriceLimit"
                               and not isinstance(v, (int, float))):
            return {"BLOCKER": blocker}
    holds = policy.get("holdsRemainderThroughSettlement")
    if holds not in (True, False):
        return {"BLOCKER": B_RESIDUAL_PLAN,
                "why": "holding a remainder through settlement must be "
                       "explicit on the ticket, not assumed from silence"}
    return {"BLOCKER": None,
            "exitPriceLimit": float(policy["exitPriceLimit"]),
            "entryCancellationDeadline": str(
                policy["entryCancellationDeadline"]),
            "residualInventoryFallback": str(
                policy["residualInventoryFallback"]),
            "holdsRemainderThroughSettlement": holds}


NOTHING_IS_INVENTED = (
    "a field the venue did not give us stays null and becomes a named "
    "blocker. The operator is never asked to supply a fact the check "
    "exists to verify")

PASSIVE_RULE = (
    "A POST-ONLY CALIBRATION ENTRY JOINS THE BEST BID AND NEVER CROSSES. "
    "The price is the venue's own best bid, which must lie strictly below "
    "the best ask and on the published tick grid. Pricing at the ask is "
    "the TAKER's price: the venue refuses it for a post-only order, and "
    "asking for it is a request to be filled now rather than a passive "
    "quote. If the rule cannot be satisfied -- no bid, a crossed or locked "
    "book, a price off the tick grid -- the proposal is REFUSED. It is "
    "never replaced by an aggressive price in order to obtain a fill.")

AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE = (
    "an isolation state we could not read is not an idle one. The check "
    "refuses on UNKNOWN exactly as it refuses on ACTIVE")

# How old the venue facts may be when the ticket is built. The book moves;
# a ticket priced off a read from ten minutes ago is a ticket priced off a
# book that no longer exists.
MAX_EVIDENCE_AGE_S = 120

# The passive rule is defined for post-only order types only. A marketable
# price is an operator's decision, not this command's.
PASSIVE_ORDER_TYPES = ("LIMIT_GTC_POST_ONLY",)


class ReaderNotWired(Exception):
    """No verified read exists for this fact in this codebase.

    Raised rather than returning a plausible default, so the fact becomes
    a named blocker. Wiring a guess here would put an invented number
    behind a safety check.
    """


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse(ts):
    """An ISO timestamp -> epoch seconds, or None when it will not parse."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        t = ts.replace("Z", "+00:00")
        d = _dt.datetime.fromisoformat(t)
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _read(fn, *a, **kw):
    """One read, with its own timestamp and its own failure."""
    at = _now()
    try:
        return {"ok": True, "at": at, "value": fn(*a, **kw)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "at": at, "value": None,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}


READ_KEYS = ("account", "openOrders", "market", "book", "rules", "fees")


def freshness(evidence, now=None, max_age_s=MAX_EVIDENCE_AGE_S):
    """How old the oldest read is, COMPUTED, never asserted.

    The first version wrote `"stateFresh": True` into the ticket as a
    literal. A ticket that claims its state is fresh without consulting a
    clock is a ticket that will claim it at four in the morning off a
    read from yesterday.
    """
    now = _parse(now) if isinstance(now, str) else now
    if now is None:
        now = time.time()
    ages, unreadable = {}, []
    for key in READ_KEYS:
        r = evidence.get(key) or {}
        at = _parse(r.get("at"))
        if at is None:
            unreadable.append(key)
            continue
        ages[key] = round(now - at, 3)
    stale = sorted(k for k, age in ages.items() if age > max_age_s)
    return {
        "maxAgeSeconds": max_age_s,
        "ages": ages,
        "unreadableTimestamps": sorted(unreadable),
        "staleReads": stale,
        "oldestSeconds": max(ages.values()) if ages else None,
        "FRESH": (not unreadable and not stale and len(ages) == len(READ_KEYS)),
    }


def passive_price(book, tick, order_type):
    """The passive limit price for this book, or a named refusal.

    See PASSIVE_RULE. Returns {"PRICE": float} or {"BLOCKER": name}, plus
    the arithmetic it used, so the number can be checked rather than
    trusted.
    """
    out = {"rule": PASSIVE_RULE, "orderType": order_type,
           "bid": None, "ask": None, "tick": tick}
    if order_type not in PASSIVE_ORDER_TYPES:
        return dict(out, BLOCKER=B_ORDER_TYPE_NOT_PRICEABLE, PRICE=None)

    def _f(v):
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return x if x == x and x not in (float("inf"), float("-inf")) else None

    bid, ask, t = _f((book or {}).get("bid")), _f((book or {}).get("ask")), _f(tick)
    out.update(bid=bid, ask=ask, tick=t)
    if t is None or t <= 0:
        return dict(out, BLOCKER=B_TICK, PRICE=None)
    if bid is None or bid <= 0:
        # No resting bid to join. There is no passive price here, and the
        # answer is not "then take the offer".
        return dict(out, BLOCKER=B_NO_BID, PRICE=None)
    if ask is None or ask <= 0:
        return dict(out, BLOCKER=B_BOOK, PRICE=None)
    if bid >= ask:
        return dict(out, BLOCKER=B_CROSSED_BOOK, PRICE=None)

    # ON THE VENUE'S OWN GRID, in integer ticks. A price the venue cannot
    # represent is refused rather than rounded into one it can.
    ticks = round(bid / t)
    if abs(ticks * t - bid) > 1e-9:
        return dict(out, BLOCKER=B_PRICE_OFF_TICK, PRICE=None, ticks=ticks)
    price = round(ticks * t, 10)
    if price >= ask:
        # Unreachable given bid < ask above; kept because this is THE
        # invariant the first version violated, and an invariant that is
        # only true by argument is one nobody notices breaking.
        return dict(out, BLOCKER=B_NOT_PASSIVE, PRICE=None)
    return dict(out, BLOCKER=None, PRICE=price, ticks=ticks,
                crossesTheSpread=False)


def gather(market_id, readers, quantity=None, outcome_side=None):
    """Every venue fact the preflight needs, each stamped, none invented.

    `readers` is a dict of callables so this is testable without a venue
    and so an authorised runtime can supply whichever verified reads it
    has.
    """
    ev = {
        "marketId": market_id,
        "declaredOutcomeSide": outcome_side,
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
    account = ev["account"]["value"] if ev["account"]["ok"] else None
    if not ev["account"]["ok"]:
        blockers.append(B_ACCOUNT)
    elif not (account or {}).get("account"):
        # The ACCOUNT the order would be sent on has to be named. The old
        # code wrote the string "NOT IDENTIFIED" into the ticket, which is
        # a nonempty value and passes any check that tests for presence.
        blockers.append(B_ACCOUNT_ID)
    if not ev["openOrders"]["ok"]:
        blockers.append(B_OPEN_ORDERS)

    market = ev["market"]["value"] if ev["market"]["ok"] else None
    if not ev["market"]["ok"] or not (market or {}).get("slug"):
        blockers.append(B_MARKET)
    else:
        if market.get("outcomeSideBlocker"):
            # DECLARED, THEN VERIFIED. The operator named a side and the
            # venue record either confirms it or does not.
            blockers.append(market["outcomeSideBlocker"])
        if not market.get("outcome"):
            blockers.append(B_OUTCOME)
        if market.get("outcomeSide") not in ("LONG", "SHORT"):
            blockers.append(B_OUTCOME_SIDE)
        elif outcome_side and market["outcomeSide"] != outcome_side:
            blockers.append(B_OUTCOME_SIDE)
        if not market.get("expiry"):
            blockers.append(B_EXPIRY)

    book = ev["book"]["value"] if ev["book"]["ok"] else None
    if not book or book.get("ask") is None:
        blockers.append(B_BOOK)
    rules = ev["rules"]["value"] if ev["rules"]["ok"] else {}
    if not (rules or {}).get("tick"):
        blockers.append(B_TICK)
    if (rules or {}).get("minQuantity") is None:
        blockers.append(B_MIN_QTY)
    # THE FEE SCHEDULE, not a fee. Fees depend on quantity, price and
    # role, so the number is quoted after sizing in propose() and
    # reconciled against the venue's own preview there. What gather
    # establishes is that an applicable, dated schedule with provenance
    # exists at all.
    fees = ev["fees"]["value"] if ev["fees"]["ok"] else None
    sched = (fees or {}).get("schedule")
    if not fees or not isinstance(sched, dict) or not sched.get(
            "TAKER_COEFFICIENT"):
        blockers.append(B_FEES)
    elif not fees.get("model") or not sched.get("EFFECTIVE_DATE"):
        # Numbers without provenance are numbers we cannot defend.
        blockers.append(B_FEE_MODEL)

    # PRICE BASIS. The book must be quoted on the side the ticket is for.
    if book and market and book.get("priceBasis") != market.get("outcomeSide"):
        blockers.append(B_PRICE_BASIS)

    ev["freshness"] = freshness(ev)
    if ev["freshness"]["unreadableTimestamps"]:
        blockers.append(B_CLOCK)
    if ev["freshness"]["staleReads"]:
        blockers.append(B_STALE)

    ev["evidenceBlockers"] = sorted(set(blockers))
    ev["evidenceComplete"] = not blockers
    return ev


def propose(evidence, session, quantity=None, order_type="LIMIT_GTC_POST_ONLY",
            inventory_plan=None, operator_stop="POST /api/calibration/stop",
            exit_policy=None, preview=None):
    """Build the ticket the evidence supports, and its exact blockers.

    THE SIZE IS DERIVED, NOT CHOSEN. The largest whole quantity whose
    all-in cost fits BOTH the $5 per-lifecycle cap and what the session
    has left. If that is below the venue's minimum the market is skipped
    -- the limit is never raised to fit a market.

    THE PRICE IS PASSIVE, NOT CHOSEN EITHER. See PASSIVE_RULE.
    """
    if not evidence.get("evidenceComplete"):
        return {"ticket": None, "blockers": list(evidence["evidenceBlockers"]),
                "submittable": False,
                "why": "the venue facts are incomplete; nothing is filled in"}

    from . import calibration_fees as cf

    plan = exit_plan(exit_policy)
    if plan["BLOCKER"]:
        return {"ticket": None, "blockers": [plan["BLOCKER"]],
                "submittable": False,
                "why": "the exit is declared before the entry; %s"
                       % plan.get("why", "the policy does not state it")}

    rules = evidence["rules"]["value"]
    tick = float(rules["tick"])
    min_qty = int(rules["minQuantity"])

    priced = passive_price(evidence["book"]["value"], tick, order_type)
    if priced["BLOCKER"]:
        return {"ticket": None, "blockers": [priced["BLOCKER"]],
                "submittable": False, "pricing": priced,
                "why": "no passive price satisfies the rule on this book; "
                       "the proposal is refused rather than repriced to "
                       "cross"}
    price = priced["PRICE"]

    # SIZED IN INTEGER CENTS, AND THE FEE IS QUOTED FOR THE SIZE.
    # Fees depend on quantity, so a fee computed before sizing is a fee
    # for a different order. The loop sizes against the fee its own
    # quantity incurs and stops at the largest quantity whose all-in
    # cost still fits; it never computes one fee and assumes it survives
    # the sizing.
    room = min(cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE, cal.remaining(session))
    room_c = int(round(room * 100))
    price_c = int(round(price * 100))

    def _fees_for(n):
        entry = cf.entry_reserve(price, n, post_only=True)
        exitr = cf.exit_reserve(n, exit_policy)
        if entry["BLOCKER"] or exitr["BLOCKER"]:
            return None, entry, exitr
        return (int(entry["FEE"] * 100) + int(exitr["FEE"] * 100)), entry, exitr

    qty, entry_q, exit_q = 0, None, None
    if quantity is not None:
        qty = int(quantity)
        _c, entry_q, exit_q = _fees_for(qty)
    elif price_c > 0:
        n = (room_c) // price_c
        while n > 0:
            fc, entry_q, exit_q = _fees_for(n)
            if fc is None:
                break
            if n * price_c + fc <= room_c:
                qty = n
                break
            n -= 1
        if qty == 0 and entry_q is None:
            _c, entry_q, exit_q = _fees_for(1)

    if entry_q is None or entry_q.get("BLOCKER") or (
            exit_q is None or exit_q.get("BLOCKER")):
        why = [b for b in ((entry_q or {}).get("BLOCKER"),
                           (exit_q or {}).get("BLOCKER")) if b]
        return {"ticket": None, "blockers": why or [cf.B_EXIT_POLICY],
                "submittable": False, "pricing": priced,
                "why": "the fee terms for the sized order could not be "
                       "established; no ticket is built"}

    # THE VENUE'S OWN PREVIEW, for the order as sized. The documented
    # coefficients and formula were not retrieved by this code, so the
    # arithmetic alone is not evidence; agreement with the preview is.
    #
    # MISSING PREVIEW EVIDENCE IS A BLOCKER, NOT A SKIPPED CHECK. The
    # previous version only reconciled when a preview happened to be
    # passed, and the CLI never passed one -- so the check that made the
    # documented numbers trustworthy never ran at all.
    observed = None
    if preview is not None:
        try:
            raw = preview(price, qty) if callable(preview) else preview
        except Exception as exc:                               # noqa: BLE001
            raw = None
            observed = {"FEE": None, "BLOCKER": cf.B_PREVIEW_UNREADABLE,
                        "error": type(exc).__name__}
        if observed is None:
            observed = cf.preview_fee(raw)
    agreement = cf.reconcile(price, qty, observed)
    if not agreement["AGREED"]:
        return {"ticket": None, "blockers": [agreement["BLOCKER"]],
                "submittable": False, "pricing": priced,
                "feeAgreement": agreement,
                "why": "the documented schedule was not confirmed against "
                       "the venue's own preview for this exact order"}

    fresh = evidence["freshness"]
    ticket = {
        "venue": evidence["market"]["value"].get("venue", "polymarket-us"),
        "account": evidence["account"]["value"]["account"],
        "marketId": evidence["market"]["value"]["slug"],
        "outcome": evidence["market"]["value"]["outcome"],
        "outcomeSide": evidence["market"]["value"]["outcomeSide"],
        "side": "BUY",
        "orderType": order_type,
        "clientOrderId": "CAL-%s" % evidence["gatheredAt"].replace(":", "")
                                                          .replace("-", ""),
        "expiry": evidence["market"]["value"]["expiry"],
        "price": price,
        "pricing": priced,
        "quantity": qty,
        "tick": tick,
        "venueMinQuantity": min_qty,
        "entryFeeReserve": float(entry_q["FEE"]),
        "exitFeeReserve": float(exit_q["FEE"]),
        "feeModel": evidence["fees"]["value"]["model"],
        "feeSchedule": evidence["fees"]["value"]["schedule"],
        "feeAgreement": agreement,
        "exitPolicy": dict(exit_policy or {}),
        "rebatesAreNotBudget": cf.REBATES_ARE_NOT_BUDGET,
        # THE EXIT IS DECLARED BEFORE THE ENTRY, in full. A permitted
        # partial fill does NOT authorise another exit order, so what
        # happens to a remainder has to be written down rather than
        # assumed.
        "entryCancellationDeadline": plan["entryCancellationDeadline"],
        "exitPriceLimit": plan["exitPriceLimit"],
        "residualInventoryFallback": plan["residualInventoryFallback"],
        "holdsRemainderThroughSettlement": plan[
            "holdsRemainderThroughSettlement"],
        "exitMaySellOnlyReconciledInventory": EXIT_SELLS_ONLY_WHAT_WE_OWN,
        "noNewEntryWhileUnresolved": NO_NEW_ENTRY_WHILE_UNRESOLVED,
        "inventoryPlan": inventory_plan or plan["residualInventoryFallback"],
        "operatorStop": operator_stop,
        # COMPUTED. Never a literal.
        "stateFresh": bool(fresh["FRESH"]),
        "stateAgeSeconds": fresh["oldestSeconds"],
        "evidenceAsOf": evidence["gatheredAt"],
    }
    why = cal.refusals(ticket, session,
                       {"available": evidence["account"]["value"].get("cash")})
    return {"ticket": ticket, "blockers": why, "submittable": False,
            "pricing": priced, "feeAgreement": agreement,
            "allInCost": cal.all_in_cost(qty, price, ticket["entryFeeReserve"],
                                         ticket["exitFeeReserve"]) if qty > 0
            else None,
            "why": "a ticket is a PROPOSAL; submission needs a human "
                   "approval and is disabled in this release"}


# ── the actual readers ───────────────────────────────────────────────

def _amount(v):
    """A venue money field as a float, or None when unreadable."""
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# The venue's OWN schema field names, from the official references. The
# first version guessed at these and every guess was wrong, which is why
# they are named here as constants rather than spelled inline:
#
#   get-market-by-slug     orderPriceMinTickSize, minimumTradeQty,
#                          marketSides[].long and its description
#   get-account-balances   currentBalance, buyingPower
#
# CURRENT BALANCE IS NOT BUYING POWER. One is cash held; the other is what
# the venue will let you commit, and on a margin-capable account they are
# different numbers. The budget check needs the cash, so both are read and
# kept apart rather than blended into one field called "cash".
F_TICK = "orderPriceMinTickSize"
F_MIN_QTY = "minimumTradeQty"
F_BALANCE = "currentBalance"
F_BUYING_POWER = "buyingPower"
F_SIDES = "marketSides"
F_LONG = "long"

B_SIDE_NOT_FOUND = "OUTCOME_SIDE_NOT_ON_THE_VENUE_RECORD"
B_SIDE_NOT_DECLARED = "OUTCOME_SIDE_NOT_DECLARED_BY_THE_OPERATOR"
B_SIDE_AMBIGUOUS = "MARKET_SIDES_AMBIGUOUS"
B_PRICE_BASIS = "PRICE_BASIS_INCONSISTENT"

THE_SIDE_IS_DECLARED_THEN_VERIFIED = (
    "the operator declares which side the ticket is for and the venue "
    "record is then checked to confirm that side exists and which of the "
    "two it is. It is NOT inferred from the market title, and it does not "
    "default to LONG: on this venue both sides share one identifier, so a "
    "defaulted side is a coin flip with money behind it")

PRICE_BASIS_RULE = (
    "every price in the evidence, the approval and the native order is "
    "quoted ON THE DECLARED SIDE. The complementary outcome trades at "
    "1 - p, so a book read for LONG and an order sent for SHORT would be "
    "priced on two different bases and differ by exactly the spread "
    "between them")


def _amount(v):
    """A venue money field as a float, or None when unreadable."""
    if isinstance(v, dict):
        v = v.get("value", v.get("amount"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _market_row(client, market_id):
    m = (client.markets.retrieve_by_slug(market_id) or {})
    m = m.get("market") if isinstance(m.get("market"), dict) else m
    if not isinstance(m, dict):
        raise ValueError("MARKET_RESPONSE_NOT_A_MAPPING")
    return m


def resolve_side(market_row, declared):
    """Which venue side the operator's declared outcome side actually is.

    DECLARED, THEN VERIFIED. The caller says LONG or SHORT; this finds
    that side on the venue's own `marketSides` and returns its
    description and identifier, or a named blocker. Nothing is read out
    of the market title.
    """
    if declared not in ("LONG", "SHORT"):
        return {"OUTCOME_SIDE": None, "BLOCKER": B_SIDE_NOT_DECLARED,
                "rule": THE_SIDE_IS_DECLARED_THEN_VERIFIED}
    sides = (market_row or {}).get(F_SIDES)
    if not isinstance(sides, list) or not sides:
        return {"OUTCOME_SIDE": None, "BLOCKER": B_SIDE_NOT_FOUND,
                "rule": THE_SIDE_IS_DECLARED_THEN_VERIFIED}
    want = declared == "LONG"
    matches = [s for s in sides
               if isinstance(s, dict) and s.get(F_LONG) is want]
    if len(matches) != 1:
        # Zero means the venue does not list that side; more than one
        # means we cannot tell them apart. Neither is a side to trade.
        return {"OUTCOME_SIDE": None,
                "BLOCKER": B_SIDE_NOT_FOUND if not matches else B_SIDE_AMBIGUOUS,
                "sidesSeen": len(sides), "matched": len(matches),
                "rule": THE_SIDE_IS_DECLARED_THEN_VERIFIED}
    side = matches[0]
    return {"OUTCOME_SIDE": declared, "BLOCKER": None,
            "description": side.get("description") or side.get("name"),
            "identifier": side.get("identifier"),
            "long": side.get(F_LONG),
            "priceBasis": declared,
            "rule": THE_SIDE_IS_DECLARED_THEN_VERIFIED}


def account_identity(config=None):
    """WHO the order would be sent as, from credential-bound configuration.

    NOT from an accountId field in the balances response: the official
    get-account-balances schema does not promise one, and requiring an
    undocumented field to appear would make a real account look
    unidentified. The identity is the configured key the reads and the
    order both use, so it is bound to the credential rather than reported
    by the payload.
    """
    if config is None:
        from .config import settings
        # `settings` is an lru_cache-wrapped FACTORY, not an instance.
        # Reading `settings.pmus_key_id` off the wrapper returns nothing,
        # which made a perfectly configured account report
        # ACCOUNT_NOT_IDENTIFIED. Found by rehearsing the workflow's own
        # command -- the unit tests passed a config object and never
        # exercised this line.
        config = settings() if callable(settings) else settings
    key = getattr(config, "pmus_key_id", None)
    if not key or not str(key).strip():
        return {"ACCOUNT": None, "BLOCKER": B_ACCOUNT_ID,
                "why": "no PMUS key id is configured, so there is no "
                       "credential to bind an account identity to"}
    key = str(key).strip()
    # The identity is the credential, not a secret: the key id names the
    # account and the secret never leaves configuration. Only a short
    # fingerprint is carried into evidence so an id cannot be copied out
    # of a report.
    return {"ACCOUNT": "pmus:%s" % key[:8], "BLOCKER": None,
            "source": "credential-bound configuration (pmus_key_id)",
            "fullKeyInEvidence": False}


def default_readers(client=None, config=None, declared_side=None,
                    market_id=None):
    """The configured READ-ONLY venue readers, on the OFFICIAL field names.

    Each is backed by a call this codebase already uses for real reads:
    `account.balances`, `orders.list` (through the calibration read
    module's strict wrapper), `markets.retrieve_by_slug` and
    `markets.bbo`. No order path is imported.

    The fee reader is no longer a refusal: it quotes the published
    schedule. What it CANNOT do alone is establish the fee, so it also
    carries the preview the evidence command reconciles it against.
    """
    from . import pmus

    def _client():
        return client if client is not None else pmus._get_client()

    def market_id_of():
        if not market_id:
            raise ValueError("PREVIEW_NEEDS_THE_MARKET_SLUG")
        return market_id

    def account():
        resp = _client().account.balances()
        if not isinstance(resp, dict):
            raise ValueError("BALANCES_RESPONSE_NOT_A_MAPPING")
        rows = resp.get("balances")
        if not isinstance(rows, list):
            raise ValueError("BALANCES_HAS_NO_BALANCES_LIST")
        usd = None
        for r in rows:
            if isinstance(r, dict) and str(
                    r.get("currency") or "").upper() in ("USD", ""):
                usd = r
                break
        if usd is None:
            raise ValueError("BALANCES_HAS_NO_USD_ROW")
        ident = account_identity(config)
        # BOTH numbers, kept apart. `cash` is the one the budget check
        # uses; buying power is reported and never spent as cash.
        return {"account": ident["ACCOUNT"],
                "accountSource": ident.get("source"),
                "cash": _amount(usd.get(F_BALANCE)),
                "currentBalance": _amount(usd.get(F_BALANCE)),
                "buyingPower": _amount(usd.get(F_BUYING_POWER)),
                "balanceIsNotBuyingPower": (
                    "currentBalance is cash held; buyingPower is what the "
                    "venue will let us commit. The budget check uses the "
                    "cash"),
                "source": "account.balances"}

    def open_orders(market_id):
        from .calibration_read import read_open_order_ids
        # The strict reader: unreadable RAISES, empty is []. It lives in
        # the read-only module, so this command does not import a module
        # that can send an order.
        return read_open_order_ids(market_id)

    def market(market_id):
        m = _market_row(_client(), market_id)
        side = resolve_side(m, declared_side)
        return {"slug": m.get("slug") or m.get("marketSlug"),
                # The outcome is the venue's OWN description of the side
                # the operator declared -- not the market title, which
                # names the event rather than a side.
                "outcome": side.get("description"),
                "outcomeSide": side.get("OUTCOME_SIDE"),
                "outcomeSideBlocker": side.get("BLOCKER"),
                "sideIdentifier": side.get("identifier"),
                "priceBasis": side.get("priceBasis"),
                "marketTitle": m.get("title") or m.get("question"),
                "expiry": (m.get("closeTime") or m.get("endDate")
                           or m.get("expirationTime")),
                "venue": "polymarket-us",
                "source": "markets.retrieve_by_slug"}

    def book(market_id):
        bid, ask = pmus._bbo_quotes(_client(), market_id)
        # THE BOOK IS READ ON THE LONG SIDE. For a SHORT ticket the
        # complementary prices are 1 - ask / 1 - bid, and the conversion
        # is done HERE, once, so one basis reaches everything downstream.
        out = {"longBid": bid, "longAsk": ask, "priceBasis": declared_side,
               "source": "markets.bbo marketData (the feed the side "
                         "attribution was proven against, run 33395797987)",
               "basisRule": PRICE_BASIS_RULE}
        if declared_side == "SHORT":
            out["bid"] = None if ask is None else round(1.0 - ask, 10)
            out["ask"] = None if bid is None else round(1.0 - bid, 10)
        else:
            out["bid"], out["ask"] = bid, ask
        return out

    def rules(market_id):
        m = _market_row(_client(), market_id)
        return {"tick": _amount(m.get(F_TICK)),
                "minQuantity": m.get(F_MIN_QTY),
                "fields": {"tick": F_TICK, "minQuantity": F_MIN_QTY},
                "source": "markets.retrieve_by_slug"}

    def preview(price, quantity):
        """The venue's OWN preview for exactly this sized ticket.

        A documented read-only operation, built from the SAME mapping the
        order would use (calibration_read.preview_params), so a
        disagreement is about the order we would actually send. It
        creates nothing.
        """
        from .calibration_read import preview_params
        req = preview_params({"marketId": market_id_of(), "price": price,
                              "quantity": quantity, "side": "BUY",
                              "outcomeSide": declared_side,
                              "orderType": "LIMIT_GTC_POST_ONLY"})
        return _client().orders.preview(req)

    def fees(_market_id):
        from . import calibration_fees as cf
        # THE DOCUMENTED SCHEDULE, with its provenance attached. It is not
        # a fee for this order -- fees depend on quantity, price and role,
        # and none of those is known until the ticket is sized. The
        # evidence command quotes and reconciles after sizing.
        return {"schedule": dict(cf.SCHEDULE),
                "model": "%s effective %s (taker %s, maker %s)" % (
                    cf.SCHEDULE["SOURCES"][0], cf.SCHEDULE["EFFECTIVE_DATE"],
                    cf.SCHEDULE["TAKER_COEFFICIENT"],
                    cf.SCHEDULE["MAKER_REBATE_COEFFICIENT"]),
                "quotedAtSizing": True,
                "source": "calibration_fees.SCHEDULE"}

    return {"account": account, "open_orders": open_orders,
            "market": market, "book": book, "rules": rules, "fees": fees,
            "preview": preview}


# ── research isolation: DELEGATED, and never optional ────────────────
#
# The previous version walked two status filters and identified members by
# a `beta48-` prefix. run85-phase2-capture is a member of the venue
# concurrency group and carries no such prefix, so an EXECUTING run85 read
# as an idle domain -- the exact collector the coordination protects. And
# `?status=queued` is a filter the API cannot express completely, so a
# concurrency-held member could be invisible.
#
# It is replaced by calibration_domain, which delegates to the
# repository's own tested machinery: the inventory venue_domain discovers
# from the workflow files, the exhaustive per-workflow walk and census in
# run_census, and venue_domain.isolation for the verdict. No second
# census, no rule of its own.
#
# COORDINATION IS NOT A FLAG. `run()` calls it unconditionally. There is
# no argument that turns it off; the only argument records an operator
# EXPLICITLY accepting a snapshot-only read for a non-production gather,
# and that acceptance is written into the report.

# ── the command ──────────────────────────────────────────────────────

def run(market_id, readers=None, session=None, quantity=None,
        order_type="LIMIT_GTC_POST_ONLY", coordination=None,
        session_loader=None, accept_snapshot_only=False, outcome_side=None,
        exit_policy=None, preview=None):
    """Gather, propose, and return the whole evidence record.

    Reads only. Returns a dict; never raises for an unreadable venue --
    an unreadable fact is a blocker, which is the point of the command.
    """
    report = {"marketId": market_id, "ranAt": _now(),
              "submissionReachable": False}

    # COORDINATION FIRST, ALWAYS. Not behind a flag: omitting an argument
    # must not be a way to read the venue during a protected capture.
    from . import calibration_domain as cd
    coord = coordination if isinstance(coordination, dict) else (
        coordination() if callable(coordination)
        else cd.coordination(require_reservation=not accept_snapshot_only))
    report["coordination"] = coord
    report["snapshotOnlyAccepted"] = bool(accept_snapshot_only)
    if coord.get("blockers"):
        # NOT COORDINATED, AND NOT READ. No venue request is made at all.
        report["evidence"] = None
        report["proposal"] = {"ticket": None, "submittable": False,
                              "blockers": list(coord["blockers"])}
        report["blockers"] = list(coord["blockers"])
        report["why"] = ("a protected research collector holds or may hold "
                         "the venue slot, the domain state could not be "
                         "established, or no execution reservation is held; "
                         "nothing was read")
        return report

    if session is None:
        # THE DURABLE SESSION, not a dict made up here. The budget that
        # bounds the proposal has to be the one the ledger holds.
        loader = session_loader or _durable_session
        try:
            session = loader()
            report["sessionSource"] = "durable"
        except Exception as exc:                               # noqa: BLE001
            report["sessionSource"] = "UNAVAILABLE"
            report["evidence"] = None
            report["proposal"] = {"ticket": None, "submittable": False,
                                  "blockers": ["BUDGET_STATE_UNAVAILABLE"]}
            report["blockers"] = ["BUDGET_STATE_UNAVAILABLE"]
            report["why"] = ("the durable budget could not be read (%s); a "
                             "proposal sized against an assumed budget is "
                             "not a proposal" % type(exc).__name__)
            return report
    else:
        report["sessionSource"] = "caller"

    rs = readers or default_readers(declared_side=outcome_side,
                                    market_id=market_id)
    ev = gather(market_id, rs, quantity=quantity, outcome_side=outcome_side)
    # THE PREVIEW IS ALWAYS SUPPLIED when the readers offer one. The
    # previous CLI never passed it, so the reconciliation that makes the
    # documented schedule trustworthy silently never ran.
    pv = preview if preview is not None else rs.get("preview")
    proposal = propose(ev, session, quantity=quantity, order_type=order_type,
                       exit_policy=exit_policy, preview=pv)
    report["evidence"] = ev
    report["proposal"] = proposal
    report["blockers"] = sorted(set(list(ev["evidenceBlockers"])
                                    + list(proposal.get("blockers") or [])))
    report["why"] = proposal.get("why")
    return report


def _durable_session():
    """The session the ledger holds. Raises when it cannot be read.

    `calibration_store.load` is a COROUTINE. The previous version returned
    the coroutine object itself, which is truthy, never touches the
    database, and would have sized a ticket against an object rather than
    a budget -- a fresh $100 by accident. It is awaited here, on the real
    async interface, and a failure propagates rather than falling back:
    there is no invented session and no default allowance.
    """
    import asyncio

    from . import calibration_store as store

    async def _read():
        return await store.load()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read())
    raise RuntimeError(
        "CALIBRATION_SESSION_READ_INSIDE_A_RUNNING_LOOP: call "
        "calibration_store.load() directly from async code")


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", required=True)
    ap.add_argument("--quantity", type=int, default=None)
    ap.add_argument("--order-type", default="LIMIT_GTC_POST_ONLY")
    ap.add_argument("--outcome-side", choices=("LONG", "SHORT"), default=None,
                    help="WHICH SIDE the operator is proposing. Required for "
                         "a ticket; verified against the venue record rather "
                         "than inferred from the market title.")
    ap.add_argument("--max-exit-orders", type=int, default=None,
                    help="how many exit orders the permitted policy allows. "
                         "No default: an unstated exit policy cannot bound "
                         "the reserve for an exit that has not happened.")
    ap.add_argument("--partial-fills", action="store_true",
                    help="the permitted exit policy allows partial fills, so "
                         "a remainder may be left unsold.")
    ap.add_argument("--automatic-replacement-orders", action="store_true",
                    help="a partial fill's remainder may be re-offered. OFF "
                         "by default: a permitted partial fill does not "
                         "authorise another exit order.")
    ap.add_argument("--exit-price-limit", type=float, default=None,
                    help="the limit the exit order may not sell below.")
    ap.add_argument("--entry-cancellation-deadline", default=None,
                    help="when an unfilled entry is cancelled (ISO instant).")
    ap.add_argument("--residual-inventory-fallback", default=None,
                    help="what happens to inventory the one permitted exit "
                         "order does not sell.")
    ap.add_argument("--hold-remainder-through-settlement",
                    dest="hold_remainder", action="store_true",
                    help="state EXPLICITLY that a remainder is held to "
                         "settlement. Silence is not this decision.")
    ap.add_argument("--accept-snapshot-only", action="store_true",
                    help="record an operator's EXPLICIT acceptance of a "
                         "census snapshot instead of an execution "
                         "reservation. Does not disable the census.")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    policy = None
    if a.max_exit_orders is not None:
        policy = {"maxExitOrders": a.max_exit_orders,
                  "partialFillsAllowed": bool(a.partial_fills),
                  "automaticReplacementOrders": bool(
                      a.automatic_replacement_orders),
                  "exitPriceLimit": a.exit_price_limit,
                  "entryCancellationDeadline": a.entry_cancellation_deadline,
                  "residualInventoryFallback": a.residual_inventory_fallback,
                  "holdsRemainderThroughSettlement": bool(a.hold_remainder)}
    report = run(a.market, quantity=a.quantity, order_type=a.order_type,
                 outcome_side=a.outcome_side, exit_policy=policy,
                 accept_snapshot_only=a.accept_snapshot_only)
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if a.out:
        with open(a.out, "w") as fh:
            fh.write(text)
    print(text)
    # A report with blockers is a SUCCESSFUL read that found blockers, so
    # the exit code distinguishes "the command worked" from "the ticket is
    # clear": 0 no blockers, 1 blockers, and nothing here ever submits.
    return 1 if report.get("blockers") else 0


if __name__ == "__main__":                                    # pragma: no cover
    sys.exit(_cli())
