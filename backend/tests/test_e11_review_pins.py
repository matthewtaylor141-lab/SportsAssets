"""E11 review pins (2026-09-08): the venue gate's priority lane, the
widened quiet rotation -- what the builder's harness could not see.

The builder's harness runs REAL threads on a FAKE clock whose sleep
advances the clock by the gap: two claimants sleeping "at once" still
land one gap apart because each sleep moves the clock, so a gate that
let go of `_busy` before the sleep would pass its 64-claim pin (the
mutant M07 of the review). The gap pin here runs on the REAL clock
(short gaps, real sleeps, real threads): every consecutive claim
instant is >= the gap, in waves and interleaved, and >= 2 x under the
circuit. The rest: a holder interrupted inside its sleep; the bounded
executor (no deadlock: the holder's release depends on time alone);
the context reaching sibling tasks or not; the thirteenth claim after a
normal's arrival; a quiet book whose venue halts; the stale mark's one
money reader (the game room's cost fallback); the E6 hot classes each
making a book hot the same tick.
"""
import asyncio
import concurrent.futures
import inspect
import pathlib
import threading
import time

import pytest

import sportsassets
from sportsassets import venue_pace
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import price_path
from tests.test_e11_venue_gate import (  # noqa: F401 -- the fake clock and its helpers
    _claim, _finish, _hold, _instants, _lane_delta, _order, _queue_one, _until, clock,
)
from tests.test_e6_tick_budget import _bbos
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    NOW, SLUG, _Venue, _armed, _census, _pool, _tick,
)

PKG = pathlib.Path(sportsassets.__file__).resolve().parent
GAP = venue_pace.MIN_GAP_S
HALTED = "MARKET_STATE_HALTED"
# the worker file's autouse rails stub time.sleep for every test: the real
# clock's functions are taken at import, before any fixture runs
_REAL_SLEEP, _REAL_MONOTONIC = time.sleep, time.monotonic


class _RealClock:
    """venue_pace's `time` on the REAL clock, remembering per thread the
    last monotonic() it read inside pace() -- after its sleep, i.e. the
    claim instant `_last` took -- so the instants are the gate's own."""

    def __init__(self):
        self.by_thread: dict = {}
        self.sleeps: list = []

    def monotonic(self):
        v = _REAL_MONOTONIC()
        self.by_thread[threading.get_ident()] = v
        return v

    def sleep(self, d):
        self.sleeps.append(float(d))
        _REAL_SLEEP(d)


@pytest.fixture
def real(monkeypatch):
    c = _RealClock()
    monkeypatch.setattr(venue_pace, "time", c)
    monkeypatch.setattr(venue_pace, "_last", _REAL_MONOTONIC())
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    monkeypatch.setattr(venue_pace, "_run", 0)
    yield c
    assert venue_pace.waiting() == (0, 0) and not venue_pace._busy


def _real_claims(rc, gap, plan, stagger=0.0):
    """`plan`: [(name, priority, claims)] run as real threads at once;
    returns the sorted claim instants and the per-thread records."""
    served: list = []
    lock = threading.Lock()

    def run(name, prio, n):
        for j in range(n):
            if stagger:
                _REAL_SLEEP(stagger)
            w = venue_pace.pace(gap, priority_claim=prio)
            with lock:
                served.append((f"{name}-{j}", rc.by_thread[threading.get_ident()], w))
    ths = [threading.Thread(target=run, args=p, daemon=True) for p in plan]
    for th in ths:
        th.start()
    for th in ths:
        th.join(20.0)
    assert not any(th.is_alive() for th in ths), "the harness timed out"
    return served


def _gaps(served):
    at = sorted(t for _, t, _ in served)
    return [b - a for a, b in zip(at, at[1:])]


# ------------------------------------------------------------ Q1: the gap

def test_review_q1_real_threads_real_clock_the_gap_holds_at_once_in_waves_and_interleaved(real):
    gap = 0.02
    # at once: 4 priority + 4 normal threads, 4 claims each
    served = _real_claims(real, gap, [(f"p{k}", True, 4) for k in range(4)] + [(f"n{k}", False, 4) for k in range(4)])
    assert len(served) == 32 and min(_gaps(served)) >= gap - 1e-4, min(_gaps(served))
    # in waves: a second wave arriving while the first is still draining
    served2 = _real_claims(real, gap, [("p", True, 3), ("n", False, 3)])
    late = _real_claims(real, gap, [("q", True, 3), ("m", False, 3)], stagger=0.005)
    both = served2 + late
    assert min(_gaps(both)) >= gap - 1e-4
    # interleaved: a priority arriving every few ms while normals stream
    served3 = _real_claims(real, gap, [("n", False, 6), ("p", True, 6)], stagger=0.003)
    assert min(_gaps(served3)) >= gap - 1e-4
    assert venue_pace._run == 0


