"""Edge status, decay, attribution, experiment ranking, management dashboard.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE.

NO BINARY "WE HAVE EDGE" FLAG
-----------------------------
Every edge component carries an ESTIMATE, its UNCERTAINTY, the out-of-sample
EVENT count behind it, its recency, its regime coverage, and a status on a
ladder from NOT_IDENTIFIED to PRODUCTION_VALIDATED. A single boolean would
compress all six into a claim none of them supports.

THE OBJECTIVE IS NOT THAT EDGE ALWAYS GROWS
-------------------------------------------
Edges decay. The objective is that BETTOR gets better at DISCOVERING,
MEASURING, USING and RETIRING them. A system that cannot retire a dead edge
will keep trading it.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOTHING_IS_TRAINED_HERE = True


# --- Section 39. Edge components and their ladder. -------------------------

EDGE_COMPONENTS = ("SETTLEMENT_EDGE", "SHORT_HORIZON_INFORMATION_EDGE",
                   "EXTERNAL_LEAD_LAG_EDGE", "RELATIVE_VALUE_EDGE",
                   "FILL_EDGE", "TOXICITY_AVOIDANCE_EDGE", "INVENTORY_EDGE",
                   "CAPITAL_ALLOCATION_EDGE")

EDGE_STATUSES = ("NOT_IDENTIFIED", "HYPOTHESIS", "DETECTED", "REPLICATED",
                 "PRODUCTION_VALIDATED", "DEGRADED")

NO_BINARY_EDGE_FLAG = (
    "a single 'we have edge' boolean compresses estimate, uncertainty, event "
    "count, recency, regime coverage and replication status into a claim none "
    "of them supports on its own")

EDGE_REQUIRED_FIELDS = ("ESTIMATE", "UNCERTAINTY", "OOS_EVENT_N", "RECENCY",
                        "REGIME_COVERAGE", "STATUS")


def edge_row(component, estimate=None, uncertainty=None, oos_event_n=0,
             recency=None, regime_coverage=None, status=None):
    """One edge component. Status is DERIVED from evidence, not asserted."""
    if component not in EDGE_COMPONENTS:
        return {"COMPONENT": component, "STATUS": "UNKNOWN_COMPONENT",
                "DECLARED": EDGE_COMPONENTS}
    if status is None:
        if estimate is None or oos_event_n <= 0:
            status = "NOT_IDENTIFIED"
        elif oos_event_n < 30:
            status = "HYPOTHESIS"
        elif oos_event_n < 200:
            status = "DETECTED"
        else:
            status = "REPLICATED"
    if status not in EDGE_STATUSES:
        return {"COMPONENT": component, "STATUS": "UNKNOWN_STATUS",
                "DECLARED": EDGE_STATUSES}
    return {
        "COMPONENT": component,
        "ESTIMATE": estimate if estimate is not None else NOT_IDENTIFIED,
        "UNCERTAINTY": uncertainty if uncertainty is not None
        else NOT_IDENTIFIED,
        "OOS_EVENT_N": oos_event_n,
        "RECENCY": recency or NOT_IDENTIFIED,
        "REGIME_COVERAGE": regime_coverage or NOT_IDENTIFIED,
        "STATUS": status,
        "NO_BINARY_EDGE_FLAG": NO_BINARY_EDGE_FLAG,
        "PRODUCTION_VALIDATED_REQUIRES_PRODUCTION": (
            "PRODUCTION_VALIDATED cannot be reached offline. It requires the "
            "edge to survive in production, which has never happened"),
    }


def edge_board():
    """Today's board. Every component NOT_IDENTIFIED -- the honest state."""
    return {c: edge_row(c) for c in EDGE_COMPONENTS}


# --- Section 40. Edge decay. -----------------------------------------------

THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS = (
    "edges decay. The objective is that BETTOR gets better at DISCOVERING, "
    "MEASURING, USING and RETIRING them. A system that cannot retire a dead "
    "edge will keep trading it")


