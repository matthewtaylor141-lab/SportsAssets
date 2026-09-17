"""Sections 6, 7, 15, 16. The prospective capture-aligned pilot, and the gate.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING PURCHASED. NOTHING REQUESTED.

THE KEY PROPERTY THAT MAKES THIS CHEAP AND CLEAN
------------------------------------------------
Historical external snapshots can be retrieved AFTER the fact. So the venue
capture does not need an odds credential running alongside it. Capture first,
persist exact venue timestamps, and only then compute which external snapshots
are worth buying -- by which point the request set is known exactly rather than
estimated.

That inverts the usual procurement risk. Instead of buying data in the hope the
experiment materialises, the experiment materialises first and the purchase is
sized to it.

WHY THIS COHORT IS BETTER THAN THE HISTORICAL ONE
-------------------------------------------------
The historical venue series is RN1-trade-triggered: observations exist because a
whale acted. The capture samples on a CLOCK, not on somebody's trading. Its
timestamps are a grid, so the external join has no selection on the venue side
at all -- which is exactly what the historical cohort cannot offer.
"""

import datetime
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

PILOT_NAME = "PROSPECTIVE_CAPTURE_ALIGNED_PILOT"
STATUS = "READY_AWAITING_CAPTURE"

EXTERNAL_MAY_BE_BACKFILLED_AFTER_THE_FACT = True
NO_ODDS_CREDENTIAL_NEEDED_DURING_CAPTURE = (
    "historical snapshots are retrievable later, so the capture runs alone and "
    "the purchase is sized after its timestamps exist")

CAPTURE_SIDE_HAS_NO_TRADE_SELECTION = (
    "the capture samples on a clock. Unlike the RN1-triggered historical "
    "series, its timestamps do not exist because somebody traded")

FORWARD_HORIZONS_SECONDS = (5, 30, 60, 300)

STATE_OBJECTS = ("POLY_STATE_T", "EXTERNAL_STATE_T_OR_BEFORE") + \
    tuple("POLY_STATE_T_PLUS_%dS" % s for s in FORWARD_HORIZONS_SECONDS)

