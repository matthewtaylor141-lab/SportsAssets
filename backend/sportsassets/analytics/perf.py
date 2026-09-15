"""Per-whale performance series: daily P&L, volume, drawdown. Pure functions.

Realized P&L is attributed to the day the realization happened (sell or
resolution), matching how per-executor stats are kept on trading platforms:
a calendar day is green if realizations that day netted positive.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime


def group_daily(
    realizations: list[tuple[datetime, float]],
    trades: list[tuple[datetime, float]],
) -> list[dict]:
    """Collapse realization events + trade notionals into per-day rows.

    Returns [{date, pnl, volume, trades}] sorted ascending; only days with
    activity appear (the calendar renders quiet days as empty).
    """
    days: dict[date, dict] = defaultdict(lambda: {"pnl": 0.0, "volume": 0.0, "trades": 0})
    for ts, amount in realizations:
        days[ts.date()]["pnl"] += amount
    for ts, notional in trades:
        d = days[ts.date()]
        d["volume"] += notional
        d["trades"] += 1
    return [
        {"date": d.isoformat(), "pnl": round(v["pnl"], 2), "volume": round(v["volume"], 2),
         "trades": v["trades"]}
        for d, v in sorted(days.items())
    ]


def max_drawdown(realizations: list[tuple[datetime, float]]) -> dict:
    """Max drawdown of the cumulative realized P&L curve.

    Returns {max_drawdown (>=0), peak, trough, peak_ts, trough_ts}.
    """
    cum = 0.0
    peak = 0.0
    peak_ts: datetime | None = None
    worst = {"max_drawdown": 0.0, "peak": 0.0, "trough": 0.0, "peak_ts": None, "trough_ts": None}
    for ts, amount in sorted(realizations, key=lambda e: e[0]):
        cum += amount
        if cum > peak:
            peak = cum
            peak_ts = ts
        dd = peak - cum
        if dd > worst["max_drawdown"]:
            worst = {
                "max_drawdown": round(dd, 2),
                "peak": round(peak, 2),
                "trough": round(cum, 2),
                "peak_ts": peak_ts.isoformat() if peak_ts else None,
                "trough_ts": ts.isoformat(),
            }
    return worst


def summarize(
    realizations: list[tuple[datetime, float]],
    trades: list[tuple[datetime, float]],
    buy_notional: float,
) -> dict:
    """Headline per-whale summary: realized P&L, volume, % earned, drawdown."""
    realized = sum(a for _, a in realizations)
    volume = sum(n for _, n in trades)
    dd = max_drawdown(realizations)
    return {
        "realized_pnl": round(realized, 2),
        "volume_traded": round(volume, 2),
        "buy_notional": round(buy_notional, 2),
        "pct_earned": round(realized / buy_notional, 6) if buy_notional > 0 else None,
        "max_drawdown": dd["max_drawdown"],
        "trade_count": len(trades),
    }
