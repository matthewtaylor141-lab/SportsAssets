#!/usr/bin/env python3
"""EV CORE -- SCORING AND CALIBRATION. How good is a probability, really?

WHY NOT ACCURACY. A forecast that says 0.93 and is right 93% of the time is
perfect and has an accuracy of 93%. A forecast that says 1.0 every time is
useless and also has an accuracy of 93%. Accuracy cannot tell them apart, so it
is not used here. The primary metrics are strictly proper scoring rules -- log
loss and Brier -- under which the honest probability is the optimal report.

THE DECOMPOSITION IS WHERE THE INFORMATION IS. Brier splits into

    RELIABILITY (how far the stated probabilities sit from the outcomes they
      actually deliver -- lower is better, zero is perfect calibration)
  - RESOLUTION (how far the forecasts move away from the base rate -- higher is
      better, zero means the forecast says nothing)
  + UNCERTAINTY (the base rate's own variance; a property of the problem, not
      of the forecast)

A model can improve its Brier by being better calibrated OR by being sharper,
and those are different achievements. Reporting only the total hides which.

THE INDEPENDENT UNIT IS THE EVENT, NOT THE ROW. Sixteen totals lines on one
fixture share one outcome. A bootstrap that resamples ROWS would treat them as
sixteen independent draws and report an interval about four times too narrow.
Every interval here resamples EVENTS, carrying all their rows together.

CALIBRATION SLOPE AND INTERCEPT, AND WHAT THEY MEAN. Regress the outcome on the
forecast's log-odds. Slope 1 and intercept 0 is perfect. Slope below 1 means the
forecast is OVERCONFIDENT -- its extremes are too extreme. Slope above 1 means
it is underconfident. The intercept picks up a systematic bias toward YES or NO.

This module contacts nothing and can place no order.
"""
import json
import math
import random
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

EPS = 1e-12
PRIMARY_METRICS = ("LOG_LOSS", "BRIER")
WHY_NOT_ACCURACY = (
    "accuracy cannot distinguish a well-calibrated forecast from a confident "
    "one; strictly proper scores can, and are optimised by honesty")
INDEPENDENT_UNIT = "EVENT"
WHY_EVENT_CLUSTERED = (
    "contracts on one fixture share one outcome; resampling rows instead of "
    "events reports an interval far narrower than the evidence supports")

# Default reliability bins. Wider at the extremes is deliberate: prediction
# markets put most of their mass near 0 and 1, and equal-width bins there hold
# almost no observations.
DEFAULT_BINS = (0.0, 0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
                0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0)


def _clip(p):
    return min(1.0 - 1e-9, max(1e-9, float(p)))


def log_loss(pairs):
    """Mean negative log likelihood. Lower is better."""
    pairs = list(pairs)
    if not pairs:
        return NOT_IDENTIFIED
    s = 0.0
    for p, y in pairs:
        p = _clip(p)
        s -= math.log(p) if y else math.log(1.0 - p)
    return s / len(pairs)


def brier(pairs):
    """Mean squared error of the probability. Lower is better."""
    pairs = list(pairs)
    if not pairs:
        return NOT_IDENTIFIED
    return sum((float(p) - float(y)) ** 2 for p, y in pairs) / len(pairs)


def brier_decomposition(pairs, bins=DEFAULT_BINS):
    """RELIABILITY - RESOLUTION + UNCERTAINTY, and the identity is checked."""
    pairs = list(pairs)
    n = len(pairs)
    if not n:
        return {"RELIABILITY": NOT_IDENTIFIED, "RESOLUTION": NOT_IDENTIFIED,
                "UNCERTAINTY": NOT_IDENTIFIED}
    base = sum(y for _, y in pairs) / float(n)
    buckets = defaultdict(list)
    for p, y in pairs:
        buckets[_bin_of(p, bins)].append((p, y))
    rel = res = 0.0
    for _, rows in buckets.items():
        k = len(rows)
        pbar = sum(p for p, _ in rows) / k
        ybar = sum(y for _, y in rows) / float(k)
        rel += k * (pbar - ybar) ** 2
        res += k * (ybar - base) ** 2
    rel /= n
    res /= n
    unc = base * (1.0 - base)
    return {
        "RELIABILITY": rel, "RESOLUTION": res, "UNCERTAINTY": unc,
        "BASE_RATE": base,
        "DECOMPOSITION_IDENTITY": rel - res + unc,
        "READ_AS": ("reliability low is good (calibration); resolution high is "
                    "good (information); uncertainty is the problem's own"),
    }