def edge_decay(recent=None, medium=None, long=None):
    """Compare windows. A weakening signal is de-rated, not defended."""
    vals = [("RECENT", recent), ("MEDIUM", medium), ("LONG", long)]
    known = [(k, float(v)) for k, v in vals if v is not None]
    if len(known) < 2:
        return {"EDGE_HALF_LIFE_ESTIMATE": NOT_IDENTIFIED,
                "WINDOWS_SUPPLIED": [k for k, _ in known],
                "WHY": "at least two windows are needed to see a trend",
                "THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS":
                    THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS}
    d = dict(known)
    weakening = ("RECENT" in d and "LONG" in d and abs(d["RECENT"])
                 < abs(d["LONG"]))
    out = {
        "WINDOWS": d,
        "WEAKENING": weakening,
        "DE_RATE": weakening,
        "RECOMMENDED_STATUS_IF_WEAKENING": "DEGRADED",
        "THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS":
            THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS,
    }
    # Half-life only where a defensible exponential read exists.
    if weakening and d.get("LONG") and d.get("RECENT"):
        ratio = abs(d["RECENT"]) / abs(d["LONG"])
        if 0 < ratio < 1:
            import math
            out["EDGE_HALF_LIFE_ESTIMATE_WINDOWS"] = round(
                math.log(0.5) / math.log(ratio), 4)
            out["HALF_LIFE_UNITS"] = "WINDOW_LENGTHS_NOT_DAYS"
            out["HALF_LIFE_CAVEAT"] = (
                "this assumes exponential decay across two points, which two "
                "points cannot establish. It is a de-rating signal, not a "
                "forecast")
    else:
        out["EDGE_HALF_LIFE_ESTIMATE"] = NOT_IDENTIFIED
    return out


# --- Section 48. Edge attribution. -----------------------------------------

ATTRIBUTION_POSITIVE = ("SETTLEMENT_ALPHA", "EXTERNAL_LEAD_LAG",
                        "MICROSTRUCTURE", "RELATIVE_VALUE", "SPREAD_CAPTURE",
                        "REBATE_INCENTIVE", "INVENTORY", "CAPITAL_RECYCLING")
ATTRIBUTION_NEGATIVE = ("FEES", "TOXICITY", "SLIPPAGE", "EXIT_COST")

NOT_ONE_BLENDED_ALPHA = (
    "one blended alpha number cannot say whether BETTOR is earning the spread "
    "and losing it to toxicity, or earning nothing and being paid a rebate. "
    "Those call for opposite responses")


def attribute(components=None):
    components = components or {}
    pos = {k: components.get(k, NOT_IDENTIFIED) for k in ATTRIBUTION_POSITIVE}
    neg = {k: components.get(k, NOT_IDENTIFIED) for k in ATTRIBUTION_NEGATIVE}
    nums = [v for v in list(pos.values()) + list(neg.values())
            if isinstance(v, (int, float))]
    total = NOT_IDENTIFIED
    if nums and len(nums) == len(pos) + len(neg):
        total = round(sum(pos[k] for k in pos) - sum(neg[k] for k in neg), 10)
    return {"POSITIVE_COMPONENTS": pos, "NEGATIVE_COMPONENTS": neg,
            "NET": total, "NOT_ONE_BLENDED_ALPHA": NOT_ONE_BLENDED_ALPHA,
            "UNATTRIBUTED_COMPONENTS": [k for k, v in
                                        list(pos.items()) + list(neg.items())
                                        if v == NOT_IDENTIFIED]}


EDGE_INTERACTION_REQUIRES_N = (
    "components may interact -- a microprice signal may only work when "
    "external consensus agrees. But an interaction estimated on a handful of "
    "events is data mining. Interactions require their own sufficient event N")

# --- CORRECTION. The interaction threshold was never earned. ---------------

EDGE_INTERACTION_STATUS = "BUILT_THRESHOLD_NOT_IDENTIFIED_PENDING_POWER_ANALYSIS"

EARLIER_THRESHOLD_SAID = (
    "an earlier build fixed MIN_EVENTS_FOR_AN_INTERACTION at 200 and returned "
    "a boolean from it. That is retracted. 200 was a round number chosen for "
    "safety and then presented as scientific sufficiency")

WHY_A_ROUND_N_IS_NOT_SUFFICIENCY = (
    "a safety heuristic and a power analysis produce the same shape of "
    "answer -- an integer -- and are not the same kind of claim. A round N "
    "cannot be wrong in a way anyone can check, because it was never derived "
    "from an effect size, a variance or a target. Sufficiency is a property "
    "of a design, not of a number that feels large")

POWER_ANALYSIS_INPUTS = (
    "INDEPENDENT_EVENT_N",
    "INTERACTION_DEGREES_OF_FREEDOM",
    "EFFECT_SIZE_OF_INTEREST",
    "EVENT_LEVEL_VARIANCE",
    "REGIME_SUPPORT",
    "PROSPECTIVE_PRECISION_OR_POWER_TARGET",
)

