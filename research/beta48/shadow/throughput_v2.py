#!/usr/bin/env python3
"""THROUGHPUT_V2 -- VOLUME CAPACITY FROM ARRIVAL RATES, NOT A MARKET CENSUS.

WHAT V1 GOT WRONG, STATED PLAINLY AND KEPT IN THE CODE. V1 multiplied 2,204
addressable markets by a $25 clip and called $55,100 an arithmetic daily
ceiling. That figure assumed exactly one fill per market per day. NO SUCH RULE
EXISTS in this system, and none is proposed. A market whose book moves twenty
times can be quoted twenty times; a quote that fills can be requoted. The
figure is therefore reclassified here as
SINGLE_PASS_ONE_FILL_PER_MARKET_NOTIONAL -- a single sweep of the board at one
clip -- and it is NOT a maximum of anything.

WHAT REPLACES IT. Volume is a rate problem, and the rate has five terms that
must each be measured or declared absent:

    OBSERVABLE OPPORTUNITY ARRIVAL   measured, from sealed tick evidence
  x PERCENT PASSING EV               NOT_IDENTIFIED -- no fair value exists
  x PERCENT PASSING RISK             NOT_IDENTIFIED -- every limit is NOT_SET
  x P_FILL                           NOT_IDENTIFIED -- no BETTOR fill evidence
  x AVERAGE FILLED SIZE              a policy choice, not a measurement
  = EXPECTED EXECUTED NOTIONAL

combined with CAPITAL OCCUPANCY TIME to give capital velocity. Three of the
five are unknown. They stay separately visible and are NEVER collapsed into
zero or into each other -- a product with an unknown factor is unknown, and
saying so is the only correct answer available today.

THE ANTI-CHURN SIDE, WHICH IS THE OTHER HALF OF THE MANDATE. High turnover is
wanted only where it is economically justified. Four counters exist to catch
volume that is not: a requote with no material state change, a duplicate
economic intent, a round trip with no positive expected EV, and an order that
exists only because we polled. All four must read zero. They are diagnostics on
the architecture, not on a live system -- nothing here has ever placed an order.

This module contacts nothing and can place no order.
"""
import json
from collections import defaultdict
from decimal import Decimal as D, InvalidOperation

import opportunity_arrival as OA

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"
SCENARIO_LABEL = "SCENARIO - NOT PREDICTED PERFORMANCE"

THIS_IS = "VOLUME_CAPACITY_DIAGNOSTIC"
THIS_IS_NOT = ("A_FORECAST", "A_TARGET", "A_PROFITABILITY_CLAIM",
               "AN_EV_ADMISSION_STANDARD")


def _d(x):
    if x in (None, "", NOT_IDENTIFIED, NOT_ESTABLISHED):
        return None
    try:
        return D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# SECTION 1: THE RECLASSIFIED FIGURE
# ---------------------------------------------------------------------------

SINGLE_PASS_MEANING = (
    "addressable markets x one clip = the notional of ONE sweep of the board "
    "at one order per market; it is a unit of measure, not a limit")
NOT_A_CEILING = (
    "calling this a maximum daily gross notional would assume a "
    "one-fill-per-market-per-day rule; no such rule exists in this system and "
    "none is proposed, so the figure bounds nothing")
WHAT_WOULD_MAKE_IT_A_CEILING = (
    "an explicit risk rule capping fills per market per day; if such a rule is "
    "ever authorised, this figure becomes a real ceiling and should be "
    "renamed then, not before")


def single_pass_notional(addressable_markets, clip_usd):
    """The V1 figure, correctly labelled."""
    m, c = _d(addressable_markets), _d(clip_usd)
    v = (m * c) if (m is not None and c is not None) else None
    return {
        "SINGLE_PASS_ONE_FILL_PER_MARKET_NOTIONAL": (str(v) if v is not None
                                                     else NOT_IDENTIFIED),
        "ADDRESSABLE_MARKETS": addressable_markets,
        "CLIP_USD": clip_usd,
        "MAX_DAILY_GROSS_NOTIONAL": NOT_IDENTIFIED,
        "IS_A_CEILING": False,
        "MEANING": SINGLE_PASS_MEANING,
        "NOT_A_CEILING": NOT_A_CEILING,
        "WHAT_WOULD_MAKE_IT_A_CEILING": WHAT_WOULD_MAKE_IT_A_CEILING,
        "ONE_FILL_PER_MARKET_RULE_EXISTS": False,
    }


