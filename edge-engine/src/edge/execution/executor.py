"""Order execution (build steps 5-6): the ONLY module that turns an approved
intent into a ledger fill — paper-logged or venue-placed by mode.

PAPER      : fill recorded in the ledger at the book's ask (plus the venue's
             modeled taker fee) — never touches a venue order endpoint.
LIVE_BETA/ : Kalshi -> maker-first (post below the ask, fee-free; cross only
LIVE         when net-of-fee edge still clears the threshold).
             Polymarket US -> preview-verified FOK limit.
Every fill carries its full decision record into the ledger.
"""

from __future__ import annotations

import logging
import uuid

from edge.ledger.service import Ledger
from edge.venues.base import MarketBook

log = logging.getLogger(__name__)


def market_key(venue: str, outcome_id: str) -> str:
    return f"{venue}:{outcome_id}"


def build_decision_record(*, fair: float, edge: float, threshold: float,
                          band: str, book: MarketBook, feed_snapshot: dict,
                          approved_usd: float, guard_reason: str) -> dict:
    return {
        "fair_value": round(fair, 4), "edge": round(edge, 4),
        "threshold": threshold, "band": band,
        "book_asks": [(lv.price, lv.size) for lv in book.asks[:5]],
        "book_bids": [(lv.price, lv.size) for lv in book.bids[:5]],
        "feed": feed_snapshot, "approved_usd": approved_usd,
        "caps_state": guard_reason,
    }


def execute(*, adapter, ledger: Ledger, mode: str, mkey: str, league: str,
            ask_price: float, ask_size: float, size_usd: float,
            edge: float, threshold: float, decision: dict,
            ts: float) -> dict:
    """Returns {placed, filled_usd, status}. Paper fills always 'fill' up to
    displayed depth (conservative: no fill beyond what the book showed)."""
    displayed_usd = ask_price * ask_size
    if mode == "PAPER":
        usd = min(size_usd, displayed_usd)
        if usd < 1.0:
            return {"placed": False, "filled_usd": 0.0, "status": "paper_no_depth"}
        qty = round(usd / ask_price, 2)
        fee = adapter.taker_fee(ask_price) * qty  # modeled, conservative taker
        ledger.record_fill(
            fill_uid=f"paper-{mkey}-{int(ts)}", venue=adapter.name,
            market_key=mkey, side="BUY", qty=qty, price=ask_price, ts=ts,
            fee=round(fee, 4), league=league, mode="PAPER", decision=decision,
        )
        return {"placed": True, "filled_usd": round(usd, 2), "status": "paper"}

    # ── live paths ──────────────────────────────────────────────────────
    outcome_id = mkey.split(":", 1)[1]
    if adapter.name == "kalshi":
        plan = adapter.plan_maker_order(ask_price, ask_price, edge, threshold)
        if plan is None:
            return {"placed": False, "filled_usd": 0.0, "status": "no_maker_no_fee_room"}
        px, taker = plan
        count = int(size_usd / px)
        if count < 1:
            return {"placed": False, "filled_usd": 0.0, "status": "sub_contract"}
        result = adapter.place_order(outcome_id, px, count,
                                     client_order_id=str(uuid.uuid4()), taker=taker)
        # ALL Kalshi fills (immediate taker and resting maker alike) are
        # recorded by sync_kalshi_fills() from the portfolio fills API — one
        # source of truth, so a taker execution is never double-counted.
        if result["ok"]:
            ledger.set_state(f"kalshi_order:{result.get('order_id')}",
                             {**decision, "px": px, "count": count, "taker": taker,
                              "market_key": mkey, "league": league, "ts": ts})
            return {"placed": True, "filled_usd": 0.0,
                    "status": f"{result.get('status')}:{result.get('order_id')}"}
        return {"placed": False, "filled_usd": 0.0,
                "status": f"rejected:{result.get('status')}"}

    if adapter.name == "polymarket-us":
        qty = int(size_usd / ask_price)
        if qty < 1:
            return {"placed": False, "filled_usd": 0.0, "status": "sub_contract"}
        # Micro orders skip the preview round-trip (FOK limit already bounds
        # cost); larger sizes keep the venue cost pre-check.
        result = adapter.place_order(outcome_id, round(ask_price, 2), qty,
                                     preview=size_usd > 25)
        if result["ok"]:
            filled = float(result["count"])
            px = float(result["price"])
            ledger.record_fill(
                fill_uid=f"pmus-{result.get('order_id')}", venue=adapter.name,
                market_key=mkey, side="BUY", qty=filled, price=px, ts=ts,
                fee=adapter.taker_fee(px) * filled, league=league, mode=mode,
                decision={**decision, "order": {k: v for k, v in result.items()
                                                if k != "raw"},
                          "commissions_raw": (result.get("raw") or {}).get("response", {})},
            )
            return {"placed": True, "filled_usd": round(filled * px, 2),
                    "status": "filled_fok"}
        return {"placed": False, "filled_usd": 0.0,
                "status": f"unfilled:{result.get('status')}"}

    return {"placed": False, "filled_usd": 0.0, "status": f"unknown_venue_{adapter.name}"}


def sync_kalshi_fills(adapter, ledger: Ledger, mode: str) -> int:
    """Reconcile resting maker orders: pull recent fills from the portfolio
    API and record them (ledger fill_uid dedupe makes this idempotent)."""
    import requests

    try:
        path = "/trade-api/v2/portfolio/fills"
        from edge.venues.kalshi import BASE

        resp = adapter._sess.get(  # noqa: SLF001 — same package
            f"{BASE}/portfolio/fills", params={"limit": 100},
            headers=adapter._auth_headers("GET", path), timeout=10)
        if resp.status_code != 200:
            return 0
        n = 0
        for f in (resp.json() or {}).get("fills") or []:
            if f.get("action") != "buy" or f.get("side") != "yes":
                continue
            qty = float(f.get("count") or 0)
            price = float(f.get("yes_price") or 0) / 100.0
            if qty <= 0 or not (0 < price < 1):
                continue
            # Order context (decision record) was parked at placement time.
            ctx = ledger.get_state(f"kalshi_order:{f.get('order_id')}") or {}
            fee = adapter.taker_fee(price) * qty if f.get("is_taker") else 0.0
            r = ledger.record_fill(
                fill_uid=f"kalshi-fill-{f.get('trade_id') or f.get('fill_id')}",
                venue="kalshi",
                market_key=ctx.get("market_key")
                    or market_key("kalshi", f.get("ticker", "")),
                side="BUY", qty=qty, price=price,
                ts=float(f.get("created_time_ts") or 0) or None,
                fee=round(fee, 4), league=ctx.get("league"),
                mode=mode, decision={**ctx, "source": "fill_sync", "raw": f},
            )
            n += int(r["applied"])
        return n
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        log.warning("kalshi fill sync failed: %s", exc)
        return 0
