"""THE THREE EXECUTION-EVIDENCE CLASSES (R30C). Pure: no I/O, and nothing
here imports a paper, order, venue, execution or funded module.

OWNER AUDIT 2026-10-04: "real fill / slippage / adverse-selection / cancel /
recovery data must eventually dominate LIVE execution estimates; simulated
fills cannot prove live execution quality." Until this module every LIVE-path
execution estimate (Eddie's fill probability, adverse selection and time to
fill; the Opportunity Score's P(fill); Allie's executable EV) was fitted on
PAPER fills and carried no statement of that -- a reader of the canonical
intent saw `expected_fill_probability 0.83` and nothing said it was the paper
simulator's fill rate, not the venue's.

THE CLASSES, kept apart in storage, estimation and display:

  PAPER_SIMULATION  paper fills from the simulated book (paper_orders /
                    paper_fills, event_source CHECKed 'SIMULATOR'): a
                    delay, a consumption ledger and a queue model over the
                    observed books. Useful; never proof of live execution.
  LIVE_SHADOW       what the SMALL LIVE SHADOW adapter would have done
                    (canonical_intent_executions adapter SMALL_LIVE, mode
                    SHADOW), priced against the venue book observed at the
                    decision. Real book, real live size -- but no order was
                    sent, so no latency, queue, rejection or venue fill is
                    in it. Never proof of live execution.
  ACTUAL            real venue fills of the canonical path
                    (small_live_order_events, source CHECKed
                    'VENUE_ORDER_RECORD', on a LIVE-mode execution). TODAY
                    THERE ARE NONE: SMALL LIVE is SHADOW (migration 225
                    CHECKs), so ACTUAL is UNMEASURED with that reason --
                    never a zero.

  NO_FILL_EVIDENCE  (not a class of fills) an estimate that is a walk of the
                    DISPLAYED book and was fitted on no fill at all (Eddie's
                    spread / slippage walk; the external-valuation lane's
                    marketable coverage). Displayed depth is not guaranteed
                    depth.

THE LIVE RULE. A LIVE execution estimate may use ACTUAL when it exists
(MIN_N observations). Otherwise it is LABELLED as derived from shadow or
simulation and its interval is WIDENED by a pre-declared transfer penalty K
(the effective sample size is divided by K, so the interval is ~sqrt(K)
wider): K = 4 for LIVE_SHADOW, 9 for PAPER_SIMULATION. K IS NOT A
MEASUREMENT of the sim-to-live gap -- nobody has one, because no canonical
live fill exists -- it is a declared, versioned prior that ACTUAL fills
replace. Changing it is a new VERSION.
"""
from __future__ import annotations

import hashlib
import json
import math

VERSION = "EXECUTION_EVIDENCE_V1"

PAPER_SIMULATION = "PAPER_SIMULATION"
LIVE_SHADOW = "LIVE_SHADOW"
ACTUAL = "ACTUAL"
CLASSES = (PAPER_SIMULATION, LIVE_SHADOW, ACTUAL)
NO_FILL_EVIDENCE = "NO_FILL_EVIDENCE_DISPLAYED_BOOK_ONLY"
FITTED_ON_VALUES = CLASSES + (NO_FILL_EVIDENCE,)

#: the order in which a LIVE estimate may draw on a class (pre-declared)
LIVE_PRECEDENCE = (ACTUAL, LIVE_SHADOW, PAPER_SIMULATION)
#: effective-sample-size divisor for a LIVE estimate drawn from the class
TRANSFER_PENALTY = {ACTUAL: 1.0, LIVE_SHADOW: 4.0, PAPER_SIMULATION: 9.0}
#: observations a class needs before its figure is MEASURED (below it the
#: figure is shown as INSUFFICIENT_SAMPLE and never used for LIVE)
MIN_N = 20
Z95 = 1.959964

MEASURED = "MEASURED"
INSUFFICIENT = "INSUFFICIENT_SAMPLE"
UNAVAILABLE = "UNAVAILABLE"
UNMEASURED = "UNMEASURED"

#: why ACTUAL is unmeasured on the canonical path today
R_NO_ACTUAL = (
    "NO_REAL_VENUE_FILL_ON_THE_CANONICAL_PATH: SMALL LIVE is SHADOW "
    "(small_live_control.mode is CHECKed 'SHADOW' and "
    "canonical_intent_executions admits only PAPER/SIMULATED and "
    "SMALL_LIVE/SHADOW, migration 225), so small_live_order_events holds no "
    "venue fill; a LIVE estimate is therefore derived from shadow or "
    "simulation and says so")

