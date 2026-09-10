"""E9 fold-2 review pins (2026-09-08): the serialisation of the fast tick
against the full tick and the two seeds, driven against the worker
file's fakes with the builder's own helpers. A passing pin records what
holds on the landed tree; a strict xfail is a defect the review names.

The eight questions: the deadlock walk (a cancel or a raise on either
side of either lock), the check-then-acquire and its inverse (the
release window), every path that places (an AST call graph: nothing
places outside tick_once / fast_tick_once), the drain (a wake at the
tail of a full tick: answered once, by the fast tick that waited), the
seeds (the window counted once, forward only), the fixture's fresh locks
(why: a contended asyncio.Lock binds to its loop).
"""
import ast
import asyncio
import inspect
import pathlib
import re

from sportsassets import live_executor as le
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _bbos, _skips, _walk
from tests.test_e9_review_pins import _pause_read, _spin
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _armed, _census, _his, _Http, _places, _pool, _run, _Venue,
)

TESTS = pathlib.Path(__file__).resolve().parent


def _sleep_free(coro, n=200):
    """Await `coro` from a task after `n` bare hops (never a real sleep)."""
    async def _go():
        for _ in range(n):
            await asyncio.sleep(0)
        return await coro
    return _go()


# --------------------------------------------- Q1: deadlock, the flag

