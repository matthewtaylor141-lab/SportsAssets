"""Live calibration: recompute the edge-engine's measured tables from OUR
continuously-updated whale ledger, on rolling windows.

Methodology matches the Phase 1 study's calib_*.csv exactly: for every BUY
fill on a resolved market with a known outcome, edge_if_held = payout - price.
Aggregated by 5-cent entry band, league slug prefix, and fill-size decile —
so drift against edge-engine/config/{bands,leagues}.yaml is directly visible.
"""

from __future__ import annotations

from ..db import get_pool

_BASE_FILTER = """
    FROM trades t
    JOIN markets m ON m.condition_id = t.condition_id
    WHERE t.side = 'BUY'
      AND m.resolved
      AND t.outcome_index IS NOT NULL AND t.outcome_index BETWEEN 0 AND 31
      AND jsonb_array_length(m.resolved_prices) > t.outcome_index
      AND t.ts > now() - make_interval(days => $1)
"""

_PAYOUT = "((m.resolved_prices -> t.outcome_index)::text)::float8"


async def by_band(window_days: int = 90) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        SELECT floor(t.price * 20) / 20.0 AS band_lo,
               count(*)::int AS n,
               sum(t.size)::float8 AS shares,
               sum(t.notional)::float8 AS stake,
               sum(t.size * ({_PAYOUT} - t.price))::float8 AS pnl_if_held,
               sum(t.size * {_PAYOUT}) / NULLIF(sum(t.size), 0) AS win_rate_w
        {_BASE_FILTER}
        GROUP BY 1 ORDER BY 1
        """,
        window_days,
    )
    out = []
    for r in rows:
        stake = r["stake"] or 0.0
        shares = r["shares"] or 0.0
        out.append({
            "band": f"{float(r['band_lo']):.2f}-{float(r['band_lo']) + 0.05:.2f}",
            "n": r["n"],
            "stake": round(stake, 2),
            # Cents of P&L per contract — directly comparable to bands.yaml edge_cents.
            "edge_cents": round(100 * r["pnl_if_held"] / shares, 4) if shares else None,
            "roi_if_held": round(r["pnl_if_held"] / stake, 6) if stake else None,
            "win_rate_weighted": round(float(r["win_rate_w"]), 6)
            if r["win_rate_w"] is not None else None,
        })
    return out


async def by_league(window_days: int = 90, min_fills: int = 50) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        SELECT split_part(COALESCE(m.event_slug, m.slug, ''), '-', 1) AS prefix,
               count(*)::int AS n,
               sum(t.notional)::float8 AS stake,
               sum(t.size * ({_PAYOUT} - t.price))::float8 AS pnl_if_held
        {_BASE_FILTER}
          AND COALESCE(m.event_slug, m.slug, '') <> ''
        GROUP BY 1 HAVING count(*) >= $2
        ORDER BY 4 DESC
        """,
        window_days, min_fills,
    )
    return [
        {"prefix": r["prefix"], "n": r["n"], "stake": round(r["stake"], 2),
         "roi_if_held": round(r["pnl_if_held"] / r["stake"], 6) if r["stake"] else None,
         "pnl_if_held": round(r["pnl_if_held"], 2)}
        for r in rows
    ]


async def by_size_bucket(window_days: int = 90) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        f"""
        SELECT CASE
                 WHEN t.notional <= 10 THEN '(0, 10]'
                 WHEN t.notional <= 50 THEN '(10, 50]'
                 WHEN t.notional <= 250 THEN '(50, 250]'
                 WHEN t.notional <= 1000 THEN '(250, 1000]'
                 WHEN t.notional <= 10000 THEN '(1000, 10000]'
                 WHEN t.notional <= 50000 THEN '(10000, 50000]'
                 ELSE '(50000, inf)'
               END AS bucket,
               count(*)::int AS n,
               sum(t.notional)::float8 AS stake,
               sum(t.size * ({_PAYOUT} - t.price))::float8 AS pnl_if_held
        {_BASE_FILTER}
        GROUP BY 1 ORDER BY min(t.notional)
        """,
        window_days,
    )
    return [
        {"bucket": r["bucket"], "n": r["n"], "stake": round(r["stake"], 2),
         "roi_if_held": round(r["pnl_if_held"] / r["stake"], 6) if r["stake"] else None}
        for r in rows
    ]


async def full_report(window_days: int = 90) -> dict:
    """Rolling recalibration snapshot; compare against edge-engine configs to
    see drift (edge decay, newly profitable leagues, dead zones moving)."""
    return {
        "window_days": window_days,
        "by_band": await by_band(window_days),
        "by_league": await by_league(window_days),
        "by_size": await by_size_bucket(window_days),
    }
