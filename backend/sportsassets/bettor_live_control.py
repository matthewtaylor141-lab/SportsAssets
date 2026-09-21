"""The observation loop's own stop control, in the database.

WHY NOT THE ENVIRONMENT VARIABLE. `BETTOR_LIVE_LOOP=off` was set on
sportsassets-workers at 2026-09-21T22:38:58Z and acknowledged (HTTP
200, three characters stored). Render raised no deploy for it. An
explicit restart was then requested and DID happen --
`server_restarted` at 22:43:40.131647Z in the service's own event log
-- and at 22:47:29Z the loop was still running the discovery path
rather than logging that it was disabled. **The restarted process did
not read the new value.** An environment variable on this service is
therefore not a demonstrated control, and the loop must not depend on
one to stop.

This control lives in `ingestion_state`, the same table the execution
gate's pause switch lives in, and it is read by the running process on
a timer. Changing it needs no deploy, no restart and no environment
change: one row, one UPDATE, and the loop stops within
`CONTROL_EVERY_S`.

IT FAILS CLOSED, and there are three separate ways to be closed:

  * UNREADABLE -- the query raised. A control we cannot read is not
    permission to keep observing.
  * ABSENT -- no row. Following `execution_gate.read_state`, absence is
    not permission. The row has to say, in so many words, that
    observation may run.
  * MALFORMED -- a row that is not a boolean and not `{"run": bool}`.
    A value nobody can parse is not a yes.

Only an explicit, parsable `true` runs. That also makes activation an
ordinary database write rather than a deployment, which is the point:
the loop ships registered and STOPPED, and someone turns it on.

THIS IS NOT A TRADING CONTROL. It governs one observation loop. It
does not read, write or influence `live_trading_paused`, `mirror_live`
or the execution gate, and it cannot authorize an order -- the loop it
stops has no order path to begin with.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

CONTROL_VERSION = "BETTOR_LIVE_CONTROL_V1"

# The one row this module reads. Namespaced to the observation lane so
# it can never be confused with a trading switch.
CONTROL_KEY = "bettor_live_observation"

# How often the running loop re-reads it. Short enough that a stop is
# operationally quick, long enough that it is not a hot query.
CONTROL_EVERY_S = 30.0

# THE READ IS BOUNDED. Resolving the process pool can retry for a long
# time against a database that is down, and a control read that hangs
# is a control that has no answer -- which is indistinguishable, from
# the outside, from a loop that decided to keep going. The wait is
# capped and the timeout is one more way to be CLOSED.
CONTROL_READ_TIMEOUT_S = 10.0

# Why the loop is not running, enumerated so a report can count them.
W_RUN = "RUNNING"
W_STOPPED = "STOPPED_BY_CONTROL"
W_ABSENT = "CONTROL_ROW_ABSENT"
W_UNREADABLE = "CONTROL_UNREADABLE"
W_MALFORMED = "CONTROL_MALFORMED"

_CLOSED = (W_STOPPED, W_ABSENT, W_UNREADABLE, W_MALFORMED)


def _parse(raw) -> tuple[bool, str, str]:
    """(run, why, detail) from the stored value.

    Accepts a bare JSON boolean and the object form `{"run": bool}`.
    The object form exists so an operator can leave a note beside the
    switch -- `{"run": false, "by": "...", "at": "..."}` -- without the
    note changing what the switch means. Anything else is MALFORMED,
    which is closed.
    """
    val = raw
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", "replace")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (TypeError, ValueError):
            return False, W_MALFORMED, "value is not JSON"
    if isinstance(val, bool):
        return (val, W_RUN if val else W_STOPPED,
                "bare boolean %s" % ("true" if val else "false"))
    if isinstance(val, dict):
        if "run" not in val:
            return False, W_MALFORMED, "object has no 'run' key"
        r = val.get("run")
        if not isinstance(r, bool):
            return False, W_MALFORMED, (
                "'run' is %s, not a boolean"
                % ("null" if r is None else type(r).__name__))
        return (r, W_RUN if r else W_STOPPED,
                "object run=%s" % ("true" if r else "false"))
    return False, W_MALFORMED, (
        "value decodes to %s, and only true, false or {\"run\": bool} "
        "may release the control"
        % ("null" if val is None else type(val).__name__))


async def read_control(pool=None) -> dict:
    """The authoritative read. EVERY failure answers `run: False`.

    `pool` is passed in by the loop (and by the tests). When it is
    None the module resolves the process pool itself, so a caller that
    has none -- a script, a probe -- still gets a real answer rather
    than an exception.
    """
    out = {"control": CONTROL_VERSION, "key": CONTROL_KEY,
           "run": False, "why": W_UNREADABLE, "detail": None,
           "read_at": time.time(), "readable": False}
    async def _fetch():
        p = pool
        if p is None:
            from .db import get_pool
            p = await get_pool()
        return await p.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", CONTROL_KEY)

    try:
        row = await asyncio.wait_for(_fetch(),
                                     timeout=CONTROL_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        out["detail"] = "read did not answer in %.0fs" % \
            CONTROL_READ_TIMEOUT_S
        return out
    except Exception as exc:                               # noqa: BLE001
        # NAMED, not swallowed. "The control is unreadable" and "the
        # control says stop" are different operational facts and the
        # report has to be able to tell them apart.
        out["detail"] = "read failed: %s" % type(exc).__name__
        return out

    out["readable"] = True
    if row is None:
        out.update(why=W_ABSENT,
                   detail="no row for %s; absence is not permission"
                          % CONTROL_KEY)
        return out
    run, why, detail = _parse(row)
    out.update(run=run, why=why, detail=detail)
    return out


def is_closed(state: dict) -> bool:
    """True when the loop must not observe. The inverse of one flag."""
    return not bool((state or {}).get("run")) or \
        (state or {}).get("why") in _CLOSED


def describe() -> dict:
    return {
        "control": CONTROL_VERSION,
        "table": "ingestion_state",
        "key": CONTROL_KEY,
        "checked": "at startup, then every %.0f s while running"
                   % CONTROL_EVERY_S,
        "fails_closed_on": [W_UNREADABLE, W_ABSENT, W_MALFORMED],
        "runs_only_on": "an explicit true, or {\"run\": true}",
        "changes_without_deploy": True,
        "governs": "the bettor observation loop only",
        "does_not_govern": ["live_trading_paused", "mirror_live",
                            "the execution gate", "any order path"],
        "why_not_env": ("BETTOR_LIVE_LOOP=off was set and the service "
                        "was restarted (server_restarted "
                        "2026-09-21T22:43:40.131647Z); the restarted "
                        "process still did not read it"),
    }
