"""MICRO_EXECUTION_CALIBRATION -- the budget, and the refusals that keep it.

Management approved a supervised instrumentation experiment on 2026-09-18:

    MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE       USD   5.00
    MAX_SESSION_CUMULATIVE_SPEND              USD 100.00
    MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES        1

This module is the ONLY place those numbers live, and it is a pure
accounting module: it computes, admits and refuses. It sends nothing,
cancels nothing and holds no venue client. Submission is a separate,
per-ticket, human-approved act.

WHAT THE $100 IS AND IS NOT.

It is a CUMULATIVE EXPENDITURE CEILING. Not a target. Not reusable
buying power. Not a stop-loss. `spent` only ever rises: a sale that
returns $4.90 of a $5.00 purchase does not hand $4.90 back to the
session. An instrument that recycles proceeds can run unbounded volume
inside a fixed-looking cap, and this one cannot.

TWO THINGS THAT LOOK ALIKE AND ARE NOT. Releasing an over-reserve is
not recycling proceeds. A lifecycle reserved at $4.60 that actually
cost $4.00 gives back $0.60 that never left the account -- that is an
estimate being corrected, and refusing to give it back would make every
conservative fee reserve permanently burn budget. Sale proceeds are the
other thing, and they never come back. So `remaining()` is NOT
monotone; the monotone quantities are `spent()`, which only rises, and
the ceiling `MAX_SESSION_CUMULATIVE_SPEND - spent()`, which only falls.
`remaining()` is that ceiling less what is currently at risk.

WHAT THE $5 COVERS.

Purchase cost PLUS a conservative reserve for the entry fee AND the
exit fee. A ticket whose exit fee is unknown is not a cheap ticket, it
is an unbounded one, and it is refused by name.

RESERVE BEFORE SUBMISSION, RELEASE ONLY ON RECONCILIATION.

The reserve is taken against every order that COULD STILL FILL, before
it is sent. A cancel request is not a terminal state: the venue may
have filled the order before it saw the cancel, so the reserve stands
until the venue's own terminal state is read AND any fills are
reconciled. `release()` refuses to act on anything less. This is the
same lesson as book 1333 and the 18:05Z cancel storm, priced in
advance rather than after.

NOT AUTHORISED HERE. Leverage, borrowing, naked shorts, automatic
re-entry, size increases after a win or a loss, and raising any limit
to make a market fit. A market that does not fit is skipped.
"""
from __future__ import annotations

import math
from typing import Any

# ── THE APPROVED LIMITS ──────────────────────────────────────────────
MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE = 5.00
MAX_SESSION_CUMULATIVE_SPEND = 100.00
MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES = 1

AUTHORISED_BY = "MANAGEMENT_DIRECTIVE_2026-09-18_SECTION_A"
EXPERIMENT = "MICRO_EXECUTION_CALIBRATION"

# These limits are approved for the calibration experiment ALONE. They
# are not an EV admission threshold and they do not relax one.
NOT_AN_EV_AUTHORISATION = (
    "the calibration budget authorises instrumentation, not autonomous EV "
    "trading, and it does not change decision-grade admission requirements")

PROCEEDS_DO_NOT_REPLENISH = (
    "sale proceeds and profits do not return to the session allowance; "
    "spend is cumulative expenditure, never recycled buying power")

A_CANCEL_IS_NOT_A_TERMINAL_STATE = (
    "a cancel request does not prove the order cannot fill; the reserve "
    "stands until the venue's own terminal state is read and any fills "
    "are reconciled")

# ── LIFECYCLE STATES ─────────────────────────────────────────────────
# The four states management asked COMMAND to distinguish, plus the
# pre-submission ones. Only RECONCILED releases a reserve.
PROPOSED = "PROPOSED"                 # a ticket exists; nothing is sent
APPROVED = "APPROVED"                 # a human approved THIS ticket
SUBMITTED = "SUBMITTED"               # the venue has it; it could fill
EXIT_CONSIDERED = "EXIT_CONSIDERED"
EXIT_SUBMITTED = "EXIT_SUBMITTED"
EXIT_FILLED = "EXIT_FILLED"
RECONCILED = "RECONCILED"             # venue terminal + fills reconciled

