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

import json
import time

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
                try:
                    net = float((p or {}).get("netPosition") or 0)
                except (TypeError, ValueError):
                    return {"check": "positions", "verdict": UNREADABLE,
                            "slug": slug, "raw": p,
                            "why": ("a position whose netPosition does not "
                                    "parse is not an empty position")}
                if net:
                    held[str(slug)] = net
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                eof = True
                break
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
    ours, ours_err = await _our_open_slugs(conn)
    out["our_open_markets"] = (None if ours is None else sorted(ours))
    out["our_book_read_error"] = ours_err
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
        unbooked = sorted(set(slugs) - ours)
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


async def _our_open_slugs(conn):
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
        "remaining_adapter_gap": None,
        "refusals": [R_NO_ADAPTER, R_NOT_RECONCILED, R_NO_ACCOUNT_ROW,
                     R_STILL_PAUSED],
    }
