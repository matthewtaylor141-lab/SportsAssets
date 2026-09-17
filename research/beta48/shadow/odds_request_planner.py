"""Sections 1-8. The corrected billing model: plan requests, then bill them.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING PURCHASED. NOTHING REQUESTED.

THE ERROR THIS FIXES
--------------------
The previous procurement table billed

    EVENT_COUNT x MARKET_COUNT x HORIZON_COUNT

as though each target game needed its own API call. It does not. The featured
historical endpoint returns ALL events for a SPORT at a requested snapshot
time, so five Premier League matches kicking off together at 15:00 share one
request at T-2H, not five.

The billable unit is therefore

    (SPORT_KEY, REQUESTED_SNAPSHOT_TIME, MARKET_SET, REGION_SET)

deduplicated, and

    TOTAL_CREDITS = sum over unique requests of 10 x markets x regions

HOW MUCH THAT ACTUALLY SAVES
----------------------------
Less than one might hope at small N, and a great deal at large N. Measured on
4,178 real fixtures with real kickoff times across the eight target leagues:

    100 events    static  3.0% saved     dense-6h   4.4% saved
    500 events    static 10.7% saved     dense-6h  15.2% saved
  1,000 events    static 18.3% saved     dense-6h  27.4% saved
  5,000 events    static 38.4% saved     dense-6h  57.1% saved

The reason the saving grows is simple: deduplication only fires when two target
events in the SAME league share a snapshot instant. At 100 events sampled
across eight leagues and a season, collisions are rare. At full-season density
the Saturday-afternoon and Sunday-evening blocks overlap heavily, and on a
five-minute grid a 15:00 kick-off's T-2H request is the same request as a
13:00 kick-off's T-0.

So the dedup is real but it is NOT the big lever. The big levers are markets
(h2h only, not three) and regions (one, not two), which together divide the
bill by six.
"""

import datetime
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# --- Section 7. Externally verified. ---------------------------------------

HISTORICAL_CREDIT_MULTIPLIER = 10
CREDIT_FORMULA = "10 * NUMBER_OF_MARKETS * NUMBER_OF_REGIONS per historical request"
CREDIT_FORMULA_STATUS = "VERIFIED"
CREDIT_FORMULA_VERIFIED_BY = "OWNER_INDEPENDENT_CHECK_OF_PUBLIC_DOCUMENTATION"
CREDIT_FORMULA_VERIFIED_ON = "2026-09-17"

PLAN_PRICING = {
    "20K": {"CREDITS": 20000, "USD_PER_MONTH": 30},
    "100K": {"CREDITS": 100000, "USD_PER_MONTH": 59},
    "5M": {"CREDITS": 5000000, "USD_PER_MONTH": 119},
    "15M": {"CREDITS": 15000000, "USD_PER_MONTH": 249},
}
PLAN_PRICING_STATUS = "VERIFIED"
PLAN_PRICING_RETRIEVED_ON = "2026-09-17"
PLAN_PRICING_SOURCE = "OWNER_INDEPENDENT_CHECK_OF_PUBLIC_DOCUMENTATION"
PRICES_ARE_NOT_PERMANENT = (
    "recorded with a retrieval date because a vendor price list is a snapshot, "
    "not a constant. Re-check before any purchase is authorised")

HISTORICAL_START_DATE = "2020-06-06"
FIVE_MINUTE_SNAPSHOTS_FROM = "2022-09"
SELECTION_RULE = "CLOSEST_SNAPSHOT_AT_OR_EARLIER_THAN_REQUESTED_TIME"

# --- Section 1. The billable unit. -----------------------------------------

BILLABLE_UNIT = ("SPORT_KEY", "REQUESTED_SNAPSHOT_TIME", "MARKET_SET",
                 "REGION_SET")
ONE_REQUEST_MAY_COVER_MANY_EVENTS = True
DO_NOT_BILL_PER_EVENT = (
    "the featured historical endpoint returns all relevant events for a sport "
    "at the requested snapshot; billing it once per target game overstates the "
    "cost by the collision rate")


def _parse_kickoff(date_str, time_str):
    # A fixture with no kickoff time cannot be scheduled around. Defaulting it
    # to midnight would put a request on the grid at a time nobody plays, and
    # would quietly plan snapshots for a match whose clock we do not know.
    if not time_str:
        return None
    t = (str(time_str) + ":00")[:8]
    try:
        d = datetime.datetime.fromisoformat("%sT%s" % (str(date_str)[:10], t))
    except Exception:
        return None
    return d.replace(tzinfo=datetime.timezone.utc)


