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
mirror's lane without a keyword on every shared callee. The priority
claimants are the money paths only: the protected workers' live mirror
tick (its process) and, in the API process (2026-10-03), the WS-triggered
single-event evaluation (pinnapi_reactive.evaluate), the small-live
execution mirror's tick (execmirror.run) and the actual entry lane's one
venue submission per execution intent (execution_intent.ActualLane._run);
the shadow's, price_path's and
the periodic collector's reads and pmus's default stay normal. THE GAP DOES NOT
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
# ── THE PROHIBITION, WHICH IS NOT PENALTY_S ─────────────────────────
# The HARD not-before window applied on an observed 429 when the venue
# supplied no `Retry-After`. A floor, not an estimate of the venue's
# window: it exists so an unlabelled 429 still stops a send, and it is
# short because a long hard halt would also stop reconciliation and the
# servicing of inventory we already hold. When the venue DOES name a
# window, that window is used and this floor is irrelevant.
GATE_FLOOR_S = 5.0
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


#: THE COOLDOWN'S EXPIRY AS A WALL-CLOCK EPOCH, beside the monotonic one.
#:
#: `_penalty_until` is `time.monotonic()`-based, and monotonic is only
#: comparable WITHIN one process and one boot. Persisting it and reading it
#: back in another process -- which is what "cooldown survives restart"
#: requires -- compares two unrelated number lines, and the result is
#: arbitrary: the cooldown either never expires or is already expired.
#:
#: So a PORTABLE expiry is kept separately, in epoch seconds, and that is
#: the only one fit to leave the process.
_penalty_until_epoch = 0.0
_penalty_reason = None
_penalty_retry_after_s = None


def penalize_observed(*, retry_after_s=None, reason=None,
                      now: float | None = None) -> dict:
    """Apply the cooldown for an OBSERVED rate-limit response.

    ── WHY THIS EXISTS ALONGSIDE `penalize()` ───────────────────────
    `penalize()` was never called on the scheduled book-read path. It
    existed, it worked in isolation, and an observed 429 changed nothing --
    so the next candidate read on the ordinary gap and collected another
    429. A control nothing invokes is not a control.

    ── `Retry-After` IS HONOURED WHEN THE VENUE SUPPLIES ONE ────────
    The venue telling us how long to wait is better evidence than our
    fixed PENALTY_S. The LONGER of the two is taken: a cooldown shorter
    than the venue asked for is a guess wearing a policy's clothes, and a
    venue asking for less than our own floor does not entitle us to
    abandon the floor.

    Returns what was applied, so a caller can record it rather than
    assert it.
    """
    global _penalty_until, _penalty_until_epoch, _penalty_reason
    global _penalty_retry_after_s
    import time as _t

    wall = _t.time() if now is None else float(now)
    mono = _t.monotonic()
    try:
        ra = None if retry_after_s is None else max(0.0, float(retry_after_s))
    except (TypeError, ValueError):
        ra = None
    hold = PENALTY_S if ra is None else max(float(PENALTY_S), ra)
    with _penalty_lock:
        _penalty_until = mono + hold
        _penalty_until_epoch = wall + hold
        _penalty_reason = reason
        _penalty_retry_after_s = ra
    # ── AND NOW THE PART THAT ACTUALLY PREVENTS A REQUEST ────────────
    #
    # Everything above this line is a REDUCED RATE: it multiplies the
    # inter-request gap by PENALTY_MULT for PENALTY_S seconds. An
    # independent reproduction showed precisely what that is worth --
    # with `Retry-After: 60` reported as a 600-second hold, the next read
    # completed ~0.7 s later, which is 0.35 × 2.0. Calling that "the
    # cooldown armed" was wrong.
    #
    # THE TWO QUANTITIES ARE DIFFERENT AND ARE NOW BOTH SET:
    #
    #   NOT-BEFORE      a hard instant before which NO request dispatches.
    #                   `Retry-After` when the venue supplied one, because
    #                   the venue saying when we may send again is better
    #                   evidence than a number we chose; otherwise
    #                   GATE_FLOOR_S, which is a floor and not a guess at
    #                   the venue's window.
    #   REDUCED RATE    PENALTY_S at PENALTY_MULT, unchanged, and no longer
    #                   mistaken for the gate.
    #
    # The not-before is deliberately NOT PENALTY_S. A 600-second hard halt
    # of the whole lane on one 429 would stop reconciliation and servicing
    # of held inventory, which is the opposite of protective. The venue's
    # own window is the prohibition; our 600 seconds is the caution.
    gate = {"armed": False, "why": "gate module unavailable"}
    try:
        from . import venue_request_gate as _grt
        not_before_s = max(float(GATE_FLOOR_S), ra if ra is not None else 0.0)
        gate = dict(_grt.hold_until(until_epoch_s=wall + not_before_s,
                                    reason=reason or "VENUE_RATE_LIMITED"),
                    armed=True, not_before_s=not_before_s,
                    not_before_is=("RETRY_AFTER" if (ra is not None
                                                    and ra > GATE_FLOOR_S)
                                   else "OUR_GATE_FLOOR"))
    except Exception as exc:                                   # noqa: BLE001
        gate = {"armed": False, "why": type(exc).__name__}
    return {"applied": True, "hold_s": hold,
            "retry_after_s": ra,
            "floor_s": PENALTY_S,
            "hold_is": ("RETRY_AFTER" if ra is not None and ra > PENALTY_S
                        else "OUR_FLOOR"),
            "expires_at_epoch_s": wall + hold,
            "reason": reason,
            # NAMED SO THE TWO CANNOT BE READ AS ONE. `hold_s` is the
            # reduced-rate period; `not_before` is the prohibition.
            "what_hold_s_is": "REDUCED_RATE_PERIOD_NOT_A_PROHIBITION",
            "not_before": gate,
            "portable_expiry_is_epoch_not_monotonic": (
                "monotonic is comparable only within one process and one "
                "boot, so the epoch value is the one fit to persist")}


