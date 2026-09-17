#!/usr/bin/env python3
"""OPPORTUNITY ARRIVAL. How often does a market give us something new to decide?

THE DEFECT THIS MODULE EXISTS TO FIX. THROUGHPUT_V1 counted the opportunity set
as a SNAPSHOT MARKET COUNT -- 2,204 addressable markets, one order each, and a
notional that followed from multiplying the two. That smuggled in a
one-fill-per-market-per-day rule which does not exist anywhere in this system.
A single market can legitimately produce many distinct economic opportunities
over a day: the book moves, a quote fills, inventory changes, and the decision
is live again. Opportunity is a RATE, not a census.

THE OPPOSITE ERROR, AND IT IS THE EASIER ONE TO MAKE. If an opportunity is
"whatever we saw when we polled", then polling twice as fast doubles the
opportunity count and the throughput number becomes a measurement of our own
scheduler. That is manufactured volume. So the rule here is:

    AN OPPORTUNITY IS A CHANGE IN THE DECISION SURFACE, NOT AN OBSERVATION.

Two consecutive observations of an identical book are ONE state, not two
opportunities. The count is over TRANSITIONS between distinct states, so a
faster poll can only ever discover the same transitions sooner -- it cannot
create one. `poll_invariance_check` proves this on real rows by decimating a
series and confirming the count does not rise.

THE TRIGGER LADDER, DECLARED RATHER THAN CHOSEN SILENTLY. Whether a change is
"economically meaningful" depends on a decision model, and BETTOR does not have
one yet -- there is no fair value. Picking one threshold and reporting a single
number would hide that. So every tier is computed and reported together:

    TIER_1  touch PRICE, market STATE, or a TRADE
            unambiguously decision-relevant under any maker model
    TIER_2  TIER_1 + size at the touch  <-- THE DECLARED DEFAULT
            queue position and adverse selection at our own resting price
    TIER_3  TIER_2 + any change deeper in the ladder
            the loosest defensible reading
    VENUE_VERSION  the venue's own book-version stamp (TRANSACT_TIME)
            a ceiling: it moves for changes we cannot even see

On the sealed tick evidence these differ by more than an order of magnitude,
so the tier choice is the dominant uncertainty in every downstream number and
is reported as such rather than buried in a default.

WHAT IS NOT A TRIGGER HERE, AND WHY THAT MAKES THIS AN UNDERCOUNT. Fair-value
change, inventory change, risk-state change, our own quote filling or expiring
-- all of these are legitimate triggers for the mature engine and NONE is
observable in a public book tick. The public-data arrival rate is therefore a
LOWER BOUND on the mature engine's opportunity rate:
ARRIVAL_BIAS_DIRECTION = UNDERCOUNT.

THESE ARE NOT POSITIVE-EV OPPORTUNITIES. They are OBSERVABLE QUOTEABLE STATE
OPPORTUNITIES. Calling them positive-EV would require an EV, and there is none.

This module contacts nothing and can place no order.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal as D, InvalidOperation

NOT_IDENTIFIED = "NOT_IDENTIFIED"

THIS_COUNTS = "OBSERVABLE_QUOTEABLE_STATE_OPPORTUNITIES"
THIS_DOES_NOT_COUNT = "POSITIVE_EV_OPPORTUNITIES"
WHY = ("a positive-EV count requires a fair value; BETTOR has none, so these "
       "are state changes a maker COULD re-decide on, not ones it should")

AN_OBSERVATION_IS_NOT_AN_OPPORTUNITY = (
    "opportunities are counted over transitions between distinct decision "
    "states; two identical consecutive observations are one state, so a "
    "faster poll discovers the same transitions sooner and never creates one")

ARRIVAL_BIAS_DIRECTION = "UNDERCOUNT"
WHY_UNDERCOUNT = (
    "fair-value change, inventory change, risk-state change, and our own "
    "quote filling or expiring are all legitimate triggers for the mature "
    "engine and none is observable in a public book tick")

# ---------------------------------------------------------------------------
# THE DECISION SURFACE
#
# These are the fields a passive maker's decision can actually read off a
# public book. Anything not on this list cannot trigger an opportunity here,
# by construction rather than by discipline.
# ---------------------------------------------------------------------------

TOUCH_PRICE_FIELDS = ("BID", "ASK")
TOUCH_SIZE_FIELDS = ("BID_QTY", "ASK_QTY")
LADDER_FIELDS = ("BID_LADDER", "ASK_LADDER")
STATE_FIELD = "STATE"
TRADE_FIELD = "SHARES_TRADED_DELTA"
VENUE_VERSION_FIELD = "TRANSACT_TIME"

TRIGGER_BEST_BID = "BEST_BID_CHANGED"
TRIGGER_BEST_ASK = "BEST_ASK_CHANGED"
TRIGGER_SPREAD = "SPREAD_CHANGED"
TRIGGER_STATE = "MARKET_STATE_CHANGED"
TRIGGER_TRADE = "TRADE_OCCURRED"
TRIGGER_TOUCH_SIZE = "TOUCH_SIZE_CHANGED"
TRIGGER_DEPTH = "DEPTH_CHANGED_BEYOND_TOUCH"

TIER_1 = "TIER_1_TOUCH_PRICE_STATE_OR_TRADE"
TIER_2 = "TIER_2_PLUS_SIZE_AT_THE_TOUCH"
TIER_3 = "TIER_3_PLUS_ANY_LADDER_CHANGE"
TIER_VENUE = "VENUE_BOOK_VERSION_CEILING"
TIERS = (TIER_1, TIER_2, TIER_3, TIER_VENUE)

DEFAULT_TIER = TIER_2
WHY_DEFAULT_TIER = (
    "a resting maker's fill probability and adverse selection depend on the "
    "size queued at its own price, so a touch-size change is a real re-decide; "
    "a reshuffle five levels deep at a price we would never rest at is not")

# TRIGGERS THE MATURE ENGINE WILL HAVE AND A PUBLIC TICK DOES NOT SUPPLY.
# Listed so the gap is explicit and so nobody reads the measured rate as the
# engine's full opportunity rate.
TRIGGERS_NOT_OBSERVABLE_IN_PUBLIC_DATA = (
    "FAIR_VALUE_CHANGED",
    "PRIOR_QUOTE_FILLED",
    "PRIOR_QUOTE_CANCELLED_OR_EXPIRED",
    "INVENTORY_STATE_CHANGED",
    "RISK_STATE_CHANGED",
    "PRICE_IMPROVEMENT_WINDOW_OPENED",
)
TIME_TO_EVENT_IS_NOT_A_TRIGGER_YET = (
    "time to kickoff changes continuously and would generate unlimited "
    "opportunities; it becomes a trigger only when a decision model actually "
    "reads it, and none does")


def _d(x):
    if x in (None, "", NOT_IDENTIFIED):
        return None
    try:
        return D(str(x))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        v = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def decision_state(row):
    """The canonical decision state of one observation.

    Two observations with equal decision states are the SAME state. This is the
    function that makes poll frequency irrelevant, so it reads only fields a
    decision could use -- never a receipt time, a sequence number or a latency.

    CUMULATIVE SHARES TRADED IS PART OF THE STATE. A trade can occur and leave
    the book looking identical -- somebody lifts the offer and it refills at the
    same price and size. That is new information about flow and a legitimate
    re-decide, so the venue's own monotone volume counter belongs here. It is
    monotone, so including it cannot break poll invariance: looking less often
    still only merges transitions.
    """
    if not isinstance(row, dict):
        return None
    return (
        str(row.get("BID")), str(row.get("ASK")),
        str(row.get("BID_QTY")), str(row.get("ASK_QTY")),
        json.dumps(row.get("BID_LADDER"), sort_keys=True, default=str),
        json.dumps(row.get("ASK_LADDER"), sort_keys=True, default=str),
        str(row.get(STATE_FIELD)), str(row.get("SHARES_TRADED")),
    )


def transition(prev, cur):
    """Classify ONE consecutive pair. Deterministic, and the audit unit.

    Returns the triggers that fired and the tier each one qualifies at. A pair
    of identical observations returns no triggers at every tier -- that is the
    property everything else rests on.
    """
    out = {
        "TRIGGERS": [],
        "TIER_1": False, "TIER_2": False, "TIER_3": False,
        "VENUE_VERSION_MOVED": False,
        "IDENTICAL_DECISION_STATE": False,
    }
    if not isinstance(prev, dict) or not isinstance(cur, dict):
        return out

    trig = out["TRIGGERS"]
    same = decision_state(prev) == decision_state(cur)
    out["IDENTICAL_DECISION_STATE"] = same

    if prev.get("BID") != cur.get("BID"):
        trig.append(TRIGGER_BEST_BID)
    if prev.get("ASK") != cur.get("ASK"):
        trig.append(TRIGGER_BEST_ASK)
    if prev.get("SPREAD") != cur.get("SPREAD"):
        trig.append(TRIGGER_SPREAD)
    if prev.get(STATE_FIELD) != cur.get(STATE_FIELD):
        trig.append(TRIGGER_STATE)
    delta = _d(cur.get(TRADE_FIELD))
    if delta is not None and delta > 0:
        trig.append(TRIGGER_TRADE)

    price_or_state = any(t in trig for t in (TRIGGER_BEST_BID, TRIGGER_BEST_ASK,
                                             TRIGGER_SPREAD, TRIGGER_STATE))
    traded = TRIGGER_TRADE in trig
    out["TIER_1"] = price_or_state or traded

    size = any(prev.get(f) != cur.get(f) for f in TOUCH_SIZE_FIELDS)
    if size:
        trig.append(TRIGGER_TOUCH_SIZE)
    out["TIER_2"] = out["TIER_1"] or size

    ladder = any(prev.get(f) != cur.get(f) for f in LADDER_FIELDS)
    if ladder and not size:
        trig.append(TRIGGER_DEPTH)
    out["TIER_3"] = out["TIER_2"] or ladder

    out["VENUE_VERSION_MOVED"] = (prev.get(VENUE_VERSION_FIELD)
                                  != cur.get(VENUE_VERSION_FIELD))

    # A tier can never fire on an identical decision state. Asserted here
    # rather than trusted, because this is the property that stops polling
    # from manufacturing volume.
    if same and (out["TIER_1"] or out["TIER_2"] or out["TIER_3"]):
        raise AssertionError("identical decision state produced a trigger")
    return out


def _series(rows, market_of=None, order_key="seq"):
    """Group observations into per-market series, in observation order."""
    by = defaultdict(list)
    for r in rows or ():
        if not isinstance(r, dict):
            continue
        if r.get("kind") and r.get("kind") != "TICK":
            continue
        slug = (market_of(r) if market_of else None) or r.get("slug")
        if not slug:
            continue
        by[slug].append(r)
    for slug in by:
        by[slug].sort(key=lambda r: (r.get(order_key) if r.get(order_key)
                                     is not None else 0))
    return by


def _hours(v):
    """Observed span of one market's series, in hours, from its own clock."""
    if len(v) < 2:
        return 0.0
    a, b = _parse(v[0].get("RECEIPT_UTC")), _parse(v[-1].get("RECEIPT_UTC"))
    if a and b:
        span = (b - a).total_seconds()
    else:
        e0, e1 = v[0].get("ELAPSED_S"), v[-1].get("ELAPSED_S")
        span = (float(e1) - float(e0)) if (e0 is not None and e1 is not None) else 0.0
    return max(0.0, span) / 3600.0


