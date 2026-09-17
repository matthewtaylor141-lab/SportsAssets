"""Sections 1-5. The HISTORICAL MATCHED-STATIC pilot. Poly first, odds second.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING PURCHASED. NOTHING REQUESTED.

THE JOIN DIRECTION IS THE WHOLE POINT
-------------------------------------
The previous plan picked attractive horizons -- T-24H, T-2H, T-15M -- and hoped
a venue observation existed there. Measured, one mostly does not: the historical
series has a median of 3 observations per market slug and a forward point at
+60m only 9.3% of the time.

So the join is reversed:

    SELECT the Poly observation FIRST
    THEN request the external snapshot at or before that instant

    KNOWN POLY STATE AT T  +  EXTERNAL STATE KNOWN BY T

not

    EXTERNAL HORIZON T  +  hope a Poly state exists there

Every external request in this pilot is therefore anchored to an observation we
already hold. Nothing is bought speculatively.

WHY IT IS CHEAP, AND WHY THAT IS NOT THE POINT
----------------------------------------------
The provider returns the closest snapshot at or before the requested time, so
two Poly observations falling in the same five-minute bucket of the same league
resolve to ONE external request. Anchoring to real observations also caps the
pilot at the corpus: 222 settled soccer events carry at least one observation,
so 500 target events is not available and the honest number is 222.

At one observation per event the whole cohort costs about 2,000 credits. That
is inside the smallest tier. The constraint on this pilot is not money; it is
that 222 selected events cannot answer a question that needs thousands.
"""

import datetime
import hashlib
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"

COHORT_NAME = "HISTORICAL_MATCHED_STATIC_COHORT"

JOIN_DIRECTION = "POLY_OBSERVATION_FIRST_THEN_EXTERNAL_AT_OR_BEFORE"
WHY = (
    "an external snapshot at a horizon where no venue state exists is an "
    "unmatched row; buying it is buying nothing")

# --- Section 3. Frozen deterministic selection rules. ----------------------

SELECTION_RULES = ("EARLIEST_ELIGIBLE_POLY_OBSERVATION",
                   "MEDIAN_ELIGIBLE_POLY_OBSERVATION",
                   "LATEST_ELIGIBLE_POLY_OBSERVATION")

PRINCIPAL_DESIGN = "ONE_OBSERVATION_PER_INDEPENDENT_EVENT"
SENSITIVITY_DESIGN = "THREE_OBSERVATIONS_PER_EVENT"

WHY_ONE_PER_EVENT = (
    "the observation density is wildly uneven -- median 3 per market slug, max "
    "1,051. Taking every observation would let a handful of heavily-traded "
    "events supply most of the sample and call it an event-level result")

SELECTION_IS_FROZEN_BEFORE_OUTCOMES = True
SELECTION_MAY_NOT_LOOK_AT = ("SETTLEMENT", "SUBSEQUENT_PRICE_MOVEMENT",
                             "THE_EXTERNAL_PRICE")
WHY_FROZEN = (
    "a rule chosen after seeing which observations happened to sit near a "
    "favourable external price is not a rule, it is a result")

# --- Section 5. What this sample is, stated once and repeated. -------------

HISTORICAL_STATIC_SAMPLE_STATUS = "SELECTED_RN1_TRIGGERED"

WHAT_IT_CAN_ANSWER = (
    "does external consensus appear useful on the market states RN1 happened "
    "to trade?")
WHAT_IT_CANNOT_PROVE = (
    "general market-wide external-consensus alpha. The observations exist "
    "because a whale acted, so the sample is selected on a variable plausibly "
    "correlated with mispricing")
THIS_DISTINCTION_GOES_IN_EVERY_REPORT = True

# --- Pregame status: labelled, never invented. -----------------------------

