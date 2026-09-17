"""Contracts and pre-registrations: what we will need, and what we promise now.

Directive sections 5, 7, 8, 10, 15, 16 and 17.

Everything here is written BEFORE the data that would judge it exists. That is
the point. A data contract defined after the data arrives is shaped by the
data; a regime named after the result is a subgroup mined out of noise; an
evidence threshold chosen after the confidence interval is seen is not a
threshold at all.
"""

from __future__ import annotations

import math

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ===========================================================================
# SECTION 10: THE EVIDENCE LADDER, AND WHAT THE CURRENT SAMPLE CAN SEE
# ===========================================================================
#
# The honest reading of the last two reports is not "no signal". It is that
# the experiment could not have found one. Measured on the 67 evaluation
# events, the event-level paired log-loss difference between the market and a
# market+challenger blend has
#
#     standard deviation  = 0.1680   (per event)
#
# From that, the number of INDEPENDENT EVENTS needed to detect an improvement
# of a given size, two-sided at 5% with 80% power, and separately the number
# needed for a 95% interval no wider than the effect itself:

# --- SUPERSEDED, and the reason is recorded rather than the number quietly
# replaced. ------------------------------------------------------------------
SUPERSEDED_PAIRED_EVENT_SD = 0.1680
SUPERSEDED_LADDER = {
    0.002: {"EVENTS_FOR_80_PCT_POWER": 55386, "EVENTS_FOR_95_PCT_CI": 27109},
    0.005: {"EVENTS_FOR_80_PCT_POWER": 8862, "EVENTS_FOR_95_PCT_CI": 4338},
    0.010: {"EVENTS_FOR_80_PCT_POWER": 2216, "EVENTS_FOR_95_PCT_CI": 1085},
    0.020: {"EVENTS_FOR_80_PCT_POWER": 554, "EVENTS_FOR_95_PCT_CI": 272},
}
WHY_THE_OLD_LADDER_WAS_WRONG = (
    "0.1680 was the SD of the per-event difference between the market and a "
    "50/50 BLEND of the market with the V2 champion. A half-weight blend sits "
    "much closer to the market than the challenger does, so its differences "
    "are much smaller, and the ladder built on them understated the "
    "requirement roughly fourfold. The contrast being tested is the "
    "challenger against B0, so the SD must be of THAT difference. Section 21 "
    "forced the object to be computed from the real paired differences and "
    "the error surfaced immediately."
)

# --- Section 21. Measured from the real paired event-level differences. ------
# Two different contrasts, two different ladders, and conflating them was the
# whole of the error above.
#
#   STANDALONE: can the challenger, on its own, be distinguished from B0?
#   INCREMENTAL: does ADDING the challenger to the market improve the blend?
#
# The second is the question section 14 asks, and it is far better powered,
# because a stacked blend differs from the market only by the small amount the
# fitted coefficient lets the challenger move it.

PAIRED_EVENT_SD_STANDALONE = {
    "P_V2_B7": 0.3392, "P_V3_B4": 0.3416, "P_V3_GBM": 0.3475,
}
PAIRED_EVENT_SD_INCREMENTAL = 0.0301
PAIRED_EVENT_SD_SOURCE = (
    "standalone: SD over 67 (62 for B4) evaluation events of D_EVENT = "
    "within-event mean log loss of the challenger minus that of P_MARKET. "
    "incremental: SD of D_EVENT between the stacked blend and the "
    "market-only stack on the 27 nested test events")

EVIDENCE_LADDER_STANDALONE = {
    0.002: {"EVENTS_FOR_80_PCT_POWER": 236954},
    0.005: {"EVENTS_FOR_80_PCT_POWER": 37913},
    0.010: {"EVENTS_FOR_80_PCT_POWER": 9479},
    0.020: {"EVENTS_FOR_80_PCT_POWER": 2370},
}
EVIDENCE_LADDER_INCREMENTAL = {
    0.002: {"EVENTS_FOR_80_PCT_POWER": 1777},
    0.005: {"EVENTS_FOR_80_PCT_POWER": 285},
    0.010: {"EVENTS_FOR_80_PCT_POWER": 72},
    0.020: {"EVENTS_FOR_80_PCT_POWER": 18},
}
EVIDENCE_LADDER = EVIDENCE_LADDER_INCREMENTAL  # SUPERSEDED by INCREMENTAL_LADDER

WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES = {
    "P_V2_B7": 0.5433, "P_V3_B4": 0.5353, "P_V3_GBM": 0.5651,
}
ROWS_PER_EVENT_MEAN = 13.9
ROW_ACCOUNTING_UNDERSTATES_SHORTFALL_BY = 6.1
WHY_ROWS_ARE_NOT_TRIES = (
    "the row-level score differences correlate about 0.55 within a fixture, "
    "because a challenger prices every contract on a match from ONE score "
    "grid: a grid that is wrong for that match is wrong on all fourteen of "
    "its rows in the same direction. Estimating variance from rows and "
    "comparing it against the 932 rows held understates the shortfall by "
    "about six times")

INDEPENDENT_TEST_EVENTS_CURRENT = 27

WHAT_THE_LADDER_MEANS = (
    "The nested test had 27 independent test events against an INCREMENTAL "
    "ladder that needs about 72 events to detect a 0.010 improvement and 18 "
    "for 0.020. The shortfall on the question section 14 actually asks is "
    "therefore about 2.7x, not the 82x previously reported -- the old figure "
    "came from a ladder built on the wrong contrast.\n\n"
    "The STANDALONE question is a different matter. Distinguishing a "
    "challenger from the market on its own needs about 9,500 events for 0.010, "
    "and that will not be reached by this programme.\n\n"
    "So the honest reading changed with the arithmetic. The incremental "
    "experiment is underpowered but not hopelessly so: roughly 72 clean test "
    "events would resolve a 0.010 effect, which is a data-acquisition target "
    "within reach rather than an impossibility. NOT_DETECTED still means not "
    "detected, and no challenger may be kept or discarded on it."
)

MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT = (
    "A negative result on incremental signal may be reported only when the "
    "test sample reaches the rung of the INCREMENTAL ladder matching the "
    "effect size being ruled out, and the rung must be named BEFORE the test "
    "is run. Ruling out 0.010 needs about 72 independent test events. Below "
    "that the only admissible statement is that the experiment was "
    "underpowered."
)
THE_LADDER_WAS_SET_BEFORE_THE_NEXT_RESULT = True

# --- The sign error that section 21's recomputation exposed. ----------------
SIGN_CONVENTION_CORRECTION = {
    "WHAT_WAS_REPORTED": (
        "the V3 report described the two negative Q4 point estimates as "
        "sitting on the improving side"),
    "WHY_THAT_WAS_WRONG": (
        "DELTA_LOG_LOSS in ev_core_three_expert is MARKET_ONLY minus BLEND, so "
        "POSITIVE means the blend is better. The negative values meant the "
        "opposite of what was written: adding the challenger to the market "
        "made the held-out forecast WORSE, by about 0.013 log loss"),
    "CORRECTED_READING": (
        "on the tested sample every challenger degraded the blend rather than "
        "improving it, with intervals spanning zero. The nested re-run agrees: "
        "the blend is worse by 0.0093, interval spanning zero"),
    "WHAT_DOES_NOT_CHANGE": (
        "the status is still NOT_DETECTED_AT_THIS_SAMPLE_SIZE, the market is "
        "still the strongest settlement forecast, and no negative claim about "
        "fundamental alpha is licensed"),
    "WHAT_DOES_CHANGE": (
        "the encouraging gloss does. There is no observed tendency for the "
        "challengers to help; the point estimates lean the other way"),
}


def events_required(delta, sd=PAIRED_EVENT_SD_INCREMENTAL, power=0.80):
    """Independent events needed to detect `delta`, two-sided 5%."""
    z = {0.80: 2.8016, 0.90: 3.2415, 0.95: 3.6049}.get(power)
    if z is None or delta <= 0:
        return NOT_IDENTIFIED
    return math.ceil((z ** 2) * (sd ** 2) / (delta ** 2))


