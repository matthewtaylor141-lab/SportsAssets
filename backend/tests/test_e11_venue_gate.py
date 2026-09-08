"""E11 (2026-09-08): the venue gate -- fewer quiet quote reads, the live
mirror's claim first.

Owner 22:4xZ ("latency must be flawless and exceptional"). With E10 live
(the 03:22Z heartbeat) the tick was 41.8 s at 87 live books,
`short.timing.books` 25.7 s of wall and `short.wall.books_venue_wall`
22.6 s of it: venue_pace.pace is ONE serial gap of MIN_GAP_S = 0.35 s
for the whole process, 47 claims are 16.4 s of floor whatever the
walk's width, ~6 s more were the shadow's and price_path's claims
interleaving on the same gate, and ~29 of the 47 were E6's quiet
rotation's due reads (87 books / 3 a tick) -- reads that place nothing.
Three parts, the gap and the venue's rate unchanged, pinned here:

  part 1  QUIET_EVERY_TICKS 3 -> 9 through capped_env("MIRROR_QUIET_
          EVERY_TICKS", 9, floor=1): the environment may only lower it;
          the rotation reads a quiet book on the tenth tick and a hot
          book every tick (the E6 harness); the deferred floor and every
          always-read class byte for byte;
  part 2  a PRIORITY CLAIM on the gate (venue_pace.pace): the next gap
          ahead of every waiting normal claimant, both lanes FIFO among
          themselves, at most PACE_PRIORITY_BURST = 12 priority claims in
          a row while a normal waits, the 429 circuit doubling both
          lanes, and THE GAP between ANY two requests process-wide never
          under MIN_GAP_S -- proved with REAL THREADS on a fake clock.
          The lane is the live mirror's TICK'S: tick_once and
          fast_tick_once run inside venue_pace.priority_claims(), a
          context asyncio.to_thread copies into every worker thread the
          tick starts, so the walk's quote reads (ms._paced_bbo, the
          shadow's function, pinned as the mirror's read by E6 / E9),
          step R's walk and the resolver's reads take it beside _paced's
          own; the keyword `priority_claim=True` is the same lane by the
          call (default False; no site in the package says it);
  part 3  `short.gate` = {wait, claims} beside `short.wall` (whose five
          keys the E10 pins fix exactly): the seconds this tick's claims
          spent on the gate, summed per claim as books_data_wait is.

THE HARNESS: venue_pace's `time` is replaced by a fake clock whose
sleep advances it (and, while a test holds the gate, blocks on an event
until the test lets go), so the claimants are REAL THREADS on the REAL
Condition and every instant is exact. A thread's claim instant is the
last monotonic() its own thread read inside pace() -- the record after
its sleep -- immune to the next claimant's advance; every claim advances
the clock by exactly the gap, so the instants order the service.
"""
import ast
import asyncio
import hashlib
import inspect
import pathlib
import re
import threading
import time
import warnings

import pytest

import sportsassets
from sportsassets import ratelimit, venue_pace
from sportsassets.analytics import mirror_live_rules as rules
from sportsassets.api import app as api_app
from sportsassets.workers import mirror_live as ml
from sportsassets.workers import mirror_shadow as ms
from sportsassets.workers import price_path
from tests.test_e10_priority_lane import WALL_KEYS
from tests.test_e6_tick_budget import _bbos, _quiet_book, _timing, _v
from tests.test_e9_fast_path import _fast, _walk
from tests.test_mirror_live_worker import (  # noqa: F401 -- the autouse rails
    CID, M, NOW, SLUG, _Venue, _armed, _census, _fill, _many_books, _pool, _tick,
)

PKG = pathlib.Path(sportsassets.__file__).resolve().parent
DOCS = PKG.parents[1] / "docs" / "mirror-coverage.md"
GAP = venue_pace.MIN_GAP_S
HALTED = "MARKET_STATE_HALTED"
_REAL_MONOTONIC = time.monotonic     # the rails patch time.sleep; the harness deadlines want the real clock
# the E6 rule as built (E6 + its fold): the always-read classes, the
# budget, the deferred queue and the skip -- byte for byte since E11
# touches the constant alone (the same digest on ee03310)
E6_RULE_FUNCTIONS = ("_book_open", "_exit_plan_stands", "_his_fill_since", "_memo_holds", "_hot_by_row",
                     "_memo_skips", "_quiet_budget", "_quiet_slots", "_deferred_due", "_deferred_read_on",
                     "_quiet_skip")
E6_RULE_SHA256 = "d8dcd06a32b2c21a648a0f24f18b480c1695c994137e455d4c5845fa21a571e9"


# ------------------------------------------------------------ the harness

