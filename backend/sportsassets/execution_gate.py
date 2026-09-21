"""One fail-closed authorization check, at the venue boundary.

WHY A SHARED GATE. Before this, authorization was re-implemented at each
caller, and the controls a route honoured depended on which route it
was. The 2026-09-21 audit found:

  * mirror_exit never called active_venue(), so LIVE_TRADING_ENABLED did
    not gate the whale-exit sell path at all;
  * _execute_manual_sell called active_venue() but neither _is_paused()
    nor copy_halted(), so the documented kill switch did not stop it;
  * whale_exits was armed by the ABSENCE of an environment variable.

Each was a different hole and each needed its own fix. There were eleven
submit_fok call sites across four modules; checking them one at a time
is how the next one gets missed.

THE BOUNDARY IS pmus.submit_fok AND pmus.close_position. Those two
functions are what actually send an order to the venue. Every route --
copy entry, manual desk, manual sell, whale exit, mirror reconciler,
underdog, calibration, any retry, any future caller -- passes through
one of them. Gating there means a new route is gated on the day it is
written, by nobody remembering anything.

CANCELLATION IS DELIBERATELY NOT GATED. pmus.cancel_order reduces
exposure. A paused, halted, unauthorized system must still be able to
pull its resting orders, and a gate that blocks cancels turns a kill
switch into a trap. Cancellation is its own path and stays open; that
is a decision, not an oversight.

AUTHORIZATION IS READ AT SUBMISSION, NEVER CARRIED. The gate takes no
token and accepts no argument saying "already checked". It reads the
authoritative state at the moment of the call, from a worker thread, via
the caller's own event loop. Work that was queued, retried, or slept
before a pause therefore cannot submit on the strength of a check made
before it -- there is nothing to carry.

FAIL CLOSED MEANS EVERY UNCERTAINTY DENIES:

    state never read            DENY  (nothing bound the gate)
    read raised                 DENY  (a database blip is not consent)
    read timed out              DENY
    value malformed             DENY  (somebody wrote something we do
                                       not understand into the switch)
    value missing               DENY  (absence is not permission -- this
                                       is the exact defect that armed
                                       WHALE_EXIT_ENABLED and left
                                       live_trading_paused unset)
    snapshot older than bound   DENY

GLOBAL vs COPY-SPECIFIC. Documented in CONTROLS below and enforced by
`lane`. Global controls bind every route. Copy controls bind only the
copy-trading lanes, because a loss breaker on the copy sleeve has no
business stopping a manual operator from flattening a position.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PAUSE_KEY = "live_trading_paused"

# How old a cached read may be before it stops counting as knowledge.
# Short, because its only job is to cover the microseconds between a
# bound loop answering and this thread using the answer.
MAX_STALE_S = 5.0

# How long to wait for the authoritative read before giving up. A gate
# that hangs is a gate that gets removed.
READ_TIMEOUT_S = 2.5

CONTROLS = {
    "global": (
        "live_trading_paused   the admin kill switch (ingestion_state)",
        "active_venue          LIVE_TRADING_ENABLED plus venue credentials",
    ),
    "copy": (
        "LIVE_COPY_HALT        emergency halt for the copy lanes",
        "mirror_loss_stop      rolling realized-loss breaker",
        "copy_overspend_halt   cumulative spend breaker",
    ),
}

# WHICH LANES ESCAPE THE COPY CONTROLS -- and it is an allowlist, not a
# blocklist, because the default has to be the strict one. A route that
# never declares its lane arrives as "unknown", and unknown gets
# everything: global controls AND copy controls. Listing the copy lanes
# instead would mean any new or unlabelled caller silently skipped the
# loss breaker, which is the same shape of defect as WHALE_EXIT_ENABLED
# being armed by absence.
#
# 'manual' is here because a loss breaker on the copy sleeve has no
# business stopping an operator flattening a position by hand. It still
# faces every global control, including the kill switch.
GLOBAL_ONLY_LANES = frozenset({"manual"})

# Kept as documentation of which lanes exist; membership is not what
# decides the check.
KNOWN_COPY_LANES = frozenset({"copy", "mirror", "whale_exit", "underdog",
                              "calibration"})


# THE LANE TRAVELS WITH THE WORK, NOT IN A PARAMETER. Order submission
# happens on a worker thread (asyncio.to_thread), which copies the
# calling context, so a ContextVar set by the route reaches the gate
# without every one of the eleven call sites growing an argument that a
# twelfth would forget.
#
# The default is "unknown", and unknown gets the strictest treatment.
_LANE = contextvars.ContextVar("execution_lane", default="unknown")


def current_lane() -> str:
    return _LANE.get()


def set_lane(lane: str):
    """Declare the lane for this task. Returns the ContextVar token."""
    return _LANE.set(lane)


class _LaneScope:
    def __init__(self, lane: str):
        self.lane = lane
        self.token = None

    def __enter__(self):
        self.token = _LANE.set(self.lane)
        return self

    def __exit__(self, *a):
        if self.token is not None:
            _LANE.reset(self.token)
        return False

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *a):
        return self.__exit__(*a)


def lane(name: str) -> _LaneScope:
    """`with gate.lane("manual"): ...` around a route's submission."""
    return _LaneScope(name)


