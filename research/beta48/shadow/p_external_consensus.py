"""P_EXTERNAL_CONSENSUS_V1 -- the bookmakers' opinion, de-vigged four ways.

Directive sections 10, 11 and 12.

WHAT THIS IS
------------
A third expert, beside the venue's own price and BETTOR's fundamental model. It
is somebody else's market, so it is not independent of markets in general -- but
it IS independent of the VENUE, and that is the useful axis. If the venue's
price and a European book's price disagree, one of them is stale, and neither
`P_MARKET_RAW` nor `P_BETTOR_INDEPENDENT` can say so.

It is kept in its own module, fed only from `EXTERNAL_MARKET_COLUMNS`, and it
never touches the fundamental model. Section 6's split is the reason: an odd is
useful and it is not allowed anywhere near `P_BETTOR_INDEPENDENT`.

WHICH SOURCE, AND WHY NOT THE OTHER ONE
---------------------------------------
Two candidates were inspected.

`nm2890/football-data` publishes OPENING and CLOSING odds separately for 1X2,
over/under 2.5 and both-teams-to-score across eight leagues. The open/close
split is exactly what section 11 is about, and it would be the better source --
except that its own README states coverage from 2009/10 to 2024/25. The
evaluation window is 2026-08 and 2026-09. **There is no overlap**, so it cannot
price a single event we evaluate. Its file layout was not resolved either: the
repository's directory listing needs the GitHub API, which is scoped away from
this session, and seven guessed paths returned 404. Recorded as
`NO_OVERLAP_WITH_EVALUATION_WINDOW`, not as a failure to try.

`xgabora/Club-Football-Match-Data` carries Bet365's 1X2, over/under 2.5 and
Asian handicap odds, plus the MAXIMUM across roughly seventeen European books,
and it runs to 2026-09-03. It covers the window. So the consensus is built from
it, with the limitations below stated rather than glossed.

SECTION 11: THESE ODDS ARE NOT TIMESTAMPED
------------------------------------------
This matters more than it sounds.

The source gives one odd per market per match. It does not say WHEN that odd
stood. Football-Data's own convention is a pre-match quote, and the `Max`
columns are a maximum taken over a set of books at an unstated moment. So:

    EXTERNAL_ODDS_TIME_PRECISION = COARSE_PREMATCH_UNTIMESTAMPED

and the consequences are enforced, not merely noted:

  * These odds may be compared against the SETTLEMENT OUTCOME, because the
    outcome is after every candidate quote time. That comparison is clean.
  * They may NOT be used to predict, explain or beat a decision made at a
    specific earlier time. We cannot show they were available at T-6H, so
    using them there would introduce look-ahead of unknown size.
  * `may_compare_at()` refuses the second case by name rather than trusting
    the caller to remember.

DE-VIGGING: FOUR METHODS, BECAUSE THE CHOICE IS NOT OBVIOUS
------------------------------------------------------------
Raw implied probabilities sum to more than one. Removing that overround is a
modelling choice and the methods disagree most exactly where it matters -- on
longshots.

  MULTIPLICATIVE  divide by the sum. Simple, and it takes the same proportion
                  off every outcome, which is known to under-correct favourites.
  ODDS_RATIO      one parameter on the odds-ratio scale (Cheung).
  POWER           raise each implied probability to a common power.
  SHIN            the insider-trading model: the overround is attributed to a
                  fraction z of informed money, which bites hardest on
                  longshots.

All four are computed and reported. None is declared correct; the evaluation
picks between them on out-of-sample log loss, and if they are indistinguishable
that is reported too.
"""

from __future__ import annotations

import math
from collections import Counter

NOT_IDENTIFIED = "NOT_IDENTIFIED"

OBJECT_NAME = "P_EXTERNAL_CONSENSUS_V1"
IS_MARKET_DERIVED = True
IS_INDEPENDENT_OF_THE_VENUE = True
IS_INDEPENDENT_OF_MARKETS_IN_GENERAL = False

EXTERNAL_ODDS_SOURCE = "xgabora/Club-Football-Match-Data (Bet365 + ~17-book max)"
EXTERNAL_ODDS_UPSTREAM = "Football-Data.co.uk"
EXTERNAL_ODDS_TIME_PRECISION = "COARSE_PREMATCH_UNTIMESTAMPED"

REJECTED_SOURCE = {
    "REPOSITORY": "nm2890/football-data",
    "WHAT_IT_HAS": "opening AND closing average odds for 1X2, over/under 2.5 "
                   "and both-teams-to-score, eight leagues",
    "WHY_NOT_USED": "NO_OVERLAP_WITH_EVALUATION_WINDOW",
    "ITS_COVERAGE": "2009/10 to 2024/25",
    "EVALUATION_WINDOW": "2026-08 to 2026-09",
    "FILE_LAYOUT_RESOLVED": False,
    "WHY_LAYOUT_UNRESOLVED": "the directory listing needs the GitHub API, "
                             "which is scoped away from this session; seven "
                             "guessed paths returned 404",
}

