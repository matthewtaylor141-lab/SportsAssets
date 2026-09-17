"""Sections A, B, K, L. The model zoo, the four targets, and the admission gates.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING IS TRAINED HERE. This module declares WHAT will be evaluated and under
WHAT rules, before the data it will be evaluated on exists.

THE OBJECTIVE, STATED ONCE
--------------------------
Not the fanciest model. THE SIMPLEST MODEL THAT CREATES THE HIGHEST REPEATABLE
FILL-CONDITIONED NET EV.

So the zoo is a LADDER, and a rung may only be admitted if it beats every
simpler rung on the SAME folds. A complicated model that ties a simple one has
lost: it costs more to fit, more to serve, and more to trust.

WHAT THE UPSTREAM AUDIT CONTRIBUTED, AND WHAT IT DID NOT
--------------------------------------------------------
Ideas, not code, and not constants. Each source below is recorded with its
licence and with the specific thing taken. A simulator-tuned constant from
somebody else's venue is not evidence about ours, so none is imported.
"""

NOT_IDENTIFIED = "NOT_IDENTIFIED"

NOTHING_IS_TRAINED_HERE = True
THE_OBJECTIVE = (
    "the SIMPLEST model that creates the highest REPEATABLE FILL-CONDITIONED "
    "NET EV -- not the best price predictor, and not the fanciest model")


# --- The upstream audit, by licence and by what was taken. -----------------

UPSTREAM_SOURCES = (
    {"SOURCE": "nkaz001/hftbacktest", "LICENCE": "MIT",
     "TAKEN": ("QUEUE_POSITION_SIMULATION", "LATENCY_SIMULATION",
               "L2_L3_REPLAY", "CUSTOM_FILL_MODELS", "MULTI_MARKET_STRUCTURE"),
     "NOT_TAKEN": ("CRYPTO_EXCHANGE_ASSUMPTIONS", "BINANCE_BYBIT_FEE_MODEL",
                   "TICK_AND_LOT_CONVENTIONS")},
    {"SOURCE": "spencerfletcher/market-maker", "LICENCE": "MIT",
     "TAKEN": ("EXACT_DECIMAL_VENUE_ARITHMETIC", "FEE_CREDIT_ROUNDING",
               "CONTENT_FRESHNESS_VS_SOCKET_LIVENESS", "QUEUE_ATTRIBUTION",
               "SHARED_REQUEST_BUDGET", "DURABLE_INTENT_RECONCILIATION",
               "FEED_DEGRADATION", "TEARDOWN_CERTIFICATION",
               "MARKOUT_QUEUE_MEASUREMENT_DISCIPLINE"),
     "NOT_TAKEN": ("ITS_PARAMETERS", "ITS_VENUE_SPECIFIC_ENDPOINTS")},
    {"SOURCE": "Stoikov microprice", "LICENCE": "METHODOLOGY_REFERENCE_ONLY",
     "TAKEN": ("MICROPRICE_METHODOLOGY",),
     "NOT_TAKEN": ("UNLICENSED_CODE",),
     "NOTE": "implemented from the published method, not copied"},
    {"SOURCE": "Cont/Kukanov/Stoikov order-flow imbalance",
     "LICENCE": "METHODOLOGY_REFERENCE_ONLY",
     "TAKEN": ("L1_OFI", "MULTI_LEVEL_OFI"),
     "NOT_TAKEN": ("UNLICENSED_CODE",),
     "NOTE": "implemented internally from the definition"},
    {"SOURCE": "octavi42/prediction-market-maker", "LICENCE": "MIT",
     "TAKEN": ("ASYMMETRIC_INVENTORY_SKEW_HYPOTHESIS",
               "ADVERSE_SELECTION_REGIME_DETECTION_HYPOTHESIS",
               "VOLATILITY_SPREAD_QUOTE_GATING_HYPOTHESIS",
               "BENIGN_FLOW_AWARE_QUOTE_SIZING_HYPOTHESIS"),
     "NOT_TAKEN": ("SIMULATOR_TUNED_CONSTANTS", "MONOPOLY_STRATEGY",
                   "14_OVER_PROB", "85_OVER_PROB"),
     "STATUS": "HYPOTHESES_ONLY"},
    {"SOURCE": "DeepLOB", "LICENCE": "METHODOLOGY_REFERENCE_ONLY",
     "TAKEN": ("SEQUENCE_MODEL_ARCHITECTURE_AS_A_LATER_CHALLENGER",),
     "STATUS": "NOT_ADMITTED_INSUFFICIENT_DATA"},
)