WHERE_NATIVE_CAPTURE_SUPPORTS_THE_HORIZON = (
    "a forward state is built only where the capture actually has one. At a "
    "4-second sampling interval the 5s and 30s horizons are dense and the 300s "
    "horizon truncates near the end of the run. Truncation is declared, never "
    "filled")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def bucket(t, minutes=5):
    t = _parse(t)
    if t is None:
        return None
    return t.replace(minute=(t.minute // minutes) * minutes, second=0,
                     microsecond=0)


def plan_from_capture(tick_rows, sport_key_of=None, markets=("h2h",),
                      regions=("uk",), bucket_minutes=5, multiplier=10,
                      time_key="REQUEST_UTC"):
    """Section 7. The exact external request set implied by a finished capture.

    Deduplicated by (SPORT, SNAPSHOT_BUCKET, MARKETS, REGION). A capture that
    samples every four seconds for ninety minutes produces at most 18 distinct
    five-minute buckets per sport, however many ticks it wrote -- which is why
    this is cheap even at high tick density.
    """
    sport_key_of = sport_key_of or (lambda r: r.get("LEAGUE")
                                    or r.get("SPORT") or NOT_IDENTIFIED)
    req = set()
    ticks = 0
    unparsed = 0
    by_sport = defaultdict(set)
    span = []
    for r in tick_rows or ():
        t = _parse(r.get(time_key))
        if t is None:
            unparsed += 1
            continue
        ticks += 1
        span.append(t)
        sk = sport_key_of(r)
        b = bucket(t, bucket_minutes)
        req.add((sk, b.isoformat()))
        by_sport[sk].add(b.isoformat())
    per_request = multiplier * len(markets) * len(regions)
    return {
        "CAPTURE_TICKS": ticks,
        "TICKS_WITH_UNREADABLE_TIME": unparsed,
        "CAPTURE_SPAN": ((min(span).isoformat(), max(span).isoformat())
                         if span else NOT_IDENTIFIED),
        "CAPTURE_EXTERNAL_REQUEST_COUNT": len(req),
        "CAPTURE_EXTERNAL_CREDITS": len(req) * per_request,
        "CAPTURE_EXTERNAL_EXPECTED_COST": NOT_IDENTIFIED,
        "REQUESTS_BY_SPORT": {k: len(v) for k, v in by_sport.items()},
        "CREDITS_PER_REQUEST": per_request,
        "BUCKET_MINUTES": bucket_minutes,
        "COST_IS_RESOLVED_BY": "recommend_plan on the credits figure",
        "NOTHING_IS_PURCHASED": True,
    }


# --- Section 16. The purchase gate. ----------------------------------------

PURCHASE_CONDITIONS = {
    "A": ("the historical matched-static cohort is defined with enough usable "
          "event N that a small purchase answers a SPECIFIC question"),
    "B": ("the continuous capture completes and the exact timestamp-aligned "
          "external request plan is known"),
}

PURCHASE_RULE = (
    "buy the SMALLEST plan that covers the valid experiment with reasonable "
    "buffer. Do not buy 100K merely because it is inexpensive")


def purchase_gate(historical_cohort_events=None, capture_completed=False,
                  capture_plan=None, min_useful_events=100):
    """May data be purchased yet? Returns a verdict and the reason.

    Condition A is deliberately strict: a cohort exists, but 'enough usable
    event N that a small purchase answers a specific question' is a higher bar
    than 'a cohort exists'.
    """
    a = (historical_cohort_events is not None
         and historical_cohort_events >= min_useful_events)
    b = bool(capture_completed and capture_plan)
    return {
        "CONDITION_A_MET": a,
        "CONDITION_A": PURCHASE_CONDITIONS["A"],
        "CONDITION_A_DETAIL": {
            "COHORT_EVENTS": historical_cohort_events,
            "MINIMUM_FOR_A_SPECIFIC_QUESTION": min_useful_events,
            "WHAT_THE_COHORT_CAN_ANSWER": (
                "a descriptive question about external consensus on "
                "RN1-selected states -- NOT a settlement result"),
        },
        "CONDITION_B_MET": b,
        "CONDITION_B": PURCHASE_CONDITIONS["B"],
        "MAY_PURCHASE": bool(a or b),
        "PURCHASE_RULE": PURCHASE_RULE,
        "RECOMMENDED_ACTION": (
            "wait for the capture" if not (a or b) else
            "size the smallest covering plan and put it to management"),
    }


# --- Section 15. The harvest decision tree, in order. ----------------------

HARVEST_ORDER = (
    ("1_INTEGRITY", "integrity / identity / observation-loss checks"),
    ("2_TRANSITIONS", "measure transition classes"),
    ("3_BASELINES", "build simple microstructure baselines"),
    ("4_COMPLEX", "test more complex short-horizon models"),
    ("5_CROSS_MARKET", "assess whether cross-market density supports "
                       "relative-value analysis"),
    ("6_EXTERNAL_PLAN", "compute the exact external-odds backfill request set"),
)

DO_NOT_REVERSE_THE_ORDER = (
    "a later step being more interesting is not a reason to run it first. "
    "Step 3 exists so that step 4 has something to beat, and step 1 exists so "
    "that neither is run on data that failed its own integrity checks")


def harvest_state(completed_steps=()):
    done = set(completed_steps or ())
    nxt = None
    for key, what in HARVEST_ORDER:
        if key not in done:
            nxt = {"STEP": key, "WHAT": what}
            break
    return {
        "ORDER": [{"STEP": k, "WHAT": w} for k, w in HARVEST_ORDER],
        "COMPLETED": sorted(done),
        "NEXT_STEP": nxt or "ALL_COMPLETE",
        "DO_NOT_REVERSE_THE_ORDER": DO_NOT_REVERSE_THE_ORDER,
    }


def describe():
    return {
        "PILOT_NAME": PILOT_NAME,
        "STATUS": STATUS,
        "EXTERNAL_MAY_BE_BACKFILLED_AFTER_THE_FACT":
            EXTERNAL_MAY_BE_BACKFILLED_AFTER_THE_FACT,
        "NO_ODDS_CREDENTIAL_NEEDED_DURING_CAPTURE":
            NO_ODDS_CREDENTIAL_NEEDED_DURING_CAPTURE,
        "CAPTURE_SIDE_HAS_NO_TRADE_SELECTION": CAPTURE_SIDE_HAS_NO_TRADE_SELECTION,
        "STATE_OBJECTS": STATE_OBJECTS,
        "PURCHASE_CONDITIONS": dict(PURCHASE_CONDITIONS),
        "PURCHASE_RULE": PURCHASE_RULE,
        "HARVEST_ORDER": [{"STEP": k, "WHAT": w} for k, w in HARVEST_ORDER],
        "NOTHING_IS_PURCHASED": True,
    }
