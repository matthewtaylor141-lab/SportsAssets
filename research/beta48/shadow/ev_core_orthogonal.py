"""Does a candidate model know anything the market price does not?

Directive section 14: "measure orthogonal information separately from
standalone performance".

WHY THE STANDALONE NUMBER IS THE WRONG TEST
-------------------------------------------
A market price contains injuries, lineups, suspensions, weather, team news and
the aggregated opinion of everyone willing to stake money. A goals-only score
model contains none of that. It will lose a head-to-head on log loss, and that
loss says almost nothing about whether it is useful, because the two objects are
not competing for the same job.

The job BETTOR needs filled is not "replace the price". It is "know when the
price is wrong". So the question is conditional:

    GIVEN the market price, does the candidate's disagreement with it predict
    the outcome?

THE TEST
--------
Fit two logistic models on the logit scale:

    A :  logit(y) ~ 1 + logit(p_market)
    AB:  logit(y) ~ 1 + logit(p_market) + logit(p_candidate)

Model A is the market price, recalibrated -- which is the honest baseline,
because a raw price that is systematically 3% long is beaten by its own
recalibration and that is not information. Model AB adds the candidate. If AB
beats A OUT OF SAMPLE, the candidate carries information the price does not.

Three guards make the answer trustworthy:

  EVENT-GROUPED FOLDS. Contracts on one fixture share an outcome. A row-level
  split puts the same match in train and test and manufactures skill out of
  nothing. The fold unit is the EVENT.

  OUT-OF-FOLD PREDICTIONS ONLY. Every reported metric is computed on
  predictions from a model that never saw that event.

  EVENT-CLUSTERED BOOTSTRAP on the difference, not on each metric separately.
  The quantity of interest is the improvement, and its uncertainty has to be
  measured on the improvement.

WHAT A POSITIVE RESULT DOES NOT MEAN
------------------------------------
Orthogonal information is not an edge. An edge also needs the disagreement to
survive the spread, the fee and the fill. This measures the first of those and
nothing further. A model that adds information and cannot clear the spread is
still not tradeable, and saying so is part of the result.
"""

from __future__ import annotations

import math
import random

import ev_core_calibration as cal

NOT_IDENTIFIED = "NOT_IDENTIFIED"

TEST_NAME = "ORTHOGONAL_INFORMATION_CONDITIONAL_ON_MARKET"
FOLD_UNIT = "EVENT"
BASELINE_IS_RECALIBRATED = True
WHY_RECALIBRATED_BASELINE = (
    "Comparing a candidate against the RAW market price lets the candidate win "
    "by correcting a constant bias in the price, which is arithmetic, not "
    "information. The baseline is the price after its own two-parameter "
    "recalibration, fitted on the same folds, so the only way the candidate "
    "wins is by carrying something the price does not contain."
)
ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE = (
    "This measures whether the disagreement predicts the outcome. It does not "
    "measure whether the disagreement is larger than the spread, survives the "
    "fee, or can be filled. A positive result here is a necessary condition "
    "for an edge and nowhere near a sufficient one."
)

EPS = 1e-6