LIVE_USE = {
    ACTUAL: "MEASURED_ON_ACTUAL_VENUE_FILLS",
    LIVE_SHADOW: "DERIVED_FROM_LIVE_SHADOW_NOT_PROOF_OF_LIVE_EXECUTION",
    PAPER_SIMULATION: ("DERIVED_FROM_PAPER_SIMULATION_NOT_PROOF_OF_LIVE_"
                       "EXECUTION"),
    NO_FILL_EVIDENCE: ("DISPLAYED_BOOK_ONLY_NOT_FITTED_ON_ANY_FILL_NOT_PROOF_"
                       "OF_LIVE_EXECUTION"),
}

RULE = {
    "version": VERSION,
    "classes": list(CLASSES),
    "live_precedence": list(LIVE_PRECEDENCE),
    "transfer_penalty": dict(TRANSFER_PENALTY),
    "min_n": MIN_N,
    "statement": (
        "a LIVE execution estimate uses ACTUAL when >= %d canonical venue "
        "observations exist; otherwise it is labelled DERIVED_FROM_LIVE_"
        "SHADOW or DERIVED_FROM_PAPER_SIMULATION and its interval is the "
        "class interval at n_eff / K (K = %g shadow, %g simulation) -- a "
        "pre-declared prior, not a measured sim-to-live gap. Simulated or "
        "shadow fills are never presented as proof of live execution."
        % (MIN_N, TRANSFER_PENALTY[LIVE_SHADOW],
           TRANSFER_PENALTY[PAPER_SIMULATION])),
}
RULE_SHA = hashlib.sha256(json.dumps(RULE, sort_keys=True).encode()
                          ).hexdigest()


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _r(v, n=6):
    return None if v is None else round(float(v), n)


# ═════════════════════════════════════════════════════════════════════
# STATISTICS (pure)
# ═════════════════════════════════════════════════════════════════════

T975 = (12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262,
        2.228, 2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101,
        2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052,
        2.048, 2.045, 2.042)


def t_crit_95(df) -> float:
    """Two-sided 95% Student-t critical value (table to df 30, then the
    Cornish-Fisher expansion about z)."""
    df = int(df)
    if df < 1:
        raise ValueError("df must be >= 1")
    if df <= len(T975):
        return T975[df - 1]
    z = Z95
    return (z + (z ** 3 + z) / (4 * df)
            + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2))


def wilson(p, n, z: float = Z95) -> tuple:
    """Wilson score interval of a proportion p over an EFFECTIVE sample n
    (n may be fractional). (None, None) when n <= 0."""
    p, n = _num(p), _num(n)
    if p is None or n is None or n <= 0:
        return None, None
    p = min(1.0, max(0.0, p))
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(max(0.0, p * (1 - p) / n + z * z / (4 * n * n))) / d
    return max(0.0, c - h), min(1.0, c + h)


def _clusters(clusters, n):
    if clusters is None:
        return list(range(n))
    out = list(clusters)
    if len(out) != n:
        raise ValueError("one cluster key per observation")
    return [("__solo__", i) if c is None else c for i, c in enumerate(out)]


def clustered_mean(xs: list, clusters=None) -> dict:
    """Mean with a CLUSTER-ROBUST (CR1) standard error: observations on one
    independent event (cluster) are not independent of each other. The
    interval is mean +/- t(G-1) x SE. Pure."""
    vals = [(_num(x), c) for x, c in zip(xs, _clusters(clusters, len(xs)))]
    vals = [(x, c) for x, c in vals if x is not None]
    n = len(vals)
    if n == 0:
        return {"value": None, "n": 0, "clusters": 0, "se": None,
                "ci_low": None, "ci_high": None,
                "ci_method": "CLUSTER_ROBUST_MEAN_T", "why": "NO_OBSERVATION"}
    m = sum(x for x, _ in vals) / n
    by: dict = {}
    for x, c in vals:
        by[c] = by.get(c, 0.0) + (x - m)
    g = len(by)
    if g < 2:
        return {"value": _r(m, 9), "n": n, "clusters": g, "se": None,
                "ci_low": None, "ci_high": None,
                "ci_method": "CLUSTER_ROBUST_MEAN_T",
                "why": "FEWER_THAN_TWO_INDEPENDENT_EVENTS"}
    var = (g / (g - 1)) * sum(s * s for s in by.values()) / (n * n)
    se = math.sqrt(max(0.0, var))
    t = t_crit_95(g - 1)
    return {"value": _r(m, 9), "n": n, "clusters": g, "se": _r(se, 9),
            "ci_low": _r(m - t * se, 9), "ci_high": _r(m + t * se, 9),
            "ci_method": "CLUSTER_ROBUST_MEAN_T", "why": None}


