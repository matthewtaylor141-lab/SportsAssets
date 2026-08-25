"""Worker: copy the source whales' OPEN positions, not just fresh fills.

Owner instruction 2026-08-03: "any open trade is copied and actually traded
on our account." The live executor's freshness gate (<5 min) only covers
detections made while everything is healthy; the six-day ingestion outage
and today's catch-up mean both whales are sitting on open positions that
were never candidates. This sweep closes that gap and then keeps closing
it on a slow clock.

Mechanics and why they're safe:
- Candidates: each whale's most recent BUY per outcome token in the last
  7 days, at any price (owner directive: every trade, one contract), in a
  market not known to be resolved and not already actually ordered.
- Execution goes through maybe_execute — the SAME caps as fresh copies
  (one contract, per-fill ceiling, $100/day, kill switch, dedupe). The
  sweep adds no new authority; it only widens the candidate stream.
- Staleness protection is the ORDER TYPE, not a heuristic: a FOK limit at
  his price + slippage fills only where the market still offers roughly
  his entry. A market that already moved away simply kills — we never pay
  the post-move price.
- Idempotent per asset, so the periodic re-run only picks up whales'
  NEW open positions that fresh-detection copying somehow missed.
"""

import asyncio
import logging
import os

from ..config import settings
from ..db import get_pool, heartbeat
from ..live_executor import maybe_execute

log = logging.getLogger(__name__)

BOOT_DELAY_S = 120       # let the poller/executor settle before sweeping
# Hourly (owner directive 2026-08-06: "make sure we aren't missing any
# trades"). The sweep is the recovery net for everything the fresh path
# drops — metadata lag, transient venue errors, deferred far-dated games
# — and at 6h a missed fresh detection sat unrecovered for most of a
# slate. The query is cheap and execution re-runs the same caps as fresh
# copies, so the only cost of running it hourly is a few venue reads.
# 10 minutes, not the original hour (owner 2026-08-09: Kalshi holds
# first claim on ALL listed-sport flow now, so this sweep is the PMUS
# reclaim leg for everything Kalshi can't list or price — an hourly
# reclaim would hand PMUS only stale, decayed copies).
# 2 minutes (owner directive 2026-08-10 night: "both firing correct
# trades and copied edges immediately"): with the flow split 50/50 and
# Kalshi deciding within ~30-60s, this reclaim is the only remaining
# slow link — at 10 minutes a position the first venue refused sat
# unpriced for most of its edge decay. Each pass stays bounded
# (COPY_SWEEP_MAX_ROWS + per-row timeout), so the tighter timer costs
# repeat reads only when there is actually a backlog to drain.
SWEEP_EVERY_S = float(os.environ.get("COPY_SWEEP_EVERY_S", "120"))
PRICE_CEILING = 0.99     # mirrors the per-fill ceiling; cheap pre-filter
# BOUNDED PASSES (2026-08-07): an unbounded pass over RN1's re-eligible
# backlog ran hours at ~1-3s/row, so every redeploy killed it before its
# completion heartbeat — copy_sweep read frozen at its boot time all day
# while the loop was actually working. Nearest games go first, the
# remainder is DISCLOSED (deferred_to_next_pass) and drains on the next
# hourly pass; a per-row timeout stops one hung venue call from wedging
# the whole loop.
MAX_ROWS_PER_SWEEP = int(os.environ.get("COPY_SWEEP_MAX_ROWS", "150"))
ROW_TIMEOUT_S = 60.0


