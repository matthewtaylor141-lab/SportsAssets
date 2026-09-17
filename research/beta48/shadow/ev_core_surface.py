#!/usr/bin/env python3
"""EV CORE -- P_MARKET_SURFACE. One coherent event distribution, fitted to the
market's own linked prices.

WHAT THIS IS, AND WHAT IT IS EMPHATICALLY NOT. For one fixture the venue quotes
a moneyline, a draw, a totals ladder, both-teams-to-score and an exact-score
grid. Those prices are not independent opinions: they are noisy, individually
vig-laden views of ONE latent score distribution. This module fits that single
distribution to all of them at once and re-prices every contract from it.

    P_MARKET_RAW      the venue's own price for one contract. B0. The current
                      strongest empirical BASELINE.
    P_MARKET_SURFACE  what this module produces -- coherent, denoised, but
                      STILL MARKET-DERIVED.
    P_BETTOR_INDEPENDENT  built WITHOUT the target market's price. Does not
                      exist yet; it is the only object that can disagree with
                      the market on information rather than on arithmetic.

**P_MARKET_SURFACE IS NOT ALPHA.** Every input is a venue price, so a
disagreement between the surface and a single contract's raw price is a
statement about *internal consistency*, not about the world. It can say "these
two contracts on the same game cannot both be right". It cannot say "the market
is wrong about this game". Confusing the two would be the most seductive error
available here, and the output labels it at every level.

WHY IT MIGHT STILL BE WORTH SOMETHING. Two honest possibilities, neither yet
established: pooling many linked quotes may denoise a thin contract's price, and
the residual between raw and surface may flag stale quotes. Both are testable
against settlements and NEITHER has been tested yet -- the status says so.

THE FIT. Three parameters -- home scoring rate, away scoring rate, and a
Dixon-Coles low-score dependence -- chosen to minimise squared error between
model-implied and observed probabilities across every linked contract. Coarse
grid then local refinement, because no optimiser library is available here and
a three-parameter surface does not need one.

IT FAILS CLOSED. Fewer than MIN_CONTRACTS linked prices, or a fit whose residual
exceeds MAX_ACCEPTABLE_RMSE, returns SURFACE_STATUS = INSUFFICIENT or
POOR_FIT with no probabilities attached. A badly fitted surface is worse than
none, because it looks like a disagreement with the market.

This module contacts nothing and can place no order.
"""
import json
import math
import re
from collections import defaultdict

import ev_core_event_model as EM

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SURFACE_OK = "FITTED"
SURFACE_INSUFFICIENT = "INSUFFICIENT_LINKED_CONTRACTS"
SURFACE_POOR = "POOR_FIT_REFUSED"

MIN_CONTRACTS = 4
MAX_ACCEPTABLE_RMSE = 0.08

OBJECT_NAME = "P_MARKET_SURFACE"
IS_MARKET_DERIVED = True
IS_INDEPENDENT_ALPHA = False
NOT_ALPHA = (
    "every input is a venue price; the surface can say two linked contracts "
    "are mutually inconsistent, and cannot say the market is wrong about the "
    "game")
DISAGREEMENT_MEANS = (
    "a gap between P_MARKET_RAW and P_MARKET_SURFACE is an INTERNAL "
    "CONSISTENCY signal, not an informational edge")
VALIDATION_STATUS = "NOT_YET_TESTED_AGAINST_SETTLEMENTS"

# THE TIMING DEFECT, NAMED. The V0 fit took the LATEST price per contract
# before settlement. Those prices are not contemporaneous with each other, and
# for a live market a late one already incorporates the observed score. A
# surface built that way cannot support ANY predictive claim -- not settlement,
# not residual alpha, not convergence. It is a coherence prototype only.
MARKET_SURFACE_V0_STATUS = "COHERENCE_PROTOTYPE_ONLY_NONCONTEMPORANEOUS"
V0_MAY_NOT_BE_USED_FOR = (
    "SETTLEMENT_PREDICTION", "RESIDUAL_ALPHA", "CONVERGENCE",
    "FAIR_VALUE_VALIDATION",
)
V0_WHY = (
    "inputs span hours and some postdate goals; the fit therefore partly "
    "describes a known score rather than forecasting an unknown one")

