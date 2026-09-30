"""ONBOARDING AN ACCOUNT FROM VERIFIED VENUE IDENTITY.

THE MISTAKE THIS MODULE REFUSES TO MAKE. "Register a new account id as ACTIVE
with CLEAN accounting" was offered as one of two ways forward. It is not a way
forward: a row this system inserts is a row this system wrote, and writing
`accounting_status = 'CLEAN'` next to an id nobody reconciled is the system
certifying itself. A NEW REGISTRY ID IS NOT EVIDENCE OF CLEAN ACCOUNTING. It
is evidence that an INSERT ran.

`bettor_funded_activation.ACCOUNTING_OK` is `("CLEAN", "RECONCILED",
"VERIFIED")`, and those words have to be earned against the VENUE, because the
venue is the only party that knows what the account holds. So onboarding is
four reconciliations, and an account becomes eligible only when all four are
answered from a venue read:

    1. BALANCES       what cash the venue says the account has
    2. POSITIONS      every open position the venue holds for it
    3. OPEN ORDERS    every resting order the venue holds for it
    4. EXECUTIONS     the trades behind the two above, over a stated window

EACH CHECK HAS THREE OUTCOMES, NOT TWO. Reconciled; a real discrepancy; or
UNREADABLE -- and unreadable blocks, because "we could not look" must never
render the same as "we looked and it was empty". That distinction is the whole
reason this module exists as something other than an UPDATE statement.

THE BALANCE READ EXISTS NOW, AND THAT IS A CHANGE FROM THE FIRST VERSION OF
THIS MODULE. It reported `ADAPTER_CANNOT_READ_BALANCES` because `pmus` had no
call that stated the account's cash. The venue does expose one --
`GET /v1/account/balances`, wrapped by the SDK as `client.account.balances()`
-- so `pmus.balances()` was written and check 1 is reachable. The refusal is
kept for an adapter that genuinely has no such call, because a venue module
without a balance read still must not produce an eligible account.

A SUCCESSFUL CALL IS STILL NOT A RECONCILIATION. A 200 with no currency row,
or a row whose `currentBalance` does not parse, establishes nothing -- and
reading an absent field as 0 is the same error as a fee schedule answering 0
when it cannot price. Both are DISCREPANCY. And the venue's two answers are
checked against EACH OTHER: cash reserved against resting orders while the
open-order list is empty means the venue contradicts itself, which is not a
basis for marking an account clean.

AND acct_fc2d773a2afa4851 STAYS PAUSED. `resolve_existing` exists to take it
through the same four reconciliations as any other account; nothing here
unpauses a row on the strength of a decision to unpause it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time

from . import bettor_funded_account as ACC
from . import bettor_funded_activation as FA

VERSION = "BETTOR_ACCOUNT_ONBOARDING_V1"

#: The four reconciliations, in the order they are attempted. Balances first
#: because an account whose cash we cannot see is not one we can size against.
CHECKS = ("balances", "positions", "open_orders", "executions")

#: Verdicts. `UNREADABLE` is deliberately distinct from `DISCREPANCY`: one
#: says the venue disagreed with us, the other says we never heard from it.
RECONCILED = "RECONCILED"
DISCREPANCY = "DISCREPANCY"
UNREADABLE = "UNREADABLE"
NOT_SUPPORTED = "NOT_SUPPORTED_BY_THE_ADAPTER"

#: Only this verdict counts toward eligibility. Named as a set so no future
#: reader has to infer that "not a discrepancy" was ever good enough.
PASSING = (RECONCILED,)

#: How far back executions are walked. A window is stated because "every
#: execution ever" is not a thing a paged API answers, and an unstated window
#: is an unstated claim.
EXECUTION_WINDOW_S = 90 * 24 * 3600.0

#: The status a row gets when it is created. NOT ACTIVE and NOT CLEAN: an
#: account enters the registry as something that has not been reconciled yet,
#: which is the truth at that moment.
NEW_STATUS = "PENDING_VERIFICATION"
NEW_ACCOUNTING = "UNVERIFIED"

R_NO_ADAPTER = "NO_VENUE_ADAPTER_IS_AVAILABLE_TO_VERIFY_AGAINST"
R_NOT_RECONCILED = "THE_ACCOUNT_IS_NOT_RECONCILED_AGAINST_THE_VENUE"
R_ALREADY_ELIGIBLE = "THE_ACCOUNT_IS_ALREADY_MARKED_ELIGIBLE"
R_NO_ACCOUNT_ROW = "NO_SUCH_ACCOUNT_IN_THE_REGISTRY"
R_STILL_PAUSED = "THE_ACCOUNT_IS_PAUSED_AND_ITS_ACCOUNTING_IS_UNRESOLVED"

#: A statement kept in code because it is the point of the module.
A_NEW_ID_IS_NOT_EVIDENCE = (
    "Inserting a registry row proves an INSERT ran. Eligibility is earned "
    "against the venue: balances, positions, open orders and the executions "
    "behind them, each read and each reconciled.")


def _adapter(mod=None):
    if mod is not None:
        return mod
    import importlib
    return importlib.import_module("sportsassets.pmus")


# ── THE FOUR READS, EACH ANSWERING WITH ITS OWN VERDICT ──────────────

def read_balances(mod) -> dict:
    """WHAT THE VENUE SAYS THE ACCOUNT HOLDS IN CASH.

    `pmus` has no such call. That is reported, not skipped: an account whose
    cash cannot be read cannot be sized against, so this returns
    NOT_SUPPORTED and the account does not become eligible.
    """
    for name in ("balances", "balance", "account_balance"):
        fn = getattr(mod, name, None)
        if not callable(fn):
            continue
        via = "%s.%s" % (mod.__name__, name)
        try:
            said = fn()
        except Exception as exc:                           # noqa: BLE001
            return {"check": "balances", "verdict": UNREADABLE,
                    "read_via": via,
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                    "why": ("the balance read failed, so what this account "
                            "holds is unknown. Unknown is not empty")}
        # THE CALL SUCCEEDING IS NOT THE RECONCILIATION.
        #
        # A 200 carrying no currency row, or a row that does not state
        # `currentBalance`, establishes nothing about the account's cash --
        # and an absent field read as 0 is exactly the failure mode this
        # module exists to refuse. So the figures are inspected.
        rows = list((said or {}).get("balances") or [])
        if not rows:
            return {"check": "balances", "verdict": DISCREPANCY,
                    "read_via": via, "venue_said": said,
                    "why": ("the venue answered with no currency row, so the "
                            "account's cash is not established")}
        usable, unusable = [], []
        for r in rows:
            cur = str(r.get("currency") or "?")
            try:
                bal = float(r.get("currentBalance"))
            except (TypeError, ValueError):
                unusable.append({"currency": cur,
                                 "currentBalance": r.get("currentBalance"),
                                 "absent_fields": r.get("absent_fields")})
                continue
            usable.append({
                "currency": cur, "current_balance": bal,
                "buying_power": r.get("buyingPower"),
                "reserved_by_open_orders": r.get("openOrders"),
                "unsettled_funds": r.get("unsettledFunds"),
                "balance_reservation": r.get("balanceReservation"),
                "pending_withdrawals": r.get("pending_withdrawals"),
                "absent_fields": r.get("absent_fields") or []})
        if unusable:
            return {"check": "balances", "verdict": DISCREPANCY,
                    "read_via": via, "unusable_rows": unusable,
                    "usable_rows": usable,
                    "why": ("a currency row that does not state a parseable "
                            "currentBalance is not a balance of zero")}
        return {"check": "balances", "verdict": RECONCILED,
                "read_via": via, "currencies": usable,
                "venue_said": said,
                "why": ("the venue stated a parseable balance for every "
                        "currency row it returned")}
    return {"check": "balances", "verdict": NOT_SUPPORTED,
            "missing": "a balance read on %s" % mod.__name__,
            "refusal": "ADAPTER_CANNOT_READ_BALANCES",
            "why": ("this adapter exposes portfolio.positions, "
                    "portfolio.activities and orders.list, and nothing that "
                    "states the account's cash. Until it does, an account on "
                    "this venue cannot be reconciled on balances"),
            "engineering_gap": True}


def read_positions(mod) -> dict:
    """EVERY OPEN POSITION THE VENUE HOLDS, paged to exhaustion."""
    try:
        client = mod._get_client()
    except Exception as exc:                               # noqa: BLE001
        return {"check": "positions", "verdict": UNREADABLE,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "why": "no venue client, so no position read"}
    held, cursor, pages, eof = {}, "", 0, False
    try:
        for _ in range(50):
            resp = client.portfolio.positions(
                {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
            pages += 1
            for slug, p in (resp.get("positions") or {}).items():
                # AN ABSENT netPosition IS UNREADABLE, NEVER FLAT. This read
                # `float(p.get("netPosition") or 0)`, the exact defect
                # `bettor_funded_account` was written to replace: a row that
                # does not state its size counted as holding nothing, so it
                # could never become a discrepancy. An explicit zero is still
                # a measurement and is still skipped as flat.
                net, prob = ACC.number((p or {}).get("netPosition")
                                       if isinstance(p, dict) else None)
                if prob == ACC.MISSING:
                    return {"check": "positions", "verdict": UNREADABLE,
                            "slug": slug, "raw": p,
                            "why": ("a position that does not state its "
                                    "netPosition is not an empty position")}
                if prob is not None:
                    return {"check": "positions", "verdict": UNREADABLE,
                            "slug": slug, "raw": p,
                            "why": ("a position whose netPosition does not "
                                    "parse is not an empty position")}
                if net:
                    held[str(slug)] = net
            # THE PAGE'S OWN END, READ BY TYPE (`bettor_funded_account.
            # page_end`). `resp.get("eof") or not cursor` read the string
            # "false" as the end and a page that stated neither eof nor a
            # cursor as a finished walk.
            end = ACC.page_end(resp, previous_cursor=cursor)
            if not end["ok"]:
                return {"check": "positions", "verdict": UNREADABLE,
                        "pages": pages, "partial": dict(held),
                        "refusal": end.get("refusal"),
                        "why": ("the position listing did not reach eof: "
                                "its termination was %s, so the set is "
                                "incomplete" % end.get("refusal"))}
            if end["done"]:
                eof = True
                break
            cursor = end["cursor"]
    except Exception as exc:                               # noqa: BLE001
        return {"check": "positions", "verdict": UNREADABLE, "pages": pages,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "why": ("the position listing raised, so what this account "
                        "holds is unknown. Unknown is not empty")}
    if not eof:
        # PAGING THAT DID NOT FINISH IS NOT AN EMPTY ACCOUNT.
        return {"check": "positions", "verdict": UNREADABLE, "pages": pages,
                "partial": dict(held),
                "why": ("the position listing did not reach eof within the "
                        "page bound, so the set is incomplete")}
    return {"check": "positions", "verdict": RECONCILED, "pages": pages,
            "open_positions": held, "count": len(held)}


def read_open_orders(mod) -> dict:
    """EVERY RESTING ORDER THE VENUE HOLDS."""
    try:
        rows = mod.open_orders()
    except Exception as exc:                               # noqa: BLE001
        return {"check": "open_orders", "verdict": UNREADABLE,
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                "why": ("a resting order we cannot see is exposure we cannot "
                        "manage")}
    rows = list(rows or [])
    return {"check": "open_orders", "verdict": RECONCILED,
            "open_orders": rows, "count": len(rows)}


def read_executions(mod, *, since_ts: float, slugs) -> dict:
    """THE TRADES BEHIND THE POSITIONS, over a STATED window.

    Scoped to the markets the account actually holds, because the adapter's
    activities read is per-slug; a market with no position has no residual to
    reconcile and is named as out of scope rather than silently omitted.
    """
    # `pmus.recent_trades(slug, since_ts)` IS this read, and it already
    # raises when the venue cannot be read OR when its pages ran out before
    # reaching `since_ts` -- "unreadable" and "truncated" are not "no fills".
    # That is exactly the contract this check needs, so it is used rather than
    # a new one being written beside it.
    fn = (getattr(mod, "recent_trades", None)
          or getattr(mod, "market_trades", None)
          or getattr(mod, "account_trades", None))
    if not callable(fn):
        return {"check": "executions", "verdict": NOT_SUPPORTED,
                "missing": "a per-market trade read on %s" % mod.__name__,
                "refusal": "ADAPTER_CANNOT_READ_EXECUTIONS",
                "engineering_gap": True,
                "why": ("the executions behind a held position are what make "
                        "its cost basis a measurement rather than a guess")}
    per, errors = {}, {}
    for slug in list(slugs or []):
        try:
            per[slug] = list(fn(slug, since_ts=since_ts) or [])
        except TypeError:
            try:
                per[slug] = list(fn(slug) or [])
            except Exception as exc:                       # noqa: BLE001
                errors[slug] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        except Exception as exc:                           # noqa: BLE001
            errors[slug] = "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if errors:
        return {"check": "executions", "verdict": UNREADABLE,
                "errors": errors, "read": {k: len(v) for k, v in per.items()},
                "why": "an unread market's executions are not zero executions"}
    return {"check": "executions", "verdict": RECONCILED,
            "window_s": EXECUTION_WINDOW_S,
            "since_ts": since_ts,
            "per_market": {k: len(v) for k, v in per.items()},
            "count": sum(len(v) for v in per.values()),
            "scope": ("the markets the venue reports a position in; a market "
                      "with no position carries no residual to reconcile")}


# ── THE WHOLE RECONCILIATION ────────────────────────────────────────

async def reconcile(conn, *, account_id: str, venue: str, adapter=None,
                    now: float | None = None) -> dict:
    """ALL FOUR READS, AND ONE VERDICT THAT DOES NOT ROUND UP.

    `conn` is used only to read what THIS system believes it holds, so a venue
    position we do not know about can be named as a discrepancy rather than
    reported as fine.
    """
    at = float(now if now is not None else time.time())
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "venue": venue, "venue_class": FA.venue_class(venue),
           "a_new_registry_id_is_not_evidence": A_NEW_ID_IS_NOT_EVIDENCE,
           "checks": []}
    try:
        mod = _adapter(adapter)
    except Exception as exc:                               # noqa: BLE001
        return dict(out, ok=False, eligible=False, refusal=R_NO_ADAPTER,
                    error=str(exc)[:200])
    out["adapter"] = mod.__name__

    bal = read_balances(mod)
    out["checks"].append(bal)
    pos = read_positions(mod)
    out["checks"].append(pos)
    orders = read_open_orders(mod)
    out["checks"].append(orders)
    slugs = sorted((pos.get("open_positions") or {}).keys())
    ex = read_executions(mod, since_ts=at - EXECUTION_WINDOW_S, slugs=slugs)
    out["checks"].append(ex)

    # WHAT WE BELIEVE WE HOLD, so a venue position nobody booked is a finding
    ours, ours_err = await _our_open_slugs(conn, account_id=account_id,
                                           venue=venue)
    out["our_open_markets"] = (None if ours is None else sorted(ours))
    out["our_book_read_error"] = ours_err
    # MARKETS WHERE A SEND OF OURS HAS NO ANSWER. Not "ours" -- whether the
    # venue holds anything there is exactly what is unknown -- and not
    # "unbooked" either: the book knows the request and an investigation is
    # open on it. A venue position on such a market is named with the
    # investigation to resolve, and it still blocks.
    unresolved, unresolved_err = await _our_unresolved_slugs(
        conn, account_id=account_id, venue=venue)
    out["markets_with_unresolved_sends"] = sorted(unresolved or ())
    if ours is None:
        # OUR OWN BOOK IS UNREADABLE. Comparing the venue against an empty set
        # would report every venue position as unbooked, which is noise, not a
        # finding.
        out["checks"].append({
            "check": "positions", "verdict": UNREADABLE,
            "error": ours_err,
            "why": ("this system's own position book could not be read, so a "
                    "venue position cannot be told from an unbooked one")})
        ours = set()
        unbooked = []
    else:
        unbooked = sorted(set(slugs) - ours - set(unresolved or ()))
    if unresolved_err:
        out["checks"].append({
            "check": "positions", "verdict": UNREADABLE,
            "error": unresolved_err,
            "why": ("the funded book's unresolved sends could not be read, so "
                    "a venue position cannot be told from an unanswered one")})
    held_under_unresolved = sorted(set(slugs) & set(unresolved or ()))
    if held_under_unresolved:
        out["checks"].append({
            "check": "positions", "verdict": DISCREPANCY,
            "venue_holds_positions_where_a_send_is_unresolved":
                held_under_unresolved,
            "why": ("a send on each of these markets has no answer and the "
                    "venue holds a position there. It may be that send's. "
                    "Resolve the investigation (GET /api/admin/"
                    "funded-investigations) before this account is clean")})
    if unbooked:
        out["checks"].append({
            "check": "positions", "verdict": DISCREPANCY,
            "venue_holds_positions_this_book_does_not_know_about": unbooked,
            "why": ("each of these is live exposure with no row here. "
                    "Marking the account clean would adopt it silently")})

    # FUNDS THE VENUE HAS RESERVED THAT OUR BOOK CANNOT EXPLAIN.
    #
    # `openOrders` on a balance row is cash the venue is holding against
    # resting orders. If it is non-zero while `orders.list` showed us none,
    # the two venue reads disagree with each other -- and an account whose own
    # venue cannot be read consistently is not one to mark clean.
    reserved = 0.0
    for cur in (bal.get("currencies") or []):
        try:
            reserved += float(cur.get("reserved_by_open_orders") or 0.0)
        except (TypeError, ValueError):
            reserved = float("nan")
            break
    seen_orders = len(orders.get("open_orders") or [])
    if reserved != reserved or (reserved > 0 and seen_orders == 0):
        out["checks"].append({
            "check": "balances", "verdict": DISCREPANCY,
            "reserved_by_open_orders": reserved,
            "open_orders_the_venue_listed": seen_orders,
            "why": ("the venue is holding cash against resting orders while "
                    "its own open-order list is empty. Its two answers do "
                    "not agree, so neither is a basis for marking the "
                    "account clean")})

    verdicts = {c["check"]: c["verdict"] for c in out["checks"]}
    blocking = [c for c in out["checks"] if c["verdict"] not in PASSING]
    out["verdicts"] = verdicts
    out["blocking"] = [{"check": c["check"], "verdict": c["verdict"],
                        "refusal": c.get("refusal"),
                        "why": c.get("why")} for c in blocking]
    out["engineering_gaps"] = [c.get("missing") for c in out["checks"]
                              if c.get("engineering_gap")]
    eligible = not blocking and set(verdicts) >= set(CHECKS)
    return dict(out, ok=True, eligible=eligible,
                refusal=None if eligible else R_NOT_RECONCILED,
                why=("every one of the four reconciliations answered "
                     "RECONCILED" if eligible else
                     "%d of %d reconciliations did not pass"
                     % (len(blocking), len(CHECKS))))


async def _our_open_slugs(conn, *, account_id=None, venue=None):
    """The markets THIS system believes it holds, across every lane.

    THE DEFECT THIS CLOSES, and the Postgres log is what found it. The query
    read `rn1x_positions.us_market_slug`, a column that does not exist -- the
    table's venue identity is `venue_market_slug` (migration 119) and there is
    no `qty`; the seeded size is `seed_qty`. Every call therefore raised, and a
    bare `except: return set()` turned that into "this book holds nothing" --
    so the cross-check that is supposed to find a venue position we never
    booked would have flagged EVERY venue position as unbooked, and an operator
    reading a wall of false discrepancies learns nothing from any of them.

    IT NO LONGER SWALLOWS. The return is (slugs, error): an unreadable book is
    reported as unreadable and BLOCKS, because "we could not read our own
    positions" is not "we hold none" -- the same rule this module applies to
    the venue's reads.
    """
    try:
        rows = await conn.fetch(
            "SELECT DISTINCT venue_market_slug AS slug "
            "  FROM rn1x_positions "
            " WHERE venue_market_slug IS NOT NULL "
            "   AND coalesce(seed_qty, 0) <> 0")
    except Exception as exc:                               # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])
    slugs = {str(r["slug"]) for r in rows if r["slug"]}
    # ── AND THE FUNDED LANE'S OWN OPEN POSITIONS ────────────────────
    #
    # THE DEFECT (production-prerequisite investigation, 2026-09-29). "Our
    # book" was rn1x_positions alone, so the first position the funded lane
    # filled came back from the venue as UNBOOKED -- a blocking discrepancy --
    # the next reconciliation failed, readiness check 2 could not be met,
    # the 24-hour authorization could not be renewed, and entry capability
    # ended within a day of the first fill. An open funded ENTRY on this
    # account and venue is a position this system booked.
    if account_id is not None:
        try:
            if await conn.fetchval(
                    "SELECT to_regclass('bettor_funded_intents') IS NOT NULL"):
                funded = await conn.fetch(
                    "SELECT DISTINCT us_market_slug AS slug "
                    "  FROM bettor_funded_intents "
                    " WHERE account_id=$1 AND ($2::text IS NULL OR venue=$2) "
                    "   AND kind='ENTRY' "
                    "   AND bettor_funded_position_is_open(state, "
                    "                                      residual_qty, "
                    "                                      closed_at) "
                    "   AND coalesce(residual_qty, 0) > 0",
                    str(account_id), venue)
                slugs |= {str(r["slug"]) for r in funded if r["slug"]}
        except Exception as exc:                           # noqa: BLE001
            return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return slugs, None


async def _our_unresolved_slugs(conn, *, account_id, venue):
    """Markets where a funded send of this account has no answer yet."""
    try:
        if not await conn.fetchval(
                "SELECT to_regclass('bettor_funded_intents') IS NOT NULL"):
            return set(), None
        rows = await conn.fetch(
            "SELECT DISTINCT us_market_slug AS slug FROM bettor_funded_intents"
            " WHERE account_id=$1 AND ($2::text IS NULL OR venue=$2) "
            "   AND state IN ('UNRESOLVED', 'SEND_ATTEMPTED')",
            str(account_id), venue)
    except Exception as exc:                               # noqa: BLE001
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:200])
    return {str(r["slug"]) for r in rows if r["slug"]}, None


# ── REGISTERING AND MARKING, EACH REFUSING TO FLATTER THE OTHER ──────

async def register(conn, *, account_id: str, venue: str, desk_id: str,
                   note: str, by: str, now: float | None = None) -> dict:
    """PUT A NEW ACCOUNT IN THE REGISTRY, as something UNVERIFIED.

    It is created `PENDING_VERIFICATION` / `UNVERIFIED` and PAUSED. That is
    not caution for its own sake: at the moment of the INSERT those are the
    true values, and `account_selection` will refuse the row on all three
    until a reconciliation changes them.
    """
    at = float(now if now is not None else time.time())
    ident = str(account_id or "").strip()
    if not ident:
        return {"version": VERSION, "ok": False,
                "refusal": "NO_ACCOUNT_ID_SUPPLIED"}
    await conn.execute(
        "INSERT INTO bettor_desk_accounts (account_id, desk_id, status, "
        "  opening_balance, opened_at, note, provenance, paused, "
        "  pause_reason, paused_at, accounting_status, accounting_detail) "
        "VALUES ($1,$2,$3,0,to_timestamp($4),$5,$6::jsonb,TRUE,$7,"
        "        to_timestamp($4),$8,$9::jsonb) "
        "ON CONFLICT (account_id) DO NOTHING",
        ident, str(desk_id), NEW_STATUS, at, str(note),
        json.dumps({"registered_by": by, "venue": venue,
                    "at": at, "version": VERSION}),
        "awaiting reconciliation against the venue", NEW_ACCOUNTING,
        json.dumps({"why": A_NEW_ID_IS_NOT_EVIDENCE,
                    "cleared_by": "bettor_account_onboarding.mark_eligible"}))
    row = await conn.fetchrow(
        "SELECT account_id, status, paused, accounting_status "
        "  FROM bettor_desk_accounts WHERE account_id = $1", ident)
    return {"version": VERSION, "ok": True, "account": dict(row or {}),
            "created_as": {"status": NEW_STATUS,
                           "accounting_status": NEW_ACCOUNTING,
                           "paused": True},
            "is_not_eligible_yet": A_NEW_ID_IS_NOT_EVIDENCE,
            "next": ("bettor_account_onboarding.reconcile, then "
                     "mark_eligible if and only if it passes")}


#: ── PERSISTED RECONCILIATION EVIDENCE AND ITS AGE (finding A3) ──────
#:
#: THE GAP THE AUDIT FOUND. Every read above existed and worked, and NOTHING
#: CALLED IT. `GET /api/admin/funded-activation-readiness` was described in the
#: activation package as rerunning venue reconciliation; it calls
#: `funded_activation.readiness`, whose account check reads the canonical
#: registry, and the prerequisites route returned this module's `describe()`.
#: A described capability is not a performed check, and a registry flag saying
#: RECONCILED is a record of a past conclusion, not evidence of a present one.
#:
#: SO THE EVIDENCE IS PERSISTED WITH ITS OWN CLOCK AND IT EXPIRES. A
#: reconciliation is a statement about balances, positions, open orders and
#: executions at ONE INSTANT. Six hours later it is a historical note: an order
#: could have filled, a position could have settled. Readiness therefore requires
#: evidence that is present, passing AND fresh, and reports the age either way.
RECONCILIATION_KEY = "bettor_funded_account_reconciliation"
#: How long a reconciliation remains admissible as CURRENT evidence. Chosen, not
#: derived -- it is a policy allowance and is labelled as one. Short enough that
#: an overnight fill cannot hide inside it.
EVIDENCE_MAX_AGE_S = 2 * 3600.0
R_NO_EVIDENCE = "NO_RECONCILIATION_EVIDENCE_HAS_EVER_BEEN_RECORDED"
R_EVIDENCE_STALE = "THE_RECONCILIATION_EVIDENCE_IS_OLDER_THAN_ITS_BOUND"
R_EVIDENCE_OTHER_ACCOUNT = "THE_RECORDED_EVIDENCE_IS_FOR_A_DIFFERENT_ACCOUNT"


async def _put_state(conn, key, value) -> None:
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        key, json.dumps(value, default=str))


async def _get_state(conn, key):
    raw = await conn.fetchval(
        "SELECT value FROM ingestion_state WHERE key = $1", key)
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:                                          # noqa: BLE001
        return None


def _completeness(rec) -> dict:
    """Whether all four reads answered, and whether each was fully paged.

    A partially paged source is NOT a source that answered: a positions read
    that stopped at the first page can miss the very position that makes the
    account a discrepancy.
    """
    checks = list((rec or {}).get("checks") or [])
    by = {}
    for c in checks:
        by.setdefault(c.get("check"), []).append(c)
    missing = [c for c in CHECKS if c not in by]
    partial = [c.get("check") for c in checks
               if c.get("complete") is False or c.get("truncated") is True
               or c.get("pages_exhausted") is False]
    # A READ THAT RETURNED UNREADABLE OR NOT_SUPPORTED DID NOT ANSWER. The
    # flags above are set by no read `reconcile` makes, so without this a
    # failed read was reported as a complete one (account map §1).
    unanswered = [c.get("check") for c in checks
                  if c.get("verdict") in (UNREADABLE, NOT_SUPPORTED)]
    ok = not missing and not partial and not unanswered
    return {"checks_expected": list(CHECKS),
            "checks_answered": sorted(by),
            "checks_missing": missing,
            "reads_not_fully_paged": sorted(set(partial)),
            "reads_that_did_not_answer": sorted(set(unanswered)),
            "complete": ok,
            "why": ("every one of the four reads answered and each exhausted "
                    "its pages" if ok else
                    "an unanswered or partially paged read is not evidence")}


async def record_reconciliation(conn, *, account_id: str, venue: str,
                                by: str, adapter=None,
                                now: float | None = None) -> dict:
    """RUN THE FOUR READS AND PERSIST THE EVIDENCE. Closes A3's integration gap.

    This is the documented operator path. It performs the venue reads, records
    source, account identity, retrieval instant, completeness, verdicts and
    discrepancies, and writes NOTHING to the account row -- eligibility is a
    separate act (`mark_eligible`), so reading cannot promote.

    IT IS NOT A READINESS ENDPOINT AND IT DOES NOT PRETEND TO BE ONE. It is the
    thing a readiness endpoint may then READ, with an age attached.
    """
    at = float(now if now is not None else time.time())
    rec = await reconcile(conn, account_id=account_id, venue=venue,
                          adapter=adapter, now=at)
    completeness = _completeness(rec)
    record = {
        "version": VERSION,
        "recorded_at": at,
        "recorded_by": by,
        "account_id": str(account_id or "").strip(),
        "venue": str(venue or "").strip(),
        "source": rec.get("adapter"),
        "ok": bool(rec.get("ok")),
        "eligible": bool(rec.get("eligible")),
        "refusal": rec.get("refusal"),
        "verdicts": rec.get("verdicts"),
        "blocking": rec.get("blocking"),
        "discrepancies": [b for b in (rec.get("blocking") or [])
                          if b.get("verdict") == DISCREPANCY],
        "completeness": completeness,
        "evidence_max_age_s": EVIDENCE_MAX_AGE_S,
        "evidence_max_age_is_a_policy_allowance": (
            "a chosen bound, not a derived one. A reconciliation describes one "
            "instant; past this age it is a historical note"),
        "wrote_account_row": False,
        "checks": rec.get("checks"),
        "our_open_markets": rec.get("our_open_markets"),
    }
    await _put_state(conn, RECONCILIATION_KEY, record)
    return record


async def reconciliation_evidence(conn, *, account_id: str | None = None,
                                  now: float | None = None) -> dict:
    """The persisted evidence, ITS AGE, and whether it may still be relied on.

    Three separate failures, three names: never recorded, recorded for another
    account, or recorded too long ago. An absent reconciliation is not a passing
    one, and neither is an old one.
    """
    at = float(now if now is not None else time.time())
    rec = await _get_state(conn, RECONCILIATION_KEY)
    out = {"key": RECONCILIATION_KEY, "asked_at": at,
           "max_age_s": EVIDENCE_MAX_AGE_S}
    if not isinstance(rec, dict):
        return dict(out, present=False, usable=False, refusal=R_NO_EVIDENCE,
                    why=("no reconciliation has ever been recorded. The "
                         "registry's accounting flag is a record of a past "
                         "conclusion, not evidence of a present one"))
    age = at - float(rec.get("recorded_at") or 0.0)
    out.update(present=True, recorded_at=rec.get("recorded_at"),
               age_s=round(age, 1), account_id=rec.get("account_id"),
               venue=rec.get("venue"), source=rec.get("source"),
               eligible=rec.get("eligible"), verdicts=rec.get("verdicts"),
               discrepancies=rec.get("discrepancies"),
               completeness=rec.get("completeness"),
               recorded_by=rec.get("recorded_by"))
    if account_id and str(rec.get("account_id")) != str(account_id).strip():
        return dict(out, usable=False, refusal=R_EVIDENCE_OTHER_ACCOUNT,
                    why=("the recorded evidence is for %s, and the bound "
                         "account is %s. A new account id does not inherit "
                         "another account's reconciliation"
                         % (rec.get("account_id"), account_id)))
    if age > EVIDENCE_MAX_AGE_S:
        return dict(out, usable=False, refusal=R_EVIDENCE_STALE,
                    why=("recorded %.0f s ago against a %.0f s bound. A "
                         "reconciliation describes one instant and this one is "
                         "no longer that instant" % (age, EVIDENCE_MAX_AGE_S)))
    if not (rec.get("completeness") or {}).get("complete"):
        return dict(out, usable=False, refusal=R_NOT_RECONCILED,
                    why=("the recorded reads were incomplete: %s"
                         % ((rec.get("completeness") or {}).get("why"))))
    if not rec.get("eligible"):
        return dict(out, usable=True, passes=False, refusal=R_NOT_RECONCILED,
                    why=("the reconciliation is current and it did NOT pass. "
                         "Unknown or contradictory evidence blocks"))
    return dict(out, usable=True, passes=True, refusal=None,
                why="a complete, passing reconciliation recorded %.0f s ago"
                    % age)


async def mark_eligible(conn, *, account_id: str, venue: str, by: str,
                        adapter=None, now: float | None = None) -> dict:
    """MAKE AN ACCOUNT ELIGIBLE, AND ONLY ON A CLEAN RECONCILIATION.

    The reconciliation is run HERE rather than passed in, so a caller cannot
    present a stale or hand-written verdict. On success the row is written
    `RECONCILED`, unpaused, and stamped with the evidence; on anything else
    NOTHING is written.
    """
    at = float(now if now is not None else time.time())
    rec = await reconcile(conn, account_id=account_id, venue=venue,
                          adapter=adapter, now=at)
    out = {"version": VERSION, "at": at, "account_id": account_id,
           "reconciliation": rec, "wrote": False}
    row = await conn.fetchrow(
        "SELECT account_id FROM bettor_desk_accounts WHERE account_id = $1",
        str(account_id or "").strip())
    if row is None:
        return dict(out, ok=False, refusal=R_NO_ACCOUNT_ROW,
                    why="register it first; this does not create rows")
    if not rec.get("eligible"):
        return dict(out, ok=False, refusal=R_NOT_RECONCILED,
                    blocking=rec.get("blocking"),
                    why=("the venue reconciliation did not pass, so nothing "
                         "was written. An account is not made clean by being "
                         "marked clean"))
    await conn.execute(
        "UPDATE bettor_desk_accounts SET status='ACTIVE', paused=FALSE, "
        "  pause_reason=NULL, accounting_status='RECONCILED', "
        "  last_verified_at=to_timestamp($2), last_verified_detail=$3::jsonb, "
        "  accounting_detail=$3::jsonb WHERE account_id=$1",
        str(account_id).strip(), at,
        json.dumps({"by": by, "at": at, "version": VERSION, "venue": venue,
                    "verdicts": rec.get("verdicts"),
                    "evidence": rec.get("checks")}, default=str))
    return dict(out, ok=True, wrote=True, refusal=None,
                marked={"status": "ACTIVE", "paused": False,
                        "accounting_status": "RECONCILED"})


async def resolve_existing(conn, *, account_id: str, venue: str, by: str,
                           adapter=None, now: float | None = None) -> dict:
    """THE PAUSED ACCOUNT'S ONLY ROUTE OUT, which is the same route in.

    `acct_fc2d773a2afa4851` is paused with unresolved accounting. This runs
    the identical four reconciliations against it and unpauses it ONLY if they
    pass. There is no flag, endpoint or argument here that unpauses a row
    because someone decided to; a decision to unpause is not evidence about
    what the account holds.
    """
    got = await mark_eligible(conn, account_id=account_id, venue=venue, by=by,
                              adapter=adapter, now=now)
    if not got.get("ok"):
        got["still_paused"] = True
        got["why_it_stays_paused"] = (
            "its accounting is unresolved and the venue reconciliation did "
            "not resolve it. The pause is on the row and this did not move it")
    return got


# ═════════════════════════════════════════════════════════════════════
# THE DISCREPANCY REPORT: BOTH DIRECTIONS, STRICT READS, AND A HISTORY
# ═════════════════════════════════════════════════════════════════════
#
# WHAT `reconcile` ABOVE DOES NOT DO (account map, 2026-09-29):
#   * it never checks the reverse direction -- a position, order or fill our
#     book holds that the venue does not show;
#   * it compares no quantities, and it compares executions with nothing;
#   * its position read took an absent `netPosition` as flat;
#   * `_completeness` looks for flags no read sets, so it always said
#     "complete";
#   * a legacy live-beta holding looks exactly like an unbooked one;
#   * it has one global record, overwritten each run.
#
# `discrepancy_report` is the owner's evidence: every finding with what it
# rests on, in both directions, from the STRICT readers in
# `bettor_funded_account`, with each read's own termination deciding whether
# the report is AUTHORITATIVE. `record_discrepancy_report` keeps it in an
# append-only history and still writes the latest-evidence key.
#
# IT DECIDES NOTHING. It never unpauses an account, never changes
# `accounting_status`, never calls `mark_eligible` or `resolve_existing`, and
# never places, modifies or cancels anything at the venue: its only venue
# calls are `account.balances`, `orders.list`, `portfolio.positions` and
# `portfolio.activities`, and a structural test holds that.

REPORT_VERSION = "BETTOR_ACCOUNT_DISCREPANCY_REPORT_V1"
REPORTS_TABLE = "bettor_account_reconciliation_reports"
#: The paused SHADOW desk account (migration 099, `bettor_desk_accounts`).
PAUSED_DESK_ACCOUNT = "acct_fc2d773a2afa4851"
#: When the identifier collision began, as migration 099 records it.
DESK_WINDOW_FROM = "2026-09-23T16:06:51Z"
#: Quantities are whole contracts on this venue; this only absorbs float
#: noise, never a real difference.
QTY_TOLERANCE = 1e-6
#: A booked fill this close to the window's start may have executed just
#: before it, so it is not called missing from a walk that stopped there.
WINDOW_EDGE_S = 300.0

R_REPORTS_UNAVAILABLE = "THE_RECONCILIATION_REPORT_HISTORY_IS_NOT_IN_THIS_DATABASE"
R_NO_ACCOUNT_NAMED = "A_DISCREPANCY_REPORT_NAMES_ITS_ACCOUNT_AND_VENUE"

REPORT_CHANGES_NOTHING = (
    "This report is evidence for an owner's decision, not the decision. It "
    "does not unpause any account, change any accounting status, or place, "
    "modify or cancel anything at the venue. `would_be_eligible` is for "
    "information and changes nothing.")

# ── the findings, each a name the report and its tests share ─────────
F_READ_NOT_ESTABLISHED = "A_READ_DID_NOT_ESTABLISH_ITS_ANSWER"
F_OUR_BOOK_UNREADABLE = "THIS_SYSTEMS_OWN_BOOK_COULD_NOT_BE_READ"
F_BALANCE_NOT_ESTABLISHED = "THE_ACCOUNTS_CASH_IS_NOT_ESTABLISHED"
F_RESERVED_WITHOUT_ORDERS = "CASH_IS_RESERVED_WHILE_NO_ORDER_IS_LISTED"
F_ORDERS_WITHOUT_RESERVATION = "BUY_ORDERS_WORK_WHILE_NOTHING_IS_RESERVED"
F_RESERVED_NOT_STATED = "ORDERS_ARE_LISTED_AND_THE_RESERVED_AMOUNT_IS_NOT_STATED"
F_VENUE_POSITION_UNBOOKED = "VENUE_HOLDS_A_POSITION_THIS_BOOK_DOES_NOT_KNOW"
F_POSITION_UNDER_UNRESOLVED_SEND = "VENUE_HOLDS_A_POSITION_WHERE_A_SEND_IS_UNRESOLVED"
F_POSITION_QTY_DIFFERS = "VENUE_POSITION_QUANTITY_DIFFERS_FROM_THE_BOOK"
F_BOOK_POSITION_NOT_AT_VENUE = "THIS_BOOK_HOLDS_A_POSITION_THE_VENUE_DOES_NOT_SHOW"
F_LEGACY_POSITION = "LEGACY_LIVE_BETA_HOLDING_AT_THE_VENUE"
F_LEGACY_BOOK_NOT_AT_VENUE = "LEGACY_BOOK_SHOWS_A_HOLDING_THE_VENUE_DOES_NOT"
F_OTHER_LANE_POSITION = "VENUE_POSITION_ON_A_MARKET_ONLY_A_SHADOW_LANE_BOOKED"
F_VENUE_ORDER_UNKNOWN = "VENUE_LISTS_AN_ORDER_THIS_BOOK_DOES_NOT_KNOW"
F_LEGACY_ORDER = "VENUE_LISTS_A_LEGACY_LIVE_BETA_ORDER"
F_ORDER_STATE_DIFFERS = "VENUE_LISTS_AS_WORKING_AN_ORDER_THIS_BOOK_FINISHED"
F_BOOK_ORDER_NOT_AT_VENUE = "THIS_BOOK_HAS_A_WORKING_ORDER_THE_VENUE_DOES_NOT_LIST"
F_SEND_UNANSWERED = "A_SEND_OF_THIS_BOOK_HAS_NO_VENUE_ANSWER"
F_EXECUTION_UNBOOKED = "VENUE_EXECUTION_THIS_BOOK_NEVER_BOOKED"
F_EXECUTION_UNATTRIBUTABLE = "VENUE_EXECUTION_NAMES_NO_OWN_ORDER"
F_LEGACY_EXECUTION = "VENUE_EXECUTION_OF_A_LEGACY_LIVE_BETA_ORDER"
F_FILL_NOT_AT_VENUE = "BOOKED_FILL_THE_VENUE_DOES_NOT_SHOW"
F_UNKNOWN_ACTIVITY = "VENUE_ACTIVITY_OF_A_TYPE_THIS_REPORT_DOES_NOT_KNOW"
F_DESK_IDS_NOT_VERIFIED = "THE_DESKS_IDENTIFIER_INTEGRITY_IS_NOT_VERIFIED"
FINDINGS = (F_READ_NOT_ESTABLISHED, F_OUR_BOOK_UNREADABLE,
            F_BALANCE_NOT_ESTABLISHED, F_RESERVED_WITHOUT_ORDERS,
            F_ORDERS_WITHOUT_RESERVATION, F_RESERVED_NOT_STATED,
            F_VENUE_POSITION_UNBOOKED, F_POSITION_UNDER_UNRESOLVED_SEND,
            F_POSITION_QTY_DIFFERS, F_BOOK_POSITION_NOT_AT_VENUE,
            F_LEGACY_POSITION, F_LEGACY_BOOK_NOT_AT_VENUE,
            F_OTHER_LANE_POSITION, F_VENUE_ORDER_UNKNOWN, F_LEGACY_ORDER,
            F_ORDER_STATE_DIFFERS, F_BOOK_ORDER_NOT_AT_VENUE,
            F_SEND_UNANSWERED, F_EXECUTION_UNBOOKED,
            F_EXECUTION_UNATTRIBUTABLE, F_LEGACY_EXECUTION,
            F_FILL_NOT_AT_VENUE, F_UNKNOWN_ACTIVITY, F_DESK_IDS_NOT_VERIFIED)

# ── what no read here can establish, named rather than approximated ───
GAP_TERMINAL_ORDER_HISTORY = "TERMINAL_ORDER_HISTORY_HAS_NO_VENUE_ENDPOINT"
GAP_ACCOUNT_IDENTITY = \
    "THE_AUTHENTICATED_VENUE_ACCOUNT_IS_NOT_TIED_TO_THIS_ACCOUNT_ID"
GAP_CREDENTIAL_CAPABILITY = \
    "THE_VENUE_CREDENTIALS_READ_ONLY_CAPABILITY_IS_NOT_ESTABLISHED"
GAP_DESK_NOT_ADDRESSED = \
    "A_VENUE_RECONCILIATION_DOES_NOT_ADDRESS_THE_DESKS_IDENTIFIER_INTEGRITY"
GAPS = (GAP_TERMINAL_ORDER_HISTORY, GAP_ACCOUNT_IDENTITY,
        GAP_CREDENTIAL_CAPABILITY, GAP_DESK_NOT_ADDRESSED)

_GAP_TEXT = {
    GAP_TERMINAL_ORDER_HISTORY: (
        "the SDK's orders.list returns RESTING orders only and has no cursor; "
        "no endpoint returns filled, cancelled or expired orders. A book "
        "order the venue no longer lists is therefore reported as not "
        "listed -- it may have filled, been cancelled or expired, and this "
        "report does not guess which. The executions walk is the evidence "
        "for fills."),
    GAP_ACCOUNT_IDENTITY: (
        "the reads use whatever account the service's venue key "
        "authenticates; no venue field ties that account to this account "
        "id (api/reconcile_read.account_identity can only answer "
        "no_identity). The report is about THAT account."),
    GAP_CREDENTIAL_CAPABILITY: (
        "the SDK exposes no permission or scope introspection, so whether "
        "the service's key can only read is NOT established, and it is not "
        "assumed. The key is used through pmus._get_client(); this path is "
        "protected by construction instead -- it calls account.balances, "
        "orders.list, portfolio.positions and portfolio.activities and "
        "nothing else, and a structural test fails on any order-mutating "
        "call in it."),
}

#: Legacy live-beta rows that still describe presence on a market.
LEGACY_LIVE_STATUSES = ("submitting", "open", "filled", "exiting")

# ── the desk's two id schemes (bettor_desk.Desk) ─────────────────────
ID_LEGACY_COUNTER = "LEGACY_COUNTER"
ID_EVIDENCE_DERIVED = "EVIDENCE_DERIVED"
ID_BOOT_DERIVED = "EVIDENCE_DERIVED_BOOT_KEY"
ID_SCHEME_UNREADABLE = "THE_ID_SCHEME_CANNOT_BE_TOLD_FROM_THE_ROW"
#: `_legacy_next_id`: "%s-%s-%06d" % (id_prefix, kind, counter)
_LEGACY_REST = re.compile(r"\d{6,}")
#: `_next_id` inside an event: "%s-%s-%s-%02d" % (id_prefix, kind,
#: "<evidence id or at:%.3f>~<6 hex market digest>", n)
_EVIDENCE_REST = re.compile(r".+~[0-9a-f]{6}-\d{2,}")
#: `_next_id` before any event: key "boot:<8 hex>"
_BOOT_REST = re.compile(r"boot:[0-9a-f]{8}-\d{2,}")


def classify_desk_id(ident, *, prefixes, kind: str) -> dict:
    """Which of `bettor_desk`'s two id schemes produced this id, or that it
    cannot be told. Pure.

    THE RULE IS THE FORMAT STRINGS, not a guess: the superseded counter wrote
    `<prefix>-<kind>-<at least six digits>`; the corrected scheme writes
    `<prefix>-<kind>-<evidence key>~<market digest>-<nn>` (or a `boot:` key
    before the first event). The two cannot produce each other's shape. An id
    that does not start with a known prefix and kind, or whose remainder
    matches neither, is `ID_SCHEME_UNREADABLE` by name."""
    s = str(ident or "")
    for p in sorted({str(x) for x in prefixes if x}, key=len, reverse=True):
        head = "%s-%s-" % (p, kind)
        if not s.startswith(head):
            continue
        rest = s[len(head):]
        if _LEGACY_REST.fullmatch(rest):
            return {"scheme": ID_LEGACY_COUNTER, "prefix": p}
        if _EVIDENCE_REST.fullmatch(rest):
            return {"scheme": ID_EVIDENCE_DERIVED, "prefix": p}
        if _BOOT_REST.fullmatch(rest):
            return {"scheme": ID_BOOT_DERIVED, "prefix": p}
        return {"scheme": ID_SCHEME_UNREADABLE, "prefix": p,
                "why": "the remainder after the prefix matches neither form"}
    return {"scheme": ID_SCHEME_UNREADABLE, "prefix": None,
            "why": "the id starts with neither the account nor the desk id"}


def _direct_read(call, *, endpoint, max_dispatches=None):
    """For an adapter without `paced_read`: one call, no retry."""
    return call()


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _finding(name, check, *, blocking, why, **evidence) -> dict:
    return dict({"finding": name, "check": check, "blocking": bool(blocking),
                 "why": why}, **evidence)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


async def _table(conn, name) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL",
                                    name))


async def _our_book_for_report(conn, *, account_id, venue) -> dict:
    """EVERYTHING THIS SYSTEM BELIEVES ABOUT THE ACCOUNT, read once.

    Raises on a read failure: the caller turns that into
    F_OUR_BOOK_UNREADABLE, which blocks and makes the report
    non-authoritative -- "we could not read our own book" is not "we hold
    nothing"."""
    book = {"funded_positions": {}, "funded_intents_by_slug": {},
            "working_orders": {}, "sends_unanswered": [],
            "order_ids": {}, "fills": [], "legacy_positions": {},
            "legacy_order_ids": set(), "shadow_slugs": set(),
            "tables_absent": []}
    if await _table(conn, "bettor_funded_intents"):
        rows = await conn.fetch(
            "SELECT intent_id, kind, parent_intent_id, us_market_slug, "
            "       order_intent, state, venue_order_id, "
            "       residual_qty::float8 AS residual, closed_at, "
            "       bettor_funded_position_is_open(state, residual_qty, "
            "                                      closed_at) AS open "
            "  FROM bettor_funded_intents "
            " WHERE account_id=$1 AND upper(venue)=upper($2)",
            str(account_id), str(venue))
        for r in rows:
            slug = str(r["us_market_slug"] or "").strip().lower()
            if r["venue_order_id"]:
                book["order_ids"][str(r["venue_order_id"])] = {
                    "intent_id": r["intent_id"], "state": r["state"],
                    "kind": r["kind"]}
            if r["state"] in ("ACKNOWLEDGED", "PARTIALLY_FILLED") \
                    and r["venue_order_id"]:
                book["working_orders"][str(r["venue_order_id"])] = {
                    "intent_id": r["intent_id"], "state": r["state"],
                    "us_market_slug": slug, "kind": r["kind"]}
            if r["state"] in ("SEND_ATTEMPTED", "UNRESOLVED"):
                book["sends_unanswered"].append({
                    "intent_id": r["intent_id"], "state": r["state"],
                    "us_market_slug": slug, "kind": r["kind"],
                    "venue_order_id": r["venue_order_id"]})
            if r["kind"] == "ENTRY" and r["closed_at"] is None \
                    and float(r["residual"] or 0) > 0:
                # SIGNED IN THE VENUE'S CONVENTION: a long is positive and a
                # short negative, because that is how netPosition reads.
                sign = -1.0 if r["order_intent"] == "ORDER_INTENT_BUY_SHORT" \
                    else 1.0
                book["funded_positions"][slug] = round(
                    book["funded_positions"].get(slug, 0.0)
                    + sign * float(r["residual"]), 6)
                book["funded_intents_by_slug"].setdefault(slug, []).append(
                    {"intent_id": r["intent_id"],
                     "order_intent": r["order_intent"],
                     "residual_qty": float(r["residual"])})
        if await _table(conn, "bettor_funded_fills"):
            book["fills"] = [dict(f) for f in await conn.fetch(
                "SELECT f.fill_id, f.intent_id, f.venue_order_id, "
                "       f.venue_fill_id, f.qty::float8 AS qty, f.direction, "
                "       extract(epoch FROM coalesce(f.venue_executed_at, "
                "                                   f.at))::float8 AS ts "
                "  FROM bettor_funded_fills f "
                "  JOIN bettor_funded_intents i ON i.intent_id = f.intent_id "
                " WHERE i.account_id=$1 AND upper(i.venue)=upper($2)",
                str(account_id), str(venue))]
    else:
        book["tables_absent"].append("bettor_funded_intents")
    # THE LEGACY LIVE-BETA LANES. They record no account id: a legacy row is
    # matched by market or order id, on the account the service's key reads.
    if await _table(conn, "live_orders"):
        for r in await conn.fetch(
                "SELECT lower(us_market_slug) AS slug, count(*) AS rows, "
                "       coalesce(sum(filled_shares), 0)::float8 AS filled, "
                "       array_agg(DISTINCT status) AS statuses "
                "  FROM live_orders WHERE us_market_slug IS NOT NULL "
                "   AND status = ANY($1::text[]) GROUP BY 1",
                list(LEGACY_LIVE_STATUSES)):
            book["legacy_positions"].setdefault(r["slug"], {})[
                "live_orders"] = {"rows": int(r["rows"]),
                                  "filled_shares": float(r["filled"]),
                                  "statuses": sorted(r["statuses"] or [])}
        book["legacy_order_ids"] |= {str(r["order_id"]) for r in
                                     await conn.fetch(
            "SELECT DISTINCT order_id FROM live_orders "
            " WHERE order_id IS NOT NULL")}
    else:
        book["tables_absent"].append("live_orders")
    if await _table(conn, "mirror_books"):
        for r in await conn.fetch(
                "SELECT lower(us_market_slug) AS slug, count(*) AS books, "
                "       sum(ledger_net)::float8 AS ledger_net, "
                "       sum(venue_net)::float8 AS venue_net "
                "  FROM mirror_books WHERE state <> 'closed' GROUP BY 1"):
            book["legacy_positions"].setdefault(r["slug"], {})[
                "mirror_books"] = {"open_books": int(r["books"]),
                                   "ledger_net": r["ledger_net"],
                                   "venue_net_last_seen": r["venue_net"]}
    else:
        book["tables_absent"].append("mirror_books")
    if await _table(conn, "mirror_orders"):
        book["legacy_order_ids"] |= {str(r["order_id"]) for r in
                                     await conn.fetch(
            "SELECT DISTINCT order_id FROM mirror_orders "
            " WHERE order_id IS NOT NULL")}
    if await _table(conn, "rn1x_positions"):
        book["shadow_slugs"] = {str(r["slug"]).lower() for r in
                                await conn.fetch(
            "SELECT DISTINCT venue_market_slug AS slug FROM rn1x_positions "
            " WHERE venue_market_slug IS NOT NULL "
            "   AND coalesce(seed_qty, 0) <> 0") if r["slug"]}
    return book


