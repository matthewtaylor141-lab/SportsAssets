"""THE FUNDED LANE'S READ OF THE ACCOUNT AT THE VENUE: COMPLETE, OR REFUSED.

The execution gate measures ACCOUNT-WIDE exposure, and part of that total is
the venue's own answer: what the account holds and what it has working. This
module is the only place the funded lane turns those two venue responses into
dollars, and it has one rule: MISSING EXPOSURE IS NOT ZERO EXPOSURE.

── THE DEFECT THIS REPLACES (reproduced by independent review) ──────────
`read_venue_account` read resting orders through `pmus.open_orders`, whose
normaliser writes `float(o.get("leavesQuantity") or 0)` -- and then did the
same again itself with `float(o.get("leaves") or 0.0)`. A BUY at $0.50 for 100
contracts whose remaining quantity the venue did not state came back

    ok=True, open_orders=1, working_usd=0.0

so an order that may be committing $50 of collateral was counted as
committing nothing, and the gate cleared against headroom that was not there.
The positions walk had the same shape one level down:
`float(p.get("netPosition") or 0.0)` reads an unstated position as flat.

── WHY THIS IS A NEW MODULE AND NOT AN EDIT TO THE ADAPTER ──────────────
`pmus._norm_order` and `mirror_shadow.account_positions_walk` are shared with
the desk and with the protected worker's code, whose client is not changed
from here. So this module reads the RAW venue responses itself, through the
same client and `pmus.paced_read` (idempotent reads only, bounded retry), and
keeps an absent field absent.

── WHAT "COMPLETE" MEANS FOR EACH RESPONSE ─────────────────────────────
  * OPEN ORDERS. The venue's published response type is `{orders: [Order]}`
    with no cursor (`GetOpenOrdersResponse`), so one response is the whole
    list BY THE VENUE'S OWN CONTRACT. That contract is checked rather than
    assumed: a response with no `orders` list, or one that states a
    `nextCursor` or `eof: false` -- a sign of paging this endpoint does not
    declare and this reader cannot follow -- is refused.
  * POSITIONS. Paged. The walk must end on `eof` or an empty cursor within
    the page cap; a truncated, repeated or unnamed row refuses the walk.

Every value a dollar figure depends on must be present, well-formed and
finite. Where the venue states zero, zero is used -- an explicit zero is a
measurement; an absent field is not.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

VERSION = "FUNDED_ACCOUNT_READ_V1"

#: The intents that COMMIT new collateral while working, and those that close.
BUY_INTENTS = ("ORDER_INTENT_BUY_LONG", "ORDER_INTENT_BUY_SHORT")
SELL_INTENTS = ("ORDER_INTENT_SELL_LONG", "ORDER_INTENT_SELL_SHORT")

#: Venue order states that are still working the book (raw, prefixed form).
WORKING_STATES = ("ORDER_STATE_NEW", "ORDER_STATE_PENDING_NEW",
                  "ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_PENDING_REPLACE",
                  "ORDER_STATE_PENDING_RISK", "ORDER_STATE_OPEN")
#: States that are finished. One in an open-orders response commits nothing
#: ONLY if it also states nothing left to fill.
FINISHED_STATES = ("ORDER_STATE_FILLED", "ORDER_STATE_CANCELED",
                   "ORDER_STATE_CANCELLED", "ORDER_STATE_EXPIRED",
                   "ORDER_STATE_REJECTED")

#: How many position pages one walk may read. A walk that has not reached the
#: end by then is truncated, and a truncated walk is not a reading.
POSITIONS_PAGES_MAX = 20
POSITIONS_PAGE_LIMIT = 100

# ── refusals ──────────────────────────────────────────────────────────
R_NO_CLIENT = "VENUE_CLIENT_UNAVAILABLE"
R_ORDERS_READ_RAISED = "VENUE_OPEN_ORDERS_READ_RAISED"
R_ORDERS_RATE_LIMITED = "VENUE_OPEN_ORDERS_RATE_LIMITED"
R_ORDERS_RESPONSE_INCOMPLETE = "VENUE_OPEN_ORDERS_RESPONSE_NOT_COMPLETE"
R_ORDER_FIELD_MISSING = "VENUE_OPEN_ORDER_FIELD_MISSING"
R_ORDER_FIELD_MALFORMED = "VENUE_OPEN_ORDER_FIELD_MALFORMED"
R_ORDER_FIELD_NON_FINITE = "VENUE_OPEN_ORDER_FIELD_NON_FINITE"
R_ORDER_INCONSISTENT = "VENUE_OPEN_ORDER_IS_INCONSISTENT"
R_POSITIONS_READ_RAISED = "VENUE_POSITIONS_READ_RAISED"
R_POSITIONS_RATE_LIMITED = "VENUE_POSITIONS_RATE_LIMITED"
R_POSITIONS_WALK_INCOMPLETE = "VENUE_POSITIONS_WALK_INCOMPLETE"
R_POSITION_FIELD_MISSING = "VENUE_POSITION_FIELD_MISSING"
R_POSITION_FIELD_MALFORMED = "VENUE_POSITION_FIELD_MALFORMED"
R_POSITION_FIELD_NON_FINITE = "VENUE_POSITION_FIELD_NON_FINITE"
R_POSITION_COST_NOT_STATED = "VENUE_POSITION_COST_NOT_STATED"
R_PAGE_END_MALFORMED = "VENUE_PAGE_TERMINATION_FIELD_MALFORMED"
R_PAGE_END_INCONSISTENT = "VENUE_PAGE_TERMINATION_IS_INCONSISTENT"
R_PAGE_END_NOT_STATED = "VENUE_PAGE_DOES_NOT_SAY_WHETHER_IT_IS_THE_LAST"
R_ACTIVITY_READ_RAISED = "VENUE_ACTIVITY_READ_RAISED"
R_ACTIVITY_RATE_LIMITED = "VENUE_ACTIVITY_RATE_LIMITED"
R_ACTIVITY_RESPONSE_INCOMPLETE = "VENUE_ACTIVITY_RESPONSE_NOT_COMPLETE"
R_ACTIVITY_FIELD_MALFORMED = "VENUE_ACTIVITY_FIELD_MALFORMED"
R_ACTIVITY_TIME_UNREADABLE = "VENUE_ACTIVITY_TIME_UNREADABLE"
R_ACTIVITY_WALK_INCOMPLETE = "VENUE_ACTIVITY_WALK_DID_NOT_REACH_THE_WINDOW"

MISSING, MALFORMED, NON_FINITE = "MISSING", "MALFORMED", "NON_FINITE"


def number(v: Any) -> tuple[float | None, str | None]:
    """A venue number as `(value, problem)`, never a default.

    Accepts a bare number, a numeric string, or the venue's Amount shape
    `{"value": "0.50", "currency": "USD"}`. `problem` is MISSING (absent,
    None, empty), MALFORMED (not a number, a boolean, a non-USD amount) or
    NON_FINITE (NaN / inf); `value` is None whenever `problem` is set.
    """
    if v is None:
        return None, MISSING
    if isinstance(v, bool):
        return None, MALFORMED
    if isinstance(v, dict):
        cur = v.get("currency")
        if cur is not None and str(cur).upper() != "USD":
            return None, MALFORMED
        if "value" not in v or v.get("value") is None:
            return None, MISSING
        v = v["value"]
        if isinstance(v, bool):
            return None, MALFORMED
    if isinstance(v, str) and not v.strip():
        return None, MISSING
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None, MALFORMED
    if not math.isfinite(f):
        return None, NON_FINITE
    return f, None


_ORDER_REFUSAL = {MISSING: R_ORDER_FIELD_MISSING,
                  MALFORMED: R_ORDER_FIELD_MALFORMED,
                  NON_FINITE: R_ORDER_FIELD_NON_FINITE}
_POSITION_REFUSAL = {MISSING: R_POSITION_FIELD_MISSING,
                     MALFORMED: R_POSITION_FIELD_MALFORMED,
                     NON_FINITE: R_POSITION_FIELD_NON_FINITE}


def normalise_order(raw: Any) -> dict:
    """ONE RAW VENUE ORDER, every field kept as the venue stated it or None.

    Returns the normalised order with `problems`: a list of
    `{field, problem}` for every value that is absent, malformed or
    non-finite. Nothing here decides whether a problem matters; `working`
    does, per intent.
    """
    o = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {
        "order_id": o.get("id"),
        "us_market_slug": o.get("marketSlug"),
        "intent": o.get("intent"),
        "state": o.get("state"),
        "problems": [],
    }
    if not isinstance(raw, dict):
        out["problems"].append({"field": "order", "problem": MALFORMED})
        return out
    for field, key in (("price", "price"), ("quantity", "quantity"),
                       ("leaves", "leavesQuantity"),
                       ("filled", "cumQuantity")):
        val, prob = number(o.get(key))
        out[field] = val
        if prob is not None:
            out["problems"].append({"field": key, "problem": prob})
    return out


def working(order: dict) -> dict:
    """THE COLLATERAL ONE NORMALISED ORDER COMMITS, or why it cannot be said.

    `{"ok": True, "collateral_usd": x}` or `{"ok": False, "refusal": ...}`.

      * a BUY that is working commits `collateral_for(price, leaves, intent)`
        and needs a price in (0, 1) and a stated, finite, non-negative
        remaining quantity no larger than the order;
      * a SELL closes inventory and commits nothing new, so its remaining
        quantity is not needed -- but a value it DOES state must still be a
        well-formed number, because a damaged field means a damaged response;
      * a finished order commits nothing only if it states nothing left.
    """
    from . import bettor_funded_execution as FX

    base = {"order_id": order.get("order_id"),
            "us_market_slug": order.get("us_market_slug")}
    probs = {p["field"]: p["problem"] for p in order.get("problems") or []}
    if "order" in probs:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MALFORMED,
                    field="order")
    # A VALUE THAT IS PRESENT BUT DAMAGED refuses whatever the intent.
    for field, prob in probs.items():
        if prob in (MALFORMED, NON_FINITE):
            return dict(base, ok=False, refusal=_ORDER_REFUSAL[prob],
                        field=field)
    intent, state = order.get("intent"), order.get("state")
    if not intent:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MISSING,
                    field="intent")
    if intent not in BUY_INTENTS + SELL_INTENTS:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MALFORMED,
                    field="intent", value=intent)
    if not state:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MISSING,
                    field="state")
    if state not in WORKING_STATES + FINISHED_STATES:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MALFORMED,
                    field="state", value=state)
    leaves = order.get("leaves")
    if state in FINISHED_STATES:
        if "leavesQuantity" in probs:
            return dict(base, ok=False, refusal=R_ORDER_FIELD_MISSING,
                        field="leavesQuantity",
                        why=("a finished order in the open list commits "
                             "nothing only if it says nothing is left"))
        if leaves != 0:
            return dict(base, ok=False, refusal=R_ORDER_INCONSISTENT,
                        why="a finished state with %r still to fill" % leaves)
        return dict(base, ok=True, collateral_usd=0.0, basis="FINISHED")
    if intent in SELL_INTENTS:
        return dict(base, ok=True, collateral_usd=0.0,
                    basis="SELL_COMMITS_NOTHING_NEW")
    for field in ("price", "quantity", "leavesQuantity"):
        if field in probs:
            return dict(base, ok=False, refusal=_ORDER_REFUSAL[probs[field]],
                        field=field,
                        why=("a working BUY's %s is needed to measure what it "
                             "commits, and an unstated value is not zero"
                             % field))
    price, qty = order["price"], order["quantity"]
    if not (0.0 < price < 1.0):
        return dict(base, ok=False, refusal=R_ORDER_FIELD_MALFORMED,
                    field="price", value=price)
    if qty < 0 or leaves < 0 or leaves > qty:
        return dict(base, ok=False, refusal=R_ORDER_INCONSISTENT,
                    why="quantity %r, remaining %r" % (qty, leaves))
    usd = float(FX.collateral_for(price, leaves, intent))
    if not math.isfinite(usd) or usd < 0:
        return dict(base, ok=False, refusal=R_ORDER_FIELD_NON_FINITE,
                    field="collateral")
    return dict(base, ok=True, collateral_usd=usd, basis="BUY_WORKING",
                price=price, leaves=leaves, intent=intent)


def page_end(resp: dict, *, previous_cursor: str = "") -> dict:
    """WHERE A PAGED WALK STANDS AFTER ONE PAGE, from fields read BY TYPE.

    `{"ok": True, "done": True}`, `{"ok": True, "done": False, "cursor": c}`,
    or `{"ok": False, "refusal": ...}`. The rules, and why each exists:

      * `eof`, when present, is a JSON boolean. The string "false" is truthy,
        and reading it as a boolean ended walks the venue said were not over.
      * `nextCursor`, when present, is a string.
      * eof true  -> done, and a non-empty cursor beside it is a contradiction.
      * eof false -> NOT done, and needs a non-empty cursor to continue; one
        without is an explicit "not finished" with no way on, which is a
        refusal, never a finished walk.
      * eof absent -> a non-empty cursor continues; no cursor either means the
        page never said it was the last, which is not a complete walk.
      * a cursor equal to the previous one would re-read the same page for
        ever; it is refused rather than counted toward the page cap.
    """
    has_eof = "eof" in resp
    eof = resp.get("eof")
    cur = resp.get("nextCursor")
    if has_eof and not isinstance(eof, bool):
        return {"ok": False, "refusal": R_PAGE_END_MALFORMED, "field": "eof",
                "type": type(eof).__name__}
    if cur is not None and not isinstance(cur, str):
        return {"ok": False, "refusal": R_PAGE_END_MALFORMED,
                "field": "nextCursor", "type": type(cur).__name__}
    cur = (cur or "").strip()
    if eof is True:
        if cur:
            return {"ok": False, "refusal": R_PAGE_END_INCONSISTENT,
                    "why": "eof is true and a continuation cursor is given"}
        return {"ok": True, "done": True}
    if not cur:
        return {"ok": False,
                "refusal": (R_PAGE_END_INCONSISTENT if eof is False
                            else R_PAGE_END_NOT_STATED),
                "why": ("the page says the walk is not finished and gives no "
                        "cursor to continue it" if eof is False else
                        "the page states neither eof nor a cursor")}
    if previous_cursor and cur == previous_cursor:
        return {"ok": False, "refusal": R_PAGE_END_INCONSISTENT,
                "why": "the continuation cursor did not advance"}
    return {"ok": True, "done": False, "cursor": cur}


def _is_rate_limit(exc: Exception) -> bool:
    try:
        from .workers.mirror_shadow import is_rate_limit
        return bool(is_rate_limit(exc))
    except Exception:                                          # noqa: BLE001
        return False


def read_open_orders_sync(client, *, paced_read=None) -> dict:
    """The account's working orders and the collateral they commit, or a
    refusal. Synchronous: callers run it in a thread."""
    if paced_read is None:
        from .pmus import paced_read
    out: dict[str, Any] = {"version": VERSION, "ok": False,
                           "endpoint": "orders.list"}
    try:
        resp = paced_read(lambda: client.orders.list(None),
                          endpoint="orders.list")
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, refusal=(R_ORDERS_RATE_LIMITED if _is_rate_limit(exc)
                                  else R_ORDERS_READ_RAISED),
                    error=type(exc).__name__)
    if not isinstance(resp, dict) or not isinstance(resp.get("orders"), list):
        return dict(out, refusal=R_ORDERS_RESPONSE_INCOMPLETE,
                    why=("the response carries no `orders` list, so it states "
                         "nothing about the account -- and nothing is not "
                         "an empty list"))
    if "eof" in resp and not isinstance(resp.get("eof"), bool):
        return dict(out, refusal=R_PAGE_END_MALFORMED, field="eof",
                    type=type(resp.get("eof")).__name__)
    if resp.get("nextCursor") is not None \
            and not isinstance(resp.get("nextCursor"), str):
        return dict(out, refusal=R_PAGE_END_MALFORMED, field="nextCursor",
                    type=type(resp.get("nextCursor")).__name__)
    if (resp.get("nextCursor") or "").strip() or resp.get("eof") is False:
        return dict(out, refusal=R_ORDERS_RESPONSE_INCOMPLETE,
                    why=("the response indicates more pages, which this "
                         "endpoint's published contract does not declare and "
                         "this reader cannot follow"))
    rows, total = [], 0.0
    for raw in resp["orders"]:
        w = working(normalise_order(raw))
        if not w.get("ok"):
            return dict(out, **{k: v for k, v in w.items() if k != "ok"},
                        orders_examined=len(rows) + 1)
        rows.append(w)
        total += w["collateral_usd"]
    return dict(out, ok=True, refusal=None, working_usd=round(total, 6),
                open_orders=len(rows), orders=rows)


def read_positions_sync(client, *, paced_read=None) -> dict:
    """What the account holds, at the venue's own all-in cost, or a refusal."""
    if paced_read is None:
        from .pmus import paced_read
    out: dict[str, Any] = {"version": VERSION, "ok": False,
                           "endpoint": "portfolio.positions", "pages": 0}
    held, slugs, seen = 0.0, [], set()
    cursor = ""
    for _ in range(POSITIONS_PAGES_MAX):
        params = {"limit": POSITIONS_PAGE_LIMIT,
                  **({"cursor": cursor} if cursor else {})}
        try:
            resp = paced_read(lambda p=params: client.portfolio.positions(p),
                              endpoint="portfolio.positions")
        except Exception as exc:                               # noqa: BLE001
            return dict(out, refusal=(R_POSITIONS_RATE_LIMITED
                                      if _is_rate_limit(exc)
                                      else R_POSITIONS_READ_RAISED),
                        error=type(exc).__name__)
        out["pages"] += 1
        if not isinstance(resp, dict) or not isinstance(
                resp.get("positions"), dict):
            return dict(out, refusal=R_POSITIONS_WALK_INCOMPLETE,
                        why="a page carries no `positions` object")
        for slug, p in resp["positions"].items():
            key = slug.strip().lower() if isinstance(slug, str) else ""
            if not key or key in seen:
                return dict(out, refusal=R_POSITIONS_WALK_INCOMPLETE,
                            why="a row that cannot be named uniquely")
            seen.add(key)
            if not isinstance(p, dict):
                return dict(out, refusal=R_POSITION_FIELD_MALFORMED,
                            slug=key, field="position")
            net, prob = number(p.get("netPosition"))
            if prob is not None:
                return dict(out, refusal=_POSITION_REFUSAL[prob], slug=key,
                            field="netPosition",
                            why=("an unstated position is not a flat one"
                                 if prob == MISSING else None))
            if net == 0:
                continue
            cost, cprob = number(p.get("cost"))
            if cprob is not None:
                return dict(out, refusal=(R_POSITION_COST_NOT_STATED
                                          if cprob == MISSING
                                          else _POSITION_REFUSAL[cprob]),
                            slug=key, field="cost",
                            why=("the account holds %s of %s and its cost is "
                                 "not a measurable number" % (net, key)))
            held += abs(cost)
            slugs.append(key)
        end = page_end(resp, previous_cursor=cursor)
        if not end["ok"]:
            return dict(out, **{k: v for k, v in end.items() if k != "ok"})
        if end["done"]:
            return dict(out, ok=True, refusal=None, held_usd=round(held, 6),
                        held_slugs=sorted(slugs))
        cursor = end["cursor"]
    return dict(out, refusal=R_POSITIONS_WALK_INCOMPLETE,
                why="the walk reached %d pages without an end"
                    % POSITIONS_PAGES_MAX)


