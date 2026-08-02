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


# The engine trades $1 tickets; the owner trades in tens-to-hundreds. Any
# performance number that mixes them is meaningless — a single $200 manual
# position swamps a hundred $1 system fills. Split on position cost.
SYSTEM_MAX_COST = 5.0


def _act_ts(act: dict) -> float:
    """Settlement time from an activity, whatever field carries it.

    Providers move this key around between payload versions, and a missing
    timestamp must not silently land a settlement in the wrong day — an
    undated row is reported as undated rather than guessed into today.
    """
    from datetime import datetime, timezone

    for key in ("timestamp", "createTime", "createdAt", "created_at",
                "settledAt", "time"):
        v = act.get(key)
        if v is None:
            continue
        if isinstance(v, (int, float)):
            # Milliseconds vs seconds: anything past year ~2001 in seconds
            # is >1e9, so a value >1e11 is certainly milliseconds.
            return float(v) / 1000.0 if float(v) > 1e11 else float(v)
        try:
            txt = str(v).replace("Z", "+00:00")
            return datetime.fromisoformat(txt).replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


def _daily(rows: list[dict], days: int = 7) -> list[dict]:
    """Settled P&L per UTC day, newest first.

    The cohort scorecard is cumulative, which cannot answer "how did we do
    yesterday" without subtracting two readings taken at arbitrary times —
    and those boundaries never line up with a day. This is the lookup.
    """
    import time
    from datetime import datetime, timezone

    cutoff = time.time() - days * 86_400
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if not r.get("settled"):
            continue
        ts = r.get("settled_at") or 0.0
        if not ts:
            buckets.setdefault("undated", []).append(r)
            continue
        if ts < cutoff:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        buckets.setdefault(day, []).append(r)

    out = []
    # Real days newest-first, then 'undated' — the column should read as a
    # calendar, not be led by a bucket that is a data problem.
    ordered = sorted((d for d in buckets if d != "undated"), reverse=True)
    if "undated" in buckets:
        ordered.append("undated")
    for day in ordered:
        rs = buckets[day]
        staked = sum(r["cost"] for r in rs)
        realized = sum(r["realized"] for r in rs)
        out.append({
            "day": day,
            "settled": len(rs),
            "wins": sum(1 for r in rs if r["realized"] > 0),
            "losses": sum(1 for r in rs if r["realized"] < 0),
            "staked": round(staked, 2),
            "realized": round(realized, 2),
            "roi": round(realized / staked, 4) if staked else None,
        })
    return out


def _scorecard(rows: list[dict], label: str) -> dict:
    """Win/loss and money for one cohort of positions."""
    settled = [r for r in rows if r["settled"]]
    wins = [r for r in settled if r["realized"] > 0]
    losses = [r for r in settled if r["realized"] < 0]
    staked = sum(r["cost"] for r in rows)
    realized = sum(r["realized"] for r in settled)
    open_rows = [r for r in rows if not r["settled"]]
    return {
        "cohort": label,
        "positions": len(rows),
        "staked": round(staked, 2),
        "settled": len(settled),
        "open": len(open_rows),
        "open_cost": round(sum(r["cost"] for r in open_rows), 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(settled), 4) if settled else None,
        "realized": round(realized, 2),
        # ROI on SETTLED money only — open positions have not paid out yet and
        # counting their cost against zero return reads as a fake loss.
        "roi": (round(realized / sum(r["cost"] for r in settled), 4)
                if settled and sum(r["cost"] for r in settled) else None),
    }


def normalize(balances_resp: dict, positions: dict[str, dict],
              activities: list[dict],
              system_max_cost: float = SYSTEM_MAX_COST) -> dict:
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

    # Settlements arrive as their OWN activity type, not on the trade. A
    # buy-and-hold book has no realized PnL until resolution, so without
    # these every position looks perpetually pending.
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        res = act.get("positionResolution") or {}
        slug = res.get("marketSlug")
        after = res.get("afterPosition") or {}
        before = res.get("beforePosition") or {}
        if not slug:
            continue
        row = next((r for r in open_rows + settled_rows
                    if r["market_slug"] == slug), None)
        realized = _amt(after.get("realized")) or _amt(before.get("realized"))
        when = _act_ts(act)
        if row is None:
            settled_rows.append({
                "market_slug": slug,
                "title": (after.get("marketMetadata") or {}).get("title") or slug,
                "outcome": None, "qty": 0.0,
                "cost": _amt(before.get("cost")), "value": 0.0,
                "realized": realized,
                "settled_at": when,
            })
        else:
            row["realized"] = realized or row["realized"]
            row["settled_at"] = when
            if row in open_rows:
                open_rows.remove(row)
                settled_rows.append(row)

    for r in open_rows:
        r["settled"] = False
    for r in settled_rows:
        r["settled"] = True
    every = open_rows + settled_rows
    system = [r for r in every if r["cost"] <= system_max_cost]
    manual = [r for r in every if r["cost"] > system_max_cost]
    realized_total = sum(r["realized"] for r in settled_rows)

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
        # The question that matters: is the SYSTEM making money, separately
        # from the owner's own (much larger) manual bets?
        "system_max_cost": system_max_cost,
        "system": _scorecard(system, "system"),
        "manual": _scorecard(manual, "manual"),
        # Per-day, system cohort only. Answers "how did the engine do
        # yesterday" as a lookup rather than by subtracting two cumulative
        # readings taken at times that never line up with a day boundary.
        "system_daily": _daily(system),
        "system_positions": sorted(system, key=lambda r: r["market_slug"])[:60],
        # The manual cohort is defined as "cost > $5", which ASSUMES anything
        # that large was placed by hand. A per-market cap breach breaks that
        # assumption: an engine position that accumulated past $5 would be
        # silently reclassified as the owner's own trade, hiding the breach
        # inside the very number used to judge the engine. Listed so the
        # boundary can be checked rather than trusted.
        "manual_positions": sorted(manual, key=lambda r: -r["cost"])[:40],
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
    acts: list[dict] = []
    cursor = ""
    for _ in range(6):  # bounded paging; trades AND settlements
        resp = client.portfolio.activities(
            {"limit": 100, "sortOrder": "SORT_ORDER_DESCENDING",
             "types": ["ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_POSITION_RESOLUTION"],
             **({"cursor": cursor} if cursor else {})}) or {}
        acts.extend(resp.get("activities") or [])
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
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
