"""Section 23. As-of provenance: when a fact became KNOWABLE, not when it is true.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE LOOKAHEAD THIS PREVENTS
---------------------------
A historical database is a record of what is true, assembled later. It is not
a record of what was knowable at the time. Those differ in three ways that all
look identical once the data is in a CSV:

  1. LATENCY. A match's shot counts and xG are computed after the whistle and
     published some minutes or hours later. Using them as a feature for a LATER
     match is fine. Using them for THAT match is not, and the file cannot tell
     you which you did.

  2. REVISION. Ratings, xG models and even scorelines get restated. A row dated
     2024-03-01 may hold a value computed in 2025 from information that did not
     exist in 2024. The date column is the AS-OF date of the quantity, not the
     publication date of the number.

  3. OBSERVABILITY. A lineup is "true" from the moment the manager decides it
     and knowable from the moment it is announced -- roughly an hour before
     kickoff for most leagues, and never for a decision reversed in the tunnel.
     An injury is knowable when reported, which may be days after it happened
     or, for a team that conceals it, not at all. Dating either by the MATCH
     date silently grants the model information nobody had.

So every source must answer three questions before any feature drawn from it
may enter a tight as-of claim:

    PUBLICATION_TIMESTAMP_AVAILABLE = YES / NO
    EVENT_TIMESTAMP                 = when the thing happened
    DATA_BECAME_KNOWN_TIMESTAMP     = when it became observable

When the third cannot be reconstructed, the source is marked
ASOF_PROVENANCE_STATUS = NOT_PROVEN and excluded from the highest-integrity
model lane. It is not deleted, and it is not called leakage -- it is called
unproven, which is what it is.

THE RULE
--------
No feature earns admission merely because it existed somewhere in a historical
database later.
"""

import datetime

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_PROVEN = "NOT_PROVEN"
PROVEN = "PROVEN"
ASSUMED_BOUNDED = "ASSUMED_BOUNDED"

THE_RULE = (
    "No feature earns admission merely because it existed somewhere in a "
    "historical database later.")

LANES = ("HIGH_INTEGRITY", "EXPLORATORY")

HIGH_INTEGRITY_ADMITS = (PROVEN,)

WHY_ASSUMED_BOUNDED_IS_NOT_ENOUGH = (
    "ASSUMED_BOUNDED means we believe the publication lag is smaller than the "
    "gap between the source match and the match being predicted, but we have "
    "no timestamp proving it. That belief is usually right and occasionally "
    "wrong, and it is exactly the kind of thing that is right on the "
    "development data and wrong on the fixture that matters. It is admissible "
    "to the exploratory lane and to loose horizons; it is not admissible to a "
    "tight as-of claim.")


def _parse(ts):
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    for f in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = (datetime.datetime.fromisoformat(s) if f is None
                 else datetime.datetime.strptime(s, f))
            if d.tzinfo is None:
                d = d.replace(tzinfo=datetime.timezone.utc)
            return d
        except Exception:
            continue
    return None


def known_before(event_timestamp, data_became_known_timestamp):
    """Was the datum observable before the moment we want to use it?

    Returns True, False, or NOT_IDENTIFIED. NOT_IDENTIFIED is not False and it
    is certainly not True: an unanswerable question is not a pass.
    """
    a = _parse(event_timestamp)
    b = _parse(data_became_known_timestamp)
    if a is None or b is None:
        return NOT_IDENTIFIED
    return b < a


