"""When did the match actually start? Three confidence classes, and a retraction.

Directive sections 1 and 2.

THE RETRACTION COMES FIRST
--------------------------
The previous report called an observation PREGAME when

    OBSERVATION_TIME > SETTLEMENT_TIME - 3.1 hours

and described that as "proven from the venue's own UTC settlement stamps,
needing no timezone assumption". The second half of that sentence is true and
the first half is not. Settlement time is not kick-off time. The rule assumes
the gap between kick-off and settlement never exceeds 3.1 hours, and nothing
authoritative establishes that. A delayed settlement, an abandoned match
resettled later, a venue batching its resolutions overnight -- any of those
breaks it, and the corpus contains no settlement rules that rule them out.

    PREGAME_PROVEN_CLAIM = WITHDRAWN

The evidence it produced is not discarded. It is reclassified: those rows are
PREGAME_LIKELY_BY_SETTLEMENT_LAG, confidence class C, and class C may not
support a tight horizon claim.

WHAT THE VENUE ITSELF CARRIES
-----------------------------
The venue does publish a native start time. Its board rows carry
`gameStartTime`, and it is exact where the market is a real fixture: the
retained row for `tsc-nfl-det-buf-2026-09-17-total-66pt5` says
2026-09-18T00:15:00Z, which is that game's real kick-off in UTC.

Every retained artifact was searched -- board snapshots, tick universe, totals
binding fixtures, mapping tables, settlement payloads, market metadata.

    2,369 distinct slugs carry a venue-native gameStartTime
    0 of them are in the settled corpus
    0 of 5,788 settled events have one
    0 of 293 settled soccer events have one

The field exists; our retained sample of it does not overlap the events we
evaluate. It is a board snapshot of CFB, NFL, MLB and futures markets taken in
a different window. So:

    VENUE_NATIVE_START_TIME_COVERAGE = 0.0%
    VENUE_NATIVE_START_TIME_SOURCE   = venue board rows (gameStartTime),
                                       retained but non-overlapping

One caveat worth keeping: on FUTURES markets gameStartTime is not a kick-off at
all -- `tec-mlb-nlchamp-2026-09-27-atl` carries 2026-09-07T00:00:00Z. So even
when coverage arrives, the field is exact for fixtures and a placeholder for
futures, and the two must not be pooled.

THE THREE CLASSES
-----------------
    A  VENUE_NATIVE_EXACT          the venue's own gameStartTime for a real
                                   fixture. Uncertainty ~0. None available.
    B  EXTERNAL_CROSS_VALIDATED    two independent public sources agree on the
                                   kick-off after the known per-country offset,
                                   AND the implied settlement lag is plausible.
                                   Uncertainty ~1 h.
    C  APPROXIMATE                 anything else, including the settlement-lag
                                   heuristic. Uncertainty >= 2 h.

Class C may not support a T-30M or T-15M claim. Neither may class B: an hour of
uncertainty swallows a thirty-minute horizon whole. On today's data that means
NO event in the corpus supports a tight pregame horizon, and saying so is the
point of the classes.
"""

from __future__ import annotations

import datetime
from collections import Counter

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ---------------------------------------------------------------------------
# The retraction
# ---------------------------------------------------------------------------

PREGAME_PROVEN_CLAIM = "WITHDRAWN"
PREGAME_PROVEN_CLAIM_WAS = (
    "observation later than settlement minus 3.1 hours is provably pregame")
WHY_WITHDRAWN = (
    "settlement time is not kick-off time; the rule assumed a bound on the "
    "settlement lag that no authoritative venue rule establishes")
RECLASSIFIED_AS = "PREGAME_LIKELY_BY_SETTLEMENT_LAG"

# ---------------------------------------------------------------------------
# Venue-native coverage, measured
# ---------------------------------------------------------------------------

VENUE_NATIVE_START_TIME_SOURCE = (
    "venue board rows carry gameStartTime; found in "
    "fixtures_board_35209604615.json, tick_universe.json and "
    "fixtures_totals_binding.json")