OPEN_STATES = (APPROVED, SUBMITTED, EXIT_CONSIDERED, EXIT_SUBMITTED,
               EXIT_FILLED)
# EXIT_FILLED is deliberately OPEN. A filled exit is not a reconciled
# position: the ledger, the venue and the dashboard have not yet been
# compared, and late fills land exactly here.

# ── REFUSAL NAMES ────────────────────────────────────────────────────
# Every refusal is named. "Cannot be bounded" is a refusal, not a zero.
R_CONCURRENCY = "CONCURRENT_LIFECYCLE_LIMIT"
R_PER_TRADE = "PER_TRADE_ALL_IN_LIMIT"
R_SESSION = "SESSION_CUMULATIVE_SPEND_LIMIT"
R_FEES_UNBOUNDED = "FEES_NOT_BOUNDED"
R_EXPOSURE_UNBOUNDED = "EXPOSURE_NOT_BOUNDED"
R_MIN_QUANTITY = "VENUE_MINIMUM_QUANTITY_DOES_NOT_FIT"
R_TICK = "PRICE_NOT_ON_VENUE_TICK"
R_PRICE_RANGE = "PRICE_OUTSIDE_ZERO_ONE"
R_QUANTITY = "QUANTITY_NOT_A_POSITIVE_WHOLE_NUMBER"
R_UNFUNDED = "NOT_FULLY_FUNDED"
R_CASH_UNKNOWN = "AVAILABLE_CASH_UNIDENTIFIED"
R_SHORT = "NAKED_SHORT_NOT_AUTHORISED"
R_LEVERAGE = "LEVERAGE_OR_BORROWING_NOT_AUTHORISED"
R_AUTO_REENTRY = "AUTOMATIC_RE_ENTRY_NOT_AUTHORISED"
R_SIZE_INCREASE = "SIZE_INCREASE_AFTER_OUTCOME_NOT_AUTHORISED"
R_IDENTITY = "TICKET_IDENTITY_INCOMPLETE"
R_STALE_STATE = "ACCOUNT_OR_ORDER_STATE_STALE"
R_NO_INVENTORY_PLAN = "NO_PREDECLARED_INVENTORY_PLAN"
R_NO_STOP = "NO_OPERATOR_STOP_MECHANISM"

CENTS = 0.01


def _money(x: float) -> float:
    """Round half-up to the cent, upward for reserves elsewhere."""
    return math.floor(x * 100 + 0.5) / 100.0


def _ceil_cent(x: float) -> float:
    """Reserves round UP. A reserve that rounds down is not a bound."""
    return math.ceil(x * 100 - 1e-9) / 100.0


def on_tick(price: float, tick: float) -> bool:
    """Is `price` an exact multiple of the venue's tick?

    Compared in integer ten-thousandths: 0.1 + 0.2 != 0.3 in binary
    floating point, and a tick check that trusts the remainder operator
    rejects perfectly valid prices (and, worse, accepts invalid ones).
    """
    if not (isinstance(price, (int, float)) and isinstance(tick, (int, float))):
        return False
    if not (tick > 0) or not math.isfinite(price) or not math.isfinite(tick):
        return False
    p = int(round(price * 10000))
    t = int(round(tick * 10000))
    if t <= 0 or abs(p - price * 10000) > 1e-6 or abs(t - tick * 10000) > 1e-6:
        return False
    return p % t == 0


def all_in_cost(quantity: float, price: float,
                entry_fee_reserve: float,
                exit_fee_reserve: float) -> float:
    """Purchase cost plus BOTH fee reserves, rounded UP to the cent.

    The exit fee is part of the lifecycle's cost even though it is paid
    later: a $4.99 purchase whose exit costs $0.40 is a $5.39 lifecycle,
    and calling it $4.99 is how a bounded experiment stops being one.
    """
    return _ceil_cent(quantity * price + entry_fee_reserve + exit_fee_reserve)


