"""FILL lane 14 (2026-09-09): the tick's record -- the tick ring, the fast
tick's prelude, the `short.speed` block, the hourly's tick-ring section
(docs section 58; FILL_plan_C lane 14, P lane 9 (iii)-(iv)).

THE ROWS. The heartbeat carries ONE tick: tick_s 38.0 at 15 books with
timing walk 0.3 / orders 1.0 / books 11.3 / candidates 16.2 / venue_calls
52 / cand_cap 36 and wall fast_wait 2.3 / fast_work 8.4, fast n 1
(tick_2245.txt 320 / 331 / 298); 12.4 s at 22:29Z with candidates 0.8 and
fast_work 2.4 for the same one-market / four-call / one-placement shape
(tick_2229.txt 319 / 330); 76.9 s at 54-64 books with candidates 32.5 /
books 31.7 / fast_wait 43.6 / fast_work 8.0 / venue_calls 106 / cand_cap
10 (hourly_2030.txt 415 / 426 / 392); 51.9 s at 17:06:36Z (h1707.txt 33
/ 44 / 10). The distribution the owner's "as fast as his" line is judged
on was in no file, and the fast tick's 6 extra seconds (8.4 vs 2.4) sat
nowhere anyone could size.

THE RULE, EXACTLY. Nothing in the order path changes (hashed below). The
full tick appends [at, tick_s, walk, orders, books, candidates,
books_live, venue_calls, cand_cap, cand_reads, yielded, fast_n,
fast_wait, fast_work, fast_prelude] to `_tick_ring` (deque, maxlen
TICK_RING_MAX = 240, a constant) at the END of tick_once's `finally`,
read off the blocks the tick just built (0 / 0.0 where a block is
missing), and persists it under ingestion_state `mirror_tick_ring` the
way the candidate memo is persisted: never before the boot read, at most
once per TERMINAL_MEMO_WRITE_S, a failed write logged and retried, the
ring kept in memory, NO boot read (a deploy starts it empty). The fast
tick stamps t.timing["fast_prelude"] just before its per-market loop
(the seconds from the lock's acquire, handed in on t.fast_acquired); a
fast tick that never reaches the loop is prelude alone (its whole work).
`short.speed` = {cand_yielded, fast_prelude, fast_walk, ring} on both
tick paths, a sibling of `short.wall` / `short.fast` (their keys exactly
as pinned). render-ops: `tick-ring` right after `mirror-tick`, the same
statement in the hourly (TEN presets now), jsonb-guarded so a NULL or
malformed row -- or a number past float8's range (a jsonb number is a
numeric) -- prints nothing and never stops the bundle.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import time as _real_time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.workers import mirror_live as ml
from tests import test_render_ops_hourly as hourly
from tests.test_e6_tick_budget import TIMING_KEYS
from tests.test_e9_fast_path import FAST_KEYS, _fast, _skips, _walk
from tests.test_e10_priority_lane import WALL_KEYS
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, NOW, SLUG, _Http, _Pool, _Venue, _armed, _census, _flat, _places, _pool, _run, _tick,
)
from tests.test_render_ops_fills_missed import World, _labels_and_help, _preset, _stmts

ROOT = Path(__file__).resolve().parents[2]
YML = ROOT / ".github" / "workflows" / "render-ops.yml"
DOCS = ROOT / "docs" / "mirror-coverage.md"
SPEED_KEYS = ("cand_yielded", "fast_prelude", "fast_walk", "ring")
KEY = "mirror_tick_ring"


def _ep(s: str) -> float:
    return round(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp(), 1)


# the three heartbeats above, as the blocks the tick's `finally` builds
def _shape(tick_s, books_live, timing, wall, fast_n, prelude=0.0, **top):
    st = ml._new_stats()
    st.update(tick_s=tick_s, books_live=books_live, **top)
    st["short"] = {"timing": dict(timing), "wall": dict(wall), "fast": {"n": fast_n},
                   "speed": {"cand_yielded": 0, "fast_prelude": prelude, "fast_walk": 0, "ring": 0}}
    return st


T2244_AT = _ep("2026-09-08T22:44:43.7")     # tick_2245.txt 336 (beat_at 22:44:43.684483)
T2229_AT = _ep("2026-09-08T22:29:30.3")     # tick_2229.txt 335 (beat_at 22:29:30.340451)
T2030_AT = _ep("2026-09-08T20:29:53.5")     # hourly_2030.txt 431 (beat_at 20:29:53.521328)
S2244 = _shape(38.0, 15, {"walk": 0.3, "orders": 1.0, "books": 11.3, "candidates": 16.2, "venue_calls": 52, "cand_cap": 36},
               {"fast_wait": 2.3, "fast_work": 8.4}, 1)
S2229 = _shape(12.4, 16, {"walk": 0.4, "orders": 2.1, "books": 5.3, "candidates": 0.8, "venue_calls": 15, "cand_cap": 40},
               {"fast_wait": 7.8, "fast_work": 2.4}, 1)
S2030 = _shape(76.9, 64, {"walk": 0.2, "orders": 3.3, "books": 31.7, "candidates": 32.5, "venue_calls": 106, "cand_cap": 10},
               {"fast_wait": 43.6, "fast_work": 8.0}, 1)
E2244 = [T2244_AT, 38.0, 0.3, 1.0, 11.3, 16.2, 15, 52, 36, 33, 0, 1, 2.3, 8.4, 0.0]
E2229 = [T2229_AT, 12.4, 0.4, 2.1, 5.3, 0.8, 16, 15, 40, 0, 0, 1, 7.8, 2.4, 0.0]   # books_live 16: tick_2229 297
E2030 = [T2030_AT, 76.9, 0.2, 3.3, 31.7, 32.5, 64, 106, 10, 10, 0, 1, 43.6, 8.0, 0.0]


def _t(now, stats, cand_reads=0):
    t = ml._Tick(pool=None, pmus=None, http=None, now=now, stats=stats)
    t.cand_reads = cand_reads
    return t


def _ring_writes(p):
    return [x for x in p.sent if "ml-state-write" in x[1] and x[2][0] == KEY]


def _ring_reads(p):
    return [x for x in p.sent if "SELECT value FROM ingestion_state" in x[1] and x[2][0] == KEY]


@pytest.fixture(autouse=True)
def _ring_reset():
    """Every test starts with an empty ring, no write on the clock and no
    prelude carried, and leaves it so."""
    ml._tick_ring.clear()
    ml._tick_ring_last["at"] = 0.0
    ml._fast_speed["prelude"] = 0.0
    yield
    ml._tick_ring.clear()
    ml._tick_ring_last["at"] = 0.0
    ml._fast_speed["prelude"] = 0.0


# ------------------------------------------------------------ the constants

def test_c14_the_constants_the_key_and_no_knob():
    assert ml.TICK_RING_MAX == 240 and ml._tick_ring.maxlen == 240 and ml._STATE_TICK_RING == KEY
    assert ml.TERMINAL_MEMO_WRITE_S == 60.0, "the candidate memo's cadence, unchanged"
    assert ml.TICK_RING_FIELDS == ("at", "tick_s", "walk", "orders", "books", "candidates", "books_live",
                                   "venue_calls", "cand_cap", "cand_reads", "yielded", "fast_n", "fast_wait",
                                   "fast_work", "fast_prelude"), "the plan's order, by position"
    src = inspect.getsource(ml)
    assert "MIRROR_TICK_RING" not in src and "MIRROR_RING" not in src and "TICK_RING_S" not in src, "no knob of its own"
    assert "TICK_RING_MAX = 240" in src and src.count("TICK_RING_MAX") >= 3
    # the ring's persistence is the candidate memo's shape: the same gate, the same cadence, the same log line
    pr = inspect.getsource(ml._persist_tick_ring)
    assert "if not _terminal_memo_loaded or not _tick_ring:\n        return" in pr
    assert "< TERMINAL_MEMO_WRITE_S:\n        return" in pr and 'log.warning("mirror_live: %s write failed (%s)", _STATE_TICK_RING' in pr
    assert "await _state(" not in pr and "_STATE_CAND_MEMO" not in pr and "_STATE_TERMINAL_MEMO" not in pr
    # NO boot read: neither boot reader names the key, and no reader of it exists in the worker
    assert KEY not in inspect.getsource(ml._load_terminal_memo) and "_STATE_TICK_RING" not in inspect.getsource(ml._load_cand_memo)
    assert src.count("_STATE_TICK_RING") == 5, "the constant, the deque's comment, the persist docstring, the write, the log line"
    assert "await _state(t.pool, _STATE_TICK_RING" not in src and src.count("_write_state(t.pool, _STATE_TICK_RING") == 1


# --------------------------------------------------------- the entry's shape

def test_c14_the_22_44z_22_29z_and_20_30z_heartbeats_read_as_entries_by_position():
    assert ml._tick_ring_entry(_t(T2244_AT, S2244, cand_reads=33), S2244) == E2244   # 33: no_mark 33, tick_2245 300
    assert ml._tick_ring_entry(_t(T2229_AT, S2229), S2229) == E2229
    assert ml._tick_ring_entry(_t(T2030_AT, S2030, cand_reads=10), S2030) == E2030
    assert all(len(e) == len(ml.TICK_RING_FIELDS) == 15 for e in (E2244, E2229, E2030))
    # lane 15's key on the heartbeat reads as yielded 1; its census name on the speed block
    st = _shape(38.0, 15, S2244["short"]["timing"], S2244["short"]["wall"], 1, yielded_tick=True)
    assert ml._tick_ring_entry(_t(T2244_AT, st), st)[10] == 1
    st["census"]["cand_yielded"] = 2
    assert ml._speed_block(_t(T2244_AT, st), 0.0) == {"cand_yielded": 2, "fast_prelude": 0.0, "fast_walk": 0, "ring": 0}
    # anything but the bool True on yielded_tick is 0
    for junk in ("1", 1, None, [True]):
        st["yielded_tick"] = junk
        assert ml._tick_ring_entry(_t(T2244_AT, st), st)[10] == 0


@pytest.mark.parametrize("stats", [
    ml._new_stats(),                                              # a refused tick: no blocks built yet
    {"tick_s": "x", "books_live": None, "short": "junk"},          # every field unreadable
    {"tick_s": float("nan"), "books_live": True, "short": {"timing": [], "wall": None, "fast": 3, "speed": "s"}},
    {},
])
def test_c14_a_missing_block_or_an_unreadable_field_reads_zero_never_a_raise(stats):
    e = ml._tick_ring_entry(_t(NOW, stats), stats)
    assert e == [round(NOW, 1)] + [0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0]
    # the append itself never raises inside the `finally`: a broken tick object is logged and dropped
    ml._tick_ring_append(object(), stats)
    assert len(ml._tick_ring) == 0


# ------------------------------------------------- three ticks, three entries

def test_c14_three_full_ticks_are_three_entries_in_order_and_short_speed_rides_beside_the_pinned_blocks():
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    sts = [_tick(p, v, now=NOW + 30 * i) for i in range(3)]
    ring = list(ml._tick_ring)
    assert len(ring) == 3 and [e[0] for e in ring] == [round(NOW + 30 * i, 1) for i in range(3)]
    for st, e in zip(sts, ring):
        assert len(e) == 15 and e[1] == st["tick_s"] and e[6] == st["books_live"] == 1
        tm = st["short"]["timing"]
        assert e[2:6] == [tm["walk"], tm["orders"], tm["books"], tm["candidates"]]
        assert e[7] == tm["venue_calls"] and e[8] == tm["cand_cap"] and isinstance(e[9], int) and e[9] >= 0
        assert e[10] == 0 and e[11] == 0 and e[12:] == [0.0, 0.0, 0.0], "no fast tick ran: the fast fields are 0"
        # the sibling blocks keep exactly their pinned keys; the new block sits beside them
        assert tuple(st["short"]["timing"]) == TIMING_KEYS and tuple(st["short"]["wall"]) == WALL_KEYS
        assert tuple(st["short"]["fast"]) == FAST_KEYS and set(st["short"]["gate"]) == {"wait", "claims"}
        assert tuple(st["short"]["speed"]) == SPEED_KEYS
    assert [st["short"]["speed"]["ring"] for st in sts] == [1, 2, 3], "the block is built before the append"
    assert all(st["short"]["speed"] == {"cand_yielded": 0, "fast_prelude": 0.0, "fast_walk": 0, "ring": n}
               for st, n in zip(sts, (1, 2, 3)))
    assert all(isinstance(x, (int, float)) and not isinstance(x, bool) for e in ring for x in e)
    assert json.loads(json.dumps(ring)) == ring, "what the write sends"


def test_c14_241_ticks_keep_240_the_oldest_dropped():
    for i in range(241):
        ml._tick_ring_append(_t(NOW + i, S2244, cand_reads=33), S2244)
    ring = list(ml._tick_ring)
    assert len(ring) == 240 == ml.TICK_RING_MAX and ring[0][0] == round(NOW + 1, 1) and ring[-1][0] == round(NOW + 240, 1)
    assert ring[0][1:] == E2244[1:]


def test_c14_a_lane_15_yield_on_the_heartbeat_reads_as_yielded_1_on_that_entry(monkeypatch):
    orig = ml._tick

    async def _yielding(t, woken):
        t.stats["yielded_tick"] = True                 # lane 15's heartbeat key, as the plan names it
        t.stats["census"]["cand_yielded"] = 1
        return await orig(t, woken)
    monkeypatch.setattr(ml, "_tick", _yielding)
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert not st["abandoned"] and ml._tick_ring[-1][10] == 1 and st["short"]["speed"]["cand_yielded"] == 1
    monkeypatch.setattr(ml, "_tick", orig)
    _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert ml._tick_ring[-1][10] == 0 and len(ml._tick_ring) == 2


# ------------------------------------------------------------ the persistence

def test_c14_the_ring_is_written_at_most_once_a_minute_after_the_boot_read_with_the_candidate_memos_shape():
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    _tick(p, v)
    assert len(_ring_writes(p)) == 1 and not _ring_reads(p), "written on the first tick after the boot read; never read"
    assert p.state[KEY] == {"ticks": list(ml._tick_ring), "at": ml._iso(NOW)} and len(p.state[KEY]["ticks"]) == 1
    _tick(p, v, now=NOW + 30)
    assert len(_ring_writes(p)) == 1 and len(ml._tick_ring) == 2, "under TERMINAL_MEMO_WRITE_S: the ring grows, nothing written"
    _tick(p, v, now=NOW + 59.9)
    assert len(_ring_writes(p)) == 1
    _tick(p, v, now=NOW + 60)
    assert len(_ring_writes(p)) == 2 and len(p.state[KEY]["ticks"]) == 4 == len(ml._tick_ring)
    assert p.state[KEY]["at"] == ml._iso(NOW + 60) and ml._tick_ring_last["at"] == NOW + 60
    assert [e[0] for e in p.state[KEY]["ticks"]] == [round(x, 1) for x in (NOW, NOW + 30, NOW + 59.9, NOW + 60)]
    # the order inside the `finally`: the speed block after the gate block and before the publish, the
    # append after the candidate memo's persist, the write last
    once = inspect.getsource(ml.tick_once)
    assert once.index('["gate"] = _gate_block(t)') < once.index('["speed"] = _speed_block(t, _fast_speed_take())') \
        < once.index("_publish_fills_dedup(t)") < once.index("_persist_cand_memo(t)") \
        < once.index("_tick_ring_append(t, stats)") < once.index("await _persist_tick_ring(t)") < once.index("_last_loss = t.loss")
    fast = inspect.getsource(ml.fast_tick_once)
    assert fast.index('["gate"] = _gate_block(t)') < fast.index('["speed"] = _speed_block(t)') < fast.index("_publish_fills_dedup(t)")
    assert "_tick_ring_append" not in fast and "_persist_tick_ring" not in fast, "the FULL tick's record alone"


def test_c14_a_refused_write_is_logged_the_ring_kept_and_the_next_eligible_tick_carries_it(caplog):
    p = _pool()
    p.add_book(ledger=300)
    v = _Venue(held={SLUG: 300})
    p.raise_on.append(("ml-state-write", RuntimeError("down")))
    with caplog.at_level(logging.WARNING, logger=ml.log.name):
        st = _tick(p, v)
    assert not st["abandoned"] and st["status"] == "ok"
    assert any(r.getMessage() == "mirror_live: mirror_tick_ring write failed (RuntimeError)" for r in caplog.records)
    assert len(ml._tick_ring) == 1 and KEY not in p.state and ml._tick_ring_last["at"] == 0.0, "kept in memory, the clock not advanced"
    p.raise_on.clear()
    _tick(p, v, now=NOW + 10)
    assert len(_ring_writes(p)) == 2 and p.state[KEY]["ticks"] == list(ml._tick_ring) and len(ml._tick_ring) == 2, \
        "retried on the next tick (the failed attempt never set the clock), both entries carried"
    assert ml._tick_ring_last["at"] == NOW + 10


def test_c14_an_empty_ring_is_never_written(monkeypatch):
    """The first tick's entry dropped (the append's except) leaves the ring
    empty: nothing is written -- an empty ring is not a record."""
    def _broken(t, stats):
        raise RuntimeError("entry")
    monkeypatch.setattr(ml, "_tick_ring_entry", _broken)
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert not st["abandoned"] and len(ml._tick_ring) == 0 and st["short"]["speed"]["ring"] == 0
    assert not _ring_writes(p) and ml._tick_ring_last["at"] == 0.0


def test_c14_nothing_is_written_before_the_boot_read_and_a_deploy_starts_the_ring_empty():
    ml._terminal_memo_loaded = False
    p = _pool()
    p.tables_absent = True                                 # a first tick refused ahead of the boot read
    st = _tick(p, _Venue())
    assert st["status"] == "degraded" and ml._terminal_memo_loaded is False
    assert len(ml._tick_ring) == 1 and ml._tick_ring[0][1:] == [st["tick_s"], 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0], \
        "the refused tick is an entry with 0.0 where the blocks are; nothing written"
    assert not _ring_writes(p) and not _ring_reads(p)
    # the deploy: the old process's ring stands in the row; the new process reads NOTHING of it and its
    # first eligible write carries only what it measured itself
    p.tables_absent = False
    p.state[KEY] = {"ticks": [E2244, E2229], "at": "2026-09-08T22:44:43Z"}
    ml._tick_ring.clear()
    p.add_book(ledger=300)
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert not st2["abandoned"] and ml._terminal_memo_loaded is True and not _ring_reads(p)
    assert len(_ring_writes(p)) == 1 and [e[0] for e in p.state[KEY]["ticks"]] == [round(NOW + 30, 1)]
    assert st2["short"]["speed"]["ring"] == 1


# ------------------------------------------------------- the fast prelude

class _Clock:
    """mirror_live's `time` on a fake monotonic clock; everything else the
    real module's."""

    def __init__(self):
        self.t = _real_time.monotonic()

    def monotonic(self):
        return self.t

    def __getattr__(self, k):
        return getattr(_real_time, k)


