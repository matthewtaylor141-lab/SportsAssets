"""Downloadable per-whale performance reports (PDF).

Weekly / monthly trader reports in the house style: headline stats, per-sport
breakdown, daily P&L ledger, and largest trades for the period. Rendered with
reportlab — no external services.
"""

from __future__ import annotations

import asyncio
import io
import os
import time as _clock
from datetime import date, datetime, time, timedelta, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import json

from ..analytics import perf
from ..analytics.positions import EPS, Fill, Position
from ..betting import american_odds, bet_label, bet_type, result_word
from ..db import get_pool
from . import queries
from .track_record import RECORD_TZ

INK = colors.HexColor("#141414")
MUTED = colors.HexColor("#6b6b66")
LINE = colors.HexColor("#dddcd5")
GOOD = colors.HexColor("#0a7a0a")
BAD = colors.HexColor("#c03535")
ACCENT = colors.HexColor("#2a78d6")

H1 = ParagraphStyle(
    "h1", fontName="Helvetica-Bold", fontSize=18, leading=22, textColor=INK, spaceAfter=4
)
SUB = ParagraphStyle(
    "sub", fontName="Helvetica", fontSize=9.5, leading=12, textColor=MUTED, spaceAfter=12
)
H2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK,
    spaceBefore=14, spaceAfter=6,
)
FOOT = ParagraphStyle("foot", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED)


def _esc(text: str) -> str:
    """Escape for reportlab Paragraph markup ('P&L' → 'P&amp;L')."""
    return text.replace("&", "&amp;")


