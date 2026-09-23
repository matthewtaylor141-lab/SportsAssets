"""THE LIVE SHADOW DESK LOOP. One writer, idempotent, restart-safe.

WHY THIS IS NOT "a background task in the web process".

Render runs the API as a web service that can have MORE THAN ONE
instance, and a deploy overlaps the old and new ones. An unguarded
`create_task` in the lifespan would therefore start a desk in EVERY
process, and two desks consuming the same evidence would double every
order, double every fill and produce a portfolio that reconciles
internally while describing nothing. So:

    ONE ACTIVE WRITER      a Postgres session-level advisory lock. The
                           instance that holds it runs; every other one
                           reports STANDBY and writes nothing. The lock
                           dies with the connection, so a killed
                           instance releases it without a timeout and
                           the next deploy takes over cleanly.

    IDEMPOTENT INTAKE      a durable cursor over the evidence's own
                           monotonic id. A restart resumes after the
                           last id COMMITTED, and every insert is
                           ON CONFLICT DO NOTHING on a natural key, so
                           re-reading an event cannot create a second
                           order.

    NON-BLOCKING           every DB call is awaited on the shared pool
                           and the loop sleeps between cycles. It never
                           holds the event loop and it never blocks a
                           request.

    RESTART-SAFE           orders, fills, positions, cash and the
                           cursor are in Postgres. The in-memory Desk
                           is rebuilt from them on start; it is a cache
                           of the ledger, never the ledger.

────────────────────────────────────────────────────────────────────
WHAT IT CANNOT DO, STRUCTURALLY.

There is NO venue client in this module's import graph. It cannot
place, amend or cancel a real order, and no funded path is reachable
from it -- not gated by a flag, but absent. A test reads this file's
own source and fails the build if a venue import or an order-submitting
symbol appears.

IT MAKES NO VENUE REQUESTS AT ALL. Its only input is evidence already
collected under existing authorization, read from our own database.
The observation collector's allowance is untouched because nothing here
consumes it.

────────────────────────────────────────────────────────────────────
IT DOES NOT WEAKEN THE PRODUCTION ENGINE.

The production lane keeps refusing exactly as it does now -- 3,629
NO_TRADE decisions carrying INDEPENDENT_EV_NOT_ESTABLISHED,
P_FILL_NOT_IDENTIFIED and NO_FAIR_VALUE are its correct output and are
not touched. This is a SEPARATE, EXPLICITLY LABELLED experimental lane
with its own execution model, its own tables and its own decision
stream. Both run; neither overrides the other; the interface keeps them
apart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import bettor_desk as DK

log = logging.getLogger(__name__)


def _js(v) -> str:
    return json.dumps(v if v is not None else {},
                      default=str)

LANE = "DESK_SHADOW_EXPERIMENTAL"
ENABLE_ENV = "BETTOR_DESK_LOOP"

# A fixed 64-bit key. `pg_try_advisory_lock` is session-scoped, so the
# holder releases it by disconnecting -- which is what makes a killed
# instance hand over without anyone having to time it out.
LOCK_KEY = 7723901544120031  # 'bettor-desk-loop'

CYCLE_S = float(os.getenv("BETTOR_DESK_CYCLE_S", "20"))
BATCH = int(os.getenv("BETTOR_DESK_BATCH", "500"))

STATE_STANDBY = "STANDBY_NOT_LOCK_HOLDER"
STATE_RUNNING = "RUNNING"
STATE_DISABLED = "DISABLED_BY_ENV"
STATE_ERROR = "ERROR"

_status = {"state": STATE_DISABLED, "since": None, "cycles": 0,
           "last_cycle_at": None, "last_event_id": None,
           "events_seen": 0, "decisions": 0, "orders": 0, "error": None,
           "lane": LANE, "makes_venue_requests": False,
           "seeded": False, "seeded_at_event_id": None,
           "universe": "all tracked cohort accounts in `trades`, not "
                       "Ferrari alone -- the policy decides, the feed "
                       "is whatever flow we already observe"}


def status() -> dict:
    """What the loop is doing, for the operations panel. Read-only."""
    return dict(_status)


def enabled() -> bool:
    return (os.getenv(ENABLE_ENV, "") or "").strip().lower() in (
        "1", "true", "yes", "on")


async def _acquire(conn) -> bool:
    """Session-level advisory lock. True only for the single writer."""
    got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY)
    return bool(got)


async def _load_cursor(conn, desk_id) -> int:
    """Resume where we left off -- or, on a FIRST start, at NOW.

    THE DEFECT THIS AVOIDS. `trades` holds 5.85 million rows going back
    to 2026-03-31. A cursor initialised to 0 would make the "live" lane
    grind through five months of history at BATCH rows a cycle, and
    every decision it published would be labelled LIVE while being a
    replay of March. A live lane consumes evidence that arrives AFTER
    it starts; that is what makes it live.

    A RESTART IS DIFFERENT AND MUST NOT DO THIS. When a state row
    exists we resume from it exactly, so a redeploy loses nothing. Only
    the genuine first start seeds from max(id), and the seeding is
    recorded so nobody later reads the gap as missing data.
    """
    row = await conn.fetchrow(
        "SELECT cursor_event_id FROM bettor_desk_state WHERE desk_id = $1",
        desk_id)
    if row and row["cursor_event_id"]:
        return int(row["cursor_event_id"])
    head = await conn.fetchval("SELECT coalesce(max(id), 0) FROM trades")
    _status["seeded_at_event_id"] = int(head)
    _status["seeded"] = True
    log.info("desk loop FIRST START: seeding cursor at current head %s "
             "(history before it is not replayed as live)", head)
    return int(head)


async def _events(conn, after_id, limit):
    """Evidence already collected, read from our own database.

    ORDERED BY THE EVIDENCE'S OWN MONOTONIC ID, not by timestamp. A
    timestamp can repeat and can arrive out of order across ingestion
    lanes; the serial id cannot, and a cursor that can go backwards is
    a cursor that replays work.
    """
    return await conn.fetch(
        """
        SELECT t.id, t.condition_id, t.outcome_index, t.side,
               t.price::float8 AS price, t.size::float8 AS size,
               extract(epoch FROM t.ts)::float8 AS ts,
               extract(epoch FROM t.detected_at)::float8 AS det
          FROM trades t
         WHERE t.id > $1
           AND t.condition_id IS NOT NULL
           AND t.outcome_index IN (0, 1)
         ORDER BY t.id
         LIMIT $2
        """, int(after_id), int(limit))


def _to_event(r) -> dict:
    # AVAILABLE_AT = max(ts, detected_at), the same conservative cutoff
    # learn/dataset.py uses. detected_at - ts is never treated as a
    # latency; run 82 established that it cannot be.
    at = max(float(r["ts"]), float(r["det"]))
    return {"kind": "PRINT", "at": at,
            "condition_id": r["condition_id"],
            "outcome_index": int(r["outcome_index"]),
            "price": float(r["price"]), "size": float(r["size"]),
            # The evidence's own row id IS the consumption key, so one
            # recorded execution can never be allocated twice.
            "evidence_id": "trade:%d" % int(r["id"])}


async def run(get_pool, *, desk_id="live1", policy=None, limits=None,
              fee_fn=None, queue_share=0.25):
    """The loop. Returns only when cancelled."""
    if not enabled():
        _status.update(state=STATE_DISABLED,
                       error="%s is not set" % ENABLE_ENV)
        log.info("desk loop disabled (%s unset)", ENABLE_ENV)
        return

    pol = policy or DK.Policy()
    lim = limits or DK.Limits()
    desk = DK.Desk(policy=pol, limits=lim, fee_fn=fee_fn,
                   queue_share=queue_share, desk_id=desk_id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        if not await _acquire(conn):
            # ANOTHER INSTANCE HOLDS IT. This one does not write, does
            # not decide, and says so. During a deploy both are up for
            # a few seconds and exactly one of them trades.
            _status.update(state=STATE_STANDBY, since=time.time(),
                           error=None)
            log.info("desk loop STANDBY: another instance holds the lock")
            while True:
                await asyncio.sleep(CYCLE_S)

        # STARTUP CAN FAIL, AND MUST SAY SO RATHER THAN DIE QUIETLY.
        # The first version read the cursor outside any try, so a schema
        # mismatch raised straight out of the task: the loop vanished,
        # every desk table stayed empty, and the status endpoint still
        # said RUNNING. A dead loop reporting RUNNING is worse than one
        # reporting ERROR.
        try:
            cursor = await _load_cursor(conn, desk_id)
        except Exception as exc:                            # noqa: BLE001
            _status.update(state=STATE_ERROR,
                           error="STARTUP: %s: %s" % (type(exc).__name__,
                                                      str(exc)[:200]))
            log.exception("desk loop could not read its cursor")
            while True:
                await asyncio.sleep(CYCLE_S)

        _status.update(state=STATE_RUNNING, since=time.time(), error=None)
        log.info("desk loop RUNNING from event id %s", cursor)

        while True:
            try:
                rows = await _events(conn, cursor, BATCH)
                for r in rows:
                    desk.step(_to_event(r))
                    cursor = int(r["id"])
                    _status["events_seen"] += 1
                if rows:
                    await _persist(conn, desk_id, desk, cursor)
                _status.update(cycles=_status["cycles"] + 1,
                               last_cycle_at=time.time(),
                               last_event_id=cursor,
                               decisions=len(desk.decisions),
                               orders=len(desk.orders), error=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                        # noqa: BLE001
                # A CYCLE THAT FAILS DOES NOT ADVANCE THE CURSOR. The
                # same events are retried next cycle, and the inserts
                # are idempotent, so a partial write cannot duplicate.
                _status.update(state=STATE_ERROR,
                               error="%s: %s" % (type(exc).__name__,
                                                 str(exc)[:200]))
                log.exception("desk loop cycle failed")
            await asyncio.sleep(CYCLE_S)


async def _persist(conn, desk_id, desk, cursor):
    """One transaction: the cursor and the snapshot move together.

    If the process dies mid-cycle the cursor has not moved, so the
    events are re-read and the idempotent keys absorb the repeat.
    """
    snap = desk.snapshot()
    # DECISIONS FIRST. `bettor_desk_orders.desk_decision_id` is a foreign
    # key into this table, so an order written before its decision would
    # be rejected by the database -- which is the FK doing its job, and
    # the reason the order of these writes is not arbitrary.
    pending = list(desk.decisions)
    async with conn.transaction():
        for d in pending:
            await conn.execute(
                """
                INSERT INTO bettor_desk_decisions
                       (desk_decision_id, desk_id, boot_id, decided_at,
                        feature_cutoff_at, condition_id, outcome_index,
                        action, reason, policy_version, model_version,
                        proposed_price, proposed_qty, inventory, ev, risk,
                        inputs, inputs_sha)
                VALUES ($1,$2,$3,to_timestamp($4),to_timestamp($4),$5,$6,
                        $7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,
                        $15::jsonb,$16::jsonb,$17)
                ON CONFLICT (desk_decision_id) DO NOTHING
                """,
                d["desk_decision_id"], desk_id, str(int(time.time())),
                float(d["at"]), d.get("condition_id"),
                d.get("outcome_index"), d["action"], d["reason"],
                d["policy_version"], d.get("learned_artifact_sha") or "",
                d.get("price"), d.get("proposed_qty"),
                _js(d.get("inventory")), _js(d.get("ev")),
                _js(d.get("risk")), _js({
                    "fill_model": d.get("fill_model"),
                    "queue_share_ASSUMED": d.get("queue_share_ASSUMED"),
                    "alternatives": d.get("alternatives"),
                    "lane": LANE}),
                d["inputs_sha"])
        await conn.execute(
            """
            INSERT INTO bettor_desk_state
                   (desk_id, boot_id, policy_version, model_version,
                    cursor_event_id, cash_usd, starting_cash_usd,
                    updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, now())
            ON CONFLICT (desk_id) DO UPDATE
               SET cursor_event_id = EXCLUDED.cursor_event_id,
                   cash_usd = EXCLUDED.cash_usd,
                   updated_at = now()
            """, desk_id, str(int(time.time())), DK.POLICY_VERSION,
            desk.policy.curve_sha, int(cursor),
            float(desk.pf.cash), float(desk.pf.starting_cash))

        for o in desk.orders.values():
            await conn.execute(
                """
                INSERT INTO bettor_desk_orders
                       (order_id, desk_id, boot_id, desk_decision_id,
                        condition_id, market_id, outcome_index, side,
                        intent, limit_price, qty, filled_qty,
                        notional_committed, avg_fill_price, fees_usd,
                        state, state_reason, placed_at, expires_at,
                        terminal_at, updated_at)
                VALUES ($1,$2,$3,$4,$5,NULL,$6,$7,$8,$9,$10,$11,$12,$13,
                        $14,$15,$16,to_timestamp($17),to_timestamp($18),
                        CASE WHEN $19::float8 IS NULL THEN NULL
                             ELSE to_timestamp($19) END, now())
                ON CONFLICT (order_id) DO UPDATE
                   SET filled_qty = EXCLUDED.filled_qty,
                       avg_fill_price = EXCLUDED.avg_fill_price,
                       fees_usd = EXCLUDED.fees_usd,
                       state = EXCLUDED.state,
                       state_reason = EXCLUDED.state_reason,
                       terminal_at = EXCLUDED.terminal_at,
                       updated_at = now()
                """, o.order_id, desk_id, str(int(time.time())),
                o.decision_id, o.condition_id, o.outcome_index, o.side,
                o.intent, o.limit_price, o.qty, o.filled_qty, o.notional,
                o.avg_fill_price, o.fees, o.state, o.state_reason,
                o.placed_at, o.expires_at, o.terminal_at)

        for (cond, oi), leg in desk.pf.legs.items():
            await conn.execute(
                """
                INSERT INTO bettor_desk_positions
                       (desk_id, condition_id, outcome_index, qty,
                        cost_basis_usd, realized_pnl_usd, fees_usd,
                        opened_at, updated_at, settled, settled_payout)
                VALUES ($1,$2,$3,$4,$5,$6,$7,
                        CASE WHEN $8::float8 IS NULL THEN NULL
                             ELSE to_timestamp($8) END, now(), $9, $10)
                ON CONFLICT (desk_id, condition_id, outcome_index)
                DO UPDATE SET qty = EXCLUDED.qty,
                              cost_basis_usd = EXCLUDED.cost_basis_usd,
                              realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                              fees_usd = EXCLUDED.fees_usd,
                              settled = EXCLUDED.settled,
                              settled_payout = EXCLUDED.settled_payout,
                              updated_at = now()
                """, desk_id, cond, int(oi), float(leg["qty"]),
                float(leg["cost"]), float(leg["realized"]),
                float(leg["fees"]), leg["opened_at"], bool(leg["settled"]),
                leg["payout"])

        inv = snap["invariant"]
        await conn.execute(
            """
            INSERT INTO bettor_desk_ledger
                   (desk_id, boot_id, at, cash_usd, committed_usd,
                    inventory_cost, inventory_mark, mark_basis,
                    realized_pnl_usd, unrealized_pnl_usd, fees_usd,
                    open_orders, open_positions, invariant_ok,
                    invariant_detail)
            VALUES ($1,$2,now(),$3,$4,$5,NULL,$6,$7,NULL,$8,$9,$10,$11,
                    $12::jsonb)
            """, desk_id, str(int(time.time())),
            float(desk.pf.cash), float(desk.committed_usd()),
            float(desk.pf.inventory_cost()),
            "NOT_IDENTIFIED: no contemporaneous book is retained for "
            "these instants, so there is no mark and no liquidation "
            "estimate",
            float(desk.pf.realized), float(desk.pf.fees),
            len(desk.open_orders()), len(desk.pf.open_legs()),
            bool(inv["ok"]), _js(inv))

    # DRAIN ONLY WHAT WAS COMMITTED. The transaction has returned, so
    # these rows are durable; dropping them keeps the in-memory desk a
    # bounded cache instead of an unbounded log. Anything the engine
    # appended during the write stays queued for the next cycle.
    del desk.decisions[:len(pending)]
