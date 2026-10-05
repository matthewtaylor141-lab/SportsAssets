"""PROFITABILITY VALIDATION PER SLEEVE (RESEARCH). Pure; no I/O.

THE QUESTION. Is the INVESTMENT / PRODUCTION-CANDIDATE policy profitable on
forward evidence? Every figure here is computed PER SLEEVE (migration 223)
and never pooled across sleeves:

  INVESTMENT    production-candidate paper -- the ONLY sleeve whose forward
                evidence may count toward live activation
  TRAINING      bounded exploration: a loss is a research cost, a win is not
                production alpha
  BENCHMARK     control arms
  UNCLASSIFIED  economic purpose not established -- shown separately, NEVER
                counted as INVESTMENT

TWO WINDOWS PER SLEEVE. ALL_TIME (every position the sleeve ever entered)
and FORWARD (positions whose group first filled at or after `since`, the R30
cutover by default). Membership is decided by the group's ENTRY time, so a
position entered before the cutover never leaks into the forward evidence
however late it resolves.

THE METRICS (each {value, unit, basis, sample_n, window, status, why}; an
unmeasurable metric is value None, status UNAVAILABLE, with a precise reason
-- never a manufactured zero; a measured zero says so in its basis):

  REALIZED_NET_USD        sum of the paper ledger's realized P&L of the
                          sleeve's positions (fees included: buy fees are in
                          the cost basis, sale fees come off proceeds;
                          partial reductions of still-open positions count)
  RESOLVED_NET_PER_POSITION_USD  mean realized net of RESOLVED positions
                          (open quantity 0), with a one-sided 95% t lower
                          bound and a bootstrap 5th percentile
  UNREALIZED_USD          marked open positions only (mark - cost basis);
                          an unmarked position adds nothing and is counted
  FEES_USD                buy + sale fees of the sleeve's paper fills
  SLIPPAGE_USD            intel_attribution (PAPER) slippage q*(fill VWAP -
                          decision price): positive = paid above plan
  MAX_DRAWDOWN_USD        largest peak-to-trough fall of cumulative realized
                          net of resolved positions ordered by release time
  CAPITAL_HOURS           sum of pos_economics_latest capital-hours (USD-h)
                          of the sleeve's positions (open and closed)
  PROFIT_PER_CAPITAL_HOUR sum(net) / sum(capital-hours) over CLOSED
                          pos_economics rows that carry both (USD per USD-h)
  TURNOVER                gross traded notional (buy gross + sale gross,
                          fees excluded, settlements are not trades) /
                          average deployed capital, where average deployed
                          capital = sum(capital-hours) / window hours. Only
                          positions that carry capital-hours enter either
                          side; the window runs from `since` (FORWARD) or
                          the sleeve's first fill (ALL_TIME) to now. Unit:
                          times over the window (turnover_per_day beside it)
  OPPORTUNITY_SCORE_CALIBRATION  Spearman rank correlation between the
                          LOL_OPPORTUNITY_SCORE (status MEASURED) of scored
                          candidates that were ENTERED and fully RESOLVED and
                          their realized net; quantile buckets (deciles at
                          >= 50, else n // 5 buckets) with mean score, mean
                          realized net and realized edge per dollar, and the
                          share of adjacent buckets that rise. UNAVAILABLE
                          below CAL_MIN_N
  FALSE_REFUSAL_RATE      FALSE_REFUSAL / (FALSE_REFUSAL + GOOD_REFUSAL)
                          from lol_ledger (latest classification per
                          decision), attributed to a sleeve by the refused
                          decision's strategy; UNKNOWABLE is reported
                          beside it and never enters the rate. Refusals with
                          no strategy are book-wide only, stated as such
  EXECUTION_QUALITY_DELTA_PP  mean (realized - predicted) execution loss per
                          contract (eddie_execution_outcomes, PAPER) --
                          positive = execution cost more than Archer
                          predicted; the delta vs the naive taker execution
                          and the qty-weighted USD are beside it
  MANAGEMENT_VALUE_DELTA_USD  sum of xavier_value_add incrementals
                          ACTUAL_XAVIER - HOLD_TO_SETTLEMENT (latest row per
                          thesis, PAPER), with ACTUAL_XAVIER - IMMEDIATE_EXIT
                          beside it; unavailable incrementals are counted

THE VERDICT (INVESTMENT, FORWARD window only) -- the PRE-DECLARED RULE is
`verdict_rule()` / VERDICT_RULE below. It never says
SUPPORTED_BY_FORWARD_EVIDENCE unless every check passes.

Nothing here reaches an order, venue, size, limit, threshold, gate or
capital path; this module computes over rows a caller has read.
"""
from __future__ import annotations

import math
import os
import random

from . import common as C

VERSION = "PROFITABILITY_VALIDATION_V1"
INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED = (
    "INVESTMENT", "TRAINING", "BENCHMARK", "UNCLASSIFIED")
#: pinned equal to bettor_paper_sleeves.SLEEVES by a test (this module may
#: not import a paper module)
SLEEVES = (INVESTMENT, TRAINING, BENCHMARK, UNCLASSIFIED)
ALL_TIME, FORWARD = "ALL_TIME", "FORWARD"
WINDOWS = (ALL_TIME, FORWARD)

#: THE R30 CUTOVER is NOT a constant: it is the EFFECTIVE production
#: cutover. live_parity.record_cutover appends ONE row per release
#: (live_parity_cutover) after the release SHA is accepted, API and workers
#: run it, migrations 225/226 are applied, the canonical hooks are installed,
#: the readback passes, no capital is active and SMALL LIVE is SHADOW; the
#: forward window starts at the latest release whose decision-logic hash
#: differs from its predecessor's (view live_parity_effective_cutover,
#: R30A): a release that does not change decision logic does not restart the
#: sample, one that does restarts it. Until one exists there is NO forward
#: window and the verdict is NOT_ESTABLISHED (NO_PRODUCTION_CUTOVER_RECORDED).
R_NO_CUTOVER = "NO_PRODUCTION_CUTOVER_RECORDED"

METRICS = ("REALIZED_NET_USD", "RESOLVED_NET_PER_POSITION_USD",
           "UNREALIZED_USD", "FEES_USD", "SLIPPAGE_USD", "MAX_DRAWDOWN_USD",
           "CAPITAL_HOURS", "PROFIT_PER_CAPITAL_HOUR", "TURNOVER",
           "OPPORTUNITY_SCORE_CALIBRATION", "FALSE_REFUSAL_RATE",
           "EXECUTION_QUALITY_DELTA_PP", "MANAGEMENT_VALUE_DELTA_USD")
