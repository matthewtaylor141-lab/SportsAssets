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

AND THE POSITION COUNT IS NOT AN INDEPENDENT SAMPLE SIZE EITHER. Positions
within one event are correlated, the artefacts carry no event id, so
FIRST_SIDE_ACQUISITIONS is an UPPER BOUND on the independent count and never
the count itself. The module says POSITION_LEVEL_N, reports
INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED, and marks every weight derived from
a position-level count as HEURISTIC and anti-confidence-capped.

EVERY WHALE NUMBER IS CONDITIONED ON WHALE-SELECTED ENTRIES. The archive
contains positions the whales chose to open. It contains nothing about the
markets and prices they looked at and declined. So a band statistic describes
the RETAINED WHALE-ENTERED POSITIONS in that band; it is not a statement about
the band. `SELECTION_CONDITION` travels with every cell, and
`band_statement()` refuses to phrase one without it.
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


class PriorScopeError(RuntimeError):
    """A prior was used outside the scope its evidence supports."""


class JointPriorError(PriorScopeError):
    """A cell was manufactured from two marginals that were never crossed."""


class FillSourceError(PriorScopeError):
    """A fill probability was sourced from something that is not a fill."""


class SelectionConditionError(PriorScopeError):
    """A selection-conditioned statistic was phrased as an unconditional one."""


# ---------------------------------------------------------------------------
# CORRECTION 1 -- SWISSTONY IS A GHOST PRIOR, NOT A PRIMARY ONE
# ---------------------------------------------------------------------------
#
# swisstony was FLAGGED_EXCLUDED in cross_account_table.ROLE before any prior
# was built (frozen at commit ee7329c, 2026-09-15). The retained priors carry
# WHICH_FIELDS_PREVENT_INCLUSION = NOT_IDENTIFIED_IN_THIS_WORKSPACE, but the
# discriminator's own gate report does name the defect, so the FIELD is
# reconstructible even though the RESOLUTION is not:
#
#     BETA48_DATA_GATE.md, `BIGGEST_DISCREPANCY`, item 1 --
#     swisstony's PAIR (merge) channel is a SIGN FLIP between two sources.
#     The external report puts it at -$3,390,000 (-0.7%); our reconstruction
#     measures +$260,123 (+0.104%). Both are near zero against a
#     $249,117,712 traded stake. The DIRECTIONAL channel reconciles almost
#     exactly ($23,357,748 vs $22,160,000), so the disagreement is specific
#     to the pair channel and is not a coverage artefact of the account.
#
# That is exactly the quantity the merge-sign consensus is built on, which is
# why the exclusion cannot be waved through. And the reconciliation residual of
# $0.00 does NOT clear it: an account can re-sum to itself perfectly and still
# disagree with an independent source about the sign of the channel. Those are
# different defects, and the second is the one that was flagged.
PRIMARY_WHALE_PRIOR_ACCOUNTS = ("rn1", "ferrarichampions2026", "homerunhazard")
SENSITIVITY_ONLY_ACCOUNTS = ("swisstony",)
WHALE_PRIOR_3_ACCOUNT = "CANONICAL"
WHALE_PRIOR_4_ACCOUNT_SENSITIVITY = "SENSITIVITY_ONLY_NEVER_CANONICAL"

SWISSTONY_STATUS = "GHOST_PRIOR_SENSITIVITY_ONLY"
SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY = NOT_ESTABLISHED
SWISSTONY_DISPUTED_FIELD = "MERGE_PAIR_CHANNEL_PNL_SIGN_ACCOUNT_LIFETIME"
SWISSTONY_DISPUTED_FIELD_SOURCE = "BETA48_DATA_GATE.md BIGGEST_DISCREPANCY 1"
SWISSTONY_DISPUTE_RESOLVED = NOT_ESTABLISHED
SWISSTONY_WHY_UNRESOLVED = (
    "the two sources disagree on the SIGN of a near-zero quantity and this "
    "workspace holds only one of them; nothing here can adjudicate which "
    "reconstruction is right, so the flag stands")
RECONCILIATION_RESIDUAL_ZERO_DOES_NOT_CLEAR_THE_EXCLUSION = True
WHY_A_ZERO_RESIDUAL_IS_NOT_A_RESOLUTION = (
    "internal reconciliation proves the account re-sums to itself; the "
    "exclusion is about disagreement with an INDEPENDENT source on the sign "
    "of the pair channel. A later residual of zero is not evidence about that")