class Denied(Exception):
    """Raised at the boundary. Carries the reason, never a credential."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__("%s%s" % (reason, (": " + detail) if detail else ""))


@dataclass
class Snapshot:
    paused: bool = True
    venue: str | None = None
    copy_halted: bool = True
    loss_stop: bool = True
    overspend: bool = True
    read_at: float = 0.0
    ok: bool = False
    why: str = "never read"


@dataclass
class _Binding:
    loop: object | None = None
    pool: object | None = None
    snapshot: Snapshot = field(default_factory=Snapshot)
    lock: threading.Lock = field(default_factory=threading.Lock)


_B = _Binding()


def bind(loop, pool) -> None:
    """Register the event loop and pool the gate may read through.

    Called once per process by whatever owns the loop. Until it is
    called the gate has no way to read the kill switch, and a process
    that cannot read the kill switch is not allowed to trade -- so the
    unbound state denies rather than assuming the best.
    """
    with _B.lock:
        _B.loop = loop
        _B.pool = pool
    log.info("execution gate bound; submissions will be authorized "
             "against the live kill switch")


async def bind_current_loop() -> bool:
    """Bind the gate to the running loop and the service's pool.

    LIVES HERE, NOT IN THE SERVICES, so it can be exercised without
    importing a workers package that pulls in the whole notification
    stack. The test that proves production binding should not be
    skippable because an unrelated dependency is missing from the
    environment -- which is exactly what happened on 2026-09-21.

    Never raises: a service that cannot bind still starts, and starts
    refusing orders rather than permitting them.
    """
    from .db import get_pool

    try:
        pool = await get_pool()
        bind(asyncio.get_running_loop(), pool)
        return True
    except Exception:  # noqa: BLE001 — unbound denies, so boot continues
        log.exception("execution gate NOT bound -- every order this "
                      "process attempts will be refused until it is")
        return False


def unbind() -> None:
    with _B.lock:
        _B.loop = None
        _B.pool = None
        _B.snapshot = Snapshot()


def _parse_switch(val) -> bool:
    """A pause value. Anything we do not understand means PAUSED.

    Somebody wrote something into the kill switch. Refusing is the only
    reading of that which cannot lose money.
    """
    if val is None:
        return False                    # absent row: handled by caller
    try:
        parsed = json.loads(val) if isinstance(val, str) else val
    except (TypeError, ValueError):
        raise Denied("kill_switch_malformed",
                     "value is not JSON; treating as paused")
    return bool(parsed)


async def read_state(pool) -> Snapshot:
    """The authoritative read. Every failure produces a denying state."""
    from . import live_executor as le

    snap = Snapshot(read_at=time.time())
    try:
        row = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", PAUSE_KEY)
    except Exception as exc:                               # noqa: BLE001
        snap.why = "kill switch unreadable: %s" % type(exc).__name__
        return snap
    try:
        # AN ABSENT ROW IS NOT PERMISSION. _is_paused historically
        # returned False here, which is how the control documented as
        # "no further orders" sat unarmed for the life of the system.
        # The gate treats absence as paused; the switch has to say, in
        # so many words, that trading may proceed.
        if row is None:
            snap.paused = True
            snap.why = ("kill switch row absent; absence is not "
                        "permission")
            return snap
        snap.paused = _parse_switch(row)
    except Denied as d:
        snap.why = d.reason
        return snap

    try:
        snap.venue = le.active_venue()
    except Exception as exc:                               # noqa: BLE001
        snap.why = "venue gate unreadable: %s" % type(exc).__name__
        return snap

    try:
        snap.copy_halted = bool(le.copy_halted())
        snap.overspend = bool(await le.overspend_halt(pool))
    except Exception as exc:                               # noqa: BLE001
        snap.why = "copy controls unreadable: %s" % type(exc).__name__
        return snap

    try:
        stop = await pool.fetchval(
            "SELECT value FROM ingestion_state WHERE key='mirror_loss_stop'")
        snap.loss_stop = stop is not None
    except Exception as exc:                               # noqa: BLE001
        snap.why = "loss stop unreadable: %s" % type(exc).__name__
        return snap

    snap.ok = True
    snap.why = "read"
    return snap


def _current() -> Snapshot:
    """Read now, through the bound loop. Falls back to a fresh-enough
    snapshot only to cover the instant between the two."""
    with _B.lock:
        loop, pool, snap = _B.loop, _B.pool, _B.snapshot

    if loop is None or pool is None:
        s = Snapshot()
        s.why = ("execution gate is not bound to a loop and pool, so the "
                 "kill switch cannot be read from this process")
        return s

    # CALLED FROM THE LOOP'S OWN THREAD? Then a blocking read is not
    # available: run_coroutine_threadsafe would schedule the coroutine
    # on the very loop this thread is blocking, and .result() would wait
    # for something that can never run. It does not hang -- the timeout
    # catches it -- but it denies for the wrong reason and burns
    # READ_TIMEOUT_S doing it.
    #
    # Production never lands here: every order path calls submit_fok
    # through asyncio.to_thread, so authorization happens on a worker
    # thread. This is for the caller who checks early from async code,
    # and for any future path that forgets. Such a caller gets the
    # snapshot if it is inside the staleness bound, and a denial if it
    # is not. The authoritative read still happens at the submission
    # itself, on the worker thread, which is the check that matters.
    try:
        asyncio.get_running_loop()
        on_loop_thread = True
    except RuntimeError:
        on_loop_thread = False

    if on_loop_thread:
        age = time.time() - snap.read_at
        if snap.ok and age <= MAX_STALE_S:
            return snap
        s = Snapshot()
        s.why = ("authorization was requested on the event loop's own "
                 "thread, where a blocking read is impossible, and no "
                 "snapshot is within %.0fs" % MAX_STALE_S)
        return s

    try:
        fut = asyncio.run_coroutine_threadsafe(read_state(pool), loop)
        fresh = fut.result(timeout=READ_TIMEOUT_S)
        with _B.lock:
            _B.snapshot = fresh
        return fresh
    except Exception as exc:                               # noqa: BLE001
        # The live read failed. A very recent snapshot is allowed to
        # stand in, and nothing older is.
        age = time.time() - snap.read_at
        if snap.ok and age <= MAX_STALE_S:
            return snap
        s = Snapshot()
        s.why = ("authorization read failed (%s) and no snapshot is "
                 "within %.0fs" % (type(exc).__name__, MAX_STALE_S))
        return s


def authorize(operation: str, *, lane: str = "unknown",
              slug: str | None = None) -> Snapshot:
    """Authorize one submission, or raise Denied. Safe from any thread.

    `operation` is what is being attempted ('submit', 'close_position').
    `lane` selects the copy controls. Anything not in COPY_LANES gets
    the global controls only -- including 'manual', because a loss
    breaker on the copy sleeve must not stop an operator flattening a
    position by hand.
    """
    snap = _current()

    if not snap.ok:
        raise Denied("authorization_unavailable", snap.why)
    if snap.paused:
        raise Denied("live_trading_paused",
                     "the admin kill switch is engaged")
    if not snap.venue:
        raise Denied("no_active_venue",
                     "LIVE_TRADING_ENABLED is off or no venue "
                     "credentials are configured")

    if lane not in GLOBAL_ONLY_LANES:
        if snap.copy_halted:
            raise Denied("copy_halted", "the copy lanes are halted")
        if snap.loss_stop:
            raise Denied("mirror_loss_stop",
                         "the rolling loss breaker has tripped")
        if snap.overspend:
            raise Denied("copy_overspend_halt",
                         "the cumulative spend breaker has tripped")

    log.info("gate: %s authorized for lane=%s venue=%s%s",
             operation, lane, snap.venue,
             (" slug=%s" % slug) if slug else "")
    return snap


def describe() -> dict:
    """For the operator endpoint and the tests. No credentials."""
    snap = _current()
    return {
        "bound": _B.loop is not None and _B.pool is not None,
        "readable": snap.ok,
        "why": snap.why,
        "paused": snap.paused,
        "venue": snap.venue,
        "copy_halted": snap.copy_halted,
        "loss_stop": snap.loss_stop,
        "overspend": snap.overspend,
        "age_s": round(time.time() - snap.read_at, 3) if snap.read_at else None,
        "controls": CONTROLS,
        "lane_now": current_lane(),
        "global_only_lanes": sorted(GLOBAL_ONLY_LANES),
        "known_copy_lanes": sorted(KNOWN_COPY_LANES),
        "cancellation": "NOT gated; cancels reduce exposure and stay "
                        "available while paused",
    }


# TESTS ONLY. Production binds a real loop and pool; this exists so the
# behavioural suite can drive every denial without a database, and it is
# the only way to install a snapshot the gate did not read itself.
def _install_snapshot_for_tests(snap: Snapshot) -> None:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("snapshot injection is test-only")
    with _B.lock:
        _B.loop = None
        _B.pool = None
        _B.snapshot = snap

    def _fixed():
        return snap

    globals()["_current"] = _fixed


def _restore_for_tests() -> None:
    globals()["_current"] = _current_real
    unbind()


_current_real = _current
