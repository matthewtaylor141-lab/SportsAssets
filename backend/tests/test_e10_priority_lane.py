"""E10 (2026-09-08): the mirror's per-market read stops queueing behind
telemetry.

Owner 22:4xZ ("latency must be flawless and exceptional"). With E9 live
the tick was 25-44 s (00:50-00:59Z) and `books_data_wait` 21-28 s SUMMED
over 16-20 per-market reads: ~1.4 s of queue per read on a process-wide
data-API throttle (6.0 rps, one FIFO) oversubscribed by the poller's two
lanes, positions_sync and the mirror (~6.6-7.2 rps offered), with two
/positions bursts not on it at all (hard2/E10_map.md). Four parts and a
boundary, pinned here:

  part 1  a PRIORITY LANE on ratelimit.Throttle (`acquire(priority=True)`):
          the next free slot ahead of every waiting normal caller, the
          RATE unchanged (6.0 rps total; env may only lower it), normal
          callers FIFO among themselves, at most PRIORITY_BURST = 12
          priority slots in a row while a normal caller waits; the ONLY
          priority caller is whale_exits.market_positions from
          mirror_live._market_snap (`priority=True`, default False);
  part 2  positions_sync_interval_seconds 300 -> 900 (read by the API
          alone: the UI's whale profile and events view, the edge
          engine's /api/signal), env floor 60;
  part 3  whale_exits._fetch_positions and _confirm_gone's two call sites
          take a NORMAL slot per request (the callee pinned byte for byte
          by the E7 review, so the wait sits at its callers);
  part 4  `short.wall` = {books_data_wall, books_venue_wall,
          books_plan_wall, fast_wait, fast_work} in WALL time beside E6's
          and E7's per-call sums (whose keys stay exact); the mode line's
          ` fast=N/W.Ws` in E9's token's place;
  part 5  not in scope: the pacer, MIRROR_BOOK_CONCURRENCY, SNAP_MAX_AGE_S,
          the E6 budgets, any read's decision -- the E6 / E7 / E9 source
          pins re-run at the end, and their files run beside this one.

The lane pins run the real Throttle at a FAKE CLOCK (ratelimit._clock /
_sleep): every slot is a fake 1/rate seconds, so a 2 s harness is
instant and deterministic (asyncio's ready queue is FIFO, so arrival
order is task-creation order).
"""
import asyncio
import inspect
import logging
import pathlib
import re
import time
from types import SimpleNamespace

import pytest

import sportsassets
from sportsassets import positions_sync, ratelimit
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.config import Settings
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import whale_exits as we
from tests.test_e6_tick_budget import TIMING_KEYS
from tests.test_e7_cand_memo import DATA_API_KEYS
from tests.test_e9_fast_path import FAST_KEYS, _fast, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, N, NOW, SLUG, _Http, _Venue, _armed, _census, _many_books, _pool, _run, _tick,
)

WALL_KEYS = ("books_data_wall", "books_venue_wall", "books_plan_wall", "fast_wait", "fast_work")
PKG = pathlib.Path(sportsassets.__file__).resolve().parent
DOCS = PKG.parents[1] / "docs" / "mirror-coverage.md"


# ------------------------------------------------------------ the harness

class _Clock:
    """A fake monotonic clock for the throttle: `sleep(d)` yields once
    (or waits on `gate` when a test holds one), THEN advances by `d`,
    so a waiter resumed by the pump reads the slot's own instant."""

    def __init__(self, t=1000.0):
        self.t = t
        self.sleeps = 0
        self.gate = None

    def now(self):
        return self.t

    async def sleep(self, d):
        self.sleeps += 1
        if self.gate is not None:
            await self.gate.wait()
        else:
            await asyncio.sleep(0)
        self.t += max(0.0, float(d))


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ratelimit, "_clock", c.now)
    monkeypatch.setattr(ratelimit, "_sleep", c.sleep)
    return c


def _queue(thr, clock, arrivals, served):
    """One task per arrival -- (name, priority) in ARRIVAL order -- each
    recording the fake instant it was served."""
    async def one(name, prio):
        await thr.acquire(priority=prio)
        served.append((name, clock.t))
    return [asyncio.create_task(one(n, p)) for n, p in arrivals]


async def _serve(thr, clock, arrivals):
    served: list = []
    tasks = _queue(thr, clock, arrivals, served)
    await asyncio.sleep(0)              # every arrival queued before the first pick
    await asyncio.gather(*tasks)
    return served


class _Lanes:
    """A recording throttle: which lane each caller took."""

    def __init__(self):
        self.calls: list = []

    async def wait(self):
        self.calls.append("normal")

    async def acquire(self, priority=False):
        self.calls.append("priority" if priority else "normal")


def _names(served):
    return [n for n, _ in served]


# ------------------------------------------------------ part 1: the lanes