def worst_case_exposure(quantity: float, price: float,
                        entry_fee_reserve: float,
                        exit_fee_reserve: float) -> float:
    """The most this lifecycle can cost us in cash.

    For a FULLY FUNDED long in a [0,1] outcome contract the purchase is
    the maximum loss -- the contract cannot settle below zero and we
    never owe more than we paid. Fees are additive. There is no path
    here that exceeds all_in_cost, and that is a property of "fully
    funded long only", not of optimism: the moment a short or any
    borrowing enters, this function is no longer the bound and the
    ticket is refused upstream by name.
    """
    return all_in_cost(quantity, price, entry_fee_reserve, exit_fee_reserve)


def empty_session(session_id: str) -> dict:
    """A fresh calibration session ledger."""
    return {
        "experiment": EXPERIMENT,
        "sessionId": session_id,
        "authorisedBy": AUTHORISED_BY,
        "limits": {
            "maxAllInCostPerTradeLifecycle": MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE,
            "maxSessionCumulativeSpend": MAX_SESSION_CUMULATIVE_SPEND,
            "maxConcurrentOrderPositionLifecycles":
                MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES,
        },
        "spent": 0.0,        # cumulative cash OUT. Never decreases.
        "lifecycles": [],    # every ticket that was ever approved
    }


def open_lifecycles(session: dict) -> list:
    return [lc for lc in session.get("lifecycles", ())
            if lc.get("state") in OPEN_STATES]


def reserved(session: dict) -> float:
    """Every dollar that could still leave the account."""
    return _ceil_cent(sum(float(lc.get("reserve") or 0.0)
                          for lc in open_lifecycles(session)))


def spent(session: dict) -> float:
    return _money(float(session.get("spent") or 0.0))


def remaining(session: dict) -> float:
    """What the session may still commit. Never negative."""
    return max(0.0, _money(MAX_SESSION_CUMULATIVE_SPEND
                           - spent(session) - reserved(session)))


def budget_block(session: dict) -> dict:
    """The three numbers COMMAND must show, plus their meanings."""
    return {
        "experiment": EXPERIMENT,
        "sessionId": session.get("sessionId"),
        "spent": spent(session),
        "reserved": reserved(session),
        "remaining": remaining(session),
        "openLifecycles": len(open_lifecycles(session)),
        "limits": dict(session.get("limits") or {}),
        "proceedsDoNotReplenish": PROCEEDS_DO_NOT_REPLENISH,
        "notAnEvAuthorisation": NOT_AN_EV_AUTHORISATION,
    }


# ── ADMISSION ────────────────────────────────────────────────────────

_REQUIRED_IDENTITY = ("venue", "account", "marketId", "outcome", "side",
                      "orderType", "clientOrderId", "expiry")


