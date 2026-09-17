"""Sections 6-10. Timestamped consensus, lead/lag, and disagreement features.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE QUESTION CHANGES HERE
-------------------------
Every previous experiment asked: can BETTOR predict SETTLEMENT better than the
market? The answer has been no, repeatedly, on samples too small to make that
a finding.

This module asks a different question, and it is the one a high-turnover desk
actually needs:

    does some source move BEFORE Polymarket moves, and by how much?

A source can be worthless for settlement and valuable for execution. If
sportsbook consensus sits two cents above Polymarket and Polymarket drifts
toward it over the next fifteen minutes, that is tradeable information even
though the consensus never beat the market on log loss. Conversely a source
that matches settlement beautifully but only after the fact is worth nothing
to a desk that has to quote now.

So there are TWO targets and they are kept apart:

    SETTLEMENT_ERROR      P(outcome) vs what happened
    POLY_PRICE_CHANGE     P_POLY(T+h) - P_POLY(T)

and the same disagreement is tested against both.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not hold any external odds -- none have been obtained, and both
providers are egress-blocked. Every function here takes snapshots as an
argument. Running it today over an empty source returns NO_EXTERNAL_DATA, which
is the honest status, not zero.
"""

import math
from collections import defaultdict

import ev_core_delta as DELTA
import ev_core_power as POW

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NO_EXTERNAL_DATA = "NO_EXTERNAL_DATA_OBTAINED"

# --- Section 6. Consensus estimators. --------------------------------------

ESTIMATORS = ("MEAN", "MEDIAN", "TRIMMED_MEAN", "QUALITY_WEIGHTED",
              "HIERARCHICAL_SOURCE_WEIGHTED")

TRIM_FRACTION = 0.20

QUALITY_WEIGHTS_MAY_ONLY_BE_LEARNED_FROM_PRIOR_OUTCOMES = True
WHY = (
    "a weight fitted on the same events it is scored on is not a weight, it is "
    "a fit. Quality weights are learned on strictly earlier events and frozen "
    "before the events they are applied to")


def _mean(v):
    return sum(v) / len(v) if v else None


def _median(v):
    if not v:
        return None
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def _trimmed(v, frac=TRIM_FRACTION):
    if not v:
        return None
    s = sorted(v)
    k = int(len(s) * frac)
    core = s[k:len(s) - k] or s
    return _mean(core)


def consensus(book_probs, estimator="MEDIAN", weights=None):
    """Combine per-bookmaker de-vigged probabilities into one consensus.

    `book_probs` maps bookmaker -> probability. Weights, when used, must have
    been learned on earlier events; this function does not fit them.
    """
    if not book_probs:
        return None, {"STATUS": NO_EXTERNAL_DATA}
    vals = [p for p in book_probs.values() if p is not None]
    if not vals:
        return None, {"STATUS": "NO_USABLE_PRICES"}
    if estimator == "MEAN":
        out = _mean(vals)
    elif estimator == "MEDIAN":
        out = _median(vals)
    elif estimator == "TRIMMED_MEAN":
        out = _trimmed(vals)
    elif estimator in ("QUALITY_WEIGHTED", "HIERARCHICAL_SOURCE_WEIGHTED"):
        if not weights:
            return None, {"STATUS": "WEIGHTS_NOT_SUPPLIED",
                          "RULE": WHY}
        num = den = 0.0
        for bk, p in book_probs.items():
            if p is None:
                continue
            w = weights.get(bk)
            if w is None:
                continue
            num += w * p
            den += w
        out = (num / den) if den > 0 else None
    else:
        return None, {"STATUS": "UNKNOWN_ESTIMATOR", "ESTIMATOR": estimator}
    return out, {"STATUS": "OK", "ESTIMATOR": estimator, "BOOKS": len(vals),
                 "DISPERSION": (max(vals) - min(vals)) if len(vals) > 1 else 0.0}


