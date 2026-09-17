"""Three experts, compared at compatible information times.

Directive sections 3, 13 and 14.

THE THREE
---------
    P_MARKET_RAW            the venue's own last trade. B0.
    P_BETTOR_INDEPENDENT    a fundamental model that has never seen a price.
    P_EXTERNAL_CONSENSUS    a European book's pre-match opinion, de-vigged.

They are not interchangeable and the differences matter. The venue price and
the external consensus are both markets, so they share whatever markets know.
The fundamental model is the only one that can disagree for a reason neither
market contains. The external consensus is the only one that is independent of
THIS venue, which makes it the natural detector of a stale venue quote.

SECTION 3: THE RN1 SAMPLE MUST NOT SET THE WEIGHTS
---------------------------------------------------
The corpus prices a contract when RN1 happened to trade it. One heavily traded
fixture can carry five hundred observations while another carries one. Scoring
those rows equally makes the loud fixture five hundred times more important
than the quiet one, and the thing that decided which was which was a whale's
attention, not the football.

So every metric is computed twice:

    ROW_WEIGHTED_SCORE          every observation counts once
    EVENT_EQUAL_WEIGHTED_SCORE  every FIXTURE counts once, its own rows
                                averaged within it first

and the second is the principal figure. The first is reported because the gap
between them measures how much the whale's attention is doing.

SECTION 14: THE ORTHOGONALITY TEST IS CHRONOLOGICAL
----------------------------------------------------
The blend `outcome ~ logit(P_MARKET) + logit(P_CHALLENGER)` is fitted on
DEVELOPMENT events and evaluated on events that happen LATER. Not a random
split -- a chronological one, because a random split lets the fit learn the
period's own idiosyncrasies and calls it skill.

FORECAST ERROR CORRELATION is reported beside the deltas. Two forecasters whose
errors correlate at 0.95 cannot help each other however good each is; two whose
errors correlate at 0.3 might, even if one is clearly worse. That number often
explains the deltas better than the standalone scores do.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict

import ev_core_calibration as cal

NOT_IDENTIFIED = "NOT_IDENTIFIED"
EPS = 1e-6

PRINCIPAL_WEIGHTING = "EVENT_EQUAL_WEIGHTED"
WHY_EVENT_EQUAL = (
    "the observation count per fixture is set by RN1's trading, not by the "
    "football; row weighting therefore scores the whale's attention")


def _clip(p):
    return min(max(float(p), EPS), 1.0 - EPS)


def _logit(p):
    p = _clip(p)
    return math.log(p / (1.0 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


# ---------------------------------------------------------------------------
# Section 3: the two weightings
# ---------------------------------------------------------------------------


def row_weighted(pairs):
    """(log loss, brier) over every observation."""
    if not pairs:
        return NOT_IDENTIFIED, NOT_IDENTIFIED
    ll = -sum(math.log(_clip(p)) if y else math.log(1 - _clip(p))
              for p, y in pairs) / len(pairs)
    br = sum((_clip(p) - y) ** 2 for p, y in pairs) / len(pairs)
    return ll, br


def event_equal_weighted(rows, pkey, ykey="Y", event_key="EVENT_KEY"):
    """Average within each fixture first, then across fixtures.

    A fixture with 500 rows and a fixture with 1 row count the same.
    """
    by = defaultdict(list)
    for r in rows:
        p, y = r.get(pkey), r.get(ykey)
        if p is None or y not in (0, 1):
            continue
        by[r[event_key]].append((p, y))
    if not by:
        return {"STATUS": "NO_ROWS"}
    lls, brs, ns = [], [], []
    for ev, prs in by.items():
        ll, br = row_weighted(prs)
        lls.append(ll)
        brs.append(br)
        ns.append(len(prs))
    ns_sorted = sorted(ns)
    return {
        "EVENTS": len(by),
        "ROWS": sum(ns),
        "LOG_LOSS": sum(lls) / len(lls),
        "BRIER": sum(brs) / len(brs),
        "ROWS_PER_EVENT_MEDIAN": ns_sorted[len(ns_sorted) // 2],
        "ROWS_PER_EVENT_MAX": ns_sorted[-1],
    }


def score_expert(rows, pkey, label=None, draws=300):
    """Both weightings, plus calibration, for one expert."""
    pairs = [(r[pkey], r["Y"]) for r in rows
             if r.get(pkey) is not None and r.get("Y") in (0, 1)]
    rll, rbr = row_weighted(pairs)
    ee = event_equal_weighted(rows, pkey)
    ev = cal.evaluate([{"EVENT_KEY": r["EVENT_KEY"], "P": r[pkey], "Y": r["Y"]}
                       for r in rows if r.get(pkey) is not None
                       and r.get("Y") in (0, 1)],
                      label=label or pkey, draws=draws)
    return {
        "EXPERT": label or pkey,
        "ROWS": len(pairs),
        "EVENTS": ee.get("EVENTS", 0),
        "ROW_WEIGHTED_LOG_LOSS": rll,
        "ROW_WEIGHTED_BRIER": rbr,
        "EVENT_EQUAL_WEIGHTED_LOG_LOSS": ee.get("LOG_LOSS", NOT_IDENTIFIED),
        "EVENT_EQUAL_WEIGHTED_BRIER": ee.get("BRIER", NOT_IDENTIFIED),
        "CALIBRATION_SLOPE": ev.get("CALIBRATION_SLOPE"),
        "CALIBRATION_INTERCEPT": ev.get("CALIBRATION_INTERCEPT"),
        "RELIABILITY": ev.get("RELIABILITY"),
        "RESOLUTION": ev.get("RESOLUTION"),
        "ROWS_PER_EVENT_MEDIAN": ee.get("ROWS_PER_EVENT_MEDIAN"),
        "ROWS_PER_EVENT_MAX": ee.get("ROWS_PER_EVENT_MAX"),
        "PRINCIPAL_WEIGHTING": PRINCIPAL_WEIGHTING,
    }


# ---------------------------------------------------------------------------
# Section 13 question 3: are the errors correlated?
# ---------------------------------------------------------------------------


def error_correlation(rows, a, b, event_equal=True):
    """Pearson correlation of forecast errors (p - y), by fixture by default."""
    if event_equal:
        by = defaultdict(list)
        for r in rows:
            if r.get(a) is None or r.get(b) is None or r.get("Y") not in (0, 1):
                continue
            by[r["EVENT_KEY"]].append((r[a] - r["Y"], r[b] - r["Y"]))
        xs = [sum(p for p, _ in v) / len(v) for v in by.values()]
        ys = [sum(q for _, q in v) / len(v) for v in by.values()]
    else:
        xs = [r[a] - r["Y"] for r in rows
              if r.get(a) is not None and r.get(b) is not None
              and r.get("Y") in (0, 1)]
        ys = [r[b] - r["Y"] for r in rows
              if r.get(a) is not None and r.get(b) is not None
              and r.get("Y") in (0, 1)]
    n = len(xs)
    if n < 8:
        return NOT_IDENTIFIED
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return NOT_IDENTIFIED
    return sxy / math.sqrt(sxx * syy)


# ---------------------------------------------------------------------------
# Section 14: chronological orthogonality
# ---------------------------------------------------------------------------


def chronological_split(rows, dev_fraction=0.6, date_key="EVENT_KEY"):
    """Split BY EVENT, in date order. Development first, test strictly after.

    The event key carries the fixture date, so sorting the keys sorts the
    fixtures. A random split would let the fit learn the period and call it
    skill; a chronological one cannot.
    """
    def date_of(ev):
        parts = str(ev).split("-")
        for i in range(len(parts) - 2):
            if (len(parts[i]) == 4 and parts[i].isdigit()
                    and len(parts[i + 1]) == 2 and len(parts[i + 2]) == 2):
                return "-".join(parts[i:i + 3])
        return str(ev)
    events = sorted({r[date_key] for r in rows}, key=lambda e: (date_of(e), e))
    cut = max(1, int(dev_fraction * len(events)))
    dev, test = set(events[:cut]), set(events[cut:])
    return ([r for r in rows if r[date_key] in dev],
            [r for r in rows if r[date_key] in test],
            {"DEV_EVENTS": len(dev), "TEST_EVENTS": len(test),
             "SPLIT_IS_CHRONOLOGICAL": True,
             "FIRST_TEST_EVENT": (sorted(test, key=lambda e: (date_of(e), e))[0]
                                  if test else None)})


def _fit2(X, y, l2=1e-3, iters=60):
    import numpy as np
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    n, k = X.shape
    if n < 8 * k:
        return None
    b = np.zeros(k)
    for _ in range(iters):
        mu = 1.0 / (1.0 + np.exp(-np.clip(X @ b, -30, 30)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
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


def _event_bootstrap_delta(rows, pa, pb, draws=400, seed=20260917):
    """Event-clustered bootstrap of the EVENT-EQUAL log-loss improvement."""
    by = defaultdict(list)
    for r, x, y in zip(rows, pa, pb):
        if x is None or y is None or r.get("Y") not in (0, 1):
            continue
        by[r["EVENT_KEY"]].append((x, y, r["Y"]))
    events = sorted(by)
    if len(events) < 8:
        return NOT_IDENTIFIED
    rnd = random.Random(seed)
    out = []
    for _ in range(draws):
        pick = [events[rnd.randrange(len(events))] for _ in events]
        d = []
        for e in pick:
            la, lb, n = 0.0, 0.0, 0
            for x, y, yy in by[e]:
                la -= math.log(_clip(x)) if yy else math.log(1 - _clip(x))
                lb -= math.log(_clip(y)) if yy else math.log(1 - _clip(y))
                n += 1
            if n:
                d.append((la - lb) / n)
        if d:
            out.append(sum(d) / len(d))
    out.sort()
    lo, hi = out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]
    return {"MEAN": sum(out) / len(out), "CI95_LOW": lo, "CI95_HIGH": hi,
            "DRAWS": draws, "POSITIVE_MEANS_CHALLENGER_ADDS": True,
            "EXCLUDES_ZERO": lo > 0 or hi < 0}


def orthogonality_chronological(rows, market="P_MARKET", challenger=None,
                                dev_fraction=0.6, draws=400):
    """Fit the blend on development events, judge it on later ones."""
    usable = [r for r in rows
              if r.get(market) is not None and r.get(challenger) is not None
              and r.get("Y") in (0, 1)]
    if len(usable) < 100:
        return {"STATUS": "TOO_FEW_ROWS", "ROWS": len(usable),
                "CHALLENGER": challenger}
    dev, test, split = chronological_split(usable, dev_fraction)
    if not dev or not test:
        return {"STATUS": "SPLIT_EMPTY", "SPLIT": split}

    Xa = [[1.0, _logit(r[market])] for r in dev]
    Xab = [[1.0, _logit(r[market]), _logit(r[challenger])] for r in dev]
    y = [r["Y"] for r in dev]
    ba, bab = _fit2(Xa, y), _fit2(Xab, y)
    if ba is None or bab is None:
        return {"STATUS": "FIT_FAILED", "CHALLENGER": challenger}

    pa = [_sigmoid(ba[0] + ba[1] * _logit(r[market])) for r in test]
    pab = [_sigmoid(bab[0] + bab[1] * _logit(r[market])
                    + bab[2] * _logit(r[challenger])) for r in test]
    for r, x, z in zip(test, pa, pab):
        r["_P_MKT_ONLY"], r["_P_BLEND"] = x, z

    m_only = event_equal_weighted(test, "_P_MKT_ONLY")
    blend = event_equal_weighted(test, "_P_BLEND")
    delta = _event_bootstrap_delta(test, pa, pab, draws=draws)
    adds = isinstance(delta, dict) and delta["CI95_LOW"] > 0

    return {
        "STATUS": "MEASURED",
        "CHALLENGER": challenger,
        "SPLIT": split,
        "TEST_ROWS": len(test),
        "MARKET_COEFFICIENT": bab[1],
        "CHALLENGER_COEFFICIENT": bab[2],
        "MARKET_ONLY_LOG_LOSS": m_only.get("LOG_LOSS"),
        "MARKET_ONLY_BRIER": m_only.get("BRIER"),
        "BLEND_LOG_LOSS": blend.get("LOG_LOSS"),
        "BLEND_BRIER": blend.get("BRIER"),
        "DELTA_LOG_LOSS": (m_only.get("LOG_LOSS", 0) - blend.get("LOG_LOSS", 0)),
        "DELTA_BRIER": (m_only.get("BRIER", 0) - blend.get("BRIER", 0)),
        "DELTA_LOG_LOSS_CI": delta,
        "FORECAST_ERROR_CORRELATION": error_correlation(test, market,
                                                        challenger),
        "INCREMENTAL_SIGNAL_STATUS": ("DETECTED" if adds
                                      else "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"),
        "PRINCIPAL_WEIGHTING": PRINCIPAL_WEIGHTING,
        "WHY_EVENT_EQUAL": WHY_EVENT_EQUAL,
        "NOT_DETECTED_IS_NOT_ABSENT": (
            "a confidence interval spanning zero says the effect was not "
            "detected in this sample, not that it is zero"),
    }


def compare_all(rows, market="P_MARKET", challengers=(), draws=400):
    """The four questions of section 13, for every challenger."""
    experts = {market: score_expert(rows, market, label="P_MARKET_RAW")}
    for c in challengers:
        if any(r.get(c) is not None for r in rows):
            experts[c] = score_expert(rows, c, label=c)

    scored = [(k, v) for k, v in experts.items()
              if isinstance(v.get("EVENT_EQUAL_WEIGHTED_LOG_LOSS"), float)]
    best_score = min(scored, key=lambda kv:
                     kv[1]["EVENT_EQUAL_WEIGHTED_LOG_LOSS"])[0] if scored \
        else NOT_IDENTIFIED
    cal_ranked = [(k, v) for k, v in experts.items()
                  if isinstance(v.get("CALIBRATION_SLOPE"), float)]
    best_cal = min(cal_ranked, key=lambda kv:
                   abs(kv[1]["CALIBRATION_SLOPE"] - 1.0))[0] if cal_ranked \
        else NOT_IDENTIFIED

    corr = {c: error_correlation(rows, market, c) for c in challengers
            if c in experts}
    for i, c1 in enumerate(challengers):
        for c2 in list(challengers)[i + 1:]:
            if c1 in experts and c2 in experts:
                corr["%s|%s" % (c1, c2)] = error_correlation(rows, c1, c2)

    ortho = {c: orthogonality_chronological(rows, market, c, draws=draws)
             for c in challengers if c in experts}

    return {
        "EXPERTS": experts,
        "Q1_BEST_STANDALONE_PROPER_SCORE": best_score,
        "Q2_BEST_CALIBRATED": best_cal,
        "Q3_FORECAST_ERROR_CORRELATION": corr,
        "Q4_INCREMENTAL_CONDITIONAL_ON_MARKET": {
            c: {"INCREMENTAL_SIGNAL_STATUS": v.get("INCREMENTAL_SIGNAL_STATUS"),
                "DELTA_LOG_LOSS": v.get("DELTA_LOG_LOSS"),
                "DELTA_BRIER": v.get("DELTA_BRIER"),
                "CHALLENGER_COEFFICIENT": v.get("CHALLENGER_COEFFICIENT"),
                "CI": v.get("DELTA_LOG_LOSS_CI")}
            for c, v in ortho.items()},
        "ORTHOGONALITY_DETAIL": ortho,
        "Q4_IS_THE_ONE_THAT_MATTERS": (
            "standalone score answers which forecaster is better; only the "
            "conditional test answers whether a forecaster is USEFUL beside "
            "the price we already have"),
        "PRINCIPAL_WEIGHTING": PRINCIPAL_WEIGHTING,
    }