REGIME_SUPPORT_FIELDS = ("REGIMES_REQUIRED", "REGIMES_WITH_SUPPORT")

POWER_TARGET_FIELDS = ("ALPHA", "POWER")

LADDER_THRESHOLDS_ARE_A_SEPARATE_OPEN_QUESTION = (
    "edge_row()'s 30/200 cutoffs name a STATUS LABEL on a ladder. They do not "
    "grant permission to estimate anything, and this correction does not "
    "cover them. That they are also unearned as sufficiency claims is "
    "recorded here rather than silently repaired")


def required_events_for_interaction(effect_size_of_interest=None,
                                    event_level_variance=None,
                                    interaction_degrees_of_freedom=None,
                                    power_target=None):
    """Derive the event N a specific interaction needs. Nothing is assumed.

    Every term comes from the caller. If any is missing the answer is
    NOT_IDENTIFIED -- there is no default effect size and no default
    variance, and substituting one would rebuild the round number this
    correction removed.
    """
    missing = []
    if not isinstance(effect_size_of_interest, (int, float)) \
            or effect_size_of_interest == 0:
        missing.append("EFFECT_SIZE_OF_INTEREST")
    if not isinstance(event_level_variance, (int, float)) \
            or event_level_variance <= 0:
        missing.append("EVENT_LEVEL_VARIANCE")
    if not isinstance(interaction_degrees_of_freedom, int) \
            or interaction_degrees_of_freedom < 1:
        missing.append("INTERACTION_DEGREES_OF_FREEDOM")
    tgt = power_target or {}
    alpha = tgt.get("ALPHA")
    power = tgt.get("POWER")
    if not isinstance(alpha, (int, float)) or not 0 < alpha < 1 \
            or not isinstance(power, (int, float)) or not 0 < power < 1:
        missing.append("PROSPECTIVE_PRECISION_OR_POWER_TARGET")
    if missing:
        return {"REQUIRED_EVENT_N": NOT_IDENTIFIED,
                "MISSING_INPUTS": tuple(missing),
                "POWER_ANALYSIS_INPUTS": POWER_ANALYSIS_INPUTS,
                "WHY_A_ROUND_N_IS_NOT_SUFFICIENCY":
                    WHY_A_ROUND_N_IS_NOT_SUFFICIENCY}
    import math
    from statistics import NormalDist
    nd = NormalDist()
    df = interaction_degrees_of_freedom
    # df enters as multiplicity: an interaction with df contrasts is df
    # simultaneous tests, so the per-contrast alpha is split across them.
    alpha_per_contrast = alpha / df
    z_a = nd.inv_cdf(1.0 - alpha_per_contrast / 2.0)
    z_b = nd.inv_cdf(power)
    n = ((z_a + z_b) ** 2) * float(event_level_variance) \
        / (float(effect_size_of_interest) ** 2)
    return {
        "REQUIRED_EVENT_N": int(math.ceil(n)),
        "INPUTS_USED": {"EFFECT_SIZE_OF_INTEREST": effect_size_of_interest,
                        "EVENT_LEVEL_VARIANCE": event_level_variance,
                        "INTERACTION_DEGREES_OF_FREEDOM": df,
                        "ALPHA": alpha, "POWER": power,
                        "ALPHA_PER_CONTRAST": alpha_per_contrast},
        "APPROXIMATION": (
            "two-sided normal approximation for one interaction contrast, "
            "Bonferroni-split across the interaction's degrees of freedom"),
        "ASSUMPTIONS": (
            "EVENT_LEVEL_VARIANCE is the per-INDEPENDENT-EVENT variance of "
            "the interaction contrast estimator's numerator, not the "
            "per-ROW variance. Rows inside one event are not independent, "
            "so a row-level variance would understate the requirement",
            "the effect size is the smallest interaction worth acting on, "
            "chosen before looking at the data",
            "this is a design calculation, not a guarantee: meeting N means "
            "the design could detect the stated effect, not that any "
            "interaction found is real"),
        "DERIVED_NOT_ASSERTED": True,
    }


