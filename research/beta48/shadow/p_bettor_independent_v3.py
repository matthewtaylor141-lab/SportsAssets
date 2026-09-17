"""V3: repair B4, own the team-strength state, and calibrate honestly.

Directive sections 3, 6, 12 and 13.

THREE SEPARATE JOBS, DELIBERATELY IN ONE PLACE
-----------------------------------------------
1. A REPAIRED BIVARIATE POISSON. V2's B4 was disqualified because it fitted a
   shared component and then predicted through an independent-Poisson grid that
   threw it away. Here the same shared component drives BOTH the likelihood and
   the emitted mass function, and a test proves the joint distribution actually
   moves when the parameter moves.

2. TEAM STRENGTH WE COMPUTE OURSELVES. The xgabora Elo covering our window is
   the repository's own provisional continuation, not ClubElo. Depending on it
   alone makes BETTOR's team-strength state one repository's arithmetic. Four
   internal states are built from match history instead: classic Elo,
   goal-difference Elo, an exponentially weighted performance strength, and a
   dynamic attack/defence state. All as-of, all ours.

3. CALIBRATION, WITH ITS LIMITS STATED. B7's slope was 0.665 -- it is about
   half again too confident. Four recalibrators are fitted on DEVELOPMENT data
   only and judged chronologically.

   CALIBRATION_CAN_IMPROVE_PROBABILITY_QUALITY = True
   CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION = True

   A monotone transform of a forecast cannot change its ranking of outcomes and
   therefore cannot manufacture orthogonal information. If a recalibrated
   challenger suddenly appears to add signal conditional on the market, the
   right suspicion is that the blend was previously being dragged around by
   miscalibration, not that new information appeared. The conditional test is
   re-run after calibration and read with that in mind.
"""

from __future__ import annotations

import math
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ---------------------------------------------------------------------------
# Section 12: the repaired bivariate Poisson
# ---------------------------------------------------------------------------

B4_REPAIR_STATUS = "REPAIRED_SHARED_COMPONENT_DRIVES_THE_EMITTED_PMF"

BIVARIATE_POISSON_PMF = (
    "P(x,y) = exp(-(l1+l2+l3)) * l1^x/x! * l2^y/y! * "
    "sum_{k=0}^{min(x,y)} C(x,k) C(y,k) k! (l3/(l1 l2))^k\n\n"
    "l3 is the shared component. It is what gives the two scores a positive "
    "covariance that independent Poisson cannot express, and l3 = 0 recovers "
    "independent Poisson exactly. The defect in V2 was not this formula -- it "
    "was that the formula was never used for prediction."
)


def _logfact_table(n):
    t = [0.0] * (n + 1)
    for i in range(2, n + 1):
        t[i] = t[i - 1] + math.log(i)
    return t


_LF = _logfact_table(64)


def bivariate_poisson_pmf(x, y, l1, l2, l3):
    """P(X=x, Y=y) for the bivariate Poisson. Exact, not an approximation."""
    if x < 0 or y < 0:
        return 0.0
    l1 = max(float(l1), 1e-9)
    l2 = max(float(l2), 1e-9)
    l3 = max(float(l3), 0.0)
    base = -(l1 + l2 + l3) + x * math.log(l1) + y * math.log(l2) \
        - _LF[x] - _LF[y]
    if l3 <= 0.0:
        return math.exp(base)
    # sum_{k} C(x,k) C(y,k) k! (l3/(l1 l2))^k, computed in logs for stability
    r = math.log(l3) - math.log(l1) - math.log(l2)
    terms = []
    for k in range(0, min(x, y) + 1):
        lt = (_LF[x] - _LF[k] - _LF[x - k]
              + _LF[y] - _LF[k] - _LF[y - k]
              + _LF[k] + k * r)
        terms.append(lt)
    m = max(terms)
    s = sum(math.exp(t - m) for t in terms)
    return math.exp(base + m + math.log(s))