def source_record(name, publication_timestamp_available, event_timestamp_field,
                  data_became_known_field=None, status=None, detail=None,
                  revision_risk=None):
    """One source's as-of provenance, with the status derived, not asserted.

    A caller may override `status` only downward -- a source cannot declare
    itself PROVEN when it has no publication timestamp.
    """
    derived = PROVEN if publication_timestamp_available and \
        data_became_known_field else NOT_PROVEN
    if status == ASSUMED_BOUNDED and derived == NOT_PROVEN:
        derived = ASSUMED_BOUNDED
    elif status and status != derived:
        if status == NOT_PROVEN:
            derived = NOT_PROVEN
    return {
        "SOURCE": name,
        "PUBLICATION_TIMESTAMP_AVAILABLE":
            "YES" if publication_timestamp_available else "NO",
        "EVENT_TIMESTAMP": event_timestamp_field or NOT_IDENTIFIED,
        "DATA_BECAME_KNOWN_TIMESTAMP": data_became_known_field or NOT_IDENTIFIED,
        "ASOF_PROVENANCE_STATUS": derived,
        "REVISION_RISK": revision_risk or NOT_IDENTIFIED,
        "DETAIL": detail or "",
    }


# ---------------------------------------------------------------------------
# The sources actually held, assessed. Nothing here is aspirational.
# ---------------------------------------------------------------------------

SOURCES = {
    "XGABORA_MATCH_RESULTS": source_record(
        "xgabora/Club-Football-Match-Data (FTHome/FTAway)",
        publication_timestamp_available=False,
        event_timestamp_field="MatchDate (+ MatchTime, timezone unverified)",
        data_became_known_field=None,
        status=ASSUMED_BOUNDED,
        revision_risk="LOW",
        detail=("a final score is knowable within minutes of the whistle and "
                "is almost never restated. Used only as lagged history of "
                "EARLIER matches, the bound holds comfortably. It is still "
                "ASSUMED_BOUNDED, not PROVEN: the file carries no publication "
                "timestamp")),
    "XGABORA_SHOT_COUNTS": source_record(
        "xgabora/Club-Football-Match-Data (HomeShots/Target/Corners)",
        publication_timestamp_available=False,
        event_timestamp_field="MatchDate",
        data_became_known_field=None,
        status=ASSUMED_BOUNDED,
        revision_risk="MEDIUM",
        detail=("box-score aggregates are published with the match report, "
                "typically within hours, but they ARE corrected -- a shot "
                "reclassified as a blocked cross changes the count days "
                "later. Lagged five matches back the risk is small; it is not "
                "zero and it is not timestamped")),
    "XGABORA_ELO": source_record(
        "xgabora/Club-Football-Match-Data (EloRatings.csv)",
        publication_timestamp_available=False,
        event_timestamp_field="date (the rating's AS-OF date)",
        data_became_known_field=None,
        status=NOT_PROVEN,
        revision_risk="HIGH",
        detail=("this is the dangerous one. The date column is the date the "
                "rating applies TO, not the date it was computed. A rating "
                "series regenerated in one pass over completed history can "
                "embed later results in an earlier row, and nothing in the "
                "file distinguishes that from a genuine contemporaneous "
                "rating. ELO_SOURCE_PROVISIONAL from 2025-06-15 compounds it. "
                "The internal strength models exist partly so the programme is "
                "not dependent on this field")),
    "XGABORA_ODDS": source_record(
        "xgabora/Club-Football-Match-Data (OddHome/Max*/Over25)",
        publication_timestamp_available=False,
        event_timestamp_field="MatchDate",
        data_became_known_field=None,
        status=NOT_PROVEN,
        revision_risk="HIGH",
        detail=("opening and closing are not timestamped and the two are not "
                "distinguished per row. Excluded from P_BETTOR_INDEPENDENT "
                "entirely on separate grounds, and excluded here as well")),
    "OPENFOOTBALL_FIXTURES": source_record(
        "openfootball (dates, local kickoff times, scores)",
        publication_timestamp_available=False,
        event_timestamp_field="DATE + TIME (local, offset measured)",
        data_became_known_field=None,
        status=ASSUMED_BOUNDED,
        revision_risk="MEDIUM",
        detail=("a repository of scheduled fixtures edited over time; a "
                "kickoff time moved after the fact leaves no trace. This is "
                "why cross-source agreement, not a single source, is what "
                "promotes an event to start-time class B")),
    "VENUE_SETTLEMENT": source_record(
        "venue settlement capture (resolved_at)",
        publication_timestamp_available=True,
        event_timestamp_field="resolved_at",
        data_became_known_field="resolved_at",
        status=PROVEN,
        revision_risk="LOW",
        detail=("the venue's own settlement stamp is a publication timestamp "
                "by construction: it is the moment the venue asserted the "
                "outcome. It is a settlement time, NOT a kickoff time, and "
                "section 1 withdrew the claim that treated it as one")),
    "VENUE_OBSERVATION": source_record(
        "venue observation capture (observation_time)",
        publication_timestamp_available=True,
        event_timestamp_field="observation_time",
        data_became_known_field="observation_time",
        status=PROVEN,
        revision_risk="LOW",
        detail="the moment this programme observed a price; known by construction"),
    "XG_ANY_PROVIDER": source_record(
        "expected goals -- any provider",
        publication_timestamp_available=False,
        event_timestamp_field=NOT_IDENTIFIED,
        data_became_known_field=None,
        status=NOT_PROVEN,
        revision_risk="HIGH",
        detail=("no xG series is held (XG_DATA_STATUS = NOT_IDENTIFIED). This "
                "record is written in advance so that when one is obtained it "
                "must clear these fields before admission. xG is MODEL output: "
                "it is revised whenever the provider's model is revised, so a "
                "model version and vintage are required, not just a date")),
    "PLAYER_AVAILABILITY": source_record(
        "lineups, injuries, suspensions -- any provider",
        publication_timestamp_available=False,
        event_timestamp_field=NOT_IDENTIFIED,
        data_became_known_field=None,
        status=NOT_PROVEN,
        revision_risk="HIGH",
        detail=("PLAYER_AVAILABILITY_DATA_STATUS = NOT_IDENTIFIED. When a "
                "source is obtained, the binding timestamp is the ANNOUNCEMENT "
                "time -- when the lineup was published, when the injury was "
                "reported -- and NOT the match date. A confirmed lineup is "
                "typically knowable about an hour before kickoff, which is "
                "inside a horizon this programme cannot currently claim "
                "anyway, since start-time class A is empty")),
}