def ladder_status(n_events):
    """Which effect sizes this many events can and cannot resolve."""
    out = {}
    for d, req in sorted(EVIDENCE_LADDER.items()):
        out["%.3f" % d] = {
            "EVENTS_REQUIRED": req["EVENTS_FOR_80_PCT_POWER"],
            "HAVE": n_events,
            "POWERED": n_events >= req["EVENTS_FOR_80_PCT_POWER"],
            "SHORTFALL_FACTOR": round(req["EVENTS_FOR_80_PCT_POWER"]
                                      / max(n_events, 1), 1),
        }
    return {
        "INDEPENDENT_EVENTS": n_events,
        "BY_EFFECT_SIZE": out,
        "ANY_RUNG_POWERED": any(v["POWERED"] for v in out.values()),
        "WHAT_THE_LADDER_MEANS": WHAT_THE_LADDER_MEANS,
        "MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT":
            MINIMUM_EVIDENCE_BEFORE_A_NEGATIVE_RESULT,
    }


# ===========================================================================
# SECTION 11: TWO WEIGHTINGS, TWO QUESTIONS
# ===========================================================================

FORECAST_VALIDATION_WEIGHTING = "EVENT_EQUAL_ONE_EVENT_ONE_VOTE"
ECONOMIC_WEIGHTING = "CONTRACT_NOTIONAL_WEIGHTED"
THEY_ANSWER_DIFFERENT_QUESTIONS = (
    "Event-equal weighting asks whether the forecast is good. Notional "
    "weighting asks whether being right paid. A forecast can be excellent on "
    "the fixtures nobody trades and irrelevant on the ones that carry the "
    "money, and only reporting both shows it. Neither substitutes for the "
    "other and they must never be averaged together."
)

# ===========================================================================
# SECTION 7: THE CONSENSUS TIMING STATUSES, KEPT APART
# ===========================================================================

OPENING_CONSENSUS_STATUS = "NOT_AVAILABLE_FOR_THE_EVALUATION_WINDOW"
CLOSING_CONSENSUS_STATUS = "COARSE_PREMATCH_UNTIMESTAMPED_SETTLEMENT_ONLY"
EXACT_TIMESTAMP_CONSENSUS_STATUS = NOT_IDENTIFIED

CONSENSUS_TIMING_RULES = (
    "The one odd per market that the current source carries is a pre-match "
    "quote of unstated time. It may be compared against the settlement "
    "outcome, because settlement is after every candidate quote time.",
    "It may NOT be used at an arbitrary earlier BETTOR decision timestamp. "
    "Nothing establishes it was available then.",
    "OPEN and CLOSE must never be combined as if both stood at once. They are "
    "two different moments and using them together implies a trader who could "
    "see the future or the past at will.",
    "Only EXACT_TIMESTAMP snapshots qualify for arbitrary as-of comparison, "
    "and none exist in any source reached so far.",
)

# ===========================================================================
# SECTION 8: EXACT-TIMESTAMP ODDS -- WHAT WOULD HAVE TO BE BOUGHT
# ===========================================================================
#
# Nothing is purchased and nothing is requested. This is the shape of the
# requirement so management can decide.

EXACT_TIMESTAMP_ODDS_REQUIRED_FIELDS = (
    "BOOK", "MARKET", "LINE", "PRICE", "SNAPSHOT_TIMESTAMP",
)

EXACT_TIMESTAMP_ODDS_PROVIDER_OPTIONS = (
    {
        "PROVIDER": "The Odds API",
        "HISTORICAL_START_DATE": "2020-06 (odds snapshots); earlier coverage thin",
        "SNAPSHOT_FREQUENCY": ("5-minute snapshots on recent plans; hourly and "
                               "coarser further back"),
        "BOOKMAKERS": "~40 including Pinnacle, Betfair, US books",
        "SPORTS": "soccer, NFL, NBA, MLB, NHL, tennis, more",
        "MARKET_TYPES": ("MONEYLINE", "SPREAD", "TOTAL", "some PLAYER_PROPS"),
        "RAW_LINE_HISTORY": "YES",
        "TIMESTAMP_PER_SNAPSHOT": "YES",
        "ACCESS": "REST API, historical endpoint; bulk by date range",
        "COST_PLAN": "published tiers by request quota; NOT_VERIFIED_HERE",
        "LICENSING": "commercial terms; redistribution restricted",
        "EXPECTED_MATCH_RATE_TO_BETTOR": ("HIGH for the eight covered soccer "
                                          "leagues; NOT_IDENTIFIED elsewhere"),
        "DATA_SUITABILITY_RANK": 1,
        "WHY": ("the only option that pairs a per-snapshot timestamp with a "
                "documented sub-hourly cadence across the leagues this "
                "programme actually evaluates"),
    },
    {
        "PROVIDER": "Betfair Exchange historical data",
        "HISTORICAL_START_DATE": "2015 and earlier for major markets",
        "SNAPSHOT_FREQUENCY": ("full order-book stream; millisecond "
                               "granularity"),
        "BOOKMAKERS": "one venue -- an exchange, not a bookmaker consensus",
        "SPORTS": "soccer, tennis, horse racing, others",
        "MARKET_TYPES": ("MONEYLINE", "TOTAL", "CORRECT_SCORE", "ASIAN_LINES"),
        "RAW_LINE_HISTORY": "YES -- the richest of the three",
        "TIMESTAMP_PER_SNAPSHOT": "YES",
        "ACCESS": "bulk file download, per-market TAR archives",
        "COST_PLAN": "published per-month archive pricing; NOT_VERIFIED_HERE",
        "LICENSING": "restrictive; explicit non-redistribution",
        "EXPECTED_MATCH_RATE_TO_BETTOR": ("HIGH for soccer; the exchange prices "
                                          "the same contracts the venue lists"),
        "DATA_SUITABILITY_RANK": 2,
        "WHY": ("the best timestamps and the best microstructure, but it is ONE "
                "venue's book. That makes it an excellent second opinion and a "
                "poor consensus -- P_EXTERNAL_CONSENSUS needs several books"),
    },
    {
        "PROVIDER": "OddsJam / OddsBlaze historical",
        "HISTORICAL_START_DATE": "2023 onward for most books",
        "SNAPSHOT_FREQUENCY": "sub-minute on live plans; historical varies",
        "BOOKMAKERS": "100+ including offshore and US retail",
        "SPORTS": "broad",
        "MARKET_TYPES": ("MONEYLINE", "SPREAD", "TOTAL", "PLAYER_PROPS"),
        "RAW_LINE_HISTORY": "YES on the historical product",
        "TIMESTAMP_PER_SNAPSHOT": "YES",
        "ACCESS": "REST API and bulk export",
        "COST_PLAN": "enterprise quote; NOT_VERIFIED_HERE",
        "LICENSING": "commercial; research use requires a specific agreement",
        "EXPECTED_MATCH_RATE_TO_BETTOR": ("HIGH on breadth, but the history "
                                          "starts too late to cover the older "
                                          "seasons the models train on"),
        "DATA_SUITABILITY_RANK": 3,
        "WHY": ("widest book coverage, but the 2023 start date is the binding "
                "constraint: it cannot price the training history"),
    },
)

PROVIDER_RANKING_CRITERION = "DATA_SUITABILITY_NOT_PRICE"
PROVIDER_RANKING_REASONING = (
    "ranked on timestamp fidelity first, then history depth, then breadth of "
    "books, then market types. Price is recorded as NOT_VERIFIED_HERE "
    "throughout because none of it was checked against a live quote and "
    "nothing may be purchased")
NOTHING_MAY_BE_PURCHASED = True
NOTHING_HAS_BEEN_PURCHASED = True
NOTHING_HAS_BEEN_REQUESTED = True

WHY_EXACT_TIMESTAMPS_MATTER = (
    "COARSE_PREMATCH_UNTIMESTAMPED odds cannot be used as an as-of consensus. "
    "Without a snapshot time there is no way to say whether the consensus a "
    "model is being compared against was formed before or after the "
    "observation being scored, and that is the whole of the comparison")