def clustered_proportion(ys: list, clusters=None) -> dict:
    """A proportion with its Wilson interval at the EFFECTIVE sample size
    n / deff, where deff = (cluster-robust variance) / (binomial variance),
    floored at 1: clustered outcomes never buy a narrower interval. Pure."""
    obs = [(1.0 if bool(y) else 0.0, c) for y, c in zip(
        ys, _clusters(clusters, len(ys))) if y is not None]
    n = len(obs)
    if n == 0:
        return {"value": None, "n": 0, "clusters": 0, "deff": None,
                "n_eff": None, "ci_low": None, "ci_high": None,
                "ci_method": "WILSON_AT_CLUSTER_EFFECTIVE_N",
                "why": "NO_OBSERVATION"}
    p = sum(y for y, _ in obs) / n
    cm = clustered_mean([y for y, _ in obs], [c for _, c in obs])
    g = cm["clusters"]
    bin_var = p * (1 - p) / n
    deff = 1.0
    if cm["se"] is not None and bin_var > 0:
        deff = max(1.0, (cm["se"] ** 2) / bin_var)
    n_eff = n / deff
    lo, hi = wilson(p, n_eff)
    return {"value": _r(p, 9), "n": n, "clusters": g, "deff": _r(deff, 6),
            "n_eff": _r(n_eff, 6), "ci_low": _r(lo, 9), "ci_high": _r(hi, 9),
            "ci_method": "WILSON_AT_CLUSTER_EFFECTIVE_N", "why": None}


def clustered_ratio(nums: list, dens: list, clusters=None) -> dict:
    """sum(num) / sum(den) with a linearized cluster-robust interval. Pure."""
    rows = [(_num(a), _num(b), c) for a, b, c in zip(
        nums, dens, _clusters(clusters, len(nums)))]
    rows = [(a, b, c) for a, b, c in rows if a is not None and b is not None]
    sd = sum(b for _, b, _ in rows)
    if not rows or sd <= 0:
        return {"value": None, "n": len(rows), "clusters": 0,
                "ci_low": None, "ci_high": None,
                "ci_method": "CLUSTER_ROBUST_RATIO_LINEARIZED",
                "why": "NO_OBSERVATION" if not rows else "ZERO_DENOMINATOR"}
    r = sum(a for a, _, _ in rows) / sd
    mb = sd / len(rows)
    d = [(a - r * b) / mb for a, b, _ in rows]
    cm = clustered_mean(d, [c for _, _, c in rows])
    out = {"value": _r(r, 9), "n": len(rows), "clusters": cm["clusters"],
           "ci_low": None, "ci_high": None,
           "ci_method": "CLUSTER_ROBUST_RATIO_LINEARIZED", "why": cm["why"]}
    if cm["se"] is not None:
        t = t_crit_95(cm["clusters"] - 1)
        out["ci_low"] = _r(r - t * cm["se"], 9)
        out["ci_high"] = _r(r + t * cm["se"], 9)
    return out


# ═════════════════════════════════════════════════════════════════════
# THE LIVE RULE (pure)
# ═════════════════════════════════════════════════════════════════════

def status_of(stat: dict) -> str:
    if not stat or stat.get("value") is None:
        return UNAVAILABLE
    return MEASURED if (stat.get("n") or 0) >= MIN_N else INSUFFICIENT


def widen(stat: dict, evidence_class: str, *, kind: str) -> dict:
    """The LIVE interval of a class statistic: the class interval at the
    effective sample size divided by TRANSFER_PENALTY[class]. `kind` is
    'proportion' or 'mean'. ACTUAL is not widened. Pure."""
    k = TRANSFER_PENALTY.get(evidence_class)
    v = _num((stat or {}).get("value"))
    if k is None or v is None:
        return {"live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": k,
                "why": "NO_VALUE" if v is None else "NOT_A_FILL_CLASS"}
    if kind == "proportion":
        n_eff = _num(stat.get("n_eff")) or _num(stat.get("n"))
        lo, hi = wilson(v, None if n_eff is None else n_eff / k)
        return {"live_ci_low": _r(lo, 9), "live_ci_high": _r(hi, 9),
                "transfer_penalty": k, "why": None if lo is not None
                else "NO_EFFECTIVE_SAMPLE"}
    lo, hi = _num(stat.get("ci_low")), _num(stat.get("ci_high"))
    if lo is None or hi is None:
        return {"live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": k, "why": "NO_CLASS_INTERVAL"}
    s = math.sqrt(k)
    return {"live_ci_low": _r(v - (v - lo) * s, 9),
            "live_ci_high": _r(v + (hi - v) * s, 9),
            "transfer_penalty": k, "why": None}