def test_e10_the_constants_and_why_twelve():
    assert ratelimit.DATA_API_MAX_RPS == 6.0 and ratelimit.PRIORITY_BURST == 12
    assert Settings.model_fields["data_api_max_rps"].default == 6.0
    # two waves of the six-wide book walk: the bound in terms of the rail it serves
    assert ratelimit.PRIORITY_BURST == 2 * rules.MIRROR_BOOK_CONCURRENCY == 12
    src = inspect.getsource(ratelimit)
    assert "MIRROR_BOOK_CONCURRENCY" in src[:src.index("PRIORITY_BURST = 12")]
    assert ratelimit.Throttle(6.0).interval == pytest.approx(1 / 6)
    assert ratelimit.Throttle(0.0).interval == 10.0, "the 0.1 rps floor, as before"


def test_e10_a_priority_caller_is_served_before_every_waiting_normal_caller(clock):
    thr = ratelimit.Throttle(6.0)
    normals = [(f"n{i}", False) for i in range(8)]
    served = _run(_serve(thr, clock, normals + [("p", True)]))
    assert _names(served) == ["p"] + [n for n, _ in normals], "the last to arrive, the first served"
    assert served[0][1] == 1000.0, "the free slot, at once"
    assert thr.waiting() == (0, 0)


def test_e10_normal_callers_keep_fifo_and_so_do_priority_callers(clock):
    thr = ratelimit.Throttle(6.0)
    served = _run(_serve(thr, clock, [("n1", False), ("n2", False), ("p1", True), ("n3", False),
                                      ("p2", True), ("n4", False)]))
    assert _names(served) == ["p1", "p2", "n1", "n2", "n3", "n4"]
    gaps = [b[1] - a[1] for a, b in zip(served, served[1:])]
    assert all(g == pytest.approx(thr.interval) for g in gaps), gaps