def _bin_of(p, bins):
    for i in range(len(bins) - 1):
        if bins[i] <= p < bins[i + 1]:
            return i
    return len(bins) - 2


def reliability_curve(pairs, bins=DEFAULT_BINS):
    """Stated probability against delivered frequency, per bin."""
    buckets = defaultdict(list)
    for p, y in pairs:
        buckets[_bin_of(p, bins)].append((p, y))
    out = []
    for i in sorted(buckets):
        rows = buckets[i]
        k = len(rows)
        out.append({
            "BIN_LOW": bins[i], "BIN_HIGH": bins[i + 1], "N": k,
            "MEAN_FORECAST": sum(p for p, _ in rows) / k,
            "OBSERVED_FREQUENCY": sum(y for _, y in rows) / float(k),
            "GAP": (sum(p for p, _ in rows) / k
                    - sum(y for _, y in rows) / float(k)),
        })
    return out


def calibration_slope_intercept(pairs, iters=200):
    """Logistic regression of outcome on forecast log-odds.

    Newton steps on a two-parameter logistic. Slope 1 / intercept 0 is perfect;
    slope < 1 is overconfidence.
    """
    pairs = [(float(p), float(y)) for p, y in pairs]
    if len(pairs) < 10:
        return {"CALIBRATION_SLOPE": NOT_IDENTIFIED,
                "CALIBRATION_INTERCEPT": NOT_IDENTIFIED,
                "WHY": "fewer than 10 observations"}
    x = [math.log(_clip(p) / (1.0 - _clip(p))) for p, _ in pairs]
    y = [v for _, v in pairs]
    a, b = 0.0, 1.0                                   # intercept, slope
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for xi, yi in zip(x, y):
            z = a + b * xi
            mu = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
            w = max(mu * (1.0 - mu), 1e-10)
            d = yi - mu
            g0 += d
            g1 += d * xi
            h00 += w
            h01 += w * xi
            h11 += w * xi * xi
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    return {
        "CALIBRATION_INTERCEPT": a,
        "CALIBRATION_SLOPE": b,
        "PERFECT_IS": "slope 1.0, intercept 0.0",
        "SLOPE_BELOW_1_MEANS": "OVERCONFIDENT - the extremes are too extreme",
        "SLOPE_ABOVE_1_MEANS": "UNDERCONFIDENT - the forecast is too timid",
    }


def event_bootstrap(rows, metric, draws=200, seed=20260917):
    """Percentile interval, resampling EVENTS and carrying all their rows.

    `rows` carry EVENT_KEY, P and Y. `metric` takes [(p, y)] and returns a
    number. The seed is fixed so a reported interval is reproducible.
    """
    by = defaultdict(list)
    for r in rows:
        by[r["EVENT_KEY"]].append((r["P"], r["Y"]))
    keys = list(by)
    if len(keys) < 2:
        return {"POINT": metric([(r["P"], r["Y"]) for r in rows]),
                "LOWER": NOT_IDENTIFIED, "UPPER": NOT_IDENTIFIED,
                "EVENTS": len(keys),
                "WHY": "fewer than 2 independent events"}
    rnd = random.Random(seed)
    point = metric([(r["P"], r["Y"]) for r in rows])
    vals = []
    for _ in range(draws):
        pick = [keys[rnd.randrange(len(keys))] for _ in range(len(keys))]
        sample = [pr for k in pick for pr in by[k]]
        v = metric(sample)
        if isinstance(v, (int, float)):
            vals.append(v)
    vals.sort()
    if not vals:
        return {"POINT": point, "LOWER": NOT_IDENTIFIED,
                "UPPER": NOT_IDENTIFIED, "EVENTS": len(keys)}
    return {
        "POINT": point,
        "LOWER": vals[int(0.025 * (len(vals) - 1))],
        "UPPER": vals[int(0.975 * (len(vals) - 1))],
        "EVENTS": len(keys),
        "ROWS": len(rows),
        "DRAWS": len(vals),
        "RESAMPLED_UNIT": INDEPENDENT_UNIT,
        "WHY_EVENT_CLUSTERED": WHY_EVENT_CLUSTERED,
    }


