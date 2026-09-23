"""HOW A MODEL IS JUDGED, and what each number can and cannot say.

THE MANDATE'S THREE ACHIEVEMENTS ARE THREE DIFFERENT MEASUREMENTS, and
conflating them is the failure this module is built to make hard:

    predicting cohort behaviour    log loss, Brier, AUC, calibration
    predicting economic outcomes   the same, against a MONEY label
    improving our own net returns  neither of the above -- only our own
                                   fills can answer it

Nothing in this file can answer the third. It says so where it would
otherwise be tempting to pretend.

EVERY SCORE COMES WITH ITS BASELINE. A log loss of 0.31 is excellent or
embarrassing depending entirely on the base rate, so `report()` refuses
to return a model's score without the base-rate score beside it and the
skill score that compares them.
"""
from __future__ import annotations

import math

VERSION = "BETTOR_LEARN_METRICS_V1"

_EPS = 1e-12


def log_loss(p, y, weights=None) -> float:
    """Weighted mean negative log-likelihood. Lower is better."""
    if len(p) != len(y):
        raise ValueError("predictions and labels differ in length")
    if not p:
        raise ValueError("cannot score zero rows")
    w = [1.0] * len(p) if weights is None else [float(v) for v in weights]
    tw = sum(w)
    if tw <= 0:
        raise ValueError("total weight is zero")
    s = 0.0
    for i in range(len(p)):
        pi = min(max(float(p[i]), _EPS), 1.0 - _EPS)
        yi = float(y[i])
        s += w[i] * -(yi * math.log(pi) + (1.0 - yi) * math.log(1.0 - pi))
    return s / tw


def brier(p, y, weights=None) -> float:
    w = [1.0] * len(p) if weights is None else [float(v) for v in weights]
    tw = sum(w) or 1.0
    return sum(w[i] * (float(p[i]) - float(y[i])) ** 2
               for i in range(len(p))) / tw


def auc(p, y) -> dict:
    """Rank AUC with ties handled, or a refusal naming why not.

    TIES ARE HALF A POINT, not a win. A model that returns the same
    score for everything has AUC 0.5, which is correct; counting ties
    as concordant would report 1.0 for a constant model.

    A SINGLE-CLASS SET HAS NO AUC. There is nothing to rank against, so
    this returns UNDEFINED with the reason rather than 0.5 -- which
    would read as "no skill" when the truth is "no measurement".
    """
    pos = [float(p[i]) for i in range(len(y)) if float(y[i]) >= 0.5]
    neg = [float(p[i]) for i in range(len(y)) if float(y[i]) < 0.5]
    if not pos or not neg:
        return {"auc": None, "status": "UNDEFINED",
                "why": "the label set has %d positives and %d negatives; "
                       "AUC needs both" % (len(pos), len(neg)),
                "n_pos": len(pos), "n_neg": len(neg)}
    # O(n log n): walk the merged order counting concordant pairs.
    marked = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    rank = 1
    while i < len(marked):
        j = i
        while j < len(marked) and marked[j][0] == marked[i][0]:
            j += 1
        avg = (rank + (rank + (j - i) - 1)) / 2.0
        for k in range(i, j):
            if marked[k][1] == 1:
                rank_sum += avg
        rank += (j - i)
        i = j
    n1, n0 = len(pos), len(neg)
    a = (rank_sum - n1 * (n1 + 1) / 2.0) / (n1 * n0)
    return {"auc": a, "status": "OK", "n_pos": n1, "n_neg": n0}


def calibration(p, y, *, bins=10, weights=None) -> dict:
    """Reliability by equal-width score bin, plus ECE and MCE.

    EMPTY BINS ARE REPORTED AS EMPTY. A bin nobody landed in is not a
    perfectly calibrated bin, and averaging over it would flatter the
    curve. ECE is weighted by occupancy, so an empty bin contributes
    nothing rather than a zero error.
    """
    w = [1.0] * len(p) if weights is None else [float(v) for v in weights]
    edges = [k / float(bins) for k in range(bins + 1)]
    acc = [{"lo": edges[k], "hi": edges[k + 1], "w": 0.0,
            "sum_p": 0.0, "sum_y": 0.0, "n": 0} for k in range(bins)]
    for i in range(len(p)):
        pi = min(max(float(p[i]), 0.0), 1.0)
        k = min(bins - 1, int(pi * bins))
        a = acc[k]
        a["w"] += w[i]
        a["sum_p"] += w[i] * pi
        a["sum_y"] += w[i] * float(y[i])
        a["n"] += 1
    tw = sum(a["w"] for a in acc) or 1.0
    ece, mce = 0.0, 0.0
    out = []
    for a in acc:
        if a["w"] <= 0.0:
            out.append({"lo": a["lo"], "hi": a["hi"], "n": 0,
                        "mean_p": None, "observed": None, "gap": None,
                        "status": "EMPTY"})
            continue
        mp = a["sum_p"] / a["w"]
        mo = a["sum_y"] / a["w"]
        gap = abs(mp - mo)
        ece += (a["w"] / tw) * gap
        mce = max(mce, gap)
        out.append({"lo": a["lo"], "hi": a["hi"], "n": a["n"],
                    "mean_p": mp, "observed": mo, "gap": mp - mo,
                    "status": "OK"})
    return {"bins": out, "ece": ece, "mce": mce,
            "n_bins_occupied": sum(1 for b in out if b["status"] == "OK")}


