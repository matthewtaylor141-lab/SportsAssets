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


def estimate(*, ladder, fair_value, fee_fn, observation_age_s=None,
             intended_notional_usd=None) -> dict:
    """Size, marketable execution estimate and price, or a named refusal.

    Returns a dict that is always safe to read: `ok` says whether an
    execution estimate exists, `refusals` names what is missing, and
    `p_fill` is present only when it was measured.
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
        "fee_per_contract_realised": round(
            (sum(abs(float(fee_fn(qty=lv["qty"], price=lv["price"])))
                 for lv in (walk.get("levels_taken") or []))
             / filled) if filled else 0.0, 8),
        "fee_basis": "SUM_OF_PER_LEVEL_FEES_DIVIDED_BY_FILLED_QTY",
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


def _sha(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   default=str).encode()).hexdigest()


LIMITS_SHA = _sha(PREDECLARED_LIMITS)


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