A_CONSTANT_FROM_ANOTHER_VENUE_IS_NOT_EVIDENCE = (
    "a constant tuned inside somebody else's simulator encodes THEIR venue's "
    "tick size, fee schedule, flow mix and latency. Importing it would import "
    "a conclusion we have not earned. Every such relationship is learned "
    "prospectively on BETTOR's own data or left NOT_IDENTIFIED")

FORBIDDEN_IMPORTS = ("SIMULATOR_TUNED_CONSTANTS", "MONOPOLY_STRATEGY",
                     "CRYPTO_EXCHANGE_ASSUMPTIONS", "UNLICENSED_CODE")


# --- Section A. The ladder. ------------------------------------------------

ZOO = (
    ("M0_NO_CHANGE", 0, "the null: the price does not move"),
    ("M1_MIDPOINT", 1, "current mid"),
    ("M2_SIMPLE_BOOK_IMBALANCE", 2, "size imbalance at the touch"),
    ("M3_STOIKOV_MICROPRICE", 3, "imbalance-weighted fair price"),
    ("M4_L1_OFI", 4, "top-of-book order-flow imbalance"),
    ("M5_MULTI_LEVEL_OFI", 5, "OFI across depth levels"),
    ("M6_REGULARIZED_LOGISTIC_OR_LINEAR_MODEL", 6, "penalised linear"),
    ("M7_GRADIENT_BOOSTED_MICROSTRUCTURE", 7, "GBM on the feature set"),
    ("M8_EXTERNAL_ODDS_PLUS_MICROSTRUCTURE", 8, "adds timestamped external"),
    ("M9_DEEPLOB", 9, "sequence model over the book"),
)

ZOO_NAMES = tuple(n for n, _, _ in ZOO)
COMPLEXITY_OF = {n: c for n, c, _ in ZOO}

SAME_FOLDS_RULE = (
    "every model is evaluated on EXACTLY the same folds, split by EVENT and "
    "chronologically. A model scored on a different split has not been "
    "compared, it has been described")

MUST_BEAT_SIMPLER = (
    "a model is ADMITTED only if it beats EVERY simpler rung on the same "
    "folds. A tie is a loss: the complicated model costs more to fit, serve "
    "and trust, so equal performance is a reason to keep the simple one")


def simpler_than(name):
    """Every rung a candidate must beat."""
    c = COMPLEXITY_OF.get(name)
    if c is None:
        return ()
    return tuple(n for n, cc, _ in ZOO if cc < c)