def cooldown_state(now: float | None = None) -> dict:
    """The cooldown, in a form safe to persist and to read back."""
    import time as _t

    wall = _t.time() if now is None else float(now)
    with _penalty_lock:
        exp, reason, ra = (_penalty_until_epoch, _penalty_reason,
                           _penalty_retry_after_s)
    return {"active": bool(exp > wall),
            "expires_at_epoch_s": exp or None,
            "seconds_left": max(0.0, exp - wall) if exp else 0.0,
            "reason": reason,
            "retry_after_s": ra,
            "multiplier_while_active": PENALTY_MULT}


def resume_cooldown(expires_at_epoch_s, *, reason=None,
                    now: float | None = None) -> dict:
    """Re-arm a cooldown read back from durable storage after a restart.

    THE FAIL-OPEN THIS CLOSES: the penalty lived in a module global and
    died with the process, so a crash-looping process resumed at full rate
    immediately after the account was rate limited -- the worst possible
    moment. An epoch expiry can be stored and honoured across that.
    """
    global _penalty_until, _penalty_until_epoch, _penalty_reason
    import time as _t

    wall = _t.time() if now is None else float(now)
    try:
        exp = float(expires_at_epoch_s)
    except (TypeError, ValueError):
        return {"resumed": False, "why": "expiry is not a number"}
    # ── NaN AND INFINITY ARE NOT INSTANTS, AND NaN IS THE DANGEROUS ONE ──
    #
    # Found by the test, and it was a real bug: `float('nan')` passes
    # `float()`, and EVERY comparison with NaN is False -- so `left <= 0`
    # was False and a NaN expiry "resumed" successfully. That arms a
    # cooldown whose remaining time is NaN, which then makes
    # `penalty_left()` and every gap decision behave arbitrarily. A
    # corrupted stored value would have been worse than no stored value.
    if exp != exp or exp in (float("inf"), float("-inf")):
        return {"resumed": False,
                "why": "expiry is NaN or infinite, which is not an instant"}
    left = exp - wall
    if left <= 0:
        return {"resumed": False, "why": "the stored cooldown had expired",
                "expired_s_ago": round(-left, 3)}
    with _penalty_lock:
        _penalty_until = _t.monotonic() + left
        _penalty_until_epoch = exp
        _penalty_reason = reason or "RESUMED_AFTER_RESTART"
    return {"resumed": True, "seconds_left": round(left, 3),
            "expires_at_epoch_s": exp}


