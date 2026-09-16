"""ONE pacer for every MEASUREMENT read of the venue in this process
(position-mirroring review round two, 2026-09-02).

Two workers each pacing their own reads at 0.35 s do not bound the sum:
price_path (the ask at fixed offsets after a copy) and mirror_shadow
(one BBO per active market, one positions walk per tick) both run on
the copy path's venue client, and the venue 429'd a board walk above
~3 req/s. When their ticks overlap the venue saw ~5.7 req/s from
measurement alone, and the next request the limit refused was the copy
path's own quote or send.

This gate is process-wide: a call blocks until MIN_GAP_S has passed
since the last paced request ANYWHERE in the process, so however many
measurement loops exist their combined rate is one request per gap. The
copy lane's money path (pre-trade quote, send, cancel, position check)
does not call it: it must not queue behind measurement. THE MIRROR
LANE'S WRITES DO (E2, 2026-09-06): a maker's cancels and rests are not
latency-critical, and under its parallel book walk six books placing
together were twelve requests inside one gap (mirror_live._paced).

ONE CLAIM PER REQUEST, NEVER A RESERVATION (E2 review round 3, HIGH-1).
A call that makes two requests -- a BUY placement is a preview and a
create -- claims a gap before EACH of them (mirror_live._paced before
the preview, pmus.submit_fok(paced_pair=True) before the create). A
reservation of the second slot at claim time was tried and dropped: it
recorded the create at its RESERVED time, so a preview whose HTTP took
longer than the gap (live latency ~0.65 s a request against a 0.35 s
gap, the common case) fired its create late and unrecorded, and the
next claimant landed a fraction of a gap after it. With a real claim
before every request, pairwise spacing holds at any latency.

Synchronous on purpose -- the reads it guards run in worker threads via
asyncio.to_thread, and the claimant holds the gate through its sleep so
callers form a queue instead of racing for the same gap.

THE PRIORITY CLAIM (E11, 2026-09-08; owner 22:4xZ "latency must be
flawless and exceptional"). At 03:22Z the live mirror's tick spent
22.6 s of its 25.7 s books stage inside paced venue calls: 47 claims
are 16.4 s of gap whatever the walk's width, and the other ~6 s were
the shadow's and price_path's claims interleaving on the same gate
(mirror_shadow's four sites, price_path's one) -- the live mirror's
quote read waiting its turn behind a measurement read. So the gate has
TWO LANES, the rule ratelimit.Throttle's priority lane (E10) restated
for a thread lock: a PRIORITY claim takes the NEXT gap ahead of every
waiting normal claimant; normal claimants keep their order among
themselves and so do priority claimants (two FIFO deques); at most
PACE_PRIORITY_BURST = 12 consecutive priority claims are served while
a normal claimant waits, then one normal, the run resetting when a
normal is served or none waits. A claim is priority when the call says
so (`pace(gap, priority_claim=True)`, default False) OR when it is made
inside `priority_claims()` -- a context the live mirror enters for its
tick (mirror_live.tick_once and fast_tick_once), and which
asyncio.to_thread copies into every worker thread the tick starts, so
the tick's claims made through functions the shadow also owns (its
paced quote read, its positions walk, the resolver's reads) take the
mirror's lane without a keyword on every shared callee. The ONLY
priority claimant is the live mirror's tick; the shadow's and
price_path's loops and pmus's default stay normal. THE GAP DOES NOT
MOVE and the venue's rate is not raised: whoever claims next computes
its wait from the LAST claim's record and sleeps it out holding the
gate (`_busy`), so between ANY two requests through here, in any mix
of lanes, at least the gap passes. How the queue is ordered under a
threading primitive: one Condition; a claimant appends a token to its
lane and waits until the gate is free, it is its lane's head and the
turn rule (_turn) names its lane; it then takes the gate, sleeps
OUTSIDE the condition's lock (so claimants can still queue behind it --
the lane lengths are what the turn rule reads), records the claim and
frees the gate with notify_all. The 429 circuit doubles the gap for
both lanes alike. Each lane's claims are measured (lane_stats: the
seconds from the call to the claim, summed per claim, and the count;
the mirror publishes its lane's delta as `short.gate`).
test_e11_venue_gate proves the gap under mixed load with real threads
on a fake clock, the lanes, the bound and the circuit.

THE 429 CIRCUIT (E2 review round 2, HIGH-B): the venue answered five
HTML 429s in 1.5 h at ~1 req/s (2026-09-06 22:48Z-00:11Z) BEFORE E2
raised the sustained rate. Every site that reads a 429 -- a placement,
a quote read, a cancel, the positions walk, in either worker -- calls
`penalize()`: the gap is multiplied by PENALTY_MULT for PENALTY_S from
the last 429, for every lane sharing this gate, and the penalty
expires on its own. A circuit on the GAP, not on the walk's
concurrency: whatever N is, the venue sees half the rate. penalize()
never touches the gate's lock (round 3, MEDIUM-2): that lock is held
by whoever is pacing, and the 429 is handled on the event loop -- the
penalty is one float store under its own tiny lock.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from collections import deque

MIN_GAP_S = 0.35
# the 429 circuit: the gap is PENALTY_MULT x for PENALTY_S after the
# last 429 any lane read
PENALTY_MULT = 2.0
PENALTY_S = 600.0
# THE PRIORITY CLAIM'S STARVATION BOUND (E11): at most this many
# consecutive priority claims while a normal claimant waits, then one
# normal. Twelve as ratelimit.PRIORITY_BURST: two waves of the live
# mirror's six-wide walk; under the bound a normal claimant (a shadow
# quote read, a price_path sample) has waited 12 x 0.35 = 4.2 s, which
# neither loop times out on (each paces its own reads and abandons on
# an empty read, never on a slow one).
PACE_PRIORITY_BURST = 12
# THE GATE (E11): one condition, two FIFO lanes of claimant tokens and
# the busy flag of the claimant sleeping out its gap. The condition's
# lock is held only to queue, to take the gate and to free it -- never
# across the sleep -- so a claim can queue behind the sleeper and
# penalize() (its own lock) never waits on a pacing thread.
_cv = threading.Condition()
_normal: deque = deque()
_priority: deque = deque()
_busy = False
# consecutive priority claims served while a normal claimant waited
_run = 0
_last = 0.0
# each lane's claims, measured: the seconds from the call to the claim
# (the queue behind other claimants and the gap itself), summed per
# claim, and the count -- process-wide since import; read as deltas
_lanes = {"normal": {"wait": 0.0, "claims": 0}, "priority": {"wait": 0.0, "claims": 0}}
# the lane a claim takes when the call does not say (priority_claims())
_priority_ctx: contextvars.ContextVar[bool] = contextvars.ContextVar("venue_pace_priority", default=False)
# the penalty's own lock: never held across a sleep, so a 429 handled
# on the event loop never waits behind a pacing thread
_penalty_lock = threading.Lock()
_penalty_until = 0.0


def pace(min_gap_s: float = MIN_GAP_S, priority_claim: bool = False) -> float:
    """Block until the gap has passed since the last paced request in
    this process, then claim the slot. The gap is min_gap_s, doubled
    (PENALTY_MULT) while the 429 circuit holds. Returns the seconds
    slept for the gap (the time spent queued behind other claimants is
    on lane_stats). One call per request: a caller that makes two
    requests calls it twice, before each.

    `priority_claim` (E11): True takes the next gap ahead of every
    waiting normal claimant, under PACE_PRIORITY_BURST; the default is
    the lane the context names -- priority inside priority_claims(),
    the live mirror's tick, else normal. Whichever lane, the claim is
    recorded when the sleep ends, so the next claimant's gap counts
    from THIS request."""
    global _busy, _last, _run
    prio = bool(priority_claim) or _priority_ctx.get()
    called = time.monotonic()
    token = object()
    lane = _priority if prio else _normal
    with _cv:
        lane.append(token)
        try:
            while _busy or lane[0] is not token or not _turn(prio):
                _cv.wait()
        except BaseException:
            # a claimant interrupted in the queue leaves its lane, never
            # charged a gap; the claimants behind it re-read the turn
            lane.remove(token)
            _cv.notify_all()
            raise
        lane.popleft()
        _run = (_run + 1 if _normal else 0) if prio else 0
        _busy = True
        now = time.monotonic()
        wait = max(0.0, _last + _gap(float(min_gap_s), now) - now)
    try:
        if wait > 0:
            time.sleep(wait)
    finally:
        with _cv:
            _last = time.monotonic()
            _busy = False
            stat = _lanes["priority" if prio else "normal"]
            stat["wait"] += max(0.0, _last - called)
            stat["claims"] += 1
            _cv.notify_all()
    return wait


def _turn(priority_claim: bool) -> bool:
    """Whose lane the free gate goes to, read by a lane's head: the
    priority lane's unless a normal claimant waits and the run has
    reached the bound; the normal lane's when no priority claimant
    waits or the bound is reached."""
    if priority_claim:
        return not _normal or _run < PACE_PRIORITY_BURST
    return not _priority or _run >= PACE_PRIORITY_BURST


@contextlib.contextmanager
def priority_claims():
    """Every claim made inside this block -- by the task that entered
    it and by every worker thread asyncio.to_thread starts for it (the
    context is copied into the thread) -- is a priority claim unless
    the call says otherwise. The live mirror's tick enters it once."""
    tok = _priority_ctx.set(True)
    try:
        yield
    finally:
        _priority_ctx.reset(tok)


