"""Bounded server-side GET transport and provider adapters.

No browser credentials, redirects, order endpoints, retries inside an API read,
or speculative running clocks. Default Odds API fallback is disabled until a
server-only key AND explicit budget authorization are configured.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from .core import LEAGUES, ScoreError, parse_espn, parse_odds, finite

MAX_BODY_BYTES = 2_000_000


class ProviderError(ScoreError):
    def __init__(self, code: str, *, retry_after: float = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = max(0, retry_after)


@dataclass(frozen=True)
class Reply:
    body: Any
    status: int
    received_at: float
    observed_at: float
    payload_hash: str
    headers: dict = field(default_factory=dict, repr=False)


class Transport(Protocol):
    async def get(self, url: str, *, params: dict | None = None) -> Reply: ...


def http_observed_at(headers: dict, received_at: float) -> float:
    """Conservative cache-age accounting, without claiming an event update time."""
    h = {str(k).lower(): v for k, v in headers.items()}
    age = finite(h.get("age"))
    if age is not None and age < 0:
        raise ProviderError("HTTP_CACHE_AGE_INVALID")
    apparent_age = 0.0
    if h.get("date"):
        try:
            dt = parsedate_to_datetime(str(h["date"]))
            if dt.tzinfo is None:
                raise ValueError("timezone missing")
            stamp = dt.timestamp()
            if stamp > received_at + 5:
                raise ProviderError("HTTP_DATE_IN_FUTURE")
            apparent_age = max(0.0, received_at-stamp)
        except (ValueError, TypeError, OverflowError):
            raise ProviderError("HTTP_DATE_INVALID") from None
    return received_at - max(age or 0, apparent_age)


def retry_after_seconds(headers: dict, now: float) -> float:
    h = {str(k).lower(): v for k, v in headers.items()}
    value = h.get("retry-after")
    n = finite(value)
    if n is not None:
        return max(1.0, n)
    if value:
        try:
            return max(1.0, parsedate_to_datetime(str(value)).timestamp()-now)
        except (ValueError, TypeError, OverflowError):
            pass
    return 60.0


class HttpTransport:
    def __init__(self, *, timeout_s=6.0, max_body_bytes=MAX_BODY_BYTES):
        if not 1 <= timeout_s <= 10 or not 1024 <= max_body_bytes <= MAX_BODY_BYTES:
            raise ValueError("invalid transport bounds")
        import httpx
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s), follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"Accept": "application/json", "User-Agent": "BETTOR-DisplayScores/1.0"})
        self._deadline = timeout_s
        self._cap = max_body_bytes
        self._sem = asyncio.Semaphore(2)

    async def get(self, url: str, *, params: dict | None = None) -> Reply:
        u = urlsplit(url)
        if (u.scheme != "https" or u.hostname not in ("site.api.espn.com", "api.the-odds-api.com")
                or u.username or u.password or u.port not in (None, 443) or u.query or u.fragment):
            raise ProviderError("PROVIDER_URL_NOT_ALLOWED")
        espn_ok = u.hostname == "site.api.espn.com" and u.path.startswith("/apis/site/v2/sports/") and u.path.endswith(("/scoreboard", "/summary"))
        odds_ok = u.hostname == "api.the-odds-api.com" and u.path.startswith("/v4/sports/") and u.path.endswith("/scores")
        if not (espn_ok or odds_ok) or ".." in u.path:
            raise ProviderError("PROVIDER_PATH_NOT_ALLOWED")
        try:
            async with asyncio.timeout(self._deadline):
                async with self._sem:
                    async with self._client.stream("GET", url, params=params) as resp:
                        received = time.time()
                        headers = dict(resp.headers)
                        if resp.status_code == 429:
                            raise ProviderError("HTTP_429", retry_after=retry_after_seconds(headers, received))
                        if resp.status_code != 200:
                            raise ProviderError("HTTP_" + str(resp.status_code))
                        if "json" not in resp.headers.get("content-type", "").lower():
                            raise ProviderError("PROVIDER_NOT_JSON")
                        chunks, length = [], 0
                        async for block in resp.aiter_bytes():
                            length += len(block)
                            if length > self._cap:
                                raise ProviderError("PROVIDER_BODY_LIMIT")
                            chunks.append(block)
                        raw = b"".join(chunks)
                        try:
                            body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                        except (ValueError, UnicodeError):
                            raise ProviderError("PROVIDER_JSON_INVALID") from None
                        # Timestamp body completion, not request start.
                        received = time.time()
                        return Reply(body, 200, received, http_observed_at(headers, received),
                                     hashlib.sha256(raw).hexdigest(), headers)
        except ProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ProviderError("PROVIDER_TIMEOUT") from None
        except Exception:
            # httpx errors can include ?apiKey=...; never return/log their message.
            raise ProviderError("PROVIDER_NETWORK_ERROR") from None

    async def close(self):
        await self._client.aclose()


class ScoreClient:
    """Small normalized cache + single-flight + shared provider quota/backoff.

    Cache hits retain the original observation time. Provider backoff is isolated
    between ESPN and Odds API, not between calls made with the same account key.
    """
    def __init__(self, transport: Transport, *, clock=time.time, odds_key: str | None = None,
                 odds_enabled=False, espn_per_minute=40, odds_per_minute=4,
                 odds_max_per_hour=120, cache_size=24):
        if not 1 <= espn_per_minute <= 120 or not 1 <= odds_per_minute <= 10:
            raise ValueError("invalid request budget")
        if not 1 <= cache_size <= 64 or not 1 <= odds_max_per_hour <= 240:
            raise ValueError("invalid cache/hour budget")
        self.transport, self.clock = transport, clock
        self._odds_key = odds_key
        self.odds_enabled = odds_enabled and bool(odds_key)
        self._cap = cache_size
        self._cache: OrderedDict = OrderedDict()
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._calls = {"ESPN": deque(), "THE_ODDS_API": deque()}
        self._hourly: deque = deque()
        self._limits = {"ESPN": espn_per_minute, "THE_ODDS_API": odds_per_minute}
        self._odds_hour_cap = odds_max_per_hour
        self._backoff = {p: 0.0 for p in self._limits}
        self._failures = {p: 0 for p in self._limits}
        self._quota_exhausted = False
        self.counters = {"requests": {p: 0 for p in self._limits}, "cache_hits": 0,
                         "coalesced": 0, "errors": {}, "odds_remaining": None}

    def _reserve(self, provider: str):
        now = self.clock()
        if now < self._backoff[provider]:
            raise ProviderError("PROVIDER_BACKOFF", retry_after=self._backoff[provider]-now)
        if provider == "THE_ODDS_API":
            if not self.odds_enabled:
                raise ProviderError("ODDS_FALLBACK_NOT_ENABLED")
            if self._quota_exhausted:
                raise ProviderError("ODDS_QUOTA_EXHAUSTED")
            while self._hourly and self._hourly[0] <= now-3600:
                self._hourly.popleft()
            if len(self._hourly) >= self._odds_hour_cap:
                raise ProviderError("ODDS_HOURLY_BUDGET", retry_after=3600-(now-self._hourly[0]))
        q = self._calls[provider]
        while q and q[0] <= now-60:
            q.popleft()
        if len(q) >= self._limits[provider]:
            raise ProviderError("PROVIDER_MINUTE_BUDGET", retry_after=60-(now-q[0]))
        q.append(now)
        if provider == "THE_ODDS_API":
            self._hourly.append(now)
        self.counters["requests"][provider] += 1

    async def _cached(self, key: tuple, ttl: float, loader):
        now = self.clock()
        got = self._cache.get(key)
        if got is not None and 0 <= now-got[0] < ttl:
            self.counters["cache_hits"] += 1
            self._cache.move_to_end(key)
            return got[1]
        if key in self._inflight:
            self.counters["coalesced"] += 1
            return await asyncio.shield(self._inflight[key])
        if len(self._inflight) >= 8:
            raise ProviderError("PROVIDER_INFLIGHT_LIMIT")
        async def run():
            try:
                result = await loader()
                self._cache[key] = self.clock(), result
                self._cache.move_to_end(key)
                while len(self._cache) > self._cap:
                    self._cache.popitem(last=False)
                return result
            finally:
                self._inflight.pop(key, None)
        task = asyncio.create_task(run())
        # Consume an abandoned failure if every waiter is cancelled.
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        self._inflight[key] = task
        return await asyncio.shield(task)

    async def _load(self, provider: str, url: str, params: dict, league: str, *, summary=False):
        self._reserve(provider)
        try:
            reply = await self.transport.get(url, params=params)
            if reply.status != 200:
                raise ProviderError("HTTP_" + str(reply.status))
            if provider == "THE_ODDS_API":
                h = {str(k).lower(): v for k, v in reply.headers.items()}
                remaining = finite(h.get("x-requests-remaining"))
                if remaining is not None:
                    self.counters["odds_remaining"] = remaining
                    self._quota_exhausted = remaining <= 0
            common = dict(league=league, received_at=reply.received_at,
                          observed_at=reply.observed_at, payload_hash=reply.payload_hash)
            rows = (parse_espn(reply.body, **common, summary=summary) if provider == "ESPN"
                    else parse_odds(reply.body, **common))
            self._failures[provider] = 0
            return rows
        except asyncio.CancelledError:
            raise
        except ScoreError as exc:
            code = str(exc)
            self.counters["errors"][code] = self.counters["errors"].get(code, 0)+1
            self._failures[provider] = min(6, self._failures[provider]+1)
            wait = getattr(exc, "retry_after", 0) or min(120, 5 * 2**(self._failures[provider]-1))
            # Never shorten a server's Retry-After.
            self._backoff[provider] = self.clock()+wait
            raise ProviderError(code, retry_after=wait) from None

    async def espn_board(self, league: str, date: str):
        if league not in LEAGUES or not __import__("re").fullmatch(r"\d{8}", date):
            raise ProviderError("SCORE_REQUEST_SCOPE_INVALID")
        sport, slug, _ = LEAGUES[league]
        params = {"dates": date, "limit": 1000}
        if league in ("NCAAF", "NCAAB", "NCAAW"):
            params["groups"] = 80 if league == "NCAAF" else 50
        return await self._cached(("ESPN", league, date), 8.0, lambda: self._load(
            "ESPN", f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/scoreboard", params, league))

    async def espn_summary(self, league: str, provider_event_id: str):
        if league not in LEAGUES or not str(provider_event_id).isdigit():
            raise ProviderError("SCORE_SUMMARY_ID_INVALID")
        sport, slug, _ = LEAGUES[league]
        return await self._cached(("ESPN_SUMMARY", league, provider_event_id), 8.0, lambda: self._load(
            "ESPN", f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{slug}/summary",
            {"event": provider_event_id}, league, summary=True))

    async def odds_scores(self, league: str):
        if league not in LEAGUES:
            raise ProviderError("SCORE_LEAGUE_UNSUPPORTED")
        return await self._cached(("THE_ODDS_API", league), 30.0, lambda: self._load(
            "THE_ODDS_API", f"https://api.the-odds-api.com/v4/sports/{LEAGUES[league][2]}/scores",
            {"apiKey": self._odds_key, "daysFrom": 3, "dateFormat": "iso"}, league))

    async def close(self):
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        close = getattr(self.transport, "close", None)
        if close:
            await close()
