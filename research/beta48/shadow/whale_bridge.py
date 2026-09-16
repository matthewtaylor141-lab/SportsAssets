#!/usr/bin/env python3
"""THE WHALE -> BETTOR EV BRIDGE. Whale evidence as a PRIOR, never as a score.

Contacts nothing. No orders, no capital, no credentials, mirror_live = false.

THE HIERARCHY THIS MODULE ENFORCES:

    PRIMARY    observed whale behaviour and reconstructed whale economics
    SECONDARY  established public research
    TERTIARY   BETTOR's own prospective observations and fills

and the direction of travel: as BETTOR-native evidence accumulates, the whale
prior's weight FALLS. It is a starting point that BETTOR grows out of, not a
ceiling and not an authority.

WHAT THE RETAINED WHALE ARTEFACTS ACTUALLY CONTAIN -- read, not assumed:

    whale_exit_priors_v1.json
        GRANULARITY            AGGREGATE
        PER_POSITION_ROWS      NOT_PRESENT
        HISTORICAL_BOOK_STATE  NOT_PRESENT
        EV_EXIT_HISTORICAL     NOT_IDENTIFIED
        JOINT_TIME_BASIS_GRANULARITY  ACCOUNT x INTERVAL (NOT x PRICE_BAND)

So the cell space is NOT the sport x league x market-type x price-band x
time-to-event grid a designer would want. It is:

    ACCOUNT x TIME_UNPAIRED_INTERVAL      (completion hazard)
    ACCOUNT x PRICE_BAND                  (channel economics)

and NOT their cross product. Every richer cell is NOT_IDENTIFIED, and
`cell_support` says so rather than interpolating one.

MILLIONS OF FILLS ARE NOT MILLIONS OF OBSERVATIONS. RN1's artefact carries
ROWS_KEPT = 4,692,866 fills against FIRST_SIDE_ACQUISITIONS = 259,271
positions. The position count is the denominator that means something; the
fill count is a slicing artefact. `effective_n` refuses the fill count by
name.
"""
from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
THIS_MODULE_CONTACTS_NOTHING = True
ORDERS = 0
CAPITAL = 0
CREDENTIALS = "NONE"
mirror_live = False

EVIDENCE_HIERARCHY = ("PRIMARY_WHALE_OBSERVATION",
                      "SECONDARY_PUBLIC_RESEARCH",
                      "TERTIARY_BETTOR_NATIVE")

# ---------------------------------------------------------------------------
# PART IV -- WHAT THE FILLS DO AND DO NOT IDENTIFY
# ---------------------------------------------------------------------------
#
# The archive observes FILLS and economic OUTCOMES. It does not observe the
# resting quotes that never filled, the cancels, the opportunities declined, or
# the contemporaneous queue. So the fill distribution is identified and the
# POLICY that produced it is not. Millions of observed fills do not identify
# one unobserved decision.
WHALE_FILL_STATE_DISTRIBUTION = "IDENTIFIED_FROM_RECONSTRUCTION"
WHALE_ORDER_POLICY = NOT_IDENTIFIED
WHALE_OPPORTUNITY_SELECTION_POLICY = NOT_IDENTIFIED
UNOBSERVED_IN_THE_ARCHIVE = (
    "RESTING_QUOTES_THAT_NEVER_FILLED", "CANCELS", "MISSED_FILLS",
    "DECLINED_OPPORTUNITIES", "CONTEMPORANEOUS_QUEUE", "ALTERNATIVE_ACTIONS",
    "INTERNAL_MODEL_PREDICTIONS",
)
WHY_POLICY_IS_NOT_IDENTIFIED = (
    "a fill is the intersection of their intention and someone else's; "
    "without the unfilled quotes the intention is not recoverable")

# ---------------------------------------------------------------------------
# PART V -- MECHANISM TAXONOMY. The whales are NOT one strategy.
# ---------------------------------------------------------------------------
M_TWO_SIDED_PASSIVE_MM = "TWO_SIDED_PASSIVE_MARKET_MAKING"
M_COMPLETION = "COMPLETION"
M_CAPITAL_RECYCLING = "CAPITAL_RECYCLING"
M_RESIDUAL_INVENTORY_RISK = "RESIDUAL_INVENTORY_RISK"
M_DIRECTIONAL_PRICING = "DIRECTIONAL_PRICING"
M_SETTLEMENT_HOLD = "SETTLEMENT_HOLD"
M_LIVE_DIRECTIONAL_PRICING = "LIVE_DIRECTIONAL_PRICING"
M_IN_PLAY_EXECUTION = "IN_PLAY_EXECUTION"
M_HEDGE_INVENTORY_CLOSE = "HEDGE_INVENTORY_CLOSE"

