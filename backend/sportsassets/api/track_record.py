"""The AI trader's public track record — from the ACTUAL venue account.

The first version of the site read the engine's shadow mirror, which logs
intents (including paper ones). A track record must come from the account
that holds the money: every row here is a real position from the venue's
portfolio API, priced by its own trade activities, settled by its own
resolution activities. Nothing is inferred from our bookkeeping; our ledger
only ANNOTATES rows (edge, band) where it recognizes the market.

Windowed from `since` (default 2026-08-01, the account's first full day) on
ENTRY time — the first trade the venue reports for the market. A row
without a venue-reported entry inside the window is excluded rather than
guessed into it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from ..config import settings
from .pmus_account import _act_ts, _amt

_raw_cache: dict[str, Any] = {"ts": 0.0, "data": None}
_RAW_TTL = 30.0
_lock = asyncio.Lock()

DEFAULT_SINCE = "2026-08-01"

# Slug grammar: {kind}-{league}-{teams...}-{date}-{qualifiers...}
#   atc   team contract (moneyline / segment h2h)   aec  event contract
#   asc   spread        tsc  total                  astatc  player prop
LEAGUE_SPORT: dict[str, tuple[str, str]] = {
    "mlb": ("Baseball", "⚾"), "nba": ("Basketball", "🏀"),
    "wnba": ("Basketball", "🏀"), "cbb": ("Basketball", "🏀"),
    "nfl": ("Football", "🏈"), "cfb": ("Football", "🏈"),
    "nhl": ("Hockey", "🏒"), "atp": ("Tennis", "🎾"), "wta": ("Tennis", "🎾"),
}
_SEGMENTS = {"f5", "h1", "h2", "p1", "q1"} | {f"i{i}" for i in range(1, 10)}


def classify_slug(slug: str) -> dict:
    """(sport, icon, category) from the venue's own naming. Pure; tested."""
    parts = (slug or "").split("-")
    kind = parts[0] if parts else ""
    league = parts[1] if len(parts) > 1 else ""
    sport, icon = LEAGUE_SPORT.get(league, ("Soccer", "⚽"))
    if league in ("lmx", "epl", "ucl", "ekst", "els", "lpa", "pdc", "scp",
                  "alsv", "bra", "arg", "mex"):
        sport, icon = "Soccer", "⚽"
    if kind == "astatc":
        cat = "Player Prop"
    elif kind == "asc":
        cat = "Spread"
    elif kind == "tsc":
        cat = "Total"
    elif any(p in _SEGMENTS for p in parts[2:]):
        cat = "Segment"
    else:
        cat = "Moneyline"
    return {"sport": sport, "icon": icon, "category": cat, "league": league}