# ---------------------------------------------------------------------------
# SECTION 4: MANY ORDER CYCLES PER MARKET
#
# The four quantities below are different sizes of the same day and must never
# be substituted for one another. V1's error was using the first as though it
# were the third.
# ---------------------------------------------------------------------------

CYCLE_PATHS = (
    ("QUOTE", "CANCEL_OR_EXPIRE", "REQUOTE"),
    ("QUOTE", "FILL", "INVENTORY_ACTION", "REQUOTE"),
    ("QUOTE", "PARTIAL_FILL", "REST_OR_CANCEL_OR_REQUOTE"),
)
COUNTING_LEVELS = ("UNIQUE_MARKETS_TRADED", "ORDER_INTENTS", "FILLED_ORDERS",
                   "FILLED_NOTIONAL")
NO_ONE_PER_MARKET_CAP = (
    "filled orders are never capped at one per market; a cap would be a risk "
    "rule and no such rule is authorised")
FILLS_PER_MARKET_CAP = NOT_IDENTIFIED          # no rule exists to read


def order_cycles(markets_touched=0, order_intents=0, filled_orders=0,
                 filled_notional=None, requotes=0, cancels=0, partials=0):
    """Keep the four counting levels apart, and show the multipliers between."""
    def ratio(a, b):
        a, b = _d(a), _d(b)
        return str(a / b) if (a is not None and b not in (None, 0)
                              and b != D(0)) else NOT_IDENTIFIED
    return {
        "UNIQUE_MARKETS_TRADED": markets_touched,
        "ORDER_INTENTS": order_intents,
        "FILLED_ORDERS": filled_orders,
        "FILLED_NOTIONAL": (filled_notional if filled_notional is not None
                            else NOT_IDENTIFIED),
        "REQUOTES": requotes,
        "CANCELS_OR_EXPIRIES": cancels,
        "PARTIAL_FILLS": partials,

        "ORDER_INTENTS_PER_MARKET": ratio(order_intents, markets_touched),
        "FILLED_ORDERS_PER_MARKET": ratio(filled_orders, markets_touched),
        "FILL_RATE_OF_INTENTS": ratio(filled_orders, order_intents),

        "CYCLE_PATHS": [list(p) for p in CYCLE_PATHS],
        "COUNTING_LEVELS": list(COUNTING_LEVELS),
        "FILLS_PER_MARKET_CAP": FILLS_PER_MARKET_CAP,
        "NO_ONE_PER_MARKET_CAP": NO_ONE_PER_MARKET_CAP,
        "A_MARKET_IS_NOT_AN_ORDER": (
            "one market can produce many order intents over a day; counting "
            "markets as orders is the V1 defect this section exists to stop"),
        "AN_INTENT_IS_NOT_A_FILL": (
            "an order intent is a decision to try; a fill is somebody trading "
            "against us, and only the venue can report one"),
    }


# ---------------------------------------------------------------------------
# SECTION 5: ANTI-CHURN
#
# Every counter here must be zero. They are not thresholds to stay under --
# each one names a way of producing order count without producing economics,
# and the intended architecture makes each impossible rather than rare.
# ---------------------------------------------------------------------------

CHURN_COUNTERS = (
    "REQUOTE_WITHOUT_MATERIAL_STATE_CHANGE",
    "DUPLICATE_ECONOMIC_INTENT",
    "ROUND_TRIP_WITHOUT_POSITIVE_EXPECTED_EV",
    "ORDERS_CREATED_ONLY_BY_POLL_FREQUENCY",
)
CHURN_MUST_BE_ZERO = (
    "each counter names a way to manufacture order count without economics; "
    "the intended architecture makes each impossible, so a non-zero reading "
    "is a defect and not a tuning signal")


def _intent_key(o):
    """The economic identity of an order intent: same market, side, price, size.

    Two intents with the same key are the same economic act. Sequence numbers,
    timestamps and retry counters are deliberately excluded -- they would make
    every duplicate look distinct.
    """
    return (o.get("MARKET_SLUG"), o.get("SIDE"), str(o.get("LIMIT_PRICE")),
            str(o.get("SIZE")))