MECHANISMS = (M_TWO_SIDED_PASSIVE_MM, M_COMPLETION, M_CAPITAL_RECYCLING,
              M_RESIDUAL_INVENTORY_RISK, M_DIRECTIONAL_PRICING,
              M_SETTLEMENT_HOLD, M_LIVE_DIRECTIONAL_PRICING,
              M_IN_PLAY_EXECUTION, M_HEDGE_INVENTORY_CLOSE)

ACCOUNT_MECHANISMS = {
    "rn1": (M_TWO_SIDED_PASSIVE_MM, M_COMPLETION, M_CAPITAL_RECYCLING,
            M_RESIDUAL_INVENTORY_RISK),
    "ferrarichampions2026": (M_TWO_SIDED_PASSIVE_MM, M_COMPLETION,
                             M_RESIDUAL_INVENTORY_RISK),
    "homerunhazard": (M_DIRECTIONAL_PRICING, M_SETTLEMENT_HOLD, M_COMPLETION),
    "swisstony": (M_LIVE_DIRECTIONAL_PRICING, M_IN_PLAY_EXECUTION,
                  M_HEDGE_INVENTORY_CLOSE, M_CAPITAL_RECYCLING),
}

# Different estimands may not be averaged merely because the states share a
# sport or a price. RN1/Ferrari completion evidence answers "will the other
# side come to me and at what basis". HRH directional evidence answers "is this
# contract mispriced". Pooling them produces a number about nothing.
NEVER_POOL_ACROSS = ((M_COMPLETION, M_DIRECTIONAL_PRICING),
                     (M_TWO_SIDED_PASSIVE_MM, M_LIVE_DIRECTIONAL_PRICING))


def mechanisms_of(account):
    return ACCOUNT_MECHANISMS.get(str(account).lower(), ())


def poolable(mech_a, mech_b):
    """May two mechanisms' estimates be combined into one prior?"""
    if mech_a == mech_b:
        return True, "SAME_MECHANISM"
    for a, b in NEVER_POOL_ACROSS:
        if {mech_a, mech_b} == {a, b}:
            return False, "DIFFERENT_ESTIMANDS_%s_VS_%s" % (a, b)
    return False, "NOT_ESTABLISHED_THAT_THESE_ARE_THE_SAME_ESTIMAND"


# ---------------------------------------------------------------------------
# PART VII -- EFFECTIVE SAMPLE SIZE
# ---------------------------------------------------------------------------
HIERARCHY = ("EVENT", "MARKET", "POSITION", "FILL")
FILL_COUNT_IS_NOT_SAMPLE_SIZE = True
WHY_FILL_COUNT_IS_REFUSED = (
    "one economic decision sliced into many fills is one observation; RN1's "
    "4,692,866 fills arise from 259,271 first-side acquisitions, a ratio of "
    "about 18 to 1, and treating fills as independent would overstate "
    "precision by roughly its square root")

# Which clustering level the retained artefacts can actually support.
CLUSTER_LEVEL_AVAILABLE = "POSITION"
CLUSTER_LEVEL_PREFERRED = "EVENT"
EVENT_IDS_IN_WHALE_ARTEFACTS = "NOT_PRESENT"
UNCERTAINTY_METHOD = "POSITION_CLUSTERED_BINOMIAL_WITH_EVENT_INFLATION_UNKNOWN"
WHY_METHOD_IS_A_FLOOR = (
    "positions within one event are correlated and the artefacts carry no "
    "event id, so a position-clustered interval is a LOWER BOUND on the true "
    "width; an event-blocked bootstrap is the right method and needs event ids")