def build(positions: dict[str, dict], activities: list[dict],
          since_ts: float) -> dict:
    """Pure builder (unit-tested): venue payloads -> the track record."""
    # Entry facts come from the venue's TRADE activities: first trade time,
    # volume-weighted entry price, buy count.
    entries: dict[str, dict] = {}
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_TRADE":
            continue
        t = act.get("trade") or {}
        slug = t.get("marketSlug")
        if not slug:
            continue
        ts = _act_ts(act) or _act_ts(t)
        qty, price = _amt(t.get("qty")), _amt(t.get("price"))
        e = entries.setdefault(slug, {"first_ts": ts, "qty": 0.0,
                                      "notional": 0.0, "fills": 0})
        if ts and (not e["first_ts"] or ts < e["first_ts"]):
            e["first_ts"] = ts
        if qty > 0 and 0 < price < 1:
            e["qty"] += qty
            e["notional"] += qty * price
            e["fills"] += 1

    resolutions: dict[str, float] = {}
    for act in activities or []:
        if act.get("type") != "ACTIVITY_TYPE_POSITION_RESOLUTION":
            continue
        slug = (act.get("positionResolution") or {}).get("marketSlug")
        if slug:
            resolutions[slug] = _act_ts(act)

    rows = []
    undatable = 0
    for slug, p in (positions or {}).items():
        meta = p.get("marketMetadata") or {}
        qty = _amt(p.get("netPosition"))
        settled = bool(p.get("expired")) or qty <= 0
        e = entries.get(slug) or {}
        entry_ts = e.get("first_ts") or 0.0
        if not entry_ts:
            # The venue reported no datable trade for this position in the
            # activity pages we fetched. A row that cannot be windowed is
            # excluded and COUNTED, never guessed into a date.
            undatable += 1
            continue
        if entry_ts < since_ts:
            continue          # pre-window entry: excluded, not re-dated
        cost = _amt(p.get("cost"))
        value = _amt(p.get("cashValue"))
        realized = _amt(p.get("realized"))
        vwap = (e.get("notional", 0) / e["qty"]) if e.get("qty") else None
        rows.append({
            "market_slug": slug,
            "title": meta.get("title") or slug,
            "outcome": meta.get("outcome"),
            **classify_slug(slug),
            "entry_ts": entry_ts or None,
            "entry_date": (datetime.fromtimestamp(entry_ts, timezone.utc)
                           .strftime("%Y-%m-%d") if entry_ts else None),
            "entry_price": round(vwap, 4) if vwap else None,
            "fills": e.get("fills", 0),
            "qty": qty if not settled else e.get("qty", 0.0),
            "stake": round(cost if cost > 0 else e.get("notional", 0.0), 4),
            "value": round(value, 4),
            "settled": settled,
            "settled_ts": resolutions.get(slug),
            "pnl": round(realized, 4) if settled else None,
            "unrealized": round(value - cost, 4) if not settled else None,
        })
    rows.sort(key=lambda r: -(r["entry_ts"] or 0))

    settled_rows = [r for r in rows if r["settled"]]
    wins = [r for r in settled_rows if (r["pnl"] or 0) > 0]
    deployed = sum(r["stake"] for r in rows)
    settled_stake = sum(r["stake"] for r in settled_rows)
    net = sum(r["pnl"] or 0 for r in settled_rows)

    # Daily series. Deployment buckets on entry day (always known inside the
    # window); realized P&L buckets on the venue's resolution day where it
    # gave one, else the entry day, FLAGGED — never silently "today".
    daily: dict[str, dict] = {}
    for r in rows:
        if not r["entry_date"]:
            continue
        d = daily.setdefault(r["entry_date"], {
            "date": r["entry_date"], "deployed": 0.0, "trades": 0,
            "pnl": 0.0, "settled": 0, "wins": 0, "pnl_estimated": False})
        d["deployed"] += r["stake"]
        d["trades"] += 1
    for r in settled_rows:
        ts = r["settled_ts"] or r["entry_ts"]
        if not ts:
            continue
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        d = daily.setdefault(day, {
            "date": day, "deployed": 0.0, "trades": 0,
            "pnl": 0.0, "settled": 0, "wins": 0, "pnl_estimated": False})
        d["pnl"] += r["pnl"] or 0
        d["settled"] += 1
        if (r["pnl"] or 0) > 0:
            d["wins"] += 1
        if not r["settled_ts"]:
            d["pnl_estimated"] = True

    for d in daily.values():
        d["deployed"] = round(d["deployed"], 2)
        d["pnl"] = round(d["pnl"], 2)

    return {
        "since": datetime.fromtimestamp(since_ts, timezone.utc)
                 .strftime("%Y-%m-%d"),
        "generated_at": time.time(),
        "summary": {
            "trades": len(rows),
            "open": len(rows) - len(settled_rows),
            "settled": len(settled_rows),
            "wins": len(wins),
            "losses": len(settled_rows) - len(wins),
            "deployed": round(deployed, 2),
            "open_value": round(sum(r["value"] for r in rows
                                    if not r["settled"]), 2),
            "net_pnl": round(net, 2),
            "settled_stake": round(settled_stake, 2),
            "roi": round(net / settled_stake, 4) if settled_stake else None,
            "win_rate": (round(len(wins) / len(settled_rows), 4)
                         if settled_rows else None),
        },
        "excluded_undatable": undatable,
        "daily": sorted(daily.values(), key=lambda d: d["date"]),
        "trades": rows,
    }


def _fetch_raw() -> dict:
    from polymarket_us import PolymarketUS

    cfg = settings()
    client = PolymarketUS(key_id=cfg.pmus_key_id, secret_key=cfg.pmus_secret_key)
    positions: dict[str, dict] = {}
    cursor = ""
    for _ in range(8):
        resp = client.portfolio.positions(
            {"limit": 100, **({"cursor": cursor} if cursor else {})}) or {}
        positions.update(resp.get("positions") or {})
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    acts: list[dict] = []
    cursor = ""
    for _ in range(12):     # deeper than the account card: entries need it
        resp = client.portfolio.activities(
            {"limit": 100, "sortOrder": "SORT_ORDER_DESCENDING",
             "types": ["ACTIVITY_TYPE_TRADE",
                       "ACTIVITY_TYPE_POSITION_RESOLUTION"],
             **({"cursor": cursor} if cursor else {})}) or {}
        acts.extend(resp.get("activities") or [])
        cursor = resp.get("nextCursor") or ""
        if resp.get("eof") or not cursor:
            break
    return {"positions": positions, "activities": acts}


async def track_record(since: str | None = None) -> dict:
    cfg = settings()
    if not (cfg.pmus_key_id and cfg.pmus_secret_key):
        return {"configured": False}
    try:
        since_ts = datetime.strptime(since or DEFAULT_SINCE, "%Y-%m-%d") \
            .replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        since_ts = datetime.strptime(DEFAULT_SINCE, "%Y-%m-%d") \
            .replace(tzinfo=timezone.utc).timestamp()
    async with _lock:
        now = time.time()
        if _raw_cache["data"] is None or now - _raw_cache["ts"] > _RAW_TTL:
            try:
                _raw_cache["data"] = await asyncio.wait_for(
                    asyncio.to_thread(_fetch_raw), timeout=30)
                _raw_cache["ts"] = now
            except Exception as exc:  # noqa: BLE001 — surface, don't 500
                return {"configured": True,
                        "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    raw = _raw_cache["data"]
    return {"configured": True,
            **build(raw["positions"], raw["activities"], since_ts)}
