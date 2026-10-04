"""XAVIER'S STANDING PROTECTIVE ORDERS, READ -- for the workspace and Audrey.

READ-ONLY. Every figure is read from the authoritative records: the funded
book (`bettor_funded_intents` / `_fills`) for what is held, resting and
filled, and migration 157's records (`bettor_standing_order_plans`,
`bettor_hedge_group_selection`, `bettor_standing_order_events`,
`bettor_standing_capacity_reservations`) for the plan, the selected
instrument, the lifecycle and the capacity held. Nothing is recomputed that
the servicing pass did not record, nothing is written, and no venue client or
order path is reached.

WHAT IT SHOWS, PER GROUP, AND KEEPS APART:
  * FILLED PROTECTION -- hedge contracts the book holds (covered / uncovered
    against the CONFIRMED primary);
  * RESTING ORDERS -- orders that may still fill: an obligation, not
    protection, with their fill-capable quantity and lifecycle state;
  * the selected instrument (the first fill's), the floor class of the plan,
    the invariant (hedge held + fill-capable <= confirmed primary), the mode
    (STRICT_FALLBACK) and the venue capability flag
    (EXCHANGE_LINKED_EXCLUSIVITY = UNAVAILABLE_OR_UNVERIFIED), and the
    latency / queue-position trade-off the strict fallback accepts.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .. import bettor_xavier_standing_orders as SPO
from .. import order_state_truth as OST

VERSION = "XAVIER_STANDING_ORDER_VIEW_V1"
EVENTS_SHOWN = 40


def _epoch(v):
    if v is None:
        return None
    if hasattr(v, "timestamp"):
        return v.timestamp()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _jsonable(d: dict) -> dict:
    return json.loads(json.dumps(d, default=lambda v: _epoch(v)
                                 if hasattr(v, "timestamp") else str(v)))


def capability() -> dict:
    return {"mode": SPO.MODE_STRICT_FALLBACK,
            "exchange_linked_exclusivity": SPO.EXCHANGE_LINKED_EXCLUSIVITY,
            "venue_capability": SPO.VENUE_CAPABILITY,
            "why_one_live_order": SPO.WHY_ONE_LIVE_ORDER,
            "latency_disclosure": SPO.LATENCY_DISCLOSURE,
            "process_death_disclosure": SPO.PROCESS_DEATH_DISCLOSURE}


async def group_view(conn, *, group_id: str, primary_intent_id: str | None
                     = None, events_shown: int = EVENTS_SHOWN) -> dict:
    """ONE GROUP'S STANDING PROTECTION, READ."""
    if not await SPO.has_schema(conn):
        return {"available": False, "why": SPO.R_SCHEMA}
    gs = await SPO.group_state(conn, group_id=group_id,
                               primary_intent_id=primary_intent_id)
    plan = gs["plans"][-1] if gs["plans"] else None
    sel = gs.get("selection")
    evs = await SPO.events(conn, group_id=group_id, limit=2000)
    reserved = gs.get("reserved") or []
    out = {
        "available": True, "group_id": group_id,
        "primary": gs.get("primary"),
        "selected_instrument": None if sel is None else {
            "candidate_id": sel.get("candidate_id"),
            "selected_by": sel.get("selected_by"),
            "first_fill_id": sel.get("first_fill_id"),
            "hedge_intent_id": sel.get("hedge_intent_id"),
            "selected_at": _epoch(sel.get("selected_at"))},
        "confirmed_primary_qty": gs["confirmed_primary_qty"],
        "filled_protection": {
            "covered_qty": gs["covered_qty"],
            "uncovered_qty": gs["uncovered_qty"],
            "hedge_held_qty": gs["hedge_held_qty"],
            "legs": gs["filled_protection"],
            "is": "HEDGE CONTRACTS THE BOOK HOLDS"},
        "resting_orders": [dict({k: o.get(k) for k in (
            "intent_id", "plan_id", "candidate_id", "venue_order_id",
            "limit_price", "quantity", "filled_qty", "fill_capable_qty",
            "book_state", "lifecycle_state", "good_till")},
            **_canonical(o)) for o in gs["resting_orders"]],
        "resting_orders_are": ("OBLIGATIONS THAT MAY STILL FILL, NOT "
                               "PROTECTION"),
        "fill_capable_qty": gs["fill_capable_qty"],
        "live_or_potentially_live_orders":
            gs["live_or_potentially_live_orders"],
        "lifecycle_state": gs["lifecycle_state"],
        "invariant": gs["invariant"],
        "floor_class": None if plan is None else plan.get("floor_class"),
        "plan": None if plan is None else {
            "plan_id": plan.get("plan_id"),
            "candidate_id": plan.get("candidate_id"),
            "wire_limit_price": float(plan["wire_limit_price"]),
            "cost_price": float(plan["cost_price"]),
            "quantity": int(plan["quantity"]),
            "tif": plan.get("tif"),
            "good_till_time": _epoch(plan.get("good_till_time")),
            "floor_class": plan.get("floor_class"),
            "floor_condition": (SPO._obj(plan.get("floor")) or {}).get(
                "condition"),
            "fee_schedule_identity": plan.get("fee_schedule_identity"),
            "settlement_identity": plan.get("settlement_identity"),
            "xavier_decision_id": plan.get("xavier_decision_id"),
            "policy_version": plan.get("policy_version")},
        "plans": len(gs["plans"]),
        "capacity_reserved": [{k: (_epoch(r.get(k)) if k.endswith("_at")
                                   else r.get(k)) for k in (
            "reservation_id", "plan_id", "hedge_intent_id", "reserved_qty",
            "reserved_collateral_usd", "state", "opened_at")}
            for r in reserved],
        "capacity_released": [{k: (_epoch(r.get(k)) if k.endswith("_at")
                                   else r.get(k)) for k in (
            "reservation_id", "plan_id", "release_reason", "released_at")}
            for r in gs["reservations"] if r["state"] == "RELEASED"],
        "events": [{"event_id": e["event_id"], "kind": e["event_kind"],
                    "source": e["source"], "at": _epoch(e["occurred_at"]),
                    "lifecycle_state": e.get("lifecycle_state"),
                    "filled_qty": e.get("filled_qty"),
                    "fill_capable_qty": e.get("fill_capable_qty"),
                    "hedge_intent_id": e.get("hedge_intent_id")}
                   for e in evs[-int(events_shown):]],
        "event_counts": _count([e["event_kind"] for e in evs]),
        **capability()}
    cov = next((e for e in reversed(evs)
                if e["event_kind"] == "COVERAGE_RECORDED"), None)
    out["payout_table_actual_inventory"] = (
        None if cov is None else cov["evidence"].get(
            "payout_table_actual_inventory"))
    return _jsonable(out)


