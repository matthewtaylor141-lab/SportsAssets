"""THE FIVE NORTH-STAR METRICS (RESEARCH). Pure; no I/O.

Each metric, per book (PAPER and ACTUAL separately, never summed), carries:
value, sample_n, ci_low / ci_high (90% percentile bootstrap where a CI is
meaningful), period, status, why, trend (against the last observation at
least TREND_MIN_AGE_H old) and data freshness (the newest source event it
saw, and its age).

STATUS
  MEASURED             value from >= MIN_POSITIONS closed positions (and,
                       for metric 3, >= MIN_DAYS days of history)
  INSUFFICIENT_SAMPLE  a value exists but the sample is below that floor --
                       shown, never trusted
  UNAVAILABLE          nothing to measure (no closed position in the period)
  UNPROVEN             data exists but the claim cannot be established (no
                       prediction recorded to calibrate against; a
                       non-positive predicted total to divide by)

DEFINITIONS (closed positions, released inside the period, from
pos_position_economics):

 1 REALIZED_NET_EDGE   sum(net profit) / sum(capital committed): realized
                       net return per dollar put at risk. CI: bootstrap over
                       positions.
 2 PROFIT_PER_CAPITAL_HOUR  sum(net profit) / sum(capital-hours), USD per
                       USD-hour. CI: bootstrap over positions.
 3 PROB_POSITIVE_ROLLING_30D_PNL  P(sum of 30 days' realized net P&L > 0)
                       under a circular block bootstrap (block BLOCK_DAYS)
                       of the daily realized P&L series over the whole
                       lookback (a day with no closure is a measured 0 once
                       the book has history). CI: outer bootstrap of the
                       daily series. Also the empirical share of positive
                       rolling 30-day windows when the history is that long.
 4 MAX_DRAWDOWN        largest peak-to-trough fall (USD, >= 0) of cumulative
                       realized net P&L ordered by release time. A path
                       statistic: no CI (named).
 5 EDGE_CALIBRATION    realization ratio sum(realized net profit) /
                       sum(expected net profit at entry) over positions that
                       carry both (1.0 = predicted edge fully realized; < 1
                       = the model over-predicts). Also the OLS slope of
                       realized on predicted edge per dollar and a quintile
                       reliability table. CI: bootstrap of the ratio.
"""
from __future__ import annotations

import random

from . import common as C

VERSION = "POS_METRICS_V1"
METRICS = ("REALIZED_NET_EDGE", "PROFIT_PER_CAPITAL_HOUR",
           "PROB_POSITIVE_ROLLING_30D_PNL", "MAX_DRAWDOWN",
           "EDGE_CALIBRATION")
HIGHER_IS_BETTER = {"REALIZED_NET_EDGE": True, "PROFIT_PER_CAPITAL_HOUR": True,
                    "PROB_POSITIVE_ROLLING_30D_PNL": True,
                    "MAX_DRAWDOWN": False, "EDGE_CALIBRATION": None}
UNITS = {"REALIZED_NET_EDGE": "USD net profit per USD committed",
         "PROFIT_PER_CAPITAL_HOUR": "USD net profit per USD-hour",
         "PROB_POSITIVE_ROLLING_30D_PNL": "probability",
         "MAX_DRAWDOWN": "USD",
         "EDGE_CALIBRATION": "realized / predicted net profit (ratio)"}
MIN_POSITIONS = 30
MIN_DAYS = 30
PERIOD_DAYS = 30.0
HORIZON_DAYS = 30
BLOCK_DAYS = 5
RESAMPLES = 1000
OUTER = 60
INNER = 200
TREND_MIN_AGE_H = 20.0
DAY = 86400.0

R_NONE_CLOSED = "NO_POSITION_CLOSED_IN_THE_PERIOD"
R_NO_PRED = "NO_CLOSED_POSITION_CARRIES_AN_EXPECTED_NET_PROFIT"
R_NONPOS_PRED = "PREDICTED_NET_PROFIT_TOTAL_NOT_POSITIVE"