def _compare_positions(venue_pos: dict, book: dict) -> list:
    out = []
    funded = book["funded_positions"]
    unresolved = {s["us_market_slug"] for s in book["sends_unanswered"]}
    legacy = book["legacy_positions"]
    shadow = book["shadow_slugs"]
    for slug in sorted(venue_pos):
        v = venue_pos[slug]
        net = float(v["net_position"])
        ev = {"us_market_slug": slug, "venue_net_position": net,
              "venue_cost_usd": v.get("cost_usd")}
        if slug in unresolved:
            out.append(_finding(
                F_POSITION_UNDER_UNRESOLVED_SEND, "positions", blocking=True,
                why=("a send on this market has no answer and the venue holds "
                     "a position here. It may be that send's; resolve the "
                     "investigation (GET /api/admin/funded-investigations) "
                     "first"), **ev,
                sends=[s for s in book["sends_unanswered"]
                       if s["us_market_slug"] == slug]))
        if slug in funded:
            want = funded[slug]
            others = [n for n, hit in (("legacy", slug in legacy),
                                       ("shadow", slug in shadow)) if hit]
            if abs(net - want) > QTY_TOLERANCE or others:
                out.append(_finding(
                    F_POSITION_QTY_DIFFERS, "positions", blocking=True,
                    why=("the venue's signed net on this market is %s and the "
                         "funded book's open residual is %s%s"
                         % (net, want,
                            "; another book also names this market, so the "
                            "venue's single net cannot be attributed"
                            if others else "")),
                    book_net_position=want, **ev,
                    also_named_by=others,
                    intents=book["funded_intents_by_slug"].get(slug)))
            continue
        if slug in unresolved:
            continue
        if slug in legacy:
            out.append(_finding(
                F_LEGACY_POSITION, "positions", blocking=True,
                why=("held at the venue on a market the LEGACY live-beta "
                     "lanes (live_orders / mirror_books) still show open. "
                     "Classified as legacy, not as unexplained -- and still "
                     "blocking, because it is exposure on this account that "
                     "the funded book does not carry; the owner decides what "
                     "becomes of it"), **ev, legacy=legacy[slug]))
            continue
        if slug in shadow:
            out.append(_finding(
                F_OTHER_LANE_POSITION, "positions", blocking=True,
                why=("only a SHADOW lane (rn1x_positions) names this market. "
                     "A shadow position is not venue exposure, so a real "
                     "venue position here is not explained by it"), **ev))
            continue
        out.append(_finding(
            F_VENUE_POSITION_UNBOOKED, "positions", blocking=True,
            why=("live exposure with no row in any book here. Marking the "
                 "account clean would adopt it silently"), **ev))
    # ── AND THE REVERSE: what our book holds that the venue does not ──
    for slug in sorted(set(funded) - set(venue_pos)):
        out.append(_finding(
            F_BOOK_POSITION_NOT_AT_VENUE, "positions", blocking=True,
            why=("the funded book carries an open residual here and the "
                 "venue's complete position walk shows none. Either the book "
                 "is wrong or the venue settled or moved it; both are for "
                 "an owner to resolve, never to assume"),
            us_market_slug=slug, book_net_position=funded[slug],
            intents=book["funded_intents_by_slug"].get(slug)))
    for slug in sorted(set(legacy) - set(venue_pos)):
        out.append(_finding(
            F_LEGACY_BOOK_NOT_AT_VENUE, "positions", blocking=False,
            why=("a legacy live-beta row still describes this market as open "
                 "and the venue holds nothing there. Reported so the legacy "
                 "bookkeeping can be corrected; it is not exposure"),
            us_market_slug=slug, legacy=legacy[slug]))
    return out


