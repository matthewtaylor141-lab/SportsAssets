#!/usr/bin/env python3
"""EV CORE -- THE EVENT DISTRIBUTION. One latent state, every contract.

WHY NOT PRICE EACH CONTRACT SEPARATELY. A fixture's moneyline, draw, totals
ladder, both-teams-to-score and exact-score grid are not separate questions.
They are all functions of ONE thing: the joint distribution over final scores.
Modelling them independently guarantees they will contradict each other -- a
model can happily say P(over 2.5) = 0.55 and P(over 3.5) = 0.60, which is not a
mispricing, it is an impossibility.

SO THE ORDER IS FIXED. Estimate P(home goals, away goals) first. Derive every
contract from it by summing the probability mass its settlement rule selects.
Coherence is then a property of construction, not something to test for and
patch afterwards -- and the tests here exist to catch a construction that has
broken, not to repair arithmetic.

THE FAMILIES. Both are declared CHALLENGERS. Neither is a champion, because
neither has been validated on enough BETTOR events to earn it.

  INDEPENDENT_POISSON   home and away goals independent Poisson. The standard
                        baseline; known to under-state low-score draws.
  DIXON_COLES           the same, with the Dixon-Coles dependence correction on
                        the four low scorelines (0-0, 1-0, 0-1, 1-1), which is
                        exactly where independent Poisson is known to fail.

WHY NEITHER IS FITTED HERE. Fitting attack and defence strengths needs many
observed scorelines per team. Outcome reconstruction recovered 156 exact scores
across 38 days, over hundreds of clubs. That is a handful of appearances each --
fitting team strengths on it would produce confident nonsense. The machinery is
built and coherence-tested so that it is ready the moment the sample supports
it; `fit_status` says so rather than returning numbers that look usable.

This module contacts nothing and can place no order.
"""
import json
import math
from collections import Counter

NOT_IDENTIFIED = "NOT_IDENTIFIED"

FAMILY_INDEPENDENT_POISSON = "INDEPENDENT_POISSON"
FAMILY_DIXON_COLES = "DIXON_COLES"
FAMILIES = (FAMILY_INDEPENDENT_POISSON, FAMILY_DIXON_COLES)

MODEL_STATUS = "CHALLENGER_NOT_VALIDATED"
WHY_NOT_CHAMPION = (
    "no family here has been validated on enough BETTOR events; the champion "
    "fair value remains the venue's own price")

MAX_GOALS = 12
COHERENCE_TOL = 1e-9