def choose_live(by_class: dict, *, kind: str) -> dict:
    """THE LIVE ESTIMATE of one metric from the three class statistics
    {class: stat}: the first class in LIVE_PRECEDENCE whose figure is
    MEASURED, labelled with what it was fitted on and widened unless it is
    ACTUAL. UNAVAILABLE (with the reason per class) when none is."""
    reasons = {}
    for cls in LIVE_PRECEDENCE:
        st = by_class.get(cls) or {}
        s = status_of(st)
        if s == MEASURED:
            w = widen(st, cls, kind=kind)
            return {"status": MEASURED, "fitted_on": cls,
                    "live_use": LIVE_USE[cls],
                    "is_proof_of_live_execution": cls == ACTUAL,
                    "value": st.get("value"), "n": st.get("n"),
                    "clusters": st.get("clusters"),
                    "class_ci_low": st.get("ci_low"),
                    "class_ci_high": st.get("ci_high"),
                    "live_ci_low": w["live_ci_low"],
                    "live_ci_high": w["live_ci_high"],
                    "transfer_penalty": w["transfer_penalty"],
                    "not_used": reasons, "rule_version": VERSION}
        reasons[cls] = (st.get("why") or (
            "FEWER_THAN_%d_OBSERVATIONS (n=%s)" % (MIN_N, st.get("n") or 0)
            if s == INSUFFICIENT else "NOT_MEASURED"))
    return {"status": UNAVAILABLE, "fitted_on": None, "live_use": None,
            "is_proof_of_live_execution": False, "value": None,
            "why": "NO_CLASS_IS_MEASURED", "not_used": reasons,
            "rule_version": VERSION}


def provenance(fitted_on: str, *, basis: str, n=None, extra=None) -> dict:
    """WHAT ONE ESTIMATE WAS FITTED ON, as it travels with the estimate.
    Pure."""
    if fitted_on not in FITTED_ON_VALUES and fitted_on != UNMEASURED:
        raise ValueError("unknown evidence class %r" % (fitted_on,))
    out = {"version": VERSION, "fitted_on": fitted_on, "basis": basis,
           "n": n,
           "live_use": LIVE_USE.get(fitted_on, "UNMEASURED_NOT_USED"),
           "is_proof_of_live_execution": fitted_on == ACTUAL,
           "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
           "actual": {"status": UNMEASURED, "why": R_NO_ACTUAL}}
    out.update(extra or {})
    return out


def proportion_view(value, numerator, denominator, fitted_on: str) -> dict:
    """A fitted proportion (e.g. a fill rate k / n) with its class interval
    and its LIVE (widened) interval. Pure."""
    v, k, n = _num(value), _num(numerator), _num(denominator)
    if v is None and k is not None and n:
        v = k / n
    if v is None or not n:
        # a rate with no recorded sample size cannot be bounded: the value
        # (if any) is shown, its intervals are not invented
        return {"value": _r(v, 9), "n": int(n or 0), "fitted_on": fitted_on,
                "class_ci_low": None, "class_ci_high": None,
                "live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
                "live_use": LIVE_USE.get(fitted_on),
                "why": ("NOT_MEASURED" if v is None
                        else "NO_SAMPLE_SIZE_RECORDED_TO_BOUND_IT")}
    lo, hi = wilson(v, n)
    w = widen({"value": v, "n": n, "n_eff": n}, fitted_on,
              kind="proportion")
    return {"value": _r(v, 9), "n": int(n), "fitted_on": fitted_on,
            "class_ci_low": _r(lo, 9), "class_ci_high": _r(hi, 9),
            "live_ci_low": w["live_ci_low"], "live_ci_high": w["live_ci_high"],
            "transfer_penalty": w["transfer_penalty"],
            "live_use": LIVE_USE.get(fitted_on), "why": None}


def mean_view(mean, sd, n, fitted_on: str) -> dict:
    """A fitted mean (e.g. a markout) with its class and LIVE intervals
    (normal, unclustered: the per-observation rows are not kept). Pure."""
    m, s, n = _num(mean), _num(sd), _num(n)
    if m is None or s is None or not n or n < 2:
        return {"value": _r(m, 9), "n": int(n or 0), "fitted_on": fitted_on,
                "class_ci_low": None, "class_ci_high": None,
                "live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
                "why": "NO_SPREAD_OR_FEWER_THAN_TWO"}
    t = t_crit_95(int(n) - 1)
    se = s / math.sqrt(n)
    k = TRANSFER_PENALTY.get(fitted_on) or 1.0
    return {"value": _r(m, 9), "n": int(n), "sd": _r(s, 9),
            "fitted_on": fitted_on,
            "class_ci_low": _r(m - t * se, 9),
            "class_ci_high": _r(m + t * se, 9),
            "live_ci_low": _r(m - t * se * math.sqrt(k), 9),
            "live_ci_high": _r(m + t * se * math.sqrt(k), 9),
            "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
            "live_use": LIVE_USE.get(fitted_on), "why": None}
