"""The three venue reads reconciliation needs. Read-only, authenticated.

WHAT THIS CORRECTS. I reported three blockers as "capability gaps in the
endpoints". Two of them were not. Reading the adapter shows the venue SDK
already offers what I said it did not:

  * client.portfolio.activities({"cursor": ..., "types": [...]}) pages
    TRADE and POSITION_RESOLUTION rows. pmus_account._fetch_sync already
    walks it -- bounded at SIX pages of 100. That bound, not the venue, is
    why the account card showed 25 recent trades. aggregate_tennis_week's
    own docstring says the activities feed is "every fill and every
    resolution".

  * client.orders.list(params) returns the account's RESTING orders, and
    pmus.open_orders has wrapped it since 2026-08-28. The reconciliation
    workflow called /api/admin/open-orders instead, which is a DATABASE
    read of live_orders WHERE whale_username='manual'. That was my wiring
    choosing the wrong endpoint, not the venue lacking one.

So the honest restatement is: blockers 2 and 3 were MINE. Only account
identity is still unestablished, and this module is built to find out
what the venue offers rather than to assume either way.

ESTABLISH BEFORE CODING. capability_probe() returns KEY NAMES ONLY from
each venue payload -- never a value, never a credential, never an account
payload. Nothing else here is worth building until that says what exists.

IDENTITY IS COMPARED INSIDE THE SERVICE, NOT PRINTED. account_identity()
returns a VERDICT -- matched / did not match / no identity field -- and
the name of the field it used. It never returns the identifier. The
reconciliation job therefore learns whether the authenticated account is
the one that generated our fills without an account identifier ever
reaching a CI log or an artifact.

UNREADABLE IS NOT EMPTY, everywhere in this module. A failed read raises
or sets ok=False; an account with nothing returns an empty list with
ok=True. Collapsing those is how "the venue holds no positions" nearly
got written down as a fact when it might have been a failed call.

INCOMPLETE IS NOT COMPLETE. Every paged read reports `complete`, the page
count and the stop reason. A walk that hits its page bound says so rather
than returning a short book as if it were the whole one -- which is the
defect that produced "25 recent trades".

NOTHING HERE SUBMITS, CANCELS, OR WRITES. No order path, no database
write, no accounting record touched. The only credentials used are the
ones already in the API service's own environment, reached through
pmus._get_client(), and none of them is returned, logged or copied.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

# Page bounds. Generous, because the point of this module is to FINISH
# the book, but bounded, because an unbounded walk against a paging bug
# is a denial of service against our own venue budget.
MAX_ACTIVITY_PAGES = 200
MAX_ORDER_PAGES = 50
PAGE_SIZE = 100

# Field names that would identify an account. Checked by name only; the
# value is never returned by any function here.
_IDENTITY_HINTS = ("accountid", "account_id", "wallet", "address", "proxy",
                   "owner", "userid", "user_id", "subject", "principal")


class VenueUnreadable(Exception):
    """The read failed. NOT the same as the account holding nothing."""


def _looks_like_identity(key: str) -> bool:
    k = re.sub(r"[^a-z]", "", (key or "").lower())
    return any(h.replace("_", "") in k for h in _IDENTITY_HINTS)


def _client():
    from .. import pmus

    return pmus._get_client()


def _keys(obj: Any) -> list[str]:
    return sorted(obj.keys()) if isinstance(obj, dict) else []


# ── 1. ESTABLISH WHAT THE VENUE SUPPORTS ─────────────────────────────

def capability_probe() -> dict:
    """What each venue payload actually contains. KEY NAMES ONLY.

    Returns no values at all. The point is to answer "does an account
    identifier exist, and what is it called" before anything is written
    that depends on the answer -- and to answer it without an identifier,
    a balance or a position ever leaving the service.

    One page of each endpoint. This is the smallest possible read.
    """
    out: dict[str, Any] = {"read_values": False,
                           "note": "key names only; no values are returned"}
    c = _client()

    for name, call in (
        ("balances", lambda: (c.account.balances() or {})),
        ("positions", lambda: (c.portfolio.positions({"limit": 1}) or {})),
        ("activities", lambda: (c.portfolio.activities(
            {"limit": 1, "sortOrder": "SORT_ORDER_DESCENDING"}) or {})),
        ("orders", lambda: (c.orders.list(None) or {})),
    ):
        try:
            resp = call()
        except Exception as exc:                           # noqa: BLE001
            out[name] = {"ok": False, "error": type(exc).__name__}
            continue

        envelope = _keys(resp)
        rows = None
        for k in ("balances", "positions", "activities", "orders"):
            v = resp.get(k) if isinstance(resp, dict) else None
            if isinstance(v, list) and v:
                rows = _keys(v[0])
                break
            if isinstance(v, dict) and v:
                rows = _keys(next(iter(v.values())))
                break
        out[name] = {
            "ok": True,
            "envelope_keys": envelope,
            "row_keys": rows,
            "identity_keys": sorted(
                k for k in (envelope + (rows or []))
                if _looks_like_identity(k)),
            "pages_supported": "nextCursor" in envelope or "eof" in envelope,
        }

    ident = sorted({k for v in out.values()
                    if isinstance(v, dict)
                    for k in v.get("identity_keys") or []})
    out["identity_fields_found"] = ident
    out["identity_available"] = bool(ident)
    return out


# ── 2. ACCOUNT IDENTITY, AS A VERDICT ────────────────────────────────

def account_identity(expected: str | None) -> dict:
    """Is the authenticated account the one we think it is?

    `expected` comes from the service's own configuration, never from a
    caller and never from a CI input. The return value says whether it
    MATCHED and which field was used. It does not contain the
    identifier, so a reconciliation log can record the answer without
    recording the account.

    verdict:
        "match"          the authenticated account is `expected`
        "mismatch"       it is a different account -- reconciliation
                         against our ledger is meaningless until this is
                         resolved
        "no_identity"    the venue exposes no identifying field, so the
                         question cannot be answered here. THIS IS THE
                         BLOCKER, and it stays a blocker rather than
                         becoming a shrug.
        "unreadable"     the read failed
        "no_expected"    nothing to compare against was configured
    """
    try:
        c = _client()
        resp = c.account.balances() or {}
    except Exception as exc:                               # noqa: BLE001
        return {"verdict": "unreadable", "error": type(exc).__name__}

    rows = resp.get("balances") or []
    row = rows[0] if rows and isinstance(rows[0], dict) else {}
    candidates = {k: v for k, v in list(resp.items()) + list(row.items())
                  if _looks_like_identity(k) and isinstance(v, (str, int))}

    if not candidates:
        return {"verdict": "no_identity",
                "checked_keys": sorted(set(_keys(resp) + _keys(row))),
                "note": ("no identifying field on the balances payload; "
                         "the authenticated account cannot be tied to the "
                         "ledger through this endpoint")}
    if not expected:
        return {"verdict": "no_expected",
                "identity_fields": sorted(candidates),
                "note": "an identity field exists and nothing was "
                        "configured to compare it against"}

    want = str(expected).strip().lower()
    for key, val in sorted(candidates.items()):
        if str(val).strip().lower() == want:
            return {"verdict": "match", "field": key}
    return {"verdict": "mismatch",
            "identity_fields": sorted(candidates),
            "note": ("the authenticated account is not the configured "
                     "one; no value is reported")}


# ── 3. PAGINATED HISTORICAL ACTIVITY ─────────────────────────────────

def historical_activity(*, since_iso: str | None = None,
                        max_pages: int = MAX_ACTIVITY_PAGES) -> dict:
    """Every trade and resolution the venue will serve, paged to eof.

    THE SIX-PAGE BOUND IS THE BUG THIS REPLACES. _fetch_sync stops after
    six pages because it feeds an account card, which is correct for a
    card and wrong for a reconciliation. The 52 fills run 2026-08-04 to
    2026-09-10, so a reconciliation read has to finish the book or say
    that it did not.

    `complete` is False whenever the walk stopped for any reason other
    than the venue saying eof. A short book is never returned as if it
    were the whole one.
    """
    rows: list[dict] = []
    cursor = ""
    pages = 0
    stop = "eof"
    complete = True
    try:
        c = _client()
        for _ in range(max_pages):
            params: dict[str, Any] = {
                "limit": PAGE_SIZE,
                "sortOrder": "SORT_ORDER_DESCENDING",
                "types": ["ACTIVITY_TYPE_TRADE",
                          "ACTIVITY_TYPE_POSITION_RESOLUTION"],
            }
            if cursor:
                params["cursor"] = cursor
            resp = c.portfolio.activities(params) or {}
            page = resp.get("activities") or []
            rows.extend(r for r in page if isinstance(r, dict))
            pages += 1
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                break
        else:
            stop = "page bound reached"
            complete = False
    except Exception as exc:                               # noqa: BLE001
        raise VenueUnreadable("activities read failed after %d page(s): %s"
                              % (pages, type(exc).__name__)) from exc

    kinds: dict[str, int] = {}
    for r in rows:
        k = str(r.get("type") or "UNKNOWN")
        kinds[k] = kinds.get(k, 0) + 1

    return {
        "ok": True,
        "rows": rows,
        "count": len(rows),
        "pages": pages,
        "complete": complete,
        "stop_reason": stop,
        "by_type": kinds,
        "since_requested": since_iso,
        "note": ("complete=False means the walk stopped before the venue "
                 "said eof; the result is a PREFIX of the book, not the "
                 "book"),
    }


# ── 4. ACTUAL VENUE RESTING ORDERS ───────────────────────────────────

def resting_orders(*, max_pages: int = MAX_ORDER_PAGES) -> dict:
    """The account's open orders AT THE VENUE.

    NOT /api/admin/open-orders, which reads live_orders WHERE
    whale_username='manual' AND status='open' -- a database view of one
    lane. This asks the venue.

    A NOTED FAIL-SOFT UPSTREAM, DELIBERATELY NOT INHERITED:
    pmus.open_orders does `client.orders.list(params) or {}` then
    `.get("orders") or []`, so a None or an error envelope has already
    become an empty list before the caller sees it. An empty list is the
    answer that would let "no outstanding commitments" be written down.
    This reads the raw response and refuses to turn an absent key into
    an empty book.
    """
    rows: list[dict] = []
    pages = 0
    cursor = ""
    stop = "single page"
    complete = True
    try:
        c = _client()
        for _ in range(max_pages):
            params: dict[str, Any] | None = (
                {"limit": PAGE_SIZE, "cursor": cursor} if cursor else None)
            resp = c.orders.list(params)
            pages += 1
            if resp is None:
                raise VenueUnreadable(
                    "orders.list returned None on page %d; that is a failed "
                    "read, not an empty book" % pages)
            if not isinstance(resp, dict):
                raise VenueUnreadable(
                    "orders.list returned %s, not a mapping"
                    % type(resp).__name__)
            if "orders" not in resp:
                raise VenueUnreadable(
                    "orders.list response has no 'orders' key (keys: %s); "
                    "an absent key is not an empty book" % _keys(resp))
            page = resp.get("orders")
            if not isinstance(page, list):
                raise VenueUnreadable(
                    "orders is %s, not a list" % type(page).__name__)
            rows.extend(o for o in page if isinstance(o, dict))
            cursor = resp.get("nextCursor") or ""
            if resp.get("eof") or not cursor:
                stop = "eof"
                break
        else:
            stop = "page bound reached"
            complete = False
    except VenueUnreadable:
        raise
    except Exception as exc:                               # noqa: BLE001
        raise VenueUnreadable("orders read failed after %d page(s): %s"
                              % (pages, type(exc).__name__)) from exc

    return {
        "ok": True,
        "orders": rows,
        "count": len(rows),
        "pages": pages,
        "complete": complete,
        "stop_reason": stop,
        "note": ("count=0 with ok=True means the venue reported NO resting "
                 "orders. An unreadable venue raises VenueUnreadable and "
                 "never reaches this return."),
    }
