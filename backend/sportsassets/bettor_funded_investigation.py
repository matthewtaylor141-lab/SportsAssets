"""THE LOST ACKNOWLEDGEMENT: INVESTIGATED ON EVERY CYCLE, CLOSED ONLY BY AN
AUTHENTICATED AND AUDITED DECISION.

WHAT A LOST ACKNOWLEDGEMENT IS. The send left this process and no answer came
back, so the venue may or may not hold an order. The book records that as an
intent in UNRESOLVED with no venue order id (and, when a leg reservation was
taken, a reservation in AMBIGUOUS). Both keep their exposure counted, and the
one-open-group rule then refuses every further entry. That containment is
correct and it is not recovery: nothing in the scheduled path can close it,
because this venue accepts no client order identity (see
`bettor_funded_book.CLIENT_ORDER_IDENTITY_SUPPORTED`) and so no order it holds
can be proved to be ours by reading it.

WHAT THIS MODULE DOES, AND THE ONE THING IT NEVER DOES.

  investigate_all      scheduled, every servicing pass. Opens one durable
                       investigation per lost acknowledgement and refreshes it
                       with what the venue shows on that market: resting
                       orders, the ones that carry every term of our request,
                       and the account's own executions since the send. It
                       writes a READ_ESTABLISHED_NOTHING evidence row when the
                       picture changes. It NEVER changes the intent or the
                       reservation.

  reservation_reader   the `venue_reader` the pair pass's recovery step calls.
                       The same investigation, bound to the reservation.

  resolve              the operator's route. Two resolutions, each re-read at
                       the venue by this code at the moment of the decision,
                       each audited whether accepted or refused:

    NAME_THE_ORDER     the operator names the venue order they have
                       established is ours (from the venue's own account
                       history, which this API cannot read). The venue must
                       return that order, with every term of our request and a
                       creation time inside the request's window, and no other
                       intent may hold it. Then it is acknowledged, its
                       executions are booked in the direction the intent
                       states, and the reservation is consumed.

    NO_EXPOSURE_EXISTS the operator attests that no exposure-bearing order
                       exists. Refused unless the request is older than
                       SETTLE_S, the resting-order read is complete and shows
                       NOTHING on that market, and the account's own
                       execution log on that market since the send is
                       complete and EMPTY. Then the intent is closed and the
                       reservation released on an OPERATOR_ATTESTED evidence
                       row -- a separate kind that no venue-absence check can
                       read.

WHY THE ABSENCE RESOLUTION IS NOT "AN EMPTY OPEN-ORDERS LIST". It is not one
read; it is two complete ones plus a human's statement. An order that filled
is in the execution log, and one that is working is in the resting list; an
order that was cancelled or expired with nothing filled holds no exposure. The
residual risk -- a request the venue processes after SETTLE_S -- is named in
the attestation the operator signs, and is why the route will not accept it
earlier.

AUTHENTICATION IS DONE BY THE ROUTE, NOT HERE. This module receives the
already-verified description of how the caller authenticated and writes it
into the audit row. It never receives, stores or logs a credential.

IT SUBMITS NOTHING. There is no send, cancel or amend on any path below.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any

from . import bettor_funded_book as FB
from . import bettor_funded_reservations as RSV

VERSION = "FUNDED_INVESTIGATION_V1"

OPEN = "OPEN"
RESOLVED_ORDER_NAMED = "RESOLVED_ORDER_NAMED"
RESOLVED_NO_EXPOSURE = "RESOLVED_NO_EXPOSURE"

REQ_NAME_THE_ORDER = "NAME_THE_ORDER"
REQ_NO_EXPOSURE_EXISTS = "NO_EXPOSURE_EXISTS"
REQUESTS = (REQ_NAME_THE_ORDER, REQ_NO_EXPOSURE_EXISTS)
#: How the audit records a request that is neither. It is still audited.
REQ_UNRECOGNISED = "UNRECOGNISED"

#: How old a request must be before the absence of any trace of it means
#: anything. A venue that processes a request late would show it after this.
SETTLE_S = 900.0
#: A named order must have been created within this long AFTER our send (and
#: no earlier than the book's clock tolerance BEFORE it).
NAMED_CREATED_WITHIN_S = 600.0
CLOCK_TOLERANCE_S = FB.CORRELATION_CLOCK_TOLERANCE_S

R_SCHEMA = "THE_INVESTIGATION_SCHEMA_IS_NOT_IN_THIS_DATABASE"
R_NO_SUCH_INTENT = FB.R_NO_SUCH_INTENT
R_NOT_A_LOST_ACK = "THAT_INTENT_IS_NOT_AN_UNRESOLVED_LOST_ACKNOWLEDGEMENT"
R_UNKNOWN_REQUEST = "THAT_IS_NOT_A_RESOLUTION_THIS_ROUTE_OFFERS"
R_CONFIRM = "CONFIRM_MUST_REPEAT_THE_INTENT_ID"
R_ATTESTER = "AN_ATTESTATION_NAMES_WHO_MADE_IT"
R_ATTESTER_NOT_AUTHENTICATED = \
    "THE_ATTESTER_IS_NOT_THE_IDENTITY_THE_RESOLUTION_KEY_AUTHENTICATES"
R_STATEMENT = "AN_ATTESTATION_STATES_WHAT_WAS_CHECKED"
R_ORDER_ID = "NAME_THE_ORDER_NEEDS_THE_VENUE_ORDER_ID"
R_ORDER_UNREADABLE = "THE_VENUE_DID_NOT_RETURN_THAT_ORDER"
R_ORDER_TERMS_DIFFER = "THAT_ORDER_DOES_NOT_CARRY_THIS_REQUESTS_TERMS"
R_ORDER_TIME_UNREADABLE = "THAT_ORDERS_CREATION_TIME_IS_NOT_READABLE"
R_ORDER_OUTSIDE_WINDOW = "THAT_ORDER_WAS_NOT_CREATED_IN_THIS_REQUESTS_WINDOW"
R_ORDER_CLAIMED = "THAT_ORDER_ALREADY_BELONGS_TO_ANOTHER_INTENT"
R_SENT_AT_UNKNOWN = "THE_REQUESTS_SEND_TIME_IS_NOT_RECORDED"
R_TOO_SOON = "THE_REQUEST_IS_TOO_RECENT_FOR_ABSENCE_TO_MEAN_ANYTHING"
R_RESTING_UNREADABLE = "THE_RESTING_ORDERS_COULD_NOT_BE_READ_COMPLETELY"
R_RESTING_ON_MARKET = "AN_ORDER_IS_RESTING_ON_THIS_MARKET"
R_RESTING_UNPLACED = "A_RESTING_ORDER_NAMES_NO_MARKET"
R_EXECUTIONS_UNREADABLE = "THE_ACCOUNTS_EXECUTIONS_COULD_NOT_BE_READ_COMPLETELY"
R_EXECUTED_SINCE_SEND = "THE_ACCOUNT_EXECUTED_ON_THIS_MARKET_SINCE_THE_SEND"
R_RESERVATION_NOT_MOVED = "THE_RESERVATION_COULD_NOT_BE_MOVED"
R_CHANGED_UNDER_US = "THE_INTENT_CHANGED_WHILE_IT_WAS_BEING_RESOLVED"
R_ORDER_WAS_REJECTED = "THE_VENUE_REJECTED_THAT_ORDER_SO_IT_CARRIES_NO_EXPOSURE"
R_NOT_YET_INVESTIGATED = "NO_INVESTIGATION_HAS_READ_THIS_INTENT_YET"
R_EVIDENCE_NOT_REFERENCED = "THE_RESOLUTION_DOES_NOT_CITE_THE_READ_IT_WAS_MADE_ON"
R_STALE_EVIDENCE = "THE_INVESTIGATION_HAS_READ_SOMETHING_NEWER_SINCE"
R_RESOLVED_DIFFERENTLY = "THIS_INTENT_WAS_ALREADY_RESOLVED_DIFFERENTLY"
R_RAISED = "THE_RESOLUTION_RAISED_AND_NOTHING_WAS_APPLIED"

#: The authentication description the route must pass. Values only say WHICH
#: factors were presented and verified -- never the factors themselves.
AUTH_FIELDS = ("admin_token_verified", "resolution_key_verified",
               "operator", "route")

ATTESTATION_TEXT = {
    REQ_NAME_THE_ORDER: (
        "I have established from the venue's own account records that this "
        "venue order was placed by this system's request for this intent, and "
        "by no other request or person."),
    REQ_NO_EXPOSURE_EXISTS: (
        "I have checked the venue's own account records and no order from "
        "this request exists that is working or has executed. I understand "
        "this closes the intent and releases its exposure, and that a request "
        "processed by the venue after this attestation would be an unbooked "
        "position."),
}


# ═════════════════════════════════════════════════════════════════════
# 1 · WHAT MAY BE ASKED OF THE VENUE
# ═════════════════════════════════════════════════════════════════════

class PmusReads:
    """The production reads. Each method returns `{ok, ...}` and never raises:
    a read that fails is a refusal with a name, not an exception that a
    caller might catch as "nothing found"."""

    def resting_orders(self) -> dict:
        from . import bettor_funded_account as ACC
        from . import pmus
        try:
            client = pmus._get_client()
        except Exception as exc:                            # noqa: BLE001
            return {"ok": False, "refusal": ACC.R_NO_CLIENT,
                    "error": type(exc).__name__}
        got = ACC.read_open_orders_sync(client)
        if not got.get("ok"):
            return {"ok": False, "refusal": got.get("refusal"),
                    "why": got.get("why")}
        return {"ok": True, "endpoint": "orders.list",
                "orders": [{"order_id": o.get("order_id"),
                            "us_market_slug": o.get("us_market_slug")}
                           for o in got.get("orders") or []]}

    def own_executions(self, us_market_slug: str, since_ts: float) -> dict:
        """The STRICT own-trades walk (`bettor_funded_account`): an absent
        list, an unreadable time, a malformed or open-ended termination and a
        walk that does not reach the window are all refusals."""
        from . import bettor_funded_account as ACC
        from . import pmus
        try:
            client = pmus._get_client()
        except Exception as exc:                            # noqa: BLE001
            return {"ok": False, "refusal": ACC.R_NO_CLIENT,
                    "error": type(exc).__name__}
        got = ACC.read_own_trades_sync(client, us_market_slug, since_ts)
        if not got.get("ok"):
            return {"ok": False, "refusal": got.get("refusal"),
                    "why": got.get("why")}
        return {"ok": True, "endpoint": "portfolio.activities (own trades)",
                "complete_by": got.get("complete_by"),
                "rows": got.get("rows") or []}

    def order(self, venue_order_id: str) -> dict:
        from . import pmus
        try:
            st = pmus.order_status(venue_order_id)
        except Exception as exc:                            # noqa: BLE001
            return {"ok": False, "refusal": R_ORDER_UNREADABLE,
                    "error": type(exc).__name__}
        if not isinstance(st, dict) or not st:
            return {"ok": False, "refusal": R_ORDER_UNREADABLE,
                    "why": "the venue has no record of that order id"}
        return {"ok": True, "endpoint": "orders.retrieve", "order": st}


async def _call(fn, *a):
    """Venue reads are synchronous network calls; they run off the loop."""
    return await asyncio.to_thread(fn, *a)


# ═════════════════════════════════════════════════════════════════════
# 2 · THE REQUEST, AND WHAT THE VENUE SHOWS ABOUT IT
# ═════════════════════════════════════════════════════════════════════

async def has_schema(conn) -> bool:
    n = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        " WHERE table_schema='public' AND table_name = ANY($1::text[])",
        ["bettor_funded_investigations", "bettor_funded_resolution_audit"])
    return int(n or 0) == 2


def venue_intent_of(intent: dict) -> str:
    """The intent word the venue order carries. An EXIT sells what the entry
    bought (`pmus._exit_intent`), so its venue word is the SELL counterpart of
    the position's recorded BUY."""
    oi = str(intent.get("order_intent") or "")
    if str(intent.get("kind")) == "EXIT":
        return {"ORDER_INTENT_BUY_LONG": "ORDER_INTENT_SELL_LONG",
                "ORDER_INTENT_BUY_SHORT": "ORDER_INTENT_SELL_SHORT"}.get(oi, "")
    return oi


