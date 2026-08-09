"""Gamma markets API client + hot token→market metadata cache.

The cache is the reason Path A enrichment is a dictionary lookup instead of an
API round trip: the metadata refresher worker continuously upserts active
sports markets into Postgres AND into a Redis hash, keyed by CLOB token id.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .bus import get_redis
from .config import settings
from .db import get_pool
from .sports import classify

log = logging.getLogger(__name__)

REDIS_TOKEN_HASH = "meta:token"  # tokenId -> json blob


def parse_market(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a Gamma market payload into our metadata record."""
    condition_id = raw.get("conditionId") or raw.get("condition_id")
    if not condition_id:
        return None

    tokens_raw = raw.get("clobTokenIds") or raw.get("clob_token_ids") or "[]"
    outcomes_raw = raw.get("outcomes") or "[]"
    if isinstance(tokens_raw, str):
        tokens = json.loads(tokens_raw or "[]")
    else:
        tokens = tokens_raw
    if isinstance(outcomes_raw, str):
        outcomes = json.loads(outcomes_raw or "[]")
    else:
        outcomes = outcomes_raw

    tag_labels: list[str] = []
    for tag in raw.get("tags") or []:
        if isinstance(tag, dict):
            tag_labels.append(str(tag.get("label") or tag.get("slug") or ""))
        else:
            tag_labels.append(str(tag))
    events = raw.get("events") or []
    event = events[0] if events else {}

    resolved_prices = None
    prices_raw = raw.get("outcomePrices") or raw.get("outcome_prices")
    if raw.get("closed") and prices_raw:
        try:
            prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            resolved_prices = [float(p) for p in prices]
            # Only treat as resolved when prices have collapsed to 0/1.
            if not all(p in (0.0, 1.0) for p in resolved_prices):
                resolved_prices = None
        except (ValueError, TypeError):
            resolved_prices = None

    # Settlement date: actual resolution time (closedTime), else scheduled
    # end date clamped to now — realizations must land on the real settle
    # date, never on "whenever we happened to fetch it".
    resolved_time = None
    for key in ("closedTime", "closed_time", "endDate", "end_date_iso"):
        val = raw.get(key)
        if val and isinstance(val, str):
            try:
                parsed = datetime.fromisoformat(val.replace("Z", "+00:00").replace(" ", "T"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                resolved_time = min(parsed, datetime.now(tz=timezone.utc))
                break
            except ValueError:
                continue

    return {
        "condition_id": condition_id,
        "title": raw.get("question") or raw.get("title"),
        "slug": raw.get("slug"),
        "event_slug": event.get("slug") or raw.get("eventSlug"),
        "event_title": event.get("title"),
        "sport": classify(
            tag_labels,
            raw.get("slug") or "",
            raw.get("question") or "",
            event_slug=event.get("slug") or raw.get("eventSlug") or "",
        ),
        "tags": tag_labels,
        "closed": bool(raw.get("closed")),
        "resolved": resolved_prices is not None,
        "resolved_time": resolved_time,
        "resolved_prices": resolved_prices,
        "tokens": [
            {"token_id": str(t), "outcome": outcomes[i] if i < len(outcomes) else None, "outcome_index": i}
            for i, t in enumerate(tokens)
        ],
    }


# Param spellings for "open markets", tried in order; the first that the API
# accepts is cached for the session. (The Gamma API 422s on params it no
# longer recognizes, so we self-discover instead of hardcoding one guess.)
_OPEN_MARKET_PARAM_VARIANTS: list[dict[str, str]] = [
    {"closed": "false", "active": "true"},
    {"closed": "false"},
    {"active": "true"},
]


class GammaClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=settings().gamma_api_base, timeout=15)
        self._open_params: dict[str, str] | None = None

    async def fetch_markets(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        resp = await self._http.get("/markets", params=params)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} for /markets {params}: {resp.text[:200]}",
                request=resp.request,
                response=resp,
            )
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    async def fetch_tag_id(self, slug: str) -> int | None:
        """Numeric tag id for a tag slug ('mlb', 'tennis'), or None if the
        deployment doesn't serve /tags/slug/{slug}. Tag-filtered /markets
        queries are the only way to reach a sport's slate without paging
        through thousands of 15-minute crypto expiries first."""
        resp = await self._http.get(f"/tags/slug/{slug}")
        if resp.status_code >= 400:
            return None
        data = resp.json() or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        try:
            return int(data.get("id"))
        except (TypeError, ValueError):
            return None

    async def _resolve_open_params(self) -> dict[str, str]:
        if self._open_params is not None:
            return self._open_params
        failures = []
        for variant in _OPEN_MARKET_PARAM_VARIANTS:
            try:
                await self.fetch_markets({**variant, "limit": 1, "offset": 0})
                self._open_params = variant
                log.info("gamma open-markets params resolved: %s", variant)
                return variant
            except httpx.HTTPStatusError as exc:
                failures.append(str(exc))
        raise RuntimeError("no gamma param variant accepted: " + " | ".join(failures))

    async def fetch_active_sports_markets(
        self, page_size: int = 100, max_pages: int = 50
    ) -> list[dict[str, Any]]:
        """All open markets, paged (bounded); classification filters non-sports later.

        A failure on a later page keeps the pages already fetched — a partial
        cache refresh beats aborting the whole metadata cycle.
        """
        base = await self._resolve_open_params()
        out: list[dict[str, Any]] = []
        for page in range(max_pages):
            try:
                batch = await self.fetch_markets(
                    {**base, "limit": page_size, "offset": page * page_size}
                )
            except httpx.HTTPStatusError as exc:
                log.warning("open-market paging stopped at page %s (%s); keeping %s markets",
                            page, exc, len(out))
                self._open_params = None  # re-probe variants next cycle
                return out
            out.extend(batch)
            if len(batch) < page_size:
                return out
        log.warning("open-market paging hit the %s-page cap; cache may be partial", max_pages)
        return out

    async def fetch_by_condition_ids(self, condition_ids: list[str]) -> list[dict[str, Any]]:
        """Batch metadata lookup. Gamma drift facts (measured, July 2026):
        condition_ids must be REPEATED params with an explicit limit (the API
        silently caps at 20 rows otherwise), and closed markets are excluded
        unless closed=true — so query closed=true first, then retry the
        remainder with the default filter for still-open markets."""
        out: list[dict[str, Any]] = []
        for i in range(0, len(condition_ids), 40):
            chunk = condition_ids[i : i + 40]
            try:
                params = [("condition_ids", c) for c in chunk]
                closed_batch = await self._fetch_markets_params(
                    params + [("limit", str(len(chunk))), ("closed", "true")]
                )
                out.extend(closed_batch)
                got = {m.get("conditionId") or m.get("condition_id") for m in closed_batch}
                rest = [c for c in chunk if c not in got]
                if rest:
                    out.extend(await self._fetch_markets_params(
                        [("condition_ids", c) for c in rest] + [("limit", str(len(rest)))]
                    ))
            except httpx.HTTPStatusError as exc:
                log.warning("gamma condition_ids chunk failed: %s", exc)
        return out

    async def _fetch_markets_params(self, params: list[tuple[str, str]]) -> list[dict[str, Any]]:
        resp = await self._http.get("/markets", params=params)
        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} for /markets: {resp.text[:200]}",
                request=resp.request, response=resp,
            )
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    async def close(self) -> None:
        await self._http.aclose()


