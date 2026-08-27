"""Shared trade pipeline.

Both detection paths call `ingest_trade`. The contract:

1. INSERT with ON CONFLICT (dedupe_key) DO UPDATE, filling NULL metadata
   columns only. `(xmax = 0) AS was_insert` is the dedupe gate.
2. If (and only if) the row was NEWLY INSERTED: publish `trades.new`
   immediately and write notification outbox rows. Fan-out fires on the
   PROVISIONAL record — enrichment must never delay notification.
3. Enrich asynchronously from the hot metadata cache; publish `trades.enriched`.

Replaying any event is therefore safe end-to-end: a conflicting insert
updates nothing but holes and returns was_insert=false, so no duplicate
publish, no duplicate outbox rows, and no duplicate copy order.

This was DO NOTHING until 2026-08-25, which meant the second pass — the
one whose entire job is to supply the condition_id the chain leg could
not know — had its result discarded on every run. That is the NOSLUG
bucket: 22,330 rejected copy rows, 22,327 of them with no token in
market_tokens at all, ~971 permanently dead copy attempts a day. A
trade with a NULL condition_id can never be mapped, never have its
sibling leg resolved, and never be classified as an entry or an exit.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from .. import gamma
from ..bus import CH_TRADES_ENRICHED, CH_TRADES_NEW, publish
from ..db import get_pool
from .dedupe import make_dedupe_key

log = logging.getLogger(__name__)


@dataclass
class TradeEvent:
    """A detected fill, provisional or enriched."""

    whale_id: int
    whale_username: str | None
    tx_hash: str
    asset: str  # outcome tokenId
    side: str  # BUY / SELL
    size: float  # shares
    price: float  # USDC / share
    ts_epoch: int  # fill timestamp (block time or API timestamp)
    source: str  # 'chain' | 'poll'
    # Enrichment fields — may arrive pre-populated from Path B:
    condition_id: str | None = None
    outcome: str | None = None
    outcome_index: int | None = None
    market_title: str | None = None
    market_slug: str | None = None
    event_slug: str | None = None
    event_title: str | None = None
    sport: str = "unclassified"

    @property
    def notional(self) -> float:
        return round(self.size * self.price, 6)

    @property
    def dedupe_key(self) -> str:
        return make_dedupe_key(
            self.tx_hash, self.asset, self.side, self.size, self.price, self.ts_epoch
        )


def _feed_payload(trade_id: int, ev: TradeEvent, detected_at: datetime, enriched: bool) -> dict:
    latency = detected_at.timestamp() - ev.ts_epoch
    return {
        "id": trade_id,
        **asdict(ev),
        "notional": ev.notional,
        "ts": datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc).isoformat(),
        "detected_at": detected_at.isoformat(),
        "latency_s": round(max(latency, 0.0), 3),
        "enriched": enriched,
    }


# THE DEDUPE ANSWER, SEPARATED FROM THE ID (2026-08-25, adversarial
# review of the ON CONFLICT change).
#
# This function used to return None on a duplicate, so `is not None`
# WAS the "was this new?" test and two callers used it that way. The
# switch to ON CONFLICT DO UPDATE (so an enrichment pass can fill in a
# NULL condition_id without re-firing the fan-out) made it return the
# id on EVERY row — and the fan-out gate inside this file was converted
# to `row["was_insert"]` while the two external callers were not.
#
# Result: reconciler.py counted its entire 500-row-per-wallet re-sweep
# as MISSED FILLS and reported permanent drift, and poller.py
# over-reported new trades on every pass. Both are displayed numbers,
# and both were confidently wrong rather than absent.
#
# The fix is not to restore the old overloading — an id that doubles as
# a boolean is what let one contract change break two readers silently.
# `ingest_trade_result` states both facts explicitly, and `ingest_trade`
# stays as the id-returning wrapper for callers that only want the id.
async def ingest_trade(ev: TradeEvent, notify: bool = True) -> int | None:
    """Insert + fan out one detected fill, returning the trade id.

    RETURNS THE ID ON DUPLICATES TOO. `is not None` is NOT a dedupe
    test — use ingest_trade_result() for that. See the note above.

    notify=False inserts silently (no publish, no outbox) — used by the deep
    history backfill so importing thousands of past trades can't page anyone.
    """
    tid, _was_new = await ingest_trade_result(ev, notify)
    return tid


async def ingest_trade_result(ev: TradeEvent,
                              notify: bool = True) -> tuple[int | None, bool]:
    """(trade id, was_this_row_newly_inserted).

    Callers asking "did I just see something new?" read the second
    element. The first is non-None for duplicates too.
    """
    pool = await get_pool()
    detected_at = datetime.now(tz=timezone.utc)
    ts = datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc)
    pre_enriched = ev.condition_id is not None

    row = await pool.fetchrow(
        """
        INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, outcome, outcome_index,
                            size, price, notional, market_title, market_slug, event_slug, sport,
                            ts, source, detected_at, enriched_at, dedupe_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
        -- ENRICHMENT MUST LAND, NOT BE DISCARDED (2026-08-25).
        --
        -- DO NOTHING threw away the only thing the second pass was for.
        -- The chain leg sees a fill before anyone knows what market it
        -- belongs to, so it writes the row with condition_id / outcome
        -- / outcome_index NULL. The hourly enrichment pass then fetches
        -- exactly those fields and re-INSERTs the same dedupe_key — and
        -- the conflict clause dropped it on the floor. Every hour.
        --
        -- The cost is the whole NOSLUG bucket: 22,330 rejected rows,
        -- 22,327 of them with no token in market_tokens, over 23 live
        -- days = 971 permanently dead copy attempts a day. A row with a
        -- NULL condition_id can never map, never resolve a sibling, and
        -- never be classified as an entry or an exit.
        --
        -- COALESCE keeps the FIRST non-null value in every case, so a
        -- late pass can only ever fill a hole. It cannot overwrite what
        -- the chain observed, which is the property that makes this
        -- safe on a table the executor reads: the price, size and side
        -- that money was staked against are not in this list and never
        -- move.
        ON CONFLICT (dedupe_key) DO UPDATE SET
            condition_id  = COALESCE(trades.condition_id,
                                     EXCLUDED.condition_id),
            outcome       = COALESCE(trades.outcome, EXCLUDED.outcome),
            outcome_index = COALESCE(trades.outcome_index,
                                     EXCLUDED.outcome_index),
            market_title  = COALESCE(trades.market_title,
                                     EXCLUDED.market_title),
            market_slug   = COALESCE(trades.market_slug,
                                     EXCLUDED.market_slug),
            event_slug    = COALESCE(trades.event_slug,
                                     EXCLUDED.event_slug),
            enriched_at   = COALESCE(trades.enriched_at,
                                     EXCLUDED.enriched_at),
            -- VENUE CORROBORATION for S1-won fills (fleet rounds 2-3):
            -- when the venue's own feed re-delivers a fill the S1
            -- emitter already ingested, the poll duplicate lands here
            -- and stamps the row. The stamp originates at the VENUE,
            -- so it corroborates the emission in a way a shared decode
            -- bug cannot fake — INCLUDING wallet attribution: the
            -- whale_id equality means a fill booked under the wrong
            -- whale is never stamped by the true whale's poll row
            -- (round 3: the whale-blind stamp self-corroborated
            -- exactly the owner/cpty confusion class). First stamp
            -- wins; the emitter's sweep judges unstamped rows.
            venue_seen_at = COALESCE(trades.venue_seen_at,
                                     CASE WHEN EXCLUDED.source = 'poll'
                                          AND EXCLUDED.whale_id = trades.whale_id
                                          THEN now() END)
        -- xmax = 0 IS THE ONLY THING SEPARATING A FILL FROM A REFILL.
        --
        -- Under DO NOTHING a duplicate returned no row, and `row is
        -- None` was the entire duplicate test. DO UPDATE returns a row
        -- every time — so without this flag the hourly enrichment pass
        -- would look like a brand-new trade, publish to the feed, and
        -- fire execute_copy AGAIN on a fill we already copied. Fixing
        -- coverage must not buy it with duplicate orders.
        --
        -- Postgres sets xmax to 0 on a genuine INSERT and to the
        -- updating transaction id on a conflict-update, so this is the
        -- engine telling us which branch it took rather than us
        -- inferring it.
        RETURNING id, (xmax = 0) AS was_insert
        """,
        ev.whale_id,
        ev.tx_hash,
        str(ev.asset),
        ev.condition_id,
        ev.side,
        ev.outcome,
        ev.outcome_index,
        ev.size,
        ev.price,
        ev.notional,
        ev.market_title,
        ev.market_slug,
        ev.event_slug,
        ev.sport,
        ts,
        ev.source,
        detected_at,
        detected_at if pre_enriched else None,  # enriched_at — no param reuse:
        # a parameter appearing in two SQL contexts (value + CASE) makes
        # Postgres deduce conflicting types ("text versus timestamptz").
        ev.dedupe_key,
    )
    if row is None:
        # The WHERE on the DO UPDATE matched nothing: a duplicate with
        # no holes to fill. Not new, and no id to hand back.
        return None, False
    trade_id = row["id"]
    # An enrichment pass filling in a NULL condition_id is not a new
    # fill. It returns the id — the row is real and callers want it —
    # but it must never reach the fan-out below, or every hourly pass
    # re-publishes and re-copies a trade we already acted on.
    if not row["was_insert"]:
        return trade_id, False

    if not notify:
        return trade_id, True

    payload = _feed_payload(trade_id, ev, detected_at, pre_enriched)

    # Fire fan-out NOW on the provisional record.
    await publish(CH_TRADES_NEW, payload)

    # Copy-trade feasibility: snapshot the residual book at exactly the moment
    # an executor would react. Fire-and-forget; never delays ingestion.
    # FRESH DETECTIONS ONLY: after an outage, the first poll ingests every
    # whale's newest ~100 trades AT ONCE — spawning hundreds of concurrent
    # probe tasks (book fetches, DB conns, order mapping) during boot, which
    # can OOM or starve health checks on a small instance (observed
    # 2026-08-02). A stale detection is not copyable and its residual book
    # is not evidence; probing it buys nothing. Backlog rows are ingested
    # as data only.
    # 600s, was 300: measured detection lag runs 3-6 minutes (poller
    # cadence + venue API), so a 5-minute freshness gate silently excluded
    # most REAL fresh fills — zero copy probes fired 21:13-22:24Z on
    # 2026-08-03 while both whales traded. Staleness cost is bounded by
    # the copier's FOK-at-his-price order, not by this gate.
    fresh = (ev.ts_epoch or 0) > (datetime.now(timezone.utc).timestamp() - 600)
    if fresh:
        from ..copy_probe import probe_trade
        from ..live_executor import execute_copy

        # Probe (measurement) and live execution are INDEPENDENT tasks
        # (2026-08-10): execution used to run inside the probe's
        # semaphore, so a whale burst serialized real orders behind book
        # snapshots — and the probe's 120s measurement gate silently
        # forfeited every slower detection to the 10-minute sweep.
        asyncio.get_running_loop().create_task(probe_trade(payload))
        asyncio.get_running_loop().create_task(execute_copy(payload))

    # Notification bookkeeping runs OFF the hot path (latency map
    # 2026-08-17): its 2-4 sequential inserts used to sit awaited between
    # the publish and the copy-execution spawn, taxing both copy legs to
    # write webpush/telegram rows neither leg reads.
    asyncio.get_running_loop().create_task(_write_outbox(trade_id, payload))

    if not pre_enriched:
        # Enrich off the hot path; never blocks the next detection.
        asyncio.get_running_loop().create_task(_enrich(trade_id, ev, detected_at))
    return trade_id, True


async def _write_outbox(trade_id: int, payload: dict) -> None:
    from ..notifications import ntfy, sms

    pool = await get_pool()
    kinds = ["webpush", "telegram"]
    if sms.enabled():
        kinds.append("sms")
    if ntfy.enabled():
        kinds.append("ntfy")
    for kind in kinds:
        try:
            await pool.execute(
                """
                INSERT INTO notification_outbox (trade_id, kind, payload)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (trade_id, kind) DO NOTHING
                """,
                trade_id,
                kind,
                json.dumps(payload, default=str),
            )
        except Exception:  # noqa: BLE001
            # A notification is a convenience; ingestion is the product. A
            # schema drift on THIS insert (kind check vs the list above)
            # silently killed six days of whale detection on 2026-07-27 —
            # an alert channel must never be able to take the pipeline
            # down with it.
            log.exception("outbox enqueue failed for kind=%s (non-fatal)", kind)


async def _enrich(trade_id: int, ev: TradeEvent, detected_at: datetime) -> None:
    try:
        meta = await gamma.lookup_token_live(str(ev.asset))
        if meta is None:
            log.warning("no metadata for token %s (tx %s); will enrich on refresh", ev.asset, ev.tx_hash)
            return
        pool = await get_pool()
        await pool.execute(
            """
            UPDATE trades SET condition_id=$2, outcome=$3, outcome_index=$4, market_title=$5,
                              market_slug=$6, event_slug=$7, sport=$8, enriched_at=now()
            WHERE id = $1
            """,
            trade_id,
            meta["condition_id"],
            meta["outcome"],
            meta["outcome_index"],
            meta["title"],
            meta["slug"],
            meta["event_slug"],
            meta["sport"],
        )
        ev.condition_id = meta["condition_id"]
        ev.outcome = meta["outcome"]
        ev.outcome_index = meta["outcome_index"]
        ev.market_title = meta["title"]
        ev.market_slug = meta["slug"]
        ev.event_slug = meta["event_slug"]
        ev.event_title = meta.get("event_title")
        ev.sport = meta["sport"]
        payload = _feed_payload(trade_id, ev, detected_at, enriched=True)
        await publish(CH_TRADES_ENRICHED, payload)
        # Refresh the outbox payload if it hasn't been dispatched yet, so the
        # push text carries market names when enrichment beats the dispatcher.
        pool2 = await get_pool()
        await pool2.execute(
            "UPDATE notification_outbox SET payload=$2::jsonb WHERE trade_id=$1 AND NOT sent",
            trade_id,
            json.dumps(payload, default=str),
        )
    except Exception:  # noqa: BLE001 — enrichment must never kill the listener
        log.exception("enrichment failed for trade %s", trade_id)


# Enrichment dead-token ledger (owner order 2026-08-13, the '(no
# slug)' funnel bucket: 1,978 rejected copies in 7 days with no
# metadata at all). The backfill window is `newest 200 unenriched`,
# so a standing population of tokens Gamma will NEVER know — other
# asset classes, delisted markets — eventually fills the entire
# window, and every fresh enrichable trade behind it is never retried
# while every cycle re-asks Gamma the same 200 dead questions.
# Review 2026-08-13 tuned two things: the write-off threshold is 30
# cycles (~30 min at the refresher's 60s cadence — 5 was faster than
# the catalog's own supply lag, so a merely-late token got branded
# dead), and a dead entry is AMNESTIED after 24h so a catalog that
# learns late still gets its hearing without waiting for a deploy.
MAX_ENRICH_FAILS = 30
ENRICH_AMNESTY_S = 24 * 3600.0
_enrich_fails: dict[str, tuple[int, float]] = {}   # asset -> (n, last_ts)
enrich_stats: dict[str, int] = {}


async def backfill_unenriched(limit: int = 200) -> int:
    """Re-attempt enrichment for trades that missed the cache (metadata worker calls this)."""
    import time as _t

    pool = await get_pool()
    now = _t.time()
    for k in [k for k, (_, ts) in _enrich_fails.items()
              if now - ts > ENRICH_AMNESTY_S]:
        _enrich_fails.pop(k, None)
    if len(_enrich_fails) > 8000:
        # Bounded — and the DEAD entries are the memory worth keeping
        # (review: trimming oldest-first evicted exactly the confirmed
        # -dead tokens and let the clog rebuild). Low-count entries
        # are cheap to relearn; drop those first.
        alive = [k for k, (n, _) in _enrich_fails.items()
                 if n < MAX_ENRICH_FAILS]
        for k in alive[:len(_enrich_fails) - 6000]:
            _enrich_fails.pop(k, None)
        while len(_enrich_fails) > 8000:
            _enrich_fails.pop(next(iter(_enrich_fails)), None)
    dead = [a for a, (n, _) in _enrich_fails.items()
            if n >= MAX_ENRICH_FAILS]
    # KEYED ON market_tokens COVERAGE, NOT ON enriched_at (2026-08-26).
    #
    # enriched_at is stamped at INSERT for any trade arriving with its
    # own condition_id, so that row never calls Gamma and its market
    # never enters market_tokens -- and this lane, selecting WHERE
    # enriched_at IS NULL, then excluded it permanently. The population
    # it was built to repair was the one population it could not see.
    #
    # classify_exit joins market_tokens to itself to find a token's
    # complementary leg, so a token absent from that table cannot be
    # classified as an exit at all: cls_token_unenriched was 1,337. The
    # selector now asks the question that matters -- which traded
    # tokens does market_tokens not cover -- so the backlog drains
    # instead of standing forever. Grouped by asset because the same
    # token is traded many times and one repair fixes them all.
    rows = await pool.fetch(
        "SELECT max(t.id) AS id, t.asset FROM trades t "
        "LEFT JOIN market_tokens mt ON mt.token_id = t.asset "
        "WHERE mt.token_id IS NULL "
        "  AND NOT (t.asset = ANY($2::text[])) "
        "GROUP BY t.asset ORDER BY max(t.id) DESC LIMIT $1",
        limit, dead[:4000]
    )
    # Visibility for the heartbeat: how big is the standing backlog in
    # the newest slice, and how much of it is known-dead.
    # The backlog that matters is the same population the selector
    # above walks: distinct traded tokens market_tokens does not cover.
    backlog = await pool.fetchval(
        "SELECT count(*) FROM (SELECT DISTINCT t.asset FROM trades t "
        "LEFT JOIN market_tokens mt ON mt.token_id = t.asset "
        "WHERE mt.token_id IS NULL LIMIT 1000) t")
    enrich_stats.update(unenriched_1k=int(backlog or 0),
                        dead_tokens=len(dead))
    fixed = 0
    for row in rows:
        meta = await gamma.lookup_token_live(row["asset"])
        if meta is None:
            a = str(row["asset"])
            n, _ts = _enrich_fails.get(a, (0, 0.0))
            _enrich_fails[a] = (n + 1, now)
            continue
        _enrich_fails.pop(str(row["asset"]), None)
        await pool.execute(
            """
            UPDATE trades SET condition_id=$2, outcome=$3, outcome_index=$4, market_title=$5,
                              market_slug=$6, event_slug=$7, sport=$8, enriched_at=now()
            WHERE id=$1
            """,
            row["id"],
            meta["condition_id"],
            meta["outcome"],
            meta["outcome_index"],
            meta["title"],
            meta["slug"],
            meta["event_slug"],
            meta["sport"],
        )
        fixed += 1
    return fixed