def refusals(ticket: dict, session: dict,
             account: dict | None = None) -> list:
    """EVERY reason this ticket may not be submitted, by name.

    A list, not a first-failure: an operator reading one blocker and
    fixing it only to meet the next is how a bounded experiment turns
    into a negotiation. `[]` means admissible under the approved limits
    -- it does not mean approved. A human approves each ticket.
    """
    out = []
    t = ticket or {}

    # 1. Identity. An order we cannot name is an order we cannot
    #    reconcile, and an ambiguous response would be unattributable.
    for k in _REQUIRED_IDENTITY:
        if not isinstance(t.get(k), str) or not t[k].strip():
            out.append("%s:%s" % (R_IDENTITY, k))
    if t.get("side") not in (None, "BUY"):
        out.append(R_SHORT)
    if t.get("leverage") or t.get("borrow"):
        out.append(R_LEVERAGE)
    if t.get("autoReentry"):
        out.append(R_AUTO_REENTRY)
    if t.get("sizeIncreaseAfterOutcome"):
        out.append(R_SIZE_INCREASE)
    if not t.get("inventoryPlan"):
        out.append(R_NO_INVENTORY_PLAN)
    if not t.get("operatorStop"):
        out.append(R_NO_STOP)

    # 2. Price and quantity, in the venue's own units.
    price, qty = t.get("price"), t.get("quantity")
    tick, min_qty = t.get("tick"), t.get("venueMinQuantity")
    if not isinstance(price, (int, float)) or not math.isfinite(price):
        out.append(R_PRICE_RANGE)
    elif not (0.0 < price < 1.0):
        out.append(R_PRICE_RANGE)
    elif not on_tick(price, tick if isinstance(tick, (int, float)) else 0):
        out.append(R_TICK)
    if not isinstance(qty, (int, float)) or not math.isfinite(qty) \
            or qty <= 0 or abs(qty - round(qty)) > 1e-9:
        out.append(R_QUANTITY)
    elif isinstance(min_qty, (int, float)) and qty < min_qty:
        out.append(R_MIN_QUANTITY)
    elif min_qty is None:
        out.append(R_MIN_QUANTITY)     # unknown minimum is not "no minimum"

    # 3. Fees. UNKNOWN IS A REFUSAL, NEVER A ZERO.
    ef, xf = t.get("entryFeeReserve"), t.get("exitFeeReserve")
    fees_ok = all(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0
                  for v in (ef, xf))
    if not fees_ok or not t.get("feeModel"):
        out.append(R_FEES_UNBOUNDED)

    # 4. The money, only once the inputs above are real numbers.
    numbers_ok = (fees_ok
                  and isinstance(price, (int, float)) and math.isfinite(price)
                  and isinstance(qty, (int, float)) and math.isfinite(qty)
                  and qty > 0)
    if numbers_ok:
        allin = all_in_cost(qty, price, ef, xf)
        exposure = worst_case_exposure(qty, price, ef, xf)
        if not math.isfinite(exposure):
            out.append(R_EXPOSURE_UNBOUNDED)
        if allin > MAX_ALL_IN_COST_PER_TRADE_LIFECYCLE + 1e-9:
            out.append(R_PER_TRADE)
        if allin > remaining(session) + 1e-9:
            out.append(R_SESSION)
        # 5. Fully funded. Unknown cash is not funded cash.
        cash = (account or {}).get("available")
        if cash is None:
            out.append(R_CASH_UNKNOWN)
        elif not isinstance(cash, (int, float)) or not math.isfinite(cash) \
                or cash < allin:
            out.append(R_UNFUNDED)
    else:
        out.append(R_EXPOSURE_UNBOUNDED)

    # 6. Concurrency.
    if len(open_lifecycles(session)) >= MAX_CONCURRENT_ORDER_POSITION_LIFECYCLES:
        out.append(R_CONCURRENCY)

    # 7. State freshness. A preflight run against stale account or open
    #    order state is not a preflight.
    if t.get("stateFresh") is not True:
        out.append(R_STALE_STATE)

    # Stable, de-duplicated, sorted -- a refusal set is a set.
    return sorted(set(out))


def admissible(ticket: dict, session: dict,
               account: dict | None = None) -> bool:
    return not refusals(ticket, session, account)


def reserve(session: dict, ticket: dict,
            account: dict | None = None) -> dict:
    """Take the reserve BEFORE submission. Refuses if not admissible.

    Returns the new lifecycle row. The caller submits only after a human
    approves the ticket; this function never submits.
    """
    why = refusals(ticket, session, account)
    if why:
        raise ValueError("CALIBRATION_TICKET_REFUSED: " + ", ".join(why))
    allin = all_in_cost(ticket["quantity"], ticket["price"],
                        ticket["entryFeeReserve"], ticket["exitFeeReserve"])
    lc = {
        "clientOrderId": ticket["clientOrderId"],
        "marketId": ticket["marketId"],
        "outcome": ticket["outcome"],
        "venue": ticket["venue"],
        "side": ticket.get("side", "BUY"),
        "quantity": ticket["quantity"],
        "price": ticket["price"],
        "reserve": allin,
        "allInCost": allin,
        "state": APPROVED,
        "spentSoFar": 0.0,
        "venueTerminalState": None,
        "fillsReconciled": False,
        "lane": "CALIBRATION",
    }
    session.setdefault("lifecycles", []).append(lc)
    return lc


def record_spend(session: dict, client_order_id: str, amount: float) -> None:
    """Cash actually left the account. Cumulative, never reversed.

    A refund or sale proceed is NOT recorded here and never reduces
    `spent` -- see PROCEEDS_DO_NOT_REPLENISH.
    """
    if not isinstance(amount, (int, float)) or not math.isfinite(amount) \
            or amount < 0:
        raise ValueError("CALIBRATION_SPEND_NOT_A_NON_NEGATIVE_AMOUNT")
    lc = lifecycle(session, client_order_id)
    if lc is None:
        raise ValueError("CALIBRATION_UNKNOWN_LIFECYCLE: %s" % client_order_id)
    lc["spentSoFar"] = _money(float(lc.get("spentSoFar") or 0.0) + amount)
    session["spent"] = _money(float(session.get("spent") or 0.0) + amount)


