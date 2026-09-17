#!/usr/bin/env python3
"""EV CORE -- THE MODEL ZOO. Baselines first, and the baseline is strong.

THE BASELINE IS NOT A STRAW MAN. On this venue the traded price is already an
extremely good probability forecast. Any model here has to beat a market, and
the honest default expectation is that it does not. Every member of this zoo is
therefore built as a TRANSFORM OF THE MARKET PRICE plus whatever else it can
justify, so that "no better than the market" is the natural null and shows up
as such rather than being hidden by a different functional form.

WHAT EACH MODEL IS:

  B0_VENUE_PRICE        the traded price, untouched. The benchmark.
  B4_GLOBAL_LOGIT       one logistic recalibration of the price's log-odds,
                        fitted on train only. Two parameters.
  B4F_FAMILY_LOGIT      the same, fitted per market family, SHRUNK toward the
                        global fit by sample size. Sparse families therefore
                        inherit the global answer instead of fitting noise.
  B3_BASE_RATE          the train base rate, ignoring the price entirely. A
                        deliberately useless forecast, included because a
                        model that cannot beat it is broken.

EVERY FIT IS TRAIN-ONLY. A factory receives train rows and returns a closure.
The closure sees a test row and may read only fields legal at decision time.

SHRINKAGE, STATED. A per-family fit with n rows is blended with the global fit
at weight n / (n + K). K is a declared constant, not tuned on test data. This is
the difference between "learn per family where there is evidence" and "overfit
per family because the code allowed it".

This module contacts nothing and can place no order.
"""
import json
import math
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# Shrinkage strength for per-group recalibration. Declared, not tuned: a group
# needs ~SHRINK_K rows before its own fit outweighs the global one.
SHRINK_K = 2000.0
SHRINK_RULE = ("a group's own fit gets weight n/(n+%d); below that it inherits "
               "the global fit, so a thin family cannot invent a slope"
               % int(SHRINK_K))

THE_BASELINE_IS_A_MARKET = (
    "B0 is the venue's own traded price; beating it means beating everyone who "
    "traded, so 'no improvement' is the expected result and is reported as one")


def _clip(p, lo=1e-6):
    return min(1.0 - lo, max(lo, float(p)))


def _logit(p):
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))


def fit_logistic(xs, ys, iters=100, l2=1e-6):
    """Two-parameter logistic on a single covariate, by Newton's method.

    `l2` is a tiny ridge so a separable group cannot send the slope to
    infinity. It is a numerical guard, not a tuned hyperparameter.
    """
    if len(xs) < 20:
        return None
    a, b = 0.0, 1.0
    for _ in range(iters):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in zip(xs, ys):
            mu = _sigmoid(a + b * x)
            w = max(mu * (1.0 - mu), 1e-10)
            d = y - mu
            g0 += d
            g1 += d * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        g0 -= l2 * a
        g1 -= l2 * (b - 1.0)
        h00 += l2
        h11 += l2
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (h00 * g1 - h01 * g0) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    return (a, b)


# ---------------------------------------------------------------------------
# THE MODELS
# ---------------------------------------------------------------------------

def b0_venue_price(_train):
    """The market's own price. No parameters, nothing fitted."""
    return lambda r: _clip(r["P_VENUE_TRADE"])


def b3_base_rate(train):
    """The train base rate. Perfectly calibrated on average, zero resolution."""
    if not train:
        return lambda r: 0.5
    base = sum(r["SETTLED_YES"] for r in train) / float(len(train))
    return lambda r, b=base: _clip(b)


def b4_global_logit(train):
    """One recalibration of the price for the whole population."""
    xs = [_logit(r["P_VENUE_TRADE"]) for r in train]
    ys = [float(r["SETTLED_YES"]) for r in train]
    fit = fit_logistic(xs, ys)
    if fit is None:
        return b0_venue_price(train)
    a, b = fit
    return lambda r: _clip(_sigmoid(a + b * _logit(r["P_VENUE_TRADE"])))


def _grouped_logit(train, key, shrink_k=SHRINK_K):
    """Per-group recalibration shrunk toward the global fit."""
    xs = [_logit(r["P_VENUE_TRADE"]) for r in train]
    ys = [float(r["SETTLED_YES"]) for r in train]
    g = fit_logistic(xs, ys) or (0.0, 1.0)

    by = defaultdict(lambda: ([], []))
    for r in train:
        gx, gy = by[key(r)]
        gx.append(_logit(r["P_VENUE_TRADE"]))
        gy.append(float(r["SETTLED_YES"]))

    params = {}
    for k, (gx, gy) in by.items():
        f = fit_logistic(gx, gy)
        n = len(gx)
        w = n / (n + shrink_k)
        if f is None:
            params[k] = g
        else:
            params[k] = (w * f[0] + (1 - w) * g[0],
                         w * f[1] + (1 - w) * g[1])

    def predict(r):
        a, b = params.get(key(r), g)
        return _clip(_sigmoid(a + b * _logit(r["P_VENUE_TRADE"])))
    return predict


def b4f_family_logit(train):
    """Recalibration per market family, shrunk."""
    return _grouped_logit(train, lambda r: r.get("MARKET_FAMILY"))


def b4s_sport_family_logit(train):
    """Recalibration per (sport, family), shrunk. The most granular member."""
    return _grouped_logit(train,
                          lambda r: (r.get("SPORT"), r.get("MARKET_FAMILY")))


ZOO = {
    "B0_VENUE_PRICE": b0_venue_price,
    "B3_BASE_RATE": b3_base_rate,
    "B4_GLOBAL_LOGIT": b4_global_logit,
    "B4F_FAMILY_LOGIT": b4f_family_logit,
    "B4S_SPORT_FAMILY_LOGIT": b4s_sport_family_logit,
}

ZOO_NOTES = {
    "B0_VENUE_PRICE": "the market itself; the benchmark every model must beat",
    "B3_BASE_RATE": "no information; a floor, not a competitor",
    "B4_GLOBAL_LOGIT": "two parameters over the whole population",
    "B4F_FAMILY_LOGIT": "per family, shrunk toward global",
    "B4S_SPORT_FAMILY_LOGIT": "per sport x family, shrunk toward global",
}


def describe():
    return {
        "MODELS": sorted(ZOO),
        "NOTES": ZOO_NOTES,
        "SHRINK_K": SHRINK_K,
        "SHRINK_RULE": SHRINK_RULE,
        "THE_BASELINE_IS_A_MARKET": THE_BASELINE_IS_A_MARKET,
        "ALL_FITS_ARE_TRAIN_ONLY": True,
        "NOT_YET_IMPLEMENTED": {
            "P_FUNDAMENTAL": "needs a sport data feed; DATA_STATUS "
                             "NOT_AVAILABLE in the retained corpus",
            "P_EXTERNAL_CONSENSUS": "needs bookmaker or exchange odds; "
                                    "DATA_STATUS NOT_AVAILABLE",
            "P_EVENT_DISTRIBUTION": "machinery exists in ev_core_event_model; "
                                    "the reconstructed-score sample is too "
                                    "small to fit a defensible champion",
            "GRADIENT_BOOSTED_TREES": "no learner library is available in this "
                                      "environment; the interface is open",
        },
    }


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
