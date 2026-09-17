"""Monte Carlo action EV, break-even, value of information, sensitivity.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. NO ORDER IS PLACED. SHADOW_ONLY.

THE POINT OF THIS MODULE
------------------------
An unknown is not a stop sign. When a defensible prior exists the unknown is a
distribution, and its uncertainty propagates into the action's EV. When no
prior exists, the EV is NOT_FULLY_IDENTIFIED -- and the module then computes
the thing that is actually useful:

    WHAT WOULD THIS UNKNOWN HAVE TO BE FOR THE ACTION TO PAY?

BREAK_EVEN_P_FILL turns "P_FILL is not identified" from a dead end into a
research question with a number attached. If the answer is 0.02, the action is
probably worth taking; if it is 0.85, no plausible fill rate saves it. Either
way the unknown has been made decision-relevant without being invented.

THE DECOMPOSITION
-----------------
    EV = P_FILL * E[VALUE | FILL]
       + (1 - P_FILL) * E[VALUE | NO_FILL]
       + INCENTIVES
       - COSTS

If adverse selection is already inside E[VALUE | FILL], it is NOT subtracted
again. The double-count guard enforces that, because subtracting a risk twice
makes every quote look worse than it is and quietly kills a real edge.
"""

import math
import random

from prior_registry import Dist, NOT_IDENTIFIED, ESTIMATED_PRIOR, \
    MEASURED_BETTOR_NATIVE

SHADOW_ONLY = True
NO_ORDER_IS_PLACED = True
NOTHING_IS_TRAINED_HERE = True

DEFAULT_DRAWS = 20000
DEFAULT_SEED = 20260917

QUANTILES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)

# --- Terms. ----------------------------------------------------------------

EV_TERMS = ("P_FILL", "VALUE_IF_FILL", "VALUE_IF_NO_FILL",
            "TOXICITY", "FEE", "REBATE", "INVENTORY_COST", "EXIT_COST",
            "CAPITAL_REQUIRED", "OCCUPANCY_SECONDS")

CRITICAL_TERMS = ("P_FILL", "VALUE_IF_FILL")

ADVERSE_SELECTION_CONVENTIONS = ("EMBEDDED_IN_VALUE_IF_FILL", "SEPARATE_TERM")
DOUBLE_COUNT_GUARD = (
    "if TOXICITY is already inside VALUE_IF_FILL, subtracting it again makes "
    "every quote look worse than it is and quietly kills a real edge. The "
    "convention is declared per evaluation and enforced here")

# --- The fill-selection term. ----------------------------------------------
#
# FILL_SELECTION_EFFECT is an OPTIONAL term and is deliberately not in
# EV_TERMS: the core terms are the money paths, this one is an adjustment to
# VALUE_IF_FILL whose prior is a STRUCTURAL_NONDIRECTIONAL_PRIOR centred at
# zero with no direction assumed. Its centre being zero is why it must never
# be collapsed to its mean and forgotten -- read only through the mean it is
# indistinguishable from not being there at all.

FILL_SELECTION_TERM = "FILL_SELECTION_EFFECT"

ALL_EV_TERMS = EV_TERMS + (FILL_SELECTION_TERM,)

FILL_SELECTION_CONVENTIONS = ("SEPARATE_TERM", "EMBEDDED_IN_VALUE_IF_FILL",
                              "EXCLUDED")

FILL_SELECTION_SIGN_CONVENTION = (
    "FILL_SELECTION_MARKOUT_DELTA = MARKOUT_FILLED - "
    "MATCHED_COUNTERFACTUAL_MARKOUT. POSITIVE is FAVOURABLE to BETTOR, "
    "NEGATIVE is ADVERSE. It enters VALUE_IF_FILL additively and only when "
    "the convention says SEPARATE_TERM")

ABSENT_IS_EXCLUDED_NOT_ZERO = (
    "an absent FILL_SELECTION_EFFECT is recorded as EXCLUDED, never as 0.0. "
    "Excluded says the EV does not carry the term; zero would say the term "
    "was carried and found to be nil. A reader cannot recover the difference "
    "from the number alone, so the number does not get to hide it")

REQUIRED_FILL_SELECTION_EXPOSURE = ("EV_AT_FILL_SELECTION_P10",
                                    "EV_AT_FILL_SELECTION_P50",
                                    "EV_AT_FILL_SELECTION_P90")

