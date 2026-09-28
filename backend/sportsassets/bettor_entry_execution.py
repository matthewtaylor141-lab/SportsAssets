"""THE ENTRY LANE'S EXECUTION ESTIMATE, SIZE AND RISK VERDICT.

WHAT THIS REPLACES, AND WHY IT IS NOT A NEW ENGINE.

The scheduled external-valuation loop reached `bettor_entry_gate.admit`
with three inputs that were not answers:

    execution_estimate={"p_fill": None, "basis": "P_FILL_NOT_IDENTIFIED",
                        "crossing": True}
    size=1.0
    risk={"permitted": True, "reason": "shadow, no capital"}

The first was honest and refused every candidate by name. The other two
were placeholders: `1.0` is not a size any policy chose, and a
hand-written `permitted: True` is the risk layer being told its own
answer. This module supplies all three from machinery that already
exists, and adds no economics of its own:

    SIZE       `shadow_bettor_sizing` -- the frozen $1,000 intended
               notional, and the only sizing arithmetic in the system
    EXECUTION  `shadow.marketable_fill` -- walks the OBSERVED ladder,
               never more than is there, NOT_IDENTIFIED rather than a
               guess
    RISK       `bettor_risk_engine.evaluate` -- with this lane's
               predeclared limits, and fail-closed without them

────────────────────────────────────────────────────────────────────
MARKETABLE EXECUTION IS NOT A RESTING ORDER'S FILL PROBABILITY.

These are two different quantities and the system already has two
different engines for them. Conflating them is the specific error this
module exists to avoid:

  RESTING     `bettor_shadow_execution`, basis
              PRINT_THROUGH_WITH_QUEUE_SHARE_V1. We joined a queue at a
              price nobody has to trade against. Whether we fill is a
              FORECAST about other people's future orders, and it
              depends on queue position -- which displayed depth does
              not tell us. This lane never uses it, because this lane
              does not rest an order.

  MARKETABLE  `shadow.marketable_fill`, class MARKETABLE_RECONSTRUCTED.
              We cross the spread. What fills is whatever the ARRIVAL
              BOOK shows at or inside our limit -- an OBSERVATION of a
              book we read, not a forecast about anyone's behaviour.

So `p_fill` here is a COVERAGE FRACTION: of the quantity the sizing
policy intended, how much did the observed ladder actually show inside
our limit. `basis` says exactly that, and `is_forecast` is False.

THE THREE WAYS THIS NUMBER CAN STILL BE WRONG, stated rather than
discounted, because an entry decision is made on it:

  1. DISPLAYED DEPTH IS NOT GUARANTEED DEPTH. The venue publishes size
     it is not obliged to still be showing when an order arrives. The
     snapshot module says so in its own payload and this record repeats
     it (`displayed_depth_is_not_a_queue`).
  2. THERE IS A LATENCY GAP between the read and any arrival, and this
     estimate does not model it. `observation_age_s` is carried so the
     gap is visible rather than assumed away. It is NOT adjusted for --
     a haircut invented here would be exactly the invented estimate the
     directive forbids.
  3. COVERAGE 1.0 IS AN OBSERVATION, NOT A DEFAULT. It occurs only when
     the ladder genuinely showed the whole intended size inside the
     limit. When the ladder is unreadable the result is
     NOT_IDENTIFIED and the entry gate refuses; there is no path here
     that reaches 1.0 for want of data.

────────────────────────────────────────────────────────────────────
THE LIMIT PRICE COMES FROM THE BELIEF, NOT FROM THE BOOK.

Sizing to "whatever the ladder holds" and then reporting full coverage
would make the coverage fraction 1.0 by construction and say nothing.
The limit is therefore the BREAK-EVEN price implied by the valuation:

    limit = fair_value - fee_per_contract

At or below it the contract is worth buying on this belief; above it,
it is not, whatever the depth. That makes coverage a real measurement
-- it is below 1 whenever the book is thin or priced beyond break-even,
and 0 when nothing is inside it, which refuses the entry.

THE PRICE THE GATE IS GIVEN IS THE VWAP OF THE ACTUAL WALK, NOT THE
BEST LEVEL. Sizing across several levels and then pricing the trade off
level one understates the cost and manufactures edge out of depth. The
loop previously passed `acquisition_price` -- the best level -- while
intending a size larger than that level. Fixed here: `ask` is the
volume-weighted price of the quantity actually being claimed.

────────────────────────────────────────────────────────────────────
THE RISK LIMITS ARE PREDECLARED FOR THIS LANE, AND DERIVED, NOT PICKED.

`bettor_risk_engine` deliberately carries no numbers. Wired as-is,
every rail is NOT_EVALUABLE, and an exposure increase fails closed --
which is correct and also means no entry can ever be demonstrated. The
limits below are therefore declared HERE, for THIS lane, and every one
of them is a stated multiple of a figure somebody already froze:

    STANDARD = shadow_bettor_sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD

so nothing in this set is a fresh judgement about how much to risk. It
is the frozen trade size, times a declared count of trades.

WHAT THESE LIMITS ARE NOT. They are SHADOW limits. This lane submits
nothing: real order submission stays disabled, capital at risk is zero,
and clearing these rails is not a capital authorization. A live-capital
pilot needs its own limit set approved by the owner against a funded
account, and this module refuses to be that: `LANE` names the shadow
experiment and `REAL_CAPITAL_AT_RISK` is 0.

A rail whose exposure is NOT MEASURED stays NOT_EVALUABLE and blocks
the entry, exactly as before. Predeclaring a limit does not supply a
measurement, and this module never substitutes one for the other.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

from . import bettor_book_snapshot as bs
from . import bettor_risk_engine as risk
from . import shadow
from . import shadow_bettor_sizing as sizing

NOT_IDENTIFIED = "NOT_IDENTIFIED"

EXECUTION_VERSION = "ENTRY_MARKETABLE_EXECUTION_V1"

#: The basis string that travels with every p_fill this module produces.
#: It names an observation. Nothing here is a forecast.
MARKETABLE_BASIS = "MARKETABLE_COVERAGE_OF_OBSERVED_ARRIVAL_LADDER"

#: The other engine, named so a reader can see it was not used.
RESTING_BASIS_NOT_USED = "PRINT_THROUGH_WITH_QUEUE_SHARE_V1"

WHY_NOT_A_FORECAST = (
    "this is the fraction of the intended quantity that the arrival "
    "ladder actually showed at or inside the break-even limit. It is an "
    "observation of a book we read, not a probability that a resting "
    "order would be filled. Displayed depth is not a queue position and "
    "is not adjusted for the latency gap between the read and an "
    "arrival; the gap is reported, not discounted")

# ── refusals, each naming one missing thing ─────────────────────────
R_NO_LADDER = "ACQUISITION_LADDER_NOT_READABLE"
R_NO_FAIR_VALUE = "BREAK_EVEN_LIMIT_NOT_COMPUTABLE_WITHOUT_A_VALUATION"
R_NO_FEE_FN = "FEE_FUNCTION_NOT_SUPPLIED"
R_LIMIT_NOT_POSITIVE = "BREAK_EVEN_LIMIT_IS_NOT_A_TRADEABLE_PRICE"
R_NOTHING_INSIDE_LIMIT = "NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT"
R_FILL_NOT_IDENTIFIED = "MARKETABLE_FILL_NOT_IDENTIFIED"


def ladder_as_arrival_book(ladder: dict) -> dict:
    """The acquisition ladder in the shape `shadow.marketable_fill` reads.

    NO ARITHMETIC HAPPENS HERE, AND THAT IS DELIBERATE. The ladder is
    already in ACQUISITION (cost) space: `acquisition_price` is what one
    contract of the side we want costs, whichever side of the venue's
    book that consumes. Acquiring is therefore a BUY at that price, and
    the levels go under "asks" unchanged. The conversion from the
    venue's YES-denominated prices happened once, in
    `bettor_book_snapshot.acquisition_ladder`, and is not repeated.
    """
    levels = [{"price": float(lv["acquisition_price"]),
               "qty": float(lv["qty"])}
              for lv in (ladder or {}).get("levels") or []]
    return {"asks": levels, "bids": []}


#: What `walk_fee` reports about whose arithmetic answered.
FEE_VIA_PUBLISHED_ORDER_SCHEDULE = "CALIBRATION_FEES_ORDER_FEES"
FEE_VIA_CALLER_PER_LEVEL_SUM = "CALLER_FEE_FN_SUMMED_PER_LEVEL"


class _NotTheTakerCurve(Exception):
    """The caller's fee_fn is not pricing the published taker curve.

    Raised internally so the reason travels with the refusal instead of being
    reconstructed. It is never allowed to escape `walk_fee`: a fee question
    must not be able to abort a sizing decision.
    """


def walk_fee(levels_taken, filled, fee_fn) -> dict:
    """THE FEE A DEPTH WALK ACTUALLY INCURS, under the published algorithm.

    A MARKETABLE WALK ACROSS THREE LADDER LEVELS IS A THREE-FILL ORDER. This
    function existed as one expression inside `estimate`:

        sum(abs(fee_fn(qty=lv["qty"], price=lv["price"]))
            for lv in levels_taken) / filled

    which charges every level its own independently rounded fee. The venue's
    published rule is the opposite: each fill is charged its banker's-rounded
    fee ADJUSTED so the order's total never exceeds the banker's rounding of
    the cumulative exact fee. Summing independent roundings is the calculation
    that rule replaces, and it was being applied to the number the economic
    comparison uses.

    THIS IS THE DEFECT MY OWN FEE WORK MISSED. I corrected
    `calibration_fees`, added `order_fees` with the cumulative cap, and
    reported the finding closed -- while `order_fees` had no production caller
    and this expression went on summing per level. A corrected module is not
    corrected fees.

    WHY THE `fee_fn` FALLBACK REMAINS, AND HOW IT IS DECIDED. `estimate` takes
    a `fee_fn` from its caller, and not every caller is on the published taker
    curve: one passes a maker-side function (a REBATE, opposite sign and about
    a fifth the size) and the tests pass doubles. The cumulative cap is a TAKER
    rule -- the same page says maker rebates are computed per fill
    independently -- so applying it to a maker walk would be a new defect of
    exactly the kind this function removes.

    So the caller's function is not guessed at from its name or its module: it
    is CHECKED against the published single-fill taker fee at each level
    actually taken. Agreement to the cent on every level means the caller is
    pricing the taker curve and the cap applies. Any disagreement means it is
    pricing something else, its own summation stands, and
    `schedule_reaches: False` says so -- so a path that is not on the
    published algorithm can never be reported as though it were.

    Both numbers are returned either way: `independent` is what the old
    expression would have produced, so the difference is visible in the record
    rather than having to be reconstructed from a schedule.
    """
    legs = [(float(lv["qty"]), float(lv["price"]))
            for lv in (levels_taken or [])
            if float(lv.get("qty") or 0) > 0]
    f = float(filled or 0.0)
    independent = None
    if legs:
        try:
            independent = round(
                sum(abs(float(fee_fn(qty=q, price=p))) for q, p in legs)
                / f, 8) if f else 0.0
        except Exception:                                      # noqa: BLE001
            independent = None
    out = {"total": None, "per_contract": independent,
           "basis": FEE_VIA_CALLER_PER_LEVEL_SUM,
           "schedule_reaches": False, "cap_applied": None,
           "independent": independent,
           "why_not_the_schedule": None}
    if not legs or not f:
        out["why_not_the_schedule"] = "no level was taken"
        return out
    try:
        from . import calibration_fees as CF
        # IS THE CALLER ON THE PUBLISHED TAKER CURVE? Checked level by level
        # against the schedule's own single-fill answer, because that is the
        # only thing that distinguishes a taker fee_fn from a maker one
        # without trusting a name.
        for q, p in legs:
            mine = CF.expected_fee(p, q, CF.ROLE_TAKER)
            if mine.get("BLOCKER") or mine.get("FEE") is None:
                raise _NotTheTakerCurve(
                    "the schedule will not price %s @ %s: %s"
                    % (q, p, mine.get("BLOCKER")))
            theirs = abs(float(fee_fn(qty=q, price=p)))
            if abs(theirs - float(mine["FEE"])) > 0.005:
                raise _NotTheTakerCurve(
                    "the caller's fee_fn gave %.4f at %s x %s where the "
                    "published TAKER curve gives %.4f, so it is pricing "
                    "something else (a maker rebate, or a double) and the "
                    "taker cumulative cap must not be imposed on it"
                    % (theirs, q, p, float(mine["FEE"])))
        got = CF.order_fees(None, legs)
    except _NotTheTakerCurve as exc:
        out["why_not_the_schedule"] = str(exc)
        return out
    except Exception as exc:                                   # noqa: BLE001
        out["why_not_the_schedule"] = (
            "the published schedule raised %s, so the caller's own "
            "summation stands and is reported as such" % type(exc).__name__)
        return out
    if got.get("BLOCKER") or got.get("TOTAL") is None:
        # A SCHEDULE THAT REFUSES TO PRICE IS NOT A ZERO FEE, and it is not a
        # licence to fall back silently either -- the refusal is named.
        out["why_not_the_schedule"] = (
            "the published schedule refused this walk: %s"
            % (got.get("BLOCKER") or "no total"))
        return out
    total = float(got["TOTAL"])
    out.update({
        "total": round(total, 8),
        "per_contract": round(total / f, 8),
        "basis": FEE_VIA_PUBLISHED_ORDER_SCHEDULE,
        "schedule_reaches": True,
        "cap_applied": bool(any(x["adjusted"] for x in got["per_fill"])),
        "cumulative_cap": float(got["cumulative_cap"]),
        "prices_walked": got.get("prices"),
        "per_fill": [{"qty": float(x["quantity"]), "price": x["price"],
                      "collected": float(x["collected"]),
                      "adjusted": x["adjusted"]}
                     for x in got["per_fill"]],
    })
    return out


def estimate(*, ladder, fair_value, fee_fn, observation_age_s=None,
             intended_notional_usd=None, headroom=None) -> dict:
    """Size, marketable execution estimate and price, or a named refusal.

    Returns a dict that is always safe to read: `ok` says whether an
    execution estimate exists, `refusals` names what is missing, and
    `p_fill` is present only when it was measured.

    `headroom` is what `headroom_from_rows` measured for the open shadow
    book. Supplied, the intended budget is REDUCED to what the rails
    still allow at the reservation price -- never raised, and no limit is
    changed. Omitted, the behaviour is exactly as before: the standard
    notional is proposed and the rails judge it afterwards.
    """
    out = {
        "ok": False,
        "version": EXECUTION_VERSION,
        "basis": MARKETABLE_BASIS,
        "is_forecast": False,
        "crossing": True,
        "resting_fill_probability": "NOT_APPLICABLE_THIS_ORDER_CROSSES",
        "resting_basis_not_used": RESTING_BASIS_NOT_USED,
        "why_not_a_forecast": WHY_NOT_A_FORECAST,
        "displayed_depth_is_not_a_queue": True,
        "observation_age_s": (None if observation_age_s is None
                              else round(float(observation_age_s), 3)),
        "latency_gap_is_reported_not_adjusted": True,
        "refusals": [],
    }
    if fee_fn is None:
        out["refusals"].append(R_NO_FEE_FN)
    try:
        fv = None if fair_value is None else float(fair_value)
    except (TypeError, ValueError):
        fv = None
    if fv is None:
        out["refusals"].append(R_NO_FAIR_VALUE)
    if not (ladder or {}).get("ok") or not (ladder or {}).get("levels"):
        out["refusals"].append(R_NO_LADDER)
    if out["refusals"]:
        out["why"] = ("no execution estimate: %s"
                      % ", ".join(out["refusals"]))
        return out

    # THE BREAK-EVEN LIMIT. The fee is charged per contract at the price
    # traded, so it is evaluated at the best acquisition price -- the
    # cheapest contract on offer, which is the most favourable place the
    # fee could be assessed and therefore the widest honest limit. A
    # limit computed at a worse price would refuse trades that are in
    # fact inside break-even.
    best = float(ladder["levels"][0]["acquisition_price"])
    fee_per = abs(float(fee_fn(qty=1.0, price=best)))
    limit = fv - fee_per
    out.update({"fair_value": fv, "fee_per_contract": fee_per,
                "break_even_limit": round(limit, 6),
                "limit_basis": ("fair_value - fee_per_contract; a "
                                "contract above this is not worth "
                                "buying on this belief at any depth")})
    if limit <= 0:
        out["refusals"].append(R_LIMIT_NOT_POSITIVE)
        out["why"] = ("break-even is %.6f, which is not a price that can "
                      "be paid" % limit)
        return out

    # THE INTENT IS A DOLLAR AMOUNT, WHICH IS WHAT THE POLICY FROZE.
    #
    # An earlier version turned the budget into a quantity at the limit
    # and walked THAT, which made the three sizing figures incoherent:
    # buying every contract intended at a price well inside break-even
    # reported UNFILLED NOTIONAL, because fewer dollars had been spent for
    # exactly what was asked for. The budget is the intent; the quantity
    # is whatever the budget buys inside the limit.
    intended_notional = float(
        intended_notional_usd
        if intended_notional_usd is not None
        else sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)
    out["policy_notional_usd"] = intended_notional

    # ── THE RAILS REDUCE THE BUDGET BEFORE THE WALK, NOT AFTER ───────
    #
    # `limit` is the reservation price the rails are measured on, so the
    # cap has to be computed here, where that number first exists.
    #
    # THE CONVERSION FROM A QUANTITY CAP BACK TO A BUDGET uses the BEST
    # price, not the limit. `fill_to_notional` spends dollars, and every
    # level it takes is priced at or above `best`, so a budget of
    # `qty_cap * best` can never buy more than `qty_cap` contracts. It is
    # exact when the walk takes one level and conservative when it takes
    # several -- conservative in the only safe direction.
    if headroom is not None:
        # THE ROUNDED RESERVATION PRICE, WHICH IS THE ONE THE RAILS SEE.
        # `worst_case_cost_per_contract` below is `round(limit, 6)`, and
        # `_entry_plan` multiplies THAT by the size to get the exposure it
        # measures. Sizing against the unrounded `limit` would let a
        # round-half-up of 5e-7 per contract push a 2,000-contract
        # reservation a tenth of a cent past a rail it was sized to fit --
        # and a rail that fails by a tenth of a cent is still a failed
        # rail. One number, used by both.
        cap = qty_cap_from_headroom(
            headroom, worst_case_cost_per_contract=round(limit, 6))
        out["rail_headroom"] = headroom
        out["qty_cap"] = cap
        if not cap.get("ok"):
            out["refusals"].extend(cap.get("refusals") or [R_NO_HEADROOM])
            out["why"] = cap.get("why") or "no rail headroom"
            return out
        rail_budget = float(cap["qty_cap"]) * best
        if rail_budget < intended_notional:
            out["notional_reduced_by_rails"] = True
            out["notional_reduction_why"] = (
                "%s left room for %.6f contracts at the %.6f reservation "
                "price, so the $%.2f standard intent was reduced to "
                "$%.2f. The limit itself is unchanged"
                % (cap["binding_rail"], cap["qty_cap"], limit,
                   intended_notional, rail_budget))
            intended_notional = rail_budget
        else:
            out["notional_reduced_by_rails"] = False
            out["notional_reduction_why"] = (
                "%s left room for %.6f contracts, which is more than the "
                "$%.2f standard intent buys, so the intent governs"
                % (cap["binding_rail"], cap["qty_cap"], intended_notional))

    walk = bs.fill_to_notional(ladder, intended_notional, max_price=limit)
    out.update({"intended_notional_usd": intended_notional,
                "budget_walk": walk,
                "sizing_policy_version": sizing.SIZING_POLICY_VERSION,
                "cohort": sizing.COHORT})
    filled = float(walk.get("filled") or 0.0)
    if filled <= 0:
        out["refusals"].append(R_NOTHING_INSIDE_LIMIT)
        out["why"] = ("the ladder's best acquisition price %.6f is "
                      "beyond the break-even limit %.6f, so nothing is "
                      "worth taking" % (best, limit))
        return out

    # THE SAME QUANTITY, RECONSTRUCTED AGAINST THE OBSERVED BOOK.
    # The budget walk decides how much to ask for; this is the engine
    # that says what the arrival book would actually have given, and it
    # is the one that refuses rather than guessing. Two walks over one
    # ladder must agree on the price, and a disagreement is a refusal
    # rather than a choice between them.
    fill = shadow.marketable_fill(
        shadow.BUY, filled, limit,
        ladder_as_arrival_book(ladder), decision_price=best)
    out["marketable_fill"] = fill
    out["execution_class"] = fill.get("executionClass")
    if fill.get("status") == shadow.NOT_IDENTIFIED or not fill.get("vwap"):
        out["refusals"].append(R_FILL_NOT_IDENTIFIED)
        out["why"] = fill.get("why") or "the arrival book gave no fill"
        return out
    vwap = float(fill["vwap"])
    if abs(vwap - float(walk.get("vwap_acquisition_price") or 0.0)) > 1e-6:
        out["refusals"].append(R_FILL_NOT_IDENTIFIED)
        out["why"] = ("the budget walk and the arrival-book reconstruction "
                      "disagree on the price of the same quantity (%.6f vs "
                      "%.6f). One of them is reading the ladder wrongly and "
                      "picking either would be guessing which"
                      % (walk.get("vwap_acquisition_price"), vwap))
        return out

    # THE FROZEN SIZING POLICY'S OWN THREE FIGURES, from the one function
    # allowed to compute them. What it is given is the total the ladder
    # supports INSIDE THE LIMIT -- so a budget fully spent is
    # FULLY_SUPPORTED and a book that ran out is LIQUIDITY_LIMITED, and
    # the two are no longer confused. That total is DISPLAYED depth at
    # read time, which is not the latency-adjusted book the policy's
    # docstring describes, so the basis is named rather than implied.
    sized = sizing.size(
        executable_notional_usd=float(walk.get("total_inside_limit") or 0.0))
    executed = float(sized.get("executedEntryNotionalUsd") or 0.0)
    coverage = min(1.0, executed / intended_notional)
    _walk_fee = walk_fee(walk.get("levels_taken") or [], filled, fee_fn)
    out.update({
        "ok": True,
        "p_fill": round(coverage, 6),
        "p_fill_is": "EXECUTED_NOTIONAL_OVER_INTENDED_NOTIONAL",
        "size": round(filled, 6),
        # ── THREE PRICES, AND THEY ARE NOT ONE PRICE ────────────────
        #
        # THE DEFECT THIS FIXES. `limit_price` carried the VWAP, and the
        # inventory writer used it as the ORDER'S LIMIT -- so a walk over
        # .62/.64/.66 was recorded as an order limited at .635556 that
        # filled at .64 and .66. An order does not fill above its own
        # limit, and a ledger that says it did cannot be reconciled
        # against any venue.
        #
        #   submitted_limit  what the order would be sent with: the
        #                    break-even price the belief implies. Nothing
        #                    is taken above it.
        #   vwap             what the quantity actually cost, volume
        #                    weighted. An OUTCOME of the walk, never a
        #                    limit.
        #   levels_taken     the per-level evidence: price, quantity and
        #                    cost at each level consumed, so the two
        #                    numbers above can be checked against the book.
        "submitted_limit": round(limit, 6),
        "limit_price": round(limit, 6),
        "limit_price_is": "THE_SUBMITTED_BREAK_EVEN_LIMIT",
        "vwap": round(vwap, 6),
        "vwap_is": "THE_REALISED_COST_OF_THE_WALK_NOT_A_LIMIT",
        "levels_taken": walk.get("levels_taken") or [],
        # ── WHICH NUMBER THE ECONOMICS USE, NAMED HERE ───────────────
        #
        # THE REGRESSION THIS EXISTS TO STOP. Separating the submitted
        # limit from the VWAP fixed the ORDER record and broke the
        # COMPARISON: the caller went on reading `limit_price`, which now
        # meant the break-even, so the gate compared the valuation against
        # the very price derived from it and found an edge of exactly
        # zero. In production that printed as
        # NO_ACTION_HAS_POSITIVE_NET_EDGE on 159 candidates -- a number
        # that looked like a market fact and was arithmetic.
        #
        # So the two uses are now named, not left to a field name:
        #
        #   acquisition_cost_per_contract  the MODELLED cost -- the
        #       volume-weighted price the walk actually paid. This is what
        #       the economic comparison uses, with the fees the walk
        #       actually incurred.
        #   worst_case_cost_per_contract   the submitted limit. Nothing
        #       fills above it, so it is the RESERVATION: the most this
        #       position could cost. Exposure is measured on it.
        #
        # They are different questions. Pricing the edge on the worst case
        # understates it; reserving exposure on the modelled cost
        # understates that. Each gets its own number.
        "acquisition_cost_per_contract": round(vwap, 6),
        "acquisition_cost_is": "MODELLED_VOLUME_WEIGHTED_COST_OF_THE_WALK",
        "fee_per_contract_realised": _walk_fee["per_contract"],
        "fee_total_usd": _walk_fee["total"],
        "fee_basis": _walk_fee["basis"],
        "fee_schedule_reaches_this_path": _walk_fee["schedule_reaches"],
        "fee_cap_applied": _walk_fee["cap_applied"],
        "fee_if_levels_were_priced_independently": _walk_fee["independent"],
        "worst_case_cost_per_contract": round(limit, 6),
        "worst_case_cost_is": (
            "THE_SUBMITTED_LIMIT_NOTHING_FILLS_ABOVE_IT"),
        "economics_use": "acquisition_cost_per_contract",
        "reservation_uses": "worst_case_cost_per_contract",
        "best_acquisition_price": best,
        "slippage_vs_best": fill.get("slippage"),
        "spread_cost": fill.get("spreadCost"),
        "cost_usd": round(float(walk.get("cost") or 0.0), 6),
        "unfilled_notional_usd": sized.get("unfilledNotionalUsd"),
        # WHICH INTENT `sizing` REPORTED AGAINST. The frozen policy holds
        # its own standard notional and compares the book against THAT,
        # so `sizing.status` answers "could the book support a standard
        # trade?" -- not "did we buy what we asked for". After a rail
        # reduction those are different questions and a reader must not
        # take LIQUIDITY_LIMITED as evidence the rails were the cause, or
        # the reverse.
        "sizing_status_is_measured_against": "THE_FROZEN_POLICY_NOTIONAL",
        "requested_notional_after_rails_usd": round(intended_notional, 6),
        "levels_consumed": walk.get("levels_used"),
        "levels_consumed_price_range": [
            best, (walk.get("levels_taken") or [{}])[-1].get("price", best)],
        "sizing": sized,
        "executable_notional_basis": "DISPLAYED_LADDER_AT_READ_TIME",
        "executable_notional_is_not_latency_adjusted": True,
        "why": ("the ladder supported $%.2f of the $%.2f intended at or "
                "inside break-even %.6f, which is %.6f contracts at a "
                "volume-weighted %.6f"
                % (executed, intended_notional, limit, filled, vwap)),
    })
    return out


# ── THIS LANE'S PREDECLARED RISK LIMITS ─────────────────────────────

LANE = "EXT_PINNACLE_EXTERNAL_VALUATION_SHADOW"
REAL_ORDER_SUBMISSION_ENABLED = False
REAL_CAPITAL_AT_RISK = 0

STANDARD = float(sizing.STANDARD_BETTOR_SHADOW_NOTIONAL_USD)

#: Every number is STANDARD times a declared count. The count is the
#: judgement and it is written down beside it; the dollar figure is not
#: an independent choice.
LIMIT_BASIS = {
    "MAX_MARKET_EXPOSURE": (1, "one standard trade per market. This lane "
                               "takes a view on one side of one market; "
                               "adding to it is a second decision that "
                               "must clear this rail again"),
    "MAX_EVENT_EXPOSURE": (1, "one standard trade per EVENT, not per "
                              "market. Two markets on the same game are "
                              "one bet on that game, which is the whole "
                              "reason this rail is separate"),
    "MAX_CAPITAL_DEPLOYED": (3, "three standard trades open at once "
                                "across the whole shadow book"),
    # MAX_CORRELATED_EXPOSURE, AND THE ASSUMPTION THAT MAKES IT
    # MEASURABLE. Nothing in this lane's data says which markets share a
    # driver -- there is no correlation matrix, and inventing one would
    # be the worst kind of invented input, since it would DISCOUNT
    # exposure. So the measurement assumes the WORST CASE: every open
    # directional position in the shadow book is treated as perfectly
    # correlated with this one, and the rail is measured against their
    # total.
    #
    # That is conservative in the only direction that matters. It can
    # refuse an entry whose real correlation is low; it can never permit
    # one whose real correlation is high. Capping the worst case at one
    # standard trade means this lane holds at most one standard trade of
    # directional exposure at a time -- binding well inside the
    # three-trade capital cap, which is the point.
    "MAX_CORRELATED_EXPOSURE": (1, "one standard trade of worst-case "
                                   "correlated exposure. No correlation "
                                   "structure is known, so every open "
                                   "position counts in full"),
}

WORST_CASE_CORRELATION_ASSUMPTION = (
    "MAX_CORRELATED_EXPOSURE is measured on the assumption that every "
    "open directional position is perfectly correlated with the "
    "proposed one, because no correlation structure is available. The "
    "assumption can only refuse entries, never permit them, which is "
    "why it is admissible where a fitted correlation would not be")

#: Directional and time rails are counted in their own units, not in
#: dollars, so they are declared separately rather than scaled.
MAX_RESIDUAL_INVENTORY_CONTRACTS = 2000.0
MAX_CAPITAL_HOURS_USD_HOURS = STANDARD * 72.0
MAX_DRAWDOWN_USD = STANDARD * 1.0

PREDECLARED_LIMITS = {
    "MAX_MARKET_EXPOSURE": STANDARD * LIMIT_BASIS[
        "MAX_MARKET_EXPOSURE"][0],
    "MAX_EVENT_EXPOSURE": STANDARD * LIMIT_BASIS["MAX_EVENT_EXPOSURE"][0],
    "MAX_CAPITAL_DEPLOYED": STANDARD * LIMIT_BASIS[
        "MAX_CAPITAL_DEPLOYED"][0],
    "MAX_RESIDUAL_INVENTORY": MAX_RESIDUAL_INVENTORY_CONTRACTS,
    "MAX_CAPITAL_HOURS": MAX_CAPITAL_HOURS_USD_HOURS,
    "MAX_DRAWDOWN": MAX_DRAWDOWN_USD,
    "MAX_CORRELATED_EXPOSURE": STANDARD * LIMIT_BASIS[
        "MAX_CORRELATED_EXPOSURE"][0],
}

UNDECLARED_RAILS = tuple(
    n for n in risk.RAILS if n not in PREDECLARED_LIMITS)

#: ── ONE TYPED LIMIT SCHEMA (audit finding A4) ───────────────────────
#:
#: THE DEFECT. The numbers were right and what they MEANT was declared in four
#: places that disagreed. `MAX_RESIDUAL_INVENTORY` is counted in CONTRACTS here
#: (`MAX_RESIDUAL_INVENTORY_CONTRACTS`, and `exposure_from_rows` sums `qty`) and
#: the activation proposal presented it as dollars -- so an owner approving "$50"
#: was approving fifty CONTRACTS, which at the observed 0.35-0.65 prices is
#: roughly $18-$33 of exposure, not $50. `MAX_DRAWDOWN` is reached through an
#: owner field called `daily_loss_stop_usd` and has no daily window at all.
#: `MAX_CAPITAL_HOURS` is dollar-hours and reads like a duration.
#:
#: SO THE UNIT, THE WINDOW AND THE AGGREGATION SCOPE ARE DECLARED ONCE, HERE,
#: beside the rail they describe, and every reader -- owner approval, activation,
#: the authorization digest, sizing, enforcement and the Command Centre -- takes
#: them from this one table. A display that formats a contract count with a
#: dollar sign is then a bug in one place rather than a difference of opinion
#: between four.
UNIT_USD = "USD"
UNIT_CONTRACTS = "CONTRACTS"
UNIT_USD_HOURS = "USD_HOURS"

WINDOW_OPEN_BOOK = "OPEN_BOOK_AT_THE_DECISION_INSTANT"
WINDOW_CUMULATIVE = "CUMULATIVE_OVER_THE_OPEN_BOOK_NO_RESET"
WINDOW_ACCRUED = "ACCRUED_TO_THE_DECISION_INSTANT"

SCOPE_ONE_MARKET = "ONE_CONDITION_ID"
SCOPE_ONE_EVENT = "ONE_VENUE_EVENT_SLUG"
SCOPE_WHOLE_BOOK = "EVERY_OPEN_POSITION_IN_THIS_LANE"

RAIL_TYPES = {
    "MAX_MARKET_EXPOSURE": {
        "unit": UNIT_USD, "window": WINDOW_OPEN_BOOK,
        "scope": SCOPE_ONE_MARKET,
        "measures": "cost basis of open positions on the same condition id",
        "owner_field": "per_order_usd"},
    "MAX_EVENT_EXPOSURE": {
        "unit": UNIT_USD, "window": WINDOW_OPEN_BOOK,
        "scope": SCOPE_ONE_EVENT,
        "measures": ("cost basis of open positions sharing the VENUE's event "
                     "slug. Requires a resolvable event identity on every open "
                     "row; see A9"),
        "owner_field": "event_exposure_usd"},
    "MAX_CAPITAL_DEPLOYED": {
        "unit": UNIT_USD, "window": WINDOW_OPEN_BOOK,
        "scope": SCOPE_WHOLE_BOOK,
        "measures": "cost basis of every open position in this lane",
        "owner_field": "capital_usd"},
    "MAX_CORRELATED_EXPOSURE": {
        "unit": UNIT_USD, "window": WINDOW_OPEN_BOOK,
        "scope": SCOPE_WHOLE_BOOK,
        "measures": ("the same total, under the worst-case assumption that "
                     "every open position moves together"),
        "owner_field": "max_exposure_usd"},
    "MAX_RESIDUAL_INVENTORY": {
        "unit": UNIT_CONTRACTS, "window": WINDOW_OPEN_BOOK,
        "scope": SCOPE_WHOLE_BOOK,
        "measures": "unmatched contract count, NOT a dollar amount",
        "owner_field": None,
        "not_owner_approvable": ("no owner field maps to this rail, so an "
                                 "approval cannot tighten it. It stays at its "
                                 "frozen contract count"),
        "a_dollar_sign_here_is_a_bug": True},
    "MAX_CAPITAL_HOURS": {
        "unit": UNIT_USD_HOURS, "window": WINDOW_ACCRUED,
        "scope": SCOPE_WHOLE_BOOK,
        "measures": ("dollars multiplied by hours ALREADY held. Not a holding "
                     "period and not a forecast"),
        "owner_field": None,
        "not_owner_approvable": "no owner field maps to this rail"},
    "MAX_DRAWDOWN": {
        "unit": UNIT_USD, "window": WINDOW_CUMULATIVE,
        "scope": SCOPE_WHOLE_BOOK,
        # ── THE THREE TERMS, EXACTLY AS `exposure_from_rows` SUMS THEM ──
        #
        # This entry used to name two terms and the summary in the activation
        # package named the same two, which UNDERSTATED the rail: a marked
        # unsettled position contributes its mark-to-market loss, and only an
        # UNMARKED one contributes its whole basis. An owner reading the old
        # text would expect a marked position to contribute nothing until it
        # settled. It contributes as soon as its mark falls below its cost.
        "measures": ("three terms summed over the lane's whole book: (1) for "
                     "each SETTLED position, max(0, -realised_net) -- realised "
                     "losses in full, and realised GAINS do not offset them; "
                     "(2) for each unsettled position WITH a mark, max(0, cost "
                     "- mark); (3) for each unsettled position WITHOUT a mark, "
                     "the entire cost basis, counted as a total loss. The "
                     "proposed position is unsettled and unmarked by "
                     "construction, so its full cost is added too"),
        "owner_field": "daily_loss_stop_usd",
        "the_owner_field_name_is_inaccurate": (
            "it says daily. There is no daily window, no calendar boundary and "
            "no reset: this is a CUMULATIVE worst-case loss ceiling on the open "
            "book. The field name is kept because recorded approvals use it, "
            "and `cumulative_loss_stop_usd` is accepted as the accurate "
            "synonym"),
        # ── WHAT ACTUALLY CLEARS IT, WHICH IS NOT A CLOCK ────────────────
        #
        # `ext_pinnacle_loop.OPEN_BOOK_SQL` is `WHERE p.experiment_id = $1`
        # with no time bound at all, so every position the lane has ever taken
        # stays in the row set. Terms (2) and (3) fall as positions mark better
        # or settle. Term (1) never falls: once a position settles at a loss it
        # contributes that loss for as long as the experiment id does. So the
        # rail is monotonically non-decreasing in realised losses, and the only
        # thing that clears accumulated realised loss is CHANGING THE
        # EXPERIMENT ID -- which starts a new book, and is an operator act, not
        # a reset.
        "what_reduces_it": ("terms 2 and 3 fall when a position marks better "
                            "or settles. Term 1 never falls"),
        "what_clears_it": ("nothing on a schedule. Accumulated realised loss "
                           "is cleared only by changing the experiment id, "
                           "because OPEN_BOOK_SQL selects on experiment_id "
                           "with no time bound. That starts a new book and is "
                           "an operator act"),
        "a_real_daily_window_would_need": ("a date-bounded realised-loss query "
                                           "and a declared reset boundary with "
                                           "a timezone. Neither exists")},
}

#: Every rail declared, and every owner field mapped to exactly one rail.
RAIL_TYPES_COVER_EVERY_DECLARED_RAIL = (
    set(RAIL_TYPES) == set(PREDECLARED_LIMITS))


def rail_type(name) -> dict:
    """One rail's unit, window and scope. Never guessed by a display."""
    return dict(RAIL_TYPES.get(str(name)) or {
        "unit": None, "window": None, "scope": None,
        "why": "this rail has no typed declaration, so no unit may be assumed"})


