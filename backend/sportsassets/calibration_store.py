"""The calibration budget, made DURABLE.

`calibration.py` computes the limits correctly against a dict. A dict is
not a budget: an API restart would report spent = 0 and a free
concurrency slot while a real order was still resting at the venue. This
module is the same arithmetic against `calibration_sessions` and
`calibration_lifecycles` (migration 065), so the ceiling survives a
restart, a second API instance and a retried request.

THREE THINGS THE DATABASE ENFORCES, NOT THIS CODE.

  * ONE OPEN LIFECYCLE -- a partial UNIQUE index over the open states.
    A check-then-insert in Python is not a limit; two instances would
    both read "none open" and both insert. Here the second insert
    raises, and the raise is translated to the named refusal.
  * ONE TICKET PER client_order_id -- UNIQUE. A retried approval cannot
    create two tickets for one intent.
  * spent_usd >= 0 -- a CHECK, so no path can make the cumulative spend
    go backwards even by accident.

THE OPERATOR STOP is a column on the session, not a process flag: a
stop that a restart forgets is not a stop. Every admission path reads
it, and a stopped session refuses by name.
"""
from __future__ import annotations

import json

from . import calibration as cal
from .db import get_pool

SESSION_ID = "MICRO-EXEC-CAL-1"

R_STOPPED = "OPERATOR_STOP_ENGAGED"
R_NO_SESSION = "NO_CALIBRATION_SESSION"
R_DUPLICATE = "DUPLICATE_CLIENT_ORDER_ID"
R_STORE_UNREADABLE = "CALIBRATION_LEDGER_UNREADABLE"


class StoreUnavailable(Exception):
    """The ledger could not be read. NOT an empty budget.

    A budget that reads as $0 spent because the database was down is
    the most dangerous possible reading: it says the whole allowance is
    free. Every caller turns this into a refusal.
    """


async def ensure_session(pool=None, session_id: str = SESSION_ID) -> None:
    """Create the session row if it does not exist. Idempotent.

    The limits are written INTO the row at creation so the ceiling a
    session ran under is auditable afterwards, even if the constants in
    calibration.py are later changed by an authorised decision.
    """
    # A DATABASE THAT CANNOT BE REACHED IS StoreUnavailable, NOT A 500.
    # The first cut let this raise raw, so a dead database answered the
    # budget endpoint with an internal error instead of the named
    # refusal every caller here is written to handle.
    try:
        pool = pool or await get_pool()
        await pool.execute(
            """
            INSERT INTO calibration_sessions
                   (session_id, authorised_by, max_all_in_usd, max_spend_usd,
                    max_open)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (session_id) DO NOTHING
            """,
            session_id, cal.AUTHORISED_BY,
            cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE,
            cal.MAX_SESSION_CUMULATIVE_SPEND,
            cal.MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES)
    except Exception as exc:                                   # noqa: BLE001
        raise StoreUnavailable("%s: %s" % (R_STORE_UNREADABLE,
                                           type(exc).__name__)) from exc


SESSION_SQL = """
SELECT session_id, experiment, authorised_by, max_all_in_usd, max_spend_usd,
       max_open, spent_usd, stopped, stopped_at, stopped_by, stop_reason
  FROM calibration_sessions WHERE session_id = $1
"""

LIFECYCLES_SQL = """
SELECT client_order_id, market_id, outcome, venue, side, quantity, price,
       reserve_usd, all_in_usd, spent_usd, state, venue_order_id,
       venue_terminal_state, fills_reconciled, created_at, updated_at
  FROM calibration_lifecycles
 WHERE session_id = $1
 ORDER BY created_at DESC
 LIMIT 500
"""


async def load(pool=None, session_id: str = SESSION_ID) -> dict:
    """The session in `calibration.py`'s own shape, from the database."""
    try:
        pool = pool or await get_pool()
        srow = await pool.fetchrow(SESSION_SQL, session_id)
        rows = await pool.fetch(LIFECYCLES_SQL, session_id)
    except Exception as exc:                                   # noqa: BLE001
        raise StoreUnavailable("%s: %s" % (R_STORE_UNREADABLE,
                                           type(exc).__name__)) from exc
    if srow is None:
        raise StoreUnavailable(R_NO_SESSION)
    s = dict(srow)
    return {
        "experiment": s.get("experiment") or cal.EXPERIMENT,
        "sessionId": s["session_id"],
        "authorisedBy": s["authorised_by"],
        "limits": {
            "maxAllInCostPerTradeLifecycle": float(s["max_all_in_usd"]),
            "maxSessionCumulativeSpend": float(s["max_spend_usd"]),
            "maxConcurrentOrderPositionLifecycles": int(s["max_open"]),
        },
        "spent": float(s["spent_usd"]),
        "stopped": bool(s["stopped"]),
        "stoppedAt": s.get("stopped_at"),
        "stoppedBy": s.get("stopped_by"),
        "stopReason": s.get("stop_reason"),
        "lifecycles": [_lifecycle(dict(r)) for r in rows],
    }