def anti_churn(intents=None, transitions_by_intent=None, ev_by_intent=None):
    """Audit a stream of order intents for volume that is not economics.

    `intents` are intent records. `transitions_by_intent` maps an intent id to
    the opportunity transition that justified it -- an intent with no material
    transition behind it is churn. `ev_by_intent` maps an intent id to the
    frozen EV verdict; absent or non-positive EV on a completed round trip is
    churn.
    """
    intents = list(intents or ())
    transitions_by_intent = transitions_by_intent or {}
    ev_by_intent = ev_by_intent or {}

    seen, dup = set(), 0
    no_change = 0
    poll_only = 0
    bad_round_trip = 0

    for o in intents:
        oid = o.get("INTENT_ID")
        k = _intent_key(o)
        if k in seen and not o.get("SUPERSEDES"):
            dup += 1
        seen.add(k)

        t = transitions_by_intent.get(oid)
        if o.get("IS_REQUOTE"):
            if t is None or not (t.get("TIER_1") or t.get("TIER_2")
                                 or t.get("TIER_3")):
                no_change += 1
            if t is not None and t.get("IDENTICAL_DECISION_STATE"):
                poll_only += 1

        if o.get("ROUND_TRIP_CLOSED"):
            ev = ev_by_intent.get(oid)
            if ev is not True and ev != "POSITIVE_EV":
                bad_round_trip += 1

    counts = {
        "REQUOTE_WITHOUT_MATERIAL_STATE_CHANGE": no_change,
        "DUPLICATE_ECONOMIC_INTENT": dup,
        "ROUND_TRIP_WITHOUT_POSITIVE_EXPECTED_EV": bad_round_trip,
        "ORDERS_CREATED_ONLY_BY_POLL_FREQUENCY": poll_only,
    }
    clean = all(v == 0 for v in counts.values())
    return {
        "INTENTS_AUDITED": len(intents),
        "COUNTERS": counts,
        "ALL_ZERO": clean,
        "ANTI_CHURN_STATUS": ("CLEAN" if clean else "CHURN_DETECTED"),
        "CHURN_COUNTERS": list(CHURN_COUNTERS),
        "CHURN_MUST_BE_ZERO": CHURN_MUST_BE_ZERO,
        "SCOPE": ("audits intent records; no live engine has produced any, so "
                  "a clean reading here is a property of the design and not "
                  "evidence about production behaviour"),
        "WHY_A_REQUOTE_NEEDS_A_TRANSITION": (
            "re-pricing a resting order moves no contracts; if the book did "
            "not change, the requote is our own scheduler talking"),
    }


# ---------------------------------------------------------------------------
# SECTION 6: EVENT-LEVEL CORRELATION
#
# The totals work made this unavoidable. 647 totals raised addressable markets
# by 41.6% and raised canonical events by zero: they landed on 41 contests,
# about sixteen alternate lines each. Sixteen lines on one football match are
# sixteen EXECUTION opportunities and ONE correlated risk unit. A throughput
# model that cannot tell those apart will report a diversified book that is a
# concentrated bet.
# ---------------------------------------------------------------------------

EXECUTION_UNITS_ARE_NOT_RISK_UNITS = (
    "sixteen alternate totals on one game are sixteen chances to execute and "
    "one thing to be wrong about; only the first number scales throughput, and "
    "only the second governs exposure")
CORRELATION_COEFFICIENT_STATUS = NOT_IDENTIFIED
WHY_CORRELATION_IS_NOT_MEASURED = (
    "a correlation coefficient between two markets on one contest needs joint "
    "outcome history; this programme holds none, so exposure is treated as "
    "FULLY CORRELATED within an event -- the conservative direction")


