"""INSTITUTIONAL REPORTING FOR THE PAPER ACCOUNT. Read-only.

What `/api/command/paper/reports/*` returns: the paper account reported to
the standards management would expect of a real-money account -- NAV,
P&L statement, performance, positions, blotters, attribution, risk,
reconciliation and CSV exports -- with every figure DERIVED from the one
paper ledger through the existing accounting:

  * cash / reserved / available, open positions, marks, realized and
    unrealized P&L and fees: `bettor_paper_ledger.balances` and
    `bettor_paper_ledger.positions` (the same function every paper page
    uses, so these reports can never disagree with them);
  * P&L by strategy: `bettor_paper_ops.pnl_by_strategy`;
  * strategy titles, versions and disclosures: `bettor_paper_ops._policy_meta`;
  * the ledger reconciliation: `agents.paper_brief.reconcile` (Audrey's);
  * both teams of each market: `team_logos.decorate`.

NOTHING HERE WRITES. Every statement is a SELECT; no table is written,
locked or reserved; no venue is called; no trading path imports this module.

THE STRUCTURE. The DB layer (`gather_*`, async) fetches plain rows; the PURE
layer (every function without `conn`) turns them into report sections and is
unit-tested on synthetic data (tests/test_paper_institutional.py). The pure
layer runs in a worker thread (`asyncio.to_thread`) so a large history never
stalls the API's event loop.

UNAVAILABLE IS NEVER ZERO. Every section is
    {"status": "OK" | "UNAVAILABLE", "reason": <why or null>, "data": ...}
and every figure that cannot be stated (an unmarked position, a day without
a marked snapshot, too few returns for a ratio) is None with its reason --
never 0. A figure that IS zero (no open positions -> unrealized P&L 0) is
stated as zero because it is exactly zero.

TRAINING IS KEPT APART. Every strategy is booked INVESTMENT or TRAINING
(`book_of`); every attribution, statistic and grouping carries the book, and
the training book always carries its disclosure: its positions may be taken
at NEGATIVE EXPECTED VALUE to generate forward experience -- a research
cost, not investment performance.
"""
from __future__ import annotations

import asyncio
import csv
import datetime as _dt
import io
import math
import time
from typing import Any, Iterable

VERSION = "PAPER_INSTITUTIONAL_REPORTS_V1"
BASIS = ("Paper account — simulated execution on live market data; reported "
         "to institutional standards")
DEFAULT_TZ = "America/New_York"
PERIODS_PER_YEAR = 365          # sports trade every day of the year
EPS = 1e-9

INVESTMENT = "INVESTMENT"
TRAINING = "TRAINING"
BOOKS = (INVESTMENT, TRAINING)
TRAINING_DISCLOSURE = (
    "TRAINING (exploration) positions are taken on the same paper account to "
    "generate forward experience and MAY FAIL the investment policy's edge "
    "and after-fee requirements: their expected value is recorded and may be "
    "NEGATIVE. Training P&L is a research cost, not investment performance, "
    "and is never added into the investment book.")

SHARPE_FORMULA = ("Sharpe-style ratio = mean(daily return) / sample standard "
                  "deviation(daily return) x sqrt(365); risk-free rate 0; "
                  "computed on PAPER results (simulated execution), not on "
                  "real-money performance")
VOL_FORMULA = ("annualised volatility = sample standard deviation(daily "
               "return) x sqrt(365)")
RETURN_FORMULA = ("daily return r_t = (NAV_t - NAV_{t-1} - F_t) / (NAV_{t-1} "
                  "+ F_t), where F_t is external funding on day t (funding at "
                  "the start of the day); a day whose NAV or prior NAV cannot "
                  "be stated has no return (never 0)")
PROFIT_FACTOR_FORMULA = ("profit factor = sum of realized P&L of winning "
                         "closed positions / |sum of realized P&L of losing "
                         "closed positions|")
EXPECTANCY_FORMULA = ("expectancy = mean realized P&L per closed position "
                      "= win rate x average win + loss rate x average loss")
WIN_RATE_FORMULA = ("win rate = closed positions with realized P&L > 0 / all "
                    "closed positions (flat positions count in the "
                    "denominator)")
NAV_FORMULA = ("NAV = ledger cash + marked value of open positions (marks: "
               "bettor_paper_ledger.MARK_METHOD); not stated while any open "
               "position has no available mark")
UTILISATION_FORMULA = ("cash utilisation = (NAV - available cash) / NAV: the "
                       "share of NAV in open positions or reserved for open "
                       "orders")
NET_EXPOSURE_FORMULA = ("net exposure = gross marked exposure less the value "
                        "locked by holding both sides of the same market "
                        "(min(long qty, short qty) x $1, which pays "
                        "regardless of outcome)")


# ═════════════════════════════════════════════════════════════════════
# SECTIONS AND NUMBERS (pure)
# ═════════════════════════════════════════════════════════════════════

def ok(data, **extra) -> dict:
    out = {"status": "OK", "reason": None, "data": data}
    out.update(extra)
    return out


def unavailable(reason: str, **extra) -> dict:
    out = {"status": "UNAVAILABLE", "reason": str(reason), "data": None}
    out.update(extra)
    return out


