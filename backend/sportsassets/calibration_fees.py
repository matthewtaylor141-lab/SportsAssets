"""THE VENUE'S PUBLISHED FEE SCHEDULE, and what it does and does not bound.

WHY THIS EXISTS. There was no programmatic fee read anywhere in this
repository, so `calibration_evidence` reported FEE_TERMS_NOT_OBTAINED and
no ticket could be built.

    source          https://docs.polymarket.us/fees
    effective       2026-09-17
    taker            0.0695
    maker rebate    -0.0125
    charge          coefficient x quantity x price x (1 - price)

A CORRECTION, RECORDED RATHER THAN QUIETLY FIXED. The first version of
this module used `min(price, 1 - price)` as the price factor. That was
NOT supplied by the management directive of 2026-09-18, which gave only
the coefficients and the effective date -- it was my own guess at the
form, written into a module whose whole purpose is not to guess at fee
terms, and left standing because the tests that checked it were computed
from the same wrong formula. The documented factor is `p x (1 - p)`. The
two agree nowhere except p = 0 and p = 1, and at p = 0.39 the guess
overstated the charge by 64%.

THE INDEPENDENT CHECK IS INDEPENDENT. The two vectors this is verified
against -- 10 contracts at 0.39 taker = 0.17, and 100 at 0.50 taker =
1.74 -- were supplied from the source, not produced by this code. They
are asserted as literals in the tests. A test that computes its own
expected answer with the implementation under test proves only that the
implementation is consistent with itself, which is exactly how the min()
form survived its first review.

THREE DISTINCT QUANTITIES, never collapsed:

    expected_fee()       what the schedule says this order should cost.
                         Documented rounding: half-up to the cent.
    reserve_allowance()  what the desk sets aside. Conservative: rounds
                         UP, assumes the taker role, and for an exit
                         assumes the worst price factor. It is not a
                         prediction and must never be compared for
                         EQUALITY with an expected charge.
    collected_fee()      what the venue actually took, read back from
                         the execution. The only one that is money that
                         has moved.

THE WORST PRICE FACTOR IS 0.25. `p x (1 - p)` is maximised at p = 0.5,
so the most any order on a given quantity can be charged is
`coefficient x quantity x 0.25`, whatever the book does. That is what
bounds an exit that has not happened; a current preview cannot.

THE ENTRY IS RESERVED AT THE TAKER RATE EVEN THOUGH IT IS POST-ONLY.
A post-only order is supposed to be a maker, and the maker coefficient is
a REBATE. But this venue has already filled a post-only rest at create
with maker=false (order 153, 2026-09-06, which is why post-only was
switched off process-wide at 17:36Z that day). A reserve that assumes the
venue honours post-only is a reserve that the one observed failure mode
breaks.

EXPECTED REBATES REDUCE NOTHING. Not the reserve, not cumulative session
spending, not remaining allowance. `recorded_rebate()` exists to record
what was actually earned, separately, and proceeds are never recycled.

THE ARITHMETIC IS EXACT. `Decimal` constructed via `str`, never binary
float. A cent of drift in the wrong direction is a cent over an approved
cap.
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
    "FORMULA": "coefficient * quantity * price * (1 - price)",
    "WORST_PRICE_FACTOR": "0.25",
    "SUPPLIED_BY": (
        "coefficients and effective date: management directive 2026-09-18. "
        "FORMULA: management directive 2026-09-19, correcting this module. "
        "The earlier `min(price, 1 - price)` form was NOT supplied by any "
        "directive -- it was this module's own guess and it was wrong"),
    "RETRIEVED_HERE": False,
    "WHY_NOT_RETRIEVED_HERE": (
        "this container's egress proxy refuses CONNECT to docs.polymarket.us "
        "(403, organisation policy), so the pages were not fetched by this "
        "code and the values are a RECORDED EXPECTATION, not an observation"),
    "VERIFIED_AGAINST": (
        "two independently supplied vectors, asserted as literals rather "
        "than computed by this implementation: 10 @ 0.39 taker = 0.17, "
        "100 @ 0.50 taker = 1.74"),
    "APPLICABILITY": (
        "binary outcome markets on polymarket.us, per filled share, charged "
        "on execution and dependent on the execution role"),
}

TAKER = Decimal(SCHEDULE["TAKER_COEFFICIENT"])
MAKER = Decimal(SCHEDULE["MAKER_REBATE_COEFFICIENT"])
CENT = Decimal("0.01")

# p * (1 - p) is maximised at p = 0.5.
WORST_PRICE_FACTOR = Decimal(SCHEDULE["WORST_PRICE_FACTOR"])

ROLE_TAKER = "TAKER"
ROLE_MAKER = "MAKER"

# ── named blockers ───────────────────────────────────────────────────
B_SCHEDULE_NOT_EFFECTIVE = "FEE_SCHEDULE_NOT_EFFECTIVE_AT_THIS_TIME"
B_DISAGREEMENT = "FEE_SOURCE_DISAGREEMENT"
B_PREVIEW_UNREADABLE = "FEE_PREVIEW_UNREADABLE"
B_PREVIEW_MISSING = "FEE_PREVIEW_NOT_OBTAINED"
B_PREVIEW_ROLE = "FEE_PREVIEW_ROLE_NOT_STATED"
B_PREVIEW_UNITS = "FEE_PREVIEW_UNITS_NOT_STATED"
B_PREVIEW_IS_HISTORICAL = "FEE_PREVIEW_FIELD_IS_COLLECTED_TO_DATE"
B_UNSUPPORTED_ROLE = "FEE_ROLE_NOT_SUPPORTED"
B_BAD_INPUT = "FEE_INPUT_NOT_A_NUMBER"
B_EXIT_POLICY = "EXIT_POLICY_NOT_STATED"

NO_INVENTED_DEFAULT = (
    "there is no default fee. A term this module cannot establish for the "
    "market in front of it is a named blocker, and the ticket is not built")

REBATES_ARE_NOT_BUDGET = (
    "an expected maker rebate is not money in hand. It never reduces a "
    "reserve, never reduces cumulative session spending, and never "
    "replenishes the allowance. Actual rebates are recorded separately "
    "and proceeds are not recycled")

A_PREVIEW_DOES_NOT_BOUND_THE_EXIT = (
    "a preview prices the order in front of us. An exit that has not "
    "happened is bounded by the worst charge its permitted orders could "
    "incur, not by today's quote")

A_RESERVE_IS_NOT_A_PREDICTION = (
    "the reserve is a conservative upper bound -- taker role, rounded up, "
    "worst price factor where the price is unknown. Comparing it for "
    "EQUALITY with an expected charge is comparing two different "
    "quantities and will fail whenever the reserve is doing its job")

# How far the documented EXPECTED charge and the venue's own expected
# charge may differ and still be called agreement. One cent.
AGREEMENT_TOLERANCE = Decimal("0.01")

# Preview fields that report money ALREADY TAKEN over the life of an
# order. They are not a quote for what this order will cost.
COLLECTED_TO_DATE_FIELDS = ("feeCollected", "collectedFee", "cumFee",
                            "cumulativeFee", "feesPaid", "totalFeesCollected")

# Preview fields that state the expected charge for the previewed order.
EXPECTED_FEE_FIELDS = ("expectedFee", "estimatedFee", "commission",
                       "feeAmount", "fee")


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


def price_factor(price):
    """p * (1 - p), exactly. None when the price is unreadable.

    NOT min(p, 1 - p). See the module docstring: that was this module's
    own guess and it overstated the charge everywhere except the ends.
    """
    p = _d(price)
    if p is None or p <= 0 or p >= 1:
        return None
    return p * (Decimal("1") - p)


def _half_up(x):
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _ceil(x):
    return x.quantize(CENT, rounding=ROUND_CEILING)


def _coefficient(role):
    return TAKER if role == ROLE_TAKER else MAKER


def expected_fee(price, quantity, role=ROLE_TAKER, at=None):
    """WHAT THE SCHEDULE SAYS THIS ORDER SHOULD COST.

    Documented rounding: half-up to the cent. Positive for a taker
    charge, negative for a maker rebate, because they are opposite
    directions of money and collapsing them would let a rebate look like
    a cost of zero.

    This is an EXPECTATION. It is not the reserve and it is not what the
    venue collected.
    """
    out = {"role": role, "quantity": quantity, "price": price,
           "schedule": SCHEDULE["EFFECTIVE_DATE"],
           "formula": SCHEDULE["FORMULA"],
           "rounding": "ROUND_HALF_UP",
           "kind": "EXPECTED_CHARGE",
           "retrievedHere": SCHEDULE["RETRIEVED_HERE"]}
    if role not in (ROLE_TAKER, ROLE_MAKER):
        return dict(out, FEE=None, BLOCKER=B_UNSUPPORTED_ROLE)
    if at is not None and str(at) < SCHEDULE["EFFECTIVE_DATE"]:
        return dict(out, FEE=None, BLOCKER=B_SCHEDULE_NOT_EFFECTIVE)
    q, factor = _d(quantity), price_factor(price)
    if q is None or q <= 0 or factor is None:
        return dict(out, FEE=None, BLOCKER=B_BAD_INPUT)
    coef = _coefficient(role)
    raw = coef * q * factor
    return dict(out, FEE=_half_up(raw), BLOCKER=None, priceFactor=factor,
                coefficient=coef, raw=raw)


def reserve_allowance(price, quantity, role=ROLE_TAKER):
    """WHAT THE DESK SETS ASIDE. Conservative, and a different quantity.

    Rounds UP and only ever reserves a CHARGE: a rebate role reserves
    zero rather than a negative number, because expected rebates reduce
    nothing.
    """
    out = {"role": role, "kind": "RESERVE_ALLOWANCE",
           "rounding": "ROUND_CEILING",
           "notAPrediction": A_RESERVE_IS_NOT_A_PREDICTION}
    q, factor = _d(quantity), price_factor(price)
    if q is None or q <= 0 or factor is None:
        return dict(out, FEE=None, BLOCKER=B_BAD_INPUT)
    coef = _coefficient(role)
    if coef <= 0:
        return dict(out, FEE=Decimal("0.00"), BLOCKER=None,
                    why=REBATES_ARE_NOT_BUDGET)
    return dict(out, FEE=_ceil(coef * q * factor), BLOCKER=None,
                priceFactor=factor, coefficient=coef)


def entry_reserve(price, quantity, post_only=True):
    """The ENTRY fee allowance, at the taker rate.

    Even for a post-only order: this venue has filled a post-only rest at
    create with maker=false, and a reserve that assumes otherwise is
    broken by the one failure mode actually observed.
    """
    got = reserve_allowance(price, quantity, role=ROLE_TAKER)
    got["postOnlyRequested"] = bool(post_only)
    got["whyTakerRate"] = (
        "a post-only order has been filled as a taker on this venue "
        "(order 153, 2026-09-06); the reserve does not assume otherwise")
    return got


def exit_reserve(quantity, policy):
    """The allowance for an exit that has not happened yet.

    `policy` must state how many exit orders are permitted, whether
    partial fills are allowed, and whether automatic replacement orders
    are permitted. There is no default: an unstated exit policy cannot
    bound anything.

    A PERMITTED PARTIAL FILL DOES NOT AUTHORISE ANOTHER EXIT ORDER. With
    automaticReplacementOrders false the remainder is NOT re-offered, so
    the bound is the permitted order count and no more.
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
    if policy.get("automaticReplacementOrders") not in (True, False):
        return {"FEE": None, "BLOCKER": B_EXIT_POLICY,
                "why": "whether a partial fill's remainder may be re-offered "
                       "changes how many fee events the exit can produce",
                "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}
    q = _d(quantity)
    if q is None or q <= 0:
        return {"FEE": None, "BLOCKER": B_BAD_INPUT}

    # THE BOUND, not a quote. p * (1 - p) <= 0.25 for every admissible
    # price, so this is the most a single exit order on this quantity can
    # be charged, whatever the book does between now and then.
    per_order = _ceil(TAKER * q * WORST_PRICE_FACTOR)
    return {"FEE": _ceil(per_order * Decimal(n)), "BLOCKER": None,
            "kind": "RESERVE_ALLOWANCE", "perOrder": per_order,
            "maxExitOrders": n,
            "partialFillsAllowed": policy["partialFillsAllowed"],
            "automaticReplacementOrders": policy["automaticReplacementOrders"],
            "worstPriceFactor": WORST_PRICE_FACTOR,
            "role": ROLE_TAKER,
            "aPartialFillDoesNotAuthoriseAnother": (
                "the remainder is not re-offered; the residual-inventory "
                "fallback on the ticket is what happens to it"),
            "note": A_PREVIEW_DOES_NOT_BOUND_THE_EXIT}


# ── the venue's own preview ──────────────────────────────────────────

def preview_fee(preview):
    """The EXPECTED charge the venue's own preview states.

    Read, never inferred. Three specific refusals:

      * a preview that states no fee at all is UNREADABLE, not zero;
      * a field that reports fees COLLECTED TO DATE is history, not a
        quote for this order, and is refused by name;
      * a preview that does not state the execution ROLE it priced
        cannot be compared against a role-dependent schedule.
    """
    if not isinstance(preview, dict):
        return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE}
    order = preview.get("order") if isinstance(preview.get("order"), dict) \
        else preview

    for key in COLLECTED_TO_DATE_FIELDS:
        if order.get(key) is not None and not any(
                order.get(k) is not None for k in EXPECTED_FEE_FIELDS):
            return {"FEE": None, "BLOCKER": B_PREVIEW_IS_HISTORICAL,
                    "field": key,
                    "why": "this field is money already taken over the life "
                           "of an order, not what the previewed order will "
                           "cost"}

    for key in EXPECTED_FEE_FIELDS:
        raw = order.get(key)
        if raw is None:
            continue
        currency = None
        if isinstance(raw, dict):
            currency = raw.get("currency")
            raw = raw.get("value", raw.get("amount"))
        f = _d(raw)
        if f is None:
            return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE, "field": key}
        if currency is not None and str(currency).upper() != "USD":
            # A number in unknown units is not a number we can compare.
            return {"FEE": None, "BLOCKER": B_PREVIEW_UNITS,
                    "field": key, "currency": currency}
        role = order.get("executionRole") or order.get("role") \
            or order.get("liquidity")
        if not role:
            return {"FEE": None, "BLOCKER": B_PREVIEW_ROLE, "field": key,
                    "why": "the schedule is role-dependent, so a charge "
                           "whose role is unstated cannot be checked "
                           "against it"}
        role = ROLE_MAKER if "MAKER" in str(role).upper() else ROLE_TAKER
        return {"FEE": _half_up(f), "BLOCKER": None, "field": key,
                "role": role, "kind": "EXPECTED_CHARGE", "currency": "USD"}

    return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE,
            "why": "the preview states no expected fee field; an unstated "
                   "fee is unreadable, not zero"}


