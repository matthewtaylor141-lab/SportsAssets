"""Worker: ALL background loops in one process (cheap single-instance deploys).

Deploy marker 2026-08-07: the worker must be running >= 0e358f0 (RN1
reinstated in copy_sports) and 460425e (hourly sweep). If the copy_sweep
heartbeat lags more than ~70 minutes, this service is on stale code —
check the host dashboard for a stuck deploy.

Runs the poller, chain listener, metadata refresher, analytics, dispatcher,
roster, and reconciler as supervised asyncio tasks. Each loop is restarted
with a delay if it ever crashes, so one bad loop can't take down the rest.

Use this on hosts where each background service is billed separately
(e.g. one Render worker). docker-compose keeps them as separate containers.
"""

import asyncio
import concurrent.futures
import logging
import math
import os
import time
from collections.abc import Awaitable, Callable

from .. import procmem
from ..db import heartbeat
from . import (analytics, chain_listener, copy_sweep, dispatcher, edge_marks,
               metadata_refresher, mirror_live, mirror_shadow, poller, premap,
               price_path, reconciler, retention, roster, roster_auto, underdog,
               whale_exits)

# THE ARENA CAP, AT IMPORT (2026-09-05). sportsassets-workers was
# OOM-killed at 2 GiB thirteen times between 17:59:41 and 20:21:49:
# every five to ten minutes until the analytics replay was bounded
# (d18cc72, ebc24ce, live 19:01-19:06), every twenty to twenty-four
# after, and the log lines in the seconds before each kill were only
# the steady-state mix. The API measured the same ratchet in August
# (api/app.py, the 2026-08-25 census): freed memory kept in glibc's
# per-thread arenas, which every asyncio.to_thread venue call in this
# process can grow, and answered it with this cap and a periodic trim.
# M_ARENA_MAX only bounds arenas that do not exist yet, so this runs at
# import, the API's placement. After the loop imports is early enough:
# an arena is created when a thread first mallocs, and no loop module
# starts a thread at import -- grep finds threading only in
# venue_pace.py (a Lock, no thread), every to_thread/run_in_executor
# sits inside a coroutine, and a fresh interpreter importing this
# module counts one thread, MainThread (test_workers_memory_watch
# measures that census). Above the imports it would cost a noqa on
# every one of them for nothing. The status string rides every trim
# line so the log says whether the cap took.
_ARENA_STATUS = procmem.cap_malloc_arenas()

log = logging.getLogger(__name__)

RESTART_DELAY_SECONDS = 5

# THE MEMORY WATCH (2026-09-05). Thirteen kills, and this process had no
# reading of its own memory: the only figure was Render's kill line,
# and the lines before each kill were the steady-state mix. This loop
# is the instrument. Every WORKERS_MEM_SAMPLE_S it reads VmRSS; a
# sample WORKERS_MEM_JUMP_MB above the previous one is ONE warning with
# both readings and the seconds between them, so the loop whose lines
# sit at that second in the log is the suspect -- the question the
# kill log could not answer. Every WORKERS_TRIM_INTERVAL_S it runs
# malloc_trim off the event loop (the API's periodic trim) on a thread
# of its own -- the default executor is the one every venue call in
# this process queues on, and a trim waiting behind a full pool would
# hold the sampling with it -- reading RSS before and after IN THAT
# THREAD, so the pair brackets the trim and nothing else: a jump that
# lands during the trim is between the two readings and warned like
# any other, never booked as a negative return. Every reading, the
# cycle's sample and both sides of a trim, is judged against the last
# one before it becomes the baseline. The trim line carries the
# figures with the arena status so the log itself says whether the cap
# took, and the loop beats 'workers_memory' (status 'high' from
# WORKERS_MEM_HIGH_MB up) so /api/health/services carries them. A
# figure that cannot be read is '?' in the line and None in the beat,
# never a number. The knobs are read once per start: a value that does
# not parse is the default, one below its floor is the floor, each
# with a line. Nothing in a cycle raises out -- a watch that dies on
# its own error is the instrument failing in the moment it is needed
# -- and the clock is _now so a test can drive it without sleeping.
_now = time.monotonic
MEMORY_SERVICE = "workers_memory"


