"""THE EXIT THAT CANCELLED ITS OWN PROTECTION, PERSISTED (migration 313).

An EXIT / REDUCE ranked on a COMPLETE packet first cancels the resting
protection (its inventory is committed; never two potentially live sells).
Before this module the continuation was inferred from "the group's latest
review": any review in between (a FRESHNESS_EXPIRY review 25 s later, while
the cancel was still pending) broke the chain, and the review that saw the
cancel terminal simply re-protected -- the EXIT became a HOLD with no record
(production 2026-10-07 05:11:31, 05:59:42, 05:59:43).

Now the intent is a row with explicit transitions:

  CANCEL_REQUESTED --(every cancelled order terminal)--> CANCEL_TERMINAL
  CANCEL_TERMINAL  --(fresh probability + fresh book, EXIT still selected,
                      order created)--> EXIT_SUBMITTED
  any open state   --> ABANDONED (resolution named; protection restored by
                       the same review whenever inventory remains)

Rules (none widens a freshness limit):
  * the deciding review's probability is NEVER reused: after the cancel is
    terminal the review must hold a fresh probability (30 s rule) and a
    fresh executable exit walk (300 s rule) of its own;
  * the position is never left unprotected past `revalidate_by` (terminal
    instant + the 30 s probability limit): no fresh evidence by then ->
    ABANDONED_NO_FRESH_PROBABILITY_WITHIN_WINDOW and protection restored;
  * a cancel not confirmed by `cancel_deadline_at` -> abandoned; the order
    still pending cancel remains the potentially-live protection.

Paper ledger only: NO venue order, cancel, funding or capital authority."""
from __future__ import annotations

import hashlib
import json

from .. import bettor_paper_ledger as L

S_CANCEL_REQUESTED = "CANCEL_REQUESTED"
S_CANCEL_TERMINAL = "CANCEL_TERMINAL"
S_EXIT_SUBMITTED = "EXIT_SUBMITTED"
S_ABANDONED = "ABANDONED"
OPEN = (S_CANCEL_REQUESTED, S_CANCEL_TERMINAL)

#: a paper cancel is confirmed on the simulator's next step (about a minute
#: in production); past this the intent is abandoned, never waited on forever
CANCEL_CONFIRM_DEADLINE_S = 300.0

R_EXIT_ORDER_CREATED = "EXIT_ORDER_CREATED"
R_NOT_EXIT = "ABANDONED_REFRESHED_SELECTION_NOT_EXIT"
R_NO_FRESH = "ABANDONED_NO_FRESH_PROBABILITY_WITHIN_WINDOW"
R_NO_DEPTH = "ABANDONED_NO_EXECUTABLE_EXIT_DEPTH"
R_PACKET = "ABANDONED_PACKET_INCOMPLETE_AT_REVALIDATION"
R_EXIT_REFUSED = "ABANDONED_EXIT_ORDER_REFUSED"
R_CANCEL_UNCONFIRMED = "ABANDONED_CANCEL_NOT_CONFIRMED_BY_DEADLINE"
R_CANCEL_REFUSED = "ABANDONED_CANCEL_REQUEST_REFUSED"
R_PROTECTION_FILLED = "ABANDONED_PROTECTION_FILLED_BEFORE_CANCEL"
R_PROTECTION_PRESENT = "ABANDONED_PROTECTION_ALREADY_PRESENT"
R_POSITION_CLOSED = "ABANDONED_POSITION_CLOSED"
RESOLUTIONS = (R_EXIT_ORDER_CREATED, R_NOT_EXIT, R_NO_FRESH, R_NO_DEPTH,
               R_PACKET, R_EXIT_REFUSED, R_CANCEL_UNCONFIRMED,
               R_CANCEL_REFUSED, R_PROTECTION_FILLED, R_PROTECTION_PRESENT,
               R_POSITION_CLOSED)

TERMINAL_EVENTS = ("CANCELED", "EXPIRED", "REJECTED", "FILLED")

PH_WAIT_CANCEL = "WAIT_FOR_CANCEL_TERMINAL"
PH_REVALIDATE = "REVALIDATE_AFTER_TERMINAL_CANCEL"
PH_RESOLVED = "RESOLVED"


def _id(*parts) -> str:
    return "paperexit:" + hashlib.sha256(
        "|".join(str(p) for p in parts).encode()).hexdigest()[:24]


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("decided_at", "cancel_requested_at", "cancel_deadline_at",
              "cancel_terminal_at", "revalidate_by", "resolved_at",
              "updated_at", "decided_probability_source_at"):
        if d.get(k) is not None:
            d[k] = L._epoch(d[k])
    d["transitions"] = L._j(d.get("transitions")) or []
    d["cancel_orders"] = list(d.get("cancel_orders") or [])
    return d