def status_of(source_key):
    rec = SOURCES.get(source_key)
    return rec["ASOF_PROVENANCE_STATUS"] if rec else NOT_IDENTIFIED


# ---------------------------------------------------------------------------
# Feature admission.
# ---------------------------------------------------------------------------

FEATURE_SOURCES = {
    "ELO_DIFF": "XGABORA_ELO", "ELO_HOME": "XGABORA_ELO",
    "ELO_AWAY": "XGABORA_ELO",
    "GF5_HOME": "XGABORA_MATCH_RESULTS", "GA5_HOME": "XGABORA_MATCH_RESULTS",
    "GF5_AWAY": "XGABORA_MATCH_RESULTS", "GA5_AWAY": "XGABORA_MATCH_RESULTS",
    "GF10_HOME": "XGABORA_MATCH_RESULTS", "GA10_HOME": "XGABORA_MATCH_RESULTS",
    "GF10_AWAY": "XGABORA_MATCH_RESULTS", "GA10_AWAY": "XGABORA_MATCH_RESULTS",
    "SHOTS5_HOME": "XGABORA_SHOT_COUNTS", "SHOTS5_AWAY": "XGABORA_SHOT_COUNTS",
    "TARGET5_HOME": "XGABORA_SHOT_COUNTS", "TARGET5_AWAY": "XGABORA_SHOT_COUNTS",
    "CORNERS5_HOME": "XGABORA_SHOT_COUNTS",
    "CORNERS5_AWAY": "XGABORA_SHOT_COUNTS",
    "HOME_GF5_AT_HOME": "XGABORA_MATCH_RESULTS",
    "HOME_GA5_AT_HOME": "XGABORA_MATCH_RESULTS",
    "AWAY_GF5_AT_AWAY": "XGABORA_MATCH_RESULTS",
    "AWAY_GA5_AT_AWAY": "XGABORA_MATCH_RESULTS",
    "FORM5_HOME": "XGABORA_MATCH_RESULTS",
    "FORM5_AWAY": "XGABORA_MATCH_RESULTS",
    "REST_DAYS_HOME": "XGABORA_MATCH_RESULTS",
    "REST_DAYS_AWAY": "XGABORA_MATCH_RESULTS",
    "INT_ELO_HOME": "XGABORA_MATCH_RESULTS",
    "INT_ELO_AWAY": "XGABORA_MATCH_RESULTS",
    "INT_ELO_DIFF": "XGABORA_MATCH_RESULTS",
    "INT_ELO_EXPECTED_HOME": "XGABORA_MATCH_RESULTS",
    "INT_GD_ELO_HOME": "XGABORA_MATCH_RESULTS",
    "INT_GD_ELO_AWAY": "XGABORA_MATCH_RESULTS",
    "INT_GD_ELO_DIFF": "XGABORA_MATCH_RESULTS",
    "INT_GD_ELO_EXPECTED_HOME": "XGABORA_MATCH_RESULTS",
    "INT_EWMA_GF_HOME": "XGABORA_MATCH_RESULTS",
    "INT_EWMA_GA_HOME": "XGABORA_MATCH_RESULTS",
    "INT_EWMA_GF_AWAY": "XGABORA_MATCH_RESULTS",
    "INT_EWMA_GA_AWAY": "XGABORA_MATCH_RESULTS",
}