WIDTH_TRAVELS_WITH_THE_MEAN = (
    "the prior's centre is zero because no direction is established, not "
    "because the effect was measured at zero. Any action whose EV materially "
    "depends on it must therefore publish the EV across the prior's own "
    "P10/P50/P90 -- or its sensitivity and break-even rows -- so that the "
    "width is on the page beside the answer")

MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD = (
    "materiality here is not a chosen cutoff. The term is material to THIS "
    "action when the sign of EV differs anywhere across the prior's P10 to "
    "P90 band -- the decision itself changes. The raw spread is reported "
    "alongside so a reader can apply their own standard")

NO_GUARANTEED_PROFIT_LANGUAGE = (
    "the engine may report POSTERIOR_EXPECTED_EV_POSITIVE, "
    "ROBUST_TO_CURRENT_UNCERTAINTY or PROSPECTIVELY_REPLICATED. It may never "
    "report GUARANTEED_PROFIT, 100_PERCENT_CONFIDENCE or CANNOT_LOSE")

FORBIDDEN_CLAIMS = ("GUARANTEED_PROFIT", "100_PERCENT_CONFIDENCE",
                    "CANNOT_LOSE", "RISK_FREE")


def _as_dist(v):
    """A term may be a Dist, a scalar (POINT), or NOT_IDENTIFIED."""
    if v is None or v == NOT_IDENTIFIED:
        return None
    if isinstance(v, Dist):
        return v
    return Dist("POINT", {"value": float(v)})


def _q(xs, p):
    if not xs:
        return NOT_IDENTIFIED
    s = sorted(xs)
    i = min(int(p * (len(s) - 1)), len(s) - 1)
    return s[i]


def _net(p_fill, v_fill, v_nofill, tox, fee, rebate, inv, exit_c, convention,
         fs=0.0):
    val = v_fill
    if convention == "SEPARATE_TERM":
        val = val - tox
    val = val + fs           # signed FAVOURABLE-positive; 0.0 when EXCLUDED
    return (p_fill * val + (1.0 - p_fill) * v_nofill
            + rebate - fee - inv - exit_c * p_fill)


def _fill_selection_setup(terms):
    """Resolve the optional fill-selection term and its convention.

    Absent means EXCLUDED, and EXCLUDED is recorded as such. It contributes
    0.0 to the arithmetic because the term is not in the model -- which is a
    different statement from the term being zero, and the status field is
    where that difference lives.
    """
    conv = (terms or {}).get("FILL_SELECTION_CONVENTION")
    dist = _as_dist((terms or {}).get(FILL_SELECTION_TERM))
    if conv is not None and conv not in FILL_SELECTION_CONVENTIONS:
        return {"FILL_SELECTION_STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS, "DIST": None,
                "APPLIES": False}
    if dist is None:
        return {
            "FILL_SELECTION_STATUS": "EXCLUDED_NOT_ZERO",
            "FILL_SELECTION_CONVENTION": conv or "EXCLUDED",
            "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
            "DIST": None, "APPLIES": False,
        }
    conv = conv or "SEPARATE_TERM"
    if conv != "SEPARATE_TERM":
        return {
            "FILL_SELECTION_STATUS": "EMBEDDED_IN_VALUE_IF_FILL_NOT_ADDED",
            "FILL_SELECTION_CONVENTION": conv,
            "DOUBLE_COUNT_GUARD": DOUBLE_COUNT_GUARD,
            "DIST": dist, "APPLIES": False,
        }
    return {
        "FILL_SELECTION_STATUS": "CARRIED_AS_SEPARATE_TERM",
        "FILL_SELECTION_CONVENTION": conv,
        "FILL_SELECTION_SIGN_CONVENTION": FILL_SELECTION_SIGN_CONVENTION,
        "DIST": dist, "APPLIES": True,
    }


