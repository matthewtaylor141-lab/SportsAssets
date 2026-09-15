"""Redis pub/sub bus. Channels:

- trades.new       — provisional trade record, published the instant a fill is detected
- trades.enriched  — same trade after metadata enrichment
- system.health    — ingestion health transitions (for admin UI)
"""

import json
from typing import Any

import redis.asyncio as aioredis

from .config import settings

CH_TRADES_NEW = "trades.new"
CH_TRADES_ENRICHED = "trades.enriched"
CH_HEALTH = "system.health"

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(settings().redis_url, decode_responses=True)
    return _client


async def publish(channel: str, message: dict[str, Any]) -> None:
    await get_redis().publish(channel, json.dumps(message, default=str))
