"""THE PAPER PASS: DEREK, THE SIMULATOR, XAVIER AND AUDREY ON THE PAPER BOOK.

Runs inside the existing scheduled cycle and servicing task
(`agents.runtime.paper_pass_hook`, called by `workers/ext_pinnacle_loop` after
the execution lock is released), ONLY when both the environment flag
PAPER_SESSION=on and the database control row are on
(`bettor_paper_session.enablement`). It never holds the funded execution
lock, never blocks Xavier's funded servicing or the collection cycle, and is
BOUNDED: the steps check a monotonic deadline (the session config's
`cadence.pass_budget_s`) and the runtime hook adds a hard timeout.

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
# THE PASS
# ═════════════════════════════════════════════════════════════════════

async def paper_pass(conn, *, now: float | None = None,
                     account_id: str = L.ACCOUNT_ID, market_data=None,
                     steps: list | None = None, config: dict | None = None,
                     force: bool = False, fee_fn=None,
                     trigger: str = "SCHEDULED_SERVICING",
                     cycle: dict | None = None, sleep=None) -> dict:
    """ONE BOUNDED PAPER PASS. Never raises (CancelledError excepted).

    `force` skips the enablement check (tests only; the scheduled hook never
    passes it). `steps` replaces the default steps (a list of callables or
    (name, callable) pairs taking (conn, ctx))."""
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
        try:
            return await _run(conn, out, at=at, t0=t0,
                              account_id=account_id, market_data=market_data,
                              steps=steps, config=config, fee_fn=fee_fn,
                              cycle=cycle, live_clock=live_clock,
                              sleep=sleep)
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                pass


async def _run(conn, out, *, at, t0, account_id, market_data, steps, config,
               fee_fn, cycle, live_clock=False, sleep=None) -> dict:
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
    for item in (steps if steps is not None else default_steps()):
        name, fn = (item if isinstance(item, tuple)
                    else (getattr(item, "__name__", "step"), item))
        try:
            out["steps"][name] = await fn(conn, ctx)
        except asyncio.CancelledError:
            raise
        except G.PaperVenueMutationRefused as exc:
            out["errors"][name] = "%s: %s" % (G.R_MUTATION_REFUSED,
                                              str(exc)[:200])
        except Exception as exc:                               # noqa: BLE001
            out["errors"][name] = "%s: %s" % (type(exc).__name__,
                                              str(exc)[:200])
    out["budget_exhausted"] = not _budget_left(ctx)
    out["books_read"] = ctx["books_read"]
    out["fills"] = ctx["fills"]
    attempts = int(getattr(md, "mutation_attempts", 0) or 0) - attempts_before
    out["mutation_attempts"] = attempts
    out["elapsed_s"] = round(time.monotonic() - t0, 3)
    for k in ("decisions_recorded", "orders_submitted", "reviews"):
        out[k] = sum(int((v or {}).get(k) or 0)
                     for v in out["steps"].values() if isinstance(v, dict))
    try:
        await S.record_pass(
            conn, sess["session_id"], result=_digest(out), now=at,
            mutation_attempts=attempts,
            last_mutation_attempt=getattr(md, "last_mutation_attempt", None),
            error=("; ".join("%s=%s" % kv for kv in out["errors"].items())
                   [:500] or None))
    except Exception as exc:                                   # noqa: BLE001
        out["errors"]["HEALTH"] = type(exc).__name__
    return out


def _digest(out: dict) -> dict:
    return {k: out.get(k) for k in (
        "version", "at", "trigger", "ran", "session_id", "resumed",
        "errors", "budget_exhausted", "books_read", "fills",
        "decisions_recorded", "orders_submitted", "reviews",
        "mutation_attempts", "elapsed_s")} | {
        "steps": {k: (v if not isinstance(v, dict) else
                      {kk: vv for kk, vv in v.items()
                       if not isinstance(vv, (list, dict))})
                  for k, v in (out.get("steps") or {}).items()}}


def describe() -> dict:
    return {"version": VERSION, "steps": [n for n, _ in default_steps()],
            "advisory_lock_key": ADVISORY_LOCK_KEY}


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
                            why=res.get("why"), written_at=time.time()),
                       default=str)
    if len(body) <= HEARTBEAT_MAX_CHARS:
        return body
    return _json.dumps({"heartbeat_truncated": True,
                        "original_chars": len(body),
                        "refusal": res.get("refusal"), "why": res.get("why"),
                        "written_at": time.time()}, default=str)


async def write_heartbeat(conn, res: dict) -> None:
    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            HEARTBEAT_KEY, _heartbeat_body(res))
    except Exception:                                          # noqa: BLE001
        pass


async def run_once(get_pool, *, trigger: str, now: float | None = None,
                   **kw) -> dict:
    """ONE PASS ON ITS OWN CONNECTION, bounded; never raises."""
    try:
        pool = await get_pool()
        async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as conn:
            try:
                res = await asyncio.wait_for(
                    paper_pass(conn, now=now, trigger=trigger, **kw),
                    HARD_TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:                           # noqa: BLE001
                res = {"ran": False, "trigger": trigger,
                       "refusal": "PAPER_PASS_RAISED_OR_TIMED_OUT",
                       "why": "%s: %s" % (type(exc).__name__,
                                          str(exc)[:200])}
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
    for strat, fn in plan:
        if strategies is not None and strat not in strategies:
            continue
        if retry_for:
            res = {"deferred": True, "why": "BOOK_RETRY",
                   "retry": {"with": retry_for[0]}}
            retry_for.append(strat)
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
        try:
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
                res=dict(rec, elapsed_s=round(time.monotonic() - t_derek, 3)))
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
_HELD: dict = {"task": None, "pending": set(), "runs": 0, "coalesced": 0,
               "last": None}


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
            groups = [r["group_id"] for r in await conn.fetch(
                "SELECT DISTINCT o.group_id FROM paper_orders o "
                "  JOIN paper_handoffs h ON h.group_id = o.group_id "
                " WHERE o.account_id = $1 AND o.role = 'ENTRY' "
                "   AND o.us_market_slug = ANY($2::text[])", acct,
                sorted(slugs or []))]
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
            return dict(out, ran=True, groups=groups, xavier=got)
        except Exception as exc:                               # noqa: BLE001
            return dict(out, error=type(exc).__name__,
                        detail=str(exc)[:200])
        finally:
            try:
                await conn.execute("SELECT pg_advisory_unlock($1)",
                                   ADVISORY_LOCK_KEY)
            except Exception:                                  # noqa: BLE001
                pass


def schedule_held_review(slugs, *, get_pool=None) -> dict:
    """Synchronous (the feed's change notification): schedule a bounded
    background held review on its own connection. Returns at once."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return {"scheduled": False, "why": "NO_RUNNING_LOOP"}
    _HELD["pending"].update(slugs or [])
    t = _HELD.get("task")
    if t is not None and not t.done():
        _HELD["coalesced"] += 1
        return {"scheduled": False, "why": "COALESCED_INTO_THE_RUNNING_REVIEW"}
    if get_pool is None:
        from ..db import get_pool

    async def run():
        while _HELD["pending"]:
            batch = set(_HELD["pending"])
            _HELD["pending"].clear()
            try:
                pool = await get_pool()
                async with pool.acquire(timeout=ACQUIRE_TIMEOUT_S) as c:
                    _HELD["last"] = await asyncio.wait_for(
                        held_review(c, slugs=batch), HELD_REVIEW_TIMEOUT_S)
                _HELD["runs"] += 1
            except asyncio.CancelledError:
                raise
            except Exception:                                  # noqa: BLE001
                log.warning("xavier held review failed", exc_info=True)
            await asyncio.sleep(HELD_REVIEW_MIN_GAP_S)

    _HELD["task"] = loop.create_task(run())
    return {"scheduled": True}


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
