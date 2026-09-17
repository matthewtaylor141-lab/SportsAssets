"""Section 13. Power for the lead/lag experiment. NOT the settlement ladder.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

WHY THE SETTLEMENT LADDER DOES NOT APPLY
----------------------------------------
The settlement ladder says 4,654 independent events at the point estimate, or
8,702 at P90, to resolve a 0.010 incremental log-loss effect. It is the right
number for that question and the wrong number for this one.

A settlement gives ONE binary outcome per fixture. However long the match, no
amount of watching produces a second draw from that distribution.

A lead/lag experiment is different in kind. One fixture observed every five
minutes for two hours yields 24 paired observations of "did the venue move
toward the external source". Those are repeated measurements of a RELATIONSHIP,
not repeated draws of an outcome.

THE TRAP, STATED PLAINLY
------------------------
    5,000 time points are NOT 5,000 independent events.

Observations within one fixture share its liquidity, its news flow, its books
and its participants. Treating them as independent inflates the effective
sample by roughly the design effect and produces a confidence interval several
times too narrow -- the same class of error as counting contract rows instead
of events, which this programme has already made once.

So the unit is the EVENT CLUSTER, with m repeated observations inside it, and
the effective sample size is

    N_EFF = N_EVENTS * m / (1 + (m - 1) * ICC)

where ICC is the intraclass correlation of the paired quantity. At ICC = 0 the
repeats are free and N_EFF = N_EVENTS * m. At ICC = 1 they are worthless and
N_EFF = N_EVENTS. Reality sits between, and the ICC is MEASURED from the data
rather than assumed.
"""

import math
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

THIS_IS_NOT_THE_SETTLEMENT_LADDER = True
DO_NOT_USE_THE_SETTLEMENT_LADDER_HERE = (
    "the 4,654 / 8,702 event figures answer whether BETTOR improves the "
    "settlement forecast. They say nothing about how many observations are "
    "needed to detect that one market follows another")

THE_TRAP = "5,000 TIME POINTS ARE NOT 5,000 INDEPENDENT EVENTS"

UNIT = "EVENT_CLUSTER_WITH_REPEATED_TIMESTAMP_OBSERVATIONS"

Z_ALPHA_TWO_SIDED_95 = 1.959963984540054
Z_POWER = {0.80: 0.8416212335729143, 0.90: 1.2815515655446004}

TARGET_CORRELATIONS = (0.02, 0.05, 0.10, 0.20)
WHY_THESE_TARGETS = (
    "a lead/lag effect is reported as a correlation between disagreement at T "
    "and the venue's move after T. 0.05 is small but tradeable at high "
    "turnover; 0.20 would be a large and obvious lead. The ladder is stated in "
    "correlation because that is what the experiment measures")


def _mean(v):
    return sum(v) / len(v) if v else None


def icc(clusters):
    """Intraclass correlation of the paired quantity, one-way random effects.

    `clusters` maps event -> list of values (for example, the per-observation
    product of standardised disagreement and standardised forward move). This
    is the number that decides whether repeated observations are nearly free or
    nearly worthless, so it is measured, never assumed.
    """
    groups = [v for v in (clusters or {}).values() if len(v) >= 2]
    if len(groups) < 2:
        return NOT_IDENTIFIED
    allv = [x for g in groups for x in g]
    n_tot, k_groups = len(allv), len(groups)
    gm = _mean(allv)
    m = n_tot / k_groups
    ss_b = sum(len(g) * (_mean(g) - gm) ** 2 for g in groups)
    ss_w = sum((x - _mean(g)) ** 2 for g in groups for x in g)
    df_b, df_w = k_groups - 1, n_tot - k_groups
    if df_b <= 0 or df_w <= 0:
        return NOT_IDENTIFIED
    ms_b, ms_w = ss_b / df_b, ss_w / df_w
    denom = ms_b + (m - 1) * ms_w
    if denom <= 0:
        return NOT_IDENTIFIED
    return max(0.0, (ms_b - ms_w) / denom)


def design_effect(m, rho):
    """1 + (m - 1) * ICC. How much each repeat is discounted."""
    if rho == NOT_IDENTIFIED or m is None:
        return NOT_IDENTIFIED
    return 1.0 + (float(m) - 1.0) * float(rho)


def effective_n(n_events, m, rho):
    """Independent-equivalent observations."""
    de = design_effect(m, rho)
    if de == NOT_IDENTIFIED or not de:
        return NOT_IDENTIFIED
    return float(n_events) * float(m) / de


def observations_required(target_r, power=0.80, alpha=0.05):
    """Independent-equivalent observations to detect a correlation of r.

    Fisher z: n = ((z_alpha + z_power) / atanh(r))^2 + 3.
    """
    if not target_r or power not in Z_POWER or alpha != 0.05:
        return NOT_IDENTIFIED
    z = Z_ALPHA_TWO_SIDED_95 + Z_POWER[power]
    return int(math.ceil((z / math.atanh(float(target_r))) ** 2 + 3))