def bivariate_grid(l1, l2, l3, max_goals=12):
    """The joint score distribution, built from the SAME l3 the fit used."""
    g, tot = {}, 0.0
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = bivariate_poisson_pmf(x, y, l1, l2, l3)
            g[(x, y)] = p
            tot += p
    if tot <= 0:
        return {}
    return {k: v / tot for k, v in g.items()}


def bivariate_covariance(l1, l2, l3, max_goals=12):
    """Cov(X, Y) from the grid. For the bivariate Poisson this equals l3.

    Computed from the emitted grid rather than returned as the parameter, so
    the test that l3 moves the JOINT DISTRIBUTION is testing the distribution
    and not the bookkeeping.
    """
    g = bivariate_grid(l1, l2, l3, max_goals)
    if not g:
        return NOT_IDENTIFIED
    ex = sum(x * p for (x, _y), p in g.items())
    ey = sum(y * p for (_x, y), p in g.items())
    exy = sum(x * y * p for (x, y), p in g.items())
    return exy - ex * ey


def fit_bivariate(frame, l2_pen=0.05, max_iter=300):
    """Attack/defence plus a shared component, by weighted MLE on the true pmf."""
    import numpy as np
    from scipy.optimize import minimize

    used = [f for f in frame
            if f.get("FT_HOME") is not None and f.get("FT_AWAY") is not None]
    if len(used) < 300:
        return {"FITTED": False, "REASON": "TOO_FEW_ROWS",
                "ROWS": len(used)}
    clubs = sorted({(f["DIVISION"], f["HOME"]) for f in used}
                   | {(f["DIVISION"], f["AWAY"]) for f in used})
    idx = {c: i for i, c in enumerate(clubs)}
    n = len(idx)
    H = [idx[(f["DIVISION"], f["HOME"])] for f in used]
    A = [idx[(f["DIVISION"], f["AWAY"])] for f in used]
    X = [int(f["FT_HOME"]) for f in used]
    Y = [int(f["FT_AWAY"]) for f in used]

    def unpack(p):
        atk = np.empty(n)
        atk[: n - 1] = p[: n - 1]
        atk[n - 1] = -p[: n - 1].sum()
        dfn = p[n - 1: 2 * n - 1]
        mu, hfa, log_l3 = p[2 * n - 1], p[2 * n], p[2 * n + 1]
        return atk, dfn, float(mu), float(hfa), math.exp(float(log_l3))

    def nll(p):
        atk, dfn, mu, hfa, l3 = unpack(p)
        tot = 0.0
        for i in range(len(used)):
            lam = math.exp(min(6.0, mu + atk[H[i]] - dfn[A[i]] + hfa))
            muu = math.exp(min(6.0, mu + atk[A[i]] - dfn[H[i]]))
            l1 = max(lam - l3, 1e-6)
            l2_ = max(muu - l3, 1e-6)
            pr = bivariate_poisson_pmf(min(X[i], 12), min(Y[i], 12), l1, l2_, l3)
            tot -= math.log(max(pr, 1e-300))
        return tot + l2_pen * float(np.sum(atk ** 2) + np.sum(dfn ** 2))

    p0 = np.zeros(2 * n + 2)
    p0[2 * n - 1] = math.log(max(0.2, sum(X) / len(X)))
    p0[2 * n] = 0.2
    p0[2 * n + 1] = math.log(0.05)
    bnds = [(-3, 3)] * (2 * n - 1) + [(-1, 2), (-1, 1), (math.log(1e-4),
                                                         math.log(0.6))]
    res = minimize(nll, p0, method="L-BFGS-B", bounds=bnds,
                   options={"maxiter": max_iter})
    atk, dfn, mu, hfa, l3 = unpack(res.x)
    return {"FITTED": True, "INDEX": idx, "ATTACK": atk.tolist(),
            "DEFENCE": dfn.tolist(), "MU": mu, "HOME_ADVANTAGE": hfa,
            "LAMBDA3": l3, "MATCHES": len(used),
            "CONVERGED": bool(res.success),
            "B4_REPAIR_STATUS": B4_REPAIR_STATUS}