def reconcile(price, quantity, observed, at=None,
              tolerance=AGREEMENT_TOLERANCE):
    """The documented EXPECTED charge vs the venue's own EXPECTED charge.

    EXPECTATION AGAINST EXPECTATION, at the role the venue says it
    priced. The conservative reserve is deliberately not what is
    compared: it rounds up and assumes the taker role, so requiring it to
    equal a maker charge would fail precisely when it is doing its job.

    Missing preview evidence is a named blocker. It is never skipped.
    """
    if observed is None:
        return {"AGREED": False, "BLOCKER": B_PREVIEW_MISSING,
                "why": "no preview was obtained for the sized order, so the "
                       "documented schedule has nothing to be checked "
                       "against and stands unverified"}
    if observed.get("BLOCKER"):
        return {"AGREED": False, "BLOCKER": observed["BLOCKER"],
                "observedDetail": observed}

    role = observed.get("role") or ROLE_TAKER
    doc = expected_fee(price, quantity, role=role, at=at)
    out = {"role": role, "documented": doc.get("FEE"),
           "observed": observed.get("FEE"), "tolerance": tolerance,
           "comparing": "EXPECTED_CHARGE vs EXPECTED_CHARGE",
           "documentedRetrievedHere": SCHEDULE["RETRIEVED_HERE"]}
    if doc.get("BLOCKER"):
        return dict(out, AGREED=False, BLOCKER=doc["BLOCKER"])
    delta = abs(doc["FEE"] - observed["FEE"])
    if delta > tolerance:
        return dict(out, AGREED=False, BLOCKER=B_DISAGREEMENT, delta=delta)
    return dict(out, AGREED=True, BLOCKER=None, delta=delta)


