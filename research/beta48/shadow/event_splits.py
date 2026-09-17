"""Section 8. Event-safe validation, enforced rather than recommended.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE FAILURE THIS PREVENTS
-------------------------
A random row split puts 18:04:12 in train and 18:04:36 in test. Those two rows
describe the same book, seconds apart, on the same game. A model "predicting"
the second from the first has memorised, not learned, and the score looks
excellent.

So the split unit is the EVENT, never the row:

    no EVENT_ID may appear in more than one split
    adjacent timestamps from the same event stay together
    chronological order is preserved where the question is forward-looking

EVERY SCORE CARRIES ITS SCALE
-----------------------------
ROWS, EVENTS, MARKETS, EVENT_HOURS and TIME_RANGE are returned with every
result, because 10,000 rows over 3 events is a different claim from 10,000
rows over 3,000 events and the number alone cannot tell them apart.
"""

import datetime

NOT_IDENTIFIED = "NOT_IDENTIFIED"

SPLIT_UNIT = "EVENT"
SPLITS = ("TRAIN_EVENTS", "VALIDATION_EVENTS", "TEST_EVENTS")

NO_RANDOM_ROW_SPLIT = (
    "a random row split puts two observations of the same book seconds apart "
    "on opposite sides of the split. The model then memorises rather than "
    "learns, and the score looks excellent")

ADJACENCY_RULE = (
    "adjacent timestamps from the same event remain in the same split, which "
    "follows automatically from splitting on EVENT_ID")

MANDATORY_SCALE_FIELDS = ("ROWS", "EVENTS", "MARKETS", "EVENT_HOURS",
                          "TIME_RANGE")

WHY_SCALE_TRAVELS_WITH_EVERY_SCORE = (
    "10,000 rows over 3 events is a different claim from 10,000 rows over "
    "3,000 events, and the score alone cannot tell them apart")


def _parse(ts):
    if isinstance(ts, datetime.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=datetime.timezone.utc)
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def scale(rows, event_key="EVENT_ID", market_key="MARKET_ID",
          time_key="DECISION_TIMESTAMP_UTC"):
    """The mandatory block that accompanies every score."""
    events, markets = set(), set()
    per_event = {}
    for r in rows or ():
        ev, mk = r.get(event_key), r.get(market_key)
        if ev not in (None, NOT_IDENTIFIED):
            events.add(ev)
        if mk not in (None, NOT_IDENTIFIED):
            markets.add(mk)
        t = _parse(r.get(time_key))
        if t and ev is not None:
            lo, hi = per_event.get(ev, (t, t))
            per_event[ev] = (min(lo, t), max(hi, t))
    hours = sum((hi - lo).total_seconds()
                for lo, hi in per_event.values()) / 3600.0
    all_t = [t for pair in per_event.values() for t in pair]
    return {
        "ROWS": len(rows or ()),
        "EVENTS": len(events),
        "MARKETS": len(markets),
        "EVENT_HOURS": round(hours, 4),
        "TIME_RANGE": ((min(all_t).isoformat(), max(all_t).isoformat())
                       if all_t else NOT_IDENTIFIED),
    }


