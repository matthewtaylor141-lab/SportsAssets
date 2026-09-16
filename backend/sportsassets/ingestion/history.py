"""Deep trade-history backfill via /activity time-window cursor pagination.

Method ported from the audited edge-engine reference pipeline (July 2026 API
constraints, measured on a 5.35M-fill account):
  - /trades caps offset at ~10,500 and ignores time params — unusable for
    full history. /activity (type=TRADE) honors start/end unix-second
    filters with limit up to 500.
  - History is split into ~4-day windows; within each window we cursor-
    paginate newest→oldest (end = oldest seen; when a full page yields no
    fresh rows, end = oldest - 1). Boundary duplicates collapse on the DB
    dedupe key.

Operational constraints (unchanged):
  - Background task, never blocks live polling; single-flight lock.
  - Bulk page inserts; progress heartbeats; rows marked source='backfill'
    (no notifications, excluded from latency metrics).
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from ..ratelimit import polite_get
from .dedupe import key_fields_valid
from .poller import parse_data_api_trade

log = logging.getLogger(__name__)

PAGE_SIZE = 500
WINDOW_SECONDS = 4 * 86400

_INSERT = """
INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, outcome, outcome_index,
                    size, price, notional, market_title, market_slug, event_slug, sport,
                    ts, source, detected_at, enriched_at, dedupe_key)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'backfill',$16,$16,$17)