def penalty_left(now: float | None = None) -> float:
    """Seconds of 429 penalty left, 0.0 when none."""
    return max(0.0, _penalty_until - (time.monotonic() if now is None else float(now)))


def effective_gap(min_gap_s: float = MIN_GAP_S) -> float:
    """The gap a paced call would honour right now."""
    return _gap(float(min_gap_s), time.monotonic())


# ══════════════════════════════════════════════════════════════════════
# THE ESCALATING 429 COOLDOWN (P0-429, 2026-10-09)
# ══════════════════════════════════════════════════════════════════════
#
# WHAT PRODUCTION SHOWED. sportsassets-workers, 04:20:08.9-04:20:16.9Z:
# gateway.polymarket.us answered "429 Too Many Requests" nine times in 8 s
# on nine different markets at ~0.7 s spacing, and the same burst shape
# recurred all morning (11:40:00-11:42:00Z: 24 of 64 gateway requests were
# 429s, in runs of up to seven at 0.7-1 s). 0.7 s is MIN_GAP_S x
# PENALTY_MULT: the "circuit" above only doubled the gap, so the walk never
# stopped. And the hard not-before hold (`penalize_observed` ->
# venue_request_gate.hold_until) was checked BEFORE the pacer's queue,
# never after it -- so every walker thread already queued for its gap when
# the 429 arrived (mirror_shadow, shadow_bettor, bettor_state, shadow_rn1,
# shadow_experimental, institutional_md) dispatched into the limit anyway,
# one per doubled gap. That is the run of 429s.
#
# THE RULE. Every 429 the transport reads arms a process-wide cooldown:
#
#   * the FIRST 429 after a successful read arms COOLDOWN_FLOOR_S (5 s,
#     GATE_FLOOR_S -- the hard floor this module already applies to an
#     observed 429 with no Retry-After; PENALTY_S is the 600 s REDUCED-RATE
#     period and was never a prohibition, see `penalize_observed`);
#   * each further 429 on a request dispatched AFTER the current cooldown
#     was armed (the probe that followed it was refused again) DOUBLES the
#     level, capped at COOLDOWN_CAP_S. A 429 on a request already in flight
#     when the cooldown was armed is counted but does not escalate: it is
#     the same episode, not new evidence;
#   * the cooldown is the level plus a jitter of [0, COOLDOWN_JITTER_FRAC)
#     of it -- three processes share the venue's limit (API, workers,
#     market plane) and must not all resume on the same instant -- never
#     more than the cap, and never less than the venue's own Retry-After
#     when the venue names a longer one;
#   * only a 2xx READ dispatched after the cooldown was armed resets it
#     (consecutive count and level to zero, the cooldown lifted). A 2xx
#     that was in flight before the 429 is not evidence the limit cleared.
#
# WHAT A COOLDOWN DOES TO A REQUEST (venue_request_gate.cooldown_check,
# called at the transport BEFORE the pacer's queue and AGAIN after it,
# immediately before dispatch):
#
#   NORMAL lane, no deadline (every measurement walker)
#       refused by name, nothing sent: R_VENUE_429_COOLDOWN_READ_DEFERRED.
#       The walkers check `normal_read_deferral()` before each market and
#       stop their pass, counting what they skipped.
#   NORMAL lane, with a deadline (a scheduled read bound by begin_read)
#       waits the cooldown out when it ends inside the deadline -- the
#       contract venue_request_gate already gives a caller that "can
#       genuinely wait" -- else refused R_COOLDOWN_EXCEEDS_DEADLINE.
#   PRIORITY lane (priority_claims(): the money paths -- the live mirror's
#       tick, the API's actual-entry submission, its execution mirror and
#       the WS-triggered evaluation; risk-reducing cancels and closes ride
#       these) waits at most PRIORITY_COOLDOWN_MAX_WAIT_S of the cooldown,
#       then proceeds: never refused by this cooldown, never starved by
#       it. Why bounded rather than full: a cancel or protective close
#       that waits out a 120 s cooldown while the position moves is a
#       larger loss than one more 429, and one priority request at the
#       pacer's (doubled) gap does not raise the venue rate -- the gap is
#       the rate. The first rung (5 s) is waited in full, so at the floor
#       a priority claim honours the cooldown exactly. The venue's own
#       hard hold (Retry-After via hold_until) is unchanged for every lane.
#
# NOTHING HERE RAISES A RATE. The gap, the lanes and PENALTY_* are as they
# were; this only withholds requests.
#
# THREE PROCESSES, FOUR PACERS -- STATED, NOT FIXED HERE (P0-429 item 7).
# This module's state is per PROCESS. sportsassets-api and
# sportsassets-workers each import it and each hold their own gap, their own
# hold and their own cooldown; whatever limit the venue counts (credential,
# account or source IP -- venue_cooldown_store records the scope because it
# is not established) is shared by both while neither sees the other's 429s.
# The portable expiry `penalize_observed` produces is persisted and read
# back ONLY by the API's ext_pinnacle_loop (venue_cooldown_store
# .drain_pending / .load_and_resume, into the API process at its own
# startup): the workers queue a save on a book_read 429 that nothing in the
# workers process drains, and no process reads another's row. The market
# plane (sportsassets-market-plane) neither imports this module nor builds
# a retail gateway client: its REST book refresh is the PMX institutional
# book (another host, GET /v1/orderbook/{symbol}) under its own 12/min
# budget and its own 60 s hold on a 429 (market_plane.active_refresh), and
# it reads institutional_same_book for that module's timestamp parser
# alone. The paper public-gateway lane (paper_market_data.AuthLaneState,
# held marks only, one request at a time) is a FOURTH pacer, in whichever
# process runs the paper owner: it replaces this transport with its own
# (1 s gap, a 5 s / Retry-After hold on every 429, the gap doubled per 429
# up to x8) and is not touched here. A cross-process cooldown would need a
# shared store read before every claim -- a database read on the
# market-data path that venue_cooldown_store deliberately refuses to make
# -- so it is not attempted here; each process now at least stops ITS OWN
# walk on the venue's first 429, and each process's cooldown is on its own
# readback (the workers: every gateway walker's beat; the API: the
# ext_pinnacle heartbeat's venue_rate_controls, through
# venue_request_gate.totals()).
COOLDOWN_FLOOR_S = GATE_FLOOR_S
COOLDOWN_CAP_S = 120.0
COOLDOWN_JITTER_FRAC = 0.2
PRIORITY_COOLDOWN_MAX_WAIT_S = COOLDOWN_FLOOR_S
#: The named refusal a normal-lane read gets during the cooldown. OURS
#: (SOFTWARE): the venue's 429 is the venue's word, but choosing not to send
#: while it stands -- and having walked into it -- is our decision.
R_VENUE_429_COOLDOWN_READ_DEFERRED = "VENUE_429_COOLDOWN_NORMAL_READ_DEFERRED"
COOLDOWN_VERSION = "VENUE_429_ESCALATING_COOLDOWN_V1"

