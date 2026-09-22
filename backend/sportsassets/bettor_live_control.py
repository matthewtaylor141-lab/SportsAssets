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


# ── THE PROBE BUDGET ─────────────────────────────────────────────────
#
# A BUDGET THAT RESETS ON RESTART IS NOT A BUDGET. `workers/all.py`
# restarts a returning loop forever, so a "40 candidates" cap held in
# memory buys 40 more every time the process cycles -- and on
# 2026-09-21 the loop cycled about 73 times in sixteen minutes. The
# same argument applies to a deadline measured from "now".
#
# So both live in one `ingestion_state` row beside the control:
#
#   started_at        when the probe first ran, ever
#   deadline_at       absolute. A restart resumes TOWARD it, never
#                     away from it.
#   distinct_consumed how many distinct markets have been enriched
#                     across the WHOLE probe, restarts included.
#
# The loop reads this before acquiring anything and refuses to exceed
# either bound. At expiry it DISARMS -- writes the observation control
# to false -- so the shutdown survives the restart that follows.
BUDGET_KEY = "bettor_live_probe_state"

# Defaults. Overridable per probe by whoever opens the budget, so the
# numbers in an approval are the numbers in the row.
PROBE_MAX_DISTINCT = 40
PROBE_DEADLINE_S = 1800.0

B_OPEN = "OPEN"
B_EXHAUSTED = "BUDGET_EXHAUSTED"
B_EXPIRED = "DEADLINE_PASSED"
B_UNREADABLE = "BUDGET_UNREADABLE"


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _parse_iso(raw) -> float | None:
    from datetime import datetime
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")) \
            .timestamp()
    except (TypeError, ValueError):
        return None


async def read_budget(pool=None, *, now: float | None = None) -> dict:
    """The probe's lifetime budget. FAILS CLOSED like the control.

    An unreadable or malformed budget is `B_UNREADABLE` with
    `remaining = 0`: we cannot show we are still inside it, so we are
    not.
    """
    now = time.time() if now is None else now
    out = {"key": BUDGET_KEY, "state": B_UNREADABLE, "open": False,
           "remaining": 0, "consumed": None, "max_distinct": None,
           "started_at": None, "deadline_at": None,
           "seconds_left": 0.0, "detail": None}

    async def _fetch():
        p = pool
        if p is None:
            from .db import get_pool
            p = await get_pool()
        return await p.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", BUDGET_KEY)

    try:
        row = await asyncio.wait_for(_fetch(),
                                     timeout=CONTROL_READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        out["detail"] = "budget read did not answer in %.0fs" % \
            CONTROL_READ_TIMEOUT_S
        return out
    except Exception as exc:                               # noqa: BLE001
        out["detail"] = "budget read failed: %s" % type(exc).__name__
        return out

    if row is None:
        out.update(state=B_UNREADABLE,
                   detail="no %s row; a probe without a declared "
                          "budget does not run" % BUDGET_KEY)
        return out

    val = row
    if isinstance(val, (bytes, bytearray)):
        val = val.decode("utf-8", "replace")
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except (TypeError, ValueError):
            out["detail"] = "budget value is not JSON"
            return out
    if not isinstance(val, dict):
        out["detail"] = "budget value is not an object"
        return out

    try:
        cap = int(val.get("max_distinct"))
        used = int(val.get("distinct_consumed", 0))
    except (TypeError, ValueError):
        out["detail"] = "max_distinct/distinct_consumed are not integers"
        return out
    deadline = _parse_iso(val.get("deadline_at"))
    if deadline is None:
        out["detail"] = "deadline_at is absent or unparsable"
        return out

    out.update(consumed=used, max_distinct=cap,
               started_at=val.get("started_at"),
               deadline_at=val.get("deadline_at"),
               seconds_left=round(deadline - now, 1),
               remaining=max(0, cap - used))
    if now >= deadline:
        out.update(state=B_EXPIRED, open=False, remaining=0,
                   detail="deadline %s passed" % val.get("deadline_at"))
    elif used >= cap:
        out.update(state=B_EXHAUSTED, open=False, remaining=0,
                   detail="%d of %d distinct markets already enriched"
                          % (used, cap))
    else:
        out.update(state=B_OPEN, open=True,
                   detail="%d of %d used, %.0fs left"
                          % (used, cap, deadline - now))
    return out


async def consume_budget(pool, n: int) -> None:
    """Add `n` distinct markets to the lifetime total.

    Written BEFORE the rows are used, so a crash between the read and
    the write costs budget rather than granting it.
    """
    if n <= 0:
        return
    await pool.execute(
        "UPDATE ingestion_state SET value = jsonb_set(value, "
        "'{distinct_consumed}', to_jsonb("
        "COALESCE((value->>'distinct_consumed')::int, 0) + $2)) "
        "WHERE key = $1", BUDGET_KEY, int(n))


async def disarm(pool, why: str) -> dict:
    """Write the observation control to FALSE. Never to true.

    This is how a deadline survives the restart that follows it: the
    loop stops itself, and the row it leaves behind stops the next
    process too. Arming is always a human action through
    `render-ops sql obs-run`; nothing in this codebase writes `true`.
    """
    try:
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, "
            "'false'::jsonb) ON CONFLICT (key) DO UPDATE SET "
            "value = 'false'::jsonb", CONTROL_KEY)
    except Exception as exc:                               # noqa: BLE001
        log.error("bettor_live_control: could not disarm (%s); the "
                  "loop stops anyway and the next process will read "
                  "whatever is there", type(exc).__name__)
        return {"disarmed": False, "why": why,
                "error": type(exc).__name__}
    log.warning("bettor_live_control: DISARMED -- %s set to false (%s)",
                CONTROL_KEY, why)
    return {"disarmed": True, "why": why}