def event_concentration(positions, event_of=None, family_of=None):
    """Per-event concentration of markets, quotes, notional and exposure.

    `positions` are per-market rows carrying EVENT_ID (or resolvable via
    `event_of`), ACTIVE_QUOTES, GROSS_NOTIONAL, SIGNED_EXPOSURE and
    CAPITAL_OCCUPIED. Correlated exposure is the SUM OF ABSOLUTE exposures
    within the event -- the fully-correlated assumption -- because no
    correlation estimate exists and under-stating concentration is the
    dangerous direction.
    """
    by = defaultdict(lambda: {
        "MARKETS_IN_EVENT": 0, "ACTIVE_QUOTES_IN_EVENT": 0,
        "GROSS_NOTIONAL_IN_EVENT": D(0), "NET_DIRECTIONAL_EXPOSURE_IN_EVENT": D(0),
        "CORRELATED_EXPOSURE_IN_EVENT": D(0), "CAPITAL_OCCUPIED_BY_EVENT": D(0),
        "FAMILIES": set(), "MARKETS": [],
    })
    for p in positions or ():
        if not isinstance(p, dict):
            continue
        slug = p.get("MARKET_SLUG")
        eid = p.get("EVENT_ID") or (event_of(slug) if event_of else None) \
            or NOT_IDENTIFIED
        b = by[eid]
        b["MARKETS_IN_EVENT"] += 1
        b["ACTIVE_QUOTES_IN_EVENT"] += int(p.get("ACTIVE_QUOTES") or 0)
        for key, src in (("GROSS_NOTIONAL_IN_EVENT", "GROSS_NOTIONAL"),
                         ("CAPITAL_OCCUPIED_BY_EVENT", "CAPITAL_OCCUPIED")):
            v = _d(p.get(src))
            if v is not None:
                b[key] += v
        e = _d(p.get("SIGNED_EXPOSURE"))
        if e is not None:
            b["NET_DIRECTIONAL_EXPOSURE_IN_EVENT"] += e
            b["CORRELATED_EXPOSURE_IN_EVENT"] += abs(e)
        fam = p.get("MARKET_FAMILY") or (family_of(slug) if family_of else None)
        if fam:
            b["FAMILIES"].add(fam)
        b["MARKETS"].append(slug)

    events = {}
    for eid, b in by.items():
        b = dict(b)
        b["FAMILIES"] = sorted(b["FAMILIES"])
        for k in ("GROSS_NOTIONAL_IN_EVENT", "NET_DIRECTIONAL_EXPOSURE_IN_EVENT",
                  "CORRELATED_EXPOSURE_IN_EVENT", "CAPITAL_OCCUPIED_BY_EVENT"):
            b[k] = str(b[k])
        events[eid] = b

    n_mkts = sum(v["MARKETS_IN_EVENT"] for v in by.values())
    n_events = len(by)
    worst = max(by.items(), key=lambda kv: kv[1]["MARKETS_IN_EVENT"],
                default=(None, {"MARKETS_IN_EVENT": 0}))
    return {
        "EVENTS": events,
        "EVENT_COUNT": n_events,
        "MARKET_COUNT": n_mkts,
        "MARKETS_PER_EVENT_MEAN": (n_mkts / float(n_events)) if n_events
                                  else NOT_IDENTIFIED,
        "MOST_CONCENTRATED_EVENT": worst[0],
        "MOST_CONCENTRATED_EVENT_MARKETS": worst[1]["MARKETS_IN_EVENT"],
        "EXECUTION_UNITS": n_mkts,
        "RISK_UNITS": n_events,
        "EXECUTION_TO_RISK_UNIT_RATIO": ((n_mkts / float(n_events))
                                         if n_events else NOT_IDENTIFIED),
        "EXECUTION_UNITS_ARE_NOT_RISK_UNITS": EXECUTION_UNITS_ARE_NOT_RISK_UNITS,
        "CORRELATION_COEFFICIENT_STATUS": CORRELATION_COEFFICIENT_STATUS,
        "WHY_CORRELATION_IS_NOT_MEASURED": WHY_CORRELATION_IS_NOT_MEASURED,
        "CORRELATED_EXPOSURE_BASIS": "FULLY_CORRELATED_WITHIN_EVENT",
        "WHY_THAT_BASIS": ("it over-states concentration rather than "
                           "under-stating it, which is the safe direction "
                           "while no estimate exists"),
    }


# ---------------------------------------------------------------------------
# SECTION 7: THE VOLUME EQUATION
# ---------------------------------------------------------------------------

EQUATION = ("OPPORTUNITY_ARRIVAL x PCT_PASSING_EV x PCT_PASSING_RISK x P_FILL "
            "x AVERAGE_FILLED_SIZE = EXPECTED_EXECUTED_NOTIONAL")
