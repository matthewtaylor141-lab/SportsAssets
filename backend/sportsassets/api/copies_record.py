"""The COPIES cohort — the record the copy-trading thesis stands on.

Owner order 2026-08-20 morning ("I am going to have to start showing
that our system is profitable"): the whale-copy sleeves are the
profitable core (+$5.3k uncapped since Aug 1: RN1 +$2.5k, swisstony
+$2.1k, 0x2c33, HRH) and the account headline buries them under the
retired engine's residue. This surface is the copies-only scorecard —
uncapped, venue-backed via the order-level audit table (live_orders),
per-whale split and daily series — served publicly so the site can
headline the number the business is actually built on.

Pure aggregation is separated from the endpoint for unit tests.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

RECORD_TZ = ZoneInfo("America/New_York")

# The copy sources. 'underdog', 'arb', 'manual' and the engine's own
# categories are NOT copies and never count here; an unknown username
# is excluded rather than guessed in (fail closed — a new whale joins
# this list when the owner promotes one).
COPY_WHALES = frozenset({
    "rn1", "swisstony", "kch123", "homerunhazard",
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465",
    # Dossier promotions (owner order 2026-08-21): $100 probation clips.
    "ferrarichampions2026", "0x076daa87",
})

# Display names for the per-whale split (addresses are unreadable).
DISPLAY = {
    "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465": "0x2c33",
    "rn1": "RN1",
    "swisstony": "SwissTony",
    "kch123": "kch123",
    "homerunhazard": "HomeRunHazard",
    "ferrarichampions2026": "ferrariChampions2026",
    "0x076daa87": "0x076daa87",
}


# CRYPTO copy sources (owner order 2026-08-21): detected by the fast
# lane and served to the engine's Kalshi crypto leg — deliberately NOT
# in COPY_WHALES, so the Polymarket sports record and executor never
# see them.
CRYPTO_WHALES = frozenset({"0xf705fa04", "jnstrtprdctnmrkts"})


# Sleeves that are neither copies nor "software": their P&L is its own
# story (dog experiment, arb class, the desk's manual relay tickets).
NON_COPY_SLEEVES = frozenset({"underdog", "arb", "manual"})


def software_scorecard(rows: list[dict]) -> dict:
    """The complement cohort (partner report, owner order 2026-08-20
    evening): every settled order that is NOT a whale copy and NOT a
    named non-copy sleeve — the retired engine's book plus unattributed
    residue. Uncapped daily series so 'what the software cost us, day by
    day' is a served number instead of a capped display artifact."""
    rows = [r for r in rows
            if (r.get("whale") or "") not in COPY_WHALES
            and (r.get("whale") or "") not in NON_COPY_SLEEVES]
    total = {"settled": 0, "wins": 0, "losses": 0,
             "pnl": 0.0, "staked": 0.0}
    by_day: dict[str, dict] = {}
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        stake = float(r.get("filled_usd") or 0)
        for b in (total,
                  by_day.setdefault(r.get("day") or "undated", {
                      "day": r.get("day") or "undated",
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0})):
            b["settled"] += 1
            b["wins"] += 1 if pnl > 0 else 0
            b["losses"] += 1 if pnl < 0 else 0
            b["pnl"] = round(b["pnl"] + pnl, 2)
            b["staked"] = round(b["staked"] + stake, 2)
    return {"cohort": "software", "uncapped": True, "total": total,
            "daily": sorted((d for d in by_day.values()
                             if d["day"] != "undated"),
                            key=lambda d: d["day"], reverse=True)[:62]}