def action_ev_mc(action, terms, convention="SEPARATE_TERM",
                 draws=DEFAULT_DRAWS, seed=DEFAULT_SEED):
    """The full EV distribution for one candidate action.

    `terms` maps EV_TERMS -> Dist | scalar | NOT_IDENTIFIED. Missing critical
    terms make the result NOT_FULLY_IDENTIFIED, and the caller is pointed at
    the break-even engine rather than given a fabricated number.
    """
    if convention not in ADVERSE_SELECTION_CONVENTIONS:
        return {"ACTION": action, "STATUS": "UNKNOWN_CONVENTION",
                "DECLARED": ADVERSE_SELECTION_CONVENTIONS}

    d = {k: _as_dist(terms.get(k)) for k in EV_TERMS}
    missing = [k for k in CRITICAL_TERMS if d.get(k) is None]
    # TOXICITY is only critical under the SEPARATE_TERM convention.
    if convention == "SEPARATE_TERM" and d.get("TOXICITY") is None:
        missing.append("TOXICITY")

    fsel = _fill_selection_setup(terms)
    if fsel["FILL_SELECTION_STATUS"] == "UNKNOWN_FILL_SELECTION_CONVENTION":
        return {"ACTION": action,
                "STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS}

    unknown = [k for k in EV_TERMS if d.get(k) is None]
    evidence = {k: (terms.get("%s_EVIDENCE_CLASS" % k)
                    or (NOT_IDENTIFIED if d.get(k) is None
                        else ESTIMATED_PRIOR))
                for k in EV_TERMS}
    evidence[FILL_SELECTION_TERM] = (
        terms.get("%s_EVIDENCE_CLASS" % FILL_SELECTION_TERM)
        or (NOT_IDENTIFIED if fsel["DIST"] is None else ESTIMATED_PRIOR))

    base = {
        "ACTION": action,
        "ADVERSE_SELECTION_CONVENTION": convention,
        "DOUBLE_COUNT_GUARD": DOUBLE_COUNT_GUARD,
        "FILL_SELECTION_STATUS": fsel["FILL_SELECTION_STATUS"],
        "FILL_SELECTION_CONVENTION": fsel.get("FILL_SELECTION_CONVENTION",
                                              "EXCLUDED"),
        "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
        "WIDTH_TRAVELS_WITH_THE_MEAN": WIDTH_TRAVELS_WITH_THE_MEAN,
        "UNKNOWN_TERMS": unknown,
        "MISSING_CRITICAL_TERMS": sorted(set(missing)),
        "EVIDENCE_CLASS_BY_TERM": evidence,
        "EVIDENCE_MIX": {
            "MEASURED": sum(1 for v in evidence.values()
                            if v == MEASURED_BETTOR_NATIVE),
            "ESTIMATED": sum(1 for v in evidence.values()
                             if v == ESTIMATED_PRIOR),
            "UNIDENTIFIED": sum(1 for v in evidence.values()
                                if v == NOT_IDENTIFIED),
        },
        "STATUS": "SHADOW_ONLY",
        "NO_ORDER_IS_PLACED": True,
        "NO_GUARANTEED_PROFIT_LANGUAGE": NO_GUARANTEED_PROFIT_LANGUAGE,
    }

    if missing:
        base.update({
            "ACTION_EV_STATUS": "NOT_FULLY_IDENTIFIED",
            "EV_MEAN": NOT_IDENTIFIED,
            "RECOMMENDED": False,
            "WHAT_TO_DO_INSTEAD": (
                "run break_even() on the missing term. 'P_FILL is not "
                "identified' becomes 'P_FILL must exceed X for this action to "
                "pay', which is a research question rather than a dead end"),
        })
        return base

    rng = random.Random(seed)
    zero = Dist("POINT", {"value": 0.0})
    fs_dist = fsel["DIST"] if fsel["APPLIES"] else None
    # The three frozen points are computed ONCE from the prior itself, and
    # the same random draws are reused for each -- common random numbers, so
    # the three EVs differ only by the fill-selection term.
    fs_points = ({p: fs_dist.quantile(p) for p in (0.10, 0.50, 0.90)}
                 if fs_dist is not None else {})
    nets, caps, hours = [], [], []
    fs_nets = {p: [] for p in fs_points}
    for _ in range(draws):
        pf = min(max(d["P_FILL"].sample(rng), 0.0), 1.0)
        vf = d["VALUE_IF_FILL"].sample(rng)
        vn = (d["VALUE_IF_NO_FILL"] or zero).sample(rng)
        tx = (d["TOXICITY"] or zero).sample(rng)
        fe = (d["FEE"] or zero).sample(rng)
        rb = (d["REBATE"] or zero).sample(rng)
        iv = (d["INVENTORY_COST"] or zero).sample(rng)
        ex = (d["EXIT_COST"] or zero).sample(rng)
        fs = fs_dist.sample(rng) if fs_dist is not None else 0.0
        nets.append(_net(pf, vf, vn, tx, fe, rb, iv, ex, convention, fs))
        for p, point in fs_points.items():
            fs_nets[p].append(
                _net(pf, vf, vn, tx, fe, rb, iv, ex, convention, point))
        if d["CAPITAL_REQUIRED"] and d["OCCUPANCY_SECONDS"]:
            c = max(d["CAPITAL_REQUIRED"].sample(rng), 0.0)
            s = max(d["OCCUPANCY_SECONDS"].sample(rng), 0.0)
            caps.append(c)
            hours.append(s / 3600.0)

    n = float(len(nets))
    mean = sum(nets) / n
    var = sum((x - mean) ** 2 for x in nets) / (n - 1) if n > 1 else 0.0
    ups = [x for x in nets if x > 0]
    downs = [x for x in nets if x < 0]

    base.update({
        "ACTION_EV_STATUS": "IDENTIFIED",
        "DRAWS": int(n), "SEED": seed,
        "EV_MEAN": round(mean, 10),
        "EV_SD": round(math.sqrt(var), 10),
        "EV_MEDIAN": round(_q(nets, 0.50), 10),
        "P_EV_GT_0": round(len(ups) / n, 6),
        "P_EV_LT_0": round(len(downs) / n, 6),
        "EXPECTED_UPSIDE": round(sum(ups) / len(ups), 10) if ups
        else NOT_IDENTIFIED,
        "EXPECTED_DOWNSIDE": round(sum(downs) / len(downs), 10) if downs
        else NOT_IDENTIFIED,
        "TAIL_LOSS_P05": round(_q(nets, 0.05), 10),
    })
    for p in QUANTILES:
        base["EV_P%02d" % int(round(p * 100))] = round(_q(nets, p), 10)
    base["CONSERVATIVE_EV_P10"] = base["EV_P10"]
    base["POSTERIOR_MEAN_EV"] = base["EV_MEAN"]

    # --- The fill-selection width, on the page beside the answer. ----------
    if fs_points:
        evs = {}
        for p in (0.10, 0.50, 0.90):
            key = "EV_AT_FILL_SELECTION_P%02d" % int(round(p * 100))
            xs = fs_nets[p]
            evs[p] = sum(xs) / float(len(xs))
            base[key] = round(evs[p], 10)
            base["FILL_SELECTION_P%02d" % int(round(p * 100))] = round(
                fs_points[p], 10)
        lo, hi = min(evs.values()), max(evs.values())
        base["EV_SPREAD_ACROSS_FILL_SELECTION"] = round(hi - lo, 10)
        base["FILL_SELECTION_FLIPS_THE_SIGN"] = (lo < 0 < hi)
        base["FILL_SELECTION_MATERIAL_TO_THIS_ACTION"] = (lo < 0 < hi)
        base["MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD"] = \
            MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD
        base["REQUIRED_FILL_SELECTION_EXPOSURE"] = \
            REQUIRED_FILL_SELECTION_EXPOSURE
    else:
        for key in REQUIRED_FILL_SELECTION_EXPOSURE:
            base[key] = fsel["FILL_SELECTION_STATUS"]
        base["EV_SPREAD_ACROSS_FILL_SELECTION"] = \
            fsel["FILL_SELECTION_STATUS"]
        base["FILL_SELECTION_MATERIAL_TO_THIS_ACTION"] = NOT_IDENTIFIED

    if caps:
        rates = [nets[i] / (caps[i] * hours[i])
                 for i in range(len(caps))
                 if caps[i] > 0 and hours[i] > 0]
        if rates:
            base["EV_PER_CAPITAL_HOUR_MEAN"] = round(
                sum(rates) / len(rates), 10)
            base["EV_PER_CAPITAL_HOUR_P10"] = round(_q(rates, 0.10), 10)
            base["EV_PER_CAPITAL_HOUR_P90"] = round(_q(rates, 0.90), 10)
        base["CAPITAL_OCCUPANCY_MEAN"] = round(sum(caps) / len(caps), 10)
    else:
        base["EV_PER_CAPITAL_HOUR_MEAN"] = NOT_IDENTIFIED
        base["WHY_NO_CAPITAL_RATE"] = (
            "capital required and occupancy must both be supplied; a rate on "
            "a guessed denominator is not a measurement")
    base["RECOMMENDED"] = False
    base["WHY_NOT_RECOMMENDED"] = (
        "SHADOW_ONLY. No production acceptance threshold has been chosen, and "
        "no order path exists")
    return base


# --- Break-even: the engine that makes unknowns actionable. ----------------

BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS = (
    "'P_FILL is not identified' is a dead end. 'P_FILL must exceed 0.031 for "
    "this action to pay' is a research question with a number attached, and "
    "it can be answered by an experiment")


def break_even(action, terms, unknown_term, convention="SEPARATE_TERM",
               lo=0.0, hi=1.0, tol=1e-9, max_iter=200):
    """Solve for the value of `unknown_term` at which mean EV crosses zero.

    Bisection on the term's point value, holding every other term at its mean.
    Returns NO_SOLUTION_IN_RANGE when EV does not cross zero -- which is
    itself informative: no attainable fill rate rescues a losing quote.
    """
    if unknown_term not in ALL_EV_TERMS:
        return {"STATUS": "UNKNOWN_TERM", "DECLARED": ALL_EV_TERMS}

    fsel = _fill_selection_setup(terms)
    means = {}
    for k in EV_TERMS:
        dd = _as_dist(terms.get(k))
        means[k] = 0.0 if dd is None else dd.mean()
    means[FILL_SELECTION_TERM] = (fsel["DIST"].mean()
                                  if fsel["APPLIES"] else 0.0)
    if unknown_term == FILL_SELECTION_TERM and not fsel["APPLIES"] \
            and fsel["DIST"] is not None:
        return {"STATUS": "TERM_NOT_CARRIED_SEPARATELY",
                "FILL_SELECTION_STATUS": fsel["FILL_SELECTION_STATUS"],
                "WHY": ("solving for a term that is embedded in "
                        "VALUE_IF_FILL would move it twice")}
    others_missing = [k for k in CRITICAL_TERMS
                      if k != unknown_term and _as_dist(terms.get(k)) is None]
    if others_missing:
        return {"STATUS": "MORE_THAN_ONE_UNKNOWN",
                "ALSO_MISSING": others_missing,
                "WHY": ("break-even solves for ONE unknown while the rest are "
                        "held at their means. With two unknowns the answer is "
                        "a surface, not a threshold")}

    def ev_at(x):
        m = dict(means)
        m[unknown_term] = x
        return _net(min(max(m["P_FILL"], 0.0), 1.0), m["VALUE_IF_FILL"],
                    m["VALUE_IF_NO_FILL"], m["TOXICITY"], m["FEE"],
                    m["REBATE"], m["INVENTORY_COST"], m["EXIT_COST"],
                    convention, m[FILL_SELECTION_TERM])

    f_lo, f_hi = ev_at(lo), ev_at(hi)
    if (f_lo > 0) == (f_hi > 0):
        return {
            "STATUS": "NO_SOLUTION_IN_RANGE",
            "UNKNOWN_TERM": unknown_term,
            "RANGE": (lo, hi),
            "EV_AT_LO": round(f_lo, 10), "EV_AT_HI": round(f_hi, 10),
            "INTERPRETATION": (
                "EV does not cross zero anywhere in the range. If both ends "
                "are negative, no attainable value of this term rescues the "
                "action; if both are positive, the action pays regardless of "
                "it"),
            "ALWAYS_POSITIVE": f_lo > 0 and f_hi > 0,
            "ALWAYS_NEGATIVE": f_lo < 0 and f_hi < 0,
            "BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS":
                BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS,
        }
    a, b = lo, hi
    for _ in range(max_iter):
        mid = (a + b) / 2.0
        fm = ev_at(mid)
        if abs(fm) < tol or (b - a) < tol:
            break
        if (fm > 0) == (f_lo > 0):
            a, f_lo = mid, fm
        else:
            b = mid
    x = (a + b) / 2.0
    return {
        "STATUS": "SOLVED",
        "ACTION": action,
        "UNKNOWN_TERM": unknown_term,
        "BREAK_EVEN_VALUE": round(x, 10),
        "BREAK_EVEN_%s" % unknown_term: round(x, 10),
        "EV_BELOW_IS": "NEGATIVE" if ev_at(x * 0.5) < 0 else "POSITIVE",
        "OTHER_TERMS_HELD_AT": {k: round(v, 10) for k, v in means.items()
                                if k != unknown_term},
        "BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS":
            BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS,
        "THIS_IS_NOT_AN_ESTIMATE_OF_THE_TERM": (
            "it is the threshold the term must clear. Whether it does is an "
            "empirical question this does not answer"),
    }


# --- Sensitivity (tornado) and value of information. -----------------------

def sensitivity(action, terms, convention="SEPARATE_TERM", seed=DEFAULT_SEED):
    """P25 -> P75 swing in EV attributable to each term, ranked."""
    fsel = _fill_selection_setup(terms)
    dists = {k: _as_dist(terms.get(k)) for k in EV_TERMS}
    dists[FILL_SELECTION_TERM] = fsel["DIST"] if fsel["APPLIES"] else None
    if any(dists.get(k) is None for k in CRITICAL_TERMS):
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "MISSING": [k for k in CRITICAL_TERMS
                            if dists.get(k) is None]}
    means = {k: (0.0 if d is None else d.mean()) for k, d in dists.items()}

    def ev_with(term, value):
        m = dict(means)
        m[term] = value
        return _net(min(max(m["P_FILL"], 0.0), 1.0), m["VALUE_IF_FILL"],
                    m["VALUE_IF_NO_FILL"], m["TOXICITY"], m["FEE"],
                    m["REBATE"], m["INVENTORY_COST"], m["EXIT_COST"],
                    convention, m[FILL_SELECTION_TERM])

    rows = []
    for k, d in dists.items():
        if d is None or d.family == "POINT":
            continue
        lo, hi = d.quantile(0.25), d.quantile(0.75)
        swing = abs(ev_with(k, hi) - ev_with(k, lo))
        rows.append({"TERM": k, "P25": round(lo, 10), "P75": round(hi, 10),
                     "EV_AT_P25": round(ev_with(k, lo), 10),
                     "EV_AT_P75": round(ev_with(k, hi), 10),
                     "EV_SWING": round(swing, 10)})
    rows.sort(key=lambda r: -r["EV_SWING"])
    return {
        "STATUS": "COMPUTED",
        "ACTION": action,
        "TORNADO": rows,
        "TOP_EV_SENSITIVITY_DRIVERS": [r["TERM"] for r in rows[:3]],
        "WHY_THIS_MATTERS": (
            "a single posterior mean gives a false sense of certainty. The "
            "tornado shows which term's uncertainty actually moves the "
            "decision"),
    }


