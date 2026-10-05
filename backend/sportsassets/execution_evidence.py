"""THE THREE EXECUTION-EVIDENCE CLASSES (R30C). Pure: no I/O, and nothing
here imports a paper, order, venue, execution or funded module.

OWNER AUDIT 2026-10-04: "real fill / slippage / adverse-selection / cancel /
recovery data must eventually dominate LIVE execution estimates; simulated
fills cannot prove live execution quality." Until this module every LIVE-path
execution estimate (Archer's fill probability, adverse selection and time to
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
                    DISPLAYED book and was fitted on no fill at all (Archer's
                    spread / slippage walk; the external-valuation lane's
                    marketable coverage). Displayed depth is not guaranteed
                    depth.

THE LIVE RULE. A LIVE execution estimate may use ACTUAL when it exists
(MIN_N INDEPENDENT EVENTS, with a clustered interval). Otherwise it is
LABELLED as derived from shadow or simulation and its interval is WIDENED by
a pre-declared transfer penalty K (the effective sample size is divided by
K, so the interval is ~sqrt(K) wider): K = 4 for LIVE_SHADOW, 9 for
PAPER_SIMULATION -- and, for a mean, never narrower than a declared
absolute floor (MEAN_TRANSFER_FLOOR: one $0.01 tick per contract, five
points of a quantity share), so a class whose observations happen to agree
exactly (zero variance) can never yield a zero-width LIVE interval. K and
the floors ARE NOT MEASUREMENTS of the sim-to-live gap -- nobody has one,
because no canonical live fill exists -- they are declared, versioned priors
that ACTUAL fills replace. Changing one is a new VERSION.

WHAT THE SHADOW CAN MEASURE (R30C review). The SMALL LIVE SHADOW order is
sized by size_within_edge WITHIN the decision book's depth at its limit,
scaled 1:1,000, and walked through THAT SAME book with nothing consumed, no
latency and no queue: it fills fully at the touch by construction. Its fill
rate, quantity share, slippage, cancel and recovery figures are therefore a
walk of the displayed book (NO_FILL_EVIDENCE) -- shown beside the other
classes with that note, and NEVER eligible for a LIVE fill / slippage /
cancel / recovery estimate (LIVE_ELIGIBLE). What the shadow does measure on
real venue books is the market's move after the decision at live size (a
tiny taker order has no fill selection: every decision would have filled),
so it stays eligible -- ahead of the simulator -- for adverse selection.

INDEPENDENT EVENTS. Every interval here is clustered by independent event;
with fewer than two events no clustered interval exists, and a figure is
MEASURED only with >= MIN_N independent events AND an interval -- never on
raw observations that all sit on one event, never with a null interval.
"""
from __future__ import annotations

import hashlib
import json
import math

VERSION = "EXECUTION_EVIDENCE_V2"

PAPER_SIMULATION = "PAPER_SIMULATION"
LIVE_SHADOW = "LIVE_SHADOW"
ACTUAL = "ACTUAL"
CLASSES = (PAPER_SIMULATION, LIVE_SHADOW, ACTUAL)
NO_FILL_EVIDENCE = "NO_FILL_EVIDENCE_DISPLAYED_BOOK_ONLY"
FITTED_ON_VALUES = CLASSES + (NO_FILL_EVIDENCE,)

#: the order in which a LIVE estimate may draw on a class (pre-declared);
#: a metric family may admit only some of them (LIVE_ELIGIBLE)
LIVE_PRECEDENCE = (ACTUAL, LIVE_SHADOW, PAPER_SIMULATION)
#: effective-sample-size divisor for a LIVE estimate drawn from the class
TRANSFER_PENALTY = {ACTUAL: 1.0, LIVE_SHADOW: 4.0, PAPER_SIMULATION: 9.0}
#: the absolute floor of a LIVE mean's half-width when it is not ACTUAL, by
#: unit (declared priors, not measurements): one $0.01 price tick per
#: contract -- the smallest move a live order can meet between the decision
#: and its arrival -- and five points of a requested-quantity share
USD_PER_CONTRACT, FRACTION = "USD_PER_CONTRACT", "FRACTION"
MEAN_TRANSFER_FLOOR = {USD_PER_CONTRACT: 0.01, FRACTION: 0.05}
#: the metric families and the classes a LIVE estimate of each may use
FILL_FAMILY = "FILL_SLIPPAGE_CANCEL_RECOVERY"
MARKOUT_FAMILY = "ADVERSE_SELECTION_MARKOUT"
LIVE_ELIGIBLE = {FILL_FAMILY: (ACTUAL, PAPER_SIMULATION),
                 MARKOUT_FAMILY: (ACTUAL, LIVE_SHADOW, PAPER_SIMULATION)}
