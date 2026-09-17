"""P_BETTOR_INDEPENDENT_V1 -- a fair value that has never seen a venue price.

Directive section 13.

WHAT MAKES IT INDEPENDENT
------------------------
Everything BETTOR has produced so far is a transform of the venue's own prices.
B0 (`P_MARKET_RAW`) is the last trade. `P_MARKET_SURFACE` is a coherence-repaired
reading of the same prices. Both are baselines. Neither can disagree with the
market for a reason the market does not already contain, so neither can be the
source of an edge.

This model's entire input is public match results: who played, when, and how many
goals each side scored. No venue price, no venue volume, no whale flow, no
contract, no settlement from the corpus. The assertion is enforced, not just
stated -- see TARGET_MARKET_PRICE_USED and `provenance()`.

DISTRIBUTION FIRST
------------------
The model does not predict a market. It predicts a JOINT DISTRIBUTION OVER THE
SCORE, and every contract is then a sum of grid mass under the venue's own
settlement rule. That ordering is what makes the totals ladder monotone, the
exact-score grid sum to one, and the draw price agree with the 1X2 set, without
any of those being enforced afterwards. `ev_core_event_model` already holds the
grid and the derivations; what was missing was the parameters, and public results
supply them.

THE MODEL
---------
Dixon-Coles. For a match between home h and away a:

    lambda_home = exp(mu + atk[h] - def[a] + hfa)
    lambda_away = exp(mu + atk[a] - def[h])

with goals independent Poisson except for a correction `tau` on the four
low-score cells (0-0, 0-1, 1-0, 1-1), where real football is measurably not
independent. Fitted by weighted maximum likelihood with

  * exponential time decay, so last season informs the fit but this season
    dominates it;
  * an L2 prior pulling every club toward the league average, so a newly
    promoted club with three matches is shrunk rather than fitted confidently;
  * a mean-zero constraint on attack, without which mu and atk trade off freely.

A separate fit on HALF-TIME goals prices the first-half ladder and the
half-time result. Half-time is not half of full-time: fitting it separately is
the difference between a model and an assumption.

Elo is fitted alongside as a cheaper reference. It rates only the 1X2 outcome,
so it cannot price a totals contract, and it is here to show what the extra
structure of the score model buys.

WALK-FORWARD
------------
A prediction for a match on date D is made from a model fitted ONLY on matches
that finished before D. The fit is cached per (league, date) and the cache key
contains the cutoff, so a later date can never leak into an earlier prediction.
"""

from __future__ import annotations

import collections
import datetime
import math

import ev_core_event_model as evm

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ---------------------------------------------------------------------------
# The independence assertion
# ---------------------------------------------------------------------------

MODEL_NAME = "P_BETTOR_INDEPENDENT_V1"
MODEL_STATUS = "BUILT_WALK_FORWARD_TESTED"

TARGET_MARKET_PRICE_USED = "NO"
VENUE_DATA_USED = "NONE"
WHALE_DATA_USED = "NONE"
CORPUS_SETTLEMENTS_USED_FOR_FITTING = "NO"

INPUTS = (
    "public match date",
    "public home club",
    "public away club",
    "public full-time goals",
    "public half-time goals",
)

WHY_IT_NEED_NOT_BEAT_B0 = (
    "A market price aggregates injuries, lineups, weather, suspensions, team "
    "news and the opinions of everyone willing to back them with money. A model "
    "fitted on goals alone does not know today's lineup. Expecting it to beat "
    "that price standalone is the wrong bar, and failing it says nothing about "
    "whether the model carries information the price does not.\n\n"
    "The bar that matters is ORTHOGONAL INFORMATION: conditional on the market "
    "price, does this model's disagreement predict the outcome? That is a "
    "different question from whether it wins alone, and it is the question "
    "section 14 measures. A model that loses standalone and still moves the "
    "combination is worth keeping. A model that loses standalone and adds "
    "nothing conditional on the price is not."
)

# ---------------------------------------------------------------------------
# Fit hyper-parameters. Chosen before any evaluation, not tuned on the result.
# ---------------------------------------------------------------------------

XI_PER_DAY = 0.0045          # half-life ~154 days, about one season
L2_PRIOR = 0.05              # shrink club strengths toward the league mean
MAX_GOALS = evm.MAX_GOALS
MIN_MATCHES_TO_FIT = 60
MIN_MATCHES_PER_CLUB = 4