SWISSTONY_DESCRIPTIVE_USES_STILL_ALLOWED = (
    "SIGN_AGREEMENT_AS_A_DESCRIPTION", "COHORT_TIME_DECAY_DESCRIPTION",
    "MECHANISM_TAXONOMY_REFERENCE")
SWISSTONY_USES_FORBIDDEN = (
    "ENTERING_THE_CANONICAL_PRIOR", "CONTRIBUTING_A_SUPPORTING_ACCOUNT_COUNT",
    "ANY_ECONOMIC_ESTIMATE_BETTOR_ACTS_ON")


def prior_account_set(include_sensitivity=False):
    """Which accounts may stand behind a prior, and under what label.

    The 4-account set is available and is NEVER canonical. A caller who wants
    it must ask for it by name and receives a payload that says so.
    """
    if not include_sensitivity:
        return {"PRIOR": "WHALE_PRIOR_3_ACCOUNT",
                "ACCOUNTS": list(PRIMARY_WHALE_PRIOR_ACCOUNTS),
                "STATUS": WHALE_PRIOR_3_ACCOUNT,
                "SUPPORTING_ACCOUNTS": len(PRIMARY_WHALE_PRIOR_ACCOUNTS),
                "SWISSTONY_STATUS": SWISSTONY_STATUS}
    return {"PRIOR": "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY",
            "ACCOUNTS": list(PRIMARY_WHALE_PRIOR_ACCOUNTS)
            + list(SENSITIVITY_ONLY_ACCOUNTS),
            "STATUS": WHALE_PRIOR_4_ACCOUNT_SENSITIVITY,
            # A sensitivity run reports the primary count. The ghost account
            # buys no support, or the exclusion would be cosmetic.
            "SUPPORTING_ACCOUNTS": len(PRIMARY_WHALE_PRIOR_ACCOUNTS),
            "SWISSTONY_STATUS": SWISSTONY_STATUS,
            "SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY":
                SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY,
            "MAY_BE_PROMOTED_TO_CANONICAL": False}


def assert_canonical(accounts):
    """Refuse a canonical prior that contains a sensitivity-only account."""
    bad = sorted(set(a.lower() for a in accounts)
                 & set(SENSITIVITY_ONLY_ACCOUNTS))
    if bad:
        raise PriorScopeError(
            "%s is %s and may not enter the canonical prior: %s. Use "
            "prior_account_set(include_sensitivity=True), which is labelled "
            "WHALE_PRIOR_4_ACCOUNT_SENSITIVITY and is never canonical."
            % (", ".join(bad), SWISSTONY_STATUS, SWISSTONY_WHY_UNRESOLVED))
    return {"CANONICAL_PRIOR_ACCOUNTS": sorted(set(a.lower()
                                                   for a in accounts)),
            "SWISSTONY_STATUS": SWISSTONY_STATUS}


# ---------------------------------------------------------------------------
# CORRECTION 2 -- EVERY WHALE CELL IS CONDITIONED ON WHALE-SELECTED ENTRIES
# ---------------------------------------------------------------------------
#
# The archive is the set of positions the whales OPENED. The markets they
# looked at and passed on, the prices they refused, the moments they stood
# aside -- none of that is in the data. A band statistic is therefore a
# statement about the retained whale-entered positions in that band, and NOT
# about the band, the price level, or what would happen to an arbitrary entry.
SELECTION_CONDITION = "OBSERVED_WHALE_ENTERED_POSITIONS_ONLY"
SELECTION_CONDITION_MEANS = (
    "the sample is positions the whales chose to open; declined markets, "
    "refused prices and unentered opportunities are absent, so no cell "
    "describes an arbitrary entry at that price")
COUNTERFACTUAL_ENTRY_OUTCOME = NOT_IDENTIFIED
WHALE_EVIDENCE_ALONE_CAN_CREATE_A_TRADE = False
WHAT_THE_PRIOR_MAY_DO = (
    "SHAPE_A_COMPONENT_BETTOR_ALREADY_PRICES",
    "RAISE_OR_LOWER_A_SUPPORT_LEVEL",
    "SEND_AN_OPPORTUNITY_TO_SHADOW")
WHAT_THE_PRIOR_MAY_NOT_DO = (
    "ORIGINATE_A_TRADE", "SUBSTITUTE_FOR_BETTOR_EV",
    "ESTABLISH_THAT_A_PRICE_LEVEL_IS_PROFITABLE")

# Phrasings that drop the conditioning. These are refused by name because the
# unconditional form is the one that reads as a trading rule.
FORBIDDEN_UNCONDITIONAL_PHRASINGS = (
    "price below 0.50 is profitable",
    "buying below 0.50 is profitable",
    "the 0.00-0.50 bands are profitable",
    "cheap contracts are profitable",
    "completion is profitable below 0.50",
)