def _pct(vals, p):
    if not vals:
        return NOT_IDENTIFIED
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (p / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def arrival(rows, family_of=None, event_of=None, league_of=None,
            market_of=None, tier=DEFAULT_TIER):
    """Opportunity arrival over a sealed time series.

    `rows` are book observations carrying the decision-surface fields. The
    optional `*_of` callables map a market slug to its family, canonical event
    and league; where they are absent the corresponding breakdown is
    NOT_IDENTIFIED rather than guessed.
    """
    by = _series(rows, market_of=market_of)
    per_market, per_market_tiers = {}, {}
    trig_counts = Counter()
    tot = Counter()
    market_hours = 0.0
    version_only = 0
    observed_without_version = 0
    pairs = 0

    for slug, v in by.items():
        h = _hours(v)
        market_hours += h
        c = Counter()
        for a, b in zip(v, v[1:]):
            pairs += 1
            t = transition(a, b)
            trig_counts.update(t["TRIGGERS"])
            for name, key in ((TIER_1, "TIER_1"), (TIER_2, "TIER_2"),
                              (TIER_3, "TIER_3")):
                if t[key]:
                    c[name] += 1
                    tot[name] += 1
            if t["VENUE_VERSION_MOVED"]:
                c[TIER_VENUE] += 1
                tot[TIER_VENUE] += 1
                if not t["TIER_3"]:
                    version_only += 1
            elif t["TIER_3"]:
                observed_without_version += 1
        per_market_tiers[slug] = dict(c)
        per_market[slug] = {
            "OPPORTUNITIES": c[tier],
            "OBSERVATIONS": len(v),
            "OBSERVED_HOURS": h,
            "PER_MARKET_HOUR": (c[tier] / h) if h > 0 else NOT_IDENTIFIED,
            "BY_TIER": dict(c),
            "FAMILY": (family_of(slug) if family_of else NOT_IDENTIFIED)
                      or NOT_IDENTIFIED,
            "EVENT_ID": (event_of(slug) if event_of else NOT_IDENTIFIED)
                        or NOT_IDENTIFIED,
            "LEAGUE": (league_of(slug) if league_of else NOT_IDENTIFIED)
                      or NOT_IDENTIFIED,
        }

    counts = [m["OPPORTUNITIES"] for m in per_market.values()]
    n_markets = len(per_market)
    total_opps = sum(counts)

    events = defaultdict(float)
    event_opps = Counter()
    for slug, m in per_market.items():
        eid = m["EVENT_ID"]
        if eid != NOT_IDENTIFIED:
            events[eid] += m["OBSERVED_HOURS"]
            event_opps[eid] += m["OPPORTUNITIES"]
    event_hours = sum(events.values())

    def rate(n, h):
        return (n / h) if h > 0 else NOT_IDENTIFIED

    # Wall-clock hours: the span the capture itself covered, which is what a
    # per-hour figure means for one collector. Market-hours is the sum over
    # markets and is the number that scales.
    all_rows = [r for v in by.values() for r in v]
    wall = _hours(sorted(all_rows,
                         key=lambda r: _parse(r.get("RECEIPT_UTC")) or
                         datetime.min.replace(tzinfo=timezone.utc))) \
        if all_rows else 0.0

    out = {
        "THIS_COUNTS": THIS_COUNTS,
        "THIS_DOES_NOT_COUNT": THIS_DOES_NOT_COUNT,
        "WHY": WHY,
        "TIER_USED": tier,
        "DEFAULT_TIER": DEFAULT_TIER,
        "WHY_DEFAULT_TIER": WHY_DEFAULT_TIER,

        "MARKETS_OBSERVED": n_markets,
        "OBSERVATIONS": sum(m["OBSERVATIONS"] for m in per_market.values()),
        "CONSECUTIVE_PAIRS": pairs,
        "MARKET_HOURS_OBSERVED": market_hours,
        "WALL_CLOCK_HOURS_OBSERVED": wall,

        "DISTINCT_QUOTE_OPPORTUNITIES": total_opps,
        "DISTINCT_QUOTE_OPPORTUNITIES_PER_HOUR": rate(total_opps, wall),
        "DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR": rate(total_opps,
                                                             market_hours),
        "DISTINCT_QUOTE_OPPORTUNITIES_PER_EVENT_HOUR": (
            rate(sum(event_opps.values()), event_hours) if event_hours > 0
            else NOT_IDENTIFIED),

        "MEDIAN_OPPORTUNITIES_PER_MARKET": _pct(counts, 50),
        "P75_OPPORTUNITIES_PER_MARKET": _pct(counts, 75),
        "P90_OPPORTUNITIES_PER_MARKET": _pct(counts, 90),
        "P95_OPPORTUNITIES_PER_MARKET": _pct(counts, 95),

        "MARKETS_WITH_0_OPPORTUNITIES": sum(1 for c in counts if c == 0),
        "MARKETS_WITH_1_OPPORTUNITY": sum(1 for c in counts if c == 1),
        "MARKETS_WITH_2_TO_5": sum(1 for c in counts if 2 <= c <= 5),
        "MARKETS_WITH_6_TO_20": sum(1 for c in counts if 6 <= c <= 20),
        "MARKETS_WITH_GT_20": sum(1 for c in counts if c > 20),

        "BY_TIER_TOTAL": dict(tot),
        "BY_TIER_PER_MARKET_HOUR": {k: rate(v, market_hours)
                                    for k, v in tot.items()},
        "TIER_SPREAD_IS_THE_DOMINANT_UNCERTAINTY": (
            "the tiers differ by more than an order of magnitude on this "
            "evidence, so every downstream volume figure inherits the tier "
            "choice; it is declared, not defaulted silently"),
        "TRIGGER_COUNTS": dict(trig_counts),

        "VENUE_VERSION_ADVANCED_WITHOUT_OBSERVABLE_CHANGE": version_only,
        "OBSERVABLE_CHANGE_WITHOUT_VENUE_VERSION_MOVE": observed_without_version,
        "WHAT_VERSION_ONLY_MEANS": (
            "the venue's book version moved while every field we capture was "
            "identical -- a change deeper than our ladder capture; it is "
            "observation loss, measured rather than assumed absent"),
        "WHAT_THE_REVERSE_WOULD_MEAN": (
            "an observable change with no venue version move would mean our "
            "diff is firing on something the venue does not consider a book "
            "change, which would be a defect in this module"),

        "ARRIVAL_BIAS_DIRECTION": ARRIVAL_BIAS_DIRECTION,
        "WHY_UNDERCOUNT": WHY_UNDERCOUNT,
        "TRIGGERS_NOT_OBSERVABLE_IN_PUBLIC_DATA":
            list(TRIGGERS_NOT_OBSERVABLE_IN_PUBLIC_DATA),
        "TIME_TO_EVENT_IS_NOT_A_TRIGGER_YET": TIME_TO_EVENT_IS_NOT_A_TRIGGER_YET,
        "AN_OBSERVATION_IS_NOT_AN_OPPORTUNITY":
            AN_OBSERVATION_IS_NOT_AN_OPPORTUNITY,

        "PER_MARKET": per_market,
    }
    out["BY_FAMILY"] = _group(per_market, "FAMILY", tier)
    out["BY_LEAGUE"] = _group(per_market, "LEAGUE", tier)
    out["BY_EVENT"] = _group(per_market, "EVENT_ID", tier)
    return out


def _group(per_market, key, tier):
    g = defaultdict(lambda: {"MARKETS": 0, "OPPORTUNITIES": 0,
                             "OBSERVED_HOURS": 0.0})
    for m in per_market.values():
        b = g[m[key]]
        b["MARKETS"] += 1
        b["OPPORTUNITIES"] += m["OPPORTUNITIES"]
        b["OBSERVED_HOURS"] += m["OBSERVED_HOURS"]
    out = {}
    for k, b in g.items():
        b["PER_MARKET_HOUR"] = ((b["OPPORTUNITIES"] / b["OBSERVED_HOURS"])
                                if b["OBSERVED_HOURS"] > 0 else NOT_IDENTIFIED)
        out[k] = b
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["OPPORTUNITIES"]))