# ===========================================================================
# SECTION 5: THE PLAYER / LINEUP AVAILABILITY CONTRACT
# ===========================================================================

PLAYER_AVAILABILITY_DATA_STATUS = NOT_IDENTIFIED
PLAYER_AVAILABILITY_WHY = (
    "No reachable source carries historically timestamped availability. What "
    "is needed is not 'who played' -- that is in every result archive and it "
    "is an OUTCOME, known only afterwards. What is needed is who was EXPECTED "
    "to play, as known at the decision time, which almost no public archive "
    "preserves because nobody stores yesterday's expectation."
)

PLAYER_AVAILABILITY_CONTRACT = {
    "GRAIN": "one row per (match, player, observation_time)",
    "REQUIRED_FIELDS": (
        "MATCH_KEY", "PLAYER_ID", "TEAM", "OBSERVED_AT",
        "STATUS",            # STARTING / BENCH / DOUBTFUL / OUT / SUSPENDED
        "STATUS_SOURCE",     # official teamsheet / press / aggregator
        "IS_GOALKEEPER", "POSITION",
    ),
    "STRONGLY_WANTED": (
        "EXPECTED_MINUTES", "SEASON_MINUTES_TO_DATE",
        "XG_CONTRIBUTION_TO_DATE", "XA_CONTRIBUTION_TO_DATE",
        "REPLACEMENT_PLAYER_ID", "REPLACEMENT_STRENGTH",
    ),
    "THE_AS_OF_RULE": (
        "OBSERVED_AT must be a real observation time, not the match date. A "
        "lineup row stamped with the match date is the confirmed teamsheet, "
        "which arrives about an hour before kick-off and is an outcome for "
        "every decision before that."),
    "WHAT_MAKES_A_ROW_USELESS": (
        "a status field back-filled from who actually appeared; it encodes the "
        "answer and would leak straight into any model that used it"),
    "DERIVED_FEATURES_ONCE_AVAILABLE": (
        "TEAM_EXPECTED_XG_WEIGHTED_AVAILABILITY",
        "KEY_ATTACKER_OUT", "FIRST_CHOICE_GOALKEEPER_OUT",
        "SUSPENSION_COUNT", "LINEUP_CHANGE_VS_LAST_MATCH",
    ),
}

DO_NOT_FABRICATE_AVAILABILITY_HISTORY = True

# ===========================================================================
# SECTION 17: THE LIVE MODEL IS A DIFFERENT MODEL
# ===========================================================================

P_BETTOR_LIVE_INDEPENDENT_STATUS = NOT_IDENTIFIED
WHY_NO_LIVE_MODEL = (
    "A pregame model asked about a live market is not a worse model, it is the "
    "wrong model: it does not know the score. Stretching it across kick-off "
    "would produce confident nonsense in exactly the states where the market "
    "moves most."
)

LIVE_MODEL_CONTRACT = {
    "REQUIRED_STATE_AT_T": (
        "SCORE_HOME", "SCORE_AWAY", "MINUTES_ELAPSED", "PERIOD",
        "RED_CARDS_HOME", "RED_CARDS_AWAY",
    ),
    "STRONGLY_WANTED": (
        "LIVE_XG_HOME", "LIVE_XG_AWAY", "SHOTS_SINCE_KICKOFF",
        "POSSESSION", "DANGEROUS_ATTACKS", "SUBSTITUTIONS_USED",
    ),
    "WHY_MANPOWER_MATTERS_MOST": (
        "a red card changes the remaining-goals distribution more than any "
        "pregame feature changes the pre-match one"),
    "THE_MODEL_IS_CONDITIONAL": (
        "it predicts the distribution of REMAINING goals given the state, then "
        "adds the goals already scored; a live model that predicts final score "
        "directly relearns the current score from its features and wastes the "
        "capacity"),
    "NOT_AVAILABLE_FROM": "the retained corpus, which carries no game state",
}

# ===========================================================================
# SECTION 15: REGIMES, PRE-REGISTERED
# ===========================================================================

REGIMES_PREREGISTERED_AT = "2026-09-17"
REGIMES_MAY_ONLY_BE_EVALUATED_ON = (
    "development windows or prospective events that were not used to invent "
    "them")

PREREGISTERED_REGIMES = {
    "LIQUIDITY": {
        "SPLIT": "median ask depth in USD at the observation, within family",
        "LEVELS": ("LOW_LIQUIDITY", "HIGH_LIQUIDITY"),
        "WHY_PLAUSIBLE": "a thin book is likelier to carry a stale quote",
    },
    "PREGAME_PHASE": {
        "SPLIT": "time to kick-off, once a class A or B clock exists",
        "LEVELS": ("EARLY_PREGAME", "LATE_PREGAME"),
        "WHY_PLAUSIBLE": ("late prices contain team news the model does not "
                          "have, so a fundamental model should do relatively "
                          "better early"),
        "BLOCKED_BY": "no class A start time; see event_start_time",
    },
    "PRICE_BAND": {
        "SPLIT": "venue price at the observation",
        "LEVELS": ("FAVORITE_GT_0_70", "MIDPRICE_0_30_TO_0_70",
                   "LONGSHOT_LT_0_30"),
        "WHY_PLAUSIBLE": ("de-vig method and model tail behaviour disagree "
                          "most on longshots"),
    },
    "MARKET_FAMILY": {
        "SPLIT": "contract family",
        "LEVELS": ("MONEYLINE", "TOTAL", "EXACT_SCORE"),
        "WHY_PLAUSIBLE": "different families load on different model structure",
    },
    "MODEL_DISAGREEMENT": {
        "SPLIT": "absolute logit gap between challenger and venue price",
        "LEVELS": ("AGREEMENT", "HIGH_CONFIDENCE_DISAGREEMENT"),
        "WHY_PLAUSIBLE": ("if a challenger has information, its value should "
                          "concentrate where it disagrees; if it has none, "
                          "disagreement is just its own noise"),
    },
}

NO_OTHER_REGIME_MAY_BE_REPORTED = (
    "A regime not on this list, discovered while looking at results, is a "
    "subgroup finding and must be treated as a hypothesis for a later window, "
    "never as a result.")

# ===========================================================================
# SECTION 16: THE EXACT-SCORE HYPOTHESIS, FROZEN
# ===========================================================================

GEN2_EXACT_SCORE_RELATIVE_VALUE_HYPOTHESIS = {
    "STATUS": "FROZEN_PREREGISTERED_NOT_VALIDATED",
    "FROZEN_AT": "2026-09-17",
    "WHAT_SUGGESTED_IT": (
        "under leave-one-family-out, every contract family's surface residual "
        "reversed sign except exact score, which kept it: +0.420 in fit, "
        "+0.340 held out, on 559 contracts"),
    "DEFINITION": (
        "on exact-score contracts, the MARKET_SURFACE_V1_ASOF residual "
        "computed with the exact-score family held out of the fit predicts the "
        "settlement direction"),
    "MINIMUM_RESIDUAL_MAGNITUDE": 0.02,
    "WHY_A_MINIMUM": (
        "a residual smaller than two cents cannot survive the spread on this "
        "venue, so testing it would answer a question with no money in it"),
    "EVALUATION_METHOD": (
        "standardised mean difference between held-out residuals on contracts "
        "settling YES and NO, event-equal weighted, event-clustered bootstrap"),
    "HORIZON": "any pregame observation; the hypothesis is not horizon-specific",
    "SUCCESS_CRITERION": (
        "SMD > 0 with a 95% event-clustered interval excluding zero, on "
        "prospective events only, at a sample size on the evidence ladder"),
    "FAILURE_IS_A_RESULT": (
        "if it fails, exact score joins the families whose residual was "
        "artefactual, and that is recorded rather than re-cut"),
    "IT_IS_NOT_ALPHA_TODAY": True,
    "MAY_NOT_BE_TRADED": True,
}