HYPERPARAMETERS_FIXED_BEFORE_EVALUATION = True
HYPERPARAMETER_NOTE = (
    "XI_PER_DAY, L2_PRIOR and the two minimum counts were set from the "
    "published Dixon-Coles literature and the size of the sample, before any "
    "score was computed. They were not searched over the evaluation set. "
    "Tuning them on the result would make the reported metrics selection "
    "statistics rather than out-of-sample ones."
)


def _days(a, b):
    f = "%Y-%m-%d"
    return (datetime.datetime.strptime(a, f).date()
            - datetime.datetime.strptime(b, f).date()).days


# ---------------------------------------------------------------------------
# Dixon-Coles fit
# ---------------------------------------------------------------------------


def _tau(x, y, lam, mu, rho):
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def fit_dixon_coles(matches, as_of, goals="FT", xi=XI_PER_DAY, l2=L2_PRIOR,
                    min_matches=MIN_MATCHES_TO_FIT):
    """Fit club strengths on matches that finished strictly before `as_of`.

    `matches` are ingest fixture rows. Returns a fitted-model dict, or a refusal
    dict carrying FITTED=False and a reason. Never raises on a thin sample: a
    league with eleven matches played is a refusal, not an exception.
    """
    hk, ak = ("FT_HOME", "FT_AWAY") if goals == "FT" else ("HT_HOME", "HT_AWAY")
    used = [m for m in matches
            if m.get("PLAYED") and m.get("DATE") and m["DATE"] < as_of
            and m.get(hk) is not None and m.get(ak) is not None
            and m.get("HOME_KEY") and m.get("AWAY_KEY")]
    if len(used) < min_matches:
        return {"FITTED": False, "REASON": "TOO_FEW_MATCHES",
                "MATCHES_AVAILABLE": len(used),
                "MIN_MATCHES_TO_FIT": min_matches, "AS_OF": as_of,
                "GOALS": goals}

    clubs = sorted({m["HOME_KEY"] for m in used} | {m["AWAY_KEY"] for m in used})
    idx = {c: i for i, c in enumerate(clubs)}
    n = len(clubs)
    apps = collections.Counter()
    for m in used:
        apps[m["HOME_KEY"]] += 1
        apps[m["AWAY_KEY"]] += 1

    w = [math.exp(-xi * max(0, _days(as_of, m["DATE"]))) for m in used]

    # Parameters: atk[0..n-1] (last one pinned by the mean-zero constraint),
    # dfn[0..n-1], mu, hfa, rho.
    import numpy as np
    from scipy.optimize import minimize

    H = np.array([idx[m["HOME_KEY"]] for m in used])
    A = np.array([idx[m["AWAY_KEY"]] for m in used])
    X = np.array([m[hk] for m in used], dtype=float)
    Y = np.array([m[ak] for m in used], dtype=float)
    W = np.array(w)

    def unpack(p):
        atk = np.empty(n)
        atk[: n - 1] = p[: n - 1]
        atk[n - 1] = -p[: n - 1].sum()        # mean-zero attack
        dfn = p[n - 1: 2 * n - 1]
        mu, hfa, rho = p[2 * n - 1], p[2 * n], p[2 * n + 1]
        return atk, dfn, mu, hfa, rho

    def nll(p):
        atk, dfn, mu, hfa, rho = unpack(p)
        lam = np.exp(mu + atk[H] - dfn[A] + hfa)
        muu = np.exp(mu + atk[A] - dfn[H])
        lam = np.clip(lam, 1e-6, 25.0)
        muu = np.clip(muu, 1e-6, 25.0)
        ll = (X * np.log(lam) - lam) + (Y * np.log(muu) - muu)
        # Dixon-Coles low-score correction, vectorised over the four cells.
        t = np.ones_like(lam)
        m00 = (X == 0) & (Y == 0)
        m01 = (X == 0) & (Y == 1)
        m10 = (X == 1) & (Y == 0)
        m11 = (X == 1) & (Y == 1)
        t[m00] = 1.0 - lam[m00] * muu[m00] * rho
        t[m01] = 1.0 + lam[m01] * rho
        t[m10] = 1.0 + muu[m10] * rho
        t[m11] = 1.0 - rho
        t = np.clip(t, 1e-9, None)
        ll = ll + np.log(t)
        pen = l2 * (np.sum(atk ** 2) + np.sum(dfn ** 2))
        return -float(np.sum(W * ll)) + pen

    p0 = np.zeros(2 * n + 2)
    p0[2 * n - 1] = math.log(max(0.2, float(np.average(X, weights=W))))
    p0[2 * n] = 0.2
    res = minimize(nll, p0, method="L-BFGS-B",
                   bounds=[(-3, 3)] * (2 * n - 1) + [(-3, 3)] * 1
                          + [(-1, 2)] + [(-0.5, 0.5)],
                   options={"maxiter": 500})
    atk, dfn, mu, hfa, rho = unpack(res.x)
    return {
        "FITTED": True,
        "GOALS": goals,
        "AS_OF": as_of,
        "MATCHES_USED": len(used),
        "EFFECTIVE_MATCHES": float(W.sum()),
        "CLUBS": clubs,
        "ATTACK": {c: float(atk[i]) for c, i in idx.items()},
        "DEFENCE": {c: float(dfn[i]) for c, i in idx.items()},
        "MU": float(mu),
        "HOME_ADVANTAGE": float(hfa),
        "RHO": float(rho),
        "APPEARANCES": dict(apps),
        "CONVERGED": bool(res.success),
        "XI_PER_DAY": xi,
        "L2_PRIOR": l2,
        "TARGET_MARKET_PRICE_USED": TARGET_MARKET_PRICE_USED,
    }