def learn_quality_weights(prior_rows, pkey="P_BOOK", ykey="Y",
                          bookkey="BOOKMAKER", floor=1e-6):
    """Inverse-log-loss weights from STRICTLY EARLIER outcomes.

    The caller is responsible for passing only prior events; this function
    records the range it was given so a later reader can check.
    """
    by = defaultdict(list)
    for r in prior_rows or ():
        p, y = r.get(pkey), r.get(ykey)
        if p is None or y not in (0, 1):
            continue
        by[r.get(bookkey)].append(DELTA.log_loss(p, y))
    if not by:
        return {}, {"STATUS": NO_EXTERNAL_DATA}
    scores = {bk: _mean(v) for bk, v in by.items()}
    weights = {bk: 1.0 / max(s, floor) for bk, s in scores.items()}
    tot = sum(weights.values())
    weights = {bk: w / tot for bk, w in weights.items()}
    dates = sorted(r.get("DATE") for r in prior_rows if r.get("DATE"))
    return weights, {
        "STATUS": "LEARNED",
        "BOOKS": len(weights),
        "LEARNED_FROM_EVENTS": len({r.get("EVENT_KEY") for r in prior_rows}),
        "LEARNED_FROM_RANGE": (dates[0], dates[-1]) if dates else NOT_IDENTIFIED,
        "RULE": WHY,
    }


# --- Sections 7 and 10. Lead/lag. ------------------------------------------

LEAD_LAG_HORIZONS_MINUTES = (5, 15, 30, 60, 120)

TWO_TARGETS = ("POLY_PRICE_CHANGE", "SETTLEMENT_ERROR")

A_SOURCE_CAN_BE_VALUABLE_WITHOUT_IMPROVING_SETTLEMENT = (
    "if external consensus leads Polymarket, that is tradeable even when the "
    "consensus never beat the market on log loss. The two questions are "
    "separate and are scored separately")


def disagreement(p_external, p_poly):
    """EXTERNAL_DISAGREEMENT = P_EXTERNAL - P_POLY. Sign is the direction."""
    if p_external is None or p_poly is None:
        return None
    return float(p_external) - float(p_poly)


def lead_lag_rows(observations, horizons=LEAD_LAG_HORIZONS_MINUTES):
    """Pair each observation with Polymarket's later price at each horizon.

    `observations` are dicts with EVENT_KEY, T (iso), P_EXTERNAL, P_POLY, and a
    POLY_LATER mapping of minutes -> price. Rows without a later price at a
    horizon are dropped FOR THAT HORIZON only, and counted.
    """
    out, missing = [], defaultdict(int)
    for o in observations or ():
        d = disagreement(o.get("P_EXTERNAL"), o.get("P_POLY"))
        if d is None:
            missing["NO_DISAGREEMENT"] += 1
            continue
        later = o.get("POLY_LATER") or {}
        row = {"EVENT_KEY": o.get("EVENT_KEY"), "T": o.get("T"),
               "EXTERNAL_DISAGREEMENT": d,
               "P_POLY": o.get("P_POLY"), "P_EXTERNAL": o.get("P_EXTERNAL"),
               "Y": o.get("Y")}
        any_h = False
        for h in horizons:
            p1 = later.get(h)
            if p1 is None:
                missing["NO_POLY_AT_%dM" % h] += 1
                continue
            row["POLY_CHANGE_%dM" % h] = float(p1) - float(o["P_POLY"])
            any_h = True
        if any_h:
            out.append(row)
    return out, dict(missing)


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return (num / (dx * dy)) if dx > 0 and dy > 0 else None