# V1 requires ONE common decision timestamp T. Every input must satisfy
# PRICE_TIMESTAMP <= T, be the latest such observation, and be no older than
# MAX_QUOTE_AGE_S. Quote age travels with every input.
MARKET_SURFACE_V1_ASOF_STATUS = "SPECIFIED_NOT_YET_BUILT"
V1_REQUIREMENTS = (
    "one common T per fitted surface",
    "every input PRICE_TIMESTAMP <= T",
    "each input is the latest observation at or before T",
    "each input no older than an explicit MAX_QUOTE_AGE_S",
    "quote age reported per input; median, p90 and max per horizon",
    "pregame and live never mixed; a live surface needs game state at T",
)
LIVE_SURFACE_STATUS = "NOT_IDENTIFIED"
LIVE_SURFACE_WHY = (
    "a live surface needs the state known at T -- score, clock, cards, period "
    "-- and the retained corpus carries none of it; a live price observed "
    "after a goal is not a pregame opinion, so contaminating the pregame "
    "model with it is worse than having no live surface")

TOTAL_RE = re.compile(r"-total-(\d+)pt5$")
EXACT_RE = re.compile(r"-exact-score-(\d+)-(\d+)$")
FIRST_HALF_RE = re.compile(r"-first-half-total-\d+pt5$")
HALFTIME_RE = re.compile(r"-halftime-result-(home|away|draw)$")


def observations(event_rows, home_code, away_code):
    """Turn one fixture's contemporaneous prices into (predicate, price) pairs.

    Each entry says: this contract's price should equal the grid mass its
    settlement rule selects. Segment contracts (half-time, first-half) are
    excluded -- they constrain a different latent quantity than the full-game
    score, and folding them in would bias the fit.
    """
    obs = []
    for r in event_rows:
        slug = r.get("MARKET_SLUG") or ""
        p = r.get("P_VENUE_TRADE")
        side = (r.get("OUTCOME") or "").strip().lower()
        if p is None or not (0.0 < float(p) < 1.0):
            continue
        if FIRST_HALF_RE.search(slug) or HALFTIME_RE.search(slug):
            continue

        m = EXACT_RE.search(slug)
        if m:
            h, a = int(m.group(1)), int(m.group(2))
            if side == "yes":
                obs.append(("EXACT_%d_%d" % (h, a),
                            lambda g, h=h, a=a: EM.p_exact(g, h, a), float(p)))
            elif side == "no":
                obs.append(("NOT_EXACT_%d_%d" % (h, a),
                            lambda g, h=h, a=a: 1.0 - EM.p_exact(g, h, a),
                            float(p)))
            continue

        m = TOTAL_RE.search(slug)
        if m:
            L = int(m.group(1)) + 0.5
            if side == "over":
                obs.append(("OVER_%s" % L,
                            lambda g, L=L: EM.p_total_over(g, L), float(p)))
            elif side == "under":
                obs.append(("UNDER_%s" % L,
                            lambda g, L=L: EM.p_total_under(g, L), float(p)))
            continue

        if slug.endswith("-draw"):
            if side == "yes":
                obs.append(("DRAW", EM.p_draw, float(p)))
            elif side == "no":
                obs.append(("NOT_DRAW", lambda g: 1.0 - EM.p_draw(g), float(p)))
            continue

        if slug.endswith("-btts"):
            if side == "yes":
                obs.append(("BTTS", EM.p_btts, float(p)))
            elif side == "no":
                obs.append(("NOT_BTTS", lambda g: 1.0 - EM.p_btts(g), float(p)))
            continue

        if home_code and slug.endswith("-" + home_code):
            if side == "yes":
                obs.append(("HOME", EM.p_home_win, float(p)))
            elif side == "no":
                obs.append(("NOT_HOME", lambda g: 1.0 - EM.p_home_win(g),
                            float(p)))
            continue
        if away_code and slug.endswith("-" + away_code):
            if side == "yes":
                obs.append(("AWAY", EM.p_away_win, float(p)))
            elif side == "no":
                obs.append(("NOT_AWAY", lambda g: 1.0 - EM.p_away_win(g),
                            float(p)))
    return obs