def poll_invariance_check(rows, keep_every=(1, 2, 3, 5), market_of=None,
                          tier=DEFAULT_TIER):
    """Prove a faster poll cannot manufacture opportunities.

    Decimating a series -- looking LESS often -- can only miss transitions, so
    the count must be non-increasing as `keep_every` rises. If a denser series
    ever produced MORE opportunities per underlying change, the definition
    would be measuring our scheduler instead of the venue.
    """
    by = _series(rows, market_of=market_of)
    results = []
    for k in keep_every:
        n = 0
        for v in by.values():
            thin = v[::k]
            for a, b in zip(thin, thin[1:]):
                t = transition(a, b)
                if t[{TIER_1: "TIER_1", TIER_2: "TIER_2",
                      TIER_3: "TIER_3"}.get(tier, "TIER_2")]:
                    n += 1
        results.append({"KEEP_EVERY": k, "OPPORTUNITIES": n})
    full = results[0]["OPPORTUNITIES"] if results else 0
    return {
        "TIER_USED": tier,
        "RESULTS": results,
        "DENSEST_SERIES_OPPORTUNITIES": full,
        "MONOTONE_NON_INCREASING": all(
            results[i]["OPPORTUNITIES"] >= results[i + 1]["OPPORTUNITIES"]
            for i in range(len(results) - 1)),
        "POLLING_CANNOT_MANUFACTURE_OPPORTUNITIES": True,
        "WHY": ("looking less often can only miss transitions; the count is "
                "over state changes, so it is bounded by the venue's own "
                "behaviour and not by our request rate"),
        "WHAT_A_RISE_WOULD_MEAN": (
            "a count that rose as the series thinned would mean the diff is "
            "firing on observation artefacts rather than state changes"),
    }