async def upsert_market(meta: dict[str, Any]) -> None:
    """Persist a normalized market record and mirror tokens into Redis."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO markets (condition_id, title, slug, event_slug, event_title, sport,
                                 tags, closed, resolved, resolved_prices, resolved_at, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10::jsonb,
                    CASE WHEN $9 THEN COALESCE($11, now()) ELSE NULL END, now())
            ON CONFLICT (condition_id) DO UPDATE SET
                title = EXCLUDED.title, slug = EXCLUDED.slug,
                event_slug = EXCLUDED.event_slug, event_title = EXCLUDED.event_title,
                sport = EXCLUDED.sport, tags = EXCLUDED.tags,
                closed = EXCLUDED.closed,
                resolved = markets.resolved OR EXCLUDED.resolved,
                resolved_prices = COALESCE(EXCLUDED.resolved_prices, markets.resolved_prices),
                resolved_at = COALESCE(markets.resolved_at, EXCLUDED.resolved_at),
                updated_at = now()
            """,
            meta["condition_id"],
            meta["title"],
            meta["slug"],
            meta["event_slug"],
            meta["event_title"],
            meta["sport"],
            json.dumps(meta["tags"]),
            meta["closed"],
            meta["resolved"],
            json.dumps(meta["resolved_prices"]) if meta["resolved_prices"] is not None else None,
            meta.get("resolved_time"),
        )
        for tok in meta["tokens"]:
            await conn.execute(
                """
                INSERT INTO market_tokens (token_id, condition_id, outcome, outcome_index)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (token_id) DO UPDATE
                    SET outcome = EXCLUDED.outcome, outcome_index = EXCLUDED.outcome_index
                """,
                tok["token_id"],
                meta["condition_id"],
                tok["outcome"],
                tok["outcome_index"],
            )

    # Mirror into the Redis hot cache for O(1) enrichment.
    r = get_redis()
    for tok in meta["tokens"]:
        await r.hset(
            REDIS_TOKEN_HASH,
            tok["token_id"],
            json.dumps(
                {
                    "condition_id": meta["condition_id"],
                    "title": meta["title"],
                    "slug": meta["slug"],
                    "event_slug": meta["event_slug"],
                    "event_title": meta["event_title"],
                    "sport": meta["sport"],
                    "outcome": tok["outcome"],
                    "outcome_index": tok["outcome_index"],
                }
            ),
        )


async def lookup_token(token_id: str) -> dict[str, Any] | None:
    """Hot-path enrichment lookup: Redis first, Postgres fallback."""
    raw = await get_redis().hget(REDIS_TOKEN_HASH, str(token_id))
    if raw:
        return json.loads(raw)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT mt.token_id, mt.outcome, mt.outcome_index, m.condition_id, m.title,
               m.slug, m.event_slug, m.event_title, m.sport
        FROM market_tokens mt JOIN markets m USING (condition_id)
        WHERE mt.token_id = $1
        """,
        str(token_id),
    )
    return dict(row) if row else None
