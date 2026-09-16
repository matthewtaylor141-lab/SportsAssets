#!/usr/bin/env python3
"""Venue-native event identity for PMUS. No slug heuristics.

WHY THIS EXISTS AND WHY IT IS NOT `eligibility.underlying_event_key`.

The derived slug key failed validation in both directions and is frozen at
`EVENT_KEY_VALIDATED = NO`. `underlying_event_key` was then written to look for
something better, and it looks in the right place -- `marketSides` -- but for
the wrong shape: it reads `side.team.providerIds` as a LIST OF DICTS, and this
venue sends `side.team.providerId` as a SCALAR. So it never reached LEVEL_B on
real rows and fell back to LEVEL_C on 773 of 788 candidates.

That was a capture-and-shape problem, not a venue one. The raw board rows carry
everything needed, on all 20,000 of them:

    marketSides    exactly 2, always
    gameStartTime  present, always
    side.teamId    the venue's own team id
    side.team      abbreviation, league, providerId

So identity here is read from the VENUE'S OWN FIELDS and never inferred from a
slug. `eligibility.underlying_event_key` is left exactly as frozen -- this is a
second, additive reader, and `test_event_identity.py` cross-checks the two
rather than replacing one with the other.

WHAT COUNTS AS AN EVENT, AND WHAT DOES NOT.

    LEVEL_V1_CONTEST   two distinct teamIds + a gameStartTime. This is a
                       head-to-head contest and the two ids name both sides of
                       it. A venue-native proof, not a naming convention.

    LEVEL_V2_SUBJECT   one teamId + a gameStartTime. A market ABOUT one team
                       ("Atlanta wins the NL"), not a contest between two. Many
                       such markets share a start time WITHOUT being the same
                       event -- a futures book is not a game -- so these group
                       by (start, team) and are NEVER merged on start alone.

    LEVEL_V3_NONE      no teamId. 16,501 of 20,000 rows. Props and futures with
                       no team binding at all, and gameStartTime alone is not an
                       identity: dozens of unrelated markets share one start.

A start time by itself is refused as an event key at every level. That is the
same error as the slug key -- a field that correlates with identity is not an
identity -- and it is the one this module exists to avoid repeating.
"""
from __future__ import annotations

NOT_IDENTIFIED = "NOT_IDENTIFIED"

LEVEL_V1_CONTEST = "LEVEL_V1_VENUE_TEAM_PAIR_CONTEST"
LEVEL_V2_SUBJECT = "LEVEL_V2_VENUE_SINGLE_TEAM_SUBJECT"
LEVEL_V3_NONE = "LEVEL_V3_NO_TEAM_BINDING"

IDENTITY_LEVELS = (LEVEL_V1_CONTEST, LEVEL_V2_SUBJECT, LEVEL_V3_NONE)

# Only the first level is a contest between two identified sides, and only a
# contest supports "these markets are the same underlying event".
LEVELS_THAT_ARE_A_CONTEST = (LEVEL_V1_CONTEST,)


def sides(market):
    """The venue's marketSides, or an empty list. Never invented."""
    ms = market.get("marketSides")
    return ms if isinstance(ms, list) else []


def team_ids(market):
    """Distinct venue team ids across this market's sides."""
    out = set()
    for s in sides(market):
        t = s.get("teamId")
        if t is None:
            t = (s.get("team") or {}).get("id")
        if t is not None:
            out.add(t)
    return out


def provider_ids(market):
    """Provider ids, accepting BOTH shapes the venue might send.

    The scalar form is what this venue actually sends. The list-of-dicts form
    is what `eligibility` expected. Reading both means a venue change in either
    direction degrades gracefully instead of silently returning nothing.
    """
    out = set()
    for s in sides(market):
        t = s.get("team") or {}
        p = t.get("providerId")
        if p is not None:
            out.add(p)
        for d in (t.get("providerIds") or []):
            if isinstance(d, dict) and d.get("providerId") is not None:
                out.add(d["providerId"])
    return out


def leagues(market):
    return {(s.get("team") or {}).get("league")
            for s in sides(market)
            if (s.get("team") or {}).get("league")}


def event_identity(market):
    """(EVENT_ID, LEVEL) for ONE market row, from the venue's own fields."""
    start = market.get("gameStartTime")
    tids = team_ids(market)

    if not start:
        # No clock, no event. A market with teams but no start time could be
        # any of that fixture's meetings.
        return NOT_IDENTIFIED, LEVEL_V3_NONE
    if len(tids) >= 2:
        key = "%s|%s" % (start, "-".join(str(t) for t in sorted(tids)))
        return key, LEVEL_V1_CONTEST
    if len(tids) == 1:
        key = "%s|SUBJECT=%s" % (start, next(iter(tids)))
        return key, LEVEL_V2_SUBJECT
    # A start time with no team is NOT an identity. Refused rather than used.
    return NOT_IDENTIFIED, LEVEL_V3_NONE


