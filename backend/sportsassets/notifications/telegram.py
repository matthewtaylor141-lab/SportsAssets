"""Telegram broadcast (channel) + admin alerts. No-ops cleanly when unconfigured."""

from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


async def _send(chat_id: str, text: str) -> bool:
    cfg = settings()
    if not cfg.telegram_bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                url,
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
            if resp.status_code == 429:
                log.warning("telegram rate limited")
                return False
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        log.warning("telegram send failed: %s", exc)
        return False


async def broadcast_channel(text: str) -> bool:
    """Post to the public/invite trade channel."""
    return await _send(settings().telegram_channel_id, text)


async def alert_admins(text: str) -> bool:
    """Ops alert to the admin chat (WS down, poll failures, drift, roster changes)."""
    return await _send(settings().telegram_admin_chat_id, text)