VENUE_NATIVE_SLUGS_RETAINED = 2369
VENUE_NATIVE_START_TIME_COVERAGE = 0.0
VENUE_NATIVE_COVERAGE_DETAIL = {
    "SETTLED_EVENTS": 5788,
    "SETTLED_EVENTS_WITH_VENUE_NATIVE_START": 0,
    "SETTLED_SOCCER_EVENTS": 293,
    "SETTLED_SOCCER_EVENTS_WITH_VENUE_NATIVE_START": 0,
    "RETAINED_SLUGS_WITH_THE_FIELD": VENUE_NATIVE_SLUGS_RETAINED,
    "WHAT_THOSE_SLUGS_ARE": "CFB, NFL, MLB and futures board snapshots from a "
                            "window that does not overlap the settled corpus",
}
VENUE_NATIVE_IS_EXACT_FOR_FIXTURES_ONLY = True
FUTURES_GAMESTARTTIME_IS_A_PLACEHOLDER = (
    "tec-mlb-nlchamp-2026-09-27-atl carries gameStartTime "
    "2026-09-07T00:00:00Z, which is not a kick-off; futures rows must never be "
    "pooled with fixture rows when this field arrives with coverage")

HOW_TO_GET_COVERAGE = (
    "the field is on the venue's board. A board capture over the window we "
    "evaluate would supply it for every market in that window. The substantive "
    "public-book capture already records board rows, so this resolves itself "
    "once that capture runs -- no purchase required."
)

# ---------------------------------------------------------------------------
# The classes
# ---------------------------------------------------------------------------

CLASS_A = "VENUE_NATIVE_EXACT"
CLASS_B = "EXTERNAL_CROSS_VALIDATED"
CLASS_C = "APPROXIMATE_OR_UNCERTAIN"
CLASSES = (CLASS_A, CLASS_B, CLASS_C)

CLASS_UNCERTAINTY_HOURS = {CLASS_A: 0.05, CLASS_B: 1.0, CLASS_C: 3.0}

# A horizon is claimable only if it is comfortably longer than the uncertainty
# on the clock it is measured against. Two-to-one is the rule used throughout
# this programme and it is applied here unchanged.
HORIZON_UNCERTAINTY_RATIO = 2.0

TIGHT_HORIZONS = ("T-30M", "T-15M", "T-5M")
TIGHT_HORIZONS_REQUIRE = CLASS_A
WHY_B_IS_NOT_ENOUGH_FOR_TIGHT_HORIZONS = (
    "class B carries about an hour of uncertainty. A thirty-minute horizon "
    "measured against a clock that could be an hour out is not a "
    "thirty-minute horizon; it is a label."
)


def horizon_claimable(horizon_hours, start_class):
    """May a horizon of this length be claimed on this class of clock?"""
    u = CLASS_UNCERTAINTY_HOURS.get(start_class)
    if u is None:
        return False, "UNKNOWN_START_TIME_CLASS"
    if horizon_hours < HORIZON_UNCERTAINTY_RATIO * u:
        return False, ("HORIZON_TOO_SHORT_FOR_%s_CLOCK_UNCERTAINTY_%.2fH"
                       % (start_class, u))
    return True, "CLAIMABLE"


# ---------------------------------------------------------------------------
# Classifying one event
# ---------------------------------------------------------------------------

# Measured in club_football_ingest: the two public sources differ by a fixed
# per-country constant with no seasonal component.
CROSS_SOURCE_OFFSET_TOLERANCE_HOURS = 1.25

# A settlement lag outside this band means the two are not describing the same
# match, or the settlement was delayed. Either way the cross-validation fails.
PLAUSIBLE_SETTLEMENT_LAG_HOURS = (1.0, 4.5)


def _parse(ts):
    if not ts:
        return None
    s = str(ts).replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d.replace(tzinfo=datetime.timezone.utc) if d.tzinfo is None else d


def _combine(date_str, time_str):
    if not (date_str and time_str):
        return None
    return _parse("%sT%s" % (date_str, str(time_str)[:8]))