def admit(name, scores, lower_is_better=True, margin=0.0):
    """May this model be admitted? `scores` maps model name -> fold score.

    Fails closed: a missing simpler score is NOT a pass. If the ladder below
    was not evaluated, the comparison did not happen.
    """
    if name not in COMPLEXITY_OF:
        return {"MODEL": name, "ADMITTED": False,
                "WHY": "UNKNOWN_MODEL_NOT_IN_THE_DECLARED_ZOO",
                "DECLARED": ZOO_NAMES}
    mine = (scores or {}).get(name)
    if mine is None:
        return {"MODEL": name, "ADMITTED": False,
                "WHY": "NO_SCORE_FOR_THIS_MODEL"}
    beat, lost, missing = [], [], []
    for s in simpler_than(name):
        other = (scores or {}).get(s)
        if other is None:
            missing.append(s)
            continue
        better = ((mine < other - margin) if lower_is_better
                  else (mine > other + margin))
        (beat if better else lost).append(
            {"MODEL": s, "ITS_SCORE": other, "OUR_SCORE": mine})
    ok = not lost and not missing
    return {
        "MODEL": name,
        "SCORE": mine,
        "ADMITTED": ok,
        "BEATS": beat,
        "DOES_NOT_BEAT": lost,
        "SIMPLER_MODELS_NOT_EVALUATED": missing,
        "MUST_BEAT_SIMPLER": MUST_BEAT_SIMPLER,
        "SAME_FOLDS_RULE": SAME_FOLDS_RULE,
        "WHY_NOT": (None if ok else
                    ("it does not beat %d simpler model(s)" % len(lost)
                     if lost else
                     "%d simpler model(s) were never evaluated, so the "
                     "comparison did not happen" % len(missing))),
        "A_TIE_IS_A_LOSS": True,
    }


THE_NULL = "M0_NO_CHANGE"
THE_NULL_CANNOT_WIN_BY_VACUITY = (
    "M0_NO_CHANGE has no simpler rung to beat, so admit() passes it "
    "trivially. It is the thing others must beat, not a candidate champion. "
    "If nothing beats it, the honest answer is NOTHING_BEAT_THE_NULL -- which "
    "is a real and reportable outcome, not a win for M0")


def simplest_admitted(scores, lower_is_better=True, margin=0.0):
    """The objective's actual answer: the SIMPLEST model that survives.

    The null is excluded as a candidate. A model that beats nothing has not
    earned anything, and returning M0 as 'the champion' would dress a null
    result as a positive one.
    """
    for name, _, _ in ZOO:
        if name == THE_NULL:
            continue
        a = admit(name, scores, lower_is_better, margin)
        if a["ADMITTED"] and a["BEATS"]:
            return a
    return {"ADMITTED": False, "WHY": "NOTHING_BEAT_THE_NULL",
            "THE_NULL": THE_NULL,
            "THE_NULL_CANNOT_WIN_BY_VACUITY": THE_NULL_CANNOT_WIN_BY_VACUITY,
            "THIS_IS_A_REAL_RESULT": (
                "no microstructure model improved on 'the price does not "
                "move'. That is a finding about the market, reported as one"),
            "THE_OBJECTIVE": THE_OBJECTIVE}


# --- Section B. Four target systems, never one. ----------------------------

HORIZONS_S = (5, 30, 60, 300)

TARGET_SYSTEMS = {
    "PRICE_MOVE": {
        "TARGETS": tuple("MID_MOVE_%dS" % h for h in HORIZONS_S),
        "STATUS": "AWAITING_CAPTURE",
        "WHAT_IT_IS": "the observable mid's forward movement",
    },
    "TOXICITY": {
        "TARGETS": tuple("ADVERSE_MARKOUT_%dS" % h for h in HORIZONS_S),
        "STATUS": "MARKET_STATE_TOXICITY_ONLY",
        "WHAT_IT_IS": "expected adverse move after a hypothetical quote",
        "EVENTUALLY_CONDITIONAL_ON": "ACTUAL_FILL",
    },
    "FILL": {
        "TARGETS": tuple("P_FILL_%dS" % h for h in HORIZONS_S),
        "STATUS": NOT_IDENTIFIED,
        "BLOCKED_ON": "BETTOR_NATIVE_ORDER_EVIDENCE",
        "WHY": ("no BETTOR order has rested on this venue, so there is no "
                "evidence about whether one would have filled. This is a "
                "status, not a zero"),
    },
    "QUEUE": {
        "TARGETS": ("QUEUE_AHEAD", "QUEUE_DEPLETION", "QUEUE_ADVANCEMENT",
                    "EXPECTED_TIME_TO_FILL"),
        "STATUS": "DISTRIBUTIONAL_ONLY",
        "WHY": ("L2 does not expose per-order position. An exact queue index "
                "inferred from aggregate depth is fake precision, so these "
                "are reported as DISTRIBUTIONS with their assumptions named"),
    },
}