def collected_fee(status):
    """WHAT THE VENUE ACTUALLY TOOK, from the execution.

    The third quantity. Unreadable stays unreadable: a status that states
    no fee is not a fee of zero, which is the defect settle() already
    carries a named refusal for.
    """
    if not isinstance(status, dict):
        return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE,
                "kind": "COLLECTED"}
    for key in COLLECTED_TO_DATE_FIELDS + EXPECTED_FEE_FIELDS:
        raw = status.get(key)
        if raw is None:
            continue
        if isinstance(raw, dict):
            raw = raw.get("value", raw.get("amount"))
        f = _d(raw)
        if f is None:
            return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE,
                    "field": key, "kind": "COLLECTED"}
        return {"FEE": _half_up(f), "BLOCKER": None, "field": key,
                "kind": "COLLECTED"}
    return {"FEE": None, "BLOCKER": B_PREVIEW_UNREADABLE, "kind": "COLLECTED",
            "why": "the execution states no fee; unreadable is not zero"}


def recorded_rebate(price, quantity, role):
    """What a maker rebate actually came to. FOR THE RECORD ONLY."""
    if role != ROLE_MAKER:
        return {"REBATE": Decimal("0.00"), "note": REBATES_ARE_NOT_BUDGET,
                "appliedToBudget": False, "appliedToReserve": False}
    q = expected_fee(price, quantity, role=ROLE_MAKER)
    return {"REBATE": q["FEE"], "BLOCKER": q["BLOCKER"],
            "note": REBATES_ARE_NOT_BUDGET,
            "appliedToBudget": False, "appliedToReserve": False}
