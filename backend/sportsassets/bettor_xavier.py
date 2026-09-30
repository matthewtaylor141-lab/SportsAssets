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
import math
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
R_WRITE_FAILED = "XAVIER_DECISION_WRITE_FAILED"
R_RECORD_ID_HELD_BY_ANOTHER_REVIEW = (
    "ANOTHER_REVIEW_ALREADY_HOLDS_THIS_RECORD_ID_WITH_A_DIFFERENT_CHOICE")


async def has_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_xavier_decisions')") is not None
    except Exception:                                           # noqa: BLE001
        return False


def _dt(epoch: float):
    import datetime as _d
    return _d.datetime.fromtimestamp(float(epoch), _d.timezone.utc)


def decision_id_for(*, intent_id: str, decided_at: float,
                    salt: str | None = None) -> str:
    """Deterministic: a re-run of the same review reaches the same row.

    `salt` separates a record that is NOT a decision (a review refused the
    group's lock) from the decision another session is taking at the same
    instant, so the refusal can never occupy the row the decision needs."""
    blob = "%s|%.3f" % (intent_id, float(decided_at))
    if salt:
        blob += "|" + str(salt)
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
                          next_review_at: float | None = None,
                          salt: str | None = None) -> dict:
    """PERSIST ONE REVIEW, BEFORE DISPATCH. Idempotent on (intent, instant).

    A replay of the same review finds its row (`already=True`). A DIFFERENT
    review that lands on the same id -- same position, same instant, another
    choice -- is refused (`R_RECORD_ID_HELD_BY_ANOTHER_REVIEW`): the row that
    exists is not this review's record, so nothing may be dispatched on it.

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
    xid = decision_id_for(intent_id=intent_id, decided_at=decided_at,
                          salt=salt)
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
        return dict(out, ok=False, refusal=R_WRITE_FAILED,
                    error=type(exc).__name__)
    already = not str(status).endswith(" 1")
    if already:
        try:
            prior = await conn.fetchrow(
                "SELECT chosen_action, chosen_plan_digest, decision_id "
                "  FROM bettor_xavier_decisions WHERE xavier_decision_id=$1",
                xid)
        except Exception as exc:                                # noqa: BLE001
            return dict(out, ok=False, refusal=R_WRITE_FAILED,
                        error=type(exc).__name__)
        if prior is None or (prior["chosen_action"], prior[
                "chosen_plan_digest"], prior["decision_id"]) != (
                chosen_action, chosen_plan_digest, decision_id):
            return dict(out, ok=False,
                        refusal=R_RECORD_ID_HELD_BY_ANOTHER_REVIEW,
                        xavier_decision_id=xid,
                        stored=None if prior is None else dict(prior))
    return dict(out, ok=True, refusal=None, xavier_decision_id=xid,
                already=already)


# ── EXECUTION HISTORY (migration 148 `bettor_xavier_execution_events`) ──
# The decision row is immutable. What execution then does with it -- the
# claim that precedes any send, the venue's acknowledgement, each partial
# fill, a cancellation, the terminal status, a recovery of a lost
# acknowledgement, a correction -- is appended here, idempotently, each event
# naming the decision, the plan digest and the venue identities.
K_CLAIMED = "DISPATCH_CLAIMED"
K_NOT_SENT = "NOT_SENT"
K_ACK = "ACKNOWLEDGED"
K_REFUSED = "REFUSED"
K_UNKNOWN = "UNKNOWN_OUTCOME"
K_FILL = "FILL"
K_CANCELLED = "CANCELLED"
K_TERMINAL = "TERMINAL"
K_RECOVERED = "RECOVERED"
K_CORRECTION = "CORRECTION"
EVENT_KINDS = (K_CLAIMED, K_NOT_SENT, K_ACK, K_REFUSED, K_UNKNOWN, K_FILL,
               K_CANCELLED, K_TERMINAL, K_RECOVERED, K_CORRECTION)
SOURCES = ("DISPATCHER", "SEND_RESPONSE", "RECOVERY_READ",
           "ORDER_STATUS_READ", "FILLS_LEDGER", "SETTLEMENT_CORRECTION",
           "OPERATOR_RECONCILIATION")

# execution status derived from the events (never stored)
X_NOT_CLAIMED = "NOT_CLAIMED"
X_NOT_SENT = "NOT_SENT"
# a claim with nothing after it: the process may have died between the claim
# and the send, so whether an order reached the venue is UNKNOWN -- exposure
# counts and the recovery investigation owns it, exactly like a lost ack
X_CLAIMED_OUTCOME_UNRECORDED = "CLAIMED_SEND_OUTCOME_NOT_RECORDED"
X_UNRESOLVED = "UNRESOLVED"
X_WORKING = "WORKING"
X_REFUSED = "REFUSED"
X_TERMINAL = "TERMINAL"          # prefix: "TERMINAL:<venue status>"
UNRESOLVED_STATUSES = (X_CLAIMED_OUTCOME_UNRECORDED, X_UNRESOLVED)

R_NO_DECISION = "THERE_IS_NO_XAVIER_DECISION_WITH_THAT_ID"
R_ALREADY_CLAIMED = "THIS_DECISION_WAS_ALREADY_CLAIMED_FOR_DISPATCH"
R_PLAN_ALREADY_CLAIMED = "THIS_PLAN_WAS_ALREADY_CLAIMED_BY_ANOTHER_DECISION"
R_NOT_THE_WINNING_PLAN = "ONLY_THE_DECISIONS_CHOSEN_PLAN_CAN_BE_CLAIMED"
R_NO_CLAIM = "NOTHING_WAS_CLAIMED_SO_NOTHING_CAN_HAVE_BEEN_SENT"
R_CONFLICTING_RETRY = "THE_SAME_EVENT_KEY_WAS_REPLAYED_WITH_DIFFERENT_FACTS"
R_EVENT_KIND = "THAT_IS_NOT_A_XAVIER_EXECUTION_EVENT_KIND"
R_EVENT_WRITE = "XAVIER_EXECUTION_EVENT_WRITE_FAILED"


async def has_event_schema(conn) -> bool:
    try:
        return await conn.fetchval(
            "SELECT to_regclass('bettor_xavier_execution_events')") \
            is not None
    except Exception:                                           # noqa: BLE001
        return False


def _num(v):
    if v is None:
        return None
    return format(float(v), ".6f")


def _fact_sha(*, kind, venue_order_id, cumulative_filled_qty,
              terminal_status, supersedes_event_id) -> str:
    """The venue FACT an event asserts. Which reader saw it, and its prose
    evidence, are not part of the fact: two readers observing the same
    cumulative fill agree; the same key asserting a different fact does not."""
    blob = json.dumps([kind, venue_order_id, _num(cumulative_filled_qty),
                       terminal_status, supersedes_event_id])
    return hashlib.sha256(blob.encode()).hexdigest()


def event_key(*, xavier_decision_id: str, kind: str,
              venue_order_id: str | None = None,
              cumulative_filled_qty=None, terminal_status: str | None = None,
              supersedes_event_id: int | None = None) -> str:
    """Deterministic identity of one execution fact. A repeated observation
    of the same fill (same order, same cumulative quantity) or the same
    terminal status reaches the same key and appends nothing."""
    blob = "|".join(str(x) for x in (
        xavier_decision_id, kind, venue_order_id or "",
        _num(cumulative_filled_qty) or "", terminal_status or "",
        supersedes_event_id or ""))
    return "xev:" + hashlib.sha256(blob.encode()).hexdigest()[:32]


async def _decision(conn, xavier_decision_id: str):
    return await conn.fetchrow(
        "SELECT xavier_decision_id, decision_id, account_id, venue, "
        " intent_id, portfolio_group_id, chosen_action, chosen_plan_digest "
        " FROM bettor_xavier_decisions WHERE xavier_decision_id=$1",
        xavier_decision_id)


async def claim_dispatch(conn, *, xavier_decision_id: str, plan_digest: str,
                         decision_id: str | None = None,
                         order_intent_id: str | None = None,
                         client_order_id: str | None = None,
                         at: float | None = None,
                         evidence: dict | None = None) -> dict:
    """THE DUPLICATE-SUBMISSION GUARD. Append the one DISPATCH_CLAIMED event
    for this decision BEFORE anything is sent. `claimed` is True only for
    the call that appended it: a replay, a restart or a concurrent review
    gets `claimed=False` with the reason and MUST NOT send. The database
    refuses a claim whose plan is not the decision's chosen plan, a second
    claim for the decision, and a second claim for the same plan."""
    out: dict[str, Any] = {"claimed": False}
    if not await has_event_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    d = await _decision(conn, xavier_decision_id)
    if d is None:
        return dict(out, ok=False, refusal=R_NO_DECISION)
    if d["chosen_plan_digest"] != plan_digest or not d["chosen_action"] \
            or d["chosen_action"] == "HOLD":
        return dict(out, ok=False, refusal=R_NOT_THE_WINNING_PLAN,
                    chosen_action=d["chosen_action"],
                    chosen_plan_digest=d["chosen_plan_digest"],
                    plan_digest=plan_digest)
    key = "xclaim:" + xavier_decision_id
    sha = _fact_sha(kind=K_CLAIMED, venue_order_id=None,
                    cumulative_filled_qty=None, terminal_status=None,
                    supersedes_event_id=None)
    import asyncpg
    try:
        # a savepoint when the caller holds a transaction: a refused claim
        # must not abort the caller's work
        async with conn.transaction():
            eid = await conn.fetchval(
            "INSERT INTO bettor_xavier_execution_events (idempotency_key, "
            " fact_sha, xavier_decision_id, decision_id, plan_digest, "
            " order_intent_id, position_intent_id, portfolio_group_id, "
            " account_id, venue, client_order_id, event_kind, source, "
            " evidence, occurred_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
            " $11,'DISPATCH_CLAIMED','DISPATCHER',$12::jsonb,"
            " to_timestamp($13)) ON CONFLICT (idempotency_key) DO NOTHING "
            " RETURNING event_id",
            key, sha, xavier_decision_id,
            decision_id if decision_id is not None else d["decision_id"],
            plan_digest, order_intent_id, d["intent_id"],
            d["portfolio_group_id"], d["account_id"], d["venue"],
            client_order_id, json.dumps(evidence or {}, default=str),
            float(at if at is not None else time.time()))
    except asyncpg.exceptions.UniqueViolationError as exc:
        # A CONCURRENT CLAIM WON THE RACE. Which unique index caught this one
        # depends on timing (the idempotency key, the one-claim-per-decision
        # index or the one-claim-per-plan index), so the refusal is named from
        # who owns the winning claim, not from the index that fired. The
        # lookup never raises (the claim is on the decision path): unreadable,
        # it is named ALREADY_CLAIMED, which sends nothing either way.
        try:
            owner = await conn.fetchval(
                "SELECT xavier_decision_id FROM bettor_xavier_execution_events"
                " WHERE event_kind='DISPATCH_CLAIMED' AND (xavier_decision_id="
                "$1 OR plan_digest=$2) ORDER BY event_id LIMIT 1",
                xavier_decision_id, plan_digest)
        except Exception:                                       # noqa: BLE001
            owner = None
        return dict(out, ok=True,
                    refusal=(R_ALREADY_CLAIMED
                             if owner in (None, xavier_decision_id)
                             else R_PLAN_ALREADY_CLAIMED),
                    constraint=getattr(exc, "constraint_name", None))
    except asyncpg.exceptions.RaiseError as exc:
        return dict(out, ok=False, refusal=R_NOT_THE_WINNING_PLAN,
                    detail=str(exc)[:300])
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_EVENT_WRITE,
                    error=type(exc).__name__)
    if eid is None:
        return dict(out, ok=True, refusal=R_ALREADY_CLAIMED)
    return {"ok": True, "claimed": True, "refusal": None, "event_id": eid}


async def record_execution_event(conn, *, xavier_decision_id: str,
                                 kind: str, source: str,
                                 occurred_at: float | None = None,
                                 venue_order_id: str | None = None,
                                 client_order_id: str | None = None,
                                 order_intent_id: str | None = None,
                                 cumulative_filled_qty=None,
                                 avg_fill_price_cents=None,
                                 fee_usd=None,
                                 terminal_status: str | None = None,
                                 supersedes_event_id: int | None = None,
                                 evidence: dict | None = None,
                                 idempotency_key: str | None = None) -> dict:
    """APPEND ONE EXECUTION FACT, idempotently. The same fact replayed
    appends nothing (`appended=False, already=True`); the same key carrying a
    different fact is refused (`R_CONFLICTING_RETRY`), never merged. Every
    event but the claim requires the claim, and carries the claimed plan."""
    out: dict[str, Any] = {"appended": False}
    if kind not in EVENT_KINDS or kind == K_CLAIMED:
        return dict(out, ok=False, refusal=R_EVENT_KIND, kind=kind)
    if not await has_event_schema(conn):
        return dict(out, ok=False, refusal=R_SCHEMA)
    d = await _decision(conn, xavier_decision_id)
    if d is None:
        return dict(out, ok=False, refusal=R_NO_DECISION)
    claim = await conn.fetchrow(
        "SELECT plan_digest, decision_id, order_intent_id, client_order_id "
        " FROM bettor_xavier_execution_events WHERE xavier_decision_id=$1 "
        " AND event_kind='DISPATCH_CLAIMED'", xavier_decision_id)
    if claim is None:
        return dict(out, ok=False, refusal=R_NO_CLAIM)
    key = idempotency_key or event_key(
        xavier_decision_id=xavier_decision_id, kind=kind,
        venue_order_id=venue_order_id,
        cumulative_filled_qty=cumulative_filled_qty,
        terminal_status=terminal_status,
        supersedes_event_id=supersedes_event_id)
    sha = _fact_sha(kind=kind, venue_order_id=venue_order_id,
                    cumulative_filled_qty=cumulative_filled_qty,
                    terminal_status=terminal_status,
                    supersedes_event_id=supersedes_event_id)
    try:
        async with conn.transaction():
            eid = await conn.fetchval(
            "INSERT INTO bettor_xavier_execution_events (idempotency_key, "
            " fact_sha, xavier_decision_id, decision_id, plan_digest, "
            " order_intent_id, position_intent_id, portfolio_group_id, "
            " account_id, venue, client_order_id, venue_order_id, "
            " event_kind, cumulative_filled_qty, avg_fill_price_cents, "
            " fee_usd, terminal_status, source, supersedes_event_id, "
            " evidence, occurred_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,"
            " $11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb,"
            " to_timestamp($21)) ON CONFLICT (idempotency_key) DO NOTHING "
            " RETURNING event_id",
            key, sha, xavier_decision_id, claim["decision_id"],
            claim["plan_digest"],
            order_intent_id or claim["order_intent_id"], d["intent_id"],
            d["portfolio_group_id"], d["account_id"], d["venue"],
            client_order_id or claim["client_order_id"], venue_order_id,
            kind, None if cumulative_filled_qty is None
            else float(cumulative_filled_qty),
            None if avg_fill_price_cents is None
            else float(avg_fill_price_cents),
            None if fee_usd is None else float(fee_usd), terminal_status,
            source, supersedes_event_id,
            json.dumps(evidence or {}, default=str),
            float(occurred_at if occurred_at is not None else time.time()))
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_EVENT_WRITE,
                    error=type(exc).__name__, detail=str(exc)[:300])
    if eid is not None:
        return {"ok": True, "appended": True, "already": False,
                "refusal": None, "event_id": eid, "idempotency_key": key}
    prior = await conn.fetchrow(
        "SELECT event_id, fact_sha FROM bettor_xavier_execution_events "
        " WHERE idempotency_key=$1", key)
    if prior is not None and prior["fact_sha"] == sha:
        return {"ok": True, "appended": False, "already": True,
                "refusal": None, "event_id": prior["event_id"],
                "idempotency_key": key}
    return dict(out, ok=False, refusal=R_CONFLICTING_RETRY,
                idempotency_key=key,
                prior_event_id=None if prior is None else prior["event_id"])


def summarise_events(events: list) -> dict:
    """THE CURRENT EXECUTION STATE, derived from the events every time
    (nothing derived is stored, so a later fill or correction is never
    hidden behind an earlier summary).

    Filled quantity per venue order: the newest CORRECTION that states a
    cumulative quantity, else the largest cumulative quantity any FILL /
    RECOVERED / ACKNOWLEDGED event observed (venue cumulative fills only
    grow; taking the maximum makes repeated and racing reads harmless)."""
    evs = sorted((dict(e) for e in events), key=lambda e: e["event_id"])
    out: dict[str, Any] = {"status": X_NOT_CLAIMED, "claimed": False,
                           "claimed_at": None, "plan_digest": None,
                           "order_intent_id": None, "venue_order_ids": [],
                           "filled_qty": 0.0, "filled_by_order": {},
                           "terminal_status": None, "events": len(evs),
                           "last_event_kind": None, "last_event_at": None}
    if not evs:
        return out
    by_order: dict[str, float] = {}
    corrected: dict[str, float] = {}
    orders: list = []
    status = X_NOT_CLAIMED
    terminal = None
    unknown_open = False
    for e in evs:
        k = e["event_kind"]
        vo = e.get("venue_order_id")
        if vo and vo not in orders:
            orders.append(vo)
        q = e.get("cumulative_filled_qty")
        if q is not None and vo:
            if k == K_CORRECTION:
                corrected[vo] = float(q)
            elif k in (K_FILL, K_RECOVERED, K_ACK):
                by_order[vo] = max(by_order.get(vo, 0.0), float(q))
        if k == K_CLAIMED:
            out.update(claimed=True, claimed_at=e.get("occurred_at"),
                       plan_digest=e.get("plan_digest"),
                       order_intent_id=e.get("order_intent_id"))
            status = X_CLAIMED_OUTCOME_UNRECORDED
        elif k == K_NOT_SENT:
            status = X_NOT_SENT
        elif k == K_UNKNOWN:
            unknown_open = True
            status = X_UNRESOLVED
        elif k == K_REFUSED:
            unknown_open = False
            status = X_REFUSED
        elif k in (K_ACK, K_FILL):
            unknown_open = False
            if terminal is None:
                status = X_WORKING
        elif k == K_RECOVERED:
            unknown_open = False
            ts = e.get("terminal_status")
            if ts:
                terminal = ts
            elif terminal is None:
                status = X_WORKING
        elif k in (K_CANCELLED, K_TERMINAL):
            unknown_open = False
            terminal = e.get("terminal_status") or (
                "CANCELLED" if k == K_CANCELLED else terminal)
        elif k == K_CORRECTION and e.get("terminal_status"):
            terminal = e["terminal_status"]
    if terminal is not None and not unknown_open:
        status = "%s:%s" % (X_TERMINAL, terminal)
    filled = {vo: corrected.get(vo, by_order.get(vo, 0.0))
              for vo in set(by_order) | set(corrected)}
    last = evs[-1]
    out.update(status=status, venue_order_ids=orders,
               filled_by_order=filled,
               filled_qty=round(sum(filled.values()), 6),
               terminal_status=terminal, last_event_kind=last["event_kind"],
               last_event_at=last.get("occurred_at"))
    return out


async def execution_events(conn, *, xavier_decision_id: str) -> list:
    if not await has_event_schema(conn):
        return []
    rows = await conn.fetch(
        "SELECT * FROM bettor_xavier_execution_events "
        " WHERE xavier_decision_id=$1 ORDER BY event_id", xavier_decision_id)
    return [_row(r) for r in rows]


async def execution_state(conn, *, xavier_decision_id: str) -> dict:
    evs = await execution_events(conn, xavier_decision_id=xavier_decision_id)
    return summarise_events(evs)


async def _executions_for(conn, ids: list) -> dict:
    if not ids or not await has_event_schema(conn):
        return {}
    rows = await conn.fetch(
        "SELECT * FROM bettor_xavier_execution_events "
        " WHERE xavier_decision_id = ANY($1::text[]) ORDER BY event_id",
        list(ids))
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r["xavier_decision_id"], []).append(_row(r))
    return {i: summarise_events(grouped.get(i, [])) for i in ids}


async def unresolved_claims(conn, *, account_id: str | None = None,
                            venue: str | None = None) -> list:
    """Every claimed decision whose send outcome is not established -- a
    claim with nothing after it, or an UNKNOWN_OUTCOME not yet answered.
    Exposure counts for each until the recovery investigation resolves it."""
    if not await has_event_schema(conn):
        return []
    args: list = []
    where = ["e.event_kind='DISPATCH_CLAIMED'"]
    if account_id:
        args.append(account_id)
        where.append("e.account_id=$%d" % len(args))
    if venue:
        args.append(venue)
        where.append("e.venue=$%d" % len(args))
    ids = [r["xavier_decision_id"] for r in await conn.fetch(
        "SELECT e.xavier_decision_id FROM bettor_xavier_execution_events e "
        " WHERE " + " AND ".join(where), *args)]
    states = await _executions_for(conn, ids)
    return [dict(s, xavier_decision_id=i) for i, s in states.items()
            if s["status"] in UNRESOLVED_STATUSES]


async def record_dispatch(conn, *, xavier_decision_id: str,
                          result: dict, at: float | None = None) -> dict:
    """COMPATIBILITY: translate one dispatch result into execution events.

    Nothing is written onto the decision. A result that says nothing was
    sent appends NOT_SENT when a claim exists (else nothing: there was no
    claim, so nothing can have been sent). A result that says something was
    sent requires the claim -- without it the send bypassed the duplicate
    guard, which is refused by name."""
    r = dict(result or {})
    when = float(at if at is not None else time.time())
    # THE ORDER THE SEND CREATED (an exit or hedge intent), named on every
    # event so a reader joins the Xavier history to the intents table.
    oi = r.get("order_intent_id")
    claim = None
    if await has_event_schema(conn):
        claim = await conn.fetchval(
            "SELECT event_id FROM bettor_xavier_execution_events "
            " WHERE xavier_decision_id=$1 AND event_kind='DISPATCH_CLAIMED'",
            xavier_decision_id)
    sent = bool(r.get("sent")) or bool(r.get("venue_order_id")) \
        or bool(r.get("unknown"))
    if not sent:
        if claim is None:
            return {"ok": True, "written": False, "refusal": None,
                    "reason": "NOTHING_WAS_CLAIMED_OR_SENT"}
        got = await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id,
            order_intent_id=oi, kind=K_NOT_SENT,
            source="DISPATCHER", occurred_at=when, evidence=r)
        return {"ok": got["ok"], "written": got.get("appended", False),
                "refusal": got.get("refusal")}
    if claim is None:
        return {"ok": False, "written": False, "refusal": R_NO_CLAIM}
    written = []
    vo = r.get("venue_order_id")
    if r.get("unknown"):
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id,
            order_intent_id=oi, kind=K_UNKNOWN,
            source="SEND_RESPONSE", occurred_at=when, evidence=r))
    elif r.get("refusal") and not vo:
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id,
            order_intent_id=oi, kind=K_REFUSED,
            source="SEND_RESPONSE", occurred_at=when, evidence=r))
    elif vo:
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id,
            order_intent_id=oi, kind=K_ACK,
            source="SEND_RESPONSE", occurred_at=when, venue_order_id=vo,
            evidence=r))
        if r.get("filled_qty") is not None:
            written.append(await record_execution_event(
                conn, xavier_decision_id=xavier_decision_id,
                order_intent_id=oi, kind=K_FILL,
                source="SEND_RESPONSE", occurred_at=when, venue_order_id=vo,
                cumulative_filled_qty=r["filled_qty"],
                avg_fill_price_cents=r.get("avg_fill_price_cents"),
                fee_usd=r.get("fee_usd")))
        if r.get("terminal_status"):
            written.append(await record_execution_event(
                conn, xavier_decision_id=xavier_decision_id,
                order_intent_id=oi, kind=K_TERMINAL,
                source="SEND_RESPONSE", occurred_at=when, venue_order_id=vo,
                terminal_status=r["terminal_status"]))
    bad = [w for w in written if not w.get("ok")]
    return {"ok": not bad, "written": any(w.get("appended") for w in written),
            "refusal": bad[0]["refusal"] if bad else None}


def _row(r) -> dict:
    d = dict(r)
    for k in ("alternatives", "reasoning", "expected_economics",
              "residual_exposure", "evidence", "obligations"):
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
    execs = await _executions_for(conn, [r["xavier_decision_id"]
                                         for r in rows])
    for r in rows:
        r["execution"] = execs.get(r["xavier_decision_id"])
    return {"ok": True, "refusal": None, "positions": rows}


async def history(conn, *, intent_id: str, limit: int = 100) -> dict:
    """EVERY REVIEW OF ONE POSITION, newest first."""
    if not await has_schema(conn):
        return {"ok": False, "refusal": R_SCHEMA, "decisions": []}
    rows = [_row(r) for r in await conn.fetch(
        "SELECT * FROM bettor_xavier_decisions WHERE intent_id=$1 "
        " ORDER BY decided_at DESC LIMIT $2", intent_id, int(limit))]
    execs = await _executions_for(conn, [r["xavier_decision_id"]
                                         for r in rows])
    for r in rows:
        r["execution"] = execs.get(r["xavier_decision_id"])
    return {"ok": True, "refusal": None, "decisions": rows}


def describe() -> dict:
    return {"name": NAME, "version": VERSION, "states": list(STATES),
            "is_a_new_execution_lane": False,
            "what_it_is": (
                "the persisted record of the scheduled funded servicing "
                "pass's responsibility for each position, from its first "
                "fill until reconciled; orders go only through the existing "
                "bound-plan dispatch")}


# ═════════════════════════════════════════════════════════════════════
# RESPONSIBILITY: WHICH POSITIONS ARE XAVIER'S, AND WHAT EACH STILL OWES
# ═════════════════════════════════════════════════════════════════════
#
# Xavier assumes a position when its entry receives ANY fill (a partial fill
# included) and keeps it until every resulting position, outstanding order and
# settlement obligation is reconciled. A position with ZERO residual is still
# Xavier's while an order of its own, an exit child, a leg claim of its group
# or a dispatch claim is unresolved -- a lost acknowledgement may be exposure.

OB_RESIDUAL = "RESIDUAL_INVENTORY"
OB_ENTRY_OUTSTANDING = "ENTRY_ORDER_OUTSTANDING"
OB_ENTRY_UNRESOLVED = "ENTRY_ORDER_UNRESOLVED_LOST_ACKNOWLEDGEMENT"
OB_EXIT_OUTSTANDING = "EXIT_ORDER_OUTSTANDING"
OB_EXIT_UNRESOLVED = "EXIT_ORDER_UNRESOLVED"
OB_CLAIM_UNRESOLVED = "GROUP_LEG_CLAIM_SENT_AND_UNRESOLVED"
OB_CLAIM_LIVE = "GROUP_LEG_CLAIM_HELD_BEFORE_SEND"
OB_DISPATCH_UNRESOLVED = "XAVIER_DISPATCH_CLAIMED_OUTCOME_UNRESOLVED"
OB_PROVISIONAL = "PROVISIONAL_ECONOMICS"
OB_SETTLEMENT_NOT_BOOKED = "SETTLEMENT_NOT_YET_BOOKED"
OB_DISAGREES = "SETTLEMENT_RE_READ_DISAGREES_AND_IS_UNANSWERED"

#: Which obligations put the position in which state, strongest first.
STATE_OF_OBLIGATION = (
    (ORDER_UNRESOLVED, (OB_ENTRY_UNRESOLVED, OB_EXIT_UNRESOLVED,
                        OB_CLAIM_UNRESOLVED, OB_DISPATCH_UNRESOLVED)),
    (ORDER_OUTSTANDING, (OB_ENTRY_OUTSTANDING, OB_EXIT_OUTSTANDING,
                         OB_CLAIM_LIVE)),
    (CORRECTION_PENDING, (OB_DISAGREES,)),
    (HELD, (OB_RESIDUAL,)),
    (SETTLEMENT_PENDING, (OB_PROVISIONAL, OB_SETTLEMENT_NOT_BOOKED)),
)

R_RESPONSIBILITY_UNREADABLE = "XAVIERS_RESPONSIBILITY_COULD_NOT_BE_READ"

_OUTSTANDING = ("INTENT_RECORDED", "SEND_ATTEMPTED", "ACKNOWLEDGED",
                "PARTIALLY_FILLED")

RESPONSIBILITY_SQL = """
    WITH e AS (
      SELECT i.intent_id, i.portfolio_group_id, i.leg_role, i.us_market_slug,
             i.event_key, i.state, i.order_intent, i.venue_order_id,
             coalesce(i.residual_qty, 0)::float8 AS residual,
             i.closed_at, i.closed_reason, i.created_at,
             coalesce((SELECT sum(f.qty) FROM bettor_funded_fills f
                        WHERE f.intent_id = i.intent_id
                          AND f.direction = 'ENTRY'), 0)::float8 AS filled
        FROM bettor_funded_intents i
       WHERE i.kind = 'ENTRY' AND i.account_id = $1
         AND upper(i.venue) = upper($2)
         AND ($4::text[] IS NULL OR i.intent_id = ANY($4::text[])))
    SELECT e.*,
      (SELECT count(*) FROM bettor_funded_intents c
        WHERE c.parent_intent_id = e.intent_id AND c.kind = 'EXIT'
          AND c.state = ANY($3::text[])) AS exits_outstanding,
      (SELECT count(*) FROM bettor_funded_intents c
        WHERE c.parent_intent_id = e.intent_id AND c.kind = 'EXIT'
          AND c.state = 'UNRESOLVED') AS exits_unresolved,
      (SELECT count(*) FROM bettor_funded_leg_reservations r
        WHERE r.group_id = e.portfolio_group_id
          AND r.state IN ('SEND_ATTEMPTED', 'AMBIGUOUS')) AS claims_unresolved,
      (SELECT count(*) FROM bettor_funded_leg_reservations r
        WHERE r.group_id = e.portfolio_group_id
          AND r.state IN ('HELD', 'COMMITTED')) AS claims_live,
      (SELECT count(*) FROM bettor_funded_economics x
         JOIN bettor_funded_intents xi ON xi.intent_id = x.intent_id
        WHERE (xi.intent_id = e.intent_id OR xi.parent_intent_id = e.intent_id)
          AND x.provisional) AS provisional_events,
      (SELECT count(*) FROM bettor_funded_economics x
        WHERE x.intent_id = e.intent_id
          AND x.kind = 'SETTLEMENT') AS settlement_events,
      (SELECT r.verdict FROM bettor_funded_settlement_rechecks r
        WHERE r.intent_id = e.intent_id AND r.verdict <> 'NOT_ESTABLISHED'
        ORDER BY r.read_at DESC, r.recheck_id DESC LIMIT 1) AS newest_recheck
      FROM e
     WHERE e.filled > 0 OR e.state = 'UNRESOLVED'
     ORDER BY e.created_at
