"""FILL lane 7 (2026-09-09): the full tick stamps its clock AFTER it holds
the lock, so a rest the fast tick placed while the full tick waited is
never 'future' to it (docs/mirror-coverage.md section 51).

THE ROW. The one requote of the 22:44Z tick (tick_2245.txt 319:
`requotes` 1) was the clock's: the tick's beat 22:44:43.68 (336) less
tick_s 38.0 (331) = a start at 22:44:05.68; order 4731 was placed at
22:44:07.64 by the fast tick (`fast: n 1, placed 1`, 320) -- 1.96 s
AFTER the full tick's stamp -- and that full tick, which had waited 2.3 s
on the fast tick's hold (`wall.fast_wait` 2.3, 320), read the rest's age
as -1.96 s: rules.rest_decision's `age < 0 -> ("replace", {"cause":
"future"})`, the cancel `replace`, and 4732 at the same 6 @0.41 (393-394).

THE RULE. tick_once stamps `now = time.time()` inside `async with
_TICK_LOCK:` (the real-clock path, `now_ts` None) -- once it holds the
lock no placement it can see is later than its clock. `started =
time.monotonic()` stays before the lock, so `tick_s` keeps counting the
wait. A caller's clock (`now_ts`) is used as given, byte for byte.
fast_tick_once is not touched: it stamps its own `now` BEFORE its lock
(`_fast_holding` raised once the lock is held), and that clock never
reaches rest_decision (the fast gate names a book with an order open
`order_open`), so it cannot trip `future`; the review's M16b pin below
also holds the real-clock path with no wait to the wall clock at the
lock, never the last fast tick's end (`_fast_last_at`).
rest_decision's `future` clause is not touched: a row genuinely placed
after `now` still replaces. No rail, no census name, no plan field, no
decision word, no migration.

Driven on test_e9_fast_path.py's `_walk` fixture and the worker file's
fakes (its autouse rails are imported), with the worker's `time` faked so
the two clocks are the row's: the fast tick's placement at T + 1.96 s,
the full tick's stamp at T before the wait and T + 2.3 s after it.
"""
import asyncio
import inspect
import pathlib
import re
import time as _time

from sportsassets.analytics import mirror as mi
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests.test_e9_fast_path import _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, NOW, SLUG, _Http, _Venue, _armed, _cancels, _census, _places, _pool, _run,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
PLACED_AFTER_S = 1.96       # 4731's placed_at 22:44:07.64 - the tick's start 22:44:05.68 (tick_2245 393 / 336 / 331)
FAST_WAIT_S = 2.3           # the full tick's wait on the fast tick's hold (tick_2245 320: wall.fast_wait)
STAMP = "now = time.time() if now_ts is None else float(now_ts)"


class _Clock:
    """The worker's `time` with a controlled wall clock: `time()` reads
    `now` (set by the test), `monotonic()` is the real one plus `skew`
    (advanced by the test for the wait), everything else the module's."""

    def __init__(self, now):
        self.now, self.skew = float(now), 0.0

    def time(self):
        return self.now

    def monotonic(self):
        return _time.monotonic() + self.skew

    def __getattr__(self, name):
        return getattr(_time, name)


def _rest(wire=0.47, qty=100, leaves=None, placed=1000.0, side=rules.BUY, intent=None):
    """test_e18_rest_life.py's `_rest`, verbatim."""
    return rules.OpenOrder(side, wire, qty, qty if leaves is None else leaves, placed, intent)


# ------------------------------------------------------------ the rule

def test_c7_rest_decision_reads_book_826s_two_clocks_future_at_t_same_at_t_plus_the_wait():
    """The rule's reading of the row at each clock: 4731 (6 @0.41, placed
    T + 1.96) against a full tick stamped at T is `future`; against one
    stamped after its 2.3 s wait it is `same` -- kept, no cancel."""
    t0 = 1000.0
    p = mi.Plan(rules.BUY, 6, 0.41, "x")
    rest = _rest(wire=0.41, qty=6, placed=t0 + PLACED_AFTER_S)
    assert rules.rest_decision(rest, p, t0, wire=0.41) == ("replace", {"cause": "future"})
    assert rules.rest_decision(rest, p, t0 + FAST_WAIT_S, wire=0.41) == ("keep", {"cause": "same"})
    assert rules.replace_decision("future") == "replace_unread"


def test_c7_test_e18s_future_pin_stands_byte_for_byte():
    """test_e18_rest_life.py 144: `_rest(placed=1030.0)` against now 1020.0."""
    p = mi.Plan(rules.BUY, 100, 0.48, "x")
    assert rules.rest_decision(_rest(placed=1030.0), p, 1020.0, wire=0.47) == ("replace", {"cause": "future"})
    src = inspect.getsource(rules.rest_decision)
    assert '    if age < 0:\n        return "replace", {"cause": "future"}\n' in src, "the clause, untouched"


# ---------------------------------------------------------- the worker

