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

THE MARKET-DATA CLIENT is a `bettor_paper_guard.PaperMarketDataClient`: one
read, every mutation refused before transmission and counted in the
session's health record (expected 0).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .. import bettor_paper_guard as G
from .. import bettor_paper_ledger as L
from .. import bettor_paper_session as S

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

async def step_books(conn, ctx: dict) -> dict:
    """Read the books of markets with open paper orders or open positions,
    within the per-pass read budget. Each read is recorded as observed."""
    from .. import bettor_paper_simulator as SIM
    acct = ctx["account_id"]
    slugs = [r["us_market_slug"] for r in await conn.fetch(
        "SELECT DISTINCT us_market_slug FROM paper_orders "
        " WHERE account_id=$1 AND state = ANY($2::text[])", acct,
        list(L.OPEN_STATES))]
    # Positions next, the stalest observation first, so marks rotate.
    held = [p["us_market_slug"] for p in await L.positions(conn, acct)
            if p["us_market_slug"] not in slugs]
    if held:
        ages = {r["us_market_slug"]: L._epoch(r["at"]) for r in
                await conn.fetch(
                    "SELECT us_market_slug, max(observed_at) AS at FROM "
                    " paper_book_observations WHERE us_market_slug = "
                    " ANY($1::text[]) GROUP BY 1", held)}
        held.sort(key=lambda s: ages.get(s) or 0.0)
    slugs.extend(dict.fromkeys(held))
    # AT MOST HALF THE PASS'S READS: the other half is Derek's.
    cap = int(ctx["config"]["cadence"]["max_book_reads_per_pass"])
    return await read_books(conn, ctx, slugs, basis="OPEN_ORDER_OR_POSITION",
                            limit=max(1, cap // 2))


async def read_books(conn, ctx: dict, slugs: list, *, basis: str,
                     limit: int | None = None) -> dict:
    from .. import bettor_paper_simulator as SIM
    cap = int(ctx["config"]["cadence"]["max_book_reads_per_pass"])
    out = {"read": 0, "errors": 0, "skipped_budget": 0, "obs": {}}
    for slug in slugs:
        if ctx["books_read"] >= cap or not _budget_left(ctx) or (
                limit is not None and out["read"] >= limit):
            out["skipped_budget"] += 1
            continue
        got = await ctx["market_data"].read_book(slug)
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


def default_steps() -> list:
    steps = [("books", step_books), ("simulate", step_simulate)]
    try:
        from . import paper_derek as PD
        steps.append(("derek", PD.step))
        steps.append(("simulate_after_delay", PD.step_after_delay))
    except ImportError:
        pass
    try:
        from . import paper_xavier as PX
        steps.append(("settle", PX.step_settle))
        steps.append(("handoff", PX.step_handoff))
        steps.append(("xavier", PX.step))
    except ImportError:
        pass
    steps.append(("equity", step_equity))
    try:
        from . import paper_audrey as PA
        steps.append(("audrey", PA.step))
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
    cfg = sess["config"]
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
        "clock": (time.time if live_clock else (lambda: at))}
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


async def write_heartbeat(conn, res: dict) -> None:
    import json as _json
    try:
        await conn.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = $2::jsonb",
            HEARTBEAT_KEY, _json.dumps(dict(_digest(res), refusal=res.get(
                "refusal"), why=res.get("why"), written_at=time.time()),
                default=str)[:60000])
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


async def decide_valuation(conn, *, valuation_id, now: float | None = None,
                           market_data=None, account_id: str | None = None,
                           fee_fn=None, schedule_fill=None) -> dict:
    """ONE PAPER DECISION FOR ONE JUST-WRITTEN VALUATION. Never raises."""
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
        cfg = sess["config"]
        ctx: dict[str, Any] = {
            "session": sess, "session_id": sess["session_id"],
            "account_id": acct, "config": cfg, "market_data": md,
            "now": at, "deadline": time.monotonic()
            + VALUATION_HOOK_TIMEOUT_S, "fee_fn": fee_fn, "books_read": 0,
            "first_fills": [], "fills": 0, "results": {},
            "clock": (time.time if live_clock else (lambda: at)),
            "decided_via": "IN_CYCLE_AT_THE_VALUATION_INSTANT",
            "context_cache_key": sess["session_id"]}
        from . import paper_derek as PD
        rec = await asyncio.wait_for(PD.decide_one(conn, ctx, dict(row)),
                                     VALUATION_HOOK_TIMEOUT_S)
        delta = int(getattr(md, "mutation_attempts", 0) or 0) - before
        if delta:
            import json as _json
            await conn.execute(
                "UPDATE paper_session_health SET mutation_attempts = "
                " mutation_attempts + $2, last_mutation_attempt = $3::jsonb "
                " WHERE session_id = $1", sess["session_id"], delta,
                _json.dumps(getattr(md, "last_mutation_attempt", None),
                            default=str))
        if rec.get("order_id"):
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
        return dict({k: rec.get(k) for k in (
            "decision_id", "verdict", "refusal", "order_id", "duplicate",
            "deferred", "fill_pass")}, decided=not rec.get("deferred"),
            mutation_attempts=delta)
    except asyncio.CancelledError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        return {"decided": False, "error": "%s: %s" % (type(exc).__name__,
                                                       str(exc)[:200])}
