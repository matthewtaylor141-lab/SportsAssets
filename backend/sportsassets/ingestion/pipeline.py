"""Shared trade pipeline.

Both detection paths call `ingest_trade`. The contract:

1. INSERT with ON CONFLICT (dedupe_key) DO NOTHING — the only dedupe gate.
2. If (and only if) the row was new: publish `trades.new` immediately and
   write notification outbox rows. Fan-out fires on the PROVISIONAL record —
   enrichment must never delay notification.
3. Enrich asynchronously from the hot metadata cache; publish `trades.enriched`.

Replaying any event is therefore safe end-to-end: a duplicate insert is a
no-op, so no duplicate publish and no duplicate outbox rows.
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


async def ingest_trade(ev: TradeEvent) -> int | None:
    """Insert + fan out one detected fill. Returns trade id if new, None if dup."""
    pool = await get_pool()
    detected_at = datetime.now(tz=timezone.utc)
    ts = datetime.fromtimestamp(ev.ts_epoch, tz=timezone.utc)
    pre_enriched = ev.condition_id is not None

    row = await pool.fetchrow(
        """
        INSERT INTO trades (whale_id, tx_hash, asset, condition_id, side, outcome, outcome_index,
                            size, price, notional, market_title, market_slug, event_slug, sport,
                            ts, source, detected_at, enriched_at, dedupe_key)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,
                CASE WHEN $18 THEN $17 ELSE NULL END, $19)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
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
        pre_enriched,
        ev.dedupe_key,
    )
    if row is None:
        return None  # duplicate — the other path saw it first
    trade_id = row["id"]

    payload = _feed_payload(trade_id, ev, detected_at, pre_enriched)

    # Fire fan-out NOW on the provisional record.
    await publish(CH_TRADES_NEW, payload)
    await _write_outbox(trade_id, payload)

    if not pre_enriched:
        # Enrich off the hot path; never blocks the next detection.
        asyncio.get_running_loop().create_task(_enrich(trade_id, ev, detected_at))
    return trade_id


async def _write_outbox(trade_id: int, payload: dict) -> None:
    pool = await get_pool()
    for kind in ("webpush", "telegram"):
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


async def _enrich(trade_id: int, ev: TradeEvent, detected_at: datetime) -> None:
    try:
        meta = await gamma.lookup_token(str(ev.asset))
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


async def backfill_unenriched(limit: int = 200) -> int:
    """Re-attempt enrichment for trades that missed the cache (metadata worker calls this)."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT id, asset FROM trades WHERE enriched_at IS NULL ORDER BY id DESC LIMIT $1", limit
    )
    fixed = 0
    for row in rows:
        meta = await gamma.lookup_token(row["asset"])
        if meta is None:
            continue
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
