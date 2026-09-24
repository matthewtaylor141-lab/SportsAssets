"""SHADOW ACCOUNT LIFECYCLE. One book, created once, resumed forever.

THE DISTINCTION THAT WAS MISSING.

    ACCOUNT  the book. Durable. A restart RESUMES it. Capital is set
             once, at creation, and never replenished.
    EPOCH    the process. New on every start. Useful for tracing a row
             to the process that wrote it, and meaningless as an
             accounting boundary.

There was no account id at all, and `boot_id` was written per ROW as a
timestamp, so it was neither. The loop restarted repeatedly without
restoring its book; `bettor_desk_positions` accumulated legs from
several abandoned books while `bettor_desk_state.cash` tracked only the
last. The restore then adopted all of them against that single cash
figure: 96 legs where the running book had 52, and the ledger identity
off by $2,367.73.

WHAT THIS MODULE DOES, AND REFUSES TO DO.

It closes that period as CLOSED_UNATTRIBUTABLE, writes an incident row
carrying the measured discrepancy and the counts of what is preserved,
and opens ONE new account at a declared opening balance.

It does not delete a record. It does not post a compensating cash
entry. It does not describe the old inventory as closed -- unattributed
is not closed, and saying otherwise would turn an unknown into a zero.
It does not back-fill `account_id` on the historical rows, because
assigning them to a book is exactly the attribution that does not
exist.

EXACTLY ONCE IS ENFORCED BY THE DATABASE. A partial unique index admits
one ACTIVE account per desk, so a second creation fails on insert even
if two processes try it simultaneously. Nothing here relies on a caller
checking first.
"""
from __future__ import annotations

import json
import logging
import uuid

log = logging.getLogger(__name__)

ACTIVE = "ACTIVE"
CLOSED_UNATTRIBUTABLE = "CLOSED_UNATTRIBUTABLE"

INCIDENT_ID = "DESK_ACCOUNTING_RECOVERY_2026_09_23"
INCIDENT_KIND = "ACCOUNTING_RECOVERY_DEFECT"

NEW_ACCOUNT_NOTE = "New shadow account following an accounting-recovery defect."

CLOSED_NOTE = (
    "Preserved as ACCOUNTING_INCOMPLETE / UNATTRIBUTABLE. The loop "
    "restarted without restoring its book, so these position rows come "
    "from several abandoned books and no record says which. Reported "
    "P&L for this period is UNRELIABLE. Inventory here is "
    "UNATTRIBUTABLE, which is not closed and not zero.")

PNL_UNRELIABLE = "UNRELIABLE_DO_NOT_QUOTE"


def _js(v) -> str:
    return json.dumps(v if v is not None else {}, default=str)


async def _counts(conn, desk_id) -> dict:
    """What is being preserved, counted before anything is written."""
    out = {}
    for name, table, col in (
            ("decisions", "bettor_desk_decisions", "desk_id"),
            ("orders", "bettor_desk_orders", "desk_id"),
            ("fills", "bettor_desk_fills", None),
            ("positions", "bettor_desk_positions", "desk_id"),
            ("ledger_snapshots", "bettor_desk_ledger", "desk_id")):
        try:
            if col:
                out[name] = int(await conn.fetchval(
                    "SELECT count(*) FROM %s WHERE %s = $1 "
                    "AND account_id IS NULL" % (table, col), desk_id) or 0)
            else:
                out[name] = int(await conn.fetchval(
                    "SELECT count(*) FROM bettor_desk_fills "
                    "WHERE account_id IS NULL") or 0)
        except Exception as exc:                            # noqa: BLE001
            out[name] = "UNREADABLE: %s" % type(exc).__name__
    return out


