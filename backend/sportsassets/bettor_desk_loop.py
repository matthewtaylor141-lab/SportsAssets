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

    ONE DURABLE ACCOUNT    `bettor_desk_accounts` holds the BOOK id. A
                           restart RESUMES it; capital is written by the
                           creating INSERT alone, so no restart can
                           replenish it. `epoch_id` identifies the
                           PROCESS and is not an accounting boundary.
                           Conflating the two is what caused the
                           $2,367.73 defect.

    RESTART-SAFE           cash, realized, fees, legs, RESTING AND
                           PARTIALLY FILLED ORDERS WITH THEIR FILLS, the
                           consumption ledger and the cursor are all
                           read back by `_restore`, scoped to the
                           account, before the first event is stepped.
                           Rows written before accounts existed carry
                           account_id IS NULL and cannot enter the book.

CORRECTION, 2026-09-23. THIS PARAGRAPH WAS FALSE WHEN FIRST WRITTEN.
It claimed the Desk was "rebuilt from them on start; a cache of the
ledger, never the ledger". No such rebuild existed. `run()` constructed
a fresh `Desk` and `_load_cursor` read the cursor and nothing else, so
every process start began the book again at `starting_cash` with no
positions, and the first `_persist` then overwrote
`bettor_desk_state.cash_usd` with the fresh figure.

None of the checks in place could see it. `invariant_ok` compares a
desk against its OWN starting cash, so a freshly emptied book
reconciles perfectly; the cursor still advanced; one state row still
existed; the earliest decision timestamps never moved because no
history was re-read. I cited those four and reported "restart-safety
demonstrated", and that conclusion did not follow from them.

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
import uuid

from decimal import Decimal

from . import bettor_desk as DK
from . import bettor_desk_accounts as ACC
from . import bettor_fee_schedule as FEES

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

# ONE ID PER PROCESS, generated once at import.
#
# `boot_id` was previously computed as `str(int(time.time()))` INSIDE
# each INSERT, so it changed from row to row and identified a write
# rather than a boot. Restarts were therefore not locatable in the
# historical ledger at all -- which is why the correction run has to
# report the epochs of that period as NOT_IDENTIFIABLE rather than
# segment them.
EPOCH_ID = uuid.uuid4().hex

# THE ONE PLACE THE ASSUMED QUEUE SHARE IS WRITTEN. It is both the
# runtime default and the value the command centre displays, so the
# number management reads cannot drift from the number that produced
# the fills. It remains an ASSUMPTION: P_FILL is NOT_IDENTIFIED.
QUEUE_SHARE_DEFAULT = 0.25

CYCLE_S = float(os.getenv("BETTOR_DESK_CYCLE_S", "20"))
BATCH = int(os.getenv("BETTOR_DESK_BATCH", "500"))

STATE_STANDBY = "STANDBY_NOT_LOCK_HOLDER"
STATE_PAUSED = "PAUSED_ACCOUNTING_RECOVERY"
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


async def _open_epoch(conn, desk_id, account_id, desk, cursor,
                      restore) -> None:
    """Record where this book begins, so a restart is visible."""
    await conn.execute(
        """
        UPDATE bettor_desk_epochs SET ended_at = now()
         WHERE desk_id = $1 AND ended_at IS NULL
        """, desk_id)
    await conn.execute(
        """
        INSERT INTO bettor_desk_epochs
               (epoch_id, desk_id, starting_cash, restored,
                restore_detail, cursor_at_start, code_version)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)
        ON CONFLICT (epoch_id) DO NOTHING
        """, EPOCH_ID, desk_id, float(desk.pf.starting_cash),
        bool(restore.get("restored")),
        _js(dict(restore, account_id=account_id)), int(cursor),
        DK.VERSION)