def skill(model_loss: float, baseline_loss: float) -> dict:
    """Fractional reduction in loss against the baseline.

    Positive is better than the baseline; ZERO OR NEGATIVE MEANS THE
    MODEL IS NOT WORTH ITS COMPLEXITY, and that verdict is returned in
    words so a table of numbers cannot be read hopefully.
    """
    if baseline_loss <= 0.0:
        return {"skill": None, "status": "UNDEFINED",
                "why": "the baseline loss is zero or negative"}
    s = 1.0 - (model_loss / baseline_loss)
    return {"skill": s, "status": "OK",
             "verdict": ("BEATS_BASELINE" if s > 0.0
                         else "NO_BETTER_THAN_BASELINE"),
             "reads_as": "%.2f%% %s the base rate"
                         % (abs(s) * 100.0,
                            "better than" if s > 0 else "worse than")}


def report(p, y, *, weights=None, baseline_rate=None, bins=10,
           label="") -> dict:
    """Everything above, with the baseline attached and named.

    `baseline_rate` is the BASE RATE OF THE TRAINING SET, not of `y`.
    Using the evaluation set's own rate would let a model be scored
    against a number it could not have known, which flatters it on any
    split where the rate moved -- and rate movement is exactly what
    drift looks like.
    """
    if baseline_rate is None:
        raise ValueError(
            "a report needs the TRAINING base rate. Scoring without a "
            "baseline is how a model with no skill gets promoted.")
    base = [float(baseline_rate)] * len(p)
    ml = log_loss(p, y, weights)
    bl = log_loss(base, y, weights)
    return {
        "label": label, "metrics_version": VERSION, "n": len(p),
        "log_loss": ml, "brier": brier(p, y, weights),
        "baseline": {"rate": float(baseline_rate), "log_loss": bl,
                     "brier": brier(base, y, weights),
                     "source": "TRAINING base rate, not this set's"},
        "skill_vs_baseline": skill(ml, bl),
        "auc": auc(p, y),
        "calibration": calibration(p, y, bins=bins, weights=weights),
        "observed_rate_here": (sum(float(v) for v in y) / len(y)
                               if y else None),
        "what_this_cannot_say":
            "These figures measure agreement between a prediction and "
            "an outcome. They say nothing about whether acting on the "
            "prediction would have made money: that needs our own "
            "fills, our own fees and our own committed capital.",
    }


def by_group(p, y, groups, *, baseline_rate, min_n=30, weights=None) -> dict:
    """The same report per group, with small groups refused a verdict.

    WHY A MINIMUM. Action-specific and account-specific performance is
    exactly where a cohort model is expected to differ, and it is also
    where the sample gets thin enough for noise to look like a finding.
    A group under `min_n` gets its counts and an explicit
    INSUFFICIENT_SAMPLE rather than a log loss somebody will quote.
    """
    buckets = {}
    for i, g in enumerate(groups):
        buckets.setdefault(str(g), []).append(i)
    out = {}
    for g, idx in sorted(buckets.items()):
        if len(idx) < min_n:
            out[g] = {"n": len(idx), "status": "INSUFFICIENT_SAMPLE",
                      "why": "%d rows is below the %d-row floor for a "
                             "per-group verdict" % (len(idx), min_n)}
            continue
        gp = [p[i] for i in idx]
        gy = [y[i] for i in idx]
        gw = None if weights is None else [weights[i] for i in idx]
        r = report(gp, gy, weights=gw, baseline_rate=baseline_rate,
                   label=g)
        r["status"] = "OK"
        out[g] = r
    return out