def _lifecycle(r: dict) -> dict:
    return {
        "clientOrderId": r["client_order_id"],
        "marketId": r["market_id"],
        "outcome": r["outcome"],
        "venue": r["venue"],
        "side": r["side"],
        "quantity": int(r["quantity"]),
        "price": float(r["price"]),
        "reserve": float(r["reserve_usd"]),
        "allInCost": float(r["all_in_usd"]),
        "spentSoFar": float(r["spent_usd"]),
        "state": r["state"],
        "venueOrderId": r.get("venue_order_id"),
        "venueTerminalState": r.get("venue_terminal_state"),
        "fillsReconciled": bool(r["fills_reconciled"]),
        "lane": "CALIBRATION",
    }


async def budget(pool=None, session_id: str = SESSION_ID) -> dict:
    """The three numbers COMMAND shows, plus the stop."""
    s = await load(pool, session_id)
    b = cal.budget_block(s)
    b.update(stopped=s["stopped"], stoppedBy=s.get("stoppedBy"),
             stopReason=s.get("stopReason"),
             stoppedAt=str(s["stoppedAt"]) if s.get("stoppedAt") else None)
    return b


async def preflight(ticket: dict, account: dict | None, checks: dict | None,
                    pool=None, session_id: str = SESSION_ID) -> dict:
    """The full pre-submission report against the DURABLE budget."""
    s = await load(pool, session_id)
    rep = cal.preflight(ticket, s, account, checks)
    if s["stopped"]:
        # THE STOP OVERRIDES EVERYTHING, including a clean budget.
        rep["refusals"] = sorted(set(rep["refusals"]) | {R_STOPPED})
        rep["submittable"] = False
    rep["durable"] = True
    rep["budget"] = await budget(pool, session_id)
    return rep


