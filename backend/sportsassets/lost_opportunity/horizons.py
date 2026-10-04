"""HORIZON FORECASTS: THE NEXT 24 HOURS, 7 DAYS AND 30 DAYS, PER BOOK.
Pure; no I/O. RESEARCH. UNPROVEN BY CONSTRUCTION.

Extends pos-econ's monthly revenue engine (profitability/forecast.py) to the
three horizons management reads, with the same method and the same
fail-closed rules, and adds the opportunity and capital lines:

  expected net P&L, P10/P50/P90, P(positive)   circular block bootstrap of
        the book's daily realized net P&L series (metrics.daily_series /
        block_paths), paths of the horizon's length; P(positive) on the 30D
        row is P(positive month)
  expected max drawdown                        mean over paths of the path's
                                               peak-to-trough
  expected opportunities                       recorded candidates per day
        (pos_capacity, the last 14 days) x horizon days      (PAPER only)
  expected qualified opportunities             MEASURED candidates with a
        positive executable opportunity per day x days       (PAPER only)
  expected turnover                            capital committed per day
        by positions first filled in the lookback x days
  expected capital-hours                       capital-hours per day x days
  deployable capital                           idle capital NOW (the book's
        latest CAPITAL snapshot) -- a state, not a forecast
  capacity utilization                         expected capital-hours /
        (account capital x horizon hours)
  expected capacity                            daily executable opportunity
        x fill probability x days (an upper bound, conditional on the
        recorded books)

NO FALSE PRECISION: dollars are rounded to $1, probabilities to 0.01,
counts to 0.1 -- the bootstrap's resolution, not more.

FAIL-CLOSED: fewer than MIN_DAYS days of realized history or MIN_POSITIONS
closed positions -> UNAVAILABLE with the reason and no P&L numbers. The
opportunity lines need OPP_MIN_DAYS days of candidate history.

UNPROVEN until the horizon's own persisted forecasts, scored against what
was realized, pass pos-econ's validation (>= 12 scored, P10-P90 coverage in
band, Brier <= 0.25). Nothing reads this to size, limit or allocate.
"""
from __future__ import annotations

import random

from ..profitability import common as C
from ..profitability import forecast as FC
from ..profitability import metrics as M

VERSION = "LOL_HORIZON_FORECAST_V1"
METHOD = "CIRCULAR_BLOCK_BOOTSTRAP_DAILY_REALIZED_NET_PNL_BY_HORIZON"
HORIZONS = (("24H", 1), ("7D", 7), ("30D", 30))
RESAMPLES = 2000
MIN_DAYS = FC.MIN_DAYS
MIN_POSITIONS = FC.MIN_POSITIONS
OPP_MIN_DAYS = 3
DAY = 86400.0


def usd(v):
    return None if v is None else float(round(v))


def prob(v):
    return None if v is None else round(float(v), 2)


def cnt(v):
    return None if v is None else round(float(v), 1)


