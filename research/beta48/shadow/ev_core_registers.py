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
EVIDENCE_LADDER = EVIDENCE_LADDER_INCREMENTAL  # the section 14 question

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
        "HISTORICAL_DEPTH": "historical endpoints from roughly 2020",
        "SNAPSHOT_FREQUENCY": "periodic snapshots, commonly 5-10 minutes on "
                              "the historical plans",
        "SPORTS": "broad, including the European football leagues we trade",
        "MARKETS": "1X2, totals, spreads; player props on higher tiers",
        "ACCESS": "commercial API key, tiered by request volume",
        "ARE_THEY_POINT_IN_TIME": "YES for the historical snapshot endpoints; "
                                  "this is the field that must be confirmed "
                                  "before purchase",
    },
    {
        "PROVIDER": "OddsJam / OddsBlaze",
        "HISTORICAL_DEPTH": "several seasons",
        "SNAPSHOT_FREQUENCY": "sub-minute on live tiers",
        "SPORTS": "broad, strong US coverage, European football included",
        "MARKETS": "wide, including alternate lines",
        "ACCESS": "commercial, enterprise pricing",
        "ARE_THEY_POINT_IN_TIME": "line-history products are marketed as "
                                  "point-in-time; must be verified rather "
                                  "than assumed",
    },
    {
        "PROVIDER": "Betfair Exchange historical data",
        "HISTORICAL_DEPTH": "many years",
        "SNAPSHOT_FREQUENCY": "full order-book stream, sub-second",
        "SPORTS": "football among many",
        "MARKETS": "exchange markets rather than bookmaker prices",
        "ACCESS": "paid historical data downloads",
        "ARE_THEY_POINT_IN_TIME": "YES -- it is a market stream, which is the "
                                  "strongest form of the thing we need, and "
                                  "it is an EXCHANGE, so it is the closest "
                                  "analogue to our own venue",
    },
)

WHY_EXACT_TIMESTAMPS_MATTER = (
    "Without a snapshot time, an external price can only ever be compared "
    "against the settlement outcome. That answers 'is the book good' and "
    "cannot answer 'was the venue stale at the moment we could have traded', "
    "which is the question with money attached. The Betfair exchange stream is "
    "the strongest candidate precisely because it is a book rather than a "
    "quote, and because an exchange's mechanics resemble our venue's."
)
NOTHING_HAS_BEEN_PURCHASED = True
NOTHING_HAS_BEEN_REQUESTED = True

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
