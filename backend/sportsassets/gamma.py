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


#: THE DEEPEST OFFSET THE API SERVES (R30A runtime, 2026-10-04). The open-
#: market paging asked for up to 50 pages and the API refused page 21 on
#: every metadata cycle: `GET /markets?...&limit=100&offset=2100 -> 422
#: {"type":"validation error","error":"offset too large, use /markets/keyset
#: for deeper pagination"}` while offset=2000 returned 200 (render-ops logs,
#: workers, 20:02:11 / 20:04:33 / 20:06:57Z, run 37230932776) -- a WARNING
#: and a refused request every ~2.4 minutes, plus a needless re-probe of
#: the param variants (the 422 reset `_open_params`). The paginator now
#: stops at the offset the API itself states it serves; reaching it with a
#: full page means the catalogue is TRUNCATED, which is recorded by name in
#: `last_paging` (and rides the metadata heartbeat), never hidden. (The
#: keyset endpoint the error names was not used then: its contract was not
#: verified. It is now -- see KEYSET_PATH.)
GAMMA_MAX_OFFSET = 2000
#: the API's own words when an offset is past what it serves
OFFSET_REFUSAL_TEXT = "offset too large"

# ── THE WHOLE OPEN CATALOGUE, BY THE VENUE'S DOCUMENTED CURSOR (P0, 2026-10-09)
#
# PRODUCTION (render-ops 37933193326 / 37933204766, sportsassets-workers,
# 2026-10-09): every metadata cycle reads `{'pages': 21, 'markets': 2100,
# 'stopped': 'OFFSET_CEILING', 'max_offset': 2000, 'truncated': True}` and
# keeps 233 sports markets, with the heartbeat status 'ok'. Offset paging
# reaches the first 2,100 open markets in the listing's order and never the
# rest -- the same 2,100 every minute -- so a sports market listed after them
# reached the metadata cache only through a trade's on-miss lookup.
#
# THE VENUE DOCUMENTS THE WAY PAST IT. The API's own refusal names it ("use
# /markets/keyset for deeper pagination"), and the public reference
# (docs.polymarket.com/api-reference/markets/list-markets-keyset-pagination,
# read 2026-10-09) specifies it: GET /markets/keyset, `limit` 1..100,
# `closed` (default false), `after_cursor` = the previous response's
# `next_cursor`; the response is {"markets": [...], "next_cursor": "..."}
# where next_cursor is "Present only when the number of returned markets
# equals the effective limit. Omitted on the last page"; `offset` is
# rejected with 422, as are an invalid cursor or filter; 503 when keyset
# pagination is not configured. (The official SDK's own market discovery
# runs on this endpoint: research/RUN836B1_CURRENT_SURFACE_AUDIT.md row 11.)
#
# WITHIN THE SAME REQUEST BUDGET. The offset walk spends
# GAMMA_MAX_OFFSET // page_size + 1 = 21 requests a cycle. The keyset walk
# spends AT MOST THE SAME 21 a cycle and keeps its cursor between cycles: a
# ROTATION through the whole open catalogue, each cycle continuing where the
# last stopped, every open market read once per rotation, the rotation's
# length (cycles, pages, seconds) published on the heartbeat. No request is
# added and the cadence is unchanged. Only documented parameters are sent
# (closed, limit, after_cursor).
#
# AND IF THE VENUE DOES NOT SERVE IT AS DOCUMENTED (a 404 / 405 / 501 / 503,
# a 422 to the first page, a body that is not the documented envelope), the
# cycle's remaining budget runs the offset walk exactly as before -- its
# truncation named -- the refusal is recorded (`keyset_unavailable`), and
# the keyset is asked again only after KEYSET_RETRY_AFTER_S, so a missing
# endpoint costs one request per half hour, inside the cycle's budget.
KEYSET_PATH = "/markets/keyset"
#: the documented maximum page size
KEYSET_PAGE_LIMIT = 100
#: documented filters only: the open catalogue, as the offset walk reads it
KEYSET_PARAMS = {"closed": "false"}
KEYSET_RETRY_AFTER_S = 1800.0
ROTATION_VERSION = "GAMMA_OPEN_MARKETS_KEYSET_ROTATION_V1"
#: how a keyset cycle ended
K_ROTATION_COMPLETE = "ROTATION_COMPLETE"
K_CYCLE_BUDGET_SPENT = "CYCLE_BUDGET_SPENT_ROTATION_CONTINUES"
K_CURSOR_REFUSED = "CURSOR_REFUSED_ROTATION_RESTARTED"
K_CURSOR_STUCK = "CURSOR_DID_NOT_ADVANCE_ROTATION_RESTARTED"
K_ENVELOPE_UNREADABLE = "KEYSET_ENVELOPE_UNREADABLE"
#: the stops after which the rotation continues normally (anything else is a
#: cycle that ended on a failure: truncated, named)
K_CLEAN_STOPS = frozenset({K_ROTATION_COMPLETE, K_CYCLE_BUDGET_SPENT})