def band_statement(price_band, channel, sign, accounts=None,
                   sensitivity=False):
    """Phrase a band finding so the conditioning cannot be dropped.

    There is one legal sentence shape, and it names the sample before it names
    the result. Callers that want a headline get this or nothing.

    `sensitivity=True` is the only way a ghost account may appear in the
    sentence, and the sentence then says so.
    """
    if sign not in ("POSITIVE", "NEGATIVE", "DISAGREE", NOT_IDENTIFIED):
        raise SelectionConditionError("unknown sign %r" % (sign,))
    accts = [a.lower() for a in (accounts or PRIMARY_WHALE_PRIOR_ACCOUNTS)]
    if not sensitivity:
        assert_canonical(accts)
    return {
        "STATEMENT": ("among RETAINED WHALE-ENTERED POSITIONS whose first leg "
                      "was acquired in %s, the %s channel was %s for %s%s"
                      % (price_band, channel, sign, ", ".join(accts),
                         " (SENSITIVITY RUN, not the canonical prior)"
                         if sensitivity else "")),
        "PRIOR": ("WHALE_PRIOR_4_ACCOUNT_SENSITIVITY" if sensitivity
                  else "WHALE_PRIOR_3_ACCOUNT"),
        "SELECTION_CONDITION": SELECTION_CONDITION,
        "SELECTION_CONDITION_MEANS": SELECTION_CONDITION_MEANS,
        "IS_NOT_A_STATEMENT_ABOUT": "AN_ARBITRARY_ENTRY_AT_THIS_PRICE",
        "COUNTERFACTUAL_ENTRY_OUTCOME": COUNTERFACTUAL_ENTRY_OUTCOME,
        "CAN_CREATE_A_TRADE_BY_ITSELF": False,
    }


def scan_for_unconditional_phrasing(text):
    """Report any phrasing that states a band result without its conditioning.

    Used by the document test so the correction cannot silently rot back into
    the prose it was written to remove.
    """
    low = str(text).lower()
    return sorted(p for p in FORBIDDEN_UNCONDITIONAL_PHRASINGS if p in low)

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

# ---------------------------------------------------------------------------
# CORRECTION 3 -- A POSITION COUNT IS NOT AN INDEPENDENT EFFECTIVE N
# ---------------------------------------------------------------------------
#
# FIRST_SIDE_ACQUISITIONS counts first-side acquisitions. Several of them can
# belong to one event, one team, one correlated line move -- and no event id
# exists to group them. So the position count BOUNDS the independent count
# from above and does not measure it. An earlier version of this module
# returned EFFECTIVE_N = n_positions with EFFECTIVE_N_IS_A_FLOOR = False; that
# label claimed the very independence the data cannot support, and it is
# withdrawn here.
INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED
WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED = (
    "positions within one event are correlated, the artefacts carry no event "
    "id, and the within-event correlation is therefore unmeasured; the "
    "independent count is bounded above by the position count and is not "
    "otherwise identified")
POSITION_LEVEL_INTERVAL_WIDTH = "LOWER_BOUND_ON_TRUE_CLUSTER_ROBUST_WIDTH"
POSITION_LEVEL_WEIGHTING_STATUS = "HEURISTIC"
POSITION_LEVEL_WEIGHTING_IS_ANTI_CONFIDENCE_CAPPED = True
# The cap is preregistered and deliberately crude: a count that is only an
# upper bound may not drive any component to certainty in either direction.
# Only a DECLARED regime shift reaches 0.0 / 1.0, because that is a statement
# about the world rather than an inference from a count.
ANTI_CONFIDENCE_WEIGHT_CAP = 0.95