def test_e10_a_priority_arrival_during_the_wait_takes_the_slot_the_lane_is_picked_after_the_sleep(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        clock.gate = asyncio.Event()
        tasks = _queue(thr, clock, [("n1", False), ("n2", False)], served)
        for _ in range(3):              # queued; n1 served; n1 recorded -- the pump now sleeps
            await asyncio.sleep(0)
        assert _names(served) == ["n1"] and clock.sleeps == 1
        tasks += _queue(thr, clock, [("p", True)], served)
        await asyncio.sleep(0)          # p queued while the pump sleeps
        clock.gate.set()
        clock.gate = None
        await asyncio.gather(*tasks)
        return served, thr
    served, thr = _run(go())
    assert _names(served) == ["n1", "p", "n2"], "the arrival during the wait took the freed slot"
    src = inspect.getsource(ratelimit.Throttle._serve)
    assert src.index("await _sleep(") < src.index("self._pick(") and "continue" in src


def test_e10_the_rate_is_unchanged_under_mixed_load_a_two_second_harness_at_a_fake_clock(clock):
    thr = ratelimit.Throttle(6.0)
    mixed = [(f"c{i}", i % 3 == 0) for i in range(40)]
    served = _run(_serve(thr, clock, mixed))
    t0 = 1000.0
    times = [t for _, t in served]
    assert times[0] == t0 and len(served) == 40
    for k, t in enumerate(times):
        assert t == pytest.approx(t0 + k * thr.interval), (k, t)
    # total slots = rate x time: twelve slots in the two seconds past the first
    assert sum(1 for t in times if t0 < t <= t0 + 2.0 + 1e-9) == 12 == 6.0 * 2.0
    assert times[-1] - t0 == pytest.approx(39 * thr.interval)
    names = _names(served)
    prio = [n for n, p in mixed if p]
    norm = [n for n, p in mixed if not p]
    # the lane rule under the mix: twelve priorities, then the starvation bound hands the
    # thirteenth slot to the first normal, then the last two priorities, then the normals FIFO
    assert len(prio) == 14 and names[:12] == prio[:12] and names[12] == norm[0]
    assert names[13:15] == prio[12:] and [n for n in names if n in norm] == norm


def test_e10_the_starvation_bound_thirteen_priority_arrivals_one_normal_served_by_the_thirteenth(clock):
    thr = ratelimit.Throttle(6.0)
    served = _run(_serve(thr, clock, [("n", False)] + [(f"p{i}", True) for i in range(1, 14)]))
    names = _names(served)
    assert names[:12] == [f"p{i}" for i in range(1, 13)]
    assert names[12] == "n", "the thirteenth slot is the normal caller's"
    assert names[13] == "p13"
    assert served[12][1] - served[0][1] == pytest.approx(12 * thr.interval), "2 s at the ceiling"


def test_e10_the_burst_run_resets_when_a_normal_is_served_or_none_waits(clock):
    thr = ratelimit.Throttle(6.0)
    # two normals behind twenty-six priorities: one served by the 13th, the other by the 26th
    served = _run(_serve(thr, clock, [("n1", False), ("n2", False)]
                         + [(f"p{i}", True) for i in range(1, 27)]))
    names = _names(served)
    assert names.index("n1") == 12 and names.index("n2") == 25 and names[26] == "p25"
    # no normal waiting: twenty priorities back to back, then a normal arriving beside a
    # priority still yields to it (the run counter reset while nobody waited)
    thr2 = ratelimit.Throttle(6.0)
    first = _run(_serve(thr2, clock, [(f"p{i}", True) for i in range(20)]))
    assert _names(first) == [f"p{i}" for i in range(20)]
    second = _run(_serve(thr2, clock, [("n", False), ("p", True)]))
    assert _names(second) == ["p", "n"]


def test_e10_a_waiter_cancelled_in_the_queue_leaves_it_and_burns_no_slot(clock):
    async def go():
        thr = ratelimit.Throttle(6.0)
        served: list = []
        tasks = _queue(thr, clock, [("n1", False), ("n2", False), ("n3", False)], served)
        await asyncio.sleep(0)
        await asyncio.sleep(0)          # n1 served, the pump asleep
        tasks[1].cancel()
        out = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(out[1], asyncio.CancelledError)
        return served, thr
    served, thr = _run(go())
    assert _names(served) == ["n1", "n3"]
    assert served[1][1] - served[0][1] == pytest.approx(thr.interval), "n3 took n2's slot: none burned"
    assert thr.waiting() == (0, 0)
    src = inspect.getsource(ratelimit.Throttle.acquire)
    assert "except asyncio.CancelledError:" in src and "lane.remove(fut)" in src and "raise" in src


def test_e10_an_idle_throttle_hands_the_slot_out_at_once_and_wait_is_the_normal_lane(clock):
    thr = ratelimit.Throttle(6.0)
    t0 = clock.t
    _run(thr.wait())
    assert clock.t == t0 and clock.sleeps == 0, "no wait on an idle throttle"
    _run(thr.acquire(priority=True))
    assert clock.t == t0 + thr.interval and clock.sleeps == 1, "the next slot, one interval on"
    assert "await self.acquire()" in inspect.getsource(ratelimit.Throttle.wait)
    src = inspect.getsource(ratelimit.polite_get)
    assert "await throttle.wait()" in src and "priority" not in src.split('"""')[2]


def test_e10_env_can_only_lower_the_rate(monkeypatch):
    for setting, want in ((12.0, 6.0), (6.0, 6.0), (3.0, 3.0), (100.0, 6.0), (float("nan"), 6.0)):
        monkeypatch.setattr(ratelimit, "settings", lambda s=setting: SimpleNamespace(data_api_max_rps=s))
        assert ratelimit.data_api_rate() == want, setting
        monkeypatch.setattr(ratelimit, "_throttle", None)
        thr = ratelimit.data_api_throttle()
        assert isinstance(thr, ratelimit.Throttle) and thr.interval == pytest.approx(1 / want)
        assert ratelimit.data_api_throttle() is thr, "built once, as before"

    def boom():
        raise RuntimeError("no settings")
    monkeypatch.setattr(ratelimit, "settings", boom)
    monkeypatch.setattr(ratelimit, "_throttle", None)
    with pytest.raises(RuntimeError):
        ratelimit.data_api_throttle()        # unreadable settings: no throttle, no request (as before)
    assert "min(rate, DATA_API_MAX_RPS)" in inspect.getsource(ratelimit.data_api_rate)


# ---------------------------------------------- part 1: the one caller

def test_e10_market_positions_priority_true_is_one_priority_wait_before_the_one_get(monkeypatch):
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    http = _Http()
    out = _run(we.market_positions(http, "0xabc", CID, long_asset=M, priority=True))
    assert out["complete"] is True and out["by_asset"] == {M: 300.0, N: 0.0}
    assert lanes.calls == ["priority"] and len(http.calls) == 1
    # the one-wait pin re-run on the priority lane: a refusal charges it once too
    lanes.calls.clear()
    assert _run(we.market_positions(_Http(status=503), "0xabc", CID, priority=True)) is None
    assert lanes.calls == ["priority"]
    # the default and an explicit False: the normal lane, exactly as before
    lanes.calls.clear()
    _run(we.market_positions(_Http(), "0xabc", CID))
    _run(we.market_positions(_Http(), "0xabc", CID, priority=False))
    assert lanes.calls == ["normal", "normal"]
    sig = inspect.signature(we.market_positions)
    assert sig.parameters["priority"].default is False
    assert sig.parameters["priority"].kind is inspect.Parameter.KEYWORD_ONLY
    # the timing split is untouched by the lane, the shape too
    d: dict = {}
    out2 = _run(we.market_positions(_Http(), "0xabc", CID, long_asset=M, timing=d, priority=True))
    assert set(out2) == {"by_asset", "avg_price", "long", "complete", "ts"}
    assert d["wait"] >= 0.0 and d["req"] >= 0.0

    def boom():
        raise RuntimeError("no settings")
    monkeypatch.setattr(ratelimit, "data_api_throttle", boom)
    http = _Http()
    assert _run(we.market_positions(http, "0xabc", CID, priority=True)) is None
    assert http.calls == [], "no budget slot, no read -- on the priority lane too"


def test_e10_market_positions_from_mirror_live_passes_priority_true_and_every_other_caller_false():
    # grep the package: the read is DEFINED in whale_exits and CALLED from one place
    sites = {}
    for path in sorted(PKG.rglob("*.py")):
        hits = re.findall(r"^[^#\n]*\bmarket_positions\(", path.read_text(), re.M)
        if hits:
            sites[path.relative_to(PKG).as_posix()] = hits
    assert set(sites) == {"workers/whale_exits.py", "workers/mirror_live.py"}, sites
    assert sites["workers/whale_exits.py"] == ["async def market_positions("]
    assert len(sites["workers/mirror_live.py"]) == 1
    snap = inspect.getsource(ml._market_snap)
    assert snap.count("market_positions(") == 1 and "priority=True" in snap
    # the priority lane's entries in the package: market_positions' own branch and, since the
    # E10 fold (MEDIUM-2), the mirror's own vanish confirmation (its site pinned in
    # test_e10_review_pins); nothing else
    entries = [p.relative_to(PKG).as_posix() for p in sorted(PKG.rglob("*.py"))
               if re.search(r"^\s*await .*acquire\(priority=True\)", p.read_text(), re.M)]
    assert entries == ["workers/mirror_live.py", "workers/whale_exits.py"], entries
    mp = inspect.getsource(we.market_positions)
    body = mp.split('"""')[2]
    assert body.count("acquire(priority=True)") == 1 and body.count("data_api_throttle().wait()") == 1
    # every other data-API consumer is a normal caller: no `priority` anywhere in them
    for mod in ("ingestion/poller.py", "ingestion/history.py", "ingestion/reconciler.py",
                "positions_sync.py", "roster.py"):
        text = (PKG / mod).read_text()
        assert "acquire(priority" not in text and "market_positions" not in text, mod


def test_e10_the_book_walk_the_candidate_read_and_the_fast_tick_read_on_the_priority_lane(monkeypatch):
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    # the book walk
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    assert st["snap_market_reads"] == 1 and lanes.calls == ["priority"]
    # the candidate read (a book opened on it shares the read: still one)
    lanes.calls.clear()
    p2 = _pool()
    st2 = _tick(p2, _Venue())
    assert p2.books and st2["snap_market_reads"] == 1 and lanes.calls == ["priority"]
    # the fast tick
    lanes.calls.clear()
    p3 = _pool()
    p3.add_book(ledger=0)
    _walk()
    fs = _fast(p3, _Venue())
    assert fs["snap_market_reads"] == 1 and fs["fast"]["placed"] == 1 and lanes.calls == ["priority"]


# ------------------------------------------------ part 2: positions_sync

def test_e10_positions_sync_default_900_and_the_env_floor_60(monkeypatch):
    assert Settings.model_fields["positions_sync_interval_seconds"].default == 900
    assert positions_sync.POSITIONS_SYNC_MIN_S == 60
    for setting, want in ((900, 900), (120, 120), (60, 60), (59, 60), (0, 60), (-5, 60), (3600, 3600)):
        monkeypatch.setattr(positions_sync, "settings",
                            lambda s=setting: SimpleNamespace(positions_sync_interval_seconds=s))
        assert positions_sync.sync_interval_s() == want, setting
    cfg = inspect.getsource(Settings)
    i = cfg.index("positions_sync_interval_seconds: int = 900")
    para = cfg[max(0, i - 900):i]
    assert "15 minutes old" in para and "UI" in para and "POSITIONS_SYNC_MIN_S = 60" in para, \
        "the docstring says what the UI's freshness becomes"
    env = (PKG.parents[1] / ".env.example").read_text()
    assert "POSITIONS_SYNC_INTERVAL_SECONDS=900" in env


def test_e10_sync_all_positions_gates_on_the_floored_interval(monkeypatch):
    monkeypatch.setattr(positions_sync, "settings",
                        lambda: SimpleNamespace(positions_sync_interval_seconds=30,
                                                data_api_base="https://example.invalid"))

    class _P:
        async def fetch(self, sql, *a):
            return []
    calls = {"pool": 0}

    async def _gp():
        calls["pool"] += 1
        return _P()
    monkeypatch.setattr(positions_sync, "get_pool", _gp)
    # 45 s after the last sync: under the 60 s floor, nothing runs (the setting said 30)
    monkeypatch.setattr(positions_sync, "_last_sync", time.monotonic() - 45)
    assert _run(positions_sync.sync_all_positions()) == {} and calls["pool"] == 0
    # 61 s after: the pass runs (no whales here: no request)
    monkeypatch.setattr(positions_sync, "_last_sync", time.monotonic() - 61)
    assert _run(positions_sync.sync_all_positions()) == {} and calls["pool"] == 1
    assert time.monotonic() - positions_sync._last_sync < 5.0
    assert "now - _last_sync < sync_interval_s()" in inspect.getsource(positions_sync.sync_all_positions)


# ----------------------------------------- part 3: the two bursts, inside

class _Pages:
    """The venue's unfiltered /positions: `answers` per GET, a 500-row page
    unless the entry says otherwise."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls: list = []

    async def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        status, n = self.answers.pop(0)
        rows = [{"asset": f"a{params['offset'] + i}", "size": 1} for i in range(n)]

        class _R:
            status_code = status

            def raise_for_status(self):
                if status >= 400:
                    raise RuntimeError(status)

            def json(self):
                return rows
        return _R()


def test_e10_fetch_positions_takes_one_normal_slot_per_request_the_retry_included(monkeypatch):
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    http = _Pages([(200, 500), (200, 500), (200, 500), (200, 10)])
    sizes, sibs, seen = _run(we._fetch_positions(http, "0xw"))
    assert seen == 1510 and len(http.calls) == 4 and lanes.calls == ["normal"] * 4
    # the retry after a 503 is a request of its own: its own slot
    orig = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda d: orig(0))
    lanes.calls.clear()
    http = _Pages([(503, 0), (200, 10)])
    sizes, sibs, seen = _run(we._fetch_positions(http, "0xw"))
    assert seen == 10 and len(http.calls) == 2 and lanes.calls == ["normal", "normal"]
    src = inspect.getsource(we._fetch_positions).split('"""')[2]
    assert src.count("await ratelimit.data_api_throttle().wait()") == 2 == src.count('http.get("/positions"')
    assert "priority" not in src
    # the pinned shape of the walk is untouched
    assert "while offset < POSITIONS_MAX" in src and "if len(rows) < POSITIONS_PAGE:" in src
    assert "raise TruncatedPositions(" in src and "sibs.setdefault(sib, a)" in src


def test_e10_confirm_gone_is_untouched_the_cycles_site_takes_a_normal_slot_first_and_the_mirrors_the_priority_lane(monkeypatch):
    """E10 fold (MEDIUM-2): the mirror's own site moved from the normal lane to the
    priority lane, its wait bounded; the callee and whale_exits._cycle's site as built."""
    cg = inspect.getsource(we._confirm_gone)
    assert "throttle" not in cg and "ratelimit" not in cg, "pinned byte for byte by the E7 review"
    assert '"user": address, "market": cid, "limit": 100,' in cg and '"sizeThreshold": 0})' in cg
    # whale_exits._cycle: the wait immediately precedes the call, inside the held check
    cyc = inspect.getsource(we._cycle)
    i = cyc.index("await ratelimit.data_api_throttle().wait()")
    tail = cyc[i:i + 400]
    assert "if await _confirm_gone(http, pool," in tail
    assert cyc.index("if a not in held_here:") < i < cyc.index("exclusion.discard(a)")
    assert cyc.count("data_api_throttle()") == 1
    # mirror_live._confirm_gone: the PRIORITY wait (bounded), then the callee; behaviour with a
    # recording throttle
    src = inspect.getsource(ml._confirm_gone)
    assert src.index("data_api_throttle().acquire(priority=True)") < src.index("whale_exits._confirm_gone(")
    lanes = _Lanes()
    monkeypatch.setattr(ratelimit, "_throttle", lanes)
    p = _pool()
    http = _Http(rows=[{"conditionId": CID, "asset": M, "size": 0},
                       {"conditionId": CID, "asset": N, "size": 0}])
    t = ml._Tick(pool=p, pmus=_Venue(), http=http, now=NOW, stats=ml._new_stats())
    assert _run(ml._confirm_gone(t, "rn1", M)) is True
    assert lanes.calls == ["priority"] and len(http.calls) == 1
    assert http.calls[0][1] == {"user": http.calls[0][1]["user"], "market": CID, "limit": 100,
                                "sizeThreshold": 0}

    def boom():
        raise RuntimeError("no settings")
    monkeypatch.setattr(ratelimit, "data_api_throttle", boom)
    http2 = _Http()
    t2 = ml._Tick(pool=p, pmus=_Venue(), http=http2, now=NOW, stats=ml._new_stats())
    assert _run(ml._confirm_gone(t2, "rn1", M)) is False and http2.calls == [], "unknown is not gone"


def test_e10_the_exit_walks_cost_at_the_ceiling_and_nothing_in_whale_exits_times_out_on_it():
    pages = we.POSITIONS_MAX / we.POSITIONS_PAGE
    assert pages == 48 and pages / ratelimit.DATA_API_MAX_RPS == 8.0, "8 s per held whale at 6 rps"
    # the client's timeout is per request and the wait precedes the request; the cycle has
    # no deadline of its own -- main sleeps INTERVAL_S after it
    main = inspect.getsource(we.main)
    assert "timeout=25.0" in main and "await asyncio.sleep(INTERVAL_S)" in main
    for fn in (we._cycle, we._fetch_positions, we.main):
        code = re.sub(r"^\s*#.*$", "", inspect.getsource(fn), flags=re.M)
        assert "wait_for(" not in code and "deadline" not in code, fn.__name__
    src = inspect.getsource(we._fetch_positions)
    assert "8 s" in src and "nothing here times out" in src


# --------------------------------------------- part 4: the wall clock

def test_e10_the_wall_block_keys_and_books_data_wall_le_books_le_tick_s():
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    wb = st["short"]["wall"]
    assert tuple(wb) == WALL_KEYS, "bounded: these keys and no others"
    for k in WALL_KEYS:
        assert isinstance(wb[k], float) and wb[k] >= 0.0 and wb[k] == round(wb[k], 1), k
    tm = st["short"]["timing"]
    assert wb["books_data_wall"] <= tm["books"] <= st["tick_s"]
    assert wb["books_venue_wall"] <= tm["books"] and wb["books_plan_wall"] <= tm["books"]
    assert wb["fast_wait"] == 0.0 and wb["fast_work"] == 0.0, "no fast tick since the last publish"
    # the neighbours keep exactly their keys; served whole inside `short`
    assert tuple(tm) == TIMING_KEYS and tuple(st["short"]["data_api"]) == DATA_API_KEYS
    assert tuple(st["short"]["fast"]) == FAST_KEYS
    served = api_app._sanitize_detail(st)
    assert served["short"]["wall"] == wb and "_truncated_keys" not in served
    assert len(st["integ"]) < api_app._DETAIL_MAX_KEYS and len(ml._new_stats()) <= api_app._DETAIL_MAX_KEYS
    from tests.test_mirror_live_worker import _comparable
    assert "wall" not in _comparable(st)["short"]
    # published after E9's block and before the fills collapse (the E7 order pin's tail)
    once = inspect.getsource(ml.tick_once)
    assert once.index('["fast"] = _fast_block()') < once.index('["wall"] = _wall_block(t') \
        < once.index("_publish_fills_dedup(t)")
    # a fresh tick's block is all zeros
    t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._new_stats())
    assert ml._wall_block(t, 0.0, 0.0) == dict.fromkeys(WALL_KEYS, 0.0)