def _is_lost_ack(intent: dict) -> bool:
    return (str(intent.get("state")) == "UNRESOLVED"
            and not intent.get("venue_order_id"))


def _sent_epoch(intent: dict):
    return FB._epoch_of(intent.get("sent_at"))


async def _intent(conn, intent_id: str, *, lock: bool = False):
    r = await conn.fetchrow(
        "SELECT * FROM bettor_funded_intents WHERE intent_id=$1"
        + (" FOR UPDATE" if lock else ""), str(intent_id))
    return dict(r) if r else None


async def _reservation_of(conn, intent_id: str):
    try:
        r = await conn.fetchrow(
            "SELECT * FROM bettor_funded_leg_reservations "
            " WHERE intent_id=$1 AND state = ANY($2::text[]) "
            " ORDER BY created_at DESC LIMIT 1",
            str(intent_id), [RSV.SEND_ATTEMPTED, RSV.AMBIGUOUS])
    except Exception:                                          # noqa: BLE001
        return None
    return dict(r) if r else None


async def read_market(reads, intent: dict) -> dict:
    """WHAT THE VENUE SHOWS ON THIS REQUEST'S MARKET, as a record.

    Pure reporting: two reads, the term matches among the resting orders, and
    a digest of the whole picture so a caller can tell whether it changed.
    """
    slug = str(intent.get("us_market_slug") or "")
    sent = _sent_epoch(intent)
    out: dict[str, Any] = {"us_market_slug": slug, "sent_at_epoch_s": sent}
    rest = await _call(reads.resting_orders)
    if rest.get("ok"):
        orders = rest.get("orders") or []
        here = [o for o in orders
                if str(o.get("us_market_slug") or "").lower() == slug.lower()]
        # AN ORDER THAT NAMES NO MARKET cannot be placed off this one, so it
        # is reported apart and blocks an absence attestation like one on it.
        unplaced = [o for o in orders
                    if not str(o.get("us_market_slug") or "").strip()]
        out["resting"] = {"ok": True, "complete": True,
                          "orders_on_this_market": sorted(
                              str(o.get("order_id")) for o in here),
                          "orders_naming_no_market": sorted(
                              str(o.get("order_id")) for o in unplaced)}
    else:
        out["resting"] = {"ok": False, "complete": False,
                          "refusal": rest.get("refusal")}
    if sent is None:
        out["own_executions"] = {"ok": False, "refusal": R_SENT_AT_UNKNOWN}
    else:
        ex = await _call(reads.own_executions, slug,
                         sent - CLOCK_TOLERANCE_S)
        if ex.get("ok"):
            rows = ex.get("rows") or []
            out["own_executions"] = {
                "ok": True, "complete": True, "count": len(rows),
                "since_epoch_s": sent - CLOCK_TOLERANCE_S,
                "order_ids": sorted({str(r.get("own_order_id"))
                                     for r in rows
                                     if r.get("own_order_id")}),
                "rows_naming_no_own_order": sum(
                    1 for r in rows if not r.get("own_order_id"))}
        else:
            out["own_executions"] = {"ok": False, "complete": False,
                                     "refusal": ex.get("refusal")}
    out["sha"] = hashlib.sha256(json.dumps(
        {k: out[k] for k in ("resting", "own_executions")},
        sort_keys=True, default=str).encode()).hexdigest()
    return out