def _usd(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _signed(v: float | None) -> str:
    s = _usd(v)
    return f"+{s}" if v is not None and v > 0 else s


def _pct(v: float | None) -> str:
    return f"{v * 100:.1f}%" if v is not None else "—"


def period_bounds(period: str, end: date | None = None) -> tuple[datetime, datetime, str]:
    """Weekly = trailing 7 days; monthly = the end date's calendar month.

    Bounds are EASTERN midnights (track_record.RECORD_TZ): the platform's
    reporting day is an ET calendar day. UTC-midnight bounds shifted every
    boundary 4-5 hours early, so a settlement after 8pm ET fell into the
    neighboring period (same defect as the track record's calendar,
    owner report 2026-08-05). Returned datetimes are timezone-aware and
    compare correctly against the aware settled_at timestamps downstream.
    """
    end = end or datetime.now(tz=RECORD_TZ).date()
    if period == "weekly":
        start = end - timedelta(days=6)
        label = f"Weekly report — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}"
    else:
        start = end.replace(day=1)
        label = f"Monthly report — {end.strftime('%B %Y')}"
    return (
        datetime.combine(start, time.min, tzinfo=RECORD_TZ),
        datetime.combine(end, time.max, tzinfo=RECORD_TZ),
        label,
    )


def _record_day(ts: datetime) -> str:
    """Timestamp -> the platform's reporting day (ET calendar day)."""
    return ts.astimezone(RECORD_TZ).strftime("%Y-%m-%d")


def group_daily_et(
    realizations: list[tuple[datetime, float]],
    trades: list[tuple[datetime, float]],
) -> list[dict]:
    """perf.group_daily with every event first moved into RECORD_TZ, so
    per-day rows bucket by the platform's ET reporting day rather than UTC
    (a 9:30pm ET settlement was landing on the next day's calendar box)."""
    return perf.group_daily(
        [(ts.astimezone(RECORD_TZ), v) for ts, v in realizations],
        [(ts.astimezone(RECORD_TZ), v) for ts, v in trades],
    )


def _stat_grid(stats: list[tuple[str, str, colors.Color | None]]) -> Table:
    label_style = ParagraphStyle(
        "l", fontName="Helvetica", fontSize=7, leading=9, textColor=MUTED
    )
    tiles = [
        Table(
            [[Paragraph(_esc(label.upper()), label_style)],
             [Paragraph(_esc(value), ParagraphStyle(
                 "v", fontName="Helvetica-Bold", fontSize=13, leading=16,
                 textColor=color or INK))]],
            colWidths=[1.62 * inch],
        )
        for label, value, color in stats
    ]
    per_row = 4
    rows = [tiles[i : i + per_row] for i in range(0, len(tiles), per_row)]
    if rows and len(rows[-1]) < per_row:
        rows[-1] += [""] * (per_row - len(rows[-1]))
    t = Table(rows, colWidths=[1.75 * inch] * per_row)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _table(headers: list[str], rows: list[list[str]], widths: list[float],
           color_col: int | None = None, raw_values: list[float] | None = None) -> Table:
    data = [headers] + rows
    t = Table(data, colWidths=[w * inch for w in widths], repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if color_col is not None and raw_values is not None:
        for i, v in enumerate(raw_values, start=1):
            style.append(("TEXTCOLOR", (color_col, i), (color_col, i), GOOD if v >= 0 else BAD))
    t.setStyle(TableStyle(style))
    return t


# The whale-day drill-down was the API's proven killer (2026-09-03 API
# restarts behind the 502s). settled_bets fetched a whale's ENTIRE
# trades-join-markets ledger per call and api_whale_day filtered by day
# afterwards: swisstony is 889,154 fills, +0.7 to +1.1 GB per call held as
# asyncpg Records, and the diagnostic probe fires /api/whales/{id}/day/{d}
# 56 times at --max-time 20 plus a retry lane. curl abandons at 20 s, the
# handler does not, so whole-ledger fetches stacked on a 1.2-1.7 GB
# resident floor in a 2 GB container and Render replaced the process --
# 5 of 5 probes today died inside that burst, every route including
# /healthz answering Render's page for 10-55 s. Two bounds fix it:
#
#   * the ledger streams through a server-side cursor in _BETS_CHUNK-row
#     chunks (the shape track_record._hydrate_all already uses) and is
#     folded per asset as it arrives, keeping ten scalar fields per asset
#     instead of the Record: on a synthetic 889,154-row ledger the peak is
#     +152 MB against +968 MB for the list, and the loop yields every
#     chunk (longest stall 0.2 s, a cyclic-GC pass) instead of one
#     multi-second synchronous replay;
#   * the finished bet list is memoized per whale, so seven day calls (and
#     the PDF builders) read the ledger once: TTL 600 s, single-flight per
#     whale so a burst of abandoned calls cannot stack readers, at most
#     _BETS_MAX_WHALES lists resident (the oldest is evicted).
#
# WHALE_DAY_STREAM=0 keeps the batch fetch for one deploy if the cursor
# misbehaves on production Postgres -- a rollback that is a variable, not
# a deploy. The cache stays either way.
_LEDGER_SQL = """
        SELECT t.asset, t.condition_id, t.outcome, t.outcome_index, t.side,
               t.size::float8 AS size, t.price::float8 AS price,
               t.notional::float8 AS notional, t.sport AS t_sport, t.ts,
               t.market_title AS t_title,
               m.title AS m_title, m.event_title, m.sport AS m_sport,
               m.resolved, m.resolved_prices, m.resolved_at
        FROM trades t LEFT JOIN markets m USING (condition_id)
        WHERE t.whale_id = $1
        ORDER BY t.ts, t.id
        """
_BETS_CHUNK = 5000
_BETS_CHUNK_TIMEOUT_S = 60.0
_SETTLE_YIELD_EVERY = 1000
_BETS_TTL = 600.0
_BETS_MAX_WHALES = 3
_bets_cache: dict[int, tuple[float, list[dict]]] = {}
_bets_locks: dict[int, asyncio.Lock] = {}


def _stream_enabled() -> bool:
    return os.environ.get("WHALE_DAY_STREAM", "1").strip() != "0"


class _Agg:
    """Per-asset fold state. `meta` is the ten fields the bet needs from
    the last row carrying a market title (else the first row), as a
    tuple: (outcome, outcome_index, t_sport, t_title, m_title,
    event_title, m_sport, resolved, resolved_prices, resolved_at)."""

    __slots__ = ("pos", "bought_shares", "first", "last", "meta")

    def __init__(self) -> None:
        self.pos = Position()
        self.bought_shares = 0.0
        self.first = None
        self.last = None
        self.meta = None


def _fold(by_asset: dict[str, _Agg], rows) -> None:
    for t in rows:
        a = by_asset.get(t["asset"])
        if a is None:
            a = by_asset[t["asset"]] = _Agg()
        a.pos.apply(Fill(side=t["side"], size=t["size"], price=t["price"]))
        if t["side"] == "BUY":
            a.bought_shares += t["size"]
        a.first = a.first or t["ts"]
        a.last = t["ts"]
        if a.meta is None or t["m_title"]:
            a.meta = (t["outcome"], t["outcome_index"], t["t_sport"], t["t_title"],
                      t["m_title"], t["event_title"], t["m_sport"], t["resolved"],
                      t["resolved_prices"], t["resolved_at"])


async def _replay_ledger(pool, whale_id: int) -> dict[str, _Agg]:
    by_asset: dict[str, _Agg] = {}
    if not _stream_enabled():
        _fold(by_asset, await pool.fetch(_LEDGER_SQL, whale_id))
        return by_asset
    async with pool.acquire() as con:
        async with con.transaction():
            cur = await con.cursor(_LEDGER_SQL, whale_id)
            while True:
                rows = await asyncio.wait_for(cur.fetch(_BETS_CHUNK),
                                              timeout=_BETS_CHUNK_TIMEOUT_S)
                if not rows:
                    break
                _fold(by_asset, rows)
                del rows
    return by_asset


async def _settle(by_asset: dict[str, _Agg]) -> list[dict]:
    bets: list[dict] = []
    n = 0
    for a in by_asset.values():
        n += 1
        if n % _SETTLE_YIELD_EVERY == 0:
            # The label regexes over 100k+ assets are the other synchronous
            # stretch on the loop (about 30 us an asset): yield every
            # thousand so no stretch outlasts a health check's patience.
            await asyncio.sleep(0)
        (outcome, outcome_index, t_sport, t_title, m_title, event_title,
         m_sport, m_resolved, resolved_prices, resolved_at) = a.meta
        resolved = bool(m_resolved)
        if resolved and not a.pos.resolved:
            prices = resolved_prices
            if isinstance(prices, str):
                prices = json.loads(prices)
            idx = outcome_index
            if prices and idx is not None and 0 <= idx < len(prices):
                a.pos.resolve(float(prices[idx]))
        fully_cashed = a.pos.shares <= EPS and a.pos.fills > 0
        if not (a.pos.resolved or (fully_cashed and not resolved)):
            continue  # still open — not a settled bet
        stake = a.pos.notional_in
        avg_price = stake / a.bought_shares if a.bought_shares > EPS else None
        settled_at = (resolved_at if a.pos.resolved and resolved_at else a.last)
        bets.append({
            "settled_at": settled_at,
            "sport": (m_sport if m_sport and m_sport != "unclassified"
                      else t_sport),
            "label": bet_label(outcome, m_title or t_title, event_title),
            "bet_type": bet_type(outcome, m_title or t_title, event_title),
            "odds": american_odds(avg_price),
            "stake": round(stake, 2),
            "result": result_word(a.pos.realized_pnl, a.pos.resolved),
            "pnl": round(a.pos.realized_pnl, 2),
        })
    bets.sort(key=lambda b: b["settled_at"])
    return bets


def _remember(whale_id: int, bets: list[dict]) -> None:
    _bets_cache[whale_id] = (_clock.time(), bets)
    while len(_bets_cache) > _BETS_MAX_WHALES:
        _bets_cache.pop(min(_bets_cache, key=lambda k: _bets_cache[k][0]))
    # A lock outlives its whale only while a flight holds it: the ids come
    # off the URL, so an unbounded lock table would be one more thing a
    # scan could grow.
    for k in [k for k, lk in _bets_locks.items()
              if k not in _bets_cache and not lk.locked()]:
        _bets_locks.pop(k, None)


async def settled_bets(whale_id: int) -> list[dict]:
    """Every settled bet in the whale's imported history, sportsbook-labeled.

    One row per (market, outcome token) position that has fully settled —
    resolved by the market, or completely cashed out before resolution.
    Memoized per whale (see above); callers get their own list.
    """
    hit = _bets_cache.get(whale_id)
    if hit is not None and _clock.time() - hit[0] < _BETS_TTL:
        return list(hit[1])
    lock = _bets_locks.get(whale_id)
    if lock is None:
        lock = _bets_locks[whale_id] = asyncio.Lock()
    try:
        async with lock:
            # Single-flight: whoever waited here reads what the leader built.
            hit = _bets_cache.get(whale_id)
            if hit is not None and _clock.time() - hit[0] < _BETS_TTL:
                return list(hit[1])
            pool = await get_pool()
            by_asset = await _replay_ledger(pool, whale_id)
            bets = await _settle(by_asset)
            del by_asset
            _remember(whale_id, bets)
            return list(bets)
    finally:
        # _remember prunes locks only after a build lands, so a failed
        # read (hotfix review, 2026-09-03 API restarts: a DB outage under
        # the probe's whale-id scan) left one Lock per distinct id behind
        # -- the same unbounded table the prune exists to prevent. Drop
        # this whale's lock on the way out whenever nothing holds it and
        # no entry vouches for it; a waiter that still references it
        # simply finishes its own flight on the object it has.
        if (not lock.locked() and whale_id not in _bets_cache
                and _bets_locks.get(whale_id) is lock):
            _bets_locks.pop(whale_id, None)


def _tint(pnl: float, max_abs: float) -> colors.Color:
    """White→blue (profit) / white→red (loss) tint scaled by magnitude."""
    if abs(pnl) < 0.01 or max_abs <= 0:
        return colors.HexColor("#f4f4f1")
    k = min(1.0, abs(pnl) / max_abs) * 0.55 + 0.12
    r, g, b = (0.22, 0.53, 0.90) if pnl > 0 else (0.90, 0.40, 0.40)
    return colors.Color(1 - k * (1 - r), 1 - k * (1 - g), 1 - k * (1 - b))


def _calendar_flowables(daily: list[dict]) -> list:
    """Monthly calendar grids of daily realized P&L."""
    tiny = ParagraphStyle("cal", fontName="Helvetica", fontSize=6.5, textColor=INK, leading=8)
    tiny_b = ParagraphStyle("calb", fontName="Helvetica-Bold", fontSize=6.5, textColor=INK, leading=8)
    by_month: dict[str, dict[int, dict]] = {}
    for d in daily:
        by_month.setdefault(d["date"][:7], {})[int(d["date"][8:10])] = d
    out: list = []
    for month_key in sorted(by_month):
        year, month = int(month_key[:4]), int(month_key[5:7])
        days = by_month[month_key]
        max_abs = max((abs(v["pnl"]) for v in days.values()), default=0)
        month_total = sum(v["pnl"] for v in days.values())
        first_wd = date(year, month, 1).weekday()  # Mon=0
        lead = (first_wd + 1) % 7  # Sun-first grid
        n_days = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day

        cells: list = [""] * lead + list(range(1, n_days + 1))
        cells += [""] * ((7 - len(cells) % 7) % 7)
        grid, styles = [], []
        header = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
        for row_i in range(len(cells) // 7):
            row = []
            for col_i in range(7):
                c = cells[row_i * 7 + col_i]
                if c == "":
                    row.append("")
                    continue
                d = days.get(c)
                if d:
                    row.append([Paragraph(str(c), tiny),
                                Paragraph(_signed(d["pnl"]), tiny_b)])
                    styles.append(("BACKGROUND", (col_i, row_i + 1), (col_i, row_i + 1),
                                   _tint(d["pnl"], max_abs)))
                else:
                    row.append(Paragraph(str(c), ParagraphStyle(
                        "q", fontName="Helvetica", fontSize=6.5, textColor=MUTED)))
            grid.append(row)

        title = date(year, month, 1).strftime("%B %Y")
        out.append(Paragraph(
            f"{title} &nbsp;&nbsp;<font color='{'#0a7a0a' if month_total >= 0 else '#c03535'}'>"
            f"{_signed(month_total)}</font>", H2))
        t = Table([header] + grid, colWidths=[1.0 * inch] * 7, rowHeights=[14] + [26] * len(grid))
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 6.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("GRID", (0, 1), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 1), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            *styles,
        ]))
        out.append(t)
    return out