def effective_n(n_fills=None, n_positions=None, n_markets=None,
                n_events=None):
    """The sample size that may be used, and the one that may not.

    Returns the best AVAILABLE clustering level with its count, and records the
    levels that are absent rather than falling back to the fill count.
    """
    out = {
        "N_FILLS": n_fills if n_fills is not None else NOT_IDENTIFIED,
        "N_POSITIONS": n_positions if n_positions is not None
        else NOT_IDENTIFIED,
        "N_MARKETS": n_markets if n_markets is not None else NOT_IDENTIFIED,
        "N_EVENTS": n_events if n_events is not None else NOT_IDENTIFIED,
        "FILL_COUNT_IS_NOT_SAMPLE_SIZE": True,
        "WHY_FILL_COUNT_IS_REFUSED": WHY_FILL_COUNT_IS_REFUSED,
    }
    if n_events:
        out["EFFECTIVE_N"] = n_events
        out["EFFECTIVE_N_LEVEL"] = "EVENT"
        out["EFFECTIVE_N_IS_A_FLOOR"] = False
    elif n_positions:
        out["EFFECTIVE_N"] = n_positions
        out["EFFECTIVE_N_LEVEL"] = "POSITION"
        out["EFFECTIVE_N_IS_A_FLOOR"] = False
        out["EFFECTIVE_N_OVERSTATES_PRECISION_BECAUSE"] = (
            "positions within one event are correlated and no event id exists")
    elif n_markets:
        out["EFFECTIVE_N"] = n_markets
        out["EFFECTIVE_N_LEVEL"] = "MARKET"
        out["EFFECTIVE_N_IS_A_FLOOR"] = False
    else:
        out["EFFECTIVE_N"] = NOT_IDENTIFIED
        out["EFFECTIVE_N_LEVEL"] = NOT_IDENTIFIED
        out["WHY_NOT_IDENTIFIED"] = (
            "no clustering level below FILL is available, and the fill count "
            "is refused as a sample size")
    return out


# ---------------------------------------------------------------------------
# PART X -- WHALE SUPPORT LEVEL
# ---------------------------------------------------------------------------
STRONG = "STRONG"
MODERATE = "MODERATE"
WEAK = "WEAK"
OUT_OF_DISTRIBUTION = "OUT_OF_DISTRIBUTION"
SUPPORT_LEVELS = (STRONG, MODERATE, WEAK, OUT_OF_DISTRIBUTION, NOT_IDENTIFIED)

# Preregistered, and deliberately blunt. A finer scale would imply a precision
# the underlying aggregate artefacts do not have.
STRONG_MIN_EFFECTIVE_N = 1000
MODERATE_MIN_EFFECTIVE_N = 200
WEAK_MIN_EFFECTIVE_N = 30
STRONG_MIN_SUPPORTING_ACCOUNTS = 2
RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG = True


def support_level(effective_n_value=None, supporting_accounts=0,
                  mechanism_agreement=None, in_distribution=None,
                  venue_equivalence=None):
    """How much whale evidence stands behind THIS state.

    A raw fill count can never reach STRONG: `effective_n_value` must come from
    `effective_n`, which refuses fills. Two independent accounts agreeing on
    the SAME mechanism is required for STRONG, because one account's habit is
    not a market regularity.
    """
    if in_distribution is False:
        return {"WHALE_SUPPORT_LEVEL": OUT_OF_DISTRIBUTION,
                "WHY": "the state lies outside the region the whales traded"}
    if effective_n_value in (None, NOT_IDENTIFIED) or in_distribution is None:
        return {"WHALE_SUPPORT_LEVEL": NOT_IDENTIFIED,
                "WHY": "effective sample size or distributional support is "
                       "not established for this state"}

    n = int(effective_n_value)
    if venue_equivalence == "NONE":
        lvl, why = WEAK, "the mechanism has no PMUS equivalent"
    elif (n >= STRONG_MIN_EFFECTIVE_N
            and supporting_accounts >= STRONG_MIN_SUPPORTING_ACCOUNTS
            and mechanism_agreement is True
            and venue_equivalence in ("STRONG", "PARTIAL")):
        lvl, why = STRONG, "multiple accounts, one mechanism, adequate n"
    elif n >= MODERATE_MIN_EFFECTIVE_N:
        lvl, why = MODERATE, "adequate n but not multi-account agreement"
    elif n >= WEAK_MIN_EFFECTIVE_N:
        lvl, why = WEAK, "thin but non-empty"
    else:
        lvl, why = OUT_OF_DISTRIBUTION, "effectively no whale observation here"
    return {"WHALE_SUPPORT_LEVEL": lvl, "WHY": why, "EFFECTIVE_N": n,
            "SUPPORTING_ACCOUNTS": supporting_accounts,
            "RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG": True}


