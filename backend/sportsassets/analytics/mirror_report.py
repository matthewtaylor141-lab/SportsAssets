"""What the position-mirroring shadow has read (phase P0, 2026-09-02):
the ratio per whale, the latest row per market, and the counts that
decide whether P1 may turn orders on -- how often the resting order the
mirror would have placed was already fillable against the book, how far
the fill-derived position drifts from the exit worker's snapshot, and
how much of his activity mapped to a venue market at all."""
from __future__ import annotations

import json
from typing import Any


def summarize(latest: list[dict], all_rows: list[dict], ratios: dict) -> dict:
    """latest: newest row per (whale, market); all_rows: every row in the
    window. Pure, so the reading is testable."""
    n = len(all_rows)
    mapped = [r for r in all_rows if r.get("us_market_slug")]
    orders = [r for r in mapped if r.get("would_side")]
    # a plan is judged against the NEXT reading of its market; a plan
    # with no later reading is unresolved and counted on neither side
    resolved = [r for r in orders if r.get("would_fill") is not None]
    fills = [r for r in resolved if r.get("would_fill")]
    frozen = [r for r in mapped if str(r.get("reason") or "").startswith("frozen")]
    # drift = |fills-derived - venue-read| over the larger of the two, so
    # "fills say he holds, the venue says he is out" (snap 0, his > 0)
    # counts as full drift instead of being dropped (review round two)
    drift = []
    for r in mapped:
        hl, sl = r.get("his_long"), r.get("snap_long")
        if hl is None or sl is None:
            continue
        big = max(float(hl), float(sl))
        if big > 0:
            drift.append(abs(float(hl) - float(sl)) / big)
    drift_sorted = sorted(drift)
    # nearest-rank 90th percentile: the smallest value at or above 90%
    # of the readings (two readings -> the larger one)
    p90 = (drift_sorted[min(len(drift_sorted) - 1,
                            max(0, -(-9 * len(drift_sorted) // 10) - 1))]
           if drift_sorted else None)
    games = {r.get("condition_id") for r in mapped}
    # a stale positions read is excluded from drift by the worker (its
    # snap_* columns are NULL) and counted here so the gap is visible
    stale = 0
    for r in mapped:
        d = r.get("detail")
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except ValueError:
                d = {}
        if isinstance(d, dict) and d.get("snap_stale"):
            stale += 1
    out: dict[str, Any] = {
        "rows": n, "mapped_rows": len(mapped), "unmapped_rows": n - len(mapped),
        "markets": len({r.get("condition_id") for r in all_rows}),
        "mapped_markets": len(games),
        "would_orders": len(orders), "would_resolved": len(resolved), "would_fill": len(fills),
        "would_fill_rate": round(len(fills) / len(resolved), 4) if resolved else None,
        "frozen_rows": len(frozen),
        "drift_n": len(drift), "drift_p90": round(p90, 4) if p90 is not None else None,
        "drift_over_5pct": sum(1 for d in drift if d > 0.05),
        "stale_snapshot_rows": stale,
        "ratios": ratios,
        "latest": [{k: r.get(k) for k in (
            "at", "whale", "condition_id", "us_market_slug", "his_long", "his_other",
            "his_net", "snap_long", "snap_other", "ratio", "target", "capped",
            "ledger_net", "venue_net", "bid", "ask", "mark", "his_last_px",
            "would_side", "would_qty", "would_px", "would_fill", "reason")}
            for r in latest],
    }
    out["reading"] = (
        f"shadow: {len(games)} mapped markets of {out['markets']} in the window; "
        f"would have placed {len(orders)} orders; of {len(resolved)} judged against the next "
        f"reading, {len(fills)} would have filled ({out['would_fill_rate'] if resolved else 'n/a'}); drift p90 "
        f"{out['drift_p90'] if p90 is not None else 'n/a'} on {len(drift)} snapshot reads; "
        f"{len(frozen)} frozen (venue/ledger disagree). NO ORDERS PLACED (P0)."
    )
    return out


async def mirror_shadow_report(pool: Any, hours: float = 24.0,
                               whale: str | None = None) -> dict:
    args: list[Any] = [float(hours)]
    wf = ""
    if whale:
        args.append(whale.lower())
        wf = "AND whale = $2"
    try:
        all_rows = [dict(r) for r in await pool.fetch(
            f"""
            SELECT at, whale, condition_id, us_market_slug, his_long, his_other, his_net,
                   snap_long, snap_other, ratio, target, capped, ledger_net, venue_net,
                   bid, ask, mark, his_last_px, would_side, would_qty, would_px,
                   would_fill, reason, detail::text AS detail
              FROM mirror_shadow
             WHERE at >= now() - ($1::float8 * interval '1 hour') {wf}
             ORDER BY at DESC
            """, *args)]
    except Exception as exc:  # noqa: BLE001 — table absent until 046
        return {"rows": 0, "error": f"unavailable: {type(exc).__name__}", "latest": []}
    latest: dict[tuple, dict] = {}
    for r in all_rows:                       # newest first
        key = (r.get("whale"), r.get("condition_id"))
        latest.setdefault(key, r)
    ratios: dict = {}
    try:
        raw = await pool.fetchval("SELECT value FROM ingestion_state WHERE key='mirror_ratio'")
        if raw:
            ratios = raw if isinstance(raw, dict) else json.loads(raw)
    except Exception:  # noqa: BLE001
        ratios = {}
    for r in all_rows:
        if hasattr(r.get("at"), "isoformat"):
            r["at"] = r["at"].isoformat()
    out = summarize(list(latest.values()), all_rows, ratios)
    out["hours"] = float(hours)
    out["whale"] = whale.lower() if whale else None
    return out


__all__ = ["summarize", "mirror_shadow_report"]
