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

# --- Term states. An absent economic term is not a zero one. ---------------
#
# The Monte Carlo used to substitute Dist("POINT", 0.0) for any term the
# caller did not supply, so an action with no fee, no rebate, no exit cost
# and no inventory cost still came back ACTION_EV_STATUS = IDENTIFIED with a
# clean EV_MEAN. Every one of those zeros was an assumption nobody made.
#
# A term now carries a STATE. Only KNOWN_ZERO contributes a numeric zero,
# and it does so because the economic quantity is known to be zero.
# NOT_APPLICABLE omits the term because it genuinely does not apply to this
# action. NOT_IDENTIFIED blocks IDENTIFIED outright.

TERM_STATES = ("MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR", "KNOWN_ZERO",
               "NOT_APPLICABLE", "NOT_IDENTIFIED")

RESOLVED_STATES = ("MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR",
                   "KNOWN_ZERO", "NOT_APPLICABLE")

CONTRIBUTES_ZERO = ("KNOWN_ZERO", "NOT_APPLICABLE")

UNKNOWN_IS_NOT_ZERO = (
    "an absent term is NOT_IDENTIFIED, and NOT_IDENTIFIED cannot be added to "
    "money. Only KNOWN_ZERO contributes a numeric zero, and only because the "
    "quantity is known to be zero; NOT_APPLICABLE omits a term that does not "
    "apply to this action. Substituting 0 for 'nobody told me' produces an "
    "EV that looks complete and is not")

# Terms that must be resolved before an EV may be called IDENTIFIED.
# CAPITAL_REQUIRED and OCCUPANCY_SECONDS are not in the EV arithmetic -- they
# feed the per-capital-hour rate -- so they gate that rate, not the EV.
ECONOMIC_TERMS = ("P_FILL", "VALUE_IF_FILL", "VALUE_IF_NO_FILL", "TOXICITY",
                  "FEE", "REBATE", "INVENTORY_COST", "EXIT_COST")

RATE_TERMS = ("CAPITAL_REQUIRED", "OCCUPANCY_SECONDS")

# --- Typed distribution domains. ------------------------------------------
#
# An impossible probability distribution is a bad model specification, not a
# value to clip. P_FILL ~ NORMAL(mu=2.0, sigma=0.1) used to be silently
# squeezed into [0, 1] and reported IDENTIFIED.

DOMAINS = {
    "PROBABILITY": (0.0, 1.0),
    "POSITIVE_MONEY": (0.0, None),
    "POSITIVE_TIME": (0.0, None),       # strict: zero occupancy is refused
    "SHARES": (0.0, None),
    "SIGNED_PRICE_EFFECT": (None, None),
    "PRICE": (0.0, 1.0),                # binary contract, venue-valid range
}

STRICTLY_POSITIVE_DOMAINS = ("POSITIVE_TIME",)

TERM_DOMAINS = {
    "P_FILL": "PROBABILITY",
    "VALUE_IF_FILL": "SIGNED_PRICE_EFFECT",
    "VALUE_IF_NO_FILL": "SIGNED_PRICE_EFFECT",
    "TOXICITY": "SIGNED_PRICE_EFFECT",
    "FEE": "POSITIVE_MONEY",
    "REBATE": "POSITIVE_MONEY",
    "INVENTORY_COST": "POSITIVE_MONEY",
    "EXIT_COST": "POSITIVE_MONEY",
    "CAPITAL_REQUIRED": "POSITIVE_MONEY",
    "OCCUPANCY_SECONDS": "POSITIVE_TIME",
    FILL_SELECTION_TERM: "SIGNED_PRICE_EFFECT",
}

DOMAIN_ENVELOPE = (0.001, 0.999)

INVALID_SUPPORT_IS_A_SPECIFICATION_ERROR = (
    "a distribution whose central envelope falls outside its term's domain "
    "describes a quantity that cannot exist. Clipping the draws would hide "
    "the error and report a number; the specification is refused instead")


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


EVALUATION_ID_FIELDS = ("ACTION", "PRICE", "SIZE", "TERM_MANIFEST_SHA",
                        "PRIOR_VERSION_MANIFEST_SHA", "CONVENTION",
                        "FILL_SELECTION_CONVENTION", "MODEL_VERSION",
                        "DECISION_ID")

ACTION_ALONE_IS_NOT_AN_EVALUATION = (
    "two evaluations of the same ACTION at different prices, sizes, term "
    "manifests or prior versions are different decisions. Binding an "
    "artifact by ACTION alone let a sensitivity run on one economic state "
    "discharge the width requirement for another")