def build(econs: list, *, book: str, horizon: str, days: int, now: float,
          lookback_days: float, opportunity=None, capital=None,
          capacity_daily=None, fill_probability=None, scores=()) -> dict:
    """ONE book's ONE horizon forecast record."""
    rows = [e for e in econs if e.get("book") == book]
    lb = now - lookback_days * DAY
    closed = [e for e in rows if e.get("state") == "CLOSED"
              and e.get("net_profit_usd") is not None
              and (e.get("released_at") or 0) >= lb]
    series = M.daily_series(closed, now=now, lookback_start=lb)
    vals = [v for _, v in series]
    val = FC.validation(list(scores))
    out = C.Out(book=book, horizon=horizon, horizon_days=float(days),
                issued_at=now, horizon_start=now,
                horizon_end=now + days * DAY, method=METHOD,
                sample_days=len(vals), sample_positions=len(closed),
                validation=val, version=VERSION, label=C.LABEL)
    basis = {}
    opp = opportunity or {}
    inputs = {"book": book, "horizon": horizon,
              "series": [(d, C.rnd(v)) for d, v in series],
              "opportunity": opp, "capital": capital,
              "capacity_daily": capacity_daily,
              "fill_probability": fill_probability}
    out["inputs_sha256"] = C.sha(inputs)

    # ── opportunity lines (PAPER decisions only) ──────────────────────
    if book != "PAPER":
        why = "OPPORTUNITY_FLOW_IS_MEASURED_ON_PAPER_DECISIONS_ONLY"
        out.put("expected_opportunities", None, why)
        out.put("expected_qualified_opportunities", None, why)
    elif (opp.get("days") or 0) < OPP_MIN_DAYS:
        why = "FEWER_THAN_%d_DAYS_OF_CANDIDATE_HISTORY" % OPP_MIN_DAYS
        out.put("expected_opportunities", None, why)
        out.put("expected_qualified_opportunities", None, why)
    else:
        d = float(opp["days"])
        out.put("expected_opportunities", cnt(opp["candidates"] / d * days))
        out.put("expected_qualified_opportunities",
                cnt(opp["qualified"] / d * days))
        basis["opportunities"] = (
            "%d candidates / %d qualified over %.1f days of pos_capacity "
            "x %d days" % (opp["candidates"], opp["qualified"], d, days))

    # ── capital lines ─────────────────────────────────────────────────
    cap = capital or {}
    out.put("deployable_capital_usd", usd(C.num(cap.get("idle_capital_usd"))),
            cap.get("idle_why") or "NO_CAPITAL_SNAPSHOT_FOR_THIS_BOOK")
    basis["deployable_capital"] = "idle capital now (latest CAPITAL snapshot)"
    if capacity_daily is None:
        out.put("expected_capacity_usd", None,
                "NO_MEASURED_DAILY_EXECUTABLE_OPPORTUNITY")
    else:
        fp = fill_probability if fill_probability is not None else 1.0
        out.put("expected_capacity_usd", usd(capacity_daily * fp * days))
        basis["capacity"] = "daily executable opportunity x %s x %d days" % (
            "measured fill probability" if fill_probability is not None
            else "1.0 (FILL PROBABILITY UNMEASURED: conditional on fill)",
            days)

    first_open = min((e["opened_at"] for e in rows if e.get("opened_at")),
                     default=None)
    if first_open is None:
        why = "NO_POSITION_IN_THE_LOOKBACK"
        out.put("expected_turnover_usd", None, why)
        out.put("expected_capital_hours", None, why)
        out.put("capacity_utilization", None, why)
    else:
        hist_h = max(1.0, (now - max(lb, first_open)) / 3600.0)
        hist_d = hist_h / 24.0
        ch = 0.0
        for e in rows:
            segs = list(e.get("segments") or [])
            if e.get("state") == "OPEN" and (e.get("open_cost_basis_usd")
                                             or 0) \
                    and e.get("last_event_at") is not None:
                segs.append((e["last_event_at"], now,
                             e["open_cost_basis_usd"]))
            ch += sum(max(0.0, min(t1, now) - max(t0, lb)) / 3600.0 * c
                      for t0, t1, c in segs)
        committed = sum(e.get("capital_committed_usd") or 0.0 for e in rows
                        if (e.get("first_fill_at") or 0) >= lb)
        out.put("expected_turnover_usd", usd(committed / hist_d * days))
        exp_ch = ch / hist_d * days
        out.put("expected_capital_hours", usd(exp_ch))
        acct = C.num(cap.get("account_capital_usd"))
        if acct is None or acct <= 0:
            out.put("capacity_utilization", None,
                    "NO_ACCOUNT_CAPITAL_RECORD")
        else:
            out.put("capacity_utilization",
                    round(exp_ch / (acct * days * 24.0), 4))
        basis["capital"] = ("history %.1f days: capital-hours (open accrued "
                            "to now) and capital committed, per day x %d"
                            % (hist_d, days))
    out["basis"] = basis

    # ── the P&L distribution ─────────────────────────────────────────
    keys = ("expected_pnl_usd", "p10_pnl_usd", "p50_pnl_usd", "p90_pnl_usd",
            "prob_positive", "expected_max_drawdown_usd")
    if len(vals) < MIN_DAYS or len(closed) < MIN_POSITIONS:
        why = ("FEWER_THAN_%d_DAYS_OF_REALIZED_HISTORY" % MIN_DAYS
               if len(vals) < MIN_DAYS else
               "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS)
        out["status"], out["why"] = C.UNAVAILABLE, why
        out["seed"], out["quantiles"] = None, None
        for k in keys:
            out.put(k, None, why)
        return out
    seed = C.seed_of(inputs)
    out["seed"] = seed
    paths = M.block_paths(vals, rng=random.Random(seed), paths=RESAMPLES,
                          horizon=days, block=min(M.BLOCK_DAYS, days))
    sums = sorted(sum(p) for p in paths)
    dds = [C.max_drawdown(p) for p in paths]
    out.put("expected_pnl_usd", usd(sum(sums) / len(sums)))
    out.put("p10_pnl_usd", usd(C.quantile(sums, 0.10)))
    out.put("p50_pnl_usd", usd(C.quantile(sums, 0.50)))
    out.put("p90_pnl_usd", usd(C.quantile(sums, 0.90)))
    out.put("prob_positive", prob(sum(1 for s in sums if s > 0)
                                  / float(len(sums))))
    out.put("expected_max_drawdown_usd", usd(sum(dds) / len(dds)))
    out["quantiles"] = {str(q): usd(C.quantile(sums, q))
                        for q in FC.QUANTILE_GRID}
    out["status"] = "FORWARD_VALIDATED" if val["validated"] else C.UNPROVEN
    out["why"] = None if val["validated"] else (
        "UNPROVEN_UNTIL_FORWARD_CALIBRATION_OF_THIS_HORIZON: %s" % val["why"])
    return out


def score(fc: dict, *, realized_pnl, realized_positions, now) -> dict:
    """pos-econ's scoring rule, for one ended horizon forecast."""
    got = FC.score(fc, realized_pnl=realized_pnl,
                   realized_positions=realized_positions, now=now)
    got["horizon"] = fc["horizon"]
    got["version"] = VERSION
    return got