COMPARE_AGAINST_SETTLEMENT = "PERMITTED"
COMPARE_AT_A_SPECIFIC_EARLIER_TIME = "REFUSED"
WHY_REFUSED = (
    "the odd is not timestamped, so it cannot be shown to have been available "
    "at that time; using it there would introduce look-ahead of unknown size")


def may_compare_at(evaluation_point):
    """Is this comparison admissible for an untimestamped pre-match odd?"""
    if evaluation_point == "SETTLEMENT":
        return True, COMPARE_AGAINST_SETTLEMENT
    return False, "%s: %s" % (COMPARE_AT_A_SPECIFIC_EARLIER_TIME, WHY_REFUSED)


# ---------------------------------------------------------------------------
# De-vigging
# ---------------------------------------------------------------------------

METHOD_MULTIPLICATIVE = "MULTIPLICATIVE"
METHOD_ODDS_RATIO = "ODDS_RATIO"
METHOD_POWER = "POWER"
METHOD_SHIN = "SHIN"
DEVIG_METHODS = (METHOD_MULTIPLICATIVE, METHOD_ODDS_RATIO, METHOD_POWER,
                 METHOD_SHIN)


def _implied(odds):
    out = []
    for o in odds:
        try:
            v = float(o)
        except (TypeError, ValueError):
            return None
        if not (v > 1.0):
            return None
        out.append(1.0 / v)
    return out


def _solve(f, lo, hi, iters=80):
    """Bisection on a monotone residual. Deterministic, no library needed."""
    flo, fhi = f(lo), f(hi)
    if flo == 0:
        return lo
    if fhi == 0:
        return hi
    if flo * fhi > 0:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if fm == 0:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return 0.5 * (lo + hi)


def devig(odds, method=METHOD_MULTIPLICATIVE):
    """Decimal odds -> probabilities summing to one, or (None, reason)."""
    imp = _implied(odds)
    if imp is None or len(imp) < 2:
        return None, "ODDS_UNREADABLE"
    total = sum(imp)
    if total <= 1.0:
        # An underround means a genuine best-of-market arbitrage, which the Max
        # columns can produce. Normalising is still the right move but it is
        # worth knowing it happened.
        return [p / total for p in imp], "UNDERROUND_NORMALISED"

    if method == METHOD_MULTIPLICATIVE:
        return [p / total for p in imp], "OK"

    if method == METHOD_POWER:
        def resid(k):
            return sum(p ** k for p in imp) - 1.0
        k = _solve(resid, 0.2, 3.0)
        if k is None:
            return [p / total for p in imp], "POWER_FELL_BACK_TO_MULTIPLICATIVE"
        s = sum(p ** k for p in imp)
        return [(p ** k) / s for p in imp], "OK"

    if method == METHOD_ODDS_RATIO:
        # p_true / (1 - p_true) = (1/c) * p_imp / (1 - p_imp)
        def conv(c):
            out = []
            for p in imp:
                o = (p / (1.0 - p)) / c
                out.append(o / (1.0 + o))
            return out

        def resid(c):
            return sum(conv(c)) - 1.0
        c = _solve(resid, 0.2, 8.0)
        if c is None:
            return [p / total for p in imp], "ODDS_RATIO_FELL_BACK"
        v = conv(c)
        s = sum(v)
        return [x / s for x in v], "OK"

    if method == METHOD_SHIN:
        def conv(z):
            out = []
            for p in imp:
                disc = z * z + 4.0 * (1.0 - z) * (p * p) / max(total, 1e-12)
                out.append((math.sqrt(max(disc, 0.0)) - z) / (2.0 * (1.0 - z)))
            return out

        def resid(z):
            return sum(conv(z)) - 1.0
        z = _solve(resid, 1e-6, 0.4)
        if z is None:
            return [p / total for p in imp], "SHIN_FELL_BACK"
        v = conv(z)
        s = sum(v)
        return [x / s for x in v], "OK"

    return None, "UNKNOWN_METHOD"


def overround(odds):
    imp = _implied(odds)
    return (sum(imp) - 1.0) if imp else NOT_IDENTIFIED


# ---------------------------------------------------------------------------
# From odds to a coherent event distribution
# ---------------------------------------------------------------------------


def consensus_1x2(market_row, method=METHOD_MULTIPLICATIVE, use_max=False):
    """(p_home, p_draw, p_away) from the 1X2 market, or a named refusal."""
    keys = (("MaxHome", "MaxDraw", "MaxAway") if use_max
            else ("OddHome", "OddDraw", "OddAway"))
    odds = [market_row.get(k) for k in keys]
    if any(o is None for o in odds):
        return None, "NO_1X2_ODDS"
    return devig(odds, method)


def consensus_over25(market_row, method=METHOD_MULTIPLICATIVE, use_max=False):
    keys = (("MaxOver25", "MaxUnder25") if use_max else ("Over25", "Under25"))
    odds = [market_row.get(k) for k in keys]
    if any(o is None for o in odds):
        return None, "NO_TOTALS_ODDS"
    return devig(odds, method)


