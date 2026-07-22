"""Polymarket adapter — shadow mode (read-only REST; CLOB WS upgrade later).

Market discovery via Gamma (drift rules per the audited puller: explicit
limit, closed=true for settlements); books via CLOB REST. place() refuses to
run — this venue config is mode: shadow.
"""

from __future__ import annotations

import json
import logging
import re
import time

import requests

from .base import BookLevel, FillIntent, MarketBook, VenueAdapter
from .mapper import VenueMarket

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
_PREFIX = re.compile(r"^([a-z0-9]+)-")


class PolymarketAdapter(VenueAdapter):
    name = "polymarket"

    def __init__(self) -> None:
        self._sess = requests.Session()
        self.last_census: dict[str, int] = {}  # slug prefix -> open markets seen

    # ── discovery ────────────────────────────────────────────────────
    def discover_markets(self, league_codes: set[str], pages: int = 30) -> list[VenueMarket]:
        """Open game markets whose event slug starts with an allowlisted league
        code. Records a census of ALL prefixes seen so the telemetry shows
        what leagues exist on the venue right now vs. what we allow."""
        census: dict[str, int] = {}
        out: list[VenueMarket] = []
        for page in range(pages):
            resp = self._sess.get(
                f"{GAMMA}/markets",
                params={"closed": "false", "limit": 100, "offset": page * 100},
                timeout=20,
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for m in batch:
                events = m.get("events") or []
                slug = (events[0].get("slug") if events else m.get("eventSlug")) or m.get("slug") or ""
                match = _PREFIX.match(slug.lower())
                code = match.group(1) if match else None
                if code:
                    census[code] = census.get(code, 0) + 1
                if code not in league_codes:
                    continue
                tokens = m.get("clobTokenIds") or "[]"
                outcomes = m.get("outcomes") or "[]"
                tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
                outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
                if not tokens:
                    continue
                out.append(VenueMarket(
                    market_id=m.get("conditionId", ""),
                    title=m.get("question") or "",
                    league_code=code,
                    outcome_tokens={
                        str(outcomes[i]) if i < len(outcomes) else f"#{i}": str(t)
                        for i, t in enumerate(tokens)
                    },
                ))
            if len(batch) < 100:
                break
        self.last_census = dict(sorted(census.items(), key=lambda kv: -kv[1])[:20])
        return out

    # ── books ────────────────────────────────────────────────────────
    def get_book(self, market_id: str, token_id: str) -> MarketBook | None:
        resp = self._sess.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        if resp.status_code != 200:
            return None
        d = resp.json()
        parse = lambda side: sorted(  # noqa: E731
            (BookLevel(float(level["price"]), float(level["size"])) for level in d.get(side) or []),
            key=lambda level: level.price,
        )
        asks = parse("asks")
        bids = sorted(parse("bids"), key=lambda level: -level.price)
        return MarketBook(venue=self.name, market_id=market_id, outcome_id=token_id,
                          bids=bids, asks=asks, ts=time.time())

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 shadow uses REST polling; CLOB WS is the latency upgrade")

    def taker_fee(self, price: float) -> float:
        return 0.0

    async def place(self, intent: FillIntent):
        raise RuntimeError("polymarket adapter is in shadow mode — placing orders is disabled")

    async def settlements(self):
        raise NotImplementedError("grader pulls settlements in batch; see shadow/grader.py")