def scorecard(rows: list[dict]) -> dict:
    """rows: settled live_orders rows with keys whale (lowercased),
    day (ET YYYY-MM-DD), pnl, filled_usd, and optionally sport.
    Returns the copies payload."""
    rows = [r for r in rows if (r.get("whale") or "") in COPY_WHALES]
    total = {"settled": 0, "wins": 0, "losses": 0,
             "pnl": 0.0, "staked": 0.0}
    by_whale: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    by_ws: dict[tuple, dict] = {}
    by_dw: dict[tuple, dict] = {}
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        stake = float(r.get("filled_usd") or 0)
        disp = DISPLAY.get(r["whale"], r["whale"])
        day = r.get("day") or "undated"
        sport = r.get("sport") or "unknown"
        for b in (total,
                  by_whale.setdefault(r["whale"], {
                      "whale": disp,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0}),
                  by_day.setdefault(day, {
                      "day": day, "settled": 0, "wins": 0, "losses": 0,
                      # per-day deployed (owner asked MERIDIAN "how much
                      # did we deploy yesterday in copies" 2026-08-22
                      # and it had no clean answer — now it does).
                      "pnl": 0.0, "staked": 0.0}),
                  by_ws.setdefault((disp, sport), {
                      "whale": disp, "sport": sport,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0}),
                  by_dw.setdefault((day, disp), {
                      "day": day, "whale": disp,
                      "settled": 0, "wins": 0, "losses": 0,
                      "pnl": 0.0, "staked": 0.0})):
            b["settled"] += 1
            b["wins"] += 1 if pnl > 0 else 0
            b["losses"] += 1 if pnl < 0 else 0
            b["pnl"] = round(b["pnl"] + pnl, 2)
            if "staked" in b:
                b["staked"] = round(b["staked"] + stake, 2)
    total["roi"] = (round(total["pnl"] / total["staked"], 4)
                    if total["staked"] else None)
    total["win_rate"] = (round(total["wins"] / total["settled"], 4)
                         if total["settled"] else None)
    for w in by_whale.values():
        w["roi"] = (round(w["pnl"] / w["staked"], 4)
                    if w["staked"] else None)
    for w in by_ws.values():
        w["roi"] = (round(w["pnl"] / w["staked"], 4)
                    if w["staked"] else None)
    return {
        "cohort": "copies",
        "uncapped": True,
        "total": total,
        "by_whale": sorted(by_whale.values(), key=lambda w: -w["pnl"]),
        "by_whale_sport": sorted(by_ws.values(),
                                 key=lambda w: (w["whale"], -w["pnl"])),
        # Full since-window (owner order 2026-08-22): the 31-day
        # truncation silently cut the record's own calendar once the
        # window outgrew a month.
        "daily": sorted((d for d in by_day.values()
                         if d["day"] != "undated"),
                        key=lambda d: d["day"], reverse=True),
        "daily_by_whale": sorted((d for d in by_dw.values()
                                  if d["day"] != "undated"),
                                 key=lambda d: (d["day"], d["whale"]),
                                 reverse=True)[:186],
    }


def trades_list(rows: list[dict], limit: int = 400) -> list[dict]:
    """The public copy ledger (owner order 2026-08-22): the newest
    settled/cashed-out copy rows, one line each, display-named. Pure —
    rows must arrive newest-first (build() orders by settled_at)."""
    out = []
    for r in rows:
        if (r.get("whale") or "") not in COPY_WHALES:
            continue
        out.append({"day": r.get("day"),
                    "whale": DISPLAY.get(r["whale"], r["whale"]),
                    "slug": r.get("slug") or r.get("us_market_slug"),
                    "stake": round(float(r.get("filled_usd") or 0), 2),
                    "pnl": round(float(r.get("pnl") or 0), 2),
                    "status": r.get("status") or "settled"})
        if len(out) >= limit:
            break
    return out


def today_stats(rows: list[dict], today: str) -> dict:
    """Today's copy scoreline (ET), uncapped. Pure; tested."""
    t = {"pnl": 0.0, "settled": 0, "wins": 0, "losses": 0}
    for r in rows:
        if (r.get("whale") or "") not in COPY_WHALES \
                or r.get("day") != today:
            continue
        pnl = float(r.get("pnl") or 0)
        t["pnl"] = round(t["pnl"] + pnl, 2)
        t["settled"] += 1
        t["wins"] += 1 if pnl > 0 else 0
        t["losses"] += 1 if pnl < 0 else 0
    return t