def rates(fit, home_key, away_key):
    """(lambda_home, lambda_away) for one fixture, or a named refusal."""
    if not fit.get("FITTED"):
        return None, fit.get("REASON", "NOT_FITTED")
    atk, dfn = fit["ATTACK"], fit["DEFENCE"]
    apps = fit.get("APPEARANCES", {})
    for k in (home_key, away_key):
        if k not in atk:
            return None, "CLUB_NOT_IN_FIT"
        if apps.get(k, 0) < MIN_MATCHES_PER_CLUB:
            # A club seen twice has a strength the fit did not learn. Refusing
            # is better than emitting a shrunk-to-average number that looks
            # like a real opinion.
            return None, "CLUB_TOO_FEW_MATCHES_IN_FIT"
    lam = math.exp(fit["MU"] + atk[home_key] - dfn[away_key]
                   + fit["HOME_ADVANTAGE"])
    mu_ = math.exp(fit["MU"] + atk[away_key] - dfn[home_key])
    return (min(lam, 25.0), min(mu_, 25.0)), "OK"


def grid_for(fit, home_key, away_key, max_goals=MAX_GOALS):
    """The joint score distribution for one fixture, or a named refusal."""
    lams, reason = rates(fit, home_key, away_key)
    if lams is None:
        return None, reason
    g = evm.score_grid(lams[0], lams[1], family=evm.FAMILY_DIXON_COLES,
                       rho=fit["RHO"], max_goals=max_goals)
    return (g or None), ("OK" if g else "DEGENERATE_GRID")


# ---------------------------------------------------------------------------
# Elo, as the cheaper reference
# ---------------------------------------------------------------------------

ELO_K = 20.0
ELO_HOME = 60.0
ELO_START = 1500.0


def fit_elo(matches, as_of, k=ELO_K, home=ELO_HOME):
    """Ratings from every match before `as_of`, in date order.

    Elo rates who wins, not how many goals, so it prices the 1X2 set and
    nothing else. Its draw probability is taken from the observed draw rate at
    each rating gap rather than assumed, because Elo has no native draw.
    """
    used = sorted((m for m in matches
                   if m.get("PLAYED") and m.get("DATE") and m["DATE"] < as_of
                   and m.get("FT_HOME") is not None),
                  key=lambda m: m["DATE"])
    if len(used) < MIN_MATCHES_TO_FIT:
        return {"FITTED": False, "REASON": "TOO_FEW_MATCHES",
                "MATCHES_AVAILABLE": len(used)}
    r = collections.defaultdict(lambda: ELO_START)
    gaps, draws = [], []
    for m in used:
        h, a = m["HOME_KEY"], m["AWAY_KEY"]
        exp_h = 1.0 / (1.0 + 10 ** ((r[a] - r[h] - home) / 400.0))
        hg, ag = m["FT_HOME"], m["FT_AWAY"]
        s = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        r[h] += k * (s - exp_h)
        r[a] -= k * (s - exp_h)
        gaps.append(abs(r[h] - r[a]))
        draws.append(1.0 if hg == ag else 0.0)
    return {"FITTED": True, "AS_OF": as_of, "RATINGS": dict(r),
            "MATCHES_USED": len(used), "HOME_BONUS": home, "K": k,
            "DRAW_RATE": sum(draws) / len(draws),
            "TARGET_MARKET_PRICE_USED": TARGET_MARKET_PRICE_USED}


