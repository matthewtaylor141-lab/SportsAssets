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

PMUS_ORDER_PREFIX = "pmus_order:"   # state key: resting maker order context


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
            ts: float, entry_price: float | None = None, taker: bool = True,
            event_key: str | None = None) -> dict:
    """Returns {placed, filled_usd, status}. Paper fills always 'fill' up to
    displayed depth (conservative: no fill beyond what the book showed).

    entry_price/taker come from the adapter's plan_entry(): when the venue
    can rest an order, entry_price is inside the spread and taker is False."""
    entry_price = ask_price if entry_price is None else entry_price
    displayed_usd = ask_price * ask_size
    if mode == "PAPER":
        usd = min(size_usd, displayed_usd)
        if usd < 1.0:
            return {"placed": False, "filled_usd": 0.0, "status": "paper_no_depth"}
        qty = round(usd / entry_price, 2)
        fee = (adapter.taker_fee if taker else adapter.maker_fee)(entry_price) * qty
        ledger.record_fill(
            fill_uid=f"paper-{mkey}-{int(ts)}", venue=adapter.name,
            market_key=mkey, side="BUY", qty=qty, price=entry_price, ts=ts,
            fee=round(fee, 4), league=league, mode="PAPER",
            decision={**decision, "entry_taker": taker, "ask_price": ask_price},
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
        qty = int(size_usd / entry_price)
        if qty < 1:
            return {"placed": False, "filled_usd": 0.0, "status": "sub_contract"}
        if not taker:
            # Maker path: rest inside the spread, post-only. The fill (if it
            # comes) arrives later via sync_pmus_fills, so nothing is written
            # to the ledger here — only the context the reconciler and the
            # reaper need. Keyed by market: one resting order per market at a
            # time, which the per-market cap already guarantees.
            result = adapter.place_order(
                outcome_id, round(entry_price, 2), qty, preview=False,
                tif="TIME_IN_FORCE_GOOD_TILL_CANCEL", post_only=True)
            if not result["ok"]:
                # Price improvement is an optimisation, never a precondition.
                # If the venue won't rest the order, cross — provided the
                # edge still clears at the ask, which is the bar we would
                # have used had we never tried to do better.
                ask_edge = edge - (ask_price - entry_price)
                if ask_edge < threshold:
                    return {"placed": False, "filled_usd": 0.0,
                            "status": f"maker_rejected:{result.get('status')}"}
                log.info("maker order refused (%s) — crossing at %.2f",
                         result.get("status"), ask_price)
                adapter.mark_force_taker(outcome_id)
                entry_price, taker, qty = ask_price, True, int(size_usd / ask_price)
                if qty < 1:
                    return {"placed": False, "filled_usd": 0.0,
                            "status": "sub_contract"}
            else:
                ledger.set_state(f"{PMUS_ORDER_PREFIX}{outcome_id}", {
                    **decision, "order_id": result.get("order_id"),
                    "px": round(entry_price, 2), "count": qty, "taker": False,
                    "market_key": mkey, "league": league, "event_key": event_key,
                    "mode": mode, "ts": ts})
                return {"placed": True, "filled_usd": 0.0, "status": "resting_maker"}

        # Taker path. Clear any stale maker context for this market first, or
        # the reconciler would record this fill a second time from the
        # activity feed.
        ledger.clear_state(f"{PMUS_ORDER_PREFIX}{outcome_id}")
        # Micro orders skip the preview round-trip (FOK limit already bounds
        # cost); larger sizes keep the venue cost pre-check.
        result = adapter.place_order(outcome_id, round(entry_price, 2), qty,
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


def sync_pmus_fills(adapter, ledger: Ledger, mode: str) -> int:
    """Record fills of RESTING Polymarket US orders from the activity feed.

    Only markets carrying a parked maker context are considered: a taker FOK
    fill is written by execute() the moment it happens and clears its
    context, so no trade can be counted twice. fill_uid dedupe makes repeated
    syncs idempotent regardless."""
    contexts = ledger.list_state(PMUS_ORDER_PREFIX)
    if not contexts:
        return 0
    by_slug = {k[len(PMUS_ORDER_PREFIX):]: v for k, v in contexts.items()}
    n = 0
    for trade in adapter.recent_trades(limit=100):
        slug = trade.get("marketSlug")
        ctx = by_slug.get(slug)
        if not ctx:
            continue
        try:
            qty = float(trade.get("qty") or 0)
            price = float((trade.get("price") or {}).get("value") or 0)
        except (TypeError, ValueError):
            continue
        if qty <= 0 or not (0 < price < 1):
            continue
        fee = (adapter.taker_fee(price) if trade.get("isAggressor")
               else adapter.maker_fee(price)) * qty
        r = ledger.record_fill(
            fill_uid=f"pmus-trade-{trade.get('id')}", venue=adapter.name,
            market_key=ctx.get("market_key") or market_key(adapter.name, slug),
            side="BUY", qty=qty, price=price, ts=None, fee=round(fee, 4),
            league=ctx.get("league"), mode=ctx.get("mode") or mode,
            decision={**ctx, "source": "maker_fill_sync", "raw": trade},
        )
        n += int(r["applied"])
    return n


def reap_pmus_makers(adapter, ledger: Ledger, ttl_s: float,
                     now: float | None = None) -> dict:
    """Close out resting orders: cancel anything past its TTL, and drop the
    context of anything the venue no longer lists as open.

    A resting order that never filled must also give back its one-per-event
    claim — otherwise a single unfilled quote retires that game for good,
    which would cost far more volume than maker pricing wins. The claim is
    only released when the market genuinely holds no position."""
    import time as _time

    now = now or _time.time()
    contexts = ledger.list_state(PMUS_ORDER_PREFIX)
    if not contexts:
        return {"cancelled": 0, "closed": 0, "released": 0}
    open_ids = {o.get("id") for o in adapter.open_orders() if o.get("id")}
    out = {"cancelled": 0, "closed": 0, "released": 0}
    for key, ctx in contexts.items():
        slug = key[len(PMUS_ORDER_PREFIX):]
        still_open = ctx.get("order_id") in open_ids
        if still_open and now - float(ctx.get("ts") or 0) <= ttl_s:
            continue                       # young and working: leave it be
        if still_open:
            adapter.cancel_order(ctx["order_id"], slug)
            out["cancelled"] += 1
        else:
            out["closed"] += 1
        ledger.clear_state(key)
        pos = ledger.position(ctx.get("market_key") or "")
        if not pos or float(pos.get("shares") or 0) <= 0:
            # Nothing filled: free the event AND stop resting on this market
            # for a cool-off, so the next look crosses instead of quoting into
            # a queue that has already proven it won't reach us.
            if hasattr(adapter, "mark_force_taker"):
                adapter.mark_force_taker(slug)
            if ctx.get("event_key") and ledger.release_event(ctx["event_key"]):
                out["released"] += 1
    return out


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
