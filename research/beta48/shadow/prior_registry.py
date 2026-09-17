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


# --- Distributions. Small, exact, seedable. --------------------------------

FAMILIES = ("BETA", "NORMAL", "LOGNORMAL", "TRIANGULAR", "POINT")

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
            raise ValueError("unknown family %r; declared %r"
                             % (family, FAMILIES))
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
    """Beta with a given centre and a given strength. Centre is preserved."""
    mean = min(max(float(mean), 1e-6), 1 - 1e-6)
    n = max(float(pseudo_n), 0.2)
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
    row["PRIOR_SHA"] = hashlib.sha256(json.dumps(
        {k: v for k, v in row.items() if k != "PRIOR_SHA"},
        sort_keys=True, default=str).encode()).hexdigest()
    return row


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


def _registry():
    """The priors that exist today, each with its reason.

    Most are NO. That is the honest state before prospective data, and the
    break-even engine is what makes the NOs actionable.
    """
    reg = {}

    # FILL_SELECTION_EFFECT -- the least-informative defensible choice.
    reg["FILL_SELECTION_EFFECT"] = make_prior(
        "FILL_SELECTION_EFFECT",
        Dist("NORMAL", {"mu": 0.0, "sigma": 0.004}),
        ESTIMATED_PRIOR, "WEAK",
        source_type="PRINCIPLED_LEAST_INFORMATIVE_CHOICE",
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

def p_fill_from_mechanism(queue_ahead, trade_intensity_per_s, horizon_s,
                          intensity_evidence_class=NOT_IDENTIFIED):
    """P(cumulative volume at our level exceeds the queue ahead) over h.

    Poisson arrivals of traded quantity at the level. Returns NOT_IDENTIFIED
    unless the intensity carries a real evidence class -- the mechanism does
    not manufacture its own input.
    """
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
            "MECHANISM": "POISSON_VOLUME_EXCEEDS_QUEUE_AHEAD",
            "LAMBDA": round(lam, 6), "QUEUE_AHEAD": q,
            "NEVER_LABEL_THIS_MEASURED_UNTIL_BETTOR_ORDERS_EXIST": True}


# --- Bayesian update. ------------------------------------------------------

UPDATE_IS_VERSIONED = (
    "a posterior never overwrites its prior. Both persist with their own "
    "versions, so every posterior is reproducible from its inputs")


def update_beta(prior_dist, successes, trials, prior_version="1",
                data_batch=None):
    """Beta-Binomial conjugate update. The canonical P_FILL update."""
    if prior_dist.family != "BETA":
        return {"STATUS": "WRONG_FAMILY", "EXPECTED": "BETA",
                "GOT": prior_dist.family}
    a, b = prior_dist.params["alpha"], prior_dist.params["beta"]
    s, n = int(successes), int(trials)
    if s < 0 or n < 0 or s > n:
        return {"STATUS": "INVALID_DATA", "SUCCESSES": s, "TRIALS": n}
    post = Dist("BETA", {"alpha": a + s, "beta": b + (n - s)})
    return {
        "STATUS": "UPDATED",
        "PRIOR_VERSION": prior_version,
        "POSTERIOR_VERSION": "%s+n%d" % (prior_version, n),
        "LIKELIHOOD_SPEC": "BINOMIAL",
        "DATA_BATCH": data_batch or NOT_IDENTIFIED,
        "PRIOR": prior_dist.summary(),
        "POSTERIOR": post.summary(),
        "POSTERIOR_DIST": post,
        "PRIOR_TO_POSTERIOR_SHIFT": round(post.mean() - prior_dist.mean(), 10),
        "UPDATE_IS_VERSIONED": UPDATE_IS_VERSIONED,
    }


def update_normal(prior_dist, obs_mean, obs_sigma, n, prior_version="1",
                  data_batch=None):
    """Normal-Normal conjugate update on the mean."""
    if prior_dist.family != "NORMAL":
        return {"STATUS": "WRONG_FAMILY", "EXPECTED": "NORMAL",
                "GOT": prior_dist.family}
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


def shrink(subgroup_mean, subgroup_n, parent_mean, k=20.0):
    """Partial pooling. Returns the parent when the subgroup is empty."""
    if subgroup_n is None or subgroup_n <= 0 or subgroup_mean is None:
        return {"SHRUNK_MEAN": parent_mean, "WEIGHT_ON_SUBGROUP": 0.0,
                "SUBGROUP_N": subgroup_n or 0,
                "SHRINKAGE_RULE": SHRINKAGE_RULE,
                "WHY": "no subgroup observations; the parent is the estimate"}
    n = float(subgroup_n)
    w = n / (n + float(k))
    return {"SHRUNK_MEAN": round(w * float(subgroup_mean)
                                 + (1 - w) * float(parent_mean), 10),
            "WEIGHT_ON_SUBGROUP": round(w, 6),
            "SUBGROUP_N": subgroup_n, "PARENT_MEAN": parent_mean,
            "K": k, "SHRINKAGE_RULE": SHRINKAGE_RULE}


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
        "REGISTRY_CENSUS": registry_census(),
        "HIERARCHY": HIERARCHY,
        "SHRINKAGE_RULE": SHRINKAGE_RULE,
        "UPDATE_IS_VERSIONED": UPDATE_IS_VERSIONED,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