async def load_open(conn, account_id: str, group_id: str,
                    position_key: str) -> dict | None:
    return _row(await conn.fetchrow(
        "SELECT * FROM paper_exit_intents WHERE account_id=$1 AND "
        " group_id=$2 AND position_key=$3 AND state = ANY($4::text[])",
        account_id, group_id, position_key, list(OPEN)))


async def record(conn, *, account_id: str, pos: dict, review_id: str,
                 selection: str, at: float, probability_source_at,
                 orders: list, cancel_results: dict) -> dict:
    """THE INTENT, PERSISTED with the cancel request (idempotent: one open
    intent per position; an existing one is returned unchanged)."""
    cur = await load_open(conn, account_id, pos["group_id"],
                          pos["position_key"])
    if cur is not None:
        return cur
    iid = _id(account_id, pos["group_id"], pos["position_key"], review_id)
    tr = [{"at": at, "to": S_CANCEL_REQUESTED, "review_id": review_id,
           "orders": orders, "cancel_results": cancel_results}]
    await conn.execute(
        "INSERT INTO paper_exit_intents (intent_id, account_id, group_id, "
        " position_key, us_market_slug, holding_side, decided_review_id, "
        " selection, decided_at, decided_probability_source_at, "
        " cancel_orders, state, cancel_requested_at, cancel_deadline_at, "
        " transitions, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,"
        " to_timestamp($9), CASE WHEN $10::float8 IS NULL THEN NULL ELSE "
        " to_timestamp($10) END, $11::text[], $12, to_timestamp($9), "
        " to_timestamp($13), $14::jsonb, to_timestamp($9)) "
        " ON CONFLICT DO NOTHING",
        iid, account_id, pos["group_id"], pos["position_key"],
        pos["us_market_slug"], pos["holding_side"], review_id, selection,
        float(at), None if probability_source_at is None
        else float(probability_source_at), list(orders), S_CANCEL_REQUESTED,
        float(at) + CANCEL_CONFIRM_DEADLINE_S, json.dumps(tr, default=str))
    return await load_open(conn, account_id, pos["group_id"],
                           pos["position_key"])


async def _transition(conn, xi: dict, *, to: str, at: float, entry: dict,
                      sets: str = "", args: tuple = ()) -> bool:
    """One guarded transition: applies only while the intent is still in
    the state the caller read (no lost or double transition)."""
    tr = dict(entry, at=at, frm=xi["state"], to=to)
    q = ("UPDATE paper_exit_intents SET state=$2, updated_at=to_timestamp($3),"
         " transitions = transitions || $4::jsonb%s WHERE intent_id=$1 AND "
         " state=$5" % sets)
    got = await conn.execute(q, xi["intent_id"], to, float(at),
                             json.dumps([tr], default=str), xi["state"], *args)
    ok = got.endswith(" 1")
    if ok:
        xi["transitions"] = xi["transitions"] + [tr]
        xi["state"] = to
    return ok


async def resolve(conn, xi: dict, *, state: str, resolution: str, at: float,
                  review_id: str | None, exit_order_id=None,
                  protection_order_id=None, detail: dict | None = None
                  ) -> bool:
    assert state in (S_EXIT_SUBMITTED, S_ABANDONED)
    assert resolution in RESOLUTIONS
    ok = await _transition(
        conn, xi, to=state, at=at,
        entry={"resolution": resolution, "review_id": review_id,
               "exit_order_id": exit_order_id,
               "protection_order_id": protection_order_id,
               "detail": detail or {}},
        sets=(", resolved_at=to_timestamp($3), resolution=$6, "
              "resolution_review_id=$7, exit_order_id=$8, "
              "protection_order_id=$9"),
        args=(resolution, review_id, exit_order_id, protection_order_id))
    if ok:
        xi.update(resolution=resolution, resolved_at=at,
                  exit_order_id=exit_order_id,
                  protection_order_id=protection_order_id)
    return ok


