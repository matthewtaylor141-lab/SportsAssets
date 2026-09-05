"""The two venue board sweeps behind the desk, bounded and instrumented.

2026-09-05, Render service sportsassets-api, 2 GiB plan: oomKilled at
19:11:36Z, 19:37:50Z and 19:48:41Z. A probe polling /healthz every 5 s
read rss_mb 1213 flat, then 1364, 1432, 1434 flat, 1684, DOWN — and the
API's own log in that window showed only two heavy things: the US
venue's event listing (twelve pages of a hundred events with full
boards, run by the warm loop AND on three request paths with no
single-flight) and the Kalshi board (twelve series at limit=1000 fetched
concurrently, no cache, 429s on two series silently dropped). The memory
census attributed 0 MB of the 1.2 GB baseline to any cache: the cost was
transient allocation in overlapping sweeps.

Each test here names the assertion that goes red if its change is
reverted; the response shapes are pinned against the pre-change code
copied in below as an oracle. Concurrency is observed through gates
(threading.Event / asyncio.Event) and a lock that reports its waiters,
never through a wall-clock threshold a slow runner could miss.
"""

import asyncio
import io
import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sportsassets import pmus, procmem
from sportsassets.api import app as app_mod


def _lines(caplog, level: int, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.levelno == level and r.getMessage().startswith(prefix)]


class _Clock:
    """A time() the test can push forward, over the real clock, so a
    fake venue visit can 'take' longer than a TTL without sleeping.
    For the API module it stands in for `time` itself (every other name
    is the real module's); pmus imports `time` inside the function, so
    there the test patches time.time with clock.time for its own
    duration — the loop, the fakes, threading and pytest read other
    clocks (monotonic, perf_counter, or a reference bound at import)."""

    def __init__(self):
        self._real = time.time
        self.offset = 0.0

    def time(self) -> float:
        return self._real() + self.offset

    def __getattr__(self, name: str):
        return getattr(time, name)


# ── (1)(2) list_desk_events: single-flight + the INFO line ───────────


def _pm_event(i: int) -> dict:
    return {"slug": f"mlb-a{i}-b{i}-2026-09-05", "title": f"A{i} vs B{i}",
            "startTime": "2026-09-05T23:00:00Z",
            "endTime": "2026-09-06T03:00:00Z", "volume": 10.0 + i,
            "markets": [{"question": f"A{i} vs B{i}", "marketSides": [
                {"identifier": f"atc-mlb-a{i}-b{i}-2026-09-05-a{i}",
                 "description": f"A{i}", "price": 0.55},
                {"identifier": f"atc-mlb-a{i}-b{i}-2026-09-05-b{i}",
                 "description": f"B{i}", "price": 0.47}]}]}


class _FakeEvents:
    """The SDK's events.list: a probe call (no offset) answers the first
    variant; paged calls answer two pages, 100 + 5 events, plus one
    event with no board yet (listed, never on the desk — so the INFO
    line's 'with markets / seen' counts differ). `entered` is set by
    the first call, and every call then waits on `gate` — a gated fake
    keeps it closed until the test opens it, so the test KNOWS a sweep
    is inside the venue call holding the lock."""

    def __init__(self, gated: bool = False):
        self.calls: list[dict] = []
        self.pages = [[_pm_event(i) for i in range(100)],
                      [_pm_event(100 + i) for i in range(5)]
                      + [{"slug": "mlb-x-y-2026-09-05", "title": "X vs Y",
                          "markets": []}]]
        self.entered = threading.Event()
        self.gate = threading.Event()
        if not gated:
            self.gate.set()

    def list(self, params: dict) -> dict:
        self.calls.append(dict(params))
        self.entered.set()
        assert self.gate.wait(timeout=10), "the test never opened the gate"
        off = params.get("offset")
        if off is None:
            return {"events": self.pages[0]}
        idx = off // 100
        return {"events": self.pages[idx] if idx < len(self.pages) else []}


class _FakeClient:
    def __init__(self, events):
        self.events = events


