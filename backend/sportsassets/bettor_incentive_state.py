"""The run row: what survives the restart the supervisor guarantees.

`workers/all.py:run_forever` is a `while True`. A process that returns
is started again five seconds later, so ANY cap held only in memory is
re-granted on every cycle -- an eight-request budget becomes eight
requests PER BOOT, which is not a budget at all. The same lesson is
already written into `bettor_live_control`'s allowance, for the same
reason, and this is that lesson applied to the public incentive reads.

So the request ledger is seeded FROM THE DATABASE at boot and written
BACK after every spend. A restart mid-run resumes the same run with
the same counters; it does not begin a fresh one. Two things identify
a run and both must match for it to be resumed: the ET DATE and the
MANIFEST ID. A different date is a different experiment; a different
manifest is a different allowlist, and resuming one run's counters into
another run's allowlist would be an accounting fiction.

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
                         subcaps: dict, boot_id: str) -> dict:
    """Continue this run if the row already describes it; else start one.

    Returns `{"run": row, "resumed": bool, "seed": {kind: spent}}`. The
    seed is what the in-process ledger starts from, so a restart cannot
    re-grant a spent allowance.
    """
    cur = await read_run(pool)
    row = cur.get("run") or {}
    same = (row.get("et_date") == et_date
            and row.get("manifest_id") == manifest_id)
    if cur["why"] == S_UNREADABLE:
        # FAIL CLOSED. If we cannot read what was spent we must not
        # assume nothing was.
        return {"ok": False, "why": S_UNREADABLE, "detail": cur.get("detail"),
                "resumed": False, "seed": {k: v for k, v in subcaps.items()}}
    if same and row.get("closed"):
        return {"ok": False, "why": S_CLOSED, "run": row, "resumed": False,
                "detail": "this run is closed (%s); arming a new one needs "
                          "a new ET date or a new manifest"
                          % row.get("close_reason"),
                "seed": {k: v for k, v in subcaps.items()}}
    if same:
        row = dict(row)
        row["boots"] = int(row.get("boots") or 0) + 1
        row["last_boot_at"] = time.time()
        row["last_boot_id"] = boot_id
        await _write(pool, row)
        return {"ok": True, "why": S_OPEN, "run": row, "resumed": True,
                "seed": dict(row.get("http_spent") or {})}

    fresh = {
        "state": STATE_VERSION,
        "run_id": "%s:%s" % (et_date, manifest_id),
        "et_date": et_date, "manifest_id": manifest_id,
        "http_spent": {k: 0 for k in subcaps},
        "subcaps": dict(subcaps),
        "opened_at": time.time(), "boots": 1, "last_boot_at": time.time(),
        "last_boot_id": boot_id, "closed": False, "close_reason": None,
    }
    await _write(pool, fresh)
    return {"ok": True, "why": S_OPEN, "run": fresh, "resumed": False,
            "seed": dict(fresh["http_spent"])}


async def persist_spend(pool, run: dict, spent: dict) -> bool:
    """Write the ledger back. Called AFTER each granted request.

    Writing after rather than before is deliberate and is the safe
    direction here: a crash between dispatch and write loses at most
    the record of one request that WAS made, and the next boot reads a
    count one low. Writing first would risk the opposite -- a count
    recorded for a request that never went out -- and of the two, an
    over-count is the one that silently shrinks the allowance while an
    under-count is visible in the journal, which records every
    dispatch.
    """
    row = dict(run)
    row["http_spent"] = dict(spent)
    row["last_spend_at"] = time.time()
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
        "survives_restart": True,
        # Named through the control module rather than spelled out, so a
        # test can assert the literal appears in NO write path here.
        "never_writes": "%s (arming stays a human action through "
                        "render-ops sql obs-run)" % _control_key(),
    }