def format_rail(name, value) -> str:
    """A rail's value WITH its unit, for any reader that shows one.

    A single function, so a contract count can never acquire a dollar sign on
    its way to a screen -- which is the A4 defect in its simplest form.
    """
    t = rail_type(name)
    if value is None:
        return "—"
    unit = t.get("unit")
    if unit == UNIT_USD:
        return "$%s" % ("%.2f" % float(value)).rstrip("0").rstrip(".")
    if unit == UNIT_CONTRACTS:
        return "%g contracts" % float(value)
    if unit == UNIT_USD_HOURS:
        return "%g $·h" % float(value)
    return "%g (unit undeclared)" % float(value)


def typed_limits(approved: dict | None = None) -> dict:
    """The side-by-side readback A4 asks for: frozen, approved, effective --
    each with its unit, window, aggregation scope and owner field."""
    eff = effective_limits(approved)
    rows = []
    for name in sorted(PREDECLARED_LIMITS):
        t = rail_type(name)
        of = t.get("owner_field")
        rows.append({
            "rail": name,
            "unit": t.get("unit"),
            "window": t.get("window"),
            "scope": t.get("scope"),
            "measures": t.get("measures"),
            "owner_field": of,
            "owner_proposed": (None if not of
                               else (approved or {}).get(of)),
            "frozen": eff["frozen"].get(name),
            "effective": eff["effective"].get(name),
            "frozen_display": format_rail(name, eff["frozen"].get(name)),
            "effective_display": format_rail(name,
                                             eff["effective"].get(name)),
            "tightened": name in (eff.get("tightened") or {}),
            "owner_approvable": of is not None,
            "note": (t.get("the_owner_field_name_is_inaccurate")
                     or t.get("not_owner_approvable")),
        })
    return {
        "rows": rows,
        "frozen_digest": eff.get("frozen_digest"),
        "effective_digest": eff.get("effective_digest"),
        "an_approval_can_only_tighten": True,
        "units_are_declared_not_inferred": (
            "every value above carries the unit its rail is measured in. "
            "MAX_RESIDUAL_INVENTORY is CONTRACTS and MAX_CAPITAL_HOURS is "
            "dollar-hours; presenting either with a dollar sign is a bug"),
        "loss_window": RAIL_TYPES["MAX_DRAWDOWN"]["window"],
        "loss_window_means": RAIL_TYPES["MAX_DRAWDOWN"][
            "the_owner_field_name_is_inaccurate"],
    }


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


