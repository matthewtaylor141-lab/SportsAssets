"""THE PAPER PASS: DEREK, THE SIMULATOR, XAVIER AND AUDREY ON THE PAPER BOOK.

Runs inside the existing scheduled cycle and servicing task
(`agents.runtime.paper_pass_hook`, called by `workers/ext_pinnacle_loop` after
the execution lock is released), ONLY when both the environment flag
PAPER_SESSION=on and the database control row are on
(`bettor_paper_session.enablement`). It never holds the funded execution
lock, never blocks Xavier's funded servicing or the collection cycle, and is
BOUNDED: the steps check a monotonic deadline (the session config's
`cadence.pass_budget_s`), each step is cut at the pass time left for steps,
and the runtime hook adds a hard timeout as the last resort. What that bound
guarantees -- and what it does not -- is stated exactly under "THE PASS'S
RECORD IS KEPT WHEN A STEP IS CUT" below.

ONE PASS AT A TIME, ACROSS TASKS AND PROCESSES: a process-level lock and a
Postgres advisory lock; a pass that finds either held returns BUSY and does
nothing (the next pass picks up). Every write is idempotent by key, so a
pass cut short or a process restart resumes without duplicate orders or
fills and without losing positions.

THE STEPS, IN ORDER (each guarded; a failing step is recorded and the rest
run):
    books     read the books of markets with open paper orders or positions
    simulate  advance open orders on the books observed so far
    derek     V2 paper decisions on this cycle's valuations; ENTER -> order
    settle    settle open positions from authoritative outcome evidence
    handoff   hand every group to Xavier from its first simulated fill
    xavier    review each held group (first fill, fill / market events,
              scheduled backstop); standing protection
    equity    one equity snapshot (drawdown is measured on these)
    audrey    continuous monitoring and the daily report (America/New_York)
    audrey_events  Audrey's audit of each meaningful event, as it happens
    learning  lessons in each agent's memory and improvement proposals
              from forward records (at most hourly; paper only)

THE MARKET-DATA CLIENT is a `bettor_paper_guard.PaperMarketDataClient`: one
read, every mutation refused before transmission and counted in the
session's health record (expected 0).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_session as S

log = logging.getLogger(__name__)

VERSION = "PAPER_RUNTIME_V1"
ADVISORY_LOCK_KEY = 0x50415052            # "PAPR"
R_BUSY = "ANOTHER_PAPER_PASS_IS_RUNNING"
R_DISABLED = "PAPER_SESSION_NOT_ENABLED"

_LOCK: dict = {"lock": None, "loop": None}


def _proc_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if _LOCK["lock"] is None or _LOCK["loop"] is not loop:
        _LOCK["lock"] = asyncio.Lock()
        _LOCK["loop"] = loop
    return _LOCK["lock"]


def _budget_left(ctx: dict) -> bool:
    return time.monotonic() < ctx["deadline"]


# ═════════════════════════════════════════════════════════════════════
# STEPS
# ═════════════════════════════════════════════════════════════════════

async def _stalest_first(conn, slugs: list) -> list:
    if not slugs:
        return []
    ages = {r["us_market_slug"]: L._epoch(r["at"]) for r in await conn.fetch(
        "SELECT us_market_slug, max(observed_at) AS at FROM "
        " paper_book_observations WHERE us_market_slug = ANY($1::text[]) "
        " GROUP BY 1", slugs)}
    return sorted(dict.fromkeys(slugs), key=lambda s: ages.get(s) or 0.0)


async def step_books(conn, ctx: dict) -> dict:
    """Read the books of markets with open paper orders or open positions,
    within the per-pass read budget. Each read is recorded as observed.

    PRIORITY (2026-10-01): (1) marketable entries waiting for their first
    eligible book -- a fill decision is due; (2) resting orders, stalest
    observation first -- a fill can only be simulated on a book observed
    after placement; (3) held positions, stalest first, for marks and
    Xavier's exit ladder. A read this process made within seconds answers
    without a second venue request (`bettor_paper_guard`).

    XAVIER'S FRESH-EVIDENCE PRIORITY LIST (owner R30, migration 226): the
    slugs of open VENUE_BOOK requests (`work_queue.priority_book_slugs`, at
    most work_queue.MAX_PRIORITY_BOOKS_PER_PASS, oldest request first) are
    read right after (1) and before everything else -- inside the same
    half-of-the-pass cap, so no venue read is added, only re-ordered; each
    read closes its request with the observation id (or FAILED)."""
    from . import work_queue as WQ
    acct = ctx["account_id"]
    rows = await conn.fetch(
        "SELECT us_market_slug, order_type, state, "
        "       extract(epoch FROM eligible_at)::float8 AS eligible_epoch "
        "  FROM paper_orders "
        " WHERE account_id=$1 AND state = ANY($2::text[])", acct,
        list(L.OPEN_STATES))
    # A MARKETABLE ENTRY IS DUE ONLY ONCE IT IS ELIGIBLE (R30A). Read before
    # its eligible instant, the book cannot be used for its fill (the
    # simulator takes the first readable book AT OR AFTER decision + delay),
    # so the read spent one of the step's half-cap slots for nothing -- and,
    # worse, primed the 6 s shared-read cache, so the after-delay read a
    # second later was answered with that same pre-eligible receipt and was
    # wasted too. Production, 7 d to 2026-10-04 20:54Z (research-sql run
    # 37233864395, E2): 75 of the 100 entry orders that expired unread had a
    # books-step read made after the decision but before eligibility. A
    # not-yet-eligible entry is read by step_after_delay / the entry-fill
    # read (`schedule_entry_fill`) at its eligible instant instead.
    clock_now = float(ctx["clock"]()) if ctx.get("clock") else float(
        ctx["now"])
    pending_slugs = {r["us_market_slug"] for r in rows
                     if r["order_type"] == "MARKETABLE"
                     and r["state"] == "PENDING_SIMULATION"}
    due = [r["us_market_slug"] for r in rows
           if r["order_type"] == "MARKETABLE"
           and r["state"] == "PENDING_SIMULATION"
           and (r["eligible_epoch"] is None
                or float(r["eligible_epoch"]) <= clock_now)]
    not_yet = pending_slugs - set(due)
    # each due entry's read must be RECEIVED at or after its eligible instant
    due_not_before = {}
    for r in rows:
        if (r["order_type"] == "MARKETABLE"
                and r["state"] == "PENDING_SIMULATION"
                and r["us_market_slug"] in due
                and r["eligible_epoch"] is not None):
            due_not_before[r["us_market_slug"]] = max(
                float(r["eligible_epoch"]),
                due_not_before.get(r["us_market_slug"], 0.0))
    prio = [s for s in await WQ.priority_book_slugs(conn, account_id=acct)
            if s not in due and s not in not_yet]
    resting = await _stalest_first(conn, [
        r["us_market_slug"] for r in rows
        if r["us_market_slug"] not in due and r["us_market_slug"]
        not in prio and r["us_market_slug"] not in not_yet])
    slugs = list(dict.fromkeys(due + prio + resting))
    held = await _stalest_first(conn, [
        p["us_market_slug"] for p in await L.positions(conn, acct)
        if p["us_market_slug"] not in slugs
        and p["us_market_slug"] not in not_yet])
    slugs.extend(held)
    # AT MOST HALF THE PASS'S READS: the other half is Derek's.
    cap = int(ctx["config"]["cadence"]["max_book_reads_per_pass"])
    got = await read_books(conn, ctx, slugs, basis="OPEN_ORDER_OR_POSITION",
                           limit=max(1, cap // 2),
                           not_before=due_not_before)
    got["deferred_until_eligible"] = len(not_yet)
    if prio:
        read_prio = [s for s in prio if s in got["obs"]]
        ctx["work_queue_books_read"] = len(read_prio)
        got["work_queue"] = dict(await WQ.complete_book_reads(
            conn, {s: got["obs"][s] for s in read_prio},
            at=ctx["clock"]() if ctx.get("clock") else ctx["now"]),
            priority=len(prio), priority_read=len(read_prio))
    return got


async def read_books(conn, ctx: dict, slugs: list, *, basis: str,
                     limit: int | None = None,
                     not_before: dict | None = None) -> dict:
    """`not_before` (R30A): {slug: epoch} -- for a pending marketable entry,
    its eligible instant: the read must be received at or after it (the
    shared-read cache may not answer with an older receipt)."""
    from .. import bettor_paper_simulator as SIM
    cap = int(ctx["config"]["cadence"]["max_book_reads_per_pass"])
    out = {"read": 0, "errors": 0, "skipped_budget": 0, "obs": {}}
    nb_map = dict(not_before or {})
    for slug in slugs:
        if ctx["books_read"] >= cap or not _budget_left(ctx) or (
                limit is not None and out["read"] >= limit):
            out["skipped_budget"] += 1
            continue
        # BOUNDED BY THE PASS DEADLINE: a read the venue cooldown would hold
        # past the pass budget is refused by name, never left to overrun it.
        from . import paper_derek as _PD
        got = await _PD.read_book_within_deadline(
            ctx, slug, not_before_epoch=nb_map.get(slug))
        ctx["books_read"] += 1
        rec = await SIM.record_book(conn, slug=slug, read=got,
                                    source="PAPER_MARKET_DATA_CLIENT",
                                    read_basis=basis)
        out["read"] += 1
        out["errors"] += 1 if rec.get("error") else 0
        out["obs"][slug] = rec
    return out


async def step_simulate(conn, ctx: dict) -> dict:
    from .. import bettor_paper_simulator as SIM
    got = await SIM.run(conn, account_id=ctx["account_id"],
                        now=ctx["clock"](),
                        fee_fn=ctx.get("fee_fn"), deadline=ctx["deadline"])
    ctx["first_fills"].extend(got.get("first_fills") or [])
    ctx["fills"] += got.get("fills") or 0
    return {k: got.get(k) for k in ("examined", "fills", "terminal",
                                    "budget_exhausted")}


async def step_equity(conn, ctx: dict) -> dict:
    from .. import bettor_paper_readmodel as RM
    return await RM.snapshot_equity(conn, session_id=ctx["session_id"],
                                    account_id=ctx["account_id"],
                                    now=ctx["now"])


def _benchmark_env_on() -> bool:
    """PAPER_BENCHMARK in (on, 1, true, yes): read here without importing
    the benchmark module, so with the flag unset nothing of it loads."""
    import os
    return str(os.environ.get("PAPER_BENCHMARK", "")).strip().lower() in (
        "on", "1", "true", "yes")


def default_steps() -> list:
    steps = [("books", step_books)]
    if _benchmark_env_on():
        # THE MAKER POLICY'S STANDING ENTRY ORDERS, re-checked against their
        # cancellation conditions BEFORE the simulator step, which confirms
        # any cancel it requests.
        from . import paper_maker as PMK
        steps.append(("maker_maintain", PMK.step_maintain))
    steps.append(("simulate", step_simulate))
    try:
        # PAPER TURNAROUND (migration 290): the predeclared loss / drawdown
        # rules applied to every strategy on the MAIN paper account, at most
        # every RUN_EVERY_S, BEFORE this pass's entries. Records demotions
        # only (never a promotion, never an order or a cap).
        from .. import bettor_strategy_lifecycle as LC
        steps.append(("turnaround", LC.step))
    except ImportError:
        pass
    try:
        # ZERO-CAPITAL SHADOW LEARNING (migration 305): the counterfactual
        # outcome of every shadow decision whose contract has settled, at
        # most every RUN_EVERY_S. Evidence only (never an order, never
        # cash); labelled SHADOW_COUNTERFACTUAL / NOT_REALIZED_PNL.
        from .. import bettor_capital_authority as CA
        steps.append(("shadow_settlement", CA.step))
    except ImportError:
        pass
    try:
        # THE PROFITABILITY BIND'S LEARNED INPUTS (migration 309): the
        # sport x family x regime calibration, the learned execution
        # economics and the expected-vs-realized residuals, refitted at most
        # every FIT_EVERY_S BEFORE this pass's entries read them. Records
        # models only.
        from .. import bettor_paper_profitability_bind as PBIND
        steps.append(("profitability_fit", PBIND.step))
    except ImportError:
        pass
    try:
        # THE PROFITABILITY STACK (migration 311): the automatic QUARANTINE
        # of a strategy whose calibration, residuals or execution
        # deteriorated (a lifecycle tightening only; the entry gate then
        # refuses its new entries) BEFORE this pass's entries, and the
        # settlement of the counterfactual variant ledger (evidence only).
        from .. import bettor_paper_profitability_stack as PSTACK
        steps.append(("profitability_quarantine", PSTACK.quarantine_step))
        steps.append(("counterfactual_settlement",
                      PSTACK.counterfactual_step))
    except ImportError:
        pass
    try:
        from . import paper_derek as PD
        steps.append(("derek", PD.step))
        # THE EXPERIMENTAL PINNACLE_ONLY_PAPER_BENCHMARK, a separate strategy
        # on the same session and ledger: present ONLY when the process sets
        # PAPER_BENCHMARK=on (default off -- then the pass is as before); its
        # step also checks its kill-switch row. Its orders are simulated by
        # the delayed-fill step that follows, like Derek's.
        if _benchmark_env_on():
            from . import paper_benchmark as PB
            steps.append(("benchmark", PB.step))
            # THE COMPLETED-GAME PAPER POLICY (experimental, own strategy and
            # version, own kill-switch row), on the same pass and ledger.
            steps.append(("benchmark_completed_game",
                          PB.step_completed_game))
            # THE MAKER-ENTRY POLICY and THE BOUNDED EXPLORATION STRATEGY
            # (owner-authorized 2026-10-01; each its own key, version and
            # kill-switch row), after the investment policy so it decides a
            # valuation first.
            from . import paper_maker as PMK
            from . import paper_explore as PEX
            steps.append(("maker_entry", PMK.step))
            steps.append(("exploration", PEX.step))
        # THE EXPLICIT CASH DECISION (migration 309): every strategy that
        # evaluated candidates this pass and entered none records CASH with
        # its binding refusals and best refused candidate.
        from .. import bettor_paper_profitability_bind as _PBIND
        steps.append(("cash_fallback", _PBIND.cash_step))
        steps.append(("simulate_after_delay", PD.step_after_delay))
        # EVERY RECORDED ENTER HAS AN ORDER OR A NAMED FINDING (P0 incident
        # 2026-10-04): an ENTER older than PD.ENTER_WITHOUT_ORDER_AFTER_S
        # with no paper order and no order refusal -> ENTER_WITHOUT_ORDER.
        # Records only; never places a late order.
        steps.append(("enter_backstop", PD.step_enter_backstop))
    except ImportError:
        pass
    try:
        from . import paper_xavier as PX
        steps.append(("settle", PX.step_settle))
        steps.append(("handoff", PX.step_handoff))
        steps.append(("xavier", PX.step))
        # XAVIER'S VALUE-ADD (migration 206): the counterfactuals frozen at
        # entry, computed once an outcome is known. Records only.
        from . import xavier_management as XM
        steps.append(("xavier_value_add", XM.step_value_add))
        # XAVIER'S FRESH-EVIDENCE WORK (owner R30, migration 226): the
        # requests his stale-evidence reviews enqueued -- expired ones
        # FAILED, landed evidence COMPLETED, held re-evaluations / priority
        # book reads / re-reviews dispatched, all bounded per pass.
        from . import work_queue as WQ
        steps.append(("xavier_work_queue", WQ.drain))
    except ImportError:
        pass
    steps.append(("equity", step_equity))
    try:
        from . import paper_audrey as PA
        steps.append(("audrey", PA.step))
    except ImportError:
        pass
    try:
        # AUDREY'S OPERATIONAL AUDIT (migration 189): funnel, book reads,
        # fees, stale inputs, missing decisions, coverage, management --
        # findings and recommendations to Derek and Xavier, at most every
        # 10 minutes. Never places or changes an order.
        from . import paper_ops_audit as POA
        steps.append(("audrey_operations", POA.step))
    except ImportError:
        pass
    try:
        # THE LEARNING RECORD (migration 185): Audrey audits each meaningful
        # event of this pass as it happens (first fill, handoff, management
        # fill, settlement incl. SETTLED_AT_VENUE_PRICE, exceptional outcome,
        # ledger inconsistency) -- once per event -- and, at most hourly,
        # the agents' lessons and improvement proposals are derived from the
        # forward records. Neither places, cancels or changes an order.
        from . import paper_learning as PLRN
        steps.append(("audrey_events", PLRN.step_audit_events))
        steps.append(("learning", PLRN.step_learning))
    except ImportError:
        pass
    try:
        # COVERAGE INTEGRITY, POSTMORTEMS AND THE IMPROVEMENT DRIVER
        # (migration 209): on the main paper account's pass only, each on its
        # own watermark (15 min / 15 min / hourly). The funnel snapshots and
        # collapse alerts (-> Audrey findings), every closed position's
        # postmortem, and ordinary deficits moved through the collaboration
        # loop up to a PAPER_ONLY experiment registration. None of them
        # places, cancels or changes an order, a limit or a policy.
        from . import coverage_integrity as COV
        from . import improvement_driver as IDRV
        from . import postmortems as PMT
        steps.append(("audrey_coverage", COV.step))
        steps.append(("audrey_postmortems", PMT.step))
        steps.append(("improvement_driver", IDRV.step))
    except ImportError:
        pass
    try:
        # THE AGENTS' DURABLE WORK QUEUES (owner R30 section 17, migration
        # 301): on the main account's pass, at most every minute, Derek's
        # candidates awaiting fresh evidence, Allie's allocation reviews and
        # Audrey's open reconciliations are enqueued with their owner, SLA,
        # blocker, evidence needed and collaborator, and closed by the
        # records that resolve them. Writes only agent_work_* records.
        from . import agent_work as AWQ
        # ROOT-CAUSE CLUSTERS (owner R30 section 20): repeated Karen /
        # Audrey findings opened as ONE engineering item each, and a linked
        # fix's measured effect -- before the queue step, which enqueues
        # Audrey's triage of every open cluster
        from . import improvement_clusters as RCC
        steps.append(("root_cause_clusters", RCC.step))
        steps.append(("agent_work_queues", AWQ.step))
        # MEMORY USEFULNESS (owner R30 section 19): which lessons were in
        # each new decision's context (point in time), and, hourly, their
        # forward INVESTMENT-sleeve evidence -- a lesson with harmful
        # evidence is downweighted / superseded (append-only; a weight only
        # falls; no decision reads it to trade)
        from . import lesson_usage as LU
        steps.append(("lesson_usage", LU.step))
    except ImportError:
        pass
    try:
        # THE AGENTS' OWN MEMORY (migration 224): at most every 10 minutes,
        # each agent learns from durable outcomes (settlements, reviews,
        # challenge outcomes, execution outcomes, tournament verdicts) into
        # its PRIVATE, append-only, evidence-grounded memory. Writes only
        # agent_memory_events / agent_conversation_messages and its own
        # watermark; never an order, limit, threshold, model or policy.
        from . import agent_memory as AMEM
        steps.append(("agent_memory", AMEM.step))
    except ImportError:
        pass
    return steps


# ═════════════════════════════════════════════════════════════════════
# THE PASS'S RECORD IS KEPT WHEN A STEP IS CUT (RC6.3b; stated exactly below)
# ═════════════════════════════════════════════════════════════════════
#
# PRODUCTION, 2026-10-10 02:17Z onward: every pass was cut by HARD_TIMEOUT_S
# inside ONE step (the coverage step's first league statement ran 50+ s on
# the pooled connection). The cut took the WHOLE pass with it: the steps
# after it never ran, `S.record_pass` never ran (paper_session_health's
# heartbeat stood at 02:17:23Z for hours), and the heartbeat row read
# "ran=false, TimeoutError" with no step named. One step could erase the
# pass's own record, and nothing said which step it was or what did not run.
#
# WHAT IS GUARANTEED, AND WHAT IS NOT (RC6.3c pass-hardening rewords the
# RC6.3b claim "no single step can prevent the pass from recording itself",
# which was wider than what the code does).
#
# Every step is bounded by the pass time that is left -- HARD_TIMEOUT_S less a
# fixed reserve kept for the record (PASS_RECORD_RESERVE_S; HARD_TIMEOUT_S
# itself is not raised). A step that runs past that bound is CANCELLED
# (asyncpg cancels the server-side query), recorded in `errors` under the
# step's name with R_STEP_EXCEEDED, and the connection is put back to a clean
# state. Every step after the point where the time is spent is recorded as
# SKIPPED by name with R_STEP_SKIPPED -- in `errors` and in `skipped_steps` --
# instead of silently not running. The step order and every step's semantics
# are unchanged. The pass then records paper_session_health and the heartbeat
# (ran=true, the errors visible) and the advisory lock is released.
#
# THAT HOLDS FOR A STEP THAT ENDS WHEN IT IS CANCELLED. Three things are
# outside it, and each is bounded or named, not ignored:
#
#  * AN OWED ENTER. A step that can owe an ENTER (ENTER_OWING_STEPS) runs its
#    ENTER's order sequence shielded (paper_derek.owed_order), so a cut does
#    not return at once: the sequence runs on for up to
#    paper_derek.owed_enter_overrun_bound_s() (15 + 3 x 5 + 2 x 1 = 32 s with
#    the defaults, every bound spent in full) so that the ENTER ends in an
#    order or a named abandonment. Such a step is therefore cut that much
#    EARLIER (at the steps' bound less the overrun bound), and is not STARTED
#    with less than the overrun bound (plus STEP_MIN_START_S) left for steps:
#    it is recorded skipped by name with R_STEP_NOT_STARTED_ENTER_OVERRUN.
#    The guarantee is: the step has returned by the steps' bound, so the
#    record keeps its whole reserve -- as long as the overrun stays inside
#    owed_enter_overrun_bound_s(), which is Derek's own published bound. (A
#    sequence that outlives even that has the pass connection TERMINATED by
#    paper_derek -- the record cannot be written on it: outside the
#    guarantee, and the ENTER is left to the backstop to name.)
#  * A STEP THAT DOES NOT END ON CANCELLATION (it swallows the cancel, or
#    keeps work running on the pass connection in a shielded task, or blocks
#    the event loop). The per-step bound cannot end it; HARD_TIMEOUT_S, in
#    run_once, is the last resort and cuts the WHOLE pass: no
#    paper_session_health row, and the heartbeat says `ran=false` and names
#    the step in flight and the steps that had ended.
#  * THE RECORD ITSELF. paper_session_health's write is bounded by what is
#    left of HARD_TIMEOUT_S; a record that hangs is a named HEALTH error.
#
# THE PASS CONNECTION ITSELF (RC6.3d pass-cancel-safety; two pre-existing
# hazards an independent race review found by fault injection, asyncpg
# 0.31.0 cancel timing):
#
#  * A CUT STATEMENT'S CANCEL ACKNOWLEDGEMENT. asyncpg cancels a cut
#    statement by a CancelRequest on a second connection, and EVERY later
#    statement on the connection first awaits the server's acknowledgement
#    (the cancelled statement's ErrorResponse and ReadyForQuery). The
#    post-cut ROLLBACK ran under asyncio.timeout(CONNECTION_RESET_TIMEOUT_S):
#    an acknowledgement slower than the bound had the bound cancel THAT
#    await, which cancels asyncpg's acknowledgement future itself -- from
#    then until the server answered, every statement (the record, the
#    unlock, the pool's release) raised CancelledError, not an asyncpg
#    error, and run_once propagated it: no paper_session_health row, no
#    heartbeat, the scheduled task ended cancelled, silently. Now the
#    ROLLBACK runs as a task WAITED FOR under the bound, never cancelled by
#    it; a server that does not answer within the bound has the pass
#    connection TERMINATED deliberately, named R_CONNECTION_RESET_TIMED_OUT,
#    and the pass goes on (below). A CancelledError that is not the
#    caller's own (asyncio's task.cancelling()) is recorded by name, never
#    propagated as a silent unrecorded pass; the caller's own always is.
#  * A TERMINATED PASS CONNECTION'S BACKEND. paper_derek terminates the pass
#    connection when an owed ENTER's sequence outlives its bounds (and the
#    pass does, above). Connection.terminate() cancels the task SENDING the
#    CancelRequest; sent too late (its own connection to the server still
#    opening), the request was lost and the backend ran the cut statement
#    to its end -- holding the pass's session-level advisory lock
#    (client_connection_check_interval 0: a backend that writes nothing
#    never notices its client is gone), so every pass meanwhile refused
#    ADVISORY_LOCK_HELD (an hour for a hung read). Now paper_derek waits
#    (inside its ENTER_TERMINATED_WAIT_S) for the request to have been sent
#    before terminating, and run_once -- which knows the connection's
#    backend pid from its acquire -- ENDS the backend from a FRESH pool
#    connection (pg_terminate_backend; a backend that exits releases its
#    session-level advisory locks server-side) whenever the pass connection
#    ends closed, whoever closed it.
#  * WHEN THE PASS CONNECTION IS CLOSED mid-pass, every later step is
#    skipped by name (R_STEP_SKIPPED_CONNECTION_CLOSED) instead of being
#    started against a dead connection, and the record -- which cannot be
#    written on it -- is NOT dropped: run_once writes paper_session_health
#    and the heartbeat on the fresh connection, after the backend is ended,
#    named R_RECORDED_ON_A_FRESH_CONNECTION with what ended the connection
#    and what became of its backend (`pass_connection` on the record).
#
# AFTER EVERY STEP (not only a cut one) the pass looks at its connection: a
# transaction the step left open, or aborted, is rolled back and named
# (R_STEP_LEFT_TRANSACTION), so the record never fails with
# InFailedSQLTransactionError and a leaked transaction -- whose uncommitted
# writes, and those of every later step and the record, would have been lost
# at the final rollback -- is never silent. A step whose own result carries an
# error it handled itself (the coverage step: COVERAGE_RUN_EXCEEDED_ITS_BUDGET,
# COVERAGE_RUN_FAILED, an unreadable watermark, a window that could not be
# written) is also named in the pass `errors` (R_STEP_RETURNED_ERROR), so
# paper_session_health.errors counts it; the step's own result is unchanged.

#: pass time kept for the record (S.record_pass and the lock release): no
#: step may run into it
PASS_RECORD_RESERVE_S = 10.0
#: a step is not started with less than this of pass time left (it could do
#: nothing in it but be cut); it is recorded as skipped instead
STEP_MIN_START_S = 1.0
#: the bound on putting the connection back after a step was cut (the
#: ROLLBACK, which waits for the server's acknowledgement of the cancelled
#: statement); a connection not back within it is terminated, by name
CONNECTION_RESET_TIMEOUT_S = 5.0
#: pass time kept after the record for the advisory-lock release
LOCK_RELEASE_MARGIN_S = 1.0
#: the bound on what run_once does on a FRESH pool connection when the pass
#: connection ends closed: ending the pass connection's backend, writing
#: paper_session_health and the heartbeat (outside HARD_TIMEOUT_S, which
#: bounds the pass; the scheduler coalesces, it never queues a second pass)
CLOSED_CONNECTION_RECORD_TIMEOUT_S = 15.0
#: how long reap_backend waits for a signalled backend to be gone from
#: pg_stat_activity (its exit is what releases the advisory lock)
BACKEND_EXIT_WAIT_S = 3.0
R_STEP_EXCEEDED = "PAPER_STEP_EXCEEDED_PASS_TIME"
R_STEP_SKIPPED = "PAPER_STEP_SKIPPED_PASS_TIME_SPENT"
R_STEP_NOT_STARTED_ENTER_OVERRUN = (
    "PAPER_STEP_NOT_STARTED_ENTER_OVERRUN_WOULD_EXCEED_RESERVE")
R_STEP_LEFT_TRANSACTION = "PAPER_STEP_LEFT_A_TRANSACTION_OPEN"
R_STEP_RETURNED_ERROR = "PAPER_STEP_RETURNED_AN_ERROR"
#: (RC6.3d) the post-cut ROLLBACK -- the server's acknowledgement of the
#: cancelled statement -- did not return within CONNECTION_RESET_TIMEOUT_S:
#: the pass connection was terminated deliberately (errors.CONNECTION_RESET)
R_CONNECTION_RESET_TIMED_OUT = "PAPER_PASS_CONNECTION_RESET_TIMED_OUT"
#: (RC6.3d) a step not started because the pass connection was closed
#: before it (terminated after a cut, or by paper_derek)
R_STEP_SKIPPED_CONNECTION_CLOSED = "PAPER_STEP_SKIPPED_PASS_CONNECTION_CLOSED"
#: (RC6.3d) the pass connection was closed before the record: run_once
#: ended its backend and wrote paper_session_health and the heartbeat on a
#: fresh pool connection (errors.PASS_CONNECTION; `pass_connection` says
#: what ended the connection and what became of its backend)
R_RECORDED_ON_A_FRESH_CONNECTION = "PAPER_PASS_RECORDED_ON_A_FRESH_CONNECTION"

#: THE DEFAULT STEPS THAT CAN OWE AN ENTER: each decides valuations and, on an
#: ENTER, runs the ENTER's row and paper order as an owed sequence
#: (paper_derek.owed_order) that a cut does not stop at once. `derek` is
#: PD.step; `benchmark` and `benchmark_completed_game` are PB.step on their
#: policies; `maker_entry` and `exploration` are PB.step with the maker /
#: exploration decision functions. NOT in it, by what they do: `cash_fallback`
#: only records CASH rows for strategies that entered nothing (it opens no
#: ENTER), `simulate_after_delay` only reads books and simulates fills of
#: orders that exist, `enter_backstop` only names an ENTER that has no order,
#: `maker_maintain` only re-checks standing orders for cancellation, and
#: `xavier` places protective / sale orders on positions already held. A new
#: step that calls paper_derek.owed_order or bounded_decision belongs here
#: (tests/test_rc63c_pass_hardening.py fails until it is).
ENTER_OWING_STEPS = frozenset({
    "derek", "benchmark", "benchmark_completed_game", "maker_entry",
    "exploration"})

#: steps whose own result reports an error the step handled itself (it never
#: raises): named in the pass `errors` as well (see `_returned_error`)
RESULT_ERROR_STEPS = frozenset({"audrey_coverage"})


def pass_steps_deadline(t0: float) -> float:
    """The monotonic instant by which every step must have ended: the pass's
    start plus HARD_TIMEOUT_S less the record's reserve."""
    return float(t0) + float(HARD_TIMEOUT_S) - float(PASS_RECORD_RESERVE_S)


