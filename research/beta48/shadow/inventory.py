#!/usr/bin/env python3
"""INVENTORY-CLOSURE LEARNING. What happens AFTER a hypothetical maker fill.

THE CENTRAL PHASE-2 QUESTION IS NOT THE WHALE QUESTION. The whale archive asks
how often a pair COMPLETES. On PMUS there is one binary book per market, so the
BETTOR question is an INVENTORY question:

    a maker fill leaves us ONE-SIDED. What then?

    TIME_TO_OPPOSITE_FILL     how long until the other side comes to us
    PRICE_OF_OPPOSITE_FILL    at what price
    NET_SPREAD_CAPTURE        what survived fees, and what of that is incentive
    INVENTORY_MARKOUT         where the market went WHILE we waited
    MAX_ADVERSE_EXCURSION     the worst it got
    MAX_FAVORABLE_EXCURSION   the best we did not take
    CAPITAL_OCCUPANCY         dollars x seconds, the cost nobody invoices
    FAILURE_TO_CLOSE          the window ended and we were still holding
    SETTLEMENT_OUTCOME        what the position was finally worth

WHAT THIS MODULE REFUSES TO DO.

An inventory outcome computed on a fill that was never identified is fiction
with a decimal point on it. So `open_inventory` REFUSES anything whose
FILL_STATUS is not a COUNTERFACTUAL_FILL_*, and UNKNOWN is refused exactly as
firmly as NOT_FILLED -- an unresolved fill is not a small fill.

Because the tick capture cannot support a positive fill without an execution
tape (see `maker_fill`), the honest first output of this module on tick-only
data is NO INVENTORY ROWS AT ALL. That is the measurement, not a bug, and it
names precisely what the next capture has to add.

THE TWO CLOSES ARE NOT THE SAME KIND OF OBJECT, and mixing them is the error
this file is shaped to prevent:

  PASSIVE_CLOSE_PRICE      the price we would REST at. Whether anyone comes to
                           it is the same unresolved question as the entry, so
                           it is always carried with its own FILL_STATUS and is
                           NEVER treated as achieved.
  AGGRESSIVE_CLOSE_PRICE   the price available NOW by crossing displayed size.
                           This one IS identified from the book, for as much
                           size as the captured ladder displays -- and
                           NOT_IDENTIFIED beyond it, rather than extrapolated.

So the aggressive close is a MEASURED floor on what the inventory could be
turned back into, and the passive close is a HOPE with a price attached. Both
are reported; only one is identified.

INCENTIVES NEVER RESCUE TRADING ECONOMICS. Every outcome goes through
`position_state.incentive_split`, which reports TRADING_NET_EX_INCENTIVES first
and stamps INCENTIVE_DEPENDENT on any row whose trading economics are negative
and whose total is positive only because of a rebate.

FEE REGIMES ARE NEVER POOLED. The taker curve changes at 2026-09-17 03:59 UTC
(theta 0.06 -> 0.0695). Every row carries the regime its timestamp falls in, and
`summarise_inventory` refuses to report a pooled net across a straddling window.
"""
from __future__ import annotations

import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "forward"))

import maker_fill as MF                                        # noqa: E402
from position_state import (                                   # noqa: E402
    NOT_IDENTIFIED, COUNTERFACTUAL, incentive_split, time_unpaired_bucket,
    INVENTORY_OUTCOME_FIELDS, X_PASSIVE_INVENTORY_CLOSE,
    X_AGGRESSIVE_INVENTORY_CLOSE, X_HOLD_INVENTORY,
)

SHADOW_ONLY = True
ORDER_PATH_EXISTS = False

LONG = "LONG"
SHORT = "SHORT"

# A filled BID leaves us long the contract; a filled ASK leaves us short it.
SIDE_AFTER_FILL = {MF.SIDE_BID: LONG, MF.SIDE_ASK: SHORT}
CLOSING_SIDE = {LONG: MF.SIDE_ASK, SHORT: MF.SIDE_BID}

MARKOUT_HORIZONS_S = (30, 60, 300, 900)

SETTLEMENT_OUTCOME_REACHABLE_IN_A_TICK_WINDOW = False