def _sport_ranking(bets: list[dict], key: str = "sport") -> list[dict]:
    by_sport: dict[str, list[dict]] = {}
    for b in bets:
        by_sport.setdefault(b.get(key) or "unclassified", []).append(b)
    rows = []
    for sport, items in by_sport.items():
        pnl = sum(b["pnl"] for b in items)
        stake = sum(b["stake"] for b in items)
        rows.append({
            "sport": sport, "bets": len(items),
            "wins": sum(1 for b in items if b["pnl"] > 0.01),
            "losses": sum(1 for b in items if b["pnl"] < -0.01),
            "stake": stake, "pnl": pnl, "roi": pnl / stake if stake > 0 else None,
            "items": items,
        })
    rows.sort(key=lambda r: r["pnl"], reverse=True)
    return rows


def _settled_sections(bets: list[dict], daily: list[dict]) -> list:
    """Shared report body: sport ranking → P&L calendars → every bet by sport."""
    if not bets:
        return [Paragraph(
            "No settled bets in this period yet. If history was recently imported, "
            "market resolutions may still be syncing — check Admin → Ingestion health "
            "(backfill / metadata / analytics rows).", SUB)]
    sport_rows = _sport_ranking(bets)
    story: list = [
        Paragraph("P&amp;L by sport — most to least profitable", H2),
        _table(
            ["Sport", "Bets", "Record", "Staked", "ROI", "P&L"],
            [[r["sport"], f"{r['bets']:,}", f"{r['wins']}-{r['losses']}", _usd(r["stake"]),
              _pct(r["roi"]), _signed(r["pnl"])] for r in sport_rows],
            [1.6, 0.9, 1.0, 1.3, 0.9, 1.4],
            color_col=5, raw_values=[r["pnl"] for r in sport_rows],
        ),
    ]
    type_rows = _sport_ranking(bets, key="bet_type")
    story.append(Paragraph("P&amp;L by bet type", H2))
    story.append(_table(
        ["Bet type", "Bets", "Record", "Staked", "ROI", "P&L"],
        [[r["sport"], f"{r['bets']:,}", f"{r['wins']}-{r['losses']}", _usd(r["stake"]),
          _pct(r["roi"]), _signed(r["pnl"])] for r in type_rows],
        [1.6, 0.9, 1.0, 1.3, 0.9, 1.4],
        color_col=5, raw_values=[r["pnl"] for r in type_rows],
    ))
    story.append(Paragraph("Daily P&amp;L calendar", H2))
    story.extend(_calendar_flowables(daily))
    for r in sport_rows:
        rows = sorted(r["items"], key=lambda b: b["settled_at"])
        story.append(Paragraph(
            _esc(f"{r['sport']} — {_signed(r['pnl'])} ({r['wins']}-{r['losses']}, "
                 f"{len(rows):,} bets)"), H2))
        for i in range(0, len(rows), 400):
            chunk = rows[i : i + 400]
            story.append(_table(
                ["Settled", "Bet", "Odds", "Stake", "Result", "P&L"],
                [[_record_day(b["settled_at"]), b["label"][:52], b["odds"],
                  _usd(b["stake"]), b["result"], _signed(b["pnl"])] for b in chunk],
                [0.85, 2.9, 0.6, 0.85, 1.0, 0.95],
                color_col=5, raw_values=[b["pnl"] for b in chunk],
            ))
    return story


