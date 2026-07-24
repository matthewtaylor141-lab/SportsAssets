"""Polymarket US adapter (build step 5a) — the regulated US exchange.

This is a DIFFERENT venue from the global CLOB the reference account trades
on: per-outcome markets (one market per team, grouped by event), own slugs,
own liquidity. Calibration from the global book transfers as a prior only;
the PAPER grader measures this venue's books directly.

Market data: public gateway via the official polymarket-us SDK (no auth).
Orders: Ed25519 API keys (EDGE_PMUS_KEY_ID / EDGE_PMUS_SECRET_KEY, minted at
polymarket.us/developer) — FOK limit, preview-verified, LIVE_* modes only.
Credentials are never logged; absent credentials => RuntimeError.

Fees: taker commission is configurable (EDGE_PMUS_TAKER_FEE, probability
units), default 0.0 per the current fee schedule. Every order response's
commission fields are stored raw in the ledger decision record, and the
nightly report recomputes net edge from actual charged commissions — the
hard rule is "log fee assumptions per venue per fill", encoded here.
"""

from __future__ import annotations

import logging
import os
import time

from .base import BookLevel, FillIntent, MarketBook, VenueAdapter
from .mapper import VenueMarket

log = logging.getLogger(__name__)


class PolymarketUSAdapter(VenueAdapter):
    name = "polymarket-us"

    def __init__(self) -> None:
        from polymarket_us import PolymarketUS

        self._pub = PolymarketUS()  # public gateway (market data)
        self._auth = None
        self.book_errors: dict[str, int] = {}
        self._taker_fee = float(os.environ.get("EDGE_PMUS_TAKER_FEE", "0.0"))

    # ── credentials / auth ─────────────────────────────────────────────

    @staticmethod
    def has_credentials() -> bool:
        return bool(os.environ.get("EDGE_PMUS_KEY_ID")
                    and os.environ.get("EDGE_PMUS_SECRET_KEY"))

    def _client(self):
        if self._auth is None:
            if not self.has_credentials():
                raise RuntimeError("Polymarket US live credentials absent")
            from polymarket_us import PolymarketUS

            self._auth = PolymarketUS(
                key_id=os.environ["EDGE_PMUS_KEY_ID"],
                secret_key=os.environ["EDGE_PMUS_SECRET_KEY"],
            )
        return self._auth

    def check_auth(self) -> dict:
        """check-live probe: balances call proves key validity + funding."""
        try:
            bal = self._client().account.balances()
            return {"ok": True, "balances": bal}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:150]}"}

    # ── discovery / books ──────────────────────────────────────────────

    def discover_markets(self, league_codes: set[str]) -> list[VenueMarket]:
        """Active events with nested per-outcome markets. The gateway's
        event categories don't carry our league codes, so league assignment
        happens at match time (mapper league filter passes None here)."""
        out: list[VenueMarket] = []
        try:
            offset = 0
            for _ in range(10):  # bounded paging
                resp = self._pub.events.list({"limit": 100, "offset": offset,
                                              "active": True}) or {}
                events = resp.get("events") or []
                for ev in events:
                    outcomes = {}
                    for m in ev.get("markets") or []:
                        oc = m.get("outcome") or (m.get("team") or {}).get("name")
                        if oc and m.get("slug") and not m.get("closed"):
                            outcomes[oc] = m["slug"]
                    if len(outcomes) >= 2 and ev.get("title"):
                        out.append(VenueMarket(
                            market_id=ev.get("slug") or ev["title"],
                            title=ev["title"],
                            league_code=None,   # resolved by the mapper's fuzzy match
                            outcome_tokens=outcomes,
                        ))
                if len(events) < 100:
                    break
                offset += 100
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"discovery_{type(exc).__name__}")
            log.warning("polymarket-us discovery failed: %s", exc)
        return out

    def _book_err(self, cause: str) -> None:
        self.book_errors[cause] = self.book_errors.get(cause, 0) + 1

    def get_book(self, market_id: str, market_slug: str) -> MarketBook | None:
        try:
            raw = self._pub.markets.book(market_slug) or {}
        except Exception as exc:  # noqa: BLE001
            self._book_err(f"exc_{type(exc).__name__}")
            return None

        def levels(rows, reverse):
            out = []
            for lvl in rows or []:
                try:
                    px = float((lvl.get("px") or {}).get("value") or 0)
                    qty = float(lvl.get("qty") or 0)
                except (TypeError, ValueError):
                    continue
                if 0 < px < 1 and qty > 0:
                    out.append(BookLevel(px, qty))
            return sorted(out, key=lambda x: -x.price if reverse else x.price)

        bids = levels(raw.get("bids"), reverse=True)
        asks = levels(raw.get("offers"), reverse=False)
        if not asks:
            self._book_err("no_asks")
        return MarketBook(venue=self.name, market_id=market_id,
                          outcome_id=market_slug, bids=bids, asks=asks,
                          ts=time.time())

    def taker_fee(self, price: float) -> float:
        return self._taker_fee

    # ── live orders (FOK limit, preview-verified) ──────────────────────

    def place_order(self, market_slug: str, price: float, quantity: int) -> dict:
        """BUY_LONG FOK limit at whole-cent price. Preview must agree with
        our costing within 2% before the real order goes out."""
        client = self._client()
        params = {
            "marketSlug": market_slug,
            "intent": "ORDER_INTENT_BUY_LONG",
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{price:.2f}", "currency": "USD"},
            "quantity": int(quantity),
            "tif": "TIME_IN_FORCE_FILL_OR_KILL",
        }
        expected = price * quantity
        preview = client.orders.preview({"request": params}) or {}
        prev = preview.get("order") or {}
        try:
            prev_cost = float((prev.get("cashOrderQty") or {}).get("value") or 0)
        except (TypeError, ValueError):
            prev_cost = 0.0
        if prev_cost and prev_cost > expected * 1.02:
            return {"ok": False, "status": "preview_mismatch", "order_id": None,
                    "price": price, "count": quantity, "taker": True,
                    "raw": {"preview": prev, "expected": expected}}

        resp = client.orders.create({**params, "synchronousExecution": True}) or {}
        filled, notional, state = 0.0, 0.0, ""
        for ex in resp.get("executions") or []:
            state = (ex.get("order") or {}).get("state") or state
            try:
                px = float((ex.get("lastPx") or {}).get("value") or 0)
                sh = float(ex.get("lastShares") or 0)
            except (TypeError, ValueError):
                continue
            if ex.get("type") in ("EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL") and px:
                filled += sh
                notional += sh * px
        ok = filled > 0
        return {"ok": ok, "order_id": resp.get("id"),
                "status": state.replace("ORDER_STATE_", "").lower() or "unknown",
                "price": round(notional / filled, 4) if filled else price,
                "count": filled, "taker": True,
                "raw": {"preview": prev, "response": resp}}

    async def subscribe_books(self, market_ids: list[str]):
        raise NotImplementedError("v1 uses REST polling")

    async def place(self, intent: FillIntent):
        raise RuntimeError("use place_order via the mode-gated executor path")

    async def settlements(self):
        raise NotImplementedError("grader pulls settlements in batch")

    def fetch_results(self, slugs: list[str]) -> dict[str, float]:
        """market slug -> payout for settled markets (grader input)."""
        out: dict[str, float] = {}
        for slug in slugs:
            try:
                s = self._pub.markets.settlement(slug) or {}
                px = (s.get("settlementPrice") or {}).get("value")
                if px is not None:
                    out[slug] = float(px)
            except Exception:  # noqa: BLE001 — unsettled/unknown: skip
                continue
        return out