class FillNotIdentified(RuntimeError):
    """Raised when an inventory outcome is asked for on a fill nobody has."""


class EstimandMismatch(RuntimeError):
    """Raised when the whale close time is compared to ours without saying so."""


def _d(v):
    return MF._d(v)


def _secs(v):
    """Seconds as a Decimal. Time is measured, so it is exact here too."""
    if v is None or v == NOT_IDENTIFIED:
        return NOT_IDENTIFIED
    return D(str(round(float(v), 3)))


# ---------------------------------------------------------------------------
# OPENING -- only from an identified counterfactual fill
# ---------------------------------------------------------------------------

def open_inventory(quote, fill_row, position_id=None):
    """One-sided inventory created by a COUNTERFACTUAL fill. Never by a touch.

    Refuses UNKNOWN as firmly as NOT_FILLED. A position opened on an unresolved
    fill would propagate into every downstream distribution as though it had
    happened, and the distributions are the entire deliverable.
    """
    if not MF.is_a_fill(fill_row):
        raise FillNotIdentified(
            "FILL_STATUS=%r does not open inventory. %s"
            % (fill_row.get("FILL_STATUS"), fill_row.get("WHY")))
    side = SIDE_AFTER_FILL[quote["SIDE"]]
    return {
        "POSITION_ID": position_id or ("SHADOW_%s_%s" % (quote.get("SLUG"),
                                                         quote.get("QUOTE_TIME"))),
        "PROVENANCE": "COUNTERFACTUAL_MAKER_FILL",
        "LABEL": COUNTERFACTUAL,
        "SLUG": quote["SLUG"],
        "INVENTORY_SIDE": side,
        "CONTRACTS": quote["QUOTE_SIZE"],
        "ENTRY_PRICE": quote["QUOTE_PRICE"],
        "ENTRY_TIME": quote["QUOTE_TIME"],
        "ENTRY_ELAPSED_S": quote["QUOTE_ELAPSED_S"],
        "ENTRY_WAS_MAKER": True,
        "ENTRY_FILL_MODEL": fill_row.get("FILL_MODEL"),
        "ENTRY_FILL_STATUS": fill_row.get("FILL_STATUS"),
        "ENTRY_FILL_BASIS": fill_row.get("FILL_STATUS_BASIS"),
        "ORDER_WAS_SUBMITTED": False,
        "ACTUAL_POSITION": NOT_IDENTIFIED,
    }


# ---------------------------------------------------------------------------
# THE TWO CLOSES
# ---------------------------------------------------------------------------

def aggressive_close(tick, inventory):
    """What crossing the displayed book RIGHT NOW would fetch. Measured.

    Walks the captured ladder for our whole size. If the five captured levels
    do not hold it, the price is NOT_IDENTIFIED for the unfilled remainder --
    never extrapolated from the last level, which would price size the venue
    never showed us.
    """
    side = CLOSING_SIDE[inventory["INVENTORY_SIDE"]]
    # Closing a LONG means SELLING into the BIDS; closing a SHORT means BUYING
    # from the OFFERS. The ladder we consume is the opposite of the side we
    # would be quoting on.
    ladder = tick.get("BID_LADDER" if side == MF.SIDE_ASK
                      else "ASK_LADDER") or []
    need = _d(inventory["CONTRACTS"])
    if need == NOT_IDENTIFIED:
        return {"AGGRESSIVE_CLOSE_PRICE": NOT_IDENTIFIED,
                "AGGRESSIVE_CLOSE_FILLABLE": NOT_IDENTIFIED,
                "WHY": "SIZE_NOT_IDENTIFIED"}
    got = D("0")
    notional = D("0")
    for px, qty in ladder:
        p, q = _d(px), _d(qty)
        if p == NOT_IDENTIFIED or q == NOT_IDENTIFIED:
            break
        take = min(q, need - got)
        if take <= 0:
            break
        got += take
        notional += take * p
    complete = got >= need and need > 0
    return {
        "AGGRESSIVE_CLOSE_PRICE": (notional / got) if complete and got > 0
                                  else NOT_IDENTIFIED,
        "AGGRESSIVE_CLOSE_FILLABLE": got,
        "AGGRESSIVE_CLOSE_SIZE_COMPLETE": complete,
        "LADDER_LEVELS_CONSUMED": len(ladder),
        "DEPTH_BEYOND_CAPTURED_LADDER": NOT_IDENTIFIED,
        "WHY": ("DISPLAYED_DEPTH_COVERS_OUR_SIZE" if complete
                else "DISPLAYED_DEPTH_DOES_NOT_COVER_OUR_SIZE"),
        "PRICE_IS_MEASURED_NOT_HOPED": complete,
    }