class _KeysetUnavailable(Exception):
    """The keyset endpoint is not served as documented: the cycle falls back
    to the offset walk. `requests` is what the attempt spent."""

    def __init__(self, why: str, *, status=None, requests: int = 1):
        super().__init__(why)
        self.why, self.status, self.requests = why, status, int(requests)


class GammaClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(base_url=settings().gamma_api_base, timeout=15)
        self._open_params: dict[str, str] | None = None
        #: how the last open-market paging ended (pages, markets, why)
        self.last_paging: dict = {}
        self._init_rotation()

    def _init_rotation(self) -> None:
        """The keyset rotation's state (kept between cycles, per process)."""
        self._rotation: dict = {"cursor": None, "started_at": None,
                                "pages": 0, "markets": 0, "cycles": 0,
                                "number": 0}
        #: the last rotation that reached the documented last page
        self.last_rotation_complete: dict | None = None
        self._keyset_retry_at = 0.0
        #: why the keyset endpoint was last found unavailable, or None
        self.keyset_unavailable: dict | None = None

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
        # never ask for an offset the API has said it does not serve
        pages = min(max_pages, GAMMA_MAX_OFFSET // page_size + 1)
        for page in range(pages):
            try:
                batch = await self.fetch_markets(
                    {**base, "limit": page_size, "offset": page * page_size}
                )
            except httpx.HTTPStatusError as exc:
                resp = getattr(exc, "response", None)
                if (resp is not None and resp.status_code == 422
                        and OFFSET_REFUSAL_TEXT in str(exc)):
                    # the API's offset ceiling moved below ours: end of the
                    # pages it serves, NOT a param-variant failure (no
                    # re-probe), counted and named
                    self.last_paging = {
                        "pages": page, "markets": len(out),
                        "stopped": "OFFSET_REFUSED_BY_API",
                        "truncated": True, "offset": page * page_size}
                    log.warning("open-market paging: the API refused offset "
                                "%s (%s); keeping %s markets", page * page_size,
                                OFFSET_REFUSAL_TEXT, len(out))
                    return out
                log.warning("open-market paging stopped at page %s (%s); keeping %s markets",
                            page, exc, len(out))
                self.last_paging = {"pages": page, "markets": len(out),
                                    "stopped": "HTTP_%s" % getattr(
                                        resp, "status_code", "ERROR"),
                                    "truncated": True}
                self._open_params = None  # re-probe variants next cycle
                return out
            out.extend(batch)
            if len(batch) < page_size:
                self.last_paging = {"pages": page + 1, "markets": len(out),
                                    "stopped": "SHORT_PAGE",
                                    "truncated": False}
                return out
        if pages < max_pages:
            # the API's offset ceiling with a FULL last page: more open
            # markets exist than offset paging can reach (keyset needed)
            self.last_paging = {"pages": pages, "markets": len(out),
                                "stopped": "OFFSET_CEILING",
                                "max_offset": GAMMA_MAX_OFFSET,
                                "truncated": True}
            return out
        log.warning("open-market paging hit the %s-page cap; cache may be partial", max_pages)
        self.last_paging = {"pages": pages, "markets": len(out),
                            "stopped": "PAGE_CAP", "truncated": True}
        return out

    def _rotation_state(self) -> dict:
        if not hasattr(self, "_rotation"):
            self._init_rotation()
        return self._rotation

    async def fetch_open_markets(self, page_size: int = 100,
                                 max_pages: int = 50) -> list[dict[str, Any]]:
        """THIS CYCLE'S SHARE OF THE WHOLE OPEN CATALOGUE (see KEYSET_PATH):
        the keyset rotation, at most the offset walk's per-cycle request
        budget, continuing from the cursor the previous cycle left; or, when
        the keyset is not served as documented, the offset walk within what
        is left of that budget, its truncation named. `last_paging` says
        which, and how the cycle ended."""
        import time as _t

        self._rotation_state()
        full = max(1, min(int(max_pages),
                          GAMMA_MAX_OFFSET // int(page_size) + 1))
        budget = full
        if _t.time() >= self._keyset_retry_at:
            try:
                return await self._keyset_cycle(budget,
                                                 min(int(page_size),
                                                     KEYSET_PAGE_LIMIT))
            except _KeysetUnavailable as exc:
                self._keyset_retry_at = _t.time() + KEYSET_RETRY_AFTER_S
                self.keyset_unavailable = {
                    "why": exc.why, "status": exc.status,
                    "at": _t.time(), "retry_after_s": KEYSET_RETRY_AFTER_S}
                log.warning("gamma keyset not served as documented (%s); the "
                            "offset walk runs this cycle, truncation named",
                            exc.why)
                budget = max(0, budget - exc.requests)
        if budget <= 0:
            self.last_paging = {"mode": "OFFSET_FALLBACK", "pages": 0,
                                "markets": 0, "stopped": "CYCLE_BUDGET_SPENT",
                                "truncated": True,
                                "keyset_unavailable": self.keyset_unavailable}
            return []
        # the whole per-cycle budget left: the offset walk exactly as before
        # (its own ceiling names the cut); a budget shortened by this cycle's
        # keyset request: that many pages and no more
        out = await self.fetch_active_sports_markets(
            page_size=page_size,
            max_pages=int(max_pages) if budget >= full else budget)
        self.last_paging = dict(self.last_paging, mode="OFFSET_FALLBACK",
                                offset_walk_page_budget=budget,
                                keyset_unavailable=self.keyset_unavailable,
                                catalogue_complete=not self.last_paging.get(
                                    "truncated", True))
        return out

    async def _keyset_cycle(self, budget: int, limit: int) -> list[dict[str, Any]]:
        """Up to `budget` keyset pages from the rotation's cursor. Raises
        _KeysetUnavailable when the first page of a rotation shows the
        endpoint is not served as documented."""
        import time as _t

        rot = self._rotation
        if rot["started_at"] is None:
            rot.update(started_at=_t.time(), pages=0, markets=0, cycles=0,
                       cursor=None, number=rot["number"] + 1)
        out: list[dict[str, Any]] = []
        requests = 0
        pages = 0
        stopped = K_CYCLE_BUDGET_SPENT
        error = None
        while requests < budget:
            params = dict(KEYSET_PARAMS, limit=str(limit))
            sent_cursor = rot["cursor"]
            if sent_cursor:
                params["after_cursor"] = sent_cursor
            requests += 1
            fresh_rotation = rot["pages"] == 0 and not sent_cursor
            try:
                resp = await self._http.get(KEYSET_PATH, params=params)
            except httpx.HTTPError as exc:
                # no answer: the cursor stays, the next cycle resumes from it
                stopped, error = "TRANSPORT_%s" % type(exc).__name__, str(exc)[:160]
                break
            status = int(getattr(resp, "status_code", 0) or 0)
            if status >= 400:
                text = str(getattr(resp, "text", "") or "")[:200]
                if fresh_rotation and status in (404, 405, 422, 501, 503):
                    raise _KeysetUnavailable("HTTP_%d: %s" % (status, text),
                                             status=status, requests=requests)
                if sent_cursor and status == 422:
                    # the documented answer to an invalid cursor: the rotation
                    # restarts from the beginning next cycle, named
                    self._restart_rotation()
                    stopped, error = K_CURSOR_REFUSED, text
                    break
                # any other refusal: keep what this cycle read, keep the cursor
                stopped, error = "HTTP_%d" % status, text
                break
            try:
                body = resp.json()
            except ValueError:
                body = None
            if not (isinstance(body, dict)
                    and isinstance(body.get("markets"), list)):
                if fresh_rotation:
                    raise _KeysetUnavailable(
                        "%s: %s" % (K_ENVELOPE_UNREADABLE,
                                    type(body).__name__),
                        status=status, requests=requests)
                stopped = K_ENVELOPE_UNREADABLE
                break
            batch = [m for m in body["markets"] if isinstance(m, dict)]
            out.extend(batch)
            pages += 1
            rot["pages"] += 1
            rot["markets"] += len(batch)
            nxt = body.get("next_cursor")
            if not nxt:
                # the documented last page: next_cursor omitted
                rot["cycles"] += 1
                self.last_rotation_complete = {
                    "rotation": rot["number"], "pages": rot["pages"],
                    "markets": rot["markets"], "cycles": rot["cycles"],
                    "started_at": rot["started_at"], "completed_at": _t.time(),
                    "seconds": round(_t.time() - rot["started_at"], 1)}
                self._restart_rotation()
                stopped = K_ROTATION_COMPLETE
                break
            if nxt == sent_cursor:
                # a cursor that does not move would read one page forever
                self._restart_rotation()
                stopped = K_CURSOR_STUCK
                break
            rot["cursor"] = nxt
        else:
            stopped = K_CYCLE_BUDGET_SPENT
        if stopped != K_ROTATION_COMPLETE and rot["started_at"] is not None:
            rot["cycles"] += 1
        self.keyset_unavailable = None
        self.last_paging = {
            "mode": "KEYSET_ROTATION", "version": ROTATION_VERSION,
            "pages": pages, "markets": len(out), "requests": requests,
            "request_budget": budget, "stopped": stopped, "error": error,
            # a cycle reads part of the catalogue BY DESIGN; it is truncated
            # only when it ended on a failure
            "truncated": stopped not in K_CLEAN_STOPS,
            # the rotation this cycle continues (None when it just ended or
            # restarted: the next cycle starts a new one from the beginning)
            "rotation": ({"number": rot["number"], "pages": rot["pages"],
                          "markets": rot["markets"], "cycles": rot["cycles"],
                          "started_at": rot["started_at"],
                          "cursor_held": bool(rot["cursor"])}
                         if rot["started_at"] is not None else None),
            "last_complete_rotation": self.last_rotation_complete,
            # the whole catalogue has been read at least once, by a rotation
            # that reached the venue's documented last page
            "catalogue_complete": self.last_rotation_complete is not None}
        return out

    def _restart_rotation(self) -> None:
        rot = self._rotation
        rot.update(cursor=None, started_at=None)

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


# On-miss live fetch (2026-08-22): the metadata refresher sweeps SPORTS
# markets only, so chain-detected trades in crypto markets never
# enriched — and now that chain wins every detection race, the Data-API
# duplicate that used to carry the slug is dedupe-dropped, so a new
# market's slug would never arrive at all. On a cache+DB miss this
# fetches the one market from the gamma API by clob token id, persists
# it through the normal upsert (markets + market_tokens + Redis), and
# returns the mapping. Dead/unknown tokens are negative-cached for
# 10 minutes so 642 known-dead tokens can't turn this into a hammer.
_live_client: GammaClient | None = None
_token_miss_cache: dict[str, float] = {}
_TOKEN_MISS_TTL_S = 600.0


async def lookup_token_live(token_id: str) -> dict[str, Any] | None:
    """lookup_token, plus a one-market gamma fetch on miss."""
    import time as _t

    global _live_client
    tid = str(token_id)
    found = await lookup_token(tid)
    if found is not None:
        return found
    miss_at = _token_miss_cache.get(tid)
    if miss_at is not None and _t.time() - miss_at < _TOKEN_MISS_TTL_S:
        return None
    if _live_client is None:
        _live_client = GammaClient()
    try:
        raws = await _live_client.fetch_markets(
            {"clob_token_ids": tid, "limit": 1})
    except Exception as exc:  # noqa: BLE001 — enrichment stays best-effort
        log.warning("live token fetch failed for %s: %s", tid, exc)
        return None
    meta = parse_market(raws[0]) if raws else None
    if meta is None:
        if len(_token_miss_cache) > 4096:
            _token_miss_cache.clear()
        _token_miss_cache[tid] = _t.time()
        return None
    await upsert_market(meta)
    return await lookup_token(tid)
