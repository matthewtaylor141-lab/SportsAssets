"""ntfy push delivery — free phone notifications, no account required.

The recipient installs the ntfy app and subscribes to a secret topic name;
we POST each alert to https://ntfy.sh/<topic> and it lands on the phone in
~1-2s. The topic name is the only secret: anyone who knows it can read the
alerts, so use a long random one (set NTFY_TOPIC on both backend services).

Same scoping as SMS: fresh detections only (never backfill), optional
NTFY_WATCH_ADDRESSES wallet filter, shared burst collapsing.
"""

from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)


def enabled() -> bool:
    cfg = settings()
    return bool(cfg.ntfy_topic.strip())


def watch_addresses() -> set[str]:
    """Wallets whose trades trigger pushes. Empty set = every tracked whale."""
    cfg = settings()
    return {a.strip().lower() for a in cfg.ntfy_watch_addresses.split(",") if a.strip()}


async def publish(title: str, body: str, priority: str = "high") -> dict:
    """POST one notification to the topic. Returns {ok, ...}; never raises."""
    cfg = settings()
    url = f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic.strip()}"
    headers = {
        "Title": title.encode("ascii", "backslashreplace").decode(),  # header-safe
        "Priority": priority,
        "Tags": "whale",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=body.encode(), headers=headers)
        if resp.status_code == 200:
            return {"ok": True, "id": resp.json().get("id")}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:  # noqa: BLE001 — delivery failure must not kill the loop
        log.warning("ntfy publish failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
