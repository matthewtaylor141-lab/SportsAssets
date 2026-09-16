"""CLOB API client — independent source for market metadata and resolution.

GET {clob}/markets/{condition_id} returns the market with its tokens, each
carrying a `winner` flag once resolved. Used as the fallback resolution
source when Gamma is unavailable or lags — settlement must not depend on a
single upstream endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .sports import classify

log = logging.getLogger(__name__)


def parse_clob_market(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a CLOB market payload into our metadata-record shape."""
    condition_id = raw.get("condition_id") or raw.get("conditionId")
    if not condition_id:
        return None
    tokens_raw = raw.get("tokens") or []
    winners = [bool(t.get("winner")) for t in tokens_raw]
    resolved = any(winners)
    tags = [str(t) for t in (raw.get("tags") or [])]
    title = raw.get("question") or ""
    slug = raw.get("market_slug") or ""
    resolved_time = None
    for key in ("end_date_iso", "game_start_time"):
        val = raw.get(key)
        if val and isinstance(val, str):
            try:
                parsed = datetime.fromisoformat(val.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                resolved_time = min(parsed, datetime.now(tz=timezone.utc))
                break
            except ValueError:
                continue
    return {
        "condition_id": condition_id,
        "title": title or None,
        "resolved_time": resolved_time,
        "slug": slug or None,
        "event_slug": None,   # CLOB payloads don't carry the event grouping
        "event_title": None,
        "sport": classify(tags, slug, title),
        "tags": tags,
        "closed": bool(raw.get("closed")),
        "resolved": resolved,
        "resolved_prices": [1.0 if w else 0.0 for w in winners] if resolved else None,
        "tokens": [
            {"token_id": str(t.get("token_id")), "outcome": t.get("outcome"), "outcome_index": i}
            for i, t in enumerate(tokens_raw)
            if t.get("token_id")
        ],
    }


async def fetch_clob_market(
    http: httpx.AsyncClient, condition_id: str
) -> dict[str, Any] | None:
    try:
        resp = await http.get(f"/markets/{condition_id}")
        if resp.status_code != 200:
            return None
        return parse_clob_market(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("clob fetch failed for %s: %s", condition_id, exc)
        return None
