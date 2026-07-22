"""Analytics engine: rebuild positions from the ledger, track resolutions,
recompute per-sport rollups, validate against the leaderboard.

The engine is a deterministic full replay: positions and rollups are derived
state, always rebuildable from (trades × markets). Running it twice in a row
produces identical rows — restarts and replays are free.

Realized P&L is attributed in TIME: each sell realizes at the trade's
timestamp, each resolution realizes at the market's resolved_at. Windowed
stats (7d/30d) sum realization events inside the window; a market joins the
window's W-L record if any of its realization events fall inside it.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..db import get_pool
from ..sports import is_sport
from .positions import EPS, Fill, Position, market_result

log = logging.getLogger(__name__)

WINDOWS: dict[str, timedelta | None] = {"7d": timedelta(days=7), "30d": timedelta(days=30), "all": None}


@dataclass
class Realization:
    ts: datetime
    amount: float


@dataclass
class PositionState:
    whale_id: int
    condition_id: str | None
    token_id: str
    outcome: str | None
    outcome_index: int | None
    sport: str
    position: Position
    realizations: list[Realization] = field(default_factory=list)
    buys: list[tuple[datetime, float]] = field(default_factory=list)  # (ts, notional)
    first_ts: datetime | None = None
    last_ts: datetime | None = None


async def _load_resolutions() -> dict[str, tuple[list[float], datetime]]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT condition_id, resolved_prices, resolved_at FROM markets WHERE resolved"
    )
    out = {}
    for r in rows:
        prices = r["resolved_prices"]
        if isinstance(prices, str):
            prices = json.loads(prices)
        if prices:
            out[r["condition_id"]] = (prices, r["resolved_at"] or datetime.now(tz=timezone.utc))
    return out


async def rebuild_positions() -> list[PositionState]:
    """Replay the full trade ledger into per-(whale, token) position states."""
    pool = await get_pool()
    trades = await pool.fetch(
        """
        SELECT whale_id, asset, condition_id, outcome, outcome_index, side,
               size::float8 AS size, price::float8 AS price, notional::float8 AS notional,
               sport, ts
        FROM trades ORDER BY ts, id
        """
    )
    resolutions = await _load_resolutions()

    states: dict[tuple[int, str], PositionState] = {}
    for t in trades:
        key = (t["whale_id"], t["asset"])
        st = states.get(key)
        if st is None:
            st = states[key] = PositionState(
                whale_id=t["whale_id"],
                condition_id=t["condition_id"],
                token_id=t["asset"],
                outcome=t["outcome"],
                outcome_index=t["outcome_index"],
                sport=t["sport"],
                position=Position(),
            )
        # Later trades may carry enrichment the first one lacked.
        st.condition_id = st.condition_id or t["condition_id"]
        st.outcome = st.outcome or t["outcome"]
        st.outcome_index = st.outcome_index if st.outcome_index is not None else t["outcome_index"]
        if st.sport == "unclassified" and t["sport"] != "unclassified":
            st.sport = t["sport"]

        before = st.position.realized_pnl
        st.position.apply(Fill(side=t["side"], size=t["size"], price=t["price"]))
        delta = st.position.realized_pnl - before
        if abs(delta) > EPS:
            st.realizations.append(Realization(ts=t["ts"], amount=delta))
        if t["side"] == "BUY":
            st.buys.append((t["ts"], t["notional"]))
        st.first_ts = st.first_ts or t["ts"]
        st.last_ts = t["ts"]

    # Apply resolutions.
    for st in states.values():
        if st.condition_id and st.condition_id in resolutions and not st.position.resolved:
            prices, resolved_at = resolutions[st.condition_id]
            idx = st.outcome_index if st.outcome_index is not None else -1
            if 0 <= idx < len(prices):
                before = st.position.realized_pnl
                st.position.resolve(float(prices[idx]))
                delta = st.position.realized_pnl - before
                if abs(delta) > EPS:
                    st.realizations.append(Realization(ts=resolved_at, amount=delta))
            # Unknown outcome index on a resolved market: leave open; the
            # enrichment backfill will fix outcome_index and the next rebuild
            # will resolve it.

    await _persist_positions(list(states.values()))
    return list(states.values())


async def _persist_positions(states: list[PositionState]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM positions")
            for st in states:
                p = st.position
                await conn.execute(
                    """
                    INSERT INTO positions (whale_id, condition_id, token_id, outcome, outcome_index,
                                           net_shares, avg_cost, realized_pnl, notional_in,
                                           resolved, first_trade_ts, last_trade_ts, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
                    """,
                    st.whale_id,
                    st.condition_id,
                    st.token_id,
                    st.outcome,
                    st.outcome_index,
                    round(p.shares, 6),
                    round(p.avg_cost, 6),
                    round(p.realized_pnl, 6),
                    round(p.notional_in, 6),
                    p.resolved,
                    st.first_ts,
                    st.last_ts,
                )


def compute_rollups(
    states: list[PositionState], now: datetime | None = None
) -> list[dict]:
    """Pure aggregation of position states → whale × sport × window rows."""
    now = now or datetime.now(tz=timezone.utc)
    # Group token positions into markets first (W/L is per-market).
    by_market: dict[tuple[int, str, str], list[PositionState]] = defaultdict(list)
    for st in states:
        cid = st.condition_id or f"token:{st.token_id}"
        by_market[(st.whale_id, st.sport, cid)].append(st)

    rows: dict[tuple[int, str, str], dict] = {}
    for (whale_id, sport, _cid), legs in by_market.items():
        for window, span in WINDOWS.items():
            cutoff = now - span if span else None

            realized = sum(
                r.amount for st in legs for r in st.realizations if cutoff is None or r.ts >= cutoff
            )
            events_in_window = any(
                (cutoff is None or r.ts >= cutoff) for st in legs for r in st.realizations
            )
            traded_in_window = any(
                st.last_ts and (cutoff is None or st.last_ts >= cutoff) for st in legs
            )
            if not events_in_window and not traded_in_window:
                continue

            notional = sum(
                n for st in legs for (ts, n) in st.buys if cutoff is None or ts >= cutoff
            )
            open_exposure = sum(st.position.open_exposure for st in legs)
            fully_resolved = all(st.position.resolved for st in legs)

            key = (whale_id, sport, window)
            agg = rows.setdefault(
                key,
                {
                    "whale_id": whale_id,
                    "sport": sport,
                    "window": window,
                    "markets_traded": 0,
                    "wins": 0,
                    "losses": 0,
                    "scratches": 0,
                    "realized_pnl": 0.0,
                    "notional": 0.0,
                    "open_exposure": 0.0,
                },
            )
            agg["markets_traded"] += 1
            agg["realized_pnl"] += realized
            agg["notional"] += notional
            agg["open_exposure"] += open_exposure
            # W-L only for fully settled markets with realization in window.
            if fully_resolved and events_in_window:
                result = market_result(realized)
                agg["wins" if result == "win" else "losses" if result == "loss" else "scratches"] += 1

    out = []
    for agg in rows.values():
        settled = agg["wins"] + agg["losses"]
        agg["win_pct"] = round(agg["wins"] / settled, 4) if settled else None
        agg["roi"] = round(agg["realized_pnl"] / agg["notional"], 6) if agg["notional"] > 0 else None
        agg["avg_position"] = (
            round(agg["notional"] / agg["markets_traded"], 6) if agg["markets_traded"] else None
        )
        out.append(agg)
    return out


async def persist_rollups(rollups: list[dict]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM whale_sport_stats")
            for r in rollups:
                await conn.execute(
                    """
                    INSERT INTO whale_sport_stats
                        (whale_id, sport, time_window, markets_traded, wins, losses, scratches,
                         win_pct, realized_pnl, notional, roi, avg_position, open_exposure, computed_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,now())
                    """,
                    r["whale_id"],
                    r["sport"],
                    r["window"],
                    r["markets_traded"],
                    r["wins"],
                    r["losses"],
                    r["scratches"],
                    r["win_pct"],
                    round(r["realized_pnl"], 6),
                    round(r["notional"], 6),
                    r["roi"],
                    r["avg_position"],
                    round(r["open_exposure"], 6),
                )


async def validate_against_leaderboard(states: list[PositionState]) -> list[dict]:
    """Drift check: summed all-time realized P&L per whale vs leaderboard figure.

    >10% drift indicates a classification or lifecycle bug — surfaced to admins.
    """
    pool = await get_pool()
    whales = await pool.fetch(
        "SELECT id, username, sports_profit_alltime::float8 AS lb FROM whales WHERE active"
    )
    totals: dict[int, float] = defaultdict(float)
    for st in states:
        if is_sport(st.sport):
            totals[st.whale_id] += st.position.realized_pnl

    alerts = []
    for w in whales:
        lb = w["lb"]
        if not lb:
            continue
        ours = totals.get(w["id"], 0.0)
        drift = abs(ours - lb) / abs(lb)
        if drift > 0.10:
            alerts.append(
                {"whale_id": w["id"], "username": w["username"], "ours": round(ours, 2),
                 "leaderboard": lb, "drift_pct": round(drift * 100, 1)}
            )
    return alerts


async def run_cycle() -> dict:
    """One full analytics pass: rebuild → rollups → drift check."""
    states = await rebuild_positions()
    rollups = compute_rollups(states)
    await persist_rollups(rollups)
    alerts = await validate_against_leaderboard(states)
    return {"positions": len(states), "rollup_rows": len(rollups), "drift_alerts": alerts}
