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
    except asyncpg.exceptions.UniqueViolationError:
        return dict(out, ok=True, refusal=R_PLAN_ALREADY_CLAIMED)
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
            conn, xavier_decision_id=xavier_decision_id, kind=K_NOT_SENT,
            source="DISPATCHER", occurred_at=when, evidence=r)
        return {"ok": got["ok"], "written": got.get("appended", False),
                "refusal": got.get("refusal")}
    if claim is None:
        return {"ok": False, "written": False, "refusal": R_NO_CLAIM}
    written = []
    vo = r.get("venue_order_id")
    if r.get("unknown"):
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id, kind=K_UNKNOWN,
            source="SEND_RESPONSE", occurred_at=when, evidence=r))
    elif r.get("refusal") and not vo:
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id, kind=K_REFUSED,
            source="SEND_RESPONSE", occurred_at=when, evidence=r))
    elif vo:
        written.append(await record_execution_event(
            conn, xavier_decision_id=xavier_decision_id, kind=K_ACK,
            source="SEND_RESPONSE", occurred_at=when, venue_order_id=vo,
            evidence=r))
        if r.get("filled_qty") is not None:
            written.append(await record_execution_event(
                conn, xavier_decision_id=xavier_decision_id, kind=K_FILL,
                source="SEND_RESPONSE", occurred_at=when, venue_order_id=vo,
                cumulative_filled_qty=r["filled_qty"],
                avg_fill_price_cents=r.get("avg_fill_price_cents"),
                fee_usd=r.get("fee_usd")))
        if r.get("terminal_status"):
            written.append(await record_execution_event(
                conn, xavier_decision_id=xavier_decision_id, kind=K_TERMINAL,
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