# ═════════════════════════════════════════════════════════════════════
# 3 · THE SCHEDULED INVESTIGATION
# ═════════════════════════════════════════════════════════════════════

def investigation_id_for(intent_id: str) -> str:
    return "inv:%s" % intent_id


async def _open(conn, intent: dict, *, operation_id: str | None) -> str:
    iid = investigation_id_for(intent["intent_id"])
    await conn.execute(
        "INSERT INTO bettor_funded_investigations "
        "(investigation_id, intent_id, operation_id, account_id, venue, "
        " us_market_slug, intent_kind, venue_intent, limit_price, quantity, "
        " sent_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) "
        "ON CONFLICT (intent_id) DO NOTHING",
        iid, intent["intent_id"], operation_id, intent["account_id"],
        intent["venue"], intent["us_market_slug"], intent["kind"],
        venue_intent_of(intent), intent["limit_price"], intent["quantity"],
        intent.get("sent_at"))
    return iid


async def _record_read_evidence(conn, *, reservation: dict, intent: dict,
                                picture: dict, at: float) -> dict:
    """A READ_ESTABLISHED_NOTHING row: what was asked and what came back,
    bound to the operation. It resolves nothing, by kind."""
    op = reservation["operation_id"]
    return await RSV.record_venue_evidence(
        conn, evidence_id="inv-read:%s:%s:%d" % (op, picture["sha"][:16],
                                                 int(at)),
        operation_id=op, account_id=intent["account_id"],
        venue=intent["venue"], us_market_slug=intent["us_market_slug"],
        kind=RSV.EV_ESTABLISHED_NOTHING,
        search_endpoint="orders.list + account activity (own fills)",
        read_at=at, covered_terminal_orders=False,
        intent_id=intent["intent_id"],
        search_scope={"resting_orders": "ALL_OPEN",
                      "own_executions_since_epoch_s":
                          (picture.get("own_executions") or {})
                          .get("since_epoch_s"),
                      "status": "OPEN_AND_OWN_FILLS_ONLY"},
        results_returned=len((picture.get("resting") or {})
                             .get("orders_on_this_market") or []),
        raw={"picture": picture,
             "why_it_resolves_nothing": (
                 "a resting order with our terms is a lead, not ownership, "
                 "and an empty picture is not the venue saying no order "
                 "exists")})