def lead_lag(rows, horizons=LEAD_LAG_HORIZONS_MINUTES, draws=2000):
    """Does disagreement at T predict Polymarket's move after T?

    A positive correlation means Polymarket moves TOWARD the external source --
    the source leads. The interval is event-clustered, because several
    observations on one match are not independent tries.
    """
    if not rows:
        return {"STATUS": NO_EXTERNAL_DATA}
    out = {}
    for h in horizons:
        key = "POLY_CHANGE_%dM" % h
        pairs = [(r["EXTERNAL_DISAGREEMENT"], r[key]) for r in rows
                 if r.get(key) is not None]
        if len(pairs) < 8:
            out["%dM" % h] = {"STATUS": "TOO_FEW_OBSERVATIONS",
                              "N": len(pairs)}
            continue
        xs = [a for a, _ in pairs]
        ys = [b for _, b in pairs]
        # event-clustered bootstrap of the correlation
        by = defaultdict(list)
        for r in rows:
            if r.get(key) is not None:
                by[r["EVENT_KEY"]].append((r["EXTERNAL_DISAGREEMENT"], r[key]))
        evs = sorted(by)
        import random
        rnd = random.Random(20260917)
        boot = []
        for _ in range(draws):
            samp = []
            for _ in range(len(evs)):
                samp += by[evs[rnd.randrange(len(evs))]]
            c = _corr([a for a, _ in samp], [b for _, b in samp])
            if c is not None:
                boot.append(c)
        boot.sort()
        lo = boot[int(0.025 * len(boot))] if boot else None
        hi = boot[int(0.975 * len(boot)) - 1] if boot else None
        out["%dM" % h] = {
            "STATUS": "MEASURED",
            "N_OBSERVATIONS": len(pairs),
            "N_EVENTS": len(evs),
            "CORRELATION": _corr(xs, ys),
            "CI95_EVENT_BOOTSTRAP": (lo, hi),
            "POSITIVE_MEANS_POLY_MOVES_TOWARD_THE_EXTERNAL_SOURCE": True,
            "LEADS": (lo is not None and lo > 0),
        }
    return {"STATUS": "MEASURED", "BY_HORIZON": out,
            "TWO_TARGETS": TWO_TARGETS,
            "NOTE": A_SOURCE_CAN_BE_VALUABLE_WITHOUT_IMPROVING_SETTLEMENT}


CONDITIONING_DIMENSIONS = ("DISAGREEMENT_MAGNITUDE", "LIQUIDITY", "SPREAD",
                           "TIME_TO_EVENT", "SPORT_LEAGUE", "MARKET_FAMILY")


def lead_lag_by(rows, dimension, bucket_of, horizons=LEAD_LAG_HORIZONS_MINUTES):
    """Section 10: the same measurement, conditioned. Buckets are predeclared."""
    if dimension not in CONDITIONING_DIMENSIONS:
        return {"STATUS": "UNDECLARED_DIMENSION", "DIMENSION": dimension,
                "DECLARED": CONDITIONING_DIMENSIONS}
    by = defaultdict(list)
    for r in rows or ():
        by[bucket_of(r)].append(r)
    return {"DIMENSION": dimension,
            "BUCKETS": {str(k): lead_lag(v, horizons) for k, v in by.items()}}


# --- Section 8. Disagreement features. -------------------------------------

DISAGREEMENT_FEATURES = (
    "BOOKMAKER_DISPERSION",
    "EXCHANGE_VS_BOOK_DISAGREEMENT",
    "POLY_VS_BOOK_DISAGREEMENT",
    "POLY_VS_BETFAIR_DISAGREEMENT",
    "FAST_BOOK_VS_SLOW_BOOK_DISAGREEMENT",
    "CONSENSUS_MOMENTUM",
    "LINE_MOVE_LAST_5M",
    "LINE_MOVE_LAST_15M",
    "LINE_MOVE_LAST_60M",
)

THESE_ARE_NOT_ASSUMED_TO_BE_ALPHA = True
THEY_MUST_BE_TESTED_PROSPECTIVELY = (
    "each is a candidate. None enters a model on the strength of being "
    "plausible; each is tested on later events under the same canonical delta "
    "convention and the same incremental ladder as any other challenger")