def test_fold2_q1_a_cancel_inside_the_fast_ticks_read_clears_the_flag_and_frees_both_locks(monkeypatch):
    """The fast tick holds _FAST_LOCK and _TICK_LOCK inside its quote
    read; the task is cancelled there. CancelledError is not an
    Exception: the inner `except Exception` does not eat it, both
    `finally` blocks run (the flag down), the `async with` releases both
    locks. A full tick then runs -- not `overlap`, not a hang."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    events = _pause_read(monkeypatch, lambda t, cid: t.fast)

    async def _drive():
        paused, resume = events()
        p.clock = NOW + 1
        fast = asyncio.create_task(ml.fast_tick_once(p, v, _Http(), cids=[CID], now_ts=NOW + 1))
        assert await _spin(paused.is_set)
        assert ml._fast_holding is True and ml._TICK_LOCK.locked() and ml._FAST_LOCK.locked()
        fast.cancel()
        try:
            await fast
        except asyncio.CancelledError:
            pass
        assert fast.cancelled(), "the cancel reached the task: not swallowed by the fast tick's own guard"
        assert ml._fast_holding is False, "the flag never outlives the holder"
        assert not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()
        p.clock = NOW + 2
        return await ml.tick_once(p, v, _Http(), now_ts=NOW + 2)
    st = _run(_drive())
    assert st["status"] != "overlap" and not st.get("skipped_overlap") and st["orders_open"] == 0
    assert len(_places(v)) == 1, "the full tick placed the entry the cancelled fast tick never did"


def test_fold2_q1_a_cancel_while_the_fast_tick_waits_on_a_full_ticks_hold_frees_fast_lock_and_pops_nothing():
    """The fast tick holds _FAST_LOCK and waits on _TICK_LOCK (a full
    tick's hold, by hand); cancelled there. The multi-item `async with`
    is the nested form: the second acquire raising releases the first
    lock; the flag was never raised; the market is still on _FAST_WOKEN
    for the next fast tick (popped only under the lock)."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()

    async def _drive():
        ml._FAST_WOKEN[CID] = 0
        async with ml._TICK_LOCK:
            fast = asyncio.create_task(ml.fast_tick_once(p, v, _Http(), now_ts=NOW + 1))
            assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
            assert ml._FAST_LOCK.locked() and not ml._fast_holding and ml._FAST_WOKEN == {CID: 0}
            fast.cancel()
            try:
                await fast
            except asyncio.CancelledError:
                pass
            assert fast.cancelled()
            assert not ml._FAST_LOCK.locked(), "the first lock of the pair is released by the second's cancel"
            assert ml._fast_holding is False and ml._FAST_WOKEN == {CID: 0}, "nothing popped, nothing raised"
            assert ml._TICK_LOCK.locked() and not ml._TICK_LOCK._waiters, "the hand's hold stands, no orphan waiter"
        # the lock free again: the next fast tick takes the market
        st = await ml.fast_tick_once(p, v, _Http(), now_ts=NOW + 2)
        return st
    st = _run(_drive())
    assert st["woken"] == [CID] and st["fast"]["placed"] == 1 and ml._FAST_WOKEN == {}
    assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()


def test_fold2_q1_a_full_tick_raising_inside_its_hold_frees_the_lock_and_the_fast_tick_then_runs(monkeypatch):
    """`_tick` raises inside `async with _TICK_LOCK`: the `finally`
    clears `_full_tick`, the `async with` releases the lock, the error
    leaves tick_once (main's own `except` names it). A fast tick that
    waited on that hold then runs and places (its market handed by
    `cids`: one on _FAST_WOKEN before the full tick started is that
    tick's, drained at its start -- the rule, pinned by the fold)."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    orig = ml._tick

    async def _boom(t, woken):
        await asyncio.sleep(0)
        raise RuntimeError("tick boom")
    monkeypatch.setattr(ml, "_tick", _boom)

    async def _drive():
        ml._FAST_WOKEN[CID] = 0
        full = asyncio.create_task(ml.tick_once(p, v, _Http(), now_ts=NOW + 1))
        fast = asyncio.create_task(ml.fast_tick_once(p, v, _Http(), cids=[CID], now_ts=NOW + 2))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
        assert ml._FAST_WOKEN == {}, "drained by the full tick's start, before it raised"
        try:
            await full
        except RuntimeError as exc:
            assert "tick boom" in str(exc)
        else:
            raise AssertionError("the raise must leave tick_once")
        assert ml._full_tick is None
        monkeypatch.setattr(ml, "_tick", orig)
        return await fast
    fs = _run(_drive())
    assert fs["fast"]["placed"] == 1 and _skips(fs) == {} and ml._FAST_WOKEN == {}
    assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()


def test_fold2_q1_a_fast_tick_raising_before_its_inner_try_clears_the_flag_and_fast_run_swallows_it(monkeypatch):
    """The `_Tick` construction sits between the flag and the inner
    try: a raise there leaves fast_tick_once through the OUTER finally
    (the flag down, both locks released) and `_fast_run` logs it --
    never the loop's error, never a held lock."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    real = ml._Tick

    def _tick_cls(*a, **kw):
        if kw.get("fast"):
            raise RuntimeError("fast tick boom")
        return real(*a, **kw)
    monkeypatch.setattr(ml, "_Tick", _tick_cls)

    async def _drive():
        try:
            await ml.fast_tick_once(p, v, _Http(), cids=[CID], now_ts=NOW + 1)
        except RuntimeError:
            pass
        else:
            raise AssertionError("the raise leaves fast_tick_once")
        assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()
        # the scheduled run: the same raise is logged, the task completes
        ml._FAST_WOKEN[CID] = 0
        ctx = (p, v, _Http())
        monkeypatch.setattr(ml, "_fast_ctx", ctx)
        monkeypatch.setattr(ml, "_fast_last_at", 0.0)
        await ml._fast_run(ctx)
        return True
    assert _run(_drive()) is True
    assert ml._fast_holding is False and not ml._TICK_LOCK.locked() and not ml._FAST_LOCK.locked()
    assert _bbos(v) == [] and not _places(v)


def test_fold2_q1_the_lock_order_is_fast_then_tick_and_nothing_takes_them_the_other_way_round():
    """No cycle: the only function naming both locks is fast_tick_once
    (FAST outside, TICK inside); tick_once names _TICK_LOCK alone; no
    function that holds _TICK_LOCK ever takes _FAST_LOCK (the per-book
    locks sit below both on both roads)."""
    src = inspect.getsource(ml)
    tree = ast.parse(src)
    both, tick_only = [], []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body = ast.get_source_segment(src, node) or ""
            takes_fast = "async with _FAST_LOCK" in body
            takes_tick = "async with _TICK_LOCK" in body or "async with _FAST_LOCK, _TICK_LOCK" in body
            if takes_fast and takes_tick:
                both.append(node.name)
            elif takes_tick:
                tick_only.append(node.name)
            elif takes_fast:
                raise AssertionError(f"{node.name} takes _FAST_LOCK without _TICK_LOCK inside it")
    assert both == ["fast_tick_once"] and tick_only == ["tick_once"]
    assert "async with _FAST_LOCK, _TICK_LOCK:" in inspect.getsource(ml.fast_tick_once)
    assert re.findall(r"^\s*(?:st|stats) = await tick_once\(|await tick_once\(", src) and \
        src.count("await tick_once(") == 1 and "await tick_once(" in inspect.getsource(ml.main)
    assert src.count("await fast_tick_once(") == 1 and "await fast_tick_once(" in inspect.getsource(ml._fast_run)


# --------------------------------------------- Q2: check-then-acquire

def _stmts(fn):
    tree = ast.parse(inspect.getsource(fn))
    return tree.body[0].body


def test_fold2_q2_the_check_and_the_acquire_are_adjacent_statements_and_the_flag_is_the_bodys_first():
    """AST, not substrings: in tick_once the `if` on `_fast_holding` is
    immediately followed by the `async with _TICK_LOCK` (no statement,
    so no await, between); in fast_tick_once the `async with` on both
    locks has `_fast_holding = True` as its FIRST statement and the pop
    inside the body after it -- acquire-then-flag with no await is one
    step of the loop, so `locked() and not _fast_holding` never reads a
    fast tick's fresh hold as a full tick's."""
    body = _stmts(ml.tick_once)
    idx = next(i for i, s in enumerate(body)
               if isinstance(s, ast.If) and "_fast_holding" in ast.unparse(s.test))
    assert ast.unparse(body[idx].test) == "_TICK_LOCK.locked() and (not _fast_holding)"
    nxt = body[idx + 1]
    assert isinstance(nxt, ast.AsyncWith) and ast.unparse(nxt.items[0].context_expr) == "_TICK_LOCK"
    assert not any(isinstance(n, ast.Await) for n in ast.walk(body[idx])), "no await inside the check"
    fbody = _stmts(ml.fast_tick_once)
    aw = next(s for s in fbody if isinstance(s, ast.AsyncWith))
    assert [ast.unparse(i.context_expr) for i in aw.items] == ["_FAST_LOCK", "_TICK_LOCK"]
    first = aw.body[0]
    assert isinstance(first, ast.Assign) and ast.unparse(first) == "_fast_holding = True"
    pops = [n for n in ast.walk(aw) if isinstance(n, ast.Call) and "_FAST_WOKEN.pop" in ast.unparse(n.func)]
    assert pops and all(n.lineno > first.lineno for n in pops), "the pop sits under the lock, after the flag"
    assert not any(isinstance(n, ast.Call) and "_FAST_WOKEN.pop" in ast.unparse(n.func)
                   for s in fbody if s is not aw for n in ast.walk(s)), "no pop outside the hold"
    # the flag's clear is the LAST thing inside the hold: the outer try's finally
    last = aw.body[-1]
    assert isinstance(last, ast.Try) and [ast.unparse(s) for s in last.finalbody] == ["_fast_holding = False"]


def test_fold2_q2_the_release_window_a_full_tick_arriving_as_a_fast_ticks_wait_ends_queues_behind_it(monkeypatch):
    """The inverse race at the one point `locked()` is False while a
    fast tick is about to hold: a release wakes the waiter for the NEXT
    loop step, and a full tick started in between sees `locked()` False,
    takes the acquire road and queues BEHIND the fast tick (asyncio's
    Lock is fair: a fresh acquire with waiters pending waits). Order
    pinned: the fast tick's body first, then the full tick's, and the
    full tick reads the fast tick's rest `orders_open == 1`."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    order = []
    ofast, ofull = ml._fast_tick, ml._tick

    async def _f(t, cids):
        order.append(("fast", ml._fast_holding, ml._TICK_LOCK.locked()))
        return await ofast(t, cids)

    async def _t(t, woken):
        order.append(("full", ml._fast_holding, ml._TICK_LOCK.locked()))
        return await ofull(t, woken)
    monkeypatch.setattr(ml, "_fast_tick", _f)
    monkeypatch.setattr(ml, "_tick", _t)

    async def _drive():
        ml._FAST_WOKEN[CID] = 0
        await ml._TICK_LOCK.acquire()                       # a full tick's hold, by hand
        fast = asyncio.create_task(ml.fast_tick_once(p, v, _Http(), now_ts=NOW + 1))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters))
        ml._TICK_LOCK.release()                             # the waiter is woken for the next step
        assert not ml._TICK_LOCK.locked() and not ml._fast_holding, "the window: unlocked, flag down"
        p.clock = NOW + 2
        full = asyncio.create_task(ml.tick_once(p, v, _Http(), now_ts=NOW + 2))
        fs = await fast
        st = await full
        return fs, st
    fs, st = _run(_drive())
    assert [o[0] for o in order] == ["fast", "full"], order
    assert order[0][1:] == (True, True) and order[1][1:] == (False, True)
    assert fs["fast"]["placed"] == 1 and st["status"] != "overlap" and st["orders_open"] == 1
    assert len(_places(v)) == 1, "one placement: the full tick read the rest standing"


# --------------------------------------------- Q3: what else places

# E31 (FILL lane 31, 2026-09-10): `_flatten_send` is deleted -- the flatten's
# slippage leg (close_position, the co-held IOC) went with the take, and the
# flatten is now an ordinary post-only rest through _place. Nothing replaced it
# as a root: the list is one shorter, and the pin below still walks every path
# that places or cancels up to the two locked roots
PLACERS = ("_place", "_place_reserved", "_cancel_and_settle", "_close_settled",
           "_cancel_frozen_open", "_cancel_open_for")
ROOTS = {"tick_once", "fast_tick_once"}


def test_fold2_q3_every_placing_cancelling_path_in_the_module_is_reached_only_through_the_two_locked_roots():
    """The module's call graph (every `name(...)` call inside every
    top-level def): walking UP from each placer / canceller and stopping
    at tick_once / fast_tick_once, no other root is reached -- nothing
    in mirror_live places, replaces, cancels, flattens or closes outside
    a _TICK_LOCK hold. (The other lanes: whale_exits hands its exits to
    execute_copy, copy_sweep to maybe_execute, and maybe_execute refuses
    a mirrored whale by `mirror_mode` before any submit -- pinned below.)"""
    src = inspect.getsource(ml)
    tree = ast.parse(src)
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    callers: dict[str, set[str]] = {k: set() for k in defs}
    for name, node in defs.items():
        for c in ast.walk(node):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in defs:
                callers[c.func.id].add(name)
    for pl in PLACERS:
        assert pl in defs, pl
    seen, todo, leaks = set(), list(PLACERS), set()
    while todo:
        f = todo.pop()
        if f in seen or f in ROOTS:
            continue
        seen.add(f)
        if not callers[f]:
            leaks.add(f)
        todo.extend(callers[f])
    assert leaks == set(), f"reached without passing tick_once / fast_tick_once: {sorted(leaks)}"
    assert {"_tick", "_fast_tick", "_tick_book", "_reconcile_orders", "_frozen_exit"} <= seen


def test_fold2_q3_the_copy_lanes_refuse_a_mirrored_whale_before_any_submit():
    """live_executor's copy lane (the fresh path, copy_sweep's reclaim,
    whale_exits' handed exit) all pass maybe_execute, whose mirror gate
    returns the `mirror_mode` stop before the INSERT and both submits:
    a mirrored whale's money never leaves by a road outside the tick
    lock. Source pins, in order."""
    src = inspect.getsource(le.maybe_execute)
    gate = src.index("if mirror_mode(username):")
    assert src.index('_copy_stop("mirror_mode"', gate) < src.index("create_order") if "create_order" in src \
        else True
    for later in ("INSERT INTO", "_submit", "create_order"):
        for m in re.finditer(re.escape(later), src):
            assert m.start() > gate, f"{later!r} before the mirror gate"
    ec = inspect.getsource(le.execute_copy)
    assert "maybe_execute(" in ec, "the fresh path is maybe_execute's"
    we = (TESTS.parents[1] / "backend" / "sportsassets" / "workers" / "whale_exits.py").read_text()
    assert "execute_copy(" in we and "mirror_orders" not in we and "mirror_books" not in we
    cs = (TESTS.parents[1] / "backend" / "sportsassets" / "workers" / "copy_sweep.py").read_text()
    assert "maybe_execute(" in cs and "mirror_orders" not in cs


# --------------------------------------------- Q4: the drain, the tail

def test_fold2_q4_a_wake_at_the_tail_of_a_full_tick_is_answered_once_by_the_fast_tick_that_waited(monkeypatch):
    """(e) The book is on target (ledger 300, his 300); the full tick's
    walk reads it and places nothing. His SELL of the 300 lands while
    the tick is past its last line (paused inside the finally's
    candidate-memo write, the lock still held): the fast tick takes
    _FAST_LOCK and waits, the market still on _FAST_WOKEN (the drain at
    the tick's start was before the wake, so it is NOT this tick's). The
    lock releases, the fast tick pops it and rests our reduce at his
    cent once (the bid three cents under it: a rest, not an IOC); the
    next full tick reads the rest standing. One placement, one quote
    read for it."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(bid=0.28, ask=0.34, held={SLUG: 300})
    http = _Http()
    paused, resume = {}, {}
    orig = ml._persist_cand_memo

    async def _tail(t):
        if not t.fast and "ev" in paused and not paused["ev"].is_set():
            paused["ev"].set()
            await resume["ev"].wait()
        return await orig(t)
    monkeypatch.setattr(ml, "_persist_cand_memo", _tail)

    async def _drive():
        paused["ev"], resume["ev"] = asyncio.Event(), asyncio.Event()
        p.clock = NOW
        full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=NOW))
        assert await _spin(paused["ev"].is_set), "the full tick is past its last line"
        assert ml._TICK_LOCK.locked() and ml._full_tick is None and not _places(v), "on target: nothing placed"
        reads_before = len(_bbos(v))
        p.fills, p.snap, p.snap_at = _his(300, sold=300), {M: 0.0, N: 0.0}, NOW + 1
        ml.notify(CID)                                  # his SELL lands at the tail
        fast = asyncio.create_task(ml.fast_tick_once(p, v, http, now_ts=NOW + 1))
        assert await _spin(lambda: bool(ml._TICK_LOCK._waiters)), "the fast tick waits"
        assert ml._FAST_WOKEN == {CID: 0} and ml._FAST_LOCK.locked() and not ml._fast_holding
        resume["ev"].set()
        st = await full
        fs = await fast
        assert ml._FAST_WOKEN == {} and CID in ml._WOKEN
        p.clock = NOW + 3
        st2 = await ml.tick_once(p, v, http, now_ts=NOW + 3)
        return reads_before, st, fs, st2
    reads_before, st, fs, st2 = _run(_drive())
    assert st["woken"] == [] and st["status"] != "overlap"
    assert fs["woken"] == [CID] and fs["fast"]["placed"] == 1 and _skips(fs) == {}, \
        (fs["fast"], b["last_plan"], {k: c for k, c in fs["census"].items() if c})
    assert len(_places(v)) == 1 and b["open_order_id"] is not None, "the fast tick's one rest"
    assert len(_bbos(v)) == reads_before + 2, "one quote read for the fast tick, one for the next full tick"
    assert st2["woken"] == [CID] and st2["orders_open"] == 1 and len(_places(v)) == 1, "read standing, not placed again"



