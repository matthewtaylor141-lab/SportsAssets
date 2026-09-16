"""SMS delivery via Twilio's REST API (no SDK needed — one form POST).

Alert path for trades that must reach a phone within seconds: the poller
detects a fill in ≤~5s, the outbox row is written immediately on the
provisional record, and the dispatcher drains every 1s — so a text lands
well inside the 1-minute requirement.

SMS costs money per message, so this channel is scoped harder than push:
  * SMS_WATCH_ADDRESSES limits texts to specific wallets (empty = all
    tracked whales)
  * the shared burst-collapse policy turns a fill spree into one summary
Configuration: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER
and SMS_TO_NUMBERS (comma-separated E.164, e.g. +15551234567).
"""

from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger(__name__)

MAX_SMS_CHARS = 320  # 2 concatenated GSM segments; keep alerts terse


def enabled() -> bool:
    cfg = settings()
    return bool(cfg.twilio_account_sid and cfg.twilio_auth_token
                and cfg.twilio_from_number and recipients())


def recipients() -> list[str]:
    cfg = settings()
    return [n.strip() for n in cfg.sms_to_numbers.split(",") if n.strip()]


def watch_addresses() -> set[str]:
    """Wallets whose trades trigger texts. Empty set = every tracked whale."""
    cfg = settings()
    return {a.strip().lower() for a in cfg.sms_watch_addresses.split(",") if a.strip()}


async def send_one(to: str, body: str) -> dict:
    """Send one SMS. Returns {ok, to, sid|error}; never raises."""
    cfg = settings()
    url = (f"https://api.twilio.com/2010-04-01/Accounts/"
           f"{cfg.twilio_account_sid}/Messages.json")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                auth=(cfg.twilio_account_sid, cfg.twilio_auth_token),
                data={"To": to, "From": cfg.twilio_from_number,
                      "Body": body[:MAX_SMS_CHARS]},
            )
        if resp.status_code in (200, 201):
            return {"ok": True, "to": to, "sid": resp.json().get("sid")}
        return {"ok": False, "to": to,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:  # noqa: BLE001 — delivery failure must not kill the loop
        log.warning("SMS to %s failed: %s", to, exc)
        return {"ok": False, "to": to, "error": str(exc)[:200]}


async def broadcast(body: str) -> list[dict]:
    """Send to every configured recipient; returns per-number results."""
    return [await send_one(to, body) for to in recipients()]