def effective_n(n_fills=None, n_positions=None, n_markets=None,
                n_events=None):
    """The counts that exist, the level each belongs to, and what is not known.

    Returns POSITION_LEVEL_N when positions are supplied, and
    INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED unless an EVENT-level count is
    supplied -- the event is the only level at which the artefacts would
    support an independence claim, and they do not carry it.

    There is deliberately NO key called EFFECTIVE_N. The name implied a
    property the data does not have, and removing it forces every caller to
    pick between POSITION_LEVEL_N and INDEPENDENT_EFFECTIVE_N explicitly.
    """
    out = {
        "N_FILLS": n_fills if n_fills is not None else NOT_IDENTIFIED,
        "N_POSITIONS": n_positions if n_positions is not None
        else NOT_IDENTIFIED,
        "N_MARKETS": n_markets if n_markets is not None else NOT_IDENTIFIED,
        "N_EVENTS": n_events if n_events is not None else NOT_IDENTIFIED,
        "FILL_COUNT_IS_NOT_SAMPLE_SIZE": True,
        "WHY_FILL_COUNT_IS_REFUSED": WHY_FILL_COUNT_IS_REFUSED,
        "POSITION_LEVEL_N": (n_positions if n_positions
                             else NOT_IDENTIFIED),
        "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
        "INDEPENDENT_N_UPPER_BOUND": NOT_IDENTIFIED,
        "WEIGHTING_STATUS": POSITION_LEVEL_WEIGHTING_STATUS,
        "ANTI_CONFIDENCE_CAPPED": POSITION_LEVEL_WEIGHTING_IS_ANTI_CONFIDENCE_CAPPED,
    }
    if n_events:
        # The only level at which independence is defensible here: one event
        # is one correlated cluster, so a count of events is a count of
        # clusters. The artefacts do not carry it; a caller who supplies it
        # has joined something this module did not.
        out["INDEPENDENT_EFFECTIVE_N"] = n_events
        out["INDEPENDENT_N_UPPER_BOUND"] = n_events
        out["N_LEVEL_USED"] = "EVENT"
        out["INTERVAL_WIDTH_STATUS"] = "CLUSTER_ROBUST_AT_THE_EVENT_LEVEL"
        out["WEIGHTING_STATUS"] = "EVENT_CLUSTERED"
        out["ANTI_CONFIDENCE_CAPPED"] = False
    elif n_positions:
        out["N_LEVEL_USED"] = "POSITION"
        out["INDEPENDENT_N_UPPER_BOUND"] = n_positions
        out["WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED"] = (
            WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED)
        out["INTERVAL_WIDTH_STATUS"] = POSITION_LEVEL_INTERVAL_WIDTH
        out["POSITION_LEVEL_INTERVAL_WIDTH"] = POSITION_LEVEL_INTERVAL_WIDTH
    elif n_markets:
        # A market is not an event: several markets on one game move together.
        out["N_LEVEL_USED"] = "MARKET"
        out["INDEPENDENT_N_UPPER_BOUND"] = n_markets
        out["WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED"] = (
            "several markets on one event are correlated and no event id "
            "exists; the market count bounds the independent count above")
        out["INTERVAL_WIDTH_STATUS"] = POSITION_LEVEL_INTERVAL_WIDTH
    else:
        out["N_LEVEL_USED"] = NOT_IDENTIFIED
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
# the underlying aggregate artefacts do not have. The thresholds are read
# against POSITION_LEVEL_N, which is an UPPER BOUND on the independent count,
# so clearing one is a necessary condition and never a sufficient one -- and
# the returned payload says that in `SUPPORT_LEVEL_IS_CAPPED_BY`.
STRONG_MIN_POSITION_LEVEL_N = 1000
MODERATE_MIN_POSITION_LEVEL_N = 200
WEAK_MIN_POSITION_LEVEL_N = 30
STRONG_MIN_SUPPORTING_ACCOUNTS = 2
RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG = True


