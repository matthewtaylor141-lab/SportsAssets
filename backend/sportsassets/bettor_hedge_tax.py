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


def describe() -> dict:
    return {
        "definitionSha": DEFINITION_SHA,
        "units": UNITS,
        "par": str(PAR),
        "vsPar": VS_PAR_DEFINITION,
        "vsHold": VS_HOLD_DEFINITION,
        "signConvention": SIGN_CONVENTION,
        "doNotAutoHedge": DO_NOT_AUTO_HEDGE,
    }
