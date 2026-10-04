"""THE PREDICTABLE MONTHLY REVENUE ENGINE (RESEARCH). Pure; no I/O.

UNPROVEN BY CONSTRUCTION. A forecast here is a resampling of what the book
has already realized, not a claim about the future. It stays UNPROVEN until
its OWN forecasts, persisted daily, have been scored against the 30 days
that followed and passed VALIDATION (below). Nothing reads it to size,
limit or allocate anything.

SCOPE (migration 227): one forecast per book AND SLEEVE, never pooled. The
INVESTMENT forecast is the PRODUCTION-CONFIDENCE forecast; TRAINING,
BENCHMARK and UNCLASSIFIED are forecast separately as research, each
validated only by its OWN scored history, and scored against its own
sleeve's realized P&L.

METHOD (per book, never summed): the daily realized net P&L series over the
lookback (metrics.daily_series; a day with no closure is a measured 0 once
history has begun) is resampled by a circular block bootstrap (BLOCK_DAYS
blocks, RESAMPLES paths of HORIZON_DAYS days, a seed derived from the
inputs so a forecast is reproducible). From the paths:

  expected 30-day net P&L      mean of the path sums
  P10 / P50 / P90              quantiles of the path sums (plus a 5%-step
                               quantile grid, kept for PIT scoring)
  P(positive 30-day P&L)       share of paths with a positive sum
  expected max drawdown        mean over paths of the path's peak-to-trough
  P95 / WORST-CASE MODELLED    the 95th percentile / the maximum over paths
    DRAWDOWN                   (worst modelled, not worst possible)

From the economics:

  capital required             time-weighted average locked capital over
                               the history (capital-hours / hours)
  capital-hours required       average daily capital-hours x 30
  turnover required            average daily capital committed x 30
  capacity ceiling             average daily EXECUTABLE opportunity (capacity
                               model) x fill probability (when measured) x
                               30: the most the recorded books could have
                               paid, an upper bound, not a forecast

FAIL-CLOSED. Fewer than MIN_DAYS days of history or MIN_POSITIONS closed
positions -> status UNAVAILABLE with the reason and no numbers.

SCORING. A forecast whose horizon has ended is scored once: realized net P&L
of positions released inside its horizon, its PIT (where that fell in the
forecast's quantile grid), whether it fell inside P10-P90, and the Brier
score of P(positive). VALIDATION: >= VALIDATION_MIN_SCORED scored forecasts,
P10-P90 coverage inside COVERAGE_BAND, mean Brier <= BRIER_MAX. Only then is
a new forecast issued as FORWARD_VALIDATED (still research).
"""
from __future__ import annotations

import random

from . import common as C
from . import metrics as M

VERSION = "POS_FORECAST_V1"
METHOD = "CIRCULAR_BLOCK_BOOTSTRAP_DAILY_REALIZED_NET_PNL"
HORIZON_DAYS = 30
BLOCK_DAYS = M.BLOCK_DAYS
RESAMPLES = 2000
MIN_DAYS = 14
MIN_POSITIONS = 10
VALIDATION_MIN_SCORED = 12
COVERAGE_BAND = (0.70, 0.90)
BRIER_MAX = 0.25
QUANTILE_GRID = tuple(round(0.05 * i, 2) for i in range(1, 20))
DAY = 86400.0


def validation(scores: list) -> dict:
    """The forward calibration of the forecast itself."""
    n = len(scores)
    out = {"scored": n, "min_scored": VALIDATION_MIN_SCORED,
           "coverage_band": list(COVERAGE_BAND), "brier_max": BRIER_MAX}
    if n == 0:
        out.update(validated=False, coverage=None, mean_brier=None,
                   why="NO_FORECAST_SCORED_YET")
        return out
    cov = sum(1 for s in scores if s["inside_p10_p90"]) / float(n)
    brier = sum(s["brier_positive"] for s in scores) / float(n)
    ok = (n >= VALIDATION_MIN_SCORED and COVERAGE_BAND[0] <= cov
          <= COVERAGE_BAND[1] and brier <= BRIER_MAX)
    out.update(validated=ok, coverage=C.rnd(cov, 6),
               mean_brier=C.rnd(brier, 6),
               why=None if ok else (
                   "FEWER_THAN_%d_SCORED_FORECASTS" % VALIDATION_MIN_SCORED
                   if n < VALIDATION_MIN_SCORED else
                   "COVERAGE_OR_BRIER_OUTSIDE_THE_BAND"))
    return out


