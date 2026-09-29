"""XAVIER: THE POSITION MANAGER'S RECORD AND ITS READERS.

Xavier is not a new lane. It is the name of the responsibility the scheduled
funded servicing pass already carries -- `bettor_funded_management.manage`
(settlement, recovery, exit valuation), `bettor_funded_pair_cycle.pass_once`
(hedge discovery, ONE ranking, dispatch of the persisted winner) and the
learning pass -- for every position from the first fill of its entry (a
partial fill included) until every resulting position, outstanding order and
settlement obligation is reconciled.

This module owns the durable half of that responsibility: the persisted
per-position decision (`bettor_xavier_decisions`, migration 148), written
BEFORE anything is dispatched, and the readers the Command Centre and the
daily review use. Decisions are produced by the existing servicing path;
nothing here places, cancels or modifies an order.

WHAT A RECORD HOLDS. The responsibility state; every considered action with
its whole-position economics or its exact blocker (never only the winner);
the reasoning (increment over HOLD, worst case, capital committed and
released); the residual exposure (matched and unpaired quantities); the
evidence (probability sources, model versions, feature shas); the open
obligations; the execution eligibility -- whether the winner was dispatched
and, if not, which gate stopped it; and the next review.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

VERSION = "XAVIER_V1"
NAME = "Xavier"

# ── RESPONSIBILITY STATES (migration 148 CHECK) ───────────────────────
HELD = "HELD"                              # inventory held, nothing working
ORDER_OUTSTANDING = "ORDER_OUTSTANDING"    # an order of ours is live
ORDER_UNRESOLVED = "ORDER_UNRESOLVED"      # a send's outcome is unknown
SETTLEMENT_PENDING = "SETTLEMENT_PENDING"  # flat of orders, awaiting venue
CORRECTION_PENDING = "CORRECTION_PENDING"  # settled, the venue now disagrees
RECONCILED = "RECONCILED"                  # nothing left to manage
STATES = (HELD, ORDER_OUTSTANDING, ORDER_UNRESOLVED, SETTLEMENT_PENDING,
          CORRECTION_PENDING, RECONCILED)

# ── EXECUTION ELIGIBILITY OF THE WINNER ───────────────────────────────
E_DISPATCHED = "DISPATCHED"
E_HOLD = "HOLD_NEEDS_NO_ORDER"
E_SUBMISSION_DISABLED = "WOULD_DISPATCH_BUT_SUBMISSION_IS_DISABLED"
E_NOTHING_SELECTABLE = "NOTHING_WAS_SELECTABLE"
E_BLOCKED = "BLOCKED"                      # prefix: "BLOCKED:<gate>"
E_NOT_DISPATCHED = "NOT_DISPATCHED"        # prefix: "NOT_DISPATCHED:<why>"

R_SCHEMA = "THE_XAVIER_DECISION_TABLE_IS_NOT_IN_THIS_DATABASE"
R_STATE = "THAT_IS_NOT_A_XAVIER_RESPONSIBILITY_STATE"
R_PLAN_REQUIRED = "A_CHOSEN_ORDER_ACTION_MUST_NAME_ITS_RANKED_PLAN"


async def has_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_xavier_decisions')") is not None
    except Exception:                                           # noqa: BLE001
        return False


def _dt(epoch: float):
    import datetime as _d
    return _d.datetime.fromtimestamp(float(epoch), _d.timezone.utc)


def decision_id_for(*, intent_id: str, decided_at: float) -> str:
    """Deterministic: a re-run of the same review reaches the same row."""
    blob = "%s|%.3f" % (intent_id, float(decided_at))
    return "xav:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


async def record_decision(conn, *, account_id: str, venue: str,
                          intent_id: str, decided_at: float,
                          responsibility_state: str,
                          execution_eligibility: str,
                          alternatives: list, reasoning: dict,
                          expected_economics: dict, residual_exposure: dict,
                          evidence: dict, obligations: list,
                          chosen_action: str | None = None,
                          chosen_plan_digest: str | None = None,
                          decision_id: str | None = None,
                          portfolio_group_id: str | None = None,
                          us_market_slug: str | None = None,
                          next_review_at: float | None = None) -> dict:
    """PERSIST ONE REVIEW, BEFORE DISPATCH. Idempotent on (intent, instant).

    Never raises on the decision path: a refusal is returned by name."""
    out: dict[str, Any] = {"version": VERSION}
    if responsibility_state not in STATES:
        return dict(out, ok=False, refusal=R_STATE,
                    state=responsibility_state)
    if chosen_action and chosen_action != "HOLD" and not chosen_plan_digest:
        return dict(out, ok=False, refusal=R_PLAN_REQUIRED,
                    chosen_action=chosen_action)
    if not await has_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    xid = decision_id_for(intent_id=intent_id, decided_at=decided_at)
    try:
        status = await conn.execute(
            "INSERT INTO bettor_xavier_decisions (xavier_decision_id, "
            " decision_id, account_id, venue, intent_id, portfolio_group_id, "
            " us_market_slug, decided_at, xavier_version, "
            " responsibility_state, chosen_action, chosen_plan_digest, "
            " execution_eligibility, alternatives, reasoning, "
            " expected_economics, residual_exposure, evidence, obligations, "
            " next_review_at) VALUES ($1,$2,$3,$4,$5,$6,$7,to_timestamp($8),"
            " $9,$10,$11,$12,$13,$14::jsonb,$15::jsonb,$16::jsonb,$17::jsonb,"
            " $18::jsonb,$19::jsonb,$20) ON CONFLICT (xavier_decision_id) "
            " DO NOTHING",
            xid, decision_id, account_id, venue, intent_id,
            portfolio_group_id, us_market_slug, float(decided_at), VERSION,
            responsibility_state, chosen_action, chosen_plan_digest,
            execution_eligibility, json.dumps(alternatives, default=str),
            json.dumps(reasoning, default=str),
            json.dumps(expected_economics, default=str),
            json.dumps(residual_exposure, default=str),
            json.dumps(evidence, default=str),
            json.dumps(obligations, default=str),
            None if next_review_at is None else _dt(next_review_at))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal="XAVIER_DECISION_WRITE_FAILED",
                    error=type(exc).__name__)
    return dict(out, ok=True, refusal=None, xavier_decision_id=xid,
                already=not str(status).endswith(" 1"))


async def record_dispatch(conn, *, xavier_decision_id: str,
                          result: dict, at: float | None = None) -> dict:
    """WHAT THE VENUE DID WITH THE WINNER, recorded once."""
    try:
        status = await conn.execute(
            "UPDATE bettor_xavier_decisions SET dispatch_result=$2::jsonb, "
            " dispatch_recorded_at=to_timestamp($3) "
            " WHERE xavier_decision_id=$1 AND dispatch_result IS NULL",
            xavier_decision_id, json.dumps(result or {}, default=str),
            float(at if at is not None else time.time()))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "refusal": "XAVIER_DISPATCH_WRITE_FAILED",
                "error": type(exc).__name__}
    return {"ok": True, "written": str(status).endswith(" 1")}


def _row(r) -> dict:
    d = dict(r)
    for k in ("alternatives", "reasoning", "expected_economics",
              "residual_exposure", "evidence", "obligations",
              "dispatch_result"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except ValueError:
                pass
    return d


async def latest_decisions(conn, *, account_id: str | None = None,
                           venue: str | None = None,
                           limit: int = 50) -> dict:
    """THE NEWEST REVIEW OF EACH POSITION, newest positions first."""
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_SCHEMA, "positions": []}
    args: list = []
    where = []
    if account_id:
        args.append(account_id)
        where.append("account_id = $%d" % len(args))
    if venue:
        args.append(venue)
        where.append("venue = $%d" % len(args))
    args.append(int(limit))
    sql = ("SELECT DISTINCT ON (intent_id) * FROM bettor_xavier_decisions "
           + ("WHERE " + " AND ".join(where) + " " if where else "")
           + "ORDER BY intent_id, decided_at DESC")
    sql = ("SELECT * FROM (%s) q ORDER BY decided_at DESC LIMIT $%d"
           % (sql, len(args)))
    rows = [_row(r) for r in await conn.fetch(sql, *args)]
    return {"ok": True, "refusal": None, "positions": rows}


async def history(conn, *, intent_id: str, limit: int = 100) -> dict:
    """EVERY REVIEW OF ONE POSITION, newest first."""
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_SCHEMA, "decisions": []}
    rows = [_row(r) for r in await conn.fetch(
        "SELECT * FROM bettor_xavier_decisions WHERE intent_id=$1 "
        " ORDER BY decided_at DESC LIMIT $2", intent_id, int(limit))]
    return {"ok": True, "refusal": None, "decisions": rows}


def describe() -> dict:
    return {"name": NAME, "version": VERSION, "states": list(STATES),
            "is_a_new_execution_lane": False,
            "what_it_is": (
                "the persisted record of the scheduled funded servicing "
                "pass's responsibility for each position, from its first "
                "fill until reconciled; orders go only through the existing "
                "bound-plan dispatch")}
