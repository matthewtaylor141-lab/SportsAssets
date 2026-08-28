"""Zero-latency order-confirmation stream (owner order 2026-08-28).

live_orders announces every INSERT and status change via a Postgres
trigger (migration 037) -> pg_notify ON COMMIT. One listener
connection per process fans events out to in-process subscriber
queues; /api/desk/stream serves them as SSE. There is no polling
anywhere in the path: the confirmation reaches the desk the moment
the venue's answer is recorded, and the write sites — executor,
workers, manual desk, cash-outs — carry zero streaming code.

The listener holds a DEDICATED asyncpg connection (never a pool
slot: the pool is the executor's pricing path) and redials on any
failure. A stalled SSE client can never block the others: queues are
bounded and a full queue drops that client's oldest view of the
world, not the stream.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator

log = logging.getLogger(__name__)

CHANNEL = "live_orders_events"
QUEUE_MAX = 256

_subs: set[asyncio.Queue] = set()
_task: asyncio.Task | None = None


def _on_notify(_conn: Any, _pid: int, _channel: str, payload: str) -> None:
    for q in list(_subs):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            # the slow client loses events; the stream never stalls
            pass


async def _listen_forever() -> None:
    import asyncpg

    from ..config import settings

    while True:
        try:
            conn = await asyncpg.connect(settings().database_url, timeout=10)
            try:
                await conn.add_listener(CHANNEL, _on_notify)
                log.info("order stream: listening on %s", CHANNEL)
                while True:
                    await asyncio.sleep(30)
                    # a dead connection raises here and we redial
                    await conn.execute("SELECT 1")
            finally:
                await conn.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the stream must outlive
            # any single connection; clients hold their SSE and events
            # resume after the redial (confirmations are also durable
            # in live_orders — the stream is presentation, not record)
            log.warning("order stream listener redialing: %s", exc)
            await asyncio.sleep(2)


def ensure_listener() -> None:
    """Idemptent per process; first SSE client starts the listener."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_listen_forever())


async def sse_events(request: Any) -> AsyncIterator[str]:
    """The SSE generator: `order` events, 15s keepalives."""
    ensure_listener()
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
    _subs.add(q)
    try:
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield f"event: order\ndata: {payload}\n\n"
    finally:
        _subs.discard(q)