#: A picture read this recently is reused rather than read again, so the
#: servicing pass and the reservation recovery it runs do not read the venue
#: twice for one lost acknowledgement in one cycle.
REUSE_WITHIN_S = 60.0


async def refresh(conn, reads, intent: dict, *, at: float,
                  reuse_within_s: float | None = None) -> dict:
    """OPEN OR REFRESH ONE INVESTIGATION. Never touches the intent."""
    res = await _reservation_of(conn, intent["intent_id"])
    iid = await _open(conn, intent,
                      operation_id=(res or {}).get("operation_id"))
    prev = await conn.fetchrow(
        "SELECT state, last_read_sha, last_read_at, last_read "
        "  FROM bettor_funded_investigations WHERE investigation_id=$1", iid)
    if prev is None or prev["state"] != OPEN:
        return {"investigation_id": iid, "state": prev and prev["state"],
                "refreshed": False}
    if reuse_within_s is not None and prev["last_read_at"] is not None \
            and at - prev["last_read_at"].timestamp() <= reuse_within_s:
        last = prev["last_read"]
        last = json.loads(last) if isinstance(last, str) else (last or {})
        return {"investigation_id": iid, "intent_id": intent["intent_id"],
                "operation_id": (res or {}).get("operation_id"),
                "refreshed": False, "reused_read_at":
                    prev["last_read_at"].timestamp(),
                "resting_on_this_market": (last.get("resting") or {}).get(
                    "orders_on_this_market"),
                "own_executions_since_send": (last.get("own_executions")
                                              or {}).get("count"),
                "exposure": "PRESERVED"}
    picture = await read_market(reads, intent)
    changed = prev["last_read_sha"] != picture["sha"]
    await conn.execute(
        "UPDATE bettor_funded_investigations SET reads = reads + 1, "
        " last_read_at = to_timestamp($2), last_read_sha = $3, "
        " last_read = $4::jsonb WHERE investigation_id = $1 AND state='OPEN'",
        iid, float(at), picture["sha"], json.dumps(picture, default=str))
    ev = None
    if changed and res is not None:
        ev = await _record_read_evidence(conn, reservation=res, intent=intent,
                                         picture=picture, at=at)
    return {"investigation_id": iid, "intent_id": intent["intent_id"],
            "operation_id": (res or {}).get("operation_id"),
            "refreshed": True, "picture_changed": changed,
            "evidence": ev and {k: ev.get(k) for k in
                                ("ok", "evidence_id", "written", "refusal")},
            "resting_on_this_market": (picture.get("resting") or {}).get(
                "orders_on_this_market"),
            "own_executions_since_send": (picture.get("own_executions")
                                          or {}).get("count"),
            "exposure": "PRESERVED",
            "resolves_by": "POST /api/admin/funded-investigations/"
                           "{intent_id}/resolve"}


async def investigate_all(conn, *, account_id: str, venue: str, reads=None,
                          now: float | None = None) -> dict:
    """EVERY LOST ACKNOWLEDGEMENT ON THIS BOOK, investigated once per pass.

    Reads the venue only when there is something to investigate.
    """
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at, "open": [],
                           "changed_anything_at_the_venue": False,
                           "changed_any_intent": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_intents WHERE account_id=$1 AND venue=$2"
        " AND state='UNRESOLVED' AND venue_order_id IS NULL "
        " ORDER BY created_at", account_id, venue)]
    if not rows:
        return dict(out, ok=True, lost_acknowledgements=0)
    reads = reads or PmusReads()
    for r in rows:
        try:
            out["open"].append(await refresh(conn, reads, r, at=at))
        except Exception as exc:                            # noqa: BLE001
            out["open"].append({"intent_id": r["intent_id"],
                                "refreshed": False,
                                "error": "%s: %s" % (type(exc).__name__,
                                                     str(exc)[:200])})
    return dict(out, ok=True, lost_acknowledgements=len(rows))


def reservation_reader(reads=None):
    """THE `venue_reader` FOR `recover_reservations`: the same investigation,
    reached from the reservation. It records evidence of what was read and
    resolves nothing; the reservation stays AMBIGUOUS until `resolve`."""
    async def _reader(conn, res, *, at):
        if not await has_schema(conn):
            return {"ok": False, "refusal": R_SCHEMA}
        iid = res.get("intent_id")
        intent = await _intent(conn, iid) if iid else None
        if intent is None:
            return {"ok": False, "refusal": R_NO_SUCH_INTENT,
                    "why": "the reservation names no readable intent"}
        if not _is_lost_ack(intent):
            return {"ok": True, "investigated": False,
                    "intent_state": intent["state"],
                    "why": ("the intent is not a lost acknowledgement; its "
                            "reservation resolves from the venue's own "
                            "answer")}
        return dict(await refresh(conn, reads or PmusReads(), intent, at=at,
                                  reuse_within_s=REUSE_WITHIN_S),
                    ok=True, investigated=True)
    return _reader


# ═════════════════════════════════════════════════════════════════════
# 4 · THE OPERATOR'S RESOLUTION
# ═════════════════════════════════════════════════════════════════════

async def _next_audit_id(conn) -> int:
    """Reserve an audit id BEFORE the effects, so every effect can name the
    audit row and the row itself -- written last, in the same transaction --
    records what the effects actually were rather than a placeholder."""
    return int(await conn.fetchval(
        "SELECT nextval(pg_get_serial_sequence("
        "'bettor_funded_resolution_audit', 'audit_id'))"))


