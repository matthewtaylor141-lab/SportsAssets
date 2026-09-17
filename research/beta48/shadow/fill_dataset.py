"""Sections 10, 11, 12. The future fill dataset, matching, and queue.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NO ORDER IS PLACED. SCHEMA AND INTERFACE ONLY.

WHY BUILD THE SCHEMA BEFORE THE ORDERS
--------------------------------------
The fields a passive order's record must carry are decided by what the
analysis needs, not by what the venue happens to return. Deciding them
afterwards means discovering that the one field which would have identified
P_FILL was never written down. Submit and ack times must be separate; cancel
request and cancel ack must be separate; first and last fill must be separate.
Each pair looks redundant until latency is the question.

WHAT STAYS UNAVAILABLE
----------------------
    P_FILL_STATUS = NOT_IDENTIFIED

until real BETTOR passive orders exist. Nothing here estimates it, and nothing
here converts an absence of orders into a fill probability of zero.
"""

import datetime

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MISSING = "MISSING"

NO_ORDER_IS_PLACED = True
ORDER_PATH_EXISTS = False
MIRROR_LIVE = False

SCHEMA_VERSION = "1"
APPEND_ONLY = True


# --- Section 10. The order record. -----------------------------------------

ORDER_FIELDS = (
    "ORDER_ID", "DECISION_ID",
    "SUBMIT_TIMESTAMP", "ACK_TIMESTAMP",
    "SIDE", "PRICE", "SIZE",
    "QUEUE_ESTIMATE_AT_SUBMIT", "QUEUE_DISTRIBUTION_PARAMETERS",
    "BEST_BID_AT_SUBMIT", "BEST_ASK_AT_SUBMIT",
    "CANCEL_REQUEST_TIMESTAMP", "CANCEL_ACK_TIMESTAMP",
    "FIRST_FILL_TIMESTAMP", "LAST_FILL_TIMESTAMP",
    "FILLED_SIZE", "REMAINING_SIZE",
    "FINAL_STATUS",
    "MARKOUT_5S", "MARKOUT_30S", "MARKOUT_60S", "MARKOUT_300S",
)

FINAL_STATUSES = ("FILLED", "PARTIALLY_FILLED", "CANCELLED", "REJECTED",
                  "EXPIRED", "UNKNOWN")

UNKNOWN_IS_NOT_NOT_FILLED = (
    "FINAL_STATUS = UNKNOWN is not CANCELLED and is not NOT_FILLED. An order "
    "whose fate we failed to read is missing evidence, and counting it as a "
    "non-fill would bias every fill rate downward")

PAIRED_TIMESTAMPS_ARE_SEPARATE = (
    "submit/ack, cancel-request/cancel-ack and first-fill/last-fill each look "
    "redundant until latency is the question. Collapsing a pair destroys the "
    "only measurement that could calibrate the simulator")

MARKOUT_HORIZONS_S = (5, 30, 60, 300)


def order_record(order_id, decision_id, side, price, size,
                 submit_timestamp=None, **kw):
    """One append-only order object. Unknown fields are MISSING, never 0."""
    rec = {f: MISSING for f in ORDER_FIELDS}
    rec.update({
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "ORDER_ID": order_id, "DECISION_ID": decision_id,
        "SIDE": side, "PRICE": price, "SIZE": size,
        "SUBMIT_TIMESTAMP": submit_timestamp or MISSING,
        "FINAL_STATUS": kw.get("FINAL_STATUS", "UNKNOWN"),
    })
    for k, v in kw.items():
        if k in ORDER_FIELDS and v is not None:
            rec[k] = v
    rec["THIS_IS_A_SCHEMA_NO_ORDER_WAS_PLACED"] = True
    return rec


def latency(rec):
    """The measurements the paired timestamps exist for."""
    def _d(a, b):
        ta, tb = _parse(rec.get(a)), _parse(rec.get(b))
        if ta is None or tb is None:
            return NOT_IDENTIFIED
        return round((tb - ta).total_seconds(), 6)
    return {
        "SUBMIT_TO_ACK_S": _d("SUBMIT_TIMESTAMP", "ACK_TIMESTAMP"),
        "CANCEL_REQUEST_TO_ACK_S": _d("CANCEL_REQUEST_TIMESTAMP",
                                      "CANCEL_ACK_TIMESTAMP"),
        "ACK_TO_FIRST_FILL_S": _d("ACK_TIMESTAMP", "FIRST_FILL_TIMESTAMP"),
        "FIRST_TO_LAST_FILL_S": _d("FIRST_FILL_TIMESTAMP",
                                   "LAST_FILL_TIMESTAMP"),
        "PAIRED_TIMESTAMPS_ARE_SEPARATE": PAIRED_TIMESTAMPS_ARE_SEPARATE,
    }


