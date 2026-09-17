"""Sections 3 and 4. External market information, aligned as-of the decision.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.
NOTHING PURCHASED. NOTHING REQUESTED. This module builds the alignment and the
request PLAN; it never calls a provider.

THE ONE INVARIANT
-----------------
    EXTERNAL_SNAPSHOT_TIMESTAMP <= DECISION_TIMESTAMP

No future odds. A consensus formed after the instant being scored is not
information the decision could have used; joining it would manufacture a
result out of hindsight. The check is a REFUSAL, not a trim: a violating row
is dropped and counted, never silently moved back in time.

WHY DE-VIGGED AND RAW ARE BOTH KEPT
-----------------------------------
The raw consensus is what the market quoted, overround included. The de-vigged
consensus is an estimate of the underlying probability, and it depends on the
de-vig METHOD -- which is an assumption. Keeping both means a later reader can
see whether a result survived the assumption or depended on it.
"""

import datetime

NOT_IDENTIFIED = "NOT_IDENTIFIED"
MISSING = "MISSING"

NOTHING_IS_PURCHASED = True
THIS_MODULE_CONTACTS_NOTHING = True

THE_INVARIANT = "EXTERNAL_SNAPSHOT_TIMESTAMP <= DECISION_TIMESTAMP"
NO_FUTURE_ODDS = (
    "a consensus formed after the instant being scored is not information the "
    "decision could have used. Joining it manufactures a result out of "
    "hindsight")
VIOLATION_IS_A_REFUSAL = (
    "a violating row is DROPPED and COUNTED. It is never re-stamped, trimmed "
    "or nudged backwards to make the join work")

EXTERNAL_FIELDS = (
    "EXTERNAL_SNAPSHOT_TIMESTAMP",
    "EXTERNAL_AGE_SECONDS",
    "BOOKMAKER_COUNT",
    "EXTERNAL_CONSENSUS_RAW",
    "EXTERNAL_CONSENSUS_DEVIGGED",
    "EXTERNAL_DISPERSION",
    "POLY_MINUS_EXTERNAL",
    "EXTERNAL_MOVE_5M", "EXTERNAL_MOVE_15M",
    "EXTERNAL_MOVE_30M", "EXTERNAL_MOVE_60M",
)

EXTERNAL_MOVE_WINDOWS_MIN = (5, 15, 30, 60)

DEVIG_METHODS = ("MULTIPLICATIVE", "ADDITIVE", "SHIN", "POWER")
DEVIG_METHOD_IS_AN_ASSUMPTION = (
    "de-vigging requires a model of how the overround is distributed across "
    "outcomes. The method is recorded on every row so a later reader can see "
    "whether a result survived the assumption or depended on it")