def elo_1x2(fit, home_key, away_key):
    """(p_home, p_draw, p_away) or a named refusal."""
    if not fit.get("FITTED"):
        return None, fit.get("REASON", "NOT_FITTED")
    r = fit["RATINGS"]
    if home_key not in r or away_key not in r:
        return None, "CLUB_NOT_IN_FIT"
    exp_h = 1.0 / (1.0 + 10 ** ((r[away_key] - r[home_key]
                                 - fit["HOME_BONUS"]) / 400.0))
    d = fit["DRAW_RATE"]
    # Elo's expected score splits the non-draw mass; the draw rate is measured.
    ph = exp_h - d / 2.0
    pa = (1.0 - exp_h) - d / 2.0
    if ph <= 0 or pa <= 0:
        lo = 1e-4
        ph, pa = max(ph, lo), max(pa, lo)
        d = max(1e-4, 1.0 - ph - pa)
    return (ph, d, pa), "OK"


# ---------------------------------------------------------------------------
# From a distribution to a contract price
# ---------------------------------------------------------------------------
#
# Every price below is a sum of grid mass under the venue's own settlement
# rule. Nothing is priced by a formula of its own, so the ladder is monotone
# and the families agree with each other by construction.
#
# A family this code cannot price is REFUSED by name. Guessing a settlement
# rule is how a model scores well against a contract it misunderstood.

import re  # noqa: E402

TOTAL_RE = re.compile(r"-total-(\d+)pt5$")
FH_TOTAL_RE = re.compile(r"-first-half-total-(\d+)pt5$")
EXACT_RE = re.compile(r"-exact-score-(\d+)-(\d+)$")
TEAM_TOTAL_RE = re.compile(r"-team-total-(home|away)-(\d+)pt5$")
HALFTIME_RE = re.compile(r"-halftime-result-(home|away|draw)$")
DRAW_RE = re.compile(r"-draw$")
BTTS_RE = re.compile(r"-btts$")
SPREAD_RE = re.compile(r"-spread-(home|away)-(\d+)pt5$")
EVENT_RE = re.compile(
    r"^([a-z0-9]+)-([a-z0-9]+)-([a-z0-9]+)-(\d{4}-\d{2}-\d{2})(-.*)?$")

SPREAD_REFUSAL = "SPREAD_HANDICAP_SIGN_NOT_ESTABLISHED"
SPREAD_REFUSAL_WHY = (
    "The slug 'epl-che-bri-2026-08-30-spread-home-2pt5' does not say which "
    "side gives the 2.5 goals. Two readings are available and they are "
    "complements of each other, so picking one at random would be right half "
    "the time and wrong half the time -- and the metric would not reveal "
    "which. The rule can be established from the corpus's own settled spread "
    "rows against reconstructed scores, and until it has been, the family is "
    "refused."
)


def _marginal(grid, side):
    out = {}
    for (h, a), p in grid.items():
        k = h if side == "home" else a
        out[k] = out.get(k, 0.0) + p
    return out


