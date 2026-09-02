"""Sample the ask at fixed offsets after every attempted copy.

MEASUREMENT ONLY. This worker never places, cancels, or touches an
order. It reads live_orders for fresh attempts by rostered whales, and
for each one reads the ask on OUR side of the market at t = 30, 60, 120,
281 and 600 seconds after placed_at, writing one price_path row per
offset. t = 0 is recorded by the copy path itself from the pre-trade
quote it already reads (live_executor.maybe_execute), because by the
time this worker could see the row its intent is 2-12 s old and a late
baseline biases every delta toward flat. analytics/price_path.py turns
the samples into the curve that decides the fill rule.

Keyed off live_orders rather than raw trades because the attempt row
carries the US market slug and the intent the ask must be read on; a
raw trade carries a token id and neither. That also restricts sampling
to exactly the population whose fill rule is being chosen.

A SAMPLE IS A READING AT t, NOT THE FIRST READING AFTER t (round three,
2026-09-01). The first version took every overdue offset whenever it
could, so a worker restart, a stalled tick or a venue outage collapsed
six offsets into one instant -- six equal asks labelled 0..600 s, zero
deltas, a flat curve by fiat, which is exactly what this instrument
disclaims. An offset not read inside its window is not read at all.

PACED (same round). The sampler shares the gateway, the httpx client and
the process with the copy path's fail-closed pre-trade quote; the venue
429'd a board walk above ~3 req/s on 2026-08-23. Reads are paced below
that, capped per tick, and a run of misses abandons the tick and backs
off instead of retrying into the limit.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..analytics.price_path import OFFSETS_S
from ..db import get_pool
from ..live_executor import ORDER_INTENT_SQL, exitable_whales
from ..venue_pace import pace

log = logging.getLogger(__name__)

POLL_S = 5.0
# How far back a fresh attempt may be to still get its remaining
# offsets: past the last offset plus a margin there is nothing to take.
LOOKBACK_S = max(OFFSETS_S) + 120
# The window inside which a reading may still be labelled with its
# nominal offset. Two ticks: a single missed tick is recoverable, a gap
# is not.
TOL_S = 2 * POLL_S
# The offsets THIS worker takes; t = 0 belongs to the copy path.
WORKER_OFFSETS = tuple(t for t in OFFSETS_S if t > 0)
# Venue pacing. LIST_PACING_S in premap.py (0.35 s) is the repo's own
# record of what this venue tolerates.
READ_PACING_S = 0.35
MAX_READS_PER_TICK = 20
MISS_STREAK_ABANDON = 3
BACKOFF_S = 60.0
_backoff_until = 0.0
_sleep = asyncio.sleep      # indirection so a test can count the pacing


async def _due_samples(pool, now_ts: float) -> list[dict]:
    """(row, offset) pairs inside their window and not yet taken."""
    whales = sorted(exitable_whales())
    if not whales:
        return []
    rows = await pool.fetch(
        f"""
        SELECT lo.id AS row_id, lo.us_market_slug AS slug,
               extract(epoch FROM lo.placed_at)::float8 AS placed_ts,
               {ORDER_INTENT_SQL} AS intent,
               COALESCE(array_agg(pp.t_s) FILTER (WHERE pp.t_s IS NOT NULL),
                        '{{}}') AS taken
          FROM live_orders lo
          LEFT JOIN price_path pp ON pp.row_id = lo.id
         WHERE lo.placed_at >= now() - make_interval(secs => $1)
           AND lo.us_market_slug IS NOT NULL
           AND lower(COALESCE(lo.whale_username,'')) = ANY($2::text[])
           AND {ORDER_INTENT_SQL} IN ('ORDER_INTENT_BUY_LONG',
                                      'ORDER_INTENT_BUY_SHORT')
         GROUP BY lo.id, lo.us_market_slug, lo.placed_at, lo.raw
        """, float(LOOKBACK_S), whales)
    due: list[dict] = []
    for r in rows:
        taken = set(int(t) for t in (r["taken"] or []))
        placed = float(r["placed_ts"])
        for t in WORKER_OFFSETS:
            if t in taken:
                continue
            if placed + t <= now_ts <= placed + t + TOL_S:
                due.append({"row_id": int(r["row_id"]), "slug": r["slug"],
                            "intent": r["intent"], "t_s": t,
                            "deadline": placed + t + TOL_S})
    return due


def _paced_ask(pmus, slug: str, intent: str | None) -> float | None:
    """One ask read behind the PROCESS-WIDE measurement pacer
    (position-mirroring review round two): this worker and the mirror
    shadow each paced their own reads at 0.35 s, and two such streams
    overlapping summed to ~5.7 req/s on the copy path's client -- above
    the rate the venue refused. One gate bounds the sum."""
    pace(READ_PACING_S)
    return pmus.side_ask(slug, intent)


async def _take(pool, pmus, d: dict) -> bool:
    """Read one ask and persist it. False on a venue miss (row skipped)."""
    try:
        ask = await asyncio.to_thread(_paced_ask, pmus, d["slug"], d["intent"])
    except Exception as exc:  # noqa: BLE001 — skip this offset only
        log.debug("price_path: ask unreadable for %s t=%s (%s)",
                  d["slug"], d["t_s"], type(exc).__name__)
        ask = None
    if ask is None:
        return False
    try:
        await pool.execute(
            "INSERT INTO price_path (row_id, t_s, ask) VALUES ($1, $2, $3) "
            "ON CONFLICT (row_id, t_s) DO NOTHING",
            d["row_id"], int(d["t_s"]), float(ask))
        return True
    except Exception as exc:  # noqa: BLE001 — missing table until 042
        log.debug("price_path: insert failed (%s)", type(exc).__name__)
        return False


async def sample_once(pool, pmus, now_ts: float | None = None) -> int:
    """One pass: take every due sample, paced. Returns how many were written."""
    global _backoff_until
    wall = now_ts is None
    now_ts = time.time() if wall else now_ts
    if now_ts < _backoff_until:
        return 0
    try:
        due = await _due_samples(pool, now_ts)
    except Exception as exc:  # noqa: BLE001 — table absent, etc.
        log.debug("price_path: due query failed (%s)", type(exc).__name__)
        return 0
    n = 0
    misses = 0
    for i, d in enumerate(due[:MAX_READS_PER_TICK]):
        if i:
            await _sleep(READ_PACING_S)
        # THE WINDOW IS RE-CHECKED AT THE READ, NOT AT THE TICK. Twenty
        # paced reads take 7-15 s; a sample whose window closed while it
        # waited its turn is not labelled with an offset it missed.
        if wall and time.time() > float(d.get("deadline") or float("inf")):
            continue
        if await _take(pool, pmus, d):
            n += 1
            misses = 0
            continue
        misses += 1
        if misses >= MISS_STREAK_ABANDON:
            # A RUN OF MISSES IS THE VENUE SAYING NO. The adapter turns a
            # 429 into None like any other miss, so the streak is the
            # signal; keep reading and the copy path's own quote is the
            # next thing the limit refuses.
            _backoff_until = now_ts + BACKOFF_S
            log.warning("price_path: %d consecutive venue misses — "
                        "abandoning the tick, backing off %ss",
                        misses, BACKOFF_S)
            break
    if len(due) > MAX_READS_PER_TICK:
        log.info("price_path: %d due, took %d (per-tick cap)",
                 len(due), MAX_READS_PER_TICK)
    return n


async def main() -> None:
    from .. import pmus

    pool = await get_pool()
    log.info("price_path sampler up: offsets=%s poll=%ss tol=%ss",
             WORKER_OFFSETS, POLL_S, TOL_S)
    while True:
        try:
            n = await sample_once(pool, pmus)
            if n:
                log.info("price_path: wrote %d samples", n)
        except Exception:  # noqa: BLE001 — a measurement worker never dies
            log.exception("price_path pass failed")
        await asyncio.sleep(POLL_S)