def term_manifest_sha(terms):
    """A stable digest of the terms actually evaluated.

    Distributions are digested by family and parameters, declared states by
    their value. Anything unhashable is rendered by repr rather than dropped
    -- a field that cannot be digested must still change the digest.
    """
    import hashlib
    import json
    items = {}
    for k in sorted((terms or {}).keys()):
        v = terms[k]
        if isinstance(v, Dist):
            items[k] = {"FAMILY": v.family,
                        "PARAMS": {pk: v.params[pk]
                                   for pk in sorted(v.params)}}
        elif isinstance(v, (int, float, str, bool)) or v is None:
            items[k] = v
        else:
            items[k] = repr(v)
    return hashlib.sha256(
        json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()


def evaluation_id(action, terms, convention="SEPARATE_TERM", price=None,
                  size=None, prior_version_manifest_sha=None,
                  model_version=None, decision_id=None):
    """The immutable identity of ONE evaluation. Artifacts must share it."""
    import hashlib
    import json
    fsel = _fill_selection_setup(terms)
    body = {
        "ACTION": action,
        "PRICE": price if price is not None else NOT_IDENTIFIED,
        "SIZE": size if size is not None else NOT_IDENTIFIED,
        "TERM_MANIFEST_SHA": term_manifest_sha(terms),
        "PRIOR_VERSION_MANIFEST_SHA": (prior_version_manifest_sha
                                       or NOT_IDENTIFIED),
        "CONVENTION": convention,
        "FILL_SELECTION_CONVENTION": fsel.get("FILL_SELECTION_CONVENTION",
                                              "EXCLUDED"),
        "MODEL_VERSION": model_version or NOT_IDENTIFIED,
        "DECISION_ID": decision_id or NOT_IDENTIFIED,
    }
    body["EVALUATION_ID"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    body["ACTION_ALONE_IS_NOT_AN_EVALUATION"] = \
        ACTION_ALONE_IS_NOT_AN_EVALUATION
    return body


def check_domain(term, dist):
    """Is this distribution admissible for the term's domain? Fails closed."""
    dom = TERM_DOMAINS.get(term)
    if dom is None:
        return {"TERM": term, "DOMAIN": NOT_IDENTIFIED, "VALID": NOT_IDENTIFIED,
                "WHY": "no domain declared for this term"}
    lo, hi = DOMAINS[dom]
    if dist is None:
        return {"TERM": term, "DOMAIN": dom, "VALID": NOT_IDENTIFIED}
    try:
        e_lo = dist.quantile(DOMAIN_ENVELOPE[0])
        e_hi = dist.quantile(DOMAIN_ENVELOPE[1])
    except Exception:
        return {"TERM": term, "DOMAIN": dom, "VALID": False,
                "WHY": "envelope not computable"}
    strict = dom in STRICTLY_POSITIVE_DOMAINS
    below = (lo is not None) and (e_lo <= lo if strict else e_lo < lo)
    above = (hi is not None) and e_hi > hi
    out = {"TERM": term, "DOMAIN": dom, "BOUNDS": (lo, hi),
           "ENVELOPE": (round(e_lo, 10), round(e_hi, 10)),
           "ENVELOPE_LEVELS": DOMAIN_ENVELOPE,
           "VALID": not (below or above)}
    if below or above:
        out["WHY"] = (
            "%s envelope %s falls outside %s %s"
            % (term, out["ENVELOPE"], dom, out["BOUNDS"]))
        out["INVALID_SUPPORT_IS_A_SPECIFICATION_ERROR"] = \
            INVALID_SUPPORT_IS_A_SPECIFICATION_ERROR
    elif dist.family in ("NORMAL", "LOGNORMAL") and (lo is not None
                                                     or hi is not None):
        # Unbounded family inside a bounded domain: admissible, but the
        # residual tail mass is stated rather than quietly clipped away.
        out["TAIL_MASS_OUTSIDE_DOMAIN"] = round(
            _tail_mass_outside(dist, lo, hi), 12)
        out["DOMAIN_STATUS"] = "ADMITTED_WITH_TAIL_MASS_DECLARED"
    return out


def _tail_mass_outside(dist, lo, hi):
    """Exact normal/lognormal mass beyond the domain bounds."""
    p = dist.params
    if dist.family == "NORMAL":
        mu, sd = float(p["mu"]), float(p["sigma"])
        def cdf(x):
            return 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))
    else:
        mu, sd = float(p["mu"]), float(p["sigma"])
        def cdf(x):
            if x <= 0:
                return 0.0
            return 0.5 * (1.0 + math.erf(
                (math.log(x) - mu) / (sd * math.sqrt(2.0))))
    m = 0.0
    if lo is not None:
        m += cdf(lo)
    if hi is not None:
        m += 1.0 - cdf(hi)
    return m


