"""PMUS fee REGIMES, taker rebate tiers, and the maker/taker EV comparison.

PROVENANCE, stated before any number is used.

`research/run85_trackb_fees.py` is a SEALED, verified implementation of the
PMUS fee rules, with 22 tests reproducing the documentation's own worked
examples. It is not edited here. This module imports its primitives and adds
only what is new: a second fee regime, the taker rebate tiers, and the EV
comparison the maker-first architecture needs.

  Fee = THETA * C * p * (1 - p)
  rounded to the nearest $0.01, BANKER'S rounding, per fill, independently.
  Cancelled and expired orders incur nothing -- fees and rebates occur only
  on execution.

  regime JUL2026 (verified, in the sealed module):
      THETA_TAKER = +0.06      THETA_MAKER = -0.0125

  regime SEP2026 (EXTERNALLY RELAYED, see below):
      THETA_TAKER = +0.0695    THETA_MAKER = -0.0125   (maker unchanged)
      effective 23:59 ET Wed 2026-09-16 = 03:59 UTC Thu 2026-09-17

VERIFICATION STATUS. This container has no egress to any Polymarket host --
docs.polymarket.us and gateway.polymarket.us both return 000 -- so the new
coefficient could NOT be checked here against the primary source. It arrives
relayed. Two things make it usable rather than assumed:

  * it is CORROBORATED IN STRUCTURE by our own earlier verified read of the
    same page: identical formula, identical maker coefficient, identical
    rounding, only the taker coefficient moved;
  * the forward collector now FETCHES the fee page and the incentives
    endpoint on every segment and stores them verbatim, so the number stops
    being relayed and becomes captured evidence within one capture cycle.

Until a capture confirms it, `THETA_TAKER_SEP2026_VERIFIED = False` and every
figure computed from it carries that label. The regime boundary is applied by
TIMESTAMP so observations either side are never silently pooled.

WHAT IS NOT VERIFIED AND IS NOT INVENTED HERE:
  * whether BETTOR qualifies for any accelerated taker rebate tier
    -> TIER_VERIFIED = False, and the base tier is the default everywhere.
  * the Market Maker Program's negotiated terms
    -> NEGOTIATED_MARKET_MAKER_ECONOMICS = NOT_IDENTIFIED. Public economics
       are NOT assumed to be BETTOR's ceiling.
  * any liquidity reward actually earned -> ESTIMATED_REWARD is kept strictly
    apart from ACTUAL_REWARD and never added into trading P&L.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from run85_trackb_fees import (  # noqa: E402
    THETA_MAKER, THETA_TAKER, bankers_cents, exact_fee,
)

# ---------------------------------------------------------------- regimes --

THETA_TAKER_JUL2026 = THETA_TAKER            # D("0.06"), verified
THETA_TAKER_SEP2026 = D("0.0695")            # relayed, not yet captured
THETA_MAKER_ALL = THETA_MAKER                # D("-0.0125"), unchanged

# 23:59 ET on Wed 2026-09-16. September ET is EDT = UTC-4, so 03:59 UTC on
# Thu 2026-09-17. Stated as UTC because every capture clock is UTC.
REGIME_CUTOVER_UTC = datetime(2026, 9, 17, 3, 59, tzinfo=timezone.utc)

# ------------------------------------------------- THREE SEPARATE FACTS ----
#
# These are kept apart on purpose. Conflating them is how a published schedule
# silently becomes an assumed entitlement.
#
#   PUBLIC_FEE_SCHEDULE      what the venue publishes for everyone.
#   BETTOR_TIER_ELIGIBILITY  which of those terms BETTOR actually qualifies
#                            for. A schedule existing is not a grant.
#   NEGOTIATED_ECONOMICS     bilateral Market Maker Program terms. Public
#                            economics are NOT assumed to be BETTOR's ceiling
#                            OR its floor.
PUBLIC_FEE_SCHEDULE = "DOCUMENTED"
BETTOR_TIER_ELIGIBILITY = "NOT_IDENTIFIED"
NEGOTIATED_ECONOMICS = "NOT_IDENTIFIED"

# Retained under its old name so existing readers keep working.
NEGOTIATED_MARKET_MAKER_ECONOMICS = NEGOTIATED_ECONOMICS

# The SEP2026 coefficient is documented but has not yet been seen IN FORCE by
# our own capture. It flips only when a segment crosses the cutover and
# captures the changed authoritative state -- not when a doc page is read.
THETA_TAKER_SEP2026_VERIFIED = False
VERIFIED_FROM_CAPTURE = False
TIER_VERIFIED = False

# Prior-month taker volume bands. A rebate REDUCES the taker fee.
#
# The documentation also allows ACCELERATED TIER PLACEMENT on verifiable
# trailing-30-day volume at another prediction market. That is a route, not an
# entitlement: it is an application whose outcome is unknown to us, so it
# changes nothing about BETTOR_TIER_ELIGIBILITY until a placement is granted
# and observed.
ACCELERATED_TIER_PLACEMENT_ROUTE = "DOCUMENTED_APPLICATION_OUTCOME_UNKNOWN"

TAKER_REBATE_TIERS = {
    "BASE": D("0.00"),
    "T10_250k_1M": D("0.10"),
    "T25_1M_10M": D("0.25"),
    "T50_10M_PLUS": D("0.50"),
}

# The prior-month taker NOTIONAL bands each tier requires, in USD. Lower bound
# inclusive, upper bound inclusive, None = unbounded above.
TAKER_REBATE_TIER_BANDS = {
    "BASE": (D("0"), D("249999.99")),
    "T10_250k_1M": (D("250000"), D("999999.99")),
    "T25_1M_10M": (D("1000000"), D("9999999.99")),
    "T50_10M_PLUS": (D("10000000"), None),
}


def tier_for_prior_month_volume(usd) -> str:
    """The tier a prior-month taker notional would earn, from the schedule.

    This reads the PUBLIC SCHEDULE. It is not a statement that BETTOR has that
    volume, and it does not set BETTOR_TIER_ELIGIBILITY.
    """
    v = D(str(usd))
    for name in ("T50_10M_PLUS", "T25_1M_10M", "T10_250k_1M", "BASE"):
        lo, hi = TAKER_REBATE_TIER_BANDS[name]
        if v >= lo and (hi is None or v <= hi):
            return name
    return "BASE"


def regime_at(ts) -> str:
    """Which fee regime an observation belongs to. Never pool across these."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return "SEP2026" if ts >= REGIME_CUTOVER_UTC else "JUL2026"


