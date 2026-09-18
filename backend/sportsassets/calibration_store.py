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
import uuid

from . import calibration as cal
from .db import get_pool

SESSION_ID = "MICRO-EXEC-CAL-1"

# THE RETAIL PRODUCTION LANE, which is what every session written before
# migration 067 was. The defaults in that migration say the same thing.
# The name the ticket builder already writes (`calibration_evidence`), and
# therefore the name every lifecycle row written so far carries. The
# migration's DEFAULT says the same word, so the backfill and the code
# agree rather than merely looking alike.
DEFAULT_VENUE = "polymarket-us"

ENV_PRODUCTION = "PRODUCTION"
ENV_PREPROD = "PREPROD"
# One tuple, defined in `calibration` and re-exported here, so a third
# environment cannot be admitted by one module and refused by the other.
ENVIRONMENTS = cal.ENVIRONMENTS

R_STOPPED = "OPERATOR_STOP_ENGAGED"
R_NO_SESSION = "NO_CALIBRATION_SESSION"
R_DUPLICATE = "DUPLICATE_CLIENT_ORDER_ID"
R_STORE_UNREADABLE = "CALIBRATION_LEDGER_UNREADABLE"

# A SESSION IS BOUND TO ONE VENUE AND ONE ENVIRONMENT (migration 067).
# These are what a ticket naming a different pair is refused with. The
# refusal is the separation: preproduction spend cannot reach a production
# session's total because it cannot be written into that session at all.
R_ENV_MISMATCH = "TICKET_ENVIRONMENT_IS_NOT_THIS_SESSION_S"
R_VENUE_MISMATCH = "TICKET_VENUE_IS_NOT_THIS_SESSION_S"
R_ENV_UNKNOWN = "TICKET_ENVIRONMENT_NOT_DECLARED"


class StoreUnavailable(Exception):
    """The ledger could not be read. NOT an empty budget.

    A budget that reads as $0 spent because the database was down is
    the most dangerous possible reading: it says the whole allowance is
    free. Every caller turns this into a refusal.
    """


async def ensure_session(pool=None, session_id: str = SESSION_ID,
                         venue: str = DEFAULT_VENUE,
                         environment: str = ENV_PRODUCTION) -> None:
    """Create the session row if it does not exist. Idempotent.

    The limits are written INTO the row at creation so the ceiling a
    session ran under is auditable afterwards, even if the constants in
    calibration.py are later changed by an authorised decision.

    THE VENUE AND ENVIRONMENT ARE WRITTEN THE SAME WAY, and for the same
    reason: a budget is only meaningful about something. `ON CONFLICT DO
    NOTHING` means an existing session keeps the pair it was opened with,
    so this can never quietly re-point a production session at a
    preproduction exchange.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError("CALIBRATION_UNKNOWN_ENVIRONMENT: %r" % (environment,))
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError("CALIBRATION_SESSION_VENUE_NOT_DECLARED")
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
                    max_open, venue, environment)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (session_id) DO NOTHING
            """,
            session_id, cal.AUTHORISED_BY,
            cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE,
            cal.MAX_SESSION_CUMULATIVE_SPEND,
            cal.MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES,
            venue.strip(), environment)
    except Exception as exc:                                   # noqa: BLE001
        raise StoreUnavailable("%s: %s" % (R_STORE_UNREADABLE,
                                           type(exc).__name__)) from exc


SESSION_SQL = """
SELECT session_id, experiment, authorised_by, max_all_in_usd, max_spend_usd,
       max_open, spent_usd, stopped, stopped_at, stopped_by, stop_reason,
       venue, environment
  FROM calibration_sessions WHERE session_id = $1
"""