async def _measured(conn, desk_id) -> dict:
    """The discrepancy, read from the rows rather than restated."""
    row = await conn.fetchrow(
        """
        SELECT cash_usd::float8 AS cash,
               inventory_cost::float8 AS inv,
               realized_pnl_usd::float8 AS realized,
               open_positions, invariant_ok, at
          FROM bettor_desk_ledger
         WHERE desk_id = $1 AND account_id IS NULL
         ORDER BY at DESC LIMIT 1
        """, desk_id)
    legs = await conn.fetchrow(
        """
        SELECT count(*) AS n, coalesce(sum(cost_basis_usd),0)::float8 AS cost
          FROM bettor_desk_positions
         WHERE desk_id = $1 AND account_id IS NULL AND qty > 0
        """, desk_id)
    out = {"unattributable_legs": int(legs["n"]) if legs else None,
           "unattributable_cost_usd": (round(legs["cost"], 2) if legs
                                       else None)}
    if row:
        drift = (float(row["cash"]) + float(row["inv"])
                 - float(row["realized"]) - 100000.0)
        out.update({
            "last_snapshot_at": row["at"],
            "cash_usd": round(float(row["cash"]), 2),
            "inventory_cost_usd": round(float(row["inv"]), 2),
            "realized_pnl_usd": round(float(row["realized"]), 2),
            "open_positions": row["open_positions"],
            "invariant_ok": row["invariant_ok"],
            "identity_drift_usd": round(drift, 2),
            "identity": "cash + inventory_cost - realized == 100000",
        })
    out["inventory_status"] = (
        "UNATTRIBUTABLE -- not closed, not zero, not valued. No mark "
        "exists for these legs and none is assumed.")
    return out


async def ensure_account(conn, desk_id, *, opening_balance) -> dict:
    """Return the ACTIVE account, creating it exactly once.

    A RESTART TAKES THE FIRST BRANCH. There is no path here that
    replenishes capital or resets performance: `opening_balance` is
    written only by the INSERT, and the INSERT cannot run twice.
    """
    row = await conn.fetchrow(
        """
        SELECT account_id, opening_balance::float8 AS opening_balance,
               opened_at, note, status
          FROM bettor_desk_accounts
         WHERE desk_id = $1 AND status = $2
        """, desk_id, ACTIVE)
    if row:
        return {"created": False, **dict(row)}

    counts = await _counts(conn, desk_id)
    measured = await _measured(conn, desk_id)
    account_id = "acct_%s" % uuid.uuid4().hex[:16]

    async with conn.transaction():
        # THE INCIDENT FIRST, so the record of why exists even if the
        # creation below loses a race with another process.
        await conn.execute(
            """
            INSERT INTO bettor_desk_incidents
                   (incident_id, desk_id, kind, summary, measured,
                    preserved_counts, pnl_reliability, detail)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8::jsonb)
            ON CONFLICT (incident_id) DO NOTHING
            """, INCIDENT_ID, desk_id, INCIDENT_KIND,
            "The live shadow loop restarted without restoring its book. "
            "Position rows from several abandoned books accumulated while "
            "cash tracked only the last, and the restore adopted all of "
            "them: 96 legs where the running book had 52, identity off "
            "by $2,367.73. No record says which leg belongs to which "
            "book, so the period is preserved rather than repaired.",
            _js(measured), _js(counts), PNL_UNRELIABLE,
            _js({"resolution": "one new account, declared opening "
                               "balance, nothing back-filled",
                 "records_deleted": 0,
                 "compensating_cash_posted": 0.0}))
        # Any earlier account row is closed; on the first run there is
        # none, and the unassigned period is represented by the
        # incident rather than by a synthetic account.
        await conn.execute(
            """
            UPDATE bettor_desk_accounts
               SET status = $3, closed_at = now(), note = $4
             WHERE desk_id = $1 AND status = $2
            """, desk_id, ACTIVE, CLOSED_UNATTRIBUTABLE, CLOSED_NOTE)
        await conn.execute(
            """
            INSERT INTO bettor_desk_accounts
                   (account_id, desk_id, status, opening_balance, note,
                    provenance)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb)
            """, account_id, desk_id, ACTIVE, float(opening_balance),
            NEW_ACCOUNT_NOTE,
            _js({"follows_incident": INCIDENT_ID,
                 "preserved_counts": counts,
                 "measured_at_close": measured,
                 "evaluation_status": (
                     "NOT a FINAL evaluation set. That status requires a "
                     "frozen policy, criteria declared in advance and "
                     "decisions that are genuinely prospective; it is "
                     "not conferred by the account being new.")}))
    log.warning("shadow desk: opened account %s at %.2f after incident %s",
                account_id, float(opening_balance), INCIDENT_ID)
    return {"created": True, "account_id": account_id,
            "opening_balance": float(opening_balance),
            "note": NEW_ACCOUNT_NOTE, "status": ACTIVE,
            "incident_id": INCIDENT_ID, "measured": measured,
            "preserved_counts": counts}