def admit(feature, lane="HIGH_INTEGRITY"):
    """May this feature enter this lane? Returns (bool, reason)."""
    if lane not in LANES:
        return False, "UNKNOWN_LANE"
    src = FEATURE_SOURCES.get(feature)
    if src is None:
        return False, "FEATURE_HAS_NO_DECLARED_SOURCE"
    st = status_of(src)
    if lane == "EXPLORATORY":
        return st != NOT_IDENTIFIED, "SOURCE_%s" % st
    return st in HIGH_INTEGRITY_ADMITS, "SOURCE_%s" % st


def lane_features(features, lane="HIGH_INTEGRITY"):
    """Partition a feature list by admission, with the reason for each cut."""
    keep, cut = [], {}
    for f in features:
        ok, why = admit(f, lane)
        if ok:
            keep.append(f)
        else:
            cut.setdefault(why, []).append(f)
    return {"LANE": lane, "ADMITTED": keep, "EXCLUDED": cut,
            "ADMITTED_COUNT": len(keep),
            "EXCLUDED_COUNT": sum(len(v) for v in cut.values())}


def tight_asof_claim_allowed(features):
    """A tight as-of claim requires every feature to be PROVEN.

    This is deliberately strict and it currently refuses everything the
    programme has. That refusal is the finding, not a bug in the gate.
    """
    unproven = []
    for f in features:
        src = FEATURE_SOURCES.get(f)
        st = status_of(src) if src else NOT_IDENTIFIED
        if st not in HIGH_INTEGRITY_ADMITS:
            unproven.append({"FEATURE": f, "SOURCE": src or NOT_IDENTIFIED,
                             "STATUS": st})
    return {
        "TIGHT_ASOF_CLAIM_ALLOWED": not unproven,
        "UNPROVEN_FEATURES": unproven,
        "UNPROVEN_COUNT": len(unproven),
        "RULE": THE_RULE,
    }


def describe():
    return {
        "THE_RULE": THE_RULE,
        "REQUIRED_FIELDS": ("PUBLICATION_TIMESTAMP_AVAILABLE",
                            "EVENT_TIMESTAMP", "DATA_BECAME_KNOWN_TIMESTAMP"),
        "STATUSES": (PROVEN, ASSUMED_BOUNDED, NOT_PROVEN),
        "HIGH_INTEGRITY_ADMITS": HIGH_INTEGRITY_ADMITS,
        "WHY_ASSUMED_BOUNDED_IS_NOT_ENOUGH": WHY_ASSUMED_BOUNDED_IS_NOT_ENOUGH,
        "SOURCES": {k: dict(v) for k, v in SOURCES.items()},
    }