#: Bound on the own-trades walk. A lost acknowledgement is investigated over
#: the minutes-to-hours since its send; a market with more of the account's
#: own trades than this in that window refuses rather than truncating.
ACTIVITY_PAGES_MAX = 10
ACTIVITY_PAGE_LIMIT = 100


def read_own_trades_sync(client, us_market_slug: str, since_ts: float, *,
                         paced_read=None) -> dict:
    """THE ACCOUNT'S OWN EXECUTIONS ON ONE MARKET SINCE AN INSTANT, or why the
    log does not establish them.

    Stricter than `pmus.recent_trades`, which the protected worker shares and
    which this module therefore does not change: that reader treats a page
    with no `activities` list as empty and a missing cursor as the end. Here:

      * every page must carry an `activities` LIST;
      * every trade row must carry a readable time -- a row that cannot be
        placed before or after the send cannot be excluded from it;
      * the walk ends only when a row older than `since_ts` has been seen
        (newest-first order) or a page says eof=true by the rules of
        `page_end`; running out of pages is a refusal, not an answer.
    """
    if paced_read is None:
        from .pmus import paced_read
    from . import pmus
    from .api.pmus_account import _any_ts
    want = str(us_market_slug or "").strip().lower()
    out: dict[str, Any] = {"version": VERSION, "ok": False,
                           "endpoint": "portfolio.activities",
                           "us_market_slug": want, "since_epoch_s": since_ts,
                           "pages": 0}
    rows: list[dict] = []
    cursor = ""
    for _ in range(ACTIVITY_PAGES_MAX):
        params = {"limit": ACTIVITY_PAGE_LIMIT,
                  "sortOrder": "SORT_ORDER_DESCENDING",
                  "types": ["ACTIVITY_TYPE_TRADE"], "marketSlug": want,
                  **({"cursor": cursor} if cursor else {})}
        try:
            resp = paced_read(lambda p=params: client.portfolio.activities(p),
                              endpoint="portfolio.activities")
        except Exception as exc:                               # noqa: BLE001
            return dict(out, refusal=(R_ACTIVITY_RATE_LIMITED
                                      if _is_rate_limit(exc)
                                      else R_ACTIVITY_READ_RAISED),
                        error=type(exc).__name__)
        out["pages"] += 1
        if not isinstance(resp, dict) or not isinstance(
                resp.get("activities"), list):
            return dict(out, refusal=R_ACTIVITY_RESPONSE_INCOMPLETE,
                        why="a page carries no `activities` list")
        reached = False
        for act in resp["activities"]:
            if not isinstance(act, dict):
                return dict(out, refusal=R_ACTIVITY_FIELD_MALFORMED,
                            field="activity")
            if act.get("type") != "ACTIVITY_TYPE_TRADE":
                continue
            t = act.get("trade")
            if not isinstance(t, dict):
                return dict(out, refusal=R_ACTIVITY_FIELD_MALFORMED,
                            field="trade")
            ts = float(_any_ts(act) or 0.0)
            if ts <= 0:
                return dict(out, refusal=R_ACTIVITY_TIME_UNREADABLE,
                            trade_id=t.get("id"))
            if ts < since_ts:
                reached = True
                continue
            if str(t.get("marketSlug") or "").strip().lower() != want:
                continue
            own = pmus.trade_own_order(t)
            rows.append({"trade_id": t.get("id"), "ts": ts,
                         "qty": t.get("qty"),
                         "own_order_id": (str(own.get("id"))
                                          if own.get("id") else None)})
        if reached:
            return dict(out, ok=True, refusal=None, rows=rows,
                        complete_by="A_ROW_OLDER_THAN_THE_WINDOW_WAS_READ")
        end = page_end(resp, previous_cursor=cursor)
        if not end["ok"]:
            return dict(out, **{k: v for k, v in end.items() if k != "ok"})
        if end["done"]:
            return dict(out, ok=True, refusal=None, rows=rows,
                        complete_by="THE_VENUE_STATED_EOF")
        cursor = end["cursor"]
    return dict(out, refusal=R_ACTIVITY_WALK_INCOMPLETE,
                why="%d pages did not reach the window's start"
                    % ACTIVITY_PAGES_MAX)


