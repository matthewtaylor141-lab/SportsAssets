"""THE VENUE'S PUBLISHED FEE SCHEDULE, and what it does and does not bound.

WHY THIS EXISTS. There was no programmatic fee read anywhere in this
repository, so `calibration_evidence` reported FEE_TERMS_NOT_OBTAINED and
no ticket could be built. Management located the official sources and
directed that the documented calculation be implemented rather than
waiting for a dedicated endpoint.

THE SCHEDULE, AND HOW IT GOT HERE

    source          https://docs.polymarket.us/fees
    effective       2026-09-17
    taker           0.0695
    maker rebate   -0.0125

RETRIEVAL PROVENANCE IS RECORDED, NOT ASSUMED. These values were supplied
by the management directive of 2026-09-18, citing the pages above. This
container's egress proxy refuses CONNECT to docs.polymarket.us, so the
pages were NOT fetched here and the numbers are NOT independently
retrieved by this code. `SCHEDULE["RETRIEVED_HERE"]` is False and says
why. That distinction is the whole point of the rest of this module:
a documented expectation and an actual observation are kept apart, and a
ticket is built only when they AGREE.

THE CALCULATION IS EXACT. `Decimal`, never binary float. A fee is money;
`0.0695 * 11 * 0.39` in binary float is not the number the venue
computes, and a cent of drift in the wrong direction is a cent over an
approved cap.

WHAT THE COEFFICIENT MULTIPLIES. The documented form is

    fee = coefficient x quantity x min(price, 1 - price)

`FORMULA_INDEPENDENTLY_RETRIEVED` is False for the same reason the
coefficients are, so this module NEVER lets the computed number stand on
its own. `reconcile()` compares it against the venue's own PREVIEW for
the actual order, and a disagreement beyond the rounding tolerance is
`FEE_SOURCE_DISAGREEMENT` -- a named blocker, not a nudge toward the
computed value. The preview is the observation; this is the expectation.

FEES DEPEND ON QUANTITY, PRICE AND ROLE, so nothing here computes a fixed
fee before sizing. `quote()` takes the sized order. The evidence command
sizes, quotes, and then re-previews the sized order, rather than sizing
against a fee computed for a different quantity.

THE ENTRY IS RESERVED AT THE TAKER RATE EVEN THOUGH IT IS POST-ONLY.
A post-only order is supposed to be a maker, and the maker coefficient is
a REBATE. But this venue has already filled a post-only rest at create
with maker=false (order 153, 2026-09-06, which is why post-only was
switched off process-wide at 17:36Z that day). A reserve that assumes the
venue honours post-only is a reserve that the one observed failure mode
breaks. So the entry reserve is the taker fee.

REBATES ARE NEVER NETTED OFF THE BUDGET. `maker_rebate()` exists to
RECORD what was actually earned, separately. It is not subtracted from a
reserve, not added to remaining spend, and not recycled: the $100 session
allowance is not replenished by anything, and an expected rebate is not
money in hand.

THE EXIT IS BOUNDED WITHOUT KNOWING ITS PRICE. A current preview prices
the order in front of us; it says nothing about an exit that has not
happened. `min(p, 1 - p)` is maximised at p = 0.5, so the worst fee any
single exit order can incur on a given quantity is `coefficient x
quantity x 0.5`. The reserve is that bound times the number of exit
orders the policy permits, because a partial fill leaves a remainder that
needs another order and each one is its own fee event.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

# ── the published schedule, with its provenance ──────────────────────
SCHEDULE = {
    "SOURCES": (
        "https://docs.polymarket.us/fees",
        "https://docs.polymarket.us/api-reference/orders/preview-order",
        "https://docs.polymarket.us/api-reference/markets/get-market-by-slug",
    ),
    "EFFECTIVE_DATE": "2026-09-17",
    "TAKER_COEFFICIENT": "0.0695",
    "MAKER_REBATE_COEFFICIENT": "-0.0125",
    "FORMULA": "coefficient * quantity * min(price, 1 - price)",
    "SUPPLIED_BY": "management directive 2026-09-18, citing the sources above",
    "RETRIEVED_HERE": False,
    "WHY_NOT_RETRIEVED_HERE": (
        "this container's egress proxy refuses CONNECT to docs.polymarket.us "
        "(403, organisation policy), so the pages were not fetched by this "
        "code and the values are a RECORDED EXPECTATION, not an observation"),
    "FORMULA_INDEPENDENTLY_RETRIEVED": False,
    "APPLICABILITY": (
        "binary outcome markets on polymarket.us, per filled share, charged "
        "on execution and dependent on the execution role"),
}

TAKER = Decimal(SCHEDULE["TAKER_COEFFICIENT"])
MAKER = Decimal(SCHEDULE["MAKER_REBATE_COEFFICIENT"])
CENT = Decimal("0.01")

# The worst value of min(p, 1-p) over any admissible price.
WORST_FEE_BASIS = Decimal("0.5")

ROLE_TAKER = "TAKER"
ROLE_MAKER = "MAKER"

# ── named blockers ───────────────────────────────────────────────────
B_SCHEDULE_NOT_APPLICABLE = "FEE_SCHEDULE_NOT_APPLICABLE_TO_THIS_MARKET"
B_SCHEDULE_NOT_EFFECTIVE = "FEE_SCHEDULE_NOT_EFFECTIVE_AT_THIS_TIME"
B_DISAGREEMENT = "FEE_SOURCE_DISAGREEMENT"
B_PREVIEW_UNREADABLE = "FEE_PREVIEW_UNREADABLE"
B_UNSUPPORTED_ROLE = "FEE_ROLE_NOT_SUPPORTED"
B_BAD_INPUT = "FEE_INPUT_NOT_A_NUMBER"
B_EXIT_POLICY = "EXIT_POLICY_NOT_STATED"

NO_INVENTED_DEFAULT = (
    "there is no default fee. A term this module cannot establish for the "
    "market in front of it is a named blocker, and the ticket is not built")

REBATES_ARE_NOT_BUDGET = (
    "an expected maker rebate is not money in hand and never reduces a "
    "reserve or replenishes the session allowance. Actual rebates are "
    "recorded separately and proceeds are not recycled")

A_PREVIEW_DOES_NOT_BOUND_THE_EXIT = (
    "a preview prices the order in front of us. An exit that has not "
    "happened is bounded by the worst fee its permitted orders could "
    "incur, not by today's quote")

# The rounding tolerance when comparing our arithmetic to the venue's.
# One cent: below that the two agree for every purpose this uses.
AGREEMENT_TOLERANCE = Decimal("0.01")


def _d(v):
    """A number as Decimal, via str so a binary float never sneaks in."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or v is None:
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return None