def price_contract(slug, outcome, ft_grid, ht_grid=None):
    """P(this contract's `outcome` settles YES), or (None, named refusal).

    `outcome` is the venue's own label: 'Over'/'Under', 'Yes'/'No', or a club
    name on the families that use one.
    """
    m = EVENT_RE.match(slug or "")
    if not m:
        return None, "SLUG_NOT_AN_EVENT_SHAPE"
    _lg, home_code, away_code, _date, rest = m.groups()
    rest = rest or ""
    o = str(outcome).strip()

    def over_under(grid, line):
        if grid is None:
            return None, "NO_GRID_FOR_THIS_SEGMENT"
        p = evm.p_total_over(grid, line)
        if p == NOT_IDENTIFIED:
            return None, "LINE_CAN_PUSH"
        if o.casefold() == "over":
            return p, "OK"
        if o.casefold() == "under":
            return 1.0 - p, "OK"
        return None, "OUTCOME_NOT_OVER_OR_UNDER"

    def yes_no(p):
        if o.casefold() == "yes":
            return p, "OK"
        if o.casefold() == "no":
            return 1.0 - p, "OK"
        return None, "OUTCOME_NOT_YES_OR_NO"

    # ORDER MATTERS. '-first-half-total-2pt5' also ends in '-total-2pt5', and
    # '-halftime-result-draw' also ends in '-draw'. The segment-qualified
    # patterns are tested first, exactly as the outcome reconstructor does --
    # the same defect class, caught the same way.
    mm = FH_TOTAL_RE.search(rest)
    if mm:
        return over_under(ht_grid, int(mm.group(1)) + 0.5)

    mm = HALFTIME_RE.search(rest)
    if mm:
        if ht_grid is None:
            return None, "NO_GRID_FOR_THIS_SEGMENT"
        which = mm.group(1)
        p = {"home": evm.p_home_win, "away": evm.p_away_win,
             "draw": evm.p_draw}[which](ht_grid)
        return yes_no(p)

    mm = TEAM_TOTAL_RE.search(rest)
    if mm:
        side, line = mm.group(1), int(mm.group(2)) + 0.5
        marg = _marginal(ft_grid, side)
        p = sum(v for k, v in marg.items() if k > line)
        if o.casefold() == "over":
            return p, "OK"
        if o.casefold() == "under":
            return 1.0 - p, "OK"
        return None, "OUTCOME_NOT_OVER_OR_UNDER"

    mm = TOTAL_RE.search(rest)
    if mm:
        return over_under(ft_grid, int(mm.group(1)) + 0.5)

    mm = EXACT_RE.search(rest)
    if mm:
        return yes_no(evm.p_exact(ft_grid, int(mm.group(1)), int(mm.group(2))))

    if BTTS_RE.search(rest):
        return yes_no(evm.p_btts(ft_grid))

    if DRAW_RE.search(rest):
        return yes_no(evm.p_draw(ft_grid))

    if SPREAD_RE.search(rest):
        return None, SPREAD_REFUSAL

    # A bare team code: 'lal-rso-cel-2026-09-03-cel' asks whether that club
    # wins. The code must be one of the two the slug already named; a third
    # code is not this fixture's team and is refused.
    bare = rest[1:] if rest.startswith("-") else rest
    if bare and "-" not in bare:
        if bare == home_code:
            return yes_no(evm.p_home_win(ft_grid))
        if bare == away_code:
            return yes_no(evm.p_away_win(ft_grid))
        return None, "BARE_CODE_IS_NOT_A_TEAM_IN_THIS_FIXTURE"

    if not rest:
        return None, "EVENT_SLUG_HAS_NO_CONTRACT"
    return None, "MARKET_FAMILY_NOT_PRICEABLE"


PRICEABLE_FAMILIES = (
    "total", "first-half-total", "team-total", "exact-score", "draw", "btts",
    "halftime-result", "bare-team-win",
)
REFUSED_FAMILIES = ("spread-home", "spread-away")


# ---------------------------------------------------------------------------
# Walk-forward driver
# ---------------------------------------------------------------------------


class WalkForward(object):
    """Fits cached per (league, cutoff date). The cutoff is IN the cache key.

    That is the whole leakage guard: a prediction for 2026-08-30 can only ever
    be served by a fit whose key says 2026-08-30, and that fit only ever saw
    matches strictly earlier. There is no path by which a later match reaches
    an earlier prediction.
    """

    def __init__(self, fixtures, xi=XI_PER_DAY, l2=L2_PRIOR):
        self.by_league = collections.defaultdict(list)
        for f in fixtures:
            if f.get("VENUE_LEAGUE"):
                self.by_league[f["VENUE_LEAGUE"]].append(f)
        self.xi, self.l2 = xi, l2
        self._dc, self._elo = {}, {}
        self.fits_computed = 0

    def dc(self, league, as_of, goals="FT"):
        key = (league, as_of, goals)
        if key not in self._dc:
            self._dc[key] = fit_dixon_coles(
                self.by_league.get(league, ()), as_of, goals=goals,
                xi=self.xi, l2=self.l2)
            self.fits_computed += 1
        return self._dc[key]

    def elo(self, league, as_of):
        key = (league, as_of)
        if key not in self._elo:
            self._elo[key] = fit_elo(self.by_league.get(league, ()), as_of)
            self.fits_computed += 1
        return self._elo[key]

    def grids(self, league, as_of, home_key, away_key):
        ft, r1 = grid_for(self.dc(league, as_of, "FT"), home_key, away_key)
        ht, _r2 = grid_for(self.dc(league, as_of, "HT"), home_key, away_key)
        return ft, ht, r1


