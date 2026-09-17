#!/usr/bin/env python3
"""THROUGHPUT_V1 -- OPPORTUNITY DENSITY AND SYSTEM CAPACITY. DIAGNOSTIC ONLY.

THE MANAGEMENT QUESTION THIS EXISTS TO ANSWER. Can BETTOR become a
high-turnover, high-volume system WITHOUT lowering its EV standard? That
question has two halves and they are measured differently:

    how many opportunities are there?      -- measurable from public evidence
    how many of them clear the EV bar?     -- NOT measurable yet, and this
                                              module refuses to pretend it is

So what follows is a FUNNEL, not a strategy. It counts how the broad venue
universe narrows to hypothetical BETTOR actions, and it stops counting at the
exact point where the inputs run out. It creates no trade, ranks nothing into
existence, and cannot lower a threshold.

THE ONE RULE THAT GOVERNS EVERY NUMBER HERE. Where FAIR_VALUE or P_FILL is
NOT_IDENTIFIED, every quantity downstream of it is NOT_IDENTIFIED. The funnel
therefore terminates honestly: it can tell you how many books were observed and
how many EV evaluations were attempted, and it CANNOT tell you how many were
positive-EV, because nothing in this repository can yet say so.

A HIGH TURNOVER NUMBER IS NOT A RESULT. Opportunity density says how often the
board offers something to look at. It says nothing about whether looking is
profitable, and a large funnel mouth with an unmeasurable exit is exactly the
shape that flatters a system into trading.

This module contacts nothing and can place no order.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D

import event_identity as EI

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_ESTABLISHED = "NOT_ESTABLISHED"

THIS_IS = "OPPORTUNITY_DENSITY_AND_CAPACITY_DIAGNOSTIC"
THIS_IS_NOT = ("A_TRADING_STRATEGY", "A_PROFITABILITY_CLAIM",
               "A_MEASUREMENT_OF_BETTOR_PERFORMANCE",
               "AN_EV_ADMISSION_STANDARD")
EV_STANDARD_IS_UNCHANGED = (
    "this module ranks and counts; it cannot admit a trade, and a fast "
    "recycling opportunity with negative or unidentified EV stays inadmissible")
DENSITY_IS_NOT_EDGE = (
    "how often the board offers something to look at is not how often looking "
    "pays; a wide funnel with an unmeasurable exit is the shape that flatters "
    "a system into trading")

# ---------------------------------------------------------------------------
# SECTION 1: THE FUNNEL
# ---------------------------------------------------------------------------

FUNNEL_STAGES = (
    "BOARD_MARKETS",
    "CANONICAL_EVENTS",
    "IDENTITY_ELIGIBLE_MARKETS",
    "ACTIVE_TRADABLE_MARKETS",
    "BOOKS_OBSERVED",
    "EV_EVALUATIONS",
    "MAKER_CANDIDATES",
    "TAKER_CANDIDATES",
    "NO_TRADE_DECISIONS",
    "POSITIVE_EV_MAKER_CANDIDATES",
    "POSITIVE_EV_TAKER_CANDIDATES",
    "RISK_ADMISSIBLE_CANDIDATES",
    "WOULD_QUOTE",
    "WOULD_TAKE",
    "WOULD_NOT_TRADE",
)

# The stages below this line cannot be counted while FAIR_VALUE and P_FILL are
# NOT_IDENTIFIED. They are reported as NOT_IDENTIFIED, never as zero: zero would
# be a measurement that no opportunity cleared the bar, and we have not made it.
STAGES_BLOCKED_BY_MISSING_EV_INPUTS = (
    "POSITIVE_EV_MAKER_CANDIDATES",
    "POSITIVE_EV_TAKER_CANDIDATES",
)
WHY_BLOCKED = (
    "a positive-EV count requires an EV, and EV requires FAIR_VALUE and a "
    "BETTOR-native P_FILL; both are NOT_IDENTIFIED, so the count is absent "
    "rather than zero")


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _two_sided(book):
    if not isinstance(book, dict):
        return False
    return bool(book.get("bids")) and bool(book.get("asks"))


def funnel(board_rows, books_by_slug=None, activity_of=None, now_iso=None,
           horizon_s=72 * 3600, ev_receipts=None, risk_receipts=None):
    """One scan interval's funnel, from the whole board to would-be actions.

    `ev_receipts` are micro_live_ev receipts if any were built; `risk_receipts`
    the corresponding risk evaluations. Both may be empty -- the funnel then
    reports the stages it can and marks the rest absent.
    """
    books_by_slug = books_by_slug or {}
    activity_of = activity_of or {}
    ev_receipts = list(ev_receipts or ())
    risk_receipts = list(risk_receipts or ())
    now = _parse(now_iso)

    seen, ident, events = set(), 0, set()
    tradable = 0
    for m in board_rows or ():
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        eid, level = EI.event_identity(m)
        if level != EI.LEVEL_V1_CONTEST:
            continue
        ident += 1
        events.add(eid)
        start = _parse(m.get("gameStartTime"))
        if now is not None and start is not None:
            if (start - now).total_seconds() > horizon_s:
                continue
        act = activity_of.get(slug) or {}
        if (act.get("HIGH_ACTIVITY_AT_DECISION")
                or act.get("ACTIVE_AT_DECISION")
                or act.get("BROAD_AT_DECISION")):
            tradable += 1

    observed = sum(1 for s in books_by_slug if _two_sided(books_by_slug[s]))
    maker = sum(1 for r in ev_receipts
                if r.get("ORDER_TYPE") == "MAKER_QUOTE")
    taker = sum(1 for r in ev_receipts
                if r.get("ORDER_TYPE") == "TAKER_CROSS")
    no_trade = sum(1 for r in ev_receipts if r.get("DECISION") == "NO_TRADE")
    admissible = sum(1 for r in risk_receipts
                     if r.get("RISK_VERDICT") == "RISK_APPROVED")

    out = {
        "BOARD_MARKETS": len(seen),
        "CANONICAL_EVENTS": len(events),
        "IDENTITY_ELIGIBLE_MARKETS": ident,
        "ACTIVE_TRADABLE_MARKETS": tradable,
        "BOOKS_OBSERVED": observed,
        "EV_EVALUATIONS": len(ev_receipts),
        "MAKER_CANDIDATES": maker,
        "TAKER_CANDIDATES": taker,
        "NO_TRADE_DECISIONS": no_trade,
        "POSITIVE_EV_MAKER_CANDIDATES": NOT_IDENTIFIED,
        "POSITIVE_EV_TAKER_CANDIDATES": NOT_IDENTIFIED,
        "RISK_ADMISSIBLE_CANDIDATES": admissible,
        "WOULD_QUOTE": sum(1 for r in risk_receipts
                           if r.get("RISK_VERDICT") == "RISK_APPROVED"),
        "WOULD_TAKE": 0,
        "WOULD_NOT_TRADE": max(0, len(ev_receipts) - admissible),
    }
    out.update({
        "STAGES": list(FUNNEL_STAGES),
        "STAGES_BLOCKED_BY_MISSING_EV_INPUTS":
            list(STAGES_BLOCKED_BY_MISSING_EV_INPUTS),
        "WHY_BLOCKED": WHY_BLOCKED,
        "WHY_WOULD_TAKE_IS_ZERO": (
            "the first live test is passive-maker only; a taker cross is not "
            "evaluated in this build, so zero is a scope statement not a "
            "measurement of taker opportunity"),
        "WHY_RISK_ADMISSIBLE_MAY_BE_ZERO": (
            "every risk limit is AUTHORIZATION_STATUS = NOT_SET, and NOT_SET "
            "blocks; this counts authorization, not opportunity"),
        "DENSITY_IS_NOT_EDGE": DENSITY_IS_NOT_EDGE,
        "THIS_IS": THIS_IS,
        "THIS_IS_NOT": list(THIS_IS_NOT),
    })
    return out


# ---------------------------------------------------------------------------
# SECTION 2: DECISIONS, ORDERS, FILLS AND TRADES ARE DIFFERENT THINGS
# ---------------------------------------------------------------------------

RATE_FIELDS = (
    "DECISIONS_PER_MINUTE",
    "QUOTE_OPPORTUNITIES_PER_MINUTE",
    "ORDER_INTENTS_PER_MINUTE",
    "RESTING_QUOTES_PER_MINUTE",
    "EXPECTED_FILLS_PER_MINUTE",
    "ACTUAL_FILLS_PER_MINUTE",
    "UNIQUE_MARKETS_PER_DAY",
    "UNIQUE_EVENTS_PER_DAY",
    "GROSS_NOTIONAL_PER_DAY",
    "CAPITAL_TURNS_PER_DAY",
)

A_QUOTE_UPDATE_IS_NOT_A_TRADE = (
    "re-pricing a resting order is one order intent replacing another; it "
    "moves no contracts and must never be counted as volume")
AN_INTENT_IS_NOT_A_FILL = (
    "an order intent is a decision to try; a fill is somebody trading against "
    "us, and only the venue can report one")


def rates(window_s, decisions=0, quote_opportunities=0, order_intents=0,
          resting_quotes=0, unique_markets=0, unique_events=0,
          expected_fills=None, gross_notional=None, capital_turns=None):
    """Per-minute and per-day rates, with the four categories kept apart.

    EXPECTED_FILLS_PER_MINUTE is SCENARIO-ONLY and is NOT_IDENTIFIED unless a
    caller supplies a hypothetical. ACTUAL_FILLS_PER_MINUTE is NOT_IDENTIFIED
    until a real fill exists; there is no argument by which a caller can set it.
    """
    mins = (float(window_s) / 60.0) if window_s else 0.0
    days = (float(window_s) / 86400.0) if window_s else 0.0

    def per_min(n):
        return (n / mins) if mins > 0 else NOT_IDENTIFIED

    def per_day(n):
        return (n / days) if days > 0 else NOT_IDENTIFIED

    return {
        "WINDOW_S": window_s,
        "DECISIONS_PER_MINUTE": per_min(decisions),
        "QUOTE_OPPORTUNITIES_PER_MINUTE": per_min(quote_opportunities),
        "ORDER_INTENTS_PER_MINUTE": per_min(order_intents),
        "RESTING_QUOTES_PER_MINUTE": per_min(resting_quotes),
        "EXPECTED_FILLS_PER_MINUTE": (per_min(expected_fills)
                                      if expected_fills is not None
                                      else NOT_IDENTIFIED),
        "EXPECTED_FILLS_IS_SCENARIO_ONLY": True,
        "ACTUAL_FILLS_PER_MINUTE": NOT_IDENTIFIED,
        "ACTUAL_FILLS_REQUIRES": "LIVE_VENUE_EVIDENCE",
        "UNIQUE_MARKETS_PER_DAY": per_day(unique_markets),
        "UNIQUE_EVENTS_PER_DAY": per_day(unique_events),
        "GROSS_NOTIONAL_PER_DAY": (per_day(gross_notional)
                                   if gross_notional is not None
                                   else NOT_IDENTIFIED),
        "CAPITAL_TURNS_PER_DAY": (capital_turns if capital_turns is not None
                                  else NOT_IDENTIFIED),
        "A_QUOTE_UPDATE_IS_NOT_A_TRADE": A_QUOTE_UPDATE_IS_NOT_A_TRADE,
        "AN_INTENT_IS_NOT_A_FILL": AN_INTENT_IS_NOT_A_FILL,
        "THE_FOUR_ARE_DISTINCT": ("DECISION", "ORDER_INTENT", "FILL", "TRADE"),
    }


# ---------------------------------------------------------------------------
# SECTION 3: THE TURNOVER-AWARE CAPITAL METRIC
# ---------------------------------------------------------------------------

CAPITAL_FIELDS = (
    "EXPECTED_NET_DOLLARS",
    "CAPITAL_REQUIRED",
    "EXPECTED_CAPITAL_OCCUPANCY_TIME",
    "EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR",
    "EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR",
    "EXPECTED_TIME_TO_RECYCLE",
)

RANKING_IS_NOT_ADMISSION = (
    "this orders opportunities that have ALREADY cleared the EV standard; it "
    "cannot move one across it, and a negative or unidentified EV stays "
    "inadmissible however fast it recycles")


def _d(x):
    if x in (None, NOT_IDENTIFIED, NOT_ESTABLISHED, ""):
        return None
    try:
        return D(str(x))
    except Exception:                                         # noqa: BLE001
        return None


def capital_metric(expected_net_dollars=NOT_IDENTIFIED,
                   capital_required=NOT_IDENTIFIED,
                   expected_occupancy_s=NOT_IDENTIFIED,
                   ev_admissible=None):
    """Net dollars per capital dollar per hour, where the inputs permit it.

    `ev_admissible` is the FROZEN EV verdict, carried through untouched. If it
    is not True, the ranking fields are still computed for diagnosis but
    ADMISSIBLE stays False and RANK_ELIGIBLE is False: a fast recycle is not a
    route across the EV bar.
    """
    net = _d(expected_net_dollars)
    cap = _d(capital_required)
    occ = _d(expected_occupancy_s)

    per_dollar = (net / cap) if (net is not None and cap and cap != 0) else None
    per_hour = None
    if per_dollar is not None and occ is not None and occ > 0:
        per_hour = per_dollar / (occ / D("3600"))

    missing = [n for n, v in (("EXPECTED_NET_DOLLARS", net),
                              ("CAPITAL_REQUIRED", cap),
                              ("EXPECTED_CAPITAL_OCCUPANCY_TIME", occ))
               if v is None]
    return {
        "EXPECTED_NET_DOLLARS": expected_net_dollars,
        "CAPITAL_REQUIRED": capital_required,
        "EXPECTED_CAPITAL_OCCUPANCY_TIME": expected_occupancy_s,
        "EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR": (
            per_dollar if per_dollar is not None else NOT_IDENTIFIED),
        "EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR": (
            per_hour if per_hour is not None else NOT_IDENTIFIED),
        "EXPECTED_TIME_TO_RECYCLE": (expected_occupancy_s
                                     if occ is not None else NOT_IDENTIFIED),
        "MISSING_INPUTS": missing,
        "EV_ADMISSIBLE": (ev_admissible if ev_admissible is not None
                          else NOT_IDENTIFIED),
        "RANK_ELIGIBLE": ev_admissible is True,
        "ADMISSIBLE": ev_admissible is True,
        "RANKING_IS_NOT_ADMISSION": RANKING_IS_NOT_ADMISSION,
        "TURNOVER_CANNOT_RESCUE_NEGATIVE_EV": True,
    }


def rank_opportunities(rows):
    """Order ADMISSIBLE opportunities by net dollars per capital dollar hour.

    Inadmissible rows are returned in a separate list rather than sorted to the
    bottom, so nothing inadmissible can be read off the end of a ranking as
    though it were merely last.
    """
    eligible, refused = [], []
    for r in rows or ():
        key = r.get("EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR")
        if r.get("RANK_ELIGIBLE") and _d(key) is not None:
            eligible.append(r)
        else:
            refused.append(r)
    eligible.sort(key=lambda r: _d(
        r["EXPECTED_NET_DOLLARS_PER_CAPITAL_DOLLAR_PER_HOUR"]), reverse=True)
    return {
        "RANKED": eligible,
        "NOT_RANKED": refused,
        "RANKED_COUNT": len(eligible),
        "NOT_RANKED_COUNT": len(refused),
        "WHY_SEPARATE_LISTS": (
            "an inadmissible opportunity at the bottom of a ranking still "
            "looks like a ranked opportunity"),
        "RANKING_IS_NOT_ADMISSION": RANKING_IS_NOT_ADMISSION,
    }


# ---------------------------------------------------------------------------
# SECTION 4: VOLUME AS SCENARIOS, NEVER AS FACT
# ---------------------------------------------------------------------------

SCENARIO_LABEL = "SCENARIO - NOT MEASURED BETTOR PERFORMANCE"
P_FILL_GRID = ("0.05", "0.10", "0.20", "0.30", "0.40", "0.50")
WHALE_P_FILL_IS_FORBIDDEN = (
    "the whale completion rate measures whether somebody else's counterparty "
    "turned up, on another venue, for trades they chose to open; it is not a "
    "BETTOR fill probability and is never used as one")
SCENARIOS_NEVER_REACH_THE_LIVE_ENGINE = (
    "these tables are diagnostic; no live EV path reads them, and a scenario "
    "fill probability must never be passed to ev_maker_quote")


def scenario_table(orders_per_day, clip_sizes, p_fill_grid=P_FILL_GRID,
                   occupancy_hours=None):
    """The volume grid. Every cell is a hypothetical and says so.

    `orders_per_day` is the MEASURED quote-opportunity rate -- that part is
    evidence. Everything multiplied by a p_fill is not.
    """
    orders = _d(orders_per_day)
    occ_h = _d(occupancy_hours)
    cells = []
    for clip in clip_sizes or ():
        c = _d(clip)
        for p in p_fill_grid:
            pf = _d(p)
            if orders is None or c is None or pf is None:
                continue
            fills = orders * pf
            notional = fills * c
            # Average concurrent inventory = fills/day x hours held / 24 h.
            concurrent = (fills * occ_h / D("24")) if occ_h is not None else None
            occupied = (concurrent * c) if concurrent is not None else None
            turns = ((notional / occupied) if (occupied and occupied != 0)
                     else None)
            cells.append({
                "LABEL": SCENARIO_LABEL,
                "HYPOTHETICAL_P_FILL": str(pf),
                "HYPOTHETICAL_CLIP_USD": str(c),
                "ORDERS_PER_DAY": str(orders),
                "FILLS_PER_DAY": str(fills),
                "GROSS_FILLED_NOTIONAL_PER_DAY": str(notional),
                "AVERAGE_CONCURRENT_INVENTORY": (str(concurrent)
                                                 if concurrent is not None
                                                 else NOT_IDENTIFIED),
                "ESTIMATED_CAPITAL_OCCUPANCY_USD": (str(occupied)
                                                    if occupied is not None
                                                    else NOT_IDENTIFIED),
                "CAPITAL_TURNS_PER_DAY": (str(turns) if turns is not None
                                          else NOT_IDENTIFIED),
            })
    return {
        "LABEL": SCENARIO_LABEL,
        "CELLS": cells,
        "CELL_COUNT": len(cells),
        "P_FILL_GRID": list(p_fill_grid),
        "CLIP_SIZES": [str(c) for c in (clip_sizes or ())],
        "ORDERS_PER_DAY_IS_MEASURED": True,
        "EVERYTHING_MULTIPLIED_BY_P_FILL_IS_NOT": True,
        "BETTOR_P_FILL": NOT_IDENTIFIED,
        "WHALE_P_FILL_IS_FORBIDDEN": WHALE_P_FILL_IS_FORBIDDEN,
        "SCENARIOS_NEVER_REACH_THE_LIVE_ENGINE":
            SCENARIOS_NEVER_REACH_THE_LIVE_ENGINE,
        "ACTUAL_FILLS_PER_DAY": NOT_IDENTIFIED,
        "REALIZED_ANYTHING": NOT_ESTABLISHED,
    }


# ---------------------------------------------------------------------------
# SECTION 5: OPPORTUNITY DENSITY BY MARKET CLASS
#
# READ FROM A TRUNCATED INSTRUCTION. The brief's section 5 arrived with its
# heading only, so this implements what the heading states: the same funnel,
# broken out by the venue's own market class, by league, and by time to
# kickoff. Those three are the venue-native axes already proven elsewhere in
# this programme; no new taxonomy is invented. If the intended breakdown was
# different, this section is the one to re-point.
# ---------------------------------------------------------------------------

TIME_BUCKETS_S = ((0, "LIVE_OR_STARTED"),
                  (3600, "WITHIN_1H"),
                  (6 * 3600, "WITHIN_6H"),
                  (24 * 3600, "WITHIN_24H"),
                  (72 * 3600, "WITHIN_72H"))
BUCKET_BEYOND = "BEYOND_72H"


def _bucket(start, now):
    if start is None or now is None:
        return NOT_IDENTIFIED
    d = (start - now).total_seconds()
    if d <= 0:
        return "LIVE_OR_STARTED"
    for limit, name in TIME_BUCKETS_S:
        if limit and d <= limit:
            return name
    return BUCKET_BEYOND


def density_by_class(board_rows, now_iso=None, books_by_slug=None):
    """Where the opportunities actually are, on the venue's own axes."""
    now = _parse(now_iso)
    books_by_slug = books_by_slug or {}
    by_class, by_league, by_bucket = (defaultdict(Counter),
                                      defaultdict(Counter),
                                      defaultdict(Counter))
    events_by_class = defaultdict(set)
    seen = set()
    for m in board_rows or ():
        if not isinstance(m, dict):
            continue
        slug = m.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        cls = (m.get("sportsMarketTypeV2") or m.get("marketType")
               or NOT_IDENTIFIED)
        lgs = EI.leagues(m)
        league = sorted(lgs)[0] if lgs else NOT_IDENTIFIED
        eid, level = EI.event_identity(m)
        contest = level == EI.LEVEL_V1_CONTEST
        bucket = _bucket(_parse(m.get("gameStartTime")), now)

        for tgt, key in ((by_class, cls), (by_league, league),
                         (by_bucket, bucket)):
            tgt[key]["BOARD_MARKETS"] += 1
            if contest:
                tgt[key]["IDENTITY_ELIGIBLE_MARKETS"] += 1
            if _two_sided(books_by_slug.get(slug)):
                tgt[key]["BOOKS_OBSERVED_TWO_SIDED"] += 1
        if contest:
            events_by_class[cls].add(eid)

    def pack(d, extra=None):
        out = {}
        for k, c in d.items():
            row = dict(c)
            row["IDENTITY_SHARE"] = (
                row.get("IDENTITY_ELIGIBLE_MARKETS", 0)
                / float(row["BOARD_MARKETS"]) if row.get("BOARD_MARKETS")
                else NOT_IDENTIFIED)
            if extra is not None:
                row["CANONICAL_EVENTS"] = len(extra.get(k, ()))
            out[k] = row
        return dict(sorted(out.items(),
                           key=lambda kv: -kv[1].get("BOARD_MARKETS", 0)))

    return {
        "BY_MARKET_CLASS": pack(by_class, events_by_class),
        "BY_LEAGUE": pack(by_league),
        "BY_TIME_TO_KICKOFF": pack(by_bucket),
        "AXES_ARE_VENUE_NATIVE": True,
        "NO_NEW_TAXONOMY_INVENTED": True,
        "SECTION_5_READ_FROM_A_TRUNCATED_INSTRUCTION": True,
        "DENSITY_IS_NOT_EDGE": DENSITY_IS_NOT_EDGE,
    }


def render(rep):
    L = ["=== FUNNEL ==="]
    f = rep.get("FUNNEL", {})
    for s in FUNNEL_STAGES:
        L.append("%-34s = %s" % (s, f.get(s, NOT_IDENTIFIED)))
    L.append("")
    L.append("=== RATES ===")
    r = rep.get("RATES", {})
    for k in RATE_FIELDS:
        v = r.get(k, NOT_IDENTIFIED)
        L.append("%-34s = %s" % (k, ("%.4f" % v) if isinstance(v, float)
                                 else v))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