def support_level(position_level_n=None, supporting_accounts=0,
                  mechanism_agreement=None, in_distribution=None,
                  venue_equivalence=None):
    """How much whale evidence stands behind THIS state.

    A raw fill count can never reach STRONG: `position_level_n` must come from
    `effective_n`, which refuses fills. Two independent accounts agreeing on
    the SAME mechanism is required for STRONG, because one account's habit is
    not a market regularity -- and a sensitivity-only account never counts
    towards that total (see `prior_account_set`).

    The count read here is a POSITION-level count and therefore an upper bound
    on the independent count. Every payload carries
    INDEPENDENT_EFFECTIVE_N = NOT_IDENTIFIED so a downstream reader cannot
    mistake a cleared threshold for measured independence.
    """
    base = {"SELECTION_CONDITION": SELECTION_CONDITION,
            "INDEPENDENT_EFFECTIVE_N": NOT_IDENTIFIED,
            "SUPPORT_LEVEL_IS_CAPPED_BY":
                "POSITION_LEVEL_N_IS_AN_UPPER_BOUND_ON_INDEPENDENT_N"}
    if in_distribution is False:
        return dict(base, WHALE_SUPPORT_LEVEL=OUT_OF_DISTRIBUTION,
                    WHY="the state lies outside the region the whales traded")
    if position_level_n in (None, NOT_IDENTIFIED) or in_distribution is None:
        return dict(base, WHALE_SUPPORT_LEVEL=NOT_IDENTIFIED,
                    WHY="position-level sample size or distributional support "
                        "is not established for this state")

    n = int(position_level_n)
    if venue_equivalence == "NONE":
        lvl, why = WEAK, "the mechanism has no PMUS equivalent"
    elif (n >= STRONG_MIN_POSITION_LEVEL_N
            and supporting_accounts >= STRONG_MIN_SUPPORTING_ACCOUNTS
            and mechanism_agreement is True
            and venue_equivalence in ("STRONG", "PARTIAL")):
        lvl, why = STRONG, "multiple accounts, one mechanism, adequate n"
    elif n >= MODERATE_MIN_POSITION_LEVEL_N:
        lvl, why = MODERATE, "adequate n but not multi-account agreement"
    elif n >= WEAK_MIN_POSITION_LEVEL_N:
        lvl, why = WEAK, "thin but non-empty"
    else:
        lvl, why = OUT_OF_DISTRIBUTION, "effectively no whale observation here"
    return dict(base, WHALE_SUPPORT_LEVEL=lvl, WHY=why, POSITION_LEVEL_N=n,
                SUPPORTING_ACCOUNTS=supporting_accounts,
                RAW_FILL_COUNT_ALONE_CANNOT_PRODUCE_STRONG=True)


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
# CORRECTION 4 -- THE JOINT PRICE x TIME PRIOR MAY NOT BE MANUFACTURED
# ---------------------------------------------------------------------------
#
# The source states its own granularity:
#     JOINT_TIME_BASIS_GRANULARITY = ACCOUNT x INTERVAL (NOT x PRICE_BAND)
#
# So the two marginals exist and their cross product does not. A hazard curve
# for "0.00-0.10 contracts after four hours unpaired" was never measured, and
# every route to inventing one is a way of asserting an interaction that was
# not observed:
#
#   MULTIPLY   lambda(band) x lambda(interval) assumes conditional independence
#   INTERPOLATE between bands assumes the surface is smooth in a direction
#              nothing was measured along
#   CROSS      a cross-product table asserts one cell per pair
#   ADD        an additive decomposition is a model, and an unfitted one
#
# The correct answer is the refusal. `cell_support` returns it.
PRICE_TIME_JOINT_PRIOR = NOT_IDENTIFIED
JOINT_PRIOR_CONSTRUCTION_FORBIDDEN = (
    "MULTIPLICATION_OF_MARGINALS", "INTERPOLATION_ACROSS_BANDS",
    "CROSS_PRODUCT_TABLE_CONSTRUCTION", "ADDITIVE_DECOMPOSITION",
)
WHY_THE_JOINT_IS_REFUSED = (
    "the source declares JOINT_TIME_BASIS_GRANULARITY = ACCOUNT x INTERVAL "
    "(NOT x PRICE_BAND); the interaction between band and time-unpaired was "
    "never measured, and every construction route asserts one")

MEASURED_CELL_AXES = ("ACCOUNT", "PRICE_BAND", "TIME_UNPAIRED_INTERVAL",
                      "FILL_SIZE_BUCKET", "ISO_WEEK")
MEASURED_CELLS = (
    ("ACCOUNT", "PRICE_BAND"),
    ("ACCOUNT", "TIME_UNPAIRED_INTERVAL"),
    # Added by the blobs_v3 probe at 100% coverage. Still marginals.
    ("ACCOUNT", "FILL_SIZE_BUCKET"),
    ("ACCOUNT", "ISO_WEEK"),
)
UNMEASURED_AXES = ("SPORT", "LEAGUE", "MARKET_TYPE", "PREGAME_LIVE",
                   "TIME_TO_EVENT", "MARKET_AGE", "TIME_OF_DAY",
                   "SEQUENCE_POSITION", "LIQUIDITY_REGIME")


