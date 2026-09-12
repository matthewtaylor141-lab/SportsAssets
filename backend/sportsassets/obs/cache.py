"""The local market-state cache -- run 83.2, owner decision 2.

WHAT THIS IS, AND WHAT IT IS NOT. The primary channel records the market state
BETTOR ALREADY HOLDS at each scheduled instant. This module holds that state and
keeps it fresh. The scheduled sample is a dictionary lookup: no request, no
await on a socket, no pacing.

IT IS NOT A STREAM, AND IT IS NOT NAMED ONE. Owner decision 2 called the channel
FAST_STREAM_PATH. Established from py-clob-client 0.34.6 (fetched and read
2026-09-12): THE VENDOR SDK HAS NO WEBSOCKET CLIENT -- zero files matching
websocket/wss/ws_. There is no pushed market-data feed to subscribe to. What
exists is `POST /books`, which takes a LIST of token ids and returns a full
ladder for each. So the cache is fed by batched polling, and the channel is
LOCAL_CACHE_BATCH_POLL_PATH.

Calling a polled cache FAST_STREAM_PATH would be the same error as calling the
legacy read FASTEST_AVAILABLE_BOOK_PATH, which this project explicitly refused.
FAST_STREAM_PATH stays declared and unimplemented, reserved for a real feed.

WHAT THE VENUE GIVES US, AND WHAT IT DOES NOT. OrderBookSummary carries
`timestamp` and `hash`; there is NO sequence number. The hash proves a book
CHANGED; it cannot prove ordering, and it cannot prove no update was missed
between two polls. So continuity is STREAM_CONTINUITY_UNVERIFIED as a standing
state, not an exception -- and because every response is a FULL ladder that
replaces the token's state wholesale, there is no incremental state to lose and
no delta bootstrap to get wrong. The cost of that simplicity is that the cache
resolves nothing finer than its refresh interval, which is why every sample
carries cache_age_ms and venue_book_hash.

THE RESOLUTION LIMIT IS THE HONEST HEADLINE. Two offsets closer together than
the refresh interval return THE SAME STATE with different ages. Reporting those
as two price observations would be a flat curve by fiat -- the defect
workers/price_path.py was rewritten to remove. The schema makes that visible
rather than preventing it: the analysis compares venue_book_hash between offsets
and reports distinct_state_fraction before any price figure.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from . import clock
from .book import _levels
from .config import (cache_batch_size, cache_hot_ttl_s, cache_refresh_interval_s,
                     cache_stale_tolerance_s, clob_base)

log = logging.getLogger(__name__)


class StateValidity:
    VALID = "VALID"
    STALE = "STALE_BEYOND_TOLERANCE"
    INVALIDATED = "INVALIDATED_BY_DISCONNECT"
    NEVER = "NEVER_BOOTSTRAPPED"


class Continuity:
    UNVERIFIED = "STREAM_CONTINUITY_UNVERIFIED"
    VERIFIED = "STREAM_CONTINUITY_VERIFIED"


class FeedEvent:
    SESSION_OPENED = "SESSION_OPENED"
    BOOTSTRAP_COMPLETE = "BOOTSTRAP_COMPLETE"
    BOOTSTRAP_FAILED = "BOOTSTRAP_FAILED"
    REFRESH_OK = "REFRESH_OK"
    REFRESH_FAILED = "REFRESH_FAILED"
    STATE_INVALIDATED = "STATE_INVALIDATED"
    TOKEN_PROMOTED = "TOKEN_PROMOTED"
    TOKEN_RETIRED = "TOKEN_RETIRED"
    SESSION_CLOSED = "SESSION_CLOSED"


@dataclass
class TokenState:
    """One token's local book state, and the provenance of that state."""

    token_id: str
    received: clock.Instant
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    market_id: str | None = None
    venue_ts: str | None = None
    venue_hash: str | None = None
    invalidated: bool = False

    def validity(self, at_monotonic: float, tolerance_s: float) -> str:
        if self.invalidated:
            return StateValidity.INVALIDATED
        if at_monotonic - self.received.monotonic > tolerance_s:
            return StateValidity.STALE
        return StateValidity.VALID

    def age_ms(self, at_monotonic: float) -> float:
        return (at_monotonic - self.received.monotonic) * 1000.0


@dataclass
class Sample:
    """What a scheduled local sample found. Purely a value; writes nothing."""

    token_id: str
    state: TokenState | None
    sampled_at: clock.Instant
    validity: str
    cache_age_ms: float | None
    continuity: str = Continuity.UNVERIFIED