def _rmse(grid, obs):
    n = 0
    s = 0.0
    for _, f, p in obs:
        v = f(grid)
        if not isinstance(v, float):
            continue
        s += (v - p) ** 2
        n += 1
    return math.sqrt(s / n) if n else None


def fit(obs, max_goals=8):
    """Find (lam_home, lam_away, rho) minimising squared price error.

    Coarse grid then two local refinements. Deterministic: the same inputs give
    the same parameters, which matters because a receipt points at them.
    """
    if len(obs) < MIN_CONTRACTS:
        return None

    best = None
    for lh in [x / 4.0 for x in range(1, 21)]:          # 0.25 .. 5.00
        for la in [x / 4.0 for x in range(1, 21)]:
            g = EM.score_grid(lh, la, EM.FAMILY_INDEPENDENT_POISSON,
                              max_goals=max_goals)
            e = _rmse(g, obs)
            if e is not None and (best is None or e < best[0]):
                best = (e, lh, la, 0.0)
    if best is None:
        return None

    step = 0.125
    for _ in range(2):
        e0, lh0, la0, rho0 = best
        for dh in (-step, 0.0, step):
            for da in (-step, 0.0, step):
                for rho in (-0.15, -0.10, -0.05, 0.0, 0.05):
                    lh, la = max(0.05, lh0 + dh), max(0.05, la0 + da)
                    g = EM.score_grid(lh, la, EM.FAMILY_DIXON_COLES, rho=rho,
                                      max_goals=max_goals)
                    e = _rmse(g, obs)
                    if e is not None and e < best[0]:
                        best = (e, lh, la, rho)
        step /= 2.0
    return best


