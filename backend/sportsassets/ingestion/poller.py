"""Path B — Data API polling (fallback + reconciliation source of truth).

Polls /trades?user={wallet} per tracked wallet on a staggered schedule:
with N wallets and interval I, one wallet is polled every I/N seconds
(5 wallets @ 5s → 1 req/s aggregate). Records arrive pre-enriched, so a
Path-B-first detection still renders fully in the feed, flagged source=poll.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from ..config import settings
from ..db import get_pool, heartbeat
from .pipeline import TradeEvent, ingest_trade

log = logging.getLogger(__name__)


def parse_data_api_trade(raw: dict[str, Any], whale_id: int, username: str | None) -> TradeEvent:
    """Map a data-api trade payload onto our TradeEvent."""
    return TradeEvent(
        whale_id=whale_id,
        whale_username=username,
        tx_hash=str(raw.get("transactionHash") or raw.get("txHash") or ""),
        asset=str(raw.get("asset") or raw.get("tokenId") or ""),
        side=str(raw.get("side", "")).upper(),
        size=float(raw.get("size", 0)),
        price=float(raw.get("price", 0)),
        ts_epoch=int(raw.get("timestamp", 0)),
        source="poll",
        condition_id=raw.get("conditionId"),
        outcome=raw.get("outcome"),
        outcome_index=raw.get("outcomeIndex"),
        market_title=raw.get("title"),
        market_slug=raw.get("slug"),
        event_slug=raw.get("eventSlug"),
        sport="unclassified",  # classified below from persisted metadata when available
    )


async def _sport_for_condition(condition_id: str | None) -> str | None:
    if not condition_id:
        return None
    pool = await get_pool()
    return await pool.fetchval("SELECT sport FROM markets WHERE condition_id=$1", condition_id)


class Poller:
    def __init__(self) -> None:
        cfg = settings()
        self._http = httpx.AsyncClient(base_url=cfg.data_api_base, timeout=10)
        self._interval = cfg.poll_interval_seconds
        self._fail_threshold = cfg.poll_failure_alert_threshold
        self._consecutive_failures = 0
        self.on_alert = None  # callable(str) set by the worker (Telegram admin alert)

    async def tracked_whales(self) -> list[dict]:
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id, address, username FROM whales WHERE active AND NOT banned ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def poll_wallet(self, whale: dict) -> int:
        """One poll cycle for one wallet. Returns count of NEW trades ingested."""
        from ..ratelimit import polite_get

        resp = await polite_get(
            self._http,
            "/trades",
            params={"user": whale["address"], "limit": 100, "takerOnly": "false"},
        )
        resp.raise_for_status()
        new = 0
        for raw in resp.json():
            ev = parse_data_api_trade(raw, whale["id"], whale["username"])
            if not ev.tx_hash or ev.size <= 0:
                continue
            sport = await _sport_for_condition(ev.condition_id)
            if sport:
                ev.sport = sport
            if await ingest_trade(ev) is not None:
                new += 1
        return new

    async def _history_loop(self) -> None:
        """One-time deep history import per whale — background, never blocks
        live polling; checks for newly added whales every minute."""
        from .history import backfill_pending  # late import to avoid cycle

        while True:
            try:
                scanned = await backfill_pending()
                if scanned:
                    log.info("deep history backfill scanned %s trades", scanned)
            except Exception:  # noqa: BLE001
                log.exception("history backfill pass failed; will retry")
            await asyncio.sleep(60)

    async def run(self) -> None:
        log.info("Path B poller starting (interval=%ss)", self._interval)
        asyncio.get_running_loop().create_task(self._history_loop())
        while True:
            whales = await self.tracked_whales()
            if not whales:
                await heartbeat("poller", "idle", {"reason": "empty roster"})
                await asyncio.sleep(self._interval)
                continue
            stagger = self._interval / len(whales)
            for whale in whales:
                try:
                    new = await self.poll_wallet(whale)
                    self._consecutive_failures = 0
                    await heartbeat("poller", "ok", {"last_wallet": whale["address"], "new": new})
                except Exception as exc:  # noqa: BLE001 — one bad wallet/payload
                    # must never kill live detection for the others
                    self._consecutive_failures += 1
                    log.warning("poll failed for %s: %s", whale["address"], exc)
                    await heartbeat(
                        "poller", "error", {"failures": self._consecutive_failures, "error": str(exc)}
                    )
                    if self._consecutive_failures == self._fail_threshold and self.on_alert:
                        await self.on_alert(
                            f"⚠️ Poll cycle failed {self._consecutive_failures}× — Path B degraded"
                        )
                await asyncio.sleep(stagger)
