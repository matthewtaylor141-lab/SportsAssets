"""Kalshi adapter — shadow mode (public market-data REST; auth only needed
for live orders, which shadow mode never places).

Discovery: GET /events?series_ticker=...&with_nested_markets=true — one
VenueMarket per game event, outcome names from each market's yes side.
Books: GET /markets/{ticker}/orderbook — resting YES/NO bids in cents; the
executable YES ask is (100 - best NO bid).
Fees: taker ≈ 0.07 * p * (1-p) per contract (maker-first is the live-mode
answer; shadow logs taker economics so the gate is conservative).
"""

from __future__ import annotations

import logging
import os
import time

import requests

from .base import BookLevel, FillIntent, MarketBook, VenueAdapter
from .mapper import VenueMarket

log = logging.getLogger(__name__)

BASE = os.environ.get("EDGE_KALSHI_BASE", "https://api.elections.kalshi.com/trade-api/v2")

# league code -> Kalshi series ticker, env-overridable:
#   EDGE_KALSHI_SERIES="nba:KXNBAGAME,epl:KXEPLGAME"
_DEFAULT_SERIES = {"nba": "KXNBAGAME", "wnba": "KXWNBAGAME", "epl": "KXEPLGAME"}


def _series_map() -> dict[str, str]:
    raw = os.environ.get("EDGE_KALSHI_SERIES", "")
    if not raw:
        return dict(_DEFAULT_SERIES)
    out = {}
    for pair in raw.split(","):
        if ":" in pair:
            code, series = pair.split(":", 1)
            out[code.strip()] = series.strip()
    return out or dict(_DEFAULT_SERIES)


class KalshiAdapter(VenueAdapter):
    name = "kalshi"

    def __init__(self) -> None:
        self._sess = requests.Session()

    def discover_markets(self, league_codes: set[str]) -> list[VenueMarket]:
        out: list[VenueMarket] = []
        for code, series in _series_map().items():
            if code not in league_codes:
                continue
            cursor = ""
            for _ in range(10):  # bounded paging
                try:
                    resp = self._sess.get(
                        f"{BASE}/events",
                        params={"series_ticker": series, "status": "open",
                                "with_nested_markets": "true", "limit": 100,
                                **({"cursor": cursor} if cursor else {})},
                        timeout=15,
                    )
                    if resp.status_code != 200:
                        log.info("kalshi discovery %s -> HTTP %s (series unavailable?)",
                                 series, resp.status_code)
                        break
                    data = resp.json()
                except (requests.RequestException, ValueError) as exc:
                    log.warning("kalshi discovery failed for %s: %s", series, exc)
                    break
                for ev in data.get("events") or []:
                    outcomes = {}
                    for m in ev.get("markets") or []:
                        team = m.get("yes_sub_title") or m.get("subtitle") or m.get("ticker", "")
                        if team and m.get("ticker"):
                            outcomes[team] = m["ticker"]
                    if len(outcomes) >= 2:
                        out.append(VenueMarket(
                            market_id=ev.get("event_ticker", ""),
                            title=ev.get("title", ""),
                            league_code=code,
                            outcome_tokens=outcomes,
                        ))
                cursor = data.get("cursor") or ""
                if not cursor:
                    break
        return out

    def get_book(self, market_id: str, market_ticker: str) -> MarketBook | None:
        try:
            resp = self._sess.get(f"{BASE}/markets/{market_ticker}/orderbook", timeout=10)
            if resp.status_code != 200:
                return None
            ob = (resp.json() or {}).get("orderbook") or {}
        except (requests.RequestException, ValueError):
            return None
        yes_bids = sorted(
            (BookLevel(p / 100.0, float(q)) for p, q in (ob.get("yes") or [])),
            key=lambda level: -level.price,
        )
        # Executable YES asks come from resting NO bids at (100 - price).
        yes_asks = sorted(
            (BookLevel((100 - p) / 100.0, float(q)) for p, q in (ob.get("no") or [])),
            key=lambda level: level.price,
        )
        return MarketBook(venue=self.name, market_id=market_id, outcome_id=market_ticker,
                          bids=yes_bids, asks=yes_asks, ts=time.time())

    def taker_fee(self, price: float) -> float:
        return 0.07 * price * (1.0 - price)

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 shadow uses REST polling")

    async def place(self, intent: FillIntent):
        raise RuntimeError("kalshi adapter is in shadow mode — placing orders is disabled")

    async def settlements(self):
        raise NotImplementedError("grader pulls settlements in batch; see shadow/grader.py")

    # Batch settlement lookup used by the grader.
    def fetch_results(self, tickers: list[str]) -> dict[str, float]:
        """market ticker -> payout (1.0 yes / 0.0 no) for settled markets."""
        out: dict[str, float] = {}
        for i in range(0, len(tickers), 100):
            chunk = tickers[i : i + 100]
            try:
                resp = self._sess.get(f"{BASE}/markets",
                                      params={"tickers": ",".join(chunk)}, timeout=15)
                if resp.status_code != 200:
                    continue
                for m in resp.json().get("markets") or []:
                    if m.get("status") == "settled" and m.get("result") in ("yes", "no"):
                        out[m["ticker"]] = 1.0 if m["result"] == "yes" else 0.0
            except (requests.RequestException, ValueError) as exc:
                log.warning("kalshi settlement fetch failed: %s", exc)
        return out