# ---------------------------------------------------------------------------
# Section 4. Shot quality and expected goals.
#
# The directive asked for xG or shot-quality features WITH FULL PROVENANCE.
# The honest answer is that no xG series was obtained for the evaluation
# window, and what the model actually carries is shot VOLUME and shot
# ACCURACY, which are not the same quantity. Both facts are recorded here so
# that no later reader can mistake SHOTS5/TARGET5 for expected goals.
# ---------------------------------------------------------------------------

XG_DATA_STATUS = NOT_IDENTIFIED

XG_SOURCES_SEARCHED = (
    {"SOURCE": "understat.com",
     "WHAT_IT_HAS": "per-match and per-shot xG for the five major leagues",
     "OUTCOME": "EGRESS_DENIED",
     "DETAIL": ("the host-scoped network policy refuses CONNECT to this host; "
                "the refusal is recorded, not worked around")},
    {"SOURCE": "xgabora/Club-Football-Match-Data (Matches.csv)",
     "WHAT_IT_HAS": ("HomeShots, AwayShots, HomeTarget, AwayTarget, "
                     "HomeCorners, AwayCorners, cards, fouls"),
     "OUTCOME": "NO_XG_COLUMN_PRESENT",
     "DETAIL": ("the 48-column header carries no expected-goals field of any "
                "kind; the shot columns are same-match outcomes and are "
                "admissible only as lagged history of EARLIER matches")},
    {"SOURCE": "vaastav/Fantasy-Premier-League (merged gameweek data)",
     "WHAT_IT_HAS": "player-level minutes, points and kickoff_time",
     "OUTCOME": "NO_XG_COLUMN_IN_THE_RETAINED_SEASONS",
     "DETAIL": ("useful later for availability and for kickoff times, not for "
                "shot quality")},
    {"SOURCE": "openfootball (public soccer fixtures)",
     "WHAT_IT_HAS": "fixtures, dates, local kickoff times, full-time scores",
     "OUTCOME": "SCORES_ONLY_NO_SHOT_DATA"},
)

XG_PROXY_IN_USE = {
    "FEATURES": ("SHOTS5_HOME", "SHOTS5_AWAY", "TARGET5_HOME", "TARGET5_AWAY",
                 "CORNERS5_HOME", "CORNERS5_AWAY"),
    "WHAT_THEY_ARE": ("trailing five-match means of shots, shots on target and "
                      "corners, built strictly from matches that had already "
                      "been played at the time of the fixture being predicted"),
    "WHAT_THEY_ARE_NOT": (
        "these are shot QUANTITY and shot ACCURACY. They are not shot QUALITY. "
        "A tap-in and a thirty-yard effort are one shot each here. Calling "
        "SHOTS5 an xG feature would be a false provenance claim"),
    "LEAKAGE_STATUS": "PASSES_THE_FORWARD_ONLY_LEAKAGE_TEST",
    "PROVENANCE": "xgabora/Club-Football-Match-Data, lagged in build_frame",
}

XG_REQUIRED_PROVENANCE_IF_EVER_OBTAINED = (
    "MODEL_NAME",                # whose xG model produced the number
    "MODEL_VERSION_OR_VINTAGE",  # xG models are revised; a revision is a new series
    "AS_OF_TIMESTAMP",           # when the value was computed, not when the match was
    "USES_POST_MATCH_INFORMATION",   # must be recorded, and is disqualifying pregame
    "SHOT_LEVEL_OR_MATCH_LEVEL",
    "LICENCE",
)

XG_IS_NOT_A_PREREQUISITE_FOR_THE_NEGATIVE = (
    "the V2 and V3 results stand on the features actually held. Missing xG is "
    "a reason the independent model is weaker than it could be, and therefore "
    "a reason NOT to close the fundamental line of research. It is not a "
    "reason to discount the market's observed superiority on this sample")

NO_XG_SERIES_HAS_BEEN_FABRICATED = True


# ---------------------------------------------------------------------------
# Section 9. How far the evaluation set could be expanded without lowering the
# identity standard, and what the remaining ceiling actually is.
# ---------------------------------------------------------------------------

EVENT_COUNT_EXPANSION = {
    "TOTAL_VENUE_SOCCER_EVENTS": 1318,
    "PUBLIC_DATA_MATCHED_EVENTS": 111,
    "UNMATCHED_EVENTS": 1207,
    "UNMATCHED_REASON_COUNTS": {
        "LEAGUE_HAS_NO_PUBLIC_SOURCE": 1096,
        "NO_PUBLIC_FIXTURE_IN_WINDOW": 64,
        "CLUB_CODE_UNBOUND": 47,
    },
    "EVALUATION_EVENTS_WITH_PREGAME_OBSERVATIONS": 67,
    "EVALUATION_ROWS": 932,
    "WHY_67_NOT_111": (
        "an event enters the forecast comparison only if it also carries a "
        "settled contract AND at least one observation this programme is "
        "willing to call pregame. 44 of the 111 matched events do not"),
    "IDENTITY_STANDARD_WAS_NOT_LOWERED": True,
    "THE_BINDING_CONSTRAINT": (
        "LEAGUE_HAS_NO_PUBLIC_SOURCE is 91 percent of the shortfall. The venue "
        "lists soccer far beyond the eight leagues covered by the two public "
        "repositories held. No amount of better matching fixes that; only more "
        "league coverage does. This is a DATA ACQUISITION problem, not a "
        "matching-quality problem, and it is the reason the sample sits three "
        "orders of magnitude below the evidence ladder"),
    "REFUSED_SHORTCUTS": (
        "no title parsing, no fuzzy matching, no nearest-start-time join, no "
        "single-team inference. CLUB_CODE_UNBOUND stays unbound"),
}

# ---------------------------------------------------------------------------
# Sections 13 and 14. The V3 challengers, measured. Reported here so the
# numbers live beside the ladder that judges them.
# ---------------------------------------------------------------------------

V3_RESULT = {
    "EVALUATION": {"ROWS": 932, "EVENTS": 67,
                   "WEIGHTING": "EVENT_EQUAL_WEIGHTED",
                   "OBSERVATIONS": "PREGAME_ONLY_BY_THE_CLASS_B_STANDARD"},
    "EXPERTS": {
        "P_MARKET_RAW": {"EE_LOG_LOSS": 0.536585, "EE_BRIER": 0.179498,
                         "CALIBRATION_SLOPE": 0.681, "ERROR_CORR_WITH_B0": None},
        "P_V3_GBM": {"EE_LOG_LOSS": 0.642480, "EE_BRIER": 0.225218,
                     "CALIBRATION_SLOPE": 0.634, "ERROR_CORR_WITH_B0": 0.912},
        "P_V2_B7": {"EE_LOG_LOSS": 0.645954, "EE_BRIER": 0.227070,
                    "CALIBRATION_SLOPE": 0.665, "ERROR_CORR_WITH_B0": 0.910},
        "P_V3_B4": {"EE_LOG_LOSS": 0.674279, "EE_BRIER": 0.238589,
                    "CALIBRATION_SLOPE": 0.727, "ERROR_CORR_WITH_B0": 0.904},
    },
    "CHAMPION": "P_V3_GBM",
    "CHAMPION_BEAT": "P_V2_B7 by 0.0035 event-equal log loss",
    "INCREMENTAL_OVER_THE_MARKET": {
        "P_V3_GBM": {"DELTA_LOG_LOSS": -0.01288,
                     "CI95": (-0.02998, 0.00228),
                     "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"},
        "P_V2_B7": {"DELTA_LOG_LOSS": -0.01363,
                    "CI95": (-0.03212, 0.00220),
                    "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"},
        "P_V3_B4": {"DELTA_LOG_LOSS": 0.00553,
                    "CI95": (-0.02672, 0.02963),
                    "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"},
    },
    "SIGN_CONVENTION": (
        "DELTA_LOG_LOSS is MARKET_ONLY minus BLEND. POSITIVE means the blend "
        "is better. All three values above are NEGATIVE, so on this sample "
        "adding the challenger made the held-out forecast WORSE"),
    "WHAT_NOT_DETECTED_MEANS_HERE": (
        "27 independent test events against an incremental ladder that needs "
        "about 72 for a 0.010 effect. Every interval above is consistent with "
        "a real improvement AND with a real degradation. The sign of the point "
        "estimate is not evidence"),
    "WHAT_IS_ACTUALLY_INFORMATIVE": (
        "B4, once repaired, stopped being absurd. That is a plumbing result, "
        "not a forecasting one. Nothing here shows a challenger helping; the "
        "point estimates lean the other way, and the sample cannot resolve "
        "either direction"),
    "ERROR_CORRELATION_READING": (
        "0.90 to 0.91 with the market. A challenger that shared no information "
        "with the market would be far below that, and one that merely "
        "rediscovered the market would be far above it. The challengers are "
        "reading the same matches with weaker instruments, not finding an "
        "independent view"),
    "B4_REPAIR_CONFIRMED": True,
    "NO_MODEL_WAS_TUNED_UNTIL_IT_LOOKED_PROFITABLE": True,
    "THIS_TABLE_PREDATES_THE_SECTION_22_NESTING": True,
    "SEE_ALSO": "NESTED_RESULT",
}

