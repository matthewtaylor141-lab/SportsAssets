#!/usr/bin/env python3
"""EXECUTION TELEMETRY. The fields the first real order must come home with.

WHAT THIS IS FOR, precisely. The whole point of a first micro-live order is to
put one number beside another: what we predicted before sending, and what
actually happened. That comparison is the deliverable -- not the P&L. If the
order fills and we cannot reconstruct why the realized outcome differed from
PREDICTED_EV_AT_SEND, the experiment produced money and no knowledge, which is
the worse outcome of the two.

So every field here exists to support one of four comparisons:
    did it fill, and how fast      (the timing block)
    what did we actually get       (the size and price block)
    what did the market do next    (the markouts)
    what did it cost               (the realized economics)

MARKOUTS ARE THE ADVERSE-SELECTION MEASUREMENT. A maker order that fills
immediately before the price moves against us has not earned the spread; it has
been picked off. The markout horizons are declared BEFORE the trade so the
horizon cannot be chosen afterwards to flatter the result.

EVERY FIELD DEFAULTS TO NOT_IDENTIFIED. A telemetry field we failed to capture
is missing, not zero, and TELEMETRY_COMPLETE is false until every required
field is present -- which, per the kill switch, blocks further execution.

This module contacts nothing and can place no order.
"""
import json

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

TIMING_FIELDS = (
    "ORDER_SENT_TIME", "VENUE_ACK_TIME", "FIRST_FILL_TIME", "LAST_FILL_TIME",
    "CANCEL_REQUEST_TIME", "CANCEL_ACK_TIME",
)
SIZE_FIELDS = (
    "REQUESTED_SIZE", "ACKNOWLEDGED_SIZE", "FILLED_SIZE", "REMAINING_SIZE",
)
PRICE_FIELDS = (
    "LIMIT_PRICE", "AVG_FILL_PRICE", "BEST_BID_AT_SEND", "BEST_ASK_AT_SEND",
    "MID_AT_SEND",
)
BOOK_FIELDS = (
    "QUEUE_POSITION_AT_SEND", "DEPTH_AT_OUR_PRICE_AT_SEND",
    "DEPTH_AHEAD_OF_US_AT_SEND", "BOOK_STATE_SOURCE",
)
MARKOUT_HORIZONS_S = (5, 30, 60, 300)
MARKOUT_FIELDS = tuple("MARKOUT_%dS" % h for h in MARKOUT_HORIZONS_S)
ECONOMICS_FIELDS = (
    "REALIZED_SPREAD", "REALIZED_FEES", "REALIZED_REBATES",
    "REALIZED_ADVERSE_SELECTION", "INVENTORY_DURATION",
    "REALIZED_TRADING_PNL", "REALIZED_INCENTIVE_PNL", "REALIZED_TOTAL_PNL",
)
COMPARISON_FIELDS = ("PREDICTED_EV_AT_SEND", "REALIZED_OUTCOME",
                     "PREDICTION_ERROR")

ALL_FIELDS = (TIMING_FIELDS + SIZE_FIELDS + PRICE_FIELDS + BOOK_FIELDS
              + MARKOUT_FIELDS + ECONOMICS_FIELDS + COMPARISON_FIELDS)

# Without these the experiment cannot answer its own question.
REQUIRED_FOR_A_VALID_OBSERVATION = (
    "ORDER_SENT_TIME", "VENUE_ACK_TIME", "REQUESTED_SIZE",
    "ACKNOWLEDGED_SIZE", "FILLED_SIZE", "LIMIT_PRICE", "BEST_BID_AT_SEND",
    "BEST_ASK_AT_SEND", "MID_AT_SEND", "PREDICTED_EV_AT_SEND",
)

MARKOUT_HORIZONS_DECLARED_BEFORE_THE_TRADE = True
WHY_MARKOUTS = (
    "a maker fill that happens just before the price moves against us did not "
    "earn the spread; it was selected against, and only the markout shows it")
MISSING_IS_NOT_ZERO = (
    "a telemetry field we failed to capture is missing, not zero; a zero here "
    "would be a measurement we never made")