UNITS = {
    "REALIZED_NET_USD": "USD",
    "RESOLVED_NET_PER_POSITION_USD": "USD per resolved position",
    "UNREALIZED_USD": "USD",
    "FEES_USD": "USD",
    "SLIPPAGE_USD": "USD (positive = paid above the decision price)",
    "MAX_DRAWDOWN_USD": "USD (>= 0)",
    "CAPITAL_HOURS": "USD-hours",
    "PROFIT_PER_CAPITAL_HOUR": "USD net profit per USD-hour",
    "TURNOVER": ("times: gross traded USD per USD of average deployed "
                 "capital over the window"),
    "OPPORTUNITY_SCORE_CALIBRATION": ("Spearman rank correlation, score vs "
                                      "realized net per entered candidate"),
    "FALSE_REFUSAL_RATE": "share of decided refusals (0..1)",
    "EXECUTION_QUALITY_DELTA_PP": ("USD per contract, realized minus "
                                   "predicted execution loss"),
    "MANAGEMENT_VALUE_DELTA_USD": ("USD, ACTUAL_XAVIER minus "
                                   "HOLD_TO_SETTLEMENT"),
}

#: sample floors (a value below its floor is shown as INSUFFICIENT_SAMPLE,
#: never trusted; calibration is UNAVAILABLE below its floor)
MIN_SAMPLE = 30
CAL_MIN_N = 20
CAL_MIN_PER_BUCKET = 5
CAL_MAX_BUCKETS = 10
RATE_MIN_N = 20
EXEC_MIN_N = 20
MGMT_MIN_N = 20
BOOTSTRAP_RESAMPLES = 2000
EPS_QTY = 1e-9
HOUR = 3600.0
DAY = 86400.0

# ── THE PRE-DECLARED VERDICT RULE ────────────────────────────────────
NOT_ESTABLISHED = "NOT_ESTABLISHED"
NEGATIVE = "NEGATIVE"
POSITIVE_BUT_INSUFFICIENT = "POSITIVE_BUT_INSUFFICIENT_SAMPLE"
SUPPORTED = "SUPPORTED_BY_FORWARD_EVIDENCE"
VERDICTS = (NOT_ESTABLISHED, NEGATIVE, POSITIVE_BUT_INSUFFICIENT, SUPPORTED)
VERDICT_MIN_RESOLVED = 30
#: INDEPENDENT resolved outcomes: positions on the same event (fixture, else
#: market) share one outcome and are ONE observation for the bounds, the
#: drawdown and this minimum. 30 parity observations are NOT profit evidence.
VERDICT_MIN_INDEPENDENT_EVENTS = 30
VERDICT_CONFIDENCE = 0.95                    # one-sided
#: the fictional paper account's starting cash (the sleeves share it; no
#: sleeve has its own allocation, migration 182 / 223)
PAPER_STARTING_CASH_USD = 500_000.0
DRAWDOWN_BOUND_FRAC = 0.02
DRAWDOWN_BOUND_USD = PAPER_STARTING_CASH_USD * DRAWDOWN_BOUND_FRAC
VERDICT_RULE = {
    "version": VERSION,
    "applies_to": "INVESTMENT sleeve, FORWARD window only",
    "order": [
        "no resolved forward INVESTMENT position -> NOT_ESTABLISHED",
        "sum of resolved realized net (after fees) < 0 -> NEGATIVE",
        "sum of resolved realized net == 0 -> NOT_ESTABLISHED",
        "sum > 0 and EVERY check below passes -> "
        "SUPPORTED_BY_FORWARD_EVIDENCE",
        "otherwise -> POSITIVE_BUT_INSUFFICIENT_SAMPLE (failed checks "
        "listed)"],
    "checks": {
        "MIN_RESOLVED": ">= %d resolved forward positions"
                        % VERDICT_MIN_RESOLVED,
        "MIN_INDEPENDENT_EVENTS":
            ">= %d INDEPENDENT resolved events (positions on one fixture, "
            "else one market, are one outcome)" % VERDICT_MIN_INDEPENDENT_EVENTS,
        "REALIZED_NET_AFTER_FEES_POSITIVE":
            "sum of resolved realized net > 0 (ledger P&L, fees included)",
        "T_LOWER_BOUND_POSITIVE":
            "one-sided %d%% t lower bound of mean per-EVENT net > 0"
            % int(VERDICT_CONFIDENCE * 100),
        "BOOTSTRAP_LOWER_BOUND_POSITIVE":
            "bootstrap %d%% lower percentile of mean per-EVENT net > 0"
            % int(round((1 - VERDICT_CONFIDENCE) * 100)),
        "DRAWDOWN_WITHIN_BOUND":
            "max realized drawdown of per-event net, by release, <= %.0f USD (%.0f%% of the $%.0f paper "
            "account)" % (DRAWDOWN_BOUND_USD, DRAWDOWN_BOUND_FRAC * 100,
                          PAPER_STARTING_CASH_USD),
        "NET_INCLUDING_MARKED_OPEN_POSITIVE":
            "resolved realized net + realized on open positions + marked "
            "unrealized > 0 (open losses are not ignored)",
        "FORWARD_ONLY":
            "the window starts at or after the R30 cutover (no "
            "pre-cutover position is evidence)",
    },
}

# reasons
R_NO_POSITIONS = "NO_POSITION_ENTERED_IN_THE_WINDOW"
R_NO_RESOLVED = "NO_RESOLVED_POSITION_IN_THE_WINDOW"
R_SOURCE = "SOURCE_NOT_AVAILABLE"


def forward_since(since, cutover):
    """The FORWARD window's start: never before the production cutover; with
    no cutover recorded there is no forward window (None)."""
    if cutover is None:
        return None
    return max(float(since), float(cutover)) if since is not None \
        else float(cutover)


# ═════════════════════════════════════════════════════════════════════
# SMALL PURE HELPERS
# ═════════════════════════════════════════════════════════════════════

def _num(v):
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def metric(name, value, *, n, basis, window, status=None, why=None,
           detail=None, min_n=MIN_SAMPLE) -> dict:
    """One metric record. A None value is UNAVAILABLE and MUST carry a
    reason; a value below its sample floor is INSUFFICIENT_SAMPLE."""
    if value is None:
        status = status or C.UNAVAILABLE
        why = why or "NOT_MEASURED"
    elif status is None:
        if n >= min_n:
            status = C.MEASURED
        else:
            status = C.INSUFFICIENT
            why = why or "FEWER_THAN_%d_OBSERVATIONS" % min_n
    return {"metric": name, "value": C.rnd(value, 9), "unit": UNITS[name],
            "basis": basis, "sample_n": int(n), "min_sample": int(min_n),
            "window": window, "status": status, "why": why,
            "detail": detail or {}}