def bivariate_grid_for(fit, division, home, away, max_goals=12):
    """Predict through the SAME distribution family that was fitted."""
    if not fit.get("FITTED"):
        return None, fit.get("REASON", "NOT_FITTED")
    idx = fit["INDEX"]
    kh, ka = (division, home), (division, away)
    if kh not in idx or ka not in idx:
        return None, "CLUB_NOT_IN_FIT"
    atk, dfn, l3 = fit["ATTACK"], fit["DEFENCE"], fit["LAMBDA3"]
    lam = math.exp(min(6.0, fit["MU"] + atk[idx[kh]] - dfn[idx[ka]]
                       + fit["HOME_ADVANTAGE"]))
    muu = math.exp(min(6.0, fit["MU"] + atk[idx[ka]] - dfn[idx[kh]]))
    g = bivariate_grid(max(lam - l3, 1e-6), max(muu - l3, 1e-6), l3, max_goals)
    return (g or None), ("OK" if g else "DEGENERATE_GRID")


# ---------------------------------------------------------------------------
# Section 6: team strength we compute ourselves
# ---------------------------------------------------------------------------

INTERNAL_STRENGTH_STATES = ("CLASSIC_ELO", "GOAL_DIFFERENCE_ELO",
                            "EWMA_PERFORMANCE", "DYNAMIC_ATTACK_DEFENCE")

WHY_INTERNAL = (
    "The only Elo covering the evaluation window is the source repository's "
    "own provisional continuation. A model whose team-strength state comes "
    "solely from that is resting on one repository's arithmetic for the "
    "period that matters most. These are computed from match results we can "
    "see, in one forward pass, so the state at any match uses only earlier "
    "matches."
)

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME = 60.0
GD_ELO_K = 8.0
EWMA_ALPHA = 0.10


def internal_strength(rows, league_of=None):
    """One forward pass producing every internal strength state, as-of.

    Returns rows carrying the state BEFORE each match, in date order. The
    single pass is the guarantee: state is read, then the match updates it.
    """
    def key(r):
        return (r.get("MatchDate") or "", r.get("MatchTime") or "",
                r.get("HomeTeam") or "")
    rows = sorted(rows, key=key)
    elo = defaultdict(lambda: ELO_START)
    gd_elo = defaultdict(lambda: ELO_START)
    ewma_for = defaultdict(lambda: None)
    ewma_ag = defaultdict(lambda: None)
    out = []
    for r in rows:
        div, h, a = r.get("Division"), r.get("HomeTeam"), r.get("AwayTeam")
        if not (div and h and a):
            continue
        kh, ka = (div, h), (div, a)
        exp_h = 1.0 / (1.0 + 10 ** ((elo[ka] - elo[kh] - ELO_HOME) / 400.0))
        gexp_h = 1.0 / (1.0 + 10 ** ((gd_elo[ka] - gd_elo[kh] - ELO_HOME)
                                     / 400.0))
        out.append({
            "DIVISION": div, "DATE": r.get("MatchDate"), "HOME": h, "AWAY": a,
            "VENUE_LEAGUE": league_of(div) if league_of else div,
            "INT_ELO_HOME": elo[kh], "INT_ELO_AWAY": elo[ka],
            "INT_ELO_DIFF": elo[kh] - elo[ka],
            "INT_ELO_EXPECTED_HOME": exp_h,
            "INT_GD_ELO_HOME": gd_elo[kh], "INT_GD_ELO_AWAY": gd_elo[ka],
            "INT_GD_ELO_DIFF": gd_elo[kh] - gd_elo[ka],
            "INT_GD_ELO_EXPECTED_HOME": gexp_h,
            "INT_EWMA_GF_HOME": ewma_for[kh], "INT_EWMA_GA_HOME": ewma_ag[kh],
            "INT_EWMA_GF_AWAY": ewma_for[ka], "INT_EWMA_GA_AWAY": ewma_ag[ka],
        })
        try:
            gh, ga = float(r.get("FTHome")), float(r.get("FTAway"))
        except (TypeError, ValueError):
            continue
        s = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
        elo[kh] += ELO_K * (s - exp_h)
        elo[ka] -= ELO_K * (s - exp_h)
        # goal-difference Elo: the margin scales the update, capped so a 7-0
        # does not rewrite a club's whole history
        margin = max(-5.0, min(5.0, gh - ga))
        gd_elo[kh] += GD_ELO_K * (margin - 5.0 * (2 * gexp_h - 1))
        gd_elo[ka] -= GD_ELO_K * (margin - 5.0 * (2 * gexp_h - 1))
        for k, f, ag in ((kh, gh, ga), (ka, ga, gh)):
            ewma_for[k] = f if ewma_for[k] is None else \
                (1 - EWMA_ALPHA) * ewma_for[k] + EWMA_ALPHA * f
            ewma_ag[k] = ag if ewma_ag[k] is None else \
                (1 - EWMA_ALPHA) * ewma_ag[k] + EWMA_ALPHA * ag
    return out


