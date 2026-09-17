"""Evidence classes, the prior registry, Bayesian update, shrinkage.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. No parameter is fitted to prospective capture data.

THE RULE THAT CHANGED
---------------------
UNKNOWN DOES NOT AUTOMATICALLY MEAN ZERO OR STOP.

Where a defensible prior exists, the unknown is a DISTRIBUTION with provenance
and uncertainty. Where none exists, it stays NOT_IDENTIFIED. What is forbidden
is the middle: silently substituting 0, 0.5, a historical average or a
convenient constant for something nobody has estimated.

THE THREE CLASSES
-----------------
    A  MEASURED_BETTOR_NATIVE  -- measured from BETTOR's own prospective data
    B  ESTIMATED_PRIOR         -- venue mechanics, public research, comparable
                                  markets, with explicit transfer assumptions
    C  NOT_IDENTIFIED          -- no defensible estimate exists

PRIOR STRENGTH AFFECTS UNCERTAINTY, NEVER THE MEAN
--------------------------------------------------
A WEAK prior is not a different expectation from a STRONG one; it is the same
expectation held less tightly. Letting strength move the mean would let the
act of admitting ignorance change the answer.
"""

import hashlib
import json
import math
import random

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True
NO_PARAMETER_FITTED_TO_CAPTURE_DATA = True

# --- Evidence classes. -----------------------------------------------------

MEASURED_BETTOR_NATIVE = "MEASURED_BETTOR_NATIVE"
ESTIMATED_PRIOR = "ESTIMATED_PRIOR"
EVIDENCE_CLASSES = (MEASURED_BETTOR_NATIVE, ESTIMATED_PRIOR, NOT_IDENTIFIED)

NEVER_SILENTLY_SUBSTITUTE = (
    "for a NOT_IDENTIFIED quantity, never silently substitute 0, 0.5, a "
    "historical average or an arbitrary constant. Those produce a confident "
    "number built on nothing, and the number then propagates into every "
    "downstream EV")

PRIOR_STRENGTHS = ("WEAK", "MODERATE", "STRONG")
STRENGTH_MEANING = {
    "WEAK": "generic or comparable-market research; large transfer risk",
    "MODERATE": "same venue historical, or a closely matched mechanism",
    "STRONG": "BETTOR-native prospective evidence",
}
STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN = (
    "a WEAK prior is the same expectation held less tightly, not a different "
    "expectation. If strength moved the mean, admitting ignorance would "
    "change the answer")

# Pseudo-observation weight by strength. Used for shrinkage and for how fast a
# prior yields to data -- NOT to shift its centre.
STRENGTH_PSEUDO_N = {"WEAK": 2.0, "MODERATE": 10.0, "STRONG": 50.0}

SHRINKAGE_CALIBRATION_STATUS = "UNCALIBRATED_CONVENTION"

SHRINKAGE_IS_A_CONVENTION_NOT_A_MEASUREMENT = (
    "2 / 10 / 50 pseudo-observations, and the shrinkage k, are modelling "
    "conventions chosen to make the machinery testable. No venue measurement "
    "produced them. They may shape a diagnostic and may not create "
    "decision-grade posterior precision")

NO_DEFAULT_SHRINKAGE_K = (
    "k had a production-looking default of 20. A caller who does not choose "
    "a shrinkage strength has not made a modelling decision, and the "
    "function must not make it for them")


# --- Distributions. Small, exact, seedable. --------------------------------

FAMILIES = ("BETA", "NORMAL", "LOGNORMAL", "TRIANGULAR", "POINT",
            "TRUNCATED_NORMAL", "LOGIT_NORMAL")

# Families whose SUPPORT is mathematically bounded to [0, 1]. An unbounded
# family is not a decision-grade probability model merely because its central
# envelope happens to land inside the interval.
BOUNDED_UNIT_FAMILIES = ("BETA", "TRIANGULAR", "POINT", "TRUNCATED_NORMAL",
                         "LOGIT_NORMAL")
BOUNDED_POSITIVE_FAMILIES = ("LOGNORMAL", "TRIANGULAR", "POINT",
                             "TRUNCATED_NORMAL")

PARAMETER_VALIDATION_IS_FAIL_CLOSED = (
    "the family name was validated and its parameters were not, so "
    "NORMAL(sigma=-1), BETA(alpha=0) and NaN everywhere were accepted and "
    "produced numbers. A distribution with impossible parameters is an "
    "INVALID_MODEL_SPECIFICATION, not a source of draws")

TRUNCATION_IS_PART_OF_THE_MODEL = (
    "clipping Monte Carlo draws and calling the result the original "
    "distribution changes the distribution without changing its name. If "
    "truncation is intended, TRUNCATED_NORMAL is the declared family and its "
    "normalisation is part of the model")


class InvalidModelSpecification(ValueError):
    """Raised when a distribution cannot exist as specified."""


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(float(x))


def validate_params(family, params):
    """Fail-closed parameter validation. Returns a list of problems."""
    p, bad = dict(params or {}), []

    def need(k):
        if k not in p:
            bad.append("MISSING_%s" % k)
            return None
        if not _finite(p[k]):
            bad.append("NON_FINITE_%s" % k)
            return None
        return float(p[k])

    if family == "BETA":
        a, b = need("alpha"), need("beta")
        if a is not None and a <= 0:
            bad.append("ALPHA_NOT_POSITIVE")
        if b is not None and b <= 0:
            bad.append("BETA_NOT_POSITIVE")
    elif family in ("NORMAL", "LOGNORMAL", "LOGIT_NORMAL"):
        need("mu")
        sd = need("sigma")
        if sd is not None and sd <= 0:
            bad.append("SIGMA_NOT_POSITIVE")
    elif family == "TRUNCATED_NORMAL":
        need("mu")
        sd = need("sigma")
        if sd is not None and sd <= 0:
            bad.append("SIGMA_NOT_POSITIVE")
        lo, hi = need("low"), need("high")
        if lo is not None and hi is not None and not lo < hi:
            bad.append("LOW_NOT_BELOW_HIGH")
    elif family == "TRIANGULAR":
        lo, mode, hi = need("low"), need("mode"), need("high")
        if None not in (lo, mode, hi) and not lo <= mode <= hi:
            bad.append("NOT_LOW_LE_MODE_LE_HIGH")
    elif family == "POINT":
        need("value")
    return bad


