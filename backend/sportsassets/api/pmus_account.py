"""Live Polymarket US account view — the REAL account, straight from the
venue's portfolio API (not our internal bookkeeping): balance, buying power,
open positions at cost/value, realized PnL, recent trades.

Uses the platform's PMUS_KEY_ID/PMUS_SECRET_KEY (same key pair as the
executor). Read-only calls; cached 30s so UI polling never hammers the venue.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..config import settings

_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_CACHE_TTL = 30.0
_lock = asyncio.Lock()


def _amt(a: Any) -> float:
    """Amount {value,currency} | number | str -> float."""
    if isinstance(a, dict):
        a = a.get("value")
    try:
        return float(a or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize(balances_resp: dict, positions: dict[str, dict],
              activities: list[dict]) -> dict:
    """Pure normalizer (unit-tested): venue payloads -> platform card."""
    bal_rows = (balances_resp or {}).get("balances") or []
    bal = bal_rows[0] if bal_rows else {}
    cash = _amt(bal.get("currentBalance"))
    asset_notional = _amt(bal.get("assetNotional"))

    open_rows, settled_rows = [], []
    realized_total = 0.0
    for slug, p in (positions or {}).items():
        meta = p.get("marketMetadata") or {}
        qty = _amt(p.get("netPosition"))
        realized = _amt(p.get("realized"))
        realized_total += realized
        row = {
            "market_slug": slug,
            "title": meta.get("title") or slug,
            "outcome": meta.get("outcome"),
            "qty": qty,
            "cost": _amt(p.get("cost")),
            "value": _amt(p.get("cashValue")),
            "realized": realized,
        }
        if qty > 0 and not p.get("expired"):
            open_rows.append(row)
        else:
            settled_rows.append(row)
    open_rows.sort(key=lambda r: -r["cost"])
    settled_rows.sort(key=lambda r: -abs(r["realized"]))

    trades = []
    for act in activities or []:
        t = act.get("trade") or {}
        if act.get("type") != "ACTIVITY_TYPE_TRADE" or not t:
            continue
        meta = (t.get("marketMetadata") or {})  # may be absent on trades
        trades.append({
            "time": t.get("createTime"),
            "market_slug": t.get("marketSlug"),
            "title": meta.get("title") or t.get("marketSlug"),
            "qty": _amt(t.get("qty")),
            "price": _amt(t.get("price")),
            "realized_pnl": _amt(t.get("realizedPnl")),
        })

    return {
        "configured": True,
        "account_value": round(cash + asset_notional, 2),
        "cash": round(cash, 2),
        "buying_power": round(_amt(bal.get("buyingPower")), 2),
        "open_value": round(asset_notional, 2),
        "unsettled_funds": round(_amt(bal.get("unsettledFunds")), 2),
        "realized_pnl": round(realized_total, 2),
        "open_positions": open_rows[:50],
        "open_count": len(open_rows),
        "settled_count": len(settled_rows),
        "recent_trades": trades[:25],
        "trade_count_recent": len(trades),
    }


def _fetch_sync() -> dict:
    from polymarket_us import PolymarketUS

    cfg = settings()
    client = PolymarketUS(key_id=cfg.pmus_key_id, secret_key=cfg.pmus_secret_key)
    balances = client.account.balances() or {}
    positions: dict[str, dict] = {}
    cursor = ""
    for _ in range(5):  # bounded paging
        resp = client.portfolio.positions(
            {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
        positions.update(resp.get("positions") or {})
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    acts = (client.portfolio.activities(
        {"limit": 50, "types": ["ACTIVITY_TYPE_TRADE"],
         "sortOrder": "SORT_ORDER_DESCENDING"}) or {}).get("activities") or []
    return normalize(balances, positions, acts)


async def account_snapshot() -> dict:
    cfg = settings()
    if not (cfg.pmus_key_id and cfg.pmus_secret_key):
        return {"configured": False,
                "hint": "set PMUS_KEY_ID + PMUS_SECRET_KEY on sportsassets-api"}
    async with _lock:
        now = time.time()
        if _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
            return _cache["data"]
        try:
            data = await asyncio.wait_for(asyncio.to_thread(_fetch_sync), timeout=25)
        except Exception as exc:  # noqa: BLE001 — surface, don't 500
            return {"configured": True, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        _cache.update(ts=now, data=data)
        return data