LIFECYCLES_SQL = """
SELECT client_order_id, market_id, outcome, venue, environment, side,
       quantity, price,
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
        # WHAT THIS BUDGET IS ABOUT. Every figure below -- the spend, the
        # reserve, the lifecycles -- belongs to this pair and to nothing
        # else. COMMAND prints them beside the numbers so a reader can
        # never mistake preproduction activity for production performance.
        "venue": s.get("venue") or DEFAULT_VENUE,
        "environment": s.get("environment") or ENV_PRODUCTION,
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
        "environment": r.get("environment") or ENV_PRODUCTION,
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
             stoppedAt=str(s["stoppedAt"]) if s.get("stoppedAt") else None,
             # A NUMBER WITHOUT ITS VENUE AND ENVIRONMENT IS NOT A BUDGET
             # FIGURE, it is a number. These travel with it everywhere,
             # including into COMMAND's own display.
             venue=s["venue"], environment=s["environment"])
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
    why = list(cal.refusals(ticket, s, account))
    # THE SESSION'S OWN BINDING, checked before anything is written. A
    # session is one venue and one environment; a ticket naming another
    # pair does not belong in this ledger row and is refused rather than
    # coerced. This is what keeps preproduction spend out of a production
    # session's total: not a filter on the way out, but a refusal on the
    # way in.
    #
    # An ABSENT or unknown environment is already named by `cal.refusals`
    # (it is an identity field there). What is added here is the only
    # thing that module cannot know: whether it matches THIS session.
    env = ticket.get("environment")
    if env in ENVIRONMENTS and env != s["environment"]:
        why.append("%s (ticket %s, session %s)"
                   % (R_ENV_MISMATCH, env, s["environment"]))
    if ticket.get("venue") != s["venue"]:
        why.append("%s (ticket %r, session %r)"
                   % (R_VENUE_MISMATCH, ticket.get("venue"), s["venue"]))
    if why:
        raise ValueError("CALIBRATION_TICKET_REFUSED: " + ", ".join(why))

    allin = cal.all_in_cost(ticket["quantity"], ticket["price"],
                            ticket["entryFeeReserve"], ticket["exitFeeReserve"])
    pool = pool or await get_pool()
    try:
        await pool.execute(
            """
            INSERT INTO calibration_lifecycles
                   (session_id, client_order_id, venue, environment, account,
                    market_id,
                    outcome, side, order_type, price, quantity,
                    entry_fee_usd, exit_fee_usd, all_in_usd, reserve_usd,
                    inventory_plan, approved_by, ticket)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$14,$15,
                    $16,$17::jsonb)
            """,
            session_id, ticket["clientOrderId"], ticket["venue"],
            ticket["environment"],
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


R_SESSION_NOT_THE_OWNER = "SESSION_IS_NOT_THIS_LIFECYCLE_S_OWNER"

SUPERSEDED_BY_BOOK_CASH = (
    "record_spend books a DELTA the caller computed. `book_cash` books the "
    "venue's cumulative total and computes the delta inside the statement, "
    "which is the only shape that is idempotent across processes. Nothing "
    "in this codebase calls record_spend; it is kept because a delta is "
    "still the right shape for a cost the venue reports only as an "
    "increment, and it is now bound to the lifecycle's own session")


async def _owning_session(con, client_order_id: str, supplied: str | None):
    """The session this lifecycle belongs to, read INSIDE the transaction.

    THE DEFECT THIS CLOSES. Both money paths took the lifecycle by
    `client_order_id` and the session by a separately supplied (or
    defaulted) `session_id`, and nothing checked that the second owned the
    first. With one session that was merely redundant. With a second
    session -- the institutional preproduction lane -- it is a cross-ledger
    write: a preprod lifecycle booked against the production session moves
    the real $100 allowance with test-funded money.

    The owner is DERIVED, never assumed. A caller that supplied a different
    session is refused by name before anything is written, because
    retargeting its write to another ledger silently would be worse than
    refusing it. `None` means "derive it", which is what every caller
    should pass.
    """
    row = await con.fetchrow(
        """
        SELECT session_id, environment, venue, cash_booked_usd
          FROM calibration_lifecycles
         WHERE client_order_id = $1
           FOR UPDATE
        """, client_order_id)
    if row is None:
        raise ValueError("CALIBRATION_UNKNOWN_LIFECYCLE: %s" % client_order_id)
    owner = row["session_id"]
    if supplied is not None and supplied != owner:
        raise ValueError(
            "%s: lifecycle %s belongs to session %r (%s/%s), not %r"
            % (R_SESSION_NOT_THE_OWNER, client_order_id, owner,
               row["venue"], row["environment"], supplied))
    return owner, row


async def record_spend(client_order_id: str, amount: float, pool=None,
                       session_id: str | None = None) -> dict:
    """Cash left the account. Cumulative; never reversed.

    Both writes happen in ONE transaction. A lifecycle whose spend was
    booked while the session total was not would understate the
    cumulative ceiling for every later ticket.

    THE SESSION IS THE LIFECYCLE'S OWN. `session_id` defaults to None,
    meaning derive it; passing one that does not own this lifecycle is
    refused and writes nothing. See `SUPERSEDED_BY_BOOK_CASH` for when to
    reach for this at all.
    """
    if not isinstance(amount, (int, float)) or amount < 0:
        raise ValueError("CALIBRATION_SPEND_NOT_A_NON_NEGATIVE_AMOUNT")
    pool = pool or await get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            owner, _ = await _owning_session(con, client_order_id, session_id)
            await con.execute(
                """
                UPDATE calibration_lifecycles
                   SET spent_usd = spent_usd + $2, updated_at = now()
                 WHERE client_order_id = $1
                """, client_order_id, amount)
            await con.execute(
                """
                UPDATE calibration_sessions
                   SET spent_usd = spent_usd + $2, updated_at = now()
                 WHERE session_id = $1
                """, owner, amount)
    return await budget(pool, owner)


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


# ─────────────────────────────────────────────────────────────────────
# THE SEND CLAIM. One approval permits at most one send attempt.
#
# `guarded_submit` used to take a caller-supplied row and an optional
# persistence callable, and it SENT BEFORE IT RECORDED. Two callers with
# the same approved ticket both passed the checks and both reached the
# venue; a crash between the send and the record left nothing to
# reconcile against. One lifecycle row is not, by itself, one venue
# submission.
#
# The claim is an INSERT with a UNIQUE client_order_id. The check and the
# act are the same statement, so there is no window between them. The
# second caller gets a unique violation and NO SEND.
# ─────────────────────────────────────────────────────────────────────

R_ALREADY_CLAIMED = "SEND_ALREADY_CLAIMED_FOR_THIS_APPROVAL"
R_NOT_APPROVED = "LIFECYCLE_NOT_IN_APPROVED_STATE"
R_NO_ROW = "NO_DURABLE_LIFECYCLE_ROW"
R_NO_RESERVE = "NO_RESERVE_HELD"
R_ALREADY_SENT = "LIFECYCLE_ALREADY_HAS_A_VENUE_ORDER"
R_CLAIM_UNAVAILABLE = "CLAIM_STORE_UNAVAILABLE"

# The economically relevant fields, bound server-side at claim time. A
# send may be made with these and nothing else.
BOUND_FIELDS = ("venue", "environment", "account", "marketId", "outcome",
                "outcomeSide", "side", "orderType", "price", "quantity",
                "entryFeeReserve", "exitFeeReserve")

ATTEMPT_UNRESOLVED = ("CLAIMED", "PRE_IMAGE_RECORDED", "SENT_OUTCOME_UNKNOWN")

AN_UNRESOLVED_ATTEMPT_BLOCKS_EVERYTHING = (
    "an attempt whose outcome is unknown may correspond to a live order. "
    "Until it is resolved no further send is permitted, on this approval "
    "or any other, because the single-lifecycle limit is spent on it")


def _bind(row: dict) -> dict:
    """The approved economic fields, read from the DURABLE row."""
    t = row.get("ticket")
    if isinstance(t, str):
        try:
            t = json.loads(t)
        except ValueError:
            t = {}
    t = t if isinstance(t, dict) else {}
    bound = {
        "venue": row.get("venue"),
        "environment": row.get("environment"),
        "account": row.get("account"),
        "marketId": row.get("market_id"),
        "outcome": row.get("outcome"),
        # The LONG/SHORT selector lives on the approved ticket; the
        # lifecycle table predates it. It is bound from the ticket the
        # human approved, never defaulted.
        "outcomeSide": t.get("outcomeSide"),
        "side": row.get("side"),
        "orderType": row.get("order_type"),
        "price": float(row["price"]) if row.get("price") is not None else None,
        "quantity": int(row["quantity"]) if row.get("quantity") is not None
        else None,
        "entryFeeReserve": (float(row["entry_fee_usd"])
                            if row.get("entry_fee_usd") is not None else None),
        "exitFeeReserve": (float(row["exit_fee_usd"])
                           if row.get("exit_fee_usd") is not None else None),
    }
    bound["clientOrderId"] = row.get("client_order_id")
    bound["inventoryPlan"] = row.get("inventory_plan")
    bound["approvedBy"] = row.get("approved_by")
    return bound


APPROVED_ROW_SQL = """
    SELECT client_order_id, venue, environment, account, market_id,
           outcome, side,
           order_type, price, quantity, entry_fee_usd, exit_fee_usd,
           all_in_usd, reserve_usd, inventory_plan, approved_by, state,
           venue_order_id, ticket
      FROM calibration_lifecycles
     WHERE client_order_id = $1