UNKNOWN_TERMS = ("PCT_PASSING_EV", "PCT_PASSING_RISK", "P_FILL")
NEVER_COLLAPSE_UNKNOWNS = (
    "an unknown factor is not a zero and not a one; a product containing one "
    "is NOT_IDENTIFIED, and each term stays separately visible so a reader can "
    "see which one is missing")


def volume_equation(opportunity_arrival_per_day, pct_passing_ev=None,
                    pct_passing_risk=None, p_fill=None,
                    average_filled_size=None, capital_occupancy_hours=None):
    """The five-term chain, with every unknown term left standing.

    Any term that is None or NOT_IDENTIFIED makes the product NOT_IDENTIFIED
    and is named in MISSING_TERMS. Supplying a hypothetical for one makes the
    whole result a SCENARIO.
    """
    terms = {
        "OPPORTUNITY_ARRIVAL_PER_DAY": _d(opportunity_arrival_per_day),
        "PCT_PASSING_EV": _d(pct_passing_ev),
        "PCT_PASSING_RISK": _d(pct_passing_risk),
        "P_FILL": _d(p_fill),
        "AVERAGE_FILLED_SIZE": _d(average_filled_size),
    }
    missing = [k for k, v in terms.items() if v is None]

    intents = terms["OPPORTUNITY_ARRIVAL_PER_DAY"]
    admissible = None
    if intents is not None and terms["PCT_PASSING_EV"] is not None \
            and terms["PCT_PASSING_RISK"] is not None:
        admissible = intents * terms["PCT_PASSING_EV"] * terms["PCT_PASSING_RISK"]
    fills = (admissible * terms["P_FILL"]) if (admissible is not None
                                               and terms["P_FILL"] is not None) \
        else None
    notional = (fills * terms["AVERAGE_FILLED_SIZE"]) \
        if (fills is not None and terms["AVERAGE_FILLED_SIZE"] is not None) \
        else None

    occ = _d(capital_occupancy_hours)
    avg_cap = ((fills * occ / D(24)) * terms["AVERAGE_FILLED_SIZE"]) \
        if (fills is not None and occ is not None
            and terms["AVERAGE_FILLED_SIZE"] is not None) else None
    turns = (D(24) / occ) if (occ is not None and occ != 0) else None

    def s(x):
        return str(x) if x is not None else NOT_IDENTIFIED

    return {
        "LABEL": SCENARIO_LABEL,
        "EQUATION": EQUATION,
        "TERMS": {k: s(v) for k, v in terms.items()},
        "MISSING_TERMS": missing,
        "UNKNOWN_TERMS_BY_CONSTRUCTION": list(UNKNOWN_TERMS),
        "CANDIDATE_ORDER_INTENTS_PER_DAY": s(intents),
        "ADMISSIBLE_ORDER_INTENTS_PER_DAY": s(admissible),
        "EXPECTED_FILLED_ORDERS_PER_DAY": s(fills),
        "EXPECTED_EXECUTED_NOTIONAL_PER_DAY": s(notional),
        "AVERAGE_CAPITAL_OCCUPIED": s(avg_cap),
        "EXPECTED_CAPITAL_TURNS_PER_DAY": s(turns),
        "RESULT_IS_IDENTIFIED": notional is not None,
        "NEVER_COLLAPSE_UNKNOWNS": NEVER_COLLAPSE_UNKNOWNS,
        "MEASURED_BETTOR_P_FILL": NOT_IDENTIFIED,
        "MEASURED_PCT_PASSING_EV": NOT_IDENTIFIED,
        "MEASURED_PCT_PASSING_RISK": NOT_IDENTIFIED,
    }


# ---------------------------------------------------------------------------
# SECTION 8: BACKSOLVE TO A MONTHLY VOLUME TARGET
#
# CAPACITY PLANNING ONLY. This answers "what would the machine have to do",
# never "what will it do". A target is not a forecast and reaching one is not
# permission to trade anything.
# ---------------------------------------------------------------------------

MONTHLY_TARGETS_USD = ("5000000", "10000000", "20000000", "50000000")
DEFAULT_CLIPS_USD = ("25", "100", "250", "1000")
DEFAULT_P_FILL = ("0.10", "0.20", "0.40")
DAYS_PER_MONTH = "30"

