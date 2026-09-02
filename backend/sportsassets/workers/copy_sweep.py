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
import datetime as _dt
import re as _re
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



def _game_date(r) -> str:
    """The slug's game date, or "0000-00-00" when it carries none."""
    m = _re.search(r"\d{4}-\d{2}-\d{2}",
                   (r["market_slug"] or r["event_slug"] or ""))
    return m.group(0) if m else "0000-00-00"


def sweep_sort_key(r) -> tuple:
    """Live games first, then undated, then already-played.

    MODULE LEVEL SO IT CAN BE TESTED (2026-08-31). This was nested
    inside sweep_once, and the tests written for it rebuilt their own
    copy of the logic — so they agreed with themselves and passed
    cleanly against a deliberately broken production sort. A key that
    decides which trades get attempted is not something to verify by
    re-implementing it in the test.

    THREE RANKS, NOT TWO. An undated row carries "0000-00-00", which is
    lexically below every real date, so a two-rank key floats undated
    rows above tonight's games — trading one starvation for another.
    Today's pool holds no undated rows, which is exactly why this is
    worth getting right now rather than after it appears.

    The date bound in the query already drops games older than
    yesterday; this orders whatever survives so a played game can never
    outrank one still to come, which is what the sweep's own comment
    has always claimed it does.
    """
    d = _game_date(r)
    if d == "0000-00-00":
        return (1, d)
    return (2, d) if d < _dt.date.today().isoformat() else (0, d)