def blank():
    """The empty observation. Every field present, every value absent."""
    row = {f: NOT_IDENTIFIED for f in ALL_FIELDS}
    row.update({
        "MARKOUT_HORIZONS_S": list(MARKOUT_HORIZONS_S),
        "MARKOUT_HORIZONS_DECLARED_BEFORE_THE_TRADE":
            MARKOUT_HORIZONS_DECLARED_BEFORE_THE_TRADE,
        "WHY_MARKOUTS": WHY_MARKOUTS,
        "MISSING_IS_NOT_ZERO": MISSING_IS_NOT_ZERO,
        "REALIZED_MAKER_ECONOMICS": NOT_ESTABLISHED,
        "ACTUAL_BETTOR_FILL": NOT_IDENTIFIED,
    })
    return row


def completeness(row):
    """Is this observation usable? Missing required fields block execution."""
    row = row or {}
    missing = [f for f in REQUIRED_FOR_A_VALID_OBSERVATION
               if row.get(f, NOT_IDENTIFIED) in (NOT_IDENTIFIED, None, "")]
    absent_all = [f for f in ALL_FIELDS
                  if row.get(f, NOT_IDENTIFIED) in (NOT_IDENTIFIED, None, "")]
    return {
        "FIELDS_DEFINED": len(ALL_FIELDS),
        "FIELDS_PRESENT": len(ALL_FIELDS) - len(absent_all),
        "REQUIRED_MISSING": missing,
        "OPTIONAL_MISSING": [f for f in absent_all if f not in missing],
        "TELEMETRY_COMPLETE": not missing,
        "TELEMETRY_FAILURE": bool(missing),
        "BLOCKS_FURTHER_EXECUTION": bool(missing),
        "WHY": ("an incomplete observation cannot support the predicted-vs-"
                "realized comparison the experiment exists to make"),
        "MISSING_IS_NOT_ZERO": MISSING_IS_NOT_ZERO,
    }


def comparison(row):
    """PREDICTED vs REALIZED, or an honest refusal to compare."""
    row = row or {}
    pred = row.get("PREDICTED_EV_AT_SEND", NOT_IDENTIFIED)
    real = row.get("REALIZED_TOTAL_PNL", NOT_IDENTIFIED)
    if NOT_IDENTIFIED in (pred, real) or None in (pred, real):
        return {
            "PREDICTED_EV_AT_SEND": pred,
            "REALIZED_OUTCOME": real,
            "PREDICTION_ERROR": NOT_IDENTIFIED,
            "COMPARISON_STATUS": "NOT_AVAILABLE",
            "WHY": "one side of the comparison was not observed",
            "ONE_OBSERVATION_IS_NOT_A_RESULT": True,
        }
    try:
        err = float(real) - float(pred)
    except Exception:                                         # noqa: BLE001
        return {"COMPARISON_STATUS": "NOT_AVAILABLE",
                "WHY": "a value could not be read as a number"}
    return {
        "PREDICTED_EV_AT_SEND": pred,
        "REALIZED_OUTCOME": real,
        "PREDICTION_ERROR": err,
        "COMPARISON_STATUS": "AVAILABLE",
        "ONE_OBSERVATION_IS_NOT_A_RESULT": True,
        "WHY_NOT_A_RESULT": (
            "a single fill's error is one draw from a distribution nobody has "
            "measured; the phase needs enough independent observations before "
            "this number describes anything"),
        "REALIZED_MAKER_ECONOMICS": NOT_ESTABLISHED,
    }


def schema():
    return {
        "TIMING_FIELDS": list(TIMING_FIELDS),
        "SIZE_FIELDS": list(SIZE_FIELDS),
        "PRICE_FIELDS": list(PRICE_FIELDS),
        "BOOK_FIELDS": list(BOOK_FIELDS),
        "MARKOUT_FIELDS": list(MARKOUT_FIELDS),
        "ECONOMICS_FIELDS": list(ECONOMICS_FIELDS),
        "COMPARISON_FIELDS": list(COMPARISON_FIELDS),
        "REQUIRED_FOR_A_VALID_OBSERVATION":
            list(REQUIRED_FOR_A_VALID_OBSERVATION),
        "TOTAL_FIELDS": len(ALL_FIELDS),
    }


def to_json(r):
    return json.dumps(r, indent=1, sort_keys=True, default=str)