# ---------------------------------------------------------------------------
# PART XI -- VENUE-MECHANISM EQUIVALENCE
# ---------------------------------------------------------------------------
#
# The whale archive is legacy Polymarket: TWO tokens per market, and a pair is
# completed by holding both and merging. PMUS is ONE book per market with a
# long and a short side. The ECONOMIC PAYOFF of "hold both sides" maps onto
# "flatten the inventory", but the IMPLEMENTATION, the fill behaviour and the
# queue mechanics do not follow from that mapping.
VENUE_EQUIVALENCE_VALUES = ("STRONG", "PARTIAL", "NONE", NOT_IDENTIFIED)
MECHANISM_VENUE_EQUIVALENCE = {
    M_COMPLETION: "PARTIAL",
    M_TWO_SIDED_PASSIVE_MM: "PARTIAL",
    M_CAPITAL_RECYCLING: "PARTIAL",
    M_RESIDUAL_INVENTORY_RISK: "STRONG",
    M_DIRECTIONAL_PRICING: "STRONG",
    M_SETTLEMENT_HOLD: "STRONG",
    M_LIVE_DIRECTIONAL_PRICING: "STRONG",
    M_IN_PLAY_EXECUTION: "PARTIAL",
    M_HEDGE_INVENTORY_CLOSE: NOT_IDENTIFIED,
}
EQUIVALENCE_NOTE = {
    M_COMPLETION: ("same economic payoff -- two legs redeeming $1 maps to a "
                   "flattened PMUS book -- but NOT the same implementation, "
                   "fill behaviour or queue mechanics"),
    M_HEDGE_INVENTORY_CLOSE: ("no external hedge instrument is established "
                              "for PMUS contracts"),
}


def venue_equivalence(mechanism):
    return {"MECHANISM": mechanism,
            "VENUE_MECHANISM_EQUIVALENCE":
                MECHANISM_VENUE_EQUIVALENCE.get(mechanism, NOT_IDENTIFIED),
            "NOTE": EQUIVALENCE_NOTE.get(mechanism, ""),
            "SAME_PAYOFF_DOES_NOT_IMPLY_SAME_EXECUTION": True}


# ---------------------------------------------------------------------------
# PART XXVIII / XXX -- PRIORS FEED COMPONENTS, AND DECAY
# ---------------------------------------------------------------------------
#
# There is no "EV = 50% whale + 50% BETTOR". The whale evidence is a prior on
# NAMED COMPONENTS, each updated by BETTOR's own observations of that same
# component.
PRIOR_COMPONENTS = ("P_COMPLETION", "COMPLETION_HAZARD", "PAIR_CLOSE_COST",
                    "RESIDUAL_OUTCOME", "SIZE_CAPACITY", "TIME_IN_INVENTORY",
                    "MARKET_CLASS_PERFORMANCE")
NO_SINGLE_BLENDED_SCORE = True

DECAY_REASONS = ("NATIVE_EVIDENCE_ACCUMULATED", "REGIME_SHIFT",
                 "HISTORICAL_EDGE_DECAYED", "VENUE_MECHANICS_DIFFER",
                 "CURRENT_EVIDENCE_CONTRADICTS_PRIOR")


