"""asyncpg pool helpers. All services share this thin layer — no ORM."""

import asyncio
import logging

import asyncpg

from .config import settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    # asyncpg accepts postgresql:// DSNs directly.
    return settings().database_url


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        for attempt in range(10):
            try:
                _pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=10)
                break
            except (OSError, asyncpg.PostgresError) as exc:
                wait = min(2**attempt, 15)
                log.warning("DB connect failed (%s); retry in %ss", exc, wait)
                await asyncio.sleep(wait)
        else:
            raise RuntimeError("could not connect to Postgres")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def heartbeat(service: str, status: str = "ok", detail: dict | None = None) -> None:
    """Record a service heartbeat (used by health checks and admin dashboard)."""
    import json

    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO service_heartbeats (service, status, detail, beat_at)
        VALUES ($1, $2, $3::jsonb, now())
        ON CONFLICT (service) DO UPDATE
            SET status = EXCLUDED.status, detail = EXCLUDED.detail, beat_at = now()
        """,
        service,
        status,
        json.dumps(detail or {}),
    )