def _canonical(o: dict) -> dict:
    """The resting order's canonical state from the ONE shared mapping
    (order_state_truth): its unfilled remainder is a standing obligation,
    never protection; only its filled quantity counts."""
    t = OST.order_state(o.get("book_state"), source=OST.SRC_FUNDED,
                        qty=o.get("quantity"), filled_qty=o.get("filled_qty"))
    return {"state": t["state"], "sub_state": t["sub_state"],
            "counts_as_filled_qty": t["filled_qty"],
            "standing_qty": t["standing_qty"],
            "pending_qty": t["pending_qty"],
            "line": OST.order_line({
                "raw_state": o.get("book_state"), "source": OST.SRC_FUNDED,
                "direction": "BUY", "qty": o.get("quantity"),
                "filled_qty": o.get("filled_qty"),
                "limit": o.get("limit_price")})}


def _count(xs) -> dict:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


async def account_view(conn, *, account_id: str | None = None,
                       venue: str | None = None, limit: int = 50) -> dict:
    """EVERY GROUP WITH A STANDING ORDER PLAN, newest first."""
    if not await SPO.has_schema(conn):
        return {"ok": False, "why": SPO.R_SCHEMA, "groups": [],
                **capability()}
    args: list = []
    where = []
    if account_id:
        args.append(account_id)
        where.append("account_id=$%d" % len(args))
    if venue:
        args.append(venue)
        where.append("upper(venue)=upper($%d)" % len(args))
    args.append(int(limit))
    rows = await conn.fetch(
        "SELECT group_id, primary_intent_id, max(created_at) AS at "
        "  FROM bettor_standing_order_plans "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "GROUP BY group_id, primary_intent_id ORDER BY at DESC LIMIT $%d"
        % len(args), *args)
    groups = [await group_view(conn, group_id=r["group_id"],
                               primary_intent_id=r["primary_intent_id"])
              for r in rows]
    return {"ok": True, "groups": groups, "read_at": time.time(),
            **capability()}