def interaction(name, event_n=0, effect_size_of_interest=None,
                event_level_variance=None,
                interaction_degrees_of_freedom=None,
                regime_support=None, power_target=None):
    """May this interaction be estimated? Default answer: NOT_IDENTIFIED.

    Eligibility is the output of a power analysis over the caller's own
    design terms. With no power analysis there is no threshold, and the
    honest answer is that we do not know -- not a boolean read off a round
    number.
    """
    req = required_events_for_interaction(
        effect_size_of_interest=effect_size_of_interest,
        event_level_variance=event_level_variance,
        interaction_degrees_of_freedom=interaction_degrees_of_freedom,
        power_target=power_target)
    out = {"INTERACTION": name,
           "INDEPENDENT_EVENT_N": event_n,
           "EDGE_INTERACTION_STATUS": EDGE_INTERACTION_STATUS,
           "POWER_ANALYSIS": req,
           "POWER_ANALYSIS_INPUTS": POWER_ANALYSIS_INPUTS,
           "EDGE_INTERACTION_REQUIRES_N": EDGE_INTERACTION_REQUIRES_N,
           "WHY_A_ROUND_N_IS_NOT_SUFFICIENCY":
               WHY_A_ROUND_N_IS_NOT_SUFFICIENCY,
           "EARLIER_THRESHOLD_SAID": EARLIER_THRESHOLD_SAID}

    # Regime support is supplied, never assumed -- an interaction estimated
    # inside one regime is an interaction with a regime, uncontrolled.
    rs = regime_support if isinstance(regime_support, dict) else None
    req_reg = (rs or {}).get("REGIMES_REQUIRED")
    have_reg = (rs or {}).get("REGIMES_WITH_SUPPORT")
    regime_ok = None
    if isinstance(req_reg, int) and req_reg >= 1 \
            and isinstance(have_reg, int) and have_reg >= 0:
        regime_ok = have_reg >= req_reg
    out["REGIME_SUPPORT"] = (
        {"REGIMES_REQUIRED": req_reg, "REGIMES_WITH_SUPPORT": have_reg,
         "REGIME_SUPPORT_SUFFICIENT": regime_ok}
        if regime_ok is not None else NOT_IDENTIFIED)
    out["REGIME_SUPPORT_FIELDS"] = REGIME_SUPPORT_FIELDS

    if req["REQUIRED_EVENT_N"] == NOT_IDENTIFIED or regime_ok is None:
        blocked = list(req.get("MISSING_INPUTS", ()))
        if regime_ok is None:
            blocked.append("REGIME_SUPPORT")
        if not isinstance(event_n, int) or event_n < 0:
            blocked.append("INDEPENDENT_EVENT_N")
        out["MAY_ESTIMATE"] = NOT_IDENTIFIED
        out["BLOCKED_ON"] = tuple(blocked)
        out["WHY_NOT"] = (
            "no power analysis has been done for this interaction, so there "
            "is no threshold to compare %s events against. NOT_IDENTIFIED is "
            "the answer, not False and not True" % event_n)
        return out

    enough_events = int(event_n) >= req["REQUIRED_EVENT_N"]
    out["MAY_ESTIMATE"] = bool(enough_events and regime_ok)
    out["REQUIRED_EVENT_N"] = req["REQUIRED_EVENT_N"]
    out["WHY_NOT"] = None if out["MAY_ESTIMATE"] else (
        "%d independent events against a derived requirement of %d, regime "
        "support %s" % (event_n, req["REQUIRED_EVENT_N"],
                        "sufficient" if regime_ok else "insufficient"))
    return out


# --- Section 39/12. Experiment prioritisation. -----------------------------

EXPERIMENT_RANK_FORMULA = (
    "EXPECTED_REDUCTION_IN_DECISION_UNCERTAINTY x ECONOMIC_RELEVANCE / COST")


def rank_experiments(candidates=None):
    """Rank by information per dollar. Refuses a candidate missing a term."""
    rows, refused = [], []
    for c in candidates or ():
        u = c.get("EXPECTED_REDUCTION_IN_DECISION_UNCERTAINTY")
        e = c.get("ECONOMIC_RELEVANCE")
        k = c.get("COST")
        if not isinstance(u, (int, float)) or not isinstance(e, (int, float)) \
                or not isinstance(k, (int, float)) or k <= 0:
            refused.append({"EXPERIMENT": c.get("EXPERIMENT", NOT_IDENTIFIED),
                            "WHY": "a missing or non-positive term cannot be "
                                   "ranked; it is not scored as zero"})
            continue
        rows.append(dict(c, SCORE=round(u * e / k, 8)))
    rows.sort(key=lambda r: -r["SCORE"])
    return {"RANKED": rows, "REFUSED": refused,
            "FORMULA": EXPERIMENT_RANK_FORMULA,
            "TOP": rows[0]["EXPERIMENT"] if rows else NOT_IDENTIFIED,
            "WHAT_THIS_ANSWERS": (
                "which research dollar has the highest expected "
                "informational return")}