def waiting() -> tuple[int, int]:
    """(normal, priority) claimants queued on the gate right now, the
    one sleeping out its gap not counted."""
    with _cv:
        return len(_normal), len(_priority)


def lane_stats() -> dict:
    """{"normal": {wait, claims}, "priority": {wait, claims}}: the
    seconds each lane's claims spent from the call to the claim (the
    queue and the gap, summed per claim) and their count, process-wide
    since import. A reader takes deltas (mirror_live's `short.gate`)."""
    with _cv:
        return {k: dict(v) for k, v in _lanes.items()}


def _gap(min_gap_s: float, now: float) -> float:
    return min_gap_s * (PENALTY_MULT if now < _penalty_until else 1.0)


def penalize(now: float | None = None) -> float:
    """A 429 was read: every paced call in this process, in every lane,
    waits PENALTY_MULT x the gap for the next PENALTY_S. Idempotent
    across a burst of 429s (the window restarts from the latest).
    Returns the time the penalty lifts. Never takes the gate's lock."""
    global _penalty_until
    with _penalty_lock:
        _penalty_until = (time.monotonic() if now is None else float(now)) + PENALTY_S
        return _penalty_until


def penalty_left(now: float | None = None) -> float:
    """Seconds of 429 penalty left, 0.0 when none."""
    return max(0.0, _penalty_until - (time.monotonic() if now is None else float(now)))


def effective_gap(min_gap_s: float = MIN_GAP_S) -> float:
    """The gap a paced call would honour right now."""
    return _gap(float(min_gap_s), time.monotonic())


__all__ = ["pace", "priority_claims", "waiting", "lane_stats", "penalize", "penalty_left",
           "effective_gap", "MIN_GAP_S", "PENALTY_MULT", "PENALTY_S", "PACE_PRIORITY_BURST"]