class _SpyLock:
    """threading.Lock plus an Event set when a caller BLOCKS on it, so a
    test knows a waiter is waiting instead of sleeping and hoping. The
    try-acquire (blocking=False) never sets it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if blocking and self._lock.locked():
            self.waiting.set()
        return self._lock.acquire(blocking, timeout)

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()


def _wire_pm(monkeypatch, gated: bool = False) -> _FakeEvents:
    fake = _FakeEvents(gated)
    monkeypatch.setattr(pmus, "_get_client", lambda: _FakeClient(fake))
    monkeypatch.setattr(pmus, "_desk_cache", {
        "ts": 0.0, "events": [], "blind_at": 0.0, "warned_at": 0.0})
    monkeypatch.setattr(pmus, "_desk_sweep_lock", threading.Lock())
    return fake


def _sweeps(fake: _FakeEvents) -> int:
    # one variant probe (no offset) per real sweep
    return sum(1 for c in fake.calls if "offset" not in c)


def _in_thread(fn) -> tuple[threading.Thread, dict]:
    """fn on a thread; its return value lands in box['result']."""
    box: dict = {}

    def run():
        box["result"] = fn()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, box


def _joined(t: threading.Thread, box: dict):
    t.join(timeout=10)
    assert "result" in box, "the thread never returned"
    return box["result"]


def test_cold_cache_two_threads_run_one_sweep_and_share_it(monkeypatch):
    """Reverting the lock: two probes -> _sweeps == 2 fails here."""
    fake = _wire_pm(monkeypatch, gated=True)
    lock = _SpyLock()
    monkeypatch.setattr(pmus, "_desk_sweep_lock", lock)
    a, box_a = _in_thread(pmus.list_desk_events)
    assert fake.entered.wait(timeout=10)       # a is sweeping, lock held
    b, box_b = _in_thread(pmus.list_desk_events)
    assert lock.waiting.wait(timeout=10), "a cold cache waits"
    fake.gate.set()
    got_a, got_b = _joined(a, box_a), _joined(b, box_b)
    assert _sweeps(fake) == 1, "a cold cache must wait, not sweep twice"
    assert got_b is got_a
    assert len(got_a) == 105, "the waiter did not answer an empty board"


def test_expired_cache_serves_stale_while_one_thread_revalidates(
        monkeypatch):
    """Reverting stale-while-revalidate: the second caller blocks on the
    lock behind the gated sweep and never answers `is stale` (the fake's
    own 10 s gate fails it); reverting the lock, it sweeps too and
    _sweeps == 3."""
    fake = _wire_pm(monkeypatch)
    stale = pmus.list_desk_events()
    assert _sweeps(fake) == 1
    pmus._desk_cache["ts"] = 0.0                    # TTL elapsed
    fake.entered.clear()
    fake.gate.clear()
    a, box = _in_thread(pmus.list_desk_events)      # the revalidator
    assert fake.entered.wait(timeout=10)           # inside the venue call
    assert pmus.list_desk_events() is stale, (
        "the other caller answers at once with the board it already had")
    assert _sweeps(fake) == 2, "exactly one thread revalidates"
    fake.gate.set()
    fresh = _joined(a, box)
    assert fresh is not stale and len(fresh) == 105
    assert pmus._desk_cache["events"] is fresh
    assert pmus.list_desk_events() is fresh


def test_fresh_cache_answers_without_the_lock_or_a_sweep(monkeypatch):
    fake = _wire_pm(monkeypatch)
    first = pmus.list_desk_events()
    assert pmus.list_desk_events() is first
    assert _sweeps(fake) == 1


def test_cold_caller_stops_waiting_after_one_ttl_and_says_so(
        monkeypatch, caplog):
    """One wedged sweep must not park every cold caller's thread in
    silence. Reverting the timed acquire: b and c block behind the
    gated sweep and `_joined` fails; reverting the rate limit: two
    lines. The TTL is patched to 0.5 s — the two waiters time out
    within microseconds of each other, well inside that window."""
    fake = _wire_pm(monkeypatch, gated=True)
    monkeypatch.setattr(pmus, "_DESK_TTL_S", 0.5)
    caplog.set_level(logging.INFO, logger="sportsassets.pmus")
    holder, box = _in_thread(pmus.list_desk_events)
    assert fake.entered.wait(timeout=10)   # wedged inside the venue call
    b, box_b = _in_thread(pmus.list_desk_events)
    c, box_c = _in_thread(pmus.list_desk_events)
    assert _joined(b, box_b) == [] and _joined(c, box_c) == []
    assert len(fake.calls) == 1, "neither timed-out caller swept"
    assert _lines(caplog, logging.WARNING, "desk sweep US:") == [
        "desk sweep US: a sweep has held the lock for more than 0.5s; "
        "serving the cache"], "two callers timed out; ONE line"
    fake.gate.set()
    assert len(_joined(holder, box)) == 105
    slow = [w for w in _lines(caplog, logging.WARNING, "desk sweep US:")
            if w.startswith("desk sweep US: the sweep took")]
    assert len(slow) == 1 and "longer than the 0.5s TTL" in slow[0]


def test_waiters_behind_a_blind_sweep_do_not_probe_again(monkeypatch):
    """Reverting the blind stamp: the waiter wakes to an empty cache and
    runs the four-variant ladder itself -> 8 calls."""
    fake = _wire_pm(monkeypatch, gated=True)
    fake.pages = [[], []]                  # every variant answers empty
    lock = _SpyLock()
    monkeypatch.setattr(pmus, "_desk_sweep_lock", lock)
    a, box_a = _in_thread(pmus.list_desk_events)
    assert fake.entered.wait(timeout=10)
    b, box_b = _in_thread(pmus.list_desk_events)
    assert lock.waiting.wait(timeout=10)
    fake.gate.set()
    assert _joined(a, box_a) == [] and _joined(b, box_b) == []
    assert len(fake.calls) == 4, "one probe ladder, not one per waiter"
    assert pmus._desk_cache["blind_at"] > 0.0


def test_waiters_behind_a_boardless_sweep_do_not_sweep_again(monkeypatch):
    """The OTHER way a sweep ends with nothing to cache: the venue lists
    events and none of them carries a board. Reverting that blind
    stamp (the `else` after the paging loop): the waiter wakes to an
    empty cache and runs its own probe + page -> 4 calls, not 2."""
    fake = _wire_pm(monkeypatch, gated=True)
    fake.pages = [[{"slug": "mlb-x-y-2026-09-05", "title": "X vs Y",
                    "markets": []}], []]
    lock = _SpyLock()
    monkeypatch.setattr(pmus, "_desk_sweep_lock", lock)
    a, box_a = _in_thread(pmus.list_desk_events)
    assert fake.entered.wait(timeout=10)
    b, box_b = _in_thread(pmus.list_desk_events)
    assert lock.waiting.wait(timeout=10)
    fake.gate.set()
    assert _joined(a, box_a) == [] and _joined(b, box_b) == []
    assert len(fake.calls) == 2, (
        "one probe + one page; the waiter did not sweep")
    assert pmus._desk_cache["blind_at"] > 0.0


def test_a_sweep_longer_than_the_ttl_is_stamped_when_it_lands(monkeypatch):
    """blind_at and ts are taken AFTER the venue visit. Stamping the
    clock the caller read before it (the first cut): each of the four
    ladder calls here takes 31 s, so the blind stamp is two minutes old
    by the time the waiter reads it and the waiter runs the ladder
    again (8 calls); and a board that took 62 s to fetch is stale on
    arrival, so the next caller sweeps at once (8 calls, not 6)."""
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock.time)
    fake = _wire_pm(monkeypatch, gated=True)
    fake.pages = [[], []]
    real_list = fake.list

    def slow(params):
        clock.offset += pmus._DESK_TTL_S + 1        # this call took 31 s
        return real_list(params)

    fake.list = slow
    lock = _SpyLock()
    monkeypatch.setattr(pmus, "_desk_sweep_lock", lock)
    a, box_a = _in_thread(pmus.list_desk_events)
    assert fake.entered.wait(timeout=10)
    b, box_b = _in_thread(pmus.list_desk_events)
    assert lock.waiting.wait(timeout=10)
    fake.gate.set()
    assert _joined(a, box_a) == [] and _joined(b, box_b) == []
    assert len(fake.calls) == 4, (
        "the waiter read a blind stamp younger than the TTL: no second "
        "ladder")
    fake.pages = [[_pm_event(i) for i in range(5)], []]
    got = pmus.list_desk_events()                   # probe + one page
    assert len(got) == 5 and len(fake.calls) == 6
    assert pmus.list_desk_events() is got
    assert len(fake.calls) == 6, (
        "stamped when it landed: a 62 s board is fresh on arrival")


def test_one_info_line_per_sweep_with_counts_and_rss(monkeypatch, caplog):
    """Reverting the log line: no 'desk sweep US' record -> len == 0."""
    _wire_pm(monkeypatch)
    caplog.set_level(logging.INFO, logger="sportsassets.pmus")
    pmus.list_desk_events()
    pmus.list_desk_events()                          # fresh: no sweep
    lines = _lines(caplog, logging.INFO, "desk sweep US:")
    assert len(lines) == 1
    (line,) = lines
    assert "pages=2 events=105/106 markets=210" in line, (
        "with markets / seen: the event without a board is seen, not on")
    assert " rss " in line and line.endswith(" MB")
    pmus._desk_cache["ts"] = 0.0
    pmus.list_desk_events()
    assert len(_lines(caplog, logging.INFO, "desk sweep US:")) == 2


def test_blind_sweep_still_writes_its_info_line(monkeypatch, caplog):
    """Reverting the _report() in the no-variant branch: no INFO at all
    for the sweep that matters most to see."""
    fake = _wire_pm(monkeypatch)
    fake.pages = [[], []]
    caplog.set_level(logging.INFO, logger="sportsassets.pmus")
    assert pmus.list_desk_events() == []
    assert len(fake.calls) == 4
    lines = _lines(caplog, logging.INFO, "desk sweep US:")
    assert len(lines) == 1
    assert "pages=0 events=0/0 markets=0" in lines[0]


def test_info_line_prints_an_unreadable_rss_as_a_question_mark(
        monkeypatch, caplog):
    _wire_pm(monkeypatch)
    monkeypatch.setattr(procmem, "rss_mb", lambda: None)
    caplog.set_level(logging.INFO, logger="sportsassets.pmus")
    pmus.list_desk_events()
    (line,) = _lines(caplog, logging.INFO, "desk sweep US:")
    assert line.endswith(" rss ?->? MB")


def test_no_raw_page_outlives_its_slim_rows(monkeypatch):
    """The paging loop must not keep raw pages. Checked from inside the
    fake at the moment page 1 is requested: page 0's raw event list
    must have exactly the references it had before the sweep started
    (the fake's own page table, this test's closure cell, and the
    getrefcount argument). Reverting the `del resp, got` leaves `got`
    and the page-0 response dict bound in the sweep's frame, and
    reverting `probe = None` leaves the probe response holding the same
    list — each is one more reference, and CPython's count is exact.
    gc.get_referrers cannot do this job: a running frame's fast locals
    are invisible to it."""
    fake = _wire_pm(monkeypatch)
    page0 = fake.pages[0]
    seen: dict = {}
    real_list = fake.list

    def spy(params):
        if params.get("offset") == 100:
            seen["refs_at_page1"] = sys.getrefcount(page0)
        return real_list(params)

    base = sys.getrefcount(page0)
    fake.list = spy
    pmus.list_desk_events()
    assert seen["refs_at_page1"] == base, (
        f"page 0's raw list held {seen['refs_at_page1'] - base} extra "
        "reference(s) inside the sweep while page 1 was being fetched")


# ── (3) procmem.rss_mb ───────────────────────────────────────────────


def _proc_status(monkeypatch, text: str) -> None:
    import builtins

    real_open = builtins.open

    def fake_open(path, *a, **kw):
        if str(path) == "/proc/self/status":
            return io.StringIO(text)
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_rss_mb_is_a_float_on_linux_and_none_without_proc(monkeypatch):
    if sys.platform.startswith("linux"):
        v = procmem.rss_mb()
        assert isinstance(v, float) and v > 0
    import builtins

    real_open = builtins.open

    def no_proc(path, *a, **kw):
        if str(path) == "/proc/self/status":
            raise OSError("no procfs")
        return real_open(path, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", no_proc)
        assert procmem.rss_mb() is None


def test_rss_mb_is_vmrss_in_mb_to_one_decimal(monkeypatch):
    """123456 kB / 1024 = 120.5625 -> 120.6. Dividing by 1000 gives
    123.5, not rounding gives 120.5625, reading VmSize gives 868.1."""
    _proc_status(monkeypatch, "Name:\tpython\nVmPeak:\t  999999 kB\n"
                              "VmSize:\t  888888 kB\nVmRSS:\t  123456 kB\n"
                              "VmData:\t       1 kB\n")
    assert procmem.rss_mb() == 120.6


@pytest.mark.parametrize("text", [
    "Name:\tpython\nVmSize:\t  888888 kB\n",      # no VmRSS line
    "VmRSS:\n",                                   # no figure: IndexError
    "VmRSS:\tabc kB\n",                           # not a number: ValueError
])
def test_rss_mb_is_none_when_the_line_is_missing_or_unreadable(
        monkeypatch, text):
    _proc_status(monkeypatch, text)
    assert procmem.rss_mb() is None


def test_rss_label_prints_none_as_a_question_mark(monkeypatch):
    monkeypatch.setattr(procmem, "rss_mb", lambda: None)
    assert procmem.rss_label() == "?"
    monkeypatch.setattr(procmem, "rss_mb", lambda: 120.6)
    assert procmem.rss_label() == "120.6"


# ── (4)(5) _kalshi_sweep: semaphore of 3, dropped series named ───────


def _close_in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kraw(series: str, side: str, close: str | None = None) -> dict:
    return {"ticker": f"{series}-26SEP05XY-{side}",
            "title": f"{series} game Winner?", "yes_sub_title": side,
            "yes_ask": 45, "status": "open", "volume": 1000,
            "close_time": close or "2026-09-06T00:00:00Z"}


def _nested(series: str) -> list[dict]:
    """Two sides for an /events row, closing an hour out — inside any
    window the fallback re-applies client-side."""
    return [_kraw(series, "A", _close_in(1)), _kraw(series, "B", _close_in(1))]


def _one(series: str) -> list[dict]:
    return [_kraw(series, "A", _close_in(1))]


class _Resp:
    def __init__(self, status: int, payload: dict):
        self.status_code = status
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Gauge:
    def __init__(self):
        self.live = 0
        self.peak = 0
        self.seen: list[tuple[str, str]] = []


def _wire_httpx(monkeypatch, gauge: _Gauge, status_for: dict | None = None,
                delay: float = 0.05, markets=None, events=None,
                delay_for: dict | None = None):
    """A stand-in for httpx.AsyncClient against the venue.

    status_for: {series: code} answers /markets with that status;
    {(path, series): code | Exception} answers that one surface — an
    Exception instance is raised from get(), the way a transport error
    is. markets(series) -> the /markets rows (default two sides);
    events(series) -> rows nested under one event on /events (default
    none: the surface answers, empty). delay_for: {series: seconds}
    overrides `delay` for that series on both surfaces."""
    rules = {(k if isinstance(k, tuple) else ("/markets", k)): v
             for k, v in (status_for or {}).items()}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, path, params=None):
            series = (params or {}).get("series_ticker")
            gauge.live += 1
            gauge.peak = max(gauge.peak, gauge.live)
            try:
                await asyncio.sleep((delay_for or {}).get(series, delay))
            finally:
                gauge.live -= 1
            gauge.seen.append((path, series))
            rule = rules.get((path, series), 200)
            if isinstance(rule, Exception):
                raise rule
            if rule != 200:
                return _Resp(rule, {})
            if path == "/markets":
                rows = (markets(series) if markets
                        else [_kraw(series, "A"), _kraw(series, "B")])
                return _Resp(200, {"markets": rows})
            rows = events(series) if events else []
            return _Resp(200, {"events": [
                {"event_ticker": f"{series}-26SEP05XY", "markets": rows}]
                if rows else []})

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


async def test_kalshi_fetch_keeps_at_most_three_responses_live(monkeypatch):
    """Reverting the semaphore: gather fires all twelve at once and
    gauge.peak == 12 fails the `<= 3` assertion."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge)
    series = list(app_mod._DESK_KALSHI_SERIES)
    assert len(series) == 12
    out = await app_mod._kalshi_fetch(series, max_close_h=168, cap=None)
    assert gauge.peak <= 3
    assert gauge.peak == 3, "the gather stays: three in flight, not one"
    assert {s for _, s in gauge.seen} == set(series), "every series fetched"
    assert len(out) == 24


