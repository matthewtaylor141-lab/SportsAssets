"""THE PROFITABILITY BIND: EVERY PROFITABILITY ITEM, PRODUCTION-ACTIVE IN PAPER
ENTRY AND MANAGEMENT DECISIONS (migration 309).

PAPER ONLY. Nothing here raises a cap, a size, a risk limit or a live
authority, and nothing here reaches a venue. Every function below can only
REFUSE a paper entry or SHRINK its quantity; none admits what another gate
refused, none changes a lifecycle state (promotion stays with a named
person, bettor_strategy_lifecycle.transition), and SMALL LIVE stays SHADOW
(nothing here is imported by a live path). Historical PAPER P&L is read,
never rewritten.

WHERE IT IS BOUND (each a test in tests/test_profitability_bind.py):

  * bettor_paper_ledger.submit_order -> bettor_capital_authority.
    ledger_entry_authority, UNDER THE ACCOUNT LOCK, for EVERY strategy's
    ENTRY BUY (Derek, the benchmarks, the maker, exploration): `entry_bind`
    (items 1-3, 6) then the regime / champion authority (items 5, 8) inside
    `bettor_capital_authority.authority`; the ledger reserves only the
    bound (smaller or equal) quantity.
  * agents.paper_derek.capital_gate (Derek and the benchmark policies), at
    the decision: the same `entry_bind` and authority, so the decision's
    recorded quantity / refusal / shadow are the bound ones.
  * agents.paper_xavier.review_group -> `alternatives`: HOLD / EXIT / REDUCE
    valued on the same calibrated probability and residual haircut, every
    sale after its fees (item 4).
  * agents.paper_runtime: `step` (fits the learned inputs, item 8) and
    `cash_step` (the explicit CASH decision, item 7) run in every pass.
  * bettor_capital_authority.acceptance_view: the per-strategy state and the
    complete marks / management reconciliation (item 9).

THE ITEMS:

 1 CALIBRATION. The entry probability is passed through a calibration keyed
   by SPORT x MARKET FAMILY x REGIME (regime = IN_PLAY or the time-to-start
   bucket), fitted on SETTLED outcomes of the account's own priced
   decisions and settled shadow counterfactuals (one observation per
   contract / side / regime). With n observations in the probability's bin
   the calibrated probability is
       p_cal = w (p_raw + (observed - predicted in the bin))
               + (1 - w) market,     w = n_bin / (n_bin + K_CALIBRATION)
   (and w scaled down by n_cell / MIN_CELL_OBSERVATIONS below the cell
   minimum), where `market` is the best executable price -- so no data
   means the market price itself and the entry is CASH. The probability
   used is min(p_raw, p_cal): calibration NEVER INFLATES.
 2 ALL-IN EXECUTABLE NET EV, on the walked fills at the order's limit:
       filled x p_used - walked cost - per-level fees
       - max(IOC worst-case bound, LEARNED post-fill markout) per contract
       - the residual haircut per contract
   and its EXPECTED value = learned fill probability x that. Must be > 0.
   The learned components (markout, fill probability: EXECUTION model, from
   the simulator's own fills and terminal orders) can only add a cost: the
   markout charged is never below the existing bound.
 3 CHURN. A re-entry into a contract the strategy exited within
   REENTRY_COOLDOWN_S, a contract of a fixture it exited within
   FIXTURE_COOLDOWN_S, or a contract the bind refused within
   REFUSAL_COOLDOWN_S is refused unless the all-in EV per contract improved
   MATERIALLY (>= MATERIAL_EV_IMPROVEMENT_USD and x MATERIAL_EV_RATIO) over
   the last recorded evaluation of that contract; a strategy may open at
   most MAX_ENTRIES_PER_HOUR entries per rolling hour (turnover cap).
 5 REGIME AUTHORITY (in bettor_capital_authority.authority). A strategy gets
   capital only in a regime where its regime-keyed forward evidence (the
   same rule as the strategy-level forward economics, restricted to
   observations in that regime) is POSITIVE; an UNKNOWN regime (no venue
   start time) is CASH / shadow.
 6 CAPITAL-HOUR, CAPACITY, CORRELATION (size, shrink only):
     capacity frontier  the walk stops at the last level whose MARGINAL
                        contract still has all-in EV > 0, and at
                        MAX_DEPTH_FRACTION of the displayed executable depth
     capital-hour       EV per capital-hour = EV / (capital x expected hold
                        hours); below MIN_EV_PER_CAPITAL_HOUR refused; size
                        factor min(1, EVCH / TARGET_EV_PER_CAPITAL_HOUR)
     correlation        each open position / live entry of the account on
                        the same fixture halves the size; MAX_CORRELATED_
                        SAME_FIXTURE of them refuses
   The bound quantity never exceeds the policy's, the lifecycle's or the
   ledger caps' (they are applied unchanged, after).
 7 CASH. `cash_step` records an explicit CASH decision (paper_cash_
   decisions) for every strategy that evaluated candidates in a pass and
   entered none, with the binding refusals and the best refused candidate.
 8 LEARNING. Shadow counterfactuals feed the calibration and the residuals.
   ABSOLUTE-POSITIVE CHAMPION RULE (in authority): capital needs forward
   net P&L > 0 AND its 95% CI lower bound > 0 -- beating the other
   strategies is not enough. RESIDUALS: expected EV at the decision vs the
   realized (or settled counterfactual) result per position, aggregated by
   strategy x sport x family; a negative mean residual becomes a
   per-contract EV haircut (a positive one is never credited).

Fail-closed: an unreadable input refuses (R_BIND_UNREADABLE).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import time
from typing import Any

VERSION = "PAPER_PROFITABILITY_BIND_V1"
AUTHORITY = "PAPER_ONLY_NO_CAPITAL_AUTHORITY"

# ── descriptors ──────────────────────────────────────────────────────
IN_PLAY = "IN_PLAY"
PRE_LT_1H = "PRE_GAME_LT_1H"
PRE_1H_24H = "PRE_GAME_1H_TO_24H"
PRE_GT_24H = "PRE_GAME_GT_24H"
REGIME_UNKNOWN = "UNKNOWN"
REGIMES = (PRE_GT_24H, PRE_1H_24H, PRE_LT_1H, IN_PLAY)
MONEYLINE, SPREAD, TOTAL, TEAM_TOTAL = ("MONEYLINE", "SPREAD", "TOTAL",
                                        "TEAM_TOTAL")
FAMILY_OTHER, UNKNOWN = "OTHER", "UNKNOWN"
#: a game's typical length (h) to settlement after its start, by sport;
#: the capital-hour denominator only (never a size)
GAME_HOURS = {"football": 3.5, "basketball": 2.5, "hockey": 2.75,
              "baseball": 3.25, "soccer": 2.0, "tennis": 2.5}
DEFAULT_GAME_HOURS = 3.5

# ── 1 calibration ────────────────────────────────────────────────────
CAL_BINS = (0.0, 0.2, 0.35, 0.45, 0.55, 0.65, 0.8, 1.0)
K_CALIBRATION = 50.0
MIN_CELL_OBSERVATIONS = 30
FIT_WINDOW_DAYS = 120.0
FIT_DECISIONS_LIMIT = 20000
CAL_MEASURED, CAL_INSUFFICIENT, CAL_NO_DATA = ("MEASURED", "INSUFFICIENT",
                                               "NO_DATA")

# ── 2 execution economics ────────────────────────────────────────────
MAKER, TAKER = "MAKER", "TAKER"
MARKOUT_HORIZON_S = 300.0
MARKOUT_TOLERANCE_S = 300.0
FILL_BOOK_MAX_S = 30.0
K_EXECUTION = 10.0
#: conservative fill-probability priors (orders' filled fraction)
FILL_PRIOR = {TAKER: 0.5, MAKER: 0.2}
MIN_EXECUTION_FILLS = 5

# ── 3 churn ──────────────────────────────────────────────────────────
REENTRY_COOLDOWN_S = 3600.0
FIXTURE_COOLDOWN_S = 1800.0
REFUSAL_COOLDOWN_S = 900.0
MATERIAL_EV_IMPROVEMENT_USD = 0.01        # per contract
MATERIAL_EV_RATIO = 1.25
MAX_ENTRIES_PER_HOUR = 12

# ── 5 regime authority / 8 champion ──────────────────────────────────
MIN_REGIME_OBSERVATIONS = 20

# ── 6 sizing (shrink only) ───────────────────────────────────────────
MAX_DEPTH_FRACTION = 0.5
MIN_EV_PER_CAPITAL_HOUR = 0.0001          # $ per $ of capital per hour
TARGET_EV_PER_CAPITAL_HOUR = 0.001
MAX_CORRELATED_SAME_FIXTURE = 3
CORRELATION_SIZE_DECAY = 0.5

# ── 8 residuals ──────────────────────────────────────────────────────
K_RESIDUAL = 20.0
MIN_RESIDUAL_OBSERVATIONS = 5

FIT_EVERY_S = 600.0

R_CALIBRATED_EV_NOT_POSITIVE = "CASH_WAIT_CALIBRATED_ALL_IN_EV_NOT_POSITIVE"
R_ALL_IN_EV_NOT_POSITIVE = "CASH_WAIT_ALL_IN_EXECUTABLE_EV_NOT_POSITIVE"
R_CAPACITY_NONE = "CASH_WAIT_CAPACITY_FRONTIER_NO_POSITIVE_MARGINAL_CONTRACT"
R_CAPITAL_HOUR_BELOW_FLOOR = "CASH_WAIT_EV_PER_CAPITAL_HOUR_BELOW_FLOOR"
R_CORRELATED_EXPOSURE = "CASH_WAIT_CORRELATED_FIXTURE_EXPOSURE_AT_LIMIT"
R_SIZE_BELOW_ONE = "CASH_WAIT_BOUND_SIZE_BELOW_ONE_CONTRACT"
R_CHURN_RECENT_EXIT = "CHURN_REENTRY_AFTER_RECENT_EXIT_WITHOUT_MATERIAL_EV_GAIN"
R_CHURN_FIXTURE_EXIT = ("CHURN_FIXTURE_REENTRY_AFTER_RECENT_EXIT_WITHOUT_"
                        "MATERIAL_EV_GAIN")
R_CHURN_RECENT_REFUSAL = ("CHURN_REENTRY_AFTER_RECENT_REFUSAL_WITHOUT_"
                          "MATERIAL_EV_GAIN")
R_TURNOVER_CAP = "CHURN_STRATEGY_TURNOVER_CAP_PER_HOUR_REACHED"
R_NO_HOLD_ESTIMATE = "CASH_WAIT_NO_EXPECTED_HOLD_FOR_CAPITAL_HOUR"
R_BIND_UNREADABLE = "PROFITABILITY_BIND_UNREADABLE"
R_BIND_NO_EVIDENCE = "PROFITABILITY_BIND_NO_EXECUTABLE_EVIDENCE"
# shadow-routable (no capital authority): see bettor_capital_authority
R_REGIME_UNKNOWN = "CASH_WAIT_REGIME_UNKNOWN_SHADOW_ONLY"
R_REGIME_NOT_POSITIVE = "CASH_WAIT_REGIME_FORWARD_ECONOMICS_NOT_POSITIVE"
R_NOT_ABSOLUTE_CHAMPION = ("CASH_WAIT_FORWARD_PNL_NOT_ABSOLUTELY_POSITIVE_"
                           "CI_LOW_NOT_ABOVE_ZERO")
R_REGIME_UNREADABLE = "REGIME_FORWARD_ECONOMICS_UNREADABLE"

#: economic refusals of the bind (never a shadow: the decision itself
#: failed the economics, not merely the capital authority)
REFUSALS = (R_CALIBRATED_EV_NOT_POSITIVE, R_ALL_IN_EV_NOT_POSITIVE,
            R_CAPACITY_NONE, R_CAPITAL_HOUR_BELOW_FLOOR,
            R_CORRELATED_EXPOSURE, R_SIZE_BELOW_ONE, R_CHURN_RECENT_EXIT,
            R_CHURN_FIXTURE_EXIT, R_CHURN_RECENT_REFUSAL, R_TURNOVER_CAP,
            R_NO_HOLD_ESTIMATE, R_BIND_UNREADABLE, R_BIND_NO_EVIDENCE)
#: no-capital-authority refusals the bind adds to the authority (a shadow
#: counterfactual is recorded when everything else passed)
AUTHORITY_REFUSALS = (R_REGIME_UNKNOWN, R_REGIME_NOT_POSITIVE,
                      R_NOT_ABSOLUTE_CHAMPION)
ALL_REFUSALS = REFUSALS + AUTHORITY_REFUSALS + (R_REGIME_UNREADABLE,)

T_MODELS = "paper_profitability_models"
T_EVAL = "paper_profitability_evaluations"
T_CASH = "paper_cash_decisions"


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v, n=9):
    return None if v is None else round(float(v), n)


def _ts(epoch: float):
    return _dt.datetime.fromtimestamp(float(epoch), _dt.timezone.utc)


def _epoch(v):
    if v is None:
        return None
    return float(v.timestamp()) if hasattr(v, "timestamp") else float(v)


def _j(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


# ═════════════════════════════════════════════════════════════════════
# DESCRIPTORS (pure)
# ═════════════════════════════════════════════════════════════════════

def family_of(sports_type) -> str:
    st = str(sports_type or "").strip().lower()
    if not st:
        return UNKNOWN
    if st.endswith(("_winner", "_moneyline")) or st == "moneyline":
        return MONEYLINE
    try:
        from . import bettor_market_family as MF
        hit = MF.venue_line_family(st)
        if hit.get("family"):
            return {"spread": SPREAD, "total": TOTAL,
                    "team_total": TEAM_TOTAL}[hit["family"]]
    except Exception:                                           # noqa: BLE001
        pass
    if "spread" in st:
        return SPREAD
    if "team_total" in st or "total_runs" in st or "total_goals" in st \
            or "points_full_game_total" in st:
        return TEAM_TOTAL
    if "total" in st:
        return TOTAL
    return FAMILY_OTHER


def sport_of(sports_type, event_slug=None) -> str:
    st = str(sports_type or "").strip().lower()
    if st:
        return st.split("_", 1)[0]
    es = str(event_slug or "").strip().lower()
    if "-" in es:
        return es.split("-", 1)[0]
    return UNKNOWN


def regime_of(game_start, at) -> str:
    gs, t = _num(game_start), _num(at)
    if gs is None or t is None:
        return REGIME_UNKNOWN
    if t >= gs:
        return IN_PLAY
    left = gs - t
    if left < 3600.0:
        return PRE_LT_1H
    if left < 86400.0:
        return PRE_1H_24H
    return PRE_GT_24H


def expected_hold_hours(*, sport, game_start, at) -> float | None:
    """Hours of capital use to settlement: to the start, plus the game's
    typical length (half of it once in play). None without a start."""
    gs, t = _num(game_start), _num(at)
    if gs is None or t is None:
        return None
    g = GAME_HOURS.get(str(sport or ""), DEFAULT_GAME_HOURS)
    if t >= gs:
        return max(0.25, g / 2.0)
    return (gs - t) / 3600.0 + g


def descriptor(meta: dict | None, *, at: float) -> dict:
    m = meta or {}
    sport = sport_of(m.get("sports_type"), m.get("event_slug"))
    fam = family_of(m.get("sports_type"))
    gs = _num(m.get("game_start_epoch"))
    return {"sport": sport, "family": fam, "regime": regime_of(gs, at),
            "game_start": gs, "event_slug": m.get("event_slug"),
            "expected_hold_hours": expected_hold_hours(sport=sport,
                                                       game_start=gs, at=at),
            "basis": ("us_premap (sports_type, event_slug, game_start)"
                      if m else "NO_CATALOGUE_ROW")}


def cell_key(sport, family, regime) -> str:
    return "%s|%s|%s" % (sport or UNKNOWN, family or UNKNOWN,
                         regime or REGIME_UNKNOWN)


PREMAP_SQL = (
    "SELECT DISTINCT ON (market_slug) market_slug, event_slug, sports_type, "
    "       extract(epoch FROM game_start)::float8 AS game_start_epoch "
    "  FROM us_premap WHERE market_slug = ANY($1::text[]) "
    " ORDER BY market_slug, (game_start IS NULL), updated_at DESC")


async def premap(conn, slugs) -> dict:
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    return {r["market_slug"]: dict(r) for r in await conn.fetch(PREMAP_SQL,
                                                                slugs)}


# ═════════════════════════════════════════════════════════════════════
# 1 · CALIBRATION (pure)
# ═════════════════════════════════════════════════════════════════════

def _bin(p: float) -> int:
    for i in range(len(CAL_BINS) - 1):
        if p < CAL_BINS[i + 1] or i == len(CAL_BINS) - 2:
            return i
    return len(CAL_BINS) - 2


def fit_calibration(observations: list) -> dict:
    """observations: [{sport, family, regime, p, y}] with y in [0, 1] the
    settled payout of the held side. Per cell: n, predicted and observed
    rate, Brier score, and per probability bin n / mean p / observed."""
    cells: dict = {}
    for o in observations or []:
        p, y = _num(o.get("p")), _num(o.get("y"))
        if p is None or y is None or not 0.0 <= p <= 1.0:
            continue
        k = cell_key(o.get("sport"), o.get("family"), o.get("regime"))
        c = cells.setdefault(k, {"n": 0, "sum_p": 0.0, "sum_y": 0.0,
                                 "brier": 0.0, "bins": {}})
        c["n"] += 1
        c["sum_p"] += p
        c["sum_y"] += y
        c["brier"] += (p - y) ** 2
        b = c["bins"].setdefault(str(_bin(p)), {"n": 0, "sum_p": 0.0,
                                                "sum_y": 0.0})
        b["n"] += 1
        b["sum_p"] += p
        b["sum_y"] += y
    out = {}
    for k, c in cells.items():
        n = c["n"]
        out[k] = {"n": n, "mean_p": _r(c["sum_p"] / n),
                  "observed": _r(c["sum_y"] / n),
                  "brier": _r(c["brier"] / n),
                  "status": (CAL_MEASURED if n >= MIN_CELL_OBSERVATIONS
                             else CAL_INSUFFICIENT),
                  "bins": {i: {"lo": CAL_BINS[int(i)],
                               "hi": CAL_BINS[int(i) + 1], "n": b["n"],
                               "mean_p": _r(b["sum_p"] / b["n"]),
                               "observed": _r(b["sum_y"] / b["n"])}
                           for i, b in sorted(c["bins"].items())}}
    return {"version": VERSION, "kind": "CALIBRATION",
            "key": "sport|family|regime", "bins": list(CAL_BINS),
            "k_calibration": K_CALIBRATION,
            "min_cell_observations": MIN_CELL_OBSERVATIONS,
            "observations": sum(c["n"] for c in out.values()),
            "cells": out}


def calibrate(model: dict | None, *, sport, family, regime, p_raw,
              market) -> dict:
    """THE PROBABILITY USED FOR EV: min(p_raw, p_cal) (module docstring).
    No model / no cell => the market price (CASH by construction)."""
    p = _num(p_raw)
    mkt = _num(market)
    key = cell_key(sport, family, regime)
    cell = ((model or {}).get("cells") or {}).get(key)
    if p is None:
        return {"key": key, "p_raw": None, "p_used": None,
                "status": CAL_NO_DATA, "weight": 0.0}
    n_cell = int((cell or {}).get("n") or 0)
    b = ((cell or {}).get("bins") or {}).get(str(_bin(p))) or {}
    n_bin = int(b.get("n") or 0)
    bias = ((_num(b.get("observed")) or 0.0) - (_num(b.get("mean_p")) or 0.0)
            if n_bin else 0.0)
    w = n_bin / (n_bin + K_CALIBRATION) if n_bin else 0.0
    if n_cell < MIN_CELL_OBSERVATIONS:
        w *= n_cell / float(MIN_CELL_OBSERVATIONS)
    anchor = mkt if mkt is not None else 0.0
    p_cal = w * min(1.0, max(0.0, p + bias)) + (1.0 - w) * anchor
    p_cal = min(1.0, max(0.0, p_cal))
    used = min(p, p_cal)
    status = (CAL_NO_DATA if n_cell == 0 else
              CAL_MEASURED if n_cell >= MIN_CELL_OBSERVATIONS
              else CAL_INSUFFICIENT)
    return {"key": key, "p_raw": _r(p), "p_calibrated": _r(p_cal),
            "p_used": _r(used), "market": _r(mkt), "weight": _r(w, 6),
            "bin_bias": _r(bias), "n_cell": n_cell, "n_bin": n_bin,
            "status": status, "inflated": False,
            "rule": "min(p_raw, w (p_raw + bias) + (1 - w) market)"}


# ═════════════════════════════════════════════════════════════════════
# 2 · EXECUTION ECONOMICS AND 8 · RESIDUALS (pure fits)
# ═════════════════════════════════════════════════════════════════════

def style_of(order_type) -> str:
    return MAKER if str(order_type or "").upper() == "RESTING" else TAKER


def fit_execution(fills: list, orders: list) -> dict:
    """fills: [{strategy, style, qty, markout_per_contract}] (held-side mid
    at the fill minus the held-side mid MARKOUT_HORIZON_S later; positive =
    moved against the fill). orders: [{strategy, style, qty, filled_qty}]
    terminal ENTRY orders. Per (strategy, style): the markout shrunk toward
    the pool (K_EXECUTION fills), the fill probability shrunk toward the
    conservative prior (K_EXECUTION orders)."""
    xs = [(f["markout_per_contract"], f["qty"]) for f in fills or []
          if _num(f.get("markout_per_contract")) is not None
          and (_num(f.get("qty")) or 0) > 0]
    qpool = sum(q for _, q in xs)
    pool = (sum(m * q for m, q in xs) / qpool) if qpool else None
    groups: dict = {}
    for f in fills or []:
        if _num(f.get("markout_per_contract")) is None:
            continue
        g = groups.setdefault((str(f.get("strategy")), f.get("style")),
                              {"fills": [], "orders": []})
        g["fills"].append(f)
    for o in orders or []:
        q = _num(o.get("qty")) or 0.0
        if q <= 0:
            continue
        g = groups.setdefault((str(o.get("strategy")), o.get("style")),
                              {"fills": [], "orders": []})
        g["orders"].append(o)
    rows = {}
    for (s, st), g in sorted(groups.items()):
        fq = sum(float(f["qty"]) for f in g["fills"])
        own = (sum(float(f["markout_per_contract"]) * float(f["qty"])
                   for f in g["fills"]) / fq) if fq else None
        n = len(g["fills"])
        shrunk = (None if pool is None and own is None else
                  ((own or 0.0) * n + K_EXECUTION * (pool if pool is not None
                                                     else (own or 0.0)))
                  / (n + K_EXECUTION))
        fr = [min(1.0, float(o.get("filled_qty") or 0) / float(o["qty"]))
              for o in g["orders"]]
        prior = FILL_PRIOR.get(st, 0.2)
        pf = (sum(fr) + K_EXECUTION * prior) / (len(fr) + K_EXECUTION)
        rows["%s|%s" % (s, st)] = {
            "strategy": s, "style": st, "fills": n,
            "markout_per_contract_raw": _r(own),
            "markout_per_contract_shrunk": _r(shrunk),
            "terminal_orders": len(fr),
            "fill_rate_raw": _r(sum(fr) / len(fr)) if fr else None,
            "fill_probability": _r(pf, 6),
            "status": ("MEASURED" if n >= MIN_EXECUTION_FILLS
                       else "INSUFFICIENT")}
    return {"version": VERSION, "kind": "EXECUTION",
            "horizon_s": MARKOUT_HORIZON_S, "k_execution": K_EXECUTION,
            "fill_priors": FILL_PRIOR, "pooled_markout_per_contract": _r(pool),
            "observations": len(xs), "by_strategy_style": rows}


def execution_terms(model: dict | None, *, strategy, style) -> dict:
    """The learned markout (>= 0 charged) and fill probability for one
    strategy / style; absent => the pool / the conservative prior."""
    m = model or {}
    row = (m.get("by_strategy_style") or {}).get("%s|%s" % (strategy, style))
    if row:
        mk = _num(row.get("markout_per_contract_shrunk"))
        pf = _num(row.get("fill_probability"))
        basis = "LEARNED_%s" % row.get("status")
    else:
        mk = _num(m.get("pooled_markout_per_contract"))
        pf = None
        basis = "POOLED" if mk is not None else "PRIOR_ONLY"
    if pf is None:
        pf = FILL_PRIOR.get(style, 0.2)
    return {"learned_adverse_per_contract": _r(max(0.0, mk or 0.0)),
            "fill_probability": _r(min(1.0, max(0.0, pf)), 6),
            "basis": basis}


def fit_residuals(rows: list) -> dict:
    """rows: [{strategy, sport, family, expected_ev_usd, realized_pnl_usd,
    qty, source}] -- realized minus expected per position (PAPER) or per
    settled filled shadow (SHADOW). Aggregated per strategy x sport x
    family and per strategy."""
    cells: dict = {}
    for r in rows or []:
        e, x, q = (_num(r.get("expected_ev_usd")),
                   _num(r.get("realized_pnl_usd")), _num(r.get("qty")))
        if e is None or x is None or not q or q <= 0:
            continue
        for k in ("%s|%s|%s" % (r.get("strategy"), r.get("sport"),
                                r.get("family")), "%s|*|*" % r.get("strategy")):
            c = cells.setdefault(k, {"n": 0, "expected": 0.0, "realized": 0.0,
                                     "qty": 0.0, "paper": 0, "shadow": 0})
            c["n"] += 1
            c["expected"] += e
            c["realized"] += x
            c["qty"] += q
            c["paper" if r.get("source") == "PAPER" else "shadow"] += 1
    out = {}
    for k, c in cells.items():
        res = c["realized"] - c["expected"]
        out[k] = {"n": c["n"], "paper": c["paper"], "shadow": c["shadow"],
                  "expected_ev_usd": _r(c["expected"], 6),
                  "realized_pnl_usd": _r(c["realized"], 6),
                  "residual_usd": _r(res, 6),
                  "residual_per_contract": _r(res / c["qty"])}
    return {"version": VERSION, "kind": "RESIDUAL",
            "k_residual": K_RESIDUAL,
            "min_observations": MIN_RESIDUAL_OBSERVATIONS,
            "observations": sum(1 for r in rows or []
                                if _num(r.get("expected_ev_usd")) is not None
                                and _num(r.get("realized_pnl_usd"))
                                is not None),
            "cells": out}


def residual_haircut(model: dict | None, *, strategy, sport, family) -> dict:
    """max(0, -mean residual per contract) x n / (n + K_RESIDUAL), from the
    most specific cell with MIN_RESIDUAL_OBSERVATIONS; never a credit."""
    cells = (model or {}).get("cells") or {}
    for k in ("%s|%s|%s" % (strategy, sport, family), "%s|*|*" % strategy):
        c = cells.get(k)
        if c and int(c.get("n") or 0) >= MIN_RESIDUAL_OBSERVATIONS:
            n = int(c["n"])
            rpc = _num(c.get("residual_per_contract")) or 0.0
            h = max(0.0, -rpc) * n / (n + K_RESIDUAL)
            return {"haircut_per_contract": _r(h), "cell": k, "n": n,
                    "residual_per_contract": _r(rpc),
                    "credited": False}
    return {"haircut_per_contract": 0.0, "cell": None, "n": 0,
            "credited": False, "why": "NO_CELL_WITH_MINIMUM_OBSERVATIONS"}


# ═════════════════════════════════════════════════════════════════════
# 2 + 6 · ALL-IN EV, CAPACITY FRONTIER, CAPITAL-HOUR, CORRELATION (pure)
# ═════════════════════════════════════════════════════════════════════

def all_in(*, fills, p_used, fee_fn, adverse_per_contract,
           haircut_per_contract, fill_probability, qty=None) -> dict:
    """The all-in executable EV of walking `fills` ([[price, qty]], price
    order) up to `qty`; `fee_fn(q, px) -> usd`."""
    left = float("inf") if qty is None else float(qty)
    filled = cost = fees = 0.0
    for px, q in fills or []:
        if left <= 1e-9:
            break
        t = min(float(q), left)
        if t <= 0:
            continue
        filled += t
        cost += t * float(px)
        fees += abs(float(fee_fn(t, float(px))))
        left -= t
    a = max(0.0, float(adverse_per_contract or 0.0))
    h = max(0.0, float(haircut_per_contract or 0.0))
    gross = filled * float(p_used)
    ev = gross - cost - fees - a * filled - h * filled
    pf = min(1.0, max(0.0, float(fill_probability if fill_probability
                                 is not None else 1.0)))
    return {"filled_qty": _r(filled, 6), "expected_payout_usd": _r(gross),
            "cost_usd": _r(cost), "fees_usd": _r(fees),
            "adverse_selection_usd": _r(a * filled),
            "residual_haircut_usd": _r(h * filled),
            "ev_given_fill_usd": _r(ev), "fill_probability": _r(pf, 6),
            "expected_ev_usd": _r(pf * ev),
            "ev_per_contract_usd": _r(ev / filled) if filled else None,
            "capital_usd": _r(cost + fees)}


def capacity_frontier(*, fills, p_used, fee_fn, adverse_per_contract,
                      haircut_per_contract, depth=None) -> dict:
    """The largest whole quantity, walking `fills` in price order, whose
    MARGINAL contract still has all-in EV > 0, capped at MAX_DEPTH_FRACTION
    of the displayed executable depth (`depth`, when known)."""
    a = max(0.0, float(adverse_per_contract or 0.0))
    h = max(0.0, float(haircut_per_contract or 0.0))
    q_ok = 0.0
    stop = None
    for px, q in fills or []:
        fee1 = abs(float(fee_fn(1.0, float(px))))
        marginal = float(p_used) - float(px) - fee1 - a - h
        if marginal <= 0:
            stop = {"price": float(px), "marginal_ev_per_contract": _r(
                marginal)}
            break
        q_ok += float(q)
    cap = q_ok
    depth_cap = None
    d = _num(depth)
    if d is not None and d > 0:
        depth_cap = math.floor(d * MAX_DEPTH_FRACTION + 1e-9)
        cap = min(cap, depth_cap)
    return {"qty": int(math.floor(cap + 1e-9)),
            "positive_marginal_qty": _r(q_ok, 6),
            "depth_cap_qty": depth_cap, "stopped_at": stop,
            "max_depth_fraction": MAX_DEPTH_FRACTION}


def capital_hour(*, ev_usd, capital_usd, hold_hours) -> dict:
    ev, cap, hh = _num(ev_usd), _num(capital_usd), _num(hold_hours)
    if ev is None or not cap or cap <= 0 or not hh or hh <= 0:
        return {"ev_per_capital_hour": None, "factor": 0.0,
                "status": "UNAVAILABLE"}
    evch = ev / (cap * hh)
    factor = (0.0 if evch < MIN_EV_PER_CAPITAL_HOUR
              else min(1.0, evch / TARGET_EV_PER_CAPITAL_HOUR))
    return {"ev_per_capital_hour": _r(evch), "factor": _r(factor, 6),
            "floor": MIN_EV_PER_CAPITAL_HOUR,
            "target": TARGET_EV_PER_CAPITAL_HOUR,
            "status": ("BELOW_FLOOR" if evch < MIN_EV_PER_CAPITAL_HOUR
                       else "OK")}


def correlation_factor(n_correlated: int) -> dict:
    n = max(0, int(n_correlated or 0))
    if n >= MAX_CORRELATED_SAME_FIXTURE:
        return {"n": n, "factor": 0.0, "refused": True}
    return {"n": n, "factor": _r(CORRELATION_SIZE_DECAY ** n, 6),
            "refused": False}


def churn_verdict(*, ev_per_contract, prior_ev_per_contract,
                  kind: str, require_prior: bool = False) -> dict:
    """A re-entry inside a cooldown passes only on a MATERIAL improvement
    of the all-in EV per contract over the last recorded evaluation. After
    an EXIT (`require_prior`) the baseline is the recorded entry EV of the
    contract; with none recorded there is nothing to improve on: refused."""
    cur = _num(ev_per_contract)
    prior = _num(prior_ev_per_contract)
    base = prior if prior is not None else 0.0
    need = max(base + MATERIAL_EV_IMPROVEMENT_USD,
               base * MATERIAL_EV_RATIO if base > 0 else -1e18)
    ok = cur is not None and cur >= need and not (
        require_prior and prior is None)
    return {"kind": kind, "ev_per_contract": _r(cur),
            "prior_ev_per_contract": _r(prior), "required": _r(need),
            "materially_improved": ok}


def bind_economics(*, evidence: dict, qty_in, fee_fn, desc: dict,
                   calibration: dict | None, execution: dict | None,
                   residuals: dict | None, strategy: str, order_type,
                   n_correlated: int, qty_cap=None) -> dict:
    """THE PURE BIND of one entry: calibration -> all-in EV -> capacity ->
    capital-hour -> correlation. {refusal or None, qty, terms...}.

    Evidence already bound at the decision carries the policy's own walk as
    `pre_bind`: the bind is re-derived from it (deterministic: the same
    inputs give the same size -- never a second shrink) and then capped at
    `qty_cap` (the order's quantity)."""
    ev = evidence or {}
    pre = ev.get("pre_bind") if isinstance(ev.get("pre_bind"), dict) else None
    if pre and pre.get("fills"):
        ev = dict(ev, fills=pre["fills"],
                  adverse_selection_usd=pre.get("adverse_selection_usd"))
        qty_in = pre.get("qty") or qty_in
    fills = [[float(px), float(q)] for px, q in (ev.get("fills") or [])
             if _num(px) is not None and (_num(q) or 0) > 0]
    best = _num(ev.get("best_price"))
    if best is None and fills:
        best = fills[0][0]
    p_raw = _num(ev.get("p"))
    out: dict[str, Any] = {"version": VERSION, "descriptor": desc,
                           "qty_in": _num(qty_in),
                           "rederived_from_pre_bind": bool(pre)}
    if not fills or p_raw is None:
        return dict(out, refusal=R_BIND_NO_EVIDENCE, qty=0)
    cal = calibrate(calibration, sport=desc.get("sport"),
                    family=desc.get("family"), regime=desc.get("regime"),
                    p_raw=p_raw, market=best)
    out["calibration"] = cal
    style = style_of(order_type)
    ex = execution_terms(execution, strategy=strategy, style=style)
    bound_pc = _num(ev.get("adverse_selection_usd")) or 0.0
    fq = sum(q for _, q in fills)
    bound_pc = bound_pc / fq if fq else 0.0
    adverse_pc = max(bound_pc, ex["learned_adverse_per_contract"] or 0.0)
    rh = residual_haircut(residuals, strategy=strategy,
                          sport=desc.get("sport"), family=desc.get("family"))
    out.update(execution=dict(ex, style=style,
                              ioc_bound_per_contract=_r(bound_pc),
                              charged_adverse_per_contract=_r(adverse_pc)),
               residual=rh)
    p_used = cal["p_used"]
    q_in = min(_num(qty_in) or fq, fq)
    full = all_in(fills=fills, p_used=p_used, fee_fn=fee_fn,
                  adverse_per_contract=adverse_pc,
                  haircut_per_contract=rh["haircut_per_contract"],
                  fill_probability=ex["fill_probability"], qty=q_in)
    out["all_in_at_policy_size"] = full
    raw = all_in(fills=fills, p_used=p_raw, fee_fn=fee_fn,
                 adverse_per_contract=adverse_pc,
                 haircut_per_contract=rh["haircut_per_contract"],
                 fill_probability=ex["fill_probability"], qty=q_in)
    out["all_in_uncalibrated_usd"] = raw["ev_given_fill_usd"]
    if (full["ev_given_fill_usd"] or 0.0) <= 0:
        r = (R_CALIBRATED_EV_NOT_POSITIVE
             if (raw["ev_given_fill_usd"] or 0.0) > 0
             else R_ALL_IN_EV_NOT_POSITIVE)
        return dict(out, refusal=r, qty=0,
                    ev_per_contract=full["ev_per_contract_usd"])
    depth = None
    lim = _num(ev.get("limit"))
    if style == TAKER and ev.get("levels"):
        depth = sum((_num(lv.get("qty")) or 0.0) for lv in ev["levels"]
                    if isinstance(lv, dict) and _num(lv.get("price"))
                    is not None and (lim is None
                                     or _num(lv.get("price")) <= lim + 1e-12))
    cap = capacity_frontier(fills=fills, p_used=p_used, fee_fn=fee_fn,
                            adverse_per_contract=adverse_pc,
                            haircut_per_contract=rh["haircut_per_contract"],
                            depth=depth)
    out["capacity"] = cap
    q_cap = min(q_in, cap["qty"])
    out["capacity_factor"] = _r(q_cap / q_in, 6) if q_in else 0.0
    if q_cap < 1:
        return dict(out, refusal=R_CAPACITY_NONE, qty=0,
                    ev_per_contract=full["ev_per_contract_usd"])
    at_cap = all_in(fills=fills, p_used=p_used, fee_fn=fee_fn,
                    adverse_per_contract=adverse_pc,
                    haircut_per_contract=rh["haircut_per_contract"],
                    fill_probability=ex["fill_probability"], qty=q_cap)
    hold = desc.get("expected_hold_hours")
    if hold is None:
        return dict(out, refusal=R_NO_HOLD_ESTIMATE, qty=0,
                    ev_per_contract=at_cap["ev_per_contract_usd"])
    ch = capital_hour(ev_usd=at_cap["expected_ev_usd"],
                      capital_usd=at_cap["capital_usd"], hold_hours=hold)
    out["capital_hour"] = ch
    if ch["status"] != "OK":
        return dict(out, refusal=R_CAPITAL_HOUR_BELOW_FLOOR, qty=0,
                    ev_per_contract=at_cap["ev_per_contract_usd"])
    cor = correlation_factor(n_correlated)
    out["correlation"] = cor
    if cor["refused"]:
        return dict(out, refusal=R_CORRELATED_EXPOSURE, qty=0,
                    ev_per_contract=at_cap["ev_per_contract_usd"])
    q_out = int(math.floor(q_cap * float(ch["factor"]) * float(cor["factor"])
                           + 1e-9))
    if _num(qty_cap) is not None:
        q_out = min(q_out, int(math.floor(float(qty_cap) + 1e-9)))
    if q_out < 1:
        return dict(out, refusal=R_SIZE_BELOW_ONE, qty=0,
                    ev_per_contract=at_cap["ev_per_contract_usd"])
    fin = all_in(fills=fills, p_used=p_used, fee_fn=fee_fn,
                 adverse_per_contract=adverse_pc,
                 haircut_per_contract=rh["haircut_per_contract"],
                 fill_probability=ex["fill_probability"], qty=q_out)
    out["all_in"] = fin
    if (fin["ev_given_fill_usd"] or 0.0) <= 0:
        return dict(out, refusal=R_ALL_IN_EV_NOT_POSITIVE, qty=0,
                    ev_per_contract=fin["ev_per_contract_usd"])
    return dict(out, refusal=None, qty=q_out,
                ev_per_contract=fin["ev_per_contract_usd"])


# ═════════════════════════════════════════════════════════════════════
# THE DATABASE READS ON THE ENTRY PATH
# ═════════════════════════════════════════════════════════════════════

async def schema(conn) -> bool:
    try:
        return bool(await conn.fetchval(
            "SELECT to_regclass('paper_profitability_models') IS NOT NULL "
            "   AND to_regclass('paper_profitability_evaluations') IS NOT NULL"
            "   AND to_regclass('paper_cash_decisions') IS NOT NULL"))
    except Exception:                                           # noqa: BLE001
        return False


async def latest_models(conn, account_id: str) -> dict:
    rows = await conn.fetch(
        "SELECT DISTINCT ON (kind) kind, model_id, payload, fitted_at, "
        "       observations FROM paper_profitability_models "
        " WHERE account_id = $1 ORDER BY kind, fitted_at DESC, model_id DESC",
        account_id)
    out = {}
    for r in rows:
        p = _j(r["payload"]) or {}
        out[r["kind"]] = dict(p, model_id=r["model_id"],
                              fitted_at=_epoch(r["fitted_at"]))
    return out


CORRELATED_SQL = """
SELECT
  (SELECT count(*) FROM paper_orders o
    WHERE o.account_id = $1 AND o.fixture = $2 AND o.role = 'ENTRY'
      AND o.direction = 'BUY' AND o.state = ANY($3::text[])) AS live,
  (SELECT count(*) FROM (
       SELECT f.group_id, f.us_market_slug, f.holding_side,
              sum(CASE WHEN f.direction = 'BUY' THEN f.qty
                       ELSE -f.qty END) AS q
         FROM paper_fills f WHERE f.account_id = $1 AND f.fixture = $2
        GROUP BY 1, 2, 3) x
    WHERE x.q > 0.000001 AND NOT EXISTS (
          SELECT 1 FROM paper_settlements s
           WHERE s.account_id = $1 AND s.group_id = x.group_id
             AND s.us_market_slug = x.us_market_slug
             AND s.holding_side = x.holding_side)) AS held
"""


async def correlated_count(conn, *, account_id: str, fixture) -> int:
    """Open (unsettled, not fully sold) positions + live entry orders of the
    ACCOUNT -- every strategy -- on the same fixture."""
    if not fixture:
        return 0
    from . import bettor_paper_ledger as L
    r = await conn.fetchrow(CORRELATED_SQL, account_id, fixture,
                            list(L.OPEN_STATES))
    return int(r["held"] or 0) + int(r["live"] or 0)


async def churn_check(conn, *, account_id: str, strategy: str, slug, side,
                      fixture, at: float, ev_per_contract) -> dict | None:
    """Item 3. None when the entry may proceed; else the refusal dict."""
    hour = await conn.fetchval(
        "SELECT count(*) FROM paper_orders WHERE account_id=$1 "
        "   AND strategy=$2 AND role='ENTRY' AND direction='BUY' "
        "   AND decided_at > $3 AND decided_at <= $4", account_id, strategy,
        _ts(at - 3600.0), _ts(at))
    if int(hour or 0) >= MAX_ENTRIES_PER_HOUR:
        return {"refusal": R_TURNOVER_CAP, "entries_last_hour": int(hour),
                "cap": MAX_ENTRIES_PER_HOUR}
    prior = await conn.fetchrow(
        "SELECT verdict, ev_per_contract_usd, evaluated_at FROM "
        " paper_profitability_evaluations WHERE account_id=$1 "
        "   AND strategy=$2 AND us_market_slug=$3 AND holding_side=$4 "
        "   AND evaluated_at < $5 ORDER BY evaluated_at DESC, eval_id DESC "
        " LIMIT 1", account_id, strategy, slug, side, _ts(at))
    prior_ev = None if prior is None else _num(prior["ev_per_contract_usd"])
    entered = await conn.fetchval(
        "SELECT ev_per_contract_usd FROM paper_profitability_evaluations "
        " WHERE account_id=$1 AND strategy=$2 AND us_market_slug=$3 "
        "   AND holding_side=$4 AND verdict='ENTER' AND evaluated_at < $5 "
        " ORDER BY evaluated_at DESC, eval_id DESC LIMIT 1", account_id,
        strategy, slug, side, _ts(at))
    entered_ev = _num(entered)
    exit_c = await conn.fetchval(
        "SELECT max(filled_at) FROM paper_fills WHERE account_id=$1 "
        "   AND strategy=$2 AND us_market_slug=$3 AND holding_side=$4 "
        "   AND direction='SELL' AND filled_at > $5 AND filled_at <= $6",
        account_id, strategy, slug, side, _ts(at - REENTRY_COOLDOWN_S),
        _ts(at))
    if exit_c is not None:
        v = churn_verdict(ev_per_contract=ev_per_contract,
                          prior_ev_per_contract=entered_ev,
                          kind="CONTRACT_EXIT", require_prior=True)
        if not v["materially_improved"]:
            return dict(v, refusal=R_CHURN_RECENT_EXIT,
                        exited_at=_epoch(exit_c),
                        cooldown_s=REENTRY_COOLDOWN_S)
    if fixture:
        exit_f = await conn.fetchval(
            "SELECT max(filled_at) FROM paper_fills WHERE account_id=$1 "
            "   AND strategy=$2 AND fixture=$3 AND direction='SELL' "
            "   AND filled_at > $4 AND filled_at <= $5", account_id,
            strategy, fixture, _ts(at - FIXTURE_COOLDOWN_S), _ts(at))
        if exit_f is not None:
            v = churn_verdict(ev_per_contract=ev_per_contract,
                              prior_ev_per_contract=entered_ev,
                              kind="FIXTURE_EXIT", require_prior=True)
            if not v["materially_improved"]:
                return dict(v, refusal=R_CHURN_FIXTURE_EXIT,
                            exited_at=_epoch(exit_f),
                            cooldown_s=FIXTURE_COOLDOWN_S)
    if prior is not None and prior["verdict"] == "CASH" and \
            _epoch(prior["evaluated_at"]) > at - REFUSAL_COOLDOWN_S:
        v = churn_verdict(ev_per_contract=ev_per_contract,
                          prior_ev_per_contract=prior_ev,
                          kind="RECENT_REFUSAL")
        if not v["materially_improved"]:
            return dict(v, refusal=R_CHURN_RECENT_REFUSAL,
                        refused_at=_epoch(prior["evaluated_at"]),
                        cooldown_s=REFUSAL_COOLDOWN_S)
    return None


async def entry_bind(conn, *, account_id: str, strategy: str, evidence,
                     qty_in, slug, side, fixture, order_type, at: float,
                     fee_fn=None, qty_cap=None) -> dict:
    """THE BIND OF ONE PAPER ENTRY (items 1, 2, 3, 6): {refusal or None,
    qty (<= qty_in), context (for the regime authority), summary}.
    Fail-closed: any unreadable input refuses."""
    from . import bettor_paper_ledger as L
    if not isinstance(evidence, dict):
        return {"refusal": R_BIND_NO_EVIDENCE, "qty": 0}
    try:
        if not await schema(conn):
            return {"refusal": R_BIND_UNREADABLE, "qty": 0,
                    "why": "MIGRATION_309_NOT_APPLIED"}
        meta = (await premap(conn, [slug])).get(slug)
        desc = descriptor(meta, at=at)
        models = await latest_models(conn, account_id)
        n_corr = await correlated_count(conn, account_id=account_id,
                                        fixture=fixture)
        econ = bind_economics(
            evidence=evidence, qty_in=qty_in,
            fee_fn=lambda q, px: float(L._fee(fee_fn, q, px, at)),
            desc=desc, calibration=models.get("CALIBRATION"),
            execution=models.get("EXECUTION"),
            residuals=models.get("RESIDUAL"), strategy=strategy,
            order_type=order_type, n_correlated=n_corr, qty_cap=qty_cap)
        econ["models"] = {k: {"model_id": v.get("model_id"),
                              "fitted_at": v.get("fitted_at")}
                          for k, v in models.items()}
        if econ.get("refusal") is None:
            ch = await churn_check(conn, account_id=account_id,
                                   strategy=strategy, slug=slug, side=side,
                                   fixture=fixture, at=at,
                                   ev_per_contract=econ.get(
                                       "ev_per_contract"))
            econ["churn"] = ch or {"refusal": None}
            if ch:
                econ = dict(econ, refusal=ch["refusal"], qty=0)
    except Exception as exc:                                    # noqa: BLE001
        return {"refusal": R_BIND_UNREADABLE, "qty": 0,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    return econ


def summary(b: dict | None) -> dict:
    """The compact record carried on the order event / decision."""
    b = b or {}
    fin = b.get("all_in") or b.get("all_in_at_policy_size") or {}
    cal = b.get("calibration") or {}
    d = b.get("descriptor") or {}
    return {"version": VERSION, "refusal": b.get("refusal"),
            "qty_in": b.get("qty_in"), "qty": b.get("qty"),
            "sport": d.get("sport"), "family": d.get("family"),
            "regime": d.get("regime"),
            "p_raw": cal.get("p_raw"), "p_used": cal.get("p_used"),
            "calibration_status": cal.get("status"),
            "calibration_weight": cal.get("weight"),
            "all_in_ev_usd": fin.get("ev_given_fill_usd"),
            "expected_ev_usd": fin.get("expected_ev_usd"),
            "ev_per_contract": b.get("ev_per_contract"),
            "ev_per_capital_hour": (b.get("capital_hour") or {}).get(
                "ev_per_capital_hour"),
            "capacity_factor": b.get("capacity_factor"),
            "correlation_factor": (b.get("correlation") or {}).get("factor"),
            "capital_hour_factor": (b.get("capital_hour") or {}).get(
                "factor"),
            "residual_haircut_per_contract": (b.get("residual") or {}).get(
                "haircut_per_contract"),
            "learned_adverse_per_contract": (b.get("execution") or {}).get(
                "learned_adverse_per_contract"),
            "fill_probability": (b.get("execution") or {}).get(
                "fill_probability")}


def bound_evidence(evidence: dict | None, b: dict | None) -> dict | None:
    """The order's capital evidence restated at the BOUND size (for the
    shadow counterfactual and the census): qty, fills, cost, fees, adverse
    selection and EV are the bind's; `p` stays the raw probability (the
    bind re-derives the used one) and the raw evidence is kept beside."""
    if not isinstance(evidence, dict) or not b or not b.get("all_in"):
        return evidence
    fin = b["all_in"]
    q = b.get("qty") or 0
    left, fills = float(q), []
    for px, fq in evidence.get("fills") or []:
        if left <= 1e-9:
            break
        t = min(float(fq), left)
        fills.append([float(px), t])
        left -= t
    return dict(evidence, qty=q, fills=fills,
                cost_usd=fin.get("cost_usd"), fees_usd=fin.get("fees_usd"),
                adverse_selection_usd=fin.get("adverse_selection_usd"),
                total_executable_ev_usd=fin.get("ev_given_fill_usd"),
                profitability_bind=summary(b),
                pre_bind=(evidence.get("pre_bind") if isinstance(
                    evidence.get("pre_bind"), dict) else
                    {k: evidence.get(k) for k in (
                        "qty", "fills", "cost_usd", "fees_usd",
                        "adverse_selection_usd",
                        "total_executable_ev_usd")}))


async def record_evaluation(conn, *, account_id: str, strategy: str,
                            stage: str, b: dict, refusal, decision_id=None,
                            order_key=None, slug=None, side=None,
                            fixture=None, at: float,
                            detail: dict | None = None) -> int | None:
    """ONE EVALUATION ROW (migration 309), its own savepoint; never raises.
    `refusal` is the FINAL refusal of the entry (the bind's or a later
    gate's); None = ENTER."""
    s = summary(b)
    try:
        if not await schema(conn):
            return None
        q_in = _num(s.get("qty_in"))
        q_out = 0.0 if refusal else float(s.get("qty") or 0)
        if q_in is not None:
            q_out = min(q_out, q_in)
        p_raw, p_used = _num(s.get("p_raw")), _num(s.get("p_used"))
        if p_raw is not None and p_used is not None:
            p_used = min(p_used, p_raw)
        cal = (b or {}).get("calibration") or {}
        async with conn.transaction():
            return await conn.fetchval(
                "INSERT INTO paper_profitability_evaluations (account_id, "
                " strategy, stage, decision_id, order_key, us_market_slug, "
                " holding_side, fixture, sport, market_family, regime, "
                " p_raw, p_used, market_price, calibration_weight, qty_in, "
                " qty_out, capacity_factor, correlation_factor, "
                " capital_hour_factor, all_in_ev_usd, ev_per_contract_usd, "
                " ev_per_capital_hour, expected_hold_hours, "
                " residual_haircut_per_contract, "
                " learned_adverse_per_contract, fill_probability, verdict, "
                " refusal, detail, evaluated_at) VALUES ($1,$2,$3,$4,$5,$6,"
                " $7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,"
                " $22,$23,$24,$25,$26,$27,$28,$29,$30::jsonb,$31) "
                "RETURNING eval_id",
                account_id, strategy, stage, decision_id, order_key, slug,
                side, fixture, s.get("sport"), s.get("family"),
                s.get("regime"), p_raw, p_used, _num(cal.get("market")),
                _num(s.get("calibration_weight")), q_in, q_out,
                _num(s.get("capacity_factor")),
                _num(s.get("correlation_factor")),
                _num(s.get("capital_hour_factor")),
                _num(s.get("all_in_ev_usd")), _num(s.get("ev_per_contract")),
                _num(s.get("ev_per_capital_hour")),
                _num(((b or {}).get("descriptor") or {}).get(
                    "expected_hold_hours")),
                _num(s.get("residual_haircut_per_contract")),
                _num(s.get("learned_adverse_per_contract")),
                _num(s.get("fill_probability")),
                "CASH" if refusal else "ENTER", refusal,
                json.dumps(dict(detail or {}, bind=s,
                                churn=(b or {}).get("churn"),
                                capacity=(b or {}).get("capacity"),
                                bind_refusal=(b or {}).get("refusal")),
                           default=str), _ts(at))
    except Exception:                                           # noqa: BLE001
        return None


# ═════════════════════════════════════════════════════════════════════
# 5 · REGIME AUTHORITY AND 8 · THE ABSOLUTE-POSITIVE CHAMPION RULE
# ═════════════════════════════════════════════════════════════════════

def champion_verdict(forward: dict | None) -> dict:
    """ABSOLUTE, not relative: forward POSITIVE and its 95% CI lower bound
    strictly above zero. Pure."""
    f = forward or {}
    lo = _num(f.get("pnl_ci95_low"))
    ok = f.get("verdict") == "POSITIVE" and lo is not None and lo > 0
    return {"champion": ok, "rule": "ABSOLUTE_POSITIVE_FORWARD_NET_CI_LOW_GT_0",
            "forward_verdict": f.get("verdict"), "pnl_ci95_low": lo,
            "net_pnl_usd": f.get("net_pnl_usd"),
            "observations": f.get("observations"),
            "refusal": None if ok else R_NOT_ABSOLUTE_CHAMPION}


async def regime_observations(conn, account_id: str, strategy: str, *,
                              since: float, positions: list | None = None
                              ) -> dict:
    """{regime: {paper: [...], shadow: [...]}}: the strategy's settled
    forward observations (bettor_capital_authority's rule) split by the
    regime at the entry (paper: first fill; shadow: decision)."""
    from . import bettor_capital_authority as CA
    from . import bettor_paper_ledger as L
    from . import bettor_strategy_lifecycle as LC
    pos = positions if positions is not None else await L.positions(
        conn, account_id, include_closed=True)
    mine = [p for p in pos if LC.strategy_of(p, L.DEFAULT_STRATEGY)
            == strategy and (_num(p.get("first_fill_at")) or 0) >= since
            and LC.closed_at(p) is not None]
    sh = [dict(r) for r in await conn.fetch(
        "SELECT s.us_market_slug, extract(epoch FROM s.decided_at)::float8 "
        "       AS decided_at, o.counterfactual_pnl_usd "
        "  FROM paper_shadow_counterfactuals s "
        "  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id) "
        " WHERE s.account_id = $1 AND s.strategy = $2 AND s.decided_at >= $3"
        "   AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND') "
        "   AND o.filled_qty > 0", account_id, strategy, CA._ts(since))]
    meta = await premap(conn, [p["us_market_slug"] for p in mine]
                        + [r["us_market_slug"] for r in sh])
    out: dict = {}
    for p in mine:
        g = (meta.get(p["us_market_slug"]) or {}).get("game_start_epoch")
        rg = regime_of(g, p.get("first_fill_at"))
        out.setdefault(rg, {"paper": [], "shadow": []})["paper"].append(
            _num(p.get("realized_pnl_usd")) or 0.0)
    for r in sh:
        g = (meta.get(r["us_market_slug"]) or {}).get("game_start_epoch")
        rg = regime_of(g, r["decided_at"])
        out.setdefault(rg, {"paper": [], "shadow": []})["shadow"].append(
            float(r["counterfactual_pnl_usd"]))
    return out


async def regime_authority(conn, *, account_id: str, strategy: str,
                           regime: str, now: float,
                           positions: list | None = None) -> dict:
    """Item 5: {refusal or None, regime, forward}. Unknown regime refuses;
    unreadable refuses (R_REGIME_UNREADABLE)."""
    from . import bettor_capital_authority as CA
    if regime in (None, REGIME_UNKNOWN):
        return {"refusal": R_REGIME_UNKNOWN, "regime": REGIME_UNKNOWN,
                "why": "no venue start time: the regime is unknown"}
    try:
        obs = await regime_observations(conn, account_id, strategy,
                                        since=CA.FORWARD_SINCE,
                                        positions=positions)
    except Exception as exc:                                    # noqa: BLE001
        return {"refusal": R_REGIME_UNREADABLE, "regime": regime,
                "why": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    o = obs.get(regime) or {"paper": [], "shadow": []}
    fwd = CA.forward_verdict(o["paper"], o["shadow"],
                             min_n=MIN_REGIME_OBSERVATIONS)
    return {"refusal": (None if fwd["verdict"] == CA.POSITIVE
                        else R_REGIME_NOT_POSITIVE),
            "regime": regime, "forward": fwd}


async def authority_extra(conn, *, account_id: str, strategy: str,
                          forward: dict, context: dict | None, now: float,
                          positions: list | None = None) -> dict:
    """Called by bettor_capital_authority.authority after the strategy's
    forward economics passed: the absolute-positive champion rule, then the
    regime authority for the entry's regime (`context` = the bind's
    descriptor; None skips the regime check -- a read with no contract)."""
    ch = champion_verdict(forward)
    if ch["refusal"]:
        return {"refusal": ch["refusal"], "champion": ch}
    if context is None:
        return {"refusal": None, "champion": ch, "regime": None}
    ra = await regime_authority(conn, account_id=account_id,
                                strategy=strategy,
                                regime=context.get("regime"), now=now,
                                positions=positions)
    return {"refusal": ra.get("refusal"), "champion": ch, "regime": ra}


# ═════════════════════════════════════════════════════════════════════
# 4 · MANAGEMENT ECONOMICS (Xavier's HOLD / EXIT / REDUCE)
# ═════════════════════════════════════════════════════════════════════

async def management_economics(conn, *, account_id: str, pos: dict, p_raw,
                               at: float) -> dict:
    """THE SAME ALL-IN ECONOMICS FOR MANAGEMENT: the HOLD probability is
    the calibrated one (min(p_raw, p_cal) at the CURRENT regime; the market
    anchor is the held side's best exit), less the strategy's residual
    haircut per contract. EXIT / REDUCE are valued on the walked bids after
    their fees (paper_xavier.alternatives). Never raises: an unreadable
    input leaves p unchanged and says so (a management review is never
    blocked by the bind)."""
    from . import bettor_paper_ledger as L
    from . import bettor_strategy_lifecycle as LC
    p = _num(p_raw)
    if p is None:
        return {"p_hold": None, "haircut_per_contract": 0.0,
                "status": "NO_MEASURE"}
    try:
        if not await schema(conn):
            return {"p_hold": p, "haircut_per_contract": 0.0,
                    "status": "MIGRATION_309_NOT_APPLIED"}
        meta = (await premap(conn, [pos.get("us_market_slug")])).get(
            pos.get("us_market_slug"))
        desc = descriptor(meta, at=at)
        models = await latest_models(conn, account_id)
        mark = await L.latest_marks(conn, [pos.get("us_market_slug")],
                                    now=at)
        anchor = (((mark.get(pos.get("us_market_slug")) or {}).get(
            pos.get("holding_side")) or {}).get("price"))
        cal = calibrate(models.get("CALIBRATION"), sport=desc["sport"],
                        family=desc["family"], regime=desc["regime"],
                        p_raw=p, market=anchor if anchor is not None else p)
        rh = residual_haircut(models.get("RESIDUAL"),
                              strategy=LC.strategy_of(pos, L.DEFAULT_STRATEGY),
                              sport=desc["sport"], family=desc["family"])
    except Exception as exc:                                    # noqa: BLE001
        return {"p_hold": p, "haircut_per_contract": 0.0,
                "status": "UNREADABLE: %s" % type(exc).__name__}
    return {"version": VERSION, "p_raw": _r(p), "p_hold": cal["p_used"],
            "calibration": cal, "descriptor": desc,
            "haircut_per_contract": rh["haircut_per_contract"],
            "residual": rh, "status": "BOUND",
            "rule": ("HOLD = q x (min(p_raw, p_cal) - residual haircut); "
                     "EXIT / REDUCE = walked proceeds - fees + kept x the "
                     "same HOLD value per contract")}


# ═════════════════════════════════════════════════════════════════════
# 8 · THE FITS (the paper pass step)
# ═════════════════════════════════════════════════════════════════════

DECISIONS_SQL = """
SELECT d.strategy, d.us_market_slug, d.holding_side,
       extract(epoch FROM d.decided_at)::float8 AS decided_at,
       coalesce(d.p_blended, d.p_pinnacle, d.p_internal) AS p
  FROM paper_decisions d
 WHERE d.account_id = $1 AND d.decided_at >= $2
   AND d.us_market_slug IS NOT NULL AND d.holding_side IS NOT NULL
   AND coalesce(d.p_blended, d.p_pinnacle, d.p_internal) IS NOT NULL
 ORDER BY d.decided_at DESC LIMIT $3
"""

SHADOW_OBS_SQL = """
SELECT s.strategy, s.us_market_slug, s.holding_side, s.p,
       extract(epoch FROM s.decided_at)::float8 AS decided_at
  FROM paper_shadow_counterfactuals s
 WHERE s.account_id = $1 AND s.decided_at >= $2
"""


async def outcomes(conn, slugs) -> dict:
    """{(slug, side): y} from authoritative settlement evidence
    (paper_xavier.outcome_for over the venue-joined valuation rows):
    WON = 1, LOST = 0; void / conflicting / pending are not observations."""
    from .agents import paper_xavier as PX
    slugs = sorted({s for s in slugs if s})
    if not slugs:
        return {}
    rows = [dict(r) for r in await conn.fetch(
        "SELECT id, us_market_slug, buy_intent, outcome, outcome_known, "
        "       outcome_basis, outcome_at FROM external_valuations "
        " WHERE us_market_slug = ANY($1::text[]) "
        "   AND outcome_basis IS NOT NULL ORDER BY id", slugs)]
    by: dict = {}
    for r in rows:
        by.setdefault(r["us_market_slug"], []).append(r)
    out = {}
    for slug, rs in by.items():
        for side in ("LONG", "SHORT"):
            got = PX.outcome_for(rs, holding_side=side)
            if got.get("outcome") == "WON":
                out[(slug, side)] = 1.0
            elif got.get("outcome") == "LOST":
                out[(slug, side)] = 0.0
    return out


async def calibration_observations(conn, account_id: str, *, now: float
                                   ) -> list:
    since = _ts(now - FIT_WINDOW_DAYS * 86400.0)
    dec = [dict(r) for r in await conn.fetch(DECISIONS_SQL, account_id,
                                             since, FIT_DECISIONS_LIMIT)]
    sh = [dict(r) for r in await conn.fetch(SHADOW_OBS_SQL, account_id,
                                            since)]
    rows = [dict(r, source="DECISION") for r in dec] + \
        [dict(r, source="SHADOW") for r in sh]
    meta = await premap(conn, [r["us_market_slug"] for r in rows])
    ys = await outcomes(conn, [r["us_market_slug"] for r in rows])
    seen: dict = {}
    for r in sorted(rows, key=lambda r: -(r["decided_at"] or 0)):
        y = ys.get((r["us_market_slug"], r["holding_side"]))
        if y is None:
            continue
        m = meta.get(r["us_market_slug"])
        d = descriptor(m, at=r["decided_at"])
        k = (r["us_market_slug"], r["holding_side"], d["regime"])
        if k in seen:
            continue            # one observation per contract / side / regime
        seen[k] = {"sport": d["sport"], "family": d["family"],
                   "regime": d["regime"], "p": _num(r["p"]), "y": y,
                   "source": r["source"]}
    return list(seen.values())


EXEC_FILLS_SQL = """
SELECT f.strategy, f.holding_side, f.qty, f.price, o.order_type,
       b0.bids AS b0_bids, b0.offers AS b0_offers,
       m.bids AS m_bids, m.offers AS m_offers
  FROM paper_fills f JOIN paper_orders o USING (order_id)
  LEFT JOIN LATERAL (
       SELECT bids, offers FROM paper_book_observations b
        WHERE b.us_market_slug = f.us_market_slug AND b.error IS NULL
          AND b.observed_at <= f.filled_at
          AND b.observed_at >= f.filled_at - make_interval(secs => $3)
        ORDER BY b.observed_at DESC LIMIT 1) b0 ON true
  LEFT JOIN LATERAL (
       SELECT bids, offers FROM paper_book_observations b
        WHERE b.us_market_slug = f.us_market_slug AND b.error IS NULL
          AND b.observed_at >= f.filled_at + make_interval(secs => $4)
          AND b.observed_at <= f.filled_at + make_interval(secs => $5)
        ORDER BY b.observed_at LIMIT 1) m ON true
 WHERE f.account_id = $1 AND f.role = 'ENTRY' AND f.direction = 'BUY'
   AND f.filled_at >= $2
 ORDER BY f.filled_at DESC LIMIT 5000
"""

EXEC_ORDERS_SQL = """
SELECT strategy, order_type, qty, filled_qty FROM paper_orders
 WHERE account_id = $1 AND role = 'ENTRY' AND direction = 'BUY'
   AND state = ANY($2::text[]) AND decided_at >= $3
 ORDER BY decided_at DESC LIMIT 10000
"""


def _held_mid(bids, offers, side):
    from .intel import common as IC
    try:
        v = IC.book_view(_j(bids) or [], _j(offers) or [])
    except Exception:                                           # noqa: BLE001
        return None
    m = v.get("mid")
    if m is None:
        return None
    return m if str(side).upper() != "SHORT" else 1.0 - m


async def execution_observations(conn, account_id: str, *, now: float
                                 ) -> tuple:
    from . import bettor_paper_ledger as L
    since = _ts(now - FIT_WINDOW_DAYS * 86400.0)
    fills = []
    for r in await conn.fetch(EXEC_FILLS_SQL, account_id, since,
                              FILL_BOOK_MAX_S, MARKOUT_HORIZON_S,
                              MARKOUT_HORIZON_S + MARKOUT_TOLERANCE_S):
        m0 = _held_mid(r["b0_bids"], r["b0_offers"], r["holding_side"])
        mh = _held_mid(r["m_bids"], r["m_offers"], r["holding_side"])
        fills.append({"strategy": r["strategy"],
                      "style": style_of(r["order_type"]),
                      "qty": float(r["qty"]),
                      "markout_per_contract": (None if m0 is None
                                               or mh is None else m0 - mh)})
    orders = [{"strategy": r["strategy"], "style": style_of(r["order_type"]),
               "qty": float(r["qty"]), "filled_qty": float(r["filled_qty"])}
              for r in await conn.fetch(EXEC_ORDERS_SQL, account_id,
                                        list(L.TERMINAL_STATES), since)]
    return fills, orders


RES_ORDERS_SQL = """
SELECT o.group_id, o.us_market_slug, o.holding_side, o.strategy, o.qty,
       o.filled_qty, o.idempotency_key, e.detail,
       (SELECT v.all_in_ev_usd / NULLIF(v.qty_out, 0)
          FROM paper_profitability_evaluations v
         WHERE v.order_key = o.idempotency_key AND v.stage = 'LEDGER'
           AND v.verdict = 'ENTER' ORDER BY v.eval_id DESC LIMIT 1)
         AS bind_ev_per_contract
  FROM paper_orders o
  LEFT JOIN paper_order_events e ON e.order_id = o.order_id
                                AND e.kind = 'SUBMITTED'
 WHERE o.account_id = $1 AND o.role = 'ENTRY' AND o.direction = 'BUY'
   AND o.filled_qty > 0
"""

RES_SHADOW_SQL = """
SELECT s.strategy, s.us_market_slug, s.qty, s.total_executable_ev_usd,
       o.filled_qty, o.counterfactual_pnl_usd
  FROM paper_shadow_counterfactuals s
  JOIN paper_shadow_counterfactual_outcomes o USING (shadow_id)
 WHERE s.account_id = $1 AND o.outcome NOT IN ('NO_FILL', 'VOID_REFUND')
   AND o.filled_qty > 0
"""


async def residual_observations(conn, account_id: str) -> list:
    """Expected EV at the decision vs the realized result: every CLOSED
    paper position with a recorded expected EV (the bind's at the ledger,
    else the capital-authority evidence on its SUBMITTED event), scaled to
    the filled quantity; every settled filled shadow (SHADOW)."""
    from . import bettor_paper_ledger as L
    from . import bettor_strategy_lifecycle as LC
    pos = {(p["group_id"], p["us_market_slug"], p["holding_side"]): p
           for p in await L.positions(conn, account_id, include_closed=True)
           if LC.closed_at(p) is not None}
    rows = []
    exp: dict = {}
    for r in await conn.fetch(RES_ORDERS_SQL, account_id):
        k = (r["group_id"], r["us_market_slug"], r["holding_side"])
        if k not in pos:
            continue
        filled = float(r["filled_qty"])
        pc = _num(r["bind_ev_per_contract"])
        if pc is not None:
            e = pc * filled
        else:
            ca = (_j(r["detail"]) or {}).get("capital_authority") or {}
            x = _num(ca.get("total_executable_ev_usd"))
            if x is None or not float(r["qty"]):
                continue
            e = x * filled / float(r["qty"])
        g = exp.setdefault(k, {"e": 0.0, "q": 0.0, "strategy": r["strategy"],
                               "slug": r["us_market_slug"]})
        g["e"] += e
        g["q"] += filled
    meta = await premap(conn, [g["slug"] for g in exp.values()])
    for k, g in exp.items():
        d = descriptor(meta.get(g["slug"]), at=0)
        rows.append({"strategy": g["strategy"], "sport": d["sport"],
                     "family": d["family"], "expected_ev_usd": g["e"],
                     "realized_pnl_usd": _num(pos[k].get("realized_pnl_usd")),
                     "qty": g["q"], "source": "PAPER"})
    sh = [dict(r) for r in await conn.fetch(RES_SHADOW_SQL, account_id)]
    meta = await premap(conn, [r["us_market_slug"] for r in sh])
    for r in sh:
        d = descriptor(meta.get(r["us_market_slug"]), at=0)
        q, f = float(r["qty"]), float(r["filled_qty"])
        rows.append({"strategy": r["strategy"], "sport": d["sport"],
                     "family": d["family"],
                     "expected_ev_usd": float(r["total_executable_ev_usd"])
                     * f / q if q else None,
                     "realized_pnl_usd": float(r["counterfactual_pnl_usd"]),
                     "qty": f, "source": "SHADOW"})
    return rows


async def record_model(conn, *, account_id: str, kind: str, payload: dict,
                       at: float) -> int | None:
    try:
        async with conn.transaction():
            return await conn.fetchval(
                "INSERT INTO paper_profitability_models (account_id, kind, "
                " version, observations, payload, fitted_at) VALUES ($1,$2,"
                " $3,$4,$5::jsonb,$6) RETURNING model_id", account_id, kind,
                VERSION, int(payload.get("observations") or 0),
                json.dumps(payload, default=str), _ts(at))
    except Exception:                                           # noqa: BLE001
        return None


async def fit_all(conn, *, account_id: str, now: float) -> dict:
    """Fit and record CALIBRATION, EXECUTION and RESIDUAL. Each fit fails
    alone (recorded as an error); the gate then keeps reading the previous
    fit or, with none, the conservative defaults."""
    out: dict = {}
    for kind, fn in (
            ("CALIBRATION", lambda: calibration_observations(
                conn, account_id, now=now)),
            ("EXECUTION", lambda: execution_observations(
                conn, account_id, now=now)),
            ("RESIDUAL", lambda: residual_observations(conn, account_id))):
        try:
            async with conn.transaction():
                got = await fn()
            if kind == "CALIBRATION":
                payload = fit_calibration(got)
            elif kind == "EXECUTION":
                payload = fit_execution(*got)
            else:
                payload = fit_residuals(got)
            payload["fitted_at"] = now
            mid = await record_model(conn, account_id=account_id, kind=kind,
                                     payload=payload, at=now)
            out[kind] = {"model_id": mid,
                         "observations": payload.get("observations")}
        except Exception as exc:                                # noqa: BLE001
            out[kind] = {"error": "%s: %s" % (type(exc).__name__,
                                              str(exc)[:160])}
    return out


_LAST_FIT: dict = {}


async def step(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP: refit the learned inputs, at most every
    FIT_EVERY_S. Records models only; never places, cancels or sizes an
    order by itself."""
    acct = ctx.get("account_id")
    now = float(ctx["now"])
    if not await schema(conn):
        return {"ran": False, "why": "MIGRATION_309_NOT_APPLIED"}
    last = _LAST_FIT.get(acct)
    if last is not None and 0 <= now - last < FIT_EVERY_S:
        return {"ran": False, "why": "RAN_WITHIN_FIT_EVERY_S"}
    _LAST_FIT[acct] = now
    return dict(await fit_all(conn, account_id=acct, now=now), ran=True)


# ═════════════════════════════════════════════════════════════════════
# 7 · THE EXPLICIT CASH DECISION
# ═════════════════════════════════════════════════════════════════════

async def record_cash(conn, *, account_id: str, window_start: float,
                      window_end: float, pass_at: float) -> dict:
    """For every strategy that recorded decisions in [window_start,
    window_end] and opened NO paper entry order there: one CASH row with
    the binding refusals and the best refused candidate (the census row
    with the highest executable EV). Idempotent per pass."""
    if not await schema(conn):
        return {"recorded": 0, "why": "MIGRATION_309_NOT_APPLIED"}
    ws, we = _ts(window_start), _ts(window_end)
    dec = {r["strategy"]: dict(r) for r in await conn.fetch(
        "SELECT strategy, count(*) AS n FROM paper_decisions "
        " WHERE account_id=$1 AND decided_at >= $2 AND decided_at <= $3 "
        " GROUP BY strategy", account_id, ws, we)}
    entered = {r["strategy"] for r in await conn.fetch(
        "SELECT DISTINCT strategy FROM paper_orders WHERE account_id=$1 "
        "   AND role='ENTRY' AND direction='BUY' AND decided_at >= $2 "
        "   AND decided_at <= $3", account_id, ws, we)}
    n = 0
    for s, d in sorted(dec.items()):
        if s in entered:
            continue
        refs = {r["refusal"]: int(r["c"]) for r in await conn.fetch(
            "SELECT coalesce(refusal, 'NO_REFUSAL_RECORDED') AS refusal, "
            "       count(*) AS c FROM paper_decisions WHERE account_id=$1 "
            "   AND strategy=$2 AND decided_at >= $3 AND decided_at <= $4 "
            " GROUP BY 1", account_id, s, ws, we)}
        best = await conn.fetchrow(
            "SELECT us_market_slug, holding_side, fixture, refusal, p, "
            "       best_price, total_executable_ev_usd, stage FROM "
            " paper_entry_refusal_census WHERE account_id=$1 AND strategy=$2"
            "   AND refused_at >= $3 AND refused_at <= $4 "
            " ORDER BY total_executable_ev_usd DESC NULLS LAST, refusal_id "
            " LIMIT 1", account_id, s, ws, we) if await conn.fetchval(
                "SELECT to_regclass('paper_entry_refusal_census') IS NOT NULL"
            ) else None
        try:
            async with conn.transaction():
                got = await conn.fetchval(
                    "INSERT INTO paper_cash_decisions (account_id, strategy, "
                    " pass_at, window_start, window_end, decisions_evaluated,"
                    " binding_refusals, best_candidate) VALUES ($1,$2,$3,$4,"
                    " $5,$6,$7::jsonb,$8::jsonb) ON CONFLICT (account_id, "
                    " strategy, pass_at) DO NOTHING RETURNING cash_id",
                    account_id, s, _ts(pass_at), ws, we, int(d["n"]),
                    json.dumps(refs),
                    None if best is None else json.dumps(dict(best),
                                                         default=str))
            n += 1 if got is not None else 0
        except Exception:                                       # noqa: BLE001
            continue
    return {"recorded": n, "strategies_evaluated": len(dec),
            "strategies_entered": len(entered & set(dec))}


async def cash_step(conn, ctx: dict) -> dict:
    """THE PAPER PASS STEP (after every entry step): the explicit CASH
    decision of each strategy that entered nothing this pass."""
    now = float(ctx["now"])
    end = float(ctx["clock"]()) if ctx.get("clock") else now
    try:
        return await record_cash(conn, account_id=ctx["account_id"],
                                 window_start=now, window_end=max(end, now),
                                 pass_at=now)
    except Exception as exc:                                    # noqa: BLE001
        return {"recorded": 0, "why": "%s: %s" % (type(exc).__name__,
                                                  str(exc)[:160])}


# ═════════════════════════════════════════════════════════════════════
# THE READ (acceptance view)
# ═════════════════════════════════════════════════════════════════════

async def strategy_state(conn, *, account_id: str, strategy: str, now: float,
                         since: float, forward: dict | None,
                         positions: list | None, models: dict) -> dict:
    """The per-strategy state of every bound item, for the acceptance
    read. Raises on a failed read (the route turns that into UNAVAILABLE)."""
    from . import bettor_capital_authority as CA
    obs = await regime_observations(conn, account_id, strategy,
                                    since=CA.FORWARD_SINCE,
                                    positions=positions)
    regimes = {}
    for rg in REGIMES + (REGIME_UNKNOWN,):
        o = obs.get(rg) or {"paper": [], "shadow": []}
        v = CA.forward_verdict(o["paper"], o["shadow"],
                               min_n=MIN_REGIME_OBSERVATIONS)
        regimes[rg] = {"verdict": (v["verdict"] if rg != REGIME_UNKNOWN
                                   else "CASH_SHADOW_ONLY"),
                       "observations": v["observations"],
                       "net_pnl_usd": v["net_pnl_usd"],
                       "entry_allowed_in_regime": (
                           rg != REGIME_UNKNOWN and v["verdict"]
                           == CA.POSITIVE)}
    ev = [dict(r) for r in await conn.fetch(
        "SELECT verdict, refusal, stage, ev_per_contract_usd, "
        "       ev_per_capital_hour, capacity_factor, correlation_factor, "
        "       capital_hour_factor, qty_in, qty_out FROM "
        " paper_profitability_evaluations WHERE account_id=$1 "
        "   AND strategy=$2 AND evaluated_at >= $3", account_id, strategy,
        _ts(since))]
    by_ref: dict = {}
    for r in ev:
        if r["refusal"]:
            by_ref[r["refusal"]] = by_ref.get(r["refusal"], 0) + 1
    churn = {k: v for k, v in by_ref.items() if k.startswith("CHURN_")}
    shrunk = [r for r in ev if r["verdict"] == "ENTER" and r["qty_in"]
              is not None and float(r["qty_out"]) < float(r["qty_in"])]
    cash = await conn.fetchrow(
        "SELECT count(*) AS n, max(pass_at) AS last FROM paper_cash_decisions"
        " WHERE account_id=$1 AND strategy=$2 AND pass_at >= $3",
        account_id, strategy, _ts(since))
    evch = [float(r["ev_per_capital_hour"]) for r in ev
            if r["verdict"] == "ENTER" and r["ev_per_capital_hour"]
            is not None]
    return {
        "version": VERSION,
        "champion": champion_verdict(forward),
        "regime_authority": regimes,
        "residual": {k: v for k, v in ((models.get("RESIDUAL") or {}).get(
            "cells") or {}).items() if k.startswith(strategy + "|")},
        "execution": {k: v for k, v in ((models.get("EXECUTION") or {}).get(
            "by_strategy_style") or {}).items()
            if k.startswith(strategy + "|")},
        "evaluations_since_cutover": {
            "total": len(ev),
            "enter": sum(1 for r in ev if r["verdict"] == "ENTER"),
            "cash": sum(1 for r in ev if r["verdict"] == "CASH"),
            "by_refusal": by_ref, "churn_refusals": churn,
            "entries_shrunk_by_bind": len(shrunk),
            "mean_ev_per_capital_hour_entered": (
                _r(sum(evch) / len(evch)) if evch else None)},
        "cash_decisions_since_cutover": {
            "count": int(cash["n"] or 0), "last_pass_at": _epoch(
                cash["last"])}}


def describe() -> dict:
    return {"version": VERSION, "authority": AUTHORITY,
            "calibration": {"key": "sport|family|regime",
                            "bins": list(CAL_BINS), "k": K_CALIBRATION,
                            "min_cell_observations": MIN_CELL_OBSERVATIONS,
                            "never_inflates": True},
            "regimes": list(REGIMES) + [REGIME_UNKNOWN],
            "churn": {"reentry_cooldown_s": REENTRY_COOLDOWN_S,
                      "fixture_cooldown_s": FIXTURE_COOLDOWN_S,
                      "refusal_cooldown_s": REFUSAL_COOLDOWN_S,
                      "material_ev_improvement_usd":
                          MATERIAL_EV_IMPROVEMENT_USD,
                      "material_ev_ratio": MATERIAL_EV_RATIO,
                      "max_entries_per_hour": MAX_ENTRIES_PER_HOUR},
            "sizing": {"max_depth_fraction": MAX_DEPTH_FRACTION,
                       "min_ev_per_capital_hour": MIN_EV_PER_CAPITAL_HOUR,
                       "target_ev_per_capital_hour":
                           TARGET_EV_PER_CAPITAL_HOUR,
                       "max_correlated_same_fixture":
                           MAX_CORRELATED_SAME_FIXTURE,
                       "correlation_size_decay": CORRELATION_SIZE_DECAY,
                       "only_shrinks": True},
            "regime_min_observations": MIN_REGIME_OBSERVATIONS,
            "champion_rule": "ABSOLUTE_POSITIVE_FORWARD_NET_CI_LOW_GT_0",
            "residual": {"k": K_RESIDUAL,
                         "min_observations": MIN_RESIDUAL_OBSERVATIONS,
                         "credits_positive_residual": False},
            "refusals": list(ALL_REFUSALS), "paper_only": True,
            "at": time.time()}