"""


def obligations_of(row: dict, *, dispatch_unresolved: bool = False) -> list:
    """EVERY OPEN OBLIGATION OF ONE POSITION, BY NAME. Pure."""
    r = dict(row or {})
    obs = []

    def _ob(name, **detail):
        obs.append(dict({"obligation": name}, **detail))

    if float(r.get("residual") or 0) > 0 and r.get("closed_at") is None:
        _ob(OB_RESIDUAL, qty=float(r["residual"]))
    st = str(r.get("state") or "")
    if st == "UNRESOLVED":
        _ob(OB_ENTRY_UNRESOLVED, state=st,
            why=("the entry's answer was lost: the venue may hold an order "
                 "or a fill, so the exposure is counted until it is "
                 "resolved"))
    elif st in _OUTSTANDING:
        _ob(OB_ENTRY_OUTSTANDING, state=st)
    if int(r.get("exits_unresolved") or 0):
        _ob(OB_EXIT_UNRESOLVED, count=int(r["exits_unresolved"]))
    if int(r.get("exits_outstanding") or 0):
        _ob(OB_EXIT_OUTSTANDING, count=int(r["exits_outstanding"]))
    if int(r.get("claims_unresolved") or 0):
        _ob(OB_CLAIM_UNRESOLVED, count=int(r["claims_unresolved"]),
            group_id=r.get("portfolio_group_id"))
    if int(r.get("claims_live") or 0):
        _ob(OB_CLAIM_LIVE, count=int(r["claims_live"]),
            group_id=r.get("portfolio_group_id"))
    if dispatch_unresolved:
        _ob(OB_DISPATCH_UNRESOLVED,
            why=("a Xavier decision was claimed for dispatch and no outcome "
                 "was recorded after it: whether an order left is unknown"))
    if int(r.get("provisional_events") or 0):
        _ob(OB_PROVISIONAL, events=int(r["provisional_events"]))
    if r.get("closed_reason") in ("SETTLED_BY_THE_VENUE",
                                  "VOIDED_BY_THE_VENUE") \
            and not int(r.get("settlement_events") or 0):
        _ob(OB_SETTLEMENT_NOT_BOOKED, closed_reason=r.get("closed_reason"))
    if str(r.get("newest_recheck") or "") == "DISAGREES":
        _ob(OB_DISAGREES,
            why=("the newest established re-read of this settlement "
                 "disagrees with what was booked, and no later reading or "
                 "correction has answered it"))
    return obs


def state_of(obligations: list) -> str:
    """The responsibility state the obligations put a position in. Pure."""
    names = {o.get("obligation") for o in (obligations or [])}
    for state, members in STATE_OF_OBLIGATION:
        if names & set(members):
            return state
    return RECONCILED


async def responsibilities(conn, *, account_id: str, venue: str,
                           now: float | None = None,
                           intent_ids: list | None = None) -> dict:
    """EVERY POSITION XAVIER IS RESPONSIBLE FOR, with its state and its open
    obligations by name. A position leaves only when ALL are cleared; the
    reconciled ones are counted, not listed. `intent_ids` narrows the read to
    those positions (the fresh re-read a review takes under its group's
    lock). Never raises."""
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"version": VERSION, "at": at,
                           "account_id": account_id, "venue": venue,
                           "positions": [], "reconciled": 0}
    try:
        rows = [dict(r) for r in await conn.fetch(
            RESPONSIBILITY_SQL, str(account_id), str(venue),
            list(_OUTSTANDING),
            None if intent_ids is None else [str(i) for i in intent_ids])]
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_RESPONSIBILITY_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    try:
        claims = {c.get("position_intent_id") or "" for c in
                  await _unresolved_claim_positions(conn, account_id, venue)}
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, refusal=R_RESPONSIBILITY_UNREADABLE,
                    error="%s: %s" % (type(exc).__name__, str(exc)[:200]))
    for r in rows:
        obs = obligations_of(r, dispatch_unresolved=r["intent_id"] in claims)
        state = state_of(obs)
        if state == RECONCILED:
            out["reconciled"] += 1
            continue
        out["positions"].append({
            "intent_id": r["intent_id"],
            "portfolio_group_id": r.get("portfolio_group_id"),
            "leg_role": r.get("leg_role"),
            "us_market_slug": r.get("us_market_slug"),
            "state": state, "obligations": obs,
            "filled_qty": float(r.get("filled") or 0),
            "residual_qty": float(r.get("residual") or 0),
            "closed_reason": r.get("closed_reason")})
    out["assumed_when"] = ("the entry received ANY fill, a partial fill "
                           "included, or its answer was lost")
    out["released_when"] = "every obligation above is cleared"
    return dict(out, ok=True, refusal=None)


async def _unresolved_claim_positions(conn, account_id, venue) -> list:
    """Dispatch claims with no recorded outcome, with their position."""
    rows = await unresolved_claims(conn, account_id=account_id, venue=venue)
    if not rows:
        return []
    ids = [r["xavier_decision_id"] for r in rows]
    got = await conn.fetch(
        "SELECT xavier_decision_id, intent_id AS position_intent_id "
        "  FROM bettor_xavier_decisions WHERE xavier_decision_id = ANY($1)",
        ids)
    return [dict(g) for g in got]


async def filled_scope(conn, *, intent_id: str,
                       group_id: str | None = None) -> dict:
    """THE FILLED QUANTITY, FROM THE FILLS LEDGER -- the scope a decision's
    bound is conditional on. `matched_units` is `min(primary filled, hedge
    filled)` for a group holding both roles and the single role's filled
    quantity otherwise -- exactly `bettor_funded_learning.observed_scope`'s
    rule, so the scope written at decision time and the scope observed at
    the outcome are the same measurement. Never raises."""
    out = {"intent_id": intent_id, "group_id": group_id,
           "intent_filled": None, "matched_units": None,
           "filled_by_role": {}, "source": "bettor_funded_fills (ENTRY)"}
    try:
        own = await conn.fetchval(
            "SELECT coalesce(sum(qty), 0)::float8 FROM bettor_funded_fills "
            " WHERE intent_id=$1 AND direction='ENTRY'", intent_id)
        out["intent_filled"] = round(float(own or 0.0), 6)
        by_role: dict[str, float] = {}
        if group_id:
            for r in await conn.fetch(
                    "SELECT coalesce(i.leg_role, 'PRIMARY') AS role, "
                    "       coalesce(sum(f.qty) FILTER "
                    "         (WHERE f.direction='ENTRY'), 0)::float8 AS q "
                    "  FROM bettor_funded_intents i LEFT JOIN "
                    "       bettor_funded_fills f ON f.intent_id=i.intent_id "
                    " WHERE i.portfolio_group_id=$1 AND i.kind='ENTRY' "
                    " GROUP BY 1", group_id):
                by_role[str(r["role"])] = round(float(r["q"]), 6)
        out["filled_by_role"] = by_role
        roles = [x for x in ("PRIMARY", "HEDGE") if by_role.get(x)]
        out["matched_units"] = (min(by_role[x] for x in roles)
                                if len(roles) > 1 else out["intent_filled"])
    except Exception as exc:                                    # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


# ═════════════════════════════════════════════════════════════════════
# THE SAME WHOLE-POSITION ECONOMICS ON EVERY ALTERNATIVE
# ═════════════════════════════════════════════════════════════════════

EXEC_NO_ORDER = "NO_ORDER"
EXEC_FOK = "FOK_AT_DISPLAYED_DEPTH_FILL_NOT_GUARANTEED"
LIMITS_NOT_APPROVED = "LIMITS_NOT_APPROVED"
LIMITS_NOT_APPLICABLE = "NOT_APPLICABLE_THIS_ACTION_ADDS_NO_EXPOSURE"
CAPITAL_END_NOT_STATED = "THE_CATALOGUE_STATES_NO_SCHEDULED_END_FOR_THE_FIXTURE"
ECONOMIC_FIELDS = ("expected_net_usd", "increment_vs_hold_usd",
                   "worst_case_net_usd", "worst_case_remaining_loss_usd",
                   "capital_required_usd", "capital_released_usd",
                   "capital_duration_h", "fees_usd", "execution_uncertainty",
                   "unpaired_residual_qty", "unpaired_value_at_risk_usd",
                   "limits_check")
#: Keys of a ranked candidate worth carrying onto the record beside the
#: standard fields. Structures, tables and predictions stay on the ledger row.
_CARRIED = ("action", "qty", "value_usd", "candidate_id", "plan_digest",
            "leg_role", "intent_id", "taxonomy", "evidence_quality",
            "value_is_conditional", "locks_a_loss", "depth_limited",
            "limit_price", "proceeds_per_contract", "inputs_expire_at",
            "value_source", "group_value_basis", "covered_qty",
            "uncovered_qty", "search_screen")


def _f(v):
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def capital_duration(*, game_start, now: float) -> dict:
    """HOW LONG CAPITAL STAYS COMMITTED, as far as the catalogue says. Pure.

    The catalogue states the fixture's scheduled START, not its end, and
    settlement follows the end. So `capital_duration_h` is None with the
    reason named, and the START gives the known lower bound -- inventing a
    game length would be inventing the number."""
    out = {"capital_duration_h": None, "reason": CAPITAL_END_NOT_STATED,
           "capital_committed_at_least_h": None, "game_start": None}
    if game_start is None:
        out["lower_bound_reason"] = "THE_CATALOGUE_GAME_START_IS_NOT_KNOWN"
        return out
    try:
        gs = game_start.timestamp() if hasattr(game_start, "timestamp") \
            else float(game_start)
    except (TypeError, ValueError):
        out["lower_bound_reason"] = "THE_CATALOGUE_GAME_START_IS_UNREADABLE"
        return out
    out["game_start"] = gs
    out["capital_committed_at_least_h"] = round(max(0.0, gs - float(now))
                                                / 3600.0, 4)
    return out


GROUP_WORST_CASE_NOT_ESTABLISHED = (
    "THE_GROUPS_JOINT_WORST_CASE_WAS_NOT_ESTABLISHED_AND_ONE_LEG_IS_NOT_IT")


def group_unpaired_after(group: dict, *, leg_role, kept) -> dict:
    """The group's unpaired quantity AFTER one leg keeps `kept`. Pure.

    Units pair one-for-one, so what is unpaired is the difference of the two
    legs' residuals, valued at the basis of whichever leg is longer."""
    g = dict(group or {})
    p = _f(g.get("primary_residual_qty"))
    h = _f(g.get("hedge_residual_qty"))
    k = _f(kept)
    if k is None or p is None or h is None:
        return {"unpaired_qty": None, "unpaired_role": None,
                "unpaired_value_at_risk_usd": None}
    if str(leg_role or "PRIMARY") == "HEDGE":
        h = k
    else:
        p = k
    role = "PRIMARY" if p > h else "HEDGE" if h > p else None
    unp = round(abs(p - h), 6)
    b = _f(g.get("primary_basis_per_contract") if role == "PRIMARY"
           else g.get("hedge_basis_per_contract"))
    return {"unpaired_qty": unp, "unpaired_role": role,
            "unpaired_value_at_risk_usd": (0.0 if not unp else None
                                           if b is None else round(b * unp,
                                                                   6))}