def cell_support(**axes):
    """Is this cell MEASURED, or is it being manufactured?

    Pass the axes that define the cell, e.g.
    `cell_support(account="rn1", price_band="0.00-0.10")`. A single measured
    marginal is supported. TWO measured marginals together are NOT: that is
    the cross product, and it returns PRICE_TIME_JOINT_PRIOR = NOT_IDENTIFIED
    rather than a number.
    """
    named = tuple(sorted(k.upper() for k, v in axes.items() if v is not None))
    unmeasured = tuple(a for a in named if a in UNMEASURED_AXES)
    if unmeasured:
        return {"CELL": named, "CELL_SUPPORT": NOT_IDENTIFIED,
                "WHY": "no measurement exists on %s" % ", ".join(unmeasured),
                "UNMEASURED_AXES_REQUESTED": unmeasured,
                "INTERPOLATED": False}
    unknown = tuple(a for a in named if a not in MEASURED_CELL_AXES)
    if unknown:
        return {"CELL": named, "CELL_SUPPORT": NOT_IDENTIFIED,
                "WHY": "unrecognised axis %s; absence of a rule is not "
                       "permission" % ", ".join(unknown),
                "INTERPOLATED": False}
    if named in MEASURED_CELLS:
        return {"CELL": named, "CELL_SUPPORT": "MEASURED",
                "SELECTION_CONDITION": SELECTION_CONDITION,
                "INTERPOLATED": False}
    non_account = tuple(a for a in named if a != "ACCOUNT")
    if len(non_account) >= 2:
        return {"CELL": named, "CELL_SUPPORT": NOT_IDENTIFIED,
                "PRICE_TIME_JOINT_PRIOR": PRICE_TIME_JOINT_PRIOR,
                "WHY": WHY_THE_JOINT_IS_REFUSED,
                "CROSSED_MARGINALS": non_account,
                "CONSTRUCTION_ROUTES_FORBIDDEN":
                    list(JOINT_PRIOR_CONSTRUCTION_FORBIDDEN),
                "INTERPOLATED": False}
    return {"CELL": named, "CELL_SUPPORT": NOT_IDENTIFIED,
            "WHY": "this combination of measured axes was not measured",
            "INTERPOLATED": False}


def join_price_and_time(price_band=None, time_unpaired_interval=None,
                        method=None):
    """The explicit route to a joint cell. It raises. That is the whole body.

    Kept as a named function so a future caller reaching for the joint finds a
    refusal with a reason instead of writing the multiplication inline.
    """
    raise JointPriorError(
        "PRICE_TIME_JOINT_PRIOR = %s. Refusing to build (%s x %s) by %s. %s"
        % (PRICE_TIME_JOINT_PRIOR, price_band, time_unpaired_interval,
           method or "any method", WHY_THE_JOINT_IS_REFUSED))


# ---------------------------------------------------------------------------
# CORRECTION 5 -- WHALE COMPLETION HAZARD IS NOT BETTOR'S FILL PROBABILITY
# ---------------------------------------------------------------------------
#
# They are different events measured on different agents at different venues.
#
#   WHALE COMPLETION HAZARD  P(the whale's opposite leg was acquired by t |
#                            still unpaired at t), on legacy Polymarket, by an
#                            account whose order policy is NOT_IDENTIFIED,
#                            over positions THAT ACCOUNT CHOSE TO OPEN.
#
#   BETTOR P_FILL            P(OUR resting order at OUR price, in OUR queue
#                            position, on PMUS, is filled within OUR horizon).
#
# Every input that makes the second a fill probability -- our price, our queue
# position, the venue's matching, our cancel policy -- is absent from the
# first. Seeding P_FILL from the hazard would import a number that describes
# someone else's success at acquiring a second leg and label it our execution.
BETTOR_P_FILL_SOURCE = "BETTOR_NATIVE_ADMITTED_FILLS_REQUIRED"
BETTOR_P_FILL_STATUS = NOT_IDENTIFIED
WHY_P_FILL_IS_NOT_IDENTIFIED = (
    "BETTOR has never had an admitted fill, so there is no fill distribution "
    "to estimate from")
WHALE_COMPLETION_AS_P_FILL = "FORBIDDEN"
FORBIDDEN_P_FILL_SOURCES = (
    "WHALE_COMPLETION_HAZARD", "WHALE_COMPLETION_RATE",
    "WHALE_COMPLETION_GRID", "HAZARD_BY_BASIS_CEILING",
    "CUMULATIVE_COMPLETION_F", "COMPLETION_RATE_1H", "COMPLETION_RATE_5S",
    "COMPLETION_RATE_SETTLEMENT", "RESIDUAL_RATE",
)
WHAT_THE_HAZARD_MAY_INFORM = (
    "P_COMPLETION_OF_A_PAIR_ONCE_WE_ALREADY_HOLD_ONE_LEG",
    "TIME_IN_INVENTORY_EXPECTATION",
    "RESIDUAL_INVENTORY_PROBABILITY")
WHY_THAT_IS_A_DIFFERENT_QUANTITY = (
    "completion asks whether the OTHER SIDE ARRIVES in a market we are "
    "already exposed to; P_FILL asks whether OUR ORDER EXECUTES. The first is "
    "about the market's flow, the second about our queue")