async def _audit(conn, *, intent_id, investigation_id, requested, outcome,
                 refusal, attested_by, statement, auth, reads, effect,
                 audit_id: int | None = None) -> int:
    if audit_id is None:
        audit_id = await _next_audit_id(conn)
    return int(await conn.fetchval(
        "INSERT INTO bettor_funded_resolution_audit "
        "(audit_id, intent_id, investigation_id, requested, outcome, refusal, "
        " attested_by, statement, authenticated_by, venue_reads, effect) "
        "VALUES ($11,$1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9::jsonb,$10::jsonb) "
        "RETURNING audit_id",
        str(intent_id), investigation_id,
        requested if requested in REQUESTS else REQ_UNRECOGNISED,
        outcome, refusal, attested_by, statement,
        json.dumps({k: auth.get(k) for k in AUTH_FIELDS}, default=str),
        json.dumps(reads or {}, default=str),
        json.dumps(effect or {}, default=str), int(audit_id)))


def _terms_disagree(intent: dict, order: dict) -> list:
    bad = []
    if str(order.get("us_market_slug") or "").lower() != \
            str(intent.get("us_market_slug") or "").lower():
        bad.append("us_market_slug")
    if str(order.get("intent") or "").upper() != venue_intent_of(intent):
        bad.append("order_intent")
    try:
        if abs(float(order.get("price")) - float(intent["limit_price"])) > 1e-9:
            bad.append("limit_price")
    except (TypeError, ValueError):
        bad.append("limit_price")
    try:
        if abs(float(order.get("quantity")) - float(intent["quantity"])) > 1e-9:
            bad.append("quantity")
    except (TypeError, ValueError):
        bad.append("quantity")
    return bad


async def _name_the_order(conn, reads, intent: dict, *, venue_order_id: str,
                          at: float, record: dict) -> dict:
    """Checks for NAME_THE_ORDER. Returns {ok, refusal?} and fills `record`."""
    claimed = await conn.fetchval(
        "SELECT intent_id FROM bettor_funded_intents "
        " WHERE venue_order_id=$1 AND intent_id<>$2",
        str(venue_order_id), intent["intent_id"])
    if claimed:
        return {"ok": False, "refusal": R_ORDER_CLAIMED,
                "why": "order %s is held by intent %s" % (venue_order_id,
                                                          claimed)}
    got = await _call(reads.order, str(venue_order_id))
    record["order_read"] = {k: v for k, v in got.items() if k != "order"}
    if not got.get("ok"):
        return {"ok": False, "refusal": got.get("refusal")
                or R_ORDER_UNREADABLE}
    order = got["order"]
    record["order"] = {k: order.get(k) for k in
                       ("order_id", "us_market_slug", "intent", "price",
                        "quantity", "filled_shares", "leaves", "state",
                        "created_at")}
    bad = _terms_disagree(intent, order)
    if bad:
        return {"ok": False, "refusal": R_ORDER_TERMS_DIFFER,
                "disagrees_on": bad}
    if str(order.get("state") or "").lower() == "rejected":
        return {"ok": False, "refusal": R_ORDER_WAS_REJECTED,
                "why": ("a rejected order holds nothing. If it is this "
                        "request's, resolve with NO_EXPOSURE_EXISTS, whose "
                        "reads will confirm nothing is working or executed")}
    made, sent = FB._epoch_of(order.get("created_at")), _sent_epoch(intent)
    if made is None or sent is None:
        return {"ok": False, "refusal": (R_ORDER_TIME_UNREADABLE
                                         if made is None
                                         else R_SENT_AT_UNKNOWN)}
    if not (sent - CLOCK_TOLERANCE_S <= made <= sent + NAMED_CREATED_WITHIN_S):
        return {"ok": False, "refusal": R_ORDER_OUTSIDE_WINDOW,
                "venue_created_at": made, "we_sent_at": sent}
    return {"ok": True, "order": order}


async def _no_exposure(conn, reads, intent: dict, *, at: float,
                       record: dict) -> dict:
    """Checks for NO_EXPOSURE_EXISTS."""
    sent = _sent_epoch(intent)
    if sent is None:
        return {"ok": False, "refusal": R_SENT_AT_UNKNOWN}
    if at - sent < SETTLE_S:
        return {"ok": False, "refusal": R_TOO_SOON,
                "seconds_since_send": round(at - sent, 1),
                "required": SETTLE_S}
    picture = await read_market(reads, intent)
    record["picture"] = picture
    rest, ex = picture.get("resting") or {}, picture.get("own_executions") or {}
    if not rest.get("ok"):
        return {"ok": False, "refusal": R_RESTING_UNREADABLE,
                "read_refusal": rest.get("refusal")}
    if rest.get("orders_on_this_market"):
        return {"ok": False, "refusal": R_RESTING_ON_MARKET,
                "orders": rest["orders_on_this_market"],
                "why": ("an order is working on this market. Name it if it "
                        "is ours; if it is a manual order, it must be "
                        "cancelled or filled before absence can be attested")}
    if rest.get("orders_naming_no_market"):
        return {"ok": False, "refusal": R_RESTING_UNPLACED,
                "orders": rest["orders_naming_no_market"],
                "why": ("a working order names no market, so it cannot be "
                        "shown not to be on this one")}
    if not ex.get("ok"):
        return {"ok": False, "refusal": R_EXECUTIONS_UNREADABLE,
                "read_refusal": ex.get("refusal")}
    # EXECUTIONS OF AN ORDER ANOTHER INTENT ALREADY OWNS are that intent's,
    # booked there; they say nothing about this request. Anything else --
    # an order no intent holds, or a row naming no order -- may be this one.
    claimed = {str(r["venue_order_id"]) for r in await conn.fetch(
        "SELECT venue_order_id FROM bettor_funded_intents "
        " WHERE venue_order_id IS NOT NULL AND intent_id <> $1",
        intent["intent_id"])}
    unexplained = [o for o in ex.get("order_ids") or [] if o not in claimed]
    if unexplained or int(ex.get("rows_naming_no_own_order") or 0) > 0:
        return {"ok": False, "refusal": R_EXECUTED_SINCE_SEND,
                "execution_order_ids": unexplained,
                "rows_naming_no_own_order": ex.get("rows_naming_no_own_order"),
                "explained_by_other_intents": sorted(
                    set(ex.get("order_ids") or []) & claimed),
                "why": ("the account traded on this market after the request "
                        "left, on an order no other intent holds. That may be "
                        "this request; name the order")}
    record["executions_explained_by_other_intents"] = sorted(
        set(ex.get("order_ids") or []) & claimed)
    return {"ok": True, "picture": picture}