def fee_basis(price):
    """min(price, 1 - price), exactly. None when the price is unreadable."""
    p = _d(price)
    if p is None or p <= 0 or p >= 1:
        return None
    return min(p, Decimal("1") - p)


def _round_up_cent(x):
    """Money the desk must RESERVE rounds up. A reserve that rounds down is
    a reserve that is a cent short exactly when it matters."""
    return x.quantize(CENT, rounding=ROUND_CEILING)


def _round_half_up_cent(x):
    """Money being REPORTED rounds the ordinary way."""
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def quote(price, quantity, role=ROLE_TAKER, at=None):
    """The documented fee for ONE sized order. Never a fixed number.

    Returns {"FEE": Decimal or None, "BLOCKER": name or None, ...}. The
    fee is positive for a taker charge and negative for a maker rebate,
    because they are different directions of money and collapsing them
    would let a rebate look like a cost of zero.
    """
    out = {"role": role, "schedule": SCHEDULE["EFFECTIVE_DATE"],
           "formula": SCHEDULE["FORMULA"],
           "retrievedHere": SCHEDULE["RETRIEVED_HERE"]}
    if role not in (ROLE_TAKER, ROLE_MAKER):
        return dict(out, FEE=None, BLOCKER=B_UNSUPPORTED_ROLE)
    if at is not None and str(at) < SCHEDULE["EFFECTIVE_DATE"]:
        # A schedule that was not in force is not the applicable term.
        return dict(out, FEE=None, BLOCKER=B_SCHEDULE_NOT_EFFECTIVE)
    q = _d(quantity)
    basis = fee_basis(price)
    if q is None or q <= 0 or basis is None:
        return dict(out, FEE=None, BLOCKER=B_BAD_INPUT)

    coef = TAKER if role == ROLE_TAKER else MAKER
    raw = coef * q * basis
    fee = _round_up_cent(raw) if coef > 0 else _round_half_up_cent(raw)
    return dict(out, FEE=fee, BLOCKER=None, basis=basis, coefficient=coef,
                quantity=q, raw=raw)