def surface(event_rows, home_code=None, away_code=None, max_goals=8):
    """Fit one event's coherent market-implied distribution, or refuse."""
    obs = observations(event_rows, home_code, away_code)
    base = {
        "OBJECT": OBJECT_NAME,
        "IS_MARKET_DERIVED": IS_MARKET_DERIVED,
        "IS_INDEPENDENT_ALPHA": IS_INDEPENDENT_ALPHA,
        "NOT_ALPHA": NOT_ALPHA,
        "DISAGREEMENT_MEANS": DISAGREEMENT_MEANS,
        "VALIDATION_STATUS": VALIDATION_STATUS,
        "LINKED_CONTRACTS_USED": len(obs),
        "MIN_CONTRACTS": MIN_CONTRACTS,
    }
    if len(obs) < MIN_CONTRACTS:
        base.update({"SURFACE_STATUS": SURFACE_INSUFFICIENT,
                     "P_MARKET_SURFACE": NOT_IDENTIFIED,
                     "WHY": "fewer than %d linked prices; a three-parameter "
                            "surface on that is unconstrained" % MIN_CONTRACTS})
        return base

    got = fit(obs, max_goals)
    if got is None:
        base.update({"SURFACE_STATUS": SURFACE_INSUFFICIENT,
                     "P_MARKET_SURFACE": NOT_IDENTIFIED,
                     "WHY": "no parameter set produced a finite residual"})
        return base

    rmse, lh, la, rho = got
    if rmse > MAX_ACCEPTABLE_RMSE:
        base.update({
            "SURFACE_STATUS": SURFACE_POOR, "P_MARKET_SURFACE": NOT_IDENTIFIED,
            "FIT_RMSE": rmse, "MAX_ACCEPTABLE_RMSE": MAX_ACCEPTABLE_RMSE,
            "WHY": ("the best fit still misses the observed prices by more "
                    "than the tolerance; a badly fitted surface reads as a "
                    "disagreement with the market and is refused")})
        return base

    grid = EM.score_grid(lh, la, EM.FAMILY_DIXON_COLES, rho=rho,
                         max_goals=max_goals)
    priced = EM.price_all(grid)
    coh = EM.coherence(grid, priced)
    base.update({
        "SURFACE_STATUS": SURFACE_OK,
        "LAMBDA_HOME": lh, "LAMBDA_AWAY": la, "RHO": rho,
        "FIT_RMSE": rmse,
        "EXPECTED_TOTAL_GOALS": priced["EXPECTED_TOTAL_GOALS"],
        "EXPECTED_MARGIN": priced["EXPECTED_MARGIN"],
        "P_MARKET_SURFACE": priced,
        "COHERENCE": coh,
        "COHERENT_BY_CONSTRUCTION": coh["COHERENT"],
        "RESIDUALS": [{"CONTRACT": name, "OBSERVED": p,
                       "SURFACE": f(grid) if isinstance(f(grid), float)
                       else NOT_IDENTIFIED,
                       "GAP": (f(grid) - p) if isinstance(f(grid), float)
                       else NOT_IDENTIFIED}
                      for name, f, p in obs],
        "EVENT_DISTRIBUTION_VERSION": "MARKET_SURFACE_DC_V1",
        "MODEL_COMPONENTS": ["DIXON_COLES_SCORE_GRID"],
    })
    return base


def surface_all(rows, home_code_of=None, away_code_of=None, max_goals=8):
    """Fit every event in a row set, and report coverage honestly."""
    by = defaultdict(list)
    for r in rows or ():
        by[r.get("EVENT_KEY")].append(r)
    out, status = {}, defaultdict(int)
    for k, rs in by.items():
        hc = home_code_of(k) if home_code_of else _code(k, 1)
        ac = away_code_of(k) if away_code_of else _code(k, 2)
        s = surface(rs, hc, ac, max_goals)
        out[k] = s
        status[s["SURFACE_STATUS"]] += 1
    fitted = [s for s in out.values() if s["SURFACE_STATUS"] == SURFACE_OK]
    n = len(out)
    return {
        "EVENTS": out,
        "EVENT_COUNT": n,
        "STATUS_COUNTS": dict(status),
        "FITTED": len(fitted),
        "FITTED_PCT": (100.0 * len(fitted) / n) if n else NOT_IDENTIFIED,
        "MEAN_FIT_RMSE": (sum(s["FIT_RMSE"] for s in fitted) / len(fitted))
                         if fitted else NOT_IDENTIFIED,
        "ALL_FITTED_ARE_COHERENT": all(s["COHERENT_BY_CONSTRUCTION"]
                                       for s in fitted),
        "OBJECT": OBJECT_NAME,
        "IS_INDEPENDENT_ALPHA": IS_INDEPENDENT_ALPHA,
        "NOT_ALPHA": NOT_ALPHA,
        "VALIDATION_STATUS": VALIDATION_STATUS,
    }


def _code(event_key, idx):
    parts = (event_key or "").split("-")
    return parts[idx] if len(parts) > idx else None


def render(rep):
    L = []
    for k in ("EVENT_COUNT", "FITTED", "FITTED_PCT", "MEAN_FIT_RMSE",
              "ALL_FITTED_ARE_COHERENT", "IS_INDEPENDENT_ALPHA"):
        L.append("%-32s = %s" % (k, rep.get(k, NOT_IDENTIFIED)))
    L.append("%-32s = %s" % ("STATUS_COUNTS", rep.get("STATUS_COUNTS")))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