_WHALE_0X2C33 = "0x2c335066fe58fe9237c3d3dc7b275c2a034a0563-1759935795465"
# Must mirror app.py's _CAT_ORDER: categories absent here are silently
# dropped from the PDF's day table even though they carry P&L (same bug
# class as the 2026-08-22 report.csv fix — ferrari/0x076daa87/underdog/
# external were missing from this copy of the list).
_CAT_ORDER = ["rn1", "swisstony", "kch123", "homerunhazard",
              _WHALE_0X2C33,
              "ferrarichampions2026", "0x076daa87",
              "manual", "underdog", "arb", "external", "software"]
_CAT_LABEL = {"rn1": "RN1 copies", "swisstony": "SwissTony copies",
              "kch123": "kch123 copies",
              "homerunhazard": "HomeRunHazard copies",
              _WHALE_0X2C33: "0x2c33…0563 copies",
              "ferrarichampions2026": "ferrariChampions2026 copies",
              "0x076daa87": "0x076daa87 copies",
              "manual": "Manual desk", "underdog": "Underdog $1 test",
              "arb": "Arbitrage", "external": "External (owner)",
              "software": "Unattributed"}


def build_category_report(data: dict) -> tuple[bytes, str]:
    """Ops report (owner directive 2026-08-07): per-day, per-category
    P&L over an arbitrary range — the shareable house-style document
    behind the site's Download buttons."""
    frm, to = data.get("from", ""), data.get("to", "")
    totals = data.get("totals") or {}
    days = data.get("days") or []
    net = data.get("net_pnl") or 0.0

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER,
                            leftMargin=0.7 * inch, rightMargin=0.7 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch)
    story = [
        Paragraph("Daily P&amp;L by Strategy", H1),
        Paragraph(_esc(f"{frm} through {to} · Eastern trading days · "
                       "single trades beyond ±$100 excluded and disclosed "
                       "separately"), SUB),
    ]
    settled_n = sum(t.get("settled", 0) for t in totals.values())
    wins_n = sum(t.get("wins", 0) for t in totals.values())
    best_cat = max(totals.items(), key=lambda kv: kv[1]["pnl"])[0] \
        if totals else None
    story.append(_stat_grid([
        ("Net P&L", _signed(net), GOOD if net >= 0 else BAD),
        ("Settled trades", f"{settled_n:,}", None),
        ("Win rate", _pct(wins_n / settled_n) if settled_n else "—", None),
        ("Best category", _CAT_LABEL.get(best_cat, "—")
         if best_cat else "—", None),
    ]))

    story.append(Paragraph("Totals by category", H2))
    rows, raws = [], []
    for cat in _CAT_ORDER:
        t = totals.get(cat)
        if not t:
            continue
        rows.append([_CAT_LABEL[cat], _signed(t["pnl"]),
                     str(t["settled"]), f"{t['wins']}-{t['losses']}"])
        raws.append(t["pnl"])
    rows.append(["ALL STRATEGIES", _signed(net),
                 str(settled_n), f"{wins_n}-{sum(t.get('losses', 0) for t in totals.values())}"])
    raws.append(net)
    story.append(_table(["Category", "P&L", "Settled", "W-L"],
                        rows, [2.4, 1.3, 1.1, 1.2],
                        color_col=1, raw_values=raws))

    story.append(Paragraph("Daily ledger", H2))
    day_cats = [c for c in _CAT_ORDER if totals.get(c)]
    drows, draws = [], []
    for d in days:
        day_net = round(sum(c["pnl"] for k, c in d.items()
                            if isinstance(c, dict)), 2)
        cells = [d["date"], _signed(day_net)]
        for cat in day_cats:
            c = d.get(cat)
            cells.append(_signed(c["pnl"]) if c else "—")
        drows.append(cells)
        draws.append(day_net)
    story.append(_table(
        ["Date", "Net"] + [_CAT_LABEL[c].replace(" copies", "")
                           for c in day_cats],
        drows,
        # Only categories with activity in range get a column, and the
        # columns share the page's usable width — a fixed 0.72"/column
        # overflowed LETTER once the sleeve roster grew past eight.
        [0.85, 0.75] + [min(0.72, 5.5 / max(1, len(day_cats)))]
        * len(day_cats),
        color_col=1, raw_values=draws))

    story.append(Spacer(1, 14))
    story.append(Paragraph(_esc(
        "Copies are order-level results from the platform's own audit "
        "table; arbitrage is split by the engine mirror's band tag; "
        "External (owner) is venue settlements no platform ledger "
        "touched. Unattributed is the derived remainder against the "
        "account calendar (owner rule 2026-08-06) — with the software "
        "wind-down complete it holds fees, open-stake mark moves, and "
        "trades past the ±$100 display cap, not software trading. "
        "Generated directly from the trading ledger — nothing entered "
        "by hand."),
        FOOT))
    doc.build(story)
    return buf.getvalue(), f"bettoredge_pnl_{frm}_{to}.pdf"