# indirections so a test drives the cooldown on a fake clock and a fixed
# jitter; production reads the real ones
_clock = time.monotonic
_wall = time.time
_rand = None            # None: random.random

_rl_lock = threading.Lock()
_rl: dict = {}
_rl_sources: dict = {}
#: the read source a claim is attributed to (a walker sets it once for its
#: task; asyncio.to_thread copies it into every thread the task starts)
_source_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "venue_pace_read_source", default=None)


def _rl_reset_state() -> None:
    _rl.clear()
    _rl.update({"consecutive": 0, "level_s": 0.0, "cooldown_s": 0.0,
                "until_mono": 0.0, "until_epoch": 0.0, "armed_mono": None,
                "cooldown_is": None, "retry_after_s": None,
                "rate_limited_total": 0, "escalations": 0,
                "in_flight_429s": 0, "deferred_total": 0,
                "deadline_waits": 0, "deadline_waited_s": 0.0,
                "priority_waits": 0, "priority_waited_s": 0.0,
                "resets": 0, "last_429": None, "last_ok_epoch": None,
                "last_deferred_epoch": None})
    _rl_sources.clear()


_rl_reset_state()


def reset_rate_limit_state() -> None:
    """Tests only: the cooldown, its counters and the per-source tallies."""
    with _rl_lock:
        _rl_reset_state()