async def test_kalshi_events_fallback_is_under_the_same_semaphore(
        monkeypatch):
    """/markets empty for every series forces the /events fallback for
    every series. Replacing its `async with sem:` with `if True:` fires
    all twelve at once -> peak 12."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, markets=lambda s: [], events=_nested)
    series = list(app_mod._DESK_KALSHI_SERIES)
    out = await app_mod._kalshi_fetch(series, max_close_h=168, cap=None)
    assert sum(1 for p, _ in gauge.seen if p == "/events") == 12
    assert gauge.peak <= 3
    assert len(out) == 24


async def test_board_names_a_series_whose_call_raised(monkeypatch, caplog):
    """Deleting `dropped[series] = type(exc).__name__` in _series: the
    timeout is swallowed as before and no warning is written."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0, markets=_one, status_for={
        ("/markets", "KXNHLGAME"): httpx.ReadTimeout("slow")})
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    out = await app_mod._kalshi_fetch_boards(
        list(app_mod._DESK_KALSHI_SERIES))
    assert len(out) == 11
    assert _lines(caplog, logging.WARNING, "kalshi board:") == [
        "kalshi board: 1 of 12 series dropped: KXNHLGAME ReadTimeout"]


async def test_board_fallback_bookkeeping_names_the_right_series(
        monkeypatch, caplog):
    """/markets: 429 for KXATPDOUBLES, empty for the rest — so every
    series falls back to /events, where KXWTADOUBLES is refused,
    KXNHLGAME times out and everyone else has markets. Dropping the
    fallback's non-200 record loses KXWTADOUBLES; dropping its
    exception record loses KXNHLGAME; dropping the pop names
    KXATPDOUBLES, whose markets ARE on the board."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0, markets=lambda s: [],
                events=_one, status_for={
                    "KXATPDOUBLES": 429,
                    ("/events", "KXWTADOUBLES"): 429,
                    ("/events", "KXNHLGAME"): httpx.ReadTimeout("slow")})
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    series = list(app_mod._DESK_KALSHI_SERIES)
    out = await app_mod._kalshi_fetch_boards(series)
    warns = _lines(caplog, logging.WARNING, "kalshi board:")
    assert warns == ["kalshi board: 2 of 12 series dropped: "
                     "KXNHLGAME ReadTimeout, KXWTADOUBLES 429"]
    assert "KXATPDOUBLES" not in warns[0]
    assert {m["series"] for m in out} == (
        set(series) - {"KXNHLGAME", "KXWTADOUBLES"})
    assert len(out) == 10


async def test_board_keeps_the_markets_status_when_events_answers_empty(
        monkeypatch, caplog):
    """A 200 from /events with nothing in it does not clear a series'
    /markets refusal. Popping on any 200: no warning at all."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0, markets=lambda s: [],
                events=lambda s: [] if s == "KXATPDOUBLES" else _one(s),
                status_for={"KXATPDOUBLES": 429})
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    out = await app_mod._kalshi_fetch_boards(
        list(app_mod._DESK_KALSHI_SERIES))
    assert _lines(caplog, logging.WARNING, "kalshi board:") == [
        "kalshi board: 1 of 12 series dropped: KXATPDOUBLES 429"]
    assert len(out) == 11