def resolve_terms(terms, convention="SEPARATE_TERM"):
    """Resolve every term to a STATE and, where it has one, a distribution.

    A term is resolved when the caller supplied a value, or declared it
    KNOWN_ZERO or NOT_APPLICABLE. Anything else is NOT_IDENTIFIED, which is
    not a zero.
    """
    terms = terms or {}
    states, dists, conflicts, invalid = {}, {}, [], []
    for term in ECONOMIC_TERMS + RATE_TERMS + (FILL_SELECTION_TERM,):
        declared = terms.get("%s_STATE" % term)
        supplied = terms.get(term)
        dist = _as_dist(supplied)
        if declared is not None and declared not in TERM_STATES:
            conflicts.append({"TERM": term, "WHY": "UNKNOWN_TERM_STATE",
                              "GOT": declared, "DECLARED": TERM_STATES})
            states[term] = NOT_IDENTIFIED
            dists[term] = None
            continue
        if declared in CONTRIBUTES_ZERO and dist is not None:
            conflicts.append({
                "TERM": term, "WHY": "STATE_CONTRADICTS_SUPPLIED_VALUE",
                "STATE": declared,
                "DETAIL": ("a term declared %s must not also carry a "
                           "distribution" % declared)})
            states[term] = NOT_IDENTIFIED
            dists[term] = None
            continue
        if dist is not None:
            st = terms.get("%s_EVIDENCE_CLASS" % term) or declared
            if st not in ("MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR"):
                st = ESTIMATED_PRIOR
            chk = check_domain(term, dist)
            if chk.get("VALID") is False:
                invalid.append(chk)
                states[term] = "INVALID_MODEL_SPECIFICATION"
                dists[term] = None
                continue
            states[term] = st
            dists[term] = dist
            continue
        states[term] = declared if declared in CONTRIBUTES_ZERO \
            else NOT_IDENTIFIED
        dists[term] = None

    # TOXICITY is only an economic term under the SEPARATE_TERM convention.
    applicable = [t for t in ECONOMIC_TERMS
                  if not (t == "TOXICITY" and convention != "SEPARATE_TERM")]
    unresolved = [t for t in applicable if states[t] not in RESOLVED_STATES]
    return {
        "STATES": states, "DISTS": dists,
        "APPLICABLE_ECONOMIC_TERMS": tuple(applicable),
        "UNRESOLVED_ECONOMIC_TERMS": tuple(unresolved),
        "CONFLICTS": tuple(conflicts),
        "INVALID_DOMAINS": tuple(invalid),
        "ALL_RESOLVED": not unresolved and not conflicts and not invalid,
        "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
    }


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
        # REFUSED is carried in its own field, not only in the status string.
        # An earlier build signalled the refusal through FILL_SELECTION_STATUS
        # alone while setting APPLIES=False, which is indistinguishable from
        # "absent" to every caller that reads APPLIES -- so break_even() and
        # sensitivity() silently held the term at 0.0 and answered anyway.
        # A refusal that only one of four callers can see is not a refusal.
        return {"FILL_SELECTION_STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS, "DIST": None,
                "APPLIES": False, "REFUSED": True}
    if dist is None:
        return {
            "FILL_SELECTION_STATUS": "EXCLUDED_NOT_ZERO",
            "FILL_SELECTION_CONVENTION": conv or "EXCLUDED",
            "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
            "DIST": None, "APPLIES": False, "REFUSED": False,
        }
    conv = conv or "SEPARATE_TERM"
    if conv != "SEPARATE_TERM":
        return {
            "FILL_SELECTION_STATUS": "EMBEDDED_IN_VALUE_IF_FILL_NOT_ADDED",
            "FILL_SELECTION_CONVENTION": conv,
            "DOUBLE_COUNT_GUARD": DOUBLE_COUNT_GUARD,
            "DIST": dist, "APPLIES": False, "REFUSED": False,
        }
    return {
        "FILL_SELECTION_STATUS": "CARRIED_AS_SEPARATE_TERM",
        "FILL_SELECTION_CONVENTION": conv,
        "FILL_SELECTION_SIGN_CONVENTION": FILL_SELECTION_SIGN_CONVENTION,
        "DIST": dist, "APPLIES": True, "REFUSED": False,
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

    res = resolve_terms(terms, convention)
    d = {k: res["DISTS"].get(k) for k in EV_TERMS}
    missing = [k for k in CRITICAL_TERMS if d.get(k) is None]
    # TOXICITY is only critical under the SEPARATE_TERM convention.
    if convention == "SEPARATE_TERM" and d.get("TOXICITY") is None:
        missing.append("TOXICITY")
    unresolved = list(res["UNRESOLVED_ECONOMIC_TERMS"])

    if res["CONFLICTS"] or res["INVALID_DOMAINS"]:
        return {"ACTION": action,
                "STATUS": "INVALID_MODEL_SPECIFICATION",
                "ACTION_EV_STATUS": "INVALID_MODEL_SPECIFICATION",
                "EV_MEAN": NOT_IDENTIFIED,
                "RECOMMENDED": False,
                "TERM_STATE_CONFLICTS": res["CONFLICTS"],
                "INVALID_DOMAINS": res["INVALID_DOMAINS"],
                "INVALID_SUPPORT_IS_A_SPECIFICATION_ERROR":
                    INVALID_SUPPORT_IS_A_SPECIFICATION_ERROR,
                "NO_ORDER_IS_PLACED": True}

    fsel = _fill_selection_setup(terms)
    if fsel["FILL_SELECTION_STATUS"] == "UNKNOWN_FILL_SELECTION_CONVENTION":
        return {"ACTION": action,
                "STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS}

    unknown = [k for k in EV_TERMS if d.get(k) is None]
    evidence = {k: res["STATES"].get(k, NOT_IDENTIFIED) for k in EV_TERMS}
    evidence[FILL_SELECTION_TERM] = res["STATES"].get(
        FILL_SELECTION_TERM, NOT_IDENTIFIED)

    _eid = evaluation_id(
        action, terms, convention,
        price=(terms or {}).get("PRICE"), size=(terms or {}).get("SIZE"),
        prior_version_manifest_sha=(terms or {}).get(
            "PRIOR_VERSION_MANIFEST_SHA"),
        model_version=(terms or {}).get("MODEL_VERSION"),
        decision_id=(terms or {}).get("DECISION_ID"))

    base = {
        "ACTION": action,
        "EVALUATION_ID": _eid["EVALUATION_ID"],
        "EVALUATION_IDENTITY": _eid,
        "ADVERSE_SELECTION_CONVENTION": convention,
        "DOUBLE_COUNT_GUARD": DOUBLE_COUNT_GUARD,
        "FILL_SELECTION_STATUS": fsel["FILL_SELECTION_STATUS"],
        "FILL_SELECTION_CONVENTION": fsel.get("FILL_SELECTION_CONVENTION",
                                              "EXCLUDED"),
        "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
        "WIDTH_TRAVELS_WITH_THE_MEAN": WIDTH_TRAVELS_WITH_THE_MEAN,
        "UNKNOWN_TERMS": unknown,
        "TERM_STATES": dict(res["STATES"]),
        "UNRESOLVED_ECONOMIC_TERMS": tuple(unresolved),
        "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
        "MISSING_CRITICAL_TERMS": sorted(set(missing)),
        "EVIDENCE_CLASS_BY_TERM": evidence,
        "EVIDENCE_MIX": {
            "MEASURED": sum(1 for v in evidence.values()
                            if v == MEASURED_BETTOR_NATIVE),
            "ESTIMATED": sum(1 for v in evidence.values()
                             if v == ESTIMATED_PRIOR),
            "KNOWN_ZERO": sum(1 for v in evidence.values()
                              if v == "KNOWN_ZERO"),
            "NOT_APPLICABLE": sum(1 for v in evidence.values()
                                  if v == "NOT_APPLICABLE"),
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
    # Domain excursions are COUNTED, never silently clipped away. The
    # specification already passed check_domain(); any draw outside the
    # domain here is an unbounded family's declared tail, and the reader is
    # told how often it happened.
    excursions = {}

    def draw(term):
        dist = d.get(term)
        if dist is None:
            return 0.0                    # KNOWN_ZERO / NOT_APPLICABLE / TBD
        x = dist.sample(rng)
        dom = DOMAINS.get(TERM_DOMAINS.get(term, ""), (None, None))
        lo, hi = dom
        if (lo is not None and x < lo) or (hi is not None and x > hi):
            excursions[term] = excursions.get(term, 0) + 1
            x = min(x, hi) if hi is not None else x
            x = max(x, lo) if lo is not None else x
        return x

    for _ in range(draws):
        pf = draw("P_FILL")
        vf = draw("VALUE_IF_FILL")
        vn = draw("VALUE_IF_NO_FILL")
        tx = draw("TOXICITY")
        fe = draw("FEE")
        rb = draw("REBATE")
        iv = draw("INVENTORY_COST")
        ex = draw("EXIT_COST")
        fs = fs_dist.sample(rng) if fs_dist is not None else 0.0
        nets.append(_net(pf, vf, vn, tx, fe, rb, iv, ex, convention, fs))
        for p, point in fs_points.items():
            fs_nets[p].append(
                _net(pf, vf, vn, tx, fe, rb, iv, ex, convention, point))
        if d["CAPITAL_REQUIRED"] and d["OCCUPANCY_SECONDS"]:
            caps.append(draw("CAPITAL_REQUIRED"))
            hours.append(draw("OCCUPANCY_SECONDS") / 3600.0)

    n = float(len(nets))
    mean = sum(nets) / n
    var = sum((x - mean) ** 2 for x in nets) / (n - 1) if n > 1 else 0.0
    ups = [x for x in nets if x > 0]
    downs = [x for x in nets if x < 0]

    base.update({
        "ACTION_EV_STATUS": "IDENTIFIED",
        "DRAWS": int(n), "SEED": seed,
        "DOMAIN_EXCURSION_DRAWS": dict(excursions),
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
    # --- Unresolved economics downgrade the whole result. -----------------
    if unresolved:
        partial = base["EV_MEAN"]
        base["PARTIAL_EV"] = partial
        base["EV_MEAN"] = NOT_IDENTIFIED
        base["ACTION_EV_STATUS"] = "NOT_FULLY_IDENTIFIED"
        base["PARTIAL_EV_TREATS_UNRESOLVED_AS_ZERO"] = (
            "PARTIAL_EV is what the EV would be if every unresolved term were "
            "zero. It is published so the shape of the answer is visible, and "
            "it is NOT EV_MEAN because those zeros are assumptions nobody "
            "made. Resolve the terms or use the break-even")
        base["BREAK_EVEN_UNKNOWN_TERM"] = (
            unresolved[0] if len(unresolved) == 1
            else "MORE_THAN_ONE_ECONOMIC_UNKNOWN")
        base["SENSITIVITY_AVAILABLE"] = True
        base["WHAT_TO_DO_INSTEAD"] = (
            "declare each unresolved term KNOWN_ZERO or NOT_APPLICABLE if that "
            "is what it is, supply a distribution if it is estimable, or run "
            "break_even() on it. What may not happen is an EV that treats "
            "'nobody said' as 'zero'")
        base["RECOMMENDED"] = False
        base["WHY_NOT_RECOMMENDED"] = (
            "%d economic term(s) unresolved: %s"
            % (len(unresolved), ", ".join(unresolved)))
        return base

    base["RECOMMENDED"] = False
    base["WHY_NOT_RECOMMENDED"] = (
        "SHADOW_ONLY. No production acceptance threshold has been chosen, and "
        "no order path exists")
    return base


# --- Break-even: the engine that makes unknowns actionable. ----------------

A_RANGE_IS_PART_OF_THE_ANSWER = (
    "'no solution in range' is only informative when the range contains the "
    "values the term can plausibly take. Searched over the wrong interval it "
    "reports confidence about a region nobody looked at, and the default "
    "[0, 1] is a probability's range, not every term's")


def _range_covers_support(dist, lo, hi):
    """Does [lo, hi] contain the term's own P01..P99? NOT_IDENTIFIED if unknown.

    A POINT term has no support to miss. A term with no distribution supplied
    cannot be checked, and an unchecked range is not a verified one.
    """
    if dist is None or dist.family == "POINT":
        return {"COVERS": NOT_IDENTIFIED, "WHY": "no distribution to check",
                "SUPPORT": NOT_IDENTIFIED, "SUGGESTED_RANGE": NOT_IDENTIFIED}
    try:
        p01, p99 = dist.quantile(0.01), dist.quantile(0.99)
    except Exception:
        return {"COVERS": NOT_IDENTIFIED, "WHY": "support not computable",
                "SUPPORT": NOT_IDENTIFIED, "SUGGESTED_RANGE": NOT_IDENTIFIED}
    pad = max(abs(p99 - p01), 1e-12)
    return {
        "COVERS": bool(lo <= p01 and hi >= p99),
        "SUPPORT": (round(p01, 10), round(p99, 10)),
        "SUGGESTED_RANGE": (round(p01 - pad, 10), round(p99 + pad, 10)),
        "A_RANGE_IS_PART_OF_THE_ANSWER": A_RANGE_IS_PART_OF_THE_ANSWER,
    }


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
    if fsel.get("REFUSED"):
        return {"STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS,
                "WHY": ("the terms declare a fill-selection convention this "
                        "module does not recognise; solving past it would "
                        "hold the term at 0.0 without saying so")}

    # Break-even solves ONE unknown while the rest are held at their means.
    # Holding an UNRESOLVED term at 0.0 is not holding it at its mean -- it
    # is inventing one, and it produced a confident BREAK_EVEN_P_FILL of
    # ~0 while fee, rebate, inventory, exit cost and value-if-no-fill were
    # all unknown.
    res = resolve_terms(terms, convention)
    if res["CONFLICTS"] or res["INVALID_DOMAINS"]:
        return {"STATUS": "INVALID_MODEL_SPECIFICATION",
                "TERM_STATE_CONFLICTS": res["CONFLICTS"],
                "INVALID_DOMAINS": res["INVALID_DOMAINS"]}
    other_unknown = [t for t in res["UNRESOLVED_ECONOMIC_TERMS"]
                     if t != unknown_term]
    if other_unknown:
        return {
            "STATUS": "MORE_THAN_ONE_ECONOMIC_UNKNOWN",
            "ACTION": action,
            "SOLVING_FOR": unknown_term,
            "ALSO_NOT_IDENTIFIED": tuple(other_unknown),
            "WHY": ("break-even solves ONE unknown while every other "
                    "applicable term is held at its mean. A term that is "
                    "NOT_IDENTIFIED has no mean to hold it at, and holding "
                    "it at zero manufactures a confident threshold out of "
                    "quantities nobody has estimated"),
            "WHAT_TO_DO_INSTEAD": (
                "declare each of these KNOWN_ZERO or NOT_APPLICABLE if that "
                "is what they are, or supply a distribution"),
            "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
        }

    means = {}
    for k in EV_TERMS:
        dd = res["DISTS"].get(k)
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

    # Does the search range actually cover the term's own plausible support?
    # The default [0, 1] suits a probability. For a term whose prior lives in
    # +/-0.012 it excludes the entire adverse side, and the function would
    # then report ALWAYS_POSITIVE while action_ev_mc() on the same terms
    # reported a negative P10 EV -- two functions contradicting each other on
    # identical inputs, with the more confident one wrong.
    cover = _range_covers_support(_as_dist(terms.get(unknown_term)), lo, hi)

    f_lo, f_hi = ev_at(lo), ev_at(hi)
    if (f_lo > 0) == (f_hi > 0):
        out = {
            "STATUS": "NO_SOLUTION_IN_RANGE",
            "ACTION": action,
            "EVALUATION_ID": evaluation_id(
                action, terms, convention,
                price=(terms or {}).get("PRICE"),
                size=(terms or {}).get("SIZE"),
                prior_version_manifest_sha=(terms or {}).get(
                    "PRIOR_VERSION_MANIFEST_SHA"),
                model_version=(terms or {}).get("MODEL_VERSION"),
                decision_id=(terms or {}).get("DECISION_ID"))[
                    "EVALUATION_ID"],
            "UNKNOWN_TERM": unknown_term,
            "RANGE": (lo, hi),
            "EV_AT_LO": round(f_lo, 10), "EV_AT_HI": round(f_hi, 10),
            "RANGE_COVERS_TERM_SUPPORT": cover["COVERS"],
            "BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS":
                BREAK_EVEN_TURNS_UNKNOWNS_INTO_QUESTIONS,
        }
        if cover["COVERS"] is False:
            # Refuse the confident reading rather than publish it.
            out.update({
                "STATUS": "RANGE_DOES_NOT_COVER_TERM_SUPPORT",
                "ALWAYS_POSITIVE": NOT_IDENTIFIED,
                "ALWAYS_NEGATIVE": NOT_IDENTIFIED,
                "TERM_SUPPORT_P01_P99": cover["SUPPORT"],
                "SUGGESTED_RANGE": cover["SUGGESTED_RANGE"],
                "INTERPRETATION": (
                    "EV does not cross zero inside the range searched, but "
                    "the range does not contain the values this term "
                    "plausibly takes. 'No solution here' is not 'no "
                    "solution', and ALWAYS_POSITIVE would be a claim about "
                    "a region that was never searched"),
            })
            return out
        out.update({
            "INTERPRETATION": (
                "EV does not cross zero anywhere in the range. If both ends "
                "are negative, no attainable value of this term rescues the "
                "action; if both are positive, the action pays regardless of "
                "it"),
            "ALWAYS_POSITIVE": f_lo > 0 and f_hi > 0,
            "ALWAYS_NEGATIVE": f_lo < 0 and f_hi < 0,
        })
        return out
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
        "EVALUATION_ID": evaluation_id(
            action, terms, convention,
            price=(terms or {}).get("PRICE"), size=(terms or {}).get("SIZE"),
            prior_version_manifest_sha=(terms or {}).get(
                "PRIOR_VERSION_MANIFEST_SHA"),
            model_version=(terms or {}).get("MODEL_VERSION"),
            decision_id=(terms or {}).get("DECISION_ID"))["EVALUATION_ID"],
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
    if fsel.get("REFUSED"):
        return {"STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS}
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
        "EVALUATION_ID": evaluation_id(
            action, terms, convention,
            price=(terms or {}).get("PRICE"), size=(terms or {}).get("SIZE"),
            prior_version_manifest_sha=(terms or {}).get(
                "PRIOR_VERSION_MANIFEST_SHA"),
            model_version=(terms or {}).get("MODEL_VERSION"),
            decision_id=(terms or {}).get("DECISION_ID"))["EVALUATION_ID"],
        "TORNADO": rows,
        "TOP_EV_SENSITIVITY_DRIVERS": [r["TERM"] for r in rows[:3]],
        "WHY_THIS_MATTERS": (
            "a single posterior mean gives a false sense of certainty. The "
            "tornado shows which term's uncertainty actually moves the "
            "decision"),
    }


VARIANCE_ATTRIBUTION_IS_NOT_EVPI = (
    "freezing a term at its mean and measuring how much EV variance "
    "disappears attributes uncertainty. It is NOT the expected value of "
    "perfect information: EVPI is the gain from acting optimally once the "
    "true value is known, which requires an action space and a decision "
    "rule, and EVSI additionally requires an observation model saying what a "
    "given experiment would actually reveal. Neither exists here, so neither "
    "is computed and neither name is used")

EVPI_STATUS = "NOT_IMPLEMENTED_REQUIRES_DECISION_RULE_OVER_ACTION_SPACE"
EVSI_STATUS = "NOT_IMPLEMENTED_REQUIRES_OBSERVATION_MODEL"


def expected_value_of_perfect_information(*_a, **_k):
    """Deliberately unimplemented. Returns the reason, never a number."""
    return {"EVPI": NOT_IDENTIFIED, "STATUS": EVPI_STATUS,
            "REQUIRES": ("ACTION_SPACE", "DECISION_RULE",
                         "JOINT_POSTERIOR_OVER_TERMS"),
            "VARIANCE_ATTRIBUTION_IS_NOT_EVPI":
                VARIANCE_ATTRIBUTION_IS_NOT_EVPI,
            "USE_INSTEAD": "uncertainty_variance_attribution()"}


def expected_value_of_sample_information(*_a, **_k):
    """Deliberately unimplemented. Returns the reason, never a number."""
    return {"EVSI": NOT_IDENTIFIED, "STATUS": EVSI_STATUS,
            "REQUIRES": ("OBSERVATION_MODEL", "SAMPLE_DESIGN",
                         "ACTION_SPACE", "DECISION_RULE"),
            "VARIANCE_ATTRIBUTION_IS_NOT_EVPI":
                VARIANCE_ATTRIBUTION_IS_NOT_EVPI,
            "USE_INSTEAD": "uncertainty_variance_attribution()"}


def uncertainty_variance_attribution(action, terms,
                                     convention="SEPARATE_TERM",
                                     draws=4000, seed=DEFAULT_SEED):
    """Which unknown, if resolved, would most reduce EV VARIANCE?

    For each uncertain term, re-run the simulation with THAT term frozen at
    its mean and measure how much EV variance disappears. The term whose
    resolution removes the most variance is the one worth an experiment.
    """
    fsel = _fill_selection_setup(terms)
    if fsel.get("REFUSED"):
        return {"STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS}
    dists = {k: _as_dist(terms.get(k)) for k in EV_TERMS}
    dists[FILL_SELECTION_TERM] = fsel["DIST"] if fsel["APPLIES"] else None
    if any(dists.get(k) is None for k in CRITICAL_TERMS):
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "MISSING": [k for k in CRITICAL_TERMS
                            if dists.get(k) is None],
                "WHY": ("with a critical term unidentified, the question is "
                        "not how much variance it explains but what it would "
                        "have to be. Use break_even()")}
    # action_ev_mc() declines on conditions this guard does not cover (an
    # unknown adverse-selection convention, TOXICITY absent under
    # SEPARATE_TERM). Reading EV_SD off such a row raised KeyError instead of
    # returning a STATUS -- a crash is not a refusal.
    probe = action_ev_mc(action, terms, convention, draws=2, seed=seed)
    if "EV_SD" not in probe:
        return {"STATUS": probe.get("ACTION_EV_STATUS")
                or probe.get("STATUS", "NOT_FULLY_IDENTIFIED"),
                "UNDERLYING": {k: probe.get(k) for k in
                               ("STATUS", "ACTION_EV_STATUS", "DECLARED",
                                "MISSING_CRITICAL_TERMS") if k in probe},
                "WHY": ("the EV itself is not identified under these terms, "
                        "so there is no variance to attribute")}

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
        "TOP_VARIANCE_ATTRIBUTION_TERM": rows[0]["TERM"] if rows
        else NOT_IDENTIFIED,
        "TOP_VALUE_OF_INFORMATION_TERM": NOT_IDENTIFIED,
        "EVPI_STATUS": EVPI_STATUS,
        "EVSI_STATUS": EVSI_STATUS,
        "VARIANCE_ATTRIBUTION_IS_NOT_EVPI": VARIANCE_ATTRIBUTION_IS_NOT_EVPI,
        "WHAT_THIS_TELLS_MANAGEMENT": (
            "which term's uncertainty dominates the EV distribution, and "
            "therefore where a measurement would most tighten the answer. "
            "How much that tightening is WORTH is a decision-theoretic "
            "question this does not answer"),
    }


def value_of_information(*args, **kwargs):
    """Superseded name. Delegates and says so."""
    out = uncertainty_variance_attribution(*args, **kwargs)
    if isinstance(out, dict):
        out = dict(out)
        out["SUPERSEDED_NAME"] = (
            "value_of_information() measured variance attribution, not "
            "value of information. Use "
            "uncertainty_variance_attribution()")
    return out


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
    """Mark actions PARETO-dominated: no better on any axis, worse on none.

    The earlier form marked A dominated by B whenever B returned at least as
    much and was better on ANY one axis -- so B with EV 2, risk 0.5 and
    capital 100 "dominated" A with EV 1, risk 1 and capital 1, although B
    ties up a hundred times the capital. That is not dominance, it is a
    preference nobody declared.

    B dominates A only when, on EVERY comparison axis, B is at least as good
    (EV higher-is-better, risk and capital lower-is-better) and strictly
    better on at least one. A missing axis makes the comparison
    NOT_IDENTIFIED rather than silently dropping that axis.
    """
    rows = list(rows or ())
    axes = ((ev_key, "HIGHER_IS_BETTER"), (risk_key, "LOWER_IS_BETTER"),
            (capital_key, "LOWER_IS_BETTER"))
    out = []
    for i, a in enumerate(rows):
        dom_by, incomparable = None, []
        for j, b in enumerate(rows):
            if i == j:
                continue
            missing = [k for k, _ in axes
                       if not isinstance(a.get(k), (int, float))
                       or not isinstance(b.get(k), (int, float))]
            if missing:
                incomparable.append({"AGAINST": b.get("ACTION",
                                                      NOT_IDENTIFIED),
                                     "MISSING_AXES": tuple(missing)})
                continue
            weakly, strictly = True, False
            for k, sense in axes:
                va, vb = float(a[k]), float(b[k])
                better = (vb > va) if sense == "HIGHER_IS_BETTER" else (vb < va)
                worse = (vb < va) if sense == "HIGHER_IS_BETTER" else (vb > va)
                if worse:
                    weakly = False
                    break
                if better:
                    strictly = True
            if weakly and strictly:
                dom_by = b.get("ACTION", NOT_IDENTIFIED)
                break
        row = dict(a)
        if dom_by is not None:
            row["DOMINATED"] = True
            row["DOMINATED_BY"] = dom_by
            row["DOMINANCE_STATUS"] = "DOMINATED"
        elif incomparable:
            row["DOMINATED"] = NOT_IDENTIFIED
            row["DOMINATED_BY"] = NOT_IDENTIFIED
            row["DOMINANCE_STATUS"] = NOT_IDENTIFIED
            row["INCOMPARABLE"] = tuple(incomparable)
        else:
            row["DOMINATED"] = False
            row["DOMINATED_BY"] = NOT_IDENTIFIED
            row["DOMINANCE_STATUS"] = "NOT_DOMINATED"
        out.append(row)
    return {"ROWS": out,
            "AXES": axes,
            "DOMINATED_COUNT": sum(1 for r in out
                                   if r["DOMINANCE_STATUS"] == "DOMINATED"),
            "NOT_IDENTIFIED_COUNT": sum(
                1 for r in out if r["DOMINANCE_STATUS"] == NOT_IDENTIFIED),
            "WHY": ("an action Pareto-dominated on every axis at once never "
                    "deserves consideration. One that merely returns more "
                    "while consuming far more capital does not dominate -- "
                    "it trades off, and the trade-off is the caller's to "
                    "make"),
            "A_MISSING_AXIS_IS_NOT_A_TIE": (
                "dropping an axis nobody supplied would silently declare the "
                "two actions equal on it")}


def fill_selection_exposure(ev_row, sens=None, be=None):
    """Does this row publish the fill-selection width beside its answer?

    Satisfied by the three quantile EVs, OR by a sensitivity row naming the
    term, OR by a break-even solved on it -- but ONLY when that artifact
    carries the SAME EVALUATION_ID. ACTION alone is not enough: the same
    action at a different price, size, term manifest or prior version is a
    different decision, and its width is not this row's width.
    """
    row = ev_row or {}
    status = row.get("FILL_SELECTION_STATUS", NOT_IDENTIFIED)
    if status != "CARRIED_AS_SEPARATE_TERM":
        return {"EXPOSURE_REQUIRED": False,
                "FILL_SELECTION_STATUS": status,
                "REPORT_OK": True,
                "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO}
    act = row.get("ACTION")
    eid = row.get("EVALUATION_ID")

    def _bound(art):
        if not art:
            return False
        a_eid = art.get("EVALUATION_ID")
        return eid is not None and a_eid is not None and a_eid == eid

    have_q = all(isinstance(row.get(k), (int, float))
                 for k in REQUIRED_FILL_SELECTION_EXPOSURE)
    sens_names = bool(sens and any(
        r.get("TERM") == FILL_SELECTION_TERM
        for r in (sens.get("TORNADO") or ())))
    be_names = bool(
        be and be.get("STATUS") in ("SOLVED", "NO_SOLUTION_IN_RANGE",
                                    "RANGE_DOES_NOT_COVER_TERM_SUPPORT")
        and be.get("UNKNOWN_TERM") == FILL_SELECTION_TERM)
    have_sens = sens_names and _bound(sens)
    have_be = be_names and _bound(be)
    unbound = [n for n, named, bound in
               (("SENSITIVITY", sens_names, have_sens),
                ("BREAK_EVEN", be_names, have_be)) if named and not bound]
    ok = have_q or have_sens or have_be
    return {
        "EXPOSURE_REQUIRED": True,
        "FILL_SELECTION_STATUS": status,
        "ACTION": act if act is not None else NOT_IDENTIFIED,
        "EVALUATION_ID": eid if eid is not None else NOT_IDENTIFIED,
        "EVALUATION_ID_FIELDS": EVALUATION_ID_FIELDS,
        "ACTION_ALONE_IS_NOT_AN_EVALUATION": ACTION_ALONE_IS_NOT_AN_EVALUATION,
        "HAS_QUANTILE_EVS": have_q,
        "HAS_SENSITIVITY_ROW": have_sens,
        "HAS_BREAK_EVEN": have_be,
        "UNBOUND_ARTIFACTS": tuple(unbound),
        "WHY_UNBOUND_DOES_NOT_COUNT": (
            "an artifact that names the term but carries a different "
            "EVALUATION_ID describes a different decision -- a different "
            "price, size, term manifest, prior version or convention. It is "
            "not this row's width" if unbound else None),
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
    elif voi and voi.get("TOP_VARIANCE_ATTRIBUTION_TERM"):
        top = voi["TOP_VARIANCE_ATTRIBUTION_TERM"]
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