LIMITS_SHA = _sha(PREDECLARED_LIMITS)


# ── AN APPROVED LIMIT SET, CONSUMED BY THE ENFORCEMENT PATH ─────────
#
# WHY THIS EXISTS. The desk let an owner record a limit set, and nothing
# read it: a number typed into a page that no rail consulted is not a
# limit. This is the consuming function, and it has exactly one rule --
#
#   AN APPROVAL CAN ONLY TIGHTEN.
#
# The effective limit for a rail is min(frozen, approved). An approved
# value ABOVE the frozen rail is ignored and reported as ignored, so no
# form can ever raise a rail; and the effective set carries its own digest,
# so a reader can tell an enforced-with-approval run from a frozen one.
APPROVED_LIMIT_TO_RAIL = {
    "capital_usd": "MAX_CAPITAL_DEPLOYED",
    "per_order_usd": "MAX_MARKET_EXPOSURE",
    "max_exposure_usd": "MAX_CORRELATED_EXPOSURE",
    "daily_loss_stop_usd": "MAX_DRAWDOWN",
    # THE RAIL AN APPROVAL COULD NOT REACH.
    #
    # The four names above leave MAX_EVENT_EXPOSURE at its frozen $1,000
    # however small the approved pilot is -- so a $250 approval bought a $250
    # capital cap, a $25 per-order cap, and an event cap forty times the whole
    # pilot. It was not a missing rail; it was a rail with no name the owner
    # could tighten it by. Adding the name changes no digest for an approval
    # that does not use it (effective_limits takes MIN over the rails, so an
    # absent name leaves that rail frozen exactly as before).
    #
    # TIGHTENING IT WAS NOT THE SAME AS ENFORCING IT, AND THAT SECOND HALF IS
    # NOW CLOSED TOO (audit finding A9). This comment used to read "OPEN_BOOK_SQL
    # selects no event key, so the event rail cannot see other positions on the
    # same event" -- true when written, and false now:
    #
    #   * `workers/ext_pinnacle_loop.OPEN_BOOK_SQL` joins `us_premap` and selects
    #     `event_slug AS event_key` with `event_key_resolved`;
    #   * the candidate's key comes from the SAME column, via
    #     `resolve_venue_identity`, so both sides are in the venue's namespace.
    #     Passing the odds provider's `event_id` would have left this rail at
    #     zero even on a repaired query;
    #   * a row whose event does not resolve makes the rail NOT_EVALUABLE and
    #     the plan refuses by name, rather than summing what it can and calling
    #     the rail satisfied.
    #
    # The one-position-at-a-time workaround this comment used to require is no
    # longer the only protection.
    "event_exposure_usd": "MAX_EVENT_EXPOSURE",
}