async def test_board_warning_lists_dropped_series_in_series_list_order(
        monkeypatch, caplog):
    """KXATPDOUBLES answers last (0.2 s) and KXWTADOUBLES first, so a
    join over dropped.items() would name WTA first. Also the one INFO
    line for the board: series=12, both partitions in one count."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0,
                delay_for={"KXATPDOUBLES": 0.2},
                status_for={"KXATPDOUBLES": 429, "KXWTADOUBLES": 429})
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    series = list(app_mod._DESK_KALSHI_SERIES)
    out = await app_mod._kalshi_fetch_boards(series)
    assert _lines(caplog, logging.WARNING, "kalshi board:") == [
        "kalshi board: 2 of 12 series dropped: "
        "KXATPDOUBLES 429, KXWTADOUBLES 429"]
    assert {m["series"] for m in out} == (
        set(series) - {"KXATPDOUBLES", "KXWTADOUBLES"})
    assert len(out) == 20
    infos = _lines(caplog, logging.INFO, "desk sweep kalshi:")
    assert len(infos) == 1
    assert "series=12 markets=20 " in infos[0]
    assert " rss " in infos[0] and infos[0].endswith(" MB")


async def test_board_all_good_logs_no_warning(monkeypatch, caplog):
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge)
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    await app_mod._kalshi_fetch_boards(["KXMLBGAME"])
    assert not _lines(caplog, logging.WARNING, "kalshi board:")


async def test_kalshi_info_line_is_per_sweep_not_per_cache_hit(
        monkeypatch, caplog):
    """A log.info before the cached return would write two."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0)
    caplog.set_level(logging.INFO, logger="sportsassets.api.app")
    await app_mod.api_desk_feed(venue="kalshi", league="mlb")
    await app_mod.api_desk_feed(venue="kalshi", league="mlb")   # cache hit
    infos = _lines(caplog, logging.INFO, "desk sweep kalshi:")
    assert len(infos) == 1
    assert "series=1 markets=2 " in infos[0] and infos[0].endswith(" MB")