def _compare_orders(venue_orders: list, book: dict) -> list:
    out = []
    listed = {}
    for o in venue_orders:
        oid = str(o.get("order_id") or "")
        listed[oid] = o
        ev = {"venue_order_id": oid or None,
              "us_market_slug": o.get("us_market_slug"),
              "collateral_usd": o.get("collateral_usd"),
              "basis": o.get("basis")}
        mine = book["order_ids"].get(oid) if oid else None
        if mine is not None:
            if mine["state"] not in ("ACKNOWLEDGED", "PARTIALLY_FILLED",
                                     "SEND_ATTEMPTED", "UNRESOLVED",
                                     "INTENT_RECORDED"):
                out.append(_finding(
                    F_ORDER_STATE_DIFFERS, "open_orders", blocking=True,
                    why=("the venue lists this order as working and the book "
                         "records it as %s" % mine["state"]),
                    intent_id=mine["intent_id"], book_state=mine["state"],
                    **ev))
            continue
        if oid and oid in book["legacy_order_ids"]:
            out.append(_finding(
                F_LEGACY_ORDER, "open_orders", blocking=True,
                why=("a LEGACY live-beta order is still working on this "
                     "account. Classified as legacy; it commits collateral "
                     "the funded book does not count"), **ev))
            continue
        out.append(_finding(
            F_VENUE_ORDER_UNKNOWN, "open_orders", blocking=True,
            why=("a working order at the venue that no book here placed or "
                 "knows. It is exposure nobody is managing"), **ev))
    for oid, w in sorted(book["working_orders"].items()):
        if oid not in listed:
            out.append(_finding(
                F_BOOK_ORDER_NOT_AT_VENUE, "open_orders", blocking=True,
                why=("the book records this order as working and the venue's "
                     "complete open-order list does not include it. It may "
                     "have filled, been cancelled or expired: terminal order "
                     "history has no venue endpoint (%s), so this is not "
                     "guessed" % GAP_TERMINAL_ORDER_HISTORY),
                venue_order_id=oid, **w))
    for s in book["sends_unanswered"]:
        out.append(_finding(
            F_SEND_UNANSWERED, "open_orders", blocking=True,
            why=("a request left this system and no venue answer was "
                 "recorded. Whether the venue holds an order for it is "
                 "unknown; resolve the investigation"), **s))
    return out