async def read_venue_account(*, client=None, paced_read=None) -> dict:
    """WHAT THE ACCOUNT HOLDS AND HAS WORKING AT THE VENUE, OR WHY NOT.

    `{"ok": True, "held_usd", "working_usd", "unresolved_usd": 0.0, ...}` only
    when BOTH reads are complete and every figure measured. Otherwise
    `{"ok": False, "refusal": <name>}` and no partial total at all.
    `unresolved_usd` is 0 here because unresolved sends are OUR state and are
    counted from our own tables; the venue cannot report an order it never
    received.
    """
    at = time.time()
    out: dict[str, Any] = {"version": VERSION, "ok": False,
                           "read_at_epoch_s": at,
                           "source": "orders.list + portfolio.positions, raw"}
    if client is None:
        try:
            from .pmus import _get_client
            client = _get_client()
        except Exception as exc:                               # noqa: BLE001
            return dict(out, refusal=R_NO_CLIENT, error=type(exc).__name__)
    pos = await asyncio.to_thread(read_positions_sync, client,
                                  paced_read=paced_read)
    out["pages"] = pos.get("pages")
    if not pos.get("ok"):
        return dict(out, **{k: v for k, v in pos.items()
                            if k not in ("ok", "version")})
    ords = await asyncio.to_thread(read_open_orders_sync, client,
                                   paced_read=paced_read)
    if not ords.get("ok"):
        return dict(out, **{k: v for k, v in ords.items()
                            if k not in ("ok", "version")})
    return dict(out, ok=True, refusal=None,
                held_usd=pos["held_usd"], working_usd=ords["working_usd"],
                unresolved_usd=0.0, held_slugs=pos["held_slugs"],
                open_orders=ords["open_orders"])