async def test_search_path_logs_at_debug_only(monkeypatch, caplog):
    """api_kalshi_markets runs one fetch per search; that is not a
    board sweep and must not write the board's WARNING or INFO."""
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0,
                status_for={"KXATPDOUBLES": 429})
    caplog.set_level(logging.DEBUG, logger="sportsassets.api.app")
    await app_mod._kalshi_fetch(list(app_mod._DESK_KALSHI_SERIES), q="x")
    assert not _lines(caplog, logging.WARNING, "kalshi board:")
    assert not _lines(caplog, logging.INFO, "desk sweep kalshi:")
    assert _lines(caplog, logging.DEBUG, "kalshi search:") == [
        "kalshi search: 1 of 12 series dropped: KXATPDOUBLES 429"]


# ── (6) _kalshi_fetch_boards: 20 s cache, single-flight, SWR ─────────


def _counting_sweep(monkeypatch, gate: asyncio.Event | None = None) -> list:
    """Stand-in for _kalshi_sweep: one entry in `calls` per partition
    swept, nothing refused; parks on `gate` while the test holds it."""
    calls: list = []

    async def fake_sweep(series_list, q="", max_close_h=None, cap=60):
        calls.append(list(series_list))
        await asyncio.sleep(0)
        if gate is not None:
            await gate.wait()
        return [{"ticker": f"{s}-26SEP05AB-A", "series": s,
                 "fetch_n": len(calls)} for s in series_list], {}

    monkeypatch.setattr(app_mod, "_kalshi_sweep", fake_sweep)
    return calls


async def _settled(pred, timeout: float = 1.0) -> None:
    async def _spin():
        while not pred():
            await asyncio.sleep(0.001)

    await asyncio.wait_for(_spin(), timeout)


async def test_boards_two_concurrent_awaits_one_fetch(monkeypatch):
    """Reverting the cache: len(calls) == 2 fails the first assertion."""
    calls = _counting_sweep(monkeypatch)
    a, b = await asyncio.gather(app_mod._kalshi_fetch_boards(["KXMLBGAME"]),
                                app_mod._kalshi_fetch_boards(["KXMLBGAME"]))
    assert len(calls) == 1
    assert a is b


async def test_boards_ttl_is_twenty_seconds(monkeypatch):
    calls = _counting_sweep(monkeypatch)
    boards = app_mod._kalshi_fetch_boards
    first = await boards(["KXMLBGAME"])
    assert len(calls) == 1
    assert await boards(["KXMLBGAME"]) is first
    assert len(calls) == 1, "a call within 20 s does not fetch"
    assert app_mod._KALSHI_BOARD_TTL_S == 20.0
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    slot["ts"] = time.time() - 19.5
    assert await boards(["KXMLBGAME"]) is first
    assert len(calls) == 1 and slot["refresh"] is None
    slot["ts"] = time.time() - 20.5
    assert await boards(["KXMLBGAME"]) is first, "stale: served as it is"
    await slot["refresh"]                          # ...and refreshed behind
    assert len(calls) == 2
    again = await boards(["KXMLBGAME"])
    assert again is not first and len(calls) == 2
    # a different series set is its own key, its own fetch
    await boards(["KXNBAGAME"])
    assert len(calls) == 3 and calls[-1] == ["KXNBAGAME"]