A_TARGET_IS_NOT_A_FORECAST = (
    "these tables say what throughput each volume level would require; they "
    "say nothing about whether it is achievable or profitable, and a target "
    "never admits an order")
VOLUME_TARGET_NEVER_LOOSENS_EV = (
    "if a target needs more orders than positive EV supplies, the answer is "
    "fewer orders, not a lower bar")


def backsolve(monthly_target_usd, clip_usd, p_fill,
              capital_occupancy_hours="2", pct_passing_ev=None,
              pct_passing_risk=None):
    """What one target requires, under one set of scenario assumptions."""
    target = _d(monthly_target_usd)
    clip = _d(clip_usd)
    pf = _d(p_fill)
    occ = _d(capital_occupancy_hours)
    ev = _d(pct_passing_ev)
    risk = _d(pct_passing_risk)
    days = _d(DAYS_PER_MONTH)

    gross_day = (target / days) if (target is not None and days) else None
    fills_day = (gross_day / clip) if (gross_day is not None and clip) else None
    intents_day = (fills_day / pf) if (fills_day is not None and pf) else None
    # Opportunities needed upstream of EV and risk, when those are supplied.
    opps_day = None
    if intents_day is not None and ev and risk:
        opps_day = intents_day / (ev * risk)
    avg_cap = ((fills_day * occ / D(24)) * clip) \
        if (fills_day is not None and occ is not None and clip) else None
    turns = (D(24) / occ) if (occ is not None and occ != 0) else None

    def s(x, q=None):
        if x is None:
            return NOT_IDENTIFIED
        return str(x.quantize(D(q)) if q else x)

    return {
        "LABEL": SCENARIO_LABEL,
        "MONTHLY_TARGET_USD": monthly_target_usd,
        "DAYS_PER_MONTH": DAYS_PER_MONTH,
        "HYPOTHETICAL_CLIP_USD": clip_usd,
        "HYPOTHETICAL_P_FILL": p_fill,
        "CAPITAL_OCCUPANCY_HOURS": capital_occupancy_hours,
        "REQUIRED_GROSS_FILLED_NOTIONAL_PER_DAY": s(gross_day, "0.01"),
        "REQUIRED_FILLED_ORDERS_PER_DAY": s(fills_day, "1"),
        "REQUIRED_DISTINCT_ORDER_INTENTS_PER_DAY": s(intents_day, "1"),
        "REQUIRED_OPPORTUNITIES_PER_DAY": s(opps_day, "1"),
        "REQUIRED_PCT_PASSING_EV": (pct_passing_ev if pct_passing_ev
                                    is not None else NOT_IDENTIFIED),
        "REQUIRED_PCT_PASSING_RISK": (pct_passing_risk if pct_passing_risk
                                      is not None else NOT_IDENTIFIED),
        "CAPITAL_REQUIRED": s(avg_cap, "0.01"),
        "CAPITAL_TURNS_PER_DAY": s(turns, "0.01"),
        "A_TARGET_IS_NOT_A_FORECAST": A_TARGET_IS_NOT_A_FORECAST,
        "VOLUME_TARGET_NEVER_LOOSENS_EV": VOLUME_TARGET_NEVER_LOOSENS_EV,
    }


def backsolve_table(targets=MONTHLY_TARGETS_USD, clips=DEFAULT_CLIPS_USD,
                    p_fills=DEFAULT_P_FILL, capital_occupancy_hours="2"):
    rows = [backsolve(t, c, p, capital_occupancy_hours)
            for t in targets for c in clips for p in p_fills]
    return {
        "LABEL": SCENARIO_LABEL,
        "ROWS": rows,
        "ROW_COUNT": len(rows),
        "TARGETS": list(targets),
        "CLIPS": list(clips),
        "P_FILLS": list(p_fills),
        "THIS_IS": "CAPACITY_PLANNING",
        "THIS_IS_NOT": list(THIS_IS_NOT),
        "A_TARGET_IS_NOT_A_FORECAST": A_TARGET_IS_NOT_A_FORECAST,
    }


# ---------------------------------------------------------------------------
# SECTION 9: THE CRITICAL QUESTION
# ---------------------------------------------------------------------------