# ---------------------------------------------------------------------------
# Section 22. The nested re-run. This, not V3_RESULT's Q4 block, is the valid
# conditional-market measurement: the calibrator never saw a development or
# test outcome, and the stack never saw a test outcome.
# ---------------------------------------------------------------------------

NESTED_RESULT = {
    "PROTOCOL": "TRAIN(W0) -> CALIBRATE(W1) -> STACK(W2) -> TEST(W3)",
    "W0_TRAIN": {"MATCHES": 87443, "CUTOFF": "DATE < 2026-05-01"},
    "W1_CALIBRATION": {"EXTERNAL_MATCHES": 489, "PAIRS": 1956,
                       "WINDOW": "2026-05-01 .. 2026-08-01",
                       "CONTAINS_NO_EVALUATION_EVENT": True},
    "W2_DEVELOPMENT": {"EVENTS": 40},
    "W3_TEST": {"EVENTS": 27, "ROWS": 520},
    "EVALUATION_FIXTURE_DATES": "2026-08-07 .. 2026-09-02",
    "NO_EVALUATION_FIXTURE_IS_INSIDE_W0_OR_W1": True,
    "LEAK_CHECK": "CLEAN",
    "CALIBRATOR_SELECTED": "IDENTITY",
    "CALIBRATOR_SELECTED_ON": "CALIBRATION_WINDOW_W1_KFOLD_CV",
    "CALIBRATION_CV_SCORES_IN_W1": {
        "IDENTITY": 0.62364, "PLATT": 0.62311, "BETA": 0.62311,
        "TEMPERATURE": 0.62316, "ISOTONIC": 0.61534},
    "WHY_IDENTITY_WON": (
        "cross-validated inside the calibration window, no calibration map "
        "beat leaving the probabilities alone. The candidates that looked "
        "better in-sample did not survive being charged for their flexibility"),
    "DELTA_LOG_LOSS": -0.00926,
    "DELTA_SIGN_CONVENTION": "POSITIVE_MEANS_THE_BLEND_IS_BETTER",
    "CI95_EVENT_BOOTSTRAP": (-0.00222, 0.02036),
    "SD_D_EVENT": 0.0301,
    "INCREMENTAL_SIGNAL_STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE",
    "POPULATION_TRANSPORT_ASSUMED": True,
    "POPULATION_TRANSPORT_NOTE": (
        "W1 is external-league fixtures and W3 is venue contracts, so the "
        "calibrator is transported across populations. With IDENTITY selected "
        "the transport carries no weight in this particular run, but the "
        "assumption is recorded because a future run may select otherwise"),
    "LEAK_MAGNITUDE_MEASURED": {
        "METHOD": ("the forbidden variant was run deliberately: the same "
                   "protocol with the calibrator fitted ON the test events"),
        "DIFFERENCE_IN_DELTA_LOG_LOSS": 0.00000,
        "WHY_SO_SMALL": (
            "the selected calibrator is the identity map, which cannot carry "
            "outcome information no matter what it is fitted on. The control "
            "bound nothing on THIS run. That is a fact about this run, not a "
            "reason to drop the control -- a run that selects isotonic or beta "
            "would leak, and nothing in the numbers would show it"),
    },
    "WHAT_IT_SAYS": (
        "under a protocol where no test outcome touched any probability, "
        "adding the independent challenger to the market made the blend worse "
        "by 0.0093 log loss, with a 95% event-clustered interval spanning "
        "zero. No incremental signal, and no evidence of harm either"),
}


# ---------------------------------------------------------------------------
# Directive K2. What the archival recovery produced, and the two lanes.
# ---------------------------------------------------------------------------

CANONICAL_DELTA_SIGN_STATUS = "ENFORCED_IN_ev_core_delta"

VENUE_NATIVE_START_TIME_SEARCH = {
    "ARTIFACTS_SCANNED": 123,
    "SLUGS_CARRYING_A_START_TIME": 2384,
    "DISTINCT_EVENTS_CARRYING_ONE": 63,
    "SETTLED_MARKET_SLUGS_IN_THE_EV_POPULATION": 16943,
    "RAW_SLUG_INTERSECTION": 0,
    "JOINED_TO_SETTLED_EV_POPULATION": 0,
    "AMBIGUOUS_JOINS": 0,
    "VENUE_NATIVE_START_TIME_COVERAGE_PCT": 0.0,
    "JOIN_METHOD": ("exact slug equality AND venue event-key equality, over "
                    "every retained JSON artifact; no fuzzy matching"),
    "WHY_ZERO": (
        "the artifacts that carry gameStartTime are FORWARD boards captured "
        "for run85 and the micro-live rehearsal -- start-time values run to "
        "2027-07-01 and the leagues are cfb, nfl and mlb. The settled EV "
        "population is historical soccer, slugs dated 2026-05-24 to "
        "2026-09-11, resolved by 2026-09-12. The two sets are disjoint by "
        "construction: one looks forward at markets not yet settled, the "
        "other is built from markets already resolved"),
    "THIS_IS_NOT_A_MATCHING_FAILURE": True,
    "WHAT_WOULD_FIX_IT": (
        "capturing gameStartTime prospectively alongside the forward board, so "
        "that when those events settle the native clock is already held"),
}

ARCHIVAL_PROVENANCE_STATUS = "BUILT"

ARCHIVAL_RECOVERY = {
    "SOURCE": "openfootball/football.json",
    "COMMITS_IN_REPO": 234,
    "COMMITS_CARRYING_DATA": 233,
    "MATCH_RESULTS_INDEXED": 44764,
    "DISTINCT_FIRST_SEEN_COMMIT_STAMPS": 80,
    "EARLIEST_PROVING_COMMIT": "2020-08-06T19:37:58+02:00",
    "LATEST_PROVING_COMMIT": "2026-09-09T09:18:42+00:00",
    "PROVING_COMMITS_INSIDE_THE_EVALUATION_WINDOW": (
        "2026-08-24", "2026-08-26", "2026-09-02", "2026-09-09"),
    "ROWS_WHOSE_VALUE_CHANGED_AFTER_FIRST_PUBLICATION": 120,
    "CHANGED_ROWS_ARE_EXCLUDED_NOT_TRUSTED": True,
    "PUBLICATION_LAG_DAYS_MEDIAN_AUG_2026": 3.4,
    "PUBLICATION_LAG_DAYS_MAX_AUG_2026": 16.5,
    "WHY_THIS_WORKS": (
        "openfootball auto-updates weekly per league file, so a match played "
        "before one of those commits and present in it is PROVEN to have been "
        "knowable before any later fixture"),
    "WHY_XGABORA_CANNOT_DO_THIS": (
        "four data commits in total; the last one before the August 2026 "
        "evaluation window is 2025-06-27, fourteen months early. The recent "
        "history its rolling features need first appears on 2026-09-05, AFTER "
        "the fixtures being predicted"),
}