INTERNAL_STRENGTH_FEATURES = (
    "INT_ELO_HOME", "INT_ELO_AWAY", "INT_ELO_DIFF", "INT_ELO_EXPECTED_HOME",
    "INT_GD_ELO_HOME", "INT_GD_ELO_AWAY", "INT_GD_ELO_DIFF",
    "INT_GD_ELO_EXPECTED_HOME",
    "INT_EWMA_GF_HOME", "INT_EWMA_GA_HOME",
    "INT_EWMA_GF_AWAY", "INT_EWMA_GA_AWAY",
)


# ---------------------------------------------------------------------------
# Section 3: calibration, and what it cannot do
# ---------------------------------------------------------------------------

CALIBRATION_CAN_IMPROVE_PROBABILITY_QUALITY = True
CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION = True
WHY_CALIBRATION_IS_NOT_INFORMATION = (
    "Platt, beta, temperature and isotonic recalibration are all MONOTONE "
    "transforms of the forecast. A monotone transform cannot change which "
    "events the forecast ranks above which others, so it cannot add anything "
    "the forecast did not already order correctly. It can only fix how "
    "confident the numbers are.\n\n"
    "That matters for the conditional test. If a recalibrated challenger "
    "starts to look like it adds signal beside the market, the likely cause is "
    "that the blend was previously being distorted by the challenger's "
    "overconfidence -- not that information appeared. Only a PROSPECTIVE "
    "improvement in the combined model counts."
)

CALIBRATORS = ("PLATT", "BETA", "TEMPERATURE", "ISOTONIC")
ISOTONIC_MIN_EVENTS = 200
ISOTONIC_REFUSAL = "TOO_FEW_EVENTS_FOR_ISOTONIC"

EPS = 1e-6


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


def _irls(X, y, l2=1e-4, iters=60):
    import numpy as np
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        mu = 1.0 / (1.0 + np.exp(-np.clip(X @ b, -30, 30)))
        w = np.clip(mu * (1 - mu), 1e-9, None)
        g = X.T @ (y - mu) - l2 * b
        Hm = (X * w[:, None]).T @ X + l2 * np.eye(X.shape[1])
        try:
            step = np.linalg.solve(Hm, g)
        except Exception:                                      # noqa: BLE001
            return None
        b = b + step
        if not np.all(np.isfinite(b)):
            return None
        if float(np.max(np.abs(step))) < 1e-10:
            break
    return [float(v) for v in b]