async def sweep_once() -> dict:
    pool = await get_pool()
    # Phantom 'submitting' rows (process died mid-order) hold the
    # one-fill-per-asset claim forever and silently retire the asset
    # from copying (audit 2026-08-21) — reap them every pass.
    from ..live_executor import _reap_stale_submitting
    await _reap_stale_submitting(pool)
    whales = sorted(settings().source_whales())
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (t.asset)
               t.id, t.whale_id, w.username AS whale_username, t.tx_hash, t.asset,
               t.condition_id, t.side, t.outcome, t.outcome_index,
               t.size::float8 AS size, t.price::float8 AS price,
               t.notional::float8 AS notional,
               -- EVENT_TITLE WAS NEVER SELECTED, SO A WHOLE KEY LANE
               -- WAS DEAD. premap.resolve builds its keys from three
               -- sources — market_title, event_title and the slug —
               -- and `trades` has no event_title column, so the sweep
               -- payload never carried one and one third of the key
               -- construction produced nothing on every copy.
               -- The markets join is already here for the resolved
               -- filter, so this costs no extra query.
               COALESCE(t.market_title, m.title) AS market_title,
               m.event_title,
               COALESCE(t.market_slug, m.slug) AS market_slug,
               COALESCE(t.event_slug, m.event_slug) AS event_slug,
               t.sport,
               extract(epoch FROM t.ts)::float8 AS ts_epoch
        FROM trades t
        JOIN whales w ON w.id = t.whale_id
        LEFT JOIN markets m ON m.condition_id = t.condition_id
        WHERE t.side = 'BUY'
          AND t.price <= $2
          AND t.ts > now() - interval '7 days'
          AND lower(w.username) = ANY($1)
          AND COALESCE(m.resolved, false) = false
          -- CAPITAL TURNOVER (owner, 2026-08-04): with a small bankroll,
          -- money locked in Wednesday's game is money not compounding
          -- today. Only games dated today/tomorrow (slug-embedded date)
          -- are candidates NOW; farther games are DEFERRED, not skipped —
          -- each 6h sweep re-evaluates, so they place once inside the
          -- window. Undated slugs pass (can't defer what can't be dated).
          AND (substring(COALESCE(t.market_slug, t.event_slug, '')
                         from '\d{4}-\d{2}-\d{2}') IS NULL
               OR substring(COALESCE(t.market_slug, t.event_slug, '')
                            from '\d{4}-\d{2}-\d{2}')::date
                  <= current_date + 1)
          -- An asset is off the table once an order was actually PLACED
          -- for it (filled/unfilled/submitting/error). Mapping rejections
          -- are retryable: a mapper fix must be able to revisit the same
          -- open positions, so only this trade-id's own audit row blocks
          -- (maybe_execute's ON CONFLICT would no-op it silently).
          -- 'cashed_out' and 'exiting' BELONG HERE (2026-08-25). The
          -- list decides what counts as "already taken". Without those
          -- two, every position mirror_exit closes reads as untaken on
          -- the next pass and the sweep BUYS IT BACK — at whatever the
          -- market has moved to, undoing the exit we just followed him
          -- out of, and doing it hourly.
          --
          -- It bites specifically because the sweep picks the NEWEST
          -- trade per asset (DISTINCT ON ... ORDER BY t.ts DESC) while
          -- maybe_execute's never-add check returns BEFORE inserting a
          -- row for a duplicate buy — so the trade it re-candidates is
          -- precisely the one with no audit row of its own to block it.
          --
          -- This is the stale-sweep re-entry only. A genuinely FRESH
          -- buy after his exit is his re-entry, which the owner wants
          -- copied ("then he re-enters at $60"), and that path is
          -- untouched.
          AND NOT EXISTS (SELECT 1 FROM live_orders lo
                          WHERE lo.asset = t.asset
                            AND lo.status IN ('submitting','filled',
                                              'settled','cashed_out',
                                              'exiting')
                            -- Manual-desk rows are invisible to the
                            -- autonomous paths (owner 2026-08-07).
                            AND COALESCE(lo.whale_username, '') <> 'manual')
          -- Cross-venue one-copy rule: positions the engine already
          -- copied on Kalshi (kalshi_claims) are taken. Without this
          -- the sweep double-bought any position PM missed fresh but
          -- Kalshi copied (latent since the Kalshi leg shipped; hourly
          -- cadence made it 6x more likely).
          AND NOT EXISTS (SELECT 1 FROM kalshi_claims kc
                          WHERE kc.asset = t.asset)
          -- Rejected rows do NOT block: a mapping rejection is retryable
          -- by design, and every mapper improvement re-runs against the
          -- backlog on the next sweep instead of applying only to future
          -- trades (8,896 candidates were permanently dead here before
          -- 2026-08-04). Unfilled and errored rows are retryable too
          -- (owner 2026-08-08: "some trades failed being copied — make
          -- sure none were missed"): a FOK that missed once is not a
          -- verdict, and an exception (venue 5xx, drained balance) is
          -- not an attempt at all. The order TYPE is the guard — each
          -- hourly retry is a fresh FOK at his+2% that only fills if
          -- the book is genuinely back in tolerance. Only rows that
          -- PLACED (submitting/filled/settled) block.
          AND NOT EXISTS (SELECT 1 FROM live_orders lo2
                          WHERE lo2.trade_id = t.id
                            AND lo2.status NOT IN
                                ('rejected', 'unfilled', 'error'))
        ORDER BY t.asset, t.ts DESC
        """,
        whales, PRICE_CEILING,
    )
    # Nearest game first: the day's copy budget goes to positions that
    # settle (and free their capital) soonest.
    def _game_date(r):
        import re as _re
        m = _re.search(r"\d{4}-\d{2}-\d{2}",
                       (r["market_slug"] or r["event_slug"] or ""))
        return m.group(0) if m else "0000-00-00"

    rows = sorted(rows, key=_game_date)
    deferred = max(0, len(rows) - MAX_ROWS_PER_SWEEP)
    rows = rows[:MAX_ROWS_PER_SWEEP]
    attempted = 0
    for r in rows:
        payload = {
            "id": r["id"],
            "whale_id": r["whale_id"],
            "whale_username": r["whale_username"],
            "asset": r["asset"],
            "condition_id": r["condition_id"],
            "side": r["side"],
            "outcome": r["outcome"],
            "outcome_index": r["outcome_index"],
            "size": r["size"],
            "price": r["price"],
            "notional": r["notional"],
            "market_title": r["market_title"],
            "event_title": r["event_title"],
            "market_slug": r["market_slug"],
            "event_slug": r["event_slug"],
            "sport": r["sport"],
            "ts_epoch": r["ts_epoch"],
            # The sweep IS the reclaim leg of the venue split — its rows
            # must never re-defer to Kalshi (live_executor checks this).
            "sweep_recovery": True,
        }
        try:
            await asyncio.wait_for(maybe_execute(payload, None),
                                   timeout=ROW_TIMEOUT_S)
            attempted += 1
        except asyncio.TimeoutError:
            log.warning("sweep copy timed out for trade %s", r["id"])
        except Exception:  # noqa: BLE001 — one bad market must not stop the sweep
            log.exception("sweep copy failed for trade %s", r["id"])
        await asyncio.sleep(1.0)   # gentle on the venue API
    # The failed-copy backlog, disclosed: how many recent copy rows sit
    # in a retryable state right now (the sweep above re-attempts their
    # trades whenever they re-qualify as candidates).
    failed = await pool.fetch(
        "SELECT status, count(*) AS n FROM live_orders "
        "WHERE placed_at > now() - interval '48 hours' "
        "  AND status IN ('unfilled', 'error', 'rejected') "
        "  AND COALESCE(whale_username, '') NOT IN ('manual', 'underdog') "
        "GROUP BY status")
    # QUEUE WAIT rides the sweep's heartbeat rather than getting a
    # worker of its own. It is the copy path's own latency — time a
    # detected trade spent waiting for a semaphore slot — and it was
    # invisible, so a copy rejected as "stale-signal" could not be told
    # apart from one that arrived late. It counts against the staleness
    # cap either way, which makes it the first number to look at before
    # touching any timeout.
    from ..live_executor import (_COPY_CONCURRENCY, _QUEUE_STATS,
                                 exit_census, exit_census_lines)

    # EXIT CENSUS rides this heartbeat for the same reason queue wait
    # does: every worker loop — poller, copy_sweep, whale_exits — runs
    # in ONE process (workers/all.py), so the counters are complete
    # here and would be near-empty anywhere else. An admin endpoint
    # reading the module global from the API process would report zeros
    # forever and read as "the exit path never ran", which is precisely
    # the wrong conclusion this census exists to prevent.
    _cen = exit_census()

    _n = _QUEUE_STATS["n"] or 0
    return {"candidates": len(rows), "attempted": attempted,
            "deferred_to_next_pass": deferred,
            "exit_census": _cen["counts"],
            "exit_recent": exit_census_lines(),
            "copy_queue": {
                "n": _n, "concurrency": _COPY_CONCURRENCY,
                "avg_wait_s": (round(_QUEUE_STATS["total_s"] / _n, 3)
                               if _n else 0.0),
                "max_wait_s": round(_QUEUE_STATS["max_s"], 3)},
            "retryable_48h": {r["status"]: r["n"] for r in failed}}


async def main() -> None:
    await asyncio.sleep(BOOT_DELAY_S)
    while True:
        try:
            result = await sweep_once()
            log.info("copy sweep: %s", result)
            await heartbeat("copy_sweep", "ok", result)
        except Exception as exc:  # noqa: BLE001
            log.exception("copy sweep failed")
            await heartbeat("copy_sweep", "error", {"error": str(exc)})
        await asyncio.sleep(SWEEP_EVERY_S)


if __name__ == "__main__":
    asyncio.run(main())
