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
    """Replay the full trade ledger into per-(whale, token) position states.

    STREAMED, never fetched whole. `pool.fetch` materialized every trade row
    in memory at once; once the deep backfill grew the table past what the
    512 MB workers instance could hold, the fetch OOM-killed the process
    mid-query on every attempt — the analytics loop never completed a cycle
    (and so never heartbeat) from 2026-07-22 onward, freezing settlements,
    rollups and the site's return figures for 12 days while every OTHER
    loop beat normally between restarts. A server-side cursor keeps memory
    flat no matter how large the ledger grows; the states dict is bounded
    by distinct (whale, token) positions, not by fill count.
    """
    pool = await get_pool()
    resolutions = await _load_resolutions()

    states: dict[tuple[int, str], PositionState] = {}

    def _replay(t) -> None:
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

    async with pool.acquire() as conn:
        async with conn.transaction():
            async for t in conn.cursor(
                """
                SELECT whale_id, asset, condition_id, outcome, outcome_index, side,
                       size::float8 AS size, price::float8 AS price,
                       notional::float8 AS notional, sport, ts
                FROM trades ORDER BY ts, id
                """,
                prefetch=5_000,
            ):
                _replay(t)

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
    # NOT NULL rescue (incident 2026-08-11/12): a single un-enriched
    # trade row (null condition_id — always the same RN1 asset) made
    # ONE tuple violate positions.condition_id NOT NULL, the batch
    # insert threw, and the surrounding transaction rolled back — so
    # the WHOLE positions snapshot froze, every cycle, for days. The
    # whale's new fills kept settling but attribution read the stale
    # snapshot and dumped them in 'unattributed'. Recover the id from
    # the token catalog when it knows the token; anything still
    # unknown is dead-lettered LOUDLY while the rest of the book
    # persists — one bad row must never freeze 7,000 good ones.
    missing = [st for st in states if not st.condition_id and st.token_id]
    if missing:
        try:
            found = await pool.fetch(
                "SELECT token_id, condition_id FROM market_tokens "
                "WHERE token_id = ANY($1::text[])",
                [str(st.token_id) for st in missing])
            by_tok = {str(r["token_id"]): r["condition_id"] for r in found}
            for st in missing:
                st.condition_id = by_tok.get(str(st.token_id))
        except Exception:  # noqa: BLE001 — rescue is best-effort
            pass
    persistable = [st for st in states if st.condition_id]
    dropped = len(states) - len(persistable)
    if dropped:
        log.warning(
            "positions persist: %d row(s) still missing condition_id "
            "after token-catalog rescue — dead-lettered (tokens: %s); "
            "the snapshot persists without them",
            dropped,
            [st.token_id for st in states if not st.condition_id][:5])
    rows = [
        (st.whale_id, st.condition_id, st.token_id, st.outcome, st.outcome_index,
         round(st.position.shares, 6), round(st.position.avg_cost, 6),
         round(st.position.realized_pnl, 6), round(st.position.notional_in, 6),
         st.position.resolved, st.first_ts, st.last_ts)
        for st in persistable
    ]
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM positions")
            # One round-trip per BATCH, not per position: at post-backfill
            # scale a per-row execute made each cycle minutes long.
            await conn.executemany(
                """
                INSERT INTO positions (whale_id, condition_id, token_id, outcome, outcome_index,
                                       net_shares, avg_cost, realized_pnl, notional_in,
                                       resolved, first_trade_ts, last_trade_ts, updated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,now())
                """,
                rows,
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


async def settle_engine_fills() -> int:
    """Settle OUR internal engine fills (Polymarket) from the same resolution
    data that settles whale positions. pnl = (payout - price) * shares."""
    pool = await get_pool()
    status = await pool.execute(
        """
        UPDATE engine_fills ef
        SET settled = TRUE,
            payout = p.payout,
            pnl = (p.payout - ef.limit_price) * (ef.size_usd / NULLIF(ef.limit_price, 0)),
            settled_at = COALESCE(p.resolved_at, now())
        FROM (
            SELECT mt.token_id, ((m.resolved_prices -> mt.outcome_index)::text)::float8 AS payout,
                   m.resolved_at
            FROM market_tokens mt
            JOIN markets m USING (condition_id)
            WHERE m.resolved AND mt.outcome_index IS NOT NULL
              AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
        ) p
        WHERE NOT ef.settled AND ef.venue = 'polymarket' AND ef.outcome_id = p.token_id
        """
    )
    return int(status.split()[-1]) if status else 0


async def settle_ai_trades() -> int:
    """Settle AI TRADER paper fills from the same resolution data.
    pnl = (payout - fill_vwap) * shares; counterfactual = the same clip at
    HIS price — the difference is the measured cost of copying late."""
    pool = await get_pool()
    resolved_sub = """
        SELECT mt.token_id, ((m.resolved_prices -> mt.outcome_index)::text)::float8 AS payout,
               m.resolved_at
        FROM market_tokens mt
        JOIN markets m USING (condition_id)
        WHERE m.resolved AND mt.outcome_index IS NOT NULL
          AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
    """
    status = await pool.execute(
        f"""
        UPDATE ai_trades a
        SET status = 'settled', payout = p.payout,
            pnl = (p.payout - a.fill_vwap) * a.shares,
            counterfactual_pnl = (p.payout - a.his_price)
                                 * (a.clip_target / NULLIF(a.his_price, 0)),
            settled_at = COALESCE(p.resolved_at, now())
        FROM ({resolved_sub}) p
        WHERE a.status = 'open' AND a.asset = p.token_id
        """
    )
    settled = int(status.split()[-1]) if status else 0
    # Missed copies still get the counterfactual at resolution — that IS the
    # opportunity cost of an empty book.
    await pool.execute(
        f"""
        UPDATE ai_trades a
        SET payout = p.payout,
            counterfactual_pnl = (p.payout - a.his_price)
                                 * (a.clip_target / NULLIF(a.his_price, 0)),
            settled_at = COALESCE(p.resolved_at, now())
        FROM ({resolved_sub}) p
        WHERE a.status = 'missed' AND a.payout IS NULL AND a.asset = p.token_id
        """
    )
    return settled


def allocate_venue_pnl(target: float, rows: list[dict]) -> dict:
    """Split one market's venue-true realized P&L across our rows on that
    market, pro-rata by filled cost (equal split when no cost recorded).
    The last row takes the rounding remainder so the per-market sum
    matches the venue to the cent."""
    base = sum(float(r["filled_usd"] or 0) for r in rows)
    out: dict = {}
    acc = 0.0
    for i, r in enumerate(rows):
        if i == len(rows) - 1:
            out[r["id"]] = round(target - acc, 4)
        else:
            share = ((float(r["filled_usd"] or 0) / base) if base
                     else 1.0 / len(rows))
            p = round(target * share, 4)
            out[r["id"]] = p
            acc += p
    return out


async def _settle_pmus_from_venue(pool, *,
                                  rescore_since: str | None = None) -> dict:
    """Settle (or, with rescore_since, RESTATE) US-venue rows from the
    venue's own ledger. A copy executes on Polymarket US against
    us_market_slug, so its result is the venue's POSITION_RESOLUTION
    realized for that slug — never the whale's global token payout,
    which graded our rows by the WHALE'S outcome and hid wrong-side
    mappings and voids (owner emergency 2026-08-23). Rows whose market
    has no venue verdict yet stay untouched. The venue figure is
    cumulative per position, so P&L already booked by cash-out rows on
    the same market is subtracted before allocation."""
    from datetime import datetime as _dt, timedelta as _td

    from ..api.pmus_account import resolution_truth

    if rescore_since:
        # asyncpg demands a date OBJECT for a date parameter — the str
        # form 500'd instantly here, which is why the startup
        # restatement failed silently on every boot (2026-08-24).
        since_d = _dt.fromisoformat(rescore_since).date()
        rows = await pool.fetch(
            """
            SELECT id, lower(us_market_slug) AS slug,
                   COALESCE(whale_username, '?') AS whale,
                   COALESCE(filled_usd, 0)::float8 AS filled_usd,
                   COALESCE(pnl, 0)::float8 AS pnl, status
            FROM live_orders
            WHERE us_market_slug IS NOT NULL
              AND status IN ('filled', 'settled')
              AND placed_at >= $1
            ORDER BY id
            """, since_d)
        from_day = rescore_since
    else:
        rows = await pool.fetch(
            """
            SELECT id, lower(us_market_slug) AS slug,
                   COALESCE(whale_username, '?') AS whale,
                   COALESCE(filled_usd, 0)::float8 AS filled_usd,
                   COALESCE(pnl, 0)::float8 AS pnl, status
            FROM live_orders
            WHERE us_market_slug IS NOT NULL AND status = 'filled'
            ORDER BY id
            """)
        oldest = await pool.fetchval(
            "SELECT min(placed_at) FROM live_orders "
            "WHERE us_market_slug IS NOT NULL AND status = 'filled'")
        from_day = ((oldest - _td(days=1)).date().isoformat()
                    if oldest else None)
    summary = {"settled": 0, "changed": 0, "delta": 0.0,
               "slugs": 0, "no_truth": 0, "whales": {}}
    if not rows or not from_day:
        return summary

    sold = await pool.fetch(
        """
        SELECT lower(us_market_slug) AS slug,
               COALESCE(sum(pnl), 0)::float8 AS pnl
        FROM live_orders
        WHERE us_market_slug IS NOT NULL AND status = 'cashed_out'
        GROUP BY 1
        """)
    sold_by = {r["slug"]: float(r["pnl"]) for r in sold}
    truth = await resolution_truth(from_day)
    # The venue's live activity export only pages back a few days
    # (first full restatement 2026-08-24: 1,150 of 1,229 markets out
    # of reach). The platform's own append-only archive holds every
    # activity from day one — fill the gap from it; the live crawl
    # wins on markets both sources carry (it is fresher).
    try:
        since_ts = _dt.fromisoformat(from_day).replace(
            tzinfo=timezone.utc).timestamp()
        arch = await pool.fetch(
            """
            SELECT payload->'positionResolution' AS pr, ts
            FROM pmus_activity_archive
            WHERE payload->>'type' = 'ACTIVITY_TYPE_POSITION_RESOLUTION'
              AND ts >= $1
            ORDER BY ts
            """, since_ts)

        def _amt(v) -> float:
            if isinstance(v, dict):
                v = v.get("value")
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        arch_truth: dict[str, dict] = {}
        for r in arch:
            pr = r["pr"]
            if isinstance(pr, str):
                pr = json.loads(pr)
            pr = pr or {}
            slug = (pr.get("marketSlug") or "").lower()
            if not slug:
                continue
            realized = (_amt((pr.get("afterPosition") or {}).get("realized"))
                        or _amt((pr.get("beforePosition") or {})
                                .get("realized")))
            ts_iso = (_dt.fromtimestamp(float(r["ts"] or 0), timezone.utc)
                      .isoformat() if r["ts"] else "")
            arch_truth[slug] = {"realized": realized, "ts": ts_iso}
        added = 0
        for slug, t in arch_truth.items():
            if slug not in truth:
                truth[slug] = t
                added += 1
        # Diagnostics ride the summary (2026-08-24: the first archive
        # run silently added nothing — never again invisible).
        summary["archive"] = {"scanned": len(arch),
                              "slugs": len(arch_truth), "added": added,
                              "err": None}
    except Exception as exc:  # noqa: BLE001 — narrows coverage, visibly
        summary["archive"] = {"err": f"{type(exc).__name__}: "
                                     f"{str(exc)[:180]}"}
        log.exception("archive resolution truth unavailable")

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["slug"], []).append(dict(r))

    def _whale_acc(w: str) -> dict:
        return summary["whales"].setdefault(w, {"old": 0.0, "new": 0.0,
                                                "rows": 0})

    for slug, grp in groups.items():
        t = truth.get(slug)
        if t is None:
            summary["no_truth"] += 1
            continue
        target = float(t["realized"]) - sold_by.get(slug, 0.0)
        alloc = allocate_venue_pnl(target, grp)
        ts = None
        if t.get("ts"):
            try:
                ts = _dt.fromisoformat(str(t["ts"]).replace("Z", "+00:00"))
            except ValueError:
                ts = None
        summary["slugs"] += 1
        for r in grp:
            newp = alloc[r["id"]]
            oldp = float(r["pnl"]) if r["status"] == "settled" else 0.0
            if r["status"] == "settled" and abs(newp - oldp) < 0.005:
                continue
            await pool.execute(
                """
                UPDATE live_orders
                SET status = 'settled', pnl = $2,
                    settled_at = COALESCE($3, settled_at, now())
                WHERE id = $1
                """, r["id"], newp, ts)
            summary["settled"] += 1
            if r["status"] == "settled":
                summary["changed"] += 1
            summary["delta"] = round(summary["delta"] + newp - oldp, 4)
            w = _whale_acc(r["whale"])
            w["old"] = round(w["old"] + oldp, 4)
            w["new"] = round(w["new"] + newp, 4)
            w["rows"] += 1
    return summary