def value_of_information(action, terms, convention="SEPARATE_TERM",
                         draws=4000, seed=DEFAULT_SEED):
    """Which unknown, if measured, would most reduce EV uncertainty?

    For each uncertain term, re-run the simulation with THAT term frozen at
    its mean and measure how much EV variance disappears. The term whose
    resolution removes the most variance is the one worth an experiment.
    """
    fsel = _fill_selection_setup(terms)
    dists = {k: _as_dist(terms.get(k)) for k in EV_TERMS}
    dists[FILL_SELECTION_TERM] = fsel["DIST"] if fsel["APPLIES"] else None
    if any(dists.get(k) is None for k in CRITICAL_TERMS):
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "MISSING": [k for k in CRITICAL_TERMS
                            if dists.get(k) is None],
                "WHY": ("with a critical term unidentified, the question is "
                        "not how much variance it explains but what it would "
                        "have to be. Use break_even()")}

    def var_with_frozen(frozen):
        t2 = dict(terms)
        if frozen:
            t2[frozen] = dists[frozen].mean()
        r = action_ev_mc(action, t2, convention, draws=draws, seed=seed)
        return r["EV_SD"] ** 2

    base_var = var_with_frozen(None)
    rows = []
    for k, d in dists.items():
        if d is None or d.family == "POINT":
            continue
        v = var_with_frozen(k)
        removed = max(base_var - v, 0.0)
        rows.append({
            "TERM": k,
            "VARIANCE_REMOVED_IF_RESOLVED": round(removed, 12),
            "SHARE_OF_EV_VARIANCE": (round(removed / base_var, 6)
                                     if base_var > 0 else NOT_IDENTIFIED),
        })
    rows.sort(key=lambda r: -r["VARIANCE_REMOVED_IF_RESOLVED"])
    return {
        "STATUS": "COMPUTED",
        "ACTION": action,
        "BASE_EV_VARIANCE": round(base_var, 12),
        "MARGINAL_VALUE_OF_REDUCING_UNCERTAINTY": rows,
        "TOP_VALUE_OF_INFORMATION_TERM": rows[0]["TERM"] if rows
        else NOT_IDENTIFIED,
        "WHAT_THIS_TELLS_MANAGEMENT": (
            "which measurement or experiment would most change the decision, "
            "and therefore which research dollar has the highest expected "
            "informational return"),
    }


