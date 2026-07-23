"""Notification dispatcher — drains the outbox, never blocks ingestion.

Design:
- Ingestion writes outbox rows and returns; this worker is the only sender.
- Rows are CLAIMED atomically (sent=true ... RETURNING) before dispatch:
  at-most-once delivery, so replays/restarts can never double-push (spec §8).
- Per-user prefs filter delivery; per-(user, whale) burst collapsing shapes it.
- Also runs the ops monitor: alerts admins if the chain listener is silent
  >30s or the poller reports repeated failures (spec §8 health rules).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque

from ..config import settings
from ..db import get_pool, heartbeat
from . import ntfy, sms, telegram
from .collapse import passes_prefs, plan_deliveries
from .webpush import broadcast

log = logging.getLogger(__name__)

TICK_SECONDS = 1.0


class Dispatcher:
    def __init__(self) -> None:
        cfg = settings()
        self._threshold = cfg.burst_collapse_threshold
        self._window = cfg.burst_collapse_window_seconds
        # (user_key, whale_id) -> deque of delivery timestamps within window
        self._history: dict[tuple[str, int], deque[float]] = defaultdict(deque)
        self._alerted: dict[str, bool] = {}

    # ── collapse-window history ────────────────────────────────────────
    def _recent_counts(self, user_key: str) -> dict[int, int]:
        now = time.time()
        counts: dict[int, int] = {}
        for (uk, whale_id), stamps in self._history.items():
            if uk != user_key:
                continue
            while stamps and now - stamps[0] > self._window:
                stamps.popleft()
            if stamps:
                counts[whale_id] = len(stamps)
        return counts

    def _record(self, user_key: str, whale_id: int, n: int = 1) -> None:
        now = time.time()
        self._history[(user_key, whale_id)].extend([now] * n)

    # ── outbox draining ────────────────────────────────────────────────
    async def _claim(self, kind: str, limit: int = 200) -> list[dict]:
        pool = await get_pool()
        rows = await pool.fetch(
            """
            UPDATE notification_outbox SET sent = TRUE, sent_at = now()
            WHERE id IN (
                SELECT id FROM notification_outbox
                WHERE kind = $1 AND NOT sent ORDER BY id LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, trade_id, payload
            """,
            kind,
            limit,
        )
        out = []
        for r in rows:
            payload = r["payload"]
            out.append(json.loads(payload) if isinstance(payload, str) else payload)
        return out

    async def _dispatch_webpush(self) -> None:
        trades = await self._claim("webpush")
        if not trades:
            return
        pool = await get_pool()
        subs = await pool.fetch("SELECT user_key, endpoint, p256dh, auth FROM push_subscriptions")
        if not subs:
            return
        prefs_rows = await pool.fetch("SELECT * FROM user_prefs")
        prefs_by_user = {r["user_key"]: dict(r) for r in prefs_rows}
        subs_by_user: dict[str, list[dict]] = defaultdict(list)
        for s in subs:
            subs_by_user[s["user_key"]].append(dict(s))

        for user_key, user_subs in subs_by_user.items():
            prefs = prefs_by_user.get(user_key, {})
            for key in ("muted_whales", "sports"):
                if isinstance(prefs.get(key), str):
                    prefs[key] = json.loads(prefs[key])
            allowed = [t for t in trades if passes_prefs(t, prefs)]
            if not allowed:
                continue
            deliveries = plan_deliveries(
                allowed, self._recent_counts(user_key), threshold=self._threshold
            )
            for d in deliveries:
                payload = {"title": d.title, "body": d.body, "url": d.url, "kind": d.kind}
                await broadcast(user_subs, payload)
                for t in allowed:
                    if int(t["id"]) in d.trade_ids:
                        self._record(user_key, int(t["whale_id"]))
                if d.kind == "summary" and d.trade_ids:
                    await pool.execute(
                        "UPDATE notification_outbox SET collapsed = TRUE "
                        "WHERE kind='webpush' AND trade_id = ANY($1::bigint[])",
                        d.trade_ids,
                    )

    async def _dispatch_telegram(self) -> None:
        trades = await self._claim("telegram")
        if not trades:
            return
        # The channel behaves like one recipient with default prefs.
        deliveries = plan_deliveries(
            trades, self._recent_counts("__telegram_channel__"), threshold=self._threshold
        )
        for d in deliveries:
            text = d.title if d.kind == "summary" else f"{d.title}\n{d.body}"
            await telegram.broadcast_channel(text)
        for t in trades:
            self._record("__telegram_channel__", int(t["whale_id"]))

    async def _watched_ids(self, cache_key: str, addrs: set[str]) -> set[int] | None:
        """whale ids for a channel's wallet watch list (cached 60s); None = all."""
        if not addrs:
            return None
        now = time.time()
        cache = getattr(self, "_watch_cache", None) or {}
        hit = cache.get(cache_key)
        if hit and now - hit[0] < 60:
            return hit[1]
        pool = await get_pool()
        rows = await pool.fetch(
            "SELECT id FROM whales WHERE lower(address) = ANY($1::text[])", list(addrs)
        )
        ids = {r["id"] for r in rows}
        cache[cache_key] = (now, ids)
        self._watch_cache = cache
        return ids

    async def _drain_watched_channel(self, kind: str, addrs: set[str], send) -> None:
        """Shared drain for wallet-scoped channels (sms, ntfy): claim, filter
        by watch list, collapse bursts, deliver."""
        trades = await self._claim(kind)
        if not trades:
            return
        watched = await self._watched_ids(kind, addrs)
        allowed = [t for t in trades
                   if watched is None or int(t["whale_id"]) in watched]
        if not allowed:
            return
        user_key = f"__{kind}__"
        deliveries = plan_deliveries(
            allowed, self._recent_counts(user_key), threshold=self._threshold
        )
        for d in deliveries:
            await send(d)
        for t in allowed:
            self._record(user_key, int(t["whale_id"]))

    async def _dispatch_sms(self) -> None:
        if not sms.enabled():
            return

        async def send(d):
            text = d.title if d.kind == "summary" else f"{d.title} — {d.body}"
            await sms.broadcast(text)

        await self._drain_watched_channel("sms", sms.watch_addresses(), send)

    async def _dispatch_ntfy(self) -> None:
        if not ntfy.enabled():
            return

        async def send(d):
            await ntfy.publish(d.title, d.body)

        await self._drain_watched_channel("ntfy", ntfy.watch_addresses(), send)

    # ── ops monitor (spec §8) ──────────────────────────────────────────
    async def _monitor_health(self) -> None:
        cfg = settings()
        pool = await get_pool()
        rows = await pool.fetch("SELECT service, status, detail, beat_at FROM service_heartbeats")
        now_rows = {r["service"]: r for r in rows}

        chain = now_rows.get("chain_listener")
        if chain and chain["status"] != "disabled":
            silent = (time.time() - chain["beat_at"].timestamp()) > cfg.ws_down_alert_seconds
            down = chain["status"] == "down" or silent
            if down and not self._alerted.get("chain"):
                self._alerted["chain"] = True
                await telegram.alert_admins(
                    f"🔴 Path A (on-chain WS) down >{cfg.ws_down_alert_seconds}s — "
                    "Path B polling is carrying detection"
                )
            elif not down and self._alerted.get("chain"):
                self._alerted["chain"] = False
                await telegram.alert_admins("🟢 Path A (on-chain WS) recovered")

        poller = now_rows.get("poller")
        if poller:
            detail = poller["detail"]
            if isinstance(detail, str):
                detail = json.loads(detail)
            failures = int(detail.get("failures", 0)) if poller["status"] == "error" else 0
            failing = failures >= cfg.poll_failure_alert_threshold
            if failing and not self._alerted.get("poller"):
                self._alerted["poller"] = True
                await telegram.alert_admins(f"🔴 Path B poll cycle failed {failures}× consecutively")
            elif not failing and self._alerted.get("poller"):
                self._alerted["poller"] = False
                await telegram.alert_admins("🟢 Path B polling recovered")

    async def run(self) -> None:
        log.info("dispatcher starting (collapse >%s/%ss)", self._threshold, self._window)
        last_monitor = 0.0
        while True:
            try:
                await self._dispatch_webpush()
                await self._dispatch_telegram()
                await self._dispatch_sms()
                await self._dispatch_ntfy()
                if time.time() - last_monitor > 10:
                    await self._monitor_health()
                    await heartbeat("dispatcher")
                    last_monitor = time.time()
            except Exception:  # noqa: BLE001
                log.exception("dispatcher tick failed")
            await asyncio.sleep(TICK_SECONDS)