async def reserve(ticket: dict, account: dict | None, approved_by: str,
                  pool=None, session_id: str = SESSION_ID) -> dict:
    """Take the reserve and record the approved ticket. PLACES NOTHING.

    The row is written BEFORE any venue call would ever be made, which
    is the only order that survives a lost response: an order we cannot
    name is an order we cannot reconcile.
    """
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise ValueError("CALIBRATION_TICKET_REFUSED: APPROVAL_NOT_ATTRIBUTED")
    s = await load(pool, session_id)
    if s["stopped"]:
        raise ValueError("CALIBRATION_TICKET_REFUSED: " + R_STOPPED)
    why = cal.refusals(ticket, s, account)
    if why:
        raise ValueError("CALIBRATION_TICKET_REFUSED: " + ", ".join(why))

    allin = cal.all_in_cost(ticket["quantity"], ticket["price"],
                            ticket["entryFeeReserve"], ticket["exitFeeReserve"])
    pool = pool or await get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO calibration_lifecycles
                   (session_id, client_order_id, venue, account, market_id,
                    outcome, side, order_type, price, quantity,
                    entry_fee_usd, exit_fee_usd, all_in_usd, reserve_usd,
                    inventory_plan, approved_by, ticket)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13,$14,$15,
                    $16::jsonb)
            """,
            session_id, ticket["clientOrderId"], ticket["venue"],
            ticket["account"], ticket["marketId"], ticket["outcome"],
            ticket.get("side", "BUY"), ticket["orderType"],
            float(ticket["price"]), int(ticket["quantity"]),
            float(ticket["entryFeeReserve"]), float(ticket["exitFeeReserve"]),
            allin, ticket["inventoryPlan"], approved_by.strip(),
            json.dumps(ticket, default=str))
    except Exception as exc:                                   # noqa: BLE001
        # THE DATABASE IS THE LIMIT. A unique violation here is not an
        # internal error, it is the concurrency cap or the duplicate
        # guard doing its job, and it is reported as such.
        name = type(exc).__name__
        if "Unique" in name or "unique" in str(exc).lower():
            raise ValueError(
                "CALIBRATION_TICKET_REFUSED: %s or %s (the database index, "
                "not a code check)" % (cal.R_CONCURRENCY, R_DUPLICATE)) from exc
        raise
    return await budget(pool, session_id)


async def record_spend(client_order_id: str, amount: float, pool=None,
                       session_id: str = SESSION_ID) -> dict:
    """Cash left the account. Cumulative; never reversed.

    Both writes happen in ONE transaction. A lifecycle whose spend was
    booked while the session total was not would understate the
    cumulative ceiling for every later ticket.
    """
    if not isinstance(amount, (int, float)) or amount < 0:
        raise ValueError("CALIBRATION_SPEND_NOT_A_NON_NEGATIVE_AMOUNT")
    pool = pool or await get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            n = await con.execute(
                """
                UPDATE calibration_lifecycles
                   SET spent_usd = spent_usd + $2, updated_at = now()
                 WHERE client_order_id = $1
                """, client_order_id, amount)
            if n and n.endswith(" 0"):
                raise ValueError("CALIBRATION_UNKNOWN_LIFECYCLE: %s"
                                 % client_order_id)
            await con.execute(
                """
                UPDATE calibration_sessions
                   SET spent_usd = spent_usd + $2, updated_at = now()
                 WHERE session_id = $1
                """, session_id, amount)
    return await budget(pool, session_id)


async def advance(client_order_id: str, state: str, pool=None,
                  venue_order_id: str | None = None) -> None:
    """Move a lifecycle forward. RECONCILED is refused here on purpose."""
    if state == cal.RECONCILED:
        raise ValueError("CALIBRATION_USE_RELEASE_TO_RECONCILE")
    if state not in (cal.APPROVED, cal.SUBMITTED, cal.EXIT_CONSIDERED,
                     cal.EXIT_SUBMITTED, cal.EXIT_FILLED):
        raise ValueError("CALIBRATION_UNKNOWN_STATE: %s" % state)
    pool = pool or await get_pool()
    await pool.execute(
        """
        UPDATE calibration_lifecycles
           SET state = $2,
               venue_order_id = COALESCE($3, venue_order_id),
               updated_at = now()
         WHERE client_order_id = $1
        """, client_order_id, state, venue_order_id)


async def release(client_order_id: str, venue_terminal_state: str | None,
                  fills_reconciled: bool, pool=None,
                  session_id: str = SESSION_ID) -> dict:
    """Release the reserve. Requires BOTH, and says so when it refuses.

    A cancel acknowledgement is not a terminal state. The venue may
    have filled the order before it saw the cancel -- that is the book
    1333 and book 863 shape -- so nothing is released until the venue's
    own terminal state has been read AND the fills have been compared.
    """
    if not isinstance(venue_terminal_state, str) \
            or not venue_terminal_state.strip():
        raise ValueError("CALIBRATION_RESERVE_HELD: "
                         + cal.A_CANCEL_IS_NOT_A_TERMINAL_STATE)
    if fills_reconciled is not True:
        raise ValueError("CALIBRATION_RESERVE_HELD: fills not reconciled")
    pool = pool or await get_pool()
    await pool.execute(
        """
        UPDATE calibration_lifecycles
           SET state = 'RECONCILED', venue_terminal_state = $2,
               fills_reconciled = true, reconciled_at = now(),
               updated_at = now()
         WHERE client_order_id = $1
        """, client_order_id, venue_terminal_state.strip())
    return await budget(pool, session_id)


async def stop(by: str, reason: str, pool=None,
               session_id: str = SESSION_ID) -> dict:
    """THE OPERATOR STOP. Durable, attributed, and never automatic.

    It does not cancel anything by itself -- cancelling is a venue act
    with its own reconciliation -- it refuses every further admission.
    Reserves already held stay held, because a stop does not make an
    outstanding order impossible to fill.
    """
    pool = pool or await get_pool()
    await pool.execute(
        """
        UPDATE calibration_sessions
           SET stopped = true, stopped_at = now(), stopped_by = $2,
               stop_reason = $3, updated_at = now()
         WHERE session_id = $1
        """, session_id, (by or "unattributed").strip(),
        (reason or "no reason given").strip())
    return await budget(pool, session_id)


async def resume(by: str, pool=None, session_id: str = SESSION_ID) -> dict:
    """Lift the stop. Separate from stop so it cannot be a toggle typo."""
    pool = pool or await get_pool()
    await pool.execute(
        """
        UPDATE calibration_sessions
           SET stopped = false, stopped_at = NULL, stopped_by = $2,
               stop_reason = NULL, updated_at = now()
         WHERE session_id = $1
        """, session_id, (by or "unattributed").strip())
    return await budget(pool, session_id)