def disagreement_features(book_probs, poly, betfair=None, fast_books=(),
                          slow_books=(), history=None):
    """Build the section 8 feature row. Missing inputs give None, never zero."""
    vals = [p for p in (book_probs or {}).values() if p is not None]
    f = {k: None for k in DISAGREEMENT_FEATURES}
    if len(vals) > 1:
        f["BOOKMAKER_DISPERSION"] = max(vals) - min(vals)
    cons = _median(vals) if vals else None
    if cons is not None and poly is not None:
        f["POLY_VS_BOOK_DISAGREEMENT"] = cons - poly
    if betfair is not None and poly is not None:
        f["POLY_VS_BETFAIR_DISAGREEMENT"] = betfair - poly
    if betfair is not None and cons is not None:
        f["EXCHANGE_VS_BOOK_DISAGREEMENT"] = betfair - cons
    fast = [book_probs[b] for b in fast_books
            if book_probs.get(b) is not None]
    slow = [book_probs[b] for b in slow_books
            if book_probs.get(b) is not None]
    if fast and slow:
        f["FAST_BOOK_VS_SLOW_BOOK_DISAGREEMENT"] = _mean(fast) - _mean(slow)
    for mins, key in ((5, "LINE_MOVE_LAST_5M"), (15, "LINE_MOVE_LAST_15M"),
                      (60, "LINE_MOVE_LAST_60M")):
        prev = (history or {}).get(mins)
        if prev is not None and cons is not None:
            f[key] = cons - prev
    moves = [f[k] for k in ("LINE_MOVE_LAST_5M", "LINE_MOVE_LAST_15M",
                            "LINE_MOVE_LAST_60M") if f[k] is not None]
    if moves:
        f["CONSENSUS_MOMENTUM"] = _mean(moves)
    return f


# --- Section 9. Three tiers. ------------------------------------------------

TIERS = {
    "TIER_A": {"OBJECT": "P_MARKET_RAW", "STATUS": "CURRENT_CHAMPION",
               "WHAT_IT_IS": "the venue's own traded price"},
    "TIER_B": {"OBJECT": "P_EXTERNAL_TIMESTAMPED",
               "STATUS": "NOT_BUILT_NO_DATA",
               "WHAT_IT_IS": "an independent MARKET-information source"},
    "TIER_C": {"OBJECT": "P_BETTOR_FUNDAMENTAL",
               "STATUS": "FROZEN_NEGATIVE_CONTROL",
               "WHAT_IT_IS": "an independent SPORTS-information source"},
}

ENSEMBLE_RULE = (
    "P_BETTOR_ENSEMBLE may not be rebuilt until TIER_B exists and passes "
    "validation. Combining a frozen negative control with the market again, on "
    "the same events, would repeat an experiment already run")


def tier_status():
    return {"TIERS": {k: dict(v) for k, v in TIERS.items()},
            "ENSEMBLE_RULE": ENSEMBLE_RULE}


def describe():
    return {
        "ESTIMATORS": ESTIMATORS,
        "QUALITY_WEIGHTS_MAY_ONLY_BE_LEARNED_FROM_PRIOR_OUTCOMES":
            QUALITY_WEIGHTS_MAY_ONLY_BE_LEARNED_FROM_PRIOR_OUTCOMES,
        "LEAD_LAG_HORIZONS_MINUTES": LEAD_LAG_HORIZONS_MINUTES,
        "TWO_TARGETS": TWO_TARGETS,
        "CONDITIONING_DIMENSIONS": CONDITIONING_DIMENSIONS,
        "DISAGREEMENT_FEATURES": DISAGREEMENT_FEATURES,
        "THESE_ARE_NOT_ASSUMED_TO_BE_ALPHA": THESE_ARE_NOT_ASSUMED_TO_BE_ALPHA,
        "THEY_MUST_BE_TESTED_PROSPECTIVELY": THEY_MUST_BE_TESTED_PROSPECTIVELY,
        "TIERS": tier_status(),
        "CURRENT_STATUS": NO_EXTERNAL_DATA,
    }