def _logit(p):
    p = min(max(float(p), EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


# ---------------------------------------------------------------------------
# Multivariate logistic by IRLS
# ---------------------------------------------------------------------------


def fit_logistic_multi(X, y, l2=1e-4, iters=60):
    """Fit logit(y) ~ X (which must already carry an intercept column).

    Returns the coefficient vector, or None if it will not converge. A tiny
    ridge keeps a separable fold from sending a coefficient to infinity; it is
    a numerical guard, not a tuned hyper-parameter.
    """
    import numpy as np
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    n, k = X.shape
    if n < 10 * k:
        return None
    b = np.zeros(k)
    for _ in range(iters):
        z = X @ b
        mu = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        w = np.clip(mu * (1.0 - mu), 1e-9, None)
        g = X.T @ (y - mu) - l2 * b
        H = (X * w[:, None]).T @ X + l2 * np.eye(k)
        try:
            step = np.linalg.solve(H, g)
        except Exception:                                      # noqa: BLE001
            return None
        b = b + step
        if not np.all(np.isfinite(b)):
            return None
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return [float(v) for v in b]


def _design(rows, keys):
    return [[1.0] + [_logit(r[k]) for k in keys] for r in rows]


# ---------------------------------------------------------------------------
# Event-grouped out-of-fold predictions
# ---------------------------------------------------------------------------


def event_folds(rows, folds=5, seed=20260917):
    """Assign each EVENT_KEY to a fold. Rows follow their event."""
    events = sorted({r["EVENT_KEY"] for r in rows})
    rnd = random.Random(seed)
    rnd.shuffle(events)
    assign = {e: i % folds for i, e in enumerate(events)}
    return assign, len(events)


def out_of_fold(rows, keys, folds=5, seed=20260917, l2=1e-4):
    """Out-of-fold predicted probabilities for the model using `keys`.

    Returns (predictions aligned to `rows`, fitted-fold count). A fold whose fit
    fails yields None for its rows rather than a silently substituted value.
    """
    assign, n_events = event_folds(rows, folds, seed)
    preds = [None] * len(rows)
    fitted = 0
    for f in range(folds):
        tr = [i for i, r in enumerate(rows) if assign[r["EVENT_KEY"]] != f]
        te = [i for i, r in enumerate(rows) if assign[r["EVENT_KEY"]] == f]
        if not tr or not te:
            continue
        Xtr = _design([rows[i] for i in tr], keys)
        ytr = [rows[i]["Y"] for i in tr]
        b = fit_logistic_multi(Xtr, ytr, l2=l2)
        if b is None:
            continue
        fitted += 1
        for i in te:
            x = [1.0] + [_logit(rows[i][k]) for k in keys]
            preds[i] = _sigmoid(sum(bi * xi for bi, xi in zip(b, x)))
    return preds, fitted, n_events


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def _pairs(rows, preds):
    return [(p, r["Y"]) for r, p in zip(rows, preds) if p is not None]


def _delta_bootstrap(rows, pa, pb, draws=400, seed=20260917):
    """Event-clustered bootstrap of the log-loss IMPROVEMENT of B over A.

    Resampling events, not rows: the improvement on one fixture's twenty
    contracts is one observation, not twenty.
    """
    by_event = {}
    for r, a, b in zip(rows, pa, pb):
        if a is None or b is None:
            continue
        by_event.setdefault(r["EVENT_KEY"], []).append((a, b, r["Y"]))
    events = sorted(by_event)
    if len(events) < 8:
        return NOT_IDENTIFIED
    rnd = random.Random(seed)
    out = []
    for _ in range(draws):
        pick = [events[rnd.randrange(len(events))] for _ in events]
        la = lb = 0.0
        n = 0
        for e in pick:
            for a, b, y in by_event[e]:
                la -= (math.log(max(a, EPS)) if y else math.log(max(1 - a, EPS)))
                lb -= (math.log(max(b, EPS)) if y else math.log(max(1 - b, EPS)))
                n += 1
        if n:
            out.append((la - lb) / n)          # positive => B is better
    out.sort()
    lo = out[int(0.025 * len(out))]
    hi = out[int(0.975 * len(out)) - 1]
    return {"MEAN": sum(out) / len(out), "CI95_LOW": lo, "CI95_HIGH": hi,
            "DRAWS": draws, "POSITIVE_MEANS_CANDIDATE_ADDS": True,
            "EXCLUDES_ZERO": lo > 0 or hi < 0}


def orthogonality(rows, market_key="P_MARKET", candidate_key="P_CANDIDATE",
                  folds=5, seed=20260917, draws=400):
    """Does `candidate_key` add to `market_key`? Out of sample, event-grouped.

    `rows` need EVENT_KEY, Y, and the two probability columns.
    """
    rows = [r for r in rows
            if r.get(market_key) is not None and r.get(candidate_key) is not None
            and r.get("EVENT_KEY") and r.get("Y") in (0, 1)]
    if len(rows) < 100:
        return {"STATUS": "TOO_FEW_ROWS", "ROWS": len(rows)}

    pa, fa, n_events = out_of_fold(rows, [market_key], folds, seed)
    pab, fb, _ = out_of_fold(rows, [market_key, candidate_key], folds, seed)
    if fa == 0 or fb == 0:
        return {"STATUS": "FOLDS_DID_NOT_FIT", "MARKET_FOLDS": fa,
                "COMBINED_FOLDS": fb}

    raw_market = cal.evaluate([{"EVENT_KEY": r["EVENT_KEY"],
                                "P": r[market_key], "Y": r["Y"]} for r in rows],
                              label="RAW_" + market_key, draws=draws)
    raw_cand = cal.evaluate([{"EVENT_KEY": r["EVENT_KEY"],
                              "P": r[candidate_key], "Y": r["Y"]} for r in rows],
                            label="RAW_" + candidate_key, draws=draws)
    recal = cal.evaluate([{"EVENT_KEY": r["EVENT_KEY"], "P": p, "Y": r["Y"]}
                          for r, p in zip(rows, pa) if p is not None],
                         label="RECALIBRATED_MARKET", draws=draws)
    comb = cal.evaluate([{"EVENT_KEY": r["EVENT_KEY"], "P": p, "Y": r["Y"]}
                         for r, p in zip(rows, pab) if p is not None],
                        label="MARKET_PLUS_CANDIDATE", draws=draws)

    # The in-sample coefficient, reported for its SIGN and SIZE, not as a test.
    full = fit_logistic_multi(_design(rows, [market_key, candidate_key]),
                              [r["Y"] for r in rows])
    coef = ({"INTERCEPT": full[0], "MARKET_LOGIT": full[1],
             "CANDIDATE_LOGIT": full[2]} if full else NOT_IDENTIFIED)

    delta = _delta_bootstrap(rows, pa, pab, draws=draws, seed=seed)
    adds = (isinstance(delta, dict) and delta["CI95_LOW"] > 0)

    return {
        "STATUS": "MEASURED",
        "TEST_NAME": TEST_NAME,
        "FOLD_UNIT": FOLD_UNIT,
        "FOLDS": folds,
        "ROWS": len(rows),
        "EVENTS": n_events,
        "RAW_MARKET": raw_market,
        "RAW_CANDIDATE": raw_cand,
        "RECALIBRATED_MARKET": recal,
        "MARKET_PLUS_CANDIDATE": comb,
        "LOG_LOSS_IMPROVEMENT": delta,
        "COEFFICIENTS_IN_SAMPLE": coef,
        "CANDIDATE_ADDS_INFORMATION": adds,
        "VERDICT": ("CANDIDATE_ADDS_INFORMATION_CONDITIONAL_ON_MARKET" if adds
                    else "NO_MEASURABLE_ADDITION_CONDITIONAL_ON_MARKET"),
        "BASELINE_IS_RECALIBRATED": BASELINE_IS_RECALIBRATED,
        "WHY_RECALIBRATED_BASELINE": WHY_RECALIBRATED_BASELINE,
        "ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE":
            ORTHOGONAL_INFORMATION_IS_NOT_AN_EDGE,
    }


def by_group(rows, group_key, **kw):
    """Run the test within each group. Thin groups refuse rather than report."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get(group_key, NOT_IDENTIFIED), []).append(r)
    out = {}
    for g, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out[g] = orthogonality(rs, **kw)
    return out


def render(rep):
    if rep.get("STATUS") != "MEASURED":
        return "STATUS = %s" % rep.get("STATUS")
    L = ["%-32s = %s" % ("VERDICT", rep["VERDICT"]),
         "%-32s = %d rows / %d events" % ("SAMPLE", rep["ROWS"], rep["EVENTS"])]
    for k in ("RAW_MARKET", "RAW_CANDIDATE", "RECALIBRATED_MARKET",
              "MARKET_PLUS_CANDIDATE"):
        r = rep[k]
        L.append("%-32s   log loss %.6f   brier %.6f   slope %.3f"
                 % (k, r["LOG_LOSS"], r["BRIER"], r["CALIBRATION_SLOPE"]))
    d = rep["LOG_LOSS_IMPROVEMENT"]
    if isinstance(d, dict):
        L.append("%-32s = %+.6f  [%+.6f, %+.6f]"
                 % ("LOG_LOSS_IMPROVEMENT", d["MEAN"], d["CI95_LOW"],
                    d["CI95_HIGH"]))
    c = rep["COEFFICIENTS_IN_SAMPLE"]
    if isinstance(c, dict):
        L.append("%-32s = market %+.4f   candidate %+.4f"
                 % ("COEFFICIENTS", c["MARKET_LOGIT"], c["CANDIDATE_LOGIT"]))
    return "\n".join(L)