"""

UNRESOLVED_SQL = """
    SELECT attempt_id, client_order_id, state, claimed_by, claimed_at,
           pre_open_order_ids, venue_order_id, outcome, reason,
           venue, environment, venue_clord_id
      FROM calibration_send_attempts
     WHERE session_id = $1 AND state = ANY($2::text[])
     ORDER BY claimed_at
"""

# THE SAME QUESTION WITHOUT THE SESSION FILTER. `claim_send` asks this one.
#
# The session-scoped read above answers "what does THIS session have
# outstanding", which is the right question for a status display. It is the
# WRONG question before a send: a second session -- the institutional
# preproduction lane is exactly that -- holds its own attempts, and an
# unresolved attempt there may correspond to a live order too. The approved
# boundary is one unresolved lifecycle, not one per session, and the
# lifecycle index enforces that globally; this makes the attempt check
# agree with it instead of quietly being narrower.
ANY_UNRESOLVED_SQL = """
    SELECT attempt_id, session_id, client_order_id, state, claimed_by,
           claimed_at, pre_open_order_ids, venue_order_id, outcome, reason,
           venue, environment, venue_clord_id
      FROM calibration_send_attempts
     WHERE state = ANY($1::text[])
     ORDER BY claimed_at
"""


async def unresolved_attempt(pool=None, session_id: str = SESSION_ID):
    """The attempt a restart has to deal with, or None.

    This is what makes ambiguity survive a crash: the row was written
    before the network call, so a process that died mid-send left it
    behind.
    """
    pool = pool or await get_pool()
    try:
        rows = await pool.fetch(UNRESOLVED_SQL, session_id,
                                list(ATTEMPT_UNRESOLVED))
    except Exception as exc:                                   # noqa: BLE001
        raise StoreUnavailable("%s: %s" % (R_CLAIM_UNAVAILABLE,
                                           type(exc).__name__)) from exc
    return dict(rows[0]) if rows else None


async def any_unresolved_attempt(pool=None):
    """The attempt outstanding ANYWHERE in the ledger, or None.

    Across every session and every environment. A send is refused while
    this is not None, because the approved limit is one unresolved
    lifecycle and a preproduction attempt occupies it just as a production
    one does.
    """
    pool = pool or await get_pool()
    try:
        rows = await pool.fetch(ANY_UNRESOLVED_SQL, list(ATTEMPT_UNRESOLVED))
    except Exception as exc:                                   # noqa: BLE001
        raise StoreUnavailable("%s: %s" % (R_CLAIM_UNAVAILABLE,
                                           type(exc).__name__)) from exc
    return dict(rows[0]) if rows else None


async def claim_send(client_order_id: str, claimed_by: str, pool=None,
                     session_id: str = SESSION_ID) -> dict:
    """Resolve the approved ticket SERVER-SIDE and claim the one send.

    Returns {"CLAIMED": True, "attemptId": ..., "bound": {...}} or
    {"CLAIMED": False, "blockers": [...]}. It never raises for a refusal
    -- a refusal is an answer -- and it NEVER returns CLAIMED without a
    durable row, so a caller that cannot reach the store cannot send.
    """
    if not isinstance(claimed_by, str) or not claimed_by.strip():
        return {"CLAIMED": False, "blockers": ["CLAIM_NOT_ATTRIBUTED"]}
    try:
        pool = pool or await get_pool()
    except Exception as exc:                                   # noqa: BLE001
        return {"CLAIMED": False,
                "blockers": ["%s: %s" % (R_CLAIM_UNAVAILABLE,
                                         type(exc).__name__)]}

    # THE SESSION AND THE STOP, from the ledger rather than an argument.
    try:
        s = await load(pool, session_id)
    except StoreUnavailable as exc:
        return {"CLAIMED": False, "blockers": ["%s" % exc]}
    if s["stopped"]:
        return {"CLAIMED": False, "blockers": [R_STOPPED]}

    # ANY UNRESOLVED ATTEMPT BLOCKS -- for another ticket, and in another
    # session or environment. Narrowing this to one session would let the
    # institutional preproduction lane and the production lane each hold
    # an unresolved send at the same time.
    try:
        outstanding = await any_unresolved_attempt(pool)
    except StoreUnavailable as exc:
        return {"CLAIMED": False, "blockers": ["%s" % exc]}
    if outstanding:
        return {"CLAIMED": False,
                "blockers": [R_ALREADY_CLAIMED],
                "outstanding": outstanding,
                "why": AN_UNRESOLVED_ATTEMPT_BLOCKS_EVERYTHING}

    try:
        row = await pool.fetchrow(APPROVED_ROW_SQL, client_order_id)
    except Exception as exc:                                   # noqa: BLE001
        return {"CLAIMED": False,
                "blockers": ["%s: %s" % (R_CLAIM_UNAVAILABLE,
                                         type(exc).__name__)]}
    if row is None:
        return {"CLAIMED": False, "blockers": [R_NO_ROW]}
    row = dict(row)

    blockers = []
    if row.get("state") != cal.APPROVED:
        blockers.append(R_NOT_APPROVED)
    if not float(row.get("reserve_usd") or 0) > 0:
        blockers.append(R_NO_RESERVE)
    if row.get("venue_order_id"):
        blockers.append(R_ALREADY_SENT)
    bound = _bind(row)
    missing = [f for f in BOUND_FIELDS if bound.get(f) in (None, "")]
    if missing:
        blockers.append("APPROVED_FIELDS_INCOMPLETE: %s" % ",".join(missing))
    # THE BUDGET, against the durable session. NOT cal.refusals(): those
    # are ADMISSION checks for a ticket that has not been reserved yet,
    # and this row's own reserve is already in the session's numbers, so
    # running them here would refuse every claim for exceeding a limit
    # with its own money. What is re-checked is what could have changed
    # since approval.
    allin = float(row.get("all_in_usd") or 0)
    if allin > cal.MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE + 1e-9:
        blockers.append(cal.R_PER_TRADE)
    if cal.spent(s) + cal.reserved(s) > cal.MAX_SESSION_CUMULATIVE_SPEND + 1e-9:
        blockers.append(cal.R_SESSION)
    open_ids = [lc["clientOrderId"] for lc in s["lifecycles"]
                if lc["state"] in cal.OPEN_STATES]
    if [i for i in open_ids if i != client_order_id]:
        # Another lifecycle holds the one permitted slot.
        blockers.append(cal.R_CONCURRENCY)
    if blockers:
        return {"CLAIMED": False, "blockers": sorted(set(blockers)),
                "bound": bound}

    attempt_id = "ATT-%s-%s" % (client_order_id, uuid.uuid4().hex[:12])
    try:
        await pool.execute(
            """
            INSERT INTO calibration_send_attempts
                   (attempt_id, session_id, client_order_id, claimed_by,
                    bound_fields, state, venue, environment)
            VALUES ($1,$2,$3,$4,$5::jsonb,'CLAIMED',$6,$7)
            """,
            attempt_id, session_id, client_order_id, claimed_by.strip(),
            json.dumps(bound, default=str),
            # FROM THE LIFECYCLE ROW, not from the session and not from the
            # caller: the attempt says which exchange THIS approved order
            # was for, so a reconciliation reading the attempts table alone
            # never has to guess.
            row.get("venue"), row.get("environment") or ENV_PRODUCTION)
    except Exception as exc:                                   # noqa: BLE001
        name = type(exc).__name__
        if "Unique" in name or "unique" in str(exc).lower():
            # THE DATABASE REFUSED THE SECOND CLAIM. This is the whole
            # mechanism, not an internal error.
            return {"CLAIMED": False, "blockers": [R_ALREADY_CLAIMED],
                    "why": AN_UNRESOLVED_ATTEMPT_BLOCKS_EVERYTHING}
        return {"CLAIMED": False,
                "blockers": ["%s: %s" % (R_CLAIM_UNAVAILABLE, name)]}
    return {"CLAIMED": True, "attemptId": attempt_id, "bound": bound,
            "state": "CLAIMED"}


async def record_pre_image(attempt_id: str, pre_ids, pool=None,
                           venue_clord_id: str | None = None) -> None:
    """Persist the pre-image and move to PRE_IMAGE_RECORDED.

    BEFORE the network call. A send whose pre-image was never written
    down cannot be attributed afterwards, and the claim is already spent,
    so the failure has to happen here rather than after the order exists.

    `venue_clord_id` IS THE VENUE'S OWN CORRELATION IDENTIFIER, where the
    venue has one. It is written here, before the send, for the same
    reason the pre-image is: an identifier minted and then lost with the
    response correlates nothing. On the retail venue there is no such
    field and this stays None -- which records that the venue offers none,
    a different fact from having failed to record one. The pre-image is
    still written either way: where both exist they corroborate, and the
    stronger evidence does not excuse dropping the weaker.
    """
    if venue_clord_id is not None and (not isinstance(venue_clord_id, str)
                                       or not venue_clord_id.strip()):
        raise ValueError("CALIBRATION_VENUE_CLORD_ID_NOT_A_STRING")
    pool = pool or await get_pool()
    n = await pool.execute(
        """
        UPDATE calibration_send_attempts
           SET pre_open_order_ids = $2::jsonb, pre_image_at = now(),
               venue_clord_id = COALESCE($3, venue_clord_id),
               state = 'PRE_IMAGE_RECORDED', updated_at = now()
         WHERE attempt_id = $1 AND state = 'CLAIMED'
        """, attempt_id, json.dumps(list(pre_ids or []), default=str),
        venue_clord_id.strip() if venue_clord_id else None)
    if isinstance(n, str) and n.endswith(" 0"):
        raise ValueError("CALIBRATION_ATTEMPT_NOT_CLAIMABLE: %s" % attempt_id)


async def mark_sent(attempt_id: str, pool=None) -> None:
    """The network call is about to happen. Durable BEFORE it does.

    From here on the attempt is SENT_OUTCOME_UNKNOWN whatever happens to
    this process: a crash between this write and the response leaves the
    ambiguity recorded, which is the only state from which it can be
    reconciled.
    """
    pool = pool or await get_pool()
    n = await pool.execute(
        """
        UPDATE calibration_send_attempts
           SET state = 'SENT_OUTCOME_UNKNOWN', sent_at = now(),
               updated_at = now()
         WHERE attempt_id = $1 AND state = 'PRE_IMAGE_RECORDED'
        """, attempt_id)
    if isinstance(n, str) and n.endswith(" 0"):
        raise ValueError("CALIBRATION_ATTEMPT_NOT_SENDABLE: %s" % attempt_id)


async def resolve_attempt(attempt_id: str, outcome: str, pool=None,
                          venue_order_id: str | None = None,
                          reason: str | None = None,
                          resolved_by: str = "system",
                          resolved: bool = True) -> None:
    """Record what the venue said. AMBIGUITY IS NOT A RESOLUTION.

    `resolved=False` keeps the attempt in SENT_OUTCOME_UNKNOWN while
    still recording what is known, so a later reconciliation has the
    details and the block stays in force.
    """
    pool = pool or await get_pool()
    state = "RESOLVED" if resolved else "SENT_OUTCOME_UNKNOWN"
    await pool.execute(
        """
        UPDATE calibration_send_attempts
           SET state = $2, outcome = $3, reason = $4,
               venue_order_id = COALESCE($5, venue_order_id),
               read_before = now(),
               resolved_at = CASE WHEN $2 = 'RESOLVED' THEN now() END,
               resolved_by = $6, updated_at = now()
         WHERE attempt_id = $1
        """, attempt_id, state, outcome, reason, venue_order_id, resolved_by)


async def abandon_attempt(attempt_id: str, reason: str, pool=None) -> None:
    """Nothing was sent. Record that and stop.

    NOT_SENT is terminal for the attempt but does NOT free the approval:
    the client_order_id is used up and a further send needs a fresh
    approval with a fresh id. An approval that could be retried after a
    failed attempt would not be an approval for one send.
    """
    pool = pool or await get_pool()
    await pool.execute(
        """
        UPDATE calibration_send_attempts
           SET state = 'NOT_SENT', outcome = 'NOT_SENT', reason = $2,
               resolved_at = now(), resolved_by = 'system', updated_at = now()
         WHERE attempt_id = $1 AND state IN ('CLAIMED','PRE_IMAGE_RECORDED')
        """, attempt_id, reason)


async def book_cash(client_order_id: str, cash_total: float, pool=None,
                    session_id: str | None = None) -> dict:
    """Book the venue's CUMULATIVE cash for this lifecycle. Idempotent.

    `record_spend` added a delta the caller computed from a prior total it
    was holding. Two processes holding the same prior total book the same
    cash twice, and a caller-provided prior total cannot establish
    idempotency across processes -- which is the book 1333 shape, where
    the ledger booked one of two fills.

    Here the high-water mark lives in the DATABASE and the delta is
    computed inside the statement: the lifecycle takes GREATEST(old, new)
    and the session takes exactly the increase. Re-reading one terminal
    status ten times books the cash once. A total that FALLS is the venue
    contradicting itself and is refused, never netted.
    """
    if not isinstance(cash_total, (int, float)) or cash_total < 0:
        raise ValueError("CALIBRATION_CASH_NOT_A_NON_NEGATIVE_TOTAL")
    pool = pool or await get_pool()
    async with pool.acquire() as con:
        async with con.transaction():
            # THE OWNING SESSION, derived in this transaction and locked
            # with the row. The session total below is recomputed for the
            # lifecycle's OWN session, so a preprod lifecycle can never
            # move a production allowance even if a caller names one.
            owner, row = await _owning_session(con, client_order_id,
                                               session_id)
            booked = float(row["cash_booked_usd"] or 0)
            if float(cash_total) + 1e-9 < booked:
                # THE VENUE CONTRADICTING ITSELF. A cumulative total that
                # falls is not a refund to net off; it is two readings
                # that cannot both be true, and the second one is
                # refused rather than quietly applied.
                raise ValueError(
                    "CALIBRATION_CASH_TOTAL_FELL: booked %.4f, read %.4f"
                    % (booked, float(cash_total)))
            await con.execute(
                """
                UPDATE calibration_lifecycles
                   SET cash_booked_usd = GREATEST(cash_booked_usd, $2),
                       spent_usd = GREATEST(spent_usd, $2),
                       updated_at = now()
                 WHERE client_order_id = $1
                """, client_order_id, float(cash_total))
            # The session total is the SUM of the per-lifecycle high-water
            # marks, recomputed inside the same transaction. It needs no
            # delta from the caller, so no caller can supply a wrong one,
            # and it is monotone because every term is.
            await con.execute(
                """
                UPDATE calibration_sessions s
                   SET spent_usd = (
                           SELECT COALESCE(SUM(l.cash_booked_usd), 0)
                             FROM calibration_lifecycles l
                            WHERE l.session_id = s.session_id),
                       updated_at = now()
                 WHERE s.session_id = $1
                """, owner)
    return await budget(pool, owner)