def build(econs: list, *, book: str, now: float, lookback_days: float,
          capacity_daily=None, fill_probability=None, scores=(),
          sleeve=None, strategy=None, capacity_why=None) -> dict:
    """ONE book's (and ONE scope's) 30-day forecast record (see the module
    docstring). `sleeve` INVESTMENT is the PRODUCTION-CONFIDENCE forecast;
    every other sleeve is forecast separately as research; `sleeve` None
    pools every sleeve (research only). `scores` must be THIS scope's own
    scored forecasts."""
    rows = [e for e in econs
            if C.in_scope(e, book=book, sleeve=sleeve, strategy=strategy)]
    lb = now - lookback_days * DAY
    closed = [e for e in rows if e.get("state") == "CLOSED"
              and e.get("net_profit_usd") is not None
              and (e.get("released_at") or 0) >= lb]
    series = M.daily_series(closed, now=now, lookback_start=lb)
    vals = [v for _, v in series]
    val = validation(list(scores))
    out = C.Out(book=book, issued_at=now, horizon_start=now,
                horizon_end=now + HORIZON_DAYS * DAY,
                horizon_days=HORIZON_DAYS, method=METHOD,
                resamples=RESAMPLES, block_days=BLOCK_DAYS,
                sample_days=len(vals), sample_positions=len(closed),
                validation=val, version=VERSION, label=C.LABEL)
    out.update(C.scope_fields(rows, book=book, sleeve=sleeve,
                              strategy=strategy or C.ALL_STRATEGIES))
    inputs = {"book": book, "series": [(d, C.rnd(v)) for d, v in series],
              "capacity_daily": capacity_daily,
              "fill_probability": fill_probability}
    if sleeve is not None:
        # the scope is part of what was forecast (a pre-227 book-wide input
        # hash is unchanged)
        inputs.update(sleeve=sleeve, strategy=strategy or C.ALL_STRATEGIES)
    out["inputs_sha256"] = C.sha(inputs)
    keys = ("expected_pnl_usd", "p10_pnl_usd", "p50_pnl_usd", "p90_pnl_usd",
            "prob_positive", "expected_max_drawdown_usd",
            "p95_max_drawdown_usd", "worst_modelled_drawdown_usd",
            "capital_required_usd", "capital_hours_required",
            "turnover_required_usd", "capacity_ceiling_usd")
    if len(vals) < MIN_DAYS or len(closed) < MIN_POSITIONS:
        why = ("FEWER_THAN_%d_DAYS_OF_REALIZED_HISTORY" % MIN_DAYS
               if len(vals) < MIN_DAYS else
               "FEWER_THAN_%d_CLOSED_POSITIONS" % MIN_POSITIONS)
        out["status"] = C.UNAVAILABLE
        out["why"] = why
        out["seed"] = None
        out["quantiles"] = None
        for k in keys:
            out.put(k, None, why)
        return out
    seed = C.seed_of(inputs)
    out["seed"] = seed
    rng = random.Random(seed)
    paths = M.block_paths(vals, rng=rng, paths=RESAMPLES,
                          horizon=HORIZON_DAYS, block=BLOCK_DAYS)
    sums = sorted(sum(p) for p in paths)
    dds = sorted(C.max_drawdown(p) for p in paths)
    out.put("expected_pnl_usd", C.rnd(sum(sums) / len(sums)))
    out.put("p10_pnl_usd", C.rnd(C.quantile(sums, 0.10)))
    out.put("p50_pnl_usd", C.rnd(C.quantile(sums, 0.50)))
    out.put("p90_pnl_usd", C.rnd(C.quantile(sums, 0.90)))
    out.put("prob_positive", C.rnd(sum(1 for s in sums if s > 0)
                                   / float(len(sums)), 6))
    out.put("expected_max_drawdown_usd", C.rnd(sum(dds) / len(dds)))
    out.put("p95_max_drawdown_usd", C.rnd(C.quantile(dds, 0.95)))
    out.put("worst_modelled_drawdown_usd", C.rnd(dds[-1]))
    out["quantiles"] = {str(q): C.rnd(C.quantile(sums, q))
                        for q in QUANTILE_GRID}
    # capital, from the economics over the same history
    first_open = min((e["opened_at"] for e in rows if e.get("opened_at")),
                     default=lb)
    hist_h = max(1.0, (now - max(lb, first_open)) / 3600.0)
    ch = 0.0
    for e in rows:
        segs = list(e.get("segments") or [])
        if e.get("state") == "OPEN" and (e.get("open_cost_basis_usd") or 0) \
                and e.get("last_event_at") is not None:
            segs.append((e["last_event_at"], now, e["open_cost_basis_usd"]))
        ch += sum(max(0.0, min(t1, now) - max(t0, lb)) / 3600.0 * cap
                  for t0, t1, cap in segs)
    days = hist_h / 24.0
    out.put("capital_required_usd", C.rnd(ch / hist_h))
    out.put("capital_hours_required", C.rnd(ch / days * HORIZON_DAYS, 6))
    committed = sum(e.get("capital_committed_usd") or 0.0 for e in rows
                    if (e.get("first_fill_at") or 0) >= lb)
    out.put("turnover_required_usd", C.rnd(committed / days * HORIZON_DAYS))
    out["capital_basis"] = (
        "history = %.1f days; capital-hours over it (open positions accrued "
        "to now) and capital committed by positions first filled in it"
        % days)
    if capacity_daily is None:
        out.put("capacity_ceiling_usd", None,
                capacity_why or "NO_MEASURED_DAILY_EXECUTABLE_OPPORTUNITY")
    else:
        fp = fill_probability if fill_probability is not None else 1.0
        out.put("capacity_ceiling_usd",
                C.rnd(capacity_daily * fp * HORIZON_DAYS))
        out["capacity_ceiling_basis"] = (
            "daily executable opportunity x %s x 30" % (
                "measured fill probability" if fill_probability is not None
                else "1.0 (FILL PROBABILITY UNMEASURED: conditional on fill)"))
    out["status"] = "FORWARD_VALIDATED" if val["validated"] else C.UNPROVEN
    out["why"] = None if val["validated"] else (
        "UNPROVEN_UNTIL_FORWARD_CALIBRATION_OF_THE_FORECAST: %s" % val["why"])
    return out