def passive_close(inventory, ticks, at_tick, model="F1", horizon_s=None):
    """The resting close: a price, and the SAME unresolved fill question.

    Returns the quote, its walk and its fill status TOGETHER, so no caller can
    pick up the price without the verdict that says whether anybody came to it.
    """
    side = CLOSING_SIDE[inventory["INVENTORY_SIDE"]]
    q = MF.hypothetical_quote(at_tick, side, inventory["CONTRACTS"])
    w = MF.walk_after_entry(q, ticks, horizon_s=horizon_s)
    f = MF.fill_status(q, w, model=model)
    filled = MF.is_a_fill(f)
    return {
        "PMUS_ACTION": X_PASSIVE_INVENTORY_CLOSE,
        "PASSIVE_CLOSE_PRICE": q["QUOTE_PRICE"],
        "PASSIVE_CLOSE_FILL_STATUS": f["FILL_STATUS"],
        "PASSIVE_CLOSE_WHY": f["WHY"],
        "PASSIVE_CLOSE_ACHIEVED": filled,
        "PRICE_IS_MEASURED_NOT_HOPED": filled,
        "TIME_TO_OPPOSITE_FILL": (_secs(w.get("WINDOW_S")) if filled
                                  else NOT_IDENTIFIED),
        "PRICE_OF_OPPOSITE_FILL": (q["QUOTE_PRICE"] if filled
                                   else NOT_IDENTIFIED),
        "QUOTE": q, "WALK": w, "FILL": f,
    }


# ---------------------------------------------------------------------------
# WHAT THE ROUND TRIP WAS WORTH
# ---------------------------------------------------------------------------

def _regime(ts):
    import fees_v2 as FEES
    try:
        return FEES.regime_at(ts)
    except Exception:                                          # noqa: BLE001
        return NOT_IDENTIFIED


def round_trip_economics(inventory, close_price, close_is_maker, close_time,
                         incentives=None):
    """GROSS_SPREAD, FEES, REBATES, then TRADING_NET before TOTAL_NET.

    The order is the discipline: trading economics are computed and reported
    without incentives first, and the incentive terms are added afterwards by
    `incentive_split`, which stamps INCENTIVE_DEPENDENT on any row that only
    clears zero because of a rebate.
    """
    import fees_v2 as FEES

    contracts = _d(inventory["CONTRACTS"])
    entry = _d(inventory["ENTRY_PRICE"])
    exit_px = _d(close_price)
    long_ = inventory["INVENTORY_SIDE"] == LONG

    if NOT_IDENTIFIED in (contracts, entry, exit_px):
        gross = NOT_IDENTIFIED
    else:
        per = (exit_px - entry) if long_ else (entry - exit_px)
        gross = per * contracts

    entry_regime = _regime(inventory.get("ENTRY_TIME"))
    exit_regime = _regime(close_time)

    # The opening leg is a maker fill by construction: a rebate, received.
    if NOT_IDENTIFIED in (contracts, entry):
        entry_rebate = NOT_IDENTIFIED
    else:
        entry_rebate = FEES.maker_rebate_at(contracts, entry)

    exit_fee = exit_rebate = NOT_IDENTIFIED
    if NOT_IDENTIFIED not in (contracts, exit_px):
        if close_is_maker:
            exit_rebate = FEES.maker_rebate_at(contracts, exit_px)
        elif exit_regime != NOT_IDENTIFIED:
            exit_fee = -FEES.taker_fee_at(contracts, exit_px, exit_regime)

    trading = {"GROSS_SPREAD": gross,
               "EXIT_TAKER_FEE": (exit_fee if not close_is_maker
                                  else D("0"))}
    incentive = {
        # A maker rebate is an INCENTIVE term, not a trading term. Putting it
        # here is what makes "the spread was negative but the rebate saved it"
        # visible instead of netted away.
        "MAKER_REBATE": (entry_rebate if entry_rebate == NOT_IDENTIFIED
                         or exit_rebate == NOT_IDENTIFIED
                         else entry_rebate + exit_rebate)
        if close_is_maker else entry_rebate,
        "LIQUIDITY_INCENTIVE": (incentives or {}).get("LIQUIDITY_INCENTIVE",
                                                      NOT_IDENTIFIED),
        "OTHER_INCENTIVE": (incentives or {}).get("OTHER_INCENTIVE",
                                                  NOT_IDENTIFIED),
    }
    split = incentive_split(trading, incentive)
    split.update({
        "GROSS_SPREAD": gross,
        "NET_SPREAD_CAPTURE": split["TRADING_NET_EX_INCENTIVES"],
        "CLOSE_WAS_MAKER": bool(close_is_maker),
        "PMUS_ACTION": (X_PASSIVE_INVENTORY_CLOSE if close_is_maker
                        else X_AGGRESSIVE_INVENTORY_CLOSE),
        "ENTRY_FEE_REGIME": entry_regime,
        "EXIT_FEE_REGIME": exit_regime,
        "FEE_REGIME_STRADDLED": (entry_regime != exit_regime
                                 and NOT_IDENTIFIED not in (entry_regime,
                                                            exit_regime)),
        "REALIZED": False,
        "LABEL": COUNTERFACTUAL,
    })
    return split


