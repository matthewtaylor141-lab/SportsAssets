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

A CACHED SNAPSHOT NEVER AUTHORIZES. 2026-09-21, found by review, and it
was mine: _current() fell back to a snapshot up to MAX_STALE_S old
whenever the live read failed, and returned the cached snapshot outright
when called on the loop thread. A reproduction installed a recent
ALLOWED snapshot, made run_coroutine_threadsafe raise TimeoutError, and
authorize("submit", lane="manual") RETURNED SUCCESSFULLY. The module
said unreadable authorization denies; the code said a five-second-old
yes is good enough. A kill switch that keeps saying yes for five seconds
after it stops being readable is not a kill switch.

The two are now separate functions and they are not interchangeable:

    _authorize_read()   fresh read, or a denial. Never cached. The ONLY
                        input to authorize().
    _last_known()       whatever was last read, with its age. Feeds
                        describe() and nothing else. Diagnostics may be
                        stale; permission may not.

And the loop-thread caller no longer gets a cached yes. A synchronous
submission that cannot obtain a fresh read is denied, and async callers
have authorize_async(), which awaits the real read on their own loop.

FAIL CLOSED MEANS EVERY UNCERTAINTY DENIES:

    state never read            DENY  (nothing bound the gate)
    read raised                 DENY  (a database blip is not consent)
    read timed out              DENY  (no cached fallback, at all)
    value malformed             DENY  (somebody wrote something we do
                                       not understand into the switch)
    value not a JSON boolean    DENY  (0, [], {}, null and "" are not
                                       false -- see _parse_switch)
    value missing               DENY  (absence is not permission -- this
                                       is the exact defect that armed
                                       WHALE_EXIT_ENABLED and left
                                       live_trading_paused unset)
    called where no fresh read  DENY  (the loop thread, synchronously)

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

# DIAGNOSTICS ONLY. How old the last known snapshot may be before
# describe() stops calling it current. It has no part in authorization:
# there is no age at which a cached snapshot may approve a submission.
# It used to be the staleness allowance on the authorization fallback,
# which is the fail-open the review reproduced.
MAX_STALE_S = 5.0

# How long to wait for the authoritative read before giving up. A gate
# that hangs is a gate that gets removed. Giving up DENIES.
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
    """A pause value. It must BE a boolean. Anything else is PAUSED.

    THE DEFECT THIS REPLACES, found by review 2026-09-21: the old body
    ended `return bool(parsed)`, and Python's truthiness is not JSON's
    boolean. Every one of these decoded to a falsy value and therefore
    to NOT PAUSED -- the permissive answer:

        "0"      -> 0     -> False     trading allowed
        "[]"     -> []    -> False     trading allowed
        "{}"     -> {}    -> False     trading allowed
        "null"   -> None  -> False     trading allowed
        '""'     -> ""    -> False     trading allowed

    None of those is `false`. They are a number, two empty containers
    and a null, and the honest reading of each is "the switch does not
    say anything I understand", which the module's own contract says
    must deny. A truthiness cast turned five kinds of nonsense into
    permission.

    Production writes 'true'::jsonb / 'false'::jsonb through render-ops
    pause-on / pause-off, so a real boolean is what the switch actually
    holds; this rejects everything that is not one.

    Returns True (paused) or False (not paused). Raises Denied for
    anything that is not a JSON boolean, which the caller turns into an
    unreadable -- and therefore denying -- snapshot.
    """
    if val is None:
        return False                    # absent row: handled by caller
    if isinstance(val, bool):
        return val                      # a driver that decodes jsonb
    if isinstance(val, (bytes, bytearray)):
        try:
            val = val.decode("utf-8")
        except UnicodeDecodeError:
            raise Denied("kill_switch_malformed",
                         "value is not decodable text")
    if not isinstance(val, str):
        raise Denied("kill_switch_not_boolean",
                     "value is %s, not a boolean" % type(val).__name__)
    try:
        parsed = json.loads(val)
    except (TypeError, ValueError):
        raise Denied("kill_switch_malformed",
                     "value is not JSON; treating as paused")
    if not isinstance(parsed, bool):
        raise Denied("kill_switch_not_boolean",
                     "value decodes to %s, and only true or false may "
                     "release the kill switch"
                     % ("null" if parsed is None else type(parsed).__name__))
    return parsed


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


def _last_known() -> Snapshot:
    """The last snapshot anyone read, whatever its age. DIAGNOSTICS ONLY.

    describe() calls this so an operator can see what the gate last
    saw and how long ago. It is deliberately not reachable from
    authorize(): a stale reading is information, not consent.
    """
    with _B.lock:
        return _B.snapshot


