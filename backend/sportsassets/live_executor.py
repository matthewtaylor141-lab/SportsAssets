"""LIVE trading beta — real Polymarket CLOB orders (REAL MONEY).

Trigger: identical to the paper AI TRADER (fresh source-whale BUY), so live
fills and paper fills are directly comparable per trade.

Safety model (every layer must pass, in order):
  1. LIVE_TRADING_ENABLED + credentials present    (off by default)
  2. Kill switch not engaged (admin pause)
  3. Buy-only, source-whale-only, fresh detections only
  4. Price protection: FOK LIMIT at his_price + max_slippage — fills at our
     price or not at all; no market orders, no resting orders, no chasing
  5. Triple caps: per-fill / daily / total bankroll (SQL-enforced)
Every order and its raw API response is stored in live_orders (audit trail).
Settlement runs through the platform's resolution pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .config import settings
from .db import get_pool

log = logging.getLogger(__name__)

_client = None
_client_lock = asyncio.Lock()
PAUSE_KEY = "live_trading_paused"


def plan_order(
    his_price: float, his_notional: float, ratio: float,
    max_per_fill: float, max_slippage_cents: float,
) -> tuple[float, float, float]:
    """Pure sizing/pricing: (limit_price, requested_usd, requested_shares)."""
    limit = round(min(his_price + max_slippage_cents / 100.0, 0.99), 3)
    usd = round(min(ratio * his_notional, max_per_fill), 2)
    shares = round(usd / limit, 2) if limit > 0 else 0.0
    return limit, usd, shares


async def _caps_room(pool) -> tuple[float, float]:
    """Remaining (daily, total) bankroll room from actual filled orders."""
    cfg = settings()
    row = await pool.fetchrow(
        """
        SELECT COALESCE(sum(filled_usd) FILTER (WHERE placed_at > now() - interval '24 hours'), 0)
                   ::float8 AS day,
               COALESCE(sum(filled_usd), 0)::float8 AS total
        FROM live_orders
        """
    )
    return (cfg.live_max_daily_usd - row["day"], cfg.live_max_total_usd - row["total"])


async def _is_paused(pool) -> bool:
    val = await pool.fetchval("SELECT value FROM ingestion_state WHERE key=$1", PAUSE_KEY)
    if val is None:
        return False
    parsed = json.loads(val) if isinstance(val, str) else val
    return bool(parsed)


def _get_client():
    """Lazy sync CLOB client (py-clob-client) — built once per process."""
    global _client
    if _client is not None:
        return _client
    from py_clob_client.client import ClobClient

    cfg = settings()
    kwargs: dict[str, Any] = {"key": cfg.pm_private_key, "chain_id": 137}
    if cfg.pm_signature_type in (1, 2) and cfg.pm_funder:
        kwargs["signature_type"] = cfg.pm_signature_type
        kwargs["funder"] = cfg.pm_funder
    client = ClobClient(cfg.clob_api_base, **kwargs)
    client.set_api_creds(client.create_or_derive_api_creds())
    _client = client
    return _client


def _submit_fok(token_id: str, price: float, shares: float) -> dict:
    """Sync order submission; returns a normalized result dict."""
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY

    client = _get_client()
    order = client.create_order(
        OrderArgs(token_id=token_id, price=price, size=shares, side=BUY)
    )
    resp = client.post_order(order, OrderType.FOK)
    ok = bool(resp.get("success")) if isinstance(resp, dict) else False
    order_id = resp.get("orderID") if isinstance(resp, dict) else None
    status = (resp.get("status") or "").lower() if isinstance(resp, dict) else ""
    fill_price = price
    if ok and order_id:
        # Try to recover the actual average fill price from the order record.
        try:
            rec = client.get_order(order_id)
            maker_amt = float(rec.get("size_matched") or shares)
            px = rec.get("price")
            if px:
                fill_price = float(px)
            shares = maker_amt or shares
        except Exception:  # noqa: BLE001 — audit still records the limit price
            pass
    return {"ok": ok, "order_id": order_id, "status": status,
            "fill_price": fill_price, "filled_shares": shares if ok else 0.0,
            "raw": resp if isinstance(resp, dict) else {"raw": str(resp)}}


async def maybe_execute(payload: dict, reaction: float | None) -> None:
    """Called on every fresh detection (after the paper trade). All guards
    re-checked here; failure of any guard is a silent no-op or logged skip."""
    cfg = settings()
    if not cfg.live_trading_enabled or not cfg.pm_private_key:
        return
    username = (payload.get("whale_username") or "").lower()
    if payload.get("side") != "BUY" or username != cfg.ai_trader_source.lower():
        return
    his_notional = float(payload.get("notional") or 0)
    his_price = float(payload.get("price") or 0)
    if his_notional <= 0 or not (0 < his_price < 1):
        return

    pool = await get_pool()
    if await _is_paused(pool):
        return
    day_room, total_room = await _caps_room(pool)
    if day_room <= 1 or total_room <= 1:
        log.warning("live caps exhausted (day room %.2f, total room %.2f) — skipping",
                    day_room, total_room)
        return

    limit, usd, shares = plan_order(
        his_price, his_notional, cfg.ai_trader_ratio,
        min(cfg.live_max_per_fill_usd, day_room, total_room),
        cfg.live_max_slippage_cents,
    )
    if usd < 1 or shares <= 0:
        return

    row_id = await pool.fetchval(
        """
        INSERT INTO live_orders (trade_id, whale_username, asset, condition_id, side,
                                 his_price, reaction_s, limit_price, requested_usd,
                                 requested_shares, status)
        VALUES ($1,$2,$3,$4,'BUY',$5,$6,$7,$8,$9,'submitting')
        ON CONFLICT (trade_id) DO NOTHING RETURNING id
        """,
        payload.get("id"), payload.get("whale_username"), str(payload["asset"]),
        payload.get("condition_id"), his_price, reaction, limit, usd, shares,
    )
    if row_id is None:
        return  # duplicate detection — never double-order one source trade

    try:
        result = await asyncio.to_thread(_submit_fok, str(payload["asset"]), limit, shares)
        filled = float(result["filled_shares"]) if result["ok"] else 0.0
        fill_price = float(result["fill_price"]) if result["ok"] else None
        await pool.execute(
            """
            UPDATE live_orders
            SET status=$2, order_id=$3, filled_shares=$4, fill_price=$5,
                filled_usd=$6, raw=$7::jsonb, error=$8
            WHERE id=$1
            """,
            row_id,
            "filled" if result["ok"] and filled > 0 else "unfilled",
            result.get("order_id"), filled, fill_price,
            round(filled * (fill_price or 0), 2),
            json.dumps(result.get("raw"), default=str),
            None if result["ok"] else str(result.get("raw"))[:300],
        )
        log.info("LIVE order %s: %s %.2f shares @ %.3f (his %.3f)",
                 "FILLED" if result["ok"] and filled > 0 else "unfilled",
                 payload.get("whale_username"), filled, fill_price or limit, his_price)
    except Exception as exc:  # noqa: BLE001 — record, never crash ingestion
        log.exception("live order failed for trade %s", payload.get("id"))
        await pool.execute(
            "UPDATE live_orders SET status='error', error=$2 WHERE id=$1",
            row_id, str(exc)[:300],
        )