def priority_now() -> bool:
    """Whether a claim made here, with no keyword, takes the priority lane
    (inside priority_claims())."""
    return bool(_priority_ctx.get())


def set_read_source(name: str | None):
    """Attribute every venue claim this task (and the threads it starts)
    makes to `name` -- a walker calls it once at the top of its loop.
    Returns the contextvar token."""
    return _source_ctx.set(str(name) if name else None)


@contextlib.contextmanager
def read_source(name: str | None):
    tok = _source_ctx.set(str(name) if name else None)
    try:
        yield
    finally:
        _source_ctx.reset(tok)


def current_read_source() -> str | None:
    return _source_ctx.get()


def _src(source: str | None) -> dict:
    name = source or "unattributed"
    s = _rl_sources.get(name)
    if s is None:
        s = _rl_sources[name] = {"rate_limited": 0, "deferred": 0,
                                 "skipped_by_walker": 0, "last_429": None,
                                 "last_deferred_epoch": None}
    return s


def _jitter() -> float:
    if _rand is not None:
        r = float(_rand())
    else:
        import random
        r = random.random()
    return min(max(r, 0.0), 0.999999)


#: A Retry-After longer than our cap is honoured (the venue's word beats our
#: number) -- up to this sanity bound, so a garbled header cannot park a lane
#: for a day.
RETRY_AFTER_SANITY_MAX_S = 3600.0


def clean_retry_after(value) -> float | None:
    """A Retry-After in seconds, or None when absent / not a finite
    non-negative number; bounded by RETRY_AFTER_SANITY_MAX_S."""
    try:
        ra = None if value is None else float(value)
    except (TypeError, ValueError):
        return None
    if ra is None or ra != ra or ra in (float("inf"), float("-inf")) or ra < 0:
        return None
    return min(ra, RETRY_AFTER_SANITY_MAX_S)


def escalate(level_s: float, retry_after_s=None, jitter: float = 0.0) -> dict:
    """PURE: the next rung of the escalating cooldown, shared by the venue
    gateway's cooldown here and the data-api throttle's (ratelimit).
    `level_s` is the current level (0: none since the last successful read);
    `jitter` in [0, 1). Returns {level_s, cooldown_s, cooldown_is}: the level
    is COOLDOWN_FLOOR_S, else double the previous one, capped at
    COOLDOWN_CAP_S; the cooldown is the level plus up to COOLDOWN_JITTER_FRAC
    of it, capped, and never less than a longer Retry-After."""
    level = (COOLDOWN_FLOOR_S if not level_s or level_s <= 0
             else min(COOLDOWN_CAP_S, float(level_s) * 2.0))
    j = min(max(float(jitter or 0.0), 0.0), 0.999999)
    wait = min(COOLDOWN_CAP_S, level * (1.0 + COOLDOWN_JITTER_FRAC * j))
    why = "ESCALATION"
    ra = clean_retry_after(retry_after_s)
    if ra is not None and ra > wait:
        wait, why = ra, "RETRY_AFTER"
    return {"level_s": level, "cooldown_s": wait, "cooldown_is": why}