def assert_p_fill_source(source):
    """Machine-enforce where a fill probability may come from."""
    s = str(source).upper()
    for bad in FORBIDDEN_P_FILL_SOURCES:
        if bad in s:
            raise FillSourceError(
                "WHALE_COMPLETION_AS_P_FILL = FORBIDDEN. %r may not seed "
                "BETTOR's P_FILL: %s. BETTOR_P_FILL_SOURCE = %s, and today "
                "BETTOR_P_FILL_STATUS = %s (%s)."
                % (source, WHY_THAT_IS_A_DIFFERENT_QUANTITY,
                   BETTOR_P_FILL_SOURCE, BETTOR_P_FILL_STATUS,
                   WHY_P_FILL_IS_NOT_IDENTIFIED))
    return {"P_FILL_SOURCE": source,
            "BETTOR_P_FILL_SOURCE": BETTOR_P_FILL_SOURCE,
            "WHALE_COMPLETION_AS_P_FILL": WHALE_COMPLETION_AS_P_FILL}


def completion_prior(hazard_value, intended_use):
    """Hand out a completion hazard ONLY for a completion question."""
    if str(intended_use).upper() in ("P_FILL", "FILL_PROBABILITY",
                                     "P_FULL_FILL", "P_PARTIAL_FILL",
                                     "EXECUTION_PROBABILITY"):
        raise FillSourceError(
            "refusing to serve a whale completion hazard as %s: %s"
            % (intended_use, WHY_THAT_IS_A_DIFFERENT_QUANTITY))
    return {"COMPLETION_HAZARD": hazard_value,
            "INTENDED_USE": intended_use,
            "MAY_INFORM": list(WHAT_THE_HAZARD_MAY_INFORM),
            "MAY_NOT_INFORM": "BETTOR_P_FILL",
            "SELECTION_CONDITION": SELECTION_CONDITION,
            "VENUE_OF_MEASUREMENT": "LEGACY_POLYMARKET_TWO_TOKEN",
            "VENUE_OF_APPLICATION": "PMUS_ONE_BOOK"}


# ---------------------------------------------------------------------------
# CORRECTION 8 -- PAIR BASIS ABOVE 1.00 IS A GROSS STRUCTURAL FACT
# ---------------------------------------------------------------------------
#
# MEAN_PAIR_BASIS > 1.00 means the two legs together cost more than the $1 the
# completed pair redeems. That is a fact about the GROSS trade prices, and it
# is exactly as far as the retained data goes. Whether the account ended the
# band down depends on rebates, maker rewards, volume programmes and any other
# incentive -- and FIELD_AVAILABILITY records FEE_REBATE as
# NOT_SEPARATELY_RETAINED. So the net outcome is NOT_IDENTIFIED, and the
# comparison between accounts is a DESCRIPTION of a structural distinction in
# the retained aggregate data, not a verdict on who traded better.
PAIR_BASIS_ABOVE_PAR_IS = "GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES"
PAIR_BASIS_ABOVE_PAR_IS_NOT = "ESTABLISHED_FINAL_NET_LOSS"
NET_OF_INCENTIVES_PAIR_OUTCOME = NOT_IDENTIFIED
INCENTIVES_IN_WHALE_ARTEFACTS = "NOT_SEPARATELY_RETAINED"
INCENTIVES_THAT_COULD_OFFSET = ("MAKER_REBATE", "MAKER_REWARD_PROGRAMME",
                                "VOLUME_TIER", "LIQUIDITY_INCENTIVE",
                                "REFERRAL_OR_FEE_HOLIDAY")
BASIS_COMPARISON_LANGUAGE = "DESCRIPTIVE_NOT_EVALUATIVE"