class _SlowByCid(_Http):
    """The data API answering per market after a real 50 ms in flight:
    six books read at once wait TOGETHER, so the wall is one wait and
    the per-call sum is six."""

    async def get(self, path, params=None):
        self.calls.append((path, params))
        await asyncio.sleep(0.05)
        want = str((params or {}).get("market"))
        m = re.fullmatch(r"0xbook(\d+)", want)
        rows = ([{"conditionId": want, "asset": f"tokL{m.group(1)}", "size": 0},
                 {"conditionId": want, "asset": f"tokO{m.group(1)}", "size": 0}] if m else self.rows)
        return await _Http(rows=rows).get(path, params)


def test_e10_books_data_wall_is_wall_time_not_a_sum_per_call():
    assert rules.MIRROR_BOOK_CONCURRENCY == 6
    p = _pool()
    _many_books(p, 6)
    st = _tick(p, _Venue(), http=_SlowByCid())
    tm, da, wb = st["short"]["timing"], st["short"]["data_api"], st["short"]["wall"]
    assert tm["read"] >= 6 and st["snap_market_reads"] >= 7
    # E6 / E7: the SUM per call -- six reads of >= 50 ms each
    assert tm["books_data"] >= 0.3 and da["books_data_req"] >= 0.3, (tm, da)
    # E10: the WALL -- the six waited together
    assert 0.05 <= wb["books_data_wall"] <= tm["books"] <= st["tick_s"], (wb, tm)
    assert wb["books_data_wall"] <= 0.5 * tm["books_data"], "wall time, not a sum"
    assert wb["books_plan_wall"] <= tm["books"] and wb["books_venue_wall"] <= tm["books"]
    # the counters are back at rest after the tick (every enter has its exit)
    assert ml._WALL._n == {"data": 0, "venue": 0, "books": 0}
    # the three counters enter and leave where the calls are made
    assert 'move("data", 1)' in inspect.getsource(ml._market_snap)
    assert 'move("venue", 1)' in inspect.getsource(ml._bbo) and 'move("venue", 1)' in inspect.getsource(ml._paced)
    for fn in (ml._walk_books, ml._fast_book, ml._tick_candidate):
        assert 'move("books", 1)' in inspect.getsource(fn), fn.__name__
    assert "books - venue - data > 0" in inspect.getsource(ml._WallClock)


