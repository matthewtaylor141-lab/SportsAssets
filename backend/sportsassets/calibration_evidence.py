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

# ── freshness blockers: what the literal True used to hide ───────────
B_STALE = "EVIDENCE_STALE"
B_CLOCK = "READ_TIMESTAMP_UNREADABLE"

# ── passive-pricing blockers ─────────────────────────────────────────
B_NO_BID = "NO_BEST_BID_TO_JOIN"
B_CROSSED_BOOK = "BOOK_CROSSED_OR_LOCKED"
B_PRICE_OFF_TICK = "PASSIVE_PRICE_NOT_ON_THE_TICK_GRID"
B_NOT_PASSIVE = "PRICE_WOULD_CROSS_THE_SPREAD"
B_ORDER_TYPE_NOT_PRICEABLE = "ORDER_TYPE_HAS_NO_PASSIVE_PRICE_RULE"

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
        if not market.get("outcome"):
            blockers.append(B_OUTCOME)
        if market.get("outcomeSide") not in ("LONG", "SHORT"):
            # Which side of a shared-identifier market this is. The venue
            # reads it through the order intent; it cannot be defaulted.
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
    fees = ev["fees"]["value"] if ev["fees"]["ok"] else None
    if not fees or fees.get("entry") is None or fees.get("exit") is None:
        blockers.append(B_FEES)
    elif not fees.get("model"):
        # Numbers without provenance are numbers we cannot defend.
        blockers.append(B_FEE_MODEL)

    ev["freshness"] = freshness(ev)
    if ev["freshness"]["unreadableTimestamps"]:
        blockers.append(B_CLOCK)
    if ev["freshness"]["staleReads"]:
        blockers.append(B_STALE)

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

    THE PRICE IS PASSIVE, NOT CHOSEN EITHER. See PASSIVE_RULE.
    """
    if not evidence.get("evidenceComplete"):
        return {"ticket": None, "blockers": list(evidence["evidenceBlockers"]),
                "submittable": False,
                "why": "the venue facts are incomplete; nothing is filled in"}

    rules = evidence["rules"]["value"]
    fees = evidence["fees"]["value"]
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
    price_c = int(round(price * 100))
    qty = max(0, (room_c - fees_c) // price_c) if price_c > 0 else 0
    if quantity is not None:
        qty = int(quantity)

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
        "entryFeeReserve": float(fees["entry"]),
        "exitFeeReserve": float(fees["exit"]),
        "feeModel": fees["model"],
        "inventoryPlan": inventory_plan or "hold to settlement; no re-entry",
        "operatorStop": operator_stop,
        # COMPUTED. Never a literal.
        "stateFresh": bool(fresh["FRESH"]),
        "stateAgeSeconds": fresh["oldestSeconds"],
        "evidenceAsOf": evidence["gatheredAt"],
    }
    why = cal.refusals(ticket, session,
                       {"available": evidence["account"]["value"].get("cash")})
    return {"ticket": ticket, "blockers": why, "submittable": False,
            "pricing": priced,
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


def default_readers(client=None):
    """The configured READ-ONLY venue readers.

    Each is backed by a call this codebase already uses for real reads:
    `account.balances`, `orders.list` (through the calibration adapter's
    strict wrapper), `markets.retrieve_by_slug` and `markets.bbo`. No
    order path is imported.

    A fact with NO verified read raises `ReaderNotWired`, so it lands as
    a named blocker rather than a plausible-looking number.
    """
    from . import pmus

    def _client():
        return client if client is not None else pmus._get_client()

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
        cash = _amount((usd or {}).get("cash") or (usd or {}).get("balance"))
        return {"account": (resp.get("accountId") or resp.get("account")
                            or (usd or {}).get("accountId")),
                "cash": cash,
                "source": "account.balances"}

    def open_orders(market_id):
        from .calibration_read import read_open_order_ids
        # The strict reader: unreadable RAISES, empty is []. It lives in
        # the read-only module, so this command does not import a module
        # that can send an order.
        return read_open_order_ids(market_id)

    def market(market_id):
        m = (_client().markets.retrieve_by_slug(market_id) or {})
        m = m.get("market") if isinstance(m.get("market"), dict) else m
        if not isinstance(m, dict):
            raise ValueError("MARKET_RESPONSE_NOT_A_MAPPING")
        return {"slug": m.get("slug") or m.get("marketSlug"),
                "outcome": m.get("title") or m.get("question") or m.get("name"),
                # The venue does not publish a LONG/SHORT selector on the
                # market row; which side the ticket is for is an approval
                # input, so it stays unset here and blocks.
                "outcomeSide": m.get("outcomeSide"),
                "expiry": (m.get("closeTime") or m.get("endDate")
                           or m.get("expirationTime")),
                "venue": "polymarket-us",
                "source": "markets.retrieve_by_slug"}

    def book(market_id):
        bid, ask = pmus._bbo_quotes(_client(), market_id)
        return {"bid": bid, "ask": ask,
                "source": "markets.bbo marketData (the feed the side "
                          "attribution was proven against, run 33395797987)"}

    def rules(market_id):
        m = (_client().markets.retrieve_by_slug(market_id) or {})
        m = m.get("market") if isinstance(m.get("market"), dict) else m
        if not isinstance(m, dict):
            raise ValueError("MARKET_RESPONSE_NOT_A_MAPPING")
        return {"tick": _amount(m.get("tickSize")),
                "minQuantity": m.get("minQuantity", m.get("minOrderQuantity")),
                "source": "markets.retrieve_by_slug"}

    def fees(_market_id):
        raise ReaderNotWired(
            "NO VERIFIED FEE READ EXISTS IN THIS CODEBASE. The venue's fee "
            "schedule has never been read programmatically here, and the "
            "$5 all-in cap cannot be enforced against a fee we invented. "
            "This stays a blocker until a real read is added.")

    return {"account": account, "open_orders": open_orders,
            "market": market, "book": book, "rules": rules, "fees": fees}


# ── research isolation ───────────────────────────────────────────────

VENUE_TOUCHING_PREFIX = "beta48-"


def domain_isolation(fetch=None, repo=None, token=None):
    """Is a protected research collector holding or waiting for the slot?

    Returns one of CLEAR / ACTIVE / UNKNOWN, never a bare boolean, so an
    unreadable answer cannot be spent as an idle one. Every beta48-*
    workflow counts as venue-touching: a superset of the concurrency
    group, which errs towards refusing to read.
    """
    repo = repo or os.environ.get("GITHUB_REPOSITORY")
    token = token or os.environ.get("GITHUB_TOKEN")
    if fetch is None:
        if not repo or not token:
            return {"STATE": "UNKNOWN", "REASON": "NO_REPO_OR_TOKEN",
                    "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}

        def fetch(url):                                   # pragma: no cover
            import httpx
            r = httpx.get(url, timeout=20.0, headers={
                "Authorization": "Bearer %s" % token,
                "Accept": "application/vnd.github+json"})
            r.raise_for_status()
            return r.json()

    occupying = []
    try:
        for status in ("in_progress", "queued"):
            page = fetch("https://api.github.com/repos/%s/actions/runs"
                         "?status=%s&per_page=100" % (repo, status))
            if not isinstance(page, dict) or "workflow_runs" not in page:
                return {"STATE": "UNKNOWN", "REASON": "CENSUS_UNREADABLE",
                        "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}
            rows = page.get("workflow_runs")
            if not isinstance(rows, list):
                return {"STATE": "UNKNOWN", "REASON": "CENSUS_UNREADABLE",
                        "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}
            if len(rows) < int(page.get("total_count") or 0):
                # An incomplete walk cannot show an empty domain.
                return {"STATE": "UNKNOWN", "REASON": "CENSUS_INCOMPLETE",
                        "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}
            for r in rows:
                if not isinstance(r, dict):
                    return {"STATE": "UNKNOWN", "REASON": "CENSUS_ROW_MALFORMED",
                            "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}
                name = r.get("name")
                if not name:
                    return {"STATE": "UNKNOWN",
                            "REASON": "UNATTRIBUTABLE_OCCUPYING_RUN",
                            "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}
                if str(name).startswith(VENUE_TOUCHING_PREFIX):
                    occupying.append({"workflow": name, "id": r.get("id"),
                                      "status": status})
    except Exception as exc:                                   # noqa: BLE001
        return {"STATE": "UNKNOWN",
                "REASON": "CENSUS_READ_FAILED: %s" % type(exc).__name__,
                "note": AN_UNESTABLISHED_ISOLATION_IS_NOT_IDLE}

    if occupying:
        return {"STATE": "ACTIVE", "REASON": "COLLECTOR_HOLDS_THE_DOMAIN",
                "runs": occupying}
    return {"STATE": "CLEAR", "REASON": None, "runs": []}


# ── the command ──────────────────────────────────────────────────────

def run(market_id, readers=None, session=None, quantity=None,
        order_type="LIMIT_GTC_POST_ONLY", require_idle_domain=False,
        isolation=None, session_loader=None):
    """Gather, propose, and return the whole evidence record.

    Reads only. Returns a dict; never raises for an unreadable venue --
    an unreadable fact is a blocker, which is the point of the command.
    """
    report = {"marketId": market_id, "ranAt": _now(),
              "submissionReachable": False}

    if require_idle_domain:
        iso = isolation if isinstance(isolation, dict) else (
            isolation() if callable(isolation) else domain_isolation())
        report["domainIsolation"] = iso
        if iso.get("STATE") != "CLEAR":
            # NOT IDLE, AND NOT READ. No venue request is made at all.
            report["evidence"] = None
            report["proposal"] = {"ticket": None, "submittable": False,
                                  "blockers": [B_RESEARCH]}
            report["blockers"] = [B_RESEARCH]
            report["why"] = ("a protected research collector holds or may "
                             "hold the venue slot, or the state could not "
                             "be established; nothing was read")
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

    ev = gather(market_id, readers or default_readers(), quantity=quantity)
    proposal = propose(ev, session, quantity=quantity, order_type=order_type)
    report["evidence"] = ev
    report["proposal"] = proposal
    report["blockers"] = sorted(set(list(ev["evidenceBlockers"])
                                    + list(proposal.get("blockers") or [])))
    report["why"] = proposal.get("why")
    return report


def _durable_session():
    """The session the ledger holds. Raises when it cannot be read."""
    from . import calibration_store as store
    return store.load()


def _cli(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", required=True)
    ap.add_argument("--quantity", type=int, default=None)
    ap.add_argument("--order-type", default="LIMIT_GTC_POST_ONLY")
    ap.add_argument("--require-idle-domain", action="store_true")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    report = run(a.market, quantity=a.quantity, order_type=a.order_type,
                 require_idle_domain=a.require_idle_domain)
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