def _parse(ts):
    if ts in (None, MISSING, NOT_IDENTIFIED):
        return None
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def fill_outcome(rec):
    """Did it fill? UNKNOWN stays UNKNOWN."""
    st = rec.get("FINAL_STATUS", "UNKNOWN")
    if st == "UNKNOWN":
        return {"FILLED": NOT_IDENTIFIED, "FINAL_STATUS": st,
                "UNKNOWN_IS_NOT_NOT_FILLED": UNKNOWN_IS_NOT_NOT_FILLED}
    filled = rec.get("FILLED_SIZE")
    try:
        filled = float(filled)
    except (TypeError, ValueError):
        return {"FILLED": NOT_IDENTIFIED, "FINAL_STATUS": st,
                "WHY": "FILLED_SIZE not readable"}
    return {"FILLED": filled > 0, "FILLED_SIZE": filled,
            "FINAL_STATUS": st,
            "PARTIAL": st == "PARTIALLY_FILLED"}


# --- Section 12. Queue and fill, distributionally. -------------------------

P_FILL_STATUS = NOT_IDENTIFIED
P_FILL_TARGETS = tuple("P_FILL_%dS" % h for h in MARKOUT_HORIZONS_S)

NO_FAKE_QUEUE_POSITION = (
    "L2 publishes aggregate size at a level, not our position within it. An "
    "exact queue index derived from aggregate depth is a number with no "
    "referent. The interface returns a DISTRIBUTION and names the assumption "
    "that produced it")

QUEUE_ASSUMPTIONS = ("UNIFORM_WITHIN_LEVEL", "ARRIVED_LAST",
                     "ARRIVED_FIRST", "MEASURED_FROM_ORDER_EVIDENCE")

QUEUE_OUTPUTS = ("QUEUE_AHEAD", "QUEUE_DEPLETION", "QUEUE_ADVANCEMENT",
                 "EXPECTED_TIME_TO_FILL", "EXPECTED_PARTIAL_FILL_QTY")


def queue_distribution(displayed_size_at_level=None,
                       assumption="UNIFORM_WITHIN_LEVEL"):
    """P_QUEUE_AHEAD(q) as a distribution, with its assumption attached.

    Under UNIFORM_WITHIN_LEVEL the quantity ahead of a newly-inserted order is
    treated as uniform on [0, displayed]. That is an ASSUMPTION about how
    resting orders are ordered, not an observation, and it is named.
    """
    if assumption not in QUEUE_ASSUMPTIONS:
        return {"STATUS": "UNKNOWN_ASSUMPTION", "DECLARED": QUEUE_ASSUMPTIONS}
    try:
        d = float(displayed_size_at_level)
    except (TypeError, ValueError):
        return {"QUEUE_AHEAD": NOT_IDENTIFIED, "STATUS": "NO_DISPLAYED_SIZE",
                "NO_FAKE_QUEUE_POSITION": NO_FAKE_QUEUE_POSITION}
    if d < 0:
        return {"QUEUE_AHEAD": NOT_IDENTIFIED, "STATUS": "NEGATIVE_SIZE"}
    if assumption == "ARRIVED_LAST":
        dist = {"KIND": "POINT_MASS", "AT": d}
    elif assumption == "ARRIVED_FIRST":
        dist = {"KIND": "POINT_MASS", "AT": 0.0}
    elif assumption == "UNIFORM_WITHIN_LEVEL":
        dist = {"KIND": "UNIFORM", "LOW": 0.0, "HIGH": d,
                "MEAN": round(d / 2.0, 10)}
    else:
        return {"QUEUE_AHEAD": NOT_IDENTIFIED,
                "STATUS": "NO_ORDER_EVIDENCE_EXISTS",
                "WHY": "MEASURED_FROM_ORDER_EVIDENCE needs real orders"}
    return {
        "QUEUE_AHEAD_DISTRIBUTION": dist,
        "DISPLAYED_SIZE_AT_LEVEL": d,
        "ASSUMPTION": assumption,
        "IS_AN_ASSUMPTION_NOT_AN_OBSERVATION": True,
        "NO_FAKE_QUEUE_POSITION": NO_FAKE_QUEUE_POSITION,
        "EXACT_QUEUE_POSITION": NOT_IDENTIFIED,
    }