def test_e10_fast_wait_plus_fast_work_is_the_old_fast_and_a_fast_tick_publishes_its_own():
    p = _pool()
    p.add_book(ledger=0)
    v = _Venue()
    _walk()
    fs = _fast(p, v)                                            # placed
    wb = fs["short"]["wall"]
    assert tuple(wb) == WALL_KEYS and fs["fast"]["placed"] == 1
    assert abs(wb["fast_wait"] + wb["fast_work"] - fs["tick_s"]) <= 0.15, (wb, fs["tick_s"])
    assert wb["books_data_wall"] <= fs["tick_s"] and wb["fast_wait"] <= 0.1
    p2 = _pool()
    p2.add_book(ledger=0)
    p2.add_order(p2.books[next(iter(p2.books))])
    _fast(p2, _Venue())                                         # skipped: order_open
    # the accumulators: the split sums to E9's seconds, to the float
    acc = ml._fast_wall
    assert acc["wait"] + acc["work"] == pytest.approx(ml._fast_seconds["s"], abs=1e-6)
    assert acc["work"] > 0.0
    # the full tick publishes both, beside `short.fast`, then resets both
    p3 = _pool()
    p3.add_book(ledger=300)
    st = _tick(p3, _Venue(held={SLUG: 300}))
    fb, wb = st["short"]["fast"], st["short"]["wall"]
    assert fb["n"] == 2 and abs(wb["fast_wait"] + wb["fast_work"] - fb["fast"]) <= 0.15, (wb, fb)
    assert ml._fast_wall == {"wait": 0.0, "work": 0.0} and ml._fast_seconds["s"] == 0.0
    st2 = _tick(p3, _Venue(held={SLUG: 300}), now=NOW + 30)
    assert st2["short"]["wall"]["fast_wait"] == 0.0 and st2["short"]["wall"]["fast_work"] == 0.0
    src = inspect.getsource(ml.fast_tick_once)
    assert src.index("acquired = time.monotonic()") < src.index("_FAST_WOKEN.pop")
    assert src.index('_fast_seconds["s"] += end - started') < src.index('_fast_wall["wait"] += acquired - started') \
        < src.index('_fast_wall["work"] += end - acquired')