FOUR_SYSTEMS_ARE_NOT_ONE = (
    "predicting the mid, predicting adverse selection, predicting whether we "
    "fill, and estimating queue are four different questions with four "
    "different targets. Collapsing them is how a price-prediction result gets "
    "reported as a trading edge")

QUEUE_MAY_NOT_BE_FAKED = (
    "do not infer an exact queue position from L2 when the venue does not "
    "expose enough information. Report a distribution, and name the "
    "assumption that generated it")


def target_status(system):
    t = TARGET_SYSTEMS.get(system)
    if not t:
        return {"SYSTEM": system, "STATUS": "UNKNOWN_SYSTEM",
                "DECLARED": tuple(TARGET_SYSTEMS)}
    return dict(t, SYSTEM=system)


def may_train(system):
    """Fail closed. FILL may not be trained before native order evidence."""
    t = TARGET_SYSTEMS.get(system) or {}
    blocked = t.get("STATUS") == NOT_IDENTIFIED
    return {"SYSTEM": system, "MAY_TRAIN": not blocked,
            "STATUS": t.get("STATUS", "UNKNOWN_SYSTEM"),
            "BLOCKED_ON": t.get("BLOCKED_ON"),
            "WHY": t.get("WHY")}


# --- Section K. DeepLOB is a challenger, not a foundation. -----------------

DEEPLOB_STATUS = "NOT_ADMITTED_INSUFFICIENT_DATA"

DEEPLOB_ADMISSION_REQUIREMENTS = {
    "MIN_INDEPENDENT_EVENTS": 200,
    "MIN_EVENT_HOURS": 400,
    "MIN_BOOK_TRANSITIONS": 500000,
    "MIN_DEPTH_OBSERVATIONS": 2000000,
}

DEEPLOB_MUST_BEAT = ("M3_STOIKOV_MICROPRICE", "M4_L1_OFI",
                     "M5_MULTI_LEVEL_OFI", "M7_GRADIENT_BOOSTED_MICROSTRUCTURE")

DEEPLOB_MUST_BEAT_ON = ("CHRONOLOGICALLY_UNSEEN_EVENTS",
                        "ECONOMIC_MOVE_RELATIVE_TO_SPREAD")

CLASSIFICATION_ACCURACY_CANNOT_ADMIT_IT = (
    "F1 or accuracy alone cannot admit DeepLOB. A classifier can be right "
    "about direction on moves far smaller than the spread and earn nothing. "
    "The economic test is the admitting one")

DO_NOT_TRAIN_ON_THE_PILOT = (
    "a sequence model trained on a three-event pilot learns those three "
    "events. It is not a weak result, it is a memorised one")


def deeplob_gate(independent_events=0, event_hours=0, book_transitions=0,
                 depth_observations=0, beats=None, economic_test_passed=False):
    """May DeepLOB be trained, and then admitted? Both gates, fail closed."""
    R = DEEPLOB_ADMISSION_REQUIREMENTS
    have = {"MIN_INDEPENDENT_EVENTS": independent_events,
            "MIN_EVENT_HOURS": event_hours,
            "MIN_BOOK_TRANSITIONS": book_transitions,
            "MIN_DEPTH_OBSERVATIONS": depth_observations}
    short = {k: {"HAVE": have[k], "NEED": R[k]}
             for k in R if have[k] < R[k]}
    may_train_it = not short
    beats = set(beats or ())
    not_beaten = [m for m in DEEPLOB_MUST_BEAT if m not in beats]
    admitted = (may_train_it and not not_beaten and bool(economic_test_passed))
    return {
        "DEEPLOB_STATUS": (DEEPLOB_STATUS if not admitted else "ADMITTED"),
        "MAY_TRAIN": may_train_it,
        "DATA_SHORTFALL": short,
        "MUST_BEAT": DEEPLOB_MUST_BEAT,
        "NOT_BEATEN": not_beaten,
        "MUST_BEAT_ON": DEEPLOB_MUST_BEAT_ON,
        "ECONOMIC_TEST_PASSED": bool(economic_test_passed),
        "ADMITTED": admitted,
        "CLASSIFICATION_ACCURACY_CANNOT_ADMIT_IT":
            CLASSIFICATION_ACCURACY_CANNOT_ADMIT_IT,
        "DO_NOT_TRAIN_ON_THE_PILOT": DO_NOT_TRAIN_ON_THE_PILOT,
    }