async def build_settled_report(whale_id: int) -> tuple[bytes, str]:
    """Complete settled-history report: sport ranking, P&L calendars, and
    EVERY settled bet grouped by sport — for management analysis."""
    pool = await get_pool()
    whale = await pool.fetchrow("SELECT * FROM whales WHERE id=$1", whale_id)
    if whale is None:
        raise LookupError("unknown whale")

    bets = await settled_bets(whale_id)
    replay = await queries.whale_replay(whale_id)
    daily = group_daily_et(replay["realizations"], [])
    dd = perf.max_drawdown(replay["realizations"])

    total_pnl = sum(b["pnl"] for b in bets)
    total_stake = sum(b["stake"] for b in bets)
    wins = sum(1 for b in bets if b["pnl"] > 0.01)
    losses = sum(1 for b in bets if b["pnl"] < -0.01)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"Settled history — {whale['username'] or whale['address']}",
    )
    name = whale["username"] or f"{whale['address'][:10]}…"
    story: list = [
        Paragraph(_esc(f"{name} — Complete Settled History"), H1),
        Paragraph(
            f"All imported settled bets &nbsp;·&nbsp; wallet {whale['address']} &nbsp;·&nbsp; "
            f"generated {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — "
            f"SportsAssets Hub", SUB),
        _stat_grid([
            ("Settled P&L", _signed(total_pnl), GOOD if total_pnl >= 0 else BAD),
            ("Total staked", _usd(total_stake), None),
            ("Record", f"{wins}-{losses}", None),
            ("ROI", _pct(total_pnl / total_stake if total_stake else None),
             GOOD if total_pnl >= 0 else BAD),
            ("Settled bets", f"{len(bets):,}", None),
            ("Max drawdown", _usd(dd["max_drawdown"]), BAD if dd["max_drawdown"] else None),
            ("Green days", f"{sum(1 for d in daily if d['pnl'] > 0):,}", GOOD),
            ("Red days", f"{sum(1 for d in daily if d['pnl'] < 0):,}", BAD),
        ]),
    ]
    story.extend(_settled_sections(bets, daily))

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Public on-chain / public-API data, displayed with attribution. Informational only — "
        "not betting or investment advice.", FOOT))
    doc.build(story)
    filename = f"{name}-settled-history.pdf".replace(" ", "_")
    return buf.getvalue(), filename


