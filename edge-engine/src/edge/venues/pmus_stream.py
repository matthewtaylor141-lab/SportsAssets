"""Polymarket US live book stream — WebSocket market data cache.

Replaces poll-latency with push-latency: a daemon thread runs the venue's
markets WebSocket (official SDK), keeps the latest full book per subscribed
slug, and the adapter serves books from this cache instantly instead of a
REST round-trip per market. The engine sees a mispricing the moment the
book moves, not up to a cycle later.

Fail-soft by design: if the socket can't connect (or drops), the adapter's
REST path still works — the stream is an accelerator, never a dependency.
Reconnects with capped backoff and resubscribes everything it knew.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger(__name__)

SUB_BATCH = 200          # slugs per subscribe request
MAX_SUBSCRIPTIONS = 4000  # safety bound


class BookStreamer:
    def __init__(self, key_id: str, secret_key: str, autostart: bool = True) -> None:
        self._key_id = key_id
        self._secret_key = secret_key
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict]] = {}   # slug -> (ts, marketData)
        self._subscribed: set[str] = set()
        self._pending: list[str] = []
        self._listeners: list[Callable[[str], None]] = []
        self.connected = False
        self.updates = 0
        self.reconnects = 0
        self._stop = False
        if autostart:
            threading.Thread(target=self._run, daemon=True,
                             name="pmus-book-stream").start()

    # ── thread-safe API (called from the sync engine) ───────────────────

    def add_listener(self, fn: Callable[[str], None]) -> None:
        """Called with the slug on every book update. This is what turns a
        polling engine into a reacting one: the listener runs on the stream
        thread, so it must be cheap and non-blocking (the reactor just sets
        a flag). A raising listener is logged and never propagates."""
        with self._lock:
            self._listeners.append(fn)

    def ensure(self, slugs: list[str]) -> None:
        """Queue subscriptions for slugs we haven't subscribed to yet."""
        with self._lock:
            room = MAX_SUBSCRIPTIONS - len(self._subscribed)
            fresh: list[str] = []
            for s in slugs:
                if s and s not in self._subscribed and s not in fresh:
                    fresh.append(s)
            fresh = fresh[:max(room, 0)]
            self._subscribed.update(fresh)
            self._pending.extend(fresh)

    def get(self, slug: str, max_age_s: float = 90.0) -> dict | None:
        """Latest full marketData for a slug, or None if absent/stale."""
        with self._lock:
            entry = self._cache.get(slug)
        if entry and time.time() - entry[0] <= max_age_s:
            return entry[1]
        return None

    def stats(self) -> dict:
        with self._lock:
            return {"connected": self.connected, "subscribed": len(self._subscribed),
                    "cached": len(self._cache), "updates": self.updates,
                    "reconnects": self.reconnects}

    # ── stream thread ───────────────────────────────────────────────────

    def _on_market_data(self, message: dict) -> None:
        md = (message or {}).get("marketData") or {}
        slug = md.get("marketSlug")
        if not slug:
            return
        with self._lock:
            self._cache[slug] = (time.time(), md)
            self.updates += 1
            listeners = list(self._listeners)
        # Notify OUTSIDE the lock: a listener must never be able to deadlock
        # the stream thread against a reader calling get()/stats().
        for fn in listeners:
            try:
                fn(slug)
            except Exception as exc:  # noqa: BLE001 — a bad listener must
                log.debug("book listener failed: %s", exc)  # not stop the feed

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:  # noqa: BLE001 — never take the engine down
            log.error("book stream thread died: %s", exc)

    async def _main(self) -> None:
        from polymarket_us.websocket.markets import MarketsWebSocket

        backoff = 1.0
        sub_seq = 0
        while not self._stop:
            conn_open = {"v": False}
            try:
                ws = MarketsWebSocket(key_id=self._key_id, secret_key=self._secret_key)
                ws.on("market_data", self._on_market_data)
                ws.on("close", lambda *a: conn_open.update(v=False))
                ws.on("error", lambda e=None: log.debug("book stream error: %s", e))
                await ws.connect()
                conn_open["v"] = True
                self.connected = True
                backoff = 1.0
                # Resubscribe everything we know, then pump new subscriptions.
                with self._lock:
                    self._pending = list(self._subscribed)
                while conn_open["v"] and not self._stop:
                    with self._lock:
                        batch, self._pending = (self._pending[:SUB_BATCH],
                                                self._pending[SUB_BATCH:])
                    if batch:
                        sub_seq += 1
                        await ws.subscribe_market_data(f"edge-md-{sub_seq}", batch)
                    else:
                        await asyncio.sleep(0.5)
            except Exception as exc:  # noqa: BLE001 — reconnect with backoff
                log.warning("book stream disconnected: %s", exc)
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