def theta_taker(regime: str) -> D:
    if regime == "SEP2026":
        return THETA_TAKER_SEP2026
    if regime == "JUL2026":
        return THETA_TAKER_JUL2026
    raise ValueError("unknown fee regime: %r" % (regime,))


def effective_theta_taker(regime: str, tier: str = "BASE") -> D:
    """Base theta reduced by the prior-month rebate tier.

    The tier is a REBATE ON THE FEE, so it scales theta down. BASE is the
    default everywhere because TIER_VERIFIED is False.
    """
    if tier not in TAKER_REBATE_TIERS:
        raise ValueError("unknown taker rebate tier: %r" % (tier,))
    return theta_taker(regime) * (D("1") - TAKER_REBATE_TIERS[tier])


def taker_fee_at(contracts, price, regime: str, tier: str = "BASE") -> D:
    """A charge, positive, banker-rounded to the cent, per fill."""
    return bankers_cents(exact_fee(effective_theta_taker(regime, tier),
                                   contracts, price))


def maker_rebate_at(contracts, price) -> D:
    """A rebate, returned POSITIVE as an amount received. Regime-invariant.

    The negation happens BEFORE the rounding, matching the sealed module's
    order exactly. Banker's rounding is symmetric about zero so the two orders
    agree numerically, but the convention is kept identical so the two modules
    can never drift apart on a sign.
    """
    return bankers_cents(-exact_fee(THETA_MAKER_ALL, contracts, price))


# ------------------------------------------------------------------- EV ----
#
# Per CONTRACT, in dollars, on the exact quantities the architecture names.
# Nothing here invents a fill probability: these are the values CONDITIONAL
# on a fill, which is what EV_MAKER vs EV_TAKER must compare. Multiplying by
# a fill probability is a separate, later, measured step.

def ev_taker(fair_value, ask, regime, tier="BASE", contracts=1):
    """Buy at the ask, pay the taker fee.

    edge = fair - ask, then the fee. Adverse selection is NOT subtracted here
    because a taker chooses its moment; the maker is the one selected against.
    """
    px = D(str(ask))
    edge = (D(str(fair_value)) - px) * D(contracts)
    fee = taker_fee_at(contracts, px, regime, tier)
    return {"channel": "TAKER", "regime": regime, "tier": tier,
            "gross_edge": edge, "fee": -fee, "rebate": D("0"),
            "net": edge - fee}


def ev_maker(fair_value, quote, adverse_selection_per_contract,
             expected_reward_per_contract=D("0"), contracts=1):
    """Rest a quote, get filled, earn the rebate, suffer adverse selection.

    `quote` is the price OUR order rests at. For a resting BID that is what we
    pay; the gross edge is fair - quote. Adverse selection is subtracted as a
    positive cost, and is the thing the forward shadow must MEASURE rather
    than assume.

    `expected_reward_per_contract` is the liquidity-incentive contribution and
    it is kept as its own field so TRADING_PNL_EX_INCENTIVES is always
    recoverable. It defaults to ZERO: a reward is never assumed.
    """
    px = D(str(quote))
    edge = (D(str(fair_value)) - px) * D(contracts)
    reb = maker_rebate_at(contracts, px)
    adv = D(str(adverse_selection_per_contract)) * D(contracts)
    rew = D(str(expected_reward_per_contract)) * D(contracts)
    trading_only = edge + reb - adv
    return {"channel": "MAKER", "gross_edge": edge, "rebate": reb,
            "adverse_selection": -adv, "estimated_reward": rew,
            "trading_net_ex_incentives": trading_only,
            "net": trading_only + rew}