def _validate(body: dict, intent_id: str, auth: dict) -> str | None:
    if body.get("request") not in REQUESTS:
        return R_UNKNOWN_REQUEST
    if str(body.get("confirm") or "") != str(intent_id):
        return R_CONFIRM
    if not str(body.get("attested_by") or "").strip():
        return R_ATTESTER
    # THE NAME ON THE ATTESTATION IS THE ONE THE KEY AUTHENTICATES. A typed
    # name is a claim; the configured operator is who proved possession.
    if str(body.get("attested_by")).strip().casefold() != \
            str(auth.get("operator") or "").strip().casefold():
        return R_ATTESTER_NOT_AUTHENTICATED
    if len(str(body.get("statement") or "").strip()) < 20:
        return R_STATEMENT
    if body["request"] == REQ_NAME_THE_ORDER \
            and not str(body.get("venue_order_id") or "").strip():
        return R_ORDER_ID
    return None


async def resolve(conn, *, intent_id: str, body: dict, auth: dict,
                  reads=None, now: float | None = None,
                  _trace: dict | None = None) -> dict:
    """RESOLVE ONE LOST ACKNOWLEDGEMENT, or refuse by name. Always audited.

    `auth` is the route's statement of how the caller authenticated
    (AUTH_FIELDS). Both factors must be verified or nothing is read.
    """
    at = float(now if now is not None else time.time())
    reads = reads or PmusReads()
    request = body.get("request")
    attested_by = str(body.get("attested_by") or "").strip()[:200] or None
    statement = str(body.get("statement") or "").strip()[:2000] or None
    out: dict[str, Any] = {"version": VERSION, "intent_id": str(intent_id),
                           "request": request, "at": at,
                           "submitted_anything": False}
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    if not (auth.get("admin_token_verified")
            and auth.get("resolution_key_verified")
            and str(auth.get("operator") or "").strip()):
        # The route refuses before calling; this is the second statement of it.
        return dict(out, ok=False, refusal="BOTH_FACTORS_ARE_REQUIRED")
    record: dict[str, Any] = {}
    if _trace is not None:
        _trace["record"] = record       # what was read, for a rolled-back audit
    refusal = _validate(body, intent_id, auth)
    investigation_id = investigation_id_for(intent_id)
    async with conn.transaction():
        intent = await _intent(conn, intent_id, lock=True)
        if refusal is None and intent is None:
            refusal = R_NO_SUCH_INTENT
        if refusal is None:
            inv = await conn.fetchrow(
                "SELECT * FROM bettor_funded_investigations "
                " WHERE intent_id=$1 FOR UPDATE", str(intent_id))
            if inv is not None and inv["state"] != OPEN:
                # ALREADY DECIDED. The SAME decision retried is idempotent and
                # audited as such; a DIFFERENT one is refused and audited --
                # it must not be reported as though it had been applied.
                prior = inv["resolution"]
                prior = json.loads(prior) if isinstance(prior, str) \
                    else (prior or {})
                same = (prior.get("request") == request and (
                    request != REQ_NAME_THE_ORDER
                    or str(prior.get("venue_order_id"))
                    == str(body.get("venue_order_id") or "").strip()))
                aid = await _audit(
                    conn, intent_id=intent_id,
                    investigation_id=inv["investigation_id"],
                    requested=request,
                    outcome="ALREADY_RESOLVED" if same else "REFUSED",
                    refusal=None if same else R_RESOLVED_DIFFERENTLY,
                    attested_by=attested_by, statement=statement, auth=auth,
                    reads={}, effect={"state": inv["state"],
                                      "prior_audit_id":
                                          prior.get("audit_id")})
                if not same:
                    return dict(out, ok=False,
                                refusal=R_RESOLVED_DIFFERENTLY,
                                audit_id=aid,
                                investigation_state=inv["state"],
                                resolution=prior)
                return dict(out, ok=True, already=True, audit_id=aid,
                            investigation_state=inv["state"],
                            resolution=prior)
            if not _is_lost_ack(intent):
                refusal = R_NOT_A_LOST_ACK
                record["intent_state"] = intent["state"]
            # ── THE DECISION CITES THE READ IT WAS MADE ON ──────────
            #
            # The operator decides from what the investigation last read at
            # the venue. If the scheduled pass has read something different
            # since, the decision was made on superseded evidence and is
            # refused; they look again.
            elif inv is None or not inv["last_read_sha"]:
                refusal = R_NOT_YET_INVESTIGATED
            elif not str(body.get("seen_read_sha") or "").strip():
                refusal = R_EVIDENCE_NOT_REFERENCED
            elif str(body["seen_read_sha"]).strip() != inv["last_read_sha"]:
                refusal = R_STALE_EVIDENCE
                record["current_read_sha"] = inv["last_read_sha"]
        if refusal is None:
            res = await _reservation_of(conn, intent_id)
            investigation_id = await _open(
                conn, intent, operation_id=(res or {}).get("operation_id"))
            if request == REQ_NAME_THE_ORDER:
                chk = await _name_the_order(
                    conn, reads, intent,
                    venue_order_id=str(body["venue_order_id"]).strip(),
                    at=at, record=record)
            else:
                chk = await _no_exposure(conn, reads, intent, at=at,
                                         record=record)
            if not chk.get("ok"):
                refusal = chk["refusal"]
                record["check"] = {k: v for k, v in chk.items()
                                   if k not in ("order", "picture")}
        if refusal is not None:
            aid = await _audit(
                conn, intent_id=intent_id,
                investigation_id=(investigation_id if intent else None),
                requested=request,
                outcome="REFUSED", refusal=refusal, attested_by=attested_by,
                statement=statement, auth=auth, reads=record, effect={})
            return dict(out, ok=False, refusal=refusal, audit_id=aid,
                        exposure="PRESERVED",
                        detail=record.get("check") or {})
        # ── ACCEPTED: the audit id first, so every effect can name it; the
        # audit ROW last, in this transaction, with the effects it caused ──
        aid = await _next_audit_id(conn)
        if request == REQ_NAME_THE_ORDER:
            effect = await _apply_named(conn, intent, chk["order"], res=res,
                                        audit_id=aid, attested_by=attested_by,
                                        at=at)
            state = RESOLVED_ORDER_NAMED
        else:
            effect = await _apply_no_exposure(
                conn, intent, chk["picture"], res=res, audit_id=aid,
                attested_by=attested_by, statement=statement, at=at)
            state = RESOLVED_NO_EXPOSURE
        if not effect.get("ok"):
            # Nothing partial survives: raising rolls back every effect, and
            # the refusal -- with what was read -- is audited outside it.
            raise _Rollback(effect)
        await _audit(
            conn, intent_id=intent_id, investigation_id=investigation_id,
            requested=request, outcome="ACCEPTED", refusal=None,
            attested_by=attested_by, statement=statement, auth=auth,
            reads=record, effect=effect, audit_id=aid)
        await conn.execute(
            "UPDATE bettor_funded_investigations SET state=$2, "
            " resolved_at=to_timestamp($3), resolution=$4::jsonb "
            " WHERE investigation_id=$1 AND state='OPEN'",
            investigation_id, state, float(at),
            json.dumps(dict(effect, audit_id=aid, request=request,
                            attested_by=attested_by,
                            authenticated_operator=auth.get("operator"),
                            seen_read_sha=str(body.get("seen_read_sha")),
                            attestation=ATTESTATION_TEXT[request]),
                       default=str))
    return dict(out, ok=True, audit_id=aid, investigation_state=state,
                effect=effect, attestation=ATTESTATION_TEXT[request])