def note_rate_limited(*, retry_after_s=None, dispatched_mono: float | None = None,
                      method: str | None = None, path: str | None = None,
                      source: str | None = None, now: float | None = None) -> dict:
    """A 429 was READ by the transport. Escalates (or, for a request already
    in flight when the cooldown was armed, only counts) and returns what was
    applied. Never raises; never takes the gate's lock. Also trips the
    reduced-rate circuit (`penalize`), as every 429 always should have."""
    mono = _clock() if now is None else float(now)
    wall = _wall()
    ra = clean_retry_after(retry_after_s)
    src = source if source is not None else _source_ctx.get()
    with _rl_lock:
        _rl["consecutive"] += 1
        _rl["rate_limited_total"] += 1
        last = {"at_epoch": round(wall, 3), "method": method, "path": path,
                "source": src or "unattributed", "retry_after_s": ra}
        _rl["last_429"] = last
        s = _src(src)
        s["rate_limited"] += 1
        s["last_429"] = dict(last)
        armed = _rl["armed_mono"]
        in_flight = (dispatched_mono is not None and armed is not None
                     and float(dispatched_mono) < armed
                     and mono < _rl["until_mono"])
        if in_flight:
            # the same episode: the request left before the cooldown was
            # armed. Counted, never escalated, never shortened.
            _rl["in_flight_429s"] += 1
            if ra is not None and mono + ra > _rl["until_mono"]:
                _rl["until_mono"] = mono + ra
                _rl["until_epoch"] = wall + ra
                _rl["cooldown_is"] = "RETRY_AFTER"
                _rl["retry_after_s"] = ra
            out = {"escalated": False, "why": "IN_FLIGHT_BEFORE_THE_COOLDOWN"}
        else:
            e = escalate(_rl["level_s"], ra, _jitter())
            level, wait, why = e["level_s"], e["cooldown_s"], e["cooldown_is"]
            _rl["level_s"] = level
            # EXTENDS ONLY: a cooldown already in force is never shortened
            if mono + wait > _rl["until_mono"]:
                _rl["until_mono"] = mono + wait
                _rl["until_epoch"] = wall + wait
            _rl["cooldown_s"] = round(wait, 3)
            _rl["armed_mono"] = mono
            _rl["cooldown_is"] = why
            _rl["retry_after_s"] = ra
            _rl["escalations"] += 1
            out = {"escalated": True, "level_s": level,
                   "cooldown_s": round(wait, 3), "cooldown_is": why}
        out.update(consecutive=_rl["consecutive"],
                   seconds_left=round(max(0.0, _rl["until_mono"] - mono), 3))
    try:
        penalize()
    except Exception:                                          # noqa: BLE001
        pass
    return out


def note_read_ok(*, dispatched_mono: float | None = None,
                 now: float | None = None) -> bool:
    """A 2xx READ came back. Resets the escalation -- consecutive count,
    level and the cooldown itself -- only when the request was dispatched
    after the current cooldown was armed (or none was ever armed). Returns
    whether it reset anything."""
    mono = _clock() if now is None else float(now)
    with _rl_lock:
        _rl["last_ok_epoch"] = round(_wall(), 3)
        armed = _rl["armed_mono"]
        if armed is not None and dispatched_mono is not None \
                and float(dispatched_mono) < armed:
            return False
        if _rl["consecutive"] == 0 and _rl["level_s"] == 0.0 \
                and mono >= _rl["until_mono"]:
            return False
        _rl.update(consecutive=0, level_s=0.0, until_mono=0.0,
                   until_epoch=0.0, armed_mono=None)
        _rl["resets"] += 1
        return True


def cooldown_left(now: float | None = None) -> float:
    """Seconds of the 429 cooldown left (0.0: none in force)."""
    mono = _clock() if now is None else float(now)
    with _rl_lock:
        return max(0.0, _rl["until_mono"] - mono)


def normal_read_deferral(now: float | None = None) -> dict | None:
    """None when a NORMAL-lane read may be sent now; else the named
    deferral a walker records before it stops its pass (nothing is counted
    here -- the walker counts what it skipped with `note_walker_skipped`)."""
    left = cooldown_left(now)
    if left <= 0:
        return None
    with _rl_lock:
        return {"refusal": R_VENUE_429_COOLDOWN_READ_DEFERRED,
                "seconds_left": round(left, 3),
                "consecutive_429": _rl["consecutive"],
                "level_s": _rl["level_s"],
                "cooldown_is": _rl["cooldown_is"]}


def note_deferred(source: str | None = None) -> None:
    """The transport refused a normal-lane read during the cooldown."""
    src = source if source is not None else _source_ctx.get()
    with _rl_lock:
        _rl["deferred_total"] += 1
        _rl["last_deferred_epoch"] = round(_wall(), 3)
        s = _src(src)
        s["deferred"] += 1
        s["last_deferred_epoch"] = _rl["last_deferred_epoch"]


