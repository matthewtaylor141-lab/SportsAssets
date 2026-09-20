"""THE HEDGE TAX. WHAT COMPLETING A PAIR COSTS, DEFINED BEFORE IT IS USED.

Owner directive, "CONTINUE THE BUILD" §11:

    "HEDGE_TAX = amount paid above the economically attractive pair
    basis / relative to the appropriate hold alternative, with exact
    definition and units frozen before use. Do not automatically hedge
    because a complement exists."

WHY THE DEFINITION IS FROZEN IN ITS OWN MODULE. A cost that is defined
after the fact can be defined to be small. This file states the
arithmetic, the units, the sign convention and the reference point
BEFORE any engine consumes it, so the number cannot be renegotiated
once it turns out to be inconvenient. The definition hash is derived
from the text below and does not move with a comment edit.

────────────────────────────────────────────────────────────────────
THERE ARE TWO HEDGE TAXES AND THEY ARE NOT THE SAME NUMBER.

1. HEDGE_TAX_VS_PAR -- IDENTIFIED FROM THE BOOK TODAY.

       PAIR_BASIS      = OWN_LEG_AVG_BASIS + COMPLEMENT_PRICE
       HEDGE_TAX_VS_PAR = PAIR_BASIS - 1.00

   A complete pair pays exactly $1 at settlement: one leg wins, the
   other is worthless. So a pair basis above par is money paid for
   certainty, and below par is a locked profit. Positive = a TAX,
   negative = a CREDIT. Units: US DOLLARS PER CONTRACT, per pair, gross
   of fees and gross of incentives.

   This is a STRUCTURAL FACT, computable now, and it is the same
   quantity the frozen whale priors call
   PAIR_BASIS_ABOVE_PAR_IS = GROSS_STRUCTURAL_FACT_BEFORE_INCENTIVES.

2. HEDGE_TAX_VS_HOLD -- NOT_IDENTIFIED, AND THIS IS THE DECISION ONE.

       HEDGE_TAX_VS_HOLD = EV_HOLD - EV_COMPLETE_PAIR

   The real question is never "does the pair cost more than par". It
   is "does the pair cost more than NOT hedging was worth". A tax of
   $0.02 against par is cheap if the unhedged leg was going to lose
   more than that and expensive if it was going to win. Answering it
   needs EV_HOLD, which needs an independent fair value, and
   FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED.

   So this one stays NOT_IDENTIFIED. It is declared anyway, by name,
   because leaving it out would let HEDGE_TAX_VS_PAR quietly become
   "the hedge tax" and be read as the decision criterion it is not.
────────────────────────────────────────────────────────────────────

A COMPLEMENT EXISTING IS NOT A REASON TO BUY IT. The venue will always
quote the other leg. Hedging is justified only when the risk- and
capital-adjusted economics of completing beat holding, and that
comparison is currently unavailable. An engine that hedged whenever a
complement was quoted would be paying a certain cost to avoid an
uncertain one it never measured.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ── the frozen definition ────────────────────────────────────────────

PAR = Decimal("1.00")

UNITS = "USD_PER_CONTRACT"
GROSS_OF = ("FEES", "INCENTIVES", "REBATES", "SLIPPAGE")

SIGN_CONVENTION = (
    "POSITIVE is a TAX -- money paid above par for certainty. NEGATIVE "
    "is a CREDIT -- a pair locked below par, which is profit. The sign "
    "is never flipped for presentation")

VS_PAR_DEFINITION = (
    "HEDGE_TAX_VS_PAR = (OWN_LEG_AVG_BASIS + COMPLEMENT_PRICE) - 1.00, "
    "in %s, gross of %s. A complete pair pays exactly $1 at "
    "settlement, so par is the reference point and nothing about it is "
    "chosen" % (UNITS, ", ".join(GROSS_OF)))

VS_HOLD_DEFINITION = (
    "HEDGE_TAX_VS_HOLD = EV_HOLD - EV_COMPLETE_PAIR, in %s. This is "
    "the decision-relevant quantity and it is NOT_IDENTIFIED: EV_HOLD "
    "requires an independent fair value and FV_BETTOR_INDEPENDENT does "
    "not exist" % UNITS)

WHY_TWO = (
    "a tax measured against par says what the pair costs. A tax "
    "measured against holding says whether the pair is worth it. Only "
    "the first is computable today, and calling it 'the hedge tax' "
    "without qualification would let a structural cost be read as a "
    "decision criterion")

DO_NOT_AUTO_HEDGE = (
    "a complement existing is not a reason to buy it -- the venue "
    "always quotes the other leg. Hedging is justified only when the "
    "risk- and capital-adjusted economics of completing exceed "
    "holding. Paying a certain cost to avoid an uncertain one that was "
    "never measured is not risk management")


def _frozen_text() -> str:
    """The definition, in a fixed order. Prose edits elsewhere do not
    move this; a change to the arithmetic or the units does."""
    return "|".join([
        str(PAR), UNITS, ",".join(GROSS_OF),
        VS_PAR_DEFINITION, VS_HOLD_DEFINITION, SIGN_CONVENTION,
    ])


DEFINITION_SHA = hashlib.sha256(_frozen_text().encode()).hexdigest()[:16]


def _d(v):
    if v is None or v == "" or v == NOT_IDENTIFIED:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def hedge_tax(own_leg_basis=None, complement_price=None) -> dict:
    """Both hedge taxes: the one that is identified, and the one that is not."""
    a, b = _d(own_leg_basis), _d(complement_price)
    out = {
        "definitionSha": DEFINITION_SHA,
        "units": UNITS,
        "grossOf": list(GROSS_OF),
        "signConvention": SIGN_CONVENTION,
        "vsParDefinition": VS_PAR_DEFINITION,
        "vsHoldDefinition": VS_HOLD_DEFINITION,
        "whyTwo": WHY_TWO,
        "doNotAutoHedge": DO_NOT_AUTO_HEDGE,
        # ALWAYS NOT_IDENTIFIED TODAY, and declared so it cannot be
        # silently replaced by the one that is computable.
        "HEDGE_TAX_VS_HOLD": NOT_IDENTIFIED,
        "whyVsHoldNotIdentified": (
            "EV_HOLD requires an independent fair value; "
            "FV_BETTOR_INDEPENDENT is NOT_IDENTIFIED"),
    }
    if a is None or b is None:
        out.update({
            "PAIR_BASIS": NOT_IDENTIFIED,
            "HEDGE_TAX_VS_PAR": NOT_IDENTIFIED,
            "why": ("both the own-leg basis and a complement price are "
                    "needed; neither is assumed"),
        })
        return out

    basis = a + b
    tax = basis - PAR
    out.update({
        "PAIR_BASIS": str(basis),
        "HEDGE_TAX_VS_PAR": str(tax),
        "isATax": tax > 0,
        "isACredit": tax < 0,
        "interpretation": (
            "the pair would be struck %s par by %s per contract"
            % ("ABOVE" if tax > 0 else "BELOW", abs(tax))),
        "isNot": ("an established net outcome: this is gross of fees "
                  "and incentives, and it does not say whether "
                  "completing beats holding"),
    })
    return out


# ══════════════════════════════════════════════════════════════════════
# §11. THE COMPLEMENT ACQUISITION, AND THE FOUR ROUTES IT COMPETES WITH
#
# Owner directive:
#
#     "If we own YES: DIRECT EXIT means selling YES. HEDGE means buying
#     NO. These are different economic routes to reducing directional
#     exposure. For a complement acquisition compute: COMPLEMENT_COST /
#     PAIR_BASIS / PAIR_VALUE / HEDGE_TAX / CAPITAL_RELEASE_IF_COMPLETED.
#     Then compare: EV_HOLD / EV_DIRECT_EXIT / EV_HEDGE /
#     EV_COMPLETE_PAIR. The rule is NOT: always hedge. The rule is: pay
#     the hedge tax only when doing so dominates the alternatives on
#     conservative economics."
# ══════════════════════════════════════════════════════════════════════

# THE TWO ROUTES ARE NOT THE SAME TRADE. Both reduce directional
# exposure on a held YES and they do it through different books, at
# different prices, with different leftovers.
EXIT_VS_HEDGE = {
    "DIRECT_EXIT": {
        "what": "SELL the leg we own",
        "book": "the BID on our own leg",
        "leaves": "nothing. The position is gone and the capital is back",
        "priceRisk": "we take whatever the bid is now",
    },
    "HEDGE": {
        "what": "BUY the complementary leg",
        "book": "the ASK on the other leg",
        "leaves": ("a matched pair. Directional exposure is gone but "
                   "the capital is still in it until the venue lets the "
                   "pair be merged or netted"),
        "priceRisk": "we pay whatever the complement ask is now",
    },
    "whyItMatters": (
        "these are routinely treated as one decision -- 'reduce the "
        "exposure' -- and they are not. DIRECT_EXIT returns capital and "
        "accepts the bid. HEDGE keeps the capital tied up and accepts "
        "the ask. Which dominates depends on the spread, the hedge tax "
        "and whether a merge mechanism exists at all"),
}

ROUTES = ("EV_HOLD", "EV_DIRECT_EXIT", "EV_HEDGE", "EV_COMPLETE_PAIR")

THE_RULE_IS_NOT_ALWAYS_HEDGE = (
    "pay the hedge tax only when doing so DOMINATES the alternatives on "
    "conservative economics. A complement existing is not a reason to "
    "buy it -- the venue always quotes the other leg. Dominance means "
    "beating HOLD, DIRECT_EXIT and COMPLETE_PAIR on the conservative "
    "read, not beating them on the mean")

CONSERVATIVE_MEANS = (
    "the LOWER_CONFIDENCE_VALUE of each route, not its mean. A route "
    "that wins on the mean and loses on the conservative read has not "
    "earned a certain cost paid up front")

WHY_CAPITAL_RELEASE_NOT_IDENTIFIED = (
    "completing a pair only returns capital if the venue lets the "
    "matched quantity be merged or netted. Retail netting is "
    "established; the institutional MERGE_MECHANISM is NOT_IDENTIFIED, "
    "so CAPITAL_RELEASE_IF_COMPLETED is NOT_IDENTIFIED. It is not zero: "
    "zero would assert that completing frees nothing, which is a claim "
    "about the venue we have not established")

PAIR_VALUE_BASIS = (
    "a complete pair pays exactly $1.00 at settlement, which is "
    "structural rather than estimated. What it is worth BEFORE "
    "settlement is a different number and it is NOT_IDENTIFIED: "
    "realising it early needs either a merge mechanism or exit books on "
    "both legs, and neither is established")


def complement_acquisition(*, own_leg=None, own_leg_basis=None,
                           complement_price=None, qty=None,
                           fee=None, merge_mechanism=None) -> dict:
    """§11's five quantities for buying the other leg.

    Per contract AND scaled, because a per-contract number multiplied
    by the wrong size is how an edge becomes a loss.
    """
    a, b, n = _d(own_leg_basis), _d(complement_price), _d(qty)
    tax = hedge_tax(own_leg_basis=own_leg_basis,
                    complement_price=complement_price)

    out = {
        "definitionSha": DEFINITION_SHA,
        "units": UNITS,
        "OWN_LEG": own_leg or NOT_IDENTIFIED,
        "COMPLEMENT_LEG": ({"YES": "NO", "NO": "YES"}.get(own_leg)
                           or NOT_IDENTIFIED),
        "QUANTITY": str(n) if n is not None else NOT_IDENTIFIED,
        # 1. COMPLEMENT_COST
        "COMPLEMENT_COST_PER_CONTRACT": (str(b) if b is not None
                                         else NOT_IDENTIFIED),
        "COMPLEMENT_COST_GROSS": (str(b * n) if b is not None
                                  and n is not None else NOT_IDENTIFIED),
        # Net of fees, which are not declared for this venue leg. Left
        # unidentified rather than zeroed -- a zero fee makes every
        # break-even look reachable.
        "COMPLEMENT_COST_NET": NOT_IDENTIFIED,
        "FEE": str(_d(fee)) if _d(fee) is not None else NOT_IDENTIFIED,
        "whyCostNetNotIdentified": (
            "no fee schedule is declared for this venue leg, so the "
            "net cost of acquiring the complement is not identified. "
            "It is not the gross cost"),
        # 2. PAIR_BASIS
        "PAIR_BASIS": tax["PAIR_BASIS"],
        # 3. PAIR_VALUE -- structural at settlement, unknown before it
        "PAIR_VALUE_AT_SETTLEMENT": str(PAR),
        "PAIR_VALUE_BEFORE_SETTLEMENT": NOT_IDENTIFIED,
        "pairValueBasis": PAIR_VALUE_BASIS,
        # 4. HEDGE_TAX -- both definitions, never collapsed into one
        "HEDGE_TAX_VS_PAR": tax["HEDGE_TAX_VS_PAR"],
        "HEDGE_TAX_VS_HOLD": tax["HEDGE_TAX_VS_HOLD"],
        "whyTwo": WHY_TWO,
        "signConvention": SIGN_CONVENTION,
        # 5. CAPITAL_RELEASE_IF_COMPLETED
        "CAPITAL_RELEASE_IF_COMPLETED": NOT_IDENTIFIED,
        "MERGE_MECHANISM": merge_mechanism or NOT_IDENTIFIED,
        "whyCapitalReleaseNotIdentified": WHY_CAPITAL_RELEASE_NOT_IDENTIFIED,
        "capitalReleaseRequires": ("MERGE_MECHANISM", "MATCHED_QTY"),
        "doNotAutoHedge": DO_NOT_AUTO_HEDGE,
    }
    if tax["HEDGE_TAX_VS_PAR"] != NOT_IDENTIFIED and n is not None:
        out["HEDGE_TAX_VS_PAR_TOTAL"] = str(_d(tax["HEDGE_TAX_VS_PAR"]) * n)
    else:
        out["HEDGE_TAX_VS_PAR_TOTAL"] = NOT_IDENTIFIED
    return out


def compare_routes(*, ev_hold=None, ev_direct_exit=None, ev_hedge=None,
                   ev_complete_pair=None, conservative=True) -> dict:
    """§11's four-way comparison, and the dominance rule over it.

    Values are LOWER_CONFIDENCE reads, not means (see CONSERVATIVE_MEANS).
    Any route that is NOT_IDENTIFIED makes the comparison
    NOT_IDENTIFIED: a missing route cannot be beaten, and treating it
    as zero would let HEDGE win by default against alternatives nobody
    measured.
    """
    vals = {
        "EV_HOLD": ev_hold,
        "EV_DIRECT_EXIT": ev_direct_exit,
        "EV_HEDGE": ev_hedge,
        "EV_COMPLETE_PAIR": ev_complete_pair,
    }
    parsed = {k: _d(v) for k, v in vals.items()}
    missing = [k for k, v in parsed.items() if v is None]

    out = {
        "routes": {k: (str(v) if v is not None else NOT_IDENTIFIED)
                   for k, v in parsed.items()},
        "routesMissing": missing,
        "conservative": bool(conservative),
        "conservativeMeans": CONSERVATIVE_MEANS,
        "theRuleIsNotAlwaysHedge": THE_RULE_IS_NOT_ALWAYS_HEDGE,
        "exitVsHedge": dict(EXIT_VS_HEDGE),
        "doNotAutoHedge": DO_NOT_AUTO_HEDGE,
    }
    if missing:
        out.update({
            "DOMINANCE_STATUS": NOT_IDENTIFIED,
            "HEDGE_PERMITTED": False,
            "why": ("a missing route cannot be beaten. %s are "
                    "NOT_IDENTIFIED, so no route dominates and the "
                    "hedge tax is not paid" % ", ".join(missing)),
            "whyNotZero": ("treating a missing alternative as zero "
                           "would let HEDGE win by default against "
                           "alternatives nobody measured"),
        })
        return out

    best = max(parsed, key=lambda k: parsed[k])
    others = [v for k, v in parsed.items() if k != "EV_HEDGE"]
    dominates = all(parsed["EV_HEDGE"] > v for v in others)
    out.update({
        "DOMINANCE_STATUS": "IDENTIFIED",
        "BEST_ROUTE": best,
        "HEDGE_DOMINATES": dominates,
        "HEDGE_PERMITTED": dominates,
        "why": ("HEDGE %s the other three routes on the %s read"
                % ("dominates" if dominates else "does not dominate",
                   "conservative" if conservative else "mean")),
    })
    return out


def describe() -> dict:
    return {
        "definitionSha": DEFINITION_SHA,
        "units": UNITS,
        "par": str(PAR),
        "vsPar": VS_PAR_DEFINITION,
        "vsHold": VS_HOLD_DEFINITION,
        "signConvention": SIGN_CONVENTION,
        "doNotAutoHedge": DO_NOT_AUTO_HEDGE,
        "routes": list(ROUTES),
        "exitVsHedge": dict(EXIT_VS_HEDGE),
        "theRuleIsNotAlwaysHedge": THE_RULE_IS_NOT_ALWAYS_HEDGE,
        "conservativeMeans": CONSERVATIVE_MEANS,
        "pairValueBasis": PAIR_VALUE_BASIS,
        "whyCapitalReleaseNotIdentified": WHY_CAPITAL_RELEASE_NOT_IDENTIFIED,
        "comparisonToday": compare_routes(),
    }