def test_e10_fast_wait_is_the_wait_for_the_tick_lock():
    p = _pool()
    p.add_book(ledger=0)
    _walk()
    p.clock = NOW + 1

    async def go():
        async with ml._TICK_LOCK:           # a full tick's hold: the fast tick waits on it
            task = asyncio.create_task(ml.fast_tick_once(p, _Venue(), _Http(), cids=[CID],
                                                         now_ts=NOW + 1))
            await asyncio.sleep(0.12)
            assert not task.done(), "waiting on the hold"
        return await task
    fs = asyncio.run(go())
    wb = fs["short"]["wall"]
    assert fs["fast"]["placed"] == 1
    assert wb["fast_wait"] >= 0.1 and ml._fast_wall["wait"] >= 0.11, wb
    assert wb["fast_work"] < wb["fast_wait"] and ml._fast_wall["work"] < ml._fast_wall["wait"]
    assert fs["tick_s"] >= 0.1, "E9's `fast` still carries the wait; the split names it"


def test_e10_the_mode_line_prints_fast_n_over_work_seconds_and_e9s_token_without_the_block(caplog):
    stats = ml._new_stats()
    stats.update(mode=ml.MODE_ON, whales=["rn1"], books_live=2, orders_open=1)
    stats["short"]["timing"] = {"walk": 1.2, "orders": 0.3, "books": 40.1, "candidates": 12.0,
                                "read": 30, "quiet_skipped": 45, "placed": 4}
    stats["short"]["fast"] = {"fast": 0.4, "n": 2, "markets": 3, "placed": 1, "skipped": 2, "failed": 0,
                              "calls": 6}
    loss = {"sum": -100.0, "books": 1, "limit": 5000.0, "since": "2026-09-07T17:30:00Z"}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS, loss)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " placed=4 fast=2 loss=-100.0/5000.0 since 17:30 venue=None" in line, "E9's line without the block"
    caplog.clear()
    stats["short"]["wall"] = {"books_data_wall": 3.1, "books_venue_wall": 9.0, "books_plan_wall": 2.2,
                              "fast_wait": 0.3, "fast_work": 0.1}
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(dict(stats), ml.MODE_LINE_EVERY_TICKS, loss)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert (" day=None t=1.2/0.3/40.1/12.0 read=30 quiet=45 placed=4 fast=2/0.1s loss=-100.0/5000.0 "
            "since 17:30 venue=None stats={") in line, line
    assert line.index(" fast=") < 200 and line.count(" fast=") == 1
    # a real tick's line
    caplog.clear()
    p = _pool()
    p.add_book(ledger=300)
    st = _tick(p, _Venue(held={SLUG: 300}))
    with caplog.at_level(logging.INFO, logger=ml.log.name):
        ml._mode_line(st, ml.MODE_LINE_EVERY_TICKS)
    line = [r.getMessage() for r in caplog.records if r.getMessage().startswith("mirror_live mode=")][0]
    assert " placed=0 fast=0/0.0s venue=" in line, line