CRITICAL_QUESTION = (
    "DOES THE BETTOR MARKET UNIVERSE GENERATE ENOUGH DISTINCT, ECONOMICALLY "
    "MEANINGFUL STATE CHANGES PER DAY THAT -- IF A REASONABLE FRACTION PASS EV "
    "AND FILL -- $20M+ MONTHLY EXECUTED VOLUME IS OPERATIONALLY PLAUSIBLE?")
ANSWERS = ("YES", "NO", NOT_IDENTIFIED)
ADDRESSABLE_COUNT_IS_NOT_AN_ANSWER = (
    "2,204 addressable markets is a census, and the question is about a rate; "
    "inferring YES from the market count would repeat the V1 defect exactly")


def critical_question(arrival_report=None, addressable_markets=None,
                      measured_markets=None, addressable_measured=None,
                      tradable_hours_per_day=None):
    """Answer only from evidence, and name what the answer is waiting on.

    The verdict turns on whether the measured arrival rate can be carried from
    the markets actually observed to the addressable universe. That is a
    representativeness claim, and it is the one this sample cannot support.
    """
    a = arrival_report or {}
    per_mkt_hr = a.get("DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR",
                       NOT_IDENTIFIED)
    blockers = []
    if not isinstance(per_mkt_hr, (int, float)):
        blockers.append("NO_MEASURED_ARRIVAL_RATE")
    if measured_markets is not None and addressable_markets:
        if addressable_measured is not None and addressable_measured < 5:
            blockers.append("ARRIVAL_MEASURED_ON_TOO_FEW_ADDRESSABLE_MARKETS")
    if tradable_hours_per_day is None:
        blockers.append("TRADABLE_HOURS_PER_DAY_NOT_MEASURED")
    blockers.append("PCT_PASSING_EV_NOT_IDENTIFIED")
    blockers.append("P_FILL_NOT_IDENTIFIED")

    return {
        "CRITICAL_QUESTION": CRITICAL_QUESTION,
        "ANSWER": NOT_IDENTIFIED,
        "PERMITTED_ANSWERS": list(ANSWERS),
        "MEASURED_ARRIVAL_PER_MARKET_HOUR": per_mkt_hr,
        "MARKETS_MEASURED": measured_markets if measured_markets is not None
                            else NOT_IDENTIFIED,
        "ADDRESSABLE_MARKETS_MEASURED": (addressable_measured
                                         if addressable_measured is not None
                                         else NOT_IDENTIFIED),
        "ADDRESSABLE_MARKETS_TOTAL": (addressable_markets
                                      if addressable_markets is not None
                                      else NOT_IDENTIFIED),
        "TRADABLE_HOURS_PER_DAY": (tradable_hours_per_day
                                   if tradable_hours_per_day is not None
                                   else NOT_IDENTIFIED),
        "BLOCKERS": blockers,
        "WHAT_WOULD_SETTLE_IT": [
            "an arrival rate measured across a representative sample of the "
            "addressable universe, not a handful of markets",
            "a measured tradable-hours-per-day figure per market",
            "a first fair value, giving PCT_PASSING_EV",
            "one real resting order, giving P_FILL",
        ],
        "ADDRESSABLE_COUNT_IS_NOT_AN_ANSWER": ADDRESSABLE_COUNT_IS_NOT_AN_ANSWER,
        "WHY_NOT_YES": (
            "the arrival rate is real but was measured on a very small, "
            "non-random set of markets; carrying it to the whole universe is "
            "an assumption, and two of the five equation terms are absent"),
        "WHY_NOT_NO": (
            "nothing measured so far rules the volume out; the state-change "
            "supply observed is not obviously too small, so NO would be as "
            "unevidenced as YES"),
    }


# ---------------------------------------------------------------------------
# SECTION 10: THE FAIR VALUE BLOCKER
#
# Documented, not solved. Inventing a pricing model to clear this field would
# be worse than leaving it open, because every EV downstream would inherit the
# invention while looking like a measurement.
# ---------------------------------------------------------------------------

FAIR_VALUE_CURRENT_STATUS = NOT_IDENTIFIED
DO_NOT_INVENT_A_MODEL = (
    "a pricing model written to clear this field would make every downstream "
    "EV look measured while being assumed; the field stays open until a fair "
    "value is built and validated on its own evidence")