async def build(since_day: str) -> dict:
    """Endpoint assembly: settled copy orders from the audit table."""
    from ..db import get_pool

    from ..copy_sports import sport_of

    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT lower(COALESCE(whale_username, '')) AS whale,
               to_char(settled_at AT TIME ZONE 'America/New_York',
                       'YYYY-MM-DD') AS day,
               pnl, filled_usd, us_market_slug, status
        FROM live_orders
        WHERE status IN ('settled', 'cashed_out')
          AND settled_at IS NOT NULL
        ORDER BY settled_at DESC
        """)
    windowed = []
    for r in rows:
        d = dict(r)
        if (d.get("day") or "") < since_day:
            continue
        d["sport"] = sport_of(d.get("us_market_slug") or "")
        windowed.append(d)
    out = scorecard(windowed)
    # The complement cohort rides along so the partner report's
    # "software cost, day by day" is served uncapped from the same
    # audit table as the copies record.
    out["software"] = software_scorecard(windowed)
    # Live edge of the record (owner order 2026-08-22): what the copy
    # sleeves have ON the table right now, today's scoreline, and the
    # row-level ledger behind the aggregates.
    open_row = await pool.fetchrow(
        """
        SELECT count(*)::int AS count,
               COALESCE(sum(COALESCE(NULLIF(filled_usd, 0),
                                     requested_usd)), 0)::float8 AS stake
        FROM live_orders
        WHERE status IN ('submitting', 'filled')
          AND lower(COALESCE(whale_username, '')) = ANY($1::text[])
        """, list(COPY_WHALES))
    out["open"] = {"count": open_row["count"],
                   "stake": round(open_row["stake"], 2)}
    out["trades"] = trades_list(windowed)
    out["today"] = today_stats(
        windowed, datetime.now(RECORD_TZ).strftime("%Y-%m-%d"))
    # KALSHI COPY SLEEVE MERGE (owner order 2026-08-22: "include volume
    # and pnl from Kalshi — we copied closer to double the volume listed
    # and lost a little on Kalshi"). The platform ledger only sees the
    # Polymarket executor; the Kalshi copy legs live in the ENGINE's
    # ledger and arrive via its heartbeat export. Merge is additive and
    # fail-open: no export -> Polymarket-only record, flagged.
    pm_total = dict(out["total"])
    kexp = await _kalshi_copies_export(pool)
    out["venues"] = {"polymarket": pm_total,
                     "kalshi": (kexp or {}).get("total")}
    out["kalshi_included"] = bool(kexp)
    if kexp:
        out["total"] = merge_totals(out["total"], kexp.get("total") or {})
        out["daily"] = merge_daily(out["daily"], kexp.get("daily") or [])
        out["by_whale"] = merge_by_whale(out["by_whale"],
                                         kexp.get("by_whale") or {})
        ktoday = next((d for d in (kexp.get("daily") or [])
                       if d.get("day") == datetime.now(RECORD_TZ)
                       .strftime("%Y-%m-%d")), None)
        if ktoday:
            out["today"] = merge_totals(out["today"], ktoday,
                                        keys=("pnl", "settled", "wins",
                                              "losses"))
    out["since"] = since_day
    out["generated_at"] = datetime.now(RECORD_TZ).isoformat()
    return out


def merge_totals(a: dict, b: dict,
                 keys: tuple = ("settled", "wins", "losses",
                                "pnl", "staked")) -> dict:
    """Additive venue merge; ROI/win-rate recomputed. Pure; tested."""
    m = dict(a)
    for k in keys:
        m[k] = round(float(a.get(k) or 0) + float(b.get(k) or 0), 2)
        if k in ("settled", "wins", "losses"):
            m[k] = int(m[k])
    if "staked" in m:
        m["roi"] = (round(m["pnl"] / m["staked"], 4)
                    if m.get("staked") else None)
    if m.get("settled"):
        m["win_rate"] = round(m.get("wins", 0) / m["settled"], 4)
    return m


def merge_daily(pm_daily: list[dict], k_daily: list[dict]) -> list[dict]:
    """Merge the two venues' ET-day series (newest first). Pure."""
    by_day = {d["day"]: dict(d) for d in pm_daily}
    for kd in k_daily:
        day = kd.get("day")
        if not day:
            continue
        if day in by_day:
            by_day[day] = merge_totals(by_day[day], kd)
            by_day[day]["day"] = day
        else:
            by_day[day] = {"day": day,
                           **{k: kd.get(k, 0) for k in
                              ("settled", "wins", "losses",
                               "pnl", "staked")}}
    return sorted(by_day.values(), key=lambda d: d["day"], reverse=True)


def merge_by_whale(pm_bw: list[dict], k_bw: dict) -> list[dict]:
    """Fold the engine's per-whale Kalshi record into the display list.
    Engine keys are lowercase usernames; only COPY_WHALES merge (the
    crypto sleeve is not copies and its whales never appear here)."""
    by_name = {w["whale"]: dict(w) for w in pm_bw}
    for uname, kb in k_bw.items():
        if uname not in COPY_WHALES:
            continue
        disp = DISPLAY.get(uname, uname)
        krow = {"settled": kb.get("settled", 0),
                "wins": kb.get("wins", 0),
                "losses": kb.get("losses", 0),
                "pnl": kb.get("pnl", kb.get("realized", 0)) or 0,
                "staked": kb.get("staked", 0) or 0}
        if disp in by_name:
            merged = merge_totals(by_name[disp], krow)
            merged["whale"] = disp
            by_name[disp] = merged
        else:
            by_name[disp] = {"whale": disp, **krow,
                             "roi": (round(krow["pnl"] / krow["staked"], 4)
                                     if krow["staked"] else None)}
    return sorted(by_name.values(), key=lambda w: -float(w["pnl"] or 0))


async def _kalshi_copies_export(pool) -> dict | None:
    """The engine heartbeat's kalshi_copies_record block, or None."""
    try:
        raw = await pool.fetchval(
            "SELECT detail FROM service_heartbeats "
            "WHERE service = 'edge_engine'")
        if not raw:
            return None
        import json as _json
        detail = raw if isinstance(raw, dict) else _json.loads(raw)
        exp = detail.get("kalshi_copies_record")
        if not isinstance(exp, dict) or "total" not in exp:
            return None
        return exp
    except Exception:  # noqa: BLE001 — record serves PM-only, flagged
        return None
