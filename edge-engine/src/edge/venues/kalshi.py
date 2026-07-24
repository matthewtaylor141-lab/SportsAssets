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
        self.book_errors: dict[str, int] = {}

    def _book_err(self, cause: str) -> None:
        self.book_errors[cause] = self.book_errors.get(cause, 0) + 1

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
        except requests.RequestException as exc:
            self._book_err(f"exc_{type(exc).__name__}")
            return None
        if resp.status_code != 200:
            self._book_err(f"http_{resp.status_code}")
            log.info("kalshi book %s for %s: %s", resp.status_code, market_ticker,
                     resp.text[:120])
            return None
        try:
            ob = (resp.json() or {}).get("orderbook") or {}
        except ValueError:
            self._book_err("bad_json")
            return None
        if not ob.get("no"):
            self._book_err("empty_no_side")
            log.info("kalshi empty book for %s: %s", market_ticker, str(ob)[:160])
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

    # ── live orders (maker-first; LIVE_* modes only) ───────────────────
    # Auth: Kalshi API key id + RSA private key; signature = RSA-PSS-SHA256
    # over "{timestamp_ms}{METHOD}{path}". Credentials come only from env
    # (EDGE_KALSHI_KEY_ID, EDGE_KALSHI_PRIVATE_KEY or _PATH) and are never
    # logged. Absent credentials => RuntimeError, so LIVE_* cannot start.

    def has_credentials(self) -> bool:
        return bool(os.environ.get("EDGE_KALSHI_KEY_ID")
                    and (os.environ.get("EDGE_KALSHI_PRIVATE_KEY")
                         or os.environ.get("EDGE_KALSHI_PRIVATE_KEY_PATH")))

    def _private_key(self):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        pem = os.environ.get("EDGE_KALSHI_PRIVATE_KEY", "")
        if not pem:
            path = os.environ.get("EDGE_KALSHI_PRIVATE_KEY_PATH", "")
            if not path:
                raise RuntimeError("Kalshi live credentials absent")
            with open(path, "rb") as f:
                pem = f.read().decode()
        return load_pem_private_key(pem.encode(), password=None)

    def _auth_headers(self, method: str, path: str) -> dict:
        import base64

        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.hashes import SHA256

        key_id = os.environ.get("EDGE_KALSHI_KEY_ID", "")
        if not key_id:
            raise RuntimeError("Kalshi live credentials absent")
        ts_ms = str(int(time.time() * 1000))
        msg = f"{ts_ms}{method.upper()}{path}".encode()
        sig = self._private_key().sign(
            msg,
            padding.PSS(mgf=padding.MGF1(SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            SHA256(),
        )
        return {"KALSHI-ACCESS-KEY": key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts_ms,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}

    def check_auth(self) -> dict:
        """check-live probe: balance call proves key validity + funding."""
        path = "/trade-api/v2/portfolio/balance"
        resp = self._sess.get(f"{BASE}/portfolio/balance",
                              headers=self._auth_headers("GET", path), timeout=10)
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:150]}"}
        bal = (resp.json() or {}).get("balance")
        return {"ok": True, "balance_usd": (bal or 0) / 100.0}

    def plan_maker_order(self, limit_price: float, best_ask: float,
                         edge: float, threshold: float) -> tuple[float, bool] | None:
        """Maker-first pricing (pure; unit-tested):
        Post at our limit BELOW the ask -> rests as maker, fee-free.
        Cross the ask ONLY if edge net of the taker fee still clears the
        threshold. Returns (price, is_taker) or None for no order."""
        tick = 0.01
        maker_px = round(min(limit_price, best_ask - tick), 2)
        if maker_px >= 0.01 and maker_px < best_ask:
            return maker_px, False
        if edge - self.taker_fee(best_ask) >= threshold:
            return round(best_ask, 2), True
        return None

    def place_order(self, market_ticker: str, price: float, count: int,
                    client_order_id: str, taker: bool) -> dict:
        """POST a YES buy limit. Resting maker orders expire after 15 min so
        stale quotes never linger; crossing orders expire almost immediately
        (IOC semantics via expiration_ts)."""
        path = "/trade-api/v2/portfolio/orders"
        body = {
            "ticker": market_ticker, "client_order_id": client_order_id,
            "side": "yes", "action": "buy", "type": "limit",
            "count": int(count), "yes_price": int(round(price * 100)),
            "expiration_ts": int(time.time()) + (2 if taker else 900),
        }
        resp = self._sess.post(f"{BASE}/portfolio/orders", json=body,
                               headers=self._auth_headers("POST", path), timeout=10)
        ok = resp.status_code in (200, 201)
        data = resp.json() if ok else {}
        order = data.get("order") or {}
        return {"ok": ok, "order_id": order.get("order_id"),
                "status": order.get("status", f"http_{resp.status_code}"),
                "price": price, "count": count, "taker": taker,
                "raw": data if ok else {"error": resp.text[:300]}}

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 uses REST polling")

    async def place(self, intent: FillIntent):
        raise RuntimeError("use place_order via the mode-gated executor path")

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