QUANTILE_LEVELS = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


def _norm_ppf(p):
    """Inverse standard normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


class Dist:
    """A parametric distribution with quantiles and seeded sampling."""

    def __init__(self, family, params):
        if family not in FAMILIES:
            raise InvalidModelSpecification(
                "unknown family %r; declared %r" % (family, FAMILIES))
        bad = validate_params(family, params)
        if bad:
            raise InvalidModelSpecification(
                "INVALID_MODEL_SPECIFICATION %s %r: %s"
                % (family, dict(params or {}), ", ".join(bad)))
        self.family = family
        self.params = dict(params)

    # -- moments ------------------------------------------------------------
    def mean(self):
        p, f = self.params, self.family
        if f == "BETA":
            return p["alpha"] / (p["alpha"] + p["beta"])
        if f == "NORMAL":
            return p["mu"]
        if f == "LOGNORMAL":
            return math.exp(p["mu"] + p["sigma"] ** 2 / 2.0)
        if f == "TRIANGULAR":
            return (p["low"] + p["mode"] + p["high"]) / 3.0
        if f in ("TRUNCATED_NORMAL", "LOGIT_NORMAL"):
            # No closed form worth the risk of a sign error: the exact
            # quantile function exists, so integrate it deterministically.
            n = 2000
            return sum(self.quantile((i + 0.5) / n) for i in range(n)) / n
        return p["value"]

    def sample(self, rng):
        p, f = self.params, self.family
        if f == "BETA":
            return rng.betavariate(p["alpha"], p["beta"])
        if f == "NORMAL":
            return rng.gauss(p["mu"], p["sigma"])
        if f == "LOGNORMAL":
            return math.exp(rng.gauss(p["mu"], p["sigma"]))
        if f == "TRIANGULAR":
            return rng.triangular(p["low"], p["high"], p["mode"])
        if f == "TRUNCATED_NORMAL":
            # Inverse-CDF on the NORMALISED truncated law. Exact, and never
            # a clipped draw from the untruncated parent.
            return self.quantile(rng.random())
        if f == "LOGIT_NORMAL":
            z = rng.gauss(p["mu"], p["sigma"])
            return 1.0 / (1.0 + math.exp(-z))
        return p["value"]

    def quantile(self, q, rng_seed=20260917, draws=20000):
        """Analytic where cheap, empirical otherwise. Deterministic either way."""
        p, f = self.params, self.family
        if f == "POINT":
            return p["value"]
        if f == "NORMAL":
            return p["mu"] + p["sigma"] * _norm_ppf(q)
        if f == "LOGNORMAL":
            return math.exp(p["mu"] + p["sigma"] * _norm_ppf(q))
        if f == "LOGIT_NORMAL":
            z = p["mu"] + p["sigma"] * _norm_ppf(min(max(q, 1e-12),
                                                     1 - 1e-12))
            return 1.0 / (1.0 + math.exp(-z))
        if f == "TRUNCATED_NORMAL":
            mu, sd = float(p["mu"]), float(p["sigma"])
            lo, hi = float(p["low"]), float(p["high"])

            def _cdf(x):
                return 0.5 * (1.0 + math.erf((x - mu) / (sd * math.sqrt(2.0))))
            a, b = _cdf(lo), _cdf(hi)
            if b <= a:
                return lo
            qq = min(max(q, 1e-12), 1 - 1e-12)
            return mu + sd * _norm_ppf(a + qq * (b - a))
        rng = random.Random(rng_seed)
        xs = sorted(self.sample(rng) for _ in range(draws))
        i = min(int(q * (len(xs) - 1)), len(xs) - 1)
        return xs[i]

    def summary(self):
        out = {"DISTRIBUTION_FAMILY": self.family,
               "PARAMETERS": dict(self.params),
               "MEAN": round(self.mean(), 10)}
        for q in QUANTILE_LEVELS:
            key = "MEDIAN" if q == 0.50 else "P%02d" % int(round(q * 100))
            out[key] = round(self.quantile(q), 10)
        return out


def beta_from_mean_n(mean, pseudo_n):
    """Beta with a given centre and a given strength. Centre is preserved.

    An out-of-range mean or a non-positive pseudo-N used to be clamped into
    a legal one, which silently turned an impossible request into a
    confident distribution. Both are now refused.
    """
    if not _finite(mean) or not 0.0 < float(mean) < 1.0:
        raise InvalidModelSpecification(
            "INVALID_MODEL_SPECIFICATION beta_from_mean_n: mean %r is not "
            "strictly inside (0, 1)" % (mean,))
    if not _finite(pseudo_n) or float(pseudo_n) <= 0.0:
        raise InvalidModelSpecification(
            "INVALID_MODEL_SPECIFICATION beta_from_mean_n: pseudo_n %r is "
            "not positive" % (pseudo_n,))
    mean, n = float(mean), float(pseudo_n)
    return Dist("BETA", {"alpha": mean * n, "beta": (1 - mean) * n})


# --- A prior, with its provenance. -----------------------------------------

PROVENANCE_FIELDS = (
    "PARAMETER_NAME", "DISTRIBUTION_FAMILY", "PARAMETERS",
    "MEAN", "MEDIAN", "P05", "P10", "P25", "P75", "P90", "P95",
    "EVIDENCE_CLASS", "PRIOR_STRENGTH", "SOURCE_TYPE", "SOURCE_REFERENCES",
    "SOURCE_POPULATION", "TARGET_POPULATION", "TRANSFER_ASSUMPTIONS",
    "TRANSFER_RISK", "LAST_UPDATED_AT", "OBSERVATION_COUNT",
    "INDEPENDENT_EVENT_COUNT", "MODEL_VERSION", "UNCERTAINTY_STATUS",
)

NO_PRIOR_WITHOUT_A_REASON = (
    "a prior with no documented source, population and transfer assumption is "
    "an opinion with a number attached. The registry refuses it")


def make_prior(parameter_name, dist, evidence_class, prior_strength,
               source_type=None, source_references=(), source_population=None,
               target_population=None, transfer_assumptions=None,
               transfer_risk=None, last_updated_at=None,
               observation_count=0, independent_event_count=0,
               model_version="1"):
    """Build one registry entry. Refuses an undocumented prior."""
    if evidence_class not in EVIDENCE_CLASSES:
        return {"PARAMETER_NAME": parameter_name, "PRIOR_AVAILABLE": False,
                "REASON": "UNKNOWN_EVIDENCE_CLASS",
                "DECLARED": EVIDENCE_CLASSES}
    if evidence_class == NOT_IDENTIFIED:
        return {"PARAMETER_NAME": parameter_name, "PRIOR_AVAILABLE": False,
                "EVIDENCE_CLASS": NOT_IDENTIFIED,
                "NEVER_SILENTLY_SUBSTITUTE": NEVER_SILENTLY_SUBSTITUTE}
    if prior_strength not in PRIOR_STRENGTHS:
        return {"PARAMETER_NAME": parameter_name, "PRIOR_AVAILABLE": False,
                "REASON": "UNKNOWN_PRIOR_STRENGTH",
                "DECLARED": PRIOR_STRENGTHS}
    if not source_type or not source_population or not transfer_assumptions:
        return {"PARAMETER_NAME": parameter_name, "PRIOR_AVAILABLE": False,
                "REASON": "UNDOCUMENTED_PRIOR",
                "MISSING": [k for k, v in
                            (("SOURCE_TYPE", source_type),
                             ("SOURCE_POPULATION", source_population),
                             ("TRANSFER_ASSUMPTIONS", transfer_assumptions))
                            if not v],
                "NO_PRIOR_WITHOUT_A_REASON": NO_PRIOR_WITHOUT_A_REASON}
    row = {"PARAMETER_NAME": parameter_name, "PRIOR_AVAILABLE": True}
    row.update(dist.summary())
    row.update({
        "EVIDENCE_CLASS": evidence_class,
        "PRIOR_STRENGTH": prior_strength,
        "STRENGTH_MEANING": STRENGTH_MEANING[prior_strength],
        "SOURCE_TYPE": source_type,
        "SOURCE_REFERENCES": tuple(source_references or ()),
        "SOURCE_POPULATION": source_population,
        "TARGET_POPULATION": target_population or NOT_IDENTIFIED,
        "TRANSFER_ASSUMPTIONS": transfer_assumptions,
        "TRANSFER_RISK": transfer_risk or NOT_IDENTIFIED,
        "LAST_UPDATED_AT": last_updated_at or NOT_IDENTIFIED,
        "OBSERVATION_COUNT": observation_count,
        "INDEPENDENT_EVENT_COUNT": independent_event_count,
        "MODEL_VERSION": model_version,
        "UNCERTAINTY_STATUS": ("WIDE_BY_CONSTRUCTION"
                               if prior_strength == "WEAK" else "MODELLED"),
        "STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN":
            STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN,
    })
    # PROVISIONAL. The registry attaches further semantic fields after this
    # point, so the immutable hash is taken by seal_prior() once the object
    # is complete. A hash over a half-built object protects the half nobody
    # reads downstream.
    row["PRIOR_SHA"] = NOT_IDENTIFIED
    row["PRIOR_SHA_STATUS"] = "PROVISIONAL_NOT_YET_SEALED"
    return row


THE_HASH_MUST_COVER_THE_OBJECT_CONSUMED = (
    "make_prior() used to seal PRIOR_SHA and the registry then merged the "
    "classification block and four more semantic fields on top. A consumer "
    "recomputing the digest over the row it actually received got a "
    "different answer, so the hash protected a version of the prior that "
    "nothing downstream ever saw")


def prior_sha(row):
    """The digest over every field except the digest itself."""
    return hashlib.sha256(json.dumps(
        {k: v for k, v in (row or {}).items()
         if k not in ("PRIOR_SHA", "PRIOR_SHA_STATUS")},
        sort_keys=True, default=str).encode()).hexdigest()


def seal_prior(row):
    """Seal the FINAL object. Called once, after every field is attached."""
    row["PRIOR_SHA_STATUS"] = "SEALED_OVER_FINAL_OBJECT"
    row["PRIOR_SHA"] = prior_sha(row)
    row["THE_HASH_MUST_COVER_THE_OBJECT_CONSUMED"] = \
        THE_HASH_MUST_COVER_THE_OBJECT_CONSUMED
    # Re-seal now that the explanatory constant is present, so a consumer
    # recomputing over the shipped row reproduces the digest exactly.
    row["PRIOR_SHA"] = NOT_IDENTIFIED
    row["PRIOR_SHA"] = prior_sha(row)
    return row


def verify_prior_sha(row):
    """Does the shipped row recompute to its own digest?"""
    stored = (row or {}).get("PRIOR_SHA")
    probe = dict(row or {})
    probe["PRIOR_SHA"] = NOT_IDENTIFIED
    recomputed = prior_sha(probe)
    return {"PRIOR_SHA": stored, "RECOMPUTED": recomputed,
            "MATCHES": stored == recomputed,
            "PRIOR_SHA_STATUS": (row or {}).get("PRIOR_SHA_STATUS",
                                                NOT_IDENTIFIED)}


# --- The registry. ---------------------------------------------------------

DECLARED_PARAMETERS = (
    "P_FILL", "TIME_TO_FILL", "PARTIAL_FILL_FRACTION", "QUEUE_AHEAD",
    "QUEUE_DEPLETION_RATE", "CANCEL_LATENCY", "ACK_LATENCY",
    "PRICE_MOVE_5S", "PRICE_MOVE_30S", "PRICE_MOVE_60S", "PRICE_MOVE_300S",
    "MARKET_STATE_TOXICITY", "FILL_SELECTION_EFFECT",
    "RELATIVE_VALUE_CONVERGENCE", "EXTERNAL_LEAD_LAG",
    "PAIR_COMPLETION_TIME", "INVENTORY_HOLD_TIME", "EXIT_COST",
    "CAPITAL_OCCUPANCY", "MAKER_INCENTIVE_VALUE", "REBATE_VALUE",
    "SLIPPAGE", "VOLATILITY", "EDGE_DECAY",
)

A_NAME_IS_NOT_A_PRIOR = (
    "do not create a prior merely because this list names the object. Each "
    "entry answers PRIOR_AVAILABLE = YES or NO on its own evidence")


# --- CORRECTION. Classify the zero-centred fill-selection prior exactly. ----
#
# A symmetric wide prior centred at zero is an acceptable NON-DIRECTIONAL
# STARTING BELIEF. It is not, and must never be read as, a finding that the
# effect is zero. The two produce the same point estimate and opposite
# obligations: a finding licenses ignoring the term, a starting belief
# obliges carrying its width through every EV that depends on it.

FILL_SELECTION_PRIOR_SOURCE = "STRUCTURAL_NONDIRECTIONAL_PRIOR"

FILL_SELECTION_PRIOR_CLASSIFICATION = {
    "FILL_SELECTION_PRIOR_SOURCE": FILL_SELECTION_PRIOR_SOURCE,
    "EVIDENCE_CLASS": ESTIMATED_PRIOR,
    "PRIOR_STRENGTH": "WEAK",
    "PRIOR_CENTER": "ZERO",
    "DIRECTION_ASSUMED": "NO",
    "BETTOR_NATIVE_OBSERVATIONS": 0,
}

NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO = (
    "PRIOR_CENTER = ZERO is NOT evidence that FILL_SELECTION_EFFECT = 0. It "
    "records that no direction has been established, which is a statement "
    "about our evidence and not about the venue. A measured zero and an "
    "unmeasured zero share a number and share nothing else")

PRIOR_WIDTH_MUST_REMAIN_VISIBLE = (
    "the width is the content of this prior. Any consumer that reads only "
    "the mean sees 0.0 and silently treats the term as absent -- which is the "
    "exact substitution the evidence-class rule exists to prevent. Every "
    "shadow action whose EV materially depends on this prior must expose "
    "EV_AT_FILL_SELECTION_P10 / _P50 / _P90, or the sensitivity and "
    "break-even outputs, so the width is on the page beside the answer")

POSTERIOR_MAY_MOVE_EITHER_WAY = (
    "once BETTOR-native fill data exist, the posterior may move ADVERSE or "
    "FAVOURABLE. Neither direction is a surprise and neither is a failure of "
    "the prior. A prior that could only be revised one way was a directional "
    "assumption wearing a symmetric distribution")

EARLIER_SOURCE_LABEL_SAID = (
    "an earlier build labelled this source PRINCIPLED_LEAST_INFORMATIVE_"
    "CHOICE. That is superseded by STRUCTURAL_NONDIRECTIONAL_PRIOR, which "
    "names what the prior IS rather than how it was chosen")


def _registry():
    """The priors that exist today, each with its reason.

    Most are NO. That is the honest state before prospective data, and the
    break-even engine is what makes the NOs actionable.
    """
    reg = {}

    # FILL_SELECTION_EFFECT -- a structural non-directional prior. Its centre
    # is zero because no direction is established, NOT because zero was
    # measured. See FILL_SELECTION_PRIOR_CLASSIFICATION above.
    reg["FILL_SELECTION_EFFECT"] = make_prior(
        "FILL_SELECTION_EFFECT",
        Dist("NORMAL", {"mu": 0.0, "sigma": 0.004}),
        ESTIMATED_PRIOR, "WEAK",
        source_type=FILL_SELECTION_PRIOR_SOURCE,
        source_references=("no directional evidence on this venue",),
        source_population="none -- this is a symmetry argument, not a dataset",
        target_population="BETTOR passive fills on this venue",
        transfer_assumptions=(
            "centred at ZERO because selection licenses 'may differ', not 'is "
            "worse'. Informed takers plausibly push it adverse; liquidity, "
            "hedging and impatient-benign flow plausibly push it favourable. "
            "With no evidence on which dominates HERE, a centre of zero is "
            "the least-informative defensible choice. The sigma is set wide "
            "enough that a half-spread-scale effect in either direction sits "
            "comfortably inside the interval"),
        transfer_risk="HIGH -- this is a symmetry argument, not a measurement")
    reg["FILL_SELECTION_EFFECT"].update(FILL_SELECTION_PRIOR_CLASSIFICATION)
    reg["FILL_SELECTION_EFFECT"].update({
        "NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO":
            NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO,
        "PRIOR_WIDTH_MUST_REMAIN_VISIBLE": PRIOR_WIDTH_MUST_REMAIN_VISIBLE,
        "POSTERIOR_MAY_MOVE_EITHER_WAY": POSTERIOR_MAY_MOVE_EITHER_WAY,
        "EARLIER_SOURCE_LABEL_SAID": EARLIER_SOURCE_LABEL_SAID,
        "REQUIRED_EV_EXPOSURE": ("EV_AT_FILL_SELECTION_P10",
                                 "EV_AT_FILL_SELECTION_P50",
                                 "EV_AT_FILL_SELECTION_P90"),
    })

    # MARKET_STATE_TOXICITY -- mechanism-bounded, deliberately wide.
    reg["MARKET_STATE_TOXICITY"] = make_prior(
        "MARKET_STATE_TOXICITY",
        Dist("NORMAL", {"mu": 0.0, "sigma": 0.006}),
        ESTIMATED_PRIOR, "WEAK",
        source_type="VENUE_MECHANICS_BOUND",
        source_references=("binary contract bounded in [0,1]",),
        source_population="comparable short-horizon microstructure studies",
        target_population="BETTOR hypothetical quotes on this venue",
        transfer_assumptions=(
            "an adverse move over 5-300 s on a binary contract is bounded by "
            "the contract's own range and empirically concentrated near zero. "
            "Centred at zero because the market-state question is symmetric "
            "across quote sides by construction"),
        transfer_risk="HIGH -- sport event contracts jump on news, and the "
                      "normal tail understates that")

    # ACK_LATENCY / CANCEL_LATENCY -- no measurement, and no defensible
    # transfer: a crypto venue's latency says nothing about this one.
    for p in ("ACK_LATENCY", "CANCEL_LATENCY"):
        reg[p] = make_prior(p, None, NOT_IDENTIFIED, None)

    # P_FILL -- the mechanism exists but its key input does not.
    reg["P_FILL"] = make_prior("P_FILL", None, NOT_IDENTIFIED, None)
    reg["P_FILL"]["WHY_NOT"] = (
        "the queue mechanism is buildable -- P_FILL over horizon h is the "
        "probability that cumulative volume traded at our level exceeds the "
        "quantity ahead of us. But BOTH inputs are unmeasured on this venue: "
        "trade intensity at the touch awaits the capture, and quantity-ahead "
        "is an assumption about resting order arrival. Multiplying two "
        "unmeasured quantities produces a number with no referent. "
        "p_fill_from_mechanism() computes it the moment intensity is "
        "supplied; until then BREAK_EVEN_P_FILL is the actionable object")

    for p in DECLARED_PARAMETERS:
        if p not in reg:
            reg[p] = make_prior(p, None, NOT_IDENTIFIED, None)
            reg[p].setdefault(
                "WHY_NOT", "no measurement on this venue and no defensible "
                           "transfer from a comparable one")
    # Seal LAST, over the complete object every consumer will receive.
    for p in reg:
        seal_prior(reg[p])
    return reg


PRIOR_REGISTRY = _registry()


def prior(name):
    return PRIOR_REGISTRY.get(
        name, {"PARAMETER_NAME": name, "PRIOR_AVAILABLE": False,
               "REASON": "NOT_A_DECLARED_PARAMETER",
               "DECLARED": DECLARED_PARAMETERS})


def registry_census():
    avail = [k for k, v in PRIOR_REGISTRY.items() if v.get("PRIOR_AVAILABLE")]
    return {
        "DECLARED": len(DECLARED_PARAMETERS),
        "PRIOR_AVAILABLE_YES": sorted(avail),
        "PRIOR_AVAILABLE_NO": sorted(set(PRIOR_REGISTRY) - set(avail)),
        "YES_COUNT": len(avail),
        "NO_COUNT": len(PRIOR_REGISTRY) - len(avail),
        "A_NAME_IS_NOT_A_PRIOR": A_NAME_IS_NOT_A_PRIOR,
        "MOST_ARE_NO_AND_THAT_IS_THE_HONEST_STATE": (
            "the break-even engine turns each NO into a research question: "
            "what would this quantity have to be for the action to pay?"),
    }


# --- The P_FILL mechanism, ready for the moment intensity is measured. -----

QUEUE_MECHANISM_STATUS = "UNCALIBRATED_TOY_MECHANISM"

QUEUE_DIMENSIONAL_CONTRACT = {
    "QUEUE_AHEAD": "SHARES_RESTING_AHEAD_OF_US_AT_OUR_LEVEL",
    "TRADE_INTENSITY_PER_SECOND": "POISSON_EVENTS_PER_SECOND_AT_OUR_LEVEL",
    "POISSON_EVENT_UNIT": "ONE_TRADE_OF_ONE_SHARE_AT_OUR_LEVEL",
    "THE_MISMATCH": (
        "the docstring promises P(cumulative VOLUME exceeds the quantity "
        "ahead) but the arithmetic is P(N >= ceil(queue_ahead)) for a "
        "Poisson COUNT. Those agree only if every trade is exactly one "
        "share. Until the trade-size distribution at the touch is measured, "
        "lambda is a count rate and queue_ahead is a share count, and the "
        "comparison is dimensionally unearned"),
    "WHAT_WOULD_FIX_IT": (
        "measure the trade-size distribution at the touch and use a "
        "compound-Poisson threshold, or express both sides in the same unit"),
}

NEVER_CALL_THIS_MEASURED_P_FILL = (
    "the output of an uncalibrated toy mechanism is not a measured P_FILL "
    "and may not be substituted for one anywhere in the EV")


def p_fill_from_mechanism(queue_ahead, trade_intensity_per_s, horizon_s,
                          intensity_evidence_class=NOT_IDENTIFIED):
    """P(cumulative volume at our level exceeds the queue ahead) over h.

    Poisson arrivals of traded quantity at the level. Returns NOT_IDENTIFIED
    unless the intensity carries a real evidence class -- the mechanism does
    not manufacture its own input.
    """
    bad = []
    if not _finite(queue_ahead) or float(queue_ahead) < 0:
        bad.append("QUEUE_AHEAD_NOT_NON_NEGATIVE_FINITE")
    if not _finite(trade_intensity_per_s) or float(trade_intensity_per_s) < 0:
        bad.append("TRADE_INTENSITY_NOT_NON_NEGATIVE_FINITE")
    if not _finite(horizon_s) or float(horizon_s) <= 0:
        bad.append("HORIZON_NOT_POSITIVE_FINITE")
    if intensity_evidence_class not in EVIDENCE_CLASSES:
        bad.append("EVIDENCE_CLASS_NOT_DECLARED")
    if bad and not (queue_ahead is None or trade_intensity_per_s is None):
        return {"P_FILL": NOT_IDENTIFIED, "STATUS": "INVALID_INPUTS",
                "PROBLEMS": tuple(bad),
                "QUEUE_MECHANISM_STATUS": QUEUE_MECHANISM_STATUS,
                "MAY_ENTER_ACTION_EV_AS_P_FILL": False}
    if intensity_evidence_class == NOT_IDENTIFIED or \
            trade_intensity_per_s is None or queue_ahead is None:
        return {"P_FILL": NOT_IDENTIFIED,
                "EVIDENCE_CLASS": NOT_IDENTIFIED,
                "WHY": ("trade intensity at the touch is not measured on this "
                        "venue. The mechanism is ready; its input is not"),
                "BLOCKED_ON": "TOUCH_TRADE_INTENSITY_MEASUREMENT"}
    lam = float(trade_intensity_per_s) * float(horizon_s)
    q = float(queue_ahead)
    if lam <= 0:
        return {"P_FILL": 0.0, "EVIDENCE_CLASS": intensity_evidence_class,
                "QUEUE_MECHANISM_STATUS": QUEUE_MECHANISM_STATUS,
                "WHY": "zero measured intensity over the horizon"}
    # P(N >= q) for Poisson(lam), q treated as a quantity threshold.
    k = int(math.ceil(q))
    cum, term = 0.0, math.exp(-lam)
    for i in range(k):
        cum += term
        term *= lam / (i + 1)
    p = max(0.0, min(1.0, 1.0 - cum))
    return {"P_FILL": round(p, 10),
            "EVIDENCE_CLASS": (ESTIMATED_PRIOR
                               if intensity_evidence_class == ESTIMATED_PRIOR
                               else intensity_evidence_class),
            "QUEUE_MECHANISM_STATUS": QUEUE_MECHANISM_STATUS,
            "MAY_ENTER_ACTION_EV_AS_P_FILL": False,
            "WHY_NOT_USABLE_AS_P_FILL": (
                "the queue units are not reconciled -- lambda is a trade "
                "COUNT rate and queue_ahead is a SHARE count -- so this "
                "probability may not be supplied as P_FILL to the action EV"),
            "QUEUE_DIMENSIONAL_CONTRACT": QUEUE_DIMENSIONAL_CONTRACT,
            "NEVER_CALL_THIS_MEASURED_P_FILL":
                NEVER_CALL_THIS_MEASURED_P_FILL,
            "MECHANISM": "POISSON_COUNT_EXCEEDS_QUEUE_AHEAD_UNCALIBRATED",
            "LAMBDA": round(lam, 6), "QUEUE_AHEAD": q,
            "NEVER_LABEL_THIS_MEASURED_UNTIL_BETTOR_ORDERS_EXIST": True}


# --- Bayesian update. ------------------------------------------------------

UPDATE_IS_VERSIONED = (
    "a posterior never overwrites its prior. Both persist with their own "
    "versions, so every posterior is reproducible from its inputs")


COUNT_VALIDATION_RULE = (
    "successes and trials are COUNTS. int() silently truncated 0.9 successes "
    "out of 1.9 trials into 0 out of 1, turning a fractional quantity nobody "
    "should have passed into a confident posterior. Floats, strings, bools "
    "and negatives are refused rather than coerced")


def _strict_count(v):
    """A non-negative integer count. bool is not an integer count."""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


EFFECTIVE_N_FIELDS = ("RAW_ROWS", "MARKETS", "INDEPENDENT_EVENTS",
                      "EVENT_HOURS", "DEPENDENCE_CLUSTER_IDS",
                      "EFFECTIVE_N", "EFFECTIVE_N_METHOD")

RAW_ROWS_ARE_NOT_INDEPENDENT_OBSERVATIONS = (
    "consecutive observations on one market overlap almost completely. "
    "Feeding a timestamp-row count into a conjugate update as if it were N "
    "independent trials shrinks the posterior by a factor nothing earned. "
    "Until the dependence is modelled, posterior PRECISION is not identified "
    "even though the posterior MEAN may be usable")

POSTERIOR_PRECISION_UNMODELLED = \
    "NOT_IDENTIFIED_PENDING_DEPENDENCE_MODEL"


EFFECTIVE_N_METHODS = ("EVENT_CLUSTERED_DESIGN_EFFECT",
                       "HIERARCHICAL_EVENT_LEVEL_LIKELIHOOD",
                       "BLOCK_BOOTSTRAP_OVER_EVENTS",
                       "MEASURED_ICC_DESIGN_EFFECT")

A_WARNING_IS_NOT_A_GATE = (
    "reporting POSTERIOR_PRECISION_STATUS = NOT_IDENTIFIED while still "
    "returning a posterior narrowed by raw row count leaves the narrowed "
    "distribution sitting there for any caller that does not read the "
    "warning. The narrowed object is now named DIAGNOSTIC_RAW_ROW_POSTERIOR "
    "and DECISION_GRADE_POSTERIOR is absent until effective N is validated")


def validate_effective_n(effective_n_value, effective_n_method,
                         raw_rows=None, relation_to_raw_rows=None):
    """Is this effective N usable for decision-grade precision? Fails closed."""
    bad = []
    if not _finite(effective_n_value) or float(effective_n_value) <= 0:
        bad.append("EFFECTIVE_N_NOT_POSITIVE_FINITE")
    if effective_n_method not in EFFECTIVE_N_METHODS:
        bad.append("EFFECTIVE_N_METHOD_NOT_DECLARED")
    if not relation_to_raw_rows:
        bad.append("RELATION_TO_RAW_ROWS_NOT_EXPLAINED")
    if _finite(effective_n_value) and _finite(raw_rows) \
            and float(effective_n_value) > float(raw_rows):
        bad.append("EFFECTIVE_N_EXCEEDS_RAW_ROWS")
    return {"VALID": not bad, "PROBLEMS": tuple(bad),
            "DECLARED_METHODS": EFFECTIVE_N_METHODS,
            "A_WARNING_IS_NOT_A_GATE": A_WARNING_IS_NOT_A_GATE}


def effective_n(raw_rows=None, markets=None, independent_events=None,
                event_hours=None, dependence_cluster_ids=None,
                effective_n=None, effective_n_method=None,
                relation_to_raw_rows=None):
    """Carry the whole provenance of an N. Never collapse rows into trials."""
    out = {
        "RAW_ROWS": raw_rows if raw_rows is not None else NOT_IDENTIFIED,
        "MARKETS": markets if markets is not None else NOT_IDENTIFIED,
        "INDEPENDENT_EVENTS": (independent_events
                               if independent_events is not None
                               else NOT_IDENTIFIED),
        "EVENT_HOURS": (event_hours if event_hours is not None
                        else NOT_IDENTIFIED),
        "DEPENDENCE_CLUSTER_IDS": (tuple(dependence_cluster_ids)
                                   if dependence_cluster_ids
                                   else NOT_IDENTIFIED),
        "EFFECTIVE_N": (effective_n if effective_n is not None
                        else NOT_IDENTIFIED),
        "EFFECTIVE_N_METHOD": effective_n_method or NOT_IDENTIFIED,
        "RAW_ROWS_ARE_NOT_INDEPENDENT_OBSERVATIONS":
            RAW_ROWS_ARE_NOT_INDEPENDENT_OBSERVATIONS,
    }
    v = validate_effective_n(effective_n, effective_n_method, raw_rows,
                             relation_to_raw_rows)
    out["EFFECTIVE_N_VALIDATION"] = v
    out["RELATION_TO_RAW_ROWS"] = relation_to_raw_rows or NOT_IDENTIFIED
    out["POSTERIOR_PRECISION_STATUS"] = (
        "MODELLED" if v["VALID"] else POSTERIOR_PRECISION_UNMODELLED)
    return out


def update_beta(prior_dist, successes, trials, prior_version="1",
                data_batch=None, n_provenance=None):
    """Beta-Binomial conjugate update. The canonical P_FILL update."""
    if prior_dist.family != "BETA":
        return {"STATUS": "WRONG_FAMILY", "EXPECTED": "BETA",
                "GOT": prior_dist.family}
    a, b = prior_dist.params["alpha"], prior_dist.params["beta"]
    if not _strict_count(successes) or not _strict_count(trials):
        return {"STATUS": "INVALID_COUNTS",
                "SUCCESSES": repr(successes), "TRIALS": repr(trials),
                "REQUIRED": "NON_NEGATIVE_INTEGER",
                "COUNT_VALIDATION_RULE": COUNT_VALIDATION_RULE}
    s, n = successes, trials
    if s > n:
        return {"STATUS": "INVALID_DATA", "SUCCESSES": s, "TRIALS": n,
                "WHY": "successes exceed trials"}
    post = Dist("BETA", {"alpha": a + s, "beta": b + (n - s)})
    prov = n_provenance if n_provenance is not None else effective_n()
    decision_grade = prov.get("POSTERIOR_PRECISION_STATUS") == "MODELLED"
    return {
        "STATUS": "UPDATED",
        "DECISION_GRADE_POSTERIOR": (post if decision_grade
                                     else NOT_IDENTIFIED),
        "DIAGNOSTIC_RAW_ROW_POSTERIOR": (NOT_IDENTIFIED if decision_grade
                                         else post),
        "A_WARNING_IS_NOT_A_GATE": A_WARNING_IS_NOT_A_GATE,
        "PRIOR_VERSION": prior_version,
        "POSTERIOR_VERSION": "%s+n%d" % (prior_version, n),
        "LIKELIHOOD_SPEC": "BINOMIAL",
        "DATA_BATCH": data_batch or NOT_IDENTIFIED,
        "PRIOR": prior_dist.summary(),
        "POSTERIOR": post.summary(),
        "POSTERIOR_DIST": post,
        "PRIOR_TO_POSTERIOR_SHIFT": round(post.mean() - prior_dist.mean(), 10),
        "UPDATE_IS_VERSIONED": UPDATE_IS_VERSIONED,
        "N_PROVENANCE": prov,
        "POSTERIOR_PRECISION_STATUS": prov.get(
            "POSTERIOR_PRECISION_STATUS", POSTERIOR_PRECISION_UNMODELLED),
        "RAW_ROWS_ARE_NOT_INDEPENDENT_OBSERVATIONS":
            RAW_ROWS_ARE_NOT_INDEPENDENT_OBSERVATIONS,
    }


def update_normal(prior_dist, obs_mean, obs_sigma, n, prior_version="1",
                  data_batch=None, n_provenance=None):
    """Normal-Normal conjugate update on the mean."""
    if prior_dist.family != "NORMAL":
        return {"STATUS": "WRONG_FAMILY", "EXPECTED": "NORMAL",
                "GOT": prior_dist.family}
    if not _strict_count(n) or n < 1:
        return {"STATUS": "INVALID_COUNTS", "N": repr(n),
                "REQUIRED": "POSITIVE_INTEGER",
                "COUNT_VALIDATION_RULE": COUNT_VALIDATION_RULE}
    if not isinstance(obs_sigma, (int, float)) or obs_sigma <= 0:
        return {"STATUS": "INVALID_DATA", "OBS_SIGMA": repr(obs_sigma),
                "WHY": "observation sigma must be positive"}
    if n <= 0 or obs_sigma is None or obs_sigma <= 0:
        return {"STATUS": "INVALID_DATA"}
    mu0, s0 = prior_dist.params["mu"], prior_dist.params["sigma"]
    tau0, tau = 1.0 / (s0 ** 2), n / (float(obs_sigma) ** 2)
    mu_post = (tau0 * mu0 + tau * float(obs_mean)) / (tau0 + tau)
    s_post = math.sqrt(1.0 / (tau0 + tau))
    post = Dist("NORMAL", {"mu": mu_post, "sigma": s_post})
    return {
        "STATUS": "UPDATED",
        "PRIOR_VERSION": prior_version,
        "POSTERIOR_VERSION": "%s+n%d" % (prior_version, n),
        "LIKELIHOOD_SPEC": "NORMAL_KNOWN_VARIANCE",
        "DATA_BATCH": data_batch or NOT_IDENTIFIED,
        "PRIOR": prior_dist.summary(),
        "POSTERIOR": post.summary(),
        "POSTERIOR_DIST": post,
        "PRIOR_TO_POSTERIOR_SHIFT": round(mu_post - mu0, 10),
        "UNCERTAINTY_FELL_BY": round(s0 - s_post, 10),
        "UPDATE_IS_VERSIONED": UPDATE_IS_VERSIONED,
    }


# --- Hierarchical shrinkage. -----------------------------------------------

SHRINKAGE_RULE = (
    "a subgroup with five observations does not get its own independent "
    "estimate. It shrinks toward its better-supported parent by n/(n+k), so "
    "sparse cells inherit the parent until they earn their own answer")

HIERARCHY = ("GLOBAL", "SPORT", "LEAGUE", "MARKET_FAMILY", "PRICE_BAND",
             "LIQUIDITY_REGIME", "TIME_TO_EVENT_REGIME", "VENUE_STATE_REGIME")


def shrink(subgroup_mean, subgroup_n, parent_mean, k=None):
    """Partial pooling. Returns the parent when the subgroup is empty.

    k has NO default. It is a shrinkage strength, and a caller who did not
    choose one has not made the modelling decision; k=20 looked like a
    production constant and was never anything of the kind.
    """
    if subgroup_n is None or subgroup_n <= 0 or subgroup_mean is None:
        return {"SHRUNK_MEAN": parent_mean, "WEIGHT_ON_SUBGROUP": 0.0,
                "SUBGROUP_N": subgroup_n or 0,
                "SHRINKAGE_RULE": SHRINKAGE_RULE,
                "SHRINKAGE_CALIBRATION_STATUS": SHRINKAGE_CALIBRATION_STATUS,
                "WHY": "no subgroup observations; the parent is the estimate"}
    if not _finite(k) or float(k) <= 0:
        return {"SHRUNK_MEAN": NOT_IDENTIFIED,
                "STATUS": "SHRINKAGE_K_NOT_IDENTIFIED",
                "SUBGROUP_N": subgroup_n, "PARENT_MEAN": parent_mean,
                "NO_DEFAULT_SHRINKAGE_K": NO_DEFAULT_SHRINKAGE_K,
                "SHRINKAGE_CALIBRATION_STATUS": SHRINKAGE_CALIBRATION_STATUS}
    n = float(subgroup_n)
    w = n / (n + float(k))
    return {"SHRUNK_MEAN": round(w * float(subgroup_mean)
                                 + (1 - w) * float(parent_mean), 10),
            "WEIGHT_ON_SUBGROUP": round(w, 6),
            "SUBGROUP_N": subgroup_n, "PARENT_MEAN": parent_mean,
            "K": k, "SHRINKAGE_RULE": SHRINKAGE_RULE,
            "SHRINKAGE_CALIBRATION_STATUS": SHRINKAGE_CALIBRATION_STATUS,
            "SHRINKAGE_IS_A_CONVENTION_NOT_A_MEASUREMENT":
                SHRINKAGE_IS_A_CONVENTION_NOT_A_MEASUREMENT,
            "DECISION_GRADE_POSTERIOR_PRECISION": False}


def describe():
    return {
        "EVIDENCE_CLASSES": EVIDENCE_CLASSES,
        "NEVER_SILENTLY_SUBSTITUTE": NEVER_SILENTLY_SUBSTITUTE,
        "PRIOR_STRENGTHS": PRIOR_STRENGTHS,
        "STRENGTH_MEANING": dict(STRENGTH_MEANING),
        "STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN":
            STRENGTH_AFFECTS_UNCERTAINTY_NOT_MEAN,
        "DECLARED_PARAMETERS": DECLARED_PARAMETERS,
        "A_NAME_IS_NOT_A_PRIOR": A_NAME_IS_NOT_A_PRIOR,
        "NO_PRIOR_WITHOUT_A_REASON": NO_PRIOR_WITHOUT_A_REASON,
        "FILL_SELECTION_PRIOR_SOURCE": FILL_SELECTION_PRIOR_SOURCE,
        "FILL_SELECTION_PRIOR_CLASSIFICATION":
            dict(FILL_SELECTION_PRIOR_CLASSIFICATION),
        "NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO":
            NOT_EVIDENCE_THAT_THE_EFFECT_IS_ZERO,
        "PRIOR_WIDTH_MUST_REMAIN_VISIBLE": PRIOR_WIDTH_MUST_REMAIN_VISIBLE,
        "POSTERIOR_MAY_MOVE_EITHER_WAY": POSTERIOR_MAY_MOVE_EITHER_WAY,
        "EARLIER_SOURCE_LABEL_SAID": EARLIER_SOURCE_LABEL_SAID,
        "PARAMETER_VALIDATION_IS_FAIL_CLOSED":
            PARAMETER_VALIDATION_IS_FAIL_CLOSED,
        "TRUNCATION_IS_PART_OF_THE_MODEL": TRUNCATION_IS_PART_OF_THE_MODEL,
        "BOUNDED_UNIT_FAMILIES": BOUNDED_UNIT_FAMILIES,
        "SHRINKAGE_CALIBRATION_STATUS": SHRINKAGE_CALIBRATION_STATUS,
        "SHRINKAGE_IS_A_CONVENTION_NOT_A_MEASUREMENT":
            SHRINKAGE_IS_A_CONVENTION_NOT_A_MEASUREMENT,
        "EFFECTIVE_N_METHODS": EFFECTIVE_N_METHODS,
        "A_WARNING_IS_NOT_A_GATE": A_WARNING_IS_NOT_A_GATE,
        "QUEUE_MECHANISM_STATUS": QUEUE_MECHANISM_STATUS,
        "REGISTRY_CENSUS": registry_census(),
        "HIERARCHY": HIERARCHY,
        "SHRINKAGE_RULE": SHRINKAGE_RULE,
        "UPDATE_IS_VERSIONED": UPDATE_IS_VERSIONED,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