def note_walker_skipped(n: int, source: str | None = None) -> None:
    """A walker stopped its pass on the cooldown and skipped `n` reads."""
    if not n:
        return
    src = source if source is not None else _source_ctx.get()
    with _rl_lock:
        _src(src)["skipped_by_walker"] += int(n)


def note_cooldown_wait(seconds: float, *, priority: bool) -> None:
    with _rl_lock:
        k = "priority" if priority else "deadline"
        _rl[k + "_waits"] += 1
        _rl[k + "_waited_s"] = round(_rl[k + "_waited_s"]
                                     + max(0.0, float(seconds)), 3)


def rate_limit_state(now: float | None = None) -> dict:
    """THE READBACK: the cooldown, the consecutive 429 count, the deferred
    and skipped counts and the last 429, process-wide and per source --
    JSON-safe, for the owning worker's heartbeat."""
    mono = _clock() if now is None else float(now)
    with _rl_lock:
        left = max(0.0, _rl["until_mono"] - mono)
        return {"version": COOLDOWN_VERSION, "scope": "THIS_PROCESS",
                "cooldown_active": left > 0,
                "cooldown_seconds_left": round(left, 3),
                "cooldown_until_epoch": (round(_rl["until_epoch"], 3)
                                         if left > 0 else None),
                "cooldown_s": _rl["cooldown_s"], "level_s": _rl["level_s"],
                "cooldown_is": _rl["cooldown_is"],
                "retry_after_s": _rl["retry_after_s"],
                "consecutive_429": _rl["consecutive"],
                "rate_limited_total": _rl["rate_limited_total"],
                "escalations": _rl["escalations"],
                "in_flight_429s": _rl["in_flight_429s"],
                "resets": _rl["resets"],
                "deferred_total": _rl["deferred_total"],
                "deadline_waits": _rl["deadline_waits"],
                "deadline_waited_s": _rl["deadline_waited_s"],
                "priority_waits": _rl["priority_waits"],
                "priority_waited_s": _rl["priority_waited_s"],
                "last_429": dict(_rl["last_429"]) if _rl["last_429"] else None,
                "last_ok_read_epoch": _rl["last_ok_epoch"],
                "last_deferred_epoch": _rl["last_deferred_epoch"],
                "by_source": {k: {kk: (dict(vv) if isinstance(vv, dict) else vv)
                                  for kk, vv in v.items()}
                              for k, v in _rl_sources.items()},
                "rule": {"floor_s": COOLDOWN_FLOOR_S, "cap_s": COOLDOWN_CAP_S,
                         "jitter_frac": COOLDOWN_JITTER_FRAC,
                         "priority_max_wait_s": PRIORITY_COOLDOWN_MAX_WAIT_S,
                         "reset": "A_2XX_READ_DISPATCHED_AFTER_THE_COOLDOWN_WAS_ARMED",
                         "normal_lane": R_VENUE_429_COOLDOWN_READ_DEFERRED,
                         "priority_lane": "BOUNDED_WAIT_THEN_PROCEED"}}


__all__ = ["pace", "priority_claims", "waiting", "lane_stats", "penalize", "penalty_left",
           "penalize_observed", "cooldown_state", "resume_cooldown",
           "effective_gap", "MIN_GAP_S", "PENALTY_MULT", "PENALTY_S",
           "GATE_FLOOR_S", "PACE_PRIORITY_BURST",
           "COOLDOWN_FLOOR_S", "COOLDOWN_CAP_S", "COOLDOWN_JITTER_FRAC",
           "PRIORITY_COOLDOWN_MAX_WAIT_S", "R_VENUE_429_COOLDOWN_READ_DEFERRED",
           "note_rate_limited", "note_read_ok", "cooldown_left",
           "normal_read_deferral", "note_deferred", "note_walker_skipped",
           "note_cooldown_wait", "rate_limit_state", "reset_rate_limit_state",
           "priority_now", "set_read_source", "read_source",
           "current_read_source", "escalate", "clean_retry_after",
           "RETRY_AFTER_SANITY_MAX_S"]