def fit_calibrator(dev_rows, pkey, method="PLATT", n_events=None):
    """Fit one recalibrator on DEVELOPMENT rows only."""
    ps = [r[pkey] for r in dev_rows if r.get(pkey) is not None
          and r.get("Y") in (0, 1)]
    ys = [r["Y"] for r in dev_rows if r.get(pkey) is not None
          and r.get("Y") in (0, 1)]
    if len(ps) < 50:
        return {"FITTED": False, "REASON": "TOO_FEW_ROWS", "METHOD": method}

    if method == "PLATT":
        b = _irls([[1.0, _logit(p)] for p in ps], ys)
        return ({"FITTED": True, "METHOD": method, "B": b} if b else
                {"FITTED": False, "REASON": "FIT_FAILED", "METHOD": method})

    if method == "TEMPERATURE":
        # one parameter: divide the logit by T. Slope only, no intercept.
        b = _irls([[_logit(p)] for p in ps], ys)
        return ({"FITTED": True, "METHOD": method, "B": [0.0, b[0]]} if b else
                {"FITTED": False, "REASON": "FIT_FAILED", "METHOD": method})

    if method == "BETA":
        # Kull's beta calibration: two shape terms plus an intercept.
        X = [[1.0, math.log(_clip(p)), -math.log(1.0 - _clip(p))] for p in ps]
        b = _irls(X, ys)
        return ({"FITTED": True, "METHOD": method, "B": b} if b else
                {"FITTED": False, "REASON": "FIT_FAILED", "METHOD": method})

    if method == "ISOTONIC":
        if (n_events or 0) < ISOTONIC_MIN_EVENTS:
            return {"FITTED": False, "REASON": ISOTONIC_REFUSAL,
                    "METHOD": method, "EVENTS": n_events,
                    "REQUIRED": ISOTONIC_MIN_EVENTS,
                    "WHY": ("isotonic regression is nonparametric and will "
                            "happily interpolate noise on a small sample; "
                            "refusing is better than a step function fitted "
                            "to sixty events")}
        try:
            from sklearn.isotonic import IsotonicRegression
        except ImportError:
            return {"FITTED": False, "REASON": "SKLEARN_NOT_AVAILABLE",
                    "METHOD": method}
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(ps, ys)
        return {"FITTED": True, "METHOD": method, "MODEL": ir}

    return {"FITTED": False, "REASON": "UNKNOWN_METHOD", "METHOD": method}


def apply_calibrator(cal, p):
    if not cal.get("FITTED"):
        return None
    m = cal["METHOD"]
    if m in ("PLATT", "TEMPERATURE"):
        b = cal["B"]
        return _clip(_sigmoid(b[0] + b[1] * _logit(p)))
    if m == "BETA":
        b = cal["B"]
        return _clip(_sigmoid(b[0] + b[1] * math.log(_clip(p))
                              - b[2] * math.log(1.0 - _clip(p))))
    if m == "ISOTONIC":
        return _clip(float(cal["MODEL"].predict([p])[0]))
    return None


def describe():
    return {
        "B4_REPAIR_STATUS": B4_REPAIR_STATUS,
        "BIVARIATE_POISSON_PMF": BIVARIATE_POISSON_PMF,
        "INTERNAL_STRENGTH_STATES": list(INTERNAL_STRENGTH_STATES),
        "INTERNAL_STRENGTH_FEATURES": list(INTERNAL_STRENGTH_FEATURES),
        "WHY_INTERNAL": WHY_INTERNAL,
        "CALIBRATORS": list(CALIBRATORS),
        "CALIBRATION_CAN_IMPROVE_PROBABILITY_QUALITY":
            CALIBRATION_CAN_IMPROVE_PROBABILITY_QUALITY,
        "CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION":
            CALIBRATION_DOES_NOT_CREATE_NEW_INFORMATION,
        "WHY_CALIBRATION_IS_NOT_INFORMATION": WHY_CALIBRATION_IS_NOT_INFORMATION,
    }


# ---------------------------------------------------------------------------
# Fitting B4 without a thousand-parameter numerical gradient
# ---------------------------------------------------------------------------
#
# `fit_bivariate` above is the honest full fit and it is computationally
# impractical here: roughly five hundred clubs give a thousand parameters, and
# L-BFGS with a numerical gradient needs a thousand likelihood evaluations per
# step. On ninety thousand matches that is hours per step.
#
# So B4 is fitted as a ONE-PARAMETER EXTENSION of B3: take Dixon-Coles's
# attack, defence, mu and home advantage as given, and estimate only the shared
# component l3 by maximum likelihood on the true bivariate pmf.
#
# This is a weaker fit than joint estimation and it is the RIGHT question. The
# comparison that matters is "does a shared component add anything to the
# score model we already have", and a one-parameter test answers exactly that
# while a joint refit would confound it with a different attack/defence
# solution. It is stated rather than presented as the full thing.