def effective_limits(approved: dict | None = None) -> dict:
    """PREDECLARED ∧ APPROVED, element-wise minimum. Never a raise."""
    import hashlib
    import json as _json

    eff = dict(PREDECLARED_LIMITS)
    tightened, ignored, unmapped = {}, {}, {}
    for name, value in dict(approved or {}).items():
        rail = APPROVED_LIMIT_TO_RAIL.get(name)
        if rail is None:
            unmapped[name] = value
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            ignored[name] = {"value": value, "why": "not a number"}
            continue
        frozen = float(PREDECLARED_LIMITS[rail])
        if v < frozen:
            eff[rail] = round(v, 6)
            tightened[rail] = {"from": frozen, "to": round(v, 6),
                               "approved_as": name}
        else:
            ignored[name] = {"value": v, "rail": rail, "frozen": frozen,
                             "why": ("an approval may only TIGHTEN a rail, "
                                     "so a looser number is ignored")}
    digest = hashlib.sha256(
        _json.dumps(eff, sort_keys=True).encode()).hexdigest()
    return {
        "effective": eff,
        "frozen": dict(PREDECLARED_LIMITS),
        "frozen_digest": LIMITS_SHA,
        "effective_digest": digest,
        "tightened": tightened,
        "ignored_because_looser": ignored,
        "unmapped_approved_names": unmapped,
        "rule": "EFFECTIVE = MIN(FROZEN, APPROVED); AN APPROVAL NEVER RAISES",
        "is_not_a_capital_authorization": (
            "tightening a shadow rail authorises nothing. Real submission "
            "stays off in code and the venue-boundary gate is separate"),
    }