def _authorize_read() -> Snapshot:
    """A snapshot THIS CALL read, or a denying one. Never cached.

    Every path out of here is either a read that just succeeded, or a
    Snapshot with ok=False. There is no branch that returns _B.snapshot.
    That is the whole point: the previous version had two such branches
    and both granted permission after a failed read.
    """
    with _B.lock:
        loop, pool = _B.loop, _B.pool

    if loop is None or pool is None:
        s = Snapshot()
        s.why = ("execution gate is not bound to a loop and pool, so the "
                 "kill switch cannot be read from this process")
        return s

    # CALLED FROM THE LOOP'S OWN THREAD? Then a blocking read is not
    # available: run_coroutine_threadsafe would schedule the coroutine
    # on the very loop this thread is blocking, and .result() would wait
    # for something that can never run.
    #
    # This used to return the cached snapshot here, which is a fail-open
    # wearing a comment about diagnostics. It now denies, and the caller
    # who legitimately needs to authorize from async code uses
    # authorize_async(), which awaits the real read on its own loop.
    #
    # Production does not land here: every order path calls submit_fok
    # through asyncio.to_thread, so authorization happens on a worker
    # thread and takes the real read below.
    try:
        asyncio.get_running_loop()
        on_loop_thread = True
    except RuntimeError:
        on_loop_thread = False

    if on_loop_thread:
        s = Snapshot()
        s.why = ("synchronous authorization was requested on the event "
                 "loop's own thread, where a fresh read is impossible; "
                 "use authorize_async() from async code")
        return s

    try:
        fut = asyncio.run_coroutine_threadsafe(read_state(pool), loop)
        fresh = fut.result(timeout=READ_TIMEOUT_S)
    except Exception as exc:                               # noqa: BLE001
        s = Snapshot()
        s.why = ("authorization read failed (%s); a previous answer does "
                 "not authorize a later submission"
                 % type(exc).__name__)
        return s
    with _B.lock:
        _B.snapshot = fresh
    return fresh


def _decide(snap: Snapshot, operation: str, lane: str,
            slug: str | None) -> Snapshot:
    """The control checks, given a snapshot that was just read."""
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


def authorize(operation: str, *, lane: str = "unknown",
              slug: str | None = None) -> Snapshot:
    """Authorize one submission, or raise Denied. Safe from any thread.

    `operation` is what is being attempted ('submit', 'close_position').
    `lane` selects the copy controls. Anything not in GLOBAL_ONLY_LANES
    gets the copy controls too -- including 'unknown', because a route
    that never declared itself must not be the one that escapes.

    Called on the event loop's own thread this DENIES rather than
    falling back to anything; async callers want authorize_async().
    """
    return _decide(_authorize_read(), operation, lane, slug)


async def authorize_async(operation: str, *, lane: str = "unknown",
                          slug: str | None = None) -> Snapshot:
    """The same decision, from async code, on a read taken right now.

    THE FRESH ASYNCHRONOUS PATH. A coroutine cannot block on
    run_coroutine_threadsafe against its own loop, so before this
    existed the only answer available on the loop thread was a cached
    one -- and a cached yes is what the review reproduced. This awaits
    read_state directly: same authority, same controls, no cache.
    """
    with _B.lock:
        pool = _B.pool
    if pool is None:
        snap = Snapshot()
        snap.why = ("execution gate is not bound to a pool, so the kill "
                    "switch cannot be read from this process")
        return _decide(snap, operation, lane, slug)
    try:
        snap = await asyncio.wait_for(read_state(pool),
                                      timeout=READ_TIMEOUT_S)
    except Exception as exc:                               # noqa: BLE001
        snap = Snapshot()
        snap.why = ("authorization read failed (%s); a previous answer "
                    "does not authorize a later submission"
                    % type(exc).__name__)
        return _decide(snap, operation, lane, slug)
    with _B.lock:
        _B.snapshot = snap
    return _decide(snap, operation, lane, slug)


def describe() -> dict:
    """For the operator endpoint and the tests. No credentials.

    Reads the LAST KNOWN snapshot, not a fresh one, and says how old it
    is. Diagnostics are allowed to be stale; this function cannot
    authorize anything.
    """
    snap = _last_known()
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
        "stale": (snap.read_at == 0.0
                  or (time.time() - snap.read_at) > MAX_STALE_S),
        "note": ("readable/paused/venue describe the LAST READ, not a "
                 "fresh one. A submission is authorized against a read "
                 "taken at the moment of the call; this view never is."),
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
#
# IT REPLACES THE AUTHORIZATION READ, which is exactly why it is
# allowlisted to a named set of modules in conftest rather than applied
# suite-wide: under it, bind(), read_state() and every fail-closed
# branch above are dead code, so a test running under it proves nothing
# about them. test_execution_gate_integration.py runs with it OFF and
# asserts so on its first line.
def _install_snapshot_for_tests(snap: Snapshot) -> None:
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("snapshot injection is test-only")
    with _B.lock:
        _B.loop = None
        _B.pool = None
        _B.snapshot = snap

    def _fixed():
        return snap

    globals()["_authorize_read"] = _fixed


def _restore_for_tests() -> None:
    globals()["_authorize_read"] = _authorize_read_real
    unbind()


_authorize_read_real = _authorize_read