def num(v) -> float | None:
    """A finite float, or None. Never turns a missing figure into 0."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def r6(v) -> float | None:
    x = num(v)
    return None if x is None else round(x, 6)


def sum_or_none(values: Iterable) -> float | None:
    """Sum, or None when ANY value is unavailable (None)."""
    total = 0.0
    for v in values:
        x = num(v)
        if x is None:
            return None
        total += x
    return round(total, 6)


def ratio(a, b) -> float | None:
    a, b = num(a), num(b)
    if a is None or b is None or abs(b) < EPS:
        return None
    return a / b


def pct(a, b) -> float | None:
    x = ratio(a, b)
    return None if x is None else round(x * 100.0, 6)


# ═════════════════════════════════════════════════════════════════════
# STRATEGIES AND BOOKS (pure)
# ═════════════════════════════════════════════════════════════════════

def strategy_meta() -> dict:
    """{strategy: {kind, title, label, version, disclosure, ...}} from the
    paper operations read (which reads the policy module), so the reports
    name exactly what the decision writer records."""
    try:
        from . import bettor_paper_ops as OPS
        return OPS._policy_meta()
    except Exception:                                           # noqa: BLE001
        return {}


def book_of(strategy, meta: dict | None = None) -> str:
    """INVESTMENT or TRAINING. A strategy is TRAINING when its recorded kind
    is TRAINING (the exploration strategy); everything else is booked as
    investment (with its own kind -- research, benchmark -- kept beside)."""
    m = (meta if meta is not None else strategy_meta()).get(strategy) or {}
    if m.get("kind") == TRAINING:
        return TRAINING
    if str(strategy or "").upper().find("EXPLORATION") >= 0:
        return TRAINING
    return INVESTMENT


def strategy_info(strategy, meta: dict) -> dict:
    m = meta.get(strategy) or {}
    book = book_of(strategy, meta)
    return {"strategy": strategy, "book": book,
            "kind": m.get("kind") or "OTHER",
            "title": m.get("title") or strategy,
            "label": m.get("label"),
            "version": m.get("version"),
            "economics_label": m.get("economics_label"),
            "disclosure": (m.get("disclosure") or (
                TRAINING_DISCLOSURE if book == TRAINING else None)),
            "negative_ev_disclosure": (TRAINING_DISCLOSURE
                                       if book == TRAINING else None)}


# ═════════════════════════════════════════════════════════════════════
# NAV, DRAWDOWN, RETURNS (pure)
# ═════════════════════════════════════════════════════════════════════

def compute_nav(cash_usd, open_positions: list) -> dict:
    """NAV = cash + marked value of open positions -- the same rule as
    bettor_paper_ledger.balances. Any open position without a mark makes
    NAV UNAVAILABLE (None), with the marked-only figure beside it."""
    cash = num(cash_usd)
    if cash is None:
        return {"nav_usd": None, "marked_only_usd": None, "unmarked": 0,
                "reason": "CASH_UNAVAILABLE"}
    value, unmarked = 0.0, 0
    for p in open_positions or []:
        mv = num(p.get("marked_value_usd"))
        if mv is None:
            unmarked += 1
        else:
            value += mv
    marked_only = round(cash + value, 6)
    return {"nav_usd": None if unmarked else marked_only,
            "marked_only_usd": marked_only, "unmarked": unmarked,
            "reason": ("%d_OPEN_POSITION(S)_HAVE_NO_AVAILABLE_MARK" % unmarked
                       if unmarked else None)}


def drawdown(points: list, *, initial_peak=None) -> dict:
    """PEAK-TO-TROUGH over a NAV series (values may be None: skipped and
    counted, never treated as 0). `initial_peak` (starting capital) is the
    first peak, as in bettor_paper_readmodel.drawdown."""
    peak = num(initial_peak)
    max_dd, max_dd_pct, skipped, cur = 0.0, 0.0, 0, None
    trough_at = None
    for i, v in enumerate(points):
        x = num(v)
        if x is None:
            skipped += 1
            continue
        cur = x
        if peak is None or x > peak:
            peak = x
        dd = peak - x
        if dd > max_dd + EPS:
            max_dd = dd
            max_dd_pct = dd / peak if peak else 0.0
            trough_at = i
    return {"high_water_mark_usd": r6(peak),
            "current_nav_usd": r6(cur),
            "current_drawdown_usd": (None if cur is None or peak is None
                                     else round(peak - cur, 6)),
            "current_drawdown_pct": (None if cur is None or not peak
                                     else round((peak - cur) / peak * 100, 6)),
            "max_drawdown_usd": round(max_dd, 6),
            "max_drawdown_pct": round(max_dd_pct * 100.0, 6),
            "trough_index": trough_at,
            "points_used": len(points) - skipped,
            "points_skipped_unavailable": skipped}


def period_return(nav_prev, flow, nav_end) -> float | None:
    """(NAV_t - NAV_{t-1} - F_t) / (NAV_{t-1} + F_t); None when a NAV is
    unavailable or the base is zero."""
    a, b = num(nav_prev), num(nav_end)
    f = num(flow) or 0.0
    if a is None or b is None:
        return None
    base = a + f
    if abs(base) < EPS:
        return None
    return (b - a - f) / base


def return_stats(returns: list, *,
                 periods_per_year: int = PERIODS_PER_YEAR) -> dict:
    """Mean, volatility and the Sharpe-style ratio over the AVAILABLE daily
    returns (None entries are excluded and counted)."""
    rs = [num(r) for r in returns]
    have = [r for r in rs if r is not None]
    n = len(have)
    out = {"n_returns": n, "n_unavailable": len(rs) - n,
           "periods_per_year": periods_per_year,
           "mean_daily_return": None, "stdev_daily_return": None,
           "volatility_annualised": None, "sharpe_ratio": None,
           "sharpe_formula": SHARPE_FORMULA, "volatility_formula": VOL_FORMULA,
           "reason": None}
    if n == 0:
        out["reason"] = "NO_DAILY_RETURN_AVAILABLE"
        return out
    mean = sum(have) / n
    out["mean_daily_return"] = round(mean, 10)
    if n < 2:
        out["reason"] = "AT_LEAST_TWO_DAILY_RETURNS_ARE_NEEDED"
        return out
    var = sum((r - mean) ** 2 for r in have) / (n - 1)
    sd = math.sqrt(var)
    out["stdev_daily_return"] = round(sd, 10)
    out["volatility_annualised"] = round(sd * math.sqrt(periods_per_year), 10)
    if sd < 1e-12:
        out["reason"] = "ZERO_VOLATILITY_RATIO_UNDEFINED"
        return out
    out["sharpe_ratio"] = round(mean / sd * math.sqrt(periods_per_year), 6)
    return out


def cumulative_return(returns: list) -> float | None:
    """Geometric link of the available daily returns; None if none."""
    have = [num(r) for r in returns if num(r) is not None]
    if not have:
        return None
    g = 1.0
    for r in have:
        g *= (1.0 + r)
    return round(g - 1.0, 10)


# ═════════════════════════════════════════════════════════════════════
# THE DAILY SERIES (pure)
# ═════════════════════════════════════════════════════════════════════

def local_day(epoch: float, tz) -> _dt.date:
    return _dt.datetime.fromtimestamp(float(epoch), tz).date()


def day_end_epoch(day: _dt.date, tz) -> float:
    nxt = _dt.datetime.combine(day + _dt.timedelta(days=1), _dt.time(0), tz)
    return nxt.timestamp()


def open_count_at_day_ends(events: list, days: list, tz) -> dict:
    """{day: number of positions with open quantity at that day's end}.
    `events`: (epoch, position_key, signed qty) -- BUY fills +qty, SELL fills
    and settlements -qty. A sweep, O(events + days)."""
    evs = sorted(events, key=lambda e: e[0])
    held: dict = {}
    out, i = {}, 0
    for d in days:
        end = day_end_epoch(d, tz)
        while i < len(evs) and evs[i][0] < end:
            _, k, q = evs[i]
            held[k] = held.get(k, 0.0) + float(q)
            i += 1
        out[d] = sum(1 for v in held.values() if v > EPS)
    return out


def realized_events(positions: list, sale_fills: list) -> list:
    """(epoch, position_key, strategy, realized_usd, kind, role) for every
    realization -- each sale fill and each settlement -- using the
    position's average cost incl. fees, exactly as
    bettor_paper_ledger.positions books realized P&L (so the events sum to
    each position's realized_pnl_usd)."""
    by_pk = {p["position_key"]: p for p in positions}
    out = []
    for s in sale_fills:
        p = by_pk.get(s.get("position_key"))
        if p is None:
            continue
        avg = num(p.get("avg_cost_per_contract_incl_fees")) or 0.0
        q = num(s.get("qty")) or 0.0
        proceeds = (num(s.get("gross_usd")) or 0.0) - (num(s.get("fee_usd"))
                                                       or 0.0)
        out.append((num(s.get("filled_at")), p["position_key"],
                    p.get("strategy"), round(proceeds - avg * q, 6), "SALE",
                    s.get("role")))
    for p in positions:
        st = p.get("settlement")
        if not st:
            continue
        avg = num(p.get("avg_cost_per_contract_incl_fees")) or 0.0
        q = num(p.get("settled_qty")) or 0.0
        out.append((num(st.get("settled_at")), p["position_key"],
                    p.get("strategy"),
                    round((num(st.get("payout_usd")) or 0.0) - avg * q, 6),
                    "SETTLEMENT", "SETTLEMENT"))
    return [e for e in out if e[0] is not None]


def build_daily(*, days: list, tz, cash_eod: dict, open_eod: dict,
                snaps: dict, realized_by_day: dict, fees_by_day: dict,
                flows_by_day: dict, live: dict | None,
                starting_capital=None) -> list:
    """ONE ROW PER REPORTING DAY.

    NAV at the day's end, by the first basis that applies:
      LIVE                  today: balances() now (None if any mark missing)
      CASH_NO_OPEN_POSITIONS no position was open at the day's end: NAV is
                            exactly the ledger cash
      MARKED_SNAPSHOT       the day's last equity snapshot with complete marks
      UNAVAILABLE           otherwise (None, never 0)
    net P&L = NAV_end - NAV_prev - funding; realized = the day's sale and
    settlement realizations; unrealized change = net - realized (the
    mark-to-market movement); fees are a memo (already inside P&L)."""
    rows = []
    prev_nav = 0.0          # before funding the account held nothing
    cash_carry = None
    peak = num(starting_capital)
    cum_g = 1.0
    cum_have = False
    today = days[-1] if days else None
    for d in days:
        if d in cash_eod:
            cash_carry = cash_eod[d]
        cash = num(cash_carry)
        flow = num(flows_by_day.get(d)) or 0.0
        nav, unreal, basis, why = None, None, "UNAVAILABLE", None
        if live is not None and d == today:
            nav, unreal = num(live.get("nav_usd")), num(live.get(
                "unrealized_usd"))
            basis = "LIVE" if nav is not None else "UNAVAILABLE"
            why = None if nav is not None else live.get("reason")
        elif open_eod.get(d, 0) == 0 and cash is not None:
            nav, unreal, basis = cash, 0.0, "CASH_NO_OPEN_POSITIONS"
        elif d in snaps and num(snaps[d].get("equity_usd")) is not None:
            s = snaps[d]
            nav, basis = num(s["equity_usd"]), "MARKED_SNAPSHOT"
            unreal = num(s.get("unrealized_pnl_usd"))
        else:
            why = "NO_COMPLETE_MARKED_SNAPSHOT_FOR_THIS_DAY"
        realized = round(num(realized_by_day.get(d)) or 0.0, 6)
        fees = round(num(fees_by_day.get(d)) or 0.0, 6)
        net = (None if nav is None or prev_nav is None
               else round(nav - prev_nav - flow, 6))
        r = period_return(prev_nav, flow, nav)
        if r is not None:
            cum_g *= (1.0 + r)
            cum_have = True
        if nav is not None:
            peak = nav if peak is None else max(peak, nav)
        dd = (None if nav is None or peak is None else round(peak - nav, 6))
        rows.append({
            "date": d.isoformat(), "nav_usd": r6(nav), "nav_basis": basis,
            "nav_unavailable_reason": why,
            "cash_usd": r6(cash), "funding_usd": round(flow, 6),
            "realized_pnl_usd": realized, "fees_usd": fees,
            "unrealized_pnl_end_usd": r6(unreal),
            "unrealized_change_usd": (None if net is None
                                      else round(net - realized, 6)),
            "net_pnl_usd": net,
            "daily_return": None if r is None else round(r, 10),
            "cumulative_return": (round(cum_g - 1.0, 10) if cum_have
                                  and r is not None else None),
            "high_water_mark_usd": r6(peak),
            "drawdown_usd": dd,
            "drawdown_pct": (None if dd is None or not peak
                             else round(dd / peak * 100.0, 6))})
        prev_nav = nav
    return rows


def period_start(today: _dt.date, period: str) -> _dt.date | None:
    p = str(period or "ALL").upper()
    if p in ("1D", "DTD", "DAY"):
        return today
    if p in ("1W", "WTD", "WEEK"):
        return today - _dt.timedelta(days=today.weekday())
    if p in ("7D",):
        return today - _dt.timedelta(days=6)
    if p in ("MTD", "MONTH"):
        return today.replace(day=1)
    if p in ("30D",):
        return today - _dt.timedelta(days=29)
    if p in ("YTD", "YEAR"):
        return today.replace(month=1, day=1)
    return None                                                 # ALL


def period_pnl(rows: list, start: _dt.date | None,
               end: _dt.date | None = None) -> dict:
    """P&L over [start, end] from the daily rows: net = NAV at the end minus
    NAV at the close before the start minus funding inside the period (None
    when either NAV is unavailable); realized and fees are summed."""
    sel, before = [], None
    for r in rows:
        d = _dt.date.fromisoformat(r["date"])
        if start is not None and d < start:
            before = r
            continue
        if end is not None and d > end:
            continue
        sel.append(r)
    if not sel:
        return {"days": 0, "net_pnl_usd": None, "realized_pnl_usd": None,
                "fees_usd": None, "unrealized_change_usd": None,
                "return": None, "reason": "NO_DAY_IN_PERIOD"}
    nav_start = 0.0 if before is None else num(before.get("nav_usd"))
    nav_end = num(sel[-1].get("nav_usd"))
    flows = sum(num(r.get("funding_usd")) or 0.0 for r in sel)
    realized = round(sum(num(r.get("realized_pnl_usd")) or 0.0
                         for r in sel), 6)
    fees = round(sum(num(r.get("fees_usd")) or 0.0 for r in sel), 6)
    net = (None if nav_start is None or nav_end is None
           else round(nav_end - nav_start - flows, 6))
    rets = [r.get("daily_return") for r in sel]
    return {"days": len(sel), "start": sel[0]["date"], "end": sel[-1]["date"],
            "nav_start_usd": r6(nav_start), "nav_end_usd": r6(nav_end),
            "funding_usd": round(flows, 6), "net_pnl_usd": net,
            "realized_pnl_usd": realized, "fees_usd": fees,
            "unrealized_change_usd": (None if net is None
                                      else round(net - realized, 6)),
            "return": (cumulative_return(rets)
                       if all(num(x) is not None for x in rets) else None),
            "reason": (None if net is not None else
                       "NAV_UNAVAILABLE_AT_THE_PERIOD_START_OR_END")}


# ═════════════════════════════════════════════════════════════════════
# CLOSED-POSITION STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

def is_closed(p: dict) -> bool:
    return (num(p.get("open_qty")) or 0.0) <= EPS


def trade_stats(positions: list) -> dict:
    """Win rate, average win / loss, profit factor, expectancy, best and
    worst -- over CLOSED positions only (sold or settled out), each with its
    realized P&L as booked by the ledger."""
    closed = [p for p in positions if is_closed(p)]
    pnl = [(num(p.get("realized_pnl_usd")) or 0.0, p) for p in closed]
    wins = [x for x, _ in pnl if x > EPS]
    losses = [x for x, _ in pnl if x < -EPS]
    n = len(pnl)
    gross_win, gross_loss = sum(wins), sum(losses)
    best = max(pnl, key=lambda t: t[0]) if pnl else None
    worst = min(pnl, key=lambda t: t[0]) if pnl else None

    def brief(t):
        if t is None:
            return None
        x, p = t
        return {"position_key": p.get("position_key"),
                "us_market_slug": p.get("us_market_slug"),
                "strategy": p.get("strategy"),
                "realized_pnl_usd": round(x, 6),
                "outcome": (p.get("settlement") or {}).get("outcome")}
    pf, pf_why = None, None
    if not pnl:
        pf_why = "NO_CLOSED_POSITION"
    elif not losses:
        pf_why = "UNDEFINED_NO_LOSING_POSITION"
    else:
        pf = round(gross_win / abs(gross_loss), 6)
    return {"closed_positions": n, "wins": len(wins), "losses": len(losses),
            "flat": n - len(wins) - len(losses),
            "settled": sum(1 for _, p in pnl if p.get("settlement")),
            "win_rate": None if not n else round(len(wins) / n, 6),
            "average_win_usd": (round(gross_win / len(wins), 6)
                                if wins else None),
            "average_loss_usd": (round(gross_loss / len(losses), 6)
                                 if losses else None),
            "gross_wins_usd": round(gross_win, 6),
            "gross_losses_usd": round(gross_loss, 6),
            "profit_factor": pf, "profit_factor_reason": pf_why,
            "expectancy_usd": (round(sum(x for x, _ in pnl) / n, 6)
                               if n else None),
            "total_realized_usd": round(sum(x for x, _ in pnl), 6),
            "best_position": brief(best), "worst_position": brief(worst),
            "formulas": {"win_rate": WIN_RATE_FORMULA,
                         "profit_factor": PROFIT_FACTOR_FORMULA,
                         "expectancy": EXPECTANCY_FORMULA}}


# ═════════════════════════════════════════════════════════════════════
# ATTRIBUTION (pure)
# ═════════════════════════════════════════════════════════════════════

def attribution_by_strategy(rows: list, meta: dict) -> list:
    """bettor_paper_ops.pnl_by_strategy rows, with each strategy's book,
    title, version and disclosure, and net = realized + unrealized (None
    while any of the strategy's open positions is unmarked)."""
    out = []
    for r in rows:
        info = strategy_info(r.get("strategy"), meta)
        u = r.get("unrealized_pnl_usd")
        out.append(dict(
            r, **info,
            net_pnl_usd=(None if u is None else round(
                (num(r.get("realized_pnl_usd")) or 0.0) + num(u), 6)),
            fees_note="fees are included in realized and unrealized P&L"))
    out.sort(key=lambda x: (BOOKS.index(x["book"]), x["strategy"] or ""))
    return out


def books_from_strategies(by_strategy: list) -> dict:
    """{INVESTMENT: {...}, TRAINING: {...}} -- each book its own total; the
    training book is never folded into investment. Unrealized/net are None
    for a book with any unmarked open position."""
    out = {}
    for b in BOOKS:
        rows = [r for r in by_strategy if r.get("book") == b]
        out[b] = {
            "book": b, "strategies": [r["strategy"] for r in rows],
            "realized_pnl_usd": round(sum(num(r.get("realized_pnl_usd"))
                                          or 0.0 for r in rows), 6),
            "unrealized_pnl_usd": sum_or_none(
                r.get("unrealized_pnl_usd") for r in rows),
            "unrealized_marked_only_usd": round(sum(
                num(r.get("unrealized_marked_only_usd")) or 0.0
                for r in rows), 6),
            "fees_usd": round(sum(num(r.get("fees_usd")) or 0.0
                                  for r in rows), 6),
            "open_positions": sum(int(r.get("open_positions") or 0)
                                  for r in rows),
            "closed_positions": sum(int(r.get("closed_positions") or 0)
                                    for r in rows),
            "unmarked_positions": sum(int(r.get("unmarked_positions") or 0)
                                      for r in rows),
            "disclosure": TRAINING_DISCLOSURE if b == TRAINING else None}
        u = out[b]["unrealized_pnl_usd"]
        out[b]["net_pnl_usd"] = (None if u is None else round(
            out[b]["realized_pnl_usd"] + u, 6))
    return out


def group_pnl(positions: list, key_fn, *, label: str) -> list:
    """P&L grouped by (key, book): realized over every position, unrealized
    over open ones (None for a group holding an unmarked position)."""
    by: dict = {}
    for p in positions:
        k = key_fn(p) or "UNIDENTIFIED"
        b = p.get("book") or INVESTMENT
        g = by.setdefault((k, b), {label: k, "book": b, "positions": 0,
                                   "open_positions": 0,
                                   "realized_pnl_usd": 0.0,
                                   "unrealized_pnl_usd": 0.0,
                                   "unmarked_positions": 0,
                                   "open_cost_basis_usd": 0.0})
        g["positions"] += 1
        g["realized_pnl_usd"] += num(p.get("realized_pnl_usd")) or 0.0
        if not is_closed(p):
            g["open_positions"] += 1
            g["open_cost_basis_usd"] += num(p.get("cost_basis_usd")) or 0.0
            u = num(p.get("unrealized_pnl_usd"))
            if u is None:
                g["unmarked_positions"] += 1
            else:
                g["unrealized_pnl_usd"] += u
    out = []
    for g in by.values():
        if g["unmarked_positions"]:
            g["unrealized_marked_only_usd"] = round(g["unrealized_pnl_usd"], 6)
            g["unrealized_pnl_usd"] = None
        else:
            g["unrealized_pnl_usd"] = round(g["unrealized_pnl_usd"], 6)
        g["realized_pnl_usd"] = round(g["realized_pnl_usd"], 6)
        g["open_cost_basis_usd"] = round(g["open_cost_basis_usd"], 6)
        g["net_pnl_usd"] = (None if g["unrealized_pnl_usd"] is None
                            else round(g["realized_pnl_usd"]
                                       + g["unrealized_pnl_usd"], 6))
        out.append(g)
    out.sort(key=lambda g: (BOOKS.index(g["book"]),
                            -(g["realized_pnl_usd"]
                              + (g["unrealized_pnl_usd"] or 0.0))))
    return out


def by_decision_type(events: list, open_positions: list,
                     meta: dict) -> list:
    """P&L by the AGENT DECISION that produced it: realized at settlement of
    Derek's entries (held to resolution), realized by each of Xavier's
    management order roles (EXIT / REDUCE / HEDGE / STANDING_PROTECTION
    sales), and the unrealized P&L of entries still open (marked)."""
    by: dict = {}

    def row(agent, typ, book):
        return by.setdefault((agent, typ, book), {
            "agent": agent, "decision_type": typ, "book": book,
            "events": 0, "realized_pnl_usd": 0.0,
            "unrealized_pnl_usd": 0.0, "unmarked_positions": 0})
    for (_at, _pk, strat, pnl, kind, role) in events:
        b = book_of(strat, meta)
        if kind == "SETTLEMENT":
            r = row("Derek", "ENTRY_HELD_TO_SETTLEMENT", b)
        else:
            r = row("Xavier" if role != "ENTRY" else "Derek",
                    "%s_SALE" % (role or "UNKNOWN"), b)
        r["events"] += 1
        r["realized_pnl_usd"] += num(pnl) or 0.0
    for p in open_positions:
        r = row("Derek", "ENTRY_OPEN_MARKED", book_of(p.get("strategy"),
                                                      meta))
        r["events"] += 1
        u = num(p.get("unrealized_pnl_usd"))
        if u is None:
            r["unmarked_positions"] += 1
        else:
            r["unrealized_pnl_usd"] += u
    out = []
    for r in by.values():
        r["realized_pnl_usd"] = round(r["realized_pnl_usd"], 6)
        r["unrealized_pnl_usd"] = (None if r["unmarked_positions"]
                                   else round(r["unrealized_pnl_usd"], 6))
        out.append(r)
    out.sort(key=lambda r: (BOOKS.index(r["book"]), r["agent"],
                            r["decision_type"]))
    return out


# ═════════════════════════════════════════════════════════════════════
# RISK (pure)
# ═════════════════════════════════════════════════════════════════════

def exposure_value(p: dict) -> tuple:
    """(value, basis): marked value when marked, else the cost basis --
    labelled, so an unmarked position still shows its capital at risk."""
    mv = num(p.get("marked_value_usd"))
    if mv is not None:
        return mv, "MARKED"
    return num(p.get("cost_basis_usd")) or 0.0, "COST_BASIS_UNMARKED"


def exposures(open_positions: list, nav, *, key_fn, label: str) -> list:
    by: dict = {}
    for p in open_positions:
        k = key_fn(p) or "UNIDENTIFIED"
        g = by.setdefault(k, {label: k, "positions": 0,
                              "cost_basis_usd": 0.0, "marked_value_usd": 0.0,
                              "unmarked_positions": 0,
                              "max_loss_usd": 0.0, "books": set()})
        g["positions"] += 1
        g["books"].add(p.get("book") or INVESTMENT)
        cb = num(p.get("cost_basis_usd")) or 0.0
        g["cost_basis_usd"] += cb
        g["max_loss_usd"] -= cb
        mv = num(p.get("marked_value_usd"))
        if mv is None:
            g["unmarked_positions"] += 1
        else:
            g["marked_value_usd"] += mv
    out = []
    for g in by.values():
        g["books"] = sorted(g["books"])
        mv = None if g["unmarked_positions"] else round(
            g["marked_value_usd"], 6)
        g["marked_value_marked_only_usd"] = round(g.pop("marked_value_usd"), 6)
        g["marked_value_usd"] = mv
        g["cost_basis_usd"] = round(g["cost_basis_usd"], 6)
        g["max_loss_usd"] = round(g["max_loss_usd"], 6)
        g["pct_of_nav"] = pct(mv if mv is not None else g["cost_basis_usd"],
                              nav)
        g["pct_basis"] = ("MARKED" if mv is not None
                          else "COST_BASIS_UNMARKED")
        out.append(g)
    out.sort(key=lambda g: -(g["marked_value_usd"]
                             if g["marked_value_usd"] is not None
                             else g["cost_basis_usd"]))
    return out


def concentration(open_positions: list, nav, *, top: int = 10) -> dict:
    vals = []
    for p in open_positions:
        v, basis = exposure_value(p)
        vals.append((v, basis, p))
    vals.sort(key=lambda t: -t[0])
    total = sum(v for v, _, _ in vals)
    rows = [{"position_key": p.get("position_key"),
             "us_market_slug": p.get("us_market_slug"),
             "market": p.get("market"), "matchup": p.get("matchup"),
             "strategy": p.get("strategy"), "book": p.get("book"),
             "value_usd": round(v, 6), "value_basis": basis,
             "pct_of_nav": pct(v, nav),
             "pct_of_gross": pct(v, total)} for v, basis, p in vals[:top]]
    hhi = (round(sum((v / total) ** 2 for v, _, _ in vals), 6)
           if total > EPS else None)
    return {"positions": len(vals), "gross_value_usd": round(total, 6),
            "largest_pct_of_nav": rows[0]["pct_of_nav"] if rows else None,
            "top5_pct_of_nav": pct(sum(v for v, _, _ in vals[:5]), nav)
            if vals else None,
            "herfindahl_index": hhi,
            "herfindahl_note": ("sum of squared shares of gross open value; "
                                "1.0 = one position, 1/N = equal weights"),
            "largest": rows}


def gross_net_exposure(open_positions: list) -> dict:
    """Gross = sum of marked value of open contracts (None if any unmarked);
    net = gross less the value locked by holding both sides of one market."""
    gross = sum_or_none(p.get("marked_value_usd") for p in open_positions)
    by_mkt: dict = {}
    for p in open_positions:
        m = by_mkt.setdefault(p.get("us_market_slug"), {"LONG": 0.0,
                                                        "SHORT": 0.0})
        side = "SHORT" if p.get("holding_side") == "SHORT" else "LONG"
        m[side] += num(p.get("open_qty")) or 0.0
    locked = round(sum(min(m["LONG"], m["SHORT"]) for m in by_mkt.values()),
                   6)
    return {"gross_exposure_usd": gross,
            "locked_both_sides_usd": locked,
            "net_exposure_usd": (None if gross is None
                                 else round(max(0.0, gross - locked), 6)),
            "net_formula": NET_EXPOSURE_FORMULA}


# ═════════════════════════════════════════════════════════════════════
# RECONCILIATION CHECKS (pure)
# ═════════════════════════════════════════════════════════════════════

def fill_ledger_discrepancies(rows: list, *, tol: float = 1e-6) -> list:
    """Every fill must have exactly one ledger entry: FILL with cash
    -(gross + fee) for a BUY, SALE with cash +(gross - fee) for a SELL."""
    out = []
    for r in rows:
        gross, fee = num(r.get("gross_usd")) or 0.0, num(r.get("fee_usd")) \
            or 0.0
        want_kind = "FILL" if r.get("direction") == "BUY" else "SALE"
        want = -(gross + fee) if want_kind == "FILL" else gross - fee
        n = int(r.get("ledger_entries") or 0)
        if n != 1:
            out.append({"check": "ONE_LEDGER_ENTRY_PER_FILL",
                        "fill_id": r.get("fill_id"),
                        "order_id": r.get("order_id"),
                        "ledger_entries": n, "expected": 1})
            continue
        if r.get("ledger_kind") != want_kind:
            out.append({"check": "LEDGER_KIND_MATCHES_FILL_DIRECTION",
                        "fill_id": r.get("fill_id"),
                        "ledger_seq": r.get("ledger_seq"),
                        "ledger_kind": r.get("ledger_kind"),
                        "expected": want_kind})
        got = num(r.get("ledger_cash_delta_usd"))
        if got is None or abs(got - want) > tol:
            out.append({"check": "LEDGER_CASH_EQUALS_FILL_CASH",
                        "fill_id": r.get("fill_id"),
                        "ledger_seq": r.get("ledger_seq"),
                        "ledger_cash_delta_usd": got,
                        "fill_cash_usd": round(want, 6)})
    return out


def settlement_discrepancies(positions: list, ledger_by_pk: dict, *,
                             tol: float = 1e-6) -> list:
    """Each settled position's latest payout must equal the ledger's
    SETTLEMENT + CORRECTION cash for that position."""
    out = []
    for p in positions:
        st = p.get("settlement")
        if not st:
            continue
        got = num(ledger_by_pk.get(p["position_key"]))
        want = num(st.get("payout_usd")) or 0.0
        if got is None or abs(got - want) > tol:
            out.append({"check": "SETTLEMENT_PAYOUT_EQUALS_LEDGER_CREDIT",
                        "position_key": p["position_key"],
                        "settlement_payout_usd": want,
                        "ledger_credit_usd": got})
    return out


def position_discrepancies(positions: list) -> list:
    out = []
    for p in positions:
        b = num(p.get("bought_qty")) or 0.0
        s = (num(p.get("sold_qty")) or 0.0) + (num(p.get("settled_qty"))
                                                or 0.0)
        if (num(p.get("open_qty")) or 0.0) < -EPS or s > b + 1e-6:
            out.append({"check": "OPEN_QUANTITY_IS_NOT_NEGATIVE",
                        "position_key": p.get("position_key"),
                        "bought_qty": b, "sold_or_settled_qty": s})
    return out


def order_fill_discrepancies(rows: list, *, tol: float = 1e-6) -> list:
    """An order's filled_qty must equal the sum of its fills."""
    out = []
    for r in rows:
        a, b = num(r.get("filled_qty")) or 0.0, num(r.get("fills_qty")) or 0.0
        if abs(a - b) > tol:
            out.append({"check": "ORDER_FILLED_QTY_EQUALS_SUM_OF_FILLS",
                        "order_id": r.get("order_id"),
                        "order_filled_qty": a, "fills_qty": b})
    return out


def reservation_discrepancy(open_reserved, ledger_reserved, *,
                            tol: float = 1e-6) -> list:
    a, b = num(open_reserved), num(ledger_reserved)
    if a is None or b is None or abs(a - b) > tol:
        return [{"check": "OPEN_ORDER_RESERVATIONS_EQUAL_LEDGER_RESERVED",
                 "open_orders_reserved_usd": a, "ledger_reserved_usd": b}]
    return []


# ═════════════════════════════════════════════════════════════════════
# CSV (pure)
# ═════════════════════════════════════════════════════════════════════

def to_csv(rows: list, columns: list, *, title: str, as_of: float) -> str:
    """CSV with two '#' disclosure lines. An unavailable figure is written
    as UNAVAILABLE, never 0 or blank-as-zero."""
    buf = io.StringIO()
    buf.write("# %s\n" % BASIS)
    buf.write("# %s · as of %s UTC\n" % (title, _dt.datetime.fromtimestamp(
        as_of, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([c[0] for c in columns])
    for r in rows:
        line = []
        for _, getter in columns:
            v = getter(r) if callable(getter) else r.get(getter)
            line.append("UNAVAILABLE" if v is None else v)
        w.writerow(line)
    return buf.getvalue()


def iso(epoch) -> str | None:
    x = num(epoch)
    if x is None:
        return None
    return _dt.datetime.fromtimestamp(x, _dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ═════════════════════════════════════════════════════════════════════
# LABELS (pure): market name, league, sport
# ═════════════════════════════════════════════════════════════════════

def league_of(p: dict) -> str | None:
    """The league of the venue's own team rows (us_premap via team_logos);
    else the decision label's competition; else None (UNIDENTIFIED)."""
    for t in (p.get("matchup") or []):
        if t.get("league"):
            return str(t["league"]).strip().lower()
    lb = p.get("label") or {}
    c = lb.get("competition") if isinstance(lb, dict) else None
    return str(c).strip().lower() if c else None


def sport_of(league) -> str | None:
    if not league:
        return None
    try:
        from . import bettor_sport_mapping as SM
        sport, _why = SM.sport_from_league(league)
        return sport
    except Exception:                                           # noqa: BLE001
        return None


def market_label(p: dict) -> dict:
    try:
        from . import bettor_paper_ops as OPS
        return OPS.market_name(p.get("label"))
    except Exception:                                           # noqa: BLE001
        return {"title": None, "selection": None}


def event_of(p: dict) -> str:
    lb = p.get("label") or {}
    m = p.get("matchup") or []
    if len(m) >= 2:
        return "%s vs %s" % (m[0].get("name"), m[1].get("name"))
    if isinstance(lb, dict) and (lb.get("event_title") or lb.get("home_team")):
        return lb.get("event_title") or "%s at %s" % (lb.get("away_team"),
                                                      lb.get("home_team"))
    return p.get("fixture") or (lb.get("event_key") if isinstance(lb, dict)
                                else None) or p.get("us_market_slug") \
        or "UNIDENTIFIED"


# ═════════════════════════════════════════════════════════════════════
# THE DB LAYER (reads only)
# ═════════════════════════════════════════════════════════════════════

def _L():
    from . import bettor_paper_ledger as L
    return L


def _epoch(v):
    return _L()._epoch(v)


def _zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:                                           # noqa: BLE001
        return _dt.timezone.utc


def base(now: float, account_id: str) -> dict:
    L = _L()
    return {"version": VERSION, "as_of": now, "as_of_iso": iso(now),
            "account_id": account_id, "basis": BASIS,
            "data_label": L.DATA_LABEL, "labels": dict(L.LABELS),
            "real_money_submission": "DISABLED", "read_only": True}


async def has_schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_ledger') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def gather_core(conn, account_id: str, now: float) -> dict:
    """THE SHARED READ every report builds on: the ledger balances and every
    position (open with marks, and closed), the account's reporting zone,
    funding, per-day ledger cash, per-day marked equity snapshots, the sale
    fills (for realization dates), daily fees and the entry decision of each
    group. Reads only."""
    L = _L()
    acct = await conn.fetchrow(
        "SELECT account_id, starting_cash_usd, reporting_tz, created_at "
        "  FROM paper_accounts WHERE account_id = $1", account_id)
    if acct is None:
        raise RuntimeError(L.R_NO_ACCOUNT)
    tzname = acct["reporting_tz"] or DEFAULT_TZ
    bal = await L.balances(conn, account_id, now=now)
    if not bal.get("ok"):
        raise RuntimeError(bal.get("refusal") or "BALANCES_REFUSED")
    allpos = await L.positions(conn, account_id, include_closed=True)
    funding = [dict(seq=r["seq"], kind=r["kind"],
                    amount_usd=float(r["cash_delta_usd"]),
                    at=_epoch(r["committed_at"]))
               for r in await conn.fetch(
                   "SELECT seq, kind, cash_delta_usd, committed_at "
                   "  FROM paper_ledger WHERE account_id = $1 "
                   "   AND kind = 'INITIAL_FUNDING' ORDER BY seq",
                   account_id)]
    cash_days = await conn.fetch(
        "SELECT DISTINCT ON (d) (committed_at AT TIME ZONE $2)::date AS d, "
        "       cash_after_usd, reserved_after_usd "
        "  FROM paper_ledger WHERE account_id = $1 "
        " ORDER BY d, seq DESC", account_id, tzname)
    snap_days, snap_hours, dd_sql = [], [], None
    if await conn.fetchval(
            "SELECT to_regclass('paper_equity_snapshots') IS NOT NULL"):
        snap_days = await conn.fetch(
            "SELECT DISTINCT ON (d) (at AT TIME ZONE $2)::date AS d, at, "
            "       equity_usd, unrealized_pnl_usd, realized_pnl_usd "
            "  FROM paper_equity_snapshots WHERE account_id = $1 "
            "   AND equity_usd IS NOT NULL ORDER BY d, at DESC",
            account_id, tzname)
        snap_hours = await conn.fetch(
            "SELECT DISTINCT ON (h) date_trunc('hour', at) AS h, at, "
            "       equity_usd FROM paper_equity_snapshots "
            " WHERE account_id = $1 AND equity_usd IS NOT NULL "
            "   AND at >= $2 ORDER BY h, at DESC", account_id,
            L._ts(now - 8 * 86400))
        # bettor_paper_readmodel.drawdown's definition (starting cash is the
        # first peak; incomplete snapshots skipped and counted), in SQL so a
        # long history never loops on the event loop
        dd_sql = await conn.fetchrow(
            "WITH s AS (SELECT at, equity_usd, greatest($2::numeric, "
            "       max(equity_usd) OVER (ORDER BY at)) AS peak "
            "  FROM paper_equity_snapshots WHERE account_id = $1 "
            "   AND equity_usd IS NOT NULL) "
            "SELECT count(*) AS n, max(peak) AS hwm, "
            "       max(peak - equity_usd) AS max_dd, "
            "       max((peak - equity_usd) / nullif(peak, 0)) AS max_dd_pct, "
            "       (SELECT count(*) FROM paper_equity_snapshots "
            "         WHERE account_id = $1 AND equity_usd IS NULL) "
            "         AS skipped FROM s", account_id,
            acct["starting_cash_usd"])
    fills = await conn.fetch(
        "SELECT fill_id, order_id, group_id, us_market_slug, holding_side, "
        "       direction, role, strategy, qty, gross_usd, fee_usd, "
        "       extract(epoch FROM filled_at)::float8 AS filled_at "
        "  FROM paper_fills WHERE account_id = $1", account_id)
    entries = await conn.fetch(
        "SELECT DISTINCT ON (o.group_id) o.group_id, o.decision_id, "
        "       o.strategy, o.intent, d.policy_version, d.verdict "
        "  FROM paper_orders o LEFT JOIN paper_decisions d "
        "    ON d.decision_id = o.decision_id "
        " WHERE o.account_id = $1 AND o.role = 'ENTRY' "
        " ORDER BY o.group_id, o.created_at", account_id)
    return {"account": {"account_id": account_id,
                        "starting_cash_usd": float(acct["starting_cash_usd"]),
                        "reporting_tz": tzname,
                        "created_at": _epoch(acct["created_at"])},
            "balances": bal, "positions": allpos, "funding": funding,
            "cash_days": [(r["d"], float(r["cash_after_usd"]))
                          for r in cash_days],
            "snap_days": [(r["d"], {"at": _epoch(r["at"]),
                                    "equity_usd": L.f(r["equity_usd"]),
                                    "unrealized_pnl_usd": L.f(
                                        r["unrealized_pnl_usd"]),
                                    "realized_pnl_usd": L.f(
                                        r["realized_pnl_usd"])})
                          for r in snap_days],
            "snap_hours": [{"at": _epoch(r["at"]),
                            "nav_usd": L.f(r["equity_usd"])}
                           for r in snap_hours],
            "snapshot_drawdown": (None if dd_sql is None or not dd_sql["n"]
                                  else {"snapshots": int(dd_sql["n"]),
                                        "high_water_mark_usd": L.f(
                                            dd_sql["hwm"]),
                                        "max_drawdown_usd": L.f(
                                            dd_sql["max_dd"]),
                                        "max_drawdown_pct": (
                                            None if dd_sql["max_dd_pct"]
                                            is None else round(float(
                                                dd_sql["max_dd_pct"]) * 100,
                                                6)),
                                        "skipped_incomplete_marks": int(
                                            dd_sql["skipped"] or 0)}),
            "fills": [dict(r) for r in fills],
            "entries": {r["group_id"]: dict(r) for r in entries}}


# ── a short-lived cache so the page's parallel reads share ONE core read ──
_CACHE: dict = {}
_LOCKS: dict = {}
CACHE_TTL_S = 15.0


async def core(conn, account_id: str, now: float | None = None,
               *, fresh: bool = False) -> tuple:
    """(core, now): the shared read, coalesced across concurrent requests
    and reused for CACHE_TTL_S seconds."""
    at = float(now if now is not None else time.time())
    if now is not None:
        return await gather_core(conn, account_id, at), at
    lock = _LOCKS.setdefault(account_id, asyncio.Lock())
    async with lock:
        hit = _CACHE.get(account_id)
        if hit and not fresh and time.time() - hit[1] < CACHE_TTL_S:
            return hit[0], hit[1]
        got = await gather_core(conn, account_id, at)
        _CACHE[account_id] = (got, at)
        return got, at


async def decorate(conn, rows: list, slug_key: str = "us_market_slug"):
    """Both teams of each row's event (team_logos), read-only; never raises."""
    try:
        from . import team_logos as TL
        await TL.decorate(conn, rows, slug_key=slug_key)
    except Exception:                                           # noqa: BLE001
        pass
    return rows


# ═════════════════════════════════════════════════════════════════════
# DERIVED VIEWS OVER THE CORE READ (pure)
# ═════════════════════════════════════════════════════════════════════

def enrich_positions(c: dict, meta: dict) -> tuple:
    """(all positions, open positions) with book, entry decision, marks,
    market name, league and sport. Open positions carry balances()' marks."""
    marks = {p["position_key"]: p for p in
             (c["balances"].get("open_positions") or [])}
    out_all, out_open = [], []
    for p in c["positions"]:
        q = dict(p)
        m = marks.get(p["position_key"])
        if m is not None:
            q.update(mark=m.get("mark"),
                     marked_value_usd=m.get("marked_value_usd"),
                     unrealized_pnl_usd=m.get("unrealized_pnl_usd"))
        elif not is_closed(p):
            q.update(mark={"status": "UNAVAILABLE", "price": None},
                     marked_value_usd=None, unrealized_pnl_usd=None)
        e = c["entries"].get(p["group_id"]) or {}
        q["book"] = book_of(p.get("strategy"), meta)
        q["decision_id"] = e.get("decision_id")
        q["policy_version"] = e.get("policy_version")
        q["intent"] = e.get("intent")
        if p.get("matchup") is None and c.get("matchups"):
            mu = c["matchups"].get(str(p.get("us_market_slug") or "").lower())
            if mu:
                q["matchup"] = mu
        q["market"] = market_label(q)
        q["league"] = league_of(q)
        q["sport"] = sport_of(q["league"])
        q["event"] = event_of(q)
        out_all.append(q)
        if not is_closed(p):
            out_open.append(q)
    return out_all, out_open


def daily_rows(c: dict, now: float) -> list:
    L = _L()
    tz = _zone(c["account"]["reporting_tz"])
    bal = c["balances"]
    fund = c["funding"]
    ats = [f["at"] for f in fund if f["at"] is not None]
    first = min(ats) if ats else (c["account"].get("created_at") or now)
    today = local_day(now, tz)
    start = min(local_day(first, tz), today)
    days = []
    d = start
    while d <= today:
        days.append(d)
        d += _dt.timedelta(days=1)
    if len(days) > 3660:                      # ten years: a guard, not a cap
        days = days[-3660:]
    pk = L.position_key
    acct = c["account"]["account_id"]
    events = []
    for f in c["fills"]:
        k = pk(account_id=acct, group_id=f["group_id"],
               slug=f["us_market_slug"], holding_side=f["holding_side"])
        q = float(f["qty"])
        events.append((f["filled_at"], k, q if f["direction"] == "BUY"
                       else -q))
    for p in c["positions"]:
        st = p.get("settlement")
        if st and st.get("settled_at") is not None:
            events.append((st["settled_at"], p["position_key"],
                           -float(p.get("settled_qty") or 0.0)))
    open_eod = open_count_at_day_ends(events, days, tz)
    sales = [dict(f, position_key=pk(account_id=acct, group_id=f["group_id"],
                                     slug=f["us_market_slug"],
                                     holding_side=f["holding_side"]))
             for f in c["fills"] if f["direction"] == "SELL"]
    realized_by_day: dict = {}
    for (at, _k, _s, pnl, _kind, _role) in realized_events(c["positions"],
                                                           sales):
        dd = local_day(at, tz)
        realized_by_day[dd] = realized_by_day.get(dd, 0.0) + pnl
    fees_by_day: dict = {}
    for f in c["fills"]:
        dd = local_day(f["filled_at"], tz)
        fees_by_day[dd] = fees_by_day.get(dd, 0.0) + float(f["fee_usd"])
    flows: dict = {}
    for f in fund:
        if f["at"] is not None:
            # funding outside the reported range books on its nearest day,
            # so no funding is ever reported as profit
            dd = min(max(local_day(f["at"], tz), days[0]), days[-1])
            flows[dd] = flows.get(dd, 0.0) + f["amount_usd"]
    live = {"nav_usd": bal.get("total_equity_usd"),
            "unrealized_usd": bal.get("unrealized_pnl_usd"),
            "reason": (None if bal.get("total_equity_usd") is not None
                       else "LIVE_MARKS_INCOMPLETE: " + str(
                           bal.get("equity_basis")))}
    return build_daily(days=days, tz=tz, cash_eod=dict(c["cash_days"]),
                       open_eod=open_eod, snaps=dict(c["snap_days"]),
                       realized_by_day=realized_by_day,
                       fees_by_day=fees_by_day, flows_by_day=flows,
                       live=live,
                       starting_capital=c["account"]["starting_cash_usd"])


def build_summary(c: dict, rows: list, now: float, meta: dict) -> dict:
    bal = c["balances"]
    tz = _zone(c["account"]["reporting_tz"])
    today = local_day(now, tz)
    _all, opn = enrich_positions(c, meta)
    nav = compute_nav(bal.get("cash_usd"), opn)
    deposits = round(sum(f["amount_usd"] for f in c["funding"]), 6)
    nav_v = nav["nav_usd"]
    dd_daily = drawdown([r["nav_usd"] for r in rows],
                        initial_peak=c["account"]["starting_cash_usd"])
    gx = gross_net_exposure(opn)
    cost = round(sum(num(p.get("cost_basis_usd")) or 0.0 for p in opn), 6)
    periods = {}
    for key in ("1D", "WTD", "MTD", "YTD", "ALL"):
        periods[key] = period_pnl(rows, period_start(today, key))
    by_strategy_rows = _pnl_by_strategy(c, opn)
    books = books_from_strategies(attribution_by_strategy(by_strategy_rows,
                                                          meta))
    return {
        "nav": {"nav_usd": nav_v, "status": "OK" if nav_v is not None
                else "UNAVAILABLE", "reason": nav["reason"],
                "nav_marked_only_usd": nav["marked_only_usd"],
                "unmarked_positions": nav["unmarked"],
                "formula": NAV_FORMULA,
                "agrees_with_ledger_balances": (
                    nav_v == bal.get("total_equity_usd")
                    or (nav_v is not None and bal.get("total_equity_usd")
                        is not None and abs(nav_v - bal["total_equity_usd"])
                        < 1e-6))},
        "capital": {"starting_capital_usd": c["account"]["starting_cash_usd"],
                    "net_deposits_usd": deposits,
                    "funding_history": [dict(f, at_iso=iso(f["at"]))
                                        for f in c["funding"]],
                    "note": ("the paper account is funded once with "
                             "fictional capital; there are no deposits or "
                             "withdrawals after it")},
        "pnl": {"realized_pnl_usd": bal.get("realized_pnl_usd"),
                "unrealized_pnl_usd": bal.get("unrealized_pnl_usd"),
                "unrealized_marked_only_usd": bal.get(
                    "unrealized_pnl_marked_only_usd"),
                "fees_paid_usd": bal.get("fees_paid_usd"),
                "fees_note": ("fees are included in realized and unrealized "
                              "P&L (cost basis is incl. fees)"),
                "total_pnl_usd": (None if nav_v is None
                                  else round(nav_v - deposits, 6)),
                "total_return_pct": (None if nav_v is None
                                     else pct(nav_v - deposits, deposits))},
        "periods": periods,
        "high_water_mark": {
            "daily_close": {k: dd_daily[k] for k in (
                "high_water_mark_usd", "current_drawdown_usd",
                "current_drawdown_pct", "max_drawdown_usd",
                "max_drawdown_pct", "points_used",
                "points_skipped_unavailable")},
            "intraday_snapshots": c.get("snapshot_drawdown"),
            "basis": ("daily close: the daily NAV series of the P&L "
                      "statement; intraday: every marked equity snapshot "
                      "(one per paper pass), as bettor_paper_readmodel."
                      "drawdown")},
        "exposure": {
            "open_positions": len(opn),
            "open_cost_basis_usd": cost,
            "max_loss_if_all_lose_usd": round(-cost, 6),
            "gross_exposure_usd": gx["gross_exposure_usd"],
            "gross_exposure_marked_only_usd": round(sum(
                num(p.get("marked_value_usd")) or 0.0 for p in opn), 6),
            "net_exposure_usd": gx["net_exposure_usd"],
            "net_formula": gx["net_formula"],
            "reserved_for_open_orders_usd": bal.get("reserved_usd"),
            "cash_usd": bal.get("cash_usd"),
            "available_cash_usd": bal.get("available_usd"),
            "cash_utilisation_pct": (None if nav_v is None else pct(
                nav_v - (num(bal.get("available_usd")) or 0.0), nav_v)),
            "utilisation_formula": UTILISATION_FORMULA,
            "unmarked_positions": nav["unmarked"],
            "stale_marks": len(bal.get("stale_marks") or [])},
        "books": books,
        "training_disclosure": TRAINING_DISCLOSURE,
        "ledger": {"last_sequence": bal.get("last_sequence"),
                   "last_updated_at": bal.get("last_updated_at"),
                   "entries": bal.get("ledger_entries"),
                   "running_balance_agrees": bal.get("ledger_consistent")},
        "mark_method": bal.get("mark_method"),
        "reporting_tz": c["account"]["reporting_tz"]}


def _pnl_by_strategy(c: dict, open_views: list) -> list:
    from . import bettor_paper_ops as OPS
    fees: dict = {}
    for f in c["fills"]:
        s = f.get("strategy") or _L().DEFAULT_STRATEGY
        fees[s] = fees.get(s, 0.0) + float(f["fee_usd"])
    return OPS.pnl_by_strategy(c["positions"], open_views, fees)


def build_pnl(rows: list, c: dict, now: float, period: str,
              start: str | None, end: str | None) -> dict:
    tz = _zone(c["account"]["reporting_tz"])
    today = local_day(now, tz)
    s = (_dt.date.fromisoformat(start) if start
         else period_start(today, period))
    e = _dt.date.fromisoformat(end) if end else None
    sel = [r for r in rows
           if (s is None or r["date"] >= s.isoformat())
           and (e is None or r["date"] <= e.isoformat())]
    hourly = c.get("snap_hours") or []
    return {"period": (period or "ALL").upper() if not start else "CUSTOM",
            "start": s.isoformat() if s else (rows[0]["date"] if rows
                                              else None),
            "end": e.isoformat() if e else today.isoformat(),
            "totals": period_pnl(rows, s, e),
            "days": list(reversed(sel)),
            "equity_curve": [{"date": r["date"], "nav_usd": r["nav_usd"],
                              "nav_basis": r["nav_basis"],
                              "high_water_mark_usd": r["high_water_mark_usd"],
                              "drawdown_usd": r["drawdown_usd"],
                              "drawdown_pct": r["drawdown_pct"]}
                             for r in sel],
            "intraday_curve": hourly + ([{
                "at": now, "nav_usd": c["balances"].get("total_equity_usd"),
                "live": True}] if c["balances"].get("total_equity_usd")
                is not None else []),
            "intraday_basis": ("the last marked equity snapshot of each hour "
                               "(8 days), then the live NAV"),
            "columns": {
                "realized_pnl_usd": "sale and settlement realizations booked "
                                    "that day (average cost incl. fees)",
                "unrealized_change_usd": "net P&L minus realized: the "
                                         "mark-to-market movement",
                "fees_usd": "fees paid that day (memo: already in P&L)",
                "net_pnl_usd": "NAV at the day's end minus the prior NAV "
                               "minus funding",
                "nav_basis": "LIVE | CASH_NO_OPEN_POSITIONS | "
                             "MARKED_SNAPSHOT | UNAVAILABLE"},
            "return_formula": RETURN_FORMULA,
            "reporting_tz": c["account"]["reporting_tz"]}


def build_performance(rows: list, c: dict, meta: dict) -> dict:
    allp, _opn = enrich_positions(c, meta)
    rets = [r["daily_return"] for r in rows]
    stats = return_stats(rets)
    deposits = sum(f["amount_usd"] for f in c["funding"])
    nav = c["balances"].get("total_equity_usd")
    books = {b: trade_stats([p for p in allp if p["book"] == b])
             for b in BOOKS}
    by_strategy = []
    for s in sorted({p.get("strategy") for p in allp}, key=str):
        ts = trade_stats([p for p in allp if p.get("strategy") == s])
        by_strategy.append(dict(ts, **strategy_info(s, meta)))
    return {
        "returns": {"daily": [{"date": r["date"],
                               "daily_return": r["daily_return"],
                               "cumulative_return": r["cumulative_return"]}
                              for r in rows],
                    "formula": RETURN_FORMULA},
        "cumulative_return": cumulative_return(rets),
        "total_return_on_deposits": (None if nav is None or not deposits
                                     else round(nav / deposits - 1.0, 10)),
        "risk_adjusted": stats,
        "positions_all_books": trade_stats(allp),
        "positions_by_book": books,
        "positions_by_strategy": by_strategy,
        "basis": ("return statistics over the account's daily NAV (one "
                  "ledger, both books); position statistics over CLOSED "
                  "positions only, each book separately. PAPER results: "
                  "simulated execution on live market data."),
        "training_disclosure": TRAINING_DISCLOSURE}


def build_positions(c: dict, meta: dict, standing: dict) -> dict:
    _allp, opn = enrich_positions(c, meta)
    nav = c["balances"].get("total_equity_usd")
    rows = []
    for p in opn:
        mk = p.get("mark") or {}
        rows.append({
            "position_key": p["position_key"], "group_id": p["group_id"],
            "us_market_slug": p["us_market_slug"], "market": p["market"],
            "matchup": p.get("matchup"), "league": p["league"],
            "sport": p["sport"], "event": p["event"],
            "side": p["holding_side"],
            "participant": (p.get("label") or {}).get("participant"),
            "quantity": p["open_qty"],
            "average_cost_usd": p["avg_cost_per_contract_incl_fees"],
            "average_cost_basis": "per contract, incl. fees",
            "cost_basis_usd": p["cost_basis_usd"],
            "mark": mk.get("price"), "mark_status": mk.get("status"),
            "mark_observed_at": mk.get("observed_at"),
            "mark_age_s": mk.get("age_s"),
            "mark_reason": mk.get("why"),
            "market_value_usd": p.get("marked_value_usd"),
            "unrealized_pnl_usd": p.get("unrealized_pnl_usd"),
            "realized_pnl_usd": p.get("realized_pnl_usd"),
            "pct_of_nav": (None if p.get("marked_value_usd") is None
                           else pct(p["marked_value_usd"], nav)),
            "strategy": p.get("strategy"), "book": p["book"],
            "strategy_title": (meta.get(p.get("strategy")) or {}).get(
                "title") or p.get("strategy"),
            "policy_version": p.get("policy_version"),
            "decision_id": p.get("decision_id"),
            "opened_at": p.get("first_fill_at"),
            "opened_at_iso": iso(p.get("first_fill_at")),
            "protection_orders": standing.get(p["group_id"], [])})
    rows.sort(key=lambda r: -(num(r["market_value_usd"]) or
                              num(r["cost_basis_usd"]) or 0.0))
    return {"positions": rows, "count": len(rows),
            "nav_usd": nav,
            "totals": {
                "cost_basis_usd": round(sum(num(r["cost_basis_usd"]) or 0.0
                                            for r in rows), 6),
                "market_value_usd": sum_or_none(r["market_value_usd"]
                                                for r in rows),
                "unrealized_pnl_usd": sum_or_none(r["unrealized_pnl_usd"]
                                                  for r in rows)},
            "by_book": {b: sum(1 for r in rows if r["book"] == b)
                        for b in BOOKS},
            "mark_method": c["balances"].get("mark_method"),
            "training_disclosure": TRAINING_DISCLOSURE}


def build_attribution(c: dict, meta: dict) -> dict:
    allp, opn = enrich_positions(c, meta)
    strat = attribution_by_strategy(_pnl_by_strategy(c, opn), meta)
    acct = c["account"]["account_id"]
    pk = _L().position_key
    sales = [dict(f, position_key=pk(account_id=acct, group_id=f["group_id"],
                                     slug=f["us_market_slug"],
                                     holding_side=f["holding_side"]))
             for f in c["fills"] if f["direction"] == "SELL"]
    events = realized_events(c["positions"], sales)
    return {
        "by_strategy": strat,
        "by_book": books_from_strategies(strat),
        "by_league": group_pnl(allp, lambda p: p.get("league"),
                               label="league"),
        "by_sport": group_pnl(allp, lambda p: p.get("sport"), label="sport"),
        "by_decision_type": by_decision_type(events, opn, meta),
        "by_policy_version": group_pnl(
            allp, lambda p: p.get("policy_version") or (
                "NOT_RECORDED:" + str(p.get("strategy"))),
            label="policy_version"),
        "training_disclosure": TRAINING_DISCLOSURE,
        "basis": ("each strategy's P&L from its own fills and settlements on "
                  "the ONE ledger (bettor_paper_ops.pnl_by_strategy); "
                  "INVESTMENT and TRAINING are separate books and are never "
                  "netted; leagues from the venue's team rows, else the "
                  "decision label (UNIDENTIFIED otherwise)")}


def build_risk(c: dict, meta: dict, orders: dict) -> dict:
    _allp, opn = enrich_positions(c, meta)
    bal = c["balances"]
    nav = bal.get("total_equity_usd")
    cost = round(sum(num(p.get("cost_basis_usd")) or 0.0 for p in opn), 6)
    return {
        "nav_usd": nav,
        "by_event": exposures(opn, nav, key_fn=lambda p: p.get("event"),
                              label="event"),
        "by_league": exposures(opn, nav, key_fn=lambda p: p.get("league"),
                               label="league"),
        "by_sport": exposures(opn, nav, key_fn=lambda p: p.get("sport"),
                              label="sport"),
        "by_book": exposures(opn, nav, key_fn=lambda p: p.get("book"),
                             label="book"),
        "concentration": concentration(opn, nav),
        "gross_net": gross_net_exposure(opn),
        "open_orders": orders,
        "cash": {"cash_usd": bal.get("cash_usd"),
                 "reserved_usd": bal.get("reserved_usd"),
                 "available_usd": bal.get("available_usd"),
                 "reserved_is": bal.get("reserved_is"),
                 "cash_utilisation_pct": (None if nav is None else pct(
                     nav - (num(bal.get("available_usd")) or 0.0), nav)),
                 "utilisation_formula": UTILISATION_FORMULA},
        "scenario": {"max_loss_if_every_open_position_loses_usd":
                     round(-cost, 6),
                     "max_gain_if_every_open_position_wins_usd": round(
                         sum(num(p.get("open_qty")) or 0.0 for p in opn)
                         - cost, 6),
                     "basis": ("each open contract pays $1 if its outcome "
                               "wins an ordinarily completed event and $0 "
                               "if it loses; less the cost basis incl. "
                               "fees")},
        "training_disclosure": TRAINING_DISCLOSURE}


# ═════════════════════════════════════════════════════════════════════
# THE REPORTS (DB + pure in a worker thread)
# ═════════════════════════════════════════════════════════════════════

async def _matchups(conn, c: dict) -> None:
    """Both teams per market slug of every position, once per core read."""
    if "matchups" in c:
        return
    try:
        from . import team_logos as TL
        c["matchups"] = await TL.matchups(conn, [p.get("us_market_slug")
                                                 for p in c["positions"]])
    except Exception:                                           # noqa: BLE001
        c["matchups"] = {}


async def _guard(fn, *args) -> dict:
    """Run a pure builder in a worker thread; a failure is UNAVAILABLE with
    the exception named, never a zero."""
    try:
        return ok(await asyncio.to_thread(fn, *args))
    except Exception as exc:                                    # noqa: BLE001
        return unavailable("%s: %s" % (type(exc).__name__, str(exc)[:200]))


async def _core_or_unavailable(conn, acct, now):
    if not await has_schema(conn):
        return None, None, "MIGRATION_171_IS_NOT_APPLIED"
    try:
        c, at = await core(conn, acct, now)
    except Exception as exc:                                    # noqa: BLE001
        return None, None, "%s: %s" % (type(exc).__name__, str(exc)[:200])
    await _matchups(conn, c)
    return c, at, None


async def summary_report(conn, *, account_id: str | None = None,
                         now: float | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["summary"] = unavailable(why)
        return out
    meta = strategy_meta()

    def build():
        rows = daily_rows(c, at)
        return build_summary(c, rows, at, meta)
    out["summary"] = await _guard(build)
    return out


async def pnl_report(conn, *, account_id: str | None = None,
                     now: float | None = None, period: str = "ALL",
                     start: str | None = None,
                     end: str | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["pnl_statement"] = unavailable(why)
        return out

    def build():
        return build_pnl(daily_rows(c, at), c, at, period, start, end)
    out["pnl_statement"] = await _guard(build)
    return out


async def performance_report(conn, *, account_id: str | None = None,
                             now: float | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["performance"] = unavailable(why)
        return out
    meta = strategy_meta()

    def build():
        return build_performance(daily_rows(c, at), c, meta)
    out["performance"] = await _guard(build)
    return out


async def standing_orders(conn, acct: str) -> dict:
    """{group_id: [open management order]} -- Xavier's standing protection
    and other open management orders on each position."""
    L = _L()
    rows = await conn.fetch(
        "SELECT order_id, group_id, role, direction, holding_side, "
        "       order_type, time_in_force, qty, filled_qty, limit_price, "
        "       state, extract(epoch FROM created_at)::float8 AS created_at, "
        "       extract(epoch FROM expires_at)::float8 AS expires_at, "
        "       strategy FROM paper_orders WHERE account_id = $1 "
        "   AND role <> 'ENTRY' AND state = ANY($2::text[]) "
        " ORDER BY created_at DESC", acct, list(L.OPEN_STATES))
    out: dict = {}
    for r in rows:
        d = {k: (float(v) if hasattr(v, "as_tuple") else v)
             for k, v in dict(r).items()}
        d["expires_at_iso"] = iso(d.get("expires_at"))
        out.setdefault(r["group_id"], []).append(d)
    return out


async def positions_report(conn, *, account_id: str | None = None,
                           now: float | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["positions"] = unavailable(why)
        return out
    try:
        standing = await standing_orders(conn, acct)
    except Exception as exc:                                    # noqa: BLE001
        standing = {}
        out["protection_orders"] = unavailable(type(exc).__name__)
    meta = strategy_meta()
    out["positions"] = await _guard(build_positions, c, meta, standing)
    return out


TRADE_COLS = (
    "f.fill_id, f.order_id, f.group_id, f.role, f.direction, "
    "f.holding_side, f.us_market_slug, f.fixture, f.label, f.qty, f.price, "
    "f.gross_usd, f.fee_usd, f.basis, f.strategy, "
    "extract(epoch FROM f.filled_at)::float8 AS filled_at, "
    "l.seq AS ledger_seq, l.kind AS ledger_kind, "
    "l.cash_delta_usd AS ledger_cash_delta_usd")
ORDER_COLS = (
    "order_id, group_id, decision_id, role, direction, holding_side, intent, "
    "us_market_slug, fixture, label, order_type, time_in_force, qty, "
    "filled_qty, limit_price, reserved_usd, reserved_remaining_usd, state, "
    "strategy, terminal_reason, "
    "extract(epoch FROM decided_at)::float8 AS decided_at, "
    "extract(epoch FROM created_at)::float8 AS created_at, "
    "extract(epoch FROM expires_at)::float8 AS expires_at, "
    "extract(epoch FROM terminal_at)::float8 AS terminal_at")


def _plain(r) -> dict:
    L = _L()
    out = {}
    for k, v in dict(r).items():
        if hasattr(v, "as_tuple"):
            out[k] = float(v)
        elif k == "label":
            out[k] = L._j(v) or {}
        else:
            out[k] = v
    return out


def trade_view(r: dict, meta: dict) -> dict:
    q = dict(r)
    q["book"] = book_of(q.get("strategy"), meta)
    q["market"] = market_label(q)
    q["filled_at_iso"] = iso(q.get("filled_at"))
    q["net_cash_usd"] = (None if q.get("ledger_cash_delta_usd") is None
                         else q["ledger_cash_delta_usd"])
    q["participant"] = (q.get("label") or {}).get("participant")
    q.pop("label", None)
    return q


def order_view(r: dict, meta: dict) -> dict:
    L = _L()
    q = dict(r)
    q["book"] = book_of(q.get("strategy"), meta)
    q["market"] = market_label(q)
    q["remaining_qty"] = round((num(q.get("qty")) or 0.0)
                               - (num(q.get("filled_qty")) or 0.0), 6)
    q["open"] = q.get("state") in L.OPEN_STATES
    for k in ("decided_at", "created_at", "expires_at", "terminal_at"):
        q[k + "_iso"] = iso(q.get(k))
    q["participant"] = (q.get("label") or {}).get("participant")
    q.pop("label", None)
    return q


async def fetch_trades(conn, acct: str, *, limit: int | None,
                       offset: int = 0) -> tuple:
    total = await conn.fetchval(
        "SELECT count(*) FROM paper_fills WHERE account_id = $1", acct)
    sql = ("SELECT %s FROM paper_fills f LEFT JOIN paper_ledger l "
           "    ON l.fill_id = f.fill_id AND l.kind IN ('FILL', 'SALE') "
           " WHERE f.account_id = $1 ORDER BY f.filled_at DESC, "
           "       l.seq DESC NULLS LAST, f.fill_id" % TRADE_COLS)
    if limit is None:
        rows = await conn.fetch(sql, acct)
    else:
        rows = await conn.fetch(sql + " LIMIT $2 OFFSET $3", acct,
                                int(limit), int(offset))
    return int(total or 0), [_plain(r) for r in rows]


async def fetch_orders(conn, acct: str, *, limit: int | None,
                       offset: int = 0) -> tuple:
    total = await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE account_id = $1", acct)
    sql = ("SELECT %s FROM paper_orders WHERE account_id = $1 "
           " ORDER BY created_at DESC, order_id" % ORDER_COLS)
    if limit is None:
        rows = await conn.fetch(sql, acct)
    else:
        rows = await conn.fetch(sql + " LIMIT $2 OFFSET $3", acct,
                                int(limit), int(offset))
    return int(total or 0), [_plain(r) for r in rows]


async def blotter_report(conn, *, account_id: str | None = None,
                         now: float | None = None, kind: str = "both",
                         limit: int = 50, offset: int = 0) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    at = float(now if now is not None else time.time())
    out = base(at, acct)
    if not await has_schema(conn):
        out["trades"] = out["orders"] = unavailable(
            "MIGRATION_171_IS_NOT_APPLIED")
        return out
    meta = strategy_meta()
    page = {"limit": int(limit), "offset": int(offset),
            "order": "newest first"}
    if kind in ("both", "trades"):
        try:
            total, rows = await fetch_trades(conn, acct, limit=limit,
                                             offset=offset)
            rows = [trade_view(r, meta) for r in rows]
            await decorate(conn, rows)
            out["trades"] = ok(rows, total=total, page=dict(
                page, has_more=offset + len(rows) < total))
        except Exception as exc:                                # noqa: BLE001
            out["trades"] = unavailable("%s: %s" % (type(exc).__name__,
                                                    str(exc)[:200]))
    if kind in ("both", "orders"):
        try:
            total, rows = await fetch_orders(conn, acct, limit=limit,
                                             offset=offset)
            rows = [order_view(r, meta) for r in rows]
            await decorate(conn, rows)
            out["orders"] = ok(rows, total=total, page=dict(
                page, has_more=offset + len(rows) < total))
        except Exception as exc:                                # noqa: BLE001
            out["orders"] = unavailable("%s: %s" % (type(exc).__name__,
                                                    str(exc)[:200]))
    out["training_disclosure"] = TRAINING_DISCLOSURE
    return out


async def attribution_report(conn, *, account_id: str | None = None,
                             now: float | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["attribution"] = unavailable(why)
        return out
    meta = strategy_meta()
    out["attribution"] = await _guard(build_attribution, c, meta)
    return out


async def open_order_commitments(conn, acct: str, meta: dict) -> dict:
    L = _L()
    rows = await conn.fetch(
        "SELECT strategy, role, direction, count(*) AS n, "
        "       sum(qty - filled_qty) AS remaining_qty, "
        "       sum(reserved_remaining_usd) AS reserved_usd, "
        "       sum((qty - filled_qty) * limit_price) AS notional_usd "
        "  FROM paper_orders WHERE account_id = $1 "
        "   AND state = ANY($2::text[]) GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        acct, list(L.OPEN_STATES))
    out = [{"strategy": r["strategy"], "book": book_of(r["strategy"], meta),
            "role": r["role"], "direction": r["direction"],
            "orders": int(r["n"]), "remaining_qty": L.f(r["remaining_qty"]),
            "reserved_usd": L.f(r["reserved_usd"]),
            "limit_notional_usd": L.f(r["notional_usd"])} for r in rows]
    return {"by_strategy_role": out,
            "open_orders": sum(r["orders"] for r in out),
            "reserved_usd": round(sum(num(r["reserved_usd"]) or 0.0
                                      for r in out), 6),
            "buy_notional_usd": round(sum(
                num(r["limit_notional_usd"]) or 0.0 for r in out
                if r["direction"] == "BUY"), 6),
            "sell_qty_committed": round(sum(
                num(r["remaining_qty"]) or 0.0 for r in out
                if r["direction"] == "SELL"), 6),
            "note": ("an order is not a fill: an open BUY reserves cash "
                     "(limit x qty + max fees) until it fills, expires or is "
                     "cancelled; an open SELL commits held contracts")}


async def risk_report(conn, *, account_id: str | None = None,
                      now: float | None = None) -> dict:
    acct = account_id or _L().ACCOUNT_ID
    c, at, why = await _core_or_unavailable(conn, acct, now)
    out = base(at or time.time(), acct)
    if c is None:
        out["risk"] = unavailable(why)
        return out
    meta = strategy_meta()
    try:
        orders = ok(await open_order_commitments(conn, acct, meta))
    except Exception as exc:                                    # noqa: BLE001
        orders = unavailable(type(exc).__name__)
    out["risk"] = await _guard(build_risk, c, meta, orders)
    return out


async def reconciliation_report(conn, *, account_id: str | None = None,
                                now: float | None = None) -> dict:
    """AUDREY'S LEDGER RECONCILIATION (agents.paper_brief.reconcile) plus
    the position/fill/settlement/reservation cross-checks. Every
    discrepancy is listed; RECONCILED only when every check passes."""
    L = _L()
    acct = account_id or L.ACCOUNT_ID
    at = float(now if now is not None else time.time())
    out = base(at, acct)
    if not await has_schema(conn):
        out["reconciliation"] = unavailable("MIGRATION_171_IS_NOT_APPLIED")
        return out
    try:
        from .agents import paper_brief as PB
        led = await PB.reconcile(conn, now=at, account_id=acct, entries=0)
        if led.get("checks") is None:
            raise RuntimeError(led.get("why") or "LEDGER_NOT_RECONCILABLE")
        fills = [dict(r) for r in await conn.fetch(
            "SELECT f.fill_id, f.order_id, f.direction, f.gross_usd, "
            "       f.fee_usd, count(l.seq) AS ledger_entries, "
            "       max(l.seq) AS ledger_seq, max(l.kind) AS ledger_kind, "
            "       sum(l.cash_delta_usd) AS ledger_cash_delta_usd "
            "  FROM paper_fills f LEFT JOIN paper_ledger l "
            "    ON l.fill_id = f.fill_id AND l.account_id = f.account_id "
            " WHERE f.account_id = $1 GROUP BY 1, 2, 3, 4, 5", acct)]
        orphan = [dict(r) for r in await conn.fetch(
            "SELECT l.seq, l.kind, l.fill_id FROM paper_ledger l "
            " WHERE l.account_id = $1 AND l.kind IN ('FILL', 'SALE') "
            "   AND NOT EXISTS (SELECT 1 FROM paper_fills f "
            "                    WHERE f.fill_id = l.fill_id)", acct)]
        orders = [dict(r) for r in await conn.fetch(
            "SELECT o.order_id, o.filled_qty, coalesce(sum(f.qty), 0) "
            "       AS fills_qty FROM paper_orders o LEFT JOIN paper_fills f "
            "    ON f.order_id = o.order_id WHERE o.account_id = $1 "
            " GROUP BY 1, 2 HAVING o.filled_qty <> coalesce(sum(f.qty), 0)",
            acct)]
        settle_ledger = {r["position_key"]: float(r["cash"]) for r in
                         await conn.fetch(
                             "SELECT position_key, sum(cash_delta_usd) AS "
                             "       cash FROM paper_ledger "
                             " WHERE account_id = $1 AND kind IN "
                             "       ('SETTLEMENT', 'CORRECTION') "
                             "   AND position_key IS NOT NULL GROUP BY 1",
                             acct)}
        open_reserved = await conn.fetchval(
            "SELECT coalesce(sum(reserved_remaining_usd), 0) "
            "  FROM paper_orders WHERE account_id = $1 "
            "   AND state = ANY($2::text[])", acct, list(L.OPEN_STATES))
        positions = await L.positions(conn, acct, include_closed=True)
        audrey = None
        if await conn.fetchval(
                "SELECT to_regclass('paper_audrey_reports') IS NOT NULL"):
            r = await conn.fetchrow(
                "SELECT report_id, report_day, version, reconciles, final, "
                "       extract(epoch FROM generated_at)::float8 AS "
                "       generated_at FROM paper_audrey_reports "
                " WHERE account_id = $1 ORDER BY report_day DESC, "
                "       version DESC LIMIT 1", acct)
            crit = await conn.fetch(
                "SELECT severity, count(*) AS n FROM paper_audrey_findings "
                " WHERE account_id = $1 AND found_at >= $2 GROUP BY 1",
                acct, L._ts(at - 7 * 86400))
            audrey = {"latest_daily_report": None if r is None else {
                "report_id": r["report_id"],
                "report_day": r["report_day"].isoformat(),
                "version": r["version"], "reconciles": r["reconciles"],
                "final": r["final"], "generated_at": r["generated_at"]},
                "findings_7d_by_severity": {x["severity"]: int(x["n"])
                                            for x in crit}}
    except Exception as exc:                                    # noqa: BLE001
        out["reconciliation"] = unavailable("%s: %s" % (type(exc).__name__,
                                                        str(exc)[:200]))
        return out

    def build():
        disc = []
        for chk in led["checks"]:
            if not chk.get("ok"):
                disc.append(dict(chk, source="agents.paper_brief.reconcile"))
        disc += fill_ledger_discrepancies(fills)
        disc += [{"check": "LEDGER_FILL_ENTRY_HAS_A_FILL_RECORD",
                  "ledger_seq": o["seq"], "ledger_kind": o["kind"],
                  "fill_id": o["fill_id"]} for o in orphan]
        disc += order_fill_discrepancies(orders)
        disc += settlement_discrepancies(positions, settle_ledger)
        disc += position_discrepancies(positions)
        disc += reservation_discrepancy(
            open_reserved, (led.get("balances") or {}).get("reserved_usd"))
        checks = [
            {"check": c["check"], "ok": bool(c.get("ok")),
             "source": "Audrey's ledger reconciliation "
                       "(agents.paper_brief.reconcile)", "detail": c}
            for c in led["checks"]]
        names = {
            "ONE_LEDGER_ENTRY_PER_FILL": "every fill has one ledger entry",
            "LEDGER_KIND_MATCHES_FILL_DIRECTION": "BUY fills are FILL "
                                                  "entries, SELL fills SALE",
            "LEDGER_CASH_EQUALS_FILL_CASH": "ledger cash equals fill cash "
                                            "(gross ± fee)",
            "LEDGER_FILL_ENTRY_HAS_A_FILL_RECORD": "every FILL/SALE entry "
                                                   "has a fill record",
            "ORDER_FILLED_QTY_EQUALS_SUM_OF_FILLS": "order filled qty equals "
                                                    "its fills",
            "SETTLEMENT_PAYOUT_EQUALS_LEDGER_CREDIT": "settlement payouts "
                                                      "equal ledger credits",
            "OPEN_QUANTITY_IS_NOT_NEGATIVE": "positions vs fills: open qty "
                                             "never negative",
            "OPEN_ORDER_RESERVATIONS_EQUAL_LEDGER_RESERVED": "open order "
                                                             "reservations "
                                                             "equal ledger "
                                                             "reserved"}
        for k, words in names.items():
            n = sum(1 for d in disc if d.get("check") == k)
            checks.append({"check": k, "ok": n == 0, "discrepancies": n,
                           "description": words,
                           "source": "paper_institutional cross-check"})
        b = led.get("balances") or {}
        return {"status": "RECONCILED" if not disc else "DISCREPANCIES",
                "reconciled": not disc, "discrepancy_count": len(disc),
                "discrepancies": disc[:500],
                "discrepancies_truncated": len(disc) > 500,
                "checks": checks,
                "ledger": {"entries": led.get("entries_count"),
                           "first_seq": led.get("first_seq"),
                           "last_seq": led.get("last_seq"),
                           "by_kind": led.get("by_kind"),
                           "ledger_cash_usd": b.get("cash_usd"),
                           "computed_cash_usd": next(
                               (c.get("ledger_sum_usd") for c in led["checks"]
                                if c["check"] ==
                                "CASH_EQUALS_SUM_OF_CASH_DELTAS"), None),
                           "reserved_usd": b.get("reserved_usd"),
                           "open_orders_reserved_usd": L.f(open_reserved)},
                "counts": {"fills": len(fills), "positions": len(positions),
                           "settled_positions": sum(
                               1 for p in positions if p.get("settlement"))},
                "audrey": audrey}
    out["reconciliation"] = await _guard(build)
    return out


# ═════════════════════════════════════════════════════════════════════
# CSV EXPORTS
# ═════════════════════════════════════════════════════════════════════

def _mname(r):
    m = r.get("market") or {}
    teams = r.get("matchup") or []
    mu = " vs ".join(t.get("name") or "" for t in teams[:2])
    return " · ".join(x for x in (m.get("title") or mu, m.get("selection"))
                      if x) or r.get("us_market_slug")


TRADE_CSV = [
    ("filled_at_utc", lambda r: iso(r.get("filled_at"))),
    ("market", _mname), ("us_market_slug", "us_market_slug"),
    ("role", "role"), ("direction", "direction"), ("side", "holding_side"),
    ("qty", "qty"), ("price", "price"), ("gross_usd", "gross_usd"),
    ("fee_usd", "fee_usd"), ("ledger_cash_usd", "ledger_cash_delta_usd"),
    ("ledger_seq", "ledger_seq"), ("order_id", "order_id"),
    ("fill_id", "fill_id"), ("strategy", "strategy"), ("book", "book")]
ORDER_CSV = [
    ("created_at_utc", lambda r: iso(r.get("created_at"))),
    ("market", _mname), ("us_market_slug", "us_market_slug"),
    ("role", "role"), ("direction", "direction"), ("side", "holding_side"),
    ("order_type", "order_type"), ("time_in_force", "time_in_force"),
    ("expires_at_utc", lambda r: iso(r.get("expires_at"))),
    ("qty", "qty"), ("filled_qty", "filled_qty"),
    ("limit_price", "limit_price"), ("reserved_usd", "reserved_usd"),
    ("state", "state"), ("terminal_reason", "terminal_reason"),
    ("order_id", "order_id"), ("decision_id", "decision_id"),
    ("strategy", "strategy"), ("book", "book")]
POSITION_CSV = [
    ("market", _mname), ("us_market_slug", "us_market_slug"),
    ("league", "league"), ("sport", "sport"), ("side", "side"),
    ("quantity", "quantity"), ("average_cost_usd", "average_cost_usd"),
    ("cost_basis_usd", "cost_basis_usd"), ("mark", "mark"),
    ("mark_status", "mark_status"), ("market_value_usd", "market_value_usd"),
    ("unrealized_pnl_usd", "unrealized_pnl_usd"),
    ("pct_of_nav", "pct_of_nav"), ("strategy", "strategy"),
    ("book", "book"), ("policy_version", "policy_version"),
    ("opened_at_utc", "opened_at_iso"),
    ("protection_orders", lambda r: len(r.get("protection_orders") or []))]
DAILY_CSV = [
    ("date", "date"), ("nav_usd", "nav_usd"), ("nav_basis", "nav_basis"),
    ("cash_usd", "cash_usd"), ("funding_usd", "funding_usd"),
    ("realized_pnl_usd", "realized_pnl_usd"),
    ("unrealized_change_usd", "unrealized_change_usd"),
    ("fees_usd", "fees_usd"), ("net_pnl_usd", "net_pnl_usd"),
    ("daily_return", "daily_return"),
    ("cumulative_return", "cumulative_return"),
    ("high_water_mark_usd", "high_water_mark_usd"),
    ("drawdown_usd", "drawdown_usd"), ("drawdown_pct", "drawdown_pct")]
EXPORTS = ("blotter_trades", "blotter_orders", "positions", "daily_pnl")


async def export_csv(conn, name: str, *, account_id: str | None = None,
                     now: float | None = None) -> tuple:
    """(filename, csv text). Raises ValueError for an unknown export and
    RuntimeError when the data cannot be read (the route answers 503: an
    export never ships an empty file in place of unavailable data)."""
    L = _L()
    acct = account_id or L.ACCOUNT_ID
    at = float(now if now is not None else time.time())
    if name not in EXPORTS:
        raise ValueError(name)
    if not await has_schema(conn):
        raise RuntimeError("MIGRATION_171_IS_NOT_APPLIED")
    meta = strategy_meta()
    stamp = _dt.datetime.fromtimestamp(at, _dt.timezone.utc).strftime(
        "%Y%m%dT%H%MZ")
    fname = "paper_%s_%s_%s.csv" % (acct, name, stamp)
    if name == "blotter_trades":
        _n, rows = await fetch_trades(conn, acct, limit=None)
        rows = [trade_view(r, meta) for r in rows]
        await decorate(conn, rows)
        return fname, await asyncio.to_thread(
            to_csv, rows, TRADE_CSV, title="Trade blotter (every simulated "
                                           "fill), newest first", as_of=at)
    if name == "blotter_orders":
        _n, rows = await fetch_orders(conn, acct, limit=None)
        rows = [order_view(r, meta) for r in rows]
        await decorate(conn, rows)
        return fname, await asyncio.to_thread(
            to_csv, rows, ORDER_CSV, title="Order blotter, newest first",
            as_of=at)
    c, at2, why = await _core_or_unavailable(conn, acct, now)
    if c is None:
        raise RuntimeError(why)
    if name == "positions":
        standing = await standing_orders(conn, acct)

        def build():
            p = build_positions(c, meta, standing)
            return to_csv(p["positions"], POSITION_CSV,
                          title="Open positions (marks: %s)" % (
                              c["balances"].get("mark_method") or ""),
                          as_of=at2)
        return fname, await asyncio.to_thread(build)

    def build_daily_csv():
        rows = daily_rows(c, at2)
        return to_csv(list(reversed(rows)), DAILY_CSV,
                      title="Daily P&L statement (reporting zone %s)"
                      % c["account"]["reporting_tz"], as_of=at2)
    return fname, await asyncio.to_thread(build_daily_csv)


def describe() -> dict:
    return {"version": VERSION, "basis": BASIS,
            "routes": ["/api/command/paper/reports/summary",
                       "/api/command/paper/reports/pnl",
                       "/api/command/paper/reports/performance",
                       "/api/command/paper/reports/positions",
                       "/api/command/paper/reports/blotter",
                       "/api/command/paper/reports/attribution",
                       "/api/command/paper/reports/risk",
                       "/api/command/paper/reports/reconciliation",
                       "/api/command/paper/reports/export/{name}.csv"],
            "exports": list(EXPORTS),
            "training_disclosure": TRAINING_DISCLOSURE}