def ev_no_trade():
    return {"channel": "NO_TRADE", "net": D("0")}


def breakeven_adverse_selection(fair_value, quote,
                                expected_reward_per_contract=D("0"),
                                contracts=1) -> D:
    """The most adverse selection a filled maker contract can absorb.

    Solves  edge + rebate + reward - adverse = 0  for adverse. Above this
    value the quote is a losing trade no matter how attractive the rebate
    looks, which is the control variable the shadow engine needs.
    """
    px = D(str(quote))
    edge = (D(str(fair_value)) - px) * D(contracts)
    reb = maker_rebate_at(contracts, px)
    rew = D(str(expected_reward_per_contract)) * D(contracts)
    return edge + reb + rew


def max_tolerable_adverse_selection(fair_value, quote, contracts=1,
                                    verified_incentive_per_contract=D("0")):
    """THE CORE CONTROL, reported in BOTH forms, always together.

    EX_INCENTIVES is the one that decides. It answers "does the underlying
    trade make money", and an incentive can never move it. INCL_VERIFIED_
    INCENTIVES answers "does the whole package make money", and is only
    allowed to use incentives that have been VERIFIED -- an observed, earned,
    settled amount, never a program that merely exists.

    Reporting only the second is how an incentive hides a losing trade, so
    this returns both plus the contribution that separates them.
    """
    ex = breakeven_adverse_selection(fair_value, quote, D("0"), contracts)
    rew = D(str(verified_incentive_per_contract)) * D(contracts)
    return {
        "MAX_TOLERABLE_ADVERSE_SELECTION_EX_INCENTIVES": ex,
        "MAX_TOLERABLE_ADVERSE_SELECTION_INCL_VERIFIED_INCENTIVES": ex + rew,
        "INCENTIVE_CONTRIBUTION": rew,
        "INCENTIVE_IS_VERIFIED": rew != 0,
        "EX_INCENTIVES_PER_CONTRACT": ex / D(contracts) if contracts else D(0),
    }


# ------------------------------------------------- the incentive channels --
#
# FOUR programs, economically different, NEVER blended. Each answers a
# different question about the same resting order, and summing them before
# each is separately verified would double-count presence as execution.
#
#   LIQUIDITY_INCENTIVE  pays for RESTING, by quote position and size.
#                        Earned without ever trading.
#   FILL_INCENTIVE       pays a resting order that ACTUALLY FILLS. Requires
#                        execution, so it carries the adverse selection that
#                        the liquidity channel does not.
#   VOLUME_INCENTIVE     pays eligible trading volume; the current detailed
#                        documentation states TAKER-SIDE notional for this
#                        program, so it does not reward passive presence and
#                        must never be credited to a maker quote.
#   NEGOTIATED_MM        application-only, bilateral. NOT_IDENTIFIED.
INCENTIVE_CHANNELS = (
    "LIQUIDITY_INCENTIVE",
    "FILL_INCENTIVE",
    "VOLUME_INCENTIVE",
    "NEGOTIATED_MM_INCENTIVE",
)

# What each channel is paid FOR. Used to refuse a credit that the channel
# cannot possibly have generated.
INCENTIVE_CHANNEL_BASIS = {
    "LIQUIDITY_INCENTIVE": "RESTING_PRESENCE",
    "FILL_INCENTIVE": "PASSIVE_EXECUTION",
    "VOLUME_INCENTIVE": "TAKER_SIDE_NOTIONAL",
    "NEGOTIATED_MM_INCENTIVE": "NOT_IDENTIFIED",
}

# Whether /v1/incentives is known to publish every program. It publishes
# liquidityProgram rows; nothing observed proves it covers the others.
INCENTIVES_ENDPOINT_COVERS_ALL_PROGRAMS = "NOT_IDENTIFIED"


# ------------------------------------------------- liquidity reward scoring --

def liquidity_score(order_size, ticks_from_best, discount_factor):
    """SCORE = DiscountFactor^(ticks_from_best) * OrderSize.

    Bid and ask sides score independently. This is the SCORE only -- it is not
    a reward and not a dollar amount. Converting a score into an expected
    payout needs the reward pool AND the total qualifying score across all
    participants, which is not observable from our own quotes alone.
    """
    return D(str(discount_factor)) ** int(ticks_from_best) * D(str(order_size))


def estimated_reward_share(our_score, total_qualifying_score, reward_pool):
    """Our share of a pool, given a TOTAL we do not normally observe.

    Returns NOT_IDENTIFIED unless the caller supplies a real total. The point
    of this signature is to make the missing quantity explicit instead of
    letting an optimistic denominator slip in unnoticed.
    """
    if not total_qualifying_score or D(str(total_qualifying_score)) <= 0:
        return "NOT_IDENTIFIED"
    return (D(str(our_score)) / D(str(total_qualifying_score))
            * D(str(reward_pool)))