def p_fill(state=None, horizon_s=None, order_evidence=None):
    """P_FILL. NOT_IDENTIFIED until real passive-order evidence exists."""
    if not order_evidence:
        return {"P_FILL": NOT_IDENTIFIED,
                "P_FILL_STATUS": P_FILL_STATUS,
                "HORIZON_S": horizon_s if horizon_s is not None
                else NOT_IDENTIFIED,
                "EXPECTED_TIME_TO_FILL": NOT_IDENTIFIED,
                "EXPECTED_PARTIAL_FILL_QTY": NOT_IDENTIFIED,
                "WHY": ("no BETTOR passive order has rested on this venue. "
                        "An absence of orders is not a fill probability of "
                        "zero"),
                "BLOCKED_ON": "BETTOR_NATIVE_ORDER_EVIDENCE"}
    return {"P_FILL": NOT_IDENTIFIED, "P_FILL_STATUS": "AWAITING_MODEL",
            "WHY": "order evidence supplied but no fill model is fitted here",
            "NOTHING_IS_TRAINED_HERE": True}


# --- Section 11. The matched fill-selection analysis. ----------------------

MATCH_CONTROLS = ("SIDE", "PRICE", "SPREAD", "DEPTH", "IMBALANCE", "OFI",
                  "VOLATILITY", "TIME_TO_EVENT", "MARKET_FAMILY",
                  "QUOTE_AGE", "EXTERNAL_MARKET_STATE",
                  "CROSS_MARKET_RESIDUAL")

FILL_SELECTION_OUTCOMES = ("MORE_ADVERSE", "NO_MATERIAL_DIFFERENCE",
                           "LESS_ADVERSE")
NO_DIRECTION_ASSUMED = True

SIGN_CONVENTION = ("FILL_SELECTION_MARKOUT_DELTA_h = "
                   "MARKOUT_FILLED_h - MATCHED_COUNTERFACTUAL_MARKOUT_h")
SIGN_MEANS = {
    "NEGATIVE": "MORE_ADVERSE", "ZERO": "NO_MATERIAL_DIFFERENCE",
    "POSITIVE": "LESS_ADVERSE",
}

UNMATCHED_IS_NOT_THE_TEST = (
    "comparing filled states to arbitrary quote-present states measures the "
    "difference between their states and attributes it to selection")


def matched_pairs(filled, quote_present, controls=MATCH_CONTROLS,
                  tolerance=None):
    """Pair each filled state with a comparable non-filled state.

    Exact match on categorical controls, within-tolerance on numeric ones.
    A filled state with no comparator is REPORTED, not dropped quietly.
    """
    tolerance = tolerance or {}
    pairs, unmatched = [], []
    used = set()
    for f in filled or ():
        best, best_dist = None, None
        for i, q in enumerate(quote_present or ()):
            if i in used:
                continue
            ok, dist = True, 0.0
            for c in controls:
                a, b = f.get(c), q.get(c)
                if a is None or b is None:
                    ok = False
                    break
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    tol = tolerance.get(c)
                    if tol is None or abs(a - b) > tol:
                        ok = False
                        break
                    dist += abs(a - b)
                elif a != b:
                    ok = False
                    break
            if ok and (best_dist is None or dist < best_dist):
                best, best_dist, best_i = q, dist, i
        if best is None:
            unmatched.append(f)
        else:
            used.add(best_i)
            pairs.append((f, best))
    return {"PAIRS": pairs, "MATCHED": len(pairs),
            "UNMATCHED_FILLED_STATES": len(unmatched),
            "CONTROLS": tuple(controls),
            "UNMATCHED_IS_NOT_THE_TEST": UNMATCHED_IS_NOT_THE_TEST,
            "UNMATCHED_ARE_REPORTED_NOT_DROPPED": True}