async def audit(conn, *, start: float, end: float) -> dict:
    """AUDREY'S CHECKS OF THE STANDING ORDERS ACTIVE IN A WINDOW. Read-only.

    For every group with a standing-order event in [start, end):
      * the invariant NOW (hedge held + fill-capable <= confirmed primary);
      * how many live or potentially-live hedge orders the group holds NOW
        (strict fallback: at most one);
      * every capacity release names a confirmed terminal state (or a send
        that never happened) -- the database refuses anything else, and this
        re-reads it;
      * the plans' floor classes, mode and capability flag;
      * fills, selections, cancels, invalidations, exits held back and
        placements refused in the window."""
    out: dict[str, Any] = {"version": VERSION, "window": [start, end],
                           "groups": [], "findings": [], **capability()}
    if not await SPO.has_schema(conn):
        return dict(out, available=False, why=SPO.R_SCHEMA)
    rows = await conn.fetch(
        "SELECT DISTINCT group_id FROM bettor_standing_order_events "
        " WHERE occurred_at >= to_timestamp($1) "
        "   AND occurred_at < to_timestamp($2) ORDER BY group_id",
        float(start), float(end))
    for r in rows:
        gid = r["group_id"]
        v = await group_view(conn, group_id=gid)
        kinds = v.get("event_counts") or {}
        bad_release = await conn.fetch(
            "SELECT r.reservation_id, r.release_reason, i.state "
            "  FROM bettor_standing_capacity_reservations r "
            "  LEFT JOIN bettor_funded_intents i "
            "    ON i.intent_id=r.hedge_intent_id "
            " WHERE r.group_id=$1 AND r.state='RELEASED' "
            "   AND ((r.release_reason='TERMINAL_CONFIRMED_BY_THE_VENUE' "
            "         AND (i.state IS NULL OR "
            "              bettor_funded_order_is_outstanding(i.state))) "
            "     OR (r.release_reason='NEVER_SENT' AND i.state IS NOT NULL "
            "         AND i.state NOT IN ('ABANDONED','REJECTED')))", gid)
        g = {"group_id": gid,
             "invariant_holds_now": (v.get("invariant") or {}).get("holds"),
             "invariant": v.get("invariant"),
             "live_or_potentially_live_orders_now":
                 v.get("live_or_potentially_live_orders"),
             "at_most_one_live_order": (v.get(
                 "live_or_potentially_live_orders") or 0) <= 1,
             "releases_only_on_terminal": not bad_release,
             "selected_instrument": v.get("selected_instrument"),
             "covered_qty": (v.get("filled_protection") or {}).get(
                 "covered_qty"),
             "uncovered_qty": (v.get("filled_protection") or {}).get(
                 "uncovered_qty"),
             "resting_orders": v.get("resting_orders"),
             "fill_capable_qty": v.get("fill_capable_qty"),
             "lifecycle_state": v.get("lifecycle_state"),
             "floor_class": v.get("floor_class"),
             "mode": v.get("mode"),
             "exchange_linked_exclusivity": v.get(
                 "exchange_linked_exclusivity"),
             "event_counts": kinds}
        out["groups"].append(g)
        for check, ok in (("INVARIANT", g["invariant_holds_now"]),
                          ("AT_MOST_ONE_LIVE_HEDGE_ORDER",
                           g["at_most_one_live_order"]),
                          ("CAPACITY_RELEASED_ONLY_ON_TERMINAL",
                           g["releases_only_on_terminal"])):
            if ok is False:
                out["findings"].append({"group_id": gid, "check": check,
                                        "severity": "BREACH"})
    out["available"] = True
    out["groups_examined"] = len(out["groups"])
    return out