def _compare_executions(activity: dict, book: dict, *, since: float) -> list:
    out = []
    trades = [r for r in activity.get("rows") or []
              if r.get("type") == "ACTIVITY_TYPE_TRADE"]
    for r in activity.get("rows") or []:
        if not r.get("known"):
            out.append(_finding(
                F_UNKNOWN_ACTIVITY, "executions", blocking=True,
                why=("the venue returned an activity of a type this report "
                     "does not know. It is reported rather than dropped, "
                     "because an unknown row may be an execution"),
                type=r.get("type"), ts=r.get("ts"), keys=r.get("keys")))
    fills_by_order: dict = {}
    for f in book["fills"]:
        fills_by_order.setdefault(str(f["venue_order_id"]), []).append(f)
    trades_by_order: dict = {}
    for t in trades:
        oid = t.get("own_order_id")
        if not oid:
            out.append(_finding(
                F_EXECUTION_UNATTRIBUTABLE, "executions", blocking=True,
                why=("an execution whose own side cannot be read (no boolean "
                     "isAggressor, or no order on it) cannot be matched to a "
                     "booked fill, nor excluded from one"),
                trade_id=t.get("trade_id"), us_market_slug=t.get(
                    "us_market_slug"), qty=t.get("qty"), ts=t.get("ts")))
            continue
        trades_by_order.setdefault(oid, []).append(t)
    # TWO WINDOWS FOR BOOKED FILLS, around the walk's start. A fill booked
    # just inside `since` may pair with a trade the walk read or with one it
    # stopped before, so: a fill may PAIR with a trade if it is no older than
    # `since - WINDOW_EDGE_S`, and it is called MISSING at the venue only if it
    # is at least `since + WINDOW_EDGE_S`. Only the ten minutes around a start
    # ninety days back are ambiguous, and they are ambiguous in both
    # directions rather than silently in the book's favour.
    pairable = {oid: [f for f in fs if float(f["ts"] or 0)
                      >= since - WINDOW_EDGE_S]
                for oid, fs in fills_by_order.items()}
    in_window = {oid: [f for f in fs if float(f["ts"] or 0)
                       >= since + WINDOW_EDGE_S]
                 for oid, fs in fills_by_order.items()}
    for oid in sorted(set(trades_by_order)
                      | {o for o, fs in in_window.items() if fs}):
        ts_ = trades_by_order.get(oid, [])
        fs_all = fills_by_order.get(oid, [])
        known = oid in book["order_ids"] or bool(fs_all)
        if ts_ and not known:
            legacy = oid in book["legacy_order_ids"]
            out.append(_finding(
                F_LEGACY_EXECUTION if legacy else F_EXECUTION_UNBOOKED,
                "executions", blocking=not legacy,
                why=("executions of a LEGACY live-beta order, booked by that "
                     "lane and not by the funded book" if legacy else
                     "the venue executed on an order of this account that no "
                     "book here knows, so the cash and the position it moved "
                     "are unbooked"),
                venue_order_id=oid,
                venue_qty=round(sum(t["qty"] for t in ts_), 6),
                trade_ids=[t.get("trade_id") for t in ts_]))
            continue
        # BY ID FIRST: a booked fill carries the venue's execution id, and a
        # trade row names the trade and (on its own side) the execution.
        ids = {str(f["venue_fill_id"]) for f in fs_all if f["venue_fill_id"]}
        matched = [t for t in ts_ if (t.get("trade_id") in ids
                                      or t.get("own_execution_id") in ids)]
        seen_ids = ({t.get("trade_id") for t in ts_}
                    | {t.get("own_execution_id") for t in ts_}) - {None}
        u_trades = [t for t in ts_ if t not in matched]
        u_pair = [f for f in pairable.get(oid, [])
                  if str(f["venue_fill_id"]) not in seen_ids]
        u_fills = [f for f in in_window.get(oid, [])
                   if str(f["venue_fill_id"]) not in seen_ids]
        q_t = round(sum(t["qty"] for t in u_trades), 6)
        q_pair = round(sum(float(f["qty"]) for f in u_pair), 6)
        q_f = round(sum(float(f["qty"]) for f in u_fills), 6)
        # THEN BY QUANTITY on what the ids did not pair: the venue's fill id
        # and its activity id are not established to be the same key, so
        # equal totals on one order reconcile; unequal ones do not.
        mine = book["order_ids"].get(oid) or {}
        ev = {"venue_order_id": oid, "intent_id": mine.get("intent_id"),
              "venue_unpaired_qty": q_t, "booked_unpaired_qty": q_pair,
              "trade_ids": [t.get("trade_id") for t in u_trades],
              "fill_ids": [f["fill_id"] for f in u_pair]}
        if q_t - q_pair > QTY_TOLERANCE:
            out.append(_finding(
                F_EXECUTION_UNBOOKED, "executions", blocking=True,
                why=("the venue executed %s more on this order than the book "
                     "has booked in the window" % round(q_t - q_pair, 6)),
                **ev))
        elif q_f - q_t > QTY_TOLERANCE:
            out.append(_finding(
                F_FILL_NOT_AT_VENUE, "executions", blocking=True,
                why=("the book carries %s more in fills on this order than "
                     "the venue's complete activity walk shows in the window"
                     % round(q_f - q_t, 6)), **dict(
                         ev, booked_unpaired_qty=q_f,
                         fill_ids=[f["fill_id"] for f in u_fills])))
    return out