def provenance():
    """What this model was allowed to see. Asserted, not merely claimed."""
    return {
        "MODEL_NAME": MODEL_NAME,
        "MODEL_STATUS": MODEL_STATUS,
        "INPUTS": list(INPUTS),
        "TARGET_MARKET_PRICE_USED": TARGET_MARKET_PRICE_USED,
        "VENUE_DATA_USED": VENUE_DATA_USED,
        "WHALE_DATA_USED": WHALE_DATA_USED,
        "CORPUS_SETTLEMENTS_USED_FOR_FITTING": CORPUS_SETTLEMENTS_USED_FOR_FITTING,
        "XI_PER_DAY": XI_PER_DAY,
        "L2_PRIOR": L2_PRIOR,
        "HYPERPARAMETERS_FIXED_BEFORE_EVALUATION":
            HYPERPARAMETERS_FIXED_BEFORE_EVALUATION,
        "HYPERPARAMETER_NOTE": HYPERPARAMETER_NOTE,
        "PRICEABLE_FAMILIES": list(PRICEABLE_FAMILIES),
        "REFUSED_FAMILIES": list(REFUSED_FAMILIES),
        "SPREAD_REFUSAL_WHY": SPREAD_REFUSAL_WHY,
        "WHY_IT_NEED_NOT_BEAT_B0": WHY_IT_NEED_NOT_BEAT_B0,
    }


# ---------------------------------------------------------------------------
# THE V1 VERDICT, RECLASSIFIED PRECISELY (directive section 1)
# ---------------------------------------------------------------------------
#
# V1 was tested on ONE sample with a specific and consequential shape: 12,213
# observations over 157 events, sampled at the moments RN1 chose to trade,
# 79.9% pregame and 20.1% in-play. V1 is a static pregame model. Twenty per
# cent of the comparison therefore pitted a model that does not know the score
# against a market price that does.
#
# The result is real and it is narrow. It is recorded at the width it was
# measured at, and the wider claim is recorded as unanswered.

INDEPENDENT_V1_RESULT = "NO_INCREMENTAL_SIGNAL_ON_TESTED_RN1_TRIGGERED_SAMPLE"
GENERAL_INDEPENDENT_ALPHA_STATUS = "NOT_YET_IDENTIFIED"

V1_RESULT_MUST_NOT_BE_RESTATED_AS = (
    "INDEPENDENT_SPORTS_DATA_CANNOT_ADD_VALUE")
WHY_NOT = (
    "One model family, one input (goals), one source, four seasons, 157 "
    "events, and an evaluation clock chosen by a whale's trading. A negative "
    "result on that is a negative result on that. The families that could "
    "carry signal and were never tested include richer sport features, "
    "external market information, ensemble diversity and modern learners. "
    "Until those are tested, GENERAL_INDEPENDENT_ALPHA_STATUS stays "
    "NOT_YET_IDENTIFIED."
)

V1_TESTED_SAMPLE = {
    "OBSERVATIONS": 12213,
    "INDEPENDENT_EVENTS": 157,
    "OBSERVATION_CLOCK": "RN1_TRADE_TRIGGERED",
    "SHARE_PREGAME_PCT": 79.9,
    "SHARE_IN_PLAY_PCT": 20.1,
    "MODEL_INFORMATION_SET": "PREGAME_ONLY",
    "THE_MISMATCH": (
        "a pregame-only model was scored against a market price that, on a "
        "fifth of the rows, already knew part of the score; that comparison is "
        "a fair business benchmark and an unfair test of whether the model "
        "carries information at the same information state"),
}

PREGAME_ONLY_REEVALUATION = (
    "the same question restricted to observations that can be PROVEN pregame; "
    "see ev_core_pregame for the gate, which uses the venue's own UTC "
    "settlement stamps and needs no timezone assumption")