class _Clock:
    """A fake monotonic clock for venue_pace: `sleep(d)` blocks on
    `release` while `hold` is set (the test holds the gate), then
    advances by d; `monotonic()` remembers the last instant each
    thread read, which after pace() returns is that thread's claim."""

    def __init__(self, t=1000.0):
        self.t = t
        self.start = t
        self.lock = threading.Lock()
        self.sleeps: list = []
        self.by_thread: dict = {}
        self.hold = threading.Event()
        self.release = threading.Event()
        self.sleeping = threading.Event()

    def monotonic(self):
        with self.lock:
            v = self.t
        self.by_thread[threading.get_ident()] = v
        return v

    def sleep(self, d):
        self.sleeps.append(float(d))
        if self.hold.is_set():
            self.sleeping.set()
            self.release.wait(5.0)
            self.sleeping.clear()
        with self.lock:
            self.t += max(0.0, float(d))


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(venue_pace, "time", c)
    monkeypatch.setattr(venue_pace, "_last", c.t)       # a whole gap owed at the start
    monkeypatch.setattr(venue_pace, "_penalty_until", 0.0)
    monkeypatch.setattr(venue_pace, "_run", 0)
    yield c
    c.release.set()
    assert venue_pace.waiting() == (0, 0) and not venue_pace._busy, "every harness leaves the gate idle"


def _until(pred, timeout=5.0):
    end = _REAL_MONOTONIC() + timeout
    while not pred():
        assert _REAL_MONOTONIC() < end, "the harness timed out"
        threading.Event().wait(0.0005)


def _claim(clock, name, priority, served, gap=GAP):
    """A real thread making one claim; records (name, claim instant,
    the seconds pace slept) once served."""
    def run():
        try:
            w = venue_pace.pace(gap, priority_claim=priority)
        except _Interrupted:
            served.append((name, None, None))
            return
        served.append((name, clock.by_thread[threading.get_ident()], w))
    th = threading.Thread(target=run, name=name, daemon=True)
    th.start()
    return th


def _queue_one(clock, name, priority, served):
    """Start a claimant and wait until it is REGISTERED in its lane, so
    arrival order is the order the test says."""
    n0, p0 = venue_pace.waiting()
    th = _claim(clock, name, priority, served)
    _until(lambda: venue_pace.waiting() == ((n0, p0 + 1) if priority else (n0 + 1, p0)))
    return th


def _hold(clock, served, name="h", priority=False):
    """A claimant takes the gate and is held inside its sleep until
    `clock.release` is set: what queues behind it queues on the gate."""
    clock.hold.set()
    th = _claim(clock, name, priority, served)
    assert clock.sleeping.wait(5.0), "the holder is sleeping out its gap"
    return th


def _finish(clock, threads):
    clock.release.set()
    for th in threads:
        th.join(5.0)
    assert not any(th.is_alive() for th in threads)


def _order(served):
    return [n for n, t, _ in sorted((s for s in served if s[1] is not None), key=lambda s: s[1])]


def _instants(served):
    return sorted(t for _, t, _ in served if t is not None)


class _Interrupted(BaseException):
    pass


class _InterruptingCV:
    """venue_pace._cv with `wait` raising once for the named thread: a
    claimant interrupted while queued."""

    def __init__(self, real, victim):
        self.real, self.victim, self.fired = real, victim, False

    def __enter__(self):
        return self.real.__enter__()

    def __exit__(self, *a):
        return self.real.__exit__(*a)

    def wait(self, timeout=None):
        if threading.current_thread().name == self.victim and not self.fired:
            self.fired = True
            raise _Interrupted()
        return self.real.wait(timeout)

    def notify_all(self):
        self.real.notify_all()


def _lane_delta(before):
    now = venue_pace.lane_stats()
    return {k: {"wait": round(now[k]["wait"] - before[k]["wait"], 6), "claims": now[k]["claims"] - before[k]["claims"]}
            for k in ("normal", "priority")}


def _real_gate(monkeypatch):
    """The real gate under the mirror's tick: the autouse rails stub
    ml.pace and (through the shadow's _nosleep) ms.pace; both back to
    venue_pace.pace, on the fake clock the `clock` fixture installed."""
    monkeypatch.setattr(ml, "pace", venue_pace.pace)
    monkeypatch.setattr(ms, "pace", venue_pace.pace)


# ------------------------------------------------- the constants, the shape

