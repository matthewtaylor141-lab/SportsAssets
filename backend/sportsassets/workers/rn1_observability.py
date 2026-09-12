"""RN1 forward-observability collector loop -- run 83.

MEASUREMENT ONLY. This worker never places, cancels or touches an order. It has
no order path at all: tests/test_obs_safety.py walks its import graph and fails
the build if one appears. It shares the wording with mirror_shadow.py,
price_path.py and edge_marks.py because it is the same kind of thing.

INERT UNTIL SWITCHED ON. With RN1_OBSERVABILITY_SHADOW unset -- the code default
-- main() logs one line and then PARKS FOREVER. Registering it in workers/all.py
therefore changes nothing about a running deployment until someone sets the flag,
and setting the flag enables DATA COLLECTION ONLY.

IT PARKS RATHER THAN RETURNS, and that is not a stylistic choice -- it was a
production defect (2026-09-12, found in the run 83 deployment gate). Every other
entry in workers/all.py's LOOPS is a `while True` that only ever ends by raising,
so the supervisor reads a CLEAN RETURN as an anomaly: it logs a WARNING and
restarts the loop after RESTART_DELAY_SECONDS. An inert main() that returned
therefore span -- start, log, return, warn, sleep 5s, repeat -- twelve times a
minute for as long as the flag stayed off, burying the worker log that every
incident is diagnosed from under about 17,000 warnings a day.

The collector cannot be woken by a flag flip in a running process either way: a
Render env change restarts the service. So parking costs nothing that polling
would buy, and it says the true thing to the supervisor -- this loop is alive and
has nothing to do -- instead of a false one.

WHY IT EXISTS. Run 82: physical actionable latency is NOT IDENTIFIABLE from
retained data, because every boundary anchored at RN1's fill crosses from a clock
we do not control to one we do, with no record of the offset. 92.04% of the chain
lane's detected_at - ts values are negative, which an elapsed time cannot be.
That is unrepairable backwards and can only be recorded forwards.
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from ..db import get_pool
from ..obs import collector, record
from ..obs.clock import boot_reference
from ..obs.config import shadow_enabled

log = logging.getLogger(__name__)

CLOCK_SYNC_INTERVAL_S = 300.0


async def _clock_sync_loop(pool) -> None:
    """Record what is known about clock synchronisation -- including nothing.

    A row saying the host's sync state could not be read is worth writing: it
    dates the ignorance. Writing nothing when nothing is known is what leaves a
    later reader unable to tell an unsynchronised host from an unobserved one --
    which is the position run 82 found itself in.
    """
    ref = boot_reference()
    while True:
        try:
            status, offset, error, method = _host_sync()
            await record.record_clock_sync(
                pool,
                method=method,
                host_sync_status=status,
                host_offset_s=offset,
                host_error_s=error,
                # No peer is read: nothing this repository calls exposes a
                # server-time endpoint, on any venue. That absence is a run 83I
                # finding, not an omission here.
                peer_name=None,
                offset_uncertainty_s=error,
                detail={k: str(v) for k, v in ref.items()},
            )
        except asyncio.CancelledError:
            raise
        except Exception:                              # noqa: BLE001
            log.exception("rn1 obs: clock sync record failed")
        await asyncio.sleep(CLOCK_SYNC_INTERVAL_S)


def _host_sync() -> tuple[str | None, float | None, float | None, str]:
    """Best-effort read of the host's clock discipline.

    Containers commonly expose none of this. When that is the case the honest
    answer is UNKNOWN with a NULL offset -- never a zero, which would read as
    "measured and perfect" to anyone who came along later.
    """
    try:
        import ctypes                                  # noqa: PLC0415

        class _Timex(ctypes.Structure):
            _fields_ = [("modes", ctypes.c_int), ("offset", ctypes.c_long),
                        ("freq", ctypes.c_long), ("maxerror", ctypes.c_long),
                        ("esterror", ctypes.c_long), ("status", ctypes.c_int),
                        ("constant", ctypes.c_long), ("precision", ctypes.c_long),
                        ("tolerance", ctypes.c_long),
                        ("time_sec", ctypes.c_long), ("time_usec", ctypes.c_long),
                        ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
                        ("jitter", ctypes.c_long), ("shift", ctypes.c_int),
                        ("stabil", ctypes.c_long), ("jitcnt", ctypes.c_long),
                        ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
                        ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int),
                        ("padding", ctypes.c_int * 11)]

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        tx = _Timex()
        rc = libc.adjtimex(ctypes.byref(tx))
        if rc < 0:
            return "UNKNOWN", None, None, "adjtimex_failed"
        # TIME_ERROR == 5: the kernel is not synchronised to a reference.
        status = "UNSYNCHRONISED" if rc == 5 else "SYNCHRONISED"
        return (status, tx.offset / 1e6, tx.maxerror / 1e6, "adjtimex")
    except Exception:                                  # noqa: BLE001
        return "UNKNOWN", None, None, "unavailable"


async def main() -> None:
    if not shadow_enabled():
        log.info("rn1 observability collector: RN1_OBSERVABILITY_SHADOW is off "
                 "(code default) -- collecting nothing, parking")
        # Park, do not return: the supervisor restarts a loop that returns.
        # asyncio.Event() that nobody sets waits forever without a timer, so
        # this costs one suspended coroutine and no wakeups at all.
        await asyncio.Event().wait()
        return                                     # unreachable; kept explicit

    pool = await get_pool()
    async with httpx.AsyncClient(timeout=10) as http:
        sync = asyncio.create_task(_clock_sync_loop(pool))
        try:
            await collector.run(pool, http)
        finally:
            sync.cancel()
            await asyncio.gather(sync, return_exceptions=True)
