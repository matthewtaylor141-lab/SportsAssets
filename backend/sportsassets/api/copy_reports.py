"""Management reports over the copy ledger (owner order 2026-08-28).

"I need management to be able to pull reports. Every trade on the
ledger assigned to the whale we are copying, with the latency between
when the whale placed the order and when we executed. Reports for each
whale, organizing results by sport, type of trade, daily, weekly,
monthly, all time."

Two surfaces, both uncapped, both over the audit table (live_orders)
joined to the whale ledger (trades) for latency:

- report(): whale × sport × category(Moneyline/Spread/Total/Segment/
  Player Prop) × period bucket (ET daily / ISO weekly / monthly /
  all-time) with n, W-L, staked, pnl, roi, and latency (mean + p50 of
  COALESCE(reaction_s, placed_at − trades.ts) — reaction_s is stamped
  by the executor at order fire; the COALESCE covers the sweep-reclaim
  lane whose reaction is NULL by design).
- ledger(): the order-level rows themselves, whale-assigned, with the
  full timestamp chain (whale fill → detected → placed → settled) and
  the latency next to every copy trade.

Copy sleeves only (COPY_WHALES); manual/underdog/arb and the software
cohort never appear here. Pure aggregation is separated from SQL for
unit tests, mirroring copies_record.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from statistics import median
from typing import Any

from .copies_record import COPY_WHALES, DISPLAY, RECORD_TZ
from .track_record import classify_slug

PERIODS = ("daily", "weekly", "monthly", "all")

LEDGER_SQL = """
SELECT lo.id,
       lower(COALESCE(lo.whale_username, ''))              AS whale,
       to_char(lo.settled_at AT TIME ZONE 'America/New_York',
               'YYYY-MM-DD')                               AS day,
       lo.us_market_slug                                   AS slug,
       lo.side, lo.status, lo.venue,
       COALESCE(raw #>> '{response,executions,0,order,intent}',
                raw #>> '{preview,intent}')                AS intent,
       lo.his_price::float8                                AS his_price,
       lo.fill_price::float8                               AS fill_price,
       COALESCE(NULLIF(lo.filled_usd, 0),
                lo.requested_usd)::float8                  AS stake,
       lo.filled_shares::float8                            AS shares,
       lo.pnl::float8                                      AS pnl,
       t.ts                                                AS whale_ts,
       t.detected_at                                       AS detected_at,
       lo.placed_at, lo.settled_at,
       COALESCE(lo.reaction_s,
                EXTRACT(EPOCH FROM (lo.placed_at - t.ts)))::float8
                                                           AS latency_s,
       EXTRACT(EPOCH FROM (t.detected_at - t.ts))::float8  AS detect_lag_s
FROM live_orders lo
LEFT JOIN trades t ON t.id = lo.trade_id
WHERE lower(COALESCE(lo.whale_username, '')) = ANY($1::text[])
  AND lo.status IN ('settled', 'cashed_out', 'filled')
ORDER BY COALESCE(lo.settled_at, lo.placed_at) DESC
"""


def _bucket(day: str, period: str) -> str:
    """ET day string -> period bucket key. Pure; tested."""
    if period == "all":
        return "all"
    if period == "daily" or not day:
        return day or "undated"
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return "undated"
    if period == "weekly":
        # ISO week, keyed by its Monday — reads as a date, sorts as one
        monday = date.fromordinal(d.toordinal() - d.weekday())
        return monday.isoformat()
    return day[:7]                                    # monthly YYYY-MM


def report(rows: list[dict], period: str = "monthly") -> dict:
    """whale × sport × category × bucket aggregation. Rows are ledger()
    dicts (settled/cashed_out only are counted; open rows skipped).
    Pure; tested."""
    cells: dict[tuple, dict] = {}
    whale_tot: dict[str, dict] = {}
    lat_all: list[float] = []
    for r in rows:
        if r.get("status") not in ("settled", "cashed_out"):
            continue
        disp = DISPLAY.get(r["whale"], r["whale"])
        cls = classify_slug(r.get("slug") or "")
        bucket = _bucket(r.get("day") or "", period)
        pnl = float(r.get("pnl") or 0)
        stake = float(r.get("stake") or 0)
        lat = r.get("latency_s")
        key = (disp, cls["sport"], cls["category"], bucket)
        cell = cells.setdefault(key, {
            "whale": disp, "sport": cls["sport"],
            "category": cls["category"], "bucket": bucket,
            "n": 0, "wins": 0, "losses": 0,
            "staked": 0.0, "pnl": 0.0, "_lat": []})
        wt = whale_tot.setdefault(disp, {
            "whale": disp, "n": 0, "wins": 0, "losses": 0,
            "staked": 0.0, "pnl": 0.0, "_lat": []})
        for b in (cell, wt):
            b["n"] += 1
            b["wins"] += 1 if pnl > 0 else 0
            b["losses"] += 1 if pnl < 0 else 0
            b["staked"] = round(b["staked"] + stake, 2)
            b["pnl"] = round(b["pnl"] + pnl, 2)
            if isinstance(lat, (int, float)) and lat >= 0:
                b["_lat"].append(float(lat))
        if isinstance(lat, (int, float)) and lat >= 0:
            lat_all.append(float(lat))

    def _fin(b: dict) -> dict:
        lats = b.pop("_lat")
        b["roi"] = round(b["pnl"] / b["staked"], 4) if b["staked"] else None
        b["lat_avg_s"] = round(sum(lats) / len(lats), 2) if lats else None
        b["lat_p50_s"] = round(median(lats), 2) if lats else None
        b["lat_n"] = len(lats)
        return b

    out_rows = sorted((_fin(c) for c in cells.values()),
                      key=lambda c: (c["whale"], c["bucket"],
                                     c["sport"], c["category"]),
                      reverse=True)
    by_whale = sorted((_fin(w) for w in whale_tot.values()),
                      key=lambda w: -w["pnl"])
    return {
        "period": period,
        "rows": out_rows,
        "by_whale": by_whale,
        "latency": {
            "n": len(lat_all),
            "avg_s": round(sum(lat_all) / len(lat_all), 2) if lat_all else None,
            "p50_s": round(median(lat_all), 2) if lat_all else None,
        },
        "generated_at": datetime.now(RECORD_TZ).isoformat(),
    }


def ledger_rows(raw: list[dict], since: str = "",
                until: str = "") -> list[dict]:
    """Normalize + window the SQL rows for the ledger surface. Every
    copy trade carries its whale and its latency. Pure; tested."""
    out = []
    for r in raw:
        day = r.get("day") or ""
        if since and day and day < since:
            continue
        if until and day and day > until:
            continue
        cls = classify_slug(r.get("slug") or "")
        lat = r.get("latency_s")
        out.append({
            "id": r.get("id"),
            "whale": DISPLAY.get(r["whale"], r["whale"]),
            "day": day or None,
            "slug": r.get("slug"),
            "sport": cls["sport"], "category": cls["category"],
            "side": r.get("side"), "venue": r.get("venue"),
            "status": r.get("status"),
            "his_price": r.get("his_price"),
            "fill_price": r.get("fill_price"),
            "stake": round(float(r.get("stake") or 0), 2),
            "shares": r.get("shares"),
            "pnl": (round(float(r["pnl"]), 2)
                    if r.get("pnl") is not None else None),
            "whale_ts": _iso(r.get("whale_ts")),
            "detected_at": _iso(r.get("detected_at")),
            "placed_at": _iso(r.get("placed_at")),
            "settled_at": _iso(r.get("settled_at")),
            "latency_s": (round(float(lat), 2)
                          if isinstance(lat, (int, float)) else None),
            "detect_lag_s": (round(float(r["detect_lag_s"]), 2)
                             if isinstance(r.get("detect_lag_s"),
                                           (int, float)) else None),
        })
    return out


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


REPORT_CSV_COLS = ("whale", "bucket", "sport", "category", "n", "wins",
                   "losses", "staked", "pnl", "roi", "lat_avg_s",
                   "lat_p50_s", "lat_n")
LEDGER_CSV_COLS = ("id", "whale", "day", "slug", "sport", "category",
                   "side", "venue", "status", "his_price", "fill_price",
                   "stake", "shares", "pnl", "whale_ts", "detected_at",
                   "placed_at", "settled_at", "latency_s",
                   "detect_lag_s")


def to_csv(rows: list[dict], cols: tuple) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") if r.get(c) is not None else ""
                    for c in cols])
    return buf.getvalue()


async def fetch_ledger(pool) -> list[dict]:
    rows = await pool.fetch(LEDGER_SQL, list(COPY_WHALES))
    return [dict(r) for r in rows]