async def test_boards_stale_board_is_served_now_and_refreshed_once_behind(
        monkeypatch):
    """Reverting stale-while-revalidate: the first stale caller awaits
    the sweep on the request path, parked on the gate, and wait_for
    times out. Reverting the single refresh: len(calls) == 3. Dropping
    the `task is None or task.done()` guard: every stale call starts a
    task of its own — each parks on the slot lock behind the first, so
    the sweep count cannot see them — and slot['refresh'] is a new
    task after each stale call."""
    gate = asyncio.Event()
    gate.set()
    calls = _counting_sweep(monkeypatch, gate)
    boards = app_mod._kalshi_fetch_boards
    first = await boards(["KXMLBGAME"])
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    slot["ts"] -= 21
    gate.clear()                     # the next sweep parks until told
    stale = await asyncio.wait_for(boards(["KXMLBGAME"]), timeout=1.0)
    assert stale is first, "a stale caller answers with the board it has"
    task = slot["refresh"]
    await _settled(lambda: len(calls) == 2)   # the ONE refresh is inside
    assert not task.done()
    a, b = await asyncio.wait_for(asyncio.gather(
        boards(["KXMLBGAME"]), boards(["KXMLBGAME"])), timeout=1.0)
    assert a is first and b is first
    assert slot["refresh"] is task, (
        "one refresh task per stale window, not one per stale call")
    third = await asyncio.wait_for(boards(["KXMLBGAME"]), timeout=1.0)
    assert third is first
    assert slot["refresh"] is task
    assert len(calls) == 2, "four stale callers: one refresh"
    gate.set()
    await task
    new = await boards(["KXMLBGAME"])
    assert new is not first and new[0]["fetch_n"] == 2
    assert len(calls) == 2


async def test_boards_two_refreshes_on_one_slot_sweep_once(monkeypatch):
    """_kalshi_board_refresh started twice on one stale slot, directly.
    Dropping its `async with slot["lock"]`: both are inside the venue
    at once and len(calls) == 3 before the gate opens. Dropping the TTL
    re-check under the lock: the second takes the lock after the first
    landed and sweeps the fresh board again -> len(calls) == 3 after
    the gather."""
    gate = asyncio.Event()
    gate.set()
    calls = _counting_sweep(monkeypatch, gate)
    first = await app_mod._kalshi_fetch_boards(["KXMLBGAME"])
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    slot["ts"] -= 21
    gate.clear()
    refresh = app_mod._kalshi_board_refresh
    t1 = asyncio.create_task(refresh(["KXMLBGAME"], slot))
    t2 = asyncio.create_task(refresh(["KXMLBGAME"], slot))
    await _settled(lambda: len(calls) >= 2)         # the first is inside
    for _ in range(3):
        await asyncio.sleep(0)                      # the second had its turns
    assert len(calls) == 2, "one refresh is inside the venue, not two"
    assert slot["lock"].locked(), "...and it holds the slot lock"
    gate.set()
    await asyncio.gather(t1, t2)
    assert len(calls) == 2, (
        "the second refresh woke to a fresh board and did not sweep")
    assert slot["board"] is not first and slot["board"][0]["fetch_n"] == 2


async def test_boards_blind_refresh_keeps_the_board_and_backs_off(
        monkeypatch, caplog):
    """A good board, then a refresh in which every series is 429 on
    both surfaces. Dropping the keep branch in _kalshi_board_fill: the
    board is [] after the refresh, for every caller, for 20 s."""
    series = list(app_mod._DESK_KALSHI_SERIES)
    _wire_httpx(monkeypatch, _Gauge(), delay=0.0)
    boards = app_mod._kalshi_fetch_boards
    good = await boards(series)
    assert len(good) == 24
    slot = app_mod._kalshi_board_cache[tuple(series)]
    slot["ts"] -= 21
    blind = _Gauge()
    _wire_httpx(monkeypatch, blind, delay=0.0, status_for={
        (p, s): 429 for s in series for p in ("/markets", "/events")})
    caplog.set_level(logging.WARNING, logger="sportsassets.api.app")
    assert await boards(series) is good
    await slot["refresh"]
    assert _lines(caplog, logging.WARNING, "kalshi board:") == [
        "kalshi board: 12 of 12 series dropped: "
        + ", ".join(f"{s} 429" for s in series)]
    assert slot["board"] is good, "a blind sweep does not blank the board"
    assert len(blind.seen) == 24            # 12 /markets + 12 /events, once
    assert await boards(series) is good
    assert len(blind.seen) == 24, "the venue gets its 20 s: no second fetch"
    assert time.time() - slot["ts"] < app_mod._KALSHI_BOARD_TTL_S


async def test_boards_one_refusal_on_an_empty_answer_keeps_the_board(
        monkeypatch, caplog):
    """The keep rule is ANY refusal, not every series refused: a good
    board, then a refresh in which KXATPDOUBLES is 429 on both surfaces
    and the other eleven answer 200 and empty. Narrowing the rule to
    'all refused' (len(dropped) == len(series_list)) passes the test
    above and replaces the board with [] here."""
    series = list(app_mod._DESK_KALSHI_SERIES)
    _wire_httpx(monkeypatch, _Gauge(), delay=0.0)
    boards = app_mod._kalshi_fetch_boards
    good = await boards(series)
    assert len(good) == 24
    slot = app_mod._kalshi_board_cache[tuple(series)]
    slot["ts"] -= 21
    _wire_httpx(monkeypatch, _Gauge(), delay=0.0, markets=lambda s: [],
                status_for={"KXATPDOUBLES": 429,
                            ("/events", "KXATPDOUBLES"): 429})
    caplog.set_level(logging.WARNING, logger="sportsassets.api.app")
    assert await boards(series) is good
    await slot["refresh"]
    assert _lines(caplog, logging.WARNING, "kalshi board:") == [
        "kalshi board: 1 of 12 series dropped: KXATPDOUBLES 429"]
    assert slot["board"] is good, (
        "one refusal on an empty answer is not an empty slate")
    assert await boards(series) is good