# --- Robustness and dominance. ---------------------------------------------

ROBUSTNESS_THRESHOLD_NOT_CHOSEN = (
    "no production acceptance threshold is chosen here. The interface reports "
    "whether EV stays positive across a conservative set; what counts as "
    "'enough' is a management decision made on evidence that does not exist "
    "yet")


def robustly_positive(ev_row, conservative_quantile=0.10):
    """Is EV positive even at the conservative end of its own distribution?"""
    key = "EV_P%02d" % int(round(conservative_quantile * 100))
    v = (ev_row or {}).get(key)
    if v == NOT_IDENTIFIED or v is None:
        return {"ROBUSTLY_POSITIVE": False, "REASON": "EV_NOT_IDENTIFIED",
                "ROBUSTNESS_THRESHOLD_NOT_CHOSEN":
                    ROBUSTNESS_THRESHOLD_NOT_CHOSEN}
    return {"ROBUSTLY_POSITIVE": v > 0,
            "CONSERVATIVE_QUANTILE": conservative_quantile,
            "EV_AT_QUANTILE": v,
            "EV_MEAN": ev_row.get("EV_MEAN"),
            "P_EV_GT_0": ev_row.get("P_EV_GT_0"),
            "ROBUSTNESS_THRESHOLD_NOT_CHOSEN":
                ROBUSTNESS_THRESHOLD_NOT_CHOSEN}