def test_e10_the_e6_e7_e9_blocks_keep_their_keys_and_the_per_call_reading(monkeypatch):
    class _Slow:
        async def wait(self):
            await asyncio.sleep(0.1)

        async def acquire(self, priority=False):
            await asyncio.sleep(0.1)
    monkeypatch.setattr(ratelimit, "_throttle", _Slow())
    p = _pool()
    _many_books(p, 4)
    st = _tick(p, _Venue())
    tm, da = st["short"]["timing"], st["short"]["data_api"]
    assert tuple(tm) == TIMING_KEYS and tuple(da) == DATA_API_KEYS
    # four books' waits of 0.1 s each, SUMMED (the E7 reading), inside `books_data`
    assert tm["read"] >= 4 and da["books_data_wait"] >= 0.35 and tm["books_data"] >= da["books_data_wait"]
    assert st["short"]["wall"]["books_data_wall"] <= 0.5 * da["books_data_wait"], "the sum stays a sum"
    for fn in (ml._timing_block, ml._data_api_block):
        assert "summed per call" in " ".join((fn.__doc__ or "").split()), fn.__name__
    assert 'timing["books_data_wait"] += float(split.get("wait")' in inspect.getsource(ml._market_snap)


def test_e10_data_rps_prints_the_rate_the_throttle_runs_at(monkeypatch):
    p = _pool()
    t = ml._Tick(pool=p, pmus=_Venue(), http=_Http(), now=NOW, stats=ml._new_stats())
    assert ml._data_api_block(t)["data_rps"] == 6.0
    monkeypatch.setattr(ratelimit, "settings", lambda: SimpleNamespace(data_api_max_rps=12.0))
    assert ml._data_api_block(t)["data_rps"] == 6.0, "the env raised nothing"
    monkeypatch.setattr(ratelimit, "settings", lambda: SimpleNamespace(data_api_max_rps=3.0))
    assert ml._data_api_block(t)["data_rps"] == 3.0

    def boom():
        raise RuntimeError("no settings")
    monkeypatch.setattr(ratelimit, "settings", boom)
    assert ml._data_api_block(t)["data_rps"] is None
    assert "ratelimit.data_api_rate()" in inspect.getsource(ml._data_api_block)