async def apply_corrections(conn, desk_id) -> dict:
    """Publish the fee correction ONCE, under the writer's lock.

    IDEMPOTENCE HAS TWO INDEPENDENT GUARDS, because one of them is a
    convention and the other is the database:

      1. `bettor_desk_correction_runs` has a UNIQUE index on
         (desk_id, version). A second run of the same version fails the
         insert and returns without writing a single correction row.
      2. `bettor_desk_corrections.correction_id` is
         version:kind:subject, so even a caller that fabricated a fresh
         run_id could not apply the same subject twice.

    THE ORIGINALS ARE NOT TOUCHED. This reads `bettor_desk_orders` and
    writes only to the two correction tables.
    """
    from . import bettor_desk_correction as CORR

    run_id = "%s:%s" % (CORR.VERSION, desk_id)
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO bettor_desk_correction_runs
                       (run_id, version, desk_id, code_version,
                        accounting_status)
                VALUES ($1,$2,$3,$4,'PENDING')
                """, run_id, CORR.VERSION, desk_id, DK.VERSION)
    except Exception as exc:                                # noqa: BLE001
        # The unique index did its job: this version is already applied.
        log.info("desk correction %s already applied (%s)",
                 CORR.VERSION, type(exc).__name__)
        return {"applied": False, "reason": "ALREADY_APPLIED"}

    rows = await conn.fetch(
        """
        SELECT order_id, intent, filled_qty::float8 AS filled_qty,
               avg_fill_price::float8 AS avg_fill_price,
               fees_usd::float8 AS fees_usd, placed_at, terminal_at
          FROM bettor_desk_orders
         WHERE desk_id = $1 AND account_id IS NULL
        """, desk_id)

    # EPOCHS OF THE HISTORICAL PERIOD ARE NOT IDENTIFIABLE. `boot_id`
    # was a per-row timestamp, so a restart cannot be located in rows
    # written before this release. Rows carrying a real `epoch_id` are
    # the ones written from here on.
    # THE CORRECTION IS SCOPED TO THE CLOSED PERIOD -- the rows with no
    # account. The new book's own fills are written per fill, so they
    # need no correction and must not be swept into this one.
    seg = await conn.fetchval(
        "SELECT count(*) FROM bettor_desk_ledger"
        " WHERE desk_id = $1 AND account_id IS NULL"
        "   AND epoch_id IS NOT NULL", desk_id)
    total = await conn.fetchval(
        "SELECT count(*) FROM bettor_desk_ledger"
        " WHERE desk_id = $1 AND account_id IS NULL", desk_id)
    epochs_identifiable = bool(total) and int(seg) == int(total)

    corr = [CORR.order_correction(dict(r)) for r in rows]
    summary = CORR.summarise(corr, epochs_identifiable=epochs_identifiable)

    written = 0
    async with conn.transaction():
        for c in corr:
            cid = "%s:ORDER:%s" % (CORR.VERSION, c["subject_id"])
            res = await conn.execute(
                """
                INSERT INTO bettor_desk_corrections
                       (correction_id, run_id, version, desk_id,
                        subject_kind, subject_id, status, reason,
                        schedule_id, original_fees_usd,
                        delta_fees_lower_usd, delta_fees_upper_usd,
                        delta_realized_lower_usd, delta_realized_upper_usd,
                        delta_cash_lower_usd, delta_cash_upper_usd,
                        delta_basis_usd, detail)
                VALUES ($1,$2,$3,$4,'ORDER',$5,$6,$7,$8,$9,$10,$11,$12,
                        $13,$14,$15,$16,$17::jsonb)
                ON CONFLICT (correction_id) DO NOTHING
                """, cid, run_id, CORR.VERSION, desk_id,
                c["subject_id"], c["status"], c["reason"],
                c.get("schedule_id"), c.get("original_fees_usd"),
                c.get("delta_fees_lower_usd"), c.get("delta_fees_upper_usd"),
                c.get("delta_realized_lower_usd"),
                c.get("delta_realized_upper_usd"),
                c.get("delta_cash_lower_usd"), c.get("delta_cash_upper_usd"),
                c.get("delta_basis_usd"), _js(c.get("detail")))
            if res and res.endswith("1"):
                written += 1
        await conn.execute(
            """
            UPDATE bettor_desk_correction_runs
               SET finished_at = now(), subjects_seen = $2,
                   subjects_exact = $3, subjects_bounded = $4,
                   subjects_incomplete = $5, subjects_written = $6,
                   accounting_status = $7, incomplete_reasons = $8::jsonb,
                   totals = $9::jsonb, reconciles = $10,
                   reconcile_detail = $11::jsonb
             WHERE run_id = $1
            """, run_id, len(corr),
            summary["counts"].get(CORR.EXACT, 0),
            summary["counts"].get(CORR.BOUNDED, 0),
            summary["counts"].get(CORR.INCOMPLETE, 0),
            written, summary["accounting_status"],
            _js(summary["incomplete_reasons"]), _js(summary["totals"]),
            bool(summary["reconcile"]["ok"]), _js(summary["reconcile"]))
    log.info("desk correction %s: %d subjects, %d written, status %s",
             CORR.VERSION, len(corr), written,
             summary["accounting_status"])
    return {"applied": True, "written": written, **summary}


async def _restore(conn, desk_id, account_id, desk) -> dict:
    """REBUILD THE BOOK FROM POSTGRES. This did not exist, and the
    module docstring claimed it did.

    THE DEFECT. `run()` constructed a fresh `Desk` and called
    `_load_cursor`, which reads `bettor_desk_state.cursor_event_id` and
    nothing else. Cash, legs and orders were never read back, so every
    process start began the book again at `starting_cash` with no
    positions -- and the first `_persist` then overwrote
    `bettor_desk_state.cash_usd` with the fresh desk's figure.

    IT PASSED EVERY CHECK WE HAD. `invariant_ok` stayed true because a
    fresh desk is internally consistent with its OWN starting cash; the
    cursor still advanced monotonically; exactly one state row still
    existed; and the decisions' earliest timestamps never moved,
    because no history was re-read. None of those can see a book that
    was silently discarded and begun again. I reported "restart-safety
    demonstrated" on the strength of them, and that was wrong.

    WHAT IS RESTORED, and what deliberately is not:

        cash, legs      from bettor_desk_positions and the state row.
        open orders     NOT restored. An order resting in the table
                        cannot be matched again without its
                        consumption ledger, which was never written
                        either; re-arming it would risk filling it a
                        second time against evidence already consumed.
                        They are marked terminal instead -- an expiry
                        that is recorded as what it is.

    The return value is written to `bettor_desk_epochs.restore_detail`
    so the first cycle after a restart can be read rather than assumed.
    """
    # SCOPED TO THE ACCOUNT, AND THAT IS THE WHOLE FIX. The previous
    # version read `bettor_desk_state` (cash from the LAST book) and
    # every row of `bettor_desk_positions` (legs from ALL books). Rows
    # written before accounts existed carry account_id IS NULL and are
    # excluded by these predicates, so they cannot enter the new book.
    st = await conn.fetchrow(
        """
        SELECT cash_usd::float8 AS cash,
               starting_cash_usd::float8 AS start,
               realized_pnl_usd::float8 AS realized,
               fees_usd::float8 AS fees
          FROM bettor_desk_account_state WHERE account_id = $1
        """, account_id)
    legs = await conn.fetch(
        """
        SELECT condition_id, outcome_index, qty::float8 AS qty,
               cost_basis_usd::float8 AS cost,
               realized_pnl_usd::float8 AS realized,
               fees_usd::float8 AS fees,
               extract(epoch FROM opened_at)::float8 AS opened,
               settled, settled_payout::float8 AS payout
          FROM bettor_desk_positions
         WHERE account_id = $1
        """, account_id)

    if st is None and not legs:
        return {"restored": False,
                "reason": "NO_PRIOR_STATE: this is a first start, not a "
                          "restart. The book begins at starting_cash."}

    if st is not None and st["cash"] is not None:
        desk.pf.cash = float(st["cash"])
    if st is not None and st["start"] is not None:
        desk.pf.starting_cash = float(st["start"])

    # REALIZED IS THE ACCOUNT'S, NOT THE SUM OF SURVIVING LEGS.
    # Summing leg["realized"] loses every leg that has since closed to
    # zero quantity and been settled, so the running total would walk
    # backwards on each restart.
    for r in legs:
        leg = desk.pf._leg(r["condition_id"], r["outcome_index"])
        leg["qty"] = float(r["qty"] or 0.0)
        leg["cost"] = float(r["cost"] or 0.0)
        leg["realized"] = float(r["realized"] or 0.0)
        leg["fees"] = float(r["fees"] or 0.0)
        leg["opened_at"] = r["opened"]
        leg["settled"] = bool(r["settled"])
        leg["payout"] = r["payout"]
    if st is not None:
        desk.pf.realized = float(st["realized"] or 0.0)
        desk.pf.fees = float(st["fees"] or 0.0)

    # ORDERS RESTING AT THE MOMENT OF THE RESTART ARE CLOSED, not
    # resurrected, and the reason is written on them rather than left
    # to be inferred from a gap.
    # RESTING ORDERS ARE RESTORED, with their partial fills intact.
    # They were previously expired on every restart because the
    # consumption ledger was not persisted. It is now: `_persist` writes
    # bettor_desk_consumption keyed on the evidence id, so an event
    # already allocated cannot be allocated again and re-arming is safe.
    orows = await conn.fetch(
        """
        SELECT order_id, desk_decision_id, condition_id, outcome_index,
               side, intent, limit_price::float8 AS limit_price,
               qty::float8 AS qty, filled_qty::float8 AS filled_qty,
               avg_fill_price::float8 AS avg_fill_price,
               fees_usd::float8 AS fees_usd, state,
               extract(epoch FROM placed_at)::float8 AS placed,
               extract(epoch FROM expires_at)::float8 AS expires
          FROM bettor_desk_orders
         WHERE account_id = $1
           AND state IN ('RESTING', 'PARTIALLY_FILLED')
        """, account_id)
    for r in orows:
        o = DK.Order(r["order_id"], condition_id=r["condition_id"],
                     outcome_index=r["outcome_index"], side=r["side"],
                     intent=r["intent"], limit_price=r["limit_price"],
                     qty=r["qty"], placed_at=r["placed"],
                     expires_at=r["expires"],
                     decision_id=r["desk_decision_id"])
        # `avg_fill_price` is a PROPERTY derived from `fills`, and
        # `Order` uses __slots__, so assigning it raises. The fills
        # themselves are what carry it, and they are reloaded below --
        # so the average comes back by derivation rather than by being
        # copied, which is why it cannot drift from its own fills.
        o.filled_qty = float(r["filled_qty"] or 0.0)
        o.fees = float(r["fees_usd"] or 0.0)
        o.state = r["state"]
        desk.orders[o.order_id] = o

    # THE PARTIAL FILLS THEMSELVES. Without them a restored
    # PARTIALLY_FILLED order would report an average fill price of None
    # while claiming a filled quantity -- the order would look filled
    # at no price.
    if orows:
        frows = await conn.fetch(
            """
            SELECT f.order_id, extract(epoch FROM f.at)::float8 AS at,
                   f.qty::float8 AS qty, f.price::float8 AS price,
                   f.fee_usd::float8 AS fee_usd, f.liquidity,
                   f.evidence_kind, f.evidence_id, f.exec_model
              FROM bettor_desk_fills f
             WHERE f.account_id = $1
               AND f.order_id = ANY($2::text[])
             ORDER BY f.at
            """, account_id, [r["order_id"] for r in orows])
        for f in frows:
            o = desk.orders.get(f["order_id"])
            if o is not None:
                o.fills.append({
                    "at": f["at"], "qty": f["qty"], "price": f["price"],
                    "fee_usd": f["fee_usd"], "liquidity": f["liquidity"],
                    "evidence_id": f["evidence_id"],
                    "evidence_kind": f["evidence_kind"],
                    "exec_model": f["exec_model"]})

    # AND THE CONSUMPTION LEDGER, so a restored order cannot re-consume
    # evidence the previous process already allocated to it.
    crows = await conn.fetch(
        """
        SELECT evidence_id, available_qty::float8 AS available,
               consumed_qty::float8 AS consumed
          FROM bettor_desk_consumption WHERE account_id = $1
        """, account_id)
    for r in crows:
        # Two dicts, matching Consumption's own shape. A single dict of
        # dicts would have been a different structure that `take()`
        # cannot read.
        desk.cons.available[r["evidence_id"]] = float(r["available"])
        desk.cons.consumed[r["evidence_id"]] = float(r["consumed"])
    closed = "restored %d resting, %d consumption rows" % (len(orows),
                                                           len(crows))

    inv = desk.pf.invariant()
    return {"restored": True, "account_id": account_id,
            "legs": len(legs),
            "cash_usd": round(desk.pf.cash, 2),
            "realized_usd": round(desk.pf.realized, 2),
            "inventory_cost_usd": round(desk.pf.inventory_cost(), 2),
            "orders_restored": closed,
            "invariant_after_restore": inv,
            # THE RESTORE IS NOT TRUSTED BLINDLY. If the rebuilt book
            # does not satisfy the identity, that is reported here and
            # the epoch row carries it; a restore that silently
            # produced an inconsistent book would be worse than no
            # restore at all.
            "reconciles": bool(inv["ok"])}


async def _pause(conn, account_id) -> dict:
    """Is this account paused? Read EVERY cycle, and FAIL CLOSED.

    WHY EVERY CYCLE rather than once at startup: pausing and resuming
    then need no deploy, which matters because a deploy is the thing
    that was destabilising the release.

    WHY FAIL CLOSED: if the flag cannot be read we do not know whether
    we are paused, and a desk that trades while unsure whether it has
    been stopped is worse than one that idles. An unreadable flag is
    treated as PAUSED and says so.

    THE FLAG ALONE CANNOT STOP A PROCESS RUNNING OLDER CODE. Nothing
    read this column before this build, so the previous writer will
    ignore it no matter what the row says. The flag stops THIS build;
    the lock handover is what stops the previous one, and that has to
    be VERIFIED rather than assumed.
    """
    try:
        row = await conn.fetchrow(
            """
            SELECT paused, pause_reason, accounting_status
              FROM bettor_desk_accounts WHERE account_id = $1
            """, account_id)
    except Exception as exc:                                # noqa: BLE001
        return {"paused": True,
                "reason": "PAUSE_FLAG_UNREADABLE (%s) -- treated as "
                          "paused, because a desk that trades while "
                          "unsure whether it was stopped is worse than "
                          "one that idles" % type(exc).__name__,
                "accounting_status": "UNKNOWN"}
    if row is None:
        return {"paused": True,
                "reason": "ACCOUNT_ROW_MISSING -- treated as paused",
                "accounting_status": "UNKNOWN"}
    return {"paused": bool(row["paused"]),
            "reason": row["pause_reason"],
            "accounting_status": row["accounting_status"]}


async def _load_cursor(conn, account_id) -> int:
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
        "SELECT cursor_event_id FROM bettor_desk_account_state"
        " WHERE account_id = $1", account_id)
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


def live_fee_fn(qty, price, maker):
    """THE FEE SCHEDULE THE LIVE LANE BOOKS AGAINST.

    The loop was started as `run(_desk_pool)` with `fee_fn` defaulting
    to None, so `Desk` fell back to a silent zero-fee lambda and every
    live fill was booked FREE. The ledger still reported `invariant_ok`
    throughout, because the identity holds whether or not a cost was
    ever charged -- so the one check watching the book could not see
    the gap. The default is now supplied here rather than left to a
    caller to remember.

    SAME SCHEDULE AS THE REPLAY, deliberately: a live total and a
    replay total already differ in execution assumptions, and letting
    them differ in COSTS as well would make them incomparable for a
    second, avoidable reason.

    THIS IS STILL A TRANSFERRED SCENARIO. The schedule is PMUS; the
    evidence driving the live lane is Polymarket global. `fee_basis`
    records that costs were applied, not that they were the right
    venue's -- `DK.VENUE_TRANSFER` is what carries that, and it is
    unchanged.
    """
    return float(FEES.LATEST.fill_fee(Decimal(str(round(qty, 6))),
                                      Decimal(str(round(price, 6))),
                                      maker=bool(maker)))


async def run(get_pool, *, desk_id="live1", policy=None, limits=None,
              fee_fn=live_fee_fn, queue_share=QUEUE_SHARE_DEFAULT):
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
            # THE ACCOUNT COMES FIRST. It is durable: this returns the
            # existing ACTIVE book on every restart and creates one only
            # the first time. `opening_balance` is written by the INSERT
            # alone, so no restart can replenish capital.
            acct = await ACC.ensure_account(
                conn, desk_id, opening_balance=lim.starting_cash)
            account_id = acct["account_id"]
            desk.account_id = account_id
            # EVERY GENERATED ID IS NAMESPACED BY THE BOOK from here.
            # With the desk_id prefix, a new account's first order id
            # collided with the previous book's and the upserts either
            # overwrote a preserved row or dropped a new one.
            desk.id_prefix = account_id
            desk.pf.starting_cash = float(acct["opening_balance"])
            # THE PAUSE IS READ BEFORE ANY STARTUP WRITE, and this is a
            # CORRECTION. The check used to sit only at the top of the
            # cycle loop, so a paused account still got its epoch row
            # ended and a new one opened on every boot -- a paused desk
            # writing. Production shows it: epoch 4 was opened at
            # 16:51:40Z, on a build that came up to an account already
            # flagged paused at 16:51:38Z. Nothing accounting-bearing
            # moved, but "writes nothing" was not true, and a claim that
            # is nearly true is the kind this release keeps having to
            # withdraw.
            #
            # `ensure_account` has to run first because the account id is
            # what the flag is keyed on; it is an INSERT ... ON CONFLICT
            # that is a no-op for an existing book.
            #
            # IT IDLES HERE RATHER THAN RESTORING. Restoring and then
            # idling would leave a live book in memory behind a pause,
            # and clearing the flag mid-cycle would resume from it. It
            # waits, re-reading the flag, and only when the flag clears
            # does it fall through to the restore -- so a resumed desk
            # always restores before it trades, which is the failure
            # that cost $2,367.72.
            while True:
                pz0 = await _pause(conn, account_id)
                if not pz0["paused"]:
                    break
                _status.update(state=STATE_PAUSED, error=None,
                               epoch_id=EPOCH_ID, account_id=account_id,
                               paused=True, pause_reason=pz0["reason"],
                               accounting_status=pz0["accounting_status"],
                               restore={"restored": False, "why":
                                        "PAUSED_AT_STARTUP: the book is "
                                        "not restored while paused, so "
                                        "nothing is held in memory that "
                                        "a cleared flag could resume."},
                               last_cycle_at=time.time())
                await asyncio.sleep(CYCLE_S)
            cursor = await _load_cursor(conn, account_id)
            # THE BOOK COMES BACK BEFORE THE FIRST EVENT IS STEPPED.
            # Restoring after the first cycle would book that cycle's
            # fills against an empty portfolio and then overwrite them.
            restore = await _restore(conn, desk_id, account_id, desk)
            await _open_epoch(conn, desk_id, account_id, desk,
                              cursor, restore)
            # A BOOK THAT DOES NOT RECONCILE MUST NOT TRADE.
            #
            # FOUND IN PRODUCTION at 15:48Z, by this check, on the
            # first run of the restore it guards. `_restore` took CASH
            # from `bettor_desk_state` -- which reflects the LAST epoch
            # -- and LEGS from `bettor_desk_positions`, which still
            # holds every leg ever written, including those of earlier
            # books abandoned when the loop restarted without restoring
            # them. Cash from one era, legs from several: open
            # positions jumped 52 -> 96 and the identity drifted
            # +$2,367.73.
            #
            # WHICH LEGS BELONG TO THIS BOOK IS NOT RECOVERABLE. The
            # rows carry no epoch, because boot_id was a per-row
            # timestamp until this release. So there is no arithmetic
            # that repairs it, and any rule I picked -- drop the
            # oldest, keep the largest, scale to fit -- would be an
            # invented accounting treatment presented as a recovery.
            #
            # Therefore: halt. Do not step events, do not write further
            # snapshots, publish the drift. A desk whose book is wrong
            # by $2,367 must stop, not continue carefully.
            if restore.get("restored") and not restore.get("reconciles"):
                inv = restore.get("invariant_after_restore") or {}
                _status.update(
                    state=STATE_ERROR, epoch_id=EPOCH_ID,
                    account_id=account_id, restore=restore,
                    error=("RESTORED_BOOK_DOES_NOT_RECONCILE: drift %s. "
                           "Cash is from the last epoch; legs include "
                           "earlier abandoned epochs, and the rows carry "
                           "no epoch to separate them. HALTED: no events "
                           "are processed and no snapshot is written. "
                           "Resolution is an explicit accounting "
                           "decision, not a default."
                           % inv.get("drift")))
                log.error("desk loop HALTED: restored book drifts %s",
                          inv.get("drift"))
                while True:
                    await asyncio.sleep(CYCLE_S)
            # THE CORRECTION RUNS HERE, under the writer lock, exactly
            # once per version. Holding the lock is what makes it safe;
            # the unique index on (desk_id, version) is what makes it
            # idempotent even if the lock were lost.
            await apply_corrections(conn, desk_id)
        except Exception as exc:                            # noqa: BLE001
            _status.update(state=STATE_ERROR,
                           error="STARTUP: %s: %s" % (type(exc).__name__,
                                                      str(exc)[:200]))
            log.exception("desk loop could not complete startup")
            while True:
                await asyncio.sleep(CYCLE_S)

        _status.update(state=STATE_RUNNING, since=time.time(), error=None,
                       epoch_id=EPOCH_ID, account_id=account_id,
                       account_opened_at=str(acct.get("opened_at") or ""),
                       account_opening_balance=acct.get("opening_balance"),
                       account_created_now=bool(acct.get("created")),
                       account_note=acct.get("note"), restore=restore)
        log.info("desk loop RUNNING from event id %s (epoch %s, restored "
                 "%s)", cursor, EPOCH_ID, restore.get("restored"))

        while True:
            try:
                # THE PAUSE IS CHECKED BEFORE ANY EVENT IS READ, so a
                # paused desk performs no work and writes nothing --
                # not even a ledger snapshot, which would otherwise
                # make a stopped desk look like a running one.
                pz = await _pause(conn, account_id)
                if pz["paused"]:
                    _status.update(state=STATE_PAUSED, error=None,
                                   account_id=account_id,
                                   paused=True,
                                   pause_reason=pz["reason"],
                                   accounting_status=pz["accounting_status"],
                                   last_cycle_at=time.time())
                    await asyncio.sleep(CYCLE_S)
                    continue
                _status.update(paused=False, pause_reason=None,
                               accounting_status=pz["accounting_status"])
                rows = await _events(conn, cursor, BATCH)
                for r in rows:
                    desk.step(_to_event(r))
                    cursor = int(r["id"])
                    _status["events_seen"] += 1
                if rows:
                    await _persist(conn, desk_id, account_id,
                                   desk, cursor)
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


async def _persist(conn, desk_id, account_id, desk, cursor):
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
                        inputs, inputs_sha, account_id)
                VALUES ($1,$2,$3,to_timestamp($4),to_timestamp($4),$5,$6,
                        $7,$8,$9,$10,$11,$12,$13::jsonb,$14::jsonb,
                        $15::jsonb,$16::jsonb,$17,$18)
                ON CONFLICT (desk_decision_id) DO NOTHING
                """,
                d["desk_decision_id"], desk_id, EPOCH_ID,
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
                d["inputs_sha"], account_id)
        # THE ACCOUNT'S STATE: cash, realized, fees AND the cursor in ONE
        # row, updated by ONE statement inside this transaction. That is
        # what makes the interrupted-write case safe -- there is no
        # ordering in which the cursor advances without the cash it
        # belongs to, because they are the same UPDATE.
        await conn.execute(
            """
            INSERT INTO bettor_desk_account_state
                   (account_id, desk_id, cursor_event_id, cash_usd,
                    starting_cash_usd, realized_pnl_usd, fees_usd,
                    epoch_id, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,now())
            ON CONFLICT (account_id) DO UPDATE
               SET cursor_event_id = EXCLUDED.cursor_event_id,
                   cash_usd = EXCLUDED.cash_usd,
                   realized_pnl_usd = EXCLUDED.realized_pnl_usd,
                   fees_usd = EXCLUDED.fees_usd,
                   epoch_id = EXCLUDED.epoch_id,
                   updated_at = now()
            """, account_id, desk_id, int(cursor),
            float(desk.pf.cash), float(desk.pf.starting_cash),
            float(desk.pf.realized), float(desk.pf.fees), EPOCH_ID)

        # THE CONSUMPTION LEDGER, persisted at last. Keyed on the
        # evidence id, so a re-read event cannot be allocated twice, and
        # the CHECK in migration 094 refuses an overdraw at the database
        # rather than trusting the matcher.
        for eid, avail in desk.cons.available.items():
            await conn.execute(
                """
                INSERT INTO bettor_desk_consumption
                       (evidence_id, account_id, evidence_kind,
                        evidence_ts, condition_id, outcome_index, price,
                        available_qty, consumed_qty, updated_at)
                VALUES ($1,$2,'COHORT_PRINT',now(),'',0,0,$3,$4,now())
                ON CONFLICT (evidence_id, account_id) DO UPDATE
                   SET available_qty = GREATEST(
                           bettor_desk_consumption.available_qty,
                           EXCLUDED.available_qty),
                       consumed_qty = EXCLUDED.consumed_qty,
                       updated_at = now()
                """, str(eid), account_id, float(avail),
                float(desk.cons.consumed.get(eid, 0.0)))

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
            """, desk_id, EPOCH_ID, DK.POLICY_VERSION,
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
                        terminal_at, updated_at, account_id)
                VALUES ($1,$2,$3,$4,$5,NULL,$6,$7,$8,$9,$10,$11,$12,$13,
                        $14,$15,$16,to_timestamp($17),to_timestamp($18),
                        CASE WHEN $19::float8 IS NULL THEN NULL
                             ELSE to_timestamp($19) END, now(), $20)
                ON CONFLICT (order_id) DO UPDATE
                   SET filled_qty = EXCLUDED.filled_qty,
                       avg_fill_price = EXCLUDED.avg_fill_price,
                       fees_usd = EXCLUDED.fees_usd,
                       state = EXCLUDED.state,
                       state_reason = EXCLUDED.state_reason,
                       terminal_at = EXCLUDED.terminal_at,
                       updated_at = now()
                """, o.order_id, desk_id, EPOCH_ID,
                o.decision_id, o.condition_id, o.outcome_index, o.side,
                o.intent, o.limit_price, o.qty, o.filled_qty, o.notional,
                o.avg_fill_price, o.fees, o.state, o.state_reason,
                o.placed_at, o.expires_at, o.terminal_at,
                account_id)

            # EVERY FILL, PER FILL. This table was created by migration
            # 094 and never written, which is the whole reason today's
            # fee correction can only be an INTERVAL: PMUS rounds per
            # fill, and an order's average price cannot reconstruct the
            # sum of independently rounded amounts.
            #
            # The evidence id is the natural key, so re-reading an
            # event after a failed cycle cannot book the fill twice.
            for i, f in enumerate(o.fills):
                await conn.execute(
                    """
                    INSERT INTO bettor_desk_fills
                           (fill_id, order_id, at, qty, price, fee_usd,
                            liquidity, evidence_kind, evidence_id,
                            evidence_ts, exec_model, account_id)
                    VALUES ($1,$2,to_timestamp($3),$4,$5,$6,$7,$8,$9,
                            to_timestamp($10),$11,$12)
                    ON CONFLICT (fill_id) DO NOTHING
                    """,
                    # THE KEYS ARE READ WITHOUT DEFAULTS ON PURPOSE.
                    # My first version reached for f["fee"], which the
                    # engine does not produce -- it writes `fee_usd` --
                    # so a `.get("fee", 0.0)` would have written 0.00
                    # into every row and recreated, in the fills table,
                    # the exact fee-free book this release exists to
                    # correct. A KeyError here fails the cycle loudly.
                    "%s:%d" % (o.order_id, i), o.order_id,
                    float(f["at"]), float(f["qty"]), float(f["price"]),
                    float(f["fee_usd"]), f["liquidity"],
                    f["evidence_kind"], str(f["evidence_id"]),
                    float(f["at"]), f["exec_model"], account_id)

        for (cond, oi), leg in desk.pf.legs.items():
            await conn.execute(
                """
                INSERT INTO bettor_desk_positions
                       (desk_id, condition_id, outcome_index, qty,
                        cost_basis_usd, realized_pnl_usd, fees_usd,
                        opened_at, updated_at, settled, settled_payout,
                        account_id)
                VALUES ($1,$2,$3,$4,$5,$6,$7,
                        CASE WHEN $8::float8 IS NULL THEN NULL
                             ELSE to_timestamp($8) END, now(), $9, $10,
                        $11)
                ON CONFLICT (desk_id, coalesce(account_id, ''),
                             condition_id, outcome_index)
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
                leg["payout"], account_id)

        inv = snap["invariant"]
        await conn.execute(
            """
            INSERT INTO bettor_desk_ledger
                   (desk_id, boot_id, at, cash_usd, committed_usd,
                    inventory_cost, inventory_mark, mark_basis,
                    realized_pnl_usd, unrealized_pnl_usd, fees_usd,
                    open_orders, open_positions, invariant_ok,
                    invariant_detail, epoch_id, account_id)
            VALUES ($1,$2,now(),$3,$4,$5,NULL,$6,$7,NULL,$8,$9,$10,$11,
                    $12::jsonb,$13,$14)
            """, desk_id, EPOCH_ID,
            float(desk.pf.cash), float(desk.committed_usd()),
            float(desk.pf.inventory_cost()),
            "NOT_IDENTIFIED: no contemporaneous book is retained for "
            "these instants, so there is no mark and no liquidation "
            "estimate",
            float(desk.pf.realized), float(desk.pf.fees),
            len(desk.open_orders()), len(desk.pf.open_legs()),
            # THE FEE BASIS IS STORED WITH THE ROW, not inferred from
            # a zero. `fees_usd = 0` is ambiguous on its own -- it is
            # equally what a fee-free book and a genuinely costless
            # window look like -- and the live lane ran fee-free for 37
            # minutes without that being visible anywhere.
            bool(inv["ok"]),
            _js(dict(inv, fee_basis=desk.fee_basis,
                     pnl_is_net_of_fees=(
                         desk.fee_basis == DK.FEE_BASIS_APPLIED))),
            EPOCH_ID, account_id)

    # DRAIN ONLY WHAT WAS COMMITTED. The transaction has returned, so
    # these rows are durable; dropping them keeps the in-memory desk a
    # bounded cache instead of an unbounded log. Anything the engine
    # appended during the write stays queued for the next cycle.
    del desk.decisions[:len(pending)]