# ── THE SUBMISSION AUTHORIZATION GATE ───────────────────────────────
#
# WHY IT IS HERE AND NOT IN THE PANEL. `bettor_funded_activation` decides
# whether an authorization may be RECORDED. Recording one proves nothing
# about execution: an authorization nothing consumes is a note in a table.
# This is the gate on the EXECUTION side -- the single sanctioned entry to a
# submission -- and it CONSUMES the recorded authorization: it matches the
# account, the venue and the digest of the limits that were approved, and
# only then reaches the hard constant that keeps real submission off.
#
# WHAT IT DOES NOT DO. It never submits, and it never enables submission.
# With a valid authorization the answer changes from
# SUBMISSION_IS_NOT_AUTHORIZED... to REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE
# -- and that difference is the proof that the authorization was read.

R_NO_AUTHORIZATION = "NO_SUBMISSION_AUTHORIZATION_HAS_BEEN_RECORDED"
R_AUTH_ACCOUNT = "THE_AUTHORIZATION_NAMES_A_DIFFERENT_ACCOUNT"
R_AUTH_VENUE = "THE_AUTHORIZATION_NAMES_A_DIFFERENT_VENUE"
R_AUTH_LIMITS = "THE_AUTHORIZATION_DOES_NOT_COVER_THESE_EFFECTIVE_LIMITS"
R_AUTH_NO_DIGEST = "THE_AUTHORIZATION_CARRIES_NO_EFFECTIVE_LIMIT_DIGEST"
R_AUTH_EXPIRED = "THE_AUTHORIZATION_HAS_EXPIRED"
R_AUTH_REVOKED = "THE_AUTHORIZATION_HAS_BEEN_REVOKED"
R_SUBMISSION_DISABLED = "REAL_ORDER_SUBMISSION_IS_DISABLED_IN_CODE"

#: AN AUTHORIZATION WITH NO USABLE END IS NOT AN AUTHORIZATION.
#:
#: THE HOLE THIS CLOSES. `expires_at` was read, and when it was absent the
#: code fell back to `at + AUTHORIZATION_TTL_S`. If `at` was ALSO missing or
#: unparseable the fallback produced None -- and the expiry check was written
#: `if exp is not None:`, so the whole check was SKIPPED. A record carrying
#: neither timestamp was therefore a standing permission that never lapsed,
#: which is the exact failure a TTL exists to prevent.
R_AUTH_NO_EXPIRY = "THE_AUTHORIZATION_HAS_NO_USABLE_EXPIRY"

#: AND A TIMESTAMP THAT IS NOT A FINITE NUMBER IS WORSE THAN A MISSING ONE,
#: because arithmetic on it silently succeeds. `float("nan")` compares FALSE
#: against everything, so `left <= 0` was false and a NaN expiry PASSED the
#: gate; `float("inf")` passed as an authorization that never ends; and a
#: malformed string raised ValueError out of the gate instead of refusing.
R_AUTH_BAD_EXPIRY = "THE_AUTHORIZATION_EXPIRY_IS_NOT_A_FINITE_TIMESTAMP"

#: THE DIGEST MUST BE COMPARED, NOT MERELY PRESENT.
#:
#: THE HOLE THIS CLOSES. The comparison was guarded by
#: `if approved_limits is not None:`, so any caller that did not happen to
#: supply the owner-approved set got past the limit check entirely -- the
#: authorization was accepted on the strength of carrying SOME digest, with
#: nothing establishing it was the digest of the limits in force NOW.
R_AUTH_LIMITS_UNKNOWN = "THE_APPROVED_LIMIT_SET_WAS_NOT_SUPPLIED_TO_COMPARE"

# ── ACCOUNT-WIDE EXPOSURE, AS A SUBMISSION PRECONDITION ─────────────
#
# WHY THESE EXIST. `bettor_account_exposure` measures exposure across every path
# sharing the account. Building that reader was not closure: NOTHING CONSULTED
# IT. This gate is the submission path, and it now demands the evidence the same
# way it demands `approved_limits` -- absent evidence is a refusal, not a pass.
#
# THE FOUR WAYS IT REFUSES ARE DIFFERENT QUESTIONS, and collapsing them would
# hide which one failed:
R_ACCOUNT_EXPOSURE_UNKNOWN = "ACCOUNT_WIDE_EXPOSURE_WAS_NOT_SUPPLIED"
R_ACCOUNT_EXPOSURE_UNREADABLE = "ACCOUNT_WIDE_EXPOSURE_COULD_NOT_BE_MEASURED"
R_ACCOUNT_EXPOSURE_STALE = "THE_ACCOUNT_WIDE_EXPOSURE_EVIDENCE_IS_TOO_OLD"
R_ACCOUNT_EXPOSURE_OTHER_ACCOUNT = "THE_EXPOSURE_EVIDENCE_IS_FOR_ANOTHER_ACCOUNT"
R_ACCOUNT_EXPOSURE_EXCEEDED = "THE_ACCOUNT_WIDE_CAP_WOULD_BE_EXCEEDED"

#: How old the exposure evidence may be at the moment of authorization.
#:
#: AN EXPLICIT POLICY ASSUMPTION, labelled as one. 60 s is chosen, not derived.
#: It bounds how long ago we looked; it does NOT bound what another writer did
#: since -- see `CONCURRENT_WRITER_LIMITATION`.
MAX_EXPOSURE_EVIDENCE_AGE_S = 60.0

#: WHAT THIS GATE CANNOT CONTROL, STATED RATHER THAN IMPLIED.
#:
#: The exposure read happens BEFORE the reservation. Between those two instants
#: another writer can add exposure. Whether that is controllable depends on who
#: the writer is, and the two cases have different answers:
#:
#:   ANOTHER LANE IN THIS DATABASE  controllable. `_ingest_locked` and
#:       `_reserve_exit` take `FOR UPDATE` on the position row in the same order,
#:       so our own lanes serialise. Extending that to cover the exposure read
#:       and the reservation in ONE transaction is what makes the check binding
#:       rather than advisory, and the caller is required to do it -- this gate
#:       cannot verify it from here, so it says so and the test drives the real
#:       transaction.
#:
#:   ANOTHER CREDENTIAL AT THE VENUE  NOT controllable. A different API key, or
#:       a human on the account's web session, writes at the VENUE and takes no
#:       lock of ours. No shared enforcement boundary exists, and none can be
#:       built from our side. For that case the only sound answer is ISOLATION --
#:       demonstrating that no such writer exists -- which is
#:       `bettor_account_exposure.isolation_evidence` and is NOT_DEMONSTRATED.
CONCURRENT_WRITER_LIMITATION = {
    "another_lane_in_this_database": {
        "controllable": True,
        "how": ("take the exposure read and the intent insert in ONE "
                "transaction holding FOR UPDATE on the account's row, in the "
                "same lock order the fill ingest and exit reservation already "
                "use"),
        "who_must_do_it": ("the CALLER. This function is synchronous and holds "
                           "no connection, so it cannot open the transaction "
                           "itself -- it can only refuse without the evidence"),
    },
    "another_credential_at_the_venue": {
        "controllable": False,
        "why": ("a second API key or a human on the account's web session "
                "writes at the VENUE and takes no lock in our database. There "
                "is no shared enforcement boundary and none can be built from "
                "our side"),
        "the_only_sound_answer": ("ISOLATION -- demonstrating no such writer "
                                  "exists. bettor_account_exposure."
                                  "isolation_evidence, currently "
                                  "NOT_DEMONSTRATED"),
        "what_must_not_be_done": ("treat a measured total as a guarantee. It is "
                                  "a measurement of the past, and against an "
                                  "uncontrolled writer that is all it can be"),
    },
}

#: The legacy shape this gate still accepts, named so it is a decision rather
#: than an accident. A record predating `expires_at` carries `at` alone, and
#: its window is `at + AUTHORIZATION_TTL_S`. `at` must itself be a finite
#: number for that to mean anything, and it is checked.
LEGACY_EXPIRY_FALLBACK = "at + AUTHORIZATION_TTL_S, when `expires_at` is absent"

#: HOW LONG AN AUTHORIZATION IS GOOD FOR, by default. An authorization with
#: no end is a standing permission nobody remembers granting, so the record
#: carries an expiry and this gate enforces it.
AUTHORIZATION_TTL_S = 24 * 3600.0