def _compare_balances(bal: dict, orders: dict) -> tuple[dict, list]:
    out = []
    if bal.get("verdict") != RECONCILED:
        out.append(_finding(
            F_BALANCE_NOT_ESTABLISHED, "balances", blocking=True,
            why=bal.get("why") or "the balance read did not establish cash",
            verdict=bal.get("verdict"), refusal=bal.get("refusal")))
    rows = list(bal.get("currencies") or [])
    reserved, stated = 0.0, bool(rows)
    for r in rows:
        v, prob = ACC.number(r.get("reserved_by_open_orders"))
        if prob is not None:
            stated = False
            continue
        reserved += v
    listed = int(orders.get("open_orders") or 0) if orders.get("ok") else None
    working = orders.get("working_usd") if orders.get("ok") else None
    summary = {"currencies": [{k: r.get(k) for k in (
        "currency", "current_balance", "buying_power",
        "reserved_by_open_orders", "unsettled_funds", "balance_reservation",
        "pending_withdrawals", "absent_fields")} for r in rows],
        "reserved_by_open_orders_usd": round(reserved, 6) if stated else None,
        "reserved_is_stated": stated,
        "open_orders_listed": listed,
        "working_buy_collateral_usd": working,
        "relationship": ("the venue's reserve may include amounts this code "
                         "does not model (fees, margins); only a "
                         "contradiction -- cash reserved with no order, or "
                         "working buys with nothing reserved -- is a finding")}
    if listed is not None and rows:
        if not stated and listed > 0:
            out.append(_finding(
                F_RESERVED_NOT_STATED, "balances", blocking=True,
                why=("orders are working and the balance row does not state "
                     "what it reserves against them, so the two reads cannot "
                     "be checked against each other"), open_orders=listed))
        elif stated and reserved > QTY_TOLERANCE and listed == 0:
            out.append(_finding(
                F_RESERVED_WITHOUT_ORDERS, "balances", blocking=True,
                why=("the venue holds cash against resting orders while its "
                     "own complete open-order list is empty; its two answers "
                     "do not agree"), reserved_usd=round(reserved, 6)))
        elif stated and reserved <= QTY_TOLERANCE \
                and float(working or 0) > QTY_TOLERANCE:
            out.append(_finding(
                F_ORDERS_WITHOUT_RESERVATION, "balances", blocking=True,
                why=("working buy orders commit %s of collateral and the "
                     "venue states nothing reserved" % working),
                working_usd=working))
    return summary, out