async def test_boards_a_sweep_longer_than_the_ttl_lands_fresh(monkeypatch):
    """The stamp is taken AFTER the sweep. Stamping before it (the first
    cut): a 21 s sweep lands already stale, the very next caller starts
    a refresh, and a slow venue gets no back-off at all — on the board
    path and on the blind-keep path alike."""
    clock = _Clock()
    monkeypatch.setattr(app_mod, "time", clock)
    calls: list = []
    venue = {"refuses": False}

    async def slow_sweep(series_list, q="", max_close_h=None, cap=60):
        calls.append(list(series_list))
        clock.offset += app_mod._KALSHI_BOARD_TTL_S + 1   # a 21 s sweep
        if venue["refuses"]:
            return [], {s: "429" for s in series_list}
        return [{"ticker": f"{s}-26SEP05AB-A", "series": s}
                for s in series_list], {}

    monkeypatch.setattr(app_mod, "_kalshi_sweep", slow_sweep)
    boards = app_mod._kalshi_fetch_boards
    first = await boards(["KXMLBGAME"])
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    assert await boards(["KXMLBGAME"]) is first
    assert slot["refresh"] is None and len(calls) == 1, (
        "stamped after a 21 s sweep, the board is fresh, not already stale")
    slot["ts"] -= 21
    venue["refuses"] = True
    assert await boards(["KXMLBGAME"]) is first
    task = slot["refresh"]
    await task
    assert len(calls) == 2 and slot["board"] is first
    assert await boards(["KXMLBGAME"]) is first
    assert slot["refresh"] is task and len(calls) == 2, (
        "a slow blind sweep gets its 20 s of back-off too")


async def test_boards_empty_slate_with_no_refusal_clears_the_board(
        monkeypatch):
    _wire_httpx(monkeypatch, _Gauge(), delay=0.0)
    boards = app_mod._kalshi_fetch_boards
    good = await boards(["KXMLBGAME"])
    assert len(good) == 2
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    slot["ts"] -= 21
    # /markets 200 and empty, /events 200 and empty: the slate is empty
    _wire_httpx(monkeypatch, _Gauge(), delay=0.0, markets=lambda s: [])
    assert await boards(["KXMLBGAME"]) is good
    await slot["refresh"]
    assert slot["board"] == []
    assert await boards(["KXMLBGAME"]) == []


async def test_boards_refresh_that_raises_warns_and_keeps_the_board(
        monkeypatch, caplog):
    _counting_sweep(monkeypatch)
    boards = app_mod._kalshi_fetch_boards
    first = await boards(["KXMLBGAME"])

    async def boom(*a, **kw):
        raise RuntimeError("shape")

    monkeypatch.setattr(app_mod, "_kalshi_sweep", boom)
    slot = app_mod._kalshi_board_cache[("KXMLBGAME",)]
    slot["ts"] -= 21
    caplog.set_level(logging.WARNING, logger="sportsassets.api.app")
    assert await boards(["KXMLBGAME"]) is first
    task = slot["refresh"]
    await task                                    # a WARNING, not a crash
    warns = _lines(caplog, logging.WARNING,
                   "kalshi board: background refresh raised")
    assert len(warns) == 1 and "RuntimeError: shape" in caplog.text
    assert await boards(["KXMLBGAME"]) is first
    assert slot["refresh"] is task, "the stamp moved: no new refresh"


async def test_boards_cold_callers_wait_for_the_one_fetch(monkeypatch):
    calls = _counting_sweep(monkeypatch)
    boards = await asyncio.gather(
        *(app_mod._kalshi_fetch_boards(["KXMLBGAME", "KXNBAGAME"])
          for _ in range(5)))
    assert len(calls) == 1
    assert all(b is boards[0] for b in boards)
    assert [m["series"] for m in boards[0]] == ["KXMLBGAME", "KXNBAGAME"]


async def test_boards_partition_and_windows_are_unchanged(monkeypatch):
    seen: list = []

    async def fake_sweep(series_list, q="", max_close_h=None, cap=60):
        seen.append((list(series_list), max_close_h, cap))
        return [], {}

    monkeypatch.setattr(app_mod, "_kalshi_sweep", fake_sweep)
    await app_mod._kalshi_fetch_boards(list(app_mod._DESK_KALSHI_SERIES))
    assert seen == [
        (["KXMLBGAME", "KXWNBAGAME", "KXNBAGAME", "KXNFLGAME", "KXNHLGAME"],
         168, None),
        (list(app_mod._TENNIS_MATCH_SERIES), None, None)]


# ── (7) response shape: byte-identical to the pre-change code ────────
#
# The oracle below IS the pre-change _kalshi_fetch_boards/_kalshi_fetch
# (git HEAD before 2026-09-05's bounding), copied verbatim; only the
# three module-level names it reads are bound to the live module.

_kalshi_shape = app_mod._kalshi_shape
KALSHI_PUBLIC_API = app_mod.KALSHI_PUBLIC_API
_TENNIS_MATCH_SERIES = app_mod._TENNIS_MATCH_SERIES


async def _oracle_fetch_boards(series_list: list[str]) -> list[dict]:
    tennis = [x for x in series_list if x in _TENNIS_MATCH_SERIES]
    rest = [x for x in series_list if x not in tennis]
    out: list[dict] = []
    if rest:
        out += await _oracle_fetch(rest, max_close_h=168, cap=None)
    if tennis:
        out += await _oracle_fetch(tennis, max_close_h=None, cap=None)
    return out


async def _oracle_fetch(series_list: list[str], q: str = "",
                        max_close_h: int | None = None,
                        cap: int | None = 60) -> list[dict]:
    import asyncio as _asyncio
    import time as _time

    import httpx

    from sportsassets.team_aliases import matches as _team_match

    ql = q.strip()
    out: list[dict] = []
    base_params: dict = {"limit": 1000,
                         "min_close_ts": int(_time.time())}
    if max_close_h is not None:
        base_params["max_close_ts"] = int(_time.time()) + max_close_h * 3600

    def _keep(m: dict, series: str) -> None:
        if (m.get("status") or "open") not in ("open", "active"):
            return
        title = m.get("title") or ""
        sub = m.get("yes_sub_title") or m.get("subtitle") or ""
        if ql and not _team_match(ql, [title, sub, m.get("ticker")]):
            return
        out.append(_kalshi_shape(m, series))

    async def _series(client: httpx.AsyncClient, series: str) -> None:
        try:
            resp = await client.get("/markets", params={
                **base_params, "series_ticker": series})
            if resp.status_code != 200:
                return
            for m in (resp.json().get("markets") or []):
                _keep(m, series)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return

    async def _series_events(client: httpx.AsyncClient,
                             series: str) -> None:
        try:
            resp = await client.get("/events", params={
                "series_ticker": series, "status": "open",
                "with_nested_markets": "true", "limit": 200})
            if resp.status_code != 200:
                return
            hi = base_params.get("max_close_ts")
            for ev in (resp.json().get("events") or []):
                for m in (ev.get("markets") or []):
                    if m.get("status") not in (None, "open", "active"):
                        continue
                    ct = m.get("close_time") or ""
                    if hi and ct:
                        try:
                            from datetime import datetime as _dt
                            if _dt.fromisoformat(
                                    ct.replace("Z", "+00:00")
                            ).timestamp() > hi:
                                continue
                        except ValueError:
                            pass
                    _keep(m, series)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return

    try:
        async with httpx.AsyncClient(base_url=KALSHI_PUBLIC_API,
                                     timeout=10) as client:
            await _asyncio.gather(*(_series(client, s)
                                    for s in series_list))
            if not out:
                await _asyncio.gather(*(_series_events(client, s)
                                        for s in series_list))
    except Exception:  # noqa: BLE001
        pass
    out.sort(key=lambda m: (m.get("close_time") or ""))
    return out[:cap] if cap else out