def enter_overrun_holdback_s() -> float:
    """The pass time an ENTER-owing step is cut short by: the most an owed
    ENTER can hold its caller past the caller's own cancellation
    (paper_derek.owed_enter_overrun_bound_s). 0.0 when paper_derek cannot be
    imported (then no ENTER-owing step exists either)."""
    try:
        from . import paper_derek as _PD
        return float(_PD.owed_enter_overrun_bound_s())
    except ImportError:
        return 0.0


def owes_enter(name: str) -> bool:
    """Whether a step of this name can owe an ENTER (ENTER_OWING_STEPS)."""
    return name in ENTER_OWING_STEPS


def _in_transaction(conn) -> bool:
    try:
        return bool(conn.is_in_transaction())
    except Exception:                                          # noqa: BLE001
        return False


def _closed(conn) -> bool:
    """Whether the pass connection is closed (terminated, or lost) -- and so
    cannot carry the record. True ALSO when is_closed() RAISES: a pooled
    connection that was terminated is detached from its proxy, and the
    proxy then raises InterfaceError ('connection has been released back to
    the pool') on every call -- it is unusable, which is what the callers
    need to know. (A plain object with no is_closed is treated as open:
    stand-in connections in tests that never terminate.)"""
    fn = getattr(conn, "is_closed", None)
    if fn is None:
        return False
    try:
        return bool(fn())
    except Exception:                                          # noqa: BLE001
        return True


def _cancelling(conn) -> bool:
    """Whether asyncpg has a cancel request in flight on `conn`: a statement
    was cancelled and the server has not yet acknowledged it (the pool's own
    predicate before its reset; every later statement waits on it)."""
    try:
        proto = conn._protocol
        return bool(proto is not None and proto._is_cancelling())
    except Exception:                                          # noqa: BLE001
        return False


def connection_identity(conn) -> dict:
    """The connection's backend pid and local (client) port, read without a
    statement -- what `reap_backend` needs to END the backend of a connection
    that is closed. None for each that cannot be read (a stand-in connection
    in a test; a unix-socket connection has no port)."""
    ident: dict[str, Any] = {"backend_pid": None, "client_port": None}
    try:
        ident["backend_pid"] = int(conn.get_server_pid())
    except Exception:                                          # noqa: BLE001
        pass
    try:
        tr = conn._transport
        name = tr.get_extra_info("sockname") if tr is not None else None
        if isinstance(name, tuple) and len(name) >= 2:
            ident["client_port"] = int(name[1])
    except Exception:                                          # noqa: BLE001
        pass
    return ident


def _terminate_quietly(conn) -> bool:
    """Close the pass connection at once (asyncpg Connection.terminate).
    Never raises; False when it was already closed or cannot be."""
    try:
        if _closed(conn):
            return False
        conn.terminate()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _retrieve(task) -> None:
    """A finished task's exception is read, so it is never logged unread."""
    try:
        if task.done() and not task.cancelled():
            task.exception()
    except Exception:                                          # noqa: BLE001
        pass


