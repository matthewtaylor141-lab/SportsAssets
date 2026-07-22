"""Read-model queries for the API layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..analytics.positions import Fill, Position
from ..db import get_pool

TRADE_COLS = """
    t.id, t.whale_id, w.username AS whale_username, t.tx_hash, t.asset, t.condition_id,
    t.side, t.outcome, t.outcome_index, t.size::float8 AS size, t.price::float8 AS price,
    t.notional::float8 AS notional, t.market_title, t.market_slug, t.event_slug, t.sport,
    t.ts, t.source, t.detected_at, t.enriched_at,
    EXTRACT(EPOCH FROM (t.detected_at - t.ts))::float8 AS latency_s
"""


async def feed(
    limit: int = 50,
    before_id: int | None = None,
    whale_id: int | None = None,
    sport: str | None = None,
    side: str | None = None,
    min_notional: float | None = None,
) -> list[dict]:
    conds, args = [], []
    if before_id is not None:
        args.append(before_id)
        conds.append(f"t.id < ${len(args)}")
    if whale_id is not None:
        args.append(whale_id)
        conds.append(f"t.whale_id = ${len(args)}")
    if sport:
        args.append(sport)
        conds.append(f"t.sport = ${len(args)}")
    if side:
        args.append(side.upper())
        conds.append(f"t.side = ${len(args)}")
    if min_notional:
        args.append(min_notional)
        conds.append(f"t.notional >= ${len(args)}")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    args.append(min(limit, 200))
    pool = await get_pool()
    rows = await pool.fetch(
        f"SELECT {TRADE_COLS} FROM trades t JOIN whales w ON w.id = t.whale_id "
        f"{where} ORDER BY t.id DESC LIMIT ${len(args)}",
        *args,
    )
    return [dict(r) for r in rows]


async def whales(include_inactive: bool = False) -> list[dict]:
    pool = await get_pool()
    where = "" if include_inactive else "WHERE active AND NOT banned"
    rows = await pool.fetch(
        f"""
        SELECT id, address, username, pinned, banned, active, source_rank,
               sports_profit_alltime::float8 AS sports_profit_alltime, added_at, removed_at
        FROM whales {where} ORDER BY source_rank NULLS LAST, id
        """
    )
    return [dict(r) for r in rows]


async def whale_profile(whale_id: int) -> dict | None:
    pool = await get_pool()
    whale = await pool.fetchrow(
        "SELECT id, address, username, pinned, active, source_rank, "
        "sports_profit_alltime::float8 AS sports_profit_alltime, added_at FROM whales WHERE id=$1",
        whale_id,
    )
    if whale is None:
        return None
    stats = await pool.fetch(
        "SELECT * FROM whale_sport_stats WHERE whale_id=$1 ORDER BY sport, time_window", whale_id
    )
    open_positions = await pool.fetch(
        """
        SELECT p.condition_id, p.token_id, p.outcome, p.net_shares::float8 AS net_shares,
               p.avg_cost::float8 AS avg_cost, p.realized_pnl::float8 AS realized_pnl,
               (p.net_shares * p.avg_cost)::float8 AS exposure,
               m.title AS market_title, m.event_title, m.sport, p.last_trade_ts
        FROM positions p LEFT JOIN markets m USING (condition_id)
        WHERE p.whale_id=$1 AND p.net_shares > 0 AND NOT p.resolved
        ORDER BY exposure DESC LIMIT 100
        """,
        whale_id,
    )
    recent = await feed(limit=25, whale_id=whale_id)
    curve = await equity_curve(whale_id)
    mix = await pool.fetch(
        """
        SELECT sport, sum(notional)::float8 AS notional
        FROM trades WHERE whale_id=$1 AND side='BUY' GROUP BY sport ORDER BY notional DESC
        """,
        whale_id,
    )
    return {
        "whale": dict(whale),
        "stats": [_stats_row(r) for r in stats],
        "open_positions": [dict(r) for r in open_positions],
        "recent_trades": recent,
        "equity_curve": curve,
        "sport_mix": [dict(r) for r in mix],
    }


def _stats_row(r: Any) -> dict:
    d = dict(r)
    # DB column is time_window ("window" is a Postgres reserved word);
    # the API contract stays `window`.
    if "time_window" in d:
        d["window"] = d.pop("time_window")
    for k in ("win_pct", "realized_pnl", "notional", "roi", "avg_position", "open_exposure"):
        if d.get(k) is not None:
            d[k] = float(d[k])
    return d


async def equity_curve(whale_id: int) -> list[dict]:
    """Cumulative realized P&L over time: replay this whale's ledger with the
    same average-cost engine, emitting a point per realization event."""
    pool = await get_pool()
    trades = await pool.fetch(
        """
        SELECT asset, condition_id, outcome_index, side, size::float8 AS size,
               price::float8 AS price, ts
        FROM trades WHERE whale_id=$1 ORDER BY ts, id
        """,
        whale_id,
    )
    res_rows = await pool.fetch(
        "SELECT condition_id, resolved_prices, resolved_at FROM markets WHERE resolved"
    )
    resolutions: dict[str, tuple[list[float], datetime]] = {}
    for r in res_rows:
        prices = r["resolved_prices"]
        if isinstance(prices, str):
            prices = json.loads(prices)
        if prices:
            resolutions[r["condition_id"]] = (prices, r["resolved_at"])

    positions: dict[str, Position] = {}
    meta: dict[str, tuple[str | None, int | None]] = {}
    events: list[tuple[datetime, float]] = []
    for t in trades:
        pos = positions.setdefault(t["asset"], Position())
        meta.setdefault(t["asset"], (t["condition_id"], t["outcome_index"]))
        before = pos.realized_pnl
        pos.apply(Fill(side=t["side"], size=t["size"], price=t["price"]))
        if abs(pos.realized_pnl - before) > 1e-9:
            events.append((t["ts"], pos.realized_pnl - before))
    for asset, pos in positions.items():
        cid, idx = meta.get(asset, (None, None))
        if cid in resolutions and not pos.resolved and idx is not None:
            prices, resolved_at = resolutions[cid]
            if 0 <= idx < len(prices):
                before = pos.realized_pnl
                pos.resolve(float(prices[idx]))
                if abs(pos.realized_pnl - before) > 1e-9:
                    events.append((resolved_at or datetime.now(tz=timezone.utc),
                                   pos.realized_pnl - before))

    events.sort(key=lambda e: e[0])
    cum, out = 0.0, []
    for ts, amount in events:
        cum += amount
        out.append({"ts": ts.isoformat(), "cumulative_pnl": round(cum, 2)})
    return out


async def matrix(window: str = "all") -> dict:
    """The signature view: whales × sports, cell = ROI + W-L + P&L."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT s.whale_id, w.username, w.address, s.sport, s.markets_traded, s.wins, s.losses,
               s.scratches, s.win_pct::float8 AS win_pct, s.realized_pnl::float8 AS realized_pnl,
               s.notional::float8 AS notional, s.roi::float8 AS roi,
               s.open_exposure::float8 AS open_exposure
        FROM whale_sport_stats s JOIN whales w ON w.id = s.whale_id
        WHERE s.time_window = $1 AND w.active
        ORDER BY w.source_rank NULLS LAST, w.id, s.sport
        """,
        window,
    )
    cells = [dict(r) for r in rows]
    sports = sorted({c["sport"] for c in cells})
    return {"window": window, "sports": sports, "cells": cells}


async def events_view(limit: int = 50) -> list[dict]:
    """Per-event aggregation of tracked whales' net positions (agreement signal)."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT m.event_slug, m.event_title, m.sport,
               p.whale_id, w.username, p.outcome,
               sum(p.net_shares)::float8 AS net_shares,
               sum(p.net_shares * p.avg_cost)::float8 AS exposure,
               sum(p.realized_pnl)::float8 AS realized_pnl
        FROM positions p
        JOIN markets m USING (condition_id)
        JOIN whales w ON w.id = p.whale_id
        WHERE m.event_slug IS NOT NULL AND (p.net_shares > 0 OR p.realized_pnl <> 0)
        GROUP BY m.event_slug, m.event_title, m.sport, p.whale_id, w.username, p.outcome
        """
    )
    events: dict[str, dict] = {}
    for r in rows:
        ev = events.setdefault(
            r["event_slug"],
            {"event_slug": r["event_slug"], "event_title": r["event_title"],
             "sport": r["sport"], "positions": [], "total_exposure": 0.0},
        )
        ev["positions"].append(
            {"whale_id": r["whale_id"], "username": r["username"], "outcome": r["outcome"],
             "net_shares": r["net_shares"], "exposure": r["exposure"] or 0.0,
             "realized_pnl": r["realized_pnl"] or 0.0}
        )
        ev["total_exposure"] += r["exposure"] or 0.0
    ranked = sorted(events.values(), key=lambda e: e["total_exposure"], reverse=True)[:limit]
    for ev in ranked:
        outcomes = {p["outcome"] for p in ev["positions"] if p["net_shares"] > 0}
        ev["disagreement"] = len(outcomes) > 1
    return ranked


async def latency_stats(hours: int = 24) -> dict:
    from ..metrics import latency_summary

    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT source, EXTRACT(EPOCH FROM (detected_at - ts))::float8 AS lat
        FROM trades WHERE detected_at > now() - make_interval(hours => $1)
        """,
        hours,
    )
    by_source: dict[str, list[float]] = {"chain": [], "poll": []}
    all_lat: list[float] = []
    for r in rows:
        lat = max(r["lat"], 0.0)
        by_source.setdefault(r["source"], []).append(lat)
        all_lat.append(lat)
    return {
        "hours": hours,
        "overall": latency_summary(all_lat),
        "chain": latency_summary(by_source.get("chain", [])),
        "poll": latency_summary(by_source.get("poll", [])),
    }