def pair_basis_reading(account, band, mean_pair_basis):
    """State what a basis figure does and does not establish."""
    above = (isinstance(mean_pair_basis, (int, float))
             and mean_pair_basis > 1.0)
    return {
        "ACCOUNT": account, "PRICE_BAND": band,
        "MEAN_PAIR_BASIS": mean_pair_basis,
        "GROSS_BASIS_ABOVE_PAR": "YES" if above else "NO",
        "ESTABLISHES": (PAIR_BASIS_ABOVE_PAR_IS if above
                        else "GROSS_BASIS_AT_OR_BELOW_PAR"),
        "DOES_NOT_ESTABLISH": PAIR_BASIS_ABOVE_PAR_IS_NOT,
        "NET_OF_INCENTIVES_PAIR_OUTCOME": NET_OF_INCENTIVES_PAIR_OUTCOME,
        "INCENTIVES_IN_WHALE_ARTEFACTS": INCENTIVES_IN_WHALE_ARTEFACTS,
        "SELECTION_CONDITION": SELECTION_CONDITION,
        "LANGUAGE": BASIS_COMPARISON_LANGUAGE,
    }


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
                  decay_reason=None, n_level="POSITION"):
    """How much of a component comes from the whales, how much from us.

    Standard credibility: NATIVE_WEIGHT = n / (n + k). The k is the native
    count at which BETTOR's own evidence carries half the weight. Both counts
    must be CLUSTER-LEVEL counts -- passing a fill count here reintroduces the
    error `effective_n` exists to prevent, so a caller who has not gone
    through it is not protected.

    ANTI-CONFIDENCE CAP. At `n_level="POSITION"` (or MARKET) neither count is
    an independent effective n -- both are upper bounds, because positions
    within an event are correlated and no event id exists. The weighting is
    therefore HEURISTIC, and the weight it can carry is capped at
    ANTI_CONFIDENCE_WEIGHT_CAP in EITHER direction: a bound cannot drive a
    component to certainty. Only a DECLARED regime shift reaches 0.0 / 1.0,
    because that is a statement about the world and not an inference from a
    count.

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
    capped = str(n_level).upper() != "EVENT"
    if capped:
        lo, hi = 1.0 - ANTI_CONFIDENCE_WEIGHT_CAP, ANTI_CONFIDENCE_WEIGHT_CAP
        w_native = min(max(w_native, lo), hi)
    return {"PRIOR_WEIGHT": round(1.0 - w_native, 6),
            "NATIVE_WEIGHT": round(w_native, 6),
            "CREDIBILITY_N": k,
            "REGIME_SHIFT_FLAG": False,
            "PRIOR_DECAY_REASON": decay_reason,
            "NATIVE_N": n,
            "N_LEVEL": str(n_level).upper(),
            "INDEPENDENT_EFFECTIVE_N": (n if not capped else NOT_IDENTIFIED),
            "WEIGHTING_STATUS": ("EVENT_CLUSTERED" if not capped
                                 else POSITION_LEVEL_WEIGHTING_STATUS),
            "ANTI_CONFIDENCE_CAPPED": capped,
            "ANTI_CONFIDENCE_WEIGHT_CAP": (ANTI_CONFIDENCE_WEIGHT_CAP
                                           if capped else None),
            "WHY_CAPPED": (WHY_INDEPENDENT_N_IS_NOT_IDENTIFIED if capped
                           else None),
            "BOTH_COUNTS_MUST_BE_CLUSTER_LEVEL": True}


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
    L = ["%-36s %s" % ("EVIDENCE_HIERARCHY", " > ".join(EVIDENCE_HIERARCHY)),
         "%-36s %s" % ("PRIMARY_WHALE_PRIOR_ACCOUNTS",
                       ", ".join(PRIMARY_WHALE_PRIOR_ACCOUNTS)),
         "%-36s %s" % ("SWISSTONY_STATUS", SWISSTONY_STATUS),
         "%-36s %s" % ("SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY",
                       SWISSTONY_PRIMARY_PRIOR_ELIGIBILITY),
         "%-36s %s" % ("WHALE_SELECTION_CONDITION", SELECTION_CONDITION),
         "%-36s %s" % ("INDEPENDENT_EFFECTIVE_N", INDEPENDENT_EFFECTIVE_N),
         "%-36s %s" % ("PRICE_TIME_JOINT_PRIOR", PRICE_TIME_JOINT_PRIOR),
         "%-36s %s" % ("BETTOR_P_FILL_SOURCE", BETTOR_P_FILL_SOURCE),
         "%-36s %s" % ("WHALE_COMPLETION_AS_P_FILL",
                       WHALE_COMPLETION_AS_P_FILL),
         "%-36s %s" % ("PAIR_BASIS_ABOVE_PAR_IS", PAIR_BASIS_ABOVE_PAR_IS),
         "%-36s %s" % ("WHALE_ORDER_POLICY", WHALE_ORDER_POLICY),
         "%-36s %s" % ("WHALE_ANCHORED_MODE_ACTIVE",
                       WHALE_ANCHORED_MODE_ACTIVE)]
    for a in sorted(ACCOUNT_MECHANISMS):
        tag = " [GHOST]" if a in SENSITIVITY_ONLY_ACCOUNTS else ""
        L.append("%-36s %s%s" % (a, ", ".join(ACCOUNT_MECHANISMS[a]), tag))
    return "\n".join(L)


if __name__ == "__main__":                                    # pragma: no cover
    print(render_bridge())