def _pois(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(x, y, lam, mu, rho):
    """The Dixon-Coles low-score correction. Identity outside the four cells."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def score_grid(lam_home, lam_away, family=FAMILY_INDEPENDENT_POISSON, rho=0.0,
               max_goals=MAX_GOALS):
    """P(home=x, away=y) over the grid, renormalised to sum to one.

    Renormalisation matters twice: the grid is truncated at `max_goals`, and the
    Dixon-Coles correction is not itself a probability measure. Skipping it is
    how a totals ladder quietly stops summing to one.
    """
    grid = {}
    total = 0.0
    for x in range(max_goals + 1):
        for y in range(max_goals + 1):
            p = _pois(x, lam_home) * _pois(y, lam_away)
            if family == FAMILY_DIXON_COLES:
                p *= max(0.0, _dc_tau(x, y, lam_home, lam_away, rho))
            grid[(x, y)] = p
            total += p
    if total <= 0:
        return {}
    return {k: v / total for k, v in grid.items()}


# ---------------------------------------------------------------------------
# CONTRACT DERIVATION. Each is a sum of grid mass under a settlement rule.
# The venue's own rules are used, including the push/void cases.
# ---------------------------------------------------------------------------

def p_home_win(grid):
    return sum(p for (x, y), p in grid.items() if x > y)


def p_away_win(grid):
    return sum(p for (x, y), p in grid.items() if x < y)


def p_draw(grid):
    return sum(p for (x, y), p in grid.items() if x == y)


def p_total_over(grid, line):
    """Over a .5 line. A whole-number line would push and is refused."""
    if float(line) == int(float(line)):
        return NOT_IDENTIFIED
    return sum(p for (x, y), p in grid.items() if (x + y) > float(line))


def p_total_under(grid, line):
    if float(line) == int(float(line)):
        return NOT_IDENTIFIED
    return sum(p for (x, y), p in grid.items() if (x + y) < float(line))


def p_exact(grid, h, a):
    return grid.get((int(h), int(a)), 0.0)


def p_btts(grid):
    return sum(p for (x, y), p in grid.items() if x > 0 and y > 0)


def p_spread_home_cover(grid, handicap):
    """Home covers a handicap. A whole-number handicap can push."""
    h = float(handicap)
    win = sum(p for (x, y), p in grid.items() if (x - y) + h > 0)
    push = sum(p for (x, y), p in grid.items()
               if abs((x - y) + h) < COHERENCE_TOL)
    return {"P_COVER": win, "P_PUSH": push,
            "P_NOT_COVER": max(0.0, 1.0 - win - push),
            "PUSH_IS_A_REAL_OUTCOME": push > COHERENCE_TOL,
            "WHY": ("a whole-number handicap can land exactly; folding a push "
                    "into a win or a loss misprices it")}


def price_all(grid, total_lines=(0.5, 1.5, 2.5, 3.5, 4.5, 5.5)):
    """Every supported contract on one fixture, from one distribution."""
    out = {
        "P_HOME": p_home_win(grid),
        "P_AWAY": p_away_win(grid),
        "P_DRAW": p_draw(grid),
        "P_BTTS": p_btts(grid),
        "TOTALS": {},
        "EXPECTED_TOTAL_GOALS": sum((x + y) * p for (x, y), p in grid.items()),
        "EXPECTED_MARGIN": sum((x - y) * p for (x, y), p in grid.items()),
    }
    for L in total_lines:
        out["TOTALS"][str(L)] = {"OVER": p_total_over(grid, L),
                                 "UNDER": p_total_under(grid, L)}
    return out


# ---------------------------------------------------------------------------
# COHERENCE. These catch a broken construction, not a mispricing.
# ---------------------------------------------------------------------------

COHERENCE_CHECKS = (
    "GRID_SUMS_TO_ONE",
    "OUTCOMES_PARTITION",
    "TOTALS_LADDER_MONOTONE",
    "OVER_UNDER_COMPLEMENT",
    "EXACT_SCORES_SUM_WITHIN_ONE",
    "BTTS_CONSISTENT_WITH_GRID",
)


def coherence(grid, priced=None, tol=1e-6):
    """Run every structural check. A failure invalidates the pricing."""
    priced = priced or price_all(grid)
    fails = []

    s = sum(grid.values())
    if abs(s - 1.0) > tol:
        fails.append({"CHECK": "GRID_SUMS_TO_ONE", "VALUE": s})

    part = priced["P_HOME"] + priced["P_AWAY"] + priced["P_DRAW"]
    if abs(part - 1.0) > tol:
        fails.append({"CHECK": "OUTCOMES_PARTITION", "VALUE": part})

    lines = sorted(float(k) for k in priced["TOTALS"])
    prev = None
    for L in lines:
        o = priced["TOTALS"][str(L)]["OVER"]
        u = priced["TOTALS"][str(L)]["UNDER"]
        if isinstance(o, float) and isinstance(u, float):
            if abs(o + u - 1.0) > tol:
                fails.append({"CHECK": "OVER_UNDER_COMPLEMENT", "LINE": L,
                              "VALUE": o + u})
            # A higher line is harder to go over. Equality is allowed only in
            # the degenerate case where both are numerically zero.
            if prev is not None and o > prev + tol:
                fails.append({"CHECK": "TOTALS_LADDER_MONOTONE", "LINE": L,
                              "P_OVER": o, "P_OVER_LOWER_LINE": prev,
                              "WHY": "P(over) rose at a higher line"})
            prev = o

    ex = sum(grid.values())
    if ex > 1.0 + tol:
        fails.append({"CHECK": "EXACT_SCORES_SUM_WITHIN_ONE", "VALUE": ex})

    btts_direct = sum(p for (x, y), p in grid.items() if x > 0 and y > 0)
    if abs(btts_direct - priced["P_BTTS"]) > tol:
        fails.append({"CHECK": "BTTS_CONSISTENT_WITH_GRID",
                      "VALUE": priced["P_BTTS"], "EXPECTED": btts_direct})

    return {
        "CHECKS_RUN": list(COHERENCE_CHECKS),
        "FAILURES": fails,
        "COHERENT": not fails,
        "TOLERANCE": tol,
        "WHY_THIS_MATTERS": (
            "an incoherent surface is not a trading opportunity, it is a bug; "
            "deriving every contract from one grid makes coherence structural"),
        "A_FAILURE_INVALIDATES_THE_PRICING": True,
    }


def fit_status(reconstructed_events, min_events_per_team=8):
    """Can team strengths be fitted from the reconstructed sample? Honestly.

    Answers with the actual appearance counts rather than an opinion.
    """
    scored = [e for e in (reconstructed_events or ())
              if e.get("RECONSTRUCTION_STATUS") == "UNIQUE"]
    apps = Counter()
    for e in scored:
        for c in (e.get("HOME_CODE"), e.get("AWAY_CODE")):
            if c and c != NOT_IDENTIFIED:
                apps[c] += 1
    enough = [t for t, n in apps.items() if n >= min_events_per_team]
    return {
        "EVENTS_WITH_A_RECONSTRUCTED_SCORE": len(scored),
        "DISTINCT_TEAMS": len(apps),
        "MEAN_APPEARANCES_PER_TEAM": (sum(apps.values()) / float(len(apps)))
                                     if apps else NOT_IDENTIFIED,
        "MAX_APPEARANCES": max(apps.values()) if apps else 0,
        "TEAMS_WITH_AT_LEAST_%d" % min_events_per_team: len(enough),
        "MIN_EVENTS_PER_TEAM_REQUIRED": min_events_per_team,
        "FITTABLE": len(enough) >= 20,
        "FIT_STATUS": ("SUFFICIENT" if len(enough) >= 20
                       else "INSUFFICIENT_SAMPLE"),
        "WHY": ("attack and defence strengths need repeated appearances per "
                "team; fitting them on two or three games each produces "
                "confident nonsense, so the model stays a challenger"),
        "MODEL_STATUS": MODEL_STATUS,
        "WHY_NOT_CHAMPION": WHY_NOT_CHAMPION,
    }


def describe():
    return {
        "FAMILIES": list(FAMILIES),
        "MODEL_STATUS": MODEL_STATUS,
        "WHY_NOT_CHAMPION": WHY_NOT_CHAMPION,
        "COHERENCE_CHECKS": list(COHERENCE_CHECKS),
        "DERIVATION_RULE": ("every contract is a sum of grid mass under the "
                            "venue's own settlement rule"),
        "PUSH_HANDLED": "whole-number handicaps report P_PUSH separately",
        "WHOLE_NUMBER_TOTAL_LINES_REFUSED": (
            "a whole-number total can push; p_total_over returns "
            "NOT_IDENTIFIED rather than silently picking a side"),
    }


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