def test_e11_the_constants_the_signature_and_the_gap_unchanged():
    assert venue_pace.MIN_GAP_S == 0.35 and venue_pace.PENALTY_MULT == 2.0 and venue_pace.PENALTY_S == 600.0
    assert venue_pace.PACE_PRIORITY_BURST == 12 == ratelimit.PRIORITY_BURST, "E10's bound, restated for the gate"
    sig = inspect.signature(venue_pace.pace)
    assert list(sig.parameters) == ["min_gap_s", "priority_claim"]
    assert sig.parameters["min_gap_s"].default == venue_pace.MIN_GAP_S
    assert sig.parameters["priority_claim"].default is False, "an explicit argument, default a normal claim"
    assert "slots" not in sig.parameters, "no reservation (E2 round 3)"
    for name in ("pace", "priority_claims", "waiting", "lane_stats", "penalize", "penalty_left",
                 "effective_gap", "PACE_PRIORITY_BURST"):
        assert name in venue_pace.__all__, name
    assert venue_pace.waiting() == (0, 0) and set(venue_pace.lane_stats()) == {"normal", "priority"}
    assert set(venue_pace.lane_stats()["priority"]) == {"wait", "claims"}
    # the gap every lane claims at: the shadow's, price_path's and the mirror's (ms.READ_PACING_S)
    assert ms.READ_PACING_S == price_path.READ_PACING_S == venue_pace.MIN_GAP_S == 0.35
    # not in scope, untouched
    assert rules.MIRROR_BOOK_CONCURRENCY == 6 and ml.HOT_S == 600.0 and ml.CONFIRM_GONE_WAIT_S == 5.0
    assert ml.DEFERRED_MIN_PER_TICK == 10 and ml.CAND_MIN_PER_TICK == 10 and ml.VENUE_CALLS_PER_TICK == 60


# ----------------------------------------------- part 2: the gate, threaded