def economics(candidate: dict, *, ctx: dict) -> dict:
    """THE STANDARD WHOLE-POSITION FIELDS FOR ONE ALTERNATIVE. Pure.

    `ctx` carries the position's quantity, per-contract basis, HOLD's value,
    the capital-duration reading, the acquisition rows and the limit checks.
    A field that cannot be computed is None, with its reason in
    `field_reasons` -- never a guessed number. `worst_case_net_usd` is a TRUE
    worst case: for HOLD the loss if the position loses; for a depth-limited
    exit the realised slice plus the retained part LOSING (not held at its
    expectation); for an acquisition the whole-position floor over joint
    outcomes net of fees."""
    c = dict(candidate or {})
    x = dict(ctx or {})
    act = str(c.get("action") or "")
    why: dict[str, str] = {}
    q = _f(x.get("qty"))
    basis = _f(x.get("basis_per_contract"))
    hold = _f(x.get("hold_value_usd"))
    value = _f(c.get("value_usd"))
    cap = dict(x.get("capital") or {})
    out: dict[str, Any] = {k: None for k in ECONOMIC_FIELDS}
    out["expected_net_usd"] = value
    if value is None:
        why["expected_net_usd"] = "THIS_ALTERNATIVE_CARRIES_NO_SCORED_VALUE"
    if value is not None and hold is not None:
        out["increment_vs_hold_usd"] = round(value - hold, 6)
    else:
        why["increment_vs_hold_usd"] = ("HOLD_IS_NOT_PRICED" if hold is None
                                        else "THIS_ALTERNATIVE_IS_NOT_SCORED")
    wc = _f(c.get("worst_case_net_usd"))
    # IN A TWO-LEG GROUP the single-leg fallbacks below are WRONG: the loss
    # when the primary loses is offset or deepened by the other leg, so a
    # `-basis x qty` figure is not the group's worst case. The group's worst
    # case comes only from `group_facts`' one joint table; if that is absent
    # the field is None with its reason, never the one-leg number.
    grp = x.get("group_detail") if x.get("group") else None
    if act == "HOLD":
        out.update(capital_required_usd=0.0, capital_released_usd=0.0,
                   fees_usd=0.0, execution_uncertainty=EXEC_NO_ORDER)
        if wc is None and grp is not None:
            why["worst_case_net_usd"] = GROUP_WORST_CASE_NOT_ESTABLISHED
        elif wc is None and q is not None and basis is not None:
            wc = round(-basis * q, 6)
        out["unpaired_residual_qty"] = _f(x.get("unpaired_qty",
                                                q if q is not None else None))
        out["unpaired_value_at_risk_usd"] = _f(x.get("unpaired_var_usd"))
        out["capital_duration_h"] = cap.get("capital_duration_h")
        if out["capital_duration_h"] is None:
            why["capital_duration_h"] = cap.get("reason") or \
                CAPITAL_END_NOT_STATED
    elif act in ("DIRECT_EXIT", "REDUCE", "EXIT"):
        sold = _f(c.get("qty"))
        kept = _f(c.get("remaining_exposure_qty"))
        if kept is None and q is not None and sold is not None:
            kept = round(max(0.0, q - sold), 6)
        out.update(capital_required_usd=0.0,
                   capital_released_usd=_f(c.get("cash_now_usd")),
                   fees_usd=_f(c.get("fees_usd")),
                   execution_uncertainty=EXEC_FOK,
                   unpaired_residual_qty=kept)
        if out["capital_released_usd"] is None:
            why["capital_released_usd"] = "THE_SELECTOR_STATED_NO_PROCEEDS"
        if out["fees_usd"] is None:
            why["fees_usd"] = "THE_SELECTOR_STATED_NO_FEE"
        slice_v = _f(c.get("slice_value_usd"))
        if grp is not None:
            if wc is None:
                why["worst_case_net_usd"] = GROUP_WORST_CASE_NOT_ESTABLISHED
            unp = group_unpaired_after(grp, leg_role=c.get("leg_role"),
                                       kept=kept)
            out["unpaired_residual_qty"] = unp.get("unpaired_qty")
            out["unpaired_value_at_risk_usd"] = unp.get(
                "unpaired_value_at_risk_usd")
            out["unpaired_role_after"] = unp.get("unpaired_role")
        else:
            if wc is None and slice_v is not None and kept is not None \
                    and basis is not None:
                wc = round(slice_v - basis * kept, 6)
            if kept is not None and basis is not None:
                out["unpaired_value_at_risk_usd"] = round(basis * kept, 6)
        if kept == 0.0:
            out["capital_duration_h"] = 0.0
        else:
            out["capital_duration_h"] = cap.get("capital_duration_h")
            if out["capital_duration_h"] is None:
                why["capital_duration_h"] = cap.get("reason") or \
                    CAPITAL_END_NOT_STATED
        out["if_not_filled"] = "THE_POSITION_IS_UNCHANGED_AND_HOLD_APPLIES"
    else:
        # AN ACQUISITION: the floor is the whole position's, net of fees.
        row = dict((x.get("acquire_rows") or {}).get(
            str(c.get("candidate_id")), {}))
        if wc is None:
            wc = _f(c.get("downside_usd"))
        out.update(capital_required_usd=_f(c.get("incremental_capital_usd")),
                   capital_released_usd=0.0, fees_usd=_f(c.get("fees_usd")),
                   execution_uncertainty=EXEC_FOK)
        unc = _f(row.get("uncovered_qty", c.get("uncovered_qty")))
        out["unpaired_residual_qty"] = unc
        if unc is not None and basis is not None:
            out["unpaired_value_at_risk_usd"] = round(unc * basis, 6)
        out["capital_duration_h"] = cap.get("capital_duration_h")
        if out["capital_duration_h"] is None:
            why["capital_duration_h"] = cap.get("reason") or \
                CAPITAL_END_NOT_STATED
        out["if_not_filled"] = "THE_POSITION_IS_UNCHANGED_AND_HOLD_APPLIES"
    out["worst_case_net_usd"] = wc
    if wc is None:
        why.setdefault("worst_case_net_usd",
                       "THE_WORST_CASE_COULD_NOT_BE_ESTABLISHED")
    else:
        out["worst_case_remaining_loss_usd"] = round(max(0.0, -wc), 6)
    for k in ("unpaired_residual_qty", "unpaired_value_at_risk_usd"):
        if out[k] is None:
            why.setdefault(k, "NOT_ESTABLISHED_FOR_THIS_ALTERNATIVE")
    key = str(c.get("plan_digest") or c.get("candidate_id") or act)
    out["limits_check"] = (dict(x.get("limits_by_candidate") or {}).get(key)
                           or ({"status": LIMITS_NOT_APPROVED}
                               if not x.get("limits_approved") else
                               {"status": LIMITS_NOT_APPLICABLE}
                               if act in ("HOLD", "DIRECT_EXIT", "REDUCE",
                                          "EXIT") else
                               {"status": "NOT_CHECKED"}))
    out["field_reasons"] = why
    return out


def alternatives_of(verdict: dict, *, ctx: dict,
                    hedge_search: dict | None = None) -> list:
    """EVERY CONSIDERED ACTION: the ranked ones with the standard economics,
    the unrankable ones with their exact blocker, and the hedge search's
    refusals summarised by name. Never only the winner. Pure.

    With `ctx["ladder_inputs"]` (the review supplies it) each alternative
    also carries Xavier's ladder fields -- payout table over every
    settlement state, worst case over established states, P(net profit),
    P(both legs win), sensitivity, executable quantity, exposure after,
    settlement compatibility, evidence age -- computed from the SAME
    candidate row (`agents.xavier_ladder`). They are reported, never
    ranked on: the choice is still decide()'s."""
    ladder = (ctx or {}).get("ladder_inputs")
    _XL = None
    if ladder is not None:
        try:
            from .agents import xavier_ladder as _XL
        except Exception:                                       # noqa: BLE001
            _XL = None
    enrich_failed: list = []

    def _enrich(row, src):
        # DISPLAY-ONLY: a fault in the ladder module blanks these fields and
        # is named on the row; it never stops the review that decides.
        try:
            row.update(_XL.enrich_one(dict(src, **row), ladder))
        except Exception as exc:                                # noqa: BLE001
            row["ladder_error"] = type(exc).__name__
            enrich_failed.append(type(exc).__name__)

    alts = []
    for c in (verdict or {}).get("candidates") or []:
        row = {k: c.get(k) for k in _CARRIED if c.get(k) is not None}
        row.update(economics(c, ctx=ctx), rankable=True)
        if _XL is not None:
            _enrich(row, c)
        alts.append(row)
    for b in (verdict or {}).get("not_rankable") or []:
        row = {k: b.get(k) for k in _CARRIED if b.get(k) is not None}
        row.update(rankable=False,
                   blocker=(b.get("blocker") or b.get("refusal")
                            or "NOT_RANKABLE"),
                   why=(str(b.get("why"))[:400] if b.get("why") else None),
                   value_usd=None)
        row.update({k: None for k in ECONOMIC_FIELDS})
        row["field_reasons"] = {"*": "NOT_RANKABLE:%s" % row["blocker"]}
        if _XL is not None:
            _enrich(row, b)
        alts.append(row)
    if _XL is not None:
        try:
            alts.extend(_XL.extra_rows(alts, ladder))
        except Exception as exc:                                # noqa: BLE001
            alts.append({"action": "XAVIER_LADDER", "rankable": False,
                         "blocker": "XAVIER_LADDER_ROWS_FAILED:%s"
                         % type(exc).__name__})
        if enrich_failed:
            alts.append({"action": "XAVIER_LADDER", "rankable": False,
                         "blocker": "XAVIER_LADDER_ENRICHMENT_FAILED:%s"
                         % enrich_failed[0],
                         "rows_without_ladder_fields": len(enrich_failed)})
    elif ladder is not None:
        alts.append({"action": "XAVIER_LADDER", "rankable": False,
                     "blocker": "XAVIER_LADDER_MODULE_UNAVAILABLE"})
    if hedge_search and hedge_search.get("total"):
        alts.append({"action": "ACQUIRE_HEDGE", "rankable": False,
                     "blocker": "HEDGE_SEARCH_REFUSALS",
                     "refusals_by_name": hedge_search.get("by_name"),
                     "refusals_by_stage": hedge_search.get("by_stage"),
                     "total_refused": hedge_search.get("total")})
    return alts