def authorize_submission(*, account_id: str, venue: str,
                         authorization: dict | None,
                         approved_limits: dict | None = None,
                         account_exposure: dict | None = None,
                         proposed_cost_usd: float | None = None,
                         adds_exposure: bool = True,
                         now: float | None = None) -> dict:
    """MAY THIS ACCOUNT SUBMIT AT THIS VENUE? The execution side's answer.

    Every refusal is named, and the LAST one is the code constant, so a
    caller can tell "you are not authorised" from "you are authorised and
    submission is off". `authorization_consumed` is true only when the
    record was actually read and matched.
    """
    out = {"lane": LANE, "account_id": account_id, "venue": venue,
           "submitted": False,
           "real_order_submission_enabled": REAL_ORDER_SUBMISSION_ENABLED,
           "authorization_consumed": False,
           "authorization_source": "bettor_funded_activation.AUTHORIZATION",
           "this_gate_never_submits": True}
    rec = dict(authorization or {})
    if not rec:
        return dict(out, ok=False, refusal=R_NO_AUTHORIZATION,
                    why=("no authorization record was supplied, so there is "
                         "nothing to consume"))
    want_acct = str(rec.get("account_id") or "")
    want_venue = str(rec.get("venue") or "")
    out["authorization"] = {k: rec.get(k) for k in
                            ("account_id", "venue", "venue_class",
                             "effective_digest", "by", "at")}
    if want_acct != str(account_id or "") or not want_acct:
        return dict(out, ok=False, refusal=R_AUTH_ACCOUNT,
                    why="authorised for %r, asked for %r"
                        % (want_acct, account_id))
    if want_venue.upper() != str(venue or "").upper() or not want_venue:
        return dict(out, ok=False, refusal=R_AUTH_VENUE,
                    why="authorised for %r, asked for %r"
                        % (want_venue, venue))
    # THE LIMITS THE OWNER APPROVED ARE PART OF THE AUTHORIZATION. If the
    # approved set has moved since, the authorization does not cover it.
    # REVOKED FIRST. A revoked authorization is not merely stale: someone
    # took it away, and that answer must not be reported as an expiry.
    if rec.get("revoked") or rec.get("revoked_at"):
        return dict(out, ok=False, refusal=R_AUTH_REVOKED,
                    revoked_at=rec.get("revoked_at"),
                    revoked_by=rec.get("revoked_by"),
                    why="the authorization was revoked and is not usable")
    # THEN EXPIRY, WHICH MUST EXIST AND MUST BE A FINITE NUMBER.
    #
    # Three things are separated on purpose, because they need different
    # answers: an expiry we can READ and that has passed (R_AUTH_EXPIRED); an
    # expiry we cannot read at all (R_AUTH_NO_EXPIRY); and an expiry that
    # parses into something arithmetic cannot be trusted with -- NaN, ±inf,
    # a bool, a malformed string (R_AUTH_BAD_EXPIRY). The old code merged the
    # last two into "skip the check".
    exp_raw = rec.get("expires_at")
    basis = "expires_at"
    if exp_raw is None:
        # THE LEGACY FALLBACK, VALIDATED RATHER THAN ASSUMED.
        exp_raw = rec.get("at")
        basis = LEGACY_EXPIRY_FALLBACK
        if exp_raw is None:
            return dict(out, ok=False, refusal=R_AUTH_NO_EXPIRY,
                        expiry_basis=None,
                        legacy_fallback=LEGACY_EXPIRY_FALLBACK,
                        why=("the record carries neither `expires_at` nor an "
                             "`at` to derive one from, so it names no window "
                             "and cannot be treated as current"))
    out["expiry_basis"] = basis
    # `bool` is an int subclass and float(True) == 1.0, which would be a
    # 1970 timestamp read as a deliberate expiry. It is malformed, not old.
    if isinstance(exp_raw, bool):
        return dict(out, ok=False, refusal=R_AUTH_BAD_EXPIRY,
                    expiry_raw=exp_raw, expiry_basis=basis,
                    why="a boolean is not a timestamp")
    try:
        exp = float(exp_raw)
    except (TypeError, ValueError):
        return dict(out, ok=False, refusal=R_AUTH_BAD_EXPIRY,
                    expiry_raw=exp_raw, expiry_basis=basis,
                    why=("%r does not parse as a timestamp, so how long this "
                         "authorization has left is unknown" % (exp_raw,)))
    if exp != exp or exp in (float("inf"), float("-inf")):
        return dict(out, ok=False, refusal=R_AUTH_BAD_EXPIRY,
                    expiry_raw=exp_raw, expiry_basis=basis,
                    why=("%r is not finite. NaN compares false against every "
                         "bound, so it would PASS an expiry test, and an "
                         "infinite expiry is a permission that never lapses"
                         % (exp_raw,)))
    if basis is LEGACY_EXPIRY_FALLBACK:
        exp = exp + AUTHORIZATION_TTL_S
    out["expires_at"] = exp
    left = exp - float(now if now is not None else time.time())
    out["seconds_until_expiry"] = round(left, 3)
    if left <= 0:
        return dict(out, ok=False, refusal=R_AUTH_EXPIRED,
                    expired_by_s=round(-left, 3), expiry_basis=basis,
                    why=("the authorization's window has closed; a new "
                         "one has to be granted"))
    got_digest = str(rec.get("effective_digest") or "").strip()
    if not got_digest:
        return dict(out, ok=False, refusal=R_AUTH_NO_DIGEST,
                    why=("the authorization does not say which effective "
                         "limits it was granted against"))
    # AND THE DIGEST IS COMPARED, ALWAYS. A caller that cannot produce the
    # owner-approved set cannot establish that this authorization covers the
    # limits in force, so it is refused rather than admitted on the strength
    # of carrying some digest.
    if approved_limits is None:
        return dict(out, ok=False, refusal=R_AUTH_LIMITS_UNKNOWN,
                    effective_digest_on_the_record=got_digest,
                    why=("the authorization names effective limits %s, and "
                         "the owner-approved set was not supplied, so "
                         "nothing establishes that those are the limits in "
                         "force now" % got_digest[:12]))
    now_digest = effective_limits(approved_limits)["effective_digest"]
    out["effective_digest_now"] = now_digest
    if now_digest != got_digest:
        return dict(out, ok=False, refusal=R_AUTH_LIMITS,
                    why=("authorised against %s, the approved set now "
                         "digests to %s" % (got_digest[:12],
                                            now_digest[:12])))
    # THE AUTHORIZATION HAS BEEN READ AND IT MATCHES.
    out["authorization_consumed"] = True

    # ── ACCOUNT-WIDE EXPOSURE, CONSUMED HERE OR NOT AT ALL ──────────
    #
    # A reader nothing consults is not a control. This is the submission path, so
    # the evidence is demanded here -- exactly as `approved_limits` is -- and its
    # absence refuses.
    #
    # THE ORDER MATTERS. These checks sit AFTER the authorization has been
    # matched, so a caller with no authorization is told that first; and BEFORE
    # the code constant, so `R_SUBMISSION_DISABLED` cannot mask a cap breach. If
    # submission were ever enabled, an exposure failure must already have
    # refused.
    from . import bettor_account_exposure as _AE
    out["account_exposure_consumed"] = False
    out["exposure_evidence_age_limit_s"] = MAX_EXPOSURE_EVIDENCE_AGE_S
    out["concurrent_writer_limitation"] = CONCURRENT_WRITER_LIMITATION
    out["adds_exposure"] = bool(adds_exposure)
    # AN EXIT REDUCES EXPOSURE, SO IT IS NOT GATED ON MEASURING IT.
    #
    # This is not a loophole and the asymmetry is the point. Requiring an
    # account-wide measurement before an EXIT would mean that an unreadable venue
    # -- the very condition most likely to coincide with wanting out -- strands
    # inventory we already hold. The cap exists to stop exposure GROWING; a sale
    # cannot breach it.
    #
    # THE CALLER DOES NOT GET TO DECIDE THIS LOOSELY. `adds_exposure=False` is
    # passed by the exit path only, the record says which branch ran, and a
    # caller that omits the flag gets the strict behaviour -- so a new entry path
    # written without knowing about this is refused, not admitted.
    if not adds_exposure:
        out["account_exposure_consumed"] = True
        out["account_exposure_not_required_because"] = (
            "this submission REDUCES exposure. The account-wide cap bounds "
            "growth, and a sale cannot breach it -- while requiring a venue "
            "read before an exit would strand inventory exactly when the venue "
            "is unreadable")
        if not REAL_ORDER_SUBMISSION_ENABLED:
            return dict(out, ok=False, refusal=R_SUBMISSION_DISABLED,
                        why=("the authorization covers this account, venue and "
                             "limit set, and real order submission is off in "
                             "code"))
        return dict(out, ok=True, refusal=None,            # pragma: no cover
                    why="authorised to reduce exposure, and submission is on")
    if not isinstance(account_exposure, dict):
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_UNKNOWN,
                    why=("no account-wide exposure evidence was supplied. "
                         "Every rail this lane enforces aggregates "
                         "EVERY_OPEN_POSITION_IN_THIS_LANE, so without an "
                         "account-wide measurement nothing here bounds what "
                         "the ACCOUNT holds"))
    ex = account_exposure
    # 1 · IS IT FOR THIS ACCOUNT? Evidence for another account is not weaker
    #     evidence, it is evidence about something else.
    ex_acct = ex.get("account_id")
    if str(ex_acct or "") != str(account_id or ""):
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_OTHER_ACCOUNT,
                    exposure_account_id=ex_acct,
                    why=("the exposure evidence is for %r and this submission "
                         "is for %r" % (ex_acct, account_id)))
    # 2 · WAS IT MEASURABLE AT ALL? `UNREADABLE` is the fail-closed state, and it
    #     must refuse rather than be read as a zero.
    if ex.get("state") != _AE.TOTAL_MEASURED or ex.get("TOTAL_USD") is None:
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_UNREADABLE,
                    unreadable_paths=list(ex.get("unreadable_required_paths")
                                          or []),
                    why=("account-wide exposure could not be measured (%s). An "
                         "unmeasured account is not an empty one, and the "
                         "venue's own position read is the path that is "
                         "normally missing" % (ex.get("state"),)))
    # 3 · HOW OLD IS THE MEASUREMENT? A number from an hour ago is a number
    #     about an hour ago.
    at = ex.get("measured_at_epoch_s")
    _now = float(now if now is not None else time.time())
    if at is None:
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_STALE,
                    why=("the exposure evidence carries no measurement "
                         "instant, so its age is unknown. An undated "
                         "measurement cannot be shown to be current"))
    age = _now - float(at)
    out["exposure_evidence_age_s"] = round(age, 3)
    if age > MAX_EXPOSURE_EVIDENCE_AGE_S or age < -1.0:
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_STALE,
                    why=("the exposure evidence is %.1f s old against a %.0f s "
                         "limit%s" % (age, MAX_EXPOSURE_EVIDENCE_AGE_S,
                                      "; a negative age means the clocks "
                                      "disagree and it is refused rather than "
                                      "trusted" if age < 0 else "")))
    # 4 · WOULD THIS ORDER BREACH THE ACCOUNT-WIDE CAP?
    #
    #     The cap is the approved MAX_CAPITAL_DEPLOYED, applied to the ACCOUNT
    #     rather than to this lane. That is the whole repair: the same number,
    #     measured over every path instead of over our own rows.
    eff = effective_limits(approved_limits)
    cap = eff["effective"].get("MAX_CAPITAL_DEPLOYED")
    total = float(ex["TOTAL_USD"])
    cost = float(proposed_cost_usd or 0.0)
    out["account_wide"] = {
        "already_committed_usd": round(total, 6),
        "proposed_usd": round(cost, 6),
        "would_be_usd": round(total + cost, 6),
        "cap_usd": cap,
        "cap_is": "MAX_CAPITAL_DEPLOYED, applied ACCOUNT-WIDE",
        "classes_counted": list(ex.get("by_class") or {}),
        "and_this_is_not_the_lane_rail": (
            "the lane's own MAX_CAPITAL_DEPLOYED aggregates "
            "EVERY_OPEN_POSITION_IN_THIS_LANE and is checked elsewhere. This "
            "check is the account, and both must pass"),
    }
    if cap is not None and (total + cost) > float(cap) + 1e-9:
        return dict(out, ok=False, refusal=R_ACCOUNT_EXPOSURE_EXCEEDED,
                    why=("the account already holds %.2f across every path and "
                         "this order proposes %.2f, which would reach %.2f "
                         "against an account-wide cap of %.2f"
                         % (total, cost, total + cost, float(cap))))
    out["account_exposure_consumed"] = True

    # What stops the submission from here is the code constant, and nothing else.
    if not REAL_ORDER_SUBMISSION_ENABLED:
        return dict(out, ok=False, refusal=R_SUBMISSION_DISABLED,
                    why=("the authorization covers this account, venue and "
                         "limit set, and real order submission is off in "
                         "code. Turning it on is a code change, not a "
                         "record"))
    return dict(out, ok=True, refusal=None,                # pragma: no cover
                why="authorised, and submission is enabled in code")


def declaration() -> dict:
    """The limit set, its derivation and what it does not authorise."""
    return {
        "lane": LANE,
        "limits": dict(PREDECLARED_LIMITS),
        "limitsSha": LIMITS_SHA,
        "derivedFrom": {
            "standardNotionalUsd": STANDARD,
            "sizingPolicyVersion": sizing.SIZING_POLICY_VERSION,
            "multiples": {k: v[0] for k, v in LIMIT_BASIS.items()},
            "why": {k: v[1] for k, v in LIMIT_BASIS.items()},
        },
        "undeclaredRails": list(UNDECLARED_RAILS),
        "undeclaredRailsBlockEntry": True,
        "worstCaseCorrelationAssumption":
            WORST_CASE_CORRELATION_ASSUMPTION,
        "realOrderSubmissionEnabled": REAL_ORDER_SUBMISSION_ENABLED,
        "realCapitalAtRisk": REAL_CAPITAL_AT_RISK,
        "isNotACapitalAuthorization": (
            "these are SHADOW limits for a lane that submits nothing. "
            "Clearing them is not permission to trade funded capital; a "
            "live pilot needs its own owner-approved set against a named "
            "account"),
    }