def selection_delta(pairs, horizon_s, markout_key=None):
    """FILL_SELECTION_MARKOUT_DELTA_h over matched pairs."""
    key = markout_key or ("MARKOUT_%dS" % horizon_s)
    ds = []
    for f, q in pairs or ():
        a, b = f.get(key), q.get(key)
        if a is None or b is None:
            continue
        ds.append(float(a) - float(b))
    if not ds:
        return {"HORIZON_S": horizon_s,
                "FILL_SELECTION_MARKOUT_DELTA": NOT_IDENTIFIED,
                "FILL_SELECTION_EFFECT": NOT_IDENTIFIED,
                "PAIRS": 0, "SIGN_CONVENTION": SIGN_CONVENTION,
                "WHY": "no matched pair carried this markout"}
    mean = sum(ds) / len(ds)
    effect = ("NO_MATERIAL_DIFFERENCE" if mean == 0 else
              ("MORE_ADVERSE" if mean < 0 else "LESS_ADVERSE"))
    return {"HORIZON_S": horizon_s,
            "FILL_SELECTION_MARKOUT_DELTA": round(mean, 10),
            "FILL_SELECTION_EFFECT": effect,
            "PAIRS": len(ds),
            "SIGN_CONVENTION": SIGN_CONVENTION,
            "SIGN_MEANS": dict(SIGN_MEANS),
            "ADMISSIBLE_OUTCOMES": FILL_SELECTION_OUTCOMES,
            "NO_DIRECTION_ASSUMED": NO_DIRECTION_ASSUMED}


# --- Section 14. The quote-size experiment (prepared, not run). ------------

SIZE_BUCKET_METRIC = "QUOTE_SIZE / ESTIMATED_BENIGN_FLOW_CAPACITY"
SIZE_BUCKET_MEASURES = ("FILL_RATE", "FILL_LATENCY", "PARTIAL_FILL_RATE",
                        "MARKOUT", "ADVERSE_SELECTION", "CAPITAL_OCCUPANCY")
LARGER_IS_NOT_ASSUMED_BETTER = (
    "the hypothesis is that size above benign capacity markouts worse. It is "
    "a hypothesis. The experiment may equally find no relationship")


def size_experiment(rows=None):
    """Prepared. Refuses while benign capacity is NOT_IDENTIFIED."""
    return {
        "STATUS": "PREPARED_NOT_RUN",
        "NORMALISED_BY": SIZE_BUCKET_METRIC,
        "MEASURES": SIZE_BUCKET_MEASURES,
        "BLOCKED_ON": ("BENIGN_FLOW_CAPACITY = NOT_IDENTIFIED and "
                       "no real fills exist"),
        "LARGER_IS_NOT_ASSUMED_BETTER": LARGER_IS_NOT_ASSUMED_BETTER,
    }


def describe():
    return {
        "SCHEMA_VERSION": SCHEMA_VERSION,
        "ORDER_FIELDS": ORDER_FIELDS,
        "FINAL_STATUSES": FINAL_STATUSES,
        "UNKNOWN_IS_NOT_NOT_FILLED": UNKNOWN_IS_NOT_NOT_FILLED,
        "PAIRED_TIMESTAMPS_ARE_SEPARATE": PAIRED_TIMESTAMPS_ARE_SEPARATE,
        "P_FILL_STATUS": P_FILL_STATUS,
        "P_FILL_TARGETS": P_FILL_TARGETS,
        "NO_FAKE_QUEUE_POSITION": NO_FAKE_QUEUE_POSITION,
        "QUEUE_ASSUMPTIONS": QUEUE_ASSUMPTIONS,
        "QUEUE_OUTPUTS": QUEUE_OUTPUTS,
        "MATCH_CONTROLS": MATCH_CONTROLS,
        "FILL_SELECTION_OUTCOMES": FILL_SELECTION_OUTCOMES,
        "NO_DIRECTION_ASSUMED": NO_DIRECTION_ASSUMED,
        "SIGN_CONVENTION": SIGN_CONVENTION,
        "UNMATCHED_IS_NOT_THE_TEST": UNMATCHED_IS_NOT_THE_TEST,
        "NO_ORDER_IS_PLACED": NO_ORDER_IS_PLACED,
        "ORDER_PATH_EXISTS": ORDER_PATH_EXISTS,
    }