class _ClockPool(_Pool):
    """The fixture pool whose table guard costs 1.5 s on the fake clock:
    the first read of the fast tick's prelude."""

    def __init__(self, *a, mono=None, guard_s=1.5, **kw):
        super().__init__(*a, **kw)
        self.mono, self.guard_s = mono, guard_s      # (`clock` is the fixture pool's own wall clock)

    def _run(self, kind, sql, a):
        if "ml-table-guard" in _flat(sql) and self.mono is not None:
            self.mono.t += self.guard_s
        return super()._run(kind, sql, a)


def test_c14_the_fast_ticks_prelude_is_the_guards_1_5_s_and_not_the_books_3_0_s_and_the_pinned_blocks_stand(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ml, "time", clock)
    p = _pool()
    p.add_book(ledger=0)                                   # his 300 held, ours 0: the fast tick places
    p.__class__ = _ClockPool
    p.mono, p.guard_s = clock, 1.5
    orig = ml._fast_book

    async def _slow_book(t, b):
        clock.t += 3.0                                     # the one book's own work
        return await orig(t, b)
    monkeypatch.setattr(ml, "_fast_book", _slow_book)
    v = _Venue()
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {} and fs["fast"]["placed"] == 1 and len(_places(v)) == 1
    assert fs["short"]["speed"] == {"cand_yielded": 0, "fast_prelude": 1.5, "fast_walk": 0, "ring": 0}
    assert fs["short"]["wall"]["fast_wait"] == 0.0 and fs["short"]["wall"]["fast_work"] == 4.5
    assert tuple(fs["short"]["wall"]) == WALL_KEYS and tuple(fs["short"]["timing"]) == TIMING_KEYS
    assert set(fs["fast"]) == {"on", "tries", "skipped", "placed"} and fs["short"]["speed"]["fast_prelude"] < fs["short"]["wall"]["fast_work"]
    assert ml._fast_speed["prelude"] == 1.5, "carried to the next full tick"
    # the next full tick publishes it beside E10's split (byte-identical keys), and the ring entry carries it
    monkeypatch.setattr(ml, "_fast_book", orig)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert st["short"]["speed"] == {"cand_yielded": 0, "fast_prelude": 1.5, "fast_walk": 0, "ring": 1}
    assert st["short"]["wall"]["fast_wait"] == 0.0 and st["short"]["wall"]["fast_work"] == 4.5
    assert tuple(st["short"]["wall"]) == WALL_KEYS and tuple(st["short"]["fast"]) == FAST_KEYS and st["short"]["fast"]["n"] == 1
    e = ml._tick_ring[-1]
    assert e[11:] == [1, 0.0, 4.5, 1.5] and ml._fast_speed["prelude"] == 0.0, "reset on the take"
    st2 = _tick(p, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert st2["short"]["speed"]["fast_prelude"] == 0.0 and st2["short"]["fast"]["n"] == 0 and ml._tick_ring[-1][11:] == [0, 0.0, 0.0, 0.0]


def test_c14_a_fast_tick_that_skipped_everything_is_prelude_alone(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(ml, "time", clock)
    v = _Venue()
    # (a) the guard refused it: the 1.5 s the guard cost is the whole work
    p = _pool()
    p.__class__ = _ClockPool
    p.mono, p.guard_s, p.tables_absent = clock, 1.5, True
    _walk()
    fs = _fast(p, v)
    assert _skips(fs) == {CID: "tables_absent"} and fs["short"]["speed"]["fast_prelude"] == 1.5 == fs["short"]["wall"]["fast_work"]
    assert fs["short"]["speed"]["ring"] == 0 and ml._fast_speed["prelude"] == 1.5
    # (b) nothing woken: no loop, the prelude is the (empty) work
    ml._fast_speed["prelude"] = 0.0
    p.tables_absent = False
    fs2 = _fast(p, v, cids=())
    assert fs2["woken"] == [] and fs2["short"]["speed"]["fast_prelude"] == 0.0 == fs2["short"]["wall"]["fast_work"]
    # (c) it raised before the loop: fast_tick_failed, the whole work as the prelude
    async def _boom(t):
        clock.t += 0.7
        raise RuntimeError("mode")
    monkeypatch.setattr(ml, "_read_mode", _boom)
    fs3 = _fast(p, v)
    assert _skips(fs3) == {CID: "fast_tick_failed"}
    assert fs3["short"]["speed"]["fast_prelude"] == round(1.5 + 0.7, 1) == fs3["short"]["wall"]["fast_work"]
    assert tuple(fs3["short"]["speed"]) == SPEED_KEYS and tuple(fs3["short"]["wall"]) == WALL_KEYS


# ------------------------------------------------ the order path is untouched

def test_c14_the_order_path_and_the_rules_are_byte_identical_to_the_tip_and_the_stamp_never_branches():
    def h(f):
        return hashlib.sha256(inspect.getsource(f).encode()).hexdigest()[:16]
    # sha256[:16] of the sources on the landing tip after lane 16 and E21 (FILL lane 10, the add's take: _act / _place /
    # _place_reserved / _entry_take / _fast_gate / _fast_book and rules.order_decision are its), re-cut at landing from
    # 219f140's: nothing here reads the ring or the prelude, and this lane touches none of them
    # _act re-cut 42e08939276dcd0a -> bbe3cae4167edb41 (the cap is per trade, docs 67: an add's standing rest
    # is compared against the plan as MIRROR_CLIP_USD would size it, p_cmp); E27 (FILL lane 27, 2026-09-09: the
    # short add's band read on the no-order path's band arm) landed after and moved it -- re-cut at E27
    # (bbe3cae4167edb41 -> f32e41b85c44b038). The per-trade cap's re-cut had left this line indented under
    # `h`'s body after its return (dead code: neither hash was read); E27 de-indents it so both are read again.
    # E27's review (HIGH-1 fold: the short's at-level take at his sell cent on the no-order path) re-cuts it
    # once more: f32e41b85c44b038 -> 59efb48ba79f793c
    # E31 (FILL lane 31, 2026-09-10: every order a post-only rest that never crosses; every take path
    # retired by code) re-cuts both -- _act lost its six take arms, its arm block and its take_lvl
    # (59efb48ba79f793c -> 2e7043299fbf834a, and the fold's `maker_no_cent` hold on a standing rest)
# and _place gained the fail-closed IOC guard and lost the
    # three take flags (ab568476817cf795 -> f559a52bfb610ef3)
    assert h(ml._act) == "2e7043299fbf834a" and h(ml._place) == "f559a52bfb610ef3"
    # E30 (FILL lane 30, 2026-09-10: the post-only rejection read and kept -- the receipt, the log, the reason's
    # word and the consecutive count on the post_only_rejected branch, the backoff's guard before any read, the
    # streak reset on an accepted placement) moved _place_reserved (6c83b8e547c83e0a -> d55d0d4a63c71c23); _place
    # (the wrapper) and _act are untouched; test_e30 hashes _place_reserved with the lane's lines excised at 66144cf's
    # E31: _place_reserved sends the flag unconditionally, re-reads a touch-bound rest before the send,
    # reads the venue's own `aggressor` on a fill at create and writes the durable block, and re-prices
    # a proven cross (d55d0d4a63c71c23 -> a83a3e9473eb7112); `_entry_take` is DELETED with the take paths
    # (2266c2b346674491 on the E30 tree), so the pin is its absence
    assert h(ml._place_reserved) == "a83a3e9473eb7112" and not hasattr(ml, "_entry_take")
    assert h(ml._fast_gate) == "1932811194268668" and h(ml._fast_book) == "286e6fa4663c3887"
    # _tick_book read a0061ad32302a610 and _tick 51b72e7567d9f197 after E21; E23 (FILL lane 23:
    # the disagree adoption arm in _tick_book, the memoed cancel re-read after step O in _tick)
    # landed after and moved both -- re-cut at E23's landing
    # E24 (FILL lane 24: the desk's hand read on the disagree and closing branches of _tick_book) landed
    # after E23 and moved _tick_book -- re-cut at E24 (859d33ff50b22047 -> d1b7103a1fcfe2e1)
    # (the E24 review's HIGH-1 / LOW-1: the order-open return and the unrounded delta -- d1b7103a1fcfe2e1 -> ce2e3dccbb4140f4)
    # E25 (FILL lane 25: the exit confirmation -- the drop judged and the hold in _tick_book's reduce
    # branch, the reference and the skip's `exit_ref`) landed after E24 and moved _tick_book again --
    # re-cut at E25 (ce2e3dccbb4140f4 -> 6a592b14a88d5558); the E25 review's HIGH-1 (the sign flip's
    # flatten excluded: the flip close is never guarded) and HIGH-2 (the hold and its reference carried
    # on the market_unreadable plan) moved it again at the fold (6a592b14a88d5558 -> baf5cd361a5a02c0);
    # landed over the per-trade cap (52e1d52: the game_room null write, the game_unreadable guard) -> 78d2c4f096b70c00
    # E29 (FILL lane 29, 2026-09-10: the desk's exit ends the book's adds -- the hand hold judged before the
    # increase recheck in _tick_book and its `hold` on the add branch; the hand-exit memo read at _tick's start)
    # moved _tick_book (78d2c4f096b70c00 -> fd374d414a559684) and _tick (a766496554ff357e -> 265461d33df59da6);
    # the E29 review's LOW-1 (the recheck makes no venue call: the hold's comment corrected, no code line
    # moved) re-cut _tick_book once more (fd374d414a559684 -> f8b3aa98172bf578)
    # E30 (FILL lane 30: the post-only backoff's hold judged LAST on _tick_book's add branch, after E29's hand
    # hold) moved _tick_book (f8b3aa98172bf578 -> a9193e21be233a95); _tick untouched
    # E31: _tick_book's E30 hold comment is re-worded (there is no IOC left to exempt under it)
    # -- a9193e21be233a95 -> d121406c0c55e65b; _fast_candidate untouched
    assert h(ml._fast_candidate) == "922585ffb6856f70" and h(ml._tick_book) == "d121406c0c55e65b"
    assert h(ml._walk_candidate) == "9c990feba5fdeb57" and h(ml._tick) == "265461d33df59da6"
    # 219f140 read 9898ac1e343b5e41; the owner's $10,000 loss stop (863ad77, docs 62) landed ahead of
    # this lane and moved the module's hash to 308fd0c45fb78448 -- the lane itself touches nothing in
    # rules; the loss stop switched off by owner order (docs 65: rules.MIRROR_LOSS_STOP, an
    # env_switch beside MIRROR_LOSS_STOP_USD) moved it again (756873bcab495fab), re-cut at that
    # landing; E25 (docs 66: the five MIRROR_EXIT_CONFIRM_* rails, his_net_drop, exit_confirmed)
    # moved it once more, re-cut at E25 (12ebaf8122b0e874); the E25 review's MEDIUM-3 re-wrote the rails'
    # comment block (the flap's reference is the plan before, 8,473.8; the 76.9 s tick) -- re-cut at the fold
    # (c5c1fb34b4d381e0); landed over the per-trade cap (52e1d52, dd7e132f616861b6 there) -> a765a9b2160f11a8
    # (E27, FILL lane 27, 2026-09-09 -- the take's tolerance: MIRROR_TAKE_BAND 0.02, MIRROR_TAKE_BAND_FRAC,
    # take_band_width / short_band_cent / short_take_in_band -- re-cuts it again on the merged tree:
    # a765a9b2160f11a8 -> 6dd4e43300f04676)
    # E28 (FILL lane 28, 2026-09-09: the walk's re-read behind rules.MIRROR_WALK_REREAD, one env_switch line
    # and its comment block) landed after and moved the module -- re-cut at E28 (6dd4e43300f04676 ->
    # 5f6996a0218e16ba); no rule of sizing, pricing or refusal moved (test_e28 hashes the module with
    # that line excised against 6c0830d)
    # E29 (FILL lane 29: the one switch line MIRROR_HAND_EXIT and its comment) landed over E28 -> 7e521ec5ffa67f5f
    # (pinned in the lane's worktree on 6c0830d as d2512139ce710b52)
    # E30 (FILL lane 30: the one switch line MIRROR_POST_ONLY_BACKOFF, the one wait line
    # MIRROR_POST_ONLY_BACKOFF_S and their comment) -> 68d94eb379d99563 (test_e28 excises the block too)
    # E31 (FILL lane 31: MAKER_TICK, maker_wire / maker_bound / maker_compare_wire, rest_decision's
    # `ttl_stands` and the retired rails' paragraph) -> f0dd755348ee82d2, and the FOLD's one clause in
    # maker_compare_wire (an UNPRICED exit's rest IS the touch, so it follows the touch in BOTH
    # directions -- without it a SELL left at bid + a tick sat under a risen bid and gave away the
    # spread this lane exists to collect) -> 59f12823b66e44e7
    assert h(rules) == "59f12823b66e44e7", "mirror_live_rules moved only by the maker lane"
    # E31: `_entry_take` is DELETED with the take paths it served
    for f in (ml._act, ml._place, ml._place_reserved, ml._fast_gate, ml._fast_book,
              ml._fast_candidate, ml._tick_book, ml._walk_candidate, ml._tick):
        s = inspect.getsource(f)
        assert "_tick_ring" not in s and "fast_prelude" not in s and "_speed_block" not in s and "fast_acquired" not in s
    # the stamp: after the open rows and the caps, before the loop; the only mention in _fast_tick is the assignment
    ft = inspect.getsource(ml._fast_tick)
    assert ft.index("t.cand_cap = _cand_cap(t)") < ft.index('t.timing["fast_prelude"] = ') < ft.index("for cid in cids:")
    assert ft.count("fast_prelude") == 2 and "if t.fast_acquired is not None:" in ft, "one statement: the read and the write"
    assert "await _fast_tick(t, taken)" in inspect.getsource(ml.fast_tick_once), "the call text the E11 / E12 pins read"
    # the speed block is bounded to its four keys and reads the census by lane 15's name alone
    sb = inspect.getsource(ml._speed_block)
    assert sb.count('"cand_yielded"') == 2 and '"fast_walk": 0' in sb and '"ring": len(_tick_ring)' in sb
    assert ml._speed_block(_t(NOW, ml._new_stats())) == {"cand_yielded": 0, "fast_prelude": 0.0, "fast_walk": 0, "ring": 0}


# ------------------------------------------------------------ the preset

def _tick_ring_sql(text: str) -> str:
    sql, to = _preset(text, "tick-ring")
    assert to == 30000
    return sql


def test_c14_the_tick_ring_label_sits_after_mirror_tick_in_the_case_order_the_help_arm_and_the_hourly():
    text = YML.read_text()
    names, labels, line = _labels_and_help(text)
    assert names == labels and names[-1] == "hourly" and len(names) == len(set(names))
    assert labels[labels.index("mirror-tick") + 1] == "tick-ring" and "|mirror-tick|tick-ring|mirror-refusals|" in line
    sql = _tick_ring_sql(text)
    h, _ = _preset(text, "hourly")
    assert "SELECT '== tick-ring' AS section; " + sql + " SELECT '== mirror-pnl' AS section; " in h
    assert h.index("'== mirror-tick'") < h.index("'== tick-ring'") < h.index("'== mirror-pnl'")
    # E31 (FILL lane 31): the read-only maker-rests preset rides after take-band (ten -> eleven)
    assert hourly.PARTS == ("mirror-tick", "tick-ring", "mirror-pnl", "paired-day", "paired-ratio", "latency-census",
                            "fills-answered", "fills-missed", "take-band", "maker-rests",
                            "on-target-why") and len(hourly.PARTS) == 11
    hourly.test_the_hourly_preset_is_the_nine_presets_sql_joined_under_section_markers()
    hourly.test_the_hourly_preset_is_read_only_with_its_own_output_cap_and_timeout()
    hourly.test_the_help_line_is_the_case_labels_with_hourly_last()
    hourly.test_the_hourly_carries_fill_lane_8s_two_edits_in_its_copies()
    # ONE statement, read-only, the key word in TWO places (the case and the hourly's copy)
    # grouped and ORDERED by the hour's instant (not its time of day: across midnight 00:00 prints above 23:00)
    assert len(_stmts(sql)) == 1 and sql.endswith(
        "FROM r GROUP BY date_trunc('hour', to_timestamp((e->>0)::float8))"
        " ORDER BY date_trunc('hour', to_timestamp((e->>0)::float8)) DESC LIMIT 48;")
    for bad in ("INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "need_confirm", "$ARG", '"'):
        assert bad not in sql, bad
    assert text.count("s.key = 'mirror_tick_ring'") == 2 and h.count("s.key = 'mirror_tick_ring'") == 1
    # the fields by position, the guards, the columns the plan names
    for pos in (0, 1, 4, 5, 10, 12, 14):
        assert "jsonb_typeof(e->%d) = 'number'" % pos in sql
    assert "jsonb_array_elements(CASE WHEN jsonb_typeof(s.value->'ticks') = 'array' THEN s.value->'ticks' ELSE '[]'::jsonb END) e" in sql
    assert "CASE WHEN jsonb_typeof(e) = 'array' THEN jsonb_array_length(e) ELSE 0 END >= 15" in sql
    assert "CASE WHEN jsonb_typeof(e->0) = 'number' THEN (e->>0)::numeric END BETWEEN 0 AND 4102444800" in sql
    for pos in (1, 4, 5, 10, 12, 14):     # a jsonb number is a numeric: the range guard keeps ::float8 from raising
        assert "CASE WHEN jsonb_typeof(e->%d) = 'number' THEN (e->>%d)::numeric END BETWEEN -1e9 AND 1e9" % (pos, pos) in sql
    assert "::float8 END BETWEEN" not in sql
    for col in ("AS hour", "AS ticks", "AS tick_med_s", "AS tick_p90_s", "AS tick_max_s", "AS cand_med_s", "AS cand_max_s",
                "AS books_med_s", "AS fast_wait_p90_s", "AS yields", "AS fast_prelude_med_s"):
        assert col in sql, col
    pglast = pytest.importorskip("pglast")
    pglast.parse_sql(sql)
    pglast.parse_sql(h)
    # the cap: the 13:35Z hourly is a 1,075-line log (h1335_db.txt, with the job's header and its two trailer
    # lines) under HEAD 1500; the section adds at most the marker's 5 lines (a blank, the header, the rule, the
    # row, the count) plus a 48-row table (header 2, rows, the count line, a blank): 57 lines
    assert 1075 + 5 + 2 + 48 + 1 + 1 <= 1500 and "HEAD=1500" in text[text.index("hourly) SQL="):]


FIXTURE_L14 = """
INSERT INTO ingestion_state (key, value) VALUES ('mirror_tick_ring', '%s');
""" % json.dumps({"ticks": [E2030, E2229, E2244], "at": "2026-09-08T22:44:43Z"})


@pytest.fixture(scope="module")
def world():
    w = World(FIXTURE_L14, "FILL lane 14")
    w.run("SET TIME ZONE 'UTC'")   # the hour buckets are UTC; the pins must not read the server's zone
    try:
        yield w
    finally:
        w.close()


def test_c14_the_preset_reads_a_three_tick_ring_by_hour_on_the_scratch_database(world):
    sql = _tick_ring_sql(YML.read_text())
    rows = [dict(r) for r in world.rows(sql)]
    assert [(str(r["hour"]), r["ticks"]) for r in rows] == [("22:00:00", 2), ("20:00:00", 1)], "newest hour first"
    r22, r20 = rows
    # 22Z: ticks 38.0 and 12.4 -> median (38.0 + 12.4) / 2 = 25.2, p90 = 12.4 + 0.9 x 25.6 = 35.44, max 38.0;
    # candidates 16.2 / 0.8 -> 8.5 / 16.2; books 11.3 / 5.3 -> 8.3; fast_wait 2.3 / 7.8 -> p90 = 2.3 + 0.9 x 5.5 = 7.25
    assert [float(r22[k]) for k in ("tick_med_s", "tick_p90_s", "tick_max_s", "cand_med_s", "cand_max_s", "books_med_s",
                                     "fast_wait_p90_s", "fast_prelude_med_s")] == [25.2, 35.4, 38.0, 8.5, 16.2, 8.3, 7.3, 0.0]
    assert r22["yields"] == 0
    assert [float(r20[k]) for k in ("tick_med_s", "tick_p90_s", "tick_max_s", "cand_med_s", "cand_max_s", "books_med_s",
                                     "fast_wait_p90_s")] == [76.9, 76.9, 76.9, 32.5, 32.5, 31.7, 43.6] and r20["yields"] == 0
    assert list(rows[0]) == ["hour", "ticks", "tick_med_s", "tick_p90_s", "tick_max_s", "cand_med_s", "cand_max_s",
                             "books_med_s", "fast_wait_p90_s", "yields", "fast_prelude_med_s"]
    # a yielded entry counts once in its hour
    world.run("UPDATE ingestion_state SET value = jsonb_set(value, '{ticks,2,10}', '1') WHERE key = 'mirror_tick_ring'")
    assert [r["yields"] for r in world.rows(sql)] == [1, 0]
    # across midnight the order is the hour's INSTANT, newest first: a 23:50Z tick and a 00:10Z tick of the next
    # day print 00:00 above 23:00 (a time-of-day order would put 23:00 first)
    world.run("UPDATE ingestion_state SET value = jsonb_set(value, '{ticks}', value->'ticks' || '%s') WHERE key = 'mirror_tick_ring'"
              % json.dumps([[_ep("2026-09-08T23:50:00.0")] + E2244[1:], [_ep("2026-09-09T00:10:00.0")] + E2229[1:]]))
    assert [(str(r["hour"]), r["ticks"]) for r in world.rows(sql)] == [("00:00:00", 1), ("23:00:00", 1), ("22:00:00", 2), ("20:00:00", 1)]
    world.run("UPDATE ingestion_state SET value = '%s' WHERE key = 'mirror_tick_ring'" % FIXTURE_L14.split("'")[3])


@pytest.mark.parametrize("value", [
    "null", '"x"', "3", "[1,2]", '{"ticks": "x"}', '{"ticks": {"a": 1}}', '{"ticks": []}', '{"at": "x"}',
    '{"ticks": [1, "x", null, {"a": 1}, [1, 2], ["x", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],'
    ' [1e300, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], [1757000000, "x", 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]]}',
    # a jsonb number is a numeric: past float8's range it passes jsonb_typeof = 'number' and the bare cast RAISES
    '{"ticks": [[1e400, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]]}',
    '{"ticks": [[1757000000, 1e400, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]]}',
    '{"ticks": [[1757000000, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, "x"]]}',
    '{"ticks": [[1757000000, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]]}',
])
def test_c14_a_null_or_malformed_state_row_prints_nothing_and_never_stops_the_hourly_bundle(world, value):
    text = YML.read_text()
    sql = _tick_ring_sql(text)
    world.run("UPDATE ingestion_state SET value = '%s' WHERE key = 'mirror_tick_ring'" % value)
    try:
        assert world.rows(sql) == []
        # a SQL NULL cannot stand in the row (001_init.sql 175: value JSONB NOT NULL) -- the jsonb 'null' above
        # is that case; the row absent prints nothing too
        with pytest.raises(Exception):
            world.run("UPDATE ingestion_state SET value = NULL WHERE key = 'mirror_tick_ring'")
        # the hourly bundle runs over the malformed row as ONE multi-statement string (psql -c under
        # ON_ERROR_STOP would end the job on a raise): its first eight sections, the ring's in second place
        # -- take-band (lane 0b's) divides by zero on an EMPTY mirror_orders and is not this lane's, so the
        # text is cut at its marker; on-target-why, after it, runs alone
        world.run("UPDATE ingestion_state SET value = '%s' WHERE key = 'mirror_tick_ring'" % value)
        h, _ = _preset(text, "hourly")
        bundle = h[:h.index("SELECT '== take-band' AS section; ")]
        assert bundle.count("AS section;") == 8 and "'== tick-ring'" in bundle
        world.run(bundle)
        world.run(h[h.index("SELECT '== on-target-why' AS section; "):])
        world.run("DELETE FROM ingestion_state WHERE key = 'mirror_tick_ring'")
        assert world.rows(sql) == []
        world.run(bundle)
    finally:
        world.run("DELETE FROM ingestion_state WHERE key = 'mirror_tick_ring'")
        world.run(FIXTURE_L14)


# ---------------------------------------------------------------- the docs

def test_c14_docs_section_58_names_the_rule_the_key_the_block_the_preset_and_the_dollars():
    text = DOCS.read_text()
    m = re.search(r"^## 58\. .*\(2026-09-09, FILL lane 14\)$", text, re.M)
    assert m, "docs section 58, FILL lane 14"
    body = text[m.end():]
    nxt = re.search(r"^## \d+\. ", body, re.M)
    body = body[:nxt.start()] if nxt else body
    for word in ("mirror_tick_ring", "TICK_RING_MAX", "240", "short.speed", "tick-ring", "fast_prelude", "cand_yielded",
                 "TERMINAL_MEMO_WRITE_S", "$0", "tick_2245", "38.0", "no boot read", "THE RULE", "THE NAMES", "THE RAILS",
                 "FAIL CLOSED", "THE TESTS", "LIVE PROOF", "DOES NOT FIX", "test_fill_c14_tick_ring.py"):
        assert word in body, word
    assert "Expected dollars: $0" in body and "The order path is byte-identical" in body