def blend_weights(prior_effective_n, native_effective_n,
                  credibility_n=None, regime_shift=False,
                  decay_reason=None):
    """How much of a component comes from the whales, how much from us.

    Standard credibility: NATIVE_WEIGHT = n / (n + k). The k is the native
    count at which BETTOR's own evidence carries half the weight. Both counts
    must be EFFECTIVE counts -- passing a fill count here reintroduces the
    error `effective_n` exists to prevent, so a caller who has not gone
    through it is not protected.

    A declared REGIME_SHIFT sends the prior weight to zero rather than
    decaying it: if the world changed, old evidence is not weak evidence about
    the new world, it is evidence about a different one.
    """
    if regime_shift:
        return {"PRIOR_WEIGHT": 0.0, "NATIVE_WEIGHT": 1.0,
                "REGIME_SHIFT_FLAG": True,
                "PRIOR_DECAY_REASON": decay_reason or "REGIME_SHIFT",
                "WHY": "a regime shift makes the prior evidence about a "
                       "different world, not weak evidence about this one"}
    if native_effective_n in (None, NOT_IDENTIFIED):
        return {"PRIOR_WEIGHT": 1.0, "NATIVE_WEIGHT": 0.0,
                "REGIME_SHIFT_FLAG": False,
                "PRIOR_DECAY_REASON": None,
                "WHY": "no BETTOR-native evidence on this component yet"}
    if prior_effective_n in (None, NOT_IDENTIFIED):
        return {"PRIOR_WEIGHT": 0.0, "NATIVE_WEIGHT": 1.0,
                "REGIME_SHIFT_FLAG": False,
                "PRIOR_DECAY_REASON": "NO_PRIOR_FOR_THIS_COMPONENT",
                "WHY": "nothing to shrink towards"}
    k = float(credibility_n if credibility_n is not None else 200)
    n = float(native_effective_n)
    w_native = n / (n + k)
    return {"PRIOR_WEIGHT": round(1.0 - w_native, 6),
            "NATIVE_WEIGHT": round(w_native, 6),
            "CREDIBILITY_N": k,
            "REGIME_SHIFT_FLAG": False,
            "PRIOR_DECAY_REASON": decay_reason,
            "NATIVE_EFFECTIVE_N": n,
            "BOTH_COUNTS_MUST_BE_EFFECTIVE": True}


# ---------------------------------------------------------------------------
# PART XXIX -- DAY-ONE WHALE-ANCHORED MODE (designed, NOT activated)
# ---------------------------------------------------------------------------
WHALE_ANCHORED_MODE_ACTIVE = False
WHALE_ANCHORED_MODE_IS = "A_LAUNCH_SAFEGUARD_NOT_A_PERMANENT_RESTRICTION"
ROUTE_A = "WHALE_SUPPORTED_MECHANISM_PLUS_POSITIVE_BETTOR_EV"
ROUTE_B = "INDEPENDENTLY_VALIDATED_STRUCTURAL_OPPORTUNITY"


def day1_admission(whale_support=None, bettor_ev_positive=None,
                   structural_validated=False, mechanism=None):
    """Would WHALE_ANCHORED_MODE admit this opportunity? Reporting only.

    Returns ADMIT_SHADOW or ADMIT_CAPITAL_CANDIDATE -- never an instruction to
    trade. WHALE_ANCHORED_MODE_ACTIVE is False and this function does not read
    it, because a design that could be switched on by a constant is not a
    safeguard.
    """
    if structural_validated:
        return {"ROUTE": ROUTE_B, "DECISION": "ADMIT_CAPITAL_CANDIDATE",
                "WHY": "edge does not depend on an unproven directional model",
                "ACTIVATED": False}
    if whale_support in (STRONG, MODERATE) and bettor_ev_positive is True:
        return {"ROUTE": ROUTE_A, "DECISION": "ADMIT_CAPITAL_CANDIDATE",
                "WHY": "whale support for the same mechanism AND positive "
                       "current BETTOR EV",
                "MECHANISM": mechanism, "ACTIVATED": False}
    if whale_support == OUT_OF_DISTRIBUTION:
        return {"ROUTE": None, "DECISION": "SHADOW",
                "WHY": "out of distribution: shadow until independently "
                       "promoted", "ACTIVATED": False}
    return {"ROUTE": None, "DECISION": "SHADOW",
            "WHY": "neither route satisfied", "ACTIVATED": False}


def render_bridge():
    L = ["%-28s %s" % ("EVIDENCE_HIERARCHY", " > ".join(EVIDENCE_HIERARCHY)),
         "%-28s %s" % ("WHALE_ORDER_POLICY", WHALE_ORDER_POLICY),
         "%-28s %s" % ("WHALE_ANCHORED_MODE_ACTIVE",
                       WHALE_ANCHORED_MODE_ACTIVE)]
    for a in sorted(ACCOUNT_MECHANISMS):
        L.append("%-28s %s" % (a, ", ".join(ACCOUNT_MECHANISMS[a])))
    return "\n".join(L)


if __name__ == "__main__":                                    # pragma: no cover
    print(render_bridge())
