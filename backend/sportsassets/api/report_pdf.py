"""Branded PDF reports, generated in-app (owner order 2026-08-28).

Two documents, both downloadable straight from the site with a desk
session:

- the MASTER whale-performance report: every settled copy attributed
  to its source whale — summary band, equity curve, per-whale table,
  and the whale x sport x period pivot, all from the same ledger the
  Reports page serves (nothing is re-derived for print);
- the KALSHI MANUAL report: the desk team's own tickets on Kalshi,
  order by order, with totals.

Pure functions over already-fetched rows -> bytes, so the builders
unit-test without a database. Brand: Bettor Token blue #0066FF, the
hex-b mark on every header, honest footers on every page.
"""

from __future__ import annotations

import io
import pathlib
from datetime import datetime, timezone

BRAND = "#0066FF"
INK = "#0b1526"
MUTED = "#5a6b85"
POS = "#0f9d58"
NEG = "#d93025"
MARK = pathlib.Path(__file__).resolve().parents[1] / "assets" / "bt_mark.png"


def _money(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _styles():
    from reportlab.lib.styles import ParagraphStyle

    return {
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=24,
                             textColor=INK, spaceAfter=4),
        "sub": ParagraphStyle("sub", fontName="Helvetica", fontSize=9, leading=12,
                              textColor=MUTED, spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11,
                             textColor=BRAND, spaceBefore=14, spaceAfter=6),
        "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8,
                               textColor=INK),
        "foot": ParagraphStyle("foot", fontName="Helvetica", fontSize=7,
                               textColor=MUTED),
    }


def _header(canvas, doc, title: str) -> None:
    from reportlab.lib.units import mm

    canvas.saveState()
    w, h = doc.pagesize
    canvas.setFillColor(BRAND)
    canvas.rect(0, h - 16 * mm, w, 16 * mm, stroke=0, fill=1)
    try:
        canvas.drawImage(str(MARK), 10 * mm, h - 13.4 * mm,
                         width=10.8 * mm, height=10.8 * mm,
                         mask="auto")
    except Exception:  # noqa: BLE001 — a missing asset never blocks a report
        pass
    canvas.setFillColorRGB(1, 1, 1)
    canvas.setFont("Helvetica-Bold", 12)
    canvas.drawString(24 * mm, h - 9.2 * mm, "BETTOR TOKEN")
    canvas.setFont("Helvetica", 6.6)
    canvas.drawString(24 * mm, h - 12.6 * mm, "TOKENIZED ASSET MANAGEMENT")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 10 * mm, h - 10.5 * mm, title)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 6.5)
    canvas.drawString(
        10 * mm, 7 * mm,
        "Read live from the platform's order-level ledger — nothing in this "
        "report is entered by hand. Informational only; not betting or "
        "investment advice.")
    canvas.drawRightString(w - 10 * mm, 7 * mm, f"page {doc.page}")
    canvas.restoreState()