async def _registry_row(conn, account_id):
    if not await _table(conn, "bettor_desk_accounts"):
        return None
    row = await conn.fetchrow(
        "SELECT account_id, desk_id, status, paused, pause_reason, "
        "       paused_at, accounting_status, accounting_detail, "
        "       last_verified_at FROM bettor_desk_accounts "
        " WHERE account_id=$1", str(account_id))
    if row is None:
        return None
    r = dict(row)
    det = r.get("accounting_detail")
    if isinstance(det, str):
        try:
            det = json.loads(det)
        except ValueError:
            det = {"unparsed": det}
    r["accounting_detail"] = det or {}
    return r


def _is_desk_recovery(account_id, row) -> bool:
    return (str(account_id) == PAUSED_DESK_ACCOUNT
            or str((row or {}).get("pause_reason") or "")
            .startswith("ACCOUNTING_RECOVERY"))


async def desk_identifier_integrity(conn, *, account_id, row) -> dict:
    """WHAT THE DESK'S OWN ROWS SAY ABOUT ITS ID SCHEME. Read-only.

    Counts the desk's orders and decisions written since the collision
    window opened, by the scheme each id was generated under. It does not
    and cannot say which historical values were overwritten: an overwritten
    row carries its last writer's id (`accounting_detail.NOT_reconstructable`).
    """
    det = (row or {}).get("accounting_detail") or {}
    window = str(det.get("window_from") or DESK_WINDOW_FROM)
    out = {"account_id": str(account_id),
           "desk_id": (row or {}).get("desk_id"),
           "window_from": window,
           "window_from_source": ("accounting_detail.window_from"
                                  if det.get("window_from") else
                                  "migration 099 (the row states none)"),
           "pause_reason": (row or {}).get("pause_reason"),
           "accounting_status": (row or {}).get("accounting_status"),
           "last_verified_at": (row or {}).get("last_verified_at"),
           "id_schemes": {
               ID_LEGACY_COUNTER: ("bettor_desk.Desk._legacy_next_id: "
                                   "<prefix>-<kind>-<6-digit per-process "
                                   "counter>, which restarted at zero in "
                                   "every process"),
               ID_EVIDENCE_DERIVED: ("bettor_desk.Desk._next_id: <prefix>-"
                                     "<kind>-<evidence id>~<market digest>-"
                                     "<nn>, restart-safe and idempotent"),
               ID_BOOT_DERIVED: ("bettor_desk.Desk._next_id before the first "
                                 "event: <prefix>-<kind>-boot:<8 hex>-<nn>")},
           "basis": ("rows with this account_id whose write or decision time "
                     "is at or after window_from; each id classified by "
                     "classify_desk_id against the account and desk ids")}
    prefixes = [str(account_id), (row or {}).get("desk_id")]
    counted = {}
    for table, id_col, kind, when in (
            ("bettor_desk_orders", "order_id", "O",
             "greatest(created_at, updated_at)"),
            ("bettor_desk_decisions", "desk_decision_id", "D",
             "greatest(decided_at, created_at)")):
        if not await _table(conn, table):
            counted[table] = {"readable": False,
                              "why": "the table is not in this database"}
            continue
        ids = [r["i"] for r in await conn.fetch(
            "SELECT %s AS i FROM %s WHERE account_id=$1 "
            "   AND %s >= $2::timestamptz" % (id_col, table, when),
            str(account_id), _iso_ts(window))]
        by = {ID_LEGACY_COUNTER: 0, ID_EVIDENCE_DERIVED: 0,
              ID_BOOT_DERIVED: 0, ID_SCHEME_UNREADABLE: 0}
        unreadable = []
        for i in ids:
            c = classify_desk_id(i, prefixes=prefixes, kind=kind)
            by[c["scheme"]] += 1
            if c["scheme"] == ID_SCHEME_UNREADABLE and len(unreadable) < 20:
                unreadable.append({"id": i, "why": c.get("why")})
        counted[table] = {"readable": True, "rows": len(ids),
                          "by_scheme": by,
                          "unclassifiable_examples": unreadable}
    out["counts"] = counted
    corrected = sum(c.get("by_scheme", {}).get(ID_EVIDENCE_DERIVED, 0)
                    + c.get("by_scheme", {}).get(ID_BOOT_DERIVED, 0)
                    for c in counted.values())
    legacy = sum(c.get("by_scheme", {}).get(ID_LEGACY_COUNTER, 0)
                 for c in counted.values())
    out["what_the_pause_reason_says_would_verify_the_corrected_build"] = [
        {"criterion": ("rows written by the corrected build on this account "
                       "carry evidence-derived ids"),
         "observed": "%d corrected-scheme id(s) since window_from" % corrected,
         "note": ("while the desk is paused the corrected build writes "
                  "nothing, so there may be none to observe")},
        {"criterion": ("no LEGACY_COUNTER id is written after the corrected "
                       "build was deployed"),
         "observed": ("%d LEGACY_COUNTER id(s) since window_from; the deploy "
                      "instant of the corrected build is not recorded in the "
                      "database, so which of them precede it cannot be told "
                      "here" % legacy)},
        {"criterion": ("last_verified_at is set by a verification of the "
                       "corrected build"),
         "observed": ("last_verified_at is %s. Its only writer is "
                      "bettor_account_onboarding.mark_eligible -- a VENUE "
                      "reconciliation, which never reads bettor_desk_* rows"
                      % ((row or {}).get("last_verified_at") or "null"))},
        {"criterion": "what even a verified build does not do",
         "observed": det.get("what_the_fix_does_not_do") or (
             "the fix prevents future collisions and repairs nothing already "
             "overwritten"),
         "not_reconstructable": det.get("NOT_reconstructable")}]
    out["verified"] = False
    out["why_not_verified"] = (
        "no code verifies the corrected build. This section REPORTS what the "
        "rows show; it does not verify, and a venue reconciliation does not "
        "address it (%s)" % GAP_DESK_NOT_ADDRESSED)
    return out