def markouts(inventory, ticks, horizons_s=MARKOUT_HORIZONS_S):
    """Where the market went WHILE we held, signed for the side we hold.

    A markout is not a P&L. It is the drift of the mid against the price we
    were filled at, which is what says whether a maker fill was selected
    against -- the question a spread figure cannot answer.
    """
    t0 = inventory.get("ENTRY_ELAPSED_S")
    entry = _d(inventory["ENTRY_PRICE"])
    long_ = inventory["INVENTORY_SIDE"] == LONG
    rows = sorted([r for r in ticks
                   if r.get("slug") == inventory["SLUG"]
                   and r.get("ELAPSED_S") is not None and t0 is not None
                   and r["ELAPSED_S"] > t0],
                  key=lambda r: r["ELAPSED_S"])
    out = {"INVENTORY_MARKOUT": {}}
    for h in horizons_s:
        pick = None
        for r in rows:
            if r["ELAPSED_S"] - t0 <= h:
                pick = r
            else:
                break
        m = MF._mid(pick) if pick else NOT_IDENTIFIED
        if m == NOT_IDENTIFIED or entry == NOT_IDENTIFIED:
            v = NOT_IDENTIFIED
        else:
            v = (m - entry) if long_ else (entry - m)
        out["INVENTORY_MARKOUT_%dS" % h] = v
        # The named field is the WHOLE horizon set, never one horizon promoted
        # to be "the" markout -- which horizon flatters a maker fill is exactly
        # the choice that must not be made after seeing the numbers.
        out["INVENTORY_MARKOUT"]["%dS" % h] = v
    out["MARKOUT_IS_NOT_PNL"] = True
    out["MARKOUT_HORIZON_SET_FROZEN"] = list(horizons_s)
    return out


def capital_occupancy(inventory, close_elapsed_s):
    """Dollars x seconds. The cost of a slow close that no invoice shows.

    Reported in dollar-seconds AND split into its two factors, because a large
    figure from a big position held briefly and a small one held for an hour
    are different problems.
    """
    contracts = _d(inventory["CONTRACTS"])
    entry = _d(inventory["ENTRY_PRICE"])
    t0 = inventory.get("ENTRY_ELAPSED_S")
    held = (_secs(close_elapsed_s - t0)
            if close_elapsed_s is not None and t0 is not None
            else NOT_IDENTIFIED)
    if NOT_IDENTIFIED in (contracts, entry, held):
        return {"CAPITAL_OCCUPANCY": NOT_IDENTIFIED,
                "CAPITAL_AT_RISK_USD": NOT_IDENTIFIED,
                "SECONDS_HELD": held}
    dollars = contracts * entry
    return {
        "CAPITAL_OCCUPANCY": dollars * held,
        "CAPITAL_OCCUPANCY_UNIT": "USD_SECONDS",
        "CAPITAL_AT_RISK_USD": dollars,
        "SECONDS_HELD": held,
        "TIME_UNPAIRED_BUCKET": time_unpaired_bucket(float(held)),
    }