def lifecycle(session: dict, client_order_id: str) -> dict | None:
    for lc in session.get("lifecycles", ()):
        if lc.get("clientOrderId") == client_order_id:
            return lc
    return None


def advance(session: dict, client_order_id: str, state: str) -> dict:
    """Move a lifecycle forward. RECONCILED is refused unless earned."""
    lc = lifecycle(session, client_order_id)
    if lc is None:
        raise ValueError("CALIBRATION_UNKNOWN_LIFECYCLE: %s" % client_order_id)
    if state == RECONCILED:
        raise ValueError("CALIBRATION_USE_RELEASE_TO_RECONCILE")
    if state not in (APPROVED, SUBMITTED, EXIT_CONSIDERED, EXIT_SUBMITTED,
                     EXIT_FILLED):
        raise ValueError("CALIBRATION_UNKNOWN_STATE: %s" % state)
    lc["state"] = state
    return lc


def release(session: dict, client_order_id: str,
            venue_terminal_state: str | None,
            fills_reconciled: bool) -> dict:
    """Release the reserve. ONLY on a venue terminal state WITH fills
    reconciled.

    A cancellation acknowledgement is not a terminal state and does not
    reach this function's happy path: pass the state the venue itself
    reports, after the fills have been compared. Anything else raises,
    and the reserve stands.
    """
    lc = lifecycle(session, client_order_id)
    if lc is None:
        raise ValueError("CALIBRATION_UNKNOWN_LIFECYCLE: %s" % client_order_id)
    if not isinstance(venue_terminal_state, str) or not venue_terminal_state.strip():
        raise ValueError("CALIBRATION_RESERVE_HELD: " +
                         A_CANCEL_IS_NOT_A_TERMINAL_STATE)
    if fills_reconciled is not True:
        raise ValueError("CALIBRATION_RESERVE_HELD: fills not reconciled")
    lc["venueTerminalState"] = venue_terminal_state
    lc["fillsReconciled"] = True
    lc["state"] = RECONCILED
    return lc


def preflight(ticket: dict, session: dict, account: dict | None = None,
              checks: dict | None = None) -> dict:
    """The full pre-submission report for ONE ticket.

    `checks` carries the verifications this module cannot make itself --
    a working cancel path, partial- and late-fill reconciliation, durable
    records, the operator stop. Each is reported by name and each
    unproved one is a blocker. Nothing here is assumed true.
    """
    required_checks = ("venueAccountInstrumentVerified", "freshAccountState",
                       "freshOpenOrderState", "uniqueClientOrderId",
                       "ambiguousResponseHandling", "cancellationPathVerified",
                       "partialFillReconciliation", "lateFillReconciliation",
                       "durableRecords", "operatorStop", "previewedCost")
    checks = dict(checks or {})
    unproved = sorted(k for k in required_checks if checks.get(k) is not True)
    why = refusals(ticket, session, account)
    ef = ticket.get("entryFeeReserve")
    xf = ticket.get("exitFeeReserve")
    q, p = ticket.get("quantity"), ticket.get("price")
    computable = all(isinstance(v, (int, float)) and math.isfinite(v)
                     for v in (ef, xf, q, p))
    allin = all_in_cost(q, p, ef, xf) if computable else None
    return {
        "experiment": EXPERIMENT,
        "authorisedBy": AUTHORISED_BY,
        "ticket": dict(ticket or {}),
        "budget": budget_block(session),
        "allInCost": allin,
        "maximumAllInExposure": allin,
        "limitsEnforced": True,
        "unprovedChecks": unproved,
        "refusals": why,
        # ADMISSIBLE IS NOT APPROVED, and a ticket with an unproved check
        # is not submittable however small it is.
        "submittable": (not why) and not unproved,
        "requiresHumanApproval": True,
        "noBlindRetries": True,
        "cancelIsNotTerminal": A_CANCEL_IS_NOT_A_TERMINAL_STATE,
    }