# --- Sections 38, 51, 52. The management dashboard. ------------------------

MOAT_METRICS = ("TOTAL_MARKET_STATES", "TOTAL_DECISION_STATES",
                "TOTAL_LABELLED_STATES", "TOTAL_INDEPENDENT_EVENTS",
                "TOTAL_EVENT_HOURS", "TOTAL_EXTERNAL_ALIGNED_STATES",
                "TOTAL_SHADOW_ACTIONS", "TOTAL_REAL_ORDERS",
                "TOTAL_REAL_FILLS", "TOTAL_REAL_NONFILLS",
                "TOTAL_FILL_MARKOUTS", "TOTAL_CALIBRATED_QUEUE_OBSERVATIONS",
                "UNIQUE_INFORMATION_DAYS", "NEW_LABELS_PER_DAY",
                "NEW_INDEPENDENT_EVENTS_PER_WEEK")

DASHBOARD_FIELDS = ("EDGE_ESTIMATE_BY_COMPONENT", "EDGE_UNCERTAINTY",
                    "P_EV_GT_0", "TOTAL_SHADOW_OPPORTUNITIES",
                    "TOTAL_ROBUSTLY_POSITIVE_SHADOW_OPPORTUNITIES",
                    "EXPECTED_DOLLARS_AVAILABLE",
                    "EXPECTED_CAPITAL_REQUIRED",
                    "EXPECTED_CAPITAL_TURNOVER", "EVIDENCE_CLASS_MIX",
                    "TOP_UNKNOWN_EV_DRIVER",
                    "TOP_VALUE_OF_INFORMATION_OPPORTUNITY")

CONFIDENCE_AREAS = ("SHORT_HORIZON_STRUCTURE", "EXECUTION_EDGE",
                    "SCALABILITY", "FUNDAMENTAL_ALPHA")

NO_ARBITRARY_CONFIDENCE_NUMBER = (
    "confidence is not a 0-100 score. Each area is tied to explicit evidence "
    "gates, and its status is whichever gate it has actually reached")

FORBIDDEN_LANGUAGE = ("GUARANTEED_PROFIT", "100_PERCENT_CONFIDENCE",
                      "CANNOT_LOSE", "RISK_FREE", "SURE_THING")

PERMITTED_LANGUAGE = ("POSTERIOR_EXPECTED_EV_POSITIVE",
                      "ROBUST_TO_CURRENT_UNCERTAINTY",
                      "PROSPECTIVELY_REPLICATED", "NOT_IDENTIFIED")


def dashboard(moat=None, shadow=None, edges=None):
    """The management view. Unknown fields stay NOT_IDENTIFIED."""
    d = {f: NOT_IDENTIFIED for f in DASHBOARD_FIELDS}
    d["EDGE_ESTIMATE_BY_COMPONENT"] = edges or edge_board()
    if shadow:
        d.update({k: v for k, v in shadow.items() if k in DASHBOARD_FIELDS})
    d["MOAT_METRICS"] = {m: (moat or {}).get(m, NOT_IDENTIFIED)
                         for m in MOAT_METRICS}
    d["CONFIDENCE_BY_AREA"] = {a: NOT_IDENTIFIED for a in CONFIDENCE_AREAS}
    d["NO_ARBITRARY_CONFIDENCE_NUMBER"] = NO_ARBITRARY_CONFIDENCE_NUMBER
    d["NO_BINARY_EDGE_FLAG"] = NO_BINARY_EDGE_FLAG
    d["FORBIDDEN_LANGUAGE"] = FORBIDDEN_LANGUAGE
    d["PERMITTED_LANGUAGE"] = PERMITTED_LANGUAGE
    return d


def language_check(text):
    """Refuse guaranteed-profit language anywhere in a report."""
    up = str(text).upper().replace(" ", "_")
    hits = [f for f in FORBIDDEN_LANGUAGE if f in up]
    return {"TEXT_OK": not hits, "FORBIDDEN_FOUND": hits,
            "PERMITTED_LANGUAGE": PERMITTED_LANGUAGE}


# --- Sections 45, 46. Report generators. -----------------------------------