FEATURE_CLASS_CENSUS = {
    "PROVEN_NATIVE_FEATURES": 0,
    "PROVEN_ARCHIVAL_FEATURES": 0,
    "PROVEN_DERIVED_FEATURES": 30,
    "CONSERVATIVELY_BOUNDED_FEATURES": 34,
    "NOT_PROVEN_FEATURES": 3,
    "NOTE": ("the 30 high-integrity features are PROVEN_DERIVED rather than "
             "PROVEN_ARCHIVAL because each is a causal transformation of "
             "archivally-proven results rather than a raw archived value. The "
             "3 NOT_PROVEN are the repository Elo trio"),
}

HIGH_INTEGRITY_FEATURE_COUNT = 30
INTERNAL_ELO_STATUS = "PROVEN_DERIVED"
INTERNAL_ELO_CAUSALITY_TEST = {
    "BASE_MATCHES": 4000,
    "EARLIER_FEATURE_ROWS_IDENTICAL": True,
    "EARLIER_STATE_SNAPSHOTS_IDENTICAL": True,
    "CAUSAL": True,
    "MEANING": "adding a future result changed no earlier Elo state",
}

HIGH_INTEGRITY_V3_RESULT = {
    "STATUS": "BUILT_AND_MEASURED",
    "MODELS": ("HI_GRADIENT_BOOSTED_FUNDAMENTALS", "HI_INTERNAL_ELO"),
    "TRAINING_MATCHES": 44355,
    "FEATURES": 30,
    "EVENTS_WITH_PROVEN_FEATURES": 147,
    "OWN_EVALUATION": {"EVENTS": 71, "ROWS": 1123,
                       "B0_LOG_LOSS": 0.575143, "B0_BRIER": 0.190638,
                       "P_HI_GBM_LOG_LOSS": 0.718779,
                       "P_HI_GBM_BRIER": 0.253581,
                       "DELTA_LOG_LOSS": -0.14364,
                       "DELTA_BRIER": -0.06294},
    "CHAMPION": "HI_INTERNAL_ELO",
    "CHAMPION_NOTE": ("the Elo-only challenger scored 0.712979 against the "
                      "GBM's 0.718779, so the extra 29 features did not pay "
                      "for themselves inside the lane either"),
    "NESTED_INCREMENTAL": {
        "W3_EVENTS": 28, "W3_ROWS": 644,
        "DELTA_LOG_LOSS": -0.11122,
        "CI95_CANONICAL": (-0.22883, 0.01376),
        "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE",
        "CALIBRATOR_SELECTED": "TEMPERATURE",
        "LEAK_CHECK": "CLEAN",
    },
}

LANE_COMPARISON_COMMON_SET = {
    "WHY_A_COMMON_SET": (
        "the two lanes bind different event sets, so their headline numbers "
        "are not comparable. The procurement question -- what is richer "
        "information worth -- can only be answered on the events both lanes "
        "can price"),
    "COMMON_EVENTS": 47,
    "COMMON_ROWS": 666,
    "B0_LOG_LOSS": 0.582821,
    "B0_BRIER": 0.198377,
    "P_HIGH_INTEGRITY": {"LOG_LOSS": 0.734698, "BRIER": 0.263768,
                         "DELTA_LOG_LOSS": -0.15188, "DELTA_BRIER": -0.06539},
    "P_RESEARCH": {"LOG_LOSS": 0.726503, "BRIER": 0.260268,
                   "DELTA_LOG_LOSS": -0.14368, "DELTA_BRIER": -0.06189},
    "RESEARCH_MINUS_HIGH_INTEGRITY_LOG_LOSS": 0.008195,
    "WHAT_THAT_BUYS": (
        "the unproven extras -- shots, shots on target, corners, repository "
        "Elo -- are worth about 0.008 event-equal log loss on this sample. "
        "That is the measured value of the information the archive cannot "
        "prove, and it is small"),
    "THE_PROCUREMENT_READING": (
        "recovering provenance cost almost nothing in accuracy: the proven "
        "lane is within 0.008 of the richer one. So the procurement case is "
        "NOT for better-timestamped shots and corners -- it is for the two "
        "things neither lane holds at all, exact-timestamp odds and real xG"),
    "BOTH_LANES_ARE_WORSE_THAN_THE_MARKET": True,
}

RESEARCH_LANE_V3_STATUS = "MEASURED_RESEARCH_ONLY"

POWER_ESTIMATE_UNCERTAINTY_STATUS = "QUANTIFIED_BY_EVENT_BOOTSTRAP"

POWER_LADDER_WITH_UNCERTAINTY = {
    "LANE": "HIGH_INTEGRITY_STANDALONE_ON_THE_COMMON_SET",
    "SD_D_EVENT": 0.3319,
    "LADDER_USES": "P90",
    0.002: {"POINT": 216138, "P90": 297620},
    0.005: {"POINT": 34582, "P90": 47620},
    0.010: {"POINT": 8646, "P90": 11905},
    0.020: {"POINT": 2162, "P90": 2977},
    "CLUSTER_SENSITIVITY_BY_LEAGUE_RAISES_NOTHING_MATERIALLY": True,
}

THE_0020_RUNG_LANGUAGE = {
    "WHAT_MAY_NOT_BE_SAID": "WE HAVE PROVEN NO +0.020 EDGE EXISTS",
    "WHY_NOT": (
        "power is not retrospective proof. Four things would have to hold "
        "first: the paired variance uncertainty incorporated (it now is, and "
        "it RAISED the requirement); the experiment population scientifically "
        "valid; as-of feature provenance passing; and the exact tested model "
        "frozen prospectively. The last is not true -- these models were "
        "built after the data was seen"),
    "WHAT_MAY_BE_SAID": (
        "on 47 common events, no incremental signal was detected, and the "
        "sample is below the conservative rung for every effect size on the "
        "ladder"),
}

GEN3_CANDIDATE_STATUS = {
    "MODELS": ("HI_GRADIENT_BOOSTED_FUNDAMENTALS", "HI_INTERNAL_ELO",
               "P_V3_GBM", "P_V3_B4"),
    "GENERATION": "GEN3_CANDIDATE",
    "WHY": ("every one of these was created after the Gen2 freeze stamp of "
            "2026-09-17T14:00:00Z, and the Gen2 pre-registration names only "
            "FULL_TIME_MONEYLINE, FULL_TIME_TOTAL, DRAW and EXACT_SCORE as "
            "challengers. A model built later is a new generation, not a "
            "late entry to an old one"),
    "GEN2_OUTCOMES_WERE_NOT_INSPECTED": True,
    "NOTHING_WAS_SELECTED_OR_TUNED_USING_GEN2_SETTLEMENTS": True,
}


# ---------------------------------------------------------------------------
# Directive K3. TWO LADDERS, PERMANENTLY SEPARATE.
#
# The §21 report conflated them again. The number published there -- 0.010
# needing 8,646 events -- is the STANDALONE ladder and is now labelled as
# such. And the INCREMENTAL figure previously published (SD 0.0301, 72 events)
# is ALSO superseded: it came from one chronological dev/test split in which
# the stack happened to give the challenger a tiny coefficient, so the blend
# barely moved and the differences were artificially small. Measured properly
# -- out-of-fold, event-clustered, across all 47 common events -- the
# incremental SD is 0.2435, not 0.0301, and the requirement is 4,654 events
# rather than 72.
#
# Both published ladders were therefore wrong, in opposite directions, for the
# same underlying reason: a variance estimated on the wrong contrast.
# ---------------------------------------------------------------------------

POWER_LADDER_AUDIT_STATUS = "CORRECTED_TWO_LADDERS_SEPARATED"