# ---------------------------------------------------------------------------
# THE WHOLE INVENTORY OUTCOME
# ---------------------------------------------------------------------------

def inventory_outcome(inventory, ticks, model="F1", horizon_s=None,
                      incentives=None):
    """Every field in INVENTORY_OUTCOME_FIELDS, or NOT_IDENTIFIED with a reason.

    The passive close is attempted first because that is the policy under test
    -- maker in, maker out. The aggressive close is computed ALONGSIDE it, not
    as a fallback: it is the measured floor the passive hope is judged against.
    """
    t0 = inventory.get("ENTRY_ELAPSED_S")
    rows = sorted([r for r in ticks
                   if r.get("slug") == inventory["SLUG"]
                   and r.get("kind") != "TICK_ERROR"
                   and r.get("ELAPSED_S") is not None and t0 is not None
                   and r["ELAPSED_S"] > t0
                   and (horizon_s is None or r["ELAPSED_S"] - t0 <= horizon_s)],
                  key=lambda r: r["ELAPSED_S"])
    if not rows:
        return dict(
            {f: NOT_IDENTIFIED for f in INVENTORY_OUTCOME_FIELDS},
            POSITION_ID=inventory["POSITION_ID"], SLUG=inventory["SLUG"],
            WHY="NO_TICKS_AFTER_ENTRY", LABEL=COUNTERFACTUAL)

    first, last = rows[0], rows[-1]
    pas = passive_close(inventory, rows, first, model=model,
                        horizon_s=horizon_s)
    agg_open = aggressive_close(first, inventory)
    agg_last = aggressive_close(last, inventory)

    closed = pas["PASSIVE_CLOSE_ACHIEVED"]
    close_px = pas["PRICE_OF_OPPOSITE_FILL"] if closed else NOT_IDENTIFIED
    close_elapsed = last["ELAPSED_S"] if closed else None

    econ = round_trip_economics(inventory, close_px, True,
                                last.get("RECEIPT_UTC"), incentives)
    # What the same inventory would have fetched by crossing at the END of the
    # window. Identified, and therefore the honest floor.
    econ_agg = round_trip_economics(inventory,
                                    agg_last["AGGRESSIVE_CLOSE_PRICE"], False,
                                    last.get("RECEIPT_UTC"), incentives)

    walk = pas["WALK"]
    occ = capital_occupancy(inventory, close_elapsed
                            if closed else last["ELAPSED_S"])
    out = {
        "POSITION_ID": inventory["POSITION_ID"],
        "SLUG": inventory["SLUG"],
        "INVENTORY_SIDE": inventory["INVENTORY_SIDE"],
        "CONTRACTS": inventory["CONTRACTS"],
        "ENTRY_PRICE": inventory["ENTRY_PRICE"],
        "LABEL": COUNTERFACTUAL,

        "TIME_TO_OPPOSITE_FILL": pas["TIME_TO_OPPOSITE_FILL"],
        "PRICE_OF_OPPOSITE_FILL": pas["PRICE_OF_OPPOSITE_FILL"],
        "PASSIVE_CLOSE_PRICE": pas["PASSIVE_CLOSE_PRICE"],
        "PASSIVE_CLOSE_FILL_STATUS": pas["PASSIVE_CLOSE_FILL_STATUS"],
        "AGGRESSIVE_CLOSE_PRICE_AT_ENTRY": agg_open["AGGRESSIVE_CLOSE_PRICE"],
        "AGGRESSIVE_CLOSE_PRICE_AT_WINDOW_END":
            agg_last["AGGRESSIVE_CLOSE_PRICE"],

        "NET_SPREAD_CAPTURE": (econ["NET_SPREAD_CAPTURE"] if closed
                               else NOT_IDENTIFIED),
        "TRADING_NET_EX_INCENTIVES": (econ["TRADING_NET_EX_INCENTIVES"]
                                      if closed else NOT_IDENTIFIED),
        "TOTAL_NET": econ["TOTAL_NET"] if closed else NOT_IDENTIFIED,
        "INCENTIVE_DEPENDENT": (econ["INCENTIVE_DEPENDENT"] if closed
                                else NOT_IDENTIFIED),
        "AGGRESSIVE_CLOSE_TRADING_NET":
            econ_agg["TRADING_NET_EX_INCENTIVES"],
        "AGGRESSIVE_CLOSE_IS_THE_MEASURED_FLOOR": True,

        "MAX_ADVERSE_EXCURSION": walk.get("MAX_ADVERSE_EXCURSION"),
        "MAX_FAVORABLE_EXCURSION": walk.get("MAX_FAVORABLE_EXCURSION"),

        "FAILURE_TO_CLOSE": (not closed),
        "FAILURE_TO_CLOSE_REASON": (NOT_IDENTIFIED if closed
                                    else pas["PASSIVE_CLOSE_WHY"]),
        "STILL_HOLDING_AT_WINDOW_END": (not closed),
        "PMUS_ACTION_AT_WINDOW_END": (X_PASSIVE_INVENTORY_CLOSE if closed
                                      else X_HOLD_INVENTORY),

        # A tick window is minutes long; settlement is days away. This is a
        # structural absence, not a gap to be filled by a guess about who won.
        "SETTLEMENT_OUTCOME": NOT_IDENTIFIED,
        "SETTLEMENT_REACHABLE_IN_WINDOW":
            SETTLEMENT_OUTCOME_REACHABLE_IN_A_TICK_WINDOW,
        "TICKS_AFTER_ENTRY": len(rows),
    }
    out.update(occ)
    out.update(markouts(inventory, rows))
    return out