def dominated(rows, ev_key="EV_MEAN", risk_key="EV_SD",
              capital_key="CAPITAL_OCCUPANCY_MEAN"):
    """Mark actions with no higher return AND higher risk or capital use."""
    out = []
    for i, a in enumerate(rows or ()):
        dom_by = None
        for j, b in enumerate(rows or ()):
            if i == j:
                continue
            ea, eb = a.get(ev_key), b.get(ev_key)
            if not isinstance(ea, (int, float)) or \
               not isinstance(eb, (int, float)):
                continue
            if eb < ea:
                continue                       # b does not return more
            worse = False
            for k in (risk_key, capital_key):
                va, vb = a.get(k), b.get(k)
                if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                    if vb < va:
                        worse = True
            if eb >= ea and worse:
                dom_by = b.get("ACTION")
                break
        out.append(dict(a, DOMINATED=dom_by is not None,
                        DOMINATED_BY=dom_by or NOT_IDENTIFIED))
    return {"ROWS": out,
            "DOMINATED_COUNT": sum(1 for r in out if r["DOMINATED"]),
            "WHY": ("an action with no higher expected return and higher risk "
                    "or capital use never deserves consideration, so removing "
                    "it shrinks the decision space for free")}


def fill_selection_exposure(ev_row, sens=None, be=None):
    """Does this row publish the fill-selection width beside its answer?

    Satisfied by the three quantile EVs, OR by a sensitivity row naming the
    term, OR by a break-even solved on it. Fails closed: a row that carries
    the term and shows none of the three is REPORT_INCOMPLETE.
    """
    row = ev_row or {}
    status = row.get("FILL_SELECTION_STATUS", NOT_IDENTIFIED)
    if status != "CARRIED_AS_SEPARATE_TERM":
        return {"EXPOSURE_REQUIRED": False,
                "FILL_SELECTION_STATUS": status,
                "REPORT_OK": True,
                "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO}
    have_q = all(isinstance(row.get(k), (int, float))
                 for k in REQUIRED_FILL_SELECTION_EXPOSURE)
    have_sens = bool(sens and any(
        r.get("TERM") == FILL_SELECTION_TERM
        for r in (sens.get("TORNADO") or ())))
    have_be = bool(be and be.get("STATUS") in ("SOLVED", "NO_SOLUTION_IN_RANGE")
                   and be.get("UNKNOWN_TERM") == FILL_SELECTION_TERM)
    ok = have_q or have_sens or have_be
    return {
        "EXPOSURE_REQUIRED": True,
        "FILL_SELECTION_STATUS": status,
        "HAS_QUANTILE_EVS": have_q,
        "HAS_SENSITIVITY_ROW": have_sens,
        "HAS_BREAK_EVEN": have_be,
        "REPORT_OK": ok,
        "REPORT_STATUS": "COMPLETE" if ok else "REPORT_INCOMPLETE",
        "REQUIRED_FILL_SELECTION_EXPOSURE": REQUIRED_FILL_SELECTION_EXPOSURE,
        "WIDTH_TRAVELS_WITH_THE_MEAN": WIDTH_TRAVELS_WITH_THE_MEAN,
    }


def shadow_decision(action, price, size, ev_row, sens=None, voi=None):
    """The shadow output format. SHADOW_ONLY, always."""
    top = NOT_IDENTIFIED
    if sens and sens.get("TOP_EV_SENSITIVITY_DRIVERS"):
        top = sens["TOP_EV_SENSITIVITY_DRIVERS"][0]
    elif voi and voi.get("TOP_VALUE_OF_INFORMATION_TERM"):
        top = voi["TOP_VALUE_OF_INFORMATION_TERM"]
    fs_keys = {k: ev_row.get(k, NOT_IDENTIFIED)
               for k in REQUIRED_FILL_SELECTION_EXPOSURE}
    return {
        "ACTION": action, "PRICE": price, "SIZE": size,
        "EV_MEAN": ev_row.get("EV_MEAN", NOT_IDENTIFIED),
        "EV_P10": ev_row.get("EV_P10", NOT_IDENTIFIED),
        "EV_P90": ev_row.get("EV_P90", NOT_IDENTIFIED),
        "P_EV_GT_0": ev_row.get("P_EV_GT_0", NOT_IDENTIFIED),
        "FILL_SELECTION_STATUS": ev_row.get("FILL_SELECTION_STATUS",
                                            NOT_IDENTIFIED),
        "FILL_SELECTION_MATERIAL_TO_THIS_ACTION": ev_row.get(
            "FILL_SELECTION_MATERIAL_TO_THIS_ACTION", NOT_IDENTIFIED),
        "FILL_SELECTION_EXPOSURE": fill_selection_exposure(ev_row, sens),
        "EV_PER_CAPITAL_HOUR": ev_row.get("EV_PER_CAPITAL_HOUR_MEAN",
                                          NOT_IDENTIFIED),
        "ACTION_EV_STATUS": ev_row.get("ACTION_EV_STATUS", NOT_IDENTIFIED),
        "TOP_UNCERTAINTY_DRIVER": top,
        "EVIDENCE_MIX": ev_row.get("EVIDENCE_MIX", {}),
        "STATUS": "SHADOW_ONLY",
        "NO_ORDER_SUBMITTED": True,
        "FORBIDDEN_CLAIMS": FORBIDDEN_CLAIMS,
        **fs_keys,
    }


def describe():
    return {
        "EV_TERMS": EV_TERMS,
        "ALL_EV_TERMS": ALL_EV_TERMS,
        "CRITICAL_TERMS": CRITICAL_TERMS,
        "ADVERSE_SELECTION_CONVENTIONS": ADVERSE_SELECTION_CONVENTIONS,
        "DOUBLE_COUNT_GUARD": DOUBLE_COUNT_GUARD,
        "FILL_SELECTION_TERM": FILL_SELECTION_TERM,
        "FILL_SELECTION_CONVENTIONS": FILL_SELECTION_CONVENTIONS,
        "FILL_SELECTION_SIGN_CONVENTION": FILL_SELECTION_SIGN_CONVENTION,
        "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
        "REQUIRED_FILL_SELECTION_EXPOSURE":
            REQUIRED_FILL_SELECTION_EXPOSURE,
        "WIDTH_TRAVELS_WITH_THE_MEAN": WIDTH_TRAVELS_WITH_THE_MEAN,
        "MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD":
            MATERIALITY_IS_A_SIGN_CHANGE_NOT_A_THRESHOLD,
        "BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS":
            BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS,
        "ROBUSTNESS_THRESHOLD_NOT_CHOSEN": ROBUSTNESS_THRESHOLD_NOT_CHOSEN,
        "NO_GUARANTEED_PROFIT_LANGUAGE": NO_GUARANTEED_PROFIT_LANGUAGE,
        "FORBIDDEN_CLAIMS": FORBIDDEN_CLAIMS,
        "SHADOW_ONLY": SHADOW_ONLY,
        "NO_ORDER_IS_PLACED": NO_ORDER_IS_PLACED,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