def _iso_ts(s: str):
    from datetime import datetime, timezone
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def discrepancy_report(conn, *, account_id: str, venue: str,
                             adapter=None, now: float | None = None) -> dict:
    """THE VENUE AND THE BOOK COMPARED IN BOTH DIRECTIONS, STRICTLY.

    READS ONLY. Nothing here writes -- `record_discrepancy_report` persists.
    Returns every finding with its evidence (`discrepancies`), the blocking
    subset, whether every read was complete and strict (`authoritative`),
    `would_be_eligible` for information, and `pause_kept: True`.
    """
    at = float(now if now is not None else time.time())
    since = at - EXECUTION_WINDOW_S
    acct = str(account_id or "").strip()
    ven = str(venue or "").strip()
    out = {"version": REPORT_VERSION, "at": at, "account_id": acct,
           "venue": ven, "venue_class": FA.venue_class(ven),
           "window": {"since_epoch_s": since, "until_epoch_s": at,
                      "window_s": EXECUTION_WINDOW_S},
           "pause_kept": True, "changes_nothing": REPORT_CHANGES_NOTHING,
           "wrote_account_row": False, "submitted_anything": False,
           "gaps": [{"gap": g, "why": _GAP_TEXT[g]} for g in
                    (GAP_TERMINAL_ORDER_HISTORY, GAP_ACCOUNT_IDENTITY,
                     GAP_CREDENTIAL_CAPABILITY)]}
    if not acct or not ven:
        return dict(out, ok=False, refusal=R_NO_ACCOUNT_NAMED,
                    authoritative=False, would_be_eligible=False,
                    discrepancies=[], blocking=[])
    reads: dict = {}
    findings: list = []
    try:
        mod = _adapter(adapter)
        out["adapter"] = getattr(mod, "__name__", type(mod).__name__)
    except Exception as exc:                               # noqa: BLE001
        mod = None
        out["adapter"] = None
        reads["adapter"] = {"ok": False, "complete": False,
                            "refusal": R_NO_ADAPTER,
                            "error": type(exc).__name__}
    client = None
    if mod is not None:
        try:
            client = mod._get_client()
        except Exception as exc:                           # noqa: BLE001
            reads["client"] = {"ok": False, "complete": False,
                               "refusal": ACC.R_NO_CLIENT,
                               "error": type(exc).__name__}
    paced = getattr(mod, "paced_read", None) if mod is not None else None
    if not callable(paced):
        paced = _direct_read

    # ── THE VENUE, READ STRICTLY. Orders before positions, for the reason
    # `read_venue_account` gives: a fill between the two reads is then seen
    # twice (a finding), never in neither (a silent pass). ──────────────
    if mod is not None:
        # A BLOCKING VENUE CALL, run off the event loop like the three reads
        # below: the route serving this report shares its loop with the API.
        bal = await asyncio.to_thread(read_balances, mod)
    else:
        bal = {"check": "balances", "verdict": UNREADABLE,
               "why": "no adapter, so no balance read"}
    reads["balances"] = {"ok": bal.get("verdict") == RECONCILED,
                         "complete": bal.get("verdict") == RECONCILED,
                         "verdict": bal.get("verdict"),
                         "read_via": bal.get("read_via"),
                         "refusal": bal.get("refusal"), "why": bal.get("why")}
    if client is not None:
        orders = await asyncio.to_thread(ACC.read_open_orders_sync, client,
                                         paced_read=paced)
        positions = await asyncio.to_thread(ACC.read_positions_sync, client,
                                            paced_read=paced)
        activity = await asyncio.to_thread(
            ACC.read_account_activity_sync, client, since_ts=since,
            paced_read=paced)
    else:
        none = {"ok": False, "refusal": ACC.R_NO_CLIENT}
        orders, positions, activity = dict(none), dict(none), dict(none)
    reads["open_orders"] = {k: orders.get(k) for k in (
        "ok", "refusal", "endpoint", "open_orders", "why", "field")}
    reads["open_orders"]["complete"] = bool(orders.get("ok"))
    reads["positions"] = {k: positions.get(k) for k in (
        "ok", "refusal", "endpoint", "pages", "why", "field", "slug")}
    reads["positions"]["complete"] = bool(positions.get("ok")
                                          and positions.get("complete"))
    reads["executions"] = {k: activity.get(k) for k in (
        "ok", "complete", "refusal", "endpoint", "pages", "max_pages",
        "complete_by", "by_type", "unknown_types", "why", "field", "type")}
    reads["executions"]["complete"] = bool(activity.get("ok")
                                           and activity.get("complete"))

    # ── OUR BOOK ─────────────────────────────────────────────────────
    try:
        book = await _our_book_for_report(conn, account_id=acct, venue=ven)
        reads["our_book"] = {"ok": True, "complete": True,
                             "tables_absent": book["tables_absent"]}
    except Exception as exc:                               # noqa: BLE001
        book = None
        reads["our_book"] = {"ok": False, "complete": False,
                             "refusal": F_OUR_BOOK_UNREADABLE,
                             "error": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])}
        findings.append(_finding(
            F_OUR_BOOK_UNREADABLE, "book", blocking=True,
            why=("this system's own book could not be read, so a venue "
                 "holding cannot be told from an unbooked one"),
            error=reads["our_book"]["error"]))

    # THE BALANCE READ names its own failure (F_BALANCE_NOT_ESTABLISHED, in
    # `_compare_balances`); every other read that did not finish is named
    # here.
    for name in ("adapter", "client", "open_orders", "positions",
                 "executions"):
        r = reads.get(name)
        if r is not None and not r.get("complete"):
            findings.append(_finding(
                F_READ_NOT_ESTABLISHED, name, blocking=True,
                why=("the %s read did not establish its answer (%s). An "
                     "unread answer is not an empty one" %
                     (name, r.get("refusal") or r.get("verdict"))),
                refusal=r.get("refusal"), verdict=r.get("verdict")))

    # ── THE COMPARISONS, each only on reads that answered ────────────
    bal_summary, bal_findings = _compare_balances(bal, orders)
    out["balances"] = bal_summary
    findings.extend(bal_findings)
    if book is not None:
        if positions.get("ok"):
            findings.extend(_compare_positions(
                positions.get("positions") or {}, book))
        if orders.get("ok"):
            findings.extend(_compare_orders(orders.get("orders") or [], book))
        if activity.get("ok"):
            findings.extend(_compare_executions(activity, book, since=since))
        out["our_book"] = {
            "funded_open_positions": book["funded_positions"],
            "funded_working_orders": sorted(book["working_orders"]),
            "funded_sends_unanswered": book["sends_unanswered"],
            "funded_fills_on_record": len(book["fills"]),
            "legacy_live_beta_markets": sorted(book["legacy_positions"]),
            "shadow_lane_markets": len(book["shadow_slugs"])}
    if positions.get("ok"):
        out["venue_positions"] = positions.get("positions")
    if activity.get("ok"):
        out["venue_activity"] = {
            "by_type": activity.get("by_type"),
            "unknown_types": activity.get("unknown_types"),
            "rows": len(activity.get("rows") or [])}
    elif activity.get("rows_are_a_prefix_of_the_window"):
        out["venue_activity"] = {
            "prefix_only": True, "rows": len(activity.get("rows") or []),
            "by_type": activity.get("by_type")}

    # ── THE REGISTRY ROW, AND THE DESK'S OWN QUESTION ────────────────
    try:
        row = await _registry_row(conn, acct)
    except Exception as exc:                               # noqa: BLE001
        row = None
        reads["registry"] = {"ok": False, "complete": False,
                             "error": type(exc).__name__}
        findings.append(_finding(
            F_READ_NOT_ESTABLISHED, "registry", blocking=True,
            why="the account registry could not be read",
            error=type(exc).__name__))
    out["registry_row"] = ({k: row.get(k) for k in (
        "account_id", "desk_id", "status", "paused", "pause_reason",
        "accounting_status", "last_verified_at")} if row else None)
    if _is_desk_recovery(acct, row):
        out["gaps"].append({"gap": GAP_DESK_NOT_ADDRESSED, "why": (
            "this account is paused for an identifier collision across "
            "restarts of the shadow desk. A venue reconciliation reads "
            "balances, positions, orders and executions at the VENUE; it "
            "never reads bettor_desk_* rows, so however it comes out it "
            "does not address the desk's identifier integrity")})
        out["venue_reconciliation_addresses_desk_identifier_integrity"] = \
            False
        try:
            integ = await desk_identifier_integrity(conn, account_id=acct,
                                                    row=row)
        except Exception as exc:                           # noqa: BLE001
            integ = {"readable": False, "verified": False,
                     "error": "%s: %s" % (type(exc).__name__,
                                          str(exc)[:200])}
        out["desk_identifier_integrity"] = integ
        findings.append(_finding(
            F_DESK_IDS_NOT_VERIFIED, "desk_identifier_integrity",
            blocking=True,
            why=("the pause reason asks for verification of the corrected "
                 "build and nothing verifies it; see "
                 "desk_identifier_integrity"),
            pause_reason=(row or {}).get("pause_reason")))

    # ── THE VERDICT FIELDS ───────────────────────────────────────────
    completeness = {name: bool(r.get("complete")) for name, r in
                    reads.items()}
    authoritative = all(completeness.get(n) for n in (
        "balances", "open_orders", "positions", "executions", "our_book")) \
        and not any(n in reads for n in ("adapter", "client")) \
        and reads.get("registry", {}).get("ok", True)
    blocking = [f for f in findings if f["blocking"]]
    would = bool(authoritative and not blocking)
    out.update(
        ok=True, refusal=None, reads=reads,
        completeness={"reads": completeness, "complete": authoritative,
                      "why": ("every read was complete by its own "
                              "termination and strict" if authoritative
                              else "a read that did not finish is not "
                                   "evidence")},
        authoritative=authoritative,
        discrepancies=findings,
        blocking=blocking,
        verdicts=_verdicts(reads, findings),
        would_be_eligible=would,
        would_be_eligible_is_information=(
            "true only when every read was complete and strict and no "
            "finding blocks. It unpauses nothing and marks nothing"),
        pause_kept=True)
    return out