def index_events(markets):
    """Group rows by venue-native event. Returns (events, per_level_counts).

    `events` maps EVENT_ID -> [slug, ...] for CONTEST-level rows only. The
    other levels are counted, never merged: grouping a futures book by start
    time would reproduce the over-merge that killed the slug key.
    """
    events, levels, unresolved = {}, {lv: 0 for lv in IDENTITY_LEVELS}, []
    subjects = {}
    for m in markets:
        slug = m.get("slug")
        key, lv = event_identity(m)
        levels[lv] += 1
        if lv == LEVEL_V1_CONTEST:
            events.setdefault(key, []).append(slug)
        elif lv == LEVEL_V2_SUBJECT:
            subjects.setdefault(key, []).append(slug)
        else:
            unresolved.append(slug)
    return {
        "EVENTS": events,
        "SUBJECT_GROUPS": subjects,
        "LEVEL_COUNTS": levels,
        "UNRESOLVED_SLUGS": unresolved,
        "VALID_EVENTS": len(events),
        "SUBJECT_GROUP_COUNT": len(subjects),
        "MARKETS_WITH_NO_EVENT_IDENTITY": len(unresolved),
        "START_TIME_ALONE_USED_AS_IDENTITY": False,
    }


# A COUNT OF EVENTS IS NOT A CAPACITY, and the first version of this function
# said otherwise. It published the validated event count as
# INDEPENDENT_CAPACITY_LOWER_BOUND, which reads as "we could deploy across at
# least this many" -- a claim about CAPITAL that an identity count cannot
# support. An event with no fillable book has a capacity of zero.
EVENT_COUNT_IS_NOT_CAPITAL_CAPACITY = True
CAPACITY_ADDITIONALLY_REQUIRES = (
    "ACTUAL_FILLABILITY", "AVAILABLE_DEPTH", "QUOTE_SIZE",
    "INVENTORY_DURATION", "CAPITAL_TURNOVER", "EVENT_EXPOSURE_LIMIT",
    "CORRELATION", "RISK_BUDGET", "EXECUTION_COSTS",
)


def independent_event_count(idx):
    """How many distinct validated events the board holds. A COUNT, not a size.

    The contest rows give a real count of distinct events. The subject and
    no-binding rows do not, and they are not quietly added. Nothing here is a
    statement about how much capital those events could absorb: that is
    DEPLOYABLE_CAPITAL_CAPACITY, it is NOT_IDENTIFIED, and every input it still
    needs is listed on the row.
    """
    unresolved = (idx["MARKETS_WITH_NO_EVENT_IDENTITY"]
                  + sum(len(v) for v in idx["SUBJECT_GROUPS"].values()))
    return {
        "VALIDATED_DISTINCT_EVENTS": idx["VALID_EVENTS"],
        "INDEPENDENT_EVENT_COUNT": idx["VALID_EVENTS"],
        "MARKETS_OF_UNRESOLVED_IDENTITY": unresolved,
        "EVENT_IDENTITY_COMPLETE": unresolved == 0,

        "DEPLOYABLE_CAPITAL_CAPACITY": NOT_IDENTIFIED,
        "CAPACITY_ADDITIONALLY_REQUIRES": list(CAPACITY_ADDITIONALLY_REQUIRES),
        "MARKET_COUNT_USED_AS_CAPACITY": False,
        "EVENT_COUNT_USED_AS_CAPACITY": False,

        "WHY": ("contest-level rows carry a venue-native two-team identity; "
                "subject-level and unbound rows do not, and are never merged "
                "on start time alone. None of that prices an event."),
    }


def markets_per_event(idx):
    """Distribution of markets per validated contest -- the correlation the
    market count hides. One contest routinely carries many markets."""
    sizes = sorted(len(v) for v in idx["EVENTS"].values())
    if not sizes:
        return {"N_EVENTS": 0, "MIN": NOT_IDENTIFIED, "P50": NOT_IDENTIFIED,
                "MAX": NOT_IDENTIFIED, "MEAN": NOT_IDENTIFIED}
    return {
        "N_EVENTS": len(sizes),
        "MIN": sizes[0],
        "P50": sizes[len(sizes) // 2],
        "MAX": sizes[-1],
        "MEAN": sum(sizes) / float(len(sizes)),
        "TOTAL_MARKETS_IN_EVENTS": sum(sizes),
    }