SUPERSEDED_INCREMENTAL_SD = 0.0301
WHY_THE_INCREMENTAL_SD_WAS_ALSO_WRONG = (
    "0.0301 was the SD of the blend-minus-market difference on a single "
    "chronological test window whose stack gave the challenger almost no "
    "weight. A blend that barely differs from the market produces small "
    "differences by construction, so the ladder built on it said 72 events "
    "when the honest figure is several thousand. Out-of-fold event-clustered "
    "stacking across the whole common set removes that artefact")

STANDALONE_QUESTION = (
    "how many independent events to establish that BETTOR's independent model "
    "itself beats B0")
INCREMENTAL_QUESTION = (
    "how many independent events to establish that adding BETTOR information "
    "improves the market forecast")

PRIMARY_MANAGEMENT_EVIDENCE_LADDER = "INCREMENTAL"
WHY_INCREMENTAL_IS_PRIMARY = (
    "BETTOR does not need to prove P_BETTOR_INDEPENDENT > P_MARKET_RAW "
    "standalone. The economically relevant question is whether MARKET plus "
    "BETTOR information beats MARKET alone, so ensemble admission is judged on "
    "the incremental ladder. The standalone ladder is retained as a research "
    "benchmark and may not be quoted as the evidence requirement")

LADDER_EVALUATION_SET = {
    "COMMON_EVENTS": 47, "CONTRACT_ROWS": 666,
    "EVENT_WEIGHTING_METHOD": "EVENT_EQUAL_WEIGHTED",
    "TIME_RANGE": "2026-08-07 .. 2026-08-30",
    "BOTH_LANES_ON_THE_SAME_EVENT_SET": True,
}

STANDALONE_SD_EVENT = {"P_HIGH_INTEGRITY": 0.3397, "P_RESEARCH": 0.3549}
INCREMENTAL_SD_EVENT = {"P_HIGH_INTEGRITY": 0.2435, "P_RESEARCH": 0.1712}
INCREMENTAL_SD_METHOD = (
    "out-of-fold, event-clustered 5-fold stacking; each event's blend was "
    "produced by a stack fitted on other events only")

STANDALONE_LADDER = {
    "P_HIGH_INTEGRITY": {
        0.002: {"POINT": 226367, "P50": 215095, "P75": 274665,
                "P90": 334216, "P95": 368847},
        0.005: {"POINT": 36219, "P50": 34416, "P75": 43947,
                "P90": 53475, "P95": 59016},
        0.010: {"POINT": 9055, "P50": 8604, "P75": 10987,
                "P90": 13369, "P95": 14754},
        0.020: {"POINT": 2264, "P50": 2151, "P75": 2747,
                "P90": 3343, "P95": 3689},
    },
    "P_RESEARCH": {
        0.002: {"POINT": 247135, "P50": 236090, "P75": 287428,
                "P90": 342842, "P95": 373740},
        0.005: {"POINT": 39542, "P50": 37775, "P75": 45989,
                "P90": 54855, "P95": 59799},
        0.010: {"POINT": 9886, "P50": 9444, "P75": 11498,
                "P90": 13714, "P95": 14950},
        0.020: {"POINT": 2472, "P50": 2361, "P75": 2875,
                "P90": 3429, "P95": 3738},
    },
}

INCREMENTAL_LADDER = {
    "P_HIGH_INTEGRITY": {
        0.002: {"POINT": 116335, "P50": 111523, "P75": 188741,
                "P90": 217537, "P95": 289894},
        0.005: {"POINT": 18614, "P50": 17844, "P75": 30199,
                "P90": 34806, "P95": 46383},
        0.010: {"POINT": 4654, "P50": 4461, "P75": 7550,
                "P90": 8702, "P95": 11596},
        0.020: {"POINT": 1164, "P50": 1116, "P75": 1888,
                "P90": 2176, "P95": 2899},
    },
    "P_RESEARCH": {
        0.002: {"POINT": 57523, "P50": 54624, "P75": 88545,
                "P90": 104709, "P95": 134704},
        0.005: {"POINT": 9204, "P50": 8740, "P75": 14168,
                "P90": 16754, "P95": 21553},
        0.010: {"POINT": 2301, "P50": 2185, "P75": 3542,
                "P90": 4189, "P95": 5389},
        0.020: {"POINT": 576, "P50": 547, "P75": 886,
                "P90": 1048, "P95": 1348},
    },
}

LADDERS_MAY_NOT_BE_COMBINED = True

INCREMENTAL_RESULT = {
    "P_HIGH_INTEGRITY": {"DELTA_LOG_LOSS": -0.02457,
                         "CI95": (-0.10545, 0.03233),
                         "INDEPENDENT_EVENTS": 47, "CONTRACT_ROWS": 666,
                         "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"},
    "P_RESEARCH": {"DELTA_LOG_LOSS": -0.02930,
                   "CI95": (-0.08488, 0.01243),
                   "INDEPENDENT_EVENTS": 47, "CONTRACT_ROWS": 666,
                   "STATUS": "NOT_DETECTED_AT_THIS_SAMPLE_SIZE"},
    "CONVENTION": "SCORE_B0 - SCORE_B0_PLUS_BETTOR; positive means BETTOR adds",
    "NOT_DETECTED_IS_NOT_PROVEN_ABSENT": True,
}

# --- Section 5. Every score table carries its own N. ------------------------
REQUIRED_TABLE_FIELDS = ("CONTRACT_ROWS", "INDEPENDENT_EVENTS",
                         "EVENT_WEIGHTING_METHOD", "TIME_RANGE")
NOT_DIRECTLY_COMPARABLE = "NOT_DIRECTLY_COMPARABLE"
WHY_EVERY_TABLE_NEEDS_ITS_N = (
    "a score without its independent event count cannot be read. Two of this "
    "programme's own tables were compared across different event sets before "
    "the common-set rule was imposed, and the difference between them was "
    "partly the sample, not the model")

# --- Section 9. The class-B audit. ------------------------------------------
START_TIME_CLASS_B_AUDIT_STATUS = "AUDITED_AND_DOWNGRADED"
START_TIME_CLASS_AUDIT = {
    "POPULATION": "176 bound soccer events with at least one public clock",
    "CLASS_A_EVENTS": 0,
    "CLASS_B_EVENTS": 0,
    "CLASS_B_SHARED_UPSTREAM_EVENTS": 40,
    "CLASS_C_EVENTS": 136,
    "DISTINCT_UPSTREAM_PAIRS": 1,
    "SOURCE_1_UPSTREAM": "openfootball community curation of league schedules",
    "SOURCE_2_UPSTREAM": "Football-Data.co.uk (stated in the xgabora README)",
    "SHARED_ROOT_AUTHORITY": "the league's own published schedule",
    "WHY_DOWNGRADED": (
        "two compilations of one authority agree on transcription, not on the "
        "true kick-off. A fixture moved after publication would be wrong in "
        "both, which is the common-mode failure independence would have "
        "caught"),
    "UNCERTAINTY_RAISED_FROM_HOURS": 1.0,
    "UNCERTAINTY_RAISED_TO_HOURS": 2.0,
    "HORIZON_COST": "the T-2h rung is no longer claimable; T-24h and T-6h are",
    "TIGHT_HORIZONS_STILL_REQUIRE_CLASS_A": True,
}

# --- Sections 10 and 11. Language. ------------------------------------------
HIGH_INTEGRITY_INCREMENTAL_SIGNAL = "NOT_DETECTED"
NOT_PROVEN_ABSENT = (
    "HI_INTERNAL_ELO is materially worse than B0 standalone -- that is "
    "factual. The incremental estimate is negative with an interval including "
    "zero. That is NOT_DETECTED, not PROVEN_ABSENT. The sample is small and "
    "the feature set is deliberately limited, so the lane is not exhausted")

XG_PROCUREMENT_LANGUAGE = {
    "WHAT_THE_RESEARCH_LANE_SHOWED": (
        "on 47 common events the research lane beat high-integrity by about "
        "0.008 event-equal log loss"),
    "WHAT_THAT_MEANS": "CURRENT_UNPROVEN_FUNDAMENTAL_EXTRAS_ADD_LITTLE",
    "WHAT_IT_DOES_NOT_MEAN": (
        "it does not show that xG specifically is worth buying, because this "
        "lane contains no real xG at all -- only shots, shots on target and "
        "corners"),
    "CORRECT_STATUS": "REAL_XG_REMAINS_UNTESTED",
}

