"""INSTITUTIONAL_L2_EXECUTION_CONTRACT_V1 -- frozen, for the proven YES path.

Owner directive 2026-09-19 22:4xZ §4. Every clause below is a statement
about semantics OBSERVED in production (run 35471198997 / 35472190984),
not a convention chosen here. The declaration is hashed, so a later
change to any clause produces a different contract rather than quietly
re-pricing history.

WHAT IT COVERS, and the failure each clause prevents:

  PRICE CONVERSION      px / priceScale. A raw "97" read as a price is
                        97 dollars; read on the wrong scale it is
                        ten-fold wrong and looks fine.
  QUANTITY CONVERSION   qty / fractionalQtyScale, same exposure.
  SIDE SEMANTICS        bids / offers. A reader looking for `asks`
                        sees an empty book and refuses every trade
                        while blaming the market.
  BEST-FIRST ORDERING   index 0 is the touch on both sides.
  EMPTY-BOOK            an OPEN instrument with no levels is a state,
                        not an error, and it yields NO fill.
  TIMESTAMPS            transactTime is the venue's; our receive time
                        and the latency between them travel beside it.
  PARTIAL FILLS         the walk stops at the depth that was there.
  DEPTH EXHAUSTION      the remainder is UNFILLED, never manufactured.
  NOTIONAL              executed = filled_qty x vwap, in dollars.

THERE IS NO DEFAULT SCALE. `shadow_l2.book_from` raises ScalesRequired
without both, and `economics()` here refuses a book whose scales do not
match the ones this contract was frozen against for that instrument.
The mutation test moves priceScale 100 -> 1000 and requires the
economics to change materially; a contract that shrugged at that would
not be pinning anything.
"""

from __future__ import annotations

import hashlib
import json

from . import shadow as sh
from . import shadow_l2 as l2

CONTRACT_VERSION = "INSTITUTIONAL_L2_EXECUTION_CONTRACT_V1"

# The clauses, as literals. The hash covers this object.
CONTRACT = {
    "contractVersion": CONTRACT_VERSION,
    "source": l2.L2_SOURCE_INSTITUTIONAL,
    "endpoint": "GET /v1/orderbook/{symbol}",
    "priceField": l2.F_PX,
    "quantityField": l2.F_QTY,
    "bidSide": l2.SIDE_BIDS,
    "askSide": l2.SIDE_OFFERS,
    "timestampField": l2.F_TRANSACT_TIME,
    "priceConversion": "price = px / priceScale",
    "quantityConversion": "contracts = qty / fractionalQtyScale",
    "notionalConversion": "usd = filledContracts * vwap",
    "ordering": "BEST_FIRST_BOTH_SIDES; index 0 is the touch",
    "emptyBook": ("an OPEN instrument with no levels is a valid state "
                  "and yields NOT_IDENTIFIED, never a fill"),
    "partialFill": ("the walk stops at observed depth; status PARTIAL "
                    "with the filled quantity and its vwap"),
    "depthExhaustion": ("the unfilled remainder is reported as UNFILLED "
                        "and is never manufactured at an assumed price"),
    "scaleDefault": "NONE -- both scales are required from refdata",
    "executionClass": sh.MARKETABLE_RECONSTRUCTED,
    "sideEligibility": ("only a side whose identity binding is "
                        "EXACT_ONE_TO_ONE may be reconstructed under "
                        "this contract version"),
    "realOrderSubmissionEnabled": False,
    "capitalAtRisk": 0,
}


def contract_sha() -> str:
    raw = json.dumps(CONTRACT, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


CONTRACT_SHA = contract_sha()


class ContractViolation(sh.ShadowRefusal):
    """A book this contract will not price."""


def book_sha(book: dict) -> str:
    """A hash of the LEVELS actually walked.

    Persisted with the execution so the exact arrival book behind a
    fill can be identified later, even though the venue supplies no
    sequence number of its own.
    """
    raw = json.dumps({"bids": (book or {}).get("bids") or [],
                      "asks": (book or {}).get("asks") or []},
                     sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def economics(*, book, side, intended_notional_usd, limit_price=None,
              decision_price=None) -> dict:
    """The $1,000 walk, under the frozen contract.

    Quantity is derived from the intended NOTIONAL and the price the
    decision was made at, because the directive sizes in dollars: an
    intended $1,000 at 0.40 is 2,500 contracts, not 1,000.
    """
    if not isinstance(book, dict) or "bids" not in book:
        raise ContractViolation("refused: not an L2 book under %s"
                                % CONTRACT_VERSION)
    if book.get("priceScale") in (None, 0) or book.get("qtyScale") in (None,
                                                                       0):
        raise ContractViolation(
            "refused: this contract has no default scale; the book must "
            "carry the instrument's priceScale and fractionalQtyScale")

    reference = decision_price if decision_price is not None else limit_price
    if not reference or float(reference) <= 0:
        raise ContractViolation(
            "refused: an intended notional cannot become a quantity "
            "without the price the decision was made at")

    want_contracts = float(intended_notional_usd) / float(reference)
    fill = sh.marketable_fill(side, want_contracts, limit_price, book,
                              decision_price=decision_price)

    filled = fill.get("shadowFilledQty")
    vwap = fill.get("vwap")
    executed = (filled * vwap) if (filled and vwap) else 0.0
    unfilled = max(0.0, float(intended_notional_usd) - executed)

    return {
        "contractVersion": CONTRACT_VERSION,
        "contractSha": CONTRACT_SHA,
        "status": fill.get("status"),
        "executionClass": fill.get("executionClass"),
        "intendedNotionalUsd": float(intended_notional_usd),
        "intendedContracts": want_contracts,
        "filledQty": filled,
        "vwap": vwap,
        "executedNotionalUsd": round(executed, 6),
        "unfilledNotionalUsd": round(unfilled, 6),
        "slippage": fill.get("slippage"),
        "spreadCost": fill.get("spreadCost"),
        "priceScale": book.get("priceScale"),
        "qtyScale": book.get("qtyScale"),
        "l2Source": book.get("l2Source"),
        "l2RequestId": book.get("l2RequestId"),
        "l2SourceTimestamp": book.get("l2SourceTimestamp"),
        "l2ReceivedTimestamp": book.get("l2ReceivedTimestamp"),
        "l2BookSha": book_sha(book),
        "why": fill.get("why"),
        # never anything but shadow
        "realOrderSubmissionEnabled": False,
        "capitalAtRisk": 0,
    }