def events_required(target_r, m, rho, power=0.80):
    """Event CLUSTERS needed, given m repeats each and a measured ICC."""
    n_eff = observations_required(target_r, power)
    if n_eff == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    de = design_effect(m, rho)
    if de == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    return int(math.ceil(n_eff * de / float(m)))


def ladder(m, rho, targets=TARGET_CORRELATIONS, power=0.80):
    """The lead/lag evidence ladder at a given cadence and measured ICC."""
    return {
        "OBSERVATIONS_PER_EVENT": m,
        "MEASURED_ICC": rho,
        "DESIGN_EFFECT": design_effect(m, rho),
        "UNIT": UNIT,
        "BY_TARGET_CORRELATION": {
            r: {"INDEPENDENT_EQUIVALENT_OBSERVATIONS":
                observations_required(r, power),
                "EVENT_CLUSTERS_REQUIRED": events_required(r, m, rho, power)}
            for r in targets},
        "THE_TRAP": THE_TRAP,
        "THIS_IS_NOT_THE_SETTLEMENT_LADDER": THIS_IS_NOT_THE_SETTLEMENT_LADDER,
        "WHY_THESE_TARGETS": WHY_THESE_TARGETS,
    }


def ladder_from_rows(rows, signal="EXTERNAL_DISAGREEMENT",
                     target="POLY_CHANGE_5M", targets=TARGET_CORRELATIONS):
    """Measure m and ICC from real rows, then build the ladder.

    The ICC is computed on the per-observation cross-product of the two
    standardised series, which is the quantity whose mean IS the correlation --
    so its clustering is the clustering that matters for the interval.
    """
    by = defaultdict(list)
    for r in rows or ():
        if r.get(signal) is not None and r.get(target) is not None:
            by[r.get("EVENT_KEY")].append((r[signal], r[target]))
    if len(by) < 2:
        return {"STATUS": "TOO_FEW_EVENTS", "EVENTS": len(by)}
    xs = [a for v in by.values() for a, _ in v]
    ys = [b for v in by.values() for _, b in v]
    mx, my = _mean(xs), _mean(ys)
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs) / max(len(xs) - 1, 1))
    sy = math.sqrt(sum((b - my) ** 2 for b in ys) / max(len(ys) - 1, 1))
    if sx <= 0 or sy <= 0:
        return {"STATUS": "DEGENERATE_SERIES"}
    clusters = {e: [((a - mx) / sx) * ((b - my) / sy) for a, b in v]
                for e, v in by.items()}
    rho = icc(clusters)
    m = sum(len(v) for v in by.values()) / len(by)
    out = ladder(m, rho, targets)
    out["STATUS"] = "MEASURED"
    out["EVENTS"] = len(by)
    out["OBSERVATIONS"] = sum(len(v) for v in by.values())
    out["EFFECTIVE_N"] = effective_n(len(by), m, rho)
    out["NAIVE_N_WOULD_HAVE_BEEN"] = sum(len(v) for v in by.values())
    if out["EFFECTIVE_N"] != NOT_IDENTIFIED and out["EFFECTIVE_N"]:
        out["NAIVE_OVERSTATES_BY"] = (out["NAIVE_N_WOULD_HAVE_BEEN"]
                                      / out["EFFECTIVE_N"])
    return out


# --- What the planned pilots would actually deliver. -----------------------

PILOT_CADENCES = {
    "STATIC_3_HORIZONS": 3,
    "DENSE_2H_AT_5MIN": 25,
    "DENSE_6H_AT_5MIN": 73,
}

ICC_IS_UNMEASURED_UNTIL_DATA_EXISTS = (
    "no external snapshots have been obtained, so the ICC of the lead/lag "
    "cross-product is NOT_IDENTIFIED. The ladder function takes it as an "
    "argument precisely so that the first pilot's own data sets it, rather "
    "than a guess being baked in now")

ILLUSTRATIVE_ICC_RANGE = (0.05, 0.20, 0.50)
ILLUSTRATIVE_ONLY = (
    "these three values are shown to bound the planning range. They are NOT "
    "measurements and must not be quoted as the programme's ICC")


def describe():
    return {
        "THE_TRAP": THE_TRAP,
        "UNIT": UNIT,
        "THIS_IS_NOT_THE_SETTLEMENT_LADDER": THIS_IS_NOT_THE_SETTLEMENT_LADDER,
        "DO_NOT_USE_THE_SETTLEMENT_LADDER_HERE":
            DO_NOT_USE_THE_SETTLEMENT_LADDER_HERE,
        "TARGET_CORRELATIONS": TARGET_CORRELATIONS,
        "PILOT_CADENCES": dict(PILOT_CADENCES),
        "ICC_IS_UNMEASURED_UNTIL_DATA_EXISTS":
            ICC_IS_UNMEASURED_UNTIL_DATA_EXISTS,
        "ILLUSTRATIVE_ONLY": ILLUSTRATIVE_ONLY,
    }
