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
               "NOT_APPLICABLE", "NOT_IDENTIFIED", "UNVERIFIED_INPUT")

# UNVERIFIED_INPUT resolves the arithmetic -- there is a number to add -- and
# blocks decision grade, because nothing establishes where the number came
# from. Shadow research continues; the result is not a decision.
RESOLVED_STATES = ("MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR",
                   "KNOWN_ZERO", "NOT_APPLICABLE", "UNVERIFIED_INPUT")

DECISION_GRADE_STATES = ("MEASURED_BETTOR_NATIVE", "ESTIMATED_PRIOR",
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

# --- Decision-grade distribution families per domain. ---------------------
#
# An unbounded Normal is not a decision-grade probability model merely
# because its P001..P999 envelope lands inside [0, 1]. It still puts mass
# outside, and clipping the draws changes the distribution without changing
# its name.

DECISION_GRADE_FAMILIES = {
    "PROBABILITY": ("BETA", "TRUNCATED_NORMAL", "LOGIT_NORMAL", "TRIANGULAR",
                    "POINT"),
    "PRICE": ("BETA", "TRUNCATED_NORMAL", "LOGIT_NORMAL", "TRIANGULAR",
              "POINT"),
    "POSITIVE_MONEY": ("LOGNORMAL", "TRUNCATED_NORMAL", "TRIANGULAR",
                       "POINT"),
    "POSITIVE_TIME": ("LOGNORMAL", "TRUNCATED_NORMAL", "TRIANGULAR", "POINT"),
    "SHARES": ("LOGNORMAL", "TRUNCATED_NORMAL", "TRIANGULAR", "POINT"),
    "SIGNED_PRICE_EFFECT": ("NORMAL", "TRUNCATED_NORMAL", "TRIANGULAR",
                            "POINT", "LOGNORMAL"),
}

SHADOW_APPROXIMATION_ONLY = "SHADOW_APPROXIMATION_ONLY"

AN_UNBOUNDED_FAMILY_IS_NOT_A_BOUNDED_QUANTITY = (
    "a quantity mathematically confined to [0, 1] is not modelled by a "
    "distribution with mass outside it. The envelope check keeps an "
    "obviously impossible specification out; it does not make the model "
    "right. For decision grade the family's own SUPPORT must match the "
    "domain -- BETA, TRUNCATED_NORMAL, LOGIT_NORMAL, TRIANGULAR or POINT")

# --- Units, basis and conditioning. ---------------------------------------

UNITS = ("PROBABILITY", "PROBABILITY_POINTS_PER_SHARE",
         "USD_PER_POSTED_SHARE", "USD_PER_FILLED_SHARE", "USD_PER_ORDER",
         "USD", "SHARES", "SECONDS", "HOURS", "USD_PER_CAPITAL_HOUR")

BASES = ("PER_POSTED_SHARE", "PER_FILLED_SHARE", "PER_ORDER",
         "TOTAL_POSITION")

CONDITIONING = ("ALWAYS", "ON_FILL", "ON_NO_FILL", "PER_POSTED_SHARE",
                "PER_FILLED_SHARE", "AT_EXIT", "AT_SETTLEMENT",
                "PER_SECOND_OF_OCCUPANCY")

# Conditioning that multiplies by P_FILL, because the quantity is only
# incurred on the filled part of the order.
FILL_CONDITIONED = ("ON_FILL", "PER_FILLED_SHARE", "AT_EXIT",
                    "AT_SETTLEMENT")

UNITS_ARE_NOT_A_CONVENTION = (
    "the EV arithmetic used to rely on every term happening to be per-share "
    "in the same currency. Nothing checked it, so a per-share number passed "
    "as a total would price a 500-share action and a 5,000-share action "
    "identically. Every term declares UNIT, BASIS and APPLIES_WHEN, and "
    "quantity appears explicitly in the conversion to total dollars")

CONDITIONING_IS_STRUCTURAL_NOT_POSITIONAL = (
    "whether a cost is incurred always or only on fill used to be decided "
    "by where its variable sat in the arithmetic. It is now declared per "
    "term, so a maker fee charged only on the filled quantity and a platform "
    "fee charged on every posted order produce different -- and correct -- "
    "economics")

# The frozen default contract. A caller may override any field, and must
# declare one explicitly for decision grade.
DEFAULT_TERM_CONTRACT = {
    "P_FILL": ("PROBABILITY", "PER_ORDER", "ALWAYS"),
    "VALUE_IF_FILL": ("PROBABILITY_POINTS_PER_SHARE", "PER_FILLED_SHARE",
                      "ON_FILL"),
    "VALUE_IF_NO_FILL": ("PROBABILITY_POINTS_PER_SHARE", "PER_POSTED_SHARE",
                         "ON_NO_FILL"),
    "TOXICITY": ("PROBABILITY_POINTS_PER_SHARE", "PER_FILLED_SHARE",
                 "ON_FILL"),
    "FEE": ("USD_PER_FILLED_SHARE", "PER_FILLED_SHARE", "ON_FILL"),
    "REBATE": ("USD_PER_FILLED_SHARE", "PER_FILLED_SHARE", "ON_FILL"),
    "INVENTORY_COST": ("USD_PER_FILLED_SHARE", "PER_FILLED_SHARE", "ON_FILL"),
    "EXIT_COST": ("USD_PER_FILLED_SHARE", "PER_FILLED_SHARE", "AT_EXIT"),
    "CAPITAL_REQUIRED": ("USD", "TOTAL_POSITION", "ALWAYS"),
    "OCCUPANCY_SECONDS": ("SECONDS", "PER_ORDER", "ALWAYS"),
    FILL_SELECTION_TERM: ("PROBABILITY_POINTS_PER_SHARE", "PER_FILLED_SHARE",
                          "ON_FILL"),
}

# Which units may occupy which term slot. A unit outside this set for the
# slot is a dimensional error, not a preference.
ADMISSIBLE_UNITS = {
    "P_FILL": ("PROBABILITY",),
    "VALUE_IF_FILL": ("PROBABILITY_POINTS_PER_SHARE", "USD_PER_FILLED_SHARE"),
    "VALUE_IF_NO_FILL": ("PROBABILITY_POINTS_PER_SHARE",
                         "USD_PER_POSTED_SHARE"),
    "TOXICITY": ("PROBABILITY_POINTS_PER_SHARE", "USD_PER_FILLED_SHARE"),
    "FEE": ("USD_PER_FILLED_SHARE", "USD_PER_POSTED_SHARE", "USD_PER_ORDER"),
    "REBATE": ("USD_PER_FILLED_SHARE", "USD_PER_POSTED_SHARE",
               "USD_PER_ORDER"),
    "INVENTORY_COST": ("USD_PER_FILLED_SHARE", "USD_PER_POSTED_SHARE",
                       "USD_PER_ORDER"),
    "EXIT_COST": ("USD_PER_FILLED_SHARE", "USD_PER_ORDER"),
    "CAPITAL_REQUIRED": ("USD",),
    "OCCUPANCY_SECONDS": ("SECONDS", "HOURS"),
    FILL_SELECTION_TERM: ("PROBABILITY_POINTS_PER_SHARE",
                          "USD_PER_FILLED_SHARE"),
}

# --- Fill-bearing actions. -------------------------------------------------

FILL_BEARING_ACTIONS = ("POST_BID", "POST_ASK", "IMPROVE_BID", "IMPROVE_ASK",
                        "JOIN_BID", "JOIN_ASK", "REQUOTE_BID", "REQUOTE_ASK",
                        "POST_BOTH_SIDES")

AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION = (
    "for an action that can be filled, omitting FILL_SELECTION_CONVENTION "
    "silently resolved to EXCLUDED -- the term vanished from a quote whose "
    "whole economics turn on who trades against it. The convention must be "
    "declared SEPARATE_TERM or EMBEDDED_IN_VALUE_IF_FILL; EXCLUDED is "
    "admissible only where the action's semantics establish that fill "
    "selection genuinely does not apply")

# --- Evidence provenance. --------------------------------------------------

UNVERIFIED_INPUT = "UNVERIFIED_INPUT"

NUMERIC_PRESENCE_IS_NOT_EVIDENCE = (
    "a number arriving in the terms dict used to be classified "
    "ESTIMATED_PRIOR automatically. Nothing was estimated -- somebody typed "
    "a value. A supplied distribution now declares MEASURED_BETTOR_NATIVE "
    "with a measurement reference, or ESTIMATED_PRIOR with provenance; "
    "anything else is UNVERIFIED_INPUT, which is usable for shadow research "
    "and blocks decision grade")

EVIDENCE_REFERENCE_FIELDS = {
    ESTIMATED_PRIOR: ("PRIOR_SHA", "TERM_MANIFEST_REFERENCE",
                      "SOURCE_REFERENCE"),
    MEASURED_BETTOR_NATIVE: ("MEASUREMENT_MANIFEST_SHA",
                             "NATIVE_EVIDENCE_REFERENCE"),
}

# --- Dependence. -----------------------------------------------------------

DEPENDENCE_MODEL_STATUS = "INDEPENDENT_MARGINALS_UNVALIDATED"

DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS = (
    "the Monte Carlo draws every term independently. Nothing has validated "
    "that P_FILL, VALUE_IF_FILL and TOXICITY are independent -- they are "
    "very likely not, since the same information that fills a passive quote "
    "is the information that moves it. This is acceptable for shadow "
    "sensitivity and NOT sufficient for decision-grade P_EV_GT_0, tail loss "
    "or robust positivity. No correlation is invented to paper over it")

DEPENDENCE_FUTURE_OPTIONS = ("EMPIRICAL_JOINT_RESAMPLING",
                             "SHARED_LATENT_STATE", "JOINT_POSTERIOR_DRAWS",
                             "CALIBRATED_COPULA")

# --- The final gate. -------------------------------------------------------

DECISION_GRADE_CONDITIONS = (
    "ACTION_EV_IDENTIFIED", "EVIDENCE_PROVENANCE_VERIFIED",
    "UNIT_CONTRACT_VALID", "CONDITIONING_CONTRACT_VALID",
    "NO_UNRESOLVED_CRITICAL_TERM", "FILL_SELECTION_CONVENTION_EXPLICIT",
    "DISTRIBUTION_DOMAINS_DECISION_GRADE", "QUANTITY_DECLARED",
    "DEPENDENCE_MODEL_VALIDATED", "POSTERIOR_PRECISION_VALID",
    "LABEL_PROVENANCE_VALID", "COMMON_SUPPORT_EVALUATION_VALID",
    "DATA_QUALITY_GATE_PASSED",
)

SHADOW_RESEARCH_REMAINS_ALLOWED = (
    "DECISION_GRADE_ACTION_EV_STATUS = BLOCKED does not stop research. It "
    "stops the number being treated as a decision")

# What a row that was never downgraded says in the PARTIAL_EV_ASSUMPTION slot.
NOT_APPLICABLE_PARTIAL = "NOT_APPLICABLE_EV_NOT_DOWNGRADED"

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


def _weight(term, p_fill, cond=None):
    """How much of this term is incurred, given its declared conditioning.

    Whether a cost lands always, only on fill, or only on no-fill used to be
    decided by where its variable sat in the arithmetic: fee, rebate and
    inventory cost were charged on every order and exit cost on the filled
    part, because that is how the expression happened to be written. It is
    now read off the term's own APPLIES_WHEN.
    """
    applies = (cond or {}).get(term) or DEFAULT_TERM_CONTRACT[term][2]
    if applies in FILL_CONDITIONED:
        return p_fill
    if applies == "ON_NO_FILL":
        return 1.0 - p_fill
    return 1.0


def _net(p_fill, v_fill, v_nofill, tox, fee, rebate, inv, exit_c, convention,
         fs=0.0, cond=None):
    """Expected value PER POSTED SHARE, with every term weighted by its
    declared conditioning. Inputs are already scaled into USD per share by
    the unit contract."""
    def w(term):
        return _weight(term, p_fill, cond)
    val = w("VALUE_IF_FILL") * v_fill
    if convention == "SEPARATE_TERM":
        val = val - w("TOXICITY") * tox
    # signed FAVOURABLE-positive; 0.0 when EXCLUDED
    val = val + w(FILL_SELECTION_TERM) * fs
    return (val
            + w("VALUE_IF_NO_FILL") * v_nofill
            + w("REBATE") * rebate
            - w("FEE") * fee
            - w("INVENTORY_COST") * inv
            - w("EXIT_COST") * exit_c)


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


# --- Family support, not merely a passing envelope. ------------------------

FAMILY_SUPPORT = {
    "BETA": (0.0, 1.0),
    "LOGIT_NORMAL": (0.0, 1.0),
    "LOGNORMAL": (0.0, None),
    "NORMAL": (None, None),
    "TRIANGULAR": ("low", "high"),
    "TRUNCATED_NORMAL": ("low", "high"),
    "POINT": ("value", "value"),
}


def family_support(dist):
    """The family's own support, resolving parametric bounds. (-inf, +inf)
    is returned as (None, None)."""
    spec = FAMILY_SUPPORT.get(dist.family)
    if spec is None:
        return (None, None)
    lo, hi = spec
    if isinstance(lo, str):
        lo = float(dist.params[lo])
    if isinstance(hi, str):
        hi = float(dist.params[hi])
    return (lo, hi)


def decision_grade_distribution(term, dist):
    """Is the family's SUPPORT inside the term's domain, or only its envelope?

    check_domain() admits a distribution whose central P001..P999 envelope
    lands inside the domain. That keeps an obviously impossible specification
    out. It does not make an unbounded Normal a model of a quantity confined
    to [0, 1]: the mass outside is still there, and the draw loop clips it,
    which changes the distribution without changing its name.
    """
    dom = TERM_DOMAINS.get(term)
    if dom is None or dist is None:
        return {"TERM": term, "DISTRIBUTION_GRADE": NOT_IDENTIFIED,
                "WHY": "no domain declared, or no distribution supplied"}
    d_lo, d_hi = DOMAINS[dom]
    s_lo, s_hi = family_support(dist)
    inside = True
    if d_lo is not None and (s_lo is None or s_lo < d_lo):
        inside = False
    if d_hi is not None and (s_hi is None or s_hi > d_hi):
        inside = False
    declared = DECISION_GRADE_FAMILIES.get(dom, ())
    out = {
        "TERM": term, "DOMAIN": dom, "FAMILY": dist.family,
        "FAMILY_SUPPORT": (s_lo if s_lo is not None else "-INF",
                           s_hi if s_hi is not None else "+INF"),
        "DOMAIN_BOUNDS": (d_lo, d_hi),
        "DECISION_GRADE_FAMILIES": declared,
        "SUPPORT_INSIDE_DOMAIN": inside,
        "DISTRIBUTION_GRADE": ("DECISION_GRADE" if inside
                               else SHADOW_APPROXIMATION_ONLY),
    }
    if not inside:
        out["WHY"] = (
            "%s support %s is not contained in %s %s; the envelope may pass "
            "while the support does not"
            % (dist.family, out["FAMILY_SUPPORT"], dom, out["DOMAIN_BOUNDS"]))
        out["AN_UNBOUNDED_FAMILY_IS_NOT_A_BOUNDED_QUANTITY"] = \
            AN_UNBOUNDED_FAMILY_IS_NOT_A_BOUNDED_QUANTITY
    return out


# --- The unit, basis and quantity contract. --------------------------------

CONTRACT_SETTLEMENT_USD = 1.0

PROBABILITY_POINTS_ARE_FRACTIONAL_NOT_PERCENT = (
    "PROBABILITY_POINTS_PER_SHARE carries the fractional probability edge "
    "per share, so 0.004 is four tenths of a probability point and not four. "
    "One unit of probability edge on a contract settling at "
    "CONTRACT_SETTLEMENT_USD is worth that many dollars per share. The scale "
    "is declared here rather than assumed at each call site")

UNIT_TO_USD_PER_SHARE = {
    "PROBABILITY_POINTS_PER_SHARE": CONTRACT_SETTLEMENT_USD,
    "USD_PER_FILLED_SHARE": 1.0,
    "USD_PER_POSTED_SHARE": 1.0,
}

PER_ORDER_UNITS = ("USD_PER_ORDER",)

# Terms that are summed into the EV. P_FILL weights them; it is not one.
ADDITIVE_TERMS = tuple(t for t in ECONOMIC_TERMS if t != "P_FILL") + \
    (FILL_SELECTION_TERM,)

EV_UNIT = "USD_PER_POSTED_SHARE"

A_PER_ORDER_COST_IS_NOT_A_PER_SHARE_COST = (
    "a flat per-order fee spread over 5,000 shares is a tenth of what it is "
    "over 500. Converting it needs the order's quantity, and without an "
    "explicit QUANTITY the sum is dimensionally invalid -- not merely "
    "imprecise. No default order size is assumed")

QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS = (
    "EV_MEAN is per posted share. EV_TOTAL_USD is that times the quantity "
    "actually posted, and a total reported without a declared quantity is a "
    "per-share number wearing a dollar sign")


def term_contract(terms, term):
    """(UNIT, BASIS, APPLIES_WHEN) for one term, and whether it was declared."""
    t = terms or {}
    dflt = DEFAULT_TERM_CONTRACT[term]
    unit = t.get("%s_UNIT" % term)
    basis = t.get("%s_BASIS" % term)
    when = t.get("%s_APPLIES_WHEN" % term)
    return {
        "TERM": term,
        "UNIT": unit if unit is not None else dflt[0],
        "BASIS": basis if basis is not None else dflt[1],
        "APPLIES_WHEN": when if when is not None else dflt[2],
        "DEFAULTED_FIELDS": tuple(
            n for n, v in (("UNIT", unit), ("BASIS", basis),
                           ("APPLIES_WHEN", when)) if v is None),
        "FULLY_DECLARED": all(v is not None for v in (unit, basis, when)),
    }


def unit_contract(terms):
    """Validate every term's UNIT / BASIS / APPLIES_WHEN and build the scales.

    SCALES converts each term from its declared unit into USD per share, so
    the arithmetic in _net() is dimensionally homogeneous instead of relying
    on every caller happening to pass per-share dollars.
    """
    t = terms or {}
    qty = t.get("QUANTITY")
    if qty is None:
        qty = t.get("SIZE")
    try:
        qty = float(qty) if qty is not None else None
    except (TypeError, ValueError):
        qty = None
    contract, scales, violations, defaulted = {}, {}, [], []
    for term in ECONOMIC_TERMS + RATE_TERMS + (FILL_SELECTION_TERM,):
        c = term_contract(terms, term)
        contract[term] = c
        if not c["FULLY_DECLARED"]:
            defaulted.append(term)
        if c["UNIT"] not in UNITS:
            violations.append({"TERM": term, "WHY": "UNKNOWN_UNIT",
                               "GOT": c["UNIT"], "DECLARED": UNITS})
            continue
        if c["BASIS"] not in BASES:
            violations.append({"TERM": term, "WHY": "UNKNOWN_BASIS",
                               "GOT": c["BASIS"], "DECLARED": BASES})
            continue
        if c["APPLIES_WHEN"] not in CONDITIONING:
            violations.append({"TERM": term, "WHY": "UNKNOWN_CONDITIONING",
                               "GOT": c["APPLIES_WHEN"],
                               "DECLARED": CONDITIONING})
            continue
        adm = ADMISSIBLE_UNITS.get(term, ())
        if c["UNIT"] not in adm:
            violations.append({"TERM": term, "WHY": "INADMISSIBLE_UNIT",
                               "GOT": c["UNIT"], "ADMISSIBLE": adm,
                               "UNITS_ARE_NOT_A_CONVENTION":
                                   UNITS_ARE_NOT_A_CONVENTION})
            continue
        if term == "P_FILL":
            scales[term] = 1.0
        elif term == "CAPITAL_REQUIRED":
            scales[term] = 1.0
        elif term == "OCCUPANCY_SECONDS":
            scales[term] = 3600.0 if c["UNIT"] == "HOURS" else 1.0
        elif c["UNIT"] in UNIT_TO_USD_PER_SHARE:
            scales[term] = UNIT_TO_USD_PER_SHARE[c["UNIT"]]
        elif c["UNIT"] in PER_ORDER_UNITS:
            if qty is None or qty <= 0:
                violations.append({
                    "TERM": term, "WHY": "PER_ORDER_UNIT_REQUIRES_QUANTITY",
                    "UNIT": c["UNIT"],
                    "A_PER_ORDER_COST_IS_NOT_A_PER_SHARE_COST":
                        A_PER_ORDER_COST_IS_NOT_A_PER_SHARE_COST})
                continue
            scales[term] = 1.0 / qty
        else:
            violations.append({"TERM": term, "WHY": "UNCONVERTIBLE_UNIT",
                               "GOT": c["UNIT"]})
    cond = {k: v["APPLIES_WHEN"] for k, v in contract.items()}
    return {
        "CONTRACT": contract,
        "CONDITIONING": cond,
        "SCALES": scales,
        "VIOLATIONS": tuple(violations),
        "DEFAULTED_TERMS": tuple(defaulted),
        "QUANTITY": qty if qty is not None else NOT_IDENTIFIED,
        "QUANTITY_DECLARED": qty is not None and qty > 0,
        "QUANTITY_STATUS": ("DECLARED" if (qty is not None and qty > 0)
                            else "NOT_DECLARED"),
        "EV_UNIT": EV_UNIT,
        "UNIT_CONTRACT_VALID": not violations,
        "CONDITIONING_CONTRACT_VALID": not any(
            v["WHY"] == "UNKNOWN_CONDITIONING" for v in violations),
        "ALL_FIELDS_DECLARED": not defaulted,
        "UNITS_ARE_NOT_A_CONVENTION": UNITS_ARE_NOT_A_CONVENTION,
        "CONDITIONING_IS_STRUCTURAL_NOT_POSITIONAL":
            CONDITIONING_IS_STRUCTURAL_NOT_POSITIONAL,
        "QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS":
            QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS,
        "PROBABILITY_POINTS_ARE_FRACTIONAL_NOT_PERCENT":
            PROBABILITY_POINTS_ARE_FRACTIONAL_NOT_PERCENT,
    }


# --- Evidence provenance. --------------------------------------------------

def evidence_provenance(terms, term, declared):
    """Classify a SUPPLIED value by the evidence actually referenced.

    A number arriving in the dict used to be classified ESTIMATED_PRIOR
    automatically, so a typed-in guess and a sealed venue-mechanics prior were
    indistinguishable downstream. A class is now honoured only when the
    evidence it claims is referenced.
    """
    t = terms or {}
    claimed = t.get("%s_EVIDENCE_CLASS" % term)
    if claimed is None and declared in (MEASURED_BETTOR_NATIVE,
                                        ESTIMATED_PRIOR):
        claimed = declared
    if claimed not in EVIDENCE_REFERENCE_FIELDS:
        return {"STATE": UNVERIFIED_INPUT, "CLAIMED": claimed or NOT_IDENTIFIED,
                "REFERENCES": (),
                "WHY": "no evidence class claimed for a supplied value",
                "NUMERIC_PRESENCE_IS_NOT_EVIDENCE":
                    NUMERIC_PRESENCE_IS_NOT_EVIDENCE}
    fields = EVIDENCE_REFERENCE_FIELDS[claimed]
    found = tuple(f for f in fields
                  if t.get("%s_%s" % (term, f)) not in (None, "",
                                                        NOT_IDENTIFIED))
    if not found:
        return {"STATE": UNVERIFIED_INPUT, "CLAIMED": claimed,
                "REQUIRED_ANY_OF": fields, "REFERENCES": (),
                "WHY": ("%s claimed for %s with none of %s supplied"
                        % (claimed, term, ", ".join(fields))),
                "NUMERIC_PRESENCE_IS_NOT_EVIDENCE":
                    NUMERIC_PRESENCE_IS_NOT_EVIDENCE}
    return {"STATE": claimed, "CLAIMED": claimed, "REFERENCES": found,
            "REQUIRED_ANY_OF": fields}


def resolve_terms(terms, convention="SEPARATE_TERM"):
    """Resolve every term to a STATE and, where it has one, a distribution.

    A term is resolved when the caller supplied a value, or declared it
    KNOWN_ZERO or NOT_APPLICABLE. Anything else is NOT_IDENTIFIED, which is
    not a zero.
    """
    terms = terms or {}
    states, dists, conflicts, invalid = {}, {}, [], []
    provenance, grades = {}, {}
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
        if declared == NOT_IDENTIFIED and dist is not None:
            # A term declared unidentified while a value is supplied is a
            # contradiction, not a preference to be resolved in favour of
            # whichever field the reader happens to consult.
            conflicts.append({
                "TERM": term, "WHY": "STATE_CONTRADICTS_SUPPLIED_VALUE",
                "STATE": NOT_IDENTIFIED,
                "DETAIL": ("a term declared NOT_IDENTIFIED must not also "
                           "carry a value; one of the two is wrong and the "
                           "module does not get to pick"),
                "NUMERIC_PRESENCE_IS_NOT_EVIDENCE":
                    NUMERIC_PRESENCE_IS_NOT_EVIDENCE})
            states[term] = "INVALID_MODEL_SPECIFICATION"
            dists[term] = None
            continue
        if dist is not None:
            prov = evidence_provenance(terms, term, declared)
            provenance[term] = prov
            chk = check_domain(term, dist)
            if chk.get("VALID") is False:
                invalid.append(chk)
                states[term] = "INVALID_MODEL_SPECIFICATION"
                dists[term] = None
                continue
            grades[term] = decision_grade_distribution(term, dist)
            states[term] = prov["STATE"]
            dists[term] = dist
            continue
        states[term] = declared if declared in CONTRIBUTES_ZERO \
            else NOT_IDENTIFIED
        dists[term] = None

    # TOXICITY is only an economic term under the SEPARATE_TERM convention.
    applicable = [t for t in ECONOMIC_TERMS
                  if not (t == "TOXICITY" and convention != "SEPARATE_TERM")]
    unresolved = [t for t in applicable if states[t] not in RESOLVED_STATES]
    unverified = tuple(t for t in states if states[t] == UNVERIFIED_INPUT)
    shadow_only_dists = tuple(
        t for t, g in grades.items()
        if g.get("DISTRIBUTION_GRADE") == SHADOW_APPROXIMATION_ONLY)
    return {
        "STATES": states, "DISTS": dists,
        "EVIDENCE_PROVENANCE": provenance,
        "DISTRIBUTION_GRADES": grades,
        "UNVERIFIED_INPUT_TERMS": unverified,
        "EVIDENCE_PROVENANCE_VERIFIED": not unverified,
        "SHADOW_APPROXIMATION_ONLY_TERMS": shadow_only_dists,
        "DISTRIBUTION_DOMAINS_DECISION_GRADE": not shadow_only_dists,
        "APPLICABLE_ECONOMIC_TERMS": tuple(applicable),
        "UNRESOLVED_ECONOMIC_TERMS": tuple(unresolved),
        "CONFLICTS": tuple(conflicts),
        "INVALID_DOMAINS": tuple(invalid),
        "ALL_RESOLVED": not unresolved and not conflicts and not invalid,
        "UNKNOWN_IS_NOT_ZERO": UNKNOWN_IS_NOT_ZERO,
        "NUMERIC_PRESENCE_IS_NOT_EVIDENCE": NUMERIC_PRESENCE_IS_NOT_EVIDENCE,
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


# --- The PARTIAL_* namespace. ----------------------------------------------
#
# When the EV is not identified, every statistic computed under the
# unresolved-terms-are-zero assumption moves into PARTIAL_*. The ordinary
# names stay NOT_IDENTIFIED. The previous build downgraded EV_MEAN alone, so
# EV_SD, EV_P10, P_EV_GT_0 and EV_PER_CAPITAL_HOUR_MEAN kept publishing
# ordinary-looking numbers off the same assumption-laden draws -- and a
# downstream reader filtering on P_EV_GT_0 never saw the downgrade.

PARTIAL_EV_ASSUMPTION = "UNRESOLVED_TERMS_HELD_AT_ZERO_FOR_DIAGNOSTIC_ONLY"

PARTIAL_STATISTIC_KEYS = (
    "EV_MEAN", "EV_SD", "EV_MEDIAN", "P_EV_GT_0", "P_EV_LT_0",
    "EXPECTED_UPSIDE", "EXPECTED_DOWNSIDE", "TAIL_LOSS_P05",
    "CONSERVATIVE_EV_P10", "POSTERIOR_MEAN_EV", "EV_TOTAL_USD",
    "EV_PER_CAPITAL_HOUR_MEAN", "EV_PER_CAPITAL_HOUR_P10",
    "EV_PER_CAPITAL_HOUR_P90", "CAPITAL_OCCUPANCY_MEAN",
    "EV_SPREAD_ACROSS_FILL_SELECTION",
)

A_DOWNGRADED_MEAN_DOES_NOT_DOWNGRADE_THE_ROW = (
    "setting EV_MEAN to NOT_IDENTIFIED while EV_SD, EV_P10 and P_EV_GT_0 "
    "still carried numbers left every one of those numbers resting on the "
    "same zeros nobody supplied. A reader filtering on P_EV_GT_0 > 0.6 never "
    "saw the downgrade, so the whole statistic namespace moves together")


def _is_ev_statistic(key):
    if key in PARTIAL_STATISTIC_KEYS:
        return True
    if key.startswith("EV_P") and key[4:].isdigit():
        return True
    if key.startswith("EV_AT_FILL_SELECTION_P") and key[22:].isdigit():
        return True
    return False


def _move_to_partial(row):
    """Move every EV statistic into PARTIAL_*, leaving the ordinary names
    NOT_IDENTIFIED. Returns the same dict, mutated."""
    for key in [k for k in row.keys() if _is_ev_statistic(k)]:
        row["PARTIAL_%s" % key] = row[key]
        row[key] = NOT_IDENTIFIED
    row["PARTIAL_EV"] = row.get("PARTIAL_EV_MEAN", NOT_IDENTIFIED)
    row["PARTIAL_EV_ASSUMPTION"] = PARTIAL_EV_ASSUMPTION
    row["A_DOWNGRADED_MEAN_DOES_NOT_DOWNGRADE_THE_ROW"] = \
        A_DOWNGRADED_MEAN_DOES_NOT_DOWNGRADE_THE_ROW
    return row


# --- The decision-grade gate. ----------------------------------------------

def decision_grade_action_ev(ev_row, terms=None):
    """Is this EV fit to be acted on, or is it shadow research?

    Every condition is evaluated explicitly and the blockers are named. The
    dependence condition cannot currently pass -- the Monte Carlo draws
    independent marginals and nothing has validated that -- so this gate is
    BLOCKED by construction until a joint model exists. That is the honest
    state of the system, not a defect in the gate.
    """
    row = ev_row or {}
    t = terms or {}
    ev_status = row.get("ACTION_EV_STATUS", NOT_IDENTIFIED)
    checks = {
        "ACTION_EV_IDENTIFIED": ev_status == "IDENTIFIED",
        "EVIDENCE_PROVENANCE_VERIFIED":
            row.get("EVIDENCE_PROVENANCE_VERIFIED") is True,
        "UNIT_CONTRACT_VALID": row.get("UNIT_CONTRACT_VALID") is True,
        "CONDITIONING_CONTRACT_VALID":
            row.get("CONDITIONING_CONTRACT_VALID") is True,
        "NO_UNRESOLVED_CRITICAL_TERM":
            not row.get("MISSING_CRITICAL_TERMS")
            and not row.get("UNRESOLVED_ECONOMIC_TERMS"),
        "FILL_SELECTION_CONVENTION_EXPLICIT":
            row.get("FILL_SELECTION_CONVENTION_DECLARED") is True,
        "DISTRIBUTION_DOMAINS_DECISION_GRADE":
            row.get("DISTRIBUTION_DOMAINS_DECISION_GRADE") is True,
        "QUANTITY_DECLARED": row.get("QUANTITY_DECLARED") is True,
        "DEPENDENCE_MODEL_VALIDATED":
            row.get("DEPENDENCE_MODEL_STATUS") == "VALIDATED",
        "POSTERIOR_PRECISION_VALID":
            t.get("POSTERIOR_PRECISION_STATUS") == "DECISION_GRADE_POSTERIOR",
        "LABEL_PROVENANCE_VALID":
            t.get("LABEL_PROVENANCE_STATUS") == "VALID",
        "COMMON_SUPPORT_EVALUATION_VALID":
            t.get("COMMON_SUPPORT_STATUS") == "VALID",
        "DATA_QUALITY_GATE_PASSED": t.get("DATA_QUALITY_GATE") == "PASS",
    }
    blockers = tuple(k for k in DECISION_GRADE_CONDITIONS if not checks[k])
    return {
        "DECISION_GRADE_ACTION_EV_STATUS": "BLOCKED" if blockers else "PASS",
        "DECISION_GRADE_CONDITIONS": DECISION_GRADE_CONDITIONS,
        "DECISION_GRADE_CHECKS": checks,
        "DECISION_GRADE_BLOCKERS": blockers,
        "DEPENDENCE_MODEL_STATUS": row.get("DEPENDENCE_MODEL_STATUS",
                                           DEPENDENCE_MODEL_STATUS),
        "DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS":
            DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS,
        "SHADOW_RESEARCH_REMAINS_ALLOWED": SHADOW_RESEARCH_REMAINS_ALLOWED,
        "NO_ORDER_IS_PLACED": True,
    }


def _attach_decision_grade(row, terms):
    """Every EV row states whether it is fit to be acted on, and why not."""
    row.update(decision_grade_action_ev(row, terms))
    return row


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

    # An action that can be filled must SAY how fill selection is treated.
    # Omitting it resolved silently to EXCLUDED, so the term vanished from a
    # quote whose whole economics turn on who trades against it.
    fs_declared = (terms or {}).get("FILL_SELECTION_CONVENTION") is not None
    if action in FILL_BEARING_ACTIONS and not fs_declared:
        return {
            "ACTION": action,
            "STATUS": "FILL_SELECTION_CONVENTION_NOT_DECLARED",
            "ACTION_EV_STATUS": "FILL_SELECTION_CONVENTION_NOT_DECLARED",
            "EV_MEAN": NOT_IDENTIFIED,
            "RECOMMENDED": False,
            "FILL_BEARING_ACTIONS": FILL_BEARING_ACTIONS,
            "DECLARED": FILL_SELECTION_CONVENTIONS,
            "AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION":
                AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION,
            "NO_ORDER_IS_PLACED": True,
        }

    # The dimensional contract. A per-order fee and a per-share fee are not
    # the same number, and nothing used to check which had been supplied.
    uc = unit_contract(terms)
    if not uc["UNIT_CONTRACT_VALID"]:
        return {
            "ACTION": action,
            "STATUS": "INVALID_UNIT_CONTRACT",
            "ACTION_EV_STATUS": "INVALID_UNIT_CONTRACT",
            "EV_MEAN": NOT_IDENTIFIED,
            "RECOMMENDED": False,
            "UNIT_CONTRACT": uc,
            "UNIT_CONTRACT_VIOLATIONS": uc["VIOLATIONS"],
            "UNITS_ARE_NOT_A_CONVENTION": UNITS_ARE_NOT_A_CONVENTION,
            "NO_ORDER_IS_PLACED": True,
        }
    scales = uc["SCALES"]
    cond = uc["CONDITIONING"]

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
        "FILL_SELECTION_CONVENTION_DECLARED": fs_declared,
        "AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION":
            AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION,
        "ABSENT_IS_EXCLUDED_NOT_ZERO": ABSENT_IS_EXCLUDED_NOT_ZERO,
        "WIDTH_TRAVELS_WITH_THE_MEAN": WIDTH_TRAVELS_WITH_THE_MEAN,
        # --- the dimensional and provenance contracts, on the row ---------
        "UNIT_CONTRACT": uc["CONTRACT"],
        "UNIT_CONTRACT_VALID": uc["UNIT_CONTRACT_VALID"],
        "CONDITIONING_CONTRACT_VALID": uc["CONDITIONING_CONTRACT_VALID"],
        "CONDITIONING_BY_TERM": cond,
        "UNIT_CONTRACT_DEFAULTED_TERMS": uc["DEFAULTED_TERMS"],
        "EV_UNIT": uc["EV_UNIT"],
        "QUANTITY": uc["QUANTITY"],
        "QUANTITY_DECLARED": uc["QUANTITY_DECLARED"],
        "QUANTITY_STATUS": uc["QUANTITY_STATUS"],
        "QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS":
            QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS,
        "EVIDENCE_PROVENANCE": res["EVIDENCE_PROVENANCE"],
        "EVIDENCE_PROVENANCE_VERIFIED": res["EVIDENCE_PROVENANCE_VERIFIED"],
        "UNVERIFIED_INPUT_TERMS": res["UNVERIFIED_INPUT_TERMS"],
        "NUMERIC_PRESENCE_IS_NOT_EVIDENCE": NUMERIC_PRESENCE_IS_NOT_EVIDENCE,
        "DISTRIBUTION_GRADES": res["DISTRIBUTION_GRADES"],
        "DISTRIBUTION_DOMAINS_DECISION_GRADE":
            res["DISTRIBUTION_DOMAINS_DECISION_GRADE"],
        "SHADOW_APPROXIMATION_ONLY_TERMS":
            res["SHADOW_APPROXIMATION_ONLY_TERMS"],
        "DEPENDENCE_MODEL_STATUS": DEPENDENCE_MODEL_STATUS,
        "DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS":
            DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS,
        "DEPENDENCE_FUTURE_OPTIONS": DEPENDENCE_FUTURE_OPTIONS,
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
            # A supplied number with no evidence reference is its own census
            # line. Folding it into ESTIMATED would be the very claim the
            # provenance check exists to refuse.
            "UNVERIFIED": sum(1 for v in evidence.values()
                              if v == UNVERIFIED_INPUT),
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
        return _attach_decision_grade(base, terms)

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
        # Into the common unit (USD per share) before anything is added.
        return x * scales.get(term, 1.0)

    fs_scale = scales.get(FILL_SELECTION_TERM, 1.0)
    fs_points = {p: v * fs_scale for p, v in fs_points.items()}

    for _ in range(draws):
        pf = draw("P_FILL")
        vf = draw("VALUE_IF_FILL")
        vn = draw("VALUE_IF_NO_FILL")
        tx = draw("TOXICITY")
        fe = draw("FEE")
        rb = draw("REBATE")
        iv = draw("INVENTORY_COST")
        ex = draw("EXIT_COST")
        fs = (fs_dist.sample(rng) * fs_scale) if fs_dist is not None else 0.0
        nets.append(_net(pf, vf, vn, tx, fe, rb, iv, ex, convention, fs, cond))
        for p, point in fs_points.items():
            fs_nets[p].append(
                _net(pf, vf, vn, tx, fe, rb, iv, ex, convention, point, cond))
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

    # EV_MEAN is per posted share. The total needs the quantity, explicitly.
    if uc["QUANTITY_DECLARED"]:
        base["EV_TOTAL_USD"] = round(mean * float(uc["QUANTITY"]), 10)
        base["EV_TOTAL_USD_BASIS"] = "EV_MEAN_PER_POSTED_SHARE_TIMES_QUANTITY"
    else:
        base["EV_TOTAL_USD"] = NOT_IDENTIFIED
        base["WHY_NO_TOTAL"] = QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS

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
        base["ACTION_EV_STATUS"] = "NOT_FULLY_IDENTIFIED"
        _move_to_partial(base)
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
        return _attach_decision_grade(base, terms)

    base["RECOMMENDED"] = False
    base["WHY_NOT_RECOMMENDED"] = (
        "SHADOW_ONLY. No production acceptance threshold has been chosen, and "
        "no order path exists")
    return _attach_decision_grade(base, terms)


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

    uc = unit_contract(terms)
    if not uc["UNIT_CONTRACT_VALID"]:
        return {"STATUS": "INVALID_UNIT_CONTRACT",
                "UNIT_CONTRACT_VIOLATIONS": uc["VIOLATIONS"],
                "UNITS_ARE_NOT_A_CONVENTION": UNITS_ARE_NOT_A_CONVENTION}
    scales, cond = uc["SCALES"], uc["CONDITIONING"]

    means = {}
    for k in EV_TERMS:
        dd = res["DISTS"].get(k)
        means[k] = 0.0 if dd is None else dd.mean() * scales.get(k, 1.0)
    means[FILL_SELECTION_TERM] = (
        fsel["DIST"].mean() * scales.get(FILL_SELECTION_TERM, 1.0)
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
        # x arrives in the term's OWN declared unit and is converted here, so
        # the threshold reported is expressed in the unit the caller uses.
        m[unknown_term] = x * scales.get(unknown_term, 1.0)
        return _net(min(max(m["P_FILL"], 0.0), 1.0), m["VALUE_IF_FILL"],
                    m["VALUE_IF_NO_FILL"], m["TOXICITY"], m["FEE"],
                    m["REBATE"], m["INVENTORY_COST"], m["EXIT_COST"],
                    convention, m[FILL_SELECTION_TERM], cond)

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

A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS = (
    "sensitivity and variance attribution read terms straight out of the "
    "dict, so an absent fee, rebate, inventory cost or exit cost entered the "
    "baseline at 0.0 and the tornado was computed around an EV the EV engine "
    "itself refuses to publish. The ranking may still be useful, and it is "
    "labelled PARTIAL_NOT_FULLY_IDENTIFIED so nobody mistakes it for a "
    "ranking around an identified EV")


def sensitivity(action, terms, convention="SEPARATE_TERM", seed=DEFAULT_SEED):
    """P25 -> P75 swing in EV attributable to each term, ranked."""
    fsel = _fill_selection_setup(terms)
    if fsel.get("REFUSED"):
        return {"STATUS": "UNKNOWN_FILL_SELECTION_CONVENTION",
                "DECLARED": FILL_SELECTION_CONVENTIONS}
    res = resolve_terms(terms, convention)
    if res["CONFLICTS"] or res["INVALID_DOMAINS"]:
        return {"STATUS": "INVALID_MODEL_SPECIFICATION",
                "TERM_STATE_CONFLICTS": res["CONFLICTS"],
                "INVALID_DOMAINS": res["INVALID_DOMAINS"]}
    uc = unit_contract(terms)
    if not uc["UNIT_CONTRACT_VALID"]:
        return {"STATUS": "INVALID_UNIT_CONTRACT",
                "UNIT_CONTRACT_VIOLATIONS": uc["VIOLATIONS"]}
    scales, cond = uc["SCALES"], uc["CONDITIONING"]
    dists = {k: res["DISTS"].get(k) for k in EV_TERMS}
    dists[FILL_SELECTION_TERM] = fsel["DIST"] if fsel["APPLIES"] else None
    if any(dists.get(k) is None for k in CRITICAL_TERMS):
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "MISSING": [k for k in CRITICAL_TERMS
                            if dists.get(k) is None]}
    unresolved = tuple(res["UNRESOLVED_ECONOMIC_TERMS"])
    means = {k: (0.0 if d is None else d.mean() * scales.get(k, 1.0))
             for k, d in dists.items()}

    def ev_with(term, value):
        m = dict(means)
        m[term] = value * scales.get(term, 1.0)
        return _net(min(max(m["P_FILL"], 0.0), 1.0), m["VALUE_IF_FILL"],
                    m["VALUE_IF_NO_FILL"], m["TOXICITY"], m["FEE"],
                    m["REBATE"], m["INVENTORY_COST"], m["EXIT_COST"],
                    convention, m[FILL_SELECTION_TERM], cond)

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
        "STATUS": ("PARTIAL_NOT_FULLY_IDENTIFIED" if unresolved
                   else "COMPUTED"),
        "UNRESOLVED_ECONOMIC_TERMS": unresolved,
        "BASELINE_ASSUMPTION": (PARTIAL_EV_ASSUMPTION if unresolved
                                else "ALL_ECONOMIC_TERMS_RESOLVED"),
        "A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS":
            A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS,
        "UNVERIFIED_INPUT_TERMS": res["UNVERIFIED_INPUT_TERMS"],
        "DEPENDENCE_MODEL_STATUS": DEPENDENCE_MODEL_STATUS,
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
    res = resolve_terms(terms, convention)
    if res["CONFLICTS"] or res["INVALID_DOMAINS"]:
        return {"STATUS": "INVALID_MODEL_SPECIFICATION",
                "TERM_STATE_CONFLICTS": res["CONFLICTS"],
                "INVALID_DOMAINS": res["INVALID_DOMAINS"]}
    dists = {k: res["DISTS"].get(k) for k in EV_TERMS}
    dists[FILL_SELECTION_TERM] = fsel["DIST"] if fsel["APPLIES"] else None
    if any(dists.get(k) is None for k in CRITICAL_TERMS):
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "MISSING": [k for k in CRITICAL_TERMS
                            if dists.get(k) is None],
                "WHY": ("with a critical term unidentified, the question is "
                        "not how much variance it explains but what it would "
                        "have to be. Use break_even()")}
    unresolved = tuple(res["UNRESOLVED_ECONOMIC_TERMS"])
    # action_ev_mc() declines on conditions this guard does not cover (an
    # unknown adverse-selection convention, TOXICITY absent under
    # SEPARATE_TERM, an undeclared fill-selection convention on a fill-bearing
    # action, an invalid unit contract). Reading EV_SD off such a row raised
    # KeyError instead of returning a STATUS -- a crash is not a refusal.
    def _sd_of(row):
        """EV_SD, or its PARTIAL_* twin when the row has been downgraded."""
        for key in ("EV_SD", "PARTIAL_EV_SD"):
            v = row.get(key)
            if isinstance(v, (int, float)):
                return v
        return None

    probe = action_ev_mc(action, terms, convention, draws=2, seed=seed)
    if _sd_of(probe) is None:
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
        sd = _sd_of(r)
        return None if sd is None else sd ** 2

    base_var = var_with_frozen(None)
    if base_var is None:
        return {"STATUS": "NOT_FULLY_IDENTIFIED",
                "WHY": "no EV variance is computable under these terms"}
    rows = []
    for k, d in dists.items():
        if d is None or d.family == "POINT":
            continue
        v = var_with_frozen(k)
        if v is None:
            continue
        removed = max(base_var - v, 0.0)
        rows.append({
            "TERM": k,
            "VARIANCE_REMOVED_IF_RESOLVED": round(removed, 12),
            "SHARE_OF_EV_VARIANCE": (round(removed / base_var, 6)
                                     if base_var > 0 else NOT_IDENTIFIED),
        })
    rows.sort(key=lambda r: -r["VARIANCE_REMOVED_IF_RESOLVED"])
    return {
        "STATUS": ("PARTIAL_NOT_FULLY_IDENTIFIED" if unresolved
                   else "COMPUTED"),
        "UNRESOLVED_ECONOMIC_TERMS": unresolved,
        "BASELINE_ASSUMPTION": (PARTIAL_EV_ASSUMPTION if unresolved
                                else "ALL_ECONOMIC_TERMS_RESOLVED"),
        "A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS":
            A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS,
        "UNVERIFIED_INPUT_TERMS": res["UNVERIFIED_INPUT_TERMS"],
        "DEPENDENCE_MODEL_STATUS": DEPENDENCE_MODEL_STATUS,
        "DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS":
            DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS,
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


ROBUSTNESS_IS_A_DECISION_CLAIM = (
    "'EV stays positive at its own P10' is a statement about acting on the "
    "number. It used to be computed from any row carrying an EV_P10, "
    "including one whose terms were unverified inputs, whose distributions "
    "were shadow approximations and whose independence assumption nothing has "
    "validated. ROBUSTLY_POSITIVE now requires ACTION_EV_STATUS = IDENTIFIED "
    "and DECISION_GRADE_ACTION_EV_STATUS = PASS. The quantile reading is "
    "still published, as a diagnostic, under its own name")


def robustly_positive(ev_row, conservative_quantile=0.10, terms=None):
    """Is EV positive even at the conservative end of its own distribution?

    Answered only for a decision-grade row. Otherwise the quantile is
    reported as SHADOW_ROBUSTNESS_DIAGNOSTIC and ROBUSTLY_POSITIVE is False
    with the blockers named -- the research value is kept, the decision claim
    is not made.
    """
    row = ev_row or {}
    key = "EV_P%02d" % int(round(conservative_quantile * 100))
    v = row.get(key)
    partial = row.get("PARTIAL_%s" % key)
    gate = row.get("DECISION_GRADE_ACTION_EV_STATUS")
    blockers = row.get("DECISION_GRADE_BLOCKERS")
    if gate is None:
        g = decision_grade_action_ev(row, terms)
        gate, blockers = (g["DECISION_GRADE_ACTION_EV_STATUS"],
                          g["DECISION_GRADE_BLOCKERS"])
    out = {
        "CONSERVATIVE_QUANTILE": conservative_quantile,
        "DECISION_GRADE_ACTION_EV_STATUS": gate,
        "DECISION_GRADE_BLOCKERS": blockers,
        "ROBUSTNESS_THRESHOLD_NOT_CHOSEN": ROBUSTNESS_THRESHOLD_NOT_CHOSEN,
        "ROBUSTNESS_IS_A_DECISION_CLAIM": ROBUSTNESS_IS_A_DECISION_CLAIM,
        "SHADOW_RESEARCH_REMAINS_ALLOWED": SHADOW_RESEARCH_REMAINS_ALLOWED,
    }
    if v == NOT_IDENTIFIED or v is None:
        out.update({"ROBUSTLY_POSITIVE": False, "REASON": "EV_NOT_IDENTIFIED",
                    "SHADOW_ROBUSTNESS_DIAGNOSTIC": (
                        partial if isinstance(partial, (int, float))
                        else NOT_IDENTIFIED)})
        return out
    if row.get("ACTION_EV_STATUS") != "IDENTIFIED":
        out.update({"ROBUSTLY_POSITIVE": False,
                    "REASON": "ACTION_EV_NOT_IDENTIFIED",
                    "ACTION_EV_STATUS": row.get("ACTION_EV_STATUS",
                                                NOT_IDENTIFIED),
                    "SHADOW_ROBUSTNESS_DIAGNOSTIC": v})
        return out
    if gate != "PASS":
        out.update({"ROBUSTLY_POSITIVE": False,
                    "REASON": "DECISION_GRADE_BLOCKED",
                    "SHADOW_ROBUSTNESS_DIAGNOSTIC": v,
                    "SHADOW_ROBUSTNESS_DIAGNOSTIC_SIGN": (
                        "POSITIVE" if v > 0 else "NOT_POSITIVE")})
        return out
    out.update({"ROBUSTLY_POSITIVE": v > 0,
                "EV_AT_QUANTILE": v,
                "EV_MEAN": row.get("EV_MEAN"),
                "P_EV_GT_0": row.get("P_EV_GT_0")})
    return out


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
        "EV_UNIT": ev_row.get("EV_UNIT", NOT_IDENTIFIED),
        "EV_TOTAL_USD": ev_row.get("EV_TOTAL_USD", NOT_IDENTIFIED),
        "QUANTITY_STATUS": ev_row.get("QUANTITY_STATUS", NOT_IDENTIFIED),
        "PARTIAL_EV_MEAN": ev_row.get("PARTIAL_EV_MEAN", NOT_IDENTIFIED),
        "PARTIAL_EV_ASSUMPTION": ev_row.get("PARTIAL_EV_ASSUMPTION",
                                            NOT_APPLICABLE_PARTIAL),
        "ACTION_EV_STATUS": ev_row.get("ACTION_EV_STATUS", NOT_IDENTIFIED),
        "DECISION_GRADE_ACTION_EV_STATUS": ev_row.get(
            "DECISION_GRADE_ACTION_EV_STATUS", NOT_IDENTIFIED),
        "DECISION_GRADE_BLOCKERS": ev_row.get("DECISION_GRADE_BLOCKERS",
                                              NOT_IDENTIFIED),
        "DEPENDENCE_MODEL_STATUS": ev_row.get("DEPENDENCE_MODEL_STATUS",
                                              DEPENDENCE_MODEL_STATUS),
        "EVIDENCE_PROVENANCE_VERIFIED": ev_row.get(
            "EVIDENCE_PROVENANCE_VERIFIED", NOT_IDENTIFIED),
        "UNVERIFIED_INPUT_TERMS": ev_row.get("UNVERIFIED_INPUT_TERMS",
                                             NOT_IDENTIFIED),
        "SHADOW_RESEARCH_REMAINS_ALLOWED": SHADOW_RESEARCH_REMAINS_ALLOWED,
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
        "ROBUSTNESS_IS_A_DECISION_CLAIM": ROBUSTNESS_IS_A_DECISION_CLAIM,
        "TERM_STATES": TERM_STATES,
        "RESOLVED_STATES": RESOLVED_STATES,
        "DECISION_GRADE_STATES": DECISION_GRADE_STATES,
        "UNVERIFIED_INPUT": UNVERIFIED_INPUT,
        "NUMERIC_PRESENCE_IS_NOT_EVIDENCE": NUMERIC_PRESENCE_IS_NOT_EVIDENCE,
        "EVIDENCE_REFERENCE_FIELDS": EVIDENCE_REFERENCE_FIELDS,
        "UNITS": UNITS, "BASES": BASES, "CONDITIONING": CONDITIONING,
        "FILL_CONDITIONED": FILL_CONDITIONED,
        "DEFAULT_TERM_CONTRACT": DEFAULT_TERM_CONTRACT,
        "ADMISSIBLE_UNITS": ADMISSIBLE_UNITS,
        "EV_UNIT": EV_UNIT,
        "CONTRACT_SETTLEMENT_USD": CONTRACT_SETTLEMENT_USD,
        "UNITS_ARE_NOT_A_CONVENTION": UNITS_ARE_NOT_A_CONVENTION,
        "CONDITIONING_IS_STRUCTURAL_NOT_POSITIONAL":
            CONDITIONING_IS_STRUCTURAL_NOT_POSITIONAL,
        "QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS":
            QUANTITY_IS_NOT_OPTIONAL_FOR_TOTALS,
        "FILL_BEARING_ACTIONS": FILL_BEARING_ACTIONS,
        "AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION":
            AN_OMITTED_CONVENTION_IS_NOT_EXCLUSION,
        "DECISION_GRADE_FAMILIES": DECISION_GRADE_FAMILIES,
        "SHADOW_APPROXIMATION_ONLY": SHADOW_APPROXIMATION_ONLY,
        "AN_UNBOUNDED_FAMILY_IS_NOT_A_BOUNDED_QUANTITY":
            AN_UNBOUNDED_FAMILY_IS_NOT_A_BOUNDED_QUANTITY,
        "DECISION_GRADE_CONDITIONS": DECISION_GRADE_CONDITIONS,
        "DEPENDENCE_MODEL_STATUS": DEPENDENCE_MODEL_STATUS,
        "DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS":
            DEPENDENCE_IS_A_BLOCKER_NOT_A_GUESS,
        "DEPENDENCE_FUTURE_OPTIONS": DEPENDENCE_FUTURE_OPTIONS,
        "SHADOW_RESEARCH_REMAINS_ALLOWED": SHADOW_RESEARCH_REMAINS_ALLOWED,
        "PARTIAL_EV_ASSUMPTION": PARTIAL_EV_ASSUMPTION,
        "A_DOWNGRADED_MEAN_DOES_NOT_DOWNGRADE_THE_ROW":
            A_DOWNGRADED_MEAN_DOES_NOT_DOWNGRADE_THE_ROW,
        "A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS":
            A_TORNADO_IS_NOT_INNOCENT_OF_THE_ZEROS,
        "NO_GUARANTEED_PROFIT_LANGUAGE": NO_GUARANTEED_PROFIT_LANGUAGE,
        "FORBIDDEN_CLAIMS": FORBIDDEN_CLAIMS,
        "SHADOW_ONLY": SHADOW_ONLY,
        "NO_ORDER_IS_PLACED": NO_ORDER_IS_PLACED,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