def _table(data: list[list], widths: list[float], aligns: str = "") -> object:
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    t = Table(data, colWidths=widths, repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 7.2),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor(MUTED)),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 7.6),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.HexColor(INK)),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor(BRAND)),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#dfe6f2")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]
    for i, a in enumerate(aligns):
        if a == "r":
            style.append(("ALIGN", (i, 0), (i, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _equity_drawing(daily: list[dict], width: float) -> object | None:
    """Cumulative P&L line over the report window, brand-styled."""
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors

    pts = []
    acc = 0.0
    for d in daily:
        acc += float(d.get("pnl") or 0.0)
        pts.append(acc)
    if len(pts) < 2:
        return None
    dr = Drawing(width, 120)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 30, 18, width - 40, 92
    lp.data = [list(enumerate(pts))]
    lp.lines[0].strokeColor = colors.HexColor(BRAND)
    lp.lines[0].strokeWidth = 1.6
    lp.xValueAxis.visibleTicks = 0
    lp.xValueAxis.labels.fontSize = 0.1
    lp.yValueAxis.labels.fontSize = 6
    lp.yValueAxis.labels.fontName = "Helvetica"
    lp.yValueAxis.strokeColor = colors.HexColor("#c9d4e8")
    lp.xValueAxis.strokeColor = colors.HexColor("#c9d4e8")
    dr.add(lp)
    dr.add(String(30, 2, f"{daily[0].get('day', '')}  ->  "
                  f"{daily[-1].get('day', '')}   cumulative realized P&L",
                  fontSize=6, fillColor=colors.HexColor(MUTED)))
    return dr


def master_report_pdf(rep: dict, ledger: list[dict],
                      window_label: str) -> bytes:
    """The master whale-performance document."""
    from functools import partial

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                    Paragraph, Spacer)

    st = _styles()
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=10 * mm, rightMargin=10 * mm,
                          topMargin=22 * mm, bottomMargin=14 * mm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="f")
    doc.addPageTemplates([PageTemplate(
        id="p", frames=[frame],
        onPage=partial(_header, title="MASTER WHALE PERFORMANCE"))])

    story: list = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    story.append(Paragraph("Master Whale Performance Report", st["h1"]))
    story.append(Paragraph(
        f"{window_label} · generated {now} · every settled copy trade, "
        f"uncapped, attributed to its source whale", st["sub"]))

    # ── summary band ──
    lat = rep.get("latency") or {}
    total_pnl = sum(w.get("pnl") or 0 for w in rep.get("by_whale") or [])
    total_staked = sum(w.get("staked") or 0 for w in rep.get("by_whale") or [])
    wins = sum(w.get("wins") or 0 for w in rep.get("by_whale") or [])
    losses = sum(w.get("losses") or 0 for w in rep.get("by_whale") or [])
    n = sum(w.get("n") or 0 for w in rep.get("by_whale") or [])
    roi = (total_pnl / total_staked) if total_staked else None
    story.append(_table(
        [["NET P&L", "STAKED (SETTLED)", "RECORD", "ROI",
          "COPIES", "LATENCY p50"],
         [_money(total_pnl), _money(total_staked), f"{wins}W – {losses}L",
          f"{roi * 100:.2f}%" if roi is not None else "—",
          f"{n:,}",
          f"{lat.get('p50_s')}s" if lat.get("p50_s") is not None else "—"]],
        [90, 100, 80, 60, 60, 70], aligns="rrrrrr"))
    story.append(Spacer(1, 8))

    # ── equity curve from the ledger's daily aggregation ──
    daily: dict[str, float] = {}
    for r in ledger:
        if r.get("status") in ("settled", "cashed_out") and r.get("day"):
            daily[r["day"]] = daily.get(r["day"], 0.0) + float(r.get("pnl") or 0)
    dr = _equity_drawing(
        [{"day": d, "pnl": p} for d, p in sorted(daily.items())],
        doc.width)
    if dr is not None:
        story.append(Paragraph("EQUITY CURVE", st["h2"]))
        story.append(dr)

    # ── by whale ──
    story.append(Paragraph("BY WHALE — WHO EARNS THE CAPITAL", st["h2"]))
    rows = [["WHALE", "COPIES", "RECORD", "STAKED", "P&L", "ROI",
             "LAT p50"]]
    for w in rep.get("by_whale") or []:
        rows.append([
            str(w.get("whale")), f"{w.get('n', 0):,}",
            f"{w.get('wins', 0)}W–{w.get('losses', 0)}L",
            _money(w.get("staked")), _money(w.get("pnl")),
            f"{(w.get('roi') or 0) * 100:.2f}%" if w.get("roi") is not None
            else "—",
            f"{w.get('lat_p50_s')}s" if w.get("lat_p50_s") is not None
            else "—"])
    story.append(_table(rows, [120, 55, 70, 75, 75, 55, 50],
                        aligns=" rrrrrr"))

    # ── the pivot: whale x sport x category x bucket ──
    story.append(Paragraph(
        f"PIVOT — WHALE × SPORT × TYPE × {str(rep.get('period', '')).upper()}",
        st["h2"]))
    prows = [["WHALE", "SPORT", "TYPE", "BUCKET", "N", "RECORD",
              "STAKED", "P&L", "ROI"]]
    for r in rep.get("rows") or []:
        prows.append([
            str(r.get("whale")), str(r.get("sport")), str(r.get("category")),
            str(r.get("bucket")), str(r.get("n")),
            f"{r.get('wins', 0)}–{r.get('losses', 0)}",
            _money(r.get("staked")), _money(r.get("pnl")),
            f"{(r.get('roi') or 0) * 100:.1f}%" if r.get("roi") is not None
            else "—"])
    story.append(_table(prows, [80, 60, 70, 55, 30, 45, 65, 65, 40],
                        aligns="    rrrrr"))
    doc.build(story)
    return buf.getvalue()


def kalshi_manual_pdf(orders: list[dict]) -> bytes:
    """The desk team's manual Kalshi tickets, order by order."""
    from functools import partial

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                    Paragraph, Spacer)

    st = _styles()
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=letter,
                          leftMargin=10 * mm, rightMargin=10 * mm,
                          topMargin=22 * mm, bottomMargin=14 * mm)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="f")
    doc.addPageTemplates([PageTemplate(
        id="p", frames=[frame],
        onPage=partial(_header, title="KALSHI MANUAL ORDERS"))])
    story: list = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ")
    story.append(Paragraph("Kalshi Manual Order Report", st["h1"]))
    story.append(Paragraph(
        f"generated {now} · every manual desk ticket routed to Kalshi, "
        f"newest first", st["sub"]))

    filled = [o for o in orders
              if o.get("status") in ("filled", "settled", "cashed_out")]
    spend = sum(float(o.get("filled_usd") or 0) for o in filled)
    realized = sum(float(o.get("pnl") or 0) for o in orders
                   if o.get("pnl") is not None)
    story.append(_table(
        [["TICKETS", "FILLED", "SPEND (FILLED)", "REALIZED P&L"],
         [f"{len(orders):,}", f"{len(filled):,}", _money(spend),
          _money(realized)]],
        [90, 90, 110, 110], aligns="rrrr"))
    story.append(Spacer(1, 8))
    story.append(Paragraph("ORDERS", st["h2"]))
    rows = [["PLACED (UTC)", "MARKET", "SIDE", "LIMIT", "FILL",
             "SHARES", "COST", "STATUS", "P&L"]]
    for o in orders:
        placed = str(o.get("placed_at") or "")[:16].replace("T", " ")
        title = (o.get("market_title") or o.get("us_market_slug")
                 or o.get("asset") or "")[:46]
        rows.append([
            placed, title, str(o.get("side") or ""),
            f"{float(o['limit_price']):.2f}" if o.get("limit_price")
            is not None else "—",
            f"{float(o['fill_price']):.2f}" if o.get("fill_price")
            is not None else "—",
            f"{float(o.get('filled_shares') or 0):,.0f}",
            _money(float(o.get("filled_usd") or 0)),
            str(o.get("status") or ""),
            _money(float(o["pnl"])) if o.get("pnl") is not None else "—"])
    story.append(_table(rows, [62, 158, 28, 32, 32, 38, 55, 48, 55],
                        aligns="  rrrrr r"))
    doc.build(story)
    return buf.getvalue()