def test_review_q1_under_the_circuit_both_lanes_keep_twice_the_gap_on_the_real_clock(real):
    gap = 0.015
    venue_pace.penalize(_REAL_MONOTONIC())
    served = _real_claims(real, gap, [("p", True, 3), ("n", False, 3), ("q", True, 3)])
    assert len(served) == 9 and min(_gaps(served)) >= 2 * gap - 1e-4, min(_gaps(served))
    # every sleep is the DOUBLED gap less the microseconds since the last record: never the single gap
    assert len(real.sleeps) == 9 and min(real.sleeps) > gap + 1e-3 and max(real.sleeps) <= 2 * gap + 1e-4, real.sleeps


def test_review_q1_a_holder_interrupted_inside_its_sleep_leaves_the_gate_consistent(clock, monkeypatch):  # noqa: F811
    """KeyboardInterrupt (a BaseException) lands in the holder's sleep:
    the finally frees the gate, records the instant, and the next claim
    -- from either lane -- is one gap after it; nothing stays queued."""
    served: list = []
    real_sleep = clock.sleep
    state = {"fired": False}

    def sleep(d):
        if threading.current_thread().name == "h" and not state["fired"]:
            state["fired"] = True
            raise KeyboardInterrupt()
        real_sleep(d)
    monkeypatch.setattr(clock, "sleep", sleep)

    def holder():
        try:
            venue_pace.pace(GAP, priority_claim=False)
        except KeyboardInterrupt:
            served.append(("h", None, None))
    th = threading.Thread(target=holder, name="h", daemon=True)
    th.start()
    th.join(5.0)
    assert ("h", None, None) in served and not venue_pace._busy and venue_pace.waiting() == (0, 0)
    t_int = venue_pace._last
    p = _claim(clock, "p", True, served)
    p.join(5.0)
    n = _claim(clock, "n", False, served)
    n.join(5.0)
    at = _instants(served)
    assert at == [pytest.approx(t_int + GAP), pytest.approx(t_int + 2 * GAP)]
    assert venue_pace.waiting() == (0, 0) and not venue_pace._busy


# ------------------------------------------------------ Q2: no deadlock

def test_review_q2_a_two_worker_executor_the_tick_and_a_sibling_together_never_deadlock(real):
    """asyncio.to_thread workers (the walk's six reads, the shadow's, the
    sampler's) all block in the gate on a default executor of TWO
    threads; the holder sleeps on the clock alone, never on the loop, so
    every claim completes -- and the lanes are the tasks' own."""
    gap = 0.005
    before = venue_pace.lane_stats()

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=2))
        go = asyncio.Event()

        async def shadow():
            await go.wait()
            for _ in range(4):
                await asyncio.to_thread(venue_pace.pace, gap)

        async def tick():
            with venue_pace.priority_claims():
                go.set()
                await asyncio.gather(*(asyncio.to_thread(venue_pace.pace, gap) for _ in range(6)))
                # the shadow's own function from the tick's worker thread
                await asyncio.to_thread(venue_pace.pace, gap, False)

        sh = asyncio.create_task(shadow())          # created before the context: no lane
        await asyncio.wait_for(asyncio.gather(sh, tick()), 10.0)
    asyncio.run(main())
    d = _lane_delta(before)
    assert d["priority"]["claims"] == 7 and d["normal"]["claims"] == 4, d
    src = inspect.getsource(venue_pace)
    assert "import asyncio" not in src and "await" not in inspect.getsource(venue_pace.pace), "the gate never touches the loop"


# --------------------------------------------------------- Q3: starvation

def test_review_q3_a_normal_arriving_mid_sleep_behind_a_priority_holder_is_the_thirteenth_claim_after_it(clock):  # noqa: F811
    served: list = []
    h = _hold(clock, served, "h", priority=True)
    n0 = _queue_one(clock, "n0", False, served)
    ps = [_queue_one(clock, f"p{i}", True, served) for i in range(20)]
    _finish(clock, [h, n0, *ps])
    order = _order(served)
    assert order == ["h"] + [f"p{i}" for i in range(12)] + ["n0"] + [f"p{i}" for i in range(12, 20)]
    assert order.index("n0") - order.index("h") == 13, "twelve priority claims after its arrival, then it"
    assert venue_pace._run == 0
    # neither normal loop has a deadline on its claim: the shadow abandons on EMPTY reads only,
    # the sampler re-checks its window BEFORE the claim (its wait is not a miss)
    assert ms.MISS_STREAK_ABANDON == 3 and price_path.MISS_STREAK_ABANDON == 3
    src = inspect.getsource(price_path.sample_once)
    assert src.index('float(d.get("deadline")') < src.index("await _take(pool, pmus, d)")
    assert price_path.TOL_S == 10.0 > (venue_pace.PACE_PRIORITY_BURST + 2) * GAP