async def advance(conn, xi: dict, *, standing, at: float, window_s: float,
                  review_id: str) -> dict:
    """WHERE THE INTENT STANDS AT THIS REVIEW (records any transition the
    order states now prove). Returns {"phase": ...}."""
    oids = xi["cancel_orders"]
    rows = {r["order_id"]: r for r in await conn.fetch(
        "SELECT order_id, state, filled_qty, updated_at FROM paper_orders "
        " WHERE order_id = ANY($1::text[])", oids)}
    other_live = [s for s in standing if s["order_id"] not in set(oids)
                  and s["state"] != "CANCEL_PENDING"]
    if xi["state"] == S_CANCEL_REQUESTED:
        if any(r["state"] == "FILLED" for r in rows.values()):
            await resolve(conn, xi, state=S_ABANDONED,
                          resolution=R_PROTECTION_FILLED, at=at,
                          review_id=review_id)
            return {"phase": PH_RESOLVED, "resolution": R_PROTECTION_FILLED}
        pending = [o for o in oids if o not in rows
                   or rows[o]["state"] in L.OPEN_STATES]
        if pending:
            if at >= xi["cancel_deadline_at"]:
                await resolve(conn, xi, state=S_ABANDONED,
                              resolution=R_CANCEL_UNCONFIRMED, at=at,
                              review_id=review_id,
                              detail={"pending": pending})
                return {"phase": PH_RESOLVED,
                        "resolution": R_CANCEL_UNCONFIRMED}
            return {"phase": PH_WAIT_CANCEL, "pending": pending,
                    "due_at": xi["cancel_deadline_at"]}
        # the terminal instant is the order's own terminal EVENT (the
        # simulator's clock), the row's update stamp only as a fallback
        evs = {r["order_id"]: L._epoch(r["at"]) for r in await conn.fetch(
            "SELECT order_id, max(at) at FROM paper_order_events WHERE "
            " order_id = ANY($1::text[]) AND kind = ANY($2::text[]) "
            " GROUP BY order_id", oids, list(TERMINAL_EVENTS))}
        term = max(evs.get(k) or L._epoch(r["updated_at"])
                   for k, r in rows.items())
        term = min(term, at)
        rv_by = term + float(window_s)
        await _transition(
            conn, xi, to=S_CANCEL_TERMINAL, at=at,
            entry={"review_id": review_id, "terminal_at": term,
                   "order_states": {k: r["state"] for k, r in rows.items()},
                   "revalidate_by": rv_by},
            sets=", cancel_terminal_at=to_timestamp($6), "
                 "revalidate_by=to_timestamp($7)",
            args=(float(term), float(rv_by)))
        xi.update(cancel_terminal_at=term, revalidate_by=rv_by)
    if other_live:
        # a protection exists again (placed by another path): the exit's
        # premise -- inventory released for the sale -- is gone
        await resolve(conn, xi, state=S_ABANDONED,
                      resolution=R_PROTECTION_PRESENT, at=at,
                      review_id=review_id,
                      protection_order_id=other_live[0]["order_id"])
        return {"phase": PH_RESOLVED, "resolution": R_PROTECTION_PRESENT}
    return {"phase": PH_REVALIDATE, "intent_id": xi["intent_id"],
            "cancel_terminal_at": xi["cancel_terminal_at"],
            "revalidate_by": xi["revalidate_by"],
            "due_at": xi["revalidate_by"]}


async def close_orphans(conn, account_id: str, held_keys, *,
                        at: float) -> int:
    """An open intent whose position no longer exists (closed by a fill or
    settlement) is resolved, never left open. `held_keys` = the account's
    open (group_id, position_key) pairs."""
    held = {(g, k) for g, k in held_keys}
    rows = await conn.fetch(
        "SELECT * FROM paper_exit_intents WHERE account_id=$1 AND "
        " state = ANY($2::text[])", account_id, list(OPEN))
    n = 0
    for r in rows:
        if (r["group_id"], r["position_key"]) in held:
            continue
        n += int(await resolve(conn, _row(r), state=S_ABANDONED,
                               resolution=R_POSITION_CLOSED, at=at,
                               review_id=None))
    return n


async def due_at_by_group(conn, account_id: str, groups) -> dict:
    """{group_id: the earliest open-intent deadline} for the review trigger."""
    if not groups:
        return {}
    rows = await conn.fetch(
        "SELECT group_id, min(CASE WHEN state='CANCEL_REQUESTED' THEN "
        " cancel_deadline_at ELSE revalidate_by END) d FROM "
        " paper_exit_intents WHERE account_id=$1 AND state = ANY($2::text[])"
        " AND group_id = ANY($3::text[]) GROUP BY group_id",
        account_id, list(OPEN), list(groups))
    return {r["group_id"]: L._epoch(r["d"]) for r in rows
            if r["d"] is not None}