def entry_reserve(price, quantity, post_only=True):
    """What to reserve for the ENTRY fee.

    AT THE TAKER RATE even for a post-only order. The venue has filled a
    post-only rest at create with maker=false before; a reserve that
    assumes post-only is honoured is broken by the one failure mode that
    has actually been observed. The rebate, if the order really is a
    maker, is recorded afterwards and changes nothing here.
    """
    q = quote(price, quantity, role=ROLE_TAKER)
    q["postOnlyRequested"] = bool(post_only)
    q["whyTakerRate"] = (
        "a post-only order has been filled as a taker on this venue "
        "(order 153, 2026-09-06); the reserve does not assume otherwise")
    return q


def exit_reserve(quantity, policy):
    """What to reserve for an exit that has not happened yet.

    `policy` must state the number of exit orders permitted and whether
    partial fills are allowed, because a partial fill leaves a remainder
    that needs another order and each order is its own fee event. There
    is no default: an unstated exit policy is a blocker.
    """
    if not isinstance(policy, dict):
        return {"FEE": None, "BLOCKER": B_EXIT_POLICY,
                "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}
    n = policy.get("maxExitOrders")
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        return {"FEE": None, "BLOCKER": B_EXIT_POLICY,
                "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}
    if policy.get("partialFillsAllowed") not in (True, False):
        return {"FEE": None, "BLOCKER": B_EXIT_POLICY,
                "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}
    q = _d(quantity)
    if q is None or q <= 0:
        return {"FEE": None, "BLOCKER": B_BAD_INPUT}

    # THE BOUND, not a quote. min(p, 1-p) <= 0.5 for every admissible
    # price, so this is the most any single exit order on this quantity
    # can cost, whatever the book does between now and then.
    per_order = _round_up_cent(TAKER * q * WORST_FEE_BASIS)
    total = _round_up_cent(per_order * Decimal(n))
    return {"FEE": total, "BLOCKER": None, "perOrder": per_order,
            "maxExitOrders": n,
            "partialFillsAllowed": policy["partialFillsAllowed"],
            "worstCaseBasis": WORST_FEE_BASIS,
            "role": ROLE_TAKER,
            "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}


def preview_fee(preview):
    """The fee the venue's OWN preview states, or a named refusal.

    This is the observation. It is read, never inferred: a preview that
    does not state a fee is unreadable, not a fee of zero.
    """
    if not isinstance(preview, dict):
        return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE}
    order = preview.get("order") if isinstance(preview.get("order"), dict) \
        else preview
    for key in ("fee", "fees", "feeAmount", "totalFee", "estimatedFee"):
        raw = order.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get("value", raw.get("amount"))
        f = _d(raw)
        if f is None:
            return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE, "field": key}
        return {"FEE": _round_half_up_cent(f), "BLOCKER": None, "field": key}
    return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE,
            "why": "the preview states no fee field; an unstated fee is "
                   "unreadable, not zero"}


def reconcile(documented, observed, tolerance=AGREEMENT_TOLERANCE):
    """Documented expectation vs the venue's own preview.

    AGREEMENT IS REQUIRED. The coefficients and the formula were not
    retrieved by this code, so the arithmetic alone is not evidence. The
    preview alone does not bound a future exit. Together, agreeing, they
    establish the entry fee; apart, they establish a blocker.
    """
    out = {"documented": documented.get("FEE"),
           "observed": observed.get("FEE"),
           "tolerance": tolerance,
           "documentedRetrievedHere": SCHEDULE["RETRIEVED_HERE"]}
    if documented.get("BLOCKER"):
        return dict(out, AGREED=False, BLOCKER=documented["BLOCKER"])
    if observed.get("BLOCKER"):
        return dict(out, AGREED=False, BLOCKER=observed["BLOCKER"])
    delta = abs(documented["FEE"] - observed["FEE"])
    if delta > tolerance:
        # NOT resolved in favour of either. A disagreement about what an
        # order costs is exactly the thing not to guess about.
        return dict(out, AGREED=False, BLOCKER=B_DISAGREEMENT, delta=delta)
    return dict(out, AGREED=True, BLOCKER=None, delta=delta,
                FEE=max(documented["FEE"], observed["FEE"]))


def recorded_rebate(price, quantity, role):
    """What a maker rebate actually came to. FOR THE RECORD ONLY.

    Never subtracted from a reserve, never added to remaining spend,
    never recycled into buying power.
    """
    if role != ROLE_MAKER:
        return {"REBATE": Decimal("0.00"), "note": REBATES_ARE_NOT_BUDGET}
    q = quote(price, quantity, role=ROLE_MAKER)
    return {"REBATE": q["FEE"], "BLOCKER": q["BLOCKER"],
            "note": REBATES_ARE_NOT_BUDGET,
            "appliedToBudget": False}