def test_review_q3_the_bounds_normal_resets_the_run_so_a_second_waiting_normal_does_not_follow_it(clock):  # noqa: F811
    """Two normals and fifteen priorities behind a holder: twelve, the
    FIRST normal, then priority again (the run reset by the normal
    served) -- the second normal waits its own twelve, not one gap. The
    builder's bound pin queues one normal, after which the empty normal
    lane zeroes the run whatever the normal's claim did (the review's
    mutant M10 survived it)."""
    served: list = []
    h = _hold(clock, served)
    n0 = _queue_one(clock, "n0", False, served)
    n1 = _queue_one(clock, "n1", False, served)
    ps = [_queue_one(clock, f"p{i}", True, served) for i in range(15)]
    assert venue_pace.waiting() == (2, 15)
    _finish(clock, [h, n0, n1, *ps])
    assert _order(served) == ["h"] + [f"p{i}" for i in range(12)] + ["n0", "p12", "p13", "p14", "n1"]
    assert venue_pace._run == 0


# ------------------------------------------------------ Q4: who is priority

def test_review_q4_the_context_reaches_the_ticks_own_tasks_and_threads_and_no_sibling(clock):  # noqa: F811
    """A sibling task created before the tick, or by another task during
    it, claims normal; a task the tick's own task creates inside the
    context (the walk's game tasks, the placement wrapper's
    ensure_future) claims priority -- even after the context exits
    (an orphaned placement stays the mirror's own request)."""
    before = venue_pace.lane_stats()
    seen: dict = {}

    async def main():
        inside = asyncio.Event()
        done = asyncio.Event()

        async def sibling():
            await inside.wait()
            seen["sibling"] = venue_pace._priority_ctx.get()
            await asyncio.to_thread(venue_pace.pace, GAP)             # normal
            child = asyncio.create_task(asyncio.to_thread(venue_pace.pace, GAP))
            await child                                                  # normal: its parent has no lane
            done.set()

        async def tick():
            orphan = None
            with venue_pace.priority_claims():
                inside.set()
                await done.wait()
                await asyncio.to_thread(venue_pace.pace, GAP)             # priority
                orphan = asyncio.ensure_future(asyncio.to_thread(venue_pace.pace, GAP))
                own = asyncio.create_task(asyncio.to_thread(venue_pace.pace, GAP))
                await own                                                # priority
            seen["after"] = venue_pace._priority_ctx.get()
            await orphan                                                 # priority: copied at creation
            await asyncio.to_thread(venue_pace.pace, GAP)                 # normal again

        sib = asyncio.create_task(sibling())
        await asyncio.wait_for(asyncio.gather(sib, tick()), 10.0)
    asyncio.run(main())
    assert seen == {"sibling": False, "after": False}
    d = _lane_delta(before)
    assert d["normal"]["claims"] == 3 and d["priority"]["claims"] == 3, d


def test_review_q4_no_venue_claim_runs_on_a_thread_the_context_cannot_reach():
    """Every venue claim in the loop modules runs in a worker thread
    started by asyncio.to_thread (which copies the context); none by a
    bare Thread or run_in_executor (which would not)."""
    for rel in ("workers/mirror_live.py", "workers/mirror_shadow.py", "workers/price_path.py", "pmus.py"):
        text = (PKG / rel).read_text()
        assert "run_in_executor" not in text and "threading.Thread(" not in text and "Thread(target" not in text, rel
        assert "ThreadPoolExecutor" not in text, rel
    assert "asyncio.to_thread(" in (PKG / "workers/mirror_live.py").read_text()
    # the E9 fast task is created by whoever wakes (the poller's task, or a tick's): either way the
    # fast tick enters the context itself
    assert "with venue_pace.priority_claims():" in inspect.getsource(ml.fast_tick_once)


# -------------------------------------------------------- Q5: the rotation

