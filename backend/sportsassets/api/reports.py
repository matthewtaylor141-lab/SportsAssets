"""Downloadable per-whale performance reports (PDF).

Weekly / monthly trader reports in the house style: headline stats, per-sport
breakdown, daily P&L ledger, and largest trades for the period. Rendered with
reportlab — no external services.
"""

from __future__ import annotations

import io
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

from ..analytics import perf
from ..db import get_pool
from . import queries

INK = colors.HexColor("#141414")
MUTED = colors.HexColor("#6b6b66")
LINE = colors.HexColor("#dddcd5")
GOOD = colors.HexColor("#0a7a0a")
BAD = colors.HexColor("#c03535")
ACCENT = colors.HexColor("#2a78d6")

H1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=18, textColor=INK, spaceAfter=2)
SUB = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, textColor=MUTED, spaceAfter=10)
H2 = ParagraphStyle(
    "h2", fontName="Helvetica-Bold", fontSize=11, textColor=INK, spaceBefore=14, spaceAfter=6
)
FOOT = ParagraphStyle("foot", fontName="Helvetica", fontSize=7.5, textColor=MUTED)


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
    """Weekly = trailing 7 days; monthly = the end date's calendar month."""
    end = end or datetime.now(tz=timezone.utc).date()
    if period == "weekly":
        start = end - timedelta(days=6)
        label = f"Weekly report — {start.strftime('%b %d')} to {end.strftime('%b %d, %Y')}"
    else:
        start = end.replace(day=1)
        label = f"Monthly report — {end.strftime('%B %Y')}"
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end, time.max, tzinfo=timezone.utc),
        label,
    )


def _stat_grid(stats: list[tuple[str, str, colors.Color | None]]) -> Table:
    cells = [
        [Paragraph(label.upper(), ParagraphStyle("l", fontName="Helvetica", fontSize=7, textColor=MUTED)),
         Paragraph(value, ParagraphStyle("v", fontName="Helvetica-Bold", fontSize=13,
                                         textColor=color or INK))]
        for label, value, color in stats
    ]
    rows = [[Table([c], colWidths=[1.55 * inch]) for c in cells[i : i + 4]] for i in range(0, len(cells), 4)]
    t = Table(rows, colWidths=[1.65 * inch] * min(4, len(cells)))
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
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


async def build_report(whale_id: int, period: str, end: date | None = None) -> tuple[bytes, str]:
    pool = await get_pool()
    whale = await pool.fetchrow("SELECT * FROM whales WHERE id=$1", whale_id)
    if whale is None:
        raise LookupError("unknown whale")
    start_dt, end_dt, label = period_bounds(period, end)

    replay = await queries.whale_replay(whale_id)
    in_period = lambda ts: start_dt <= ts <= end_dt  # noqa: E731
    p_real = [(ts, a) for ts, a in replay["realizations"] if in_period(ts)]
    p_trades = [(ts, n) for ts, n in replay["trade_events"] if in_period(ts)]

    trade_rows = await pool.fetch(
        """
        SELECT ts, side, outcome, market_title, sport, size::float8 AS size,
               price::float8 AS price, notional::float8 AS notional
        FROM trades WHERE whale_id=$1 AND ts BETWEEN $2 AND $3
        ORDER BY notional DESC LIMIT 15
        """,
        whale_id, start_dt, end_dt,
    )
    sport_rows = await pool.fetch(
        """
        SELECT sport, count(*)::int AS trades, sum(notional)::float8 AS volume
        FROM trades WHERE whale_id=$1 AND ts BETWEEN $2 AND $3
        GROUP BY sport ORDER BY volume DESC
        """,
        whale_id, start_dt, end_dt,
    )
    daily = perf.group_daily(p_real, p_trades)
    summary = perf.summarize(p_real, p_trades, sum(n for _, n in p_trades if n) or 0.0)
    dd = perf.max_drawdown(p_real)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER, leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f"Trader report — {whale['username'] or whale['address']}",
    )
    name = whale["username"] or f"{whale['address'][:10]}…"
    story = [
        Paragraph(f"{name} — Trader Performance", H1),
        Paragraph(
            f"{label} &nbsp;·&nbsp; wallet {whale['address']} &nbsp;·&nbsp; "
            f"generated {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} — SportsAssets Hub",
            SUB,
        ),
        _stat_grid([
            ("Realized P&L", _signed(summary["realized_pnl"]),
             GOOD if summary["realized_pnl"] >= 0 else BAD),
            ("Volume traded", _usd(summary["volume_traded"]), None),
            ("% earned", _pct(summary["pct_earned"]),
             GOOD if (summary["pct_earned"] or 0) >= 0 else BAD),
            ("Max drawdown", _usd(dd["max_drawdown"]), BAD if dd["max_drawdown"] else None),
            ("Trades", f"{summary['trade_count']:,}", None),
            ("Active days", f"{len(daily)}", None),
            ("Best day", _signed(max((d['pnl'] for d in daily), default=None)), GOOD),
            ("Worst day", _signed(min((d['pnl'] for d in daily), default=None)), BAD),
        ]),
    ]

    if sport_rows:
        story.append(Paragraph("Activity by sport", H2))
        story.append(_table(
            ["Sport", "Trades", "Volume"],
            [[r["sport"], f"{r['trades']:,}", _usd(r["volume"])] for r in sport_rows],
            [2.4, 1.4, 1.8],
        ))

    if daily:
        story.append(Paragraph("Daily P&L", H2))
        story.append(_table(
            ["Date", "Trades", "Volume", "Realized P&L"],
            [[d["date"], f"{d['trades']:,}", _usd(d["volume"]), _signed(d["pnl"])] for d in daily],
            [1.6, 1.2, 1.6, 1.8],
            color_col=3, raw_values=[d["pnl"] for d in daily],
        ))

    if trade_rows:
        story.append(Paragraph("Largest trades", H2))
        story.append(_table(
            ["Date", "Side", "Outcome", "Market", "Price", "Notional"],
            [[r["ts"].strftime("%m-%d %H:%M"), r["side"], (r["outcome"] or "—")[:18],
              (r["market_title"] or "—")[:38], f"{round(r['price'] * 100)}¢", _usd(r["notional"])]
             for r in trade_rows],
            [1.0, 0.55, 1.3, 2.7, 0.6, 1.0],
        ))

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Public on-chain / public-API data, displayed with attribution. Informational only — "
        "not betting or investment advice.", FOOT))
    doc.build(story)
    filename = f"{name}-{period}-{end_dt.date().isoformat()}.pdf".replace(" ", "_")
    return buf.getvalue(), filename