# ---------------------------------------------------------------------------
# WHALE PRIOR vs BETTOR OBSERVATION -- the comparison, with its caveat attached
# ---------------------------------------------------------------------------

WHALE_CLOSE_ESTIMAND = "TIME_TO_PAIR_COMPLETION_BY_ANY_MEANS_TWO_TOKEN_VENUE"
BETTOR_CLOSE_ESTIMAND = "TIME_TO_PASSIVE_INVENTORY_CLOSE_ONE_BINARY_BOOK"
ESTIMANDS_ARE_THE_SAME = False

MILESTONE_KEYS = ("TIME_TO_25_PERCENT_EVENTUAL_S",
                  "TIME_TO_50_PERCENT_EVENTUAL_S",
                  "TIME_TO_75_PERCENT_EVENTUAL_S")


def whale_prior_expected_close(priors, which="MILESTONES_3"):
    """The whale prior's completion milestones, read -- never re-derived."""
    sens = (priors or {}).get("SWISSTONY_SENSITIVITY") or {}
    m = sens.get(which)
    if not isinstance(m, dict):
        return {"WHALE_PRIOR_EXPECTED_CLOSE_TIME": NOT_IDENTIFIED,
                "WHY": "MILESTONES_NOT_PRESENT", "PRIOR_SET": which}
    out = {"PRIOR_SET": which,
           "EVENTUAL_COMPLETION_RATE": m.get("EVENTUAL_COMPLETION_RATE",
                                             NOT_IDENTIFIED)}
    for k in MILESTONE_KEYS:
        v = m.get(k, NOT_IDENTIFIED)
        out[k] = v if isinstance(v, (int, float)) else NOT_IDENTIFIED
        if not isinstance(v, (int, float)):
            out[k + "_RAW"] = v
    out["WHALE_PRIOR_EXPECTED_CLOSE_TIME"] = out[
        "TIME_TO_50_PERCENT_EVENTUAL_S"]
    out["ESTIMAND"] = WHALE_CLOSE_ESTIMAND
    return out