B4_FIT_MODE = "SHARED_COMPONENT_ON_FIXED_DIXON_COLES_STRENGTHS"
B4_FIT_MODE_NOTE = (
    "l3 only; attack, defence, mu and home advantage are B3's. A joint fit is "
    "the stronger experiment and needs an analytic gradient; this one answers "
    "whether the shared component earns its place on top of B3.")


def fit_shared_component(frame, ad_fit, grid_lo=1e-4, grid_hi=0.45, steps=46):
    """Estimate l3 alone, on the true bivariate pmf, by a grid search.

    A grid rather than an optimiser: one parameter on a bounded interval, and a
    grid cannot get stuck or report a spurious convergence.
    """
    if not ad_fit.get("FITTED"):
        return {"FITTED": False, "REASON": "NO_BASE_FIT"}
    idx = ad_fit["INDEX"]
    atk, dfn = ad_fit["ATTACK"], ad_fit["DEFENCE"]
    mu, hfa = ad_fit["MU"], ad_fit["HOME_ADVANTAGE"]
    used = []
    for f in frame:
        if f.get("FT_HOME") is None or f.get("FT_AWAY") is None:
            continue
        kh, ka = (f["DIVISION"], f["HOME"]), (f["DIVISION"], f["AWAY"])
        if kh not in idx or ka not in idx:
            continue
        lam = math.exp(min(6.0, mu + atk[idx[kh]] - dfn[idx[ka]] + hfa))
        muu = math.exp(min(6.0, mu + atk[idx[ka]] - dfn[idx[kh]]))
        used.append((min(int(f["FT_HOME"]), 12), min(int(f["FT_AWAY"]), 12),
                     lam, muu))
    if len(used) < 300:
        return {"FITTED": False, "REASON": "TOO_FEW_ROWS", "ROWS": len(used)}

    best = None
    curve = []
    for i in range(steps):
        l3 = grid_lo + (grid_hi - grid_lo) * i / (steps - 1.0)
        ll = 0.0
        for x, y, lam, muu in used:
            p = bivariate_poisson_pmf(x, y, max(lam - l3, 1e-6),
                                      max(muu - l3, 1e-6), l3)
            ll += math.log(max(p, 1e-300))
        curve.append((l3, ll))
        if best is None or ll > best[1]:
            best = (l3, ll)
    zero = [ll for l3, ll in curve if l3 <= grid_lo + 1e-12]
    return {
        "FITTED": True,
        "B4_FIT_MODE": B4_FIT_MODE,
        "B4_FIT_MODE_NOTE": B4_FIT_MODE_NOTE,
        "LAMBDA3": best[0],
        "LOGLIK": best[1],
        "LOGLIK_AT_ZERO": zero[0] if zero else NOT_IDENTIFIED,
        "LOGLIK_GAIN_OVER_INDEPENDENT": (best[1] - zero[0]) if zero
                                        else NOT_IDENTIFIED,
        "MATCHES": len(used),
        "BASE": ad_fit,
        "B4_REPAIR_STATUS": B4_REPAIR_STATUS,
    }


def shared_grid_for(fit, division, home, away, max_goals=12):
    """Predict through the bivariate pmf using the fitted l3."""
    if not fit.get("FITTED"):
        return None, fit.get("REASON", "NOT_FITTED")
    base, l3 = fit["BASE"], fit["LAMBDA3"]
    idx = base["INDEX"]
    kh, ka = (division, home), (division, away)
    if kh not in idx or ka not in idx:
        return None, "CLUB_NOT_IN_FIT"
    atk, dfn = base["ATTACK"], base["DEFENCE"]
    lam = math.exp(min(6.0, base["MU"] + atk[idx[kh]] - dfn[idx[ka]]
                       + base["HOME_ADVANTAGE"]))
    muu = math.exp(min(6.0, base["MU"] + atk[idx[ka]] - dfn[idx[kh]]))
    g = bivariate_grid(max(lam - l3, 1e-6), max(muu - l3, 1e-6), l3, max_goals)
    return (g or None), ("OK" if g else "DEGENERATE_GRID")