def unavailable(name, why, *, window, basis="", n=0, detail=None) -> dict:
    return metric(name, None, n=n, basis=basis, window=window, why=why,
                  detail=detail)


T95_ONE_SIDED = (6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860,
                 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753, 1.746,
                 1.740, 1.734, 1.729, 1.725, 1.721, 1.717, 1.714, 1.711,
                 1.708, 1.706, 1.703, 1.701, 1.699, 1.697)


def t_crit_95(df: int) -> float:
    """One-sided 95% Student-t critical value (table to df 30, then the
    Cornish-Fisher expansion about z = 1.6449)."""
    if df < 1:
        raise ValueError("df must be >= 1")
    if df <= len(T95_ONE_SIDED):
        return T95_ONE_SIDED[df - 1]
    z = 1.6448536
    return (z + (z ** 3 + z) / (4 * df)
            + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2))


def mean_lower_bounds(xs: list, *, seed: int,
                      resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """One-sided 95% lower bounds of the mean: Student-t and percentile
    bootstrap. None (with why) below two observations."""
    n = len(xs)
    if n < 2:
        return {"t_lower_95": None, "bootstrap_lower_95": None,
                "mean": C.rnd(xs[0], 9) if n else None, "stdev": None,
                "why": "FEWER_THAN_TWO_RESOLVED_POSITIONS"}
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    t_lo = m - t_crit_95(n - 1) * sd / math.sqrt(n)
    rng = random.Random(seed)
    k = max(200, min(resamples, C.DRAW_BUDGET // n))
    means = sorted(sum(xs[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(k))
    return {"t_lower_95": C.rnd(t_lo, 9),
            "bootstrap_lower_95": C.rnd(C.quantile(means, 0.05), 9),
            "mean": C.rnd(m, 9), "stdev": C.rnd(sd, 9),
            "bootstrap_resamples": k, "why": None}


def ranks(xs: list) -> list:
    """Average ranks (1-based), ties share their mean rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            out[order[k]] = r
        i = j + 1
    return out


def spearman(xs: list, ys: list):
    """Spearman rho (Pearson correlation of average ranks); None when either
    side is constant or fewer than 3 pairs."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


# ═════════════════════════════════════════════════════════════════════
# SCOPE: which groups / positions a sleeve-window holds
# ═════════════════════════════════════════════════════════════════════

def group_entry_times(positions: list) -> dict:
    """{group_id: first fill epoch of the group (min over its positions)}."""
    out: dict = {}
    for p in positions:
        g, t = p.get("group_id"), _num(p.get("first_fill_at"))
        if g is None or t is None:
            continue
        out[g] = t if g not in out else min(out[g], t)
    return out


def group_sleeves(positions: list) -> dict:
    """{group_id: sleeve}; a missing or unknown sleeve is UNCLASSIFIED --
    never INVESTMENT."""
    out = {}
    for p in positions:
        s = p.get("sleeve")
        out[p.get("group_id")] = s if s in SLEEVES else UNCLASSIFIED
    return out


def scope_groups(positions: list, sleeve: str, since) -> set:
    gs, entry = group_sleeves(positions), group_entry_times(positions)
    return {g for g, s in gs.items() if s == sleeve and g in entry
            and (since is None or entry[g] >= since)}


def window_of(kind: str, *, since, now: float, positions: list,
              groups: set) -> dict:
    if kind == FORWARD:
        start = since
    else:
        entry = group_entry_times(positions)
        ts = [entry[g] for g in groups if g in entry]
        start = min(ts) if ts else None
    return {"kind": kind, "start": start, "end": now,
            "since": since if kind == FORWARD else None,
            "hours": (None if start is None else
                      C.rnd(max(0.0, now - start) / HOUR, 6)),
            "membership": ("positions whose group first filled at or after "
                           "`since`" if kind == FORWARD else
                           "every position the sleeve entered")}


# ═════════════════════════════════════════════════════════════════════
# THE METRICS (one sleeve, one window)
# ═════════════════════════════════════════════════════════════════════

def _resolved(p) -> bool:
    return (_num(p.get("open_qty")) or 0.0) <= EPS_QTY


def ledger_metrics(pos: list, *, window: dict, seed: int) -> dict:
    """REALIZED_NET_USD, RESOLVED_NET_PER_POSITION_USD, UNREALIZED_USD,
    FEES_USD, MAX_DRAWDOWN_USD from the sleeve's ledger positions."""
    out = {}
    basis_l = "paper ledger positions (paper_fills + paper_settlements)"
    if not pos:
        for m in ("REALIZED_NET_USD", "RESOLVED_NET_PER_POSITION_USD",
                  "UNREALIZED_USD", "FEES_USD", "MAX_DRAWDOWN_USD"):
            out[m] = unavailable(m, R_NO_POSITIONS, window=window,
                                 basis=basis_l)
        return out
    realized = [_num(p.get("realized_pnl_usd")) for p in pos]
    rz_missing = sum(1 for v in realized if v is None)
    rz = [v for v in realized if v is not None]
    resolved = [p for p in pos if _resolved(p)
                and _num(p.get("realized_pnl_usd")) is not None]
    open_ = [p for p in pos if not _resolved(p)]
    out["REALIZED_NET_USD"] = metric(
        "REALIZED_NET_USD", sum(rz) if rz else None, n=len(rz),
        basis=basis_l + "; realized P&L incl. fees, partial reductions of "
        "open positions included", window=window,
        why=None if rz else "NO_POSITION_CARRIES_A_REALIZED_PNL",
        detail={"positions": len(pos), "resolved_positions": len(resolved),
                "open_positions": len(open_),
                "positions_missing_realized": rz_missing,
                "resolved_net_usd": C.rnd(sum(
                    _num(p["realized_pnl_usd"]) for p in resolved)),
                "realized_on_open_positions_usd": C.rnd(sum(
                    _num(p.get("realized_pnl_usd")) or 0.0 for p in open_)),
                "wins": sum(1 for p in resolved
                            if _num(p["realized_pnl_usd"]) > 0),
                "losses": sum(1 for p in resolved
                              if _num(p["realized_pnl_usd"]) < 0)})
    # per resolved position
    xs = [_num(p["realized_pnl_usd"]) for p in resolved]
    if not xs:
        out["RESOLVED_NET_PER_POSITION_USD"] = unavailable(
            "RESOLVED_NET_PER_POSITION_USD", R_NO_RESOLVED, window=window,
            basis=basis_l, detail={"open_positions": len(open_)})
    else:
        lb = mean_lower_bounds(xs, seed=seed)
        out["RESOLVED_NET_PER_POSITION_USD"] = metric(
            "RESOLVED_NET_PER_POSITION_USD", sum(xs) / len(xs), n=len(xs),
            basis=basis_l + "; positions with open quantity 0",
            window=window, detail=lb)
    # unrealized: marked open positions only
    marked = [p for p in open_ if p.get("marked")
              and _num(p.get("unrealized_pnl_usd")) is not None]
    marked_ids = {id(p) for p in marked}
    unmarked = [p for p in open_ if id(p) not in marked_ids]
    if not open_:
        out["UNREALIZED_USD"] = metric(
            "UNREALIZED_USD", 0.0, n=0, status=C.MEASURED,
            basis="MEASURED_ZERO: the window holds no open position",
            window=window, detail={"open_positions": 0})
    elif not marked:
        out["UNREALIZED_USD"] = unavailable(
            "UNREALIZED_USD", "NO_OPEN_POSITION_HAS_A_MARK", window=window,
            basis="ledger marks (bettor_paper_ledger.balances)",
            n=len(open_), detail={
                "open_positions": len(open_), "unmarked_positions":
                len(unmarked), "unmarked_cost_basis_usd": C.rnd(sum(
                    _num(p.get("cost_basis_usd")) or 0.0
                    for p in unmarked))})
    else:
        out["UNREALIZED_USD"] = metric(
            "UNREALIZED_USD", sum(_num(p["unrealized_pnl_usd"])
                                  for p in marked),
            n=len(marked), status=C.MEASURED,
            basis=("ledger marks (bettor_paper_ledger.balances): marked open "
                   "positions only; UNMARKED positions add nothing"),
            window=window,
            why=None if not unmarked else
            "%d_OPEN_POSITIONS_UNMARKED_EXCLUDED" % len(unmarked),
            detail={"open_positions": len(open_),
                    "marked_positions": len(marked),
                    "unmarked_positions": len(unmarked),
                    "complete": not unmarked,
                    "unmarked_cost_basis_usd": C.rnd(sum(
                        _num(p.get("cost_basis_usd")) or 0.0
                        for p in unmarked))})
    # fees
    fees = [(_num(p.get("buy_fees_usd")), _num(p.get("sale_fees_usd")))
            for p in pos]
    fee_ok = [(a or 0.0) + (b or 0.0) for a, b in fees
              if a is not None or b is not None]
    out["FEES_USD"] = metric(
        "FEES_USD", sum(fee_ok) if fee_ok else None, n=len(fee_ok),
        status=C.MEASURED if fee_ok else None,
        basis="sum of paper_fills.fee_usd (buy + sale) of the positions",
        window=window, why=None if fee_ok else "NO_POSITION_CARRIES_FEES",
        detail={"buy_fees_usd": C.rnd(sum(a or 0.0 for a, _ in fees)),
                "sale_fees_usd": C.rnd(sum(b or 0.0 for _, b in fees))})
    # drawdown of the realized path
    path = sorted(((_num(p.get("released_at")) or 0.0,
                    _num(p["realized_pnl_usd"])) for p in resolved),
                  key=lambda t: t[0])
    if not path:
        out["MAX_DRAWDOWN_USD"] = unavailable(
            "MAX_DRAWDOWN_USD", R_NO_RESOLVED, window=window, basis=basis_l)
    else:
        vals = [v for _, v in path]
        cum = peak = 0.0
        for v in vals:
            cum += v
            peak = max(peak, cum)
        out["MAX_DRAWDOWN_USD"] = metric(
            "MAX_DRAWDOWN_USD", C.max_drawdown(vals), n=len(vals),
            basis=("cumulative realized net of resolved positions by release "
                   "time (max of last fill / settlement)"), window=window,
            detail={"current_drawdown_usd": C.rnd(peak - cum),
                    "cumulative_net_usd": C.rnd(cum)})
    return out


def capital_metrics(pos: list, econ: list, *, window: dict,
                    source_why=None) -> dict:
    """CAPITAL_HOURS, PROFIT_PER_CAPITAL_HOUR, TURNOVER."""
    names = ("CAPITAL_HOURS", "PROFIT_PER_CAPITAL_HOUR", "TURNOVER")
    basis_e = "pos_economics_latest (PAPER) rows of the sleeve's groups"
    if source_why:
        return {m: unavailable(m, source_why, window=window, basis=basis_e)
                for m in names}
    if not pos:
        return {m: unavailable(m, R_NO_POSITIONS, window=window,
                               basis=basis_e) for m in names}
    out = {}
    by_key = {e.get("position_key"): e for e in econ
              if e.get("position_key") is not None}
    with_ch = [(p, by_key[p["position_key"]]) for p in pos
               if p.get("position_key") in by_key
               and _num(by_key[p["position_key"]].get("capital_hours"))
               is not None]
    missing = len(pos) - len(with_ch)
    ch = sum(_num(e["capital_hours"]) for _, e in with_ch)
    if not with_ch:
        out["CAPITAL_HOURS"] = unavailable(
            "CAPITAL_HOURS", "NO_POSITION_HAS_MEASURED_CAPITAL_HOURS",
            window=window, basis=basis_e,
            detail={"positions_without_capital_hours": missing})
    else:
        out["CAPITAL_HOURS"] = metric(
            "CAPITAL_HOURS", ch, n=len(with_ch), status=C.MEASURED,
            basis=basis_e, window=window,
            why=None if not missing else
            "%d_POSITIONS_WITHOUT_CAPITAL_HOURS_EXCLUDED" % missing,
            detail={"positions_without_capital_hours": missing})
    closed = [e for _, e in with_ch if e.get("state") == "CLOSED"
              and _num(e.get("net_profit_usd")) is not None
              and _num(e["capital_hours"]) > 0]
    if not closed:
        out["PROFIT_PER_CAPITAL_HOUR"] = unavailable(
            "PROFIT_PER_CAPITAL_HOUR",
            "NO_CLOSED_POSITION_WITH_NET_PROFIT_AND_CAPITAL_HOURS",
            window=window, basis=basis_e)
    else:
        net = sum(_num(e["net_profit_usd"]) for e in closed)
        den = sum(_num(e["capital_hours"]) for e in closed)
        out["PROFIT_PER_CAPITAL_HOUR"] = metric(
            "PROFIT_PER_CAPITAL_HOUR", net / den, n=len(closed),
            basis=basis_e + "; closed rows: sum(net_profit_usd) / "
            "sum(capital_hours)", window=window,
            detail={"net_profit_usd": C.rnd(net),
                    "capital_hours": C.rnd(den, 6)})
    hours = _num(window.get("hours"))
    gross = [(_num(p.get("gross_traded_usd")), e) for p, e in with_ch]
    gross = [(g, e) for g, e in gross if g is not None]
    if not with_ch:
        out["TURNOVER"] = unavailable(
            "TURNOVER", "NO_POSITION_HAS_MEASURED_CAPITAL_HOURS",
            window=window, basis=basis_e)
    elif not hours or hours <= 0:
        out["TURNOVER"] = unavailable(
            "TURNOVER", "WINDOW_HAS_NO_DURATION", window=window,
            basis=basis_e)
    elif not gross:
        out["TURNOVER"] = unavailable(
            "TURNOVER", "NO_POSITION_CARRIES_GROSS_TRADED_NOTIONAL",
            window=window, basis=basis_e)
    elif ch <= 0:
        out["TURNOVER"] = unavailable(
            "TURNOVER", "ZERO_CAPITAL_HOURS_NO_DEPLOYED_CAPITAL",
            window=window, basis=basis_e)
    else:
        g = sum(x for x, _ in gross)
        avg_dep = sum(_num(e["capital_hours"]) for _, e in gross) / hours
        t = g / avg_dep
        out["TURNOVER"] = metric(
            "TURNOVER", t, n=len(gross), status=C.MEASURED,
            basis=("gross traded notional (buy + sale gross, fees excluded) "
                   "/ average deployed capital (sum capital-hours / window "
                   "hours); positions without capital-hours excluded from "
                   "both"), window=window,
            why=None if not missing else
            "%d_POSITIONS_WITHOUT_CAPITAL_HOURS_EXCLUDED" % missing,
            detail={"gross_traded_usd": C.rnd(g),
                    "average_deployed_capital_usd": C.rnd(avg_dep),
                    "window_hours": C.rnd(hours, 6),
                    "turnover_per_day": C.rnd(t / (hours / 24.0), 9)})
    return out


def slippage_metric(attr: list, groups: set, *, window: dict,
                    source_why=None) -> dict:
    basis = "intel_attribution (PAPER) rows of the sleeve's groups"
    if source_why:
        return unavailable("SLIPPAGE_USD", source_why, window=window,
                           basis=basis)
    rows = [a for a in attr if a.get("group_id") in groups]
    if not rows:
        return unavailable("SLIPPAGE_USD", "NO_ATTRIBUTION_ROW_FOR_THE_"
                           "WINDOW'S_POSITIONS", window=window, basis=basis)
    got = [_num(a.get("slippage_usd")) for a in rows]
    ok = [v for v in got if v is not None]
    if not ok:
        return unavailable("SLIPPAGE_USD",
                           "SLIPPAGE_UNMEASURED_ON_EVERY_ATTRIBUTION_ROW",
                           window=window, basis=basis, n=0,
                           detail={"attribution_rows": len(rows)})
    pcs = [_num(a.get("slippage_pc")) for a in rows]
    pcs = [v for v in pcs if v is not None]
    return metric("SLIPPAGE_USD", sum(ok), n=len(ok), status=C.MEASURED,
                  basis=basis + "; q * (fill VWAP - decision price)",
                  window=window,
                  why=None if len(ok) == len(rows) else
                  "%d_ROWS_WITH_SLIPPAGE_UNMEASURED_EXCLUDED"
                  % (len(rows) - len(ok)),
                  detail={"attribution_rows": len(rows),
                          "mean_slippage_per_contract": C.rnd(
                              C.mean(pcs), 9)})


def calibration_metric(scores: list, pos: list, groups: set, *,
                       window: dict, source_why=None) -> dict:
    """Score vs realized net of ENTERED and fully RESOLVED candidates."""
    name = "OPPORTUNITY_SCORE_CALIBRATION"
    basis = ("lol_opportunity_scores_latest (MEASURED) joined to the entered "
             "group (paper_orders ENTRY) and its resolved ledger positions")
    if source_why:
        return unavailable(name, source_why, window=window, basis=basis)
    by_group: dict = {}
    for p in pos:
        by_group.setdefault(p.get("group_id"), []).append(p)
    pairs, unresolved = [], 0
    for s in scores:
        g, sc = s.get("group_id"), _num(s.get("opportunity_score"))
        if g not in groups or sc is None:
            continue
        ps = by_group.get(g) or []
        if not ps or not all(_resolved(p) for p in ps) or any(
                _num(p.get("realized_pnl_usd")) is None for p in ps):
            unresolved += 1
            continue
        net = sum(_num(p["realized_pnl_usd"]) for p in ps)
        cost = sum(_num(p.get("acquisition_cost_usd")) or 0.0 for p in ps)
        pairs.append((sc, net, net / cost if cost > 0 else None))
    n = len(pairs)
    detail = {"scored_entered_resolved": n, "scored_entered_unresolved":
              unresolved, "min_sample": CAL_MIN_N}
    if n < CAL_MIN_N:
        return metric(name, None, n=n, basis=basis, window=window,
                      why="FEWER_THAN_%d_SCORED_ENTERED_RESOLVED_CANDIDATES"
                      % CAL_MIN_N, detail=detail, min_n=CAL_MIN_N)
    pairs.sort(key=lambda t: t[0])
    rho = spearman([a for a, _, _ in pairs], [b for _, b, _ in pairs])
    k = max(2, min(CAL_MAX_BUCKETS, n // CAL_MIN_PER_BUCKET))
    buckets = []
    for i in range(k):
        chunk = pairs[i * n // k:(i + 1) * n // k]
        if not chunk:
            continue
        edges = [e for _, _, e in chunk if e is not None]
        buckets.append({
            "bucket": i + 1, "n": len(chunk),
            "score_min": C.rnd(chunk[0][0], 9),
            "score_max": C.rnd(chunk[-1][0], 9),
            "mean_score": C.rnd(C.mean([a for a, _, _ in chunk]), 9),
            "mean_realized_net_usd": C.rnd(C.mean([b for _, b, _ in chunk])),
            "sum_realized_net_usd": C.rnd(sum(b for _, b, _ in chunk)),
            "mean_realized_edge_per_dollar": C.rnd(C.mean(edges), 9)})
    rises = [buckets[i + 1]["mean_realized_net_usd"]
             > buckets[i]["mean_realized_net_usd"]
             for i in range(len(buckets) - 1)]
    detail.update(
        buckets=buckets, bucket_kind=("DECILES" if k == 10 else
                                      "%d_QUANTILE_BUCKETS" % k),
        adjacent_rise_share=C.rnd(sum(rises) / len(rises), 6)
        if rises else None,
        monotone_non_decreasing=all(
            buckets[i + 1]["mean_realized_net_usd"]
            >= buckets[i]["mean_realized_net_usd"]
            for i in range(len(buckets) - 1)),
        top_minus_bottom_mean_net_usd=C.rnd(
            buckets[-1]["mean_realized_net_usd"]
            - buckets[0]["mean_realized_net_usd"]),
        spearman_t_stat=(None if rho is None or abs(rho) >= 1 else C.rnd(
            rho * math.sqrt((n - 2) / (1 - rho * rho)), 6)))
    if rho is None:
        return metric(name, None, n=n, basis=basis, window=window,
                      why="SCORE_OR_REALIZED_NET_IS_CONSTANT_NO_RANK_ORDER",
                      detail=detail, min_n=CAL_MIN_N)
    return metric(name, rho, n=n, basis=basis, window=window, detail=detail,
                  min_n=CAL_MIN_N)


def false_refusal_metric(refusals: list, *, window: dict, sleeve,
                         since, source_why=None) -> dict:
    """`sleeve` None = BOOK-WIDE (every refusal, attributable or not)."""
    name = "FALSE_REFUSAL_RATE"
    basis = ("lol_ledger, latest classification per decision; sleeve by the "
             "refused decision's strategy" if sleeve else
             "lol_ledger, latest classification per decision; BOOK-WIDE: "
             "includes refusals with no strategy (not attributable to a "
             "sleeve)")
    if source_why:
        return unavailable(name, source_why, window=window, basis=basis)
    rows = [r for r in refusals
            if (since is None or (_num(r.get("decided_at")) or 0) >= since)
            and (sleeve is None or r.get("sleeve") == sleeve)]
    cnt = {k: sum(1 for r in rows if r.get("classification") == k)
           for k in ("FALSE_REFUSAL", "GOOD_REFUSAL", "UNKNOWABLE")}
    hyp = [_num(r.get("hypothetical_pnl_usd")) for r in rows
           if r.get("classification") == "FALSE_REFUSAL"]
    hyp = [v for v in hyp if v is not None]
    decided = cnt["FALSE_REFUSAL"] + cnt["GOOD_REFUSAL"]
    detail = {"false_refusals": cnt["FALSE_REFUSAL"],
              "good_refusals": cnt["GOOD_REFUSAL"],
              "unknowable": cnt["UNKNOWABLE"],
              "unknowable_excluded_from_rate": True,
              "false_refusal_hypothetical_pnl_usd": C.rnd(sum(hyp))
              if hyp else None,
              "hypothetical_label": "HYPOTHETICAL",
              "membership": "refusals decided at or after `since`"
              if since is not None else "every refusal"}
    if sleeve is None:
        detail["unattributed_refusals"] = sum(
            1 for r in rows if r.get("sleeve") is None)
    if not decided:
        return metric(name, None, n=0, basis=basis, window=window,
                      why=("NO_DECIDED_REFUSAL_ONLY_UNKNOWABLE"
                           if cnt["UNKNOWABLE"] else
                           "NO_CLASSIFIED_REFUSAL_IN_THE_WINDOW"),
                      detail=detail, min_n=RATE_MIN_N)
    return metric(name, cnt["FALSE_REFUSAL"] / decided, n=decided,
                  basis=basis, window=window, detail=detail,
                  min_n=RATE_MIN_N)


def execution_metric(outs: list, groups: set, *, window: dict,
                     source_why=None) -> dict:
    name = "EXECUTION_QUALITY_DELTA_PP"
    basis = ("eddie_execution_outcomes (PAPER) of the sleeve's entered "
             "decisions (paper_orders ENTRY -> group)")
    if source_why:
        return unavailable(name, source_why, window=window, basis=basis)
    rows = [o for o in outs if o.get("group_id") in groups]
    if not rows:
        return unavailable(name, "NO_EXECUTION_OUTCOME_FOR_THE_WINDOW'S_"
                           "POSITIONS", window=window, basis=basis)
    both = [(_num(o.get("realized_execution_loss_pp")),
             _num(o.get("predicted_execution_loss_pp")),
             _num(o.get("filled_qty"))) for o in rows]
    pred = [(r, p, q) for r, p, q in both if r is not None and p is not None]
    naive = [(_num(o.get("realized_execution_loss_pp")),
              _num(o.get("naive_execution_loss_pp"))) for o in rows]
    naive = [(r, b) for r, b in naive if r is not None and b is not None]
    detail = {
        "outcomes": len(rows), "with_realized_and_predicted": len(pred),
        "with_realized_and_naive": len(naive),
        "mean_realized_minus_naive_pp": C.rnd(
            C.mean([r - b for r, b in naive]), 9) if naive else None,
        "realized_minus_naive_why": None if naive else
        "NO_OUTCOME_CARRIES_A_NAIVE_EXECUTION_LOSS",
        "qty_weighted_realized_minus_predicted_usd": C.rnd(sum(
            (r - p) * q for r, p, q in pred if q is not None))
        if pred else None,
        "mean_abs_error_pp": C.rnd(C.mean([abs(r - p) for r, p, _ in pred]),
                                   9) if pred else None,
        "sign": "positive = execution cost MORE than predicted"}
    if not pred:
        return metric(name, None, n=0, basis=basis, window=window,
                      why="NO_OUTCOME_CARRIES_BOTH_REALIZED_AND_PREDICTED_"
                      "EXECUTION_LOSS", detail=detail, min_n=EXEC_MIN_N)
    return metric(name, C.mean([r - p for r, p, _ in pred]), n=len(pred),
                  basis=basis, window=window, detail=detail,
                  min_n=EXEC_MIN_N)


HOLD_KEY = "ACTUAL_XAVIER_minus_HOLD_TO_SETTLEMENT"
EXIT_KEY = "ACTUAL_XAVIER_minus_IMMEDIATE_EXIT"


def management_metric(vadd: list, groups: set, *, window: dict,
                      source_why=None) -> dict:
    name = "MANAGEMENT_VALUE_DELTA_USD"
    basis = ("xavier_value_add (PAPER), latest row per thesis, of the "
             "sleeve's groups; incrementals frozen on the entry thesis")
    if source_why:
        return unavailable(name, source_why, window=window, basis=basis)
    rows = [v for v in vadd if v.get("group_id") in groups]
    if not rows:
        return unavailable(name, "NO_VALUE_ADD_ROW_FOR_THE_WINDOW'S_"
                           "POSITIONS", window=window, basis=basis)

    def _inc(key):
        ok, na = [], 0
        for v in rows:
            inc = (v.get("incremental") or {}).get(key) or {}
            x = _num(inc.get("pnl_usd"))
            if inc.get("available") and x is not None:
                ok.append(x)
            else:
                na += 1
        return ok, na
    hold, hold_na = _inc(HOLD_KEY)
    ex, ex_na = _inc(EXIT_KEY)
    detail = {"theses": len(rows),
              "final": sum(1 for v in rows if v.get("status") == "FINAL"),
              "hold_incremental_unavailable": hold_na,
              "exit_incremental_available": len(ex),
              "exit_incremental_unavailable": ex_na,
              "actual_minus_immediate_exit_usd": C.rnd(sum(ex))
              if ex else None,
              "actual_minus_immediate_exit_mean_usd": C.rnd(C.mean(ex))
              if ex else None,
              "mean_actual_minus_hold_usd": C.rnd(C.mean(hold))
              if hold else None,
              "sign": "positive = Xavier's management added value"}
    if not hold:
        return metric(name, None, n=0, basis=basis, window=window,
                      why="HOLD_TO_SETTLEMENT_COUNTERFACTUAL_UNAVAILABLE_ON_"
                      "EVERY_ROW", detail=detail, min_n=MGMT_MIN_N)
    return metric(name, sum(hold), n=len(hold), basis=basis, window=window,
                  detail=detail, min_n=MGMT_MIN_N)


# ═════════════════════════════════════════════════════════════════════
# THE VERDICT (INVESTMENT, FORWARD)
# ═════════════════════════════════════════════════════════════════════

def event_key(p: dict) -> str:
    """The independent-outcome key of a position: its fixture, else its
    market, else its group (never pooled with another group by guess)."""
    return str(p.get("fixture") or p.get("us_market_slug")
               or "group:%s" % p.get("group_id"))


def verdict_rule(pos: list, *, since, cutover: float, seed: int = 0) -> dict:
    """THE PRE-DECLARED RULE over the INVESTMENT sleeve's FORWARD positions.
    `pos` must be exactly those positions (the caller scopes them)."""
    resolved = [p for p in pos if _resolved(p)
                and _num(p.get("realized_pnl_usd")) is not None]
    xs = [_num(p["realized_pnl_usd"]) for p in sorted(
        resolved, key=lambda p: _num(p.get("released_at")) or 0.0)]
    n = len(xs)
    net = sum(xs)
    # INDEPENDENT OUTCOMES: one observation per event, ordered by the event's
    # last release (its outcome is known only then)
    ev: dict = {}
    for p in resolved:
        k = event_key(p)
        e = ev.setdefault(k, {"net": 0.0, "released": 0.0})
        e["net"] += _num(p["realized_pnl_usd"])
        e["released"] = max(e["released"], _num(p.get("released_at")) or 0.0)
    es = [e["net"] for e in sorted(ev.values(), key=lambda e: e["released"])]
    n_ev = len(es)
    open_ = [p for p in pos if not _resolved(p)]
    open_realized = sum(_num(p.get("realized_pnl_usd")) or 0.0
                        for p in open_)
    marked_unreal = sum(_num(p.get("unrealized_pnl_usd")) or 0.0
                        for p in open_ if p.get("marked"))
    lb = mean_lower_bounds(es, seed=seed) if n_ev else {
        "t_lower_95": None, "bootstrap_lower_95": None, "mean": None,
        "why": R_NO_RESOLVED}
    dd = C.max_drawdown(es) if es else None
    forward_only = (since is not None and cutover is not None
                    and since >= cutover)
    checks = {
        "MIN_RESOLVED": n >= VERDICT_MIN_RESOLVED,
        "MIN_INDEPENDENT_EVENTS": n_ev >= VERDICT_MIN_INDEPENDENT_EVENTS,
        "REALIZED_NET_AFTER_FEES_POSITIVE": n > 0 and net > 0,
        "T_LOWER_BOUND_POSITIVE": (lb.get("t_lower_95") is not None
                                   and lb["t_lower_95"] > 0),
        "BOOTSTRAP_LOWER_BOUND_POSITIVE": (
            lb.get("bootstrap_lower_95") is not None
            and lb["bootstrap_lower_95"] > 0),
        "DRAWDOWN_WITHIN_BOUND": dd is not None and dd <= DRAWDOWN_BOUND_USD,
        "NET_INCLUDING_MARKED_OPEN_POSITIVE":
            n > 0 and (net + open_realized + marked_unreal) > 0,
        "FORWARD_ONLY": forward_only,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if n == 0:
        v, why = NOT_ESTABLISHED, "NO_RESOLVED_FORWARD_INVESTMENT_POSITION"
    elif net < 0:
        v, why = NEGATIVE, "FORWARD_REALIZED_NET_AFTER_FEES_IS_NEGATIVE"
    elif net == 0:
        v, why = NOT_ESTABLISHED, "FORWARD_REALIZED_NET_IS_ZERO"
    elif not failed:
        v, why = SUPPORTED, None
    else:
        v, why = POSITIVE_BUT_INSUFFICIENT, "FAILED_CHECKS: " + ",".join(
            failed)
    assert v != SUPPORTED or not failed      # never SUPPORTED without it
    return {
        "verdict": v, "why": why, "sleeve": INVESTMENT, "window": FORWARD,
        "since": since, "cutover": cutover, "checks": checks,
        "failed_checks": failed, "rule": VERDICT_RULE,
        "evidence": {
            "resolved_positions": n, "independent_resolved_events": n_ev,
            "min_independent_events": VERDICT_MIN_INDEPENDENT_EVENTS,
            "bounds_basis": "per-EVENT realized net (independent outcomes)",
            "open_positions": len(open_),
            "resolved_net_usd": C.rnd(net) if n else None,
            "realized_on_open_positions_usd": C.rnd(open_realized),
            "marked_unrealized_usd": C.rnd(marked_unreal),
            "unmarked_open_positions": sum(
                1 for p in open_ if not p.get("marked")),
            "mean_net_per_event_usd": lb.get("mean"),
            "t_lower_95": lb.get("t_lower_95"),
            "bootstrap_lower_95": lb.get("bootstrap_lower_95"),
            "max_drawdown_usd": C.rnd(dd),
            "drawdown_bound_usd": DRAWDOWN_BOUND_USD,
            "min_resolved": VERDICT_MIN_RESOLVED},
        "claim": ("the INVESTMENT policy is profitable on forward paper "
                  "evidence" if v == SUPPORTED else
                  "NO profitability claim: the forward evidence does not "
                  "establish it"),
    }


# ═════════════════════════════════════════════════════════════════════
# THE WHOLE REPORT
# ═════════════════════════════════════════════════════════════════════

def sleeve_window(data: dict, sleeve: str, kind: str, *, since,
                  now: float, sources: dict) -> dict:
    positions = data.get("positions") or []
    groups = scope_groups(positions, sleeve,
                          since if kind == FORWARD else None)
    pos = [p for p in positions if p.get("group_id") in groups]
    win = window_of(kind, since=since, now=now, positions=positions,
                    groups=groups)
    seed = C.seed_of([VERSION, sleeve, kind, sorted(
        (p.get("position_key") or "", _num(p.get("realized_pnl_usd")))
        for p in pos)])
    m = ledger_metrics(pos, window=win, seed=seed)
    m.update(capital_metrics(pos, data.get("econ") or [], window=win,
                             source_why=sources.get("econ")))
    m["SLIPPAGE_USD"] = slippage_metric(
        data.get("attribution") or [], groups, window=win,
        source_why=sources.get("attribution"))
    m["OPPORTUNITY_SCORE_CALIBRATION"] = calibration_metric(
        data.get("scores") or [], pos, groups, window=win,
        source_why=sources.get("scores"))
    m["FALSE_REFUSAL_RATE"] = false_refusal_metric(
        data.get("refusals") or [], window=win, sleeve=sleeve,
        since=since if kind == FORWARD else None,
        source_why=sources.get("refusals"))
    m["EXECUTION_QUALITY_DELTA_PP"] = execution_metric(
        data.get("exec_outcomes") or [], groups, window=win,
        source_why=sources.get("exec_outcomes"))
    m["MANAGEMENT_VALUE_DELTA_USD"] = management_metric(
        data.get("value_add") or [], groups, window=win,
        source_why=sources.get("value_add"))
    for v in m.values():
        v["sleeve"] = sleeve
    return {"window": win, "positions": len(pos), "groups": len(groups),
            "metrics": {k: m[k] for k in METRICS}}


SLEEVE_ROLE = {
    INVESTMENT: "PRODUCTION_CANDIDATE: the only sleeve whose FORWARD "
                "evidence counts toward live activation",
    TRAINING: "EXPLORATION: a loss is RESEARCH COST, a win is NOT "
              "production alpha; never activation evidence",
    BENCHMARK: "CONTROL: never activation evidence",
    UNCLASSIFIED: "NOT ESTABLISHED: never counted as INVESTMENT, never "
                  "activation evidence",
}


def compute(data: dict, *, now: float, since, cutover: float,
            since_source: str = "", sources: dict | None = None,
            build_logic: dict | None = None) -> dict:
    """`data`: {positions, econ, attribution, scores, refusals,
    exec_outcomes, value_add} (see the module docstring for each row's
    keys); `sources`: {name: reason the source is unavailable} for the
    sources that cannot be read; `build_logic`: decision_logic.
    build_logic_check for the serving build against the effective cutover
    (R30A review: when the serving build's decision logic has no cutover,
    the forward window would silently span two logics, so no verdict is
    established -- CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER). Pure."""
    sources = sources or {}
    fsince = forward_since(since, cutover)
    no_cutover = fsince is None
    # no production cutover -> an EMPTY forward window (a start after now),
    # never "everything"
    since = (float(now) + 1.0) if no_cutover else fsince
    sleeves = {}
    for s in SLEEVES:
        sleeves[s] = {
            "sleeve": s, "role": SLEEVE_ROLE[s],
            "activation_evidence": s == INVESTMENT,
            "windows": {k: sleeve_window(data, s, k, since=since, now=now,
                                         sources=sources)
                        for k in WINDOWS}}
    positions = data.get("positions") or []
    inv_groups = scope_groups(positions, INVESTMENT, since)
    inv_pos = [p for p in positions if p.get("group_id") in inv_groups]
    seed = C.seed_of([VERSION, "VERDICT", sorted(
        (p.get("position_key") or "", _num(p.get("realized_pnl_usd")))
        for p in inv_pos)])
    verdict = verdict_rule(inv_pos, since=since, cutover=cutover, seed=seed)
    if no_cutover:
        verdict = dict(verdict, verdict=NOT_ESTABLISHED, why=R_NO_CUTOVER,
                       claim=("NO profitability claim: no production cutover "
                              "is recorded, so no forward evidence exists"))
    elif build_logic is not None and not build_logic.get("matches"):
        verdict = dict(verdict, verdict=NOT_ESTABLISHED,
                       why=build_logic.get("refusal")
                       or "CURRENT_BUILD_LOGIC_HAS_NO_CUTOVER",
                       claim=("NO profitability claim: the serving build "
                              "decides with decision logic no recorded "
                              "cutover names, so the forward window would "
                              "span two decision logics"),
                       verdict_before_the_logic_check=verdict.get("verdict"))
    book = {k: false_refusal_metric(
        data.get("refusals") or [],
        window={"kind": k, "start": since if k == FORWARD else None,
                "end": now, "since": since if k == FORWARD else None},
        sleeve=None, since=since if k == FORWARD else None,
        source_why=sources.get("refusals")) for k in WINDOWS}
    return {
        "version": VERSION, "computed_at": now,
        "since": None if no_cutover else since,
        "since_source": since_source, "cutover": cutover,
        "build_logic": build_logic,
        "cutover_basis": ("live_parity_effective_cutover.cutover_at (the "
                          "latest release whose decision-logic hash changed; "
                          "each release recorded after every production "
                          "condition was verified)"
                          if not no_cutover else R_NO_CUTOVER),
        "forward_rule": ("FORWARD = positions whose group first filled at or "
                         "after `since`; refusals decided at or after it"),
        "sleeves": sleeves,
        "book_wide": {"FALSE_REFUSAL_RATE": book,
                      "note": ("book-wide figures include refusals that "
                               "carry no strategy and so cannot be "
                               "attributed to a sleeve")},
        "profitability_verdict": verdict,
        "activation_evidence": {
            "sleeve": INVESTMENT, "window": FORWARD,
            "rule": ("live activation evidence comes ONLY from the "
                     "INVESTMENT sleeve's FORWARD window; TRAINING, "
                     "BENCHMARK and UNCLASSIFIED are never pooled into it")},
        "sources_unavailable": dict(sources),
        "metric_units": dict(UNITS),
    }