# ── THE MEASUREMENTS THE RAILS ARE COMPARED AGAINST ─────────────────
#
# A PREDECLARED LIMIT WITHOUT A MEASUREMENT IS STILL NOT_EVALUABLE, and
# NOT_EVALUABLE still blocks. So each rail below is given a number that
# is actually computed from the open shadow book plus the position being
# proposed -- never a default, never a zero standing in for "we did not
# look". `rows` is the open book; passing None means the book was not
# read, and then nothing is measured and every rail blocks.
#
# EVERY MEASUREMENT INCLUDES THE PROPOSED POSITION. A rail that compared
# only what is already held would permit the trade that breaches it.
#
# WHERE A QUANTITY CANNOT BE OBSERVED, THE WORST CASE IS USED, never a
# favourable assumption:
#
#   MAX_DRAWDOWN            an unsettled position whose current mark is
#                           unavailable is counted as a TOTAL LOSS. It
#                           may recover; the rail may not assume it
#                           will.
#   MAX_CORRELATED_EXPOSURE every open position is treated as perfectly
#                           correlated (see above).
#   MAX_CAPITAL_HOURS       measures capital-hours ACCRUED to now, which
#                           is observable. It is not a forecast of how
#                           long the proposed position will be held --
#                           this lane has no exit rule yet, so that
#                           number does not exist and is not invented.

R_BOOK_NOT_READ = "OPEN_SHADOW_BOOK_NOT_READ"

CAPITAL_HOURS_IS_ACCRUED_NOT_FORECAST = (
    "MAX_CAPITAL_HOURS is measured as capital x time ALREADY ACCRUED by "
    "the open book. The proposed position contributes nothing yet "
    "because it has been held for no time. Forecasting its occupancy "
    "would need an exit rule, which this lane does not have, and a "
    "guessed holding period is exactly the invented input this system "
    "refuses")

DRAWDOWN_IS_WORST_CASE = (
    "MAX_DRAWDOWN counts realised losses in full plus the entire cost "
    "basis of every unsettled position whose current mark is not "
    "available, as though it settled worthless. An unmarked position is "
    "not a position that is fine")


def _accrued(cost, opened_at, now):
    """Capital-hours this position has already accrued, or 0 if unmeasurable."""
    if opened_at is None or now is None:
        return 0.0
    return float(cost) * max(0.0, (float(now) - float(opened_at)) / 3600.0)


def exposure_from_rows(rows, *, condition_id, event_key, proposed_cost_usd,
                       proposed_qty, now=None,
                       proposed_cost_basis=None) -> dict:
    """Measured exposure per rail, including the proposed position.

    `rows` is the OPEN shadow book: dicts carrying `condition_id`,
    `event_key`, `cost_usd`, `qty`, `opened_at` (epoch seconds) and
    optionally `marked_value_usd` and `realized_net_usd`.

    `proposed_cost_basis` NAMES WHICH PRICE THE RESERVATION USED. A rail
    reserved at the worst-case limit and one reserved at the modelled
    walk are different decisions, and a row that does not say which it
    was cannot be audited later.
    """
    out = {"observed": {}, "rows_read": None, "refusals": [],
           "capitalHoursIsAccruedNotForecast":
               CAPITAL_HOURS_IS_ACCRUED_NOT_FORECAST,
           "drawdownIsWorstCase": DRAWDOWN_IS_WORST_CASE,
           "worstCaseCorrelation": WORST_CASE_CORRELATION_ASSUMPTION,
           "includesTheProposedPosition": True}
    if rows is None:
        out["refusals"].append(R_BOOK_NOT_READ)
        out["why"] = ("the open shadow book was not read, so no rail can "
                      "be measured. Every rail therefore stays "
                      "NOT_EVALUABLE and the entry is blocked")
        return out

    rows = list(rows)
    out["rows_read"] = len(rows)
    cost = float(proposed_cost_usd or 0.0)
    qty = float(proposed_qty or 0.0)

    market = cost
    event = cost
    total = cost
    residual = qty
    capital_hours = 0.0
    drawdown = 0.0
    unmarked = 0
    settled = 0
    for r in rows:
        c = float(r.get("cost_usd") or 0.0)
        realized = r.get("realized_net_usd")
        # A SETTLED POSITION NO LONGER OCCUPIES CAPITAL, and counting it as
        # though it did is not conservatism, it is a wrong measurement: the
        # basis came back at settlement. Left in the exposure sums it
        # would accumulate forever and eventually refuse every entry on a
        # book that is actually flat. Its LOSS still counts, on the
        # drawdown rail, which is the rail that governs realised damage.
        if realized is not None:
            settled += 1
            drawdown += max(0.0, -float(realized))
            capital_hours += _accrued(c, r.get("opened_at"), now)
            continue
        total += c
        residual += float(r.get("qty") or 0.0)
        if str(r.get("condition_id") or "") == str(condition_id or ""):
            market += c
        if (r.get("event_key") is not None
                and str(r.get("event_key")) == str(event_key or "")):
            event += c
        capital_hours += _accrued(c, r.get("opened_at"), now)
        if r.get("marked_value_usd") is not None:
            drawdown += max(0.0, c - float(r["marked_value_usd"]))
        else:
            # UNMARKED AND UNSETTLED: the whole basis is at risk.
            drawdown += c
            unmarked += 1
    # The proposed position is unsettled and unmarked by construction.
    drawdown += cost
    unmarked += 1

    out["observed"] = {
        "MAX_MARKET_EXPOSURE": round(market, 6),
        "MAX_EVENT_EXPOSURE": round(event, 6),
        "MAX_CAPITAL_DEPLOYED": round(total, 6),
        # Worst case: all of it moves together.
        "MAX_CORRELATED_EXPOSURE": round(total, 6),
        "MAX_RESIDUAL_INVENTORY": round(residual, 6),
        "MAX_CAPITAL_HOURS": round(capital_hours, 6),
        "MAX_DRAWDOWN": round(drawdown, 6),
    }
    out["positions_counted_as_total_loss"] = unmarked
    out["settled_positions_excluded_from_exposure"] = settled
    out["proposed"] = {"cost_usd": round(cost, 6), "qty": round(qty, 6),
                       "condition_id": condition_id,
                       "event_key": event_key,
                       "cost_basis": (str(proposed_cost_basis)
                                      if proposed_cost_basis
                                      else "COST_BASIS_NOT_STATED")}
    out["proposed_cost_basis"] = out["proposed"]["cost_basis"]
    out["proposed_cost_usd"] = out["proposed"]["cost_usd"]
    if now is None:
        # An accrued figure needs an instant to accrue to. Reporting 0
        # without one would be a measurement that was never taken.
        out["observed"]["MAX_CAPITAL_HOURS"] = None
        out["capital_hours_why"] = ("no observation instant was supplied, "
                                    "so accrued capital-hours is not "
                                    "measured and that rail blocks")
    return out


# ── HOW BIG A POSITION THE RAILS STILL ALLOW ────────────────────────
#
# THE DEFECT THIS EXISTS TO FIX, and it made this lane unable to enter
# anything at all.
#
# Sizing asked for a STANDARD-dollar notional and the rails then measured
# the RESERVATION -- quantity times the break-even limit, which is the
# right conservative basis. But the dollar rails are themselves STANDARD
# times one. So the comparison was
#
#     qty * break_even_limit   <=   STANDARD
#     (STANDARD / vwap) * limit <=  STANDARD
#     limit / vwap              <=  1
#
# and `limit > vwap` is exactly what having an edge MEANS. So every
# candidate with any edge at all breached MAX_MARKET_EXPOSURE,
# MAX_EVENT_EXPOSURE, MAX_CORRELATED_EXPOSURE and MAX_DRAWDOWN
# simultaneously, and the only position that could ever clear the rails
# was one with zero edge. Production showed it precisely: five candidates
# priced positively, all five carried
# rails_failed [MAX_EVENT_EXPOSURE, MAX_MARKET_EXPOSURE,
# MAX_RESIDUAL_INVENTORY, MAX_CORRELATED_EXPOSURE, MAX_DRAWDOWN], and the
# reserved figures were $1042.05 and $2275.86 against a $1000 rail.
#
# MAX_RESIDUAL_INVENTORY was unreachable for a second, independent
# reason: a STANDARD budget buys STANDARD/price contracts, which exceeds
# the 2000-contract rail at any price under $0.50 -- that is, on every
# underdog.
#
# THE REPAIR IS TO SIZE TO THE HEADROOM, NOT TO PROPOSE AND HOPE. No
# limit moves. The standard trade stays the INTENT CEILING and the rails
# can only ever reduce it, never raise it. Nothing here touches funded
# limits: these are this lane's own predeclared SHADOW numbers.

#: Rails the proposed position enters in DOLLARS of reservation. Read off
#: `exposure_from_rows` above rather than assumed: `cost` is added to
#: market, event, total (which feeds MAX_CAPITAL_DEPLOYED and
#: MAX_CORRELATED_EXPOSURE) and drawdown.
DOLLAR_SCALING_RAILS = (
    "MAX_MARKET_EXPOSURE", "MAX_EVENT_EXPOSURE", "MAX_CAPITAL_DEPLOYED",
    "MAX_CORRELATED_EXPOSURE", "MAX_DRAWDOWN",
)
#: The one rail counted in CONTRACTS.
QTY_SCALING_RAILS = ("MAX_RESIDUAL_INVENTORY",)
#: Rails the proposed position does not move, so no size can fix them.
#: MAX_CAPITAL_HOURS accrues from time held, and the proposal has been
#: held for none.
NON_SCALING_RAILS = ("MAX_CAPITAL_HOURS",)

R_NO_HEADROOM = "NO_RAIL_HEADROOM_FOR_ANY_POSITION"
R_HEADROOM_NOT_MEASURED = "RAIL_HEADROOM_NOT_MEASURED"

#: One millionth of a dollar, held back from every dollar-denominated
#: rail. The exposure a rail is measured on is `round(qty * price, 6)`, so
#: a quantity sized to land EXACTLY on the limit can round up past it.
#: This is the cheapest possible way to make "sized to fit" mean it.
SAFETY_USD = 1e-6


def headroom_from_rows(rows, *, condition_id, event_key, now=None,
                       approved_limits=None) -> dict:
    """What each rail still allows BEFORE anything is proposed.

    The SAME measurement function, asked with a proposed position of
    zero. Using a second implementation here would let the number that
    sizes a position drift from the number that judges it.
    """
    used = exposure_from_rows(
        rows, condition_id=condition_id, event_key=event_key,
        proposed_cost_usd=0.0, proposed_qty=0.0, now=now,
        proposed_cost_basis="NOTHING_PROPOSED_THIS_MEASURES_THE_BOOK_ALONE")
    # THE APPROVED SET IS CONSUMED HERE, and it can only tighten. With no
    # approval this is exactly the frozen set, so the unapproved path is
    # unchanged.
    _eff = effective_limits(approved_limits)
    _limits = _eff["effective"]
    out = {"ok": False, "used": used.get("observed") or {},
           "limits": dict(_limits), "frozen_limits": dict(PREDECLARED_LIMITS),
           "approved_tightened": _eff["tightened"],
           "effective_digest": _eff["effective_digest"], "headroom": {},
           "refusals": list(used.get("refusals") or []),
           "basis": ("PREDECLARED_SHADOW_LIMIT_MINUS_THE_OPEN_BOOK_"
                     "MEASURED_WITH_NOTHING_PROPOSED"),
           "does_not_change_any_limit": True}
    if out["refusals"]:
        out["why"] = used.get("why")
        return out
    # THE PROPOSED POSITION IS ALWAYS COUNTED AS AN UNMARKED TOTAL LOSS
    # by `exposure_from_rows`, and asking with zero proposed removes that
    # +cost -- which is the point: this is the book alone.
    for name, limit in _limits.items():
        obs = out["used"].get(name)
        if obs is None:
            out["headroom"][name] = None
            continue
        out["headroom"][name] = round(float(limit) - float(obs), 6)
    out["ok"] = True
    return out