def evaluate(rows, label="", bins=DEFAULT_BINS, draws=200):
    """The full scorecard for one forecast over one row set."""
    pairs = [(r["P"], r["Y"]) for r in rows]
    n_ev = len({r["EVENT_KEY"] for r in rows})
    out = {
        "LABEL": label,
        "N_ROWS": len(rows),
        "N_INDEPENDENT_EVENTS": n_ev,
        "LOG_LOSS": log_loss(pairs),
        "BRIER": brier(pairs),
        "PRIMARY_METRICS": list(PRIMARY_METRICS),
        "WHY_NOT_ACCURACY": WHY_NOT_ACCURACY,
    }
    out.update(brier_decomposition(pairs, bins))
    out.update(calibration_slope_intercept(pairs))
    out["RELIABILITY_CURVE"] = reliability_curve(pairs, bins)
    out["LOG_LOSS_CI"] = event_bootstrap(rows, log_loss, draws)
    out["BRIER_CI"] = event_bootstrap(rows, brier, draws)
    # Sharpness: how far the forecasts sit from the base rate. A flat forecast
    # can be perfectly calibrated and worthless, so this sits beside it.
    if pairs:
        base = sum(y for _, y in pairs) / float(len(pairs))
        out["SHARPNESS_MEAN_ABS_DEV_FROM_BASE"] = (
            sum(abs(p - base) for p, _ in pairs) / len(pairs))
    return out


def compare(a, b):
    """Is a's advantage over b larger than the event-clustered noise?"""
    def gap(x, y, key):
        va, vb = x.get(key), y.get(key)
        if not isinstance(va, (int, float)) or not isinstance(vb, (int, float)):
            return NOT_IDENTIFIED
        return vb - va                       # positive = a is better (lower)
    ll_a, ll_b = a.get("LOG_LOSS_CI", {}), b.get("LOG_LOSS_CI", {})
    overlap = NOT_IDENTIFIED
    if all(isinstance(ll_a.get(k), (int, float)) for k in ("LOWER", "UPPER")) \
            and all(isinstance(ll_b.get(k), (int, float))
                    for k in ("LOWER", "UPPER")):
        overlap = not (ll_a["UPPER"] < ll_b["LOWER"]
                       or ll_b["UPPER"] < ll_a["LOWER"])
    return {
        "A": a.get("LABEL"), "B": b.get("LABEL"),
        "LOG_LOSS_ADVANTAGE_OF_A": gap(a, b, "LOG_LOSS"),
        "BRIER_ADVANTAGE_OF_A": gap(a, b, "BRIER"),
        "LOG_LOSS_INTERVALS_OVERLAP": overlap,
        "A_IS_DISTINGUISHABLY_BETTER": (overlap is False
                                        and isinstance(gap(a, b, "LOG_LOSS"),
                                                       float)
                                        and gap(a, b, "LOG_LOSS") > 0),
        "CAUTION": ("overlapping event-clustered intervals mean the difference "
                    "is not established, however large the point gap looks"),
    }


def render(rep):
    L = ["%-34s = %s" % ("LABEL", rep.get("LABEL"))]
    for k in ("N_ROWS", "N_INDEPENDENT_EVENTS", "LOG_LOSS", "BRIER",
              "RELIABILITY", "RESOLUTION", "UNCERTAINTY", "BASE_RATE",
              "CALIBRATION_SLOPE", "CALIBRATION_INTERCEPT",
              "SHARPNESS_MEAN_ABS_DEV_FROM_BASE"):
        v = rep.get(k, NOT_IDENTIFIED)
        L.append("%-34s = %s" % (k, ("%.6f" % v) if isinstance(v, float)
                                 else v))
    for k in ("LOG_LOSS_CI", "BRIER_CI"):
        ci = rep.get(k) or {}
        if isinstance(ci.get("LOWER"), float):
            L.append("%-34s = %.6f  [%.6f, %.6f] over %d events"
                     % (k, ci["POINT"], ci["LOWER"], ci["UPPER"], ci["EVENTS"]))
    return "\n".join(L)


def render_curve(rep):
    L = ["%-14s %8s %14s %14s %10s" % ("BIN", "N", "MEAN_FORECAST",
                                       "OBSERVED_FREQ", "GAP")]
    for b in rep.get("RELIABILITY_CURVE", ()):
        L.append("%-14s %8d %14.4f %14.4f %10.4f"
                 % ("%.2f-%.2f" % (b["BIN_LOW"], b["BIN_HIGH"]), b["N"],
                    b["MEAN_FORECAST"], b["OBSERVED_FREQUENCY"], b["GAP"]))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