PREGAME_CLASSIFICATION = "CANNOT_BE_PROVEN_START_TIME_CLASS_A_IS_EMPTY"
TIME_LABEL_POLICY = (
    "an observation is labelled by the best defensible class -- "
    "EXTERNAL_CROSS_VALIDATED_SHARED_UPSTREAM at best, APPROXIMATE otherwise. "
    "The LATEST rule is therefore NOT called 'latest pregame'; it is called "
    "latest eligible, because pregame status is not provable here")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def snapshot_bucket(t, minutes=5):
    """Floor to the provider's snapshot grid.

    The provider returns the closest snapshot at or BEFORE the request, so two
    observations in the same bucket resolve to the same underlying snapshot and
    cost one request between them. Flooring is what makes that explicit.
    """
    t = _parse(t)
    if t is None:
        return None
    return t.replace(minute=(t.minute // minutes) * minutes, second=0,
                     microsecond=0)


def select_observations(observations_by_event, rule=SELECTION_RULES[0],
                        per_event=1):
    """Apply a frozen rule. Deterministic, outcome-blind, tie-broken by hash.

    `observations_by_event` maps event key -> [(timestamp, league), ...].
    """
    if rule not in SELECTION_RULES:
        return {}, {"STATUS": "UNKNOWN_RULE", "RULE": rule,
                    "DECLARED": SELECTION_RULES}
    out, refused = {}, defaultdict(int)
    for ev, obs in (observations_by_event or {}).items():
        usable = sorted([(t, lg) for t, lg in
                         ((_parse(a), b) for a, b in obs) if t])
        if not usable:
            refused["NO_PARSEABLE_OBSERVATION"] += 1
            continue
        if per_event == 1:
            if rule.startswith("EARLIEST"):
                pick = [usable[0]]
            elif rule.startswith("MEDIAN"):
                pick = [usable[len(usable) // 2]]
            else:
                pick = [usable[-1]]
        else:
            if len(usable) < per_event:
                refused["FEWER_THAN_%d_OBSERVATIONS" % per_event] += 1
                continue
            idx = [0, len(usable) // 2, len(usable) - 1][:per_event]
            pick = [usable[i] for i in sorted(set(idx))]
        out[ev] = pick
    return out, {"EVENTS_SELECTED": len(out), "REFUSED": dict(refused),
                 "RULE": rule, "PER_EVENT": per_event,
                 "SELECTION_IS_FROZEN_BEFORE_OUTCOMES":
                     SELECTION_IS_FROZEN_BEFORE_OUTCOMES,
                 "SELECTION_MAY_NOT_LOOK_AT": SELECTION_MAY_NOT_LOOK_AT}


def plan_external_requests(selected, markets=("h2h",), regions=("uk",),
                           bucket_minutes=5, multiplier=10):
    """Deduplicated external requests anchored on the selected Poly times."""
    req = set()
    stamps = 0
    for ev, picks in (selected or {}).items():
        for t, lg in picks:
            b = snapshot_bucket(t, bucket_minutes)
            if b is None:
                continue
            stamps += 1
            req.add((lg, b.isoformat()))
    per_request = multiplier * len(markets) * len(regions)
    credits = len(req) * per_request
    events = len(selected or {})
    return {
        "POLY_TIMESTAMPS_SELECTED": stamps,
        "UNIQUE_EXTERNAL_REQUESTS": len(req),
        "CREDITS": credits,
        "EXPECTED_MATCHED_ROWS": stamps,
        "CREDITS_PER_MATCHED_EVENT": (credits / events) if events else
            NOT_IDENTIFIED,
        "EVENTS": events,
        "MARKETS": tuple(markets),
        "REGIONS": tuple(regions),
        "CREDITS_PER_REQUEST": per_request,
        "BUCKET_MINUTES": bucket_minutes,
        "SAMPLE_STATUS": HISTORICAL_STATIC_SAMPLE_STATUS,
    }


def cohort_digest(selected):
    """Stable hash of the frozen cohort, so the selection can be checked."""
    h = hashlib.sha256()
    for ev in sorted(selected or {}):
        for t, lg in selected[ev]:
            h.update(("%s|%s|%s\n" % (ev, _parse(t).isoformat(), lg)).encode())
    return h.hexdigest()


# --- Measured on the real corpus. ------------------------------------------

MEASURED_UNIVERSE = {
    "SOCCER_EVENTS_WITH_AT_LEAST_ONE_OBSERVATION": 222,
    "AND_SETTLED": 222,
    "OBSERVATIONS_PER_EVENT_MEDIAN": 86,
    "OBSERVATIONS_PER_EVENT_P90": 321,
    "OBSERVATIONS_PER_EVENT_MAX": 1051,
    "THE_CAP_IS_THE_CORPUS": (
        "500 target events is not available. The universe is 222, so the "
        "500-event line in any cost table is the 222-event line"),
}

MEASURED_COST = {
    "EARLIEST": {100: {"REQUESTS": 90, "CREDITS": 900},
                 250: {"REQUESTS": 203, "CREDITS": 2030},
                 500: {"REQUESTS": 203, "CREDITS": 2030}},
    "MEDIAN": {100: {"REQUESTS": 100, "CREDITS": 1000},
               250: {"REQUESTS": 210, "CREDITS": 2100},
               500: {"REQUESTS": 210, "CREDITS": 2100}},
    "LATEST": {100: {"REQUESTS": 91, "CREDITS": 910},
               250: {"REQUESTS": 193, "CREDITS": 1930},
               500: {"REQUESTS": 193, "CREDITS": 1930}},
    "NOTE": ("h2h, one region, one observation per event, 5-minute buckets. "
             "The 250 and 500 rows are identical because the universe caps at "
             "222 events"),
}

WHAT_THE_CHEAPNESS_MEANS = (
    "about 2,000 credits buys the entire matched historical cohort, which sits "
    "inside the smallest tier. Money is not the constraint on this pilot. The "
    "constraint is that 222 RN1-selected events cannot answer a question whose "
    "incremental ladder needs thousands, so the pilot's honest output is a "
    "description of external consensus on the states RN1 traded -- not a "
    "settlement result")


def describe():
    return {
        "COHORT_NAME": COHORT_NAME,
        "JOIN_DIRECTION": JOIN_DIRECTION,
        "WHY": WHY,
        "SELECTION_RULES": SELECTION_RULES,
        "PRINCIPAL_DESIGN": PRINCIPAL_DESIGN,
        "SENSITIVITY_DESIGN": SENSITIVITY_DESIGN,
        "WHY_ONE_PER_EVENT": WHY_ONE_PER_EVENT,
        "SELECTION_IS_FROZEN_BEFORE_OUTCOMES":
            SELECTION_IS_FROZEN_BEFORE_OUTCOMES,
        "HISTORICAL_STATIC_SAMPLE_STATUS": HISTORICAL_STATIC_SAMPLE_STATUS,
        "WHAT_IT_CAN_ANSWER": WHAT_IT_CAN_ANSWER,
        "WHAT_IT_CANNOT_PROVE": WHAT_IT_CANNOT_PROVE,
        "PREGAME_CLASSIFICATION": PREGAME_CLASSIFICATION,
        "TIME_LABEL_POLICY": TIME_LABEL_POLICY,
        "MEASURED_UNIVERSE": dict(MEASURED_UNIVERSE),
        "MEASURED_COST": {k: v for k, v in MEASURED_COST.items()},
        "WHAT_THE_CHEAPNESS_MEANS": WHAT_THE_CHEAPNESS_MEANS,
        "NOTHING_IS_PURCHASED": True,
    }