DAILY_REPORT_SECTIONS = ("DATA_COLLECTED", "NEW_LABELS_MATURED",
                         "INDEPENDENT_EVENTS_ADDED", "DRIFT_ALERTS",
                         "CHAMPION_HEALTH", "CHALLENGER_HEALTH",
                         "SHADOW_PERFORMANCE", "EDGE_STATUS_CHANGES",
                         "DATA_QUALITY_ISSUES", "RETRAINING_TRIGGERS",
                         "PROMOTION_CANDIDATES", "ROLLBACK_WARNINGS")

WEEKLY_REVIEW_SECTIONS = ("LONGER_WINDOWS", "EVENT_LEVEL_UNCERTAINTY",
                          "REGIME_STABILITY", "CAPITAL_EFFICIENCY", "DRIFT",
                          "MODEL_REDUNDANCY", "CANDIDATE_RETIREMENTS")

A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL = (
    "the daily report observes; the weekly review decides. One day of numbers "
    "is noise at this event count, and a model swapped on noise is a model "
    "swapped at random")

NO_HYPE_LANGUAGE = True


def daily_report(sections=None):
    s = {k: (sections or {}).get(k, NOT_IDENTIFIED)
         for k in DAILY_REPORT_SECTIONS}
    return {"REPORT": "DAILY_LEARNING_REPORT", "SECTIONS": s,
            "NO_HYPE_LANGUAGE": NO_HYPE_LANGUAGE,
            "MAY_REPLACE_A_MODEL": False,
            "A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL":
                A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL}


def weekly_review(sections=None):
    s = {k: (sections or {}).get(k, NOT_IDENTIFIED)
         for k in WEEKLY_REVIEW_SECTIONS}
    return {"REPORT": "WEEKLY_MODEL_REVIEW", "SECTIONS": s,
            "MAY_RECOMMEND_PROMOTION": True,
            "MAY_PROMOTE_AUTOMATICALLY": False,
            "AUTO_PRODUCTION_PROMOTION": "DISABLED"}


def describe():
    return {
        "EDGE_COMPONENTS": EDGE_COMPONENTS,
        "EDGE_STATUSES": EDGE_STATUSES,
        "EDGE_REQUIRED_FIELDS": EDGE_REQUIRED_FIELDS,
        "NO_BINARY_EDGE_FLAG": NO_BINARY_EDGE_FLAG,
        "THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS":
            THE_OBJECTIVE_IS_NOT_THAT_EDGE_GROWS,
        "ATTRIBUTION_POSITIVE": ATTRIBUTION_POSITIVE,
        "ATTRIBUTION_NEGATIVE": ATTRIBUTION_NEGATIVE,
        "NOT_ONE_BLENDED_ALPHA": NOT_ONE_BLENDED_ALPHA,
        "EDGE_INTERACTION_REQUIRES_N": EDGE_INTERACTION_REQUIRES_N,
        "EDGE_INTERACTION_STATUS": EDGE_INTERACTION_STATUS,
        "POWER_ANALYSIS_INPUTS": POWER_ANALYSIS_INPUTS,
        "WHY_A_ROUND_N_IS_NOT_SUFFICIENCY":
            WHY_A_ROUND_N_IS_NOT_SUFFICIENCY,
        "EARLIER_THRESHOLD_SAID": EARLIER_THRESHOLD_SAID,
        "LADDER_THRESHOLDS_ARE_A_SEPARATE_OPEN_QUESTION":
            LADDER_THRESHOLDS_ARE_A_SEPARATE_OPEN_QUESTION,
        "EXPERIMENT_RANK_FORMULA": EXPERIMENT_RANK_FORMULA,
        "MOAT_METRICS": MOAT_METRICS,
        "DASHBOARD_FIELDS": DASHBOARD_FIELDS,
        "CONFIDENCE_AREAS": CONFIDENCE_AREAS,
        "NO_ARBITRARY_CONFIDENCE_NUMBER": NO_ARBITRARY_CONFIDENCE_NUMBER,
        "FORBIDDEN_LANGUAGE": FORBIDDEN_LANGUAGE,
        "PERMITTED_LANGUAGE": PERMITTED_LANGUAGE,
        "DAILY_REPORT_SECTIONS": DAILY_REPORT_SECTIONS,
        "WEEKLY_REVIEW_SECTIONS": WEEKLY_REVIEW_SECTIONS,
        "A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL":
            A_DAILY_FLUCTUATION_CANNOT_REPLACE_A_MODEL,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
    }