def classify(venue_native=None, source_a=None, source_b=None,
             offset_hours=None, resolved_at=None):
    """Assign a start-time class to one event, with its uncertainty.

    `source_a` and `source_b` are (date, time) pairs from two independent
    public sources. `offset_hours` is the measured constant between them for
    this country; it is SUBTRACTED from source_b before comparison, because the
    offset is a known property of the pair, not an error in either.
    """
    if venue_native:
        t = _parse(venue_native)
        if t:
            return {
                "START_TIME_STATUS": CLASS_A,
                "START_TIME_UTC": t.isoformat().replace("+00:00", "Z"),
                "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_A],
                "BASIS": "VENUE_NATIVE_gameStartTime",
            }

    ta = _combine(*source_a) if source_a else None
    tb = _combine(*source_b) if source_b else None
    if ta and tb:
        adj = tb - datetime.timedelta(hours=offset_hours or 0.0)
        gap = abs((ta - adj).total_seconds()) / 3600.0
        if gap <= CROSS_SOURCE_OFFSET_TOLERANCE_HOURS:
            lag_ok, lag = True, None
            r = _parse(resolved_at)
            if r:
                lag = (r - ta).total_seconds() / 3600.0
                lo, hi = PLAUSIBLE_SETTLEMENT_LAG_HOURS
                lag_ok = lo <= lag <= hi
            if lag_ok:
                return {
                    "START_TIME_STATUS": CLASS_B,
                    "START_TIME_UTC": ta.isoformat().replace("+00:00", "Z"),
                    "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_B],
                    "BASIS": "TWO_PUBLIC_SOURCES_AGREE_AFTER_KNOWN_OFFSET",
                    "CROSS_SOURCE_GAP_HOURS": gap,
                    "IMPLIED_SETTLEMENT_LAG_HOURS": lag,
                }
            return {
                "START_TIME_STATUS": CLASS_C,
                "START_TIME_UTC": ta.isoformat().replace("+00:00", "Z"),
                "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_C],
                "BASIS": "SOURCES_AGREE_BUT_SETTLEMENT_LAG_IMPLAUSIBLE",
                "IMPLIED_SETTLEMENT_LAG_HOURS": lag,
            }
        return {
            "START_TIME_STATUS": CLASS_C,
            "START_TIME_UTC": ta.isoformat().replace("+00:00", "Z"),
            "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_C],
            "BASIS": "PUBLIC_SOURCES_DISAGREE_BEYOND_THE_KNOWN_OFFSET",
            "CROSS_SOURCE_GAP_HOURS": gap,
        }

    only = ta or tb
    if only:
        return {
            "START_TIME_STATUS": CLASS_C,
            "START_TIME_UTC": only.isoformat().replace("+00:00", "Z"),
            "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_C],
            "BASIS": "ONE_PUBLIC_SOURCE_ONLY_NOT_CROSS_VALIDATED",
        }
    return {
        "START_TIME_STATUS": CLASS_C,
        "START_TIME_UTC": NOT_IDENTIFIED,
        "UNCERTAINTY_HOURS": CLASS_UNCERTAINTY_HOURS[CLASS_C],
        "BASIS": "NO_START_TIME_AT_ALL",
    }


def classify_all(events):
    """events: [{VENUE_NATIVE, SOURCE_A, SOURCE_B, OFFSET_HOURS, RESOLVED_AT}]"""
    out, counts, bases = {}, Counter(), Counter()
    unc = []
    for key, e in (events or {}).items():
        c = classify(e.get("VENUE_NATIVE"), e.get("SOURCE_A"),
                     e.get("SOURCE_B"), e.get("OFFSET_HOURS"),
                     e.get("RESOLVED_AT"))
        out[key] = c
        counts[c["START_TIME_STATUS"]] += 1
        bases[c["BASIS"]] += 1
        unc.append(c["UNCERTAINTY_HOURS"])
    n = sum(counts.values())
    claim = {h: horizon_claimable(hh, CLASS_B)[0]
             for h, hh in (("T-24H", 24), ("T-6H", 6), ("T-2H", 2),
                           ("T-1H", 1), ("T-30M", 0.5), ("T-15M", 0.25))}
    return out, {
        "EVENTS": n,
        "BY_CLASS": dict(counts),
        "BY_BASIS": dict(bases),
        "CLASS_A_PCT": (100.0 * counts[CLASS_A] / n) if n else 0.0,
        "CLASS_B_PCT": (100.0 * counts[CLASS_B] / n) if n else 0.0,
        "CLASS_C_PCT": (100.0 * counts[CLASS_C] / n) if n else 0.0,
        "MEAN_UNCERTAINTY_HOURS": (sum(unc) / len(unc)) if unc else
                                  NOT_IDENTIFIED,
        "HORIZON_CLAIMABLE_ON_BEST_AVAILABLE_CLASS": claim,
        "TIGHT_HORIZONS_REQUIRE": TIGHT_HORIZONS_REQUIRE,
        "WHY_B_IS_NOT_ENOUGH_FOR_TIGHT_HORIZONS":
            WHY_B_IS_NOT_ENOUGH_FOR_TIGHT_HORIZONS,
        "PREGAME_PROVEN_CLAIM": PREGAME_PROVEN_CLAIM,
        "VENUE_NATIVE_START_TIME_COVERAGE": VENUE_NATIVE_START_TIME_COVERAGE,
    }