# --- Section L. No RL yet. -------------------------------------------------

RL_STATUS = "NOT_ELIGIBLE"

RL_SYSTEMS_NOT_DEPLOYED = ("AlphaTrade", "JAX_LOB_RL", "Spooner_RL",
                           "ABIDES_POLICY_OPTIMIZATION")

WHY_NOT_RL_YET = (
    "RL over an UNCALIBRATED simulator learns the simulator's defects. It "
    "will find the fill model's seams, not the market's")

RL_PRECONDITIONS = ("P_FILL_IDENTIFIED", "LATENCY_MEASURED",
                    "QUEUE_MODEL_CALIBRATED",
                    "SIMULATED_FILLS_REPRODUCE_REAL_BETTOR_FILLS")


def rl_gate(p_fill_identified=False, latency_measured=False,
            queue_model_calibrated=False, sim_reproduces_real=False):
    met = {
        "P_FILL_IDENTIFIED": bool(p_fill_identified),
        "LATENCY_MEASURED": bool(latency_measured),
        "QUEUE_MODEL_CALIBRATED": bool(queue_model_calibrated),
        "SIMULATED_FILLS_REPRODUCE_REAL_BETTOR_FILLS": bool(sim_reproduces_real),
    }
    ok = all(met.values())
    return {"RL_STATUS": "ELIGIBLE" if ok else RL_STATUS,
            "PRECONDITIONS": met,
            "BLOCKED_BY": [k for k, v in met.items() if not v],
            "MAY_DEPLOY_RL": ok,
            "WHY_NOT_RL_YET": WHY_NOT_RL_YET,
            "NOT_DEPLOYED": RL_SYSTEMS_NOT_DEPLOYED}


def describe():
    return {
        "THE_OBJECTIVE": THE_OBJECTIVE,
        "NOTHING_IS_TRAINED_HERE": NOTHING_IS_TRAINED_HERE,
        "UPSTREAM_SOURCES": [dict(s) for s in UPSTREAM_SOURCES],
        "A_CONSTANT_FROM_ANOTHER_VENUE_IS_NOT_EVIDENCE":
            A_CONSTANT_FROM_ANOTHER_VENUE_IS_NOT_EVIDENCE,
        "FORBIDDEN_IMPORTS": FORBIDDEN_IMPORTS,
        "ZOO": ZOO_NAMES,
        "SAME_FOLDS_RULE": SAME_FOLDS_RULE,
        "MUST_BEAT_SIMPLER": MUST_BEAT_SIMPLER,
        "TARGET_SYSTEMS": {k: dict(v) for k, v in TARGET_SYSTEMS.items()},
        "FOUR_SYSTEMS_ARE_NOT_ONE": FOUR_SYSTEMS_ARE_NOT_ONE,
        "QUEUE_MAY_NOT_BE_FAKED": QUEUE_MAY_NOT_BE_FAKED,
        "DEEPLOB_STATUS": DEEPLOB_STATUS,
        "DEEPLOB_ADMISSION_REQUIREMENTS": dict(DEEPLOB_ADMISSION_REQUIREMENTS),
        "RL_STATUS": RL_STATUS,
        "RL_PRECONDITIONS": RL_PRECONDITIONS,
        "WHY_NOT_RL_YET": WHY_NOT_RL_YET,
    }
