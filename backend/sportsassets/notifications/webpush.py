"""Web Push (VAPID) sender. Batches sends; prunes dead subscriptions on 404/410."""

from __future__ import annotations

import asyncio
import json
import logging

from pywebpush import WebPushException, webpush

from ..config import settings
from ..db import get_pool

log = logging.getLogger(__name__)


def _send_sync(subscription: dict, payload: dict) -> None:
    cfg = settings()
    webpush(
        subscription_info={
            "endpoint": subscription["endpoint"],
            "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
        },
        data=json.dumps(payload),
        vapid_private_key=cfg.vapid_private_key,
        vapid_claims={"sub": cfg.vapid_claims_email},
        ttl=120,  # a stale live-trade alert is worthless
    )


async def send_push(subscription: dict, payload: dict) -> bool:
    """Send one push off the event loop. Returns False if the sub is dead."""
    try:
        await asyncio.to_thread(_send_sync, subscription, payload)
        return True
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (404, 410):
            await _drop_subscription(subscription["endpoint"])
            return False
        log.warning("webpush failed (%s): %s", status, exc)
        return True  # transient — keep the subscription


async def _drop_subscription(endpoint: str) -> None:
    pool = await get_pool()
    await pool.execute("DELETE FROM push_subscriptions WHERE endpoint=$1", endpoint)
    log.info("dropped dead push subscription")


async def broadcast(subscriptions: list[dict], payload: dict, concurrency: int = 20) -> int:
    """Send a payload to many subscriptions concurrently. Returns success count."""
    sem = asyncio.Semaphore(concurrency)

    async def one(sub: dict) -> bool:
        async with sem:
            return await send_push(sub, payload)

    results = await asyncio.gather(*(one(s) for s in subscriptions), return_exceptions=True)
    return sum(1 for r in results if r is True)