async def build_report(whale_id: int, period: str, end: date | None = None) -> tuple[bytes, str]:
    """Weekly/monthly report — same settled-bet organization as the full
    history report, scoped to the period, plus the period's largest trades."""
    pool = await get_pool()
    whale = await pool.fetchrow("SELECT * FROM whales WHERE id=$1", whale_id)
    if whale is None:
        raise LookupError("unknown whale")
    start_dt, end_dt, label = period_bounds(period, end)

    bets = [b for b in await settled_bets(whale_id) if start_dt <= b["settled_at"] <= end_dt]
    replay = await queries.whale_replay(whale_id)
    p_real = [(ts, a) for ts, a in replay["realizations"] if start_dt <= ts <= end_dt]
    daily = group_daily_et(p_real, [])
    dd = perf.max_drawdown(p_real)

    activity = await pool.fetchrow(
        "SELECT count(*)::int AS trades, COALESCE(sum(notional), 0)::float8 AS volume "
        "FROM trades WHERE whale_id=$1 AND ts BETWEEN $2 AND $3",
        whale_id, start_dt, end_dt,
    )
    largest = await pool.fetch(
        """
        SELECT t.ts, t.side, t.outcome, t.price::float8 AS price,
               t.notional::float8 AS notional,
               COALESCE(m.title, t.market_title) AS title, m.event_title
        FROM trades t LEFT JOIN markets m USING (condition_id)
        WHERE t.whale_id=$1 AND t.ts BETWEEN $2 AND $3
        ORDER BY t.notional DESC LIMIT 15
        """,
        whale_id, start_dt, end_dt,
    )

    total_pnl = sum(b["pnl"] for b in bets)
    total_stake = sum(b["stake"] for b in bets)
    wins = sum(1 for b in bets if b["pnl"] > 0.01)
    losses = sum(1 for b in bets if b["pnl"] < -0.01)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.65 * inch, rightMargin=0.65 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"Trader report — {whale['username'] or whale['address']}",
    )
    name = whale["username"] or f"{whale['address'][:10]}…"
    story: list = [
        Paragraph(_esc(f"{name} — Trader Performance"), H1),
        Paragraph(
            f"{_esc(label)} &nbsp;·&nbsp; wallet {whale['address']} &nbsp;·&nbsp; "
            f"generated {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — "
            f"SportsAssets Hub", SUB),
        _stat_grid([
            ("Settled P&L", _signed(total_pnl), GOOD if total_pnl >= 0 else BAD),
            ("Total staked", _usd(total_stake), None),
            ("Record", f"{wins}-{losses}", None),
            ("ROI", _pct(total_pnl / total_stake if total_stake else None),
             GOOD if total_pnl >= 0 else BAD),
            ("Settled bets", f"{len(bets):,}", None),
            ("Max drawdown", _usd(dd["max_drawdown"]), BAD if dd["max_drawdown"] else None),
            ("Trades placed", f"{activity['trades']:,}", None),
            ("Volume traded", _usd(activity["volume"]), None),
        ]),
    ]
    story.extend(_settled_sections(bets, daily))

    if largest:
        story.append(Paragraph("Largest trades placed in period", H2))
        story.append(_table(
            ["Date", "Side", "Bet", "Price", "Notional"],
            [[r["ts"].astimezone(RECORD_TZ).strftime("%m-%d %H:%M"), r["side"],
              bet_label(r["outcome"], r["title"], r["event_title"])[:46],
              f"{round(r['price'] * 100)}¢", _usd(r["notional"])] for r in largest],
            [1.0, 0.6, 3.3, 0.7, 1.0],
        ))

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Public on-chain / public-API data, displayed with attribution. Informational only — "
        "not betting or investment advice.", FOOT))
    doc.build(story)
    filename = f"{name}-{period}-{end_dt.date().isoformat()}.pdf".replace(" ", "_")
    return buf.getvalue(), filename