def qty_cap_from_headroom(headroom: dict, *,
                          worst_case_cost_per_contract) -> dict:
    """The largest quantity every rail still permits, and which one binds.

    The reservation basis is the SAME one the rails are measured on --
    quantity times the worst-case cost per contract -- so the cap and the
    later verdict cannot disagree about what was proposed.
    """
    out = {"ok": False, "qty_cap": None, "binding_rail": None,
           "per_rail_qty_cap": {}, "refusals": [],
           "reservation_basis": "QTY_TIMES_WORST_CASE_COST_PER_CONTRACT",
           "non_scaling_rails": list(NON_SCALING_RAILS)}
    try:
        px = float(worst_case_cost_per_contract)
    except (TypeError, ValueError):
        px = 0.0
    if px <= 0:
        out["refusals"].append(R_HEADROOM_NOT_MEASURED)
        out["why"] = ("the reservation price is %r, so no quantity can be "
                      "derived from a dollar headroom" % (
                          worst_case_cost_per_contract,))
        return out
    if not (headroom or {}).get("ok"):
        out["refusals"].append(R_HEADROOM_NOT_MEASURED)
        out["why"] = ("the open book was not measured, so no rail "
                      "headroom exists to size against")
        return out
    heads = headroom["headroom"]
    caps = {}
    for name in DOLLAR_SCALING_RAILS:
        h = heads.get(name)
        if h is None:
            out["refusals"].append(R_HEADROOM_NOT_MEASURED)
            out["why"] = "%s is not measured, so it cannot be sized against" % name
            return out
        caps[name] = max(0.0, float(h)) / px
    for name in QTY_SCALING_RAILS:
        h = heads.get(name)
        if h is None:
            out["refusals"].append(R_HEADROOM_NOT_MEASURED)
            out["why"] = "%s is not measured, so it cannot be sized against" % name
            return out
        caps[name] = max(0.0, float(h))
    # A NON-SCALING RAIL ALREADY BREACHED IS NOT A SIZING PROBLEM. No
    # quantity reduces accrued capital-hours, so if that rail has no
    # headroom the answer is a refusal, not a smaller trade.
    for name in NON_SCALING_RAILS:
        h = heads.get(name)
        if h is None:
            out["refusals"].append(R_HEADROOM_NOT_MEASURED)
            out["why"] = ("%s is not measured; the proposal does not move "
                          "it, so it cannot be sized around" % name)
            return out
        if float(h) < 0:
            out["refusals"].append(R_NO_HEADROOM)
            out["why"] = ("%s is already beyond its predeclared limit and "
                          "the proposed position does not move it, so no "
                          "size is admissible" % name)
            return out
    out["per_rail_qty_cap"] = {k: round(v, 6) for k, v in caps.items()}
    binding = min(caps, key=lambda k: caps[k])
    raw = caps[binding]
    # FLOORED TO THE SIXTH DECIMAL, which is the precision every quantity
    # on this path is rounded to. Rounding UP here would hand back a cap
    # that breaches by half a micro-contract times the reservation price,
    # and a rail that fails by 5e-7 is still a failed rail.
    #
    # THE MICRO-DOLLAR SHAVE IS NOT SUPERSTITION. Downstream, the cost is
    # `round(qty * price, 6)`, so a product sitting exactly on the rail
    # can round UP past it. `SAFETY_USD` costs a millionth of a dollar of
    # headroom and removes the whole class.
    out["safety_usd"] = SAFETY_USD
    if binding in DOLLAR_SCALING_RAILS:
        raw = max(0.0, (max(0.0, float(heads[binding])) - SAFETY_USD) / px)
    out["qty_cap"] = math.floor(raw * 1e6) / 1e6
    out["binding_rail"] = binding
    out["ok"] = out["qty_cap"] > 0
    if not out["ok"]:
        out["refusals"].append(R_NO_HEADROOM)
        out["why"] = ("%s leaves no headroom at a reservation price of "
                      "%.6f, so no position fits inside the predeclared "
                      "limits" % (binding, px))
    else:
        out["why"] = ("%s binds at %.6f contracts, reserved at %.6f each"
                      % (binding, out["qty_cap"], px))
    return out


# ── THE STATE GATES, DERIVED FROM EVIDENCE ──────────────────────────
#
# `bettor_risk_engine` asks four condition questions. Each is answered
# here from something that was actually established, and `None` --
# NOT_EVALUABLE, which blocks -- whenever nothing was. None of them is
# answered by assertion.
#
#   STALE_DATA                      the freshness contract both sides
#                                   already enforce. Two clocks, and the
#                                   stalest one governs.
#   UNRESOLVED_SETTLEMENT_SEMANTICS the condition/payout comparison from
#                                   `bettor_settlement_terms`. COMPATIBLE
#                                   clears it, INCOMPATIBLE fails it, and
#                                   UNKNOWN leaves it NOT_EVALUABLE.
#   OUT_OF_DISTRIBUTION             the declared support the external
#                                   source is defined over, checked
#                                   against this quote.
#   MODEL_TRUST_DRIFT               calibration evidence for the source
#                                   actually producing the number.
#
# WHY MODEL_TRUST_DRIFT IS THE ONE THAT STANDS. `bettor_pinnacle_devig`
# says of its own default method: "validate in shadow, which is what this
# source is for". So PINNACLE_DEVIG_V1's calibration is UNMEASURED, and
# no argument from this module can change that. The gate is therefore
# NOT_EVALUABLE and it BLOCKS inventory creation.
#
# THAT IS NOT CIRCULAR AND IT IS NOT A DEAD END. What the gate blocks is
# creating a POSITION. It does not block the lane's actual shadow work,
# which is recording the valuation, the price, the costs and the verdict
# on every supported market every cycle -- and those records, paired with
# settled outcomes, are exactly the calibration evidence the gate is
# waiting for. The lane accumulates its own key.
#
# IT IS ALSO NOT SOMETHING TO ARGUE AROUND. A `permitted: True` written
# here because the lane is "only shadow" would be the same placeholder
# this module was built to delete, and it would state that an
# unvalidated external valuation may size a position. It may not.

#: The range PINNACLE_DEVIG_V1 is defined over. Outside it the de-vig
#: arithmetic is not wrong so much as uninformative: a 0.995 favourite
#: carries essentially no vig to remove and the number is dominated by
#: the book's rounding.
SUPPORT_MIN = 0.02
SUPPORT_MAX = 0.98

R_NO_CALIBRATION = "EXTERNAL_SOURCE_CALIBRATION_NOT_MEASURED"


def state_from_evidence(*, freshness=None, settlement=None,
                        probability=None, calibration=None) -> dict:
    """The four gate answers, each with the evidence that produced it."""
    gates, why = {}, {}

    if freshness is None or freshness.get("fresh") is None:
        gates["STALE_DATA"] = None
        why["STALE_DATA"] = ("no freshness verdict was supplied, so "
                             "whether this book is current is unknown")
    else:
        # The gate is CLEAR when the data is NOT stale.
        gates["STALE_DATA"] = bool(freshness["fresh"])
        why["STALE_DATA"] = freshness.get("why") or (
            "the freshness contract was evaluated")

    compat = (settlement or {}).get("compatibility")
    if compat == "COMPATIBLE":
        gates["UNRESOLVED_SETTLEMENT_SEMANTICS"] = True
        why["UNRESOLVED_SETTLEMENT_SEMANTICS"] = (
            "the venue's and the book's payouts were compared condition "
            "by condition and agree")
    elif compat == "INCOMPATIBLE":
        gates["UNRESOLVED_SETTLEMENT_SEMANTICS"] = False
        why["UNRESOLVED_SETTLEMENT_SEMANTICS"] = (
            "the payouts differ under at least one terminal condition: "
            "%s" % ", ".join((settlement or {}).get(
                "mismatched_conditions") or ["unnamed"]))
    else:
        gates["UNRESOLVED_SETTLEMENT_SEMANTICS"] = None
        why["UNRESOLVED_SETTLEMENT_SEMANTICS"] = (
            "settlement compatibility is %s, which is not an agreement"
            % (compat or "NOT_ESTABLISHED"))

    try:
        p = None if probability is None else float(probability)
    except (TypeError, ValueError):
        p = None
    if p is None:
        gates["OUT_OF_DISTRIBUTION"] = None
        why["OUT_OF_DISTRIBUTION"] = "no probability was supplied"
    else:
        inside = SUPPORT_MIN <= p <= SUPPORT_MAX
        gates["OUT_OF_DISTRIBUTION"] = bool(inside)
        why["OUT_OF_DISTRIBUTION"] = (
            "%.6f is %s the declared support [%.2f, %.2f]"
            % (p, "inside" if inside else "outside", SUPPORT_MIN,
               SUPPORT_MAX))

    if not (calibration or {}).get("measured"):
        gates["MODEL_TRUST_DRIFT"] = None
        why["MODEL_TRUST_DRIFT"] = (
            "%s: the external source's calibration has not been "
            "measured, so whether it has drifted cannot be answered. "
            "This gate is the lane's standing blocker on creating "
            "inventory and it does not lift by argument" % R_NO_CALIBRATION)
    else:
        gates["MODEL_TRUST_DRIFT"] = bool(calibration.get("within_tolerance"))
        why["MODEL_TRUST_DRIFT"] = calibration.get("why") or (
            "a calibration measurement was supplied and compared")

    return {"state": gates, "why": why,
            "blocking": sorted(k for k, v in gates.items() if v is not True),
            "declaredSupport": [SUPPORT_MIN, SUPPORT_MAX]}


def verdict(action, *, observed=None, state=None) -> dict:
    """The risk engine's answer for this lane, with its own limits.

    The engine is not told what to conclude. It is given this lane's
    predeclared numbers and whatever has actually been measured, and an
    unmeasured rail still blocks.
    """
    out = risk.evaluate(action, observed=observed, state=state,
                        limits=PREDECLARED_LIMITS)
    out["limitsSha"] = LIMITS_SHA
    out["lane"] = LANE
    out["reason"] = out.get("why")
    return out


def describe() -> dict:
    return {
        "executionVersion": EXECUTION_VERSION,
        "marketableBasis": MARKETABLE_BASIS,
        "restingBasisNotUsed": RESTING_BASIS_NOT_USED,
        "whyNotAForecast": WHY_NOT_A_FORECAST,
        "refusals": [R_NO_LADDER, R_NO_FAIR_VALUE, R_NO_FEE_FN,
                     R_LIMIT_NOT_POSITIVE, R_NOTHING_INSIDE_LIMIT,
                     R_FILL_NOT_IDENTIFIED],
        "riskDeclaration": declaration(),
        "ladderSpaces": bs.PRICE_SPACES,
        "stateGatesAreDerived": list(risk.STATE_GATES),
        "standingBlocker": R_NO_CALIBRATION,
        "declaredSupport": [SUPPORT_MIN, SUPPORT_MAX],
    }