async def sweep_once() -> dict:
    pool = await get_pool()
    # Phantom 'submitting' rows (process died mid-order) hold the
    # one-fill-per-asset claim forever and silently retire the asset
    # from copying (audit 2026-08-21) — reap them every pass.
    from ..live_executor import (_reap_stale_exiting,
                                 _reap_stale_resting_bids,
    _reap_stale_submitting)
    await _reap_stale_submitting(pool)
    # VENUE-SIDE NET for the rest lane: a resting bid the ledger never
    # saw is still cancelled. Cheap (one list call), best-effort.
    try:
        await _reap_stale_resting_bids(pool)
    except Exception:  # noqa: BLE001 — the sweep must not die on it
        pass
    # 'exiting' had no reaper at all. A cancellation between
    # mirror_exit's atomic claim and its terminal UPDATE strands the row
    # there permanently, and this sweep is itself the canceller (the
    # 60s wait_for below), so it is the right place to clean up after.
    _reaped_exiting = await _reap_stale_exiting(pool)
    # THE MIRROR HAND-OFF'S BELT (mirror P1 step 7, spec 3.1; owner
    # order 2026-09-02, "go for it, let's get this working"). A whale in
    # mirror mode is refused by maybe_execute's own gate on every row
    # this pass hands it, so selecting his candidates here would only
    # spend the pass's bounded slots (MAX_ROWS_PER_SWEEP, a second per
    # row) on refusals and ration the other whales' reclaim. He is
    # subtracted from the roster the query reads, never from the roster
    # itself: the gate, not this list, is what stops the dollar. With
    # PMUS_MIRROR unset mirror_mode is False for everyone and the list
    # is what it always was. Imported at the pass like the reapers
    # above, so the belt reads the same predicate the gate reads at the
    # moment it runs.
    from ..live_executor import mirror_mode
    _roster = settings().source_whales()
    whales = sorted(_roster - {w for w in _roster if mirror_mode(w)})
    rows = await pool.fetch(
        r"""
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
               -- ONLY event_title is sourced from markets. The other
               -- three COALESCEs were wrong to add and are reverted:
               -- market_title feeds match_side's yes/no question
               -- agreement and its line extraction, and market_slug is
               -- the global_slug that date_of and slug_lines read — so
               -- swapping their source silently changes SIDE and GAME
               -- selection on any row where the two disagree. That is
               -- not the coverage-only change I labelled it.
               t.market_title,
               m.event_title,
               t.market_slug,
               t.event_slug,
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
          -- ...AND NOT ALREADY PLAYED (2026-08-31, run 33426256819).
          -- This bound existed only from ABOVE, so a game whose date
          -- has passed stayed a candidate for the whole 7-day trade
          -- window, and rejected rows are deliberately retryable and
          -- never stop being candidates. Measured on the first pass
          -- where the queue was observable:
          --     candidates=10938 processed=150 deferred=10788
          --     pool={past:10293, today_tomorrow:645}
          --     head={past:150,  today_tomorrow:0}
          -- Not "mostly past" — every slot, every two minutes, spent
          -- on finished games while the 645 candidates for today and
          -- tomorrow were never reached once.
          --
          -- current_date - 1, not current_date: the slug's date is the
          -- LOCAL game date and this compares in UTC, so a late game
          -- reads a day behind and can still be live after midnight
          -- UTC. One day of slack keeps a small tail of stale rows and
          -- cannot drop a game that is still playable.
          AND (substring(COALESCE(t.market_slug, t.event_slug, '')
                         from '\d{4}-\d{2}-\d{2}') IS NULL
               OR (substring(COALESCE(t.market_slug, t.event_slug, '')
                             from '\d{4}-\d{2}-\d{2}')::date
                   BETWEEN current_date - 1 AND current_date + 1))
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
                            AND (lo.status IN ('submitting','filled',
                                               'settled','cashed_out',
                                               'exiting')
                                 -- A NAMED row (round seven) stands for
                                 -- shares the account holds that the
                                 -- ledger cannot name: it blocks like a
                                 -- fill until the reaper resolves it.
                                 OR (lo.status = 'error' AND
                                     (lo.error LIKE 'venue holds a POSITION%'
                                      OR lo.error LIKE 'ORPHAN FILL RECORDED%'
                                      OR lo.error LIKE 'venue has no record of order%')))
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
                            AND (lo2.status NOT IN
                                     ('rejected', 'unfilled', 'error')
                                 OR lo2.error LIKE 'venue holds a POSITION%'
                                 OR lo2.error LIKE 'ORPHAN FILL RECORDED%'
                                 OR lo2.error LIKE 'venue has no record of order%'
                                 -- an add leg merged into its standing
                                 -- row (migration 045) is money spent,
                                 -- not a retryable miss
                                 OR lo2.status = 'merged'))
        ORDER BY t.asset, t.ts DESC
        """,
        whales, PRICE_CEILING,
    )
    # Nearest game first: the day's copy budget goes to positions that
    # settle (and free their capital) soonest.
    rows = sorted(rows, key=sweep_sort_key)
    # WHAT THE QUEUE IS MADE OF, BEFORE THE CAP TAKES A SLICE OF IT
    # (2026-08-31). Two separate things hid the backlog:
    #
    #   * `candidates` was computed AFTER the truncation below, so it
    #     could never exceed MAX_ROWS_PER_SWEEP. However deep the pool
    #     got, the heartbeat reported a bounded, healthy-looking 150.
    #     `deferred_to_next_pass` was the honest number, and nothing
    #     has ever printed it.
    #
    #   * the sort is ASCENDING on a date string that falls back to
    #     "0000-00-00" when the slug carries no date, and the WHERE
    #     clause bounds the date only from ABOVE (<= tomorrow). So
    #     undated rows sort first, then the OLDEST games — including
    #     ones already played — while the comment above says "nearest
    #     game first ... settle soonest". Rejected rows are
    #     deliberately retryable and never stop being candidates, so a
    #     row that cannot map can hold a slot on every pass forever.
    #
    # Whether that starves today's flow is a question about numbers,
    # so this counts it instead of arguing it.
    _total_candidates = len(rows)
    _today = _dt.date.today()
    _tomorrow = _today + _dt.timedelta(days=1)

    def _bucket(seq):
        u = past = cur = fut = 0
        for _r in seq:
            d = _game_date(_r)
            try:
                gd = _dt.date.fromisoformat(d) if d != "0000-00-00" else None
            except ValueError:
                gd = None
            if gd is None:
                u += 1
            elif gd < _today:
                past += 1
            elif gd <= _tomorrow:
                cur += 1
            else:
                fut += 1
        return {"undated": u, "past": past, "today_tomorrow": cur,
                "future": fut}

    _pool_mix = _bucket(rows)
    deferred = max(0, len(rows) - MAX_ROWS_PER_SWEEP)
    rows = rows[:MAX_ROWS_PER_SWEEP]
    _head_mix = _bucket(rows)
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
                                 exit_census, exit_census_lines,
                                 copy_census_snapshot)

    # EXIT CENSUS rides this heartbeat for the same reason queue wait
    # does: every worker loop — poller, copy_sweep, whale_exits — runs
    # in ONE process (workers/all.py), so the counters are complete
    # here and would be near-empty anywhere else. An admin endpoint
    # reading the module global from the API process would report zeros
    # forever and read as "the exit path never ran", which is precisely
    # the wrong conclusion this census exists to prevent.
    _cen = exit_census()

    _n = _QUEUE_STATS["n"] or 0
    return {"candidates": _total_candidates, "attempted": attempted,
            "processed": len(rows),
            "deferred_to_next_pass": deferred,
            # The pool's composition beside the slice the cap let
            # through. If the head is undated/past while the pool holds
            # today's games, the cap is not rationing — it is starving
            # the flow that still has edge.
            "pool_mix": _pool_mix, "head_mix": _head_mix,
            # Always present, never conditionally added: an absent key
            # and a zero key look identical to a reader, and this
            # codebase has shipped that confusion before.
            "reaped_exiting": _reaped_exiting,
            "exit_census": _cen["counts"],
            # WHERE THE COPIES GO BEFORE ANY ROW EXISTS (2026-08-31).
            # maybe_execute has 22 returns ahead of its first
            # INSERT INTO live_orders, and until now exactly one of
            # them recorded anything — into _GATE_CENSUS, which
            # nothing read. Every funnel number this system quotes is
            # computed FROM live_orders, so a copy refused before the
            # row exists has been invisible by construction. rn1 puts
            # up 1,061 playable positions a day and we place 54; this
            # is the only counter that can say where the rest went.
            "copy_census": copy_census_snapshot(),
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