def hedge_search_refusals(*, facts: dict | None, step: dict | None,
                          option_refusals: list | None = None) -> dict:
    """EVERY REFUSAL THE HEDGE SEARCH MADE BEFORE THE DECISION, by name and
    count: the candidate reader's, discovery's rejections, the ranking's
    not-rankable rows, the options' refusals and the supplier's named
    unavailable inputs. Pure."""
    f = dict(facts or {})
    s = dict(step or {})
    stages = {
        "candidate_legs_for": [r.get("refusal") for r in (
            (f.get("candidate_legs_read") or {}).get("refused") or [])],
        "discover": [r.get("refusal") for r in (
            (s.get("discovery") or {}).get("rejected") or [])],
        "rank_admitted": [r.get("refusal") for r in (
            (s.get("hedge_candidate_ranking") or {}).get("not_rankable")
            or [])],
        "decision_options": [r.get("refusal") for r in (option_refusals
                                                        or [])],
        "supplier_unavailable": list(f.get("unavailable") or []),
        "acquisition_ineligible": ([s["acquisition_ineligible"]]
                                   if s.get("acquisition_ineligible")
                                   else []),
        "discovery_refusal": ([(s.get("discovery") or {}).get("refusal")]
                              if (s.get("discovery") or {}).get("refusal")
                              else []),
    }
    by_name: dict[str, int] = {}
    by_stage: dict[str, dict] = {}
    for stage, names in stages.items():
        counts: dict[str, int] = {}
        for n in names:
            n = str(n or "UNNAMED")
            counts[n] = counts.get(n, 0) + 1
            by_name[n] = by_name.get(n, 0) + 1
        if counts:
            by_stage[stage] = counts
    return {"by_name": by_name, "by_stage": by_stage,
            "total": sum(by_name.values())}


# ═════════════════════════════════════════════════════════════════════
# THE SEARCH ORDER'S SCREEN: WHAT A STRUCTURE'S CLAIMED FLOOR SURVIVES
# ═════════════════════════════════════════════════════════════════════

def structure_screen(admitted: dict, ranked_row: dict | None = None, *,
                     held_qty=None) -> dict:
    """PER STRUCTURE: matched-unit cost, and whether a claimed floor survives
    fees, executable depth, quantities and every settlement state. Pure.

    A SCREEN FOR THE SEARCH ORDER AND THE RECORD, NEVER A PURCHASE RULE:
    "under $1.00" buys nothing by itself, and a structure whose floor fails
    ANY of the four is never reported as locking one. Each check is
    true / false / None (unknown) with its reason."""
    a = dict(admitted or {})
    st = dict(a.get("structure") or {})
    row = dict(ranked_row or {})
    tax = str(a.get("taxonomy") or st.get("taxonomy") or "")
    cost = st.get("cost_cents")
    units = a.get("units", st.get("units"))
    min_pay = a.get("min_payout_cents", st.get("min_payout_cents"))
    max_pay = a.get("max_payout_cents", st.get("max_payout_cents"))
    out = {"condition_id": a.get("condition_id"), "taxonomy": tax,
           "matched_cost_cents": cost,
           "matched_cost_under_one_dollar": (None if cost is None
                                             else int(cost) < 100),
           "overlapping_winning_region": (
               None if max_pay is None else (tax == "MIDDLE"
                                             and int(max_pay) >= 200)),
           "floor_survives": {}, "is_a_purchase_rule": False}
    fs = out["floor_survives"]
    # FEES: the per-unit floor, after the fee the ranking charged.
    fee = _f(row.get("fee_usd"))
    if cost is None or min_pay is None or units in (None, 0):
        fs["fees"] = {"survives": None,
                      "why": "THE_STRUCTURE_STATES_NO_COST_OR_NO_MINIMUM_PAYOUT"}
    elif fee is None:
        fs["fees"] = {"survives": None, "why": "THE_FEE_WAS_NOT_PRICED"}
    else:
        net = (int(min_pay) - int(cost)) * int(units) / 100.0 - fee
        fs["fees"] = {"survives": net > 0, "net_floor_usd": round(net, 6),
                      "why": ("the matched units' minimum payout less their "
                              "cost and the fee")}
    # EXECUTABLE DEPTH: the displayed depth at this side's own price.
    dep = row.get("depth") or {}
    if row.get("depth_qty") is None:
        fs["executable_depth"] = {"survives": None,
                                  "why": "THE_DISPLAYED_DEPTH_WAS_NOT_READ"}
    else:
        fs["executable_depth"] = {
            "survives": bool(dep.get("fully_supported")),
            "depth_qty": row.get("depth_qty"),
            "why": "displayed depth, not a queue position: a FOK may still "
                   "not fill"}
    # QUANTITIES: units are min of both legs, so uncovered inventory can lose.
    unc = _f(row.get("uncovered_qty"))
    if unc is None:
        fs["quantities"] = {"survives": None,
                            "why": "THE_COVERED_QUANTITY_WAS_NOT_ESTABLISHED"}
    else:
        fs["quantities"] = {
            "survives": unc <= 1e-9, "uncovered_qty": unc,
            "held_qty": _f(held_qty),
            "why": ("units = min(held, hedge); held contracts the hedge does "
                    "not cover carry the unhedged downside")}
    # EVERY SETTLEMENT STATE: void, push and postponement included.
    table = list(st.get("table") or [])
    states = sorted({str(r.get("state")) for r in table})
    undetermined = [r.get("region") for r in table if not r.get("determined")]
    postponed = [r.get("region") for r in table
                 if str(r.get("state")) == "POSTPONED"]
    if not table:
        fs["settlement_states"] = {"survives": None,
                                   "why": "NO_PAYOFF_TABLE_WAS_CLASSIFIED"}
    elif undetermined:
        fs["settlement_states"] = {"survives": False,
                                   "undetermined": undetermined,
                                   "why": "A_REGION_HAS_NO_DETERMINED_PAYOUT"}
    elif postponed:
        fs["settlement_states"] = {
            "survives": None, "states": states,
            "why": ("a postponement is not a payout: the floor holds only "
                    "once the fixture resolves")}
    else:
        worst = min(float(r.get("joint_cents") or 0) for r in table)
        fs["settlement_states"] = {
            "survives": (cost is not None and worst > float(cost)),
            "states": states, "worst_joint_cents": worst,
            "why": ("the minimum joint payout over every classified state, "
                    "void and push included, against the matched cost")}
    out["locks_a_floor"] = all(v.get("survives") is True
                               for v in fs.values()) and len(fs) == 4
    out["search_rank"] = (0 if (out["overlapping_winning_region"]
                                and out["matched_cost_under_one_dollar"])
                          else 1 if out["overlapping_winning_region"] else 2)
    return out


# ═════════════════════════════════════════════════════════════════════
# EXECUTION ELIGIBILITY: THE GATES, READ BEFORE THE RECORD IS WRITTEN
# ═════════════════════════════════════════════════════════════════════
#
# The record is written BEFORE dispatch, so it can only state what the gates
# say when read now. These readers mirror, in order and read-only, the
# refusals the real submission path makes before it sends; the real path
# still runs afterwards and remains the authority, and what it actually did
# is appended as execution events. A divergence between the two is a defect
# the tests pin (the switch-off case must name the same gate).

def _blocked(gate: str) -> str:
    return "%s:%s" % (E_BLOCKED, gate)


async def exit_eligibility(conn, *, plan, account_id: str, venue: str,
                           now: float | None = None) -> dict:
    """WOULD THIS BOUND EXIT PLAN BE SENT? The servicing gates, read-only."""
    from . import bettor_funded_book as FB
    from . import bettor_funded_management as FM
    from . import bettor_funded_schema as FS

    out = {"gates_checked": [], "switches_off": []}

    def _stop(gate, **kw):
        return dict(out, eligibility=_blocked(gate), gate=gate, **kw)

    try:
        if await FS.require(conn) is not None:
            return _stop("SCHEMA")
        out["gates_checked"].append("SCHEMA")
        row = await conn.fetchrow(
            "SELECT residual_qty::float8 AS r, closed_at FROM "
            " bettor_funded_intents WHERE intent_id=$1 AND kind='ENTRY'",
            plan.intent_id)
        if row is None or float(row["r"] or 0) <= 0 \
                or row["closed_at"] is not None:
            return _stop("NOTHING_HELD")
        avail = float(await conn.fetchval(
            "SELECT bettor_funded_available_to_exit($1)::float8",
            plan.intent_id) or 0.0)
        out["available_to_exit"] = avail
        if float(plan.quantity) > avail + 1e-9:
            return _stop("INVENTORY_RESERVED_BY_AN_OUTSTANDING_EXIT")
        out["gates_checked"].append("INVENTORY")
        clock = time.time()
        if plan.check_not_expired(clock)["expired"]:
            return _stop("EVIDENCE_EXPIRED", checked_at=clock)
        out["gates_checked"].append("EVIDENCE_NOT_EXPIRED_REAL_CLOCK")
        own = await FB.check_servicing(conn, intent_id=plan.intent_id,
                                       account_id=account_id, venue=venue)
        if not own.get("ok"):
            return _stop("SERVICING_OWNERSHIP:%s" % own.get("refusal"))
        out["gates_checked"].append("SERVICING_OWNERSHIP")
    except Exception as exc:                                    # noqa: BLE001
        return _stop("ELIGIBILITY_UNREADABLE:%s" % type(exc).__name__)
    if not FM.FUNDED_EXIT_SUBMISSION_ENABLED:
        out["switches_off"].append("FUNDED_EXIT_SUBMISSION_ENABLED")
        return dict(out, eligibility=E_SUBMISSION_DISABLED,
                    gate="FUNDED_EXIT_SUBMISSION_ENABLED")
    return dict(out, eligibility=E_DISPATCHED, gate=None,
                not_evaluated_here=["pmus execution_gate.authorize",
                                    "venue credentials"])


async def acquire_eligibility(conn, *, record: dict, account_id: str,
                              venue: str, venue_positions=None,
                              group_id: str | None = None,
                              now: float | None = None) -> dict:
    """WOULD THIS ADMITTED HEDGE BE SENT? The ENTRY-side gates a hedge passes
    (it goes out through the entry connector), read-only and in the order
    `submit_for_decision` applies them. A protective hedge blocked by an
    entry-side gate -- the entry switch, the rails, the loss stop, the account
    pause -- is named EXACTLY here; no gate is loosened for it."""
    from . import bettor_account_exposure as AE
    from . import bettor_entry_execution as EX
    from . import bettor_funded_activation as FA
    from . import bettor_funded_execution as FX
    from . import bettor_funded_reservations as RSV
    from . import bettor_funded_schema as FS

    out = {"gates_checked": [], "switches_off": []}
    rec = dict(record or {})

    def _stop(gate, **kw):
        return dict(out, eligibility=_blocked(gate), gate=gate, **kw)

    try:
        exp = rec.get("inputs_expire_at")
        if exp is not None and time.time() >= float(exp):
            return _stop("EVIDENCE_EXPIRED")
        out["gates_checked"].append("EVIDENCE_NOT_EXPIRED_REAL_CLOCK")
        if await FS.require(conn) is not None:
            return _stop("SCHEMA")
        if FA.venue_class(venue) not in FX.ALLOWED_VENUE_CLASSES:
            return _stop("VENUE_CLASS")
        plan = FX.plan_from_decision(rec)
        if not plan.get("ok"):
            return _stop("PLAN:%s" % plan.get("refusal"))
        out["gates_checked"].append("PLAN")
        if group_id:
            live = [r for r in await RSV.live(conn, group_id=group_id)
                    if r.get("leg_role") == "HEDGE"]
            if live:
                return _stop("THE_HEDGE_LEG_IS_ALREADY_CLAIMED")
        sel = await FA.account_selection(conn, account_id)
        if not sel.get("ok"):
            ref = sel.get("refusal")
            return _stop({FA.R_ACCOUNT_PAUSED: "ACCOUNT_PAUSED",
                          FA.R_SETTLEMENT_CONTESTED: "SETTLEMENT_CONTESTED"}
                         .get(ref, "ACCOUNT:%s" % ref))
        out["gates_checked"].append("ACCOUNT")
        approved = await FX._approved(conn)
        if not approved:
            return _stop(LIMITS_NOT_APPROVED)
        eff = EX.effective_limits(approved)
        rails = await FX.check_rails(conn, plan, eff["effective"],
                                     account_id=sel["account_id"],
                                     venue=venue)
        out["rails"] = {k: rails.get(k) for k in ("over", "unmeasured",
                                                  "refusal")}
        if not rails.get("ok", True) and rails.get("refusal"):
            return _stop("RAILS:%s" % rails["refusal"])
        if rails.get("unmeasured"):
            return _stop(("LOSS_STOP_NOT_MEASURED"
                          if "MAX_DRAWDOWN" in rails["unmeasured"]
                          else "RAIL_NOT_MEASURED:%s"
                          % ",".join(rails["unmeasured"])))
        if rails.get("over"):
            names = [o["rail"] for o in rails["over"]]
            return _stop("LOSS_STOP" if "MAX_DRAWDOWN" in names
                         else "RAILS_EXCEEDED:%s" % ",".join(names))
        out["gates_checked"].append("RAILS")
        exposure = await AE.account_exposure(
            conn, account_id=sel["account_id"],
            venue_positions=venue_positions, now=time.time())
        auth = EX.authorize_submission(
            account_id=sel["account_id"], venue=venue,
            authorization=FA._obj(await FA._state(conn,
                                                  FA.AUTHORIZATION_KEY)),
            approved_limits=approved, account_exposure=exposure,
            proposed_cost_usd=float(plan["collateral_usd"]), now=time.time())
        if not auth.get("authorization_consumed"):
            return _stop("AUTHORIZATION:%s" % auth.get("refusal"))
        if not auth.get("ok") and auth.get("refusal") != \
                EX.R_SUBMISSION_DISABLED:
            return _stop("ACCOUNT_EXPOSURE:%s" % auth.get("refusal"))
        out["gates_checked"].append("AUTHORIZATION")
        if auth.get("refusal") == EX.R_SUBMISSION_DISABLED:
            out["switches_off"].append("REAL_ORDER_SUBMISSION_ENABLED")
    except Exception as exc:                                    # noqa: BLE001
        return _stop("ELIGIBILITY_UNREADABLE:%s" % type(exc).__name__)
    if not FX.FUNDED_SUBMISSION_ENABLED:
        out["switches_off"].append("FUNDED_SUBMISSION_ENABLED")
    if out["switches_off"]:
        return dict(out, eligibility=E_SUBMISSION_DISABLED,
                    gate=",".join(out["switches_off"]))
    return dict(out, eligibility=E_DISPATCHED, gate=None,
                not_evaluated_here=["pmus execution_gate.authorize",
                                    "venue credentials",
                                    "the one-open-leg-per-role index"])


#: The scheduled cycle's length (`ext_pinnacle_loop.CYCLE_S`); a test pins that
#: the two agree. A stopped lane polls sooner and services on every poll.
DEFAULT_REVIEW_INTERVAL_S = 900.0


def next_review(*, at: float, interval_s: float | None = None,
                order_outstanding: bool = False) -> dict:
    """WHEN THIS POSITION IS REVIEWED AGAIN, and on what basis. Pure.

    No earlier wake exists on this path: an outstanding order is reconciled
    by the NEXT review's recovery step, which runs first. Stated rather than
    promised, so the record does not claim a review nothing schedules."""
    iv = float(interval_s or DEFAULT_REVIEW_INTERVAL_S)
    return {"next_review_at": float(at) + iv, "interval_s": iv,
            "basis": ("NO_LATER_THAN_THE_NEXT_SCHEDULED_CYCLE; a stopped "
                      "lane polls every IDLE_POLL_S and services on each "
                      "poll"),
            "outstanding_order_reconciled_by": (
                "bettor_funded_book.recover at the start of that review"
                if order_outstanding else None)}


def brief(record: dict) -> dict:
    """ONE LINE PER POSITION FOR THE HEARTBEAT. Pure."""
    r = dict(record or {})
    blockers = [a.get("blocker") for a in (r.get("alternatives") or [])
                if a.get("blocker")]
    return {"intent_id": r.get("intent_id"),
            "group_id": r.get("portfolio_group_id"),
            "xavier_decision_id": r.get("xavier_decision_id"),
            "decision_id": r.get("decision_id"),
            "state": r.get("responsibility_state"),
            "chosen_action": r.get("chosen_action"),
            "eligibility": r.get("execution_eligibility"),
            "top_blockers": blockers[:3],
            "next_review_at": r.get("next_review_at")}


# ═════════════════════════════════════════════════════════════════════
# ONE DECISION PER GROUP: A PRIMARY AND ITS HEDGE LEG, VALUED TOGETHER
# ═════════════════════════════════════════════════════════════════════
#
# THE DEFECT THIS CLOSES (Xavier map Q1/Q6). Each leg of a group was its own
# ENTRY row, decided alone: the PRIMARY's HOLD ignored the hedge it already
# held, and the HEDGE leg could produce a second, conflicting dispatch in the
# same cycle. A group holding both legs now gets ONE decision, reviewed on the
# PRIMARY row, whose alternatives include exiting or reducing EITHER leg.
#
# THE VALUATION IS LINEAR, and that is what makes it exact: the group's
# expected value is the sum over legs of (quantity x expected payout -
# remaining basis), each leg's expected payout from ITS OWN marginal (the
# probability row its own HOLD was priced from). An action on one leg changes
# only that leg's term, so every group alternative is that leg's action value
# plus the other leg's HOLD value. Worst cases are NOT summed -- two separately
# minimised pieces can sit in regions that cannot occur together -- they are
# the floor of ONE joint table over the real quantities.