def test_review_q5_a_quiet_book_whose_venue_halts_is_read_on_its_turn_and_nothing_moves_meanwhile():
    """On target with nothing open, the venue HALTS on tick 3: ticks 3-9
    skip it (no venue call, no order path), tick 10 reads it -- no_mark,
    hot by row from then (the last read not on target), read every tick.
    Nothing was placed or cancelled on the stale quote."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    st1 = _tick(p, v)
    assert _bbos(v) == [SLUG] and _census(st1, "on_target") == 1
    halted = _Venue(held={SLUG: 300}, states={SLUG: HALTED})
    for seq in range(2, 10):
        v_now = v if seq == 2 else halted
        v_now.calls.clear()
        st = _tick(p, v_now, now=NOW + 30 * (seq - 1))
        assert not _bbos(v_now) and _census(st, "book_quiet_skipped") == 1, seq
        assert not [c for c in v_now.calls if c[0] in ("place", "cancel")] and st["ops"] == 0
        assert b["last_reason"] == "book_quiet_skipped" and b["last_plan"]["read_on"] == 10
    for seq in (10, 11, 12):
        halted.calls.clear()
        st = _tick(p, halted, now=NOW + 30 * (seq - 1))
        assert _bbos(halted) == [SLUG] and _census(st, "no_mark") == 1 and b["last_reason"] == "no_mark", seq


def test_review_q5_the_stale_mark_has_one_money_reader_the_game_rooms_cost_fallback():
    """The skip lands the carried mark on t.marks (mirror_live :6210);
    _held_exposure reads it for a SIBLING's game room -- but
    book_exposure prices the held leg at COST and takes the mark only
    when the cost is unreadable. So a quiet book's stale mark moves a
    sibling's room only on a book with no readable avg_cost."""
    assert rules.book_exposure(100, 0.40, 0.90) == 40.0
    assert rules.book_exposure(100, None, 0.90) == 90.0
    assert rules.book_exposure(100, "x", 0.90) == 90.0
    src = inspect.getsource(ml._held_exposure)
    assert "t.marks.get(book" in src and "rules.book_exposure(" in src
    assert "t.marks[book[\"id\"]] = float(plan[\"mark\"])" in inspect.getsource(ml._tick_book)


@pytest.mark.parametrize("cls", ["never_read", "not_on_target", "not_live", "order_open", "exit_plan", "woken", "his_fill", "other_hand"])
def test_review_q5_each_hot_class_makes_a_quiet_book_hot_the_same_tick(cls):
    """After a quiet read (tick 1) and a skip (tick 2), the class lands
    before tick 3 and tick 3 READS the book -- the rotation's turn (10)
    is not waited for."""
    p = _pool()
    b = p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    v.calls.clear()
    _tick(p, v, now=NOW + 30)
    assert not _bbos(v) and b["last_reason"] == "book_quiet_skipped"
    t3 = NOW + 60
    if cls == "never_read":
        ml._quiet_memo.pop(b["id"])
    elif cls == "not_on_target":
        ml._quiet_memo[b["id"]]["quiet"] = False
    elif cls == "not_live":
        b["state"] = "frozen"
    elif cls == "order_open":
        b["open_order_id"] = "o-1"
    elif cls == "exit_plan":
        b["last_reason"] = "exit_take"
    elif cls == "woken":
        ml._WOKEN.add(b["condition_id"])
    elif cls == "his_fill":
        from tests.test_mirror_live_worker import M, _fill
        p.fills = p.fills + [_fill(M, "BUY", 50.0, 0.31, t3 - 10)]
    elif cls == "other_hand":
        b["last_plan"] = {**b["last_plan"], "at": b["last_plan"]["at"] - 5.0}
    v.calls.clear()
    st = _tick(p, v, now=t3)
    assert SLUG in _bbos(v) and _census(st, "book_quiet_skipped") == 0, (cls, st["census"])


def test_review_q5_the_env_lowers_the_rotation_and_the_module_reads_the_capped_value(monkeypatch):
    for env, want in (("2", 2), ("9", 9), ("27", 9), ("1e9", 9), ("0.5", 1)):
        monkeypatch.setenv("MIRROR_QUIET_EVERY_TICKS", env)
        assert int(rules.capped_env("MIRROR_QUIET_EVERY_TICKS", 9, floor=1)) == want, env
    assert ml.QUIET_EVERY_TICKS == 9 and ml.DEFERRED_MIN_PER_TICK == 10


# --------------------------------------------------------- Q6: the measure

def test_review_q6_the_gate_block_is_the_priority_lanes_delta_per_claim_and_the_wall_keys_stand():
    from tests.test_e10_priority_lane import WALL_KEYS
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert st["short"]["gate"] == {"wait": 0.0, "claims": 0} and tuple(st["short"]["wall"]) == WALL_KEYS
    src = inspect.getsource(ml._gate_block)
    assert 'now["wait"] - float(g0.get("wait")' in src and 'now["claims"] - int(g0.get("claims")' in src
    assert 'lane_stats()["priority"]' in inspect.getsource(ml._gate_snapshot)
    # the tick's gate0 is taken INSIDE _TICK_LOCK (a fast tick's claims are not this tick's)
    once = inspect.getsource(ml.tick_once)
    assert once.index("async with _TICK_LOCK:") < once.index("t = _Tick(")
    fast = inspect.getsource(ml.fast_tick_once)
    assert fast.index("async with _FAST_LOCK, _TICK_LOCK:") < fast.index("t = _Tick(")
    # the lane's `wait` is call-to-claim, summed per claim (books_data_wait's reading), by source
    assert 'stat["wait"] += max(0.0, _last - called)' in inspect.getsource(venue_pace.pace)