def _mlb_slate(series: str) -> list[dict]:
    """Two games, one in play, one settled row the desk must drop, one
    row without a volume; mixed close times so the sort matters."""
    def row(game, side, ask, close, status="open", vol=None, extra=None):
        m = {"ticker": f"{series}-{game}-{side}",
             "title": f"{side} at {game} Winner?", "yes_sub_title": side,
             "yes_ask": ask, "yes_bid": ask - 2, "no_ask": 100 - ask + 2,
             "no_bid": 100 - ask, "status": status, "close_time": close}
        if vol is not None:
            m["volume"] = vol
        if extra:
            m.update(extra)
        return m
    return [
        row("26SEP05NYYBOS", "NYY", 45, "2026-09-06T02:00:00Z",
            vol=1000),
        row("26SEP05NYYBOS", "BOS", 57, "2026-09-06T02:00:00Z",
            vol=500, extra={"volume_dollars": "5.00"}),
        row("26SEP05LADSF", "LAD", 61, "2026-09-05T23:10:00Z",
            status="active", vol=2200),
        row("26SEP05LADSF", "SF", 41, "2026-09-05T23:10:00Z"),
        row("26SEP04OLD", "X", 99, "2026-09-05T01:00:00Z",
            status="settled", vol=9),
    ]


async def test_kalshi_mlb_feed_cards_are_byte_identical_to_before(
        monkeypatch):
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0, markets=_mlb_slate)
    with monkeypatch.context() as m:
        m.setattr(app_mod, "_kalshi_fetch_boards", _oracle_fetch_boards)
        before = await app_mod.api_desk_feed(venue="kalshi", league="mlb")
    after = await app_mod.api_desk_feed(venue="kalshi", league="mlb")
    assert json.dumps(before) == json.dumps(after)
    # and the pinned shape, so a change in BOTH paths cannot hide
    assert [c["id"] for c in after["cards"]] == [
        "KXMLBGAME-26SEP05LADSF", "KXMLBGAME-26SEP05NYYBOS"]
    assert after["counts"] == {"mlb": 2, "all": 2}
    nyy = after["cards"][1]
    assert nyy["title"] == "NYY vs BOS"
    assert nyy["volume_usd"] == 15.0
    assert nyy["outcomes"] == [
        {"label": "NYY", "id": "KXMLBGAME-26SEP05NYYBOS-NYY", "price": 0.45},
        {"label": "BOS", "id": "KXMLBGAME-26SEP05NYYBOS-BOS", "price": 0.57}]
    assert nyy["history_id"] == "KXMLBGAME-26SEP05NYYBOS-NYY"
    # the second read is the cached board: still the same bytes
    assert json.dumps(await app_mod.api_desk_feed(
        venue="kalshi", league="mlb")) == json.dumps(before)


async def test_kalshi_all_feed_is_byte_identical_to_before(monkeypatch):
    gauge = _Gauge()
    _wire_httpx(monkeypatch, gauge, delay=0.0)
    with monkeypatch.context() as m:
        m.setattr(app_mod, "_kalshi_fetch_boards", _oracle_fetch_boards)
        before = await app_mod.api_desk_feed(venue="kalshi", league="all")
    after = await app_mod.api_desk_feed(venue="kalshi", league="all")
    assert json.dumps(before) == json.dumps(after)
    assert after["counts"]["all"] == 12


def test_the_warm_loop_and_ttl_move_together_and_only_lengthen():
    """25 s / 30 s -> 120 s / 120 s (2026-09-05): the sweep's own log
    line put ~65,000 raw markets through the allocator every 25 s and
    the API at its 2 GiB kill line five times in an hour. The cadence
    and the TTL are one number so a request between warm ticks reads
    the cache; the environment can lengthen the cadence, never shorten
    it below 120 s (the constant is read at import, so the floor is
    pinned by the expression's text)."""
    import inspect

    assert pmus._DESK_TTL_S == 120.0
    assert app_mod.DESK_WARM_S == 120.0 == pmus._DESK_TTL_S
    src = inspect.getsource(app_mod.lifespan)
    assert "await asyncio.sleep(DESK_WARM_S)" in src.split("_desk_feed_warm_loop")[1]
    assert "asyncio.sleep(25)" not in src
    # the trim loop says what it returned, in the workers' grammar
    assert '"api rss %s MB trim returned %s MB"' in src
    module_src = inspect.getsource(app_mod)
    assert 'DESK_WARM_S = max(120.0, float(os.environ.get("DESK_WARM_S", "120") or 120.0))' in module_src


@pytest.mark.parametrize("name", ["_desk_sweep_lock", "_desk_warn_lock"])
def test_the_sweep_lock_is_a_threading_primitive(name):
    """Every caller runs in asyncio.to_thread — an asyncio.Lock would
    not be visible across those threads. The warn lock guards the
    warned_at compare-and-set the same way, for the same callers."""
    assert isinstance(getattr(pmus, name), type(threading.Lock()))