def static_snapshot_times(kickoff, horizons_minutes):
    return [kickoff - datetime.timedelta(minutes=m) for m in horizons_minutes]


def dense_snapshot_times(kickoff, window_minutes, step_minutes=5):
    """The 5-minute grid from T-window through T-0, inclusive."""
    n = int(window_minutes // step_minutes)
    return [kickoff - datetime.timedelta(minutes=window_minutes - i * step_minutes)
            for i in range(n + 1)]


def plan_requests(events, horizons_minutes=None, dense_window_minutes=None,
                  step_minutes=5, markets=("h2h",), regions=("uk",),
                  sport_key_of=None):
    """Build the DEDUPLICATED request set and bill it.

    `events` are dicts with DATE, TIME and a league. Events whose kickoff
    cannot be parsed are REFUSED and counted -- a fixture with no clock cannot
    be scheduled around, and silently dropping it would understate the plan.
    """
    sport_key_of = sport_key_of or (lambda e: e.get("VENUE_LEAGUE") or
                                    e.get("LEAGUE") or NOT_IDENTIFIED)
    unique = set()
    naive = 0
    unparsed = 0
    by_sport = defaultdict(set)
    for e in events or ():
        ko = _parse_kickoff(e.get("DATE"), e.get("TIME"))
        if ko is None:
            unparsed += 1
            continue
        sk = sport_key_of(e)
        if dense_window_minutes:
            stamps = dense_snapshot_times(ko, dense_window_minutes, step_minutes)
        else:
            stamps = static_snapshot_times(ko, horizons_minutes or [])
        naive += len(stamps)
        for t in stamps:
            unique.add((sk, t.isoformat()))
            by_sport[sk].add(t.isoformat())
    per_request = HISTORICAL_CREDIT_MULTIPLIER * len(markets) * len(regions)
    saved = (100.0 * (1 - len(unique) / naive)) if naive else 0.0
    return {
        "EVENTS_PLANNED": len(events or ()) - unparsed,
        "EVENTS_REFUSED_NO_KICKOFF": unparsed,
        "MARKETS": tuple(markets),
        "REGIONS": tuple(regions),
        "CREDITS_PER_REQUEST": per_request,
        "NAIVE_EVENT_MULTIPLICATION_REQUESTS": naive,
        "NAIVE_EVENT_MULTIPLICATION_CREDITS": naive * per_request,
        "DEDUPED_API_REQUESTS": len(unique),
        "DEDUPED_API_REQUEST_CREDITS": len(unique) * per_request,
        "DEDUPLICATION_SAVINGS_PCT": saved,
        "UNIQUE_REQUESTS_BY_SPORT": {k: len(v) for k, v in by_sport.items()},
        "BILLABLE_UNIT": BILLABLE_UNIT,
        "CREDIT_FORMULA": CREDIT_FORMULA,
        "CREDIT_FORMULA_STATUS": CREDIT_FORMULA_STATUS,
    }


def recommend_plan(credits_required):
    """Smallest verified tier that covers the requirement, with headroom."""
    tiers = sorted(PLAN_PRICING.items(), key=lambda kv: kv[1]["CREDITS"])
    for name, t in tiers:
        if t["CREDITS"] >= credits_required:
            return {
                "RECOMMENDED_PLAN": name,
                "PLAN_CREDITS": t["CREDITS"],
                "ESTIMATED_COST_USD_PER_MONTH": t["USD_PER_MONTH"],
                "CREDITS_REQUIRED": credits_required,
                "HEADROOM_PCT": 100.0 * (t["CREDITS"] - credits_required)
                / max(credits_required, 1),
                "PRICING_STATUS": PLAN_PRICING_STATUS,
                "PRICING_RETRIEVED_ON": PLAN_PRICING_RETRIEVED_ON,
                "PRICES_ARE_NOT_PERMANENT": PRICES_ARE_NOT_PERMANENT,
            }
    return {"RECOMMENDED_PLAN": "ABOVE_THE_LARGEST_LISTED_TIER",
            "CREDITS_REQUIRED": credits_required}


# --- Sections 3-5. Two experiments, not one. -------------------------------

STATIC_CONSENSUS_HORIZONS_MINUTES = (24 * 60, 120, 15)
STATIC_QUESTION = (
    "does external consensus improve settlement / fair value at selected "
    "decision points?")

DENSE_WINDOWS_MINUTES = {"T-2H": 120, "T-6H": 360}
DENSE_QUESTION = (
    "does external consensus LEAD the venue, and at what horizon?")

WHY_THEY_ARE_SEPARATE = (
    "three snapshots per event cannot estimate 5, 15, 30 and 60 minute "
    "following dynamics. A lead/lag experiment needs a time series; a static "
    "consensus experiment needs a few well-chosen decision points. Running "
    "them as one design would either overpay for the static question or "
    "underpower the dynamic one")

PHASE_1_MARKETS = ("h2h",)
PHASE_1_REGIONS_COUNT = 1
WHY_PHASE_1_IS_NARROW = (
    "spreads and totals triple the bill, and a second region doubles it again. "
    "Neither is worth buying before any external lead/lag value has been "
    "demonstrated at all. If h2h in one region shows a lead, expansion is "
    "justified on evidence; if it does not, we learned it for a sixth of the "
    "price")

# --- Section 6. The region decision. ---------------------------------------

REGION_CANDIDATES = ("uk", "eu", "us")
INITIAL_REGION_RECOMMENDATION = "uk"
REGION_RECOMMENDATION_STATUS = "PROVISIONAL_NOT_MEASURED"
BOOKMAKERS_IN_REGION = NOT_IDENTIFIED
WHY_NOT_MEASURED = (
    "the per-region bookmaker list is an API call, and the provider hosts are "
    "egress-blocked from this environment. No count is asserted")
HOW_TO_SETTLE_IT_CHEAPLY = (
    "the /sports and current-odds endpoints are free or near-free relative to "
    "historical calls. On the day a credential exists, one current-odds call "
    "per candidate region on one target league returns the bookmaker list, and "
    "the region choice becomes a measurement rather than a habit. Do this "
    "BEFORE spending historical credits")
WHY_UK_PROVISIONALLY = (
    "the eight evaluated leagues are European and UK-listed books price them "
    "as primary markets rather than as overnight exotics. That is a reason to "
    "test uk first, not a reason to skip the test")
DO_NOT_BUY_TWO_REGIONS_BY_DEFAULT = (
    "a second region doubles every historical request. It must earn its place "
    "by adding genuinely different information, which is itself measurable "
    "once one region's data is in hand")


def describe():
    return {
        "HISTORICAL_CREDIT_MULTIPLIER": HISTORICAL_CREDIT_MULTIPLIER,
        "CREDIT_FORMULA": CREDIT_FORMULA,
        "CREDIT_FORMULA_STATUS": CREDIT_FORMULA_STATUS,
        "CREDIT_FORMULA_VERIFIED_ON": CREDIT_FORMULA_VERIFIED_ON,
        "PLAN_PRICING": {k: dict(v) for k, v in PLAN_PRICING.items()},
        "PLAN_PRICING_STATUS": PLAN_PRICING_STATUS,
        "PLAN_PRICING_RETRIEVED_ON": PLAN_PRICING_RETRIEVED_ON,
        "PRICES_ARE_NOT_PERMANENT": PRICES_ARE_NOT_PERMANENT,
        "BILLABLE_UNIT": BILLABLE_UNIT,
        "DO_NOT_BILL_PER_EVENT": DO_NOT_BILL_PER_EVENT,
        "STATIC_QUESTION": STATIC_QUESTION,
        "DENSE_QUESTION": DENSE_QUESTION,
        "WHY_THEY_ARE_SEPARATE": WHY_THEY_ARE_SEPARATE,
        "PHASE_1_MARKETS": PHASE_1_MARKETS,
        "WHY_PHASE_1_IS_NARROW": WHY_PHASE_1_IS_NARROW,
        "INITIAL_REGION_RECOMMENDATION": INITIAL_REGION_RECOMMENDATION,
        "REGION_RECOMMENDATION_STATUS": REGION_RECOMMENDATION_STATUS,
        "BOOKMAKERS_IN_REGION": BOOKMAKERS_IN_REGION,
        "HOW_TO_SETTLE_IT_CHEAPLY": HOW_TO_SETTLE_IT_CHEAPLY,
        "NOTHING_IS_PURCHASED": True,
    }