def score(fc: dict, *, realized_pnl: float, realized_positions: int,
          now: float) -> dict:
    """Score one ended forecast against what was realized in its horizon."""
    q = C.jload(fc.get("quantiles")) or {}
    grid = sorted((float(k), float(v)) for k, v in q.items()
                  if C.num(v) is not None)
    pit = None
    if grid:
        if realized_pnl <= grid[0][1]:
            pit = grid[0][0] / 2.0
        elif realized_pnl >= grid[-1][1]:
            pit = (1.0 + grid[-1][0]) / 2.0
        else:
            for (qa, va), (qb, vb) in zip(grid, grid[1:]):
                if va <= realized_pnl <= vb:
                    pit = qa if vb == va else qa + (qb - qa) * (
                        realized_pnl - va) / (vb - va)
                    break
    pos = realized_pnl > 0
    pp = float(fc["prob_positive"])
    return {"forecast_id": fc["forecast_id"], "book": fc["book"],
            # the forecast's own scope (NULL for a pre-227 book-wide one)
            "sleeve": fc.get("sleeve"), "strategy": fc.get("strategy"),
            "confidence_scope": fc.get("confidence_scope"),
            "scored_at": now, "realized_pnl_usd": C.rnd(realized_pnl),
            "realized_positions": int(realized_positions),
            "pit": C.rnd(pit, 6),
            "inside_p10_p90": bool(float(fc["p10_pnl_usd"]) <= realized_pnl
                                   <= float(fc["p90_pnl_usd"])),
            "realized_positive": pos,
            "brier_positive": C.rnd((pp - (1.0 if pos else 0.0)) ** 2, 9),
            "abs_error_vs_p50_usd": C.rnd(abs(realized_pnl
                                              - float(fc["p50_pnl_usd"]))),
            "version": VERSION}