def _drive(p, v, http, clock, full_now_ts):
    """The 22:44Z shape: a fast tick holds the lock and places at
    T + 1.96 while a full tick arrives at T and waits; the fast tick lets
    go 2.3 s later. Returns (fast stats, full stats, placed_ts)."""
    orig = ml._fast_book

    async def _scenario():
        reached, release = asyncio.Event(), asyncio.Event()

        async def _held(t, book):
            # the fast tick, the lock held, about to place: the full
            # tick arrives now and waits on `_fast_holding`
            reached.set()
            await release.wait()
            return await orig(t, book)

        ml._fast_book = _held
        try:
            p.clock = NOW + PLACED_AFTER_S          # the INSERT's now(): the fast tick's placed_at
            fast = asyncio.create_task(ml.fast_tick_once(p, v, http, cids=[CID], now_ts=NOW + PLACED_AFTER_S))
            await reached.wait()
            assert ml._fast_holding is True and ml._TICK_LOCK.locked()
            clock.now = NOW                          # the full tick's real clock at its arrival: T
            full = asyncio.create_task(ml.tick_once(p, v, http, now_ts=full_now_ts))
            await asyncio.sleep(0)                   # it runs to the lock and waits there
            assert not full.done() and not fast.done(), "waited on the fast tick's hold, not skipped"
            clock.now, clock.skew = NOW + FAST_WAIT_S, FAST_WAIT_S     # 2.3 s of waiting pass
            release.set()
            fs = await fast
            st = await full
            return fs, st
        finally:
            ml._fast_book = orig
    fs, st = _run(_scenario())
    oids = sorted(p.orders)
    assert oids, "the fast tick placed"
    return fs, st, p.orders[oids[0]]["placed_ts"]


def test_c7_the_full_tick_that_waited_on_the_fast_ticks_hold_keeps_the_rest_it_placed_no_cancel_requotes_0():
    """After: the full tick's `now` is read once it holds the lock
    (T + 2.3 >= the placement at T + 1.96): the rest is `keep` cause
    `same` -- `open_order_pending`, no cancel, `requotes` 0, one
    placement -- and `tick_s` still counts the 2.3 s wait."""
    p = _pool()
    b = p.add_book(ledger=0)                          # his 300 held, ours 0: the fast tick's increase
    v, http = _Venue(), _Http()
    _walk()
    clock = _Clock(NOW)
    ml.time = clock
    try:
        fs, st, placed_ts = _drive(p, v, http, clock, None)
    finally:
        ml.time = _time
    assert fs["fast"]["placed"] == 1 and placed_ts == NOW + PLACED_AFTER_S
    assert st["status"] != "overlap" and _census(st, "fast_tick_placed") == 1
    assert ml._last_tick_at == NOW + FAST_WAIT_S and ml._last_tick_at >= placed_ts, "stamped after the wait"
    assert _cancels(v) == [] and st["requotes"] == 0
    assert len(_places(v)) == 1 and _places(v)[0][1] == SLUG
    # the one rest_placed is the fast tick's own, folded into this census (E9); the full tick placed nothing
    assert _census(st, "open_order_pending") == 1 and _census(st, "rest_placed") == 1
    assert fs["fast"]["placed"] == 1 and _census(fs, "rest_placed") == 1
    oid = b["open_order_id"]
    assert oid is not None and p.orders[oid]["state"] == "open" and p.orders[oid]["reason"] != "replace"
    assert not any(r["what"] == "cancel" for r in st["recent"])
    assert st["tick_s"] >= FAST_WAIT_S, "the wait on the lock is still the tick's time"


def test_c7_a_callers_clock_is_used_byte_for_byte_and_reads_the_row_as_today_replace_future():
    """`now_ts` given: the caller's clock stands whatever the wait --
    the injected-clock tests' contract -- and it reproduces today's row:
    stamped at T against the placement at T + 1.96, the rest is
    `future`, cancelled `replace`, re-placed at the same wire and
    quantity, `requotes` 1 (4731 -> 4732, tick_2245 393-394)."""
    p = _pool()
    b = p.add_book(ledger=0)
    v, http = _Venue(), _Http()
    _walk()
    clock = _Clock(NOW)
    ml.time = clock
    try:
        fs, st, placed_ts = _drive(p, v, http, clock, NOW)
    finally:
        ml.time = _time
    assert fs["fast"]["placed"] == 1 and placed_ts == NOW + PLACED_AFTER_S
    assert ml._last_tick_at == NOW, "the caller's clock, not the wall clock after the wait"
    assert st["requotes"] == 1
    cancels = _cancels(v)
    assert len(cancels) == 1
    pl = _places(v)
    assert len(pl) == 2 and pl[1][1:4] == pl[0][1:4], "the same market, wire and quantity re-placed"
    first, second = (p.orders[i] for i in sorted(p.orders))
    assert first["state"] == "cancelled" and first["reason"] == "replace"
    assert second["state"] == "open" and (second["wire"], second["qty"]) == (first["wire"], first["qty"])
    assert b["open_order_id"] == second["id"]
    assert [r["reason"] for r in st["recent"] if r["what"] == "cancel"] == ["replace"]