R_GROUP_ALREADY_HOLDS_A_HEDGE_LEG = (
    "THE_GROUP_ALREADY_HOLDS_ITS_HEDGE_LEG_SO_A_SECOND_IS_NOT_ACQUIRED")
R_HEDGE_LEG_HOLD_NOT_PRICED = (
    "THE_HEDGE_LEGS_HOLD_IS_NOT_PRICED_SO_ITS_ACTIONS_CANNOT_BE_COMPARED")
GROUP_BASIS_BOTH = "WHOLE_GROUP: this leg's action plus the other leg held"
GROUP_BASIS_PRIMARY_ONLY = (
    "PRIMARY_LEG_TERMS_ONLY: the hedge leg's HOLD is not priced, so every "
    "primary alternative omits the SAME unpriced hedge term -- their "
    "differences, which decide, are exact; the level is not the group's")


def two_leg_groups(held: list) -> dict:
    """Groups with BOTH an open PRIMARY and an open HEDGE entry row. Pure."""
    by: dict[str, dict] = {}
    for p in held or []:
        gid = p.get("portfolio_group_id")
        role = str(p.get("leg_role") or "PRIMARY")
        if gid and role in ("PRIMARY", "HEDGE"):
            by.setdefault(gid, {})[role] = p
    return {g: v for g, v in by.items() if "PRIMARY" in v and "HEDGE" in v}


def _joint_floor(p_leg, p_qty, h_leg, h_qty, *, tie, void, postpone):
    """The floor of ONE joint table over the real quantities, fee-free."""
    import dataclasses

    from . import bettor_funded_indirect_pair as FIP

    pq, hq = float(p_qty or 0), float(h_qty or 0)
    if pq <= 0 and hq <= 0:
        return 0.0
    if pq <= 0:
        return (None if h_leg is None or h_leg.cost_cents_per_unit is None
                else round(-h_leg.cost_cents_per_unit / 100.0 * hq, 6))
    if hq <= 0:
        return (None if p_leg is None or p_leg.cost_cents_per_unit is None
                else round(-p_leg.cost_cents_per_unit / 100.0 * pq, 6))
    if p_leg is None or h_leg is None or tie is None:
        return None
    try:
        got = FIP.position_worst_case(
            held_leg=dataclasses.replace(p_leg, quantity=int(pq)),
            hedge_leg=h_leg, hedge_qty=hq, sport_permits_tie=bool(tie),
            fee_usd=0.0, fee_basis="GROUP_FLOOR_FEES_ARE_ON_EACH_EXIT",
            fixture_can_void=void, fixture_can_postpone=postpone)
    except Exception:                                           # noqa: BLE001
        return None
    return (float(got["whole_position_usd"]) if got.get("ok") else None)


def leg_identity(pos: dict, facts: dict | None) -> dict:
    """WHAT ONE LEG PAYS ON AND HOW IT SETTLES, as its own rows state it.
    Pure. `payout_event` is the intent's column; the settlement identity is
    the one it was admitted on (a hedge's `decision_ref.settlement_identity`,
    written at acquisition) and the rule its valuation read."""
    p = dict(pos or {})
    ref = p.get("decision_ref")
    if isinstance(ref, str):
        try:
            ref = json.loads(ref)
        except ValueError:
            ref = {}
    ev = dict((facts or {}).get("management_evidence") or {})
    de = dict(ev.get("decision_evidence") or {})
    out = {"intent_id": p.get("intent_id"),
           "leg_role": str(p.get("leg_role") or "PRIMARY"),
           "us_market_slug": p.get("us_market_slug"),
           "order_intent": p.get("order_intent"),
           "payout_event": p.get("payout_event"),
           "settlement_identity": (ref or {}).get("settlement_identity"),
           "valuation_settlement_rule": de.get("settlement_rule"),
           "valuation_settlement_source": de.get("settlement_source"),
           "valuation_row_id": de.get("valuation_row_id")}
    gaps = []
    if not out["payout_event"]:
        gaps.append("PAYOUT_EVENT")
    if not (out["settlement_identity"] or out["valuation_settlement_rule"]):
        gaps.append("SETTLEMENT_RULES")
    out["gaps"] = gaps
    return out


def group_facts(pfacts: dict, hfacts: dict | None, *, primary: dict,
                hedge: dict, orders_in_flight: list | None = None) -> dict:
    """THE PRIMARY'S FACTS, TURNED INTO THE GROUP'S ONE DECISION. Pure.

    The group is valued whole: both legs' remaining quantities, the unmatched
    remainder, and every order of the group still in flight or unresolved
    (listed with its size, its exposure counted, and -- through the gate --
    no new order sent until it resolves)."""
    pf = dict(pfacts or {})
    hf = dict(hfacts or {}) if (hfacts or {}).get("ok") else {}
    p_hr = dict(pf.get("hold_ranking") or {})
    h_hr = dict(hf.get("hold_ranking") or {})
    pc = {str(c.get("action")): c for c in (p_hr.get("candidates") or [])}
    hc = {str(c.get("action")): c for c in (h_hr.get("candidates") or [])}
    hp = _f((pc.get("HOLD") or {}).get("value_usd"))
    hh = _f((hc.get("HOLD") or {}).get("value_usd"))
    p_res = float(primary.get("residual_qty") or 0)
    h_res = float(hedge.get("residual_qty") or 0)
    p_leg, h_leg = pf.get("held_leg"), hf.get("held_leg")
    flags = dict(tie=pf.get("sport_permits_tie"),
                 void=bool(pf.get("fixture_can_void", True)),
                 postpone=bool(pf.get("fixture_can_postpone", True)))
    basis_label = GROUP_BASIS_BOTH if hh is not None else \
        GROUP_BASIS_PRIMARY_ONLY
    add_h = hh if hh is not None else 0.0
    cands, blocked = [], []
    for c in p_hr.get("candidates") or []:
        c = dict(c, leg_role="PRIMARY",
                 intent_id=c.get("intent_id") or primary.get("intent_id"),
                 group_value_basis=basis_label)
        if _f(c.get("value_usd")) is not None:
            c["leg_value_usd"] = c["value_usd"]
            c["value_usd"] = round(float(c["value_usd"]) + add_h, 6)
            c["expected_net_usd"] = c["value_usd"]
        act = str(c.get("action"))
        kept = (p_res if act == "HOLD" else
                _f(c.get("remaining_exposure_qty")))
        slice_v = 0.0 if act == "HOLD" else _f(c.get("slice_value_usd"))
        jf = (None if kept is None or slice_v is None else
              _joint_floor(p_leg, kept, h_leg, h_res, **flags))
        c["worst_case_net_usd"] = (None if jf is None
                                   else round(slice_v + jf, 6))
        if act != "HOLD" and c["worst_case_net_usd"] is not None:
            c["downside_usd"] = c["worst_case_net_usd"]
        cands.append(c)
    for c in h_hr.get("candidates") or []:
        act = str(c.get("action"))
        if act == "HOLD":
            continue                      # the group's HOLD is the primary's
        c = dict(c, leg_role="HEDGE",
                 intent_id=c.get("intent_id") or hedge.get("intent_id"),
                 group_value_basis=GROUP_BASIS_BOTH)
        if hh is None or hp is None:
            blocked.append(dict(c, value_usd=None,
                                blocker=R_HEDGE_LEG_HOLD_NOT_PRICED,
                                why=("this leg's action can only be compared "
                                     "with the group's HOLD when both legs' "
                                     "HOLD are priced")))
            continue
        if _f(c.get("value_usd")) is None:
            blocked.append(dict(c, value_usd=None,
                                blocker="THIS_LEGS_ACTION_IS_NOT_SCORED"))
            continue
        c["leg_value_usd"] = c["value_usd"]
        c["value_usd"] = round(float(c["value_usd"]) + hp, 6)
        c["expected_net_usd"] = c["value_usd"]
        kept = _f(c.get("remaining_exposure_qty"))
        slice_v = _f(c.get("slice_value_usd"))
        jf = (None if kept is None or slice_v is None else
              _joint_floor(p_leg, p_res, h_leg, kept, **flags))
        c["worst_case_net_usd"] = (None if jf is None
                                   else round(slice_v + jf, 6))
        if c["worst_case_net_usd"] is not None:
            c["downside_usd"] = c["worst_case_net_usd"]
        cands.append(c)
    for b in p_hr.get("not_rankable") or []:
        blocked.append(dict(b, leg_role="PRIMARY"))
    for b in h_hr.get("not_rankable") or []:
        blocked.append(dict(b, leg_role="HEDGE"))
    if not hf:
        blocked.append({"action": "HEDGE_LEG_ACTIONS", "leg_role": "HEDGE",
                        "value_usd": None,
                        "blocker": (hfacts or {}).get("refusal")
                        or "THE_HEDGE_LEGS_FACTS_WERE_NOT_SUPPLIED"})
    matched = round(min(p_res, h_res), 6)
    unpaired_role = ("PRIMARY" if p_res > h_res else
                     "HEDGE" if h_res > p_res else None)
    unpaired = round(abs(p_res - h_res), 6)
    pb = ((pf.get("management_evidence") or {}).get("basis") or {}).get(
        "basis_per_contract")
    hb = ((hf.get("management_evidence") or {}).get("basis") or {}).get(
        "basis_per_contract")
    ub = pb if unpaired_role == "PRIMARY" else hb
    group = {
        "group_id": primary.get("portfolio_group_id"),
        "primary_intent_id": primary.get("intent_id"),
        "hedge_intent_id": hedge.get("intent_id"),
        "primary_residual_qty": p_res, "hedge_residual_qty": h_res,
        "matched_units": matched, "unpaired_role": unpaired_role,
        "unpaired_qty": unpaired,
        "unpaired_value_at_risk_usd": (None if ub is None or not unpaired
                                       else round(float(ub) * unpaired, 6)),
        "primary_basis_per_contract": _f(pb),
        "hedge_basis_per_contract": _f(hb),
        "group_hold_value_usd": (None if hp is None or hh is None
                                 else round(hp + hh, 6)),
        "group_value_basis": basis_label,
        "rule": ("whole-group EV = sum over legs of qty x expected payout - "
                 "remaining basis; each leg's expected payout from its own "
                 "marginal"),
        "marginals": {
            "PRIMARY": _marginal_of(pf), "HEDGE": _marginal_of(hf)},
        "worst_case_is": ("the floor of one joint table over the real "
                          "quantities, never a sum of separate minima"),
        # THE ORDERS THE GROUP HAS IN FLIGHT OR UNRESOLVED, with sizes: an
        # exit still working lowers what a leg will hold, a hedge claim not
        # answered may have raised it. Their exposure is counted and the
        # group's gate sends nothing new until they resolve.
        "orders_in_flight": list(orders_in_flight or []),
        "orders_in_flight_qty": round(sum(float(o.get("qty") or 0)
                                          for o in orders_in_flight or []),
                                      6),
        # BOTH LEGS' PAYOUT AND SETTLEMENT IDENTITY, from their own rows.
        "legs_identity": {"PRIMARY": leg_identity(primary, pf),
                          "HEDGE": leg_identity(hedge, hf)},
    }
    plans = dict(pf.get("executable_plans_by_digest") or {})
    plans.update(hf.get("executable_plans_by_digest") or {})
    return dict(pf, hold_ranking={"version": p_hr.get("version"),
                                  "candidates": cands,
                                  "not_rankable": blocked},
                executable_plans_by_digest=plans, group=group,
                companion_facts_ok=bool(hf),
                companion_refusal=(None if hf else (hfacts or {}).get(
                    "refusal")))


def _marginal_of(facts: dict) -> dict:
    ev = dict((facts or {}).get("management_evidence") or {})
    h = dict(ev.get("ev_hold") or {})
    d = dict(ev.get("decision_evidence") or {})
    return {"probability": h.get("probability"), "status": h.get("status"),
            "payout_event": h.get("payout_event_held"),
            "source_row_id": d.get("valuation_row_id"),
            "observed_at": d.get("valuation_observed_at")}


# ═════════════════════════════════════════════════════════════════════
# ONE REVIEW OF A GROUP AT A TIME, ON THE QUANTITIES IT WAS DECIDED ON
# ═════════════════════════════════════════════════════════════════════
#
# THE GAP THIS CLOSES. The claim stops the SAME decision being sent twice, and
# the leg reservation stops the same LEG being bought twice. Neither stops two
# reviews of one group -- two passes, two connections -- from each deciding on
# their own snapshot and committing DIFFERENT actions (one sells the primary
# while the other buys the hedge). So decide -> record -> claim -> dispatch
# for a group runs under one advisory lock keyed on the group, and a review
# that cannot take it sends nothing and says so.
#
# WHY A SESSION LOCK AND NOT `pg_advisory_xact_lock`. The dispatch commits its
# intent BEFORE the request leaves (the durability rule every send path is
# built on); wrapping it in one outer transaction would turn those commits
# into savepoints that a crash rolls back after the order has left. So the
# lock is held by the session across the review's own transactions, released
# explicitly, and a crashed session releases it by ending. `try` rather than
# wait: a review never blocks the scheduled cycle behind another's venue call.
#
# AND THE QUANTITIES ARE CHECKED AGAIN BEFORE THE SEND. A fill of an order
# still working on either leg can land between the decision and the send and
# change the residual or the matched units. The plan was valued on the old
# numbers, so it is not sent; the claim is spent (NOT_SENT names why) and the
# next review decides again on what is now held.

#: The advisory-lock namespace (first key of the two-key form), 'XAV1'.
LOCK_NAMESPACE = 0x58415631
R_GROUP_REVIEW_IN_PROGRESS = (
    "ANOTHER_REVIEW_OF_THIS_GROUP_HOLDS_ITS_LOCK_SO_THIS_ONE_SENDS_NOTHING")
R_GROUP_LOCK_UNAVAILABLE = (
    "THE_GROUP_LOCK_COULD_NOT_BE_TAKEN_SO_NOTHING_IS_SENT")
R_POSITION_CHANGED = "THE_POSITION_CHANGED_AFTER_THE_DECISION_REVALIDATE"
R_POSITION_CLOSED_BEFORE_REVIEW = (
    "THE_POSITION_CLOSED_BETWEEN_THE_PASS_SNAPSHOT_AND_ITS_REVIEW")
G_GROUP_ORDER_IN_FLIGHT = "GROUP_ORDER_IN_FLIGHT_OR_UNRESOLVED"
R_GROUP_ORDER_IN_FLIGHT = (
    "THE_GROUP_HAS_AN_ORDER_IN_FLIGHT_OR_UNRESOLVED_SO_NOTHING_NEW_IS_SENT")
#: The obligations that mean the group's quantity is moving or unknown. An
#: entry order still working (a partial entry) is NOT one of them: it is
#: re-checked before the send instead, so a partially filled position can
#: still be managed.
GROUP_GATING_OBLIGATIONS = (OB_ENTRY_UNRESOLVED, OB_EXIT_UNRESOLVED,
                            OB_CLAIM_UNRESOLVED, OB_DISPATCH_UNRESOLVED,
                            OB_CLAIM_LIVE, OB_EXIT_OUTSTANDING)
LOCK_SALT = "GROUP_LOCK_HELD_BY_ANOTHER_REVIEW"


def group_key(pos: dict) -> str:
    """The group a position is reviewed under: its portfolio group, else
    itself. Pure."""
    p = dict(pos or {})
    gid = p.get("portfolio_group_id")
    return str(gid) if gid else "intent:%s" % p.get("intent_id")


async def try_group_lock(conn, key: str) -> dict:
    """Take the group's review lock for this session, or say it is held."""
    try:
        got = await conn.fetchval(
            "SELECT pg_try_advisory_lock($1::int4, hashtext($2))",
            LOCK_NAMESPACE, str(key))
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "key": key, "refusal": R_GROUP_LOCK_UNAVAILABLE,
                "error": type(exc).__name__}
    if not got:
        return {"ok": False, "key": key, "refusal": R_GROUP_REVIEW_IN_PROGRESS}
    return {"ok": True, "key": key, "refusal": None}


async def release_group_lock(conn, lock: dict | None) -> None:
    if not (lock or {}).get("ok"):
        return
    try:
        await conn.fetchval(
            "SELECT pg_advisory_unlock($1::int4, hashtext($2))",
            LOCK_NAMESPACE, str(lock["key"]))
    except Exception:                                           # noqa: BLE001
        pass


async def release_session_group_locks(conn) -> int:
    """Release every Xavier group lock THIS session still holds -- left by a
    pass that raised before it released. Only this namespace; never a lock
    another module took. Returns how many were released."""
    released = 0
    try:
        for _ in range(64):
            n = await conn.fetchval(
                "SELECT count(*) FILTER (WHERE pg_advisory_unlock($1::int4, "
                "  (objid::bigint - CASE WHEN objid::bigint > 2147483647 "
                "   THEN 4294967296 ELSE 0 END)::int4)) FROM pg_locks "
                " WHERE locktype='advisory' AND pid=pg_backend_pid() "
                "   AND classid=$1::int4::oid AND objsubid=2 AND granted",
                LOCK_NAMESPACE)
            if not n:
                break
            released += int(n)
    except Exception:                                           # noqa: BLE001
        pass
    return released