ON CONFLICT (dedupe_key) DO NOTHING
"""


async def _sport_map() -> dict[str, str]:
    pool = await get_pool()
    rows = await pool.fetch("SELECT condition_id, sport FROM markets WHERE sport <> 'unclassified'")
    return {r["condition_id"]: r["sport"] for r in rows}


def _history_start_epoch() -> int:
    raw = settings().history_start_date
    try:
        return int(datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        log.warning("bad HISTORY_START_DATE %r; defaulting to 2025-07-01", raw)
        return int(datetime(2025, 7, 1, tzinfo=timezone.utc).timestamp())


def _fill_key(ev) -> tuple:
    return (ev.tx_hash, ev.asset, ev.side, ev.price, ev.size, ev.ts_epoch)


async def _insert_page(pool, rows: list[tuple]) -> None:
    if not rows:
        return
    async with pool.acquire() as conn:
        try:
            await conn.executemany(_INSERT, rows)
        except Exception:  # noqa: BLE001 — round 25 (major): one row
            # the gate cannot pre-judge (a DB constraint drift) used
            # to kill the whole page's healthy fills inside this
            # uncontained batch, wedging the whale's import on the
            # retry loop. One row costs one row: fall back to
            # row-by-row, land what can land, skip what cannot.
            for row in rows:
                try:
                    await conn.execute(_INSERT, *row)
                except Exception:  # noqa: BLE001
                    log.warning("backfill: row cannot land, skipping "
                                "tx %s", str(row[1])[:16])


async def backfill_whale_history(http: httpx.AsyncClient, whale: dict) -> int:
    """Import the wallet's full history over time windows. Returns fills scanned."""
    pool = await get_pool()
    sports = await _sport_map()
    now_dt = datetime.now(tz=timezone.utc)
    max_trades = settings().history_max_trades
    start_epoch = _history_start_epoch()
    now_epoch = int(_time.time()) + 3600

    scanned = 0
    window_start = start_epoch
    while window_start < now_epoch and scanned < max_trades:
        window_end = min(window_start + WINDOW_SECONDS, now_epoch)
        end_cursor = window_end
        boundary: set[tuple] = set()
        while scanned < max_trades:
            resp = await polite_get(
                http, "/activity",
                params={"user": whale["address"], "type": "TRADE", "limit": PAGE_SIZE,
                        "start": window_start, "end": end_cursor},
            )
            resp.raise_for_status()
            batch = resp.json()
            if not isinstance(batch, list):
                # round 24 (major): an HTTP-200 error envelope (a
                # dict, a bare string) silently broke EVERY window,
                # scanned 0 fills — and line "SET history_backfilled=
                # TRUE" below then marked the whale's entire deep
                # history imported, permanently, behind an ok
                # heartbeat: a transient venue burp truncated months
                # of history with zero alarm and zero retry, while
                # the identical outage as HTTP 500 raised and stayed
                # pending. The venue error shape now raises like its
                # siblings (poller round 23, reconciler round 19), so
                # the per-whale handler leaves the whale PENDING and
                # the next cycle retries.
                raise ValueError(
                    "venue served a non-list /activity body: "
                    + type(batch).__name__)
            if not batch:
                break

            rows: list[tuple] = []
            evs = []
            bad = 0
            for raw in batch:
                # per-row containment (round 24, major: one JSON null
                # in a page raised at the parse, escaped the per-whale
                # except tuple, aborted the ENTIRE pass — every whale
                # after the poisoned one starved forever while the 60s
                # retry loop re-crashed identically. And the old
                # tx/size-only gate let a side-less stub through to
                # kill the whole executemany batch on the side CHECK
                # constraint). One junk row costs one row; the shared
                # validity gate guards the INSERT exactly as it does
                # in the poll and reconcile carriers.
                try:
                    ev = parse_data_api_trade(
                        raw, whale["id"], whale["username"])
                    if not key_fields_valid(ev):
                        bad += 1
                        continue
                except Exception:  # noqa: BLE001
                    bad += 1
                    continue
                evs.append(ev)
            if batch and bad == len(batch):
                # an all-junk page is venue degradation, not history
                # exhaustion — fail the whale, stay pending, retry
                raise ValueError(
                    f"venue served {bad} activity rows, none usable")
            # END-CONTRACT ENFORCEMENT (round 26, major): every cursor
            # inference below — the window math, the pinned-tie
            # boundary, the fresh==0 step-past — assumes the venue
            # honors `end` (rows ts <= end_cursor). Round 26 proved an
            # end-violating venue defeats the round-25 accumulate fix:
            # rows NEWER than oldest never enter the boundary, count
            # fresh every page, the cursor re-pins forever, and the
            # cap burns on re-serves behind a durable
            # history_backfilled=TRUE. Verify the premise directly
            # (the round-21 ordering-check philosophy): a served row
            # past the requested end fails the whale — pending, error
            # heartbeat, retried — never a silent wedge.
            if any(ev.ts_epoch > end_cursor for ev in evs):
                raise ValueError(
                    "venue served rows past the requested end cursor")
            fresh = 0
            oldest = None
            for ev in evs:
                oldest = ev.ts_epoch if oldest is None else min(oldest, ev.ts_epoch)
                if _fill_key(ev) in boundary:
                    continue
                fresh += 1
                rows.append((
                    ev.whale_id, ev.tx_hash, str(ev.asset), ev.condition_id, ev.side,
                    ev.outcome, ev.outcome_index, ev.size, ev.price, ev.notional,
                    ev.market_title, ev.market_slug, ev.event_slug,
                    sports.get(ev.condition_id or "", "unclassified"),
                    datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc), now_dt, ev.dedupe_key,
                ))
            await _insert_page(pool, rows)
            scanned += fresh
            await heartbeat(
                "backfill", "running",
                {"whale": whale["username"] or whale["address"], "scanned": scanned,
                 "window": datetime.fromtimestamp(window_start, tz=timezone.utc).date().isoformat()},
            )
            if len(batch) < PAGE_SIZE or oldest is None:
                break
            if fresh == 0:
                end_cursor = oldest - 1  # >500 fills in one second — step past it
                boundary = set()
            else:
                # from the already-parsed events — never a second
                # unguarded parse of the raw batch (round 24)
                new_boundary = {_fill_key(ev) for ev in evs
                                if ev.ts_epoch == oldest}
                if oldest == end_cursor:
                    # round 25 (major): the cursor is PINNED at a
                    # >PAGE_SIZE tie second and the venue's unstable
                    # tie order (the proven feed property from fleet
                    # rounds 17-18) re-serves a different subset each
                    # query. Replacing the boundary every page let
                    # re-served keys count as fresh forever: the walk
                    # spun at the tie second, burned the cap and
                    # `scanned` on duplicates, and the older history
                    # was never queried behind a durable
                    # history_backfilled=TRUE. ACCUMULATE at a pinned
                    # cursor so each key counts fresh at most once and
                    # fresh==0 eventually steps past the second.
                    boundary |= new_boundary
                else:
                    boundary = new_boundary
                end_cursor = oldest
            if end_cursor <= window_start:
                break
        window_start += WINDOW_SECONDS

    if scanned >= max_trades:
        # honest hint (round 25): the flag below is durable, so a
        # raised cap alone changes nothing for THIS wallet — the flag
        # must be cleared too for a re-import.
        log.warning("backfill for %s hit HISTORY_MAX_TRADES=%s — raise it AND clear "
                    "history_backfilled on this wallet for complete history",
                    whale["address"], max_trades)
        await heartbeat("backfill", "capped",
                        {"whale": whale["username"] or whale["address"], "scanned": scanned,
                         "hint": f"HISTORY_MAX_TRADES={max_trades} reached"})

    if scanned == 0:
        has_any = await pool.fetchval(
            "SELECT 1 FROM trades WHERE whale_id=$1 LIMIT 1", whale["id"])
        if has_any:
            # round 25 (minor): 200-[] on every window for a whale we
            # KNOW has fills is a cold venue index, not an empty
            # history — the round-7/round-24 class through the one
            # body shape round 24 did not cover. Defer: the whale
            # stays PENDING (per-whale handler), retried next cycle,
            # with an error heartbeat instead of a silent TRUE flag.
            # A genuinely tradeless wallet (no rows from any carrier)
            # still completes: [] is its valid answer.
            raise ValueError(
                "venue served an empty history for a whale with known fills")

    await pool.execute("UPDATE whales SET history_backfilled=TRUE WHERE id=$1", whale["id"])
    log.info("history backfill for %s: %s fills imported",
             whale["username"] or whale["address"], scanned)
    return scanned