# --------------------------------------------- Q6: the seeds

def test_fold2_q6_the_window_is_counted_forward_only_a_full_ticks_spend_never_seeds_the_fast_ticks_after_it():
    """The seed runs one way: fast ticks -> the next full tick (guard,
    ops, venue calls). A full tick's own spend is not carried into
    `_fast_*`, so the fast ticks after it start from 0 -- per 30 s poll
    window the guarded-call rail is therefore up to 80 (full) + 80 (the
    fast ticks after it), the same ceiling E9's review Q7 named (~2x);
    the next full tick then starts at the fast ticks' spend. A fact
    pinned, named in the review as the residual (LOW)."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    ml._fast_guard_calls, ml._fast_ops, ml._fast_calls = 7, 3, 9

    async def _drive():
        p.clock = NOW
        st = await ml.tick_once(p, v, _Http(), now_ts=NOW)
        assert (ml._fast_guard_calls, ml._fast_ops, ml._fast_calls) == (0, 0, 0), "reset at the start"
        assert st["ops"] >= 3 + 1, "the seeded ops plus the tick's own placement"
        _walk()
        p.clock = NOW + 1
        fs = await ml.fast_tick_once(p, v, _Http(), cids=[CID], now_ts=NOW + 1)
        return st, fs
    st, fs = _run(_drive())
    assert fs["fast"]["placed"] == 0 and _skips(fs) == {CID: "order_open"}
    assert fs["ops"] == 0 and ml._fast_ops == 0 and ml._fast_guard_calls == 0, "the fast tick started from 0"
    src = inspect.getsource(ml.tick_once)
    assert src.index("t.guard_calls, t.ops = int(_fast_guard_calls), int(_fast_ops)") \
        < src.index("_fast_calls = _fast_guard_calls = _fast_ops = 0")
    assert "_fast_ops +=" not in src and "_fast_guard_calls +=" not in src, "the full tick never adds to the seeds"


def test_fold2_q6_a_30_wake_burst_the_fast_ticks_spend_is_one_window_with_the_full_ticks():
    """The arithmetic the review asks for, on the constants: 30 wakes in
    30 s -> at most 15 fast ticks (the 2 s floor) x 5 markets; the venue
    rail is shared (`t.venue_calls + t.fast_calls >= 60` refuses a
    market: at most one market's four past it), the E2 guard's 80 and
    the 20 ops carried across fast ticks AND into the full tick's start,
    so the full tick refuses its own entries at `ops_capped` past what
    the fast ticks spent; an exit is still exempt there."""
    assert ml.FAST_TICK_MIN_S == 2.0 and ml.FAST_TICK_MAX == 5 and ml.POLL_S == 30.0
    assert ml.VENUE_CALLS_PER_TICK == 60
    from sportsassets.analytics import mirror_live_rules as rules
    assert rules.MIRROR_VENUE_CALLS_PER_TICK == 80 and rules.MIRROR_MAX_ORDER_OPS_PER_TICK == 20
    fast_ticks = int(30 / ml.FAST_TICK_MIN_S)
    assert fast_ticks * ml.FAST_TICK_MAX == 75 >= 30, "every wake of the burst is read by a fast tick"
    assert 30 * 4 == 120 > ml.VENUE_CALLS_PER_TICK, "the venue rail, not the burst, bounds the fast ticks' calls"
    src = inspect.getsource(ml._fast_tick)
    assert "t.venue_calls + t.fast_calls >= int(VENUE_CALLS_PER_TICK)" in src
    slot = inspect.getsource(ml._op_slot)
    assert "if not exit and t.ops + t.ops_pending >= rules.MIRROR_MAX_ORDER_OPS_PER_TICK" in slot


# --------------------------------------------- Q7: the fixture, the loop

def test_fold2_q7_a_contended_lock_binds_to_its_loop_which_is_why_every_file_takes_the_fresh_pair():
    """Python 3.11: a Lock's first CONTENDED acquire binds it to the
    running loop; a wait on another loop raises. asyncio.run makes a
    loop per test, so a lock contended in one test would break the
    next -- the autouse fixture hands each test fresh locks, and every
    file that drives a tick imports it (`_armed`). Production: one loop
    (all.py supervises mirror_live.main as a coroutine on it) and no
    thread or executor ever awaits tick_once / fast_tick_once."""
    lk = asyncio.Lock()

    async def _contend():
        await lk.acquire()
        w = asyncio.create_task(lk.acquire())
        await asyncio.sleep(0)
        lk.release()
        await w
        lk.release()
    asyncio.run(_contend())

    async def _other_loop():
        await lk.acquire()
        w = asyncio.create_task(lk.acquire())
        await asyncio.sleep(0)
        try:
            lk.release()
            await w
        except RuntimeError as exc:
            return str(exc)
        return None
    msg = asyncio.run(_other_loop())
    assert msg and "different event loop" in msg, msg
    for f in ("test_e9_fast_path.py", "test_e9_review_pins.py", "test_l2_review_pins.py",
              "test_mirror_live_day_cap.py", "test_e6_tick_budget.py", "test_e7_review_pins.py"):
        assert "_armed" in (TESTS / f).read_text(), f
    fx = inspect.getsource(__import__("tests.test_mirror_live_worker", fromlist=["_armed"])._armed)
    assert 'monkeypatch.setattr(ml, "_TICK_LOCK", asyncio.Lock())' in fx
    assert 'monkeypatch.setattr(ml, "_FAST_LOCK", asyncio.Lock(), raising=False)' in fx
    assert 'monkeypatch.setattr(ml, "_fast_holding", False, raising=False)' in fx
    allpy = (TESTS.parents[1] / "backend" / "sportsassets" / "workers" / "all.py").read_text()
    assert '("mirror_live", mirror_live.main)' in allpy
    src = inspect.getsource(ml)
    assert "run_in_executor" not in src and "new_event_loop" not in src and "run_coroutine_threadsafe" not in src
    assert "to_thread(tick_once" not in src and "to_thread(fast_tick_once" not in src
