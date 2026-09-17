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

MIN_EVENTS_FOR_AN_INTERACTION = 200


def interaction(name, event_n=0):
    ok = event_n >= MIN_EVENTS_FOR_AN_INTERACTION
    return {"INTERACTION": name, "EVENT_N": event_n,
            "MAY_ESTIMATE": ok,
            "MIN_EVENTS_FOR_AN_INTERACTION": MIN_EVENTS_FOR_AN_INTERACTION,
            "EDGE_INTERACTION_REQUIRES_N": EDGE_INTERACTION_REQUIRES_N,
            "WHY_NOT": (None if ok else
                        "%d events cannot support an interaction estimate; "
                        "that is subgroup mining" % event_n)}


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