_BACKFILL_LOCK = asyncio.Lock()


async def backfill_pending() -> int:
    """Backfill every active whale that hasn't had a history import yet.

    Single-flight: if a pass is already running in this process, additional
    callers return immediately instead of doubling API traffic.
    """
    if _BACKFILL_LOCK.locked():
        return 0
    async with _BACKFILL_LOCK:
        return await _backfill_pending_inner()


async def _backfill_pending_inner() -> int:
    pool = await get_pool()
    whales = await pool.fetch(
        "SELECT id, address, username FROM whales "
        "WHERE active AND NOT banned AND NOT history_backfilled"
    )
    if not whales:
        return 0
    total = 0
    failed = 0
    async with httpx.AsyncClient(base_url=settings().data_api_base, timeout=30) as http:
        for whale in whales:
            try:
                total += await backfill_whale_history(http, dict(whale))
            except Exception as exc:  # noqa: BLE001 — round 24: one
                # whale must never abort the pass. The old narrow
                # tuple (HTTPError, ValueError) let a parse
                # AttributeError or a DB constraint error escape,
                # killing the whole pass every 60s and starving every
                # whale AFTER the poisoned one forever. Any failure
                # leaves THIS whale pending for retry; the rest of
                # the roster still backfills. (CancelledError is a
                # BaseException and still propagates.)
                failed += 1
                log.warning("history backfill failed for %s: %s (will retry next cycle)",
                            whale["address"], exc)
                await heartbeat("backfill", "error", {"whale": whale["address"], "error": str(exc)})
    # round 28 (minor): this final beat unconditionally said 'ok' and,
    # since service_heartbeats holds ONE durable row per service, it
    # overwrote the per-whale error beats milliseconds after they
    # landed — a total-loss pass (venue down, every whale still
    # pending) ended green, byte-identical to a healthy pass over
    # tradeless wallets. The round-27 law, in its fifth home: a dead
    # carrier never wears a green heartbeat, and the failed count
    # rides the durable detail on every pass.
    status = "error" if whales and failed == len(whales) else "ok"
    await heartbeat("backfill", status,
                    {"scanned": total, "failed": failed})
    return total