class _Rollback(Exception):
    def __init__(self, effect):
        super().__init__(effect.get("refusal"))
        self.effect = effect


async def resolve_audited(conn, *, intent_id: str, body: dict, auth: dict,
                          reads=None, now: float | None = None) -> dict:
    """`resolve`, with an effect that failed half-way rolled back AND audited."""
    trace: dict = {}
    try:
        return await resolve(conn, intent_id=intent_id, body=body, auth=auth,
                             reads=reads, now=now, _trace=trace)
    except Exception as exc:                                # noqa: BLE001
        # EVERY PATH IS AUDITED, including one that raised. The transaction
        # rolled back, so nothing was applied; the audit row says so.
        rb = exc if isinstance(exc, _Rollback) else _Rollback(
            {"refusal": R_RAISED,
             "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])})
        if not await has_schema(conn):
            raise
        known = await conn.fetchval(
            "SELECT 1 FROM bettor_funded_investigations WHERE intent_id=$1",
            str(intent_id))
        aid = await _audit(
            conn, intent_id=intent_id,
            investigation_id=investigation_id_for(intent_id) if known else None,
            requested=body.get("request"),
            outcome="REFUSED",
            refusal=rb.effect.get("refusal") or R_RESERVATION_NOT_MOVED,
            attested_by=str(body.get("attested_by") or "")[:200] or None,
            statement=str(body.get("statement") or "")[:2000] or None,
            auth=auth, reads=trace.get("record") or {},
            effect={"rolled_back": rb.effect})
        return {"version": VERSION, "intent_id": str(intent_id), "ok": False,
                "refusal": rb.effect.get("refusal") or R_RESERVATION_NOT_MOVED,
                "audit_id": aid, "exposure": "PRESERVED",
                "rolled_back": True, "detail": rb.effect,
                "submitted_anything": False}


async def _apply_named(conn, intent: dict, order: dict, *, res, audit_id: int,
                       attested_by: str, at: float) -> dict:
    iid, vid = intent["intent_id"], str(order.get("order_id"))
    ack = await FB.record_acknowledgement(
        conn, iid, venue_order_id=vid, status=order.get("state") or "open",
        raw={"named_by_operator": True, "audit_id": audit_id,
             "is_a_venue_acknowledgement": False,
             "attested_by": attested_by,
             "venue_order": {k: order.get(k) for k in
                             ("order_id", "us_market_slug", "intent", "price",
                              "quantity", "state", "created_at")}})
    if ack.get("state") != "ACKNOWLEDGED":
        return {"ok": False, "refusal": R_CHANGED_UNDER_US, "ack": ack}
    read = FB.executions_of(order)
    direction = "EXIT" if str(intent["kind"]) == "EXIT" else "ENTRY"
    ing = await FB.ingest_fills(conn, iid, read["executions"], at=at,
                                direction=direction)
    effect: dict[str, Any] = {
        "ok": True, "venue_order_id": vid, "direction": direction,
        "executions_booked": len(ing.get("written") or []),
        "executions_skipped": read.get("skipped"),
        "residual_qty": ing.get("residual_qty")}
    if order.get("executions") is None and float(
            order.get("filled_shares") or 0) > 0:
        # THE VENUE REPORTS FILLS AND SENT NO LIST. The order is ours, and the
        # quantity is not placeable: the intent stays UNRESOLVED on that
        # ground, which the next recovery pass reads from the venue by id.
        await FB.mark_unresolved(
            conn, iid, "operator named order %s; the venue reports %s filled "
                       "and sent no executions list" % (
                           vid, order.get("filled_shares")))
        effect["intent_state"] = "UNRESOLVED"
        effect["why"] = "FILLED_SHARES_WITH_NO_EXECUTIONS"
    else:
        term = await FB.record_venue_terminal(
            conn, iid, venue_state=order.get("state"),
            venue_filled=float(order.get("filled_shares") or 0.0),
            leaves=float(order.get("leaves") or 0.0),
            ledger_filled=float(ing.get("filled_qty_from_the_ledger") or 0.0))
        effect["terminal"] = term
    if res is not None:
        op = res["operation_id"]
        if res["state"] == RSV.SEND_ATTEMPTED:
            await RSV.mark_ambiguous(conn, operation_id=op,
                                     why="resolved by an operator")
        # THE OPERATOR'S NAMING, under its own kind: the venue returned the
        # order and its terms were checked, but "it is this request's" is the
        # operator's statement, never recorded as the venue's.
        ev = await RSV.record_venue_evidence(
            conn, evidence_id="inv-named:%s:%s" % (op, audit_id),
            operation_id=op, account_id=intent["account_id"],
            venue=intent["venue"], us_market_slug=intent["us_market_slug"],
            kind=RSV.EV_OPERATOR_NAMED, search_endpoint="orders.retrieve",
            read_at=at, covered_terminal_orders=True, intent_id=iid,
            venue_order_id=vid,
            search_scope={"named_by": "OPERATOR", "audit_id": audit_id,
                          "terms_checked": list(FB.CORRELATION_TERMS),
                          "created_within_s": [-CLOCK_TOLERANCE_S,
                                               NAMED_CREATED_WITHIN_S]},
            results_returned=1,
            raw={"attested_by": attested_by, "audit_id": str(audit_id)})
        if not ev.get("ok"):
            return {"ok": False, "refusal": R_RESERVATION_NOT_MOVED,
                    "evidence": ev}
        moved = await RSV.consume_on_operator_naming(conn, operation_id=op,
                                                     venue_order_id=vid)
        if not moved.get("ok"):
            return {"ok": False, "refusal": R_RESERVATION_NOT_MOVED,
                    "reservation": moved}
        effect["reservation"] = {"operation_id": op, "state": RSV.CONSUMED}
    effect["intent_state"] = await conn.fetchval(
        "SELECT state FROM bettor_funded_intents WHERE intent_id=$1", iid)
    return effect


async def _apply_no_exposure(conn, intent: dict, picture: dict, *, res,
                             audit_id: int, attested_by: str, statement: str,
                             at: float) -> dict:
    iid = intent["intent_id"]
    status = await conn.execute(
        "UPDATE bettor_funded_intents SET state='ABANDONED', "
        "  unresolved_reason=$2, resolved_at=to_timestamp($3), "
        "  updated_at=now() "
        " WHERE intent_id=$1 AND state='UNRESOLVED' "
        "   AND venue_order_id IS NULL",
        iid, ("closed on audited operator attestation %s: no resting order "
              "and no execution on the market since the send" % audit_id)[:500],
        float(at))
    if not str(status).endswith(" 1"):
        return {"ok": False, "refusal": R_CHANGED_UNDER_US}
    effect: dict[str, Any] = {"ok": True, "intent_state": "ABANDONED",
                              "exposure": "RELEASED_ON_ATTESTATION"}
    if res is not None:
        op = res["operation_id"]
        if res["state"] == RSV.SEND_ATTEMPTED:
            await RSV.mark_ambiguous(conn, operation_id=op,
                                     why="resolved by an operator")
        ev = await RSV.record_venue_evidence(
            conn, evidence_id="inv-attested:%s:%s" % (op, audit_id),
            operation_id=op, account_id=intent["account_id"],
            venue=intent["venue"], us_market_slug=intent["us_market_slug"],
            kind=RSV.EV_ATTESTED_NO_EXPOSURE,
            search_endpoint="orders.list + account activity (own fills)",
            read_at=at, covered_terminal_orders=False, intent_id=iid,
            search_scope={"resting_orders": "ALL_OPEN",
                          "own_executions_since_epoch_s":
                              (picture.get("own_executions") or {})
                              .get("since_epoch_s"),
                          "settle_s": SETTLE_S},
            results_returned=0,
            raw={"attested_by": attested_by, "audit_id": str(audit_id),
                 "statement": statement, "picture": picture,
                 "attestation": ATTESTATION_TEXT[REQ_NO_EXPOSURE_EXISTS]})
        if not ev.get("ok"):
            return {"ok": False, "refusal": R_RESERVATION_NOT_MOVED,
                    "evidence": ev}
        moved = await RSV.release_on_attestation(conn, operation_id=op)
        if not moved.get("ok"):
            return {"ok": False, "refusal": R_RESERVATION_NOT_MOVED,
                    "reservation": moved}
        effect["reservation"] = {"operation_id": op, "state": RSV.RELEASED}
    effect["group_release"] = await FB.release_group_if_earned(conn, iid)
    return effect


async def listing(conn, *, state: str | None = None, limit: int = 50) -> dict:
    """What an operator sees before deciding: every investigation and the
    audit trail of attempts on it."""
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_SCHEMA}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT * FROM bettor_funded_investigations "
        " WHERE ($1::text IS NULL OR state=$1) "
        " ORDER BY opened_at DESC LIMIT $2", state, int(limit))]
    for r in rows:
        r["attempts"] = [dict(a) for a in await conn.fetch(
            "SELECT audit_id, at, requested, outcome, refusal, attested_by "
            "  FROM bettor_funded_resolution_audit WHERE intent_id=$1 "
            " ORDER BY audit_id", r["intent_id"])]
    return {"ok": True, "version": VERSION, "investigations": rows,
            "resolutions_offered": list(REQUESTS),
            "attestations": ATTESTATION_TEXT,
            "settle_s": SETTLE_S,
            "never": ("adopts a term-matched order on its own, resends, "
                      "or submits anything")}