FIT_STATUS_OK = "FITTED"
FIT_STATUS_REFUSED = "INSUFFICIENT_MARKETS"


def consensus_grid(market_row, method=METHOD_MULTIPLICATIVE, use_max=False,
                   max_goals=12):
    """One score distribution consistent with BOTH the 1X2 and the 2.5 line.

    The odds give four numbers; a Poisson pair plus a low-score correction has
    three parameters, so the system is over-determined and the fit is a
    compromise rather than an inversion. That is the honest framing: the books
    are not quoting a Poisson, and forcing one on them loses something.

    Returns (grid, report). Refuses when fewer than three of the four prices
    are readable -- a grid fitted to two numbers is an assumption wearing a
    distribution.
    """
    import ev_core_event_model as EM

    obs = []
    p1x2, why1 = consensus_1x2(market_row, method, use_max)
    if p1x2:
        obs.append(("HOME", EM.p_home_win, p1x2[0]))
        obs.append(("DRAW", EM.p_draw, p1x2[1]))
        obs.append(("AWAY", EM.p_away_win, p1x2[2]))
    pou, why2 = consensus_over25(market_row, method, use_max)
    if pou:
        obs.append(("OVER25", lambda g: EM.p_total_over(g, 2.5), pou[0]))
    if len(obs) < 3:
        return None, {"STATUS": FIT_STATUS_REFUSED,
                      "PRICES_READ": len(obs), "WHY_1X2": why1,
                      "WHY_TOTALS": why2}

    best = None
    for lh in [x / 10.0 for x in range(3, 41)]:
        for la in [x / 10.0 for x in range(3, 41)]:
            g = EM.score_grid(lh, la, EM.FAMILY_INDEPENDENT_POISSON,
                              max_goals=max_goals)
            if not g:
                continue
            e = sum((fn(g) - p) ** 2 for _n, fn, p in obs)
            if best is None or e < best[0]:
                best = (e, lh, la, 0.0)
    step = 0.05
    for _ in range(3):
        e0, lh0, la0, r0 = best
        for dh in (-step, 0.0, step):
            for da in (-step, 0.0, step):
                for rho in (-0.15, -0.08, 0.0, 0.05):
                    lh, la = max(0.05, lh0 + dh), max(0.05, la0 + da)
                    g = EM.score_grid(lh, la, EM.FAMILY_DIXON_COLES, rho=rho,
                                      max_goals=max_goals)
                    if not g:
                        continue
                    e = sum((fn(g) - p) ** 2 for _n, fn, p in obs)
                    if e < best[0]:
                        best = (e, lh, la, rho)
        step /= 2.0
    e, lh, la, rho = best
    g = EM.score_grid(lh, la, EM.FAMILY_DIXON_COLES, rho=rho,
                      max_goals=max_goals)
    return g, {
        "STATUS": FIT_STATUS_OK,
        "PRICES_USED": len(obs),
        "RMSE": math.sqrt(e / len(obs)),
        "LAMBDA_HOME": lh, "LAMBDA_AWAY": la, "RHO": rho,
        "DEVIG_METHOD": method,
        "USED_MAX_ODDS": use_max,
        "OVERROUND_1X2": overround([market_row.get(k) for k in
                                    (("MaxHome", "MaxDraw", "MaxAway")
                                     if use_max else
                                     ("OddHome", "OddDraw", "OddAway"))]),
        "THE_BOOKS_ARE_NOT_QUOTING_A_POISSON": (
            "four prices, three parameters: this is a compromise fit, and its "
            "residual is a real disagreement between the book and any Poisson "
            "score model, not a numerical artefact"),
    }


def describe():
    return {
        "OBJECT": OBJECT_NAME,
        "IS_MARKET_DERIVED": IS_MARKET_DERIVED,
        "IS_INDEPENDENT_OF_THE_VENUE": IS_INDEPENDENT_OF_THE_VENUE,
        "IS_INDEPENDENT_OF_MARKETS_IN_GENERAL":
            IS_INDEPENDENT_OF_MARKETS_IN_GENERAL,
        "EXTERNAL_ODDS_SOURCE": EXTERNAL_ODDS_SOURCE,
        "EXTERNAL_ODDS_UPSTREAM": EXTERNAL_ODDS_UPSTREAM,
        "EXTERNAL_ODDS_TIME_PRECISION": EXTERNAL_ODDS_TIME_PRECISION,
        "REJECTED_SOURCE": dict(REJECTED_SOURCE),
        "DEVIG_METHODS": list(DEVIG_METHODS),
        "COMPARE_AGAINST_SETTLEMENT": COMPARE_AGAINST_SETTLEMENT,
        "COMPARE_AT_A_SPECIFIC_EARLIER_TIME": COMPARE_AT_A_SPECIFIC_EARLIER_TIME,
        "WHY_REFUSED": WHY_REFUSED,
    }