def _status(n, *, extra_ok=True):
    return C.MEASURED if (n >= MIN_POSITIONS and extra_ok) else C.INSUFFICIENT


def _metric(name, *, value, n, status, why=None, ci=(None, None),
            ci_why=None, period=None, detail=None):
    out = {"metric": name, "value": C.rnd(value, 9), "sample_n": int(n),
           "ci_low": C.rnd(ci[0], 9), "ci_high": C.rnd(ci[1], 9),
           "ci_level": 0.90 if ci[0] is not None else None,
           "ci_why": None if ci[0] is not None else (
               ci_why or ("NO_VALUE" if value is None else
                          "FEWER_THAN_TWO_OBSERVATIONS")),
           "status": status, "why": why, "period": period,
           "unit": UNITS[name], "higher_is_better": HIGHER_IS_BETTER[name],
           "detail": detail or {}}
    if value is None and why is None:
        out["why"] = "NOT_MEASURED"
    return out


def daily_series(closed: list, *, now: float, lookback_start: float) -> list:
    """[(day_index, realized net P&L)] from the first release day in the
    lookback to the last COMPLETE day before `now`; days without a closure
    are measured zeros once history has begun."""
    rows = [(e["released_at"], e["net_profit_usd"]) for e in closed
            if e.get("released_at") is not None
            and e.get("net_profit_usd") is not None
            and lookback_start <= e["released_at"] < now]
    if not rows:
        return []
    last_complete = int(now // DAY) - 1
    first = int(min(t for t, _ in rows) // DAY)
    if last_complete < first:
        return []
    by = {d: 0.0 for d in range(first, last_complete + 1)}
    for t, v in rows:
        d = int(t // DAY)
        if d in by:
            by[d] += v
    return sorted(by.items())


def block_paths(values: list, *, rng, paths: int, horizon=HORIZON_DAYS,
                block=BLOCK_DAYS) -> list:
    """Circular block bootstrap: `paths` resampled horizons of daily values."""
    n = len(values)
    out = []
    for _ in range(paths):
        path = []
        while len(path) < horizon:
            s = rng.randrange(n)
            for j in range(block):
                path.append(values[(s + j) % n])
                if len(path) >= horizon:
                    break
        out.append(path)
    return out


def _prob_positive(values, rng, paths):
    ps = block_paths(values, rng=rng, paths=paths)
    return sum(1 for p in ps if sum(p) > 0) / float(len(ps))


def compute(econs: list, *, book: str, now: float, lookback_days: float,
            period_days=PERIOD_DAYS) -> list:
    """The five metrics for ONE book."""
    rows = [e for e in econs if e.get("book") == book]
    closed = [e for e in rows if e.get("state") == "CLOSED"
              and e.get("net_profit_usd") is not None]
    lo = now - period_days * DAY
    lb = now - lookback_days * DAY
    period = {"kind": "TRAILING_DAYS", "days": period_days, "start": lo,
              "end": now}
    inp = [e for e in closed if (e.get("released_at") or 0) >= lo]
    seed = C.seed_of([book, sorted(e["position_key"] for e in inp),
                      [e.get("net_profit_usd") for e in inp]])
    out = []
    # 1 · realized net edge
    pairs = [(e["net_profit_usd"], e["capital_committed_usd"]) for e in inp
             if (e.get("capital_committed_usd") or 0) > 0]
    if not pairs:
        out.append(_metric("REALIZED_NET_EDGE", value=None, n=0,
                           status=C.UNAVAILABLE, why=R_NONE_CLOSED,
                           period=period))
    else:
        v = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
        out.append(_metric(
            "REALIZED_NET_EDGE", value=v, n=len(pairs),
            status=_status(len(pairs)),
            why=None if len(pairs) >= MIN_POSITIONS else
            "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS,
            ci=C.bootstrap_ratio_ci(pairs, seed=seed, resamples=RESAMPLES),
            period=period, detail={
                "net_profit_usd": C.rnd(sum(a for a, _ in pairs)),
                "capital_committed_usd": C.rnd(sum(b for _, b in pairs))}))
    # 2 · profit per capital-hour
    pairs = [(e["net_profit_usd"], e["capital_hours"]) for e in inp
             if (e.get("capital_hours") or 0) > 0]
    if not pairs:
        out.append(_metric("PROFIT_PER_CAPITAL_HOUR", value=None, n=0,
                           status=C.UNAVAILABLE, why=R_NONE_CLOSED,
                           period=period))
    else:
        v = sum(a for a, _ in pairs) / sum(b for _, b in pairs)
        out.append(_metric(
            "PROFIT_PER_CAPITAL_HOUR", value=v, n=len(pairs),
            status=_status(len(pairs)),
            why=None if len(pairs) >= MIN_POSITIONS else
            "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS,
            ci=C.bootstrap_ratio_ci(pairs, seed=seed + 1,
                                    resamples=RESAMPLES),
            period=period, detail={
                "net_profit_usd": C.rnd(sum(a for a, _ in pairs)),
                "capital_hours": C.rnd(sum(b for _, b in pairs), 6)}))
    # 3 · probability of a positive rolling 30-day P&L (whole lookback)
    series = daily_series(closed, now=now, lookback_start=lb)
    vals = [v for _, v in series]
    n_pos = sum(1 for e in closed if (e.get("released_at") or 0) >= lb)
    lperiod = {"kind": "LOOKBACK_DAILY_SERIES", "days": lookback_days,
               "start": lb, "end": now, "series_days": len(vals)}
    if not vals:
        out.append(_metric("PROB_POSITIVE_ROLLING_30D_PNL", value=None, n=0,
                           status=C.UNAVAILABLE,
                           why="NO_COMPLETE_DAY_OF_REALIZED_PNL",
                           period=lperiod))
    else:
        rng = random.Random(seed + 2)
        v = _prob_positive(vals, rng, RESAMPLES)
        outer = []
        if len(vals) >= 2:
            for _ in range(OUTER):
                rs = [vals[rng.randrange(len(vals))] for _ in vals]
                outer.append(_prob_positive(rs, rng, INNER))
            outer.sort()
        emp = None
        if len(vals) >= HORIZON_DAYS:
            wins = [sum(vals[i:i + HORIZON_DAYS])
                    for i in range(len(vals) - HORIZON_DAYS + 1)]
            emp = sum(1 for w in wins if w > 0) / float(len(wins))
        ok = len(vals) >= MIN_DAYS
        out.append(_metric(
            "PROB_POSITIVE_ROLLING_30D_PNL", value=v, n=n_pos,
            status=_status(n_pos, extra_ok=ok),
            why=None if (ok and n_pos >= MIN_POSITIONS) else
            "BELOW_%d_DAYS_OR_%d_CLOSED_POSITIONS" % (MIN_DAYS,
                                                      MIN_POSITIONS),
            ci=((C.quantile(outer, 0.05), C.quantile(outer, 0.95))
                if outer else (None, None)),
            period=lperiod, detail={
                "series_days": len(vals), "block_days": BLOCK_DAYS,
                "resamples": RESAMPLES,
                "empirical_positive_window_share": C.rnd(emp, 6),
                "empirical_why": None if emp is not None else
                "HISTORY_SHORTER_THAN_30_DAYS"}))
    # 4 · maximum drawdown of the realized path (period)
    path = [e["net_profit_usd"] for e in sorted(
        inp, key=lambda e: e["released_at"])]
    if not path:
        out.append(_metric("MAX_DRAWDOWN", value=None, n=0,
                           status=C.UNAVAILABLE, why=R_NONE_CLOSED,
                           period=period))
    else:
        cum = peak = 0.0
        for x in path:
            cum += x
            peak = max(peak, cum)
        out.append(_metric(
            "MAX_DRAWDOWN", value=C.max_drawdown(path), n=len(path),
            status=_status(len(path)),
            why=None if len(path) >= MIN_POSITIONS else
            "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS,
            ci_why="PATH_STATISTIC_NO_CI_SEE_FORECAST_DRAWDOWN_DISTRIBUTION",
            period=period, detail={
                "current_drawdown_usd": C.rnd(peak - cum),
                "cumulative_net_profit_usd": C.rnd(cum),
                "basis": "cumulative realized net P&L by release time"}))
    # 5 · predicted-edge -> realized-edge calibration
    both = [e for e in inp if e.get("expected_net_profit_usd") is not None]
    if not inp:
        out.append(_metric("EDGE_CALIBRATION", value=None, n=0,
                           status=C.UNAVAILABLE, why=R_NONE_CLOSED,
                           period=period))
    elif not both:
        out.append(_metric("EDGE_CALIBRATION", value=None, n=0,
                           status=C.UNPROVEN, why=R_NO_PRED, period=period))
    else:
        sp = sum(e["expected_net_profit_usd"] for e in both)
        sr = sum(e["net_profit_usd"] for e in both)
        xs = [e.get("predicted_edge_per_dollar") for e in both]
        ys = [e.get("realized_edge_per_dollar") for e in both]
        xy = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
        slope = None
        if len(xy) >= 3:
            mx = sum(x for x, _ in xy) / len(xy)
            my = sum(y for _, y in xy) / len(xy)
            sxx = sum((x - mx) ** 2 for x, _ in xy)
            if sxx > 0:
                slope = sum((x - mx) * (y - my) for x, y in xy) / sxx
        bins = []
        if len(xy) >= 5:
            srt = sorted(xy)
            k = 5
            for i in range(k):
                chunk = srt[i * len(srt) // k:(i + 1) * len(srt) // k]
                if chunk:
                    bins.append({"n": len(chunk),
                                 "mean_predicted": C.rnd(C.mean(
                                     [x for x, _ in chunk]), 9),
                                 "mean_realized": C.rnd(C.mean(
                                     [y for _, y in chunk]), 9)})
        detail = {"predicted_net_profit_usd": C.rnd(sp),
                  "realized_net_profit_usd": C.rnd(sr),
                  "ols_slope_realized_on_predicted": C.rnd(slope, 9),
                  "reliability_quintiles": bins}
        if sp <= 0:
            out.append(_metric("EDGE_CALIBRATION", value=None, n=len(both),
                               status=C.UNPROVEN, why=R_NONPOS_PRED,
                               period=period, detail=detail))
        else:
            ci = C.bootstrap_ratio_ci(
                [(e["net_profit_usd"], e["expected_net_profit_usd"])
                 for e in both], seed=seed + 3, resamples=RESAMPLES)
            out.append(_metric(
                "EDGE_CALIBRATION", value=sr / sp, n=len(both),
                status=_status(len(both)),
                why=None if len(both) >= MIN_POSITIONS else
                "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS,
                ci=ci, period=period, detail=detail))
    # freshness: the newest source event seen for this book
    as_of = max([e.get("last_event_at") or 0 for e in rows] or [0]) or None
    for m in out:
        m["book"] = book
        m["data_as_of"] = as_of
    return out


def trend(current: dict, previous: dict | None) -> dict:
    """Direction against the last observation >= TREND_MIN_AGE_H old."""
    if previous is None:
        return {"direction": "UNAVAILABLE", "why": "NO_EARLIER_OBSERVATION"}
    a, b = current.get("value"), previous.get("value")
    if a is None or b is None:
        return {"direction": "UNAVAILABLE",
                "why": "VALUE_MISSING_NOW_OR_THEN", "previous_value": b,
                "previous_at": previous.get("computed_at")}
    d = a - b
    tol = max(1e-12, abs(b) * 0.01)
    direction = "FLAT" if abs(d) <= tol else ("UP" if d > 0 else "DOWN")
    hib = HIGHER_IS_BETTER.get(current["metric"])
    if current["metric"] == "EDGE_CALIBRATION":
        improving = abs(a - 1.0) < abs(b - 1.0) if direction != "FLAT" \
            else None
    else:
        improving = None if (hib is None or direction == "FLAT") else (
            (d > 0) == hib)
    return {"direction": direction, "delta": C.rnd(d, 9),
            "previous_value": b, "previous_at": previous.get("computed_at"),
            "improving": improving, "why": None}