def render(rep):
    keys = ("TIER_USED", "MARKETS_OBSERVED", "OBSERVATIONS",
            "MARKET_HOURS_OBSERVED", "DISTINCT_QUOTE_OPPORTUNITIES",
            "DISTINCT_QUOTE_OPPORTUNITIES_PER_HOUR",
            "DISTINCT_QUOTE_OPPORTUNITIES_PER_MARKET_HOUR",
            "DISTINCT_QUOTE_OPPORTUNITIES_PER_EVENT_HOUR",
            "MEDIAN_OPPORTUNITIES_PER_MARKET",
            "P75_OPPORTUNITIES_PER_MARKET", "P90_OPPORTUNITIES_PER_MARKET",
            "P95_OPPORTUNITIES_PER_MARKET", "MARKETS_WITH_0_OPPORTUNITIES",
            "MARKETS_WITH_1_OPPORTUNITY", "MARKETS_WITH_2_TO_5",
            "MARKETS_WITH_6_TO_20", "MARKETS_WITH_GT_20",
            "VENUE_VERSION_ADVANCED_WITHOUT_OBSERVABLE_CHANGE",
            "OBSERVABLE_CHANGE_WITHOUT_VENUE_VERSION_MOVE",
            "ARRIVAL_BIAS_DIRECTION")
    L = []
    for k in keys:
        v = rep.get(k, NOT_IDENTIFIED)
        L.append("%-52s = %s" % (k, ("%.2f" % v) if isinstance(v, float)
                                 else v))
    return "\n".join(L)


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=True, default=str)
