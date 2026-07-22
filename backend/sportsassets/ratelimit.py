"""Polite Data-API access: one shared throttle per process + 429 backoff.

Every Data-API caller (live poller, history backfill, position snapshots,
reconciler) goes through `polite_get`, so their combined request rate stays
under the configured ceiling and a 429 backs everyone off together instead
of spiraling into a rate-limit ban.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import settings

log = logging.getLogger(__name__)


class Throttle:
    """Serializes request starts to at most `rate` per second (process-wide)."""

    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / max(rate, 0.1)
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


_throttle: Throttle | None = None


def data_api_throttle() -> Throttle:
    global _throttle
    if _throttle is None:
        _throttle = Throttle(settings().data_api_max_rps)
    return _throttle


async def polite_get(
    http: httpx.AsyncClient, url: str, params: dict | None = None, retries: int = 3
) -> httpx.Response:
    """Throttled GET with Retry-After-aware 429 backoff. Returns the final
    response (raise_for_status is the caller's decision)."""
    throttle = data_api_throttle()
    resp: httpx.Response | None = None
    for attempt in range(retries + 1):
        await throttle.wait()
        resp = await http.get(url, params=params)
        if resp.status_code != 429:
            return resp
        retry_after = resp.headers.get("Retry-After")
        try:
            wait = min(float(retry_after), 120.0) if retry_after else 10.0 * (attempt + 1)
        except ValueError:
            wait = 10.0 * (attempt + 1)
        log.warning("data-api 429 — backing off %.0fs (attempt %s)", wait, attempt + 1)
        await asyncio.sleep(wait)
    return resp  # last 429 — caller's raise_for_status surfaces it