@dataclass
class MarketStateCache:
    """The cache, its hot set, and its own session identity.

    The hot set is bounded and TTL'd. A token is promoted when an admitted event
    names it and retires when nothing has named it for cache_hot_ttl_s. Nothing
    else is kept warm: keeping the whole board warm is what the API's
    desk-feed loop does, and it is the standing cause of the 2 GiB OOM kills.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    states: dict[str, TokenState] = field(default_factory=dict)
    hot: dict[str, float] = field(default_factory=dict)   # token -> promoted_at mono
    reconnects: int = 0
    bootstrap_status: str = "PENDING"
    refreshes_ok: int = 0
    refreshes_failed: int = 0

    # ---------------------------------------------------------------- hot set
    def promote(self, token_id: str | None, cap: int) -> bool:
        """Mark a token as wanted. Returns True if it was newly promoted."""
        if not token_id:
            return False
        now = time.monotonic()
        fresh = token_id not in self.hot
        self.hot[token_id] = now
        if len(self.hot) > cap:
            # Evict the least recently wanted, never the one just promoted.
            for tok, _ in sorted(self.hot.items(), key=lambda kv: kv[1]):
                if tok != token_id:
                    self.hot.pop(tok, None)
                    self.states.pop(tok, None)
                    break
        return fresh

    def retire_cold(self, ttl_s: float) -> list[str]:
        now = time.monotonic()
        dead = [t for t, at in self.hot.items() if now - at > ttl_s]
        for t in dead:
            self.hot.pop(t, None)
            self.states.pop(t, None)
        return dead

    # ----------------------------------------------------------- the sampler
    def sample(self, token_id: str | None, tolerance_s: float | None = None
               ) -> Sample:
        """THE SCHEDULED OPERATION. Local, synchronous, no I/O whatsoever.

        Returns a Sample even when there is nothing cached, because "we held no
        state for this token at that instant" is a finding about BETTOR's
        knowledge and is exactly as important as a price.
        """
        tol = cache_stale_tolerance_s() if tolerance_s is None else tolerance_s
        at = clock.now()
        state = self.states.get(token_id) if token_id else None
        if state is None:
            return Sample(token_id=token_id or "", state=None, sampled_at=at,
                          validity=StateValidity.NEVER, cache_age_ms=None)
        return Sample(token_id=state.token_id, state=state, sampled_at=at,
                      validity=state.validity(at.monotonic, tol),
                      cache_age_ms=state.age_ms(at.monotonic))

    def invalidate_all(self, reason: str) -> int:
        """After a failure we cannot prove the local book stayed complete.

        With no sequence number there is no way to establish that nothing was
        missed, so the state is marked invalidated rather than kept and quietly
        trusted. A sample taken against invalidated state is recorded
        CACHE_INVALID with its age -- not dropped, and not served as a reading.
        """
        n = 0
        for state in self.states.values():
            if not state.invalidated:
                state.invalidated = True
                n += 1
        if n:
            log.warning("rn1 obs cache: invalidated %d token states (%s)", n, reason)
        return n


def parse_books_response(raw) -> list[TokenState]:
    """Turn one POST /books body into token states, stamping arrival once.

    The receive instant is taken ONCE for the whole response, before parsing, so
    that JSON decoding time inflates nothing. Every token in a batch therefore
    shares an arrival instant -- which is true: they arrived in one response.
    """
    received = clock.now()
    out: list[TokenState] = []
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        token = entry.get("asset_id") or entry.get("token_id")
        if not token:
            continue
        out.append(TokenState(
            token_id=str(token),
            received=received,
            bids=_levels(entry.get("bids")),
            asks=_levels(entry.get("asks")),
            market_id=entry.get("market"),
            venue_ts=str(entry["timestamp"]) if entry.get("timestamp") else None,
            venue_hash=str(entry["hash"]) if entry.get("hash") else None,
        ))
    return out


async def refresh_once(http, cache: MarketStateCache, tokens: list[str],
                       base_url: str | None = None) -> tuple[int, str | None]:
    """One batched refresh. Returns (tokens updated, error word or None).

    NEVER RAISES INTO THE CALLER. A refresher that can kill its own loop turns a
    venue hiccup into a silently dead instrument, which is worse than a gap the
    rows can date.
    """
    if not tokens:
        return 0, None
    url = f"{base_url or clob_base()}/books"
    body = [{"token_id": t} for t in tokens]
    try:
        resp = await http.post(url, json=body)
        if resp.status_code != 200:
            cache.refreshes_failed += 1
            return 0, f"http_{resp.status_code}"
        states = parse_books_response(resp.json())
    except Exception as exc:                              # noqa: BLE001
        cache.refreshes_failed += 1
        return 0, type(exc).__name__
    for state in states:
        cache.states[state.token_id] = state
    cache.refreshes_ok += 1
    return len(states), None


async def refresher(http, cache: MarketStateCache, pacer, record_feed=None,
                    base_url: str | None = None) -> None:
    """Keep the hot set fresh. Runs forever; owns all of this channel's I/O.

    The refresher is DECOUPLED FROM THE SCHEDULE. It does not know what offsets
    are pending and never reacts to one, which is what keeps owner decision 3
    true by construction: no scheduled observation can be waiting on a request.
    A burst of events costs promotions, not requests.
    """
    base = base_url or clob_base()
    if record_feed:
        await record_feed(FeedEvent.SESSION_OPENED, tokens_tracked=len(cache.hot))
    first = True
    while True:
        try:
            cache.retire_cold(cache_hot_ttl_s())
            tokens = list(cache.hot)
            if not tokens:
                await asyncio.sleep(cache_refresh_interval_s())
                continue
            batch = cache_batch_size()
            for i in range(0, len(tokens), batch):
                await pacer.wait()
                started = time.monotonic()
                n, err = await refresh_once(http, cache, tokens[i:i + batch], base)
                if err:
                    cache.invalidate_all(f"refresh failed: {err}")
                    if record_feed:
                        await record_feed(FeedEvent.REFRESH_FAILED,
                                          detail={"error": err})
                        await record_feed(FeedEvent.STATE_INVALIDATED,
                                          detail={"error": err})
                elif record_feed:
                    await record_feed(
                        FeedEvent.REFRESH_OK, tokens_refreshed=n,
                        refresh_duration_ms=round((time.monotonic() - started) * 1000, 3))
            if first and cache.refreshes_ok:
                first = False
                cache.bootstrap_status = "COMPLETE"
                if record_feed:
                    await record_feed(FeedEvent.BOOTSTRAP_COMPLETE,
                                      tokens_tracked=len(cache.hot))
        except asyncio.CancelledError:
            raise
        except Exception:                                 # noqa: BLE001
            log.exception("rn1 obs cache: refresher cycle failed")
        await asyncio.sleep(cache_refresh_interval_s())
