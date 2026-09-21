"""What the venue and the account actually support, and how a quote got here.

TWO THINGS THE DECISION ENGINE GOT WRONG BY NOT HAVING THIS.

1. IT PRICED AN ARBITRAGE THAT CANNOT EXIST ON THIS VENUE.
   bettor_state_capture says it, and the sprint verdict measured it:

       "never derived as 1 - YES, which would assert a no-arbitrage
        identity the taker-pair measurement has already refuted
        (0 of 3,732 observed pairs traded at or below par)"

       Class C -- taker complementary pair: FALSIFIED
           observations 3,732   below par 0   at par 0
           median basis 1.0400  min basis 1.0050
           ask + (1 - bid) = 1 + spread

   The venue publishes ONE long contract per slug. The opposite side's
   ask is the same book read backwards, so buying both asks costs
   1 + the spread BEFORE fees. It is an identity, not an opportunity,
   and the cheapest pair ever observed cost half a cent above par.

   So a complement quote has to carry its PROVENANCE. An independently
   observed sibling book can in principle disagree with par; a derived
   one cannot, ever, and an engine that treats the two alike is pricing
   its own arithmetic.

2. IT LET AN UNKNOWN VENUE AND AN UNKNOWN FEE SCHEDULE PRODUCE AN
   ELIGIBLE ACTION. Reproduced by review: venue="UNVERIFIED_VENUE" with
   fees omitted returned PAIR_BUY with data_quality "OK". Omitted fees
   defaulted to FREE EXECUTION, which is the most optimistic possible
   assumption, silently applied.

CAPABILITIES ARE PER ACCOUNT, NOT PER VENUE FAMILY. A mechanism a
reference retail account is observed to use is not thereby available to
the institutional account. Every capability below is UNKNOWN until an
observation on THAT account establishes it, and UNKNOWN blocks the
action rather than permitting it.
"""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED = "SUPPORTED"
UNSUPPORTED = "UNSUPPORTED"
UNKNOWN = "UNKNOWN"

# How a price reached us. The distinction is economic, not clerical.
OBSERVED = "OBSERVED"          # read from that instrument's own book
DERIVED = "DERIVED"            # computed from the other side, e.g. 1 - bid
ABSENT = "ABSENT"

# Fee mechanics verified on PMUS and recorded in the sprint verdict:
# "fees round to the nearest $0.01, banker's rounding, PER FILL. A
# 1-contract fill at p=0.445 earns a $0.00 rebate; a 10-contract fill
# earns $0.03." Rounding per fill means fee-per-contract is NOT linear
# and small fills can round the whole incentive away.
FEE_ROUNDING_PER_FILL_USD = 0.01


@dataclass(frozen=True)
class VenueCapabilities:
    """What one ACCOUNT on one venue can actually do.

    `holds_both_legs_independently` is the one the pair actions turn on:
    if buying the complement NETS against an existing position instead
    of creating an independent holding, then the cash flow is a CLOSE,
    not an acquisition, and the pair payoff model does not apply at all.
    """
    venue: str
    account_class: str                       # retail | institutional
    holds_both_legs_independently: str = UNKNOWN
    native_merge: str = UNKNOWN
    complement_quote_source: str = UNKNOWN   # OBSERVED | DERIVED | ABSENT
    maker_orders: str = UNKNOWN
    cancel_replace: str = UNKNOWN
    verified_fee_schedule: bool = False

    def permits(self, capability: str) -> bool:
        """UNKNOWN is not permission. This is the whole point."""
        return getattr(self, capability, UNKNOWN) == SUPPORTED

    def blocker_for(self, capability: str) -> str | None:
        v = getattr(self, capability, UNKNOWN)
        if v == SUPPORTED:
            return None
        return "%s_%s_ON_%s_%s" % (capability.upper(), v, self.venue.upper(),
                                   self.account_class.upper())


# THE REGISTRY. Nothing is SUPPORTED here that has not been observed on
# that account. The institutional account's entries are UNKNOWN because
# the reconciliation reads that would establish them have not run --
# see reconcile_read.capability_probe(), which exists for exactly this.
CAPABILITIES = {
    ("polymarket-us", "institutional"): VenueCapabilities(
        venue="polymarket-us",
        account_class="institutional",
        # The venue publishes one long contract per slug and the sibling
        # book is a separate instrument that BETTOR has never read in the
        # same cycle. Until it does, the complement is ABSENT -- and a
        # derived one is not a substitute.
        complement_quote_source=ABSENT,
        holds_both_legs_independently=UNKNOWN,
        native_merge=UNSUPPORTED,        # bettor_merge: not observed
        maker_orders=UNKNOWN,
        cancel_replace=UNKNOWN,
        verified_fee_schedule=False,
    ),
    ("polymarket-us", "retail"): VenueCapabilities(
        venue="polymarket-us",
        account_class="retail",
        complement_quote_source=ABSENT,
        holds_both_legs_independently=UNKNOWN,
        native_merge=UNSUPPORTED,
        maker_orders=UNKNOWN,
        cancel_replace=UNKNOWN,
        verified_fee_schedule=False,
    ),
}


def capabilities(venue: str, account_class: str = "institutional"):
    """What this account can do, or an all-UNKNOWN record.

    An unrecognised venue does NOT fall back to a permissive default.
    It gets a record in which every capability is UNKNOWN, which blocks
    every action that depends on one -- which is what "UNVERIFIED_VENUE
    returned PAIR_BUY" should have done in the first place.
    """
    key = (str(venue or "").lower(), str(account_class or "").lower())
    found = CAPABILITIES.get(key)
    if found is not None:
        return found
    return VenueCapabilities(venue=str(venue or "unknown"),
                             account_class=str(account_class or "unknown"))


def pair_is_an_identity(complement_source: str) -> bool:
    """Is the complement price the same book read backwards?

    If so, ask_yes + ask_no == 1 + spread by construction, and no fee
    schedule or size makes that positive. Class C measured it: 3,732
    observations, zero at or below par, minimum basis 1.0050.
    """
    return complement_source == DERIVED


CLASS_C_FALSIFIED = {
    "class": "C -- taker complementary pair",
    "verdict": "FALSIFIED",
    "observations": 3732,
    "below_par": 0,
    "at_par": 0,
    "median_basis": 1.0400,
    "min_basis": 1.0050,
    "identity": "ask + (1 - bid) = 1 + spread",
    "scope": ("same venue, same population, no cross-venue term. Needs no "
              "fill model and no external population."),
    "consequence": ("a taker pair is not an engine action. It is not "
                    "'unmeasured' -- it was measured and refuted, and an "
                    "engine that re-proposes it is re-running a failed "
                    "experiment."),
}

OPEN_CLASSES = {
    "A": {"name": "passive same-venue complementary maker pair",
          "status": "INSUFFICIENT_EVIDENCE",
          "measured_gross_term": 0.0050,
          "unit": "per share half-spread"},
    "B": {"name": "maker first leg + controlled completion",
          "status": "INSUFFICIENT_EVIDENCE",
          "measured_gross_term": 0.0050,
          "unit": "per share half-spread"},
}


def describe() -> dict:
    return {
        "capabilities": {"%s/%s" % k: vars(v) for k, v in CAPABILITIES.items()},
        "unknown_is_not_permission": True,
        "class_c": CLASS_C_FALSIFIED,
        "open_classes": OPEN_CLASSES,
        "fee_rounding_per_fill_usd": FEE_ROUNDING_PER_FILL_USD,
        "provenance_values": [OBSERVED, DERIVED, ABSENT],
    }
