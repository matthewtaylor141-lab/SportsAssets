"""The run row: what survives the restart the supervisor guarantees.

`workers/all.py:run_forever` is a `while True`. A process that returns
is started again five seconds later, so ANY bound held only in memory
is re-granted on every cycle.

THE HTTP ALLOWANCE IS NOT HERE, AND THAT IS THE POINT. An earlier
version kept it here as a counter seeded at boot and written back
after each spend, which is not a ceiling: two overlapping workers seed
from the same number and both spend it, and a crash between two writes
loses the record of a request that was already sent. That allowance
now lives in `bettor_live_control`'s row and is taken by `reserve()`
under a row lock BEFORE each dispatch. Nothing in this module counts
requests.

WHAT DOES LIVE HERE is the run's IDENTITY and the socket totals. Two
things identify a run and both must match for it to be resumed: the ET
DATE and the MANIFEST ID. A different date is a different experiment;
a different manifest is a different allowlist. And the reconnect and
resubscription totals are carried across boots, because a bound that
resets when the supervisor restarts a crashed worker describes a run
that made ten times as many.

THIS MODULE NEVER WRITES THE OBSERVATION CONTROL. Arming is a human
action through `render-ops sql obs-run`, exactly as before. Nothing
here writes `bettor_live_observation` at all -- not true, not false.

Run:  python -m pytest backend/tests/test_bettor_incentive_state.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

log = logging.getLogger(__name__)

STATE_VERSION = "BETTOR_INCENTIVE_STATE_V1"
RUN_KEY = "bettor_incentive_run"
READ_TIMEOUT_S = 5.0

S_ABSENT = "RUN_ROW_ABSENT"
S_UNREADABLE = "RUN_ROW_UNREADABLE"
S_MALFORMED = "RUN_ROW_MALFORMED"
S_OPEN = "RUN_OPEN"
S_CLOSED = "RUN_CLOSED"


async def _resolve(pool):
    if pool is not None:
        return pool
    from .db import get_pool
    return await get_pool()


async def read_run(pool=None) -> dict:
    """The run row, or a NAMED refusal. Never raises."""
    out = {"state": STATE_VERSION, "key": RUN_KEY, "why": S_UNREADABLE,
           "run": None, "readable": False, "read_at": time.time()}

    async def _fetch():
        p = await _resolve(pool)
        return await p.fetchval(
            "SELECT value FROM ingestion_state WHERE key=$1", RUN_KEY)

    try:
        row = await asyncio.wait_for(_fetch(), timeout=READ_TIMEOUT_S)
    except asyncio.TimeoutError:
        out["detail"] = "read did not answer in %.0fs" % READ_TIMEOUT_S
        return out
    except Exception as exc:                # noqa: BLE001 -- named
        out["detail"] = "read failed: %s" % type(exc).__name__
        return out

    out["readable"] = True
    if row is None:
        return dict(out, why=S_ABSENT,
                    detail="no row for %s" % RUN_KEY)
    try:
        v = json.loads(row) if isinstance(row, (str, bytes)) else row
    except Exception:                       # noqa: BLE001
        return dict(out, why=S_MALFORMED, detail="value is not JSON")
    if not isinstance(v, dict):
        return dict(out, why=S_MALFORMED, detail="value is not an object")
    return dict(out, why=(S_CLOSED if v.get("closed") else S_OPEN), run=v)


async def _write(pool, value: dict) -> bool:
    try:
        p = await _resolve(pool)
        await p.execute(
            "INSERT INTO ingestion_state (key, value) VALUES ($1, $2::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            RUN_KEY, json.dumps(value, default=str))
        return True
    except Exception as exc:                # noqa: BLE001
        log.error("bettor_incentive_state: could not write the run row "
                  "(%s); the in-process counters still bind this boot, "
                  "but a restart would not see them", type(exc).__name__)
        return False


async def open_or_resume(pool, *, et_date: str, manifest_id: str,
                         boot_id: str) -> dict:
    """Continue this run if the row already describes it; else start one.

    Returns `{"run": row, "resumed": bool}`. The socket totals on the
    row are what `ReconnectBounds` is seeded from, so the reconnect and
    resubscription ceilings are PER RUN rather than per boot.
    """
    cur = await read_run(pool)
    row = cur.get("run") or {}
    same = (row.get("et_date") == et_date
            and row.get("manifest_id") == manifest_id)
    if cur["why"] == S_UNREADABLE:
        # FAIL CLOSED. A run row we cannot read is not a run we may
        # start: the socket totals it holds are a bound, and an
        # unreadable bound is not an absent one.
        return {"ok": False, "why": S_UNREADABLE, "detail": cur.get("detail"),
                "resumed": False}
    if same and row.get("closed"):
        return {"ok": False, "why": S_CLOSED, "run": row, "resumed": False,
                "detail": "this run is closed (%s); arming a new one needs "
                          "a new ET date or a new manifest"
                          % row.get("close_reason")}
    if same:
        row = dict(row)
        row["boots"] = int(row.get("boots") or 0) + 1
        row["last_boot_at"] = time.time()
        row["last_boot_id"] = boot_id
        await _write(pool, row)
        return {"ok": True, "why": S_OPEN, "run": row, "resumed": True}

    fresh = {
        "state": STATE_VERSION,
        "run_id": "%s:%s" % (et_date, manifest_id),
        "et_date": et_date, "manifest_id": manifest_id,
        # SOCKET TOTALS FOR THE WHOLE RUN, not for one process.
        "reconnects": 0, "resubscribes": 0,
        "opened_at": time.time(), "boots": 1, "last_boot_at": time.time(),
        "last_boot_id": boot_id, "closed": False, "close_reason": None,
    }
    await _write(pool, fresh)
    return {"ok": True, "why": S_OPEN, "run": fresh, "resumed": False}


async def note_socket(pool, run: dict, *, reconnects: int,
                      resubscribes: int) -> bool:
    """Carry the run's socket totals forward.

    Written at every epoch transition and at close -- low frequency by
    construction, because an epoch transition IS a reconnect. Absolute
    run totals are stored, not deltas, so a lost write costs at most
    one boot's contribution rather than corrupting the running sum.
    """
    row = dict(run)
    row["reconnects"] = max(0, int(reconnects))
    row["resubscribes"] = max(0, int(resubscribes))
    row["last_socket_at"] = time.time()
    return await _write(pool, row)


def _control_key() -> str:
    from . import bettor_live_control as _ctl
    return _ctl.CONTROL_KEY


async def close_run(pool, run: dict, reason: str) -> bool:
    row = dict(run)
    row["closed"] = True
    row["close_reason"] = reason
    row["closed_at"] = time.time()
    return await _write(pool, row)


def describe() -> dict:
    return {
        "state": STATE_VERSION, "table": "ingestion_state", "key": RUN_KEY,
        "identifies_a_run_by": ["et_date", "manifest_id"],
        "holds": ["run identity", "boot count",
                  "reconnect and resubscribe totals FOR THE RUN"],
        "does_not_hold": "the HTTP allowance -- that is reserved under a "
                         "row lock by bettor_live_control.reserve()",
        "survives_restart": True,
        # Named through the control module rather than spelled out, so a
        # test can assert the literal appears in NO write path here.
        "never_writes": "%s (arming stays a human action through "
                        "render-ops sql obs-run)" % _control_key(),
    }