async def pool_settle_live() -> int:
    """Settle LIVE beta fills from the venue's OWN ledger (owner
    emergency 2026-08-23). The old implementation joined a.asset — the
    whale's GLOBAL token — against global resolutions and paid
    (payout - fill_price) * filled_shares, i.e. it graded every row by
    the whale's outcome. Wrong-side/wrong-market mappings and venue
    voids were booked as the whale's wins for two weeks while the venue
    paid the opposite. US-venue rows now settle exclusively from
    resolution_truth; a row with no venue verdict stays 'filled' —
    never fall back to the whale-token join for a us_market_slug row.
    Legacy global-CLOB rows (no us_market_slug) keep the old join."""
    pool = await get_pool()
    n = 0
    try:
        summary = await _settle_pmus_from_venue(pool)
        n = summary["settled"]
        if n:
            log.info("venue-truth settle: %s", summary)
    except Exception:  # noqa: BLE001 — leave rows filled, next cycle retries
        log.exception("venue-truth settle failed; rows left filled")
    status = await pool.execute(
        """
        UPDATE live_orders a
        SET status = 'settled', payout = p.payout,
            pnl = (p.payout - a.fill_price) * a.filled_shares,
            settled_at = COALESCE(p.resolved_at, now())
        FROM (
            SELECT mt.token_id, ((m.resolved_prices -> mt.outcome_index)::text)::float8 AS payout,
                   m.resolved_at
            FROM market_tokens mt
            JOIN markets m USING (condition_id)
            WHERE m.resolved AND mt.outcome_index IS NOT NULL
              AND jsonb_array_length(m.resolved_prices) > mt.outcome_index
        ) p
        WHERE a.status = 'filled' AND a.asset = p.token_id
          AND a.us_market_slug IS NULL
        """
    )
    return n + (int(status.split()[-1]) if status else 0)


async def run_cycle() -> dict:
    """One full analytics pass: rebuild → rollups → drift check → engine settle."""
    states = await rebuild_positions()
    rollups = compute_rollups(states)
    await persist_rollups(rollups)
    alerts = await validate_against_leaderboard(states)
    engine_settled = await settle_engine_fills()
    ai_settled = await settle_ai_trades()
    # LIVE beta orders settle with the same resolution data.
    await pool_settle_live()
    return {"positions": len(states), "rollup_rows": len(rollups), "drift_alerts": alerts,
            "engine_settled": engine_settled, "ai_settled": ai_settled}