def fair_value_gap():
    """What exactly stands between here and a first prospective fair value."""
    return {
        "FAIR_VALUE_CURRENT_STATUS": FAIR_VALUE_CURRENT_STATUS,
        "FAIR_VALUE_MISSING_INPUTS": [
            "an independent probability estimate per market outcome that does "
            "not come from the venue's own price",
            "a validated mapping from that estimate to a tradable edge net of "
            "the venue's 6% fee coefficient",
            "an out-of-sample calibration record showing the estimate is "
            "better than the venue mid, prospectively and not in hindsight",
        ],
        "FAIR_VALUE_DATA_ALREADY_AVAILABLE": [
            "sealed public board snapshots with venue best bid and ask",
            "sealed book tick series with top-of-book, ladder and "
            "shares-traded deltas",
            "canonical event identity across moneyline, spread and totals",
            "settled outcomes for historical contests in the snapshot log",
            "the venue's own fee coefficient and tick size per market",
        ],
        "FAIR_VALUE_DATA_NOT_YET_AVAILABLE": [
            "an independent outcome model or external probability source",
            "a prospective, timestamped prediction record that predates the "
            "outcome it is scored against",
            "BETTOR-native fill evidence, needed to turn an edge into an "
            "expected net result",
        ],
        "SHORTEST_PATH_TO_FIRST_PROSPECTIVE_FAIR_VALUE": [
            "1. pick ONE narrow, high-frequency market family with settled "
            "history already on disk -- the natural candidate is totals, now "
            "that 647 of them carry canonical event identity",
            "2. freeze a prediction rule BEFORE seeing outcomes, and stamp "
            "every prediction with the time it was made",
            "3. run it prospectively against the venue mid on new contests "
            "only, scoring calibration and log loss, with no refitting",
            "4. publish the calibration record; only if it beats the mid "
            "out of sample does a fair value exist at all",
            "5. THEN, and only then, PCT_PASSING_EV becomes measurable",
        ],
        "ESTIMATED_CALENDAR_TIME": NOT_IDENTIFIED,
        "WHY_TIME_IS_NOT_ESTIMATED": (
            "step 3 runs for as long as new contests take to settle, and how "
            "many are needed depends on an effect size nobody has measured"),
        "DO_NOT_INVENT_A_MODEL": DO_NOT_INVENT_A_MODEL,
        "THROUGHPUT_DOES_NOT_SUBSTITUTE_FOR_THIS": (
            "capacity and edge are different questions; no amount of "
            "throughput work moves this field"),
    }


def render(rep):
    L = []
    sp = rep.get("SINGLE_PASS", {})
    if sp:
        L.append("=== SECTION 1: THE RECLASSIFIED FIGURE ===")
        for k in ("SINGLE_PASS_ONE_FILL_PER_MARKET_NOTIONAL",
                  "MAX_DAILY_GROSS_NOTIONAL", "IS_A_CEILING",
                  "ONE_FILL_PER_MARKET_RULE_EXISTS"):
            L.append("%-46s = %s" % (k, sp.get(k, NOT_IDENTIFIED)))
    bs = rep.get("BACKSOLVE", {})
    if bs:
        L.append("")
        L.append("=== SECTION 8: BACKSOLVE - %s ===" % SCENARIO_LABEL)
        L.append("%-12s %8s %7s %14s %12s %14s %12s" % (
            "TARGET/MO", "CLIP", "P_FILL", "GROSS/DAY", "FILLS/DAY",
            "INTENTS/DAY", "CAPITAL"))
        for r in bs.get("ROWS", ()):
            L.append("%-12s %8s %7s %14s %12s %14s %12s" % (
                r["MONTHLY_TARGET_USD"], r["HYPOTHETICAL_CLIP_USD"],
                r["HYPOTHETICAL_P_FILL"],
                r["REQUIRED_GROSS_FILLED_NOTIONAL_PER_DAY"],
                r["REQUIRED_FILLED_ORDERS_PER_DAY"],
                r["REQUIRED_DISTINCT_ORDER_INTENTS_PER_DAY"],
                r["CAPITAL_REQUIRED"]))
    cq = rep.get("CRITICAL_QUESTION", {})
    if cq:
        L.append("")
        L.append("=== SECTION 9: THE CRITICAL QUESTION ===")
        L.append("ANSWER = %s" % cq.get("ANSWER"))
        for b in cq.get("BLOCKERS", ()):
            L.append("  blocked by: %s" % b)
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