GROUP_QUANTITIES_SQL = """
    SELECT i.intent_id, coalesce(i.leg_role, 'PRIMARY') AS role, i.state,
           i.quantity::float8 AS quantity,
           coalesce(i.residual_qty, 0)::float8 AS residual,
           coalesce((SELECT sum(f.qty) FROM bettor_funded_fills f
                      WHERE f.intent_id = i.intent_id
                        AND f.direction = 'ENTRY'), 0)::float8 AS filled
      FROM bettor_funded_intents i
     WHERE i.kind = 'ENTRY'
       AND (i.intent_id = ANY($1::text[])
            OR ($2::text IS NOT NULL AND i.portfolio_group_id = $2))
     ORDER BY i.intent_id
"""


async def group_quantities(conn, *, intent_ids: list,
                           group_id: str | None = None) -> dict:
    """WHAT THE GROUP HOLDS AND HAS IN FLIGHT, NOW, from our own rows: each
    entry leg's residual, filled and still-working quantity, every exit of
    those legs not yet finished, and every live leg claim. Never raises."""
    out: dict[str, Any] = {"ok": False, "legs": {}, "orders_in_flight": []}
    try:
        legs = await conn.fetch(GROUP_QUANTITIES_SQL,
                                [str(i) for i in intent_ids if i],
                                None if not group_id else str(group_id))
        ids = []
        for r in legs:
            working = (max(0.0, float(r["quantity"]) - float(r["filled"]))
                       if r["state"] in _OUTSTANDING else 0.0)
            out["legs"][r["intent_id"]] = {
                "role": r["role"], "residual": round(float(r["residual"]), 6),
                "filled": round(float(r["filled"]), 6),
                "working": round(working, 6), "state": r["state"]}
            ids.append(r["intent_id"])
        for r in await conn.fetch(
                "SELECT intent_id, parent_intent_id, state, "
                "       quantity::float8 AS q FROM bettor_funded_intents "
                " WHERE kind='EXIT' AND parent_intent_id = ANY($1::text[]) "
                "   AND state = ANY($2::text[]) ORDER BY intent_id",
                ids, list(_OUTSTANDING) + ["UNRESOLVED"]):
            out["orders_in_flight"].append(
                {"kind": "EXIT", "id": r["intent_id"],
                 "leg": r["parent_intent_id"], "state": r["state"],
                 "qty": round(float(r["q"]), 6)})
        if group_id:
            for r in await conn.fetch(
                    "SELECT operation_id, leg_role, state, "
                    "       quantity::float8 AS q "
                    "  FROM bettor_funded_leg_reservations "
                    " WHERE group_id=$1 AND state IN ('HELD','COMMITTED',"
                    "       'SEND_ATTEMPTED','AMBIGUOUS') "
                    " ORDER BY operation_id", str(group_id)):
                out["orders_in_flight"].append(
                    {"kind": "LEG_CLAIM", "id": r["operation_id"],
                     "leg": r["leg_role"], "state": r["state"],
                     "qty": round(float(r["q"]), 6)})
        out["ok"] = True
    except Exception as exc:                                    # noqa: BLE001
        out["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return out


def valued_on(*, positions: list, scope: dict | None,
              orders_in_flight: list) -> dict:
    """THE QUANTITIES A DECISION WAS VALUED ON, as the pass used them: each
    reviewed leg's residual, the filled quantity the bound is conditional on
    (`bound_filled_qty`'s source) and the orders then in flight. Pure."""
    sc = dict(scope or {})
    legs = {}
    for p in positions or []:
        if not p:
            continue
        legs[str(p.get("intent_id"))] = {
            "role": str(p.get("leg_role") or "PRIMARY"),
            "residual": round(float(p.get("residual_qty") or 0), 6)}
    return {"legs": legs, "intent_filled": sc.get("intent_filled"),
            "filled_by_role": dict(sc.get("filled_by_role") or {}),
            "matched_units": sc.get("matched_units"),
            "orders_in_flight": sorted(
                (o.get("kind"), o.get("id"), o.get("state"), o.get("qty"))
                for o in (orders_in_flight or []))}


def revalidate(decided: dict, now_q: dict, *, now_scope: dict | None) -> dict:
    """HAS ANYTHING THE DECISION WAS VALUED ON CHANGED? Pure.

    Compared exactly (to 1e-9): each reviewed leg's residual, the filled
    quantity by intent and by role, the matched units, and the set of orders
    in flight with their states and sizes. Any difference -- or a re-read that
    failed -- refuses the send."""
    d = dict(decided or {})
    changed = []
    if not (now_q or {}).get("ok"):
        return {"ok": False, "refusal": R_POSITION_CHANGED,
                "changed": [{"what": "RE_READ_FAILED",
                             "error": (now_q or {}).get("error")}]}
    legs_now = now_q.get("legs") or {}

    def _diff(what, was, now):
        if was is None and now is None:
            return
        if was is None or now is None or abs(float(was) - float(now)) > 1e-9:
            changed.append({"what": what, "decided_on": was, "now": now})

    for iid, leg in (d.get("legs") or {}).items():
        _diff("residual:%s" % iid, leg.get("residual"),
              (legs_now.get(iid) or {}).get("residual"))
    sc = dict(now_scope or {})
    _diff("intent_filled", d.get("intent_filled"), sc.get("intent_filled"))
    _diff("matched_units", d.get("matched_units"), sc.get("matched_units"))
    roles = set(d.get("filled_by_role") or {}) | set(
        sc.get("filled_by_role") or {})
    for role in sorted(roles):
        _diff("filled_by_role:%s" % role,
              (d.get("filled_by_role") or {}).get(role, 0.0),
              (sc.get("filled_by_role") or {}).get(role, 0.0))
    fl_now = sorted((o.get("kind"), o.get("id"), o.get("state"), o.get("qty"))
                    for o in (now_q.get("orders_in_flight") or []))
    fl_was = [tuple(x) for x in (d.get("orders_in_flight") or [])]
    if fl_now != fl_was:
        changed.append({"what": "orders_in_flight", "decided_on": fl_was,
                        "now": fl_now})
    if changed:
        return {"ok": False, "refusal": R_POSITION_CHANGED,
                "changed": changed,
                "why": ("the group changed after the decision was valued: "
                        "the plan is not sent and the next review decides "
                        "again on what is now held")}
    return {"ok": True, "refusal": None, "changed": []}


def group_gate(responsibilities_of_legs: list) -> dict | None:
    """THE GROUP'S QUANTITY IS MOVING OR UNKNOWN: an exit still working, a
    lost answer on any leg, a leg claim held or unresolved, or a dispatch
    claim with no outcome. The review is still recorded in full; nothing new
    is sent for the group until recovery or the venue resolves it. Pure."""
    hits = []
    for r in responsibilities_of_legs or []:
        for o in (r or {}).get("obligations") or []:
            if o.get("obligation") in GROUP_GATING_OBLIGATIONS:
                hits.append({"intent_id": (r or {}).get("intent_id"),
                             "obligation": o.get("obligation")})
    if not hits:
        return None
    names = sorted({h["obligation"] for h in hits})
    return {"eligibility": "%s:%s:%s" % (E_BLOCKED, G_GROUP_ORDER_IN_FLIGHT,
                                         ",".join(names)),
            "gate": G_GROUP_ORDER_IN_FLIGHT,
            "refusal": R_GROUP_ORDER_IN_FLIGHT,
            "obligations": hits,
            "why": ("an order of this group is still working or its outcome "
                    "is unknown, so the quantity a new order would act on is "
                    "not established; the exposure stays counted")}


# ── RECOVERY OF DISPATCH CLAIMS, ON THE BOOK'S OWN EVIDENCE ──────────────
#
# A claim with nothing after it (the process died between the claim and the
# send, or the outcome write failed) and an UNKNOWN outcome both count as an
# unresolved send, and the group is gated on them. They are answered only by
# evidence, and only while no review of the group is in flight (its lock is
# free):
#   * NO intent names the decision -> nothing was sent: every send path
#     commits its intent, carrying `decision_ref.xavier_decision_id`, BEFORE
#     the request leaves. NOT_SENT is appended.
#   * the intent it created reached a state the book established (FILLED,
#     CANCELLED, REJECTED, ABANDONED, or working with a venue order id) ->
#     RECOVERED with that state and the fills ledger's quantity.
#   * anything else (the intent is itself UNRESOLVED) stays UNRESOLVED and the
#     exposure stays counted; only the audited investigation resolves it.
RECOVERY_NO_INTENT = "NO_ORDER_INTENT_NAMES_THIS_DECISION_SO_NOTHING_WAS_SENT"
_BOOK_TERMINAL = ("FILLED", "CANCELLED", "REJECTED", "ABANDONED")


async def recover_claims(conn, *, account_id: str, venue: str,
                         at: float | None = None) -> dict:
    """Answer unresolved dispatch claims from the book's own rows. Never
    raises; appends only (NOT_SENT / RECOVERED, source RECOVERY_READ)."""
    when = float(at if at is not None else time.time())
    out: dict[str, Any] = {"examined": 0, "recovered": [],
                           "left_unresolved": [], "skipped_in_flight": []}
    try:
        rows = await unresolved_claims(conn, account_id=account_id,
                                       venue=venue)
        # AND A WORKING ORDER THE BOOK HAS SINCE MOVED ON: its later fills
        # and its end arrive by the book's recovery (`reconcile`), which
        # writes no execution event -- so without this a send answered with a
        # partial fill read WORKING at that quantity for ever, while the book
        # held the order FILLED.
        rows = list(rows) + await working_claims_behind_the_book(
            conn, account_id=account_id, venue=venue)
    except Exception as exc:                                    # noqa: BLE001
        return dict(out, ok=False, error=type(exc).__name__)
    for s in rows:
        xid = s["xavier_decision_id"]
        out["examined"] += 1
        try:
            d = await _decision(conn, xid)
            if d is None:
                continue
            lock = await try_group_lock(conn, group_key(
                {"portfolio_group_id": d["portfolio_group_id"],
                 "intent_id": d["intent_id"]}))
            if not lock.get("ok"):
                out["skipped_in_flight"].append(xid)
                continue
            try:
                got = await _recover_one(conn, xid, s, when)
            finally:
                await release_group_lock(conn, lock)
            (out["recovered"] if got.get("appended") or got.get("already")
             else out["left_unresolved"]).append(dict(got,
                                                      xavier_decision_id=xid))
        except Exception as exc:                                # noqa: BLE001
            out["left_unresolved"].append({"xavier_decision_id": xid,
                                           "error": type(exc).__name__})
    return dict(out, ok=True)


async def working_claims_behind_the_book(conn, *, account_id: str,
                                         venue: str) -> list:
    """Claimed decisions whose execution reads WORKING while the order they
    created has, in the book, filled more than the record says or reached
    an established end. Raises on a failed read (the caller contains it)."""
    if not await has_event_schema(conn):
        return []
    # Only claims with no ending on the record yet (a terminal status, or a
    # send that never happened / was refused): settled history is not
    # re-read every pass.
    ids = [r["xavier_decision_id"] for r in await conn.fetch(
        "SELECT c.xavier_decision_id FROM bettor_xavier_execution_events c "
        " WHERE c.event_kind='DISPATCH_CLAIMED' AND c.account_id=$1 "
        "   AND c.venue=$2 AND NOT EXISTS ("
        "       SELECT 1 FROM bettor_xavier_execution_events t "
        "        WHERE t.xavier_decision_id = c.xavier_decision_id "
        "          AND (t.terminal_status IS NOT NULL "
        "               OR t.event_kind IN ('NOT_SENT', 'REFUSED')))",
        account_id, venue)]
    out = []
    for xid, s in (await _executions_for(conn, ids)).items():
        if s["status"] != X_WORKING:
            continue
        it = await conn.fetchrow(
            "SELECT i.state, (SELECT coalesce(sum(f.qty), 0)::float8 "
            "   FROM bettor_funded_fills f WHERE f.intent_id = i.intent_id) "
            "   AS filled FROM bettor_funded_intents i "
            " WHERE i.decision_ref->>'xavier_decision_id'=$1 "
            " ORDER BY i.created_at LIMIT 1", xid)
        if it is None:
            continue
        if str(it["state"] or "") in _BOOK_TERMINAL or \
                float(it["filled"] or 0) > float(s["filled_qty"] or 0) + 1e-9:
            out.append(dict(s, xavier_decision_id=xid))
    return out


async def _recover_one(conn, xid: str, state: dict, when: float) -> dict:
    intents = await conn.fetch(
        "SELECT intent_id, kind, state, venue_order_id FROM "
        " bettor_funded_intents WHERE decision_ref->>'xavier_decision_id'=$1 "
        " ORDER BY created_at", xid)
    if not intents:
        if state.get("status") != X_CLAIMED_OUTCOME_UNRECORDED:
            # An UNKNOWN outcome with no intent naming it: two records
            # disagree about whether a send happened. Not a resolution.
            return {"resolution": None,
                    "why": "UNKNOWN_OUTCOME_RECORDED_AND_NO_INTENT_NAMES_IT"}
        got = await record_execution_event(
            conn, xavier_decision_id=xid, kind=K_NOT_SENT,
            source="RECOVERY_READ", occurred_at=when,
            evidence={"resolution": RECOVERY_NO_INTENT,
                      "rule": ("every send commits its intent, naming the "
                               "decision, before the request leaves")})
        return dict(got, resolution=RECOVERY_NO_INTENT)
    it = intents[0]
    st = str(it["state"] or "")
    vo = it["venue_order_id"]
    if st in _BOOK_TERMINAL or (st in ("ACKNOWLEDGED", "PARTIALLY_FILLED")
                                and vo):
        filled = await conn.fetchval(
            "SELECT coalesce(sum(qty), 0)::float8 FROM bettor_funded_fills "
            " WHERE intent_id=$1", it["intent_id"])
        got = await record_execution_event(
            conn, xavier_decision_id=xid, kind=K_RECOVERED,
            source="RECOVERY_READ", occurred_at=when, venue_order_id=vo,
            order_intent_id=it["intent_id"],
            cumulative_filled_qty=float(filled or 0) if vo else None,
            terminal_status=st if st in _BOOK_TERMINAL else None,
            evidence={"resolution": "THE_BOOK_ESTABLISHED_THE_ORDERS_STATE",
                      "intent_state": st})
        return dict(got, resolution="BOOK_STATE:%s" % st)
    return {"resolution": None, "intent_state": st,
            "why": ("the order this decision created is itself unresolved; "
                    "its exposure stays counted until it is investigated")}


# ═════════════════════════════════════════════════════════════════════
# ONE REVIEW PER POSITION, THROUGH THE SCHEDULED PASS
# ═════════════════════════════════════════════════════════════════════
#
# `bettor_funded_pair_cycle.pass_once` calls these. Nothing here decides a
# value or sends anything: the ranking is `bettor_funded_decision.decide`'s,
# the order is the bound plan's, the gates are the submission path's. This is
# what makes each review durable and readable BEFORE any of it acts.

R_DECIDED_WITH_THE_GROUP = "THIS_LEG_IS_DECIDED_WITH_ITS_GROUPS_PRIMARY_THIS_CYCLE"
R_XAVIER_RECORD_NOT_PERSISTED = (
    "XAVIERS_PRE_ACTION_RECORD_DID_NOT_PERSIST_SO_NOTHING_IS_SENT")
R_DISPATCH_NOT_CLAIMED = "THE_DISPATCH_CLAIM_WAS_NOT_TAKEN_SO_NOTHING_IS_SENT"
R_NOT_UNDER_RESPONSIBILITY = (
    "THIS_POSITION_HAS_NO_FILL_AND_NO_LOST_ANSWER_SO_IT_IS_NOT_YET_XAVIERS")
R_NO_OPEN_ENTRY_ROW = (
    "NO_OPEN_POSITION_TO_ACT_ON_THE_OBLIGATIONS_ARE_ORDERS_OR_SETTLEMENT")
R_XAVIER_REVIEW_RAISED = "XAVIERS_REVIEW_RAISED_SO_NO_RECORD_AND_NOTHING_IS_SENT"
G_NO_PLAN = "THE_WINNER_CARRIES_NO_EXECUTABLE_PLAN"
#: The common (one-measure) valuation refused the funded dispatch; the
#: record's eligibility is "BLOCKED:COMMON_VALUATION:<its refusal>".
G_COMMON_VALUATION = "COMMON_VALUATION"
#: The side an exit is sent on, by the side the position was opened with --
#: `pmus._exit_intent`'s rule (a test pins that the two agree).
EXIT_SIDE_OF = {"ORDER_INTENT_BUY_LONG": "ORDER_INTENT_SELL_LONG",
                "ORDER_INTENT_BUY_SHORT": "ORDER_INTENT_SELL_SHORT"}
E_DECIDED_BY_GROUP = "%s:DECIDED_BY_THE_GROUP_REVIEW" % E_NOT_DISPATCHED
HISTORY_IS_NOT_A_REASON = (
    "a loss already taken enters only through the remaining basis every "
    "alternative is scored against; nothing is forced by it. The comparison is "
    "forward value and worst case against the capital recoverable now")


def hedge_record_supplied(facts: dict, admitted_all, candidate_id) -> dict:
    """THE HEDGE LEG'S PAYOUT EVENT, FROM THE SUPPLIER'S BUILT LEG. Pure.

    THE DEFECT THIS CLOSES (Xavier map Q6). The production supplier sets
    `hedge_decision_record=None`, and the admission record took the payout
    event from it -- so every hedge intent was written with `payout_event`
    None and `select_exit` refused that leg forever (R_NO_PAYOUT_EVENT). The
    event is now taken from the candidate's own built leg; a supplied record
    may still add evidence, and one that disagrees with the plan on an order
    field is still refused by the admission check."""
    sup = dict((facts or {}).get("hedge_decision_record") or {})
    if sup.get("payout_event"):
        return sup
    pe, basis = None, None
    for d in (facts or {}).get("candidate_leg_details") or []:
        if str(d.get("candidate_id")) == str(candidate_id) \
                and d.get("payout_event"):
            pe, basis = d["payout_event"], d.get("payout_event_basis")
            break
    if pe is None:
        from . import bettor_funded_hedge_supply as HS

        leg = next((a.get("leg") for a in (admitted_all or [])
                    if str(a.get("condition_id")) == str(candidate_id)),
                   None)
        pe = HS.payout_event_of_leg(leg)
        basis = HS.PAYOUT_EVENT_BASIS
    if pe:
        sup.update(payout_event=pe, payout_event_basis=basis)
    # AND HOW IT SETTLES: the settlement rules the leg was built and admitted
    # on, carried onto the hedge intent so both legs of the group state their
    # payout AND settlement identity from their own rows. Evidence only: it is
    # not an order field, so the admission check is unchanged by it.
    if not sup.get("settlement_identity"):
        leg = next((a.get("leg") for a in (admitted_all or [])
                    if str(a.get("condition_id")) == str(candidate_id)), None)
        si = settlement_identity_of_leg(leg)
        if si:
            sup["settlement_identity"] = dict(si, payout_event=pe,
                                              payout_event_basis=basis)
    return sup or None


def settlement_identity_of_leg(leg) -> dict | None:
    """THE SETTLEMENT RULES A BUILT LEG CARRIES, compactly and JSON-safe.
    Each exceptional outcome's reading (established or not, its clause and
    resolution), the grading facts, and the prose's hash -- never the prose.
    Pure."""
    if leg is None or getattr(leg, "condition_id", None) is None:
        return None
    rules = {}
    for outcome, r in dict(getattr(leg, "settlement_rules", None)
                           or {}).items():
        r = dict(r or {})
        rules[str(outcome)] = {k: r.get(k) for k in (
            "established", "resolution", "clause", "refusal")
            if r.get(k) is not None}
    prov = dict(getattr(leg, "settlement_provenance", None) or {})
    # JSON-SAFE BY CONSTRUCTION: the intent writer serialises decision_ref
    # without a fallback, so anything not plain JSON is stringified here.
    return json.loads(json.dumps({"condition_id": leg.condition_id,
            "fixture_id": getattr(leg, "fixture_id", None),
            "period": getattr(leg, "period", None),
            "kind": getattr(leg, "kind", None),
            "overtime": getattr(leg, "overtime", None),
            "tie_rule": getattr(leg, "tie_rule", None),
            "void_rule": getattr(leg, "void_rule", None),
            "rules": rules,
            "rules_established": sorted(o for o, r in rules.items()
                                        if r.get("established")),
            "prose_sha256": prov.get("content_sha256"),
            "prose_source": prov.get("source"),
            "interpretation_version": prov.get("interpretation_version")},
        default=str))


_TERMINAL_STATES = ("FILLED", "CANCELLED", "REJECTED")


def exit_result(sent: dict) -> dict:
    """What an exit dispatch did, in the shape `record_dispatch` reads. Pure."""
    s = dict(sent or {})
    if s.get("outcome_unknown") or s.get("submitted") is None:
        return {"sent": True, "unknown": True, "refusal": s.get("refusal"),
                "error": s.get("error"),
                "order_intent_id": s.get("exit_intent_id")}
    if not s.get("submitted"):
        return {"sent": False, "refusal": s.get("refusal"),
                "why": s.get("why")}
    vo = s.get("venue_order_id")
    if not vo and s.get("refusal") == "THE_REQUEST_LEFT_AND_THE_ANSWER_WAS_LOST":
        return {"sent": True, "unknown": True, "refusal": s.get("refusal"),
                "order_intent_id": s.get("exit_intent_id")}
    st = s.get("acknowledged_state")
    return {"sent": True, "venue_order_id": vo,
            "refusal": s.get("refusal"),
            "filled_qty": s.get("filled_qty") if vo else None,
            "terminal_status": st if st in _TERMINAL_STATES else None,
            "order_intent_id": s.get("exit_intent_id")}


def acquisition_result(got: dict) -> dict:
    """What an acquisition dispatch did, for `record_dispatch`. Pure."""
    g = dict(got or {})
    sub = dict(g.get("submission") or {})
    if not g.get("submitted"):
        return {"sent": False, "refusal": g.get("refusal") or sub.get(
            "refusal"), "why": g.get("why") or sub.get("why")}
    if g.get("refusal") == "THE_REQUEST_LEFT_AND_THE_ANSWER_WAS_LOST":
        return {"sent": True, "unknown": True, "refusal": g.get("refusal"),
                "order_intent_id": g.get("intent_id")}
    order = dict(sub.get("order") or {})
    vo = order.get("venue_order_id")
    st = sub.get("state")
    return {"sent": True, "venue_order_id": vo,
            "refusal": None if g.get("ok") else (g.get("refusal")
                                                 or order.get("status")),
            "filled_qty": order.get("filled_qty_from_the_ledger") if vo
            else None,
            "terminal_status": st if st in _TERMINAL_STATES else None,
            "order_intent_id": g.get("intent_id")}


class ReviewContext:
    """ONE PASS'S XAVIER REVIEWS: the responsibility read once, then one
    pre-dispatch record per position. Every method returns a refusal by name;
    none raises on the decision path."""

    def __init__(self, *, account_id, venue, at, review_interval_s=None,
                 venue_positions=None):
        self.account_id, self.venue, self.at = account_id, venue, float(at)
        self.interval = review_interval_s
        self.venue_positions = venue_positions
        self.resp: dict = {"ok": False, "refusal": "NOT_READ",
                           "positions": []}
        self.by_intent: dict = {}
        self.approved: dict = {}

    async def load(self, conn):
        self.resp = await responsibilities(conn, account_id=self.account_id,
                                           venue=self.venue, now=self.at)
        self.by_intent = {p["intent_id"]: p
                          for p in self.resp.get("positions") or []}
        try:
            from . import bettor_funded_execution as FX
            self.approved = dict(await FX._approved(conn) or {})
        except Exception:                                       # noqa: BLE001
            self.approved = {}
        return self

    def summary(self) -> dict:
        states: dict[str, int] = {}
        for p in self.resp.get("positions") or []:
            states[p["state"]] = states.get(p["state"], 0) + 1
        return {"ok": self.resp.get("ok"), "refusal": self.resp.get("refusal"),
                "error": self.resp.get("error"),
                "positions": len(self.resp.get("positions") or []),
                "by_state": states, "reconciled": self.resp.get("reconciled")}

    def positions(self) -> list:
        return list(self.resp.get("positions") or [])

    def _responsibility_for(self, pos) -> dict | None:
        iid = str((pos or {}).get("intent_id"))
        r = self.by_intent.get(iid)
        if r is not None:
            return r
        if not self.resp.get("ok"):
            # THE READ FAILED: the position's own row still says what it
            # holds, and the record says the read failed rather than
            # pretending the position has no obligations.
            obs = obligations_of({"residual": pos.get("residual_qty"),
                                  "state": pos.get("state"),
                                  "closed_at": pos.get("closed_at")})
            return {"intent_id": iid, "state": state_of(obs),
                    "obligations": obs,
                    "derived_from_the_row": self.resp.get("refusal")}
        return None

    def _next(self, state) -> dict:
        return next_review(at=self.at, interval_s=self.interval,
                           order_outstanding=state in (ORDER_OUTSTANDING,
                                                       ORDER_UNRESOLVED))

    async def refresh(self, conn, intent_ids: list) -> dict:
        """RE-READ these positions' responsibility NOW (under the group's
        lock), so the record and the gate state what is true at the decision,
        not at the start of the pass. A failed re-read keeps the pass-start
        reading and says so."""
        ids = [str(i) for i in intent_ids if i]
        got = await responsibilities(conn, account_id=self.account_id,
                                     venue=self.venue, now=self.at,
                                     intent_ids=ids)
        if not got.get("ok"):
            return {"ok": False, "refusal": got.get("refusal"),
                    "kept": "THE_PASS_START_READING"}
        fresh = {p["intent_id"]: p for p in got.get("positions") or []}
        for iid in ids:
            if iid in fresh:
                self.by_intent[iid] = fresh[iid]
            else:
                self.by_intent.pop(iid, None)
        return {"ok": True, "refreshed": sorted(fresh)}

    def responsibility_of(self, intent_id) -> dict | None:
        return self.by_intent.get(str(intent_id))

    async def record_lock_refused(self, conn, pos, *, lock) -> dict:
        """A review that could not take its group's lock: recorded, nothing
        decided, nothing sent. Salted, so it never occupies the id the review
        holding the lock records its decision under."""
        r = self._responsibility_for(pos)
        if r is None:
            return {"ok": True, "skipped": R_NOT_UNDER_RESPONSIBILITY,
                    "brief": {"intent_id": pos.get("intent_id"),
                              "recorded": False,
                              "why": R_NOT_UNDER_RESPONSIBILITY}}
        refusal = (lock or {}).get("refusal") or R_GROUP_REVIEW_IN_PROGRESS
        return await self._write(
            conn, intent_id=str(pos.get("intent_id")),
            group_id=pos.get("portfolio_group_id"),
            slug=pos.get("us_market_slug"), r=r,
            eligibility="%s:%s" % (E_NOT_DISPATCHED, refusal),
            alternatives=[{"action": "ANY", "rankable": False,
                           "blocker": refusal}],
            reasoning={"why": ("another review of this group holds its lock "
                               "and is deciding now; this review decides and "
                               "sends nothing, and the next one re-reads the "
                               "group"), "group_key": (lock or {}).get("key")},
            exposure={"held_qty": _f(pos.get("residual_qty"))},
            salt=LOCK_SALT)

    async def _write(self, conn, *, intent_id, group_id, slug, r,
                     eligibility, alternatives, reasoning, economics_=None,
                     exposure=None, evidence=None, chosen=None, digest=None,
                     decision_id=None, salt=None) -> dict:
        try:
            return await self._write_or_raise(
                conn, intent_id=intent_id, group_id=group_id, slug=slug, r=r,
                eligibility=eligibility, alternatives=alternatives,
                reasoning=reasoning, economics_=economics_,
                exposure=exposure, evidence=evidence, chosen=chosen,
                digest=digest, decision_id=decision_id, salt=salt)
        except Exception as exc:                                # noqa: BLE001
            return {"ok": False, "refusal": R_WRITE_FAILED,
                    "error": type(exc).__name__,
                    "brief": {"intent_id": intent_id, "recorded": False,
                              "record_refusal": R_WRITE_FAILED}}

    async def _write_or_raise(self, conn, *, intent_id, group_id, slug, r,
                              eligibility, alternatives, reasoning,
                              economics_=None, exposure=None, evidence=None,
                              chosen=None, digest=None,
                              decision_id=None, salt=None) -> dict:
        nr = self._next(r["state"])
        rec = await record_decision(
            conn, account_id=self.account_id, venue=self.venue,
            intent_id=intent_id, decided_at=self.at,
            responsibility_state=r["state"],
            execution_eligibility=eligibility,
            alternatives=alternatives, reasoning=reasoning,
            expected_economics=economics_ or {},
            residual_exposure=exposure or {},
            evidence=dict(evidence or {}, next_review=nr),
            obligations=r.get("obligations") or [],
            chosen_action=chosen, chosen_plan_digest=digest,
            decision_id=decision_id, portfolio_group_id=group_id,
            us_market_slug=slug, next_review_at=nr["next_review_at"],
            salt=salt)
        rec["brief"] = brief({
            "intent_id": intent_id, "portfolio_group_id": group_id,
            "xavier_decision_id": rec.get("xavier_decision_id"),
            "decision_id": decision_id,
            "responsibility_state": r["state"], "chosen_action": chosen,
            "execution_eligibility": eligibility,
            "alternatives": alternatives,
            "next_review_at": nr["next_review_at"]})
        rec["brief"]["recorded"] = bool(rec.get("ok"))
        if not rec.get("ok"):
            rec["brief"]["record_refusal"] = rec.get("refusal")
        return rec

    async def record_obligations_only(self, conn, r) -> dict:
        """A position still owed something with no open entry row."""
        return await self._write(
            conn, intent_id=r["intent_id"],
            group_id=r.get("portfolio_group_id"),
            slug=r.get("us_market_slug"), r=r,
            eligibility=E_NOTHING_SELECTABLE,
            alternatives=[{"action": "ANY", "rankable": False,
                           "blocker": R_NO_OPEN_ENTRY_ROW}],
            reasoning={"why": ("no inventory is held, so no action is "
                               "selectable; the obligations below are what "
                               "keep the position under responsibility")},
            exposure={"held_qty": r.get("residual_qty"),
                      "filled_qty": r.get("filled_qty")})

    async def record_without_decision(self, conn, pos, *, facts, why,
                                      eligibility=None,
                                      companion=None) -> dict:
        """A position under responsibility whose decision was not made."""
        r = self._responsibility_for(pos)
        if r is None:
            return {"ok": True, "skipped": R_NOT_UNDER_RESPONSIBILITY,
                    "brief": {"intent_id": pos.get("intent_id"),
                              "recorded": False,
                              "why": R_NOT_UNDER_RESPONSIBILITY}}
        f = dict(facts or {})
        alts = [dict(b, rankable=False, blocker=(b.get("blocker")
                                                 or "NOT_RANKABLE"))
                for b in ((f.get("hold_ranking") or {}).get("not_rankable")
                          or [])]
        alts.append({"action": "ANY", "rankable": False,
                     "blocker": f.get("refusal") or "NO_DECISION_WAS_MADE",
                     "missing": f.get("missing")})
        return await self._write(
            conn, intent_id=str(pos.get("intent_id")),
            group_id=pos.get("portfolio_group_id"),
            slug=pos.get("us_market_slug"), r=r,
            eligibility=eligibility or E_NOTHING_SELECTABLE,
            alternatives=alts, reasoning={"why": why},
            exposure={"held_qty": _f(pos.get("residual_qty"))},
            evidence={"supplier_refusal": f.get("refusal"),
                      "supplier_unavailable": f.get("unavailable")},
            decision_id=f.get("decision_id") if f.get("ok") else None)

    async def _rails_for(self, conn, plan, pos) -> dict:
        from . import bettor_entry_execution as EX
        from . import bettor_funded_execution as FX

        try:
            eff = EX.effective_limits(self.approved)["effective"]
            got = await FX.check_rails(
                conn, {"collateral_usd": plan.collateral_usd,
                       "event_key": pos.get("event_key"),
                       "us_market_slug": plan.venue_slug,
                       "quantity": plan.quantity, "intent": plan.side},
                eff, account_id=self.account_id, venue=self.venue)
        except Exception as exc:                                # noqa: BLE001
            return {"status": "NOT_MEASURED", "error": type(exc).__name__}
        if got.get("refusal"):
            return {"status": "REFUSED", "refusal": got["refusal"]}
        if got.get("unmeasured"):
            return {"status": "NOT_MEASURED", "unmeasured": got["unmeasured"]}
        if got.get("over"):
            return {"status": "EXCEEDED",
                    "over": [o.get("rail") for o in got["over"]]}
        return {"status": "WITHIN", "rails": [r.get("rail")
                                              for r in got.get("rails") or []]}

    async def review(self, conn, **kw) -> dict:
        """`_review`, contained: an exception is a record that did NOT
        persist -- the caller then sends nothing -- never a raise out of the
        scheduled pass."""
        try:
            return await self._review(conn, **kw)
        except Exception as exc:                                # noqa: BLE001
            pos = dict(kw.get("pos") or {})
            return {"ok": False, "refusal": R_XAVIER_REVIEW_RAISED,
                    "error": "%s: %s" % (type(exc).__name__, str(exc)[:160]),
                    "brief": {"intent_id": pos.get("intent_id"),
                              "recorded": False,
                              "why": R_XAVIER_REVIEW_RAISED},
                    "companion_briefs": []}

    async def _review(self, conn, *, pos, facts, dec, step, ranking=None,
                      admitted_all=(), acquisition_plans=None,
                      option_refusals=None, screens=None, companion=None,
                      deferred_exits=None, gate=None, valued=None) -> dict:
        """ONE POSITION'S RECORD, BEFORE DISPATCH. Returns the record's id,
        the execution eligibility and -- for an acquisition -- the admission
        record the dispatch then uses, so both rest on one admission.

        `gate` is the group's in-flight gate (`group_gate`): when set, the
        review is recorded in full and its eligibility names the gate, and
        the caller sends nothing. `valued` is `valued_on(...)`: the
        quantities the decision was valued on, persisted so the pre-send
        revalidation compares against what the record states."""
        from . import bettor_funded_pair_cycle as PC

        r = self._responsibility_for(pos)
        if r is None:
            return {"ok": False, "refusal": R_NOT_UNDER_RESPONSIBILITY,
                    "brief": {"intent_id": pos.get("intent_id"),
                              "recorded": False,
                              "why": R_NOT_UNDER_RESPONSIBILITY}}
        f = dict(facts or {})
        verdict = dict((dec or {}).get("decision") or {})
        action = (dec or {}).get("action")
        selected = dict((dec or {}).get("selected") or {})
        mev = dict(f.get("management_evidence") or {})
        basis = dict(mev.get("basis") or {})
        if basis.get("basis_per_contract") is None:
            # THE SAME READING `manage` VALUES ON, taken here when the supplier
            # did not carry it: the fills ledger's remaining basis. Not a
            # substitute number -- the one source every valuation names.
            try:
                from . import bettor_funded_book as FB
                rb = await FB.remaining_basis(conn, str(pos.get("intent_id")))
                basis = {k: _f(rb.get(k)) for k in (
                    "basis_per_contract", "remaining_basis_usd",
                    "residual_qty", "entry_qty")}
                basis["read_here"] = "bettor_funded_book.remaining_basis"
            except Exception:                                   # noqa: BLE001
                pass
        group = f.get("group")
        qty = _f(pos.get("residual_qty"))
        hold_c = next((c for c in verdict.get("candidates") or []
                       if c.get("action") == "HOLD"), None)
        rows = {str(rw.get("condition_id")): rw
                for rw in (ranking or {}).get("ranked") or []}
        limits_by = {}
        if self.approved:
            for _cid, pl in (acquisition_plans or {}).items():
                limits_by[pl.digest] = await self._rails_for(conn, pl, pos)
        if group:
            unp_q = group.get("unpaired_qty")
            unp_v = group.get("unpaired_value_at_risk_usd")
        else:
            unp_q = qty
            unp_v = (None if qty is None or basis.get("basis_per_contract")
                     is None else round(qty * float(
                         basis["basis_per_contract"]), 6))
        ctx = {"qty": qty, "basis_per_contract": basis.get(
                   "basis_per_contract"),
               "hold_value_usd": None if hold_c is None else hold_c.get(
                   "value_usd"),
               "capital": f.get("capital"), "acquire_rows": rows,
               "limits_approved": bool(self.approved),
               "limits_by_candidate": limits_by,
               "unpaired_qty": unp_q, "unpaired_var_usd": unp_v,
               "group": bool(group), "group_detail": group}
        # ── XAVIER'S LADDER INPUTS (agents.xavier_ladder): read-only facts
        # this review already holds, so every alternative states its payout
        # table, probabilities and exposure on ONE measure. Nothing here
        # changes the ranking or what is dispatched.
        policy = None
        try:
            from .agents import xavier_policy as _XP
            policy = await _XP.load(conn)
        except Exception:                                       # noqa: BLE001
            _XP = None
        _cvr = dict((dec or {}).get("common_valuation") or {})
        _de = dict(mev.get("decision_evidence") or {})
        try:
            from .agents import xavier_ladder as _XLm
            _adm_view = _XLm.admitted_view(admitted_all)
        except Exception:                                       # noqa: BLE001
            _XLm, _adm_view = None, {}
        ctx["ladder_inputs"] = {
            "at": self.at, "held_leg": f.get("held_leg"),
            "hedge_held_leg": ((companion or {}).get("facts") or {}).get(
                "held_leg"),
            "sport_permits_tie": f.get("sport_permits_tie"),
            "fixture_can_void": bool(f.get("fixture_can_void", True)),
            "p_win": None if hold_c is None else hold_c.get(
                "value_per_contract"),
            "void_read": _cvr.get("void_rate_read"),
            "qty": qty, "basis_per_contract": basis.get("basis_per_contract"),
            "group": group, "rows": rows, "admitted": _adm_view,
            "capital": f.get("capital"),
            "evidence": {"probability_observed_at": _de.get(
                             "valuation_observed_at"),
                         "inputs_expire_at": mev.get("inputs_expire_at"),
                         "assessed_at": mev.get("assessed_at")},
            "policy": policy,
            "hedge_unavailable": f.get("unavailable"),
            "acquisition_ineligible": (step or {}).get(
                "acquisition_ineligible")}
        search = hedge_search_refusals(facts=f, step=step,
                                       option_refusals=option_refusals)
        alts = alternatives_of(verdict, ctx=ctx, hedge_search=search)
        for a in alts:
            scr = (screens or {}).get(str(a.get("candidate_id")))
            if scr is not None:
                a["search_screen"] = scr
        chosen, digest, adm, elig = None, None, None, {}
        chosen_plan = None
        if not (dec or {}).get("ok"):
            elig = {"eligibility": "%s:THE_DECISION_DID_NOT_PERSIST"
                    % E_NOT_DISPATCHED}
        elif action == PC.ACTION_HOLD:
            chosen, elig = "HOLD", {"eligibility": E_HOLD}
        elif action in (None, PC.ACTION_NOTHING_RANKABLE):
            elig = {"eligibility": E_NOTHING_SELECTABLE,
                    "why": verdict.get("refusal")}
        elif action in PC.LEDGER_EXIT_ACTIONS:
            plan = dict(f.get("executable_plans_by_digest") or {}).get(
                str(selected.get("plan_digest") or ""))
            if plan is not None and plan.digest != selected.get("plan_digest"):
                # A PLAN FILED UNDER THE WINNER'S DIGEST THAT IS NOT THAT PLAN
                # is not the ranked order; the record never names it.
                plan = None
            if plan is None:
                elig = {"eligibility": _blocked(G_NO_PLAN), "gate": G_NO_PLAN}
            else:
                chosen, digest = action, plan.digest
                # THE ORDER ITSELF, PERSISTED: instrument, action, quantity,
                # wire limit, proceeds bound, evidence expiry and the side
                # the adapter derives for closing this position.
                chosen_plan = dict(plan.as_dict(), kind="EXIT",
                                   position_order_intent=pos.get(
                                       "order_intent"),
                                   order_intent=EXIT_SIDE_OF.get(
                                       str(pos.get("order_intent") or "")),
                                   order_intent_rule="pmus._exit_intent")
                elig = await exit_eligibility(conn, plan=plan,
                                              account_id=self.account_id,
                                              venue=self.venue)
        elif action == PC.ACTION_ACQUIRE:
            cid = selected.get("candidate_id")
            acq = (acquisition_plans or {}).get(cid)
            if acq is None or selected.get("plan_digest") != acq.digest:
                elig = {"eligibility": _blocked(G_NO_PLAN), "gate": G_NO_PLAN}
            else:
                chosen, digest = action, acq.digest
                chosen_plan = dict(acq.as_dict(), kind="ACQUISITION",
                                   us_market_slug=acq.venue_slug,
                                   order_intent=acq.side)
                adm = PC.hedge_admission_record(
                    plan=acq, selected=selected,
                    admitted=next((a for a in admitted_all
                                   if str(a.get("condition_id")) == str(cid)),
                                  None),
                    ranked_row=rows.get(str(cid)), position=pos,
                    decision_id=f.get("decision_id"), now=self.at,
                    supplied=hedge_record_supplied(f, admitted_all, cid))
                if not adm.get("ok"):
                    elig = {"eligibility": _blocked(
                        "HEDGE_ADMISSION:%s" % adm.get("refusal")),
                        "failed": adm.get("failed"),
                        "conflicts": adm.get("conflicts")}
                else:
                    elig = await acquire_eligibility(
                        conn, record=adm["record"],
                        account_id=self.account_id, venue=self.venue,
                        venue_positions=self.venue_positions,
                        group_id=pos.get("portfolio_group_id"))
        else:
            elig = {"eligibility": "%s:UNRECOGNISED_ACTION_%s"
                    % (E_NOT_DISPATCHED, action)}
        if gate and chosen and chosen != "HOLD":
            # THE GROUP'S QUANTITY IS MOVING OR UNKNOWN: the winner is
            # recorded with its plan, and the gate is what the record names.
            elig = dict(gate, underlying=elig)
        elif chosen and chosen != "HOLD":
            # ── THE ONE-MEASURE GATE, NAMED ON THE RECORD (integration, XC) ──
            #
            # The common valuation (880377f) refuses a funded dispatch that is
            # not the same robust fixed action on one measure, and `pass_once`
            # checks it right after the group gate and before any binding or
            # claim. It was wired AFTER this record, so a refused dispatch was
            # persisted as DISPATCHED while nothing was sent. The record now
            # names that gate exactly -- it is read from the decision, never
            # recomputed -- and keeps what the later gates said beneath it.
            _cvg = dict((dec or {}).get("funded_dispatch_gate") or {})
            if _cvg and not _cvg.get("permitted"):
                elig = {"eligibility": _blocked("%s:%s" % (
                            G_COMMON_VALUATION, _cvg.get("refusal"))),
                        "gate": G_COMMON_VALUATION,
                        "why": ("the one-measure valuation over the void "
                                "rate's range does not permit this dispatch "
                                "(%s)" % _cvg.get("refusal")),
                        "underlying": elig}
        win = next((a for a in alts if a.get("rankable") and (
            (digest and a.get("plan_digest") == digest)
            or (not digest and a.get("action") == (selected.get("action")
                                                   or action)))), None)
        econ = {k: (win or {}).get(k) for k in ECONOMIC_FIELDS} if win else {}
        reasoning = {
            "policy": verdict.get("policy"),
            "selection_reason": verdict.get("selection_reason"),
            "tie_break": verdict.get("tie_break"),
            "ledger_action": action, "chosen_action": chosen,
            "chosen_leg_role": (win or {}).get("leg_role"),
            "margin_over_runner_up": verdict.get("margin_over_runner_up"),
            "refusal": verdict.get("refusal"),
            "increment_vs_hold_usd": econ.get("increment_vs_hold_usd"),
            "worst_case_net_usd": econ.get("worst_case_net_usd"),
            "capital_required_usd": econ.get("capital_required_usd"),
            "capital_released_usd": econ.get("capital_released_usd"),
            "eligibility": {k: elig.get(k) for k in (
                "eligibility", "gate", "gates_checked", "switches_off",
                "not_evaluated_here", "failed", "conflicts", "why",
                "obligations", "underlying")
                if elig.get(k) is not None},
            "history_is_not_a_reason": HISTORY_IS_NOT_A_REASON,
            "group_decision": group}
        # ── THE LADDER, THE SEARCH'S COMPLETENESS AND THE POLICY, NAMED ──
        _xl: dict[str, Any] = {}
        try:
            if _XLm is not None:
                _xl = {"version": _XLm.VERSION,
                       "measure": _XLm.measure_of(ctx["ladder_inputs"]),
                       "ladder": _XLm.ladder_view(alts),
                       "search_completeness": _XLm.search_completeness(
                           facts=f, step=step, alts=alts,
                           option_refusals=option_refusals)}
            if _XP is not None:
                _xl["policy"] = _XP.record(verdict, policy)
        except Exception as exc:                                # noqa: BLE001
            _xl["error"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        reasoning["xavier_ladder"] = _xl
        # ── THE POLICY THAT CHOSE, ITS SHADOW, AND THE SEARCH'S SCOPE ─────
        # `decision_policy`: key, version, source (ACTIVE_POLICY /
        # CODE_DEFAULT), rule, decision function and parameters of the ONE
        # decision function that ran. `shadow_comparison`: the other
        # policy's choice on the same frozen inputs -- displayed, never
        # dispatched. `selection_scope`: never "best available" when the
        # hedge search was limited.
        reasoning["decision_policy"] = (dec or {}).get("decision_policy")
        reasoning["shadow_comparison"] = (dec or {}).get("shadow_comparison")
        reasoning["search_policy_gate"] = (dec or {}).get(
            "search_policy_gate")
        _sc = dict(_xl.get("search_completeness") or {})
        reasoning["selection_scope"] = (
            "%s selected; HOLD / EXIT / REDUCE compared as priced; indirect "
            "pairs: %s" % (chosen or action or "NOTHING",
                           _sc.get("comparison_scope") or "NOT_ESTABLISHED"))
        scope = dict(step.get("filled_scope") or {})
        exposure = {"held_qty": qty, "filled_qty": scope.get("intent_filled"),
                    "matched_units": scope.get("matched_units"),
                    "remaining_basis_usd": basis.get("remaining_basis_usd"),
                    "basis_per_contract": basis.get("basis_per_contract"),
                    "unpaired_qty": unp_q,
                    "unpaired_value_at_risk_usd": unp_v, "group": group,
                    # THE QUANTITIES THIS DECISION WAS VALUED ON, compared
                    # again immediately before any send.
                    "valued_on": valued}
        pred = dict((dec or {}).get("prediction") or {})
        evidence = {
            "probability": mev.get("ev_hold"),
            "probability_row": mev.get("decision_evidence"),
            "probability_read": mev.get("probability_read"),
            "basis_source": "bettor_funded_book.remaining_basis",
            "book_and_probability_expire_at": mev.get("inputs_expire_at"),
            "assessed_at": mev.get("assessed_at"),
            "winner_inputs_expire_at": selected.get("inputs_expire_at"),
            "model": ({k: pred.get(k) for k in ("model_key", "model_version",
                                                 "feature_sha")}
                      if pred.get("model_version") else None),
            "region_probabilities_came_from": (dec or {}).get(
                "region_probabilities_came_from"),
            "capital": f.get("capital"),
            "hedge_supply_unavailable": f.get("unavailable"),
            # WHAT THE EXECUTABLE GRID EXCLUDED before any alternative was
            # valued: the exit ladder's and every quoted hedge candidate's.
            "executable_grid": f.get("executable_grid"),
            "responsibility_read_ok": bool(self.resp.get("ok")),
            "chosen_plan": chosen_plan}
        dec_id = f.get("decision_id") if (dec or {}).get("ok") else None
        rec = await self._write(
            conn, intent_id=str(pos.get("intent_id")),
            group_id=pos.get("portfolio_group_id"),
            slug=pos.get("us_market_slug"), r=r,
            eligibility=elig.get("eligibility") or E_NOTHING_SELECTABLE,
            alternatives=alts, reasoning=reasoning, economics_=econ,
            exposure=exposure, evidence=evidence, chosen=chosen,
            digest=digest, decision_id=dec_id)
        out = {"ok": bool(rec.get("ok")), "refusal": rec.get("refusal"),
               "error": rec.get("error"),
               "xavier_decision_id": rec.get("xavier_decision_id"),
               "eligibility": elig, "admission": adm,
               "gate_blocked": bool(gate and chosen and chosen != "HOLD"),
               "chosen_plan": chosen_plan,
               "brief": rec.get("brief"), "companion_briefs": []}
        if companion is not None and group:
            out["companion_briefs"].append(await self._companion(
                conn, companion=companion, group=group, alts=alts,
                primary_xid=rec.get("xavier_decision_id"), win=win,
                decision_id=dec_id))
        return out

    async def _companion(self, conn, *, companion, group, alts, primary_xid,
                         win, decision_id) -> dict:
        """THE HEDGE ROW'S RECORD: governed by the group decision, no dispatch
        of its own, its own alternatives and obligations shown."""
        hpos = dict(companion.get("pos") or {})
        r = self._responsibility_for(hpos)
        if r is None:
            return {"intent_id": hpos.get("intent_id"), "recorded": False,
                    "why": R_NOT_UNDER_RESPONSIBILITY}
        hf = dict(companion.get("facts") or {})
        acts_here = (win or {}).get("leg_role") == "HEDGE"
        got = await self._write(
            conn, intent_id=str(hpos.get("intent_id")),
            group_id=hpos.get("portfolio_group_id"),
            slug=hpos.get("us_market_slug"), r=r,
            eligibility=E_DECIDED_BY_GROUP,
            alternatives=[a for a in alts if a.get("leg_role") == "HEDGE"],
            reasoning={"group_decision_xavier_id": primary_xid,
                       "group_winner": {k: (win or {}).get(k) for k in (
                           "action", "leg_role", "plan_digest")},
                       "the_winner_acts_on_this_leg": acts_here,
                       "why": ("one decision per group, taken on the primary "
                               "row; this leg is dispatched only if that "
                               "decision's winner acts on it, and only "
                               "through that decision's claim")},
            exposure={"held_qty": _f(hpos.get("residual_qty")),
                      "group": group},
            evidence={"marginal": _marginal_of(hf),
                      "facts_refusal": hf.get("refusal")},
            decision_id=decision_id)
        return got.get("brief") or {"recorded": False}

    async def record_execution(self, conn, *, xavier_decision_id,
                               result) -> dict:
        """WHAT THE VENUE DID, appended as execution events (never onto the
        decision). A write that fails is reported, and the claim it leaves
        unanswered counts as an unresolved send until recovery answers it."""
        if not xavier_decision_id:
            return {"ok": False, "refusal": R_NO_DECISION}
        try:
            return await record_dispatch(
                conn, xavier_decision_id=xavier_decision_id,
                result=result, at=time.time())
        except Exception as exc:                                # noqa: BLE001
            return {"ok": False, "refusal": R_EVENT_WRITE,
                    "error": type(exc).__name__}


def review_context(**kw) -> ReviewContext:
    return ReviewContext(**kw)