def _verdicts(reads: dict, findings: list) -> dict:
    """The four historical checks' verdicts, derived from the report, for the
    latest-evidence key's consumers."""
    out = {}
    for check, read in (("balances", "balances"),
                        ("positions", "positions"),
                        ("open_orders", "open_orders"),
                        ("executions", "executions")):
        if not (reads.get(read) or {}).get("complete"):
            out[check] = UNREADABLE
        elif any(f["blocking"] and f["check"] == check for f in findings):
            out[check] = DISCREPANCY
        else:
            out[check] = RECONCILED
    return out


async def record_discrepancy_report(conn, *, account_id: str, venue: str,
                                    by: str, adapter=None,
                                    now: float | None = None) -> dict:
    """RUN THE REPORT, KEEP IT IN THE HISTORY, AND WRITE THE LATEST-EVIDENCE
    KEY. Writes nothing to the account row.

    The history table is required: a report that could not be kept is
    refused by name rather than returned as though it had been recorded.
    """
    at = float(now if now is not None else time.time())
    rep = await discrepancy_report(conn, account_id=account_id, venue=venue,
                                   adapter=adapter, now=at)
    if not rep.get("ok"):
        return dict(rep, recorded=False)
    if not await _table(conn, REPORTS_TABLE):
        return dict(rep, ok=False, recorded=False,
                    refusal=R_REPORTS_UNAVAILABLE,
                    why="migration 145 is not applied here")
    body = dict(rep, recorded_by=str(by or ""), recorded_at=at)
    sha = _sha(body)
    report_id = "rcr:%s:%d:%s" % (rep["account_id"], int(at * 1000), sha[:12])
    async with conn.transaction():
        await conn.execute(
            "INSERT INTO bettor_account_reconciliation_reports (report_id, "
            " account_id, venue, recorded_at, recorded_by, authoritative, "
            " would_be_eligible, blocking_count, report, report_sha) VALUES "
            " ($1,$2,$3,to_timestamp($4),$5,$6,$7,$8,$9::jsonb,$10)",
            report_id, rep["account_id"], rep["venue"], at, str(by or ""),
            bool(rep["authoritative"]), bool(rep["would_be_eligible"]),
            len(rep["blocking"]), json.dumps(body, default=str), sha)
        # THE LATEST-EVIDENCE KEY, for the consumers that already read it
        # (`reconciliation_evidence`, readiness). Its `eligible` is the
        # report's `would_be_eligible` and its completeness is the report's
        # truthful one -- never a laxer verdict than the report reached.
        await _put_state(conn, RECONCILIATION_KEY, {
            "version": REPORT_VERSION,
            "recorded_at": at, "recorded_by": str(by or ""),
            "account_id": rep["account_id"], "venue": rep["venue"],
            "source": rep.get("adapter"), "ok": True,
            "eligible": bool(rep["would_be_eligible"]),
            "refusal": None if rep["would_be_eligible"] else R_NOT_RECONCILED,
            "verdicts": rep["verdicts"],
            "blocking": [{k: f.get(k) for k in ("check", "finding", "why")}
                         for f in rep["blocking"]],
            "discrepancies": [{k: f.get(k) for k in (
                "check", "finding", "blocking", "why")}
                for f in rep["discrepancies"]],
            "completeness": {"complete": bool(rep["authoritative"]),
                             "reads": rep["completeness"]["reads"],
                             "why": rep["completeness"]["why"]},
            "evidence_max_age_s": EVIDENCE_MAX_AGE_S,
            "wrote_account_row": False,
            "report_id": report_id, "report_sha": sha})
    return dict(body, recorded=True, report_id=report_id, report_sha=sha,
                latest_evidence_key=RECONCILIATION_KEY)


async def report_history(conn, *, account_id: str | None = None,
                         limit: int = 50) -> dict:
    """THE HISTORY: ids, times and verdict fields -- not the bodies."""
    if not await _table(conn, REPORTS_TABLE):
        return {"ok": False, "refusal": R_REPORTS_UNAVAILABLE}
    rows = await conn.fetch(
        "SELECT report_id, account_id, venue, "
        "       extract(epoch FROM recorded_at)::float8 AS recorded_at, "
        "       recorded_by, authoritative, would_be_eligible, "
        "       blocking_count, report_sha "
        "  FROM bettor_account_reconciliation_reports "
        " WHERE ($1::text IS NULL OR account_id=$1) "
        " ORDER BY recorded_at DESC, report_id DESC LIMIT $2",
        (str(account_id).strip() or None) if account_id else None,
        max(1, min(int(limit), 500)))
    return {"ok": True, "version": REPORT_VERSION,
            "reports": [dict(r) for r in rows],
            "bodies_are_not_listed": ("each report's full body is kept in "
                                      "the history table; this listing "
                                      "carries its verdict fields and sha"),
            "changes_nothing": REPORT_CHANGES_NOTHING}


def describe() -> dict:
    return {
        "version": VERSION,
        "checks": list(CHECKS),
        "verdicts": {"passing": list(PASSING),
                     "all": [RECONCILED, DISCREPANCY, UNREADABLE,
                             NOT_SUPPORTED]},
        "unreadable_blocks": ("'we could not look' must never render the "
                              "same as 'we looked and it was empty'"),
        "a_new_registry_id_is_not_evidence": A_NEW_ID_IS_NOT_EVIDENCE,
        "created_as": {"status": NEW_STATUS,
                       "accounting_status": NEW_ACCOUNTING, "paused": True},
        "execution_window_s": EXECUTION_WINDOW_S,
        "accounting_status_that_activation_accepts": list(FA.ACCOUNTING_OK),
        "pmus_reads_used": {
            "balances": "pmus.balances() -> GET /v1/account/balances",
            "positions": "client.portfolio.positions, paged to eof",
            "open_orders": "pmus.open_orders() -> orders.list",
            "executions": ("pmus.recent_trades(slug, since_ts) -> "
                           "portfolio.activities; it RAISES on an "
                           "unreadable or truncated page set")},
        # NOT None, which is what this said: terminal order history has no
        # venue endpoint, and the account the service's key reads is not
        # tied to the account id by any venue field.
        "remaining_adapter_gap": [GAP_TERMINAL_ORDER_HISTORY,
                                  GAP_ACCOUNT_IDENTITY],
        "discrepancy_report": {
            "version": REPORT_VERSION,
            "reads": ["balances", "open_orders (strict)",
                      "positions (strict)", "account activity (strict)"],
            "history": REPORTS_TABLE,
            "findings": list(FINDINGS),
            "gaps": list(GAPS),
            "changes_nothing": REPORT_CHANGES_NOTHING},
        "refusals": [R_NO_ADAPTER, R_NOT_RECONCILED, R_NO_ACCOUNT_ROW,
                     R_STILL_PAUSED],
    }