def test_e11_the_gap_holds_between_any_two_requests_under_mixed_load_with_real_threads(clock):
    """Eight threads, eight claims each, the lanes alternating per
    claim, all racing: every claim lands exactly one gap after the
    previous one -- the rate is one request per gap whatever the mix,
    and nothing is ever inside a gap."""
    before = venue_pace.lane_stats()
    served: list = []

    def worker(k):
        for j in range(8):
            w = venue_pace.pace(GAP, priority_claim=(k + j) % 2 == 0)
            served.append((f"t{k}-{j}", clock.by_thread[threading.get_ident()], w))
    ths = [threading.Thread(target=worker, args=(k,), daemon=True) for k in range(8)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(10.0)
    assert len(served) == 64 and not any(th.is_alive() for th in ths)
    at = _instants(served)
    gaps = [b - a for a, b in zip(at, at[1:])]
    assert min(gaps) >= GAP - 1e-9, "never two requests inside a gap"
    assert max(gaps) == pytest.approx(GAP), "and never a gap wasted: the rate unchanged"
    assert at[0] == pytest.approx(clock.start + GAP) and at[-1] == pytest.approx(clock.start + 64 * GAP)
    assert len(clock.sleeps) == 64 and all(s == pytest.approx(GAP) for s in clock.sleeps)
    assert all(w == pytest.approx(GAP) for _, _, w in served), "each claim slept its own gap"
    d = _lane_delta(before)
    assert d["normal"]["claims"] == 32 and d["priority"]["claims"] == 32
    assert venue_pace._run == 0


def test_e11_the_429_circuit_doubles_the_gap_for_both_lanes_and_lifts_on_its_own(clock):
    served: list = []
    venue_pace.penalize(clock.t)
    ths = [_claim(clock, f"{lane}{i}", lane == "p", served) for i in range(3) for lane in ("n", "p")]
    for th in ths:
        th.join(5.0)
    at = _instants(served)
    assert len(at) == 6 and all(b - a == pytest.approx(2 * GAP) for a, b in zip(at, at[1:]))
    assert all(s == pytest.approx(2 * GAP) for s in clock.sleeps), "both lanes at the doubled gap"
    assert venue_pace.penalty_left(clock.t) > 590.0
    # the window passes: the plain gap again, in either lane
    clock.t += venue_pace.PENALTY_S + 1
    clock.sleeps.clear()
    assert venue_pace.pace(GAP, priority_claim=True) == 0.0, "the gap long passed: no sleep"
    assert venue_pace.pace(GAP) == pytest.approx(GAP) and venue_pace.pace(GAP, priority_claim=True) == pytest.approx(GAP)
    assert clock.sleeps == [pytest.approx(GAP)] * 2


def test_e11_a_priority_claim_is_served_before_eight_waiting_normals(clock):
    before = venue_pace.lane_stats()
    served: list = []
    h = _hold(clock, served)
    normals = [_queue_one(clock, f"n{i}", False, served) for i in range(8)]
    p = _queue_one(clock, "p", True, served)
    assert venue_pace.waiting() == (8, 1)
    _finish(clock, [h, *normals, p])
    assert _order(served) == ["h", "p"] + [f"n{i}" for i in range(8)]
    at = _instants(served)
    assert all(b - a == pytest.approx(GAP) for a, b in zip(at, at[1:])), "one gap apart, all ten"
    # measured PER CLAIM, the queue included (books_data_wait's reading): the
    # priority claim waited the holder's gap and then its own = 2 gaps,
    # while pace() itself slept one; the eighth normal waited ten
    d = _lane_delta(before)
    assert d["priority"] == {"wait": pytest.approx(2 * GAP), "claims": 1}
    assert d["normal"]["claims"] == 9 and d["normal"]["wait"] == pytest.approx(GAP + sum(range(3, 11)) * GAP)
    assert dict((n, w) for n, _, w in served)["p"] == pytest.approx(GAP)


def test_e11_a_priority_arrival_during_the_holders_sleep_takes_the_freed_gap_ahead_of_a_queued_normal(clock):
    """The lane is picked when the gate FREES, not when the queue formed
    (E10's pick-after-the-sleep, on a thread lock)."""
    served: list = []
    h = _hold(clock, served)
    n0 = _queue_one(clock, "n0", False, served)
    p = _queue_one(clock, "p", True, served)
    _finish(clock, [h, n0, p])
    assert _order(served) == ["h", "p", "n0"]


def test_e11_normal_claimants_keep_fifo_and_so_do_priority_claimants(clock):
    served: list = []
    h = _hold(clock, served)
    arrivals = [("n0", False), ("p0", True), ("n1", False), ("p1", True), ("n2", False), ("p2", True), ("n3", False)]
    ths = [_queue_one(clock, n, pr, served) for n, pr in arrivals]
    assert venue_pace.waiting() == (4, 3)
    _finish(clock, [h, *ths])
    assert _order(served) == ["h", "p0", "p1", "p2", "n0", "n1", "n2", "n3"]


def test_e11_the_burst_bound_hands_a_waiting_normal_the_thirteenth_gap_and_the_run_resets(clock):
    # fifteen priority claims queued behind one normal: twelve go first,
    # then the normal, then the rest -- the run reset by the normal
    served: list = []
    h = _hold(clock, served)
    n0 = _queue_one(clock, "n0", False, served)
    ps = [_queue_one(clock, f"p{i}", True, served) for i in range(15)]
    assert venue_pace.waiting() == (1, 15)
    _finish(clock, [h, n0, *ps])
    assert _order(served) == ["h"] + [f"p{i}" for i in range(12)] + ["n0", "p12", "p13", "p14"]
    assert venue_pace._run == 0, "reset by the normal, and never counted with no normal waiting"
    # a run already at the bound hands the NEXT gap to the waiting normal
    clock.release.clear()
    served.clear()
    h2 = _hold(clock, served, "h2")
    venue_pace._run = venue_pace.PACE_PRIORITY_BURST
    n1 = _queue_one(clock, "n1", False, served)
    q = [_queue_one(clock, f"q{i}", True, served) for i in range(2)]
    _finish(clock, [h2, n1, *q])
    assert _order(served) == ["h2", "n1", "q0", "q1"] and venue_pace._run == 0
    # no normal waiting: no bound -- fourteen priority claims straight, the run never counts
    clock.release.clear()
    served.clear()
    h3 = _hold(clock, served, "h3", priority=True)
    r = [_queue_one(clock, f"r{i}", True, served) for i in range(14)]
    _finish(clock, [h3, *r])
    assert _order(served) == ["h3"] + [f"r{i}" for i in range(14)] and venue_pace._run == 0


def test_e11_a_claimant_interrupted_in_the_queue_leaves_its_lane_and_the_rest_are_served(clock, monkeypatch):
    before = venue_pace.lane_stats()
    served: list = []
    h = _hold(clock, served)
    monkeypatch.setattr(venue_pace, "_cv", _InterruptingCV(venue_pace._cv, "n0"))
    n0 = _claim(clock, "n0", False, served)          # raises inside its first wait: leaves the lane
    n0.join(5.0)
    assert ("n0", None, None) in served and venue_pace.waiting() == (0, 0)
    n1 = _queue_one(clock, "n1", False, served)
    p = _queue_one(clock, "p", True, served)
    _finish(clock, [h, n1, p])
    assert _order(served) == ["h", "p", "n1"]
    d = _lane_delta(before)
    assert d["normal"]["claims"] == 2 and d["priority"]["claims"] == 1, "the interrupted claimant charged nothing"


def test_e11_pace_returns_the_gap_slept_and_an_idle_gate_hands_the_slot_out_at_once(clock):
    before = venue_pace.lane_stats()
    clock.t += 5.0                                    # the last claim long past
    assert venue_pace.pace(GAP) == 0.0 and clock.sleeps == []
    assert venue_pace.pace(GAP) == pytest.approx(GAP) and clock.sleeps == [pytest.approx(GAP)]
    assert venue_pace.pace(GAP, priority_claim=True) == pytest.approx(GAP)
    assert venue_pace.pace(0.1, priority_claim=True) == pytest.approx(0.1), "the caller's gap"
    d = _lane_delta(before)
    assert d["normal"] == {"wait": pytest.approx(GAP), "claims": 2}
    assert d["priority"] == {"wait": pytest.approx(GAP + 0.1), "claims": 2}
    assert venue_pace._last == pytest.approx(clock.t) and venue_pace.waiting() == (0, 0)


def test_e11_priority_claims_names_the_lane_by_context_and_the_context_reaches_to_thread(clock):
    before = venue_pace.lane_stats()
    assert venue_pace._priority_ctx.get() is False
    venue_pace.pace(GAP)
    with venue_pace.priority_claims():
        assert venue_pace._priority_ctx.get() is True
        venue_pace.pace(GAP)                          # the context's lane
        venue_pace.pace(GAP, priority_claim=False)    # False is "the context's lane", not "normal"

        async def main():
            await asyncio.to_thread(venue_pace.pace, GAP)
            with venue_pace.priority_claims():
                await asyncio.to_thread(venue_pace.pace, GAP)
        asyncio.run(main())
    assert venue_pace._priority_ctx.get() is False, "reset on exit"
    venue_pace.pace(GAP)
    venue_pace.pace(GAP, priority_claim=True)          # the keyword outside the context
    asyncio.run(asyncio.to_thread(venue_pace.pace, GAP))
    d = _lane_delta(before)
    assert d["normal"]["claims"] == 3 and d["priority"]["claims"] == 5


# ------------------------------------------------ part 2: who is priority

def _priority_sites():
    """(file, innermost function) of every call passing the literal
    `priority_claim=True` and of every `priority_claims()` entry, by
    AST over the package."""
    keyword, entries = set(), set()

    def walk(node, fn, rel):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Call):
                if any(k.arg == "priority_claim" and isinstance(k.value, ast.Constant) and k.value.value is True
                       for k in child.keywords):
                    keyword.add((rel, fn))
                f = child.func
                if (isinstance(f, ast.Attribute) and f.attr == "priority_claims") or \
                        (isinstance(f, ast.Name) and f.id == "priority_claims"):
                    entries.add((rel, fn))
            walk(child, name, rel)
    for path in sorted(PKG.rglob("*.py")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(path.read_text())
        walk(tree, None, path.relative_to(PKG).as_posix())
    return keyword, entries


def test_e11_the_only_priority_claimant_is_the_mirrors_tick_by_ast_and_the_shadows_sites_say_no_lane():
    keyword, entries = _priority_sites()
    assert keyword == set(), "no site in the package passes the keyword: the lane is the tick's context"
    assert entries == {("workers/mirror_live.py", "tick_once"), ("workers/mirror_live.py", "fast_tick_once")}
    once, fast = inspect.getsource(ml.tick_once), inspect.getsource(ml.fast_tick_once)
    assert once.index("with venue_pace.priority_claims():") < once.index("await _tick(t, woken)") < once.index("finally:")
    assert fast.index("with venue_pace.priority_claims():") < fast.index("await _fast_tick(t, taken)")
    # the shadow's four sites, price_path's one and pmus's create claim: bare, the default lane
    for mod, n in (("workers/mirror_shadow.py", 4), ("workers/price_path.py", 1)):
        text = (PKG / mod).read_text()
        assert len(re.findall(r"^\s*pace\(READ_PACING_S\)\s*$", text, re.M)) == n and "priority" not in text, mod
    pm = (PKG / "pmus.py").read_text()
    assert "            pace()\n" in pm and "priority_claim" not in pm and "priority_claims" not in pm
    # the mirror's own sites: the E6 literal in _paced, the three direct claims, and _bbo's read through
    # the shadow's _paced_bbo (E9's pin) -- none says a lane; the tick's context does
    src = inspect.getsource(ml)
    assert "pace(ms.READ_PACING_S)" in inspect.getsource(ml._paced)
    assert len(re.findall(r"^\s*pace\(ms\.READ_PACING_S\)\s*$", src, re.M)) == 4
    assert "await asyncio.to_thread(ms._paced_bbo, t.pmus, slug)" in inspect.getsource(ml._bbo)
    assert "priority_claim=" not in src, "the mirror names no lane at any call: its tick's context does"
    assert "from ..venue_pace import pace" in (PKG / "workers" / "mirror_live.py").read_text()
    # the E10 fold's own census of `priority=True` (the data-API lane) is untouched by this
    from tests.test_e10_review_pins import _priority_true_sites
    assert _priority_true_sites() == {("workers/mirror_live.py", "_market_snap"),
                                      ("workers/mirror_live.py", "_confirm_gone"),
                                      ("workers/whale_exits.py", "market_positions")}


def test_e11_the_mirrors_tick_claims_the_priority_lane_for_its_walk_its_reads_and_its_writes(clock, monkeypatch):
    """The real gate on the fake clock under a real tick: step R's
    positions walk (the shadow's account_positions_walk), step O's open
    orders (_paced), the book's quote read (the shadow's _paced_bbo) and
    a placement (_paced) -- every claim the tick made is on the priority
    lane, none on the normal lane, and `short.gate` counts them."""
    _real_gate(monkeypatch)
    before = venue_pace.lane_stats()
    p = _pool(conds=[])
    p.add_book(ledger=0)                              # an increase: the tick rests a BUY
    v = _Venue()
    st = _tick(p, v)
    d = _lane_delta(before)
    claims = len(clock.sleeps)
    assert claims >= 4 and all(s == pytest.approx(GAP) for s in clock.sleeps)
    assert d["priority"]["claims"] == claims and d["normal"]["claims"] == 0, d
    assert claims <= _census(st, "venue_calls"), "a claim per request; a BUY's create claims in the adapter"
    assert st["short"]["gate"]["claims"] == claims
    assert st["short"]["gate"]["wait"] == round(d["priority"]["wait"], 1) >= round(claims * GAP, 1) - 0.1
    assert [c[0] for c in v.calls if c[0] == "place"] == ["place"], "the BUY went out"
    # the same shadow function outside the tick's context is a NORMAL claim (the shadow's own tick)
    before = venue_pace.lane_stats()
    ms._paced_bbo(v, SLUG)
    d = _lane_delta(before)
    assert d["normal"]["claims"] == 1 and d["priority"]["claims"] == 0


def test_e11_the_fast_tick_claims_the_same_lane_and_each_tick_publishes_its_own_gate_block(clock, monkeypatch):
    _real_gate(monkeypatch)
    before = venue_pace.lane_stats()
    p = _pool()
    p.add_book(ledger=0)
    _walk()
    fs = _fast(p, _Venue())
    fast_claims = len(clock.sleeps)
    assert fs["fast"]["placed"] == 1 and fast_claims >= 2
    d = _lane_delta(before)
    assert d["priority"]["claims"] == fast_claims and d["normal"]["claims"] == 0
    assert fs["short"]["gate"] == {"wait": round(d["priority"]["wait"], 1), "claims": fast_claims}
    assert tuple(fs["short"]["wall"]) == WALL_KEYS
    # the full tick after it: its OWN claims, not the fast tick's (a delta since its start)
    p2 = _pool(conds=[])
    p2.add_book(ledger=300)
    st = _tick(p2, _Venue(held={SLUG: 300}))
    assert st["short"]["gate"]["claims"] == len(clock.sleeps) - fast_claims >= 3
    assert st["short"]["gate"]["claims"] < len(clock.sleeps)


def test_e11_the_gate_block_keys_are_served_whole_beside_the_wall_block_whose_keys_stand():
    p, b = _quiet_book()
    st = _tick(p, _v())
    g = st["short"]["gate"]
    assert tuple(g) == ("wait", "claims"), "bounded: these keys and no others"
    assert isinstance(g["wait"], float) and g["wait"] == round(g["wait"], 1) and isinstance(g["claims"], int)
    assert g == {"wait": 0.0, "claims": 0}, "the rails stub every pace: no claim reached the gate"
    assert tuple(st["short"]["wall"]) == WALL_KEYS, "E10's five keys, exactly, beside it"
    keys = list(st["short"])
    assert keys.index("wall") < keys.index("gate") and len(st["short"]) < api_app._DETAIL_MAX_KEYS
    served = api_app._sanitize_detail(st)
    assert served["short"]["gate"] == g and served["short"]["wall"] == st["short"]["wall"]
    # by source: after the wall block, before the fills collapse (the E7 tail pin holds), on both ticks
    once = inspect.getsource(ml.tick_once)
    assert once.index('["wall"] = _wall_block(t') < once.index('["gate"] = _gate_block(t)') < once.index("_publish_fills_dedup(t)")
    fast = inspect.getsource(ml.fast_tick_once)
    assert fast.index('["wall"] = _wall_block(t') < fast.index('["gate"] = _gate_block(t)') < fast.index("_publish_fills_dedup(t)")
    # the block off a fresh tick, and the reading it documents
    t = ml._Tick(pool=p, pmus=_Venue(), http=None, now=NOW, stats=ml._new_stats())
    assert ml._gate_block(t) == {"wait": 0.0, "claims": 0} and set(t.gate0) == {"wait", "claims"}
    assert "SUMMED PER CLAIM" in ml._gate_block.__doc__ and "books_data_wait" in ml._gate_block.__doc__
    assert "PRIORITY lane" in ml._gate_snapshot.__doc__ and "only claimant" in ml._gate_snapshot.__doc__


def test_e11_gate_wait_is_the_priority_lanes_delta_the_queue_included(clock):
    """books_data_wait's reading: the seconds from the call to the claim,
    summed per claim -- a claim that queued behind a holder counts the
    holder's gap too, so under the six-wide walk the sum exceeds the
    wall time it cost."""
    t = ml._Tick(pool=None, pmus=None, http=None, now=NOW, stats=ml._new_stats())
    served: list = []
    h = _hold(clock, served)                           # a normal holder
    ps = [_queue_one(clock, f"p{i}", True, served) for i in range(3)]
    _finish(clock, [h, *ps])
    # p0 waited h's gap + its own (2), p1 three, p2 four: nine gaps summed over three claims
    g = ml._gate_block(t)
    assert g["claims"] == 3 and abs(g["wait"] - 9 * GAP) <= 0.06, g
    assert g["wait"] > 4 * GAP, "more than the wall time the three cost (a sum per claim)"


# ---------------------------------------------- part 1: the quiet rotation

def test_e11_quiet_every_ticks_is_nine_env_lowers_to_one_and_never_raises_it(monkeypatch):
    assert ml.QUIET_EVERY_TICKS == 9 and isinstance(ml.QUIET_EVERY_TICKS, int)
    src = inspect.getsource(ml)
    assert 'QUIET_EVERY_TICKS = int(rules.capped_env("MIRROR_QUIET_EVERY_TICKS", 9, floor=1))' in src
    for env, want in (("3", 3), ("1", 1), ("0", 1), ("-4", 1), ("9", 9), ("12", 9), ("100", 9),
                      ("x", 9), ("", 9), ("nan", 9), ("inf", 9), ("4.7", 4)):
        monkeypatch.setenv("MIRROR_QUIET_EVERY_TICKS", env)
        assert int(rules.capped_env("MIRROR_QUIET_EVERY_TICKS", 9, floor=1)) == want, env
    monkeypatch.delenv("MIRROR_QUIET_EVERY_TICKS", raising=False)
    assert int(rules.capped_env("MIRROR_QUIET_EVERY_TICKS", 9, floor=1)) == 9
    # the paragraph says why nine and what stays
    i = src.index("QUIET_EVERY_TICKS = int(")
    para = src[src.rindex("# THE QUIET ROTATION, WIDENED (E11", 0, i):i]
    for k in ("3 -> 9", "places\n# NOTHING", "~5 minutes", "HOT the same tick", "never raise it past 9", "floor 1"):
        assert k in para, k


def test_e11_the_rotation_reads_a_quiet_book_on_the_tenth_tick_and_a_hot_book_every_tick():
    """The E6 harness: a book on target with nothing open beside a book
    with no mark (a halted venue: `no_mark`, hot by row). The hot one
    is read on every one of eleven ticks; the quiet one on the first
    and the tenth, its skips naming tick 10 as `read_on`."""
    p = _pool()
    on = p.add_book(ledger=300)
    halt = "aec-atp-halt-x-2026-09-06"
    hot = p.add_book(ledger=300, condition_id="0xhalt", us_market_slug=halt, long_asset="tokLh", other_asset="tokOh")
    p.markets["0xhalt"] = {"closed": False, "resolved": False, "resolved_prices": None}
    v = _Venue(held={SLUG: 300, halt: 300}, states={halt: HALTED})
    for seq in range(1, 12):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        reads = _bbos(v)
        assert halt in reads and hot["last_reason"] == "no_mark" and _census(st, "no_mark") == 1, seq
        assert (SLUG in reads) == (seq in (1, 10)), (seq, reads)
        if SLUG not in reads:
            assert on["last_reason"] == "book_quiet_skipped" and _census(st, "book_quiet_skipped") == 1
            assert on["last_plan"]["read_on"] == (10 if seq < 10 else 19) and _timing(st)["quiet_skipped"] == 1
        else:
            assert on["last_plan"]["reason"] == "on target" and _census(st, "on_target") == 1
    assert ml._quiet_memo[on["id"]]["seq"] == 10
    # every event that makes a quiet book matter makes it hot the same tick (E6's classes, untouched):
    # his fill inside HOT_S on its market (the E6 woken pin's shape, no wake)
    p.fills = p.fills + [_fill(M, "BUY", 50.0, 0.31, NOW + 30 * 11 - 10)]
    p.snap[M] = 350.0
    v.calls.clear()
    _tick(p, v, now=NOW + 30 * 11)
    assert SLUG in _bbos(v), "a fill of his inside HOT_S: read on tick 12, not on 19"


def test_e11_at_the_envs_floor_of_one_a_quiet_book_is_read_every_tick(monkeypatch):
    monkeypatch.setattr(ml, "QUIET_EVERY_TICKS", 1)
    p, b = _quiet_book()
    v = _v()
    for seq in range(1, 5):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        assert _bbos(v) == [SLUG] and _census(st, "book_quiet_skipped") == 0 and _census(st, "on_target") == 1, seq


def test_e11_at_87_books_the_quiet_fleet_costs_two_calls_a_tick_between_due_ticks_and_is_reread_inside_eleven():
    """The live shape (87 live books at the default budget): tick 1 reads
    every book (an empty memo), ticks 2-9 make two venue calls each
    (steps R and O), tick 10 reads the 48 the quiet share fits (60 - 2 -
    the candidates' floor) and queues 39 with `read_on` 11, tick 11
    reads those 39 under the queue's slots -- every book read again
    within QUIET_EVERY_TICKS + ceil(87 / 48) = 11 ticks of its last read,
    ~5.5 min at a 30 s tick."""
    p = _pool(conds=[])
    slugs = _many_books(p, 87)
    v = _Venue()
    st1 = _tick(p, v)
    assert sorted(_bbos(v)) == sorted(slugs) and _census(st1, "on_target") == 87
    for seq in range(2, 10):
        v.calls.clear()
        st = _tick(p, v, now=NOW + 30 * (seq - 1))
        assert not _bbos(v) and _census(st, "book_quiet_skipped") == 87 and _census(st, "venue_calls") == 2, seq
    v.calls.clear()
    st10 = _tick(p, v, now=NOW + 270)
    assert len(_bbos(v)) == 48 == _timing(st10)["quiet_budget"] == 60 - 2 - ml.CAND_MIN_PER_TICK
    assert _census(st10, "book_quiet_skipped") == 39 and _census(st10, "venue_calls") == 50 <= ml.VENUE_CALLS_PER_TICK
    assert len(ml._quiet_deferred) == 39 and all(b["last_plan"]["read_on"] == 11 for b in p.books.values()
                                                 if b["last_reason"] == "book_quiet_skipped")
    v.calls.clear()
    st11 = _tick(p, v, now=NOW + 300)
    assert len(_bbos(v)) == 39 == _timing(st11)["quiet_reads"] and not ml._quiet_deferred
    assert _census(st11, "venue_calls") == 41 and _census(st11, "book_quiet_skipped") == 48
    assert 11 == ml.QUIET_EVERY_TICKS + -(-87 // 48)


def test_e11_the_always_read_classes_the_budget_and_the_deferred_floor_are_byte_for_byte():
    src = "".join(inspect.getsource(getattr(ml, f)) for f in E6_RULE_FUNCTIONS)
    assert hashlib.sha256(src.encode()).hexdigest() == E6_RULE_SHA256, "E6's rule as built: the constant alone moved"
    assert "QUIET_EVERY_TICKS" in inspect.getsource(ml._quiet_skip) and "DEFERRED_MIN_PER_TICK" in inspect.getsource(ml._quiet_slots)
    from tests import test_e6_tick_budget as e6
    e6.test_e6_a_book_with_an_open_order_is_never_skipped()
    e6.test_e6_a_not_on_target_book_is_never_skipped()
    e6.test_e6_a_book_with_his_fill_inside_hot_s_is_never_skipped()
    e6.test_e6_a_woken_market_with_his_fill_is_read_first_and_hot()
    e6.test_e6_a_frozen_book_is_never_skipped()


# ------------------------------------------------- the older pins re-run

def test_e11_the_e6_e7_e9_e10_and_e2_pins_stand(monkeypatch):
    from tests import test_e6_review_pins as e6r
    from tests import test_e6_tick_budget as e6
    from tests import test_e10_priority_lane as e10
    e6.test_e6_the_constants_and_the_env_can_only_lower_the_budget(monkeypatch)
    e6.test_e6_the_book_reads_time_is_summed_per_call(monkeypatch)        # the `pace(ms.READ_PACING_S)` literal
    e6r.test_review_the_superseded_pins_are_the_budgets_arithmetic()
    e10.test_e10_the_wall_block_keys_and_books_data_wall_le_books_le_tick_s()
    # E2 round 3: penalize never takes the gate's lock; the gate's own lock is the condition's
    src = inspect.getsource(venue_pace.penalize)
    assert "with _penalty_lock:" in src and "with _cv:" not in src and "_lock" not in inspect.getsource(venue_pace.pace)
    # E9: the mirror's quote read is the shadow's function, by source
    from tests.test_e9_review_pins import test_review_q4_the_fast_tick_reads_through_the_same_functions_with_no_fast_branch
    test_review_q4_the_fast_tick_reads_through_the_same_functions_with_no_fast_branch()


def test_e11_the_docs_section_32():
    doc = DOCS.read_text()
    sec = doc[doc.index("## 32. E11"):]
    for k in ("PACE_PRIORITY_BURST", "priority_claims", "MIRROR_QUIET_EVERY_TICKS", "`short.gate`",
              "lane_stats", "0.35", "ms._paced_bbo", "asyncio.to_thread", "book_quiet_skipped",
              "test_e11_venue_gate", "47 claims", "3 → 9"):
        assert k in sec, k
    assert sec.count("\n\n**") >= 2, "three short paragraphs"
