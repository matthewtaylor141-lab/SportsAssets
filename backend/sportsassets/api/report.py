"""Downloadable reports on the AI trader's account record.

Three periods (daily / weekly / monthly), three formats:

  md    the operating report — written to be READ, by the team or by the
        AI cofounder: summary, dailies, breakdowns, and the full ledger,
        each figure carrying its sample size
  csv   the position-level export — one row per trade, for spreadsheets
  json  the raw payload — for anything programmatic

Everything is derived from the same /api/track-record builder the site
renders, so a report can never disagree with the page. Reports state their
own window and exclusion rules in the header: a report whose filters are
invisible is not evidence, it is marketing.
"""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime, timezone

from .track_record import DEFAULT_SINCE, track_record

PERIOD_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}

CSV_COLUMNS = [
    "entry_ts_utc", "entry_date", "sport", "category", "league", "title",
    "outcome", "market_slug", "entry_price", "fills", "qty", "stake",
    "value", "settled", "settled_ts_utc", "pnl", "unrealized",
]


def _iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _since_for(period: str) -> str:
    days = PERIOD_DAYS.get(period, 30)
    floor = datetime.strptime(DEFAULT_SINCE, "%Y-%m-%d").replace(
        tzinfo=timezone.utc)
    start = datetime.fromtimestamp(time.time() - days * 86_400, timezone.utc)
    return max(start, floor).strftime("%Y-%m-%d")


def to_csv(data: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in data.get("trades") or []:
        w.writerow([
            _iso(r.get("entry_ts")), r.get("entry_date"), r.get("sport"),
            r.get("category"), r.get("league"), r.get("title"),
            r.get("outcome"), r.get("market_slug"), r.get("entry_price"),
            r.get("fills"), r.get("qty"), r.get("stake"), r.get("value"),
            int(bool(r.get("settled"))), _iso(r.get("settled_ts")),
            r.get("pnl"), r.get("unrealized"),
        ])
    return buf.getvalue()


def _money(v) -> str:
    if v is None:
        return "—"
    return f"{'-' if v < 0 else ''}${abs(v):,.2f}"


def _group(rows: list[dict], key) -> list[tuple[str, dict]]:
    out: dict[str, dict] = {}
    for r in rows:
        g = out.setdefault(key(r), {"n": 0, "wins": 0, "stake": 0.0,
                                    "pnl": 0.0, "open": 0})
        if r["settled"] and r["pnl"] is not None:
            g["n"] += 1
            g["stake"] += r["stake"]
            g["pnl"] += r["pnl"]
            if r["pnl"] > 0:
                g["wins"] += 1
        else:
            g["open"] += 1
    return sorted(out.items(), key=lambda kv: -kv[1]["pnl"])


def to_markdown(data: dict, period: str) -> str:
    s = data["summary"]
    L: list[str] = []
    A = L.append
    A(f"# BettorEdge AI — {period.capitalize()} Report")
    A("")
    A(f"Generated {_iso(time.time())} · window {data['since']} → today (UTC), "
      f"entries in window · source: the live venue account "
      f"(positions + its own trade and resolution activities)")
    A("")
    ex = data.get("excluded_over_limit")
    if ex:
        A(f"> Exclusion rule: positions costing more than ${ex['limit']:.0f} "
          f"are excluded — this period: {ex['count']} "
          f"(stake {_money(ex['stake'])}, settled net {_money(ex['net_pnl'])}). "
          f"Undatable positions excluded: {data.get('excluded_undatable', 0)}.")
        A("")
    A("## Summary")
    A("")
    A(f"| | |")
    A(f"|---|---|")
    A(f"| Net P&L (settled) | **{_money(s['net_pnl'])}** |")
    A(f"| Record | {s['wins']}W – {s['losses']}L "
      f"({s['settled']} settled, {s['open']} open) |")
    win_rate = f"{s['win_rate']:.1%}" if s["win_rate"] is not None else "—"
    A(f"| Win rate | {win_rate} |")
    A(f"| Capital deployed | {_money(s['deployed'])} across {s['trades']} positions |")
    roi_txt = f"{s['roi']:+.2%}" if s["roi"] is not None else "—"
    A(f"| ROI on settled stake | {roi_txt} (on {_money(s['settled_stake'])}) |")
    A(f"| Open value | {_money(s['open_value'])} |")
    A("")
    if s["settled"] < 30:
        A(f"> EARLY SAMPLE: {s['settled']} settled. Return figures at this "
          f"sample size are noise; treat direction, not magnitude.")
        A("")

    daily = data.get("daily") or []
    if daily:
        A("## By day")
        A("")
        A("| date | entered | deployed | settled | W | P&L | note |")
        A("|---|---|---|---|---|---|---|")
        for d in daily:
            A(f"| {d['date']} | {d['trades']} | {_money(d['deployed'])} "
              f"| {d['settled']} | {d['wins']} | {_money(d['pnl'])} "
              f"| {'settle-day estimated' if d.get('pnl_estimated') else ''} |")
        A("")

    rows = data.get("trades") or []
    for title, key in (("By sport", lambda r: r["sport"]),
                       ("By bet type", lambda r: r["category"])):
        groups = _group(rows, key)
        if not groups:
            continue
        A(f"## {title}")
        A("")
        A("| | settled | W–L | open | stake | P&L | ROI |")
        A("|---|---|---|---|---|---|---|")
        for name, g in groups:
            roi = (f"{g['pnl'] / g['stake']:+.1%}"
                   if g["n"] >= 12 and g["stake"] else f"n={g['n']}")
            A(f"| {name} | {g['n']} | {g['wins']}–{g['n'] - g['wins']} "
              f"| {g['open']} | {_money(g['stake'])} | {_money(g['pnl'])} "
              f"| {roi} |")
        A("")

    A("## Ledger")
    A("")
    A("| entered | sport | type | market | outcome | entry | stake | status | P&L |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        status = ("WON" if r["settled"] and (r["pnl"] or 0) > 0 else
                  "LOST" if r["settled"] and (r["pnl"] or 0) < 0 else
                  "PUSH" if r["settled"] else "OPEN")
        entry = f"{r['entry_price'] * 100:.0f}c" if r.get("entry_price") else "—"
        A(f"| {r.get('entry_date') or '—'} | {r['sport']} | {r['category']} "
          f"| {(r['title'] or '')[:40]} | {(r['outcome'] or '')[:28]} "
          f"| {entry} | {_money(r['stake'])} | {status} "
          f"| {_money(r['pnl']) if r['settled'] else '—'} |")
    A("")
    A("---")
    A("*Every figure derives from the venue account via /api/track-record; "
      "this report cannot disagree with the site. Zero-realized settlements "
      "count as pushes, not losses.*")
    return "\n".join(L) + "\n"


async def build_report(period: str, fmt: str,
                       max_stake: float | None = 100.0):
    period = period if period in PERIOD_DAYS else "monthly"
    data = await track_record(_since_for(period), max_stake=max_stake)
    if not data.get("configured") or data.get("error"):
        return None, data
    if fmt == "csv":
        return to_csv(data), data
    if fmt == "md":
        return to_markdown(data, period), data
    return data, data
