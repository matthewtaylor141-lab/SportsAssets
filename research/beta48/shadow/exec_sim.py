"""Sections C and D. BETTOR_EXECUTION_SIMULATOR, and the gate that trusts it.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
THIS MODULE CONTACTS NOTHING AND PLACES NOTHING.

WHAT IS PORTED, AND WHAT IS NOT
-------------------------------
The CONCEPTS come from hftbacktest (MIT): queue-position simulation, latency
simulation, replay, custom fill models. The EXCHANGE does not. Every venue
behaviour below must be sourced from one of exactly two places:

    VENUE_RULE        -- a documented rule of this venue
    BETTOR_MEASURED   -- a behaviour BETTOR has measured on this venue

An assumption carried over from a crypto exchange is neither, and is refused
by name. A binary event contract is not a perpetual future: it settles to 0 or
1, its variance collapses toward settlement, and its YES and NO legs are the
same claim seen from two sides.

SECTION D: A SIMULATOR IS NOT TRUSTED BECAUSE IT IS SOPHISTICATED
-----------------------------------------------------------------
It is trusted when its output matches reality on the same period. Until those
comparisons exist, SIMULATOR_STATUS = UNVALIDATED, and an unvalidated
simulator's fill rate is a hypothesis with a number attached.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

THIS_MODULE_CONTACTS_NOTHING = True
ORDER_PATH_EXISTS = False
MIRROR_LIVE = False

SIMULATOR_NAME = "BETTOR_EXECUTION_SIMULATOR"
SIMULATOR_STATUS = "UNVALIDATED"


# --- Section C. Every behaviour needs a source. ----------------------------

SOURCE_VENUE_RULE = "VENUE_RULE"
SOURCE_BETTOR_MEASURED = "BETTOR_MEASURED"
ALLOWED_SOURCES = (SOURCE_VENUE_RULE, SOURCE_BETTOR_MEASURED)

FORBIDDEN_SOURCES = ("BINANCE_ASSUMPTION", "BYBIT_ASSUMPTION",
                     "CRYPTO_EXCHANGE_DEFAULT", "SIMULATOR_TUNED_CONSTANT",
                     "PLAUSIBLE_GUESS")

WHY_A_GUESS_IS_REFUSED = (
    "a plausible default is the most dangerous input a simulator can take: it "
    "produces a confident number that nothing measured supports, and the "
    "number then propagates into every downstream EV")

REQUIRED_CAPABILITIES = (
    "HISTORICAL_EVENT_REPLAY",
    "FEED_LATENCY",
    "ORDER_REQUEST_LATENCY",
    "CANCEL_LATENCY",
    "QUEUE_MODEL",
    "PARTIAL_FILLS",
    "POST_ONLY_BEHAVIOR",
    "DYNAMIC_TICK_SIZES",
    "FEES_AND_REBATES",
    "MARKET_SUSPENSION",
    "SETTLEMENT",
    "YES_NO_COMPLEMENT_SEMANTICS",
    "MULTI_MARKET_INVENTORY",
)

# Current sourcing state. Everything a venue rule or a BETTOR measurement has
# not yet established stays NOT_IDENTIFIED -- never a default.
CAPABILITY_SOURCE = {
    "HISTORICAL_EVENT_REPLAY": {"SOURCE": SOURCE_BETTOR_MEASURED,
                                "STATUS": "AWAITING_CAPTURE"},
    "FEED_LATENCY": {"SOURCE": NOT_IDENTIFIED, "STATUS": "NOT_MEASURED"},
    "ORDER_REQUEST_LATENCY": {"SOURCE": NOT_IDENTIFIED,
                              "STATUS": "NOT_MEASURED",
                              "WHY": "no BETTOR order has been placed"},
    "CANCEL_LATENCY": {"SOURCE": NOT_IDENTIFIED, "STATUS": "NOT_MEASURED"},
    "QUEUE_MODEL": {"SOURCE": NOT_IDENTIFIED, "STATUS": "NOT_CALIBRATED"},
    "PARTIAL_FILLS": {"SOURCE": NOT_IDENTIFIED, "STATUS": "NOT_MEASURED"},
    "POST_ONLY_BEHAVIOR": {"SOURCE": SOURCE_BETTOR_MEASURED,
                           "STATUS": "PARTIALLY_MEASURED",
                           "NOTE": "post-only rejections observed and named"},
    "DYNAMIC_TICK_SIZES": {"SOURCE": SOURCE_VENUE_RULE,
                           "STATUS": "NEEDS_RESTATEMENT_FROM_VENUE_RULES"},
    "FEES_AND_REBATES": {"SOURCE": SOURCE_VENUE_RULE,
                         "STATUS": "NEEDS_RESTATEMENT_FROM_VENUE_RULES"},
    "MARKET_SUSPENSION": {"SOURCE": SOURCE_BETTOR_MEASURED,
                          "STATUS": "OBSERVED_NOT_CHARACTERISED"},
    "SETTLEMENT": {"SOURCE": SOURCE_VENUE_RULE, "STATUS": "KNOWN"},
    "YES_NO_COMPLEMENT_SEMANTICS": {"SOURCE": SOURCE_VENUE_RULE,
                                    "STATUS": "KNOWN"},
    "MULTI_MARKET_INVENTORY": {"SOURCE": SOURCE_BETTOR_MEASURED,
                               "STATUS": "AWAITING_CAPTURE"},
}

NOT_A_PERPETUAL_FUTURE = (
    "a binary event contract settles to 0 or 1, its variance collapses toward "
    "settlement rather than growing with horizon, and its YES and NO legs are "
    "one claim seen from two sides. Diffusive market-making assumptions "
    "imported wholesale are wrong here, not merely approximate")


def declare_behaviour(name, source, value=None, evidence=None):
    """Register one venue behaviour. A forbidden or absent source is refused."""
    if source in FORBIDDEN_SOURCES:
        return {"BEHAVIOUR": name, "ACCEPTED": False,
                "REASON": "FORBIDDEN_SOURCE", "SOURCE": source,
                "WHY_A_GUESS_IS_REFUSED": WHY_A_GUESS_IS_REFUSED}
    if source not in ALLOWED_SOURCES:
        return {"BEHAVIOUR": name, "ACCEPTED": False,
                "REASON": "SOURCE_NOT_ALLOWED", "SOURCE": source,
                "ALLOWED_SOURCES": ALLOWED_SOURCES}
    if value is None or not evidence:
        return {"BEHAVIOUR": name, "ACCEPTED": False,
                "REASON": "NO_VALUE_OR_NO_EVIDENCE",
                "VALUE": NOT_IDENTIFIED}
    return {"BEHAVIOUR": name, "ACCEPTED": True, "SOURCE": source,
            "VALUE": value, "EVIDENCE": evidence}


def readiness():
    """What the simulator can honestly model today."""
    ready, missing = [], []
    for cap in REQUIRED_CAPABILITIES:
        s = CAPABILITY_SOURCE.get(cap, {})
        (ready if s.get("SOURCE") in ALLOWED_SOURCES
         and s.get("STATUS") in ("KNOWN", "PARTIALLY_MEASURED")
         else missing).append(cap)
    return {
        "SIMULATOR_NAME": SIMULATOR_NAME,
        "SIMULATOR_STATUS": SIMULATOR_STATUS,
        "CAPABILITIES_SOURCED": ready,
        "CAPABILITIES_NOT_SOURCED": missing,
        "MAY_PRODUCE_TRUSTED_FILLS": False,
        "WHY": ("the simulator may be BUILT before these are measured, but "
                "its output may not be believed until section D's "
                "comparisons align"),
        "NOT_A_PERPETUAL_FUTURE": NOT_A_PERPETUAL_FUTURE,
    }


# --- Section D. Calibration against reality. -------------------------------

CALIBRATION_PAIRS = (
    ("SIMULATED_FILL_RATE", "REAL_BETTOR_FILL_RATE"),
    ("SIMULATED_FILL_LATENCY", "REAL_FILL_LATENCY"),
    ("SIMULATED_MARKOUT", "REAL_MARKOUT"),
    ("SIMULATED_QUEUE_DEPLETION", "OBSERVED_QUEUE_BEHAVIOR"),
)

SAME_PERIOD_RULE = (
    "the comparison is on IDENTICAL time periods. A simulator matching an "
    "average over a different window has matched a coincidence")

SOPHISTICATION_IS_NOT_TRUST = (
    "a simulator does not become trusted because it models more things. It "
    "becomes trusted when its output matches measured reality on the same "
    "period, and stays UNVALIDATED until then")

# Tolerances declared before any comparison exists.
CALIBRATION_TOLERANCE = {
    "SIMULATED_FILL_RATE": 0.20,        # relative
    "SIMULATED_FILL_LATENCY": 0.30,
    "SIMULATED_MARKOUT": 0.25,
    "SIMULATED_QUEUE_DEPLETION": 0.30,
}


def calibration(observed=None, same_period=False):
    """Does the simulator reproduce reality? Fails closed on missing pairs.

    `observed` maps the simulated name -> {"SIM": x, "REAL": y}.
    """
    observed = observed or {}
    rows, aligned, unmeasured = [], [], []
    for sim_name, real_name in CALIBRATION_PAIRS:
        pair = observed.get(sim_name)
        if not pair or pair.get("SIM") is None or pair.get("REAL") is None:
            unmeasured.append(sim_name)
            rows.append({"SIMULATED": sim_name, "REAL": real_name,
                         "STATUS": NOT_IDENTIFIED})
            continue
        sim, real = float(pair["SIM"]), float(pair["REAL"])
        tol = CALIBRATION_TOLERANCE[sim_name]
        denom = abs(real) if real else None
        rel = (abs(sim - real) / denom) if denom else None
        ok = rel is not None and rel <= tol
        rows.append({"SIMULATED": sim_name, "REAL": real_name,
                     "SIM": sim, "REAL_VALUE": real,
                     "RELATIVE_ERROR": (round(rel, 5) if rel is not None
                                        else NOT_IDENTIFIED),
                     "TOLERANCE": tol, "ALIGNED": ok})
        if ok:
            aligned.append(sim_name)
    validated = (not unmeasured
                 and len(aligned) == len(CALIBRATION_PAIRS)
                 and bool(same_period))
    return {
        "SIMULATOR_STATUS": "CALIBRATED" if validated else SIMULATOR_STATUS,
        "COMPARISONS": rows,
        "ALIGNED": aligned,
        "NOT_MEASURED": unmeasured,
        "SAME_PERIOD": bool(same_period),
        "SAME_PERIOD_RULE": SAME_PERIOD_RULE,
        "SIMULATOR_MAY_BE_TRUSTED": validated,
        "SOPHISTICATION_IS_NOT_TRUST": SOPHISTICATION_IS_NOT_TRUST,
        "WHY_NOT": (None if validated else
                    ("no real BETTOR execution data exists to compare against"
                     if len(unmeasured) == len(CALIBRATION_PAIRS)
                     else "not every comparison aligns within its tolerance")),
    }


def describe():
    return {
        "SIMULATOR_NAME": SIMULATOR_NAME,
        "SIMULATOR_STATUS": SIMULATOR_STATUS,
        "REQUIRED_CAPABILITIES": REQUIRED_CAPABILITIES,
        "ALLOWED_SOURCES": ALLOWED_SOURCES,
        "FORBIDDEN_SOURCES": FORBIDDEN_SOURCES,
        "WHY_A_GUESS_IS_REFUSED": WHY_A_GUESS_IS_REFUSED,
        "NOT_A_PERPETUAL_FUTURE": NOT_A_PERPETUAL_FUTURE,
        "CALIBRATION_PAIRS": CALIBRATION_PAIRS,
        "CALIBRATION_TOLERANCE": dict(CALIBRATION_TOLERANCE),
        "SAME_PERIOD_RULE": SAME_PERIOD_RULE,
        "SOPHISTICATION_IS_NOT_TRUST": SOPHISTICATION_IS_NOT_TRUST,
        "ORDER_PATH_EXISTS": ORDER_PATH_EXISTS,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