def test_c7_every_injected_clock_tick_is_the_callers_clock_exactly():
    """The plain fixture path: `now_ts` given, no wait -- the tick's
    clock is the float handed in."""
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    p.clock = NOW + 5.0
    st = _run(ml.tick_once(p, v, _Http(), now_ts=NOW + 5.0))
    assert st["status"] != "overlap" and ml._last_tick_at == NOW + 5.0
    st2 = _run(ml.tick_once(p, v, _Http(), now_ts=int(NOW)))
    assert ml._last_tick_at == float(int(NOW)) and isinstance(ml._last_tick_at, float)


# ------------------------------------------------------------- the pins

def test_c7_the_stamp_sits_inside_the_lock_started_before_it_and_the_fast_ticks_stamp_is_untouched():
    src = inspect.getsource(ml.tick_once)
    assert src.count(STAMP) == 1
    lock, stamp = src.index("    async with _TICK_LOCK:\n"), src.index(STAMP)
    assert src.index("started = time.monotonic()") < src.index("if _TICK_LOCK.locked() and not _fast_holding:") < lock
    assert lock < stamp < src.index("t = _Tick(pool=pool, pmus=pmus, http=http, now=now, stats=stats, started=started)")
    assert "now" not in src[src.index("started = time.monotonic()"):lock].replace("# the real clock: `now` may be the caller's", ""), \
        "nothing before the lock reads the tick's clock"
    assert 'stats["tick_s"] = round(time.monotonic() - started, 1)' in src
    # the fast tick: its stamp before its lock, under `_fast_holding` once held -- as before
    fsrc = inspect.getsource(ml.fast_tick_once)
    assert fsrc.count(STAMP) == 1
    assert fsrc.index(STAMP) < fsrc.index("started = time.monotonic()") < fsrc.index("async with _FAST_LOCK, _TICK_LOCK:")
    assert fsrc.index("async with _FAST_LOCK, _TICK_LOCK:") < fsrc.index("_fast_holding = True")


def test_c7_no_rail_no_name_no_field_no_word_no_migration():
    src = inspect.getsource(ml)
    assert "FILL lane 7" in inspect.getsource(ml.tick_once)
    assert "future_clock" not in src and "lane_7" not in src
    migs = sorted(p.name for p in (ROOT / "backend" / "migrations").glob("*.sql"))
    # no migration from this lane: the newest file is lane 9's 061 (landed after), none names the clock
    assert migs[-1] == "064_run833_stream_channels.sql" and not [m for m in migs if "tick_clock" in m or "lane_7" in m or "now_ts" in m]  # re-pinned 2026-09-12 (run 83.3): run 83.3's 064 is the newest; this lane still adds none
    assert "future" not in ml.CENSUS_KEYS and "clock" not in ml.CENSUS_KEYS


def test_c7_the_docs_name_the_rule():
    doc = (ROOT / "docs" / "mirror-coverage.md").read_text()
    m = re.search(r"^## (\d+)\. .*\(2026-09-09, FILL lane 7\)", doc, re.M)
    assert m, "the FILL lane 7 section header"
    section = doc[m.start():]
    for k in ("tick_once", "_TICK_LOCK", "_fast_holding", "now_ts", "rest_decision", "future", "tick_s",
              "fast_tick_once", "requotes", "4731", "4732", "test_fill_c7_clock.py", "future_clock", "same_quote"):
        assert k in section, k
    assert "$0" in section


def test_c7_the_real_clock_path_with_no_wait_stamps_the_wall_clock_at_the_lock_never_the_fast_ticks_end():
    """The review's surviving mutant M16b: the real-clock path (`now_ts`
    None) with NOTHING to wait on. The tick's clock is the wall clock read
    at the lock -- never the last fast tick's end (`_fast_last_at`, behind
    or ahead of the wall by 100 s) -- and a rest placed one second before
    it is kept (no cancel, `requotes` 0, the row still open, held
    `resting_above_level` under the ask): a clock 100 s behind would read
    the rest `future` and cancel it `replace`."""
    for fast_end in (NOW - 100.0, NOW + 100.0):
        p = _pool()
        b = p.add_book(ledger=0)
        o = p.add_order(b, placed_ts=NOW + 8.0)      # placed one second before the tick's clock
        v = _Venue(ask=0.32)
        v.rest("oid-1")
        clock = _Clock(NOW + 9.0)
        p.clock = NOW + 9.0
        saved = ml._fast_last_at
        ml.time, ml._fast_last_at = clock, fast_end
        try:
            st = _run(ml.tick_once(p, v, _Http()))
        finally:
            ml.time, ml._fast_last_at = _time, saved
        assert st["status"] != "overlap" and ml._last_tick_at == NOW + 9.0 == clock.now, fast_end
        assert _cancels(v) == [] and not _places(v) and st["requotes"] == 0, fast_end
        assert p.orders[o["id"]]["state"] == "open" and b["open_order_id"] == o["id"]
        assert _census(st, "resting_above_level") == 1 and not any(r["what"] == "cancel" for r in st["recent"])