def compare_close_times(priors, observed_close_times_s, which="MILESTONES_3",
                        estimand_difference_acknowledged=False):
    """WHALE_PRIOR_EXPECTED_CLOSE_TIME beside BETTOR_OBSERVED_..., not equated.

    THE TWO NUMBERS ARE NOT THE SAME QUANTITY. The whale figure is time to pair
    completion BY ANY MEANS on a two-token venue; ours is time to a PASSIVE
    inventory close on one binary book. A difference between them is therefore
    not, on its own, evidence that BETTOR is slower or faster -- it may be
    entirely the difference in what is being timed.

    The caller must say it knows that. Silently comparing them is exactly how a
    prior gets quoted as a benchmark it was never measured against.
    """
    if not estimand_difference_acknowledged:
        raise EstimandMismatch(
            "whale completion time and BETTOR passive-close time are different "
            "estimands (%s vs %s); pass "
            "estimand_difference_acknowledged=True to compare them"
            % (WHALE_CLOSE_ESTIMAND, BETTOR_CLOSE_ESTIMAND))
    prior = whale_prior_expected_close(priors, which)
    obs = [float(t) for t in observed_close_times_s
           if t not in (None, NOT_IDENTIFIED)]
    obs.sort()
    n = len(obs)
    med = obs[n // 2] if n else NOT_IDENTIFIED
    return {
        "WHALE_PRIOR_EXPECTED_CLOSE_TIME":
            prior["WHALE_PRIOR_EXPECTED_CLOSE_TIME"],
        "WHALE_ESTIMAND": WHALE_CLOSE_ESTIMAND,
        "BETTOR_OBSERVED_COUNTERFACTUAL_CLOSE_TIME": med,
        "BETTOR_ESTIMAND": BETTOR_CLOSE_ESTIMAND,
        "BETTOR_N_CLOSES": n,
        "ESTIMANDS_ARE_THE_SAME": ESTIMANDS_ARE_THE_SAME,
        "DIFFERENCE_IS_EVIDENCE_ABOUT_BETTOR_SPEED": NOT_IDENTIFIED,
        "PRIOR_SET": which,
        "WHY": ("a gap between these two figures mixes a venue-structure "
                "difference with a speed difference, and this dataset does not "
                "separate them"),
    }


# ---------------------------------------------------------------------------
# THE PHASE-2A REPORT, with the forbidden fields absent rather than zeroed
# ---------------------------------------------------------------------------

REPORTABLE_INVENTORY_FIELDS = (
    "INVENTORY_POSITIONS_OPENED", "CLOSED_PASSIVELY", "STILL_HOLDING",
    "AGGRESSIVE_CLOSE_IDENTIFIED", "MEDIAN_SECONDS_HELD",
    "FEE_REGIME_STRADDLED",
)

FORBIDDEN_INVENTORY_FIELDS = ("PROFITABILITY", "WIN_RATE",
                              "EXPECTED_MONTHLY_RETURN", "SHARPE",
                              "EXPECTED_SPREAD_CAPTURE_PER_DAY")


def summarise_inventory(outcomes):
    """Counts and durations. No net is pooled across a fee-regime straddle."""
    n = len(outcomes)
    closed = [o for o in outcomes if not o.get("FAILURE_TO_CLOSE", True)]
    held = sorted(float(o["SECONDS_HELD"]) for o in outcomes
                  if o.get("SECONDS_HELD") not in (None, NOT_IDENTIFIED))
    straddle = any(o.get("FEE_REGIME_STRADDLED") for o in outcomes)
    return {
        "INVENTORY_POSITIONS_OPENED": n,
        "CLOSED_PASSIVELY": len(closed),
        "STILL_HOLDING": n - len(closed),
        "AGGRESSIVE_CLOSE_IDENTIFIED": sum(
            1 for o in outcomes
            if o.get("AGGRESSIVE_CLOSE_PRICE_AT_WINDOW_END")
            not in (None, NOT_IDENTIFIED)),
        "MEDIAN_SECONDS_HELD": (held[len(held) // 2] if held
                                else NOT_IDENTIFIED),
        "FEE_REGIME_STRADDLED": straddle,
        "POOLED_NET": (NOT_IDENTIFIED if straddle or n != len(closed)
                       else "COMPUTED_ONLY_WHEN_EVERY_POSITION_CLOSED"),
        "PROFITABILITY": NOT_IDENTIFIED,
        "WIN_RATE": NOT_IDENTIFIED,
        "EXPECTED_MONTHLY_RETURN": NOT_IDENTIFIED,
        "WHY_NOT_REPORTED": ("the sample and the counterfactual execution "
                             "methodology do not support them"),
    }