async def _end_open_transaction(conn, *, was_in_tx: bool,
                                after_cut: bool = False
                                ) -> tuple[bool, str | None]:
    """Roll back a transaction the step opened and did not close (open or
    aborted). A transaction the CALLER held before the pass is not the pass's
    to end. Returns (whether one was rolled back, the problem by name or
    None).

    THE STATEMENT RUNS AS A TASK WAITED FOR UNDER THE BOUND, NEVER CANCELLED
    BY IT (RC6.3d). After a cut, the connection's next statement first
    awaits asyncpg's acknowledgement future for the cancelled statement
    (the server's ErrorResponse and ReadyForQuery). asyncio.timeout around
    the ROLLBACK cancelled THAT await when the acknowledgement was slower
    than CONNECTION_RESET_TIMEOUT_S -- which cancels the acknowledgement
    future itself, so that until the server answered every statement on the
    connection (the record, the unlock, the pool's release) raised
    CancelledError and run_once propagated it: an unrecorded pass, a
    scheduled task ended cancelled, silently. Now a ROLLBACK the server does
    not answer within the bound (a cancel it does not acknowledge in time,
    a server that is wedged) has the pass connection TERMINATED deliberately
    and is named R_CONNECTION_RESET_TIMED_OUT; the pass goes on, its later
    steps skipped by name, its record written by run_once on a fresh
    connection once this connection's backend is ended. After a cut outside
    a transaction (an autocommit statement) the acknowledgement is waited
    out the same way with a SELECT 1, so the next step's first statement --
    under its own bound -- is not the one left waiting on it. The caller's
    own cancellation is re-raised (the statement's task cancelled first)."""
    if _closed(conn):
        return False, None
    in_tx = _in_transaction(conn)
    if in_tx:
        if was_in_tx:
            return False, None          # the caller's transaction: not ours
        stmt = "ROLLBACK"
    elif after_cut and _cancelling(conn):
        stmt = "SELECT 1"
    else:
        return False, None
    task = asyncio.ensure_future(conn.execute(stmt))
    try:
        done, _ = await asyncio.wait({task}, timeout=CONNECTION_RESET_TIMEOUT_S)
    except asyncio.CancelledError:
        task.cancel()
        task.add_done_callback(_retrieve)
        raise
    if task in done:
        if task.cancelled():
            return False, ("CancelledError: the %s after the %s was cancelled"
                           % (stmt, "cut" if after_cut else "step"))
        exc = task.exception()
        if exc is None:
            return in_tx, None
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    # NOT ANSWERED WITHIN THE BOUND: the connection is unusable for the time
    # the pass has left; it is terminated deliberately (never cancelled into
    # a state asyncpg cannot recover from) and named. run_once ends its
    # backend and writes the record on a fresh connection.
    pid = connection_identity(conn)["backend_pid"]
    terminated = _terminate_quietly(conn)
    task.cancel()
    task.add_done_callback(_retrieve)
    return False, (
        "%s: the %s after the %s (the server's acknowledgement of the "
        "cancelled statement) did not return within %.1fs; the pass "
        "connection was %s (backend pid %s) -- the pass is recorded on a "
        "fresh connection" % (
            R_CONNECTION_RESET_TIMED_OUT, stmt,
            "cut" if after_cut else "step", float(CONNECTION_RESET_TIMEOUT_S),
            "terminated" if terminated else "already closed", pid))


async def _reset_after_cut(conn, *, was_in_tx: bool) -> str | None:
    """Put the pass connection back after a step was cancelled: a transaction
    the step opened and did not close is rolled back (asyncpg has already
    cancelled the statement; the ROLLBACK, or a SELECT 1 outside a
    transaction, waits out the server's acknowledgement -- bounded, see
    _end_open_transaction). A transaction the CALLER held before the pass
    is not the pass's to end. None when clean, else the problem, by name."""
    return (await _end_open_transaction(conn, was_in_tx=was_in_tx,
                                        after_cut=True))[1]


def _returned_error(name: str, result) -> str | None:
    """The error a step's OWN RESULT reports (RESULT_ERROR_STEPS), or None.
    The coverage step never raises: a run cut at its budget or failed comes
    back as {"error": R_RUN_TIMED_OUT | R_RUN_FAILED | "<Exc>: ..", "why":
    ..}, a window that could not be written as {"errors": {window: ..}}. A
    step that is merely not due (`why` NOT_DUE, backed_off) reports none."""
    if name not in RESULT_ERROR_STEPS or not isinstance(result, dict):
        return None
    parts = []
    err = result.get("error")
    if err:
        why = result.get("why")
        parts.append(str(why) if why and str(why).startswith(str(err))
                     else "%s (%s)" % (err, why) if why else str(err))
    errs = result.get("errors")
    if isinstance(errs, dict) and errs:
        parts.append("window errors: " + "; ".join(
            "%s=%s" % (k, v) for k, v in list(errs.items())[:5]))
    if not parts:
        return None
    return ("%s: %s" % (R_STEP_RETURNED_ERROR, " | ".join(parts)))[:400]


# ═════════════════════════════════════════════════════════════════════
# THE PASS
# ═════════════════════════════════════════════════════════════════════