#: independent events a class needs before its figure is MEASURED (below it
#: the figure is shown as INSUFFICIENT_SAMPLE and never used for LIVE)
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

#: why the shadow's fill-family figures are never a LIVE estimate
R_SHADOW_TAUTOLOGICAL = (
    "LIVE_SHADOW_FILL_IS_TAUTOLOGICAL_AT_LIVE_SCALE: the shadow order is "
    "sized within the decision book's depth at its limit (size_within_edge),"
    " scaled 1:1,000 and walked through that same book with nothing "
    "consumed, no latency and no queue -- it fills fully at the touch by "
    "construction. This figure is a walk of the displayed book "
    "(NO_FILL_EVIDENCE_DISPLAYED_BOOK_ONLY), shown beside the other classes"
    " and never used for a LIVE fill / slippage / cancel / recovery estimate")

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
    "live_eligible": {k: list(v) for k, v in LIVE_ELIGIBLE.items()},
    "transfer_penalty": dict(TRANSFER_PENALTY),
    "mean_transfer_floor": dict(MEAN_TRANSFER_FLOOR),
    "min_independent_events": MIN_N,
    "statement": (
        "a LIVE execution estimate uses ACTUAL when >= %d independent "
        "events of canonical venue fills exist with a clustered interval; "
        "otherwise it is labelled DERIVED_FROM_LIVE_SHADOW or DERIVED_FROM_"
        "PAPER_SIMULATION and its interval is the class interval at n_eff / "
        "K (K = %g shadow, %g simulation), a mean's half-width never below "
        "its declared floor (%g USD per contract, %g of a quantity share) "
        "-- pre-declared priors, not a measured sim-to-live gap. The "
        "shadow's fill, quantity-share, slippage, cancel and recovery "
        "figures are a walk of the decision book that sized the order "
        "(tautological at live scale) and are never a LIVE estimate; it is "
        "eligible for adverse selection only. Simulated or shadow fills are "
        "never presented as proof of live execution."
        % (MIN_N, TRANSFER_PENALTY[LIVE_SHADOW],
           TRANSFER_PENALTY[PAPER_SIMULATION],
           MEAN_TRANSFER_FLOOR[USD_PER_CONTRACT],
           MEAN_TRANSFER_FLOOR[FRACTION])),
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
    floored at 1: clustered outcomes never buy a narrower interval. With
    FEWER THAN TWO independent events the cluster-robust variance does not
    exist, so there is no interval and no effective sample size (R30C
    review: 40 orders on one event are one observation of that event, not
    40). Pure."""
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
    if cm["se"] is None:
        return {"value": _r(p, 9), "n": n, "clusters": g, "deff": None,
                "n_eff": None, "ci_low": None, "ci_high": None,
                "ci_method": "WILSON_AT_CLUSTER_EFFECTIVE_N",
                "why": cm["why"] or "NO_CLUSTER_ROBUST_VARIANCE"}
    bin_var = p * (1 - p) / n
    deff = 1.0
    if bin_var > 0:
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

def independent_events(stat: dict) -> int:
    """The independent events a statistic rests on: its cluster count, or
    its observation count when no clustering was possible (each observation
    its own event)."""
    st = stat or {}
    c = st.get("clusters")
    return int(c if c is not None else (st.get("n") or 0))


def status_of(stat: dict) -> str:
    """MEASURED only with >= MIN_N INDEPENDENT EVENTS and an interval;
    otherwise INSUFFICIENT_SAMPLE (shown, never used for LIVE), or
    UNAVAILABLE when there is no value at all."""
    if not stat or stat.get("value") is None:
        return UNAVAILABLE
    if independent_events(stat) < MIN_N:
        return INSUFFICIENT
    if stat.get("ci_low") is None or stat.get("ci_high") is None:
        return INSUFFICIENT
    return MEASURED


def insufficient_why(stat: dict) -> str:
    st = stat or {}
    ev = independent_events(st)
    if ev < MIN_N:
        return "FEWER_THAN_%d_INDEPENDENT_EVENTS (events=%d, n=%s)" % (
            MIN_N, ev, st.get("n") or 0)
    return "NO_CLUSTERED_INTERVAL: %s" % (st.get("why") or "UNDEFINED")


def mean_floor(evidence_class: str, floor_unit: str | None) -> float:
    """The absolute floor of a LIVE mean's half-width: none for ACTUAL (it
    is the live measurement), the declared MEAN_TRANSFER_FLOOR of the unit
    otherwise."""
    if evidence_class == ACTUAL or floor_unit is None:
        return 0.0
    return float(MEAN_TRANSFER_FLOOR.get(floor_unit, 0.0))


def widen(stat: dict, evidence_class: str, *, kind: str,
          floor_unit: str | None = None) -> dict:
    """The LIVE interval of a class statistic: the class interval at the
    effective sample size divided by TRANSFER_PENALTY[class]; for a mean the
    half-width is also never below mean_floor(class, unit), so a
    zero-variance class still gets a non-zero LIVE width. `kind` is
    'proportion' or 'mean'. ACTUAL is not widened. Pure."""
    k = TRANSFER_PENALTY.get(evidence_class)
    v = _num((stat or {}).get("value"))
    if k is None or v is None:
        return {"live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": k, "transfer_floor": None,
                "why": "NO_VALUE" if v is None else "NOT_A_FILL_CLASS"}
    if kind == "proportion":
        n_eff = _num(stat.get("n_eff")) or _num(stat.get("n"))
        lo, hi = wilson(v, None if n_eff is None else n_eff / k)
        return {"live_ci_low": _r(lo, 9), "live_ci_high": _r(hi, 9),
                "transfer_penalty": k, "transfer_floor": None,
                "why": None if lo is not None else "NO_EFFECTIVE_SAMPLE"}
    lo, hi = _num(stat.get("ci_low")), _num(stat.get("ci_high"))
    if lo is None or hi is None:
        return {"live_ci_low": None, "live_ci_high": None,
                "transfer_penalty": k, "transfer_floor": None,
                "why": "NO_CLASS_INTERVAL"}
    s = math.sqrt(k)
    fl = mean_floor(evidence_class, floor_unit)
    return {"live_ci_low": _r(v - max((v - lo) * s, fl), 9),
            "live_ci_high": _r(v + max((hi - v) * s, fl), 9),
            "transfer_penalty": k, "transfer_floor": fl or None,
            "why": None}


def choose_live(by_class: dict, *, kind: str, eligible=None,
                ineligible_why: dict | None = None,
                floor_unit: str | None = None) -> dict:
    """THE LIVE ESTIMATE of one metric from the three class statistics
    {class: stat}: the first class in LIVE_PRECEDENCE that is ELIGIBLE for
    the metric and whose figure is MEASURED (>= MIN_N independent events
    with an interval), labelled with what it was fitted on and widened
    unless it is ACTUAL. Never MEASURED without a LIVE interval. UNAVAILABLE
    (with the reason per class) when no class qualifies."""
    reasons = {}
    elig = tuple(eligible) if eligible is not None else LIVE_PRECEDENCE
    for cls in LIVE_PRECEDENCE:
        st = by_class.get(cls) or {}
        if cls not in elig:
            reasons[cls] = ((ineligible_why or {}).get(cls)
                            or "NOT_ELIGIBLE_FOR_THIS_METRIC")
            continue
        s = status_of(st)
        if s == MEASURED:
            w = widen(st, cls, kind=kind, floor_unit=floor_unit)
            if w["live_ci_low"] is None or w["live_ci_high"] is None:
                reasons[cls] = "NO_LIVE_INTERVAL: %s" % w["why"]
                continue
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
                    "transfer_floor": w["transfer_floor"],
                    "eligible_classes": list(elig),
                    "not_used": reasons, "rule_version": VERSION}
        reasons[cls] = (insufficient_why(st) if s == INSUFFICIENT
                        else st.get("why") or "NOT_MEASURED")
    return {"status": UNAVAILABLE, "fitted_on": None, "live_use": None,
            "is_proof_of_live_execution": False, "value": None,
            "why": "NO_ELIGIBLE_CLASS_IS_MEASURED",
            "eligible_classes": list(elig), "not_used": reasons,
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


def proportion_view(value, numerator, denominator, fitted_on: str, *,
                    n_eff=None, clusters=None) -> dict:
    """A fitted proportion (e.g. a fill rate k / n) with its class interval
    and its LIVE (widened) interval. Pure. A value the fitter declared
    unmeasured (None) gets NO interval -- it is never re-derived from the
    numerator (R30C review). `n_eff` / `clusters`: the cluster-effective
    sample size and independent events, when the fitter kept event keys;
    without them the interval treats every observation as independent and
    says so."""
    v, n = _num(value), _num(denominator)
    ne = _num(n_eff)
    base = {"n": int(n or 0), "numerator": numerator,
            "clusters": clusters, "n_eff": _r(ne, 6), "fitted_on": fitted_on,
            "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
            "live_use": LIVE_USE.get(fitted_on),
            "ci_method": ("WILSON_AT_CLUSTER_EFFECTIVE_N" if ne is not None
                          else "WILSON_UNCLUSTERED_NO_EVENT_KEY_RECORDED")}
    if v is None or not n:
        # a rate with no value or no recorded sample size cannot be
        # bounded: the value (if any) is shown, its intervals are not
        # invented
        return dict(base, value=_r(v, 9), class_ci_low=None,
                    class_ci_high=None, live_ci_low=None, live_ci_high=None,
                    why=("NOT_MEASURED" if v is None
                         else "NO_SAMPLE_SIZE_RECORDED_TO_BOUND_IT"))
    m = ne if ne is not None else n
    lo, hi = wilson(v, m)
    w = widen({"value": v, "n": n, "n_eff": m}, fitted_on,
              kind="proportion")
    return dict(base, value=_r(v, 9), class_ci_low=_r(lo, 9),
                class_ci_high=_r(hi, 9), live_ci_low=w["live_ci_low"],
                live_ci_high=w["live_ci_high"], why=None)


def mean_view(mean, sd, n, fitted_on: str, *, se=None, clusters=None,
              floor_unit: str = USD_PER_CONTRACT) -> dict:
    """A fitted mean (e.g. a markout) with its class and LIVE intervals.
    With `se` / `clusters` (a cluster-robust standard error over that many
    independent events) the interval is t(G-1) x se; otherwise normal over
    n unclustered observations, labelled so. The LIVE half-width is the
    class half-width x sqrt(K), never below mean_floor(class, unit), so a
    zero-spread history never yields a zero-width LIVE interval. Pure."""
    m, s, n = _num(mean), _num(sd), _num(n)
    sec, g = _num(se), (int(clusters) if clusters is not None else None)
    k = TRANSFER_PENALTY.get(fitted_on) or 1.0
    fl = mean_floor(fitted_on, floor_unit)
    base = {"n": int(n or 0), "fitted_on": fitted_on, "clusters": g,
            "transfer_penalty": TRANSFER_PENALTY.get(fitted_on),
            "transfer_floor": fl or None,
            "live_use": LIVE_USE.get(fitted_on)}
    if m is not None and sec is not None and g is not None and g >= 2:
        half = t_crit_95(g - 1) * sec
        method = "CLUSTER_ROBUST_MEAN_T"
    elif m is not None and s is not None and n and n >= 2:
        half = t_crit_95(int(n) - 1) * s / math.sqrt(n)
        method = "NORMAL_T_UNCLUSTERED_NO_EVENT_KEY_RECORDED"
    else:
        return dict(base, value=_r(m, 9), sd=_r(s, 9), class_ci_low=None,
                    class_ci_high=None, live_ci_low=None, live_ci_high=None,
                    ci_method=None, why="NO_SPREAD_OR_FEWER_THAN_TWO")
    live_half = max(half * math.sqrt(k), fl)
    return dict(base, value=_r(m, 9), sd=_r(s, 9), ci_method=method,
                class_ci_low=_r(m - half, 9), class_ci_high=_r(m + half, 9),
                live_ci_low=_r(m - live_half, 9),
                live_ci_high=_r(m + live_half, 9), why=None)


def live_executable_bounds(*, net_pp, adverse_pp, adverse_live: tuple,
                           fill_live: tuple, qty, fitted_on: str) -> dict:
    """THE LIVE INTERVAL OF AN EXECUTABLE NET EDGE (per contract) AND ITS EV
    (R30C review: the label alone is not the wider uncertainty the spec
    asks for). From the point net edge (which already subtracts
    `adverse_pp`), the LIVE interval of the adverse-selection markout and
    of the fill probability, and the executable quantity:

      edge_low  = net + adverse_pp - max(0, adverse_live_high)
                  - mean_floor(class, USD_PER_CONTRACT)   [the displayed-
                  book walk's spread / slippage were fitted on no fill: one
                  declared tick of transfer on the downside]
      edge_high = net + adverse_pp - max(0, adverse_live_low)
      EV_low    = edge_low  x (fill_high if edge_low < 0 else fill_low) x q
      EV_high   = edge_high x (fill_low if edge_high < 0 else fill_high) x q

    UNAVAILABLE with the missing input named when any is absent. ACTUAL
    takes no floor. Pure."""
    n, a = _num(net_pp), _num(adverse_pp)
    alo, ahi = (_num(x) for x in (adverse_live or (None, None)))
    flo, fhi = (_num(x) for x in (fill_live or (None, None)))
    q = _num(qty)
    missing = [k for k, v in (("net_executable_edge", n),
                              ("adverse_selection_used", a),
                              ("adverse_selection_live_interval",
                               None if alo is None or ahi is None else 1),
                              ("fill_probability_live_interval",
                               None if flo is None or fhi is None else 1),
                              ("executable_qty", q)) if v is None]
    if missing:
        return {"status": UNAVAILABLE, "fitted_on": fitted_on,
                "why": "LIVE_INTERVAL_INPUTS_UNMEASURED: %s"
                       % ", ".join(missing)}
    fl = mean_floor(fitted_on, USD_PER_CONTRACT)
    e_lo = n + a - max(0.0, ahi) - fl
    e_hi = n + a - max(0.0, alo)
    ev_lo = e_lo * (fhi if e_lo < 0 else flo) * q
    ev_hi = e_hi * (flo if e_hi < 0 else fhi) * q
    return {"status": MEASURED, "fitted_on": fitted_on,
            "live_use": LIVE_USE.get(fitted_on),
            "is_proof_of_live_execution": fitted_on == ACTUAL,
            "expected_net_executable_edge_pp": {"low": _r(e_lo, 9),
                                                "high": _r(e_hi, 9)},
            "expected_executable_ev_usd": {"low": _r(ev_lo, 6),
                                           "high": _r(ev_hi, 6)},
            "transfer_floor_per_contract": fl or None,
            "basis": ("the point edge with adverse selection at its LIVE "
                      "interval bounds, minus one declared tick of transfer "
                      "for the displayed-book walk on the downside; EV at "
                      "the fill probability's LIVE interval bound that "
                      "makes it worst / best"),
            "why": None}


def fill_probability_label(est_row: dict | None) -> dict:
    """THE DISPLAY LABEL of a recorded Archer estimate's fill probability
    (R30C review: the classes are kept apart in DISPLAY too): the class it
    was fitted on, its live use and LIVE interval, from the estimate's own
    execution_evidence (persisted in `inputs`). An estimate recorded before
    R30C carries none; its history was still the paper simulator's
    (agents/archer.history_stats reads paper_orders / paper_fills only), so
    it is labelled PAPER_SIMULATION and says the label was inferred.
    Pure."""
    r = est_row if isinstance(est_row, dict) else {}
    ins = r.get("inputs")
    if isinstance(ins, str):
        try:
            ins = json.loads(ins)
        except ValueError:
            ins = None
    ins = ins if isinstance(ins, dict) else {}
    ev = r.get("execution_evidence") or ins.get("execution_evidence") or {}
    fp = ev.get("fill_probability") or {}
    if ev.get("fitted_on"):
        return {"fitted_on": ev["fitted_on"],
                "live_use": ev.get("live_use"),
                "is_proof_of_live_execution": bool(
                    ev.get("is_proof_of_live_execution")),
                "live_ci_low": fp.get("live_ci_low"),
                "live_ci_high": fp.get("live_ci_high"),
                "n": fp.get("n"), "clusters": fp.get("clusters"),
                "basis": "the estimate's own execution_evidence"}
    return {"fitted_on": PAPER_SIMULATION,
            "live_use": LIVE_USE[PAPER_SIMULATION],
            "is_proof_of_live_execution": False,
            "live_ci_low": None, "live_ci_high": None,
            "basis": ("LABEL_INFERRED_ESTIMATE_PREDATES_R30C: Eddie's history "
                      "has only ever been paper_orders / paper_fills")}