# ------------------------------------ part 5: the pins that stand, the docs

def test_e10_the_source_pins_of_e6_e7_e9_hold_and_the_docs_carry_section_31():
    mp = inspect.getsource(we.market_positions)
    assert "data_api_throttle().wait()" in mp and '"sizeThreshold": 0})' in mp
    assert "venue_pace" not in mp.split('"""')[2]
    snap = inspect.getsource(ml._market_snap)
    assert "data_api_max_rps" not in snap and "abs(t.now - ts) > ms.SNAP_MAX_AGE_S" in snap
    assert "asyncio.wait_for" in snap and "_SNAP_READ_TIMEOUT_S" in snap
    for fn in (ml._tick_book, ml._read_market, ml._bbo, ml._market_snap, ml._snapshot):
        s = inspect.getsource(fn)
        assert "t.fast" not in s and "fast_tick" not in s, fn.__name__
    assert "await _tick_book(t, fresh)" in inspect.getsource(ml._fast_book)
    # not in scope, untouched
    assert "READ_PACING_S = 0.35" in inspect.getsource(ml.ms)
    assert 'capped_env("MIRROR_SNAP_MAX_AGE_S", 300.0, floor=120.0)' in inspect.getsource(ml.ms)
    assert ml.VENUE_CALLS_PER_TICK == 60 and ml.MAX_MARKETS_PER_TICK == 40 and ml._SNAP_READ_TIMEOUT_S == 5.0
    # the module paragraph and the docs
    para = inspect.getsource(ml)
    assert "THE PRIORITY LANE AND THE WALL CLOCK (E10" in para and "PRIORITY_BURST = 12" in para
    doc = DOCS.read_text()
    i = doc.index("## 31. E10")
    sec = doc[i:]
    for k in WALL_KEYS + ("PRIORITY_BURST", "priority=True", "positions_sync_interval_seconds",
                          "_fetch_positions", "fast=N/W.Ws"):
        assert k in sec, k