def _env_float(name: str, default: float, *, floor: float | None = None) -> float:
    """A knob from the env: the default when unset, blank or unparseable
    (nan and inf included -- a nan interval never trims and an inf one
    never samples), the floor when below it, each with a line."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        log.warning("memory watch: %s=%r does not parse; using %s", name, raw, default)
        return default
    if floor is not None and value < floor:
        log.warning("memory watch: %s=%s is below the floor; using %s", name, value, floor)
        return floor
    return value


def _mb(value: float | None, *, signed: bool = False) -> str:
    """One figure for the trim line: whole MB, rounded (not truncated:
    1250.7 is 1251), '?' when it could not be read -- never a number
    the process did not measure."""
    if value is None:
        return "?"
    return ("%+d" if signed else "%d") % round(value)


async def _beat(status: str, detail: dict) -> None:
    """One heartbeat: written when it can be, logged when it cannot,
    never a raise. memory_watch runs this as a task it owns rather than
    awaiting it in the cycle: db.heartbeat goes through get_pool, and
    against a dead database get_pool walks 1+2+4+8+15x6 = 105 s of
    backoff before it raises (tests/test_workers_survive_a_dead_db.py).
    Awaited, that walk would hold the sampling for 105 s at every trim
    -- and on exactly the night this watch is for, the database was
    dead too. At most one beat is in flight; one still pending when the
    next is due is skipped with a line, and the loop cancels a pending
    beat on its way out."""
    try:
        await heartbeat(MEMORY_SERVICE, status, detail)
    except Exception as exc:  # noqa: BLE001 -- the beat is telemetry
        # the type is named because a TimeoutError's str() is empty
        log.warning("workers_memory heartbeat %r not written: %s: %s",
                    status, type(exc).__name__, exc)


async def memory_watch() -> None:
    sample_s = _env_float("WORKERS_MEM_SAMPLE_S", 5.0, floor=1.0)
    # floor 1: at 0 (or below) every non-decreasing sample is a "jump"
    jump_mb = _env_float("WORKERS_MEM_JUMP_MB", 64.0, floor=1.0)
    trim_s = _env_float("WORKERS_TRIM_INTERVAL_S", 60.0, floor=5.0)
    high_mb = _env_float("WORKERS_MEM_HIGH_MB", 1536.0)
    boot: float | None = None          # the first readable reading
    peak: float | None = None
    prev: float | None = None          # the last readable reading ...
    prev_at = _now()                   # ... and the clock when it was read
    jumps = 0
    last_trim = _now()
    pending: asyncio.Task | None = None
    warned_no_trim = False
    # THE TRIM'S OWN THREAD (2026-09-05). asyncio.to_thread would queue
    # the trim on the default executor, the pool every venue call in
    # this process runs on (forty-odd asyncio.to_thread sites in
    # live_executor.py alone, the slowest under a two-minute
    # wait_for); with that pool full the trim waits its turn and the
    # sampling waits with it. One thread of the watch's own, named so a
    # thread census can tell it apart, shut down (without waiting on a
    # trim in flight) the moment the loop leaves.
    trim_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="memory.trim")
    loop = asyncio.get_running_loop()

    def judge(reading: float | None, at: float) -> None:
        """Every reading passes here -- the cycle's sample and both
        sides of a trim -- and none becomes the baseline unjudged, so a
        jump that lands while the trim thread runs is the step from
        `before` to `after`, warned like any other. An unreadable
        reading changes nothing: the baseline stays the last readable
        one and the seconds on the next jump line count from it."""
        nonlocal boot, peak, prev, prev_at, jumps
        if reading is None:
            return
        if boot is None:
            boot = reading
        if peak is None or reading > peak:
            peak = reading
        if prev is not None:
            # one-decimal readings: the step is rounded to one before it
            # is judged and printed, so 1024.1 - 960.1 is 64, not
            # 63.999999999999886; the figures are rounded, not truncated
            step = round(reading - prev, 1)
            if step >= jump_mb:
                jumps += 1
                log.warning("workers rss jump +%d MB in %.0fs: %d -> %d MB",
                            round(step), at - prev_at, round(prev), round(reading))
        prev, prev_at = reading, at

    def trim_pair() -> tuple[float | None, float, bool, float | None, float]:
        """In the trim thread: the reading before, the trim, the reading
        after, each with the clock -- one thread and no event-loop
        yield between them, so the pair brackets the trim and nothing
        else. Read on the loop with the trim awaited in between, the
        'before' was a sample taken before an unbounded yield, and a
        jump landing in that yield came back as a negative return."""
        before, before_at = procmem.rss_mb(), _now()
        ran = procmem.malloc_trim()
        after, after_at = procmem.rss_mb(), _now()
        return before, before_at, ran, after, after_at

    try:
        while True:
            try:
                now = _now()
                if now - last_trim < trim_s:
                    judge(procmem.rss_mb(), now)          # a plain sample
                else:
                    last_trim = now
                    before, before_at, ran, after, after_at = await loop.run_in_executor(
                        trim_pool, trim_pair)
                    judge(before, before_at)
                    judge(after, after_at)
                    if not ran:
                        freed: float | None = 0.0
                        if not warned_no_trim:
                            warned_no_trim = True
                            log.warning("workers malloc_trim unavailable: "
                                        "nothing is returned to the OS")
                    elif before is None or after is None:
                        freed = None
                    else:
                        # what the trim gave back: another loop allocating
                        # between the readings can only shrink it, never
                        # make it negative -- the raw movement, sign and
                        # all, rides the beat as rss_delta_mb
                        freed = max(0.0, round(before - after, 1))
                    delta = (None if before is None or after is None
                             else round(after - before, 1))
                    since_boot = (None if after is None or boot is None
                                  else round(after - boot, 1))
                    log.info("workers rss %s MB (boot %s, peak %s) trim returned %s MB arena=%s",
                             _mb(after), _mb(since_boot, signed=True), _mb(peak),
                             _mb(freed), _ARENA_STATUS)
                    status = "high" if after is not None and after >= high_mb else "ok"
                    detail = {"rss_mb": after, "boot_mb": boot, "peak_mb": peak,
                              "trim_freed_mb": freed, "rss_delta_mb": delta,
                              "jumps": jumps, "arena": _ARENA_STATUS}
                    if pending is not None and not pending.done():
                        log.warning("workers_memory heartbeat skipped: "
                                    "the previous one is still in flight")
                    else:
                        pending = asyncio.create_task(_beat(status, detail),
                                                      name="memory.beat")
            except Exception:  # noqa: BLE001 -- the instrument outlives its own bugs
                log.exception("workers memory watch cycle failed; next sample in %ss",
                              sample_s)
            await asyncio.sleep(sample_s)
    finally:
        trim_pool.shutdown(wait=False)
        if pending is not None and not pending.done():
            pending.cancel()


# THE BOOT STAMPEDE (outage review 2026-09-05): gather() started every
# loop in the same instant, so the loops' opening database reads hit
# this process's ten-connection pool together on every boot. get_pool is
# single-flight now (db.py) -- one pool built once, not one per caller --
# but the reads behind it still queue on it. The API's own /healthz read
# its pool at size == max, idle == 0 at 01:06Z with nothing of note in
# flight; this process, with more loops on the same pool size, is
# inferred to do the same at every boot. Each loop's FIRST start is
# offset by its LOOPS index times this, so the poller (index 0) starts
# at once and the tail of the list about twelve seconds later.
BOOT_STAGGER_S = 0.75

LOOPS: list[tuple[str, Callable[[], Awaitable[None]]]] = [
    ("poller", poller.main),
    ("chain_listener", chain_listener.main),
    ("metadata", metadata_refresher.main),
    ("analytics", analytics.main),
    ("dispatcher", dispatcher.main),
    ("roster", roster.main),
    ("reconciler", reconciler.main),
    ("copy_sweep", copy_sweep.main),
    ("underdog", underdog.main),
    ("premap", premap.main),
    # Exit detection from POSITIONS (2026-08-25). These whales close
    # by merging, not selling: 860k buys and 0 sells for swisstony,
    # while 62 of his 75 held positions sit below what he bought. No
    # trade listener can see that, so this diffs holdings and feeds
    # mirror_exit the fraction it measured.
    ("whale_exits", whale_exits.main),
    # MEASUREMENT ONLY: samples the ask after every attempted copy so the
    # post-fill price curve -- the thing that decides the fill rule --
    # exists as data instead of an argument. Never places or cancels.
    ("price_path", price_path.main),
    # THE ROSTER BY EVIDENCE (owner order 2026-09-01, evening): whales
    # enter, are promoted and are demoted by two numbers -- the edge
    # gate's verdict on HIS book and the proof cohort's interval on OUR
    # copies of him. Hourly; fails closed; every decision is written to
    # roster_decisions with the numbers that made it.
    ("roster_auto", roster_auto.main),
    # MEASUREMENT ONLY: marks every whale buy at the prices that split
    # his edge into selection and timing (owner question 2026-09-01).
    # Public CLOB reads, paced; never touches an order.
    ("edge_marks", edge_marks.main),
    # POSITION MIRRORING, PHASE P0 (owner order 2026-09-02): reads each
    # mirrored whale's net position per market from his fills, computes
    # the target we would hold and the one order we would place, and
    # writes it to mirror_shadow. NO ORDERS. Thirty games of shadow gate
    # phase P1 (long-only live) by the numbers.
    ("mirror_shadow", mirror_shadow.main),
    # MONEY. POSITION MIRRORING, PHASE P1 (owner order 2026-09-02, "go for
    # it, let's get this working"): the live reconciler behind
    # mirror_shadow's plan -- long-only, post-only rests at his level,
    # one standing live_orders row per book, every existing breaker
    # honoured. Registered whatever PMUS_MIRROR says: with the flag off
    # (the default) this is a CANCEL-ONLY loop that places nothing and
    # reconciles whatever a previous process left resting, so a deploy
    # that drops the flag cannot orphan a bid (spec section 3.7). The
    # DB switch 'mirror_live' must read true before it increases.
    ("mirror_live", mirror_live.main),
    # RETENTION (2026-09-05, the full-disk outage): the paper trader's
    # two measurement tables, ai_trades and copy_probes, filled the
    # 15 GB disk and took the database's hostname with it for ~15
    # hours. Hourly, bounded batches, a per-cycle cap, windows derived
    # from what /api/ai-trader and /api/copy-report can ask for; touches
    # exactly those two tables and nothing else. RETENTION=off stops it.
    ("retention", retention.main),
    # THE MEMORY WATCH (2026-09-05, the thirteen OOM kills): samples
    # VmRSS, names the second RSS jumps, trims on a timer and beats
    # 'workers_memory'. LAST, so the poller stays index 0 and every
    # existing loop's boot delay is unchanged; its first reading -- the
    # 'boot' figure on its lines -- is taken about thirteen seconds into
    # the process, after the other seventeen have started.
    ("memory", memory_watch),
]


async def supervise(name: str, factory: Callable[[], Awaitable[None]], *,
                    boot_delay: float = 0.0) -> None:
    # The stagger belongs before the FIRST start and nowhere else
    # (2026-09-05): a restart is one loop alone, its neighbours long
    # since spread out, so re-applying boot_delay there would only hold
    # a crashed loop -- the poller's Path B included -- out of service
    # for nothing. RESTART_DELAY_SECONDS is the restart's whole wait.
    if boot_delay > 0:
        await asyncio.sleep(boot_delay)
    while True:
        try:
            log.info("starting loop: %s", name)
            await factory()
            log.warning("loop %s exited cleanly; restarting in %ss", name, RESTART_DELAY_SECONDS)
        except Exception:  # noqa: BLE001
            log.exception("loop %s crashed; restarting in %ss", name, RESTART_DELAY_SECONDS)
        await asyncio.sleep(RESTART_DELAY_SECONDS)


async def _record_boot() -> None:
    """Workers-side /healthz (2026-08-24): three probes in a row read a
    silent premap sweep and the diagnosis stalled on 'is the worker even
    running the new code?'. The API answers that with /healthz commit;
    the workers now answer it with this state row, written at every
    boot and surfaced by the probe."""
    import json
    import os
    from datetime import datetime, timezone

    from ..db import get_pool

    try:
        pool = await get_pool()
        await pool.execute(
            "INSERT INTO ingestion_state (key, value) "
            "VALUES ('workers_boot', $1::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=$1::jsonb",
            json.dumps({
                "commit": (os.environ.get("RENDER_GIT_COMMIT") or "?")[:7],
                "at": datetime.now(tz=timezone.utc)
                .isoformat(timespec="seconds")}))
    except Exception:  # noqa: BLE001 — the marker must not block boot
        log.exception("workers_boot marker write failed")


async def main() -> None:
    # The marker runs CONCURRENTLY with the loops (leak-hunt find
    # 2026-08-24): awaiting it first serialized a DB connect retry in
    # front of every worker — a slow DB would have delayed the chain
    # listener itself for a status row. The marker is not staggered
    # either: it is the one write the probe waits on to learn which
    # commit is booting.
    await asyncio.gather(_record_boot(),
                         *(supervise(name, fn, boot_delay=i * BOOT_STAGGER_S)
                           for i, (name, fn) in enumerate(LOOPS)))


if __name__ == "__main__":
    asyncio.run(main())
