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
asyncio.to_thread, and the lock is held through the sleep so callers
form a queue instead of racing for the same gap.

THE 429 CIRCUIT (E2 review round 2, HIGH-B): the venue answered five
HTML 429s in 1.5 h at ~1 req/s (2026-09-06 22:48Z-00:11Z) BEFORE E2
raised the sustained rate. Every site that reads a 429 -- a placement,
a quote read, a cancel, the positions walk, in either worker -- calls
`penalize()`: the gap is multiplied by PENALTY_MULT for PENALTY_S from
the last 429, for every lane sharing this gate, and the penalty
expires on its own. A circuit on the GAP, not on the walk's
concurrency: whatever N is, the venue sees half the rate. penalize()
never touches the gate's lock (round 3, MEDIUM-2): that lock is held
through a sleep by whoever is pacing, and the 429 is handled on the
event loop -- the penalty is one float store under its own tiny lock.
"""
from __future__ import annotations

import threading
import time

MIN_GAP_S = 0.35
# the 429 circuit: the gap is PENALTY_MULT x for PENALTY_S after the
# last 429 any lane read
PENALTY_MULT = 2.0
PENALTY_S = 600.0
_lock = threading.Lock()
_last = 0.0
# the penalty's own lock: never held across a sleep, so a 429 handled
# on the event loop never waits behind a pacing thread
_penalty_lock = threading.Lock()
_penalty_until = 0.0


def pace(min_gap_s: float = MIN_GAP_S) -> float:
    """Block until the gap has passed since the last paced request in
    this process, then claim the slot. The gap is min_gap_s, doubled
    (PENALTY_MULT) while the 429 circuit holds. Returns the seconds
    waited. One call per request: a caller that makes two requests
    calls it twice, before each."""
    global _last
    with _lock:
        now = time.monotonic()
        wait = max(0.0, _last + _gap(float(min_gap_s), now) - now)
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
        return wait


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


__all__ = ["pace", "penalize", "penalty_left", "effective_gap",
           "MIN_GAP_S", "PENALTY_MULT", "PENALTY_S"]