async def paper_pass(conn, *, now: float | None = None,
                     account_id: str = L.ACCOUNT_ID, market_data=None,
                     steps: list | None = None, config: dict | None = None,
                     force: bool = False, fee_fn=None,
                     trigger: str = "SCHEDULED_SERVICING",
                     cycle: dict | None = None, sleep=None,
                     progress: dict | None = None) -> dict:
    """ONE BOUNDED PAPER PASS. Never raises (CancelledError excepted).

    `force` skips the enablement check (tests only; the scheduled hook never
    passes it). `steps` replaces the default steps (a list of callables or
    (name, callable) pairs taking (conn, ctx)). `progress`, when given, is
    updated as the pass runs (the step in flight, the steps ended) so a
    caller that has to cut the whole pass can still say where it was."""
    live_clock = now is None
    at = float(now if now is not None else time.time())
    t0 = time.monotonic()
    out: dict[str, Any] = {"version": VERSION, "at": at, "trigger": trigger,
                           "ran": False, "errors": {}, "steps": {},
                           "mutation_attempts": 0}
    if not force:
        try:
            en = await S.enablement(conn)
        except Exception as exc:                               # noqa: BLE001
            return dict(out, refusal=R_DISABLED,
                        why="enablement unreadable: %s" % type(exc).__name__)
        if not en.get("enabled"):
            return dict(out, refusal=R_DISABLED, why=en.get("refusal"),
                        enablement=en)
    lock = _proc_lock()
    if lock.locked():
        return dict(out, refusal=R_BUSY, why="PROCESS_LOCK_HELD")
    async with lock:
        try:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                      ADVISORY_LOCK_KEY)
        except Exception as exc:                               # noqa: BLE001
            return dict(out, refusal=R_BUSY,
                        why="advisory lock unreadable: %s"
                        % type(exc).__name__)
        if not got:
            return dict(out, refusal=R_BUSY, why="ADVISORY_LOCK_HELD")
        was_in_tx = _in_transaction(conn)
        try:
            return await _run(conn, out, at=at, t0=t0,
                              account_id=account_id, market_data=market_data,
                              steps=steps, config=config, fee_fn=fee_fn,
                              cycle=cycle, live_clock=live_clock,
                              sleep=sleep, progress=progress,
                              was_in_tx=was_in_tx)
        finally:
            # THE LOCK IS RELEASED ON EVERY EXIT: a transaction the pass left
            # open (a cut step) would make the unlock fail ("current
            # transaction is aborted") and keep the lock for as long as this
            # pooled connection lives, so it is ended first. On a CLOSED
            # connection neither can run: the lock goes with the backend,
            # which run_once ends (reap_backend) if it has not exited.
            try:
                if not _closed(conn) and _in_transaction(conn) \
                        and not was_in_tx:
                    await conn.execute("ROLLBACK")
            except Exception:                                  # noqa: BLE001
                pass
            try:
                if not _closed(conn):
                    await conn.execute("SELECT pg_advisory_unlock($1)",
                                       ADVISORY_LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                pass


def _skipped(out: dict, name: str, *, left: float) -> None:
    """A step that did not run because the pass time was spent: named, in
    `skipped_steps` and in `errors` (a pass that skipped work is not clean)."""
    out["skipped_steps"][name] = R_STEP_SKIPPED
    out["errors"][name] = "%s: %.1fs of pass time left for steps" % (
        R_STEP_SKIPPED, max(0.0, left))


def _skipped_connection_closed(out: dict, name: str) -> None:
    """A step not started because the pass connection is CLOSED (terminated
    after a cut the server did not acknowledge in time, or by paper_derek):
    named, in `skipped_steps` and in `errors`, instead of being started
    against a dead connection (a step's venue reads would run, and nothing
    it did could be recorded)."""
    out["skipped_steps"][name] = R_STEP_SKIPPED_CONNECTION_CLOSED
    out["errors"][name] = ("%s: the pass connection was closed before the "
                           "step started" % R_STEP_SKIPPED_CONNECTION_CLOSED)


def _not_started_enter_overrun(out: dict, name: str, *, left: float,
                               holdback: float) -> None:
    """A step that can owe an ENTER was not started because the pass time left
    for steps does not cover the most an owed ENTER can run past its cut
    (`holdback`) plus STEP_MIN_START_S: started, a late ENTER would run into
    the time kept for the record. Named, in `skipped_steps` and in `errors`."""
    out["skipped_steps"][name] = R_STEP_NOT_STARTED_ENTER_OVERRUN
    out["errors"][name] = (
        "%s: %.1fs of pass time left for steps, %.1fs of it held back for an "
        "owed ENTER (paper_derek.owed_enter_overrun_bound_s) and at least "
        "%.1fs needed to start" % (R_STEP_NOT_STARTED_ENTER_OVERRUN,
                                   max(0.0, left), holdback,
                                   STEP_MIN_START_S))


async def _check_connection_after_step(conn, out: dict, name: str, *,
                                       was_in_tx: bool) -> None:
    """AFTER ANY STEP, CUT OR NOT: a transaction it left open or aborted is
    rolled back and NAMED -- under the step's own name in `errors`, next to
    the step's own error if it has one. Without it the next step ran inside
    the leaked transaction and the record (or an aborted transaction's first
    statement) failed with InFailedSQLTransactionError, or its writes were
    lost silently at the final rollback. A transaction the caller held
    before the pass is not the pass's to end."""
    ended, problem = await _end_open_transaction(conn, was_in_tx=was_in_tx)
    if not ended and not problem:
        return
    note = ("%s: the step ended with a transaction open or aborted; it was "
            "%s" % (R_STEP_LEFT_TRANSACTION,
                    "rolled back (its uncommitted writes were not kept)"
                    if ended else "NOT rolled back"))
    prev = out["errors"].get(name)
    out["errors"][name] = note if not prev else "%s | %s" % (prev, note)
    if problem:
        out["errors"]["CONNECTION_RESET"] = problem


async def _run(conn, out, *, at, t0, account_id, market_data, steps, config,
               fee_fn, cycle, live_clock=False, sleep=None, progress=None,
               was_in_tx=False) -> dict:
    try:
        sess = await S.ensure_session(conn, now=at, config=config,
                                      account_id=account_id)
    except Exception as exc:                                   # noqa: BLE001
        out["errors"]["SESSION"] = "%s: %s" % (type(exc).__name__,
                                               str(exc)[:200])
        return out
    if not sess.get("ok"):
        out["refusal"] = sess.get("refusal")
        return out
    cfg = sess.get("effective_config") or sess["config"]
    md = market_data if market_data is not None else \
        G.PaperMarketDataClient()
    attempts_before = int(getattr(md, "mutation_attempts", 0) or 0)
    budget = float((cfg.get("cadence") or {}).get("pass_budget_s") or 20.0)
    ctx: dict[str, Any] = {
        "session": sess, "session_id": sess["session_id"],
        "account_id": account_id, "config": cfg, "market_data": md,
        "now": at, "deadline": t0 + budget, "fee_fn": fee_fn,
        "books_read": 0, "first_fills": [], "fills": 0, "cycle": cycle or {},
        "results": out, "sleep": sleep,
        # THE DECISION CLOCK: the real clock in production (a decision made
        # 15 s into a pass is stamped 15 s later); the pass instant in tests.
        "clock": (time.time if live_clock else (lambda: at)),
        # FRESHNESS EXPIRY REQUEUES XAVIER (owner P0): on the live clock a
        # fresh review schedules its own re-review at source stamp + limit
        "schedule_review_at": (schedule_expiry_review if live_clock
                               else None)}
    out.update(ran=True, session_id=sess["session_id"],
               resumed=sess.get("resumed"))
    # EACH STEP'S WALL TIME, on the pass record (SW-2): the pass holds the
    # paper lock for its whole run, and which step holds it was not
    # recorded anywhere -- 27.7 s / 36.4 s passes with nothing held could
    # not be attributed. Record only.
    out["step_elapsed_s"] = {}
    # EVERY STEP IS BOUNDED BY `steps_end`, the pass's start plus
    # HARD_TIMEOUT_S less the record's reserve (an ENTER-owing step by that
    # less the owed-ENTER overrun bound, below)
    steps_end = pass_steps_deadline(t0)
    holdback_s = enter_overrun_holdback_s()
    out["pass_time"] = {"hard_timeout_s": float(HARD_TIMEOUT_S),
                        "record_reserve_s": float(PASS_RECORD_RESERVE_S),
                        "steps_bound_s": round(steps_end - t0, 3),
                        "enter_owing_steps_bound_s": round(
                            steps_end - t0 - holdback_s, 3)}
    out["skipped_steps"] = {}
    out["exceeded_step"] = None
    prog = progress if progress is not None else {}
    prog.update(step=None, steps_ended=[])
    for item in (steps if steps is not None else default_steps()):
        name, fn = (item if isinstance(item, tuple)
                    else (getattr(item, "__name__", "step"), item))
        if _closed(conn):
            _skipped_connection_closed(out, name)
            continue
        left = steps_end - time.monotonic()
        if left < STEP_MIN_START_S:
            _skipped(out, name, left=left)
            continue
        # A STEP THAT CAN OWE AN ENTER IS CUT EARLIER, BY THE MOST AN OWED
        # ENTER CAN RUN PAST ITS CUT (paper_derek.owed_enter_overrun_bound_s):
        # its order sequence is shielded, so the cut does not return at once,
        # and an overrun past `steps_end` would run into the record's reserve
        # and take the whole pass with it (HARD_TIMEOUT_S). With too little
        # time for that it is not started, and named.
        holdback = holdback_s if owes_enter(name) else 0.0
        room = left - holdback
        if holdback and room < STEP_MIN_START_S:
            _not_started_enter_overrun(out, name, left=left,
                                       holdback=holdback)
            continue
        # the step may read how long it has (a step that has its own budget
        # ends before it is cut, and so can still write its own watermark)
        ctx["step_deadline"] = time.monotonic() + room
        prog["step"] = name
        step_in_tx = _in_transaction(conn)
        t_step = time.monotonic()
        bound = asyncio.timeout(room)
        cut = False
        try:
            async with bound:
                out["steps"][name] = await fn(conn, ctx)
        except asyncio.CancelledError:
            raise
        except G.PaperVenueMutationRefused as exc:
            out["errors"][name] = "%s: %s" % (G.R_MUTATION_REFUSED,
                                              str(exc)[:200])
        except Exception as exc:                               # noqa: BLE001
            if bound.expired():
                # the bound cancelled the step (whatever it raised on being
                # cancelled): named, with the time it had and the time it took
                cut = True
                out["steps"].pop(name, None)
                out["errors"][name] = (
                    "%s: cancelled after %.1fs of the %.1fs pass time left "
                    "for steps (HARD_TIMEOUT_S %.0fs less %.0fs kept for the "
                    "record%s)" % (
                        R_STEP_EXCEEDED, time.monotonic() - t_step, room,
                        float(HARD_TIMEOUT_S), float(PASS_RECORD_RESERVE_S),
                        (" and %.0fs held back for an owed ENTER" % holdback)
                        if holdback else ""))
            else:
                out["errors"][name] = "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:200])
        else:
            if bound.expired():
                # the step swallowed its cancellation and returned late
                out["errors"][name] = (
                    "%s: returned after %.1fs, past the %.1fs pass time "
                    "left for steps" % (R_STEP_EXCEEDED,
                                        time.monotonic() - t_step, room))
                cut = True
            else:
                # a step that handled its own error and says so in its result
                returned = _returned_error(name, out["steps"].get(name))
                if returned:
                    out["errors"][name] = returned
        if cut:
            out["exceeded_step"] = out["exceeded_step"] or name
        out["step_elapsed_s"][name] = round(time.monotonic() - t_step, 3)
        prog["steps_ended"].append(name)
        prog["step"] = None
        if cut:
            # asyncpg has cancelled the statement; end what the step opened
            problem = await _reset_after_cut(conn, was_in_tx=step_in_tx)
            if problem:
                out["errors"]["CONNECTION_RESET"] = problem
        else:
            # ... and after a step that was not cut, the same look at the
            # connection: a transaction it left open or aborted is ended
            # and named
            await _check_connection_after_step(conn, out, name,
                                               was_in_tx=step_in_tx)
        # A HELD MARKET THAT MOVED WHILE THIS PASS RUNS is reviewed now,
        # between steps, not after the pass (SW-2; see HELD_IN_PASS_MAX_S) --
        # and only in the pass time that is left
        await _bounded_checkpoint(conn, ctx, out, steps_end)
    out["budget_exhausted"] = not _budget_left(ctx)
    out["books_read"] = ctx["books_read"]
    out["fills"] = ctx["fills"]
    attempts = int(getattr(md, "mutation_attempts", 0) or 0) - attempts_before
    out["mutation_attempts"] = attempts
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    for k in ("decisions_recorded", "orders_submitted", "reviews"):
        out[k] = sum(int((v or {}).get(k) or 0)
                     for v in out["steps"].values() if isinstance(v, dict))
    record = {"session_id": sess["session_id"], "now": at,
              "mutation_attempts": attempts,
              "last_mutation_attempt": getattr(md, "last_mutation_attempt",
                                               None)}
    if _closed(conn):
        # THE PASS CONNECTION IS CLOSED (terminated by the pass after a cut
        # the server did not acknowledge within CONNECTION_RESET_TIMEOUT_S,
        # by paper_derek for an owed sequence that outlived its bounds, or
        # lost): the record cannot be written on it. It is NOT dropped:
        # run_once writes it, and the heartbeat, on a fresh pool connection
        # once this connection's backend is ended (R_RECORDED_ON_A_FRESH_
        # CONNECTION, with what became of the backend).
        out["record_pending"] = record
        out["errors"]["PASS_CONNECTION"] = (
            "%s: the pass connection was closed before the record"
            % R_RECORDED_ON_A_FRESH_CONNECTION)
        return out
    # THE RECORD: paper_session_health, bounded by what is left of the hard
    # timeout (less the lock release) -- a record that hangs is a named
    # HEALTH error on a pass that still returns, and so still writes its
    # heartbeat, not a cut that erases the pass
    try:
        record_room = max(1.0, t0 + float(HARD_TIMEOUT_S)
                          - time.monotonic() - LOCK_RELEASE_MARGIN_S)
        async with asyncio.timeout(record_room):
            await S.record_pass(
                conn, record["session_id"], result=_digest(out), now=at,
                mutation_attempts=attempts,
                last_mutation_attempt=record["last_mutation_attempt"],
                error=_record_error(out))
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        out["errors"]["HEALTH"] = type(exc).__name__
    return out


def _record_error(out: dict) -> str | None:
    """paper_session_health.last_error: every pass error, by name."""
    return ("; ".join("%s=%s" % kv for kv in (out.get("errors") or {}).items())
            [:500] or None)


def _digest(out: dict) -> dict:
    return {k: out.get(k) for k in (
        "version", "at", "trigger", "ran", "session_id", "resumed",
        "errors", "budget_exhausted", "books_read", "fills",
        "decisions_recorded", "orders_submitted", "reviews",
        "mutation_attempts", "elapsed_s", "step_elapsed_s",
        "held_in_pass", "skipped_steps", "exceeded_step",
        "pass_time", "pass_connection")} | {
        "steps": {k: (v if not isinstance(v, dict) else
                      {kk: vv for kk, vv in v.items()
                       if not isinstance(vv, (list, dict))})
                  for k, v in (out.get("steps") or {}).items()}}


def describe() -> dict:
    return {"version": VERSION, "steps": [n for n, _ in default_steps()],
            "advisory_lock_key": ADVISORY_LOCK_KEY,
            "hard_timeout_s": HARD_TIMEOUT_S,
            "record_reserve_s": PASS_RECORD_RESERVE_S,
            "steps_bound_s": HARD_TIMEOUT_S - PASS_RECORD_RESERVE_S,
            "enter_owing_steps": sorted(ENTER_OWING_STEPS),
            "enter_overrun_holdback_s": enter_overrun_holdback_s(),
            "enter_owing_steps_bound_s": (
                HARD_TIMEOUT_S - PASS_RECORD_RESERVE_S
                - enter_overrun_holdback_s())}


# ═════════════════════════════════════════════════════════════════════
# THE IN-API SCHEDULE: A BACKGROUND TASK ON ITS OWN POOL CONNECTION
# ═════════════════════════════════════════════════════════════════════
#
# `schedule(get_pool, trigger=...)` is what the servicing task (every 60 s)
# and the collection cycle call, through `agents.runtime.paper_pass_hook`.
# It returns at once: the pass runs as ONE background task in this process
# (a second call while one runs is coalesced, never queued twice), on its own
# pool connection (bounded acquire), under a hard timeout. So neither the
# funded servicing pass nor the collection cycle ever waits on the paper
# engine, and nothing the paper engine does can raise into their loop.
#
# EVERY ATTEMPT WRITES A HEARTBEAT readable from the database:
# ingestion_state['paper_session_last_pass'] (including a disabled or busy
# attempt, with its reason); a pass that ran also updates
# paper_session_health.

HEARTBEAT_KEY = "paper_session_last_pass"
ACQUIRE_TIMEOUT_S = 10.0
HARD_TIMEOUT_S = 90.0
_TASK: dict = {"task": None, "last": None, "scheduled": 0, "coalesced": 0}


#: the heartbeat's size bound. Bounded on the CONTENT (R30A runtime): the
#: serialised string used to be cut at this length, and cut JSON fails the
#: jsonb cast, so an oversized digest lost the whole heartbeat silently.
HEARTBEAT_MAX_CHARS = 60000


def _heartbeat_body(res: dict) -> str:
    import json as _json
    body = _json.dumps(dict(_digest(res), refusal=res.get("refusal"),
                            why=res.get("why"), in_step=res.get("in_step"),
                            steps_ended=res.get("steps_ended"),
                            written_at=time.time()),
                       default=str)
    if len(body) <= HEARTBEAT_MAX_CHARS:
        return body
    return _json.dumps({"heartbeat_truncated": True,
                        "original_chars": len(body),
                        "refusal": res.get("refusal"), "why": res.get("why"),
                        "written_at": time.time()}, default=str)


async def _heartbeat_write(conn, res: dict) -> None:
    """The heartbeat's write; raises what the connection raises."""
    await conn.execute(
        "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
        "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
        HEARTBEAT_KEY, _heartbeat_body(res))


async def write_heartbeat(conn, res: dict) -> None:
    try:
        await _heartbeat_write(conn, res)
    except Exception:                                          # noqa: BLE001
        pass


def _cancelled_by_the_caller() -> bool:
    """Whether the CancelledError in flight is the current task's own
    cancellation (the caller -- the scheduler shutting down, an enclosing
    timeout -- asked for it: asyncio's task.cancelling() counts the
    requests) rather than one raised by an await on a future that was
    cancelled underneath the task (asyncpg's cancel-acknowledgement future
    after a bound cut the statement waiting on it). Without task.cancelling
    (Python < 3.11) every CancelledError is the caller's, as before."""
    task = asyncio.current_task()
    cancelling = getattr(task, "cancelling", None)
    if cancelling is None:
        return True
    try:
        return int(cancelling()) > 0
    except Exception:                                          # noqa: BLE001
        return True


#: the pass connection's backend, ended from a fresh connection: ours by
#: pid AND database AND (when known) client port, never another's
REAP_BACKEND_SQL = """
    SELECT pid, state, pg_terminate_backend(pid) AS signalled
      FROM pg_stat_activity
     WHERE pid = $1 AND datname = current_database()
       AND pid <> pg_backend_pid()
       AND ($2::int IS NULL OR client_port = $2)
"""
BACKEND_PRESENT_SQL = ("SELECT count(*) FROM pg_stat_activity WHERE pid = $1 "
                       "AND datname = current_database()")


async def reap_backend(conn, ident: dict, *,
                       exit_wait_s: float | None = None) -> dict:
    """END THE BACKEND OF A CLOSED CONNECTION, from `conn` (a fresh one), by
    the identity read at acquire (`connection_identity`). A backend whose
    client is gone keeps running its statement to the end -- holding the
    session's advisory locks, since a backend that writes nothing never
    notices (client_connection_check_interval 0) -- unless the CancelRequest
    reached it; terminated, it exits and its session-level advisory locks
    are released server-side (what frees the pass lock). Waits, bounded by
    `exit_wait_s` (BACKEND_EXIT_WAIT_S), for the backend to be gone. Returns
    the outcome by name: BACKEND_PID_UNKNOWN (no pid to end), BACKEND_
    ALREADY_GONE (it had exited: the cancel reached it, or it noticed),
    BACKEND_TERMINATED (signalled; `gone` says whether it exited within
    the wait), BACKEND_TERMINATE_REFUSED (the server would not signal it).
    Raises what the statement raises (the caller bounds and names it)."""
    pid = ident.get("backend_pid")
    if not pid:
        return {"outcome": "BACKEND_PID_UNKNOWN"}
    port = ident.get("client_port")
    rows = await conn.fetch(REAP_BACKEND_SQL, int(pid),
                            int(port) if port is not None else None)
    if not rows:
        return {"outcome": "BACKEND_ALREADY_GONE", "pid": int(pid)}
    r = rows[0]
    out = {"outcome": ("BACKEND_TERMINATED" if r["signalled"]
                       else "BACKEND_TERMINATE_REFUSED"),
           "pid": int(pid), "state": r["state"], "gone": False}
    if not r["signalled"]:
        return out
    wait = float(BACKEND_EXIT_WAIT_S if exit_wait_s is None else exit_wait_s)
    end = time.monotonic() + wait
    t0 = time.monotonic()
    while True:
        if not await conn.fetchval(BACKEND_PRESENT_SQL, int(pid)):
            out["gone"] = True
            out["exit_s"] = round(time.monotonic() - t0, 3)
            return out
        if time.monotonic() >= end:
            return out
        await asyncio.sleep(0.05)


def _who_closed(res: dict, pid, *, hint: str | None) -> str:
    """What ended the pass connection, by what the pass recorded."""
    if hint:
        return hint
    reset = (res.get("errors") or {}).get("CONNECTION_RESET") or ""
    if R_CONNECTION_RESET_TIMED_OUT in str(reset):
        return ("terminated by the pass: %s" % R_CONNECTION_RESET_TIMED_OUT)
    try:
        from . import paper_derek as _PD
        t = _PD.last_termination(pid)
    except Exception:                                          # noqa: BLE001
        t = None
    if t:
        sent = t.get("cancel_sent")
        return ("terminated by paper_derek for an owed ENTER's sequence that "
                "outlived its bounds (its cancel request %s)" % (
                    "sent" if sent else
                    "NOT sent: lost at the terminate" if sent is False else
                    "not in flight"))
    return "closed by the server or the pool, not by the pass"


async def _record_on_a_fresh_connection(pool, res: dict, ident: dict, *,
                                        hint: str | None = None) -> None:
    """THE PASS CONNECTION ENDED CLOSED: end its backend and write the pass
    record and the heartbeat on a FRESH pool connection (RC6.3d), bounded
    as a whole by CLOSED_CONNECTION_RECORD_TIMEOUT_S; never raises (the
    caller's own cancellation excepted). `res["record_pending"]` (set by
    the pass when it could not write paper_session_health) is written; a
    pass that did not run (ran=False) gets its heartbeat only, as always.
    What happened is on the record: errors.PASS_CONNECTION names
    R_RECORDED_ON_A_FRESH_CONNECTION with what ended the connection and
    what became of its backend; `pass_connection` carries the detail."""
    pid = ident.get("backend_pid")
    note: dict[str, Any] = {
        "closed": True, "backend_pid": pid,
        "client_port": ident.get("client_port"),
        "closed_by": _who_closed(res, pid, hint=hint),
        "reap": None, "recorded": None, "heartbeat": False}
    res["pass_connection"] = note
    pending = res.pop("record_pending", None)
    errors = res.setdefault("errors", {})
    try:
        async with asyncio.timeout(CLOSED_CONNECTION_RECORD_TIMEOUT_S):
            async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as fresh:
                try:
                    note["reap"] = await reap_backend(fresh, ident)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:                       # noqa: BLE001
                    note["reap"] = {
                        "outcome": "BACKEND_TERMINATE_FAILED",
                        "error": "%s: %s" % (type(exc).__name__,
                                             str(exc)[:160])}
                reap = note["reap"] or {}
                errors["PASS_CONNECTION"] = (
                    "%s: the pass connection (backend pid %s) was closed "
                    "before the record: %s; its backend: %s%s; "
                    "paper_session_health and the heartbeat written on a "
                    "fresh connection" % (
                        R_RECORDED_ON_A_FRESH_CONNECTION, pid,
                        note["closed_by"], reap.get("outcome"),
                        (" (exited in %.3fs)" % reap["exit_s"]
                         if reap.get("gone") and "exit_s" in reap else
                         " (NOT gone within %.1fs)" % BACKEND_EXIT_WAIT_S
                         if reap.get("outcome") == "BACKEND_TERMINATED"
                         else "")))
                if pending:
                    await S.record_pass(
                        fresh, pending["session_id"], result=_digest(res),
                        now=pending["now"],
                        mutation_attempts=pending["mutation_attempts"],
                        last_mutation_attempt=pending["last_mutation_attempt"],
                        error=_record_error(res))
                    note["recorded"] = True
                await _heartbeat_write(fresh, res)
                note["heartbeat"] = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        note["failed"] = "%s: %s" % (type(exc).__name__, str(exc)[:160])
        if pending and not note.get("recorded"):
            errors["HEALTH"] = type(exc).__name__


async def run_once(get_pool, *, trigger: str, now: float | None = None,
                   **kw) -> dict:
    """ONE PASS ON ITS OWN CONNECTION, bounded; never raises (the caller's
    own cancellation excepted: it always propagates).

    HARD_TIMEOUT_S is the last resort. Since RC6.3b every step is bounded
    inside it (paper_pass), so a pass that reaches it again was held by
    something outside the steps; its heartbeat now says which step was in
    flight and which had ended, instead of an empty "TimeoutError: ".

    THE PASS CONNECTION MAY END CLOSED (RC6.3d): terminated by the pass
    after a cut the server did not acknowledge in time, or by paper_derek
    for an owed sequence that outlived its bounds. Then its backend is
    ENDED and the record and heartbeat are written on a FRESH pool
    connection (_record_on_a_fresh_connection) -- an orphaned backend
    would otherwise hold the pass's advisory lock for the rest of its
    statement. A CancelledError the caller did not ask for (a statement
    waited on asyncpg's cancel acknowledgement and that wait was cancelled)
    is recorded by name on a fresh connection, never propagated as a
    silent unrecorded pass."""
    progress = kw.pop("progress", None)
    if progress is None:
        progress = {}
    try:
        pool = await get_pool()
        async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as conn:
            ident = connection_identity(conn)
            hint = None
            try:
                res = await asyncio.wait_for(
                    paper_pass(conn, now=now, trigger=trigger,
                               progress=progress, **kw),
                    HARD_TIMEOUT_S)
            except asyncio.CancelledError:
                if _cancelled_by_the_caller():
                    raise
                # NOT THE CALLER'S: raised by an await on a future cancelled
                # underneath the pass (asyncpg's acknowledgement future for
                # a cut statement), so the connection raises it on every
                # statement until the server answers. Named, never silent;
                # the connection is terminated (nothing can run on it) and
                # the heartbeat is written on a fresh one.
                res = {"ran": False, "trigger": trigger,
                       "refusal": "PAPER_PASS_RAISED_OR_TIMED_OUT",
                       "why": "%s%s" % (
                           "CancelledError not requested by the caller: a "
                           "statement of the pass awaited asyncpg's "
                           "acknowledgement of a cancelled statement and "
                           "that wait was cancelled; the pass connection is "
                           "terminated and this heartbeat written on a fresh "
                           "connection",
                           (" in step %s" % progress["step"])
                           if progress.get("step") else ""),
                       "in_step": progress.get("step"),
                       "steps_ended": list(progress.get("steps_ended")
                                           or [])}
                _terminate_quietly(conn)
                hint = ("terminated by run_once: a CancelledError the caller "
                        "did not request")
            except Exception as exc:                           # noqa: BLE001
                said = str(exc)[:200] or (
                    "the pass was cut by HARD_TIMEOUT_S (%.0f s)"
                    % HARD_TIMEOUT_S if isinstance(exc, TimeoutError)
                    else "")
                res = {"ran": False, "trigger": trigger,
                       "refusal": "PAPER_PASS_RAISED_OR_TIMED_OUT",
                       "why": "%s: %s%s" % (
                           type(exc).__name__, said,
                           (" in step %s" % progress["step"])
                           if progress.get("step") else ""),
                       "in_step": progress.get("step"),
                       "steps_ended": list(progress.get("steps_ended")
                                           or [])}
            if _closed(conn):
                await _record_on_a_fresh_connection(pool, res, ident,
                                                    hint=hint)
            else:
                await write_heartbeat(conn, res)
            _TASK["last"] = res
            return res
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        res = {"ran": False, "trigger": trigger,
               "refusal": "PAPER_PASS_HAD_NO_CONNECTION",
               "why": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        _TASK["last"] = res
        return res


def schedule(get_pool, *, trigger: str, **kw) -> dict:
    """START ONE BACKGROUND PASS unless one is running. Returns at once."""
    if not S.env_on():
        # NOT A PASS: the process is not configured for paper trading. No
        # task, no connection, no write.
        return {"scheduled": False, "why": S.R_ENV_OFF}
    # THE HELD-MARK REFRESH (agents.paper_mark_refresh): every held market
    # re-read inside the mark SLA, on its own connection and its own
    # explicit, bounded read budget -- never the pass's lock or read cap.
    # Scheduled on every servicing / cycle tick (coalesced, at most one run
    # per MIN_RUN_INTERVAL_S), whether or not a pass is already running.
    try:
        from . import paper_mark_refresh as _PMR
        _PMR.schedule(get_pool, trigger=trigger)
    except Exception:                                           # noqa: BLE001
        log.warning("held-mark refresh not scheduled", exc_info=True)
    t = _TASK.get("task")
    if t is not None and not t.done():
        _TASK["coalesced"] += 1
        return {"scheduled": False, "why": R_BUSY,
                "coalesced": _TASK["coalesced"]}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    _TASK["task"] = loop.create_task(run_once(get_pool, trigger=trigger,
                                              **kw))
    _TASK["scheduled"] += 1
    return {"scheduled": True, "trigger": trigger,
            "scheduled_total": _TASK["scheduled"]}


# ═════════════════════════════════════════════════════════════════════
# THE PER-VALUATION HOOK: DEREK DECIDES AT THE VALUATION INSTANT
# ═════════════════════════════════════════════════════════════════════
#
# Called by the collection cycle right after each entry-experiment valuation
# is persisted (`workers/ext_pinnacle_loop._paper_valuation`), on the cycle's
# own connection: Derek's paper decision is formed from the same inputs at
# the same instant the lane formed its own, so the 30 s Pinnacle rule is
# applied where it belongs rather than after the cycle's whole duration.
# The decision (with the Pinnacle age at that instant) is persisted first;
# an ENTER submits its paper order at once with the 2 s delay, and a
# background pass is scheduled to observe the book after that delay and
# simulate the fill. The paper pass keeps deciding any valuation this hook
# missed, labelled PAPER_PASS_BACKSTOP.
#
# CHEAP WHEN OFF: with PAPER_SESSION unset it returns before any I/O.
# BOUNDED: one valuation, at most one book read, VALUATION_HOOK_TIMEOUT_S.

VALUATION_HOOK_TIMEOUT_S = 8.0
DEFAULT_ACCOUNT_ID = L.ACCOUNT_ID
_CLIENT: dict = {"client": None}


def _client() -> G.PaperMarketDataClient:
    if _CLIENT["client"] is None:
        _CLIENT["client"] = G.PaperMarketDataClient()
    return _CLIENT["client"]


async def _record_hook_failure(conn, *, ctx: dict, valuation_id: int,
                               strategy: str, res: dict | None) -> None:
    """A decision the hook did not make, as a row (migration 187): raised,
    timed out, or deferred. A strategy that is switched off is not a
    failure. Never raises; an absent table is skipped."""
    r = res or {}
    if r.get("timeout"):
        outcome = "TIMEOUT"
    elif r.get("error"):
        outcome = "ERROR"
    elif r.get("deferred"):
        outcome = "DEFERRED"
    else:
        return
    try:
        import json as _json
        await conn.execute(
            "INSERT INTO paper_hook_failures (session_id, account_id, "
            " valuation_id, strategy, stage, outcome, elapsed_s, error, "
            " detail) VALUES ($1,$2,$3,$4,'IN_CYCLE_VALUATION_HOOK',$5,$6,$7,"
            " $8::jsonb)", ctx.get("session_id"), ctx.get("account_id"),
            int(valuation_id), str(strategy), outcome, r.get("elapsed_s"),
            (str(r.get("error"))[:500] if r.get("error") else r.get("why")),
            _json.dumps({k: r.get(k) for k in ("why", "decision_id",
                                                "refusal", "deferred")},
                        default=str))
    except Exception:                                           # noqa: BLE001
        log.warning("paper hook failure not recorded (valuation %s, %s)",
                    valuation_id, strategy, exc_info=True)


# ── THE BOOK-READ RETRY: BOUNDED CONCURRENCY, AN EXPLICIT BUDGET ─────────
#
# A strategy whose book read was cut by the venue cooldown (and whose
# Pinnacle reading would still be inside its 30 s rule after the cooldown)
# is decided ONCE MORE, after the cooldown, on its own pool connection, off
# the collection cycle. At most RETRY_CONCURRENCY run at once and at most
# RETRY_BUDGET_PER_HOUR are scheduled per rolling hour; a retry that cannot
# be scheduled decides at once instead (and records its refusal). The retry
# itself has no retry.
RETRY_CONCURRENCY = 2
RETRY_BUDGET_PER_HOUR = 60
_RETRY: dict = {"sem": None, "loop": None, "recent": [], "tasks": set(),
                "scheduled": 0, "refused_budget": 0}


def _retry_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _RETRY["sem"] is None or _RETRY["loop"] is not loop:
        _RETRY["sem"] = asyncio.Semaphore(RETRY_CONCURRENCY)
        _RETRY["loop"] = loop
    return _RETRY["sem"]


def retry_capacity(now: float | None = None) -> bool:
    """Whether a retry could be scheduled now (hourly budget left and a
    running loop). Checked BEFORE a decision defers, so a valuation with no
    retry available is decided at once on its first read."""
    at = time.time() if now is None else float(now)
    recent = [t for t in _RETRY["recent"] if at - t < 3600.0]
    if len(recent) >= RETRY_BUDGET_PER_HOUR:
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def schedule_book_retry(*, valuation_id: int, strategies: list,
                        after_s: float, get_pool=None,
                        now: float | None = None) -> dict:
    """Start ONE background retry of `strategies` on this valuation after
    `after_s`, within the concurrency and hourly budget. Returns at once."""
    at = time.time() if now is None else float(now)
    _RETRY["recent"] = [t for t in _RETRY["recent"] if at - t < 3600.0]
    if len(_RETRY["recent"]) >= RETRY_BUDGET_PER_HOUR:
        _RETRY["refused_budget"] += 1
        return {"scheduled": False, "why": "RETRY_BUDGET_EXHAUSTED",
                "budget_per_hour": RETRY_BUDGET_PER_HOUR}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    if get_pool is None:
        from ..db import get_pool
    _RETRY["recent"].append(at)
    _RETRY["scheduled"] += 1

    async def run():
        async with _retry_sem():
            await asyncio.sleep(max(0.0, float(after_s)))
            try:
                pool = await get_pool()
                async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as c:
                    await decide_valuation(
                        c, valuation_id=valuation_id,
                        strategies=list(strategies), via="BOOK_RETRY",
                        attempt_no=2)
            except asyncio.CancelledError:
                raise
            except Exception:                                  # noqa: BLE001
                log.warning("paper book retry failed (valuation %s)",
                            valuation_id, exc_info=True)

    t = loop.create_task(run())
    _RETRY["tasks"].add(t)
    t.add_done_callback(_RETRY["tasks"].discard)
    return {"scheduled": True, "after_s": round(float(after_s), 3),
            "in_flight": len(_RETRY["tasks"]),
            "concurrency": RETRY_CONCURRENCY,
            "budget_used_last_hour": len(_RETRY["recent"]),
            "budget_per_hour": RETRY_BUDGET_PER_HOUR}


# ── THE ENTRY-FILL READ: ONE BOOK READ AT THE ENTRY'S ELIGIBLE INSTANT ───
#
# R30A INCIDENT REPAIR (the ENTER -> FILL collapse). An in-cycle ENTER used
# to depend on the paper pass for the book its fill is simulated on: the
# hook schedules a pass, but (1) a pass already running coalesces the
# request away, (2) the pass reads the entry in its books step BEFORE it is
# eligible (wasted, and it primes the 6 s shared cache with a pre-eligible
# receipt), and (3) its after-delay step reads with whatever is left of the
# 20 s pass budget -- usually nothing, so the read was refused by our own
# gate and recorded as an errored observation inside the order's window.
# Production, 7 d to 2026-10-04 20:54Z (research-sql run 37233864395): 100
# of 168 paper entry orders expired with no readable book; E6 -- once
# expired, their market was next read p50 229 s (completed-game) / 863 s
# (exploration) later, far outside the 90 s TTL.
#
# So each in-cycle ENTER gets ONE dedicated, bounded read: on its own pool
# connection, off the cycle, it waits for the order's eligible instant
# (decision + the session's unchanged 2 s delay), reads the book with
# `not_before` = that instant and a deadline of its own (never past the
# order's TTL, at most ENTRY_FILL_MAX_WAIT_S, so a venue cooldown is waited
# out rather than refused against an exhausted pass budget), records it like
# every other read, and simulates the order on it under the simulator's
# unchanged rules (first READABLE book at or after eligible, the limit, the
# depth walk, the consumption ledger, the fees). At most ENTRY_FILL_
# CONCURRENCY run at once and ENTRY_FILL_BUDGET_PER_HOUR per rolling hour --
# one read per ENTER, a few dozen a day, against the thousands of position
# reads the pass makes. It decides nothing, changes no threshold, and a read
# it cannot make leaves the order pending for the pass, as before.
ENTRY_FILL_CONCURRENCY = 2
ENTRY_FILL_BUDGET_PER_HOUR = 240
ENTRY_FILL_MAX_WAIT_S = 30.0
ENTRY_FILL_TTL_MARGIN_S = 2.0
ENTRY_FILL_READ_BASIS = "ENTRY_AT_ELIGIBLE"
_ENTRY_FILL: dict = {"sem": None, "loop": None, "recent": [], "tasks": set(),
                     "scheduled": 0, "refused_budget": 0, "last": None}


def _entry_fill_sem() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if _ENTRY_FILL["sem"] is None or _ENTRY_FILL["loop"] is not loop:
        _ENTRY_FILL["sem"] = asyncio.Semaphore(ENTRY_FILL_CONCURRENCY)
        _ENTRY_FILL["loop"] = loop
    return _ENTRY_FILL["sem"]


async def entry_fill(conn, order_ids, *, market_data=None, clock=None,
                     sleep=None, fee_fn=None) -> dict:
    """THE ENTRY-FILL READ for `order_ids` (pending marketable entries):
    wait for each market's eligible instant, read its book once (received at
    or after that instant), record it, simulate the orders on it. Bounded;
    never raises."""
    from .. import bettor_paper_simulator as SIM
    from . import paper_derek as PD
    clock = clock or time.time
    sleep = sleep or asyncio.sleep
    out: dict[str, Any] = {"orders": len(list(order_ids or [])), "read": 0,
                           "errors": 0, "results": []}
    try:
        rows = await conn.fetch(
            "SELECT order_id, us_market_slug, state, order_type, "
            "       extract(epoch FROM eligible_at)::float8 AS eligible_epoch, "
            "       extract(epoch FROM expires_at)::float8 AS expires_epoch "
            "  FROM paper_orders WHERE order_id = ANY($1::text[]) "
            "   AND order_type = 'MARKETABLE' "
            "   AND state = 'PENDING_SIMULATION'",
            [str(o) for o in (order_ids or [])])
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, error=type(exc).__name__)
    by_slug: dict = {}
    for r in rows:
        by_slug.setdefault(r["us_market_slug"], []).append(dict(r))
    md = market_data if market_data is not None else _client()
    for slug, orders in sorted(by_slug.items()):
        elig = max(float(o["eligible_epoch"]) for o in orders)
        expires = min(float(o["expires_epoch"]) for o in orders)
        wait = elig - float(clock())
        if wait > 0:
            await sleep(wait)
        now = max(float(clock()), elig)
        budget = min(ENTRY_FILL_MAX_WAIT_S,
                     expires - ENTRY_FILL_TTL_MARGIN_S - now)
        if budget <= PD.BOOK_READ_RESERVE_S:
            out["results"].append({"us_market_slug": slug,
                                   "skipped": "NO_TIME_LEFT_IN_THE_TTL"})
            continue
        rctx = {"market_data": md,
                "deadline": time.monotonic() + budget}
        # A PENDING ENTRY'S FILL READ is a management read: it goes ahead of
        # discovery in the one paper market-data owner (held reads first).
        from .. import paper_market_data as _PMD
        with _PMD.lane(_PMD.LANE_MANAGE):
            got = await PD.read_book_within_deadline(rctx, slug,
                                                     not_before_epoch=elig)
        rec = await SIM.record_book(conn, slug=slug, read=got,
                                    source="PAPER_MARKET_DATA_CLIENT",
                                    read_basis=ENTRY_FILL_READ_BASIS)
        out["read"] += 1
        out["errors"] += 1 if rec.get("error") else 0
        sim_now = max(float(clock()), float(rec["observed_at"]))
        for o in orders:
            r = await SIM.simulate_order(conn, o["order_id"], now=sim_now,
                                         fee_fn=fee_fn)
            out["results"].append({k: r.get(k) for k in (
                "order_id", "state", "filled_qty", "refusal", "pending")}
                | {"book_obs_id": rec.get("obs_id"),
                   "book_error": rec.get("error")})
    return out


def schedule_entry_fill(order_ids, *, get_pool=None, now=None,
                        after=None) -> dict:
    """START ONE BACKGROUND ENTRY-FILL READ for `order_ids`, within the
    concurrency and hourly budget. Returns at once; never raises. `after`
    (optional) is called once the read finishes (the runtime passes the
    paper-pass trigger, so the handoff and Xavier follow a first fill)."""
    ids = [str(o) for o in (order_ids or []) if o]
    if not ids:
        return {"scheduled": False, "why": "NO_ORDERS"}
    at = time.time() if now is None else float(now)
    _ENTRY_FILL["recent"] = [t for t in _ENTRY_FILL["recent"]
                             if at - t < 3600.0]
    if len(_ENTRY_FILL["recent"]) >= ENTRY_FILL_BUDGET_PER_HOUR:
        _ENTRY_FILL["refused_budget"] += 1
        return {"scheduled": False, "why": "ENTRY_FILL_BUDGET_EXHAUSTED",
                "budget_per_hour": ENTRY_FILL_BUDGET_PER_HOUR}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    if get_pool is None:
        from ..db import get_pool
    _ENTRY_FILL["recent"].append(at)
    _ENTRY_FILL["scheduled"] += 1

    async def run():
        async with _entry_fill_sem():
            try:
                pool = await get_pool()
                async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as c:
                    _ENTRY_FILL["last"] = await asyncio.wait_for(
                        entry_fill(c, ids),
                        ENTRY_FILL_MAX_WAIT_S + 15.0)
            except asyncio.CancelledError:
                raise
            except Exception:                                  # noqa: BLE001
                log.warning("paper entry-fill read failed (%s)", ids,
                            exc_info=True)
            if after is not None:
                try:
                    after()
                except Exception:                              # noqa: BLE001
                    pass

    t = loop.create_task(run())
    _ENTRY_FILL["tasks"].add(t)
    t.add_done_callback(_ENTRY_FILL["tasks"].discard)
    return {"scheduled": True, "orders": ids,
            "in_flight": len(_ENTRY_FILL["tasks"]),
            "budget_used_last_hour": len(_ENTRY_FILL["recent"]),
            "budget_per_hour": ENTRY_FILL_BUDGET_PER_HOUR}


def entry_fill_status() -> dict:
    return {"scheduled": _ENTRY_FILL["scheduled"],
            "refused_budget": _ENTRY_FILL["refused_budget"],
            "in_flight": len(_ENTRY_FILL["tasks"]),
            "budget_per_hour": ENTRY_FILL_BUDGET_PER_HOUR,
            "last": _ENTRY_FILL["last"]}


# ── A VALUATION A NEWER PRICE HAS REPLACED IS NOT DECIDED (P1) ───────────
#
# `pinnapi_primary.R_SUPERSEDED` (read its note): the feed now holds a
# strictly newer, fresh price of the SAME market of the same record of the
# same fixture, and that newer price is itself going to be valued -- queued
# on (or in flight at) the reactive scheduler, or a newer valuation of the
# same contract already written. Then no strategy decides THIS valuation:
# each would refuse PINNAPI_PRIMARY_INPUT_CHANGED (correctly) and record a
# SOFTWARE first loss for a contract whose newer valuation decides it. It is
# recorded instead as a hook DEFERRED row (`paper_hook_failures`, why =
# R_SUPERSEDED, detail.superseded = true, with the evidence), which the
# paper-pass backstop reads so it does not re-decide it either. Nothing is
# valued on any price: no threshold, clock or the 30 s rule moves; a phase,
# fixture, epoch or staleness failure keeps its own refusal.
R_SUPERSEDED = "PINNAPI_PRIMARY_VALUATION_SUPERSEDED_BY_A_NEWER_QUOTE"
SUPERSEDED_COUNTS: dict = {"skipped": 0, "by_queued_change": 0,
                           "by_newer_valuation": 0}

NEWER_VALUATION_SQL = """
    SELECT id FROM external_valuations
     WHERE experiment_id = $1 AND us_market_slug = $2
       AND buy_intent IS NOT DISTINCT FROM $3 AND id > $4
     ORDER BY id DESC LIMIT 1
"""


def _row_reference(row: dict) -> dict:
    import json as _json
    scmp = row.get("settlement_comparison")
    if isinstance(scmp, str):
        try:
            scmp = _json.loads(scmp)
        except ValueError:
            scmp = None
    return dict((scmp or {}).get("reference_input") or {}) \
        if isinstance(scmp, dict) else {}


async def superseded_by(conn, ctx: dict, row: dict) -> dict | None:
    """None, or why this valuation is SUPERSEDED (R_SUPERSEDED) at the
    decision instant `ctx['clock']()`. Never raises: any doubt is None, and
    the strategies then decide (and refuse) exactly as before."""
    try:
        from .. import pinnapi_primary as primary
        ref = _row_reference(row)
        if ref.get("provider") != primary.PROVIDER or \
                ref.get("version") != primary.VERSION:
            return None
        from .. import pinnapi_feed_runtime as feed
        from .. import pinnapi_reactive as reactive
        owner = feed._STATE.get("owner")
        cache = owner.cache if owner else None
        if cache is None:
            return None
        at = float(ctx["clock"]()) if ctx.get("clock") else float(ctx["now"])
        max_age = float(((ctx.get("config") or {}).get("entry") or {})
                        .get("pinnacle_max_age_s", 30.0))
        sup = primary.supersession(
            cache, {"reference_input": ref}, at=at, max_age_s=max_age,
            runtime_id=feed._STATE.get("runtime_id"))
        if sup is None:
            return None
        sched = reactive.ACTIVE
        if sched is not None and sched.cache is cache and \
                sched.will_value(sup["fixture_id"], sup["version"]):
            return dict(sup, by="QUEUED_CHANGE", at=at)
        newer = None
        if row.get("us_market_slug") and row.get("id") is not None:
            newer = await conn.fetchval(
                NEWER_VALUATION_SQL, row.get("experiment_id"),
                row.get("us_market_slug"), row.get("buy_intent"),
                int(row["id"]))
        if newer is not None:
            return dict(sup, by="NEWER_VALUATION", at=at,
                        newer_valuation_id=int(newer))
        return None
    except asyncio.CancelledError:
        raise
    except Exception:                                           # noqa: BLE001
        return None


def _superseded_result(sup: dict) -> dict:
    SUPERSEDED_COUNTS["skipped"] += 1
    SUPERSEDED_COUNTS["by_queued_change" if sup.get("by") == "QUEUED_CHANGE"
                      else "by_newer_valuation"] += 1
    return {"deferred": True, "superseded": True, "why": R_SUPERSEDED,
            "superseded_by": {k: v for k, v in sup.items()
                              if k != "version"}}


async def _record_superseded(conn, *, ctx: dict, valuation_id: int,
                             strategy: str, res: dict) -> None:
    """The DEFERRED hook row naming the supersession. Never raises."""
    try:
        import json as _json
        await conn.execute(
            "INSERT INTO paper_hook_failures (session_id, account_id, "
            " valuation_id, strategy, stage, outcome, elapsed_s, error, "
            " detail) VALUES ($1,$2,$3,$4,'IN_CYCLE_VALUATION_HOOK',"
            " 'DEFERRED',NULL,$5,$6::jsonb)", ctx.get("session_id"),
            ctx.get("account_id"), int(valuation_id), str(strategy),
            R_SUPERSEDED, _json.dumps(
                {"why": R_SUPERSEDED, "superseded": True,
                 "deferred": True, "decided_via": ctx.get("decided_via"),
                 "superseded_by": res.get("superseded_by")}, default=str))
    except Exception:                                           # noqa: BLE001
        log.warning("paper supersession not recorded (valuation %s, %s)",
                    valuation_id, strategy, exc_info=True)


async def _decide_paper_strategies(conn, ctx: dict, row: dict, *, vid: int,
                                   strategies, via: str, attempt_no: int,
                                   schedule_retry) -> dict:
    """The investment policy first, then the maker-entry and exploration
    strategies, on ONE shared book read; each attempt recorded. A strategy
    whose read was cut earns the retry for itself and every strategy after
    it in this valuation (they would meet the same cooldown)."""
    from . import paper_benchmark as PB
    from . import paper_maker as PMK
    from . import paper_explore as PEX
    T = VALUATION_HOOK_TIMEOUT_S                               # noqa: N806
    plan = [
        (PB.CG_STRATEGY, lambda c: PB.decide_for_hook(
            conn, c, dict(row), timeout_s=T, pol=PB.CG_POLICY)),
        (PB.MAKER_STRATEGY, lambda c: PMK.decide_for_hook(
            conn, c, dict(row), timeout_s=T)),
        (PB.EXPLORE_STRATEGY, lambda c: PEX.decide_for_hook(
            conn, c, dict(row), timeout_s=T))]
    results: dict = {}
    retry_for: list = []
    retry_after = None
    sup = None
    for strat, fn in plan:
        if strategies is not None and strat not in strategies:
            continue
        if retry_for:
            res = {"deferred": True, "why": "BOOK_RETRY",
                   "retry": {"with": retry_for[0]}}
            retry_for.append(strat)
        elif sup is not None or (sup := await superseded_by(
                conn, ctx, row)) is not None:
            # A NEWER PRICE REPLACED THIS VALUATION'S (R_SUPERSEDED): this
            # strategy, and every one after it, leaves it to the newer
            # valuation -- recorded, never decided on the old price
            res = _superseded_result(sup)
            results[strat] = res
            await _record_superseded(conn, ctx=ctx, valuation_id=vid,
                                     strategy=strat, res=res)
            continue
        else:
            res = await fn(ctx)
            if res.get("deferred") and res.get("why") == "BOOK_RETRY":
                retry_for.append(strat)
                retry_after = res.get("retry_after_s")
        results[strat] = res
        await PB.record_attempt(conn, ctx, valuation_id=vid, strategy=strat,
                                via=via, res=res, attempt_no=attempt_no)
        if not (res.get("deferred") and res.get("why") == "BOOK_RETRY"):
            await _record_hook_failure(conn, ctx=ctx, valuation_id=vid,
                                       strategy=strat, res=res)
    if retry_for:
        sched = schedule_retry(valuation_id=vid, strategies=retry_for,
                               after_s=float(retry_after or 1.0))
        await PB.record_attempt(
            conn, ctx, valuation_id=vid, strategy=retry_for[0], via=via,
            attempt_no=attempt_no,
            res={"retry_outcome": ("RETRY_SCHEDULED" if sched.get(
                "scheduled") else "RETRY_NOT_SCHEDULED"),
                "why": sched.get("why"), "retry": dict(
                    sched, strategies=retry_for)})
        if not sched.get("scheduled"):
            # NO RETRY AVAILABLE: decide now, without one (the refusal is
            # recorded), rather than leave the valuation undecided.
            ctx2 = dict(ctx, book_retry_ok=False)
            for strat, fn in plan:
                if strat in retry_for:
                    res = await fn(ctx2)
                    results[strat] = res
                    await PB.record_attempt(
                        conn, ctx2, valuation_id=vid, strategy=strat,
                        via=via, res=res, attempt_no=attempt_no)
                    await _record_hook_failure(conn, ctx=ctx2,
                                               valuation_id=vid,
                                               strategy=strat, res=res)
        results["retry"] = sched
    return results


def _start_entry_fill(order_ids, *, schedule_fill, schedule_entry):
    """The entry-fill read for these orders (R30A). Production (no injected
    `schedule_fill`): `schedule_entry_fill`, then a paper pass so the handoff
    and Xavier follow a first fill. A test that injects `schedule_fill`
    without `schedule_entry` gets nothing new. Never raises."""
    fn = schedule_entry
    if fn is None and schedule_fill is None:
        from . import runtime as _RT

        def fn(ids):
            return schedule_entry_fill(ids, after=lambda: _RT.paper_pass_hook(
                trigger="ENTRY_FILL_READ"))
    if fn is None:
        return None
    try:
        return fn(list(dict.fromkeys(order_ids)))
    except Exception as exc:                                   # noqa: BLE001
        return {"scheduled": False, "error": type(exc).__name__}


async def decide_valuation(conn, *, valuation_id, now: float | None = None,
                           market_data=None, account_id: str | None = None,
                           fee_fn=None, schedule_fill=None,
                           strategies=None,
                           via: str = "IN_CYCLE_AT_THE_VALUATION_INSTANT",
                           attempt_no: int = 1,
                           schedule_retry=None,
                           book_retry: bool = True,
                           schedule_entry=None) -> dict:
    """ONE PAPER DECISION FOR ONE JUST-WRITTEN VALUATION. Never raises.

    `strategies` (the book retry) limits the run to those benchmark-family
    strategies; the retry decides only what its first attempt deferred.
    `book_retry=False` decides a cut read at once (no retry).
    `schedule_entry` (R30A) starts the entry-fill read for the orders this
    valuation produced; by default `schedule_entry_fill` in production (when
    `schedule_fill` is not injected), and nothing when a test injects
    `schedule_fill` without it."""
    if not S.env_on():
        return {"decided": False, "why": S.R_ENV_OFF}
    acct = account_id or DEFAULT_ACCOUNT_ID
    live_clock = now is None
    at = float(now if now is not None else time.time())
    try:
        en = await S.enablement(conn)
        if not en.get("enabled"):
            return {"decided": False, "why": en.get("refusal")}
        sess = await S.ensure_session(conn, now=at, account_id=acct)
        if not sess.get("ok"):
            return {"decided": False, "why": sess.get("refusal")}
        row = await conn.fetchrow(
            "SELECT * FROM external_valuations WHERE id = $1",
            int(valuation_id))
        from .. import bettor_external_shadow as ext
        if row is None or row["experiment_id"] != ext.EXPERIMENT_ID:
            return {"decided": False, "why": "NOT_AN_ENTRY_EXPERIMENT_ROW"}
        md = market_data if market_data is not None else _client()
        before = int(getattr(md, "mutation_attempts", 0) or 0)
        cfg = sess.get("effective_config") or sess["config"]
        ctx: dict[str, Any] = {
            "session": sess, "session_id": sess["session_id"],
            "account_id": acct, "config": cfg, "market_data": md,
            "now": at, "deadline": time.monotonic()
            + VALUATION_HOOK_TIMEOUT_S, "fee_fn": fee_fn, "books_read": 0,
            "first_fills": [], "fills": 0, "results": {},
            "clock": (time.time if live_clock else (lambda: at)),
            "decided_via": via,
            "context_cache_key": sess["session_id"],
            # ONE BOOK READ for every strategy deciding this valuation
            "books_by_slug": {},
            # the first attempt may earn one bounded retry (when one can be
            # scheduled); a retry may not
            "book_retry_ok": (attempt_no == 1 and book_retry
                              and retry_capacity())}
        from . import paper_derek as PD
        bench_on = _benchmark_env_on()
        bench = None
        bench_cg = None
        family: dict = {}
        vid = int(valuation_id)
        if bench_on:
            # THE ACTIVE ENTRY EXPERIMENT FIRST. The completed-game policy is
            # the investment policy, so it decides closest to the valuation
            # instant; the maker-entry policy and the bounded exploration
            # strategy follow on the SAME book read.
            from . import paper_benchmark as PB
            family = await _decide_paper_strategies(
                conn, ctx, dict(row), vid=vid, strategies=strategies,
                via=via, attempt_no=attempt_no,
                schedule_retry=schedule_retry or schedule_book_retry)
            bench_cg = family.get(PB.CG_STRATEGY)
        if via == "BOOK_RETRY":
            # THE RETRY DECIDES ONLY WHAT ITS FIRST ATTEMPT DEFERRED: the
            # two-model and strict records were made on the first attempt.
            orders = [r.get("order_id") for r in family.values()
                      if isinstance(r, dict) and r.get("order_id")]
            if orders:
                sched = schedule_fill
                if sched is None:
                    from . import runtime as _RT
                    sched = (lambda: _RT.paper_pass_hook(
                        trigger="VALUATION_DECISION_FILL"))
                try:
                    sched()
                except Exception:                              # noqa: BLE001
                    pass
                _start_entry_fill(orders, schedule_fill=schedule_fill,
                                  schedule_entry=schedule_entry)
            return {"decided": True, "via": via, "attempt_no": attempt_no,
                    "strategies": {k: {kk: (v or {}).get(kk) for kk in (
                        "decision_id", "verdict", "refusal", "order_id",
                        "deferred", "why")} for k, v in family.items()
                        if k != "retry"}}
        t_derek = time.monotonic()
        sup = await superseded_by(conn, ctx, dict(row))
        try:
            if sup is not None:
                # R_SUPERSEDED: a newer price replaced this valuation's and
                # is itself being valued; Derek leaves it to that valuation
                rec = _superseded_result(sup)
                await _record_superseded(
                    conn, ctx=ctx, valuation_id=vid,
                    strategy="DEREK_ENTRY_POLICY_V2", res=rec)
            else:
                dctx = dict(ctx, deadline=time.monotonic()
                            + VALUATION_HOOK_TIMEOUT_S)
                # THE DEADLINE BOUNDS THE DECISION; a recorded ENTER's order
                # completes (PD.bounded_decision, P0 incident 2026-10-04).
                rec = await PD.bounded_decision(
                    lambda c: PD.decide_one(conn, c, dict(row)), dctx,
                    timeout_s=VALUATION_HOOK_TIMEOUT_S)
                await _record_hook_failure(
                    conn, ctx=ctx, valuation_id=vid,
                    strategy="DEREK_ENTRY_POLICY_V2",
                    res=dict(rec, elapsed_s=round(
                        time.monotonic() - t_derek, 3)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:                               # noqa: BLE001
            await _record_hook_failure(
                conn, ctx=ctx, valuation_id=vid,
                strategy="DEREK_ENTRY_POLICY_V2",
                res={"error": "%s: %s" % (type(exc).__name__, str(exc)[:200]),
                     "timeout": isinstance(exc, asyncio.TimeoutError),
                     # set when an ENTER was recorded and its order outran
                     # the grace (PD.EnterOrderGraceExceeded)
                     "decision_id": getattr(exc, "decision_id", None),
                     "elapsed_s": round(time.monotonic() - t_derek, 3)})
            if not bench_on:
                raise
            # WITH THE BENCHMARK ON, a failing two-model decision does not
            # stop the benchmark's separate decision on the same valuation.
            rec = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}
        if bench_on:
            # THE STRICT PINNACLE_ONLY_PAPER_BENCHMARK, its own record on the
            # same valuation (new entries off since 184: it returns at once).
            bench = await PB.decide_for_hook(
                conn, ctx, dict(row), timeout_s=VALUATION_HOOK_TIMEOUT_S)
            await _record_hook_failure(conn, ctx=ctx, valuation_id=vid,
                                       strategy=PB.STRATEGY, res=bench)

        delta = int(getattr(md, "mutation_attempts", 0) or 0) - before
        if delta:
            import json as _json
            await conn.execute(
                "UPDATE paper_session_health SET mutation_attempts = "
                " mutation_attempts + $2, last_mutation_attempt = $3::jsonb "
                " WHERE session_id = $1", sess["session_id"], delta,
                _json.dumps(getattr(md, "last_mutation_attempt", None),
                            default=str))
        if rec.get("order_id") or (bench or {}).get("order_id") or \
                any(isinstance(r, dict) and r.get("order_id")
                    for r in family.values()):
            sched = schedule_fill
            if sched is None:
                from . import runtime as _RT
                sched = (lambda: _RT.paper_pass_hook(
                    trigger="VALUATION_DECISION_FILL"))
            try:
                rec["fill_pass"] = sched()
            except Exception as exc:                           # noqa: BLE001
                rec["fill_pass"] = {"scheduled": False,
                                    "error": type(exc).__name__}
            ef = _start_entry_fill(
                [o for o in ([rec.get("order_id"),
                              (bench or {}).get("order_id")]
                             + [r.get("order_id") for r in family.values()
                                if isinstance(r, dict)]) if o],
                schedule_fill=schedule_fill, schedule_entry=schedule_entry)
            if ef is not None:
                rec["entry_fill"] = ef
        out = dict({k: rec.get(k) for k in (
            "decision_id", "verdict", "refusal", "order_id", "duplicate",
            "deferred", "fill_pass")}, decided=not rec.get("deferred"),
            mutation_attempts=delta)
        if rec.get("entry_fill") is not None:
            out["entry_fill"] = rec["entry_fill"]
        if rec.get("superseded"):
            out.update(superseded=True, why=rec.get("why"),
                       superseded_by=rec.get("superseded_by"))
        if bench is not None:
            out["benchmark"] = bench
            if bench_cg is not None:
                out["benchmark_completed_game"] = bench_cg
            for k, v in family.items():
                if k not in ("retry",) and k != getattr(
                        PB, "CG_STRATEGY", None):
                    out.setdefault("paper_family", {})[k] = v
            if family.get("retry"):
                out["book_retry"] = family["retry"]
            if rec.get("error"):
                out.update(decided=False, error=rec["error"])
        return out
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return {"decided": False, "error": "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:200])}


# ═════════════════════════════════════════════════════════════════════
# XAVIER'S HELD REVIEW ON A HELD MARKET'S PRICE CHANGE (pinnapi_held)
# ═════════════════════════════════════════════════════════════════════
#
# A held market's price changed on the PinnAPI feed: a fresh probability
# exists for the 30 s limit from now. Waiting for the next servicing pass
# (every 60 s) would usually miss it, so Xavier reviews the groups holding
# that contract at once -- Xavier's step only, on the paper pass's own
# locks (busy -> skipped: the running pass reviews it as a MARKET_EVENT),
# no book read, no entry decision. Debounced: one run at a time, at least
# HELD_REVIEW_MIN_GAP_S between runs, slugs coalesced meanwhile.

HELD_REVIEW_MIN_GAP_S = 5.0
HELD_REVIEW_BUDGET_S = 10.0
HELD_REVIEW_TIMEOUT_S = 20.0
#: A HELD REVIEW REFUSED BECAUSE A PAPER PASS HOLDS THE LOCK IS RETRIED, not
#: dropped. Before this the batch was cleared from `pending` before the run,
#: so a held market's change that arrived while a pass ran (the pass holds
#: the lock up to its budget / hard timeout, and its Xavier step built its
#: due list before the change) was never reviewed while its probability was
#: fresh: the next pass saw it >= 60 s later, stale by construction. A slug
#: is retried every HELD_REVIEW_MIN_GAP_S for at most this long after it was
#: first queued -- the odds source's 30 s rule (ext_pinnacle_loop.
#: PINNACLE_MAX_AGE_S, pinned by a test): after that no review of that
#: change could be fresh, so the slug is dropped (counted). The review
#: itself still judges freshness by the unchanged rule; this only stops the
#: chance from being thrown away.
HELD_REVIEW_RETRY_WINDOW_S = 30.0
#: A HELD CHANGE NOTIFIED WHILE A PAPER PASS RUNS IS REVIEWED BY THAT PASS
#: (RC6.2 p-xavier SW-2). The retry above can only succeed once the pass
#: releases the lock. PROBABLE cause of late held reviews -- to be confirmed
#: by the counters below once deployed (they were never persisted before):
#: a pass holds the lock for most of its run (production 2026-10-09
#: 14:37-14:39Z, research-sql 37945613145 / 37945859108: two SERVICING
#: passes of 27.7 s and 36.4 s with nothing held, budget exhausted; pass
#: lengths over the earlier 72 h are unknown because the heartbeat ring kept
#: the wrong end), and its Xavier step builds its due list part-way
#: through, so a change notified after that list was built can only be
#: reviewed after the pass and is dropped once its 30 s window passes (a
#: busy drop needs the pass to run on some 25 s after the change). The
#: population this can reach (research-sql 37961502655, 72 h to 2026-10-09
#: 16:46Z): of 364 PAPER MARKET_EVENT assessments made 30 s or more after
#: their due instant, 196 were made due by a PinnAPI held-market change and
#: 168 by a venue book move, which this does not touch; 7 of the 196 were
#: due in a desk-sweep minute (:14, :15, :44, :45).
#: The pass now services the pending held slugs between its own steps,
#: under the locks it already holds, through the same Xavier step a held
#: review runs (paper_xavier.step on the holding groups), so a change's
#: latency is bounded by the step it arrived in. It does NOT shorten a
#: single long step or a starved event loop (the 30-minute desk sweep and
#: the candidate-row bursts that coincide with PKE's five missed stamps are
#: outside its reach), and it does not touch a MARKET_EVENT made due by a
#: venue book move. Each notification's outcome is the review that actually
#: happened: a slug whose holding group was deferred (budget) or whose
#: review raised is retried inside its window and otherwise dropped under
#: its own named outcome -- never recorded as reviewed. A slug past its
#: window is dropped (counted), as requeue_busy drops it; the probability is
#: still judged by the unchanged 30 s rule on the provider's stamp.
#:
#: BOUNDED, AND NOT CHARGED TO THE PASS. A checkpoint starts only while less
#: than HELD_IN_PASS_MAX_S of in-pass held review has run in this pass, and
#: its Xavier budget AND reserved budget are both cut to what is left, so
#: the in-pass total is at most HELD_IN_PASS_MAX_S plus the one group review
#: in flight when it ran out (and each checkpoint's due-list read). The
#: pass's own deadline is moved later by every checkpoint's time: books,
#: simulation (resting protective and exit orders), the entry steps and
#: Xavier's own step keep exactly the budget they had without it. Worst
#: case added to a pass: HELD_IN_PASS_MAX_S + one group review, inside the
#: HARD_TIMEOUT_S that bounds the whole pass (pinned by a test).
HELD_IN_PASS_MAX_S = 20.0
#: the newest notify-to-review outcomes kept in memory for the heartbeat
HELD_LATENCY_KEPT = 50
#: a notification whose review was deferred or raised is retried at most
#: this many times (one per HELD_REVIEW_MIN_GAP_S across the 30 s window)
HELD_UNFINISHED_RETRIES = 6
_HELD: dict = {"task": None, "pending": set(), "runs": 0, "coalesced": 0,
               "last": None, "queued_at": {}, "busy_retries": 0,
               "busy_dropped": 0, "serviced_in_pass": 0,
               "in_pass_slugs": 0, "in_pass_dropped": 0,
               "unfinished_retries": 0, "unfinished_dropped": 0,
               "attempts": {}, "latency": []}

#: EACH CHANGE NOTIFICATION'S OUTCOME (the ring's `via`). Only the first two
#: are reviews; every other one names why no review of that change ran.
V_HELD_REVIEW = "HELD_REVIEW"
V_IN_PASS = "PAPER_PASS_CHECKPOINT"
#: refused busy (or found pending by a pass) after its 30 s window
V_DROPPED = "DROPPED_PAST_RETRY_WINDOW"
#: a holding group was deferred (review budget spent), retried, window over
V_DROPPED_DEFERRED = "DROPPED_DEFERRED_PAST_RETRY_WINDOW"
#: a holding group's review raised (or the review never ran), window over
V_DROPPED_ERROR = "DROPPED_REVIEW_ERROR_PAST_RETRY_WINDOW"
#: no holding group was due: its newest review already postdates the change
#: (another review covered it) or it no longer holds the contract
V_NOT_DUE = "NO_REVIEW_DUE"
#: the background review's account holds no group on the slug
V_NOT_HELD = "NOT_HELD_BY_THE_REVIEWING_ACCOUNT"
#: the held review refused for a reason other than busy (paper disabled)
V_REFUSED = "HELD_REVIEW_REFUSED"
REVIEWED_VIAS = (V_HELD_REVIEW, V_IN_PASS)
ALL_VIAS = (V_HELD_REVIEW, V_IN_PASS, V_DROPPED, V_DROPPED_DEFERRED,
            V_DROPPED_ERROR, V_NOT_DUE, V_NOT_HELD, V_REFUSED)

HELD_GROUPS_SQL = (
    "SELECT DISTINCT o.group_id, o.us_market_slug FROM paper_orders o "
    "  JOIN paper_handoffs h ON h.group_id = o.group_id "
    " WHERE o.account_id = $1 AND o.role = 'ENTRY' "
    "   AND o.us_market_slug = ANY($2::text[])")


def _note_outcome(slug, *, queued_at, at, via) -> None:
    """One change notification's outcome on the bounded in-memory ring:
    reviewed (by the held review or inside a pass) or why not, with the
    scheduler latency -- our notification clock to the review start, never
    a probability age."""
    ring = _HELD.setdefault("latency", [])
    try:
        ring.append({"slug": slug, "via": via,
                     "queued_at": round(float(queued_at), 3),
                     "at": round(float(at), 3),
                     "latency_s": round(float(at) - float(queued_at), 3)})
    except (TypeError, ValueError):
        return
    del ring[:-HELD_LATENCY_KEPT]


def _settle_reviewed(taken: dict, slug_groups: dict, got, *, at: float,
                     now: float, via: str) -> dict:
    """WHAT A HELD REVIEW ACTUALLY DID FOR EACH NOTIFIED SLUG (the in-pass
    and the background path both). `taken`: slug -> the instant its window
    runs from; `slug_groups`: slug -> the reviewing account's groups holding
    it; `got`: paper_xavier.step's result, None when the step raised or
    never ran (then every slug counts as errored). A slug any holding group
    of which was deferred or errored goes back to `pending` while inside its
    window, else is dropped under its own outcome; otherwise it is `via`
    (latency from `at`) when one of its groups was reviewed, else
    NO_REVIEW_DUE. A review that did not happen is never noted as one.
    Pure apart from _HELD."""
    g = got if isinstance(got, dict) else None
    reviewed = set((g or {}).get("reviewed_groups") or [])
    deferred = {d.get("group_id") for d in (g or {}).get("deferred") or []
                if isinstance(d, dict)}
    errored = {e.get("group_id") for e in (g or {}).get("review_errors")
               or [] if isinstance(e, dict)}
    pend = _HELD.setdefault("pending", set())
    qa = _HELD.setdefault("queued_at", {})
    tries = _HELD.setdefault("attempts", {})
    res: dict[str, list] = {"reviewed": [], "retried": [], "dropped": [],
                            "not_due": []}
    for s in sorted(taken):
        q = float(taken[s])
        gs = set(slug_groups.get(s) or ())
        err = g is None or bool(gs & errored)
        if err or gs & deferred:
            # retried inside the window, and at most HELD_UNFINISHED_RETRIES
            # times per notification (bounded even on a clock that stalls)
            if now - q <= HELD_REVIEW_RETRY_WINDOW_S and \
                    int(tries.get(s) or 0) < HELD_UNFINISHED_RETRIES:
                tries[s] = int(tries.get(s) or 0) + 1
                pend.add(s)
                qa.setdefault(s, q)
                res["retried"].append(s)
                continue
            res["dropped"].append(s)
            _note_outcome(s, queued_at=q, at=now,
                          via=V_DROPPED_ERROR if err else V_DROPPED_DEFERRED)
        elif gs & reviewed:
            res["reviewed"].append(s)
            _note_outcome(s, queued_at=q, at=at, via=via)
        else:
            res["not_due"].append(s)
            _note_outcome(s, queued_at=q, at=at, via=V_NOT_DUE)
        # a newer notification of the slug (pending again, a later window
        # start) keeps its own clock
        if s not in pend and qa.get(s) == taken[s]:
            qa.pop(s, None)
            tries.pop(s, None)
    _HELD["unfinished_retries"] = int(_HELD.get("unfinished_retries")
                                      or 0) + len(res["retried"])
    _HELD["unfinished_dropped"] = int(_HELD.get("unfinished_dropped")
                                      or 0) + len(res["dropped"])
    return res


def held_review_status() -> dict:
    """THE HELD-REVIEW SCHEDULER, for the feed heartbeat (persisted every
    beat): cumulative counters of this process and the newest outcomes.
    Only HELD_REVIEW and PAPER_PASS_CHECKPOINT outcomes are reviews; the
    latency percentiles are over those alone. Pure apart from reading
    _HELD."""
    ring = list(_HELD.get("latency") or [])
    reviewed = sorted(x["latency_s"] for x in ring
                      if x.get("via") in REVIEWED_VIAS)

    def pct(q):
        if not reviewed:
            return None
        return reviewed[min(len(reviewed) - 1, int(q * len(reviewed)))]
    return {
        "runs": int(_HELD.get("runs") or 0),
        "coalesced": int(_HELD.get("coalesced") or 0),
        "busy_retries": int(_HELD.get("busy_retries") or 0),
        "busy_dropped": int(_HELD.get("busy_dropped") or 0),
        # checkpoints that reviewed at least one notified slug, and those
        # slugs (a deferred, errored or not-due slug is in neither)
        "serviced_in_pass": int(_HELD.get("serviced_in_pass") or 0),
        "in_pass_slugs": int(_HELD.get("in_pass_slugs") or 0),
        "in_pass_dropped": int(_HELD.get("in_pass_dropped") or 0),
        # a holding group deferred or its review raised: retried inside the
        # window / dropped after it (both paths)
        "unfinished_retries": int(_HELD.get("unfinished_retries") or 0),
        "unfinished_dropped": int(_HELD.get("unfinished_dropped") or 0),
        "pending": len(_HELD.get("pending") or ()),
        "retry_window_s": HELD_REVIEW_RETRY_WINDOW_S,
        "recent_outcomes": {
            "kept": len(ring),
            "by_via": {v: sum(1 for x in ring if x.get("via") == v)
                       for v in ALL_VIAS},
            "reviewed_latency_p50_s": pct(0.5),
            "reviewed_latency_p90_s": pct(0.9),
            "reviewed_latency_max_s": reviewed[-1] if reviewed else None,
            "reviewed_after_window": sum(
                1 for x in reviewed if x > HELD_REVIEW_RETRY_WINDOW_S)},
        "recent": ring[-10:],
        "clock": ("latency_s is our receipt of the change notification to "
                  "the start of its review (the scheduler's delay); the "
                  "probability's age is judged by the review on the "
                  "provider's own stamp under the unchanged 30 s rule")}


async def held_review(conn, *, slugs, now: float | None = None,
                      account_id: str | None = None, fee_fn=None) -> dict:
    """XAVIER'S STEP FOR THE GROUPS HOLDING `slugs`, now. Never raises."""
    from . import paper_xavier as PX
    acct = account_id or DEFAULT_ACCOUNT_ID
    live_clock = now is None
    at = float(now if now is not None else time.time())
    out: dict[str, Any] = {"at": at, "slugs": sorted(slugs or []),
                           "ran": False}
    if not S.env_on():
        return dict(out, refusal=S.R_ENV_OFF)
    try:
        en = await S.enablement(conn)
    except Exception as exc:                                   # noqa: BLE001
        return dict(out, refusal=R_DISABLED, why=type(exc).__name__)
    if not en.get("enabled"):
        return dict(out, refusal=R_DISABLED, why=en.get("refusal"))
    lock = _proc_lock()
    if lock.locked():
        return dict(out, refusal=R_BUSY, why="PROCESS_LOCK_HELD")
    async with lock:
        if not await conn.fetchval("SELECT pg_try_advisory_lock($1)",
                                   ADVISORY_LOCK_KEY):
            return dict(out, refusal=R_BUSY, why="ADVISORY_LOCK_HELD")
        try:
            sess = await S.ensure_session(conn, now=at, account_id=acct)
            if not sess.get("ok"):
                return dict(out, refusal=sess.get("refusal"))
            # which of this account's groups hold each slug: the scheduler
            # settles every slug on what its own groups' reviews did
            slug_groups: dict[str, list] = {}
            for r in await conn.fetch(HELD_GROUPS_SQL, acct,
                                      sorted(slugs or [])):
                slug_groups.setdefault(r["us_market_slug"], []).append(
                    r["group_id"])
            slug_groups = {s: sorted(set(v)) for s, v in slug_groups.items()}
            groups = sorted({x for v in slug_groups.values() for x in v})
            ctx: dict[str, Any] = {
                "session": sess, "session_id": sess["session_id"],
                "account_id": acct,
                "config": sess.get("effective_config") or sess["config"],
                "now": at, "fee_fn": fee_fn,
                "deadline": time.monotonic() + HELD_REVIEW_BUDGET_S,
                "clock": (time.time if live_clock else (lambda: at)),
                "schedule_review_at": (schedule_expiry_review if live_clock
                                       else None)}
            got = await PX.step(conn, ctx, only_groups=groups)
            return dict(out, ran=True, groups=groups,
                        slug_groups=slug_groups, xavier=got)
        except Exception as exc:                               # noqa: BLE001
            return dict(out, error=type(exc).__name__,
                        detail=str(exc)[:200])
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                pass


def requeue_busy(batch, *, now: float) -> dict:
    """A batch refused R_BUSY: every slug still inside its retry window
    (first queued <= HELD_REVIEW_RETRY_WINDOW_S ago) goes back to `pending`;
    the rest are dropped and counted. Pure apart from _HELD."""
    qa = _HELD.setdefault("queued_at", {})
    kept, dropped = [], []
    for s in sorted(batch or []):
        first = qa.get(s, now)
        if now - first <= HELD_REVIEW_RETRY_WINDOW_S:
            kept.append(s)
            qa.setdefault(s, first)
        else:
            dropped.append(s)
            qa.pop(s, None)
            _HELD.setdefault("attempts", {}).pop(s, None)
            _note_outcome(s, queued_at=first, at=now, via=V_DROPPED)
    _HELD["pending"].update(kept)
    _HELD["busy_retries"] = _HELD.get("busy_retries", 0) + len(kept)
    _HELD["busy_dropped"] = _HELD.get("busy_dropped", 0) + len(dropped)
    return {"requeued": kept, "dropped": dropped}


def _ensure_scheduler() -> bool:
    """Slugs handed back to `pending` (another account's, or a deferred /
    errored review's retry) are picked up by the background scheduler even
    when no new change notification arrives: while a pass holds the lock it
    is refused busy and retries inside the window (requeue_busy), after the
    pass it reviews them. Started only when none runs and this process has
    started one before (its pool and clock are reused). Never raises."""
    if not _HELD.get("pending"):
        return False
    t = _HELD.get("task")
    if t is not None and not t.done():
        return False
    spawn = _HELD.get("respawn")
    if not spawn:
        return False
    try:
        got = schedule_held_review([], get_pool=spawn.get("get_pool"),
                                   clock=spawn.get("clock") or time.time)
    except Exception:                                          # noqa: BLE001
        return False
    return bool(got.get("scheduled"))


async def service_held_in_pass(conn, ctx: dict, *,
                               limit_s: float = HELD_REVIEW_BUDGET_S
                               ) -> dict | None:
    """XAVIER'S HELD REVIEW FROM INSIDE A RUNNING PAPER PASS (SW-2): the
    pending held slugs inside their retry window whose groups this pass's
    account holds are reviewed now -- paper_xavier.step on those groups,
    the held review's context -- under the locks the pass already holds,
    for at most `limit_s` (budget AND Xavier's reserved budget; never more
    than HELD_REVIEW_BUDGET_S). Slugs past their window are dropped
    (counted); slugs no group of this account holds go back to the
    scheduler; each reviewed slug's outcome is what paper_xavier.step
    actually did (_settle_reviewed): a deferred or errored group's slug is
    retried inside its window, never noted as reviewed. None when nothing
    is pending. Never raises (CancelledError excepted)."""
    pend = _HELD.get("pending")
    if not pend:
        return None
    from . import paper_xavier as PX
    clock = _HELD.get("clock") or time.time
    now = float(clock())
    qa = _HELD.setdefault("queued_at", {})
    # TAKEN BEFORE ANY AWAIT: the background scheduler, sharing this loop,
    # then finds them gone and does not run them a second time
    batch = sorted(pend)
    pend.difference_update(batch)
    taken, dropped = {}, []
    for s in batch:
        q = float(qa.get(s, now))
        if now - q <= HELD_REVIEW_RETRY_WINDOW_S:
            taken[s] = q
        else:
            dropped.append(s)
            qa.pop(s, None)
            _HELD.setdefault("attempts", {}).pop(s, None)
            _note_outcome(s, queued_at=q, at=now, via=V_DROPPED)
    _HELD["in_pass_dropped"] = int(_HELD.get("in_pass_dropped") or 0) + \
        len(dropped)
    out: dict[str, Any] = {"slugs": 0, "groups": 0, "reviews": 0,
                           "reviewed_slugs": 0, "retried": 0,
                           "dropped": len(dropped), "dropped_unfinished": 0,
                           "not_due": 0, "deferred": 0, "review_errors": 0,
                           "left_for_scheduler": 0}
    if not taken:
        return out
    acct = ctx["account_id"]
    try:
        rows = await conn.fetch(HELD_GROUPS_SQL, acct, sorted(taken))
    except asyncio.CancelledError:
        pend.update(taken)
        raise
    except Exception as exc:                                   # noqa: BLE001
        pend.update(taken)
        _ensure_scheduler()
        return dict(out, left_for_scheduler=len(taken),
                    error="%s: %s" % (type(exc).__name__, str(exc)[:160]))
    slug_groups: dict[str, set] = {}
    for r in rows:
        slug_groups.setdefault(r["us_market_slug"], set()).add(r["group_id"])
    # not this account's: the scheduler's, as before (it reviews the main
    # account's groups once the lock is free, or drops it past the window)
    back = [s for s in taken if s not in slug_groups]
    pend.update(back)
    out["left_for_scheduler"] = len(back)
    mine = {s: q for s, q in taken.items() if s in slug_groups}
    groups = sorted({x for gs in slug_groups.values() for x in gs})
    if not groups:
        _ensure_scheduler()
        return out
    # THE CAP IS A REAL BOUND: this checkpoint's Xavier budget and its
    # reserved budget are both what is left of HELD_IN_PASS_MAX_S (and never
    # more than the held review's own budget)
    limit = max(0.0, min(HELD_REVIEW_BUDGET_S, float(limit_s)))
    cfg = dict(ctx.get("config") or {})
    cad = dict(cfg.get("cadence") or {})
    cad["xavier_reserved_budget_s"] = min(
        float(cad.get("xavier_reserved_budget_s", PX.RESERVED_BUDGET_S)),
        limit)
    cfg["cadence"] = cad
    t_review = float(clock())
    sub: dict[str, Any] = {
        "session": ctx["session"], "session_id": ctx["session_id"],
        "account_id": acct, "config": cfg,
        "now": float(ctx["clock"]()) if ctx.get("clock") else time.time(),
        "fee_fn": ctx.get("fee_fn"),
        "deadline": time.monotonic() + limit,
        "clock": ctx.get("clock") or time.time,
        "schedule_review_at": ctx.get("schedule_review_at")}
    failed = None
    try:
        got = await PX.step(conn, sub, only_groups=groups)
    except asyncio.CancelledError:
        pend.update(mine)
        raise
    except Exception as exc:                                   # noqa: BLE001
        # nothing is assumed reviewed: every slug is retried in its window
        got = None
        failed = "%s: %s" % (type(exc).__name__, str(exc)[:160])
    settled = _settle_reviewed(mine, slug_groups, got, at=t_review,
                               now=float(clock()), via=V_IN_PASS)
    if settled["reviewed"]:
        _HELD["serviced_in_pass"] = int(_HELD.get("serviced_in_pass")
                                        or 0) + 1
        _HELD["in_pass_slugs"] = int(_HELD.get("in_pass_slugs") or 0) + \
            len(settled["reviewed"])
    if back or settled["retried"]:
        _ensure_scheduler()
    g = got if isinstance(got, dict) else {}
    errs = ([failed] if failed else []) + [
        "%s: %s" % (e.get("group_id"), e.get("error"))
        for e in g.get("review_errors") or [] if isinstance(e, dict)]
    res = dict(out, slugs=len(mine), groups=len(groups),
               reviews=int(g.get("reviews") or 0),
               reviewed_slugs=len(settled["reviewed"]),
               retried=len(settled["retried"]),
               dropped_unfinished=len(settled["dropped"]),
               not_due=len(settled["not_due"]),
               deferred=len(g.get("deferred") or []),
               review_errors=len(g.get("review_errors") or []),
               by_trigger=dict(g.get("by_trigger") or {}),
               budget_s=round(limit, 3))
    if errs:
        res["error"] = "; ".join(errs)[:300]
    return res


#: the held_in_pass counters a pass record carries (summed per checkpoint)
HELD_IN_PASS_COUNTS = ("slugs", "groups", "reviews", "reviewed_slugs",
                       "retried", "dropped", "dropped_unfinished", "not_due",
                       "deferred", "review_errors", "left_for_scheduler")


async def _held_checkpoint(conn, ctx: dict, out: dict, *,
                           room: float | None = None) -> None:
    """Between two steps of a pass: the pending held changes, at most
    HELD_IN_PASS_MAX_S of them per pass (recorded on the pass as
    held_in_pass). The time it takes is NOT charged to the pass: the
    pass's deadline moves later by it, so the steps after it (books,
    simulate, entries, Xavier) keep their whole budget. Free when nothing
    is pending. `room`, when given, is the pass time left for steps (RC6.3b:
    the cap is cut to it, so the checkpoint cannot run into the time kept
    for the pass's record); with none left it is capped like a spent
    HELD_IN_PASS_MAX_S and the slugs stay with the scheduler. Never raises
    (CancelledError excepted)."""
    if not _HELD.get("pending"):
        return
    rec = out.setdefault("held_in_pass", dict(
        {k: 0 for k in HELD_IN_PASS_COUNTS}, checkpoints=0, elapsed_s=0.0,
        pass_deadline_moved_s=0.0, capped=False, errors=[]))
    left = HELD_IN_PASS_MAX_S - rec["elapsed_s"]
    if room is not None:
        left = min(left, float(room))
    if left <= 0:
        rec["capped"] = True
        # the slugs stay with the scheduler (retried busy, then dropped)
        _ensure_scheduler()
        return
    t0 = time.monotonic()
    try:
        got = await service_held_in_pass(conn, ctx, limit_s=left)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        got = {"error": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    spent = time.monotonic() - t0
    if ctx.get("deadline") is not None:
        ctx["deadline"] = float(ctx["deadline"]) + spent
        rec["pass_deadline_moved_s"] = round(
            rec["pass_deadline_moved_s"] + spent, 3)
    rec["elapsed_s"] = round(rec["elapsed_s"] + spent, 3)
    if got is None:
        return
    rec["checkpoints"] += 1
    for k in HELD_IN_PASS_COUNTS:
        rec[k] += int(got.get(k) or 0)
    if got.get("error"):
        rec["errors"] = (rec["errors"] + [got["error"]])[-5:]


async def _bounded_checkpoint(conn, ctx: dict, out: dict,
                              steps_end: float) -> None:
    """The between-steps held checkpoint, inside the pass time that is left
    for steps (`steps_end`, a monotonic instant). The checkpoint's own cap
    is cut to that room; should it still run past it (one group review in
    flight) it is cancelled -- its slugs go back to the pending set, where
    the scheduler retries or drops them -- the cut is named on
    held_in_pass.errors, and the connection is put back. Never raises
    (CancelledError excepted)."""
    if not _HELD.get("pending") or _closed(conn):
        return
    room = steps_end - time.monotonic()
    t_cp = time.monotonic()
    in_tx = _in_transaction(conn)
    bound = asyncio.timeout(max(0.0, room))
    try:
        async with bound:
            await _held_checkpoint(conn, ctx, out, room=max(0.0, room))
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        rec = out.setdefault("held_in_pass", dict(
            {k: 0 for k in HELD_IN_PASS_COUNTS}, checkpoints=0, elapsed_s=0.0,
            pass_deadline_moved_s=0.0, capped=False, errors=[]))
        why = ("%s: the held checkpoint was cancelled after %.1fs at the end "
               "of the pass time left for steps" % (R_STEP_EXCEEDED,
                                                    time.monotonic() - t_cp)
               if bound.expired() else
               "%s: %s" % (type(exc).__name__, str(exc)[:160]))
        rec["errors"] = (rec["errors"] + [why])[-5:]
        rec["capped"] = True
        problem = await _reset_after_cut(conn, was_in_tx=in_tx)
        if problem:
            out["errors"]["CONNECTION_RESET"] = problem
    else:
        # the same look at the connection as after a step: a transaction the
        # checkpoint left open would carry into the next step
        ended, problem = await _end_open_transaction(conn, was_in_tx=in_tx)
        if ended or problem:
            rec = out.setdefault("held_in_pass", dict(
                {k: 0 for k in HELD_IN_PASS_COUNTS}, checkpoints=0,
                elapsed_s=0.0, pass_deadline_moved_s=0.0, capped=False,
                errors=[]))
            rec["errors"] = (rec["errors"] + [
                "%s: the held checkpoint ended with a transaction open or "
                "aborted; it was %s" % (
                    R_STEP_LEFT_TRANSACTION,
                    "rolled back" if ended else "NOT rolled back")])[-5:]
            if problem:
                out["errors"]["CONNECTION_RESET"] = problem


def schedule_held_review(slugs, *, get_pool=None, clock=time.time) -> dict:
    """Synchronous (the feed's change notification): schedule a bounded
    background held review on its own connection. Returns at once."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    qa = _HELD.setdefault("queued_at", {})
    # the clock that stamps `queued_at` is the one every window is judged on
    # (requeue_busy here, service_held_in_pass inside a pass)
    _HELD["clock"] = clock
    t_now = float(clock())
    tries = _HELD.setdefault("attempts", {})
    for s in (slugs or []):
        # the window runs from the LATEST request for the slug: a newer
        # change has a fresh probability of its own (and its own retries)
        qa[s] = t_now
        tries.pop(s, None)
    _HELD["pending"].update(slugs or [])
    t = _HELD.get("task")
    if t is not None and not t.done():
        _HELD["coalesced"] += 1
        return {"scheduled": False, "why": "COALESCED_INTO_THE_RUNNING_REVIEW"}
    if get_pool is None:
        from ..db import get_pool
    # a pass that hands slugs back restarts this scheduler with the same
    # pool and clock (_ensure_scheduler)
    _HELD["respawn"] = {"get_pool": get_pool, "clock": clock}

    async def run():
        while _HELD["pending"]:
            batch = set(_HELD["pending"])
            _HELD["pending"].clear()
            res = None
            t_review = float(clock())
            # each slug's window start as it was when taken (a notification
            # arriving during the review starts a newer window of its own)
            taken = {s: float(qa.get(s, t_review)) for s in batch}
            try:
                pool = await get_pool()
                async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as c:
                    res = await asyncio.wait_for(
                        held_review(c, slugs=batch), HELD_REVIEW_TIMEOUT_S)
                    _HELD["last"] = res
                _HELD["runs"] += 1
            except asyncio.CancelledError:
                raise
            except Exception:                                  # noqa: BLE001
                log.warning("xavier held review failed", exc_info=True)
            if isinstance(res, dict) and res.get("refusal") == R_BUSY:
                # a paper pass holds the lock: retry inside the window
                requeue_busy(batch, now=float(clock()))
            else:
                settle_background(taken, res, at=t_review,
                                  now=float(clock()))
            await asyncio.sleep(HELD_REVIEW_MIN_GAP_S)

    _HELD["task"] = loop.create_task(run())
    return {"scheduled": True}


def settle_background(taken: dict, res, *, at: float, now: float) -> dict:
    """THE BACKGROUND HELD REVIEW'S OUTCOME FOR EACH SLUG (not busy): what
    held_review actually did. No result (it raised, timed out or had no
    connection) or an error: nothing was reviewed -- retried inside the
    window, then dropped as a review error. A refusal (paper disabled):
    HELD_REVIEW_REFUSED. A slug no group of the account holds:
    NOT_HELD_BY_THE_REVIEWING_ACCOUNT. Otherwise _settle_reviewed on the
    groups that hold it -- a deferred or errored group's slug is never
    noted as reviewed. Pure apart from _HELD."""
    qa = _HELD.setdefault("queued_at", {})
    pend = _HELD.setdefault("pending", set())

    def _close(s, via):
        _note_outcome(s, queued_at=taken[s], at=at, via=via)
        if s not in pend and qa.get(s) == taken[s]:
            qa.pop(s, None)
            _HELD.setdefault("attempts", {}).pop(s, None)
    if not isinstance(res, dict) or res.get("error"):
        return _settle_reviewed(taken, {}, None, at=at, now=now,
                                via=V_HELD_REVIEW)
    if not res.get("ran"):
        for s in sorted(taken):
            _close(s, V_REFUSED)
        return {"refused": sorted(taken)}
    sg = res.get("slug_groups") or {}
    mine = {s: q for s, q in taken.items() if sg.get(s)}
    for s in sorted(set(taken) - set(mine)):
        _close(s, V_NOT_HELD)
    got = _settle_reviewed(mine, sg, res.get("xavier"), at=at, now=now,
                           via=V_HELD_REVIEW)
    return dict(got, not_held=sorted(set(taken) - set(mine)))


# ═════════════════════════════════════════════════════════════════════
# FRESHNESS EXPIRY REQUEUES XAVIER (owner P0, 2026-10-04)
# ═════════════════════════════════════════════════════════════════════
#
# A fresh review stands on a probability that stops being current at its
# own source stamp + the existing limit (the 30 s rule). Waiting for the
# next 60 s pass left a stale recommendation on display; so each fresh
# review schedules a held review of its market for that instant, through
# the SAME debounced, bounded mechanism a PinnAPI change uses
# (schedule_held_review). One timer per market (a newer fresh review
# replaces it), at most EXPIRY_MAX_TIMERS, never sooner than now, never
# further out than EXPIRY_MAX_DELAY_S -- no loop: the review it causes is
# FRESHNESS_EXPIRY once (paper_xavier._trigger), then WAITING or fresh.

EXPIRY_GRACE_S = 0.5
EXPIRY_MAX_DELAY_S = 300.0
EXPIRY_MAX_TIMERS = 400
_EXPIRY: dict = {"timers": {}, "scheduled": 0, "replaced": 0, "fired": 0,
                 "dropped": 0}


def schedule_expiry_review(slug, expires_at, *, now=None, loop=None,
                           fire=None) -> dict:
    """Synchronous: schedule Xavier's held review of `slug` for the instant
    its fresh probability expires. `fire` (tests) replaces the held-review
    scheduler. Returns at once; never raises."""
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    if not slug or expires_at is None:
        return {"scheduled": False, "why": "NO_SLUG_OR_EXPIRY"}
    timers = _EXPIRY["timers"]
    if slug not in timers and len(timers) >= EXPIRY_MAX_TIMERS:
        _EXPIRY["dropped"] += 1
        return {"scheduled": False, "why": "EXPIRY_TIMER_BOUND_REACHED"}
    t_now = float(now if now is not None else time.time())
    delay = min(max(float(expires_at) + EXPIRY_GRACE_S - t_now, 0.0),
                EXPIRY_MAX_DELAY_S)
    old = timers.pop(slug, None)
    if old is not None:
        old.cancel()
        _EXPIRY["replaced"] += 1
    run = fire or schedule_held_review

    def _fire():
        timers.pop(slug, None)
        _EXPIRY["fired"] += 1
        try:
            run([slug])
        except Exception:                                      # noqa: BLE001
            log.warning("xavier expiry review failed", exc_info=True)

    timers[slug] = loop.call_later(delay, _fire)
    _EXPIRY["scheduled"] += 1
    return {"scheduled": True, "slug": slug, "delay_s": round(delay, 3),
            "expires_at": float(expires_at)}


def expiry_status() -> dict:
    return {k: (len(v) if k == "timers" else v) for k, v in _EXPIRY.items()}