EXACT_TIMESTAMP_ODDS_PROVIDER_STATUS = "COMPARISON_COMPLETE_RANKED_NOT_PURCHASED"
WHY_ODDS_ARE_THE_CLEAREST_GAP = (
    "current consensus odds are coarse and untimestamped; the market price is "
    "the strongest forecast we hold; an independent market-consensus "
    "observation at exactly T is what the comparison needs; and timing is "
    "central to deciding whether information leads or lags")

NEXT_LARGEST_EXPECTED_INFORMATION_GAIN = (
    "EXACT_TIMESTAMP_EXTERNAL_ODDS",
    "CONTINUOUS_SUBSTANTIVE_CAPTURE",
    "MICROSTRUCTURE_RELATIVE_VALUE_EVIDENCE",
    "BETTOR_NATIVE_PASSIVE_FILL_AND_TOXICITY_EVIDENCE",
)
STATIC_SOCCER_MODEL_TUNING_IS_NOT_THE_NEXT_CYCLE = True


def describe():
    return {
        "EVIDENCE_LADDER": {("%.3f" % k): v
                            for k, v in EVIDENCE_LADDER.items()},
        "PAIRED_EVENT_SD_STANDALONE": dict(PAIRED_EVENT_SD_STANDALONE),
        "PAIRED_EVENT_SD_INCREMENTAL": PAIRED_EVENT_SD_INCREMENTAL,
        "EVIDENCE_LADDER_STANDALONE": {("%.3f" % k): v for k, v
                                       in EVIDENCE_LADDER_STANDALONE.items()},
        "SUPERSEDED_PAIRED_EVENT_SD": SUPERSEDED_PAIRED_EVENT_SD,
        "WHY_THE_OLD_LADDER_WAS_WRONG": WHY_THE_OLD_LADDER_WAS_WRONG,
        "SIGN_CONVENTION_CORRECTION": dict(SIGN_CONVENTION_CORRECTION),
        "WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES":
            dict(WITHIN_EVENT_CORRELATION_OF_ROW_DIFFERENCES),
        "WHY_ROWS_ARE_NOT_TRIES": WHY_ROWS_ARE_NOT_TRIES,
        "NESTED_RESULT": dict(NESTED_RESULT),
        "INDEPENDENT_TEST_EVENTS_CURRENT": INDEPENDENT_TEST_EVENTS_CURRENT,
        "WHAT_THE_LADDER_MEANS": WHAT_THE_LADDER_MEANS,
        "FORECAST_VALIDATION_WEIGHTING": FORECAST_VALIDATION_WEIGHTING,
        "ECONOMIC_WEIGHTING": ECONOMIC_WEIGHTING,
        "OPENING_CONSENSUS_STATUS": OPENING_CONSENSUS_STATUS,
        "CLOSING_CONSENSUS_STATUS": CLOSING_CONSENSUS_STATUS,
        "EXACT_TIMESTAMP_CONSENSUS_STATUS": EXACT_TIMESTAMP_CONSENSUS_STATUS,
        "EXACT_TIMESTAMP_ODDS_PROVIDER_OPTIONS":
            [dict(p) for p in EXACT_TIMESTAMP_ODDS_PROVIDER_OPTIONS],
        "NOTHING_HAS_BEEN_PURCHASED": NOTHING_HAS_BEEN_PURCHASED,
        "CANONICAL_DELTA_SIGN_STATUS": CANONICAL_DELTA_SIGN_STATUS,
        "POWER_LADDER_AUDIT_STATUS": POWER_LADDER_AUDIT_STATUS,
        "PRIMARY_MANAGEMENT_EVIDENCE_LADDER": PRIMARY_MANAGEMENT_EVIDENCE_LADDER,
        "STANDALONE_SD_EVENT": dict(STANDALONE_SD_EVENT),
        "INCREMENTAL_SD_EVENT": dict(INCREMENTAL_SD_EVENT),
        "STANDALONE_LADDER": {k: {("%.3f" % d): v for d, v in lad.items()}
                              for k, lad in STANDALONE_LADDER.items()},
        "INCREMENTAL_LADDER": {k: {("%.3f" % d): v for d, v in lad.items()}
                               for k, lad in INCREMENTAL_LADDER.items()},
        "INCREMENTAL_RESULT": dict(INCREMENTAL_RESULT),
        "LADDER_EVALUATION_SET": dict(LADDER_EVALUATION_SET),
        "SUPERSEDED_INCREMENTAL_SD": SUPERSEDED_INCREMENTAL_SD,
        "START_TIME_CLASS_B_AUDIT_STATUS": START_TIME_CLASS_B_AUDIT_STATUS,
        "START_TIME_CLASS_AUDIT": dict(START_TIME_CLASS_AUDIT),
        "HIGH_INTEGRITY_INCREMENTAL_SIGNAL": HIGH_INTEGRITY_INCREMENTAL_SIGNAL,
        "XG_PROCUREMENT_LANGUAGE": dict(XG_PROCUREMENT_LANGUAGE),
        "EXACT_TIMESTAMP_ODDS_PROVIDER_STATUS":
            EXACT_TIMESTAMP_ODDS_PROVIDER_STATUS,
        "VENUE_NATIVE_START_TIME_SEARCH": dict(VENUE_NATIVE_START_TIME_SEARCH),
        "ARCHIVAL_PROVENANCE_STATUS": ARCHIVAL_PROVENANCE_STATUS,
        "ARCHIVAL_RECOVERY": dict(ARCHIVAL_RECOVERY),
        "FEATURE_CLASS_CENSUS": dict(FEATURE_CLASS_CENSUS),
        "HIGH_INTEGRITY_FEATURE_COUNT": HIGH_INTEGRITY_FEATURE_COUNT,
        "INTERNAL_ELO_STATUS": INTERNAL_ELO_STATUS,
        "HIGH_INTEGRITY_V3_RESULT": dict(HIGH_INTEGRITY_V3_RESULT),
        "LANE_COMPARISON_COMMON_SET": dict(LANE_COMPARISON_COMMON_SET),
        "POWER_ESTIMATE_UNCERTAINTY_STATUS": POWER_ESTIMATE_UNCERTAINTY_STATUS,
        "POWER_LADDER_WITH_UNCERTAINTY": {str(k): v for k, v
                                          in POWER_LADDER_WITH_UNCERTAINTY.items()},
        "THE_0020_RUNG_LANGUAGE": dict(THE_0020_RUNG_LANGUAGE),
        "GEN3_CANDIDATE_STATUS": dict(GEN3_CANDIDATE_STATUS),
        "EVENT_COUNT_EXPANSION": dict(EVENT_COUNT_EXPANSION),
        "V3_RESULT": dict(V3_RESULT),
        "XG_DATA_STATUS": XG_DATA_STATUS,
        "XG_SOURCES_SEARCHED": [dict(s) for s in XG_SOURCES_SEARCHED],
        "XG_PROXY_IN_USE": dict(XG_PROXY_IN_USE),
        "NO_XG_SERIES_HAS_BEEN_FABRICATED": NO_XG_SERIES_HAS_BEEN_FABRICATED,
        "PLAYER_AVAILABILITY_DATA_STATUS": PLAYER_AVAILABILITY_DATA_STATUS,
        "P_BETTOR_LIVE_INDEPENDENT_STATUS": P_BETTOR_LIVE_INDEPENDENT_STATUS,
        "PREREGISTERED_REGIMES": {k: dict(v)
                                  for k, v in PREREGISTERED_REGIMES.items()},
        "GEN2_EXACT_SCORE_RELATIVE_VALUE_HYPOTHESIS":
            dict(GEN2_EXACT_SCORE_RELATIVE_VALUE_HYPOTHESIS),
    }