# Declared before any external data exists.
MAX_EXTERNAL_AGE_S = 3600.0
WHY_AN_AGE_CAP = (
    "an external snapshot four hours stale is not 'the external consensus at "
    "the decision', it is a different question. Rows beyond the cap are "
    "labelled, not silently used")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _num(v):
    if v in (None, NOT_IDENTIFIED, MISSING, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validate_asof(decision_ts, external_ts):
    """The invariant, enforced. Returns a verdict, never a corrected time."""
    d, e = _parse(decision_ts), _parse(external_ts)
    if d is None or e is None:
        return {"OK": False, "REASON": "UNREADABLE_TIMESTAMP",
                "THE_INVARIANT": THE_INVARIANT}
    if e > d:
        return {"OK": False, "REASON": "REFUSAL_FUTURE_EXTERNAL_SNAPSHOT",
                "EXTERNAL_AHEAD_BY_S": (e - d).total_seconds(),
                "NO_FUTURE_ODDS": NO_FUTURE_ODDS,
                "VIOLATION_IS_A_REFUSAL": VIOLATION_IS_A_REFUSAL,
                "THE_INVARIANT": THE_INVARIANT}
    return {"OK": True, "EXTERNAL_AGE_SECONDS": (d - e).total_seconds()}


def devig(prices, method="MULTIPLICATIVE"):
    """Remove the overround. The method travels with the answer."""
    if method not in DEVIG_METHODS:
        return {"DEVIGGED": MISSING, "REASON": "UNKNOWN_METHOD",
                "DECLARED": DEVIG_METHODS}
    vals = [_num(p) for p in (prices or ())]
    vals = [v for v in vals if v is not None and v > 0]
    if len(vals) < 2:
        return {"DEVIGGED": MISSING, "REASON": "FEWER_THAN_TWO_OUTCOMES"}
    total = sum(vals)
    if total <= 0:
        return {"DEVIGGED": MISSING, "REASON": "NON_POSITIVE_TOTAL"}
    if method == "MULTIPLICATIVE":
        out = [v / total for v in vals]
    elif method == "ADDITIVE":
        adj = (total - 1.0) / len(vals)
        out = [max(v - adj, 0.0) for v in vals]
        s = sum(out)
        out = [v / s for v in out] if s > 0 else out
    else:
        # SHIN and POWER need an iterative solve that is not implemented here.
        return {"DEVIGGED": MISSING, "REASON": "METHOD_NOT_IMPLEMENTED",
                "METHOD": method,
                "WHY": "declared as admissible but not yet built; a wrong "
                       "implementation would be worse than an absent one"}
    return {"DEVIGGED": [round(v, 10) for v in out], "METHOD": method,
            "OVERROUND": round(total - 1.0, 10),
            "DEVIG_METHOD_IS_AN_ASSUMPTION": DEVIG_METHOD_IS_AN_ASSUMPTION}


def align(decision_row, external_snapshot=None, history=(),
          devig_method="MULTIPLICATIVE", max_age_s=MAX_EXTERNAL_AGE_S):
    """Attach the external block to one decision row, or say why not.

    `history` is prior external snapshots for the same market, used for the
    move windows. Every one of them is subject to the same invariant.
    """
    out = {f: MISSING for f in EXTERNAL_FIELDS}
    out["EXTERNAL_ALIGNMENT_STATUS"] = "NO_EXTERNAL_DATA"
    out["THE_INVARIANT"] = THE_INVARIANT
    if not external_snapshot:
        out["WHY"] = ("no external snapshot supplied. No credential exists "
                      "and nothing has been purchased")
        return out

    d_ts = decision_row.get("DECISION_TIMESTAMP_UTC")
    v = validate_asof(d_ts, external_snapshot.get("SNAPSHOT_TIMESTAMP"))
    if not v["OK"]:
        out["EXTERNAL_ALIGNMENT_STATUS"] = v["REASON"]
        out["WHY"] = v.get("NO_FUTURE_ODDS", v["REASON"])
        return out

    age = v["EXTERNAL_AGE_SECONDS"]
    out["EXTERNAL_SNAPSHOT_TIMESTAMP"] = _parse(
        external_snapshot["SNAPSHOT_TIMESTAMP"]).isoformat()
    out["EXTERNAL_AGE_SECONDS"] = round(age, 3)
    out["EXTERNAL_AGE_EXCEEDS_CAP"] = age > max_age_s
    out["MAX_EXTERNAL_AGE_S"] = max_age_s

    prices = external_snapshot.get("BOOKMAKER_PRICES") or []
    out["BOOKMAKER_COUNT"] = len(prices) if prices else MISSING
    vals = [_num(p) for p in prices]
    vals = [x for x in vals if x is not None]
    if vals:
        mean = sum(vals) / len(vals)
        out["EXTERNAL_CONSENSUS_RAW"] = round(mean, 10)
        if len(vals) > 1:
            var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
            out["EXTERNAL_DISPERSION"] = round(var ** 0.5, 10)
        dv = devig(external_snapshot.get("OUTCOME_PRICES") or [], devig_method)
        if dv.get("DEVIGGED") != MISSING:
            out["EXTERNAL_CONSENSUS_DEVIGGED"] = dv["DEVIGGED"][0]
            out["DEVIG_METHOD"] = dv["METHOD"]
        poly = _num(decision_row.get("MID"))
        base = out.get("EXTERNAL_CONSENSUS_DEVIGGED")
        if base == MISSING:
            base = out["EXTERNAL_CONSENSUS_RAW"]
        if poly is not None and isinstance(base, float):
            out["POLY_MINUS_EXTERNAL"] = round(poly - base, 10)

    now = _parse(out["EXTERNAL_SNAPSHOT_TIMESTAMP"])
    cur = out.get("EXTERNAL_CONSENSUS_RAW")
    for w in EXTERNAL_MOVE_WINDOWS_MIN:
        key = "EXTERNAL_MOVE_%dM" % w
        if not isinstance(cur, float) or now is None:
            continue
        target = now - datetime.timedelta(minutes=w)
        prior, gap = None, None
        for h in history or ():
            t = _parse(h.get("SNAPSHOT_TIMESTAMP"))
            if t is None or t > now:
                continue                       # the invariant, again
            g = abs((t - target).total_seconds())
            if g <= 300 and (gap is None or g < gap):
                prior, gap = h, g
        pv = _num((prior or {}).get("CONSENSUS_RAW"))
        out[key] = round(cur - pv, 10) if pv is not None else MISSING
    out["EXTERNAL_ALIGNMENT_STATUS"] = "ALIGNED"
    return out


def align_many(rows, snapshots_by_market=None, devig_method="MULTIPLICATIVE"):
    """Align a batch and COUNT the refusals rather than hiding them."""
    snapshots_by_market = snapshots_by_market or {}
    aligned, refused, no_data = 0, 0, 0
    reasons = {}
    out = []
    for r in rows or ():
        snaps = snapshots_by_market.get(r.get("MARKET_ID")) or []
        d = _parse(r.get("DECISION_TIMESTAMP_UTC"))
        usable = [s for s in snaps
                  if _parse(s.get("SNAPSHOT_TIMESTAMP")) is not None
                  and _parse(s.get("SNAPSHOT_TIMESTAMP")) <= d] if d else []
        best = max(usable, key=lambda s: _parse(s["SNAPSHOT_TIMESTAMP"])) \
            if usable else None
        blk = align(r, best, history=snaps, devig_method=devig_method)
        st = blk["EXTERNAL_ALIGNMENT_STATUS"]
        if st == "ALIGNED":
            aligned += 1
        elif st == "NO_EXTERNAL_DATA":
            no_data += 1
        else:
            refused += 1
        reasons[st] = reasons.get(st, 0) + 1
        out.append(dict(r, **blk))
    return out, {"ALIGNED": aligned, "REFUSED": refused,
                 "NO_EXTERNAL_DATA": no_data, "BY_REASON": reasons,
                 "VIOLATION_IS_A_REFUSAL": VIOLATION_IS_A_REFUSAL}


# --- Section 4. The backfill request planner. ------------------------------

PLANNER_NAME = "EXTERNAL_BACKFILL_REQUEST_PLANNER"
DO_NOT_QUERY_PER_POLL = (
    "one external request per captured poll would bill thousands of calls for "
    "a handful of distinct provider snapshots. The provider returns the "
    "closest snapshot at or before the request, so requests deduplicate by "
    "bucket")

CAPTURE_FIRST_BACKFILL_AFTER = (
    "the prospective capture runs alone. Historical external snapshots are "
    "retrievable afterwards, so the request set is computed from timestamps "
    "that already exist rather than estimated in advance")


def plan_backfill(rows, bucket_minutes=5, markets=("h2h",), regions=("uk",),
                  multiplier=10, sport_of=None,
                  time_key="DECISION_TIMESTAMP_UTC"):
    """Minimum deduplicated external requests for a set of captured states.

    Deduplicated by (SPORT, SNAPSHOT_BUCKET, MARKET_SET, REGION_SET), which is
    the provider's actual billable unit.
    """
    sport_of = sport_of or (lambda r: r.get("SPORT") or r.get("LEAGUE")
                            or NOT_IDENTIFIED)
    req, unreadable = set(), 0
    by_sport = {}
    for r in rows or ():
        t = _parse(r.get(time_key))
        if t is None:
            unreadable += 1
            continue
        b = t.replace(minute=(t.minute // bucket_minutes) * bucket_minutes,
                      second=0, microsecond=0)
        key = (sport_of(r), b.isoformat())
        req.add(key)
        by_sport.setdefault(key[0], set()).add(key[1])
    per = multiplier * len(markets) * len(regions)
    return {
        "PLANNER_NAME": PLANNER_NAME,
        "INPUT_STATES": len(rows or ()),
        "STATES_WITH_UNREADABLE_TIME": unreadable,
        "UNIQUE_EXTERNAL_REQUESTS": len(req),
        "CREDITS": len(req) * per,
        "CREDITS_PER_REQUEST": per,
        "BUCKET_MINUTES": bucket_minutes,
        "MARKETS": tuple(markets), "REGIONS": tuple(regions),
        "REQUESTS_BY_SPORT": {k: len(v) for k, v in by_sport.items()},
        "BILLABLE_UNIT": ("SPORT_KEY", "SNAPSHOT_BUCKET", "MARKET_SET",
                          "REGION_SET"),
        "DO_NOT_QUERY_PER_POLL": DO_NOT_QUERY_PER_POLL,
        "CAPTURE_FIRST_BACKFILL_AFTER": CAPTURE_FIRST_BACKFILL_AFTER,
        "NOTHING_IS_PURCHASED": True,
    }


def describe():
    return {
        "THE_INVARIANT": THE_INVARIANT,
        "NO_FUTURE_ODDS": NO_FUTURE_ODDS,
        "VIOLATION_IS_A_REFUSAL": VIOLATION_IS_A_REFUSAL,
        "EXTERNAL_FIELDS": EXTERNAL_FIELDS,
        "DEVIG_METHODS": DEVIG_METHODS,
        "DEVIG_METHOD_IS_AN_ASSUMPTION": DEVIG_METHOD_IS_AN_ASSUMPTION,
        "MAX_EXTERNAL_AGE_S": MAX_EXTERNAL_AGE_S,
        "WHY_AN_AGE_CAP": WHY_AN_AGE_CAP,
        "PLANNER_NAME": PLANNER_NAME,
        "DO_NOT_QUERY_PER_POLL": DO_NOT_QUERY_PER_POLL,
        "NOTHING_IS_PURCHASED": NOTHING_IS_PURCHASED,
        "THIS_MODULE_CONTACTS_NOTHING": THIS_MODULE_CONTACTS_NOTHING,
    }