def split(rows, train_frac=0.6, validation_frac=0.2, chronological=True,
          event_key="EVENT_ID", time_key="DECISION_TIMESTAMP_UTC"):
    """Split by EVENT. Chronological by the event's FIRST observation.

    Events are ordered by when they started, so the test set is genuinely
    later than the train set rather than merely disjoint from it.
    """
    first_seen = {}
    for r in rows or ():
        ev = r.get(event_key)
        if ev in (None, NOT_IDENTIFIED):
            continue
        t = _parse(r.get(time_key))
        if t is None:
            continue
        if ev not in first_seen or t < first_seen[ev]:
            first_seen[ev] = t
    if not first_seen:
        return {"STATUS": "NO_USABLE_EVENTS",
                "WHY": "no row carried both an EVENT_ID and a readable time",
                "SPLITS": {k: [] for k in SPLITS}}

    order = sorted(first_seen, key=lambda e: (first_seen[e], str(e))) \
        if chronological else sorted(first_seen, key=str)
    n = len(order)
    n_tr = int(n * train_frac)
    n_va = int(n * validation_frac)
    # With few events, integer truncation can empty a split. Report it rather
    # than silently returning a two-way split that looks like a three-way one.
    assign = {"TRAIN_EVENTS": order[:n_tr],
              "VALIDATION_EVENTS": order[n_tr:n_tr + n_va],
              "TEST_EVENTS": order[n_tr + n_va:]}
    empty = [k for k, v in assign.items() if not v]
    return {
        "STATUS": "SPLIT" if not empty else "SPLIT_WITH_EMPTY_PARTS",
        "SPLIT_UNIT": SPLIT_UNIT,
        "CHRONOLOGICAL": bool(chronological),
        "SPLITS": assign,
        "EVENT_N": n,
        "EMPTY_SPLITS": empty,
        "WHY_EMPTY": (None if not empty else
                      "%d events cannot fill three splits at these fractions; "
                      "reported rather than silently collapsed" % n),
        "NO_RANDOM_ROW_SPLIT": NO_RANDOM_ROW_SPLIT,
        "ADJACENCY_RULE": ADJACENCY_RULE,
    }


def leak_check(assignment, rows=None, event_key="EVENT_ID"):
    """No EVENT_ID may exist in more than one split. Fails closed."""
    seen, overlaps = {}, []
    for name in SPLITS:
        for ev in (assignment.get("SPLITS", assignment) or {}).get(name, []):
            if ev in seen:
                overlaps.append({"EVENT_ID": ev, "IN": [seen[ev], name]})
            seen[ev] = name
    unassigned = []
    if rows:
        known = set(seen)
        for r in rows:
            ev = r.get(event_key)
            if ev not in (None, NOT_IDENTIFIED) and ev not in known:
                unassigned.append(ev)
    clean = not overlaps and not unassigned
    return {
        "LEAKAGE_CHECK": "CLEAN" if clean else "LEAKED",
        "OVERLAPPING_EVENTS": overlaps,
        "UNASSIGNED_EVENTS": sorted(set(unassigned)),
        "SPLIT_UNIT": SPLIT_UNIT,
        "MAY_SCORE": clean,
        "WHY_NOT": (None if clean else
                    "an event appears in more than one split, or a row's "
                    "event was never assigned"),
    }


def subset(rows, assignment, which, event_key="EVENT_ID"):
    keep = set((assignment.get("SPLITS", assignment) or {}).get(which, []))
    return [r for r in rows or () if r.get(event_key) in keep]


def scored(rows, assignment, which, score=None, metric=None,
           event_key="EVENT_ID"):
    """A score is never returned without its scale."""
    part = subset(rows, assignment, which, event_key)
    out = {"SPLIT": which, "SCORE": score if score is not None
           else NOT_IDENTIFIED,
           "METRIC": metric or NOT_IDENTIFIED}
    out.update(scale(part))
    out["WHY_SCALE_TRAVELS_WITH_EVERY_SCORE"] = \
        WHY_SCALE_TRAVELS_WITH_EVERY_SCORE
    return out


def describe():
    return {
        "SPLIT_UNIT": SPLIT_UNIT,
        "SPLITS": SPLITS,
        "NO_RANDOM_ROW_SPLIT": NO_RANDOM_ROW_SPLIT,
        "ADJACENCY_RULE": ADJACENCY_RULE,
        "MANDATORY_SCALE_FIELDS": MANDATORY_SCALE_FIELDS,
        "WHY_SCALE_TRAVELS_WITH_EVERY_SCORE":
            WHY_SCALE_TRAVELS_WITH_EVERY_SCORE,
    }
