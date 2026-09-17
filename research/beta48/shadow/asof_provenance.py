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

# ---------------------------------------------------------------------------
# Section 6. Five classes. The earlier two-value split was too coarse to carry
# the distinction that matters -- between a value we can PROVE was published
# before the forecast and one we merely believe was.
# ---------------------------------------------------------------------------

PROVEN_NATIVE = "PROVEN_NATIVE"
# the venue or source carries an authoritative timestamp of availability

PROVEN_ARCHIVAL = "PROVEN_ARCHIVAL"
# the value demonstrably existed in a frozen archival snapshot before decision T

PROVEN_DERIVED = "PROVEN_DERIVED"
# a deterministic causal transformation of PROVEN_NATIVE / PROVEN_ARCHIVAL
# inputs, with a frozen algorithm that reads state strictly before the match
# it consumes (section 7)

CONSERVATIVELY_BOUNDED = "CONSERVATIVELY_BOUNDED"
# availability can be bounded from event mechanics but is not timestamped

NOT_PROVEN = "NOT_PROVEN"
# no defensible availability bound

# Retained so older records and tests resolve; it maps onto the new scheme.
PROVEN = PROVEN_NATIVE
ASSUMED_BOUNDED = CONSERVATIVELY_BOUNDED

CLASSES = (PROVEN_NATIVE, PROVEN_ARCHIVAL, PROVEN_DERIVED,
           CONSERVATIVELY_BOUNDED, NOT_PROVEN)

THE_RULE = (
    "No feature earns admission merely because it existed somewhere in a "
    "historical database later.")

LANES = ("HIGH_INTEGRITY", "RESEARCH", "EXPLORATORY")

HIGH_INTEGRITY_ADMITS = (PROVEN_NATIVE, PROVEN_ARCHIVAL, PROVEN_DERIVED)
RESEARCH_ADMITS = HIGH_INTEGRITY_ADMITS + (CONSERVATIVELY_BOUNDED,)

D_IS_EXCLUDED_FROM_ANY_ASOF_MODEL = True

LANE_RULES = {
    "HIGH_INTEGRITY": {
        "ADMITS": HIGH_INTEGRITY_ADMITS,
        "MAY_BECOME_BETTOR_INDEPENDENT_FAIR_VALUE": True,
        "NOTE": "the only lane eligible to become BETTOR independent fair value",
    },
    "RESEARCH": {
        "ADMITS": RESEARCH_ADMITS,
        "MAY_BECOME_BETTOR_INDEPENDENT_FAIR_VALUE": False,
        "NOTE": ("tells us whether richer information looks promising. It can "
                 "never directly earn production status, and every result it "
                 "produces is labelled RESEARCH_ONLY"),
    },
    "EXPLORATORY": {
        "ADMITS": CLASSES,
        "MAY_BECOME_BETTOR_INDEPENDENT_FAIR_VALUE": False,
        "NOTE": "anything with a declared source; diagnostics only",
    },
}

WHY_RESULTS_SEPARATE_LANES = (
    "if the research lane improves materially while the high-integrity lane "
    "does not, that is not a disappointment. It is a measurement of what "
    "better-timestamped data would be worth, and it becomes a procurement "
    "case rather than a modelling one")

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
    derived = PROVEN_NATIVE if publication_timestamp_available and \
        data_became_known_field else NOT_PROVEN
    # A caller may only move a source DOWN the ladder, never up. Declaring
    # PROVEN_NATIVE without a publication timestamp is exactly the claim this
    # module exists to refuse.
    if status in (PROVEN_ARCHIVAL, PROVEN_DERIVED) and derived == NOT_PROVEN:
        derived = status              # proven by the archive, not by a stamp
    elif status == CONSERVATIVELY_BOUNDED and derived == NOT_PROVEN:
        derived = CONSERVATIVELY_BOUNDED
    elif status == NOT_PROVEN:
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
    "OPENFOOTBALL_ARCHIVAL_RESULTS": source_record(
        "openfootball/football.json, proven from repository commit history",
        # NOT a native publication stamp: the provider publishes no timestamp.
        # The proof is the frozen commit, which is why this is class B and not
        # class A. The distinction is the whole point of having both.
        publication_timestamp_available=False,
        event_timestamp_field="match date in the committed file",
        data_became_known_field="FIRST_PROVEN_AVAILABLE_AT (commit timestamp)",
        status=PROVEN_ARCHIVAL,
        revision_risk="MEASURED",
        detail=("233 data commits, 44,764 match results indexed, weekly "
                "auto-updates. Each result carries the timestamp of the first "
                "commit that contained it, so availability before a later "
                "fixture is PROVEN rather than assumed. 120 of the 44,764 "
                "rows changed value after first publication and are flagged "
                "VALUE_CHANGED_LATER=YES rather than trusted")),
    "XGABORA_MATCH_RESULTS": source_record(
        "xgabora/Club-Football-Match-Data (FTHome/FTAway)",
        publication_timestamp_available=False,
        event_timestamp_field="MatchDate (+ MatchTime, timezone unverified)",
        data_became_known_field=None,
        status=CONSERVATIVELY_BOUNDED,
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
        status=CONSERVATIVELY_BOUNDED,
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
        status=CONSERVATIVELY_BOUNDED,
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
        status=PROVEN_NATIVE,
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
        status=PROVEN_NATIVE,
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

# ---------------------------------------------------------------------------
# Section 7. The high-integrity feature set, rebuilt on the archive.
#
# Every feature here is a deterministic causal transformation of openfootball
# match results whose availability before the forecast is proven by a commit
# timestamp. The algorithm is frozen and reads state strictly before the match
# it consumes (bettor_internal_elo.assert_causal), so the outputs inherit the
# status of the inputs: PROVEN_DERIVED.
#
# Note what is NOT here: shots, shots on target, corners, cards, repository
# Elo, odds, xG. Not because they are useless -- because the archive cannot
# prove them in time, and the standard is not being lowered to admit them.
# ---------------------------------------------------------------------------

HIGH_INTEGRITY_FEATURES = (
    "HI_ELO_HOME", "HI_ELO_AWAY", "HI_ELO_DIFF", "HI_ELO_EXPECTED_HOME",
    "HI_GD_ELO_HOME", "HI_GD_ELO_AWAY", "HI_GD_ELO_DIFF",
    "HI_GD_ELO_EXPECTED_HOME",
    "HI_EWMA_GF_HOME", "HI_EWMA_GA_HOME", "HI_EWMA_GF_AWAY", "HI_EWMA_GA_AWAY",
    "HI_GF5_HOME", "HI_GA5_HOME", "HI_GF5_AWAY", "HI_GA5_AWAY",
    "HI_GF10_HOME", "HI_GA10_HOME", "HI_GF10_AWAY", "HI_GA10_AWAY",
    "HI_HOME_GF5_AT_HOME", "HI_HOME_GA5_AT_HOME",
    "HI_AWAY_GF5_AT_AWAY", "HI_AWAY_GA5_AT_AWAY",
    "HI_FORM5_HOME", "HI_FORM5_AWAY",
    "HI_REST_DAYS_HOME", "HI_REST_DAYS_AWAY",
    "HI_MATCHES_SEEN_HOME", "HI_MATCHES_SEEN_AWAY",
)

for _f in HIGH_INTEGRITY_FEATURES:
    FEATURE_SOURCES[_f] = "OPENFOOTBALL_ARCHIVAL_RESULTS"

DERIVED_FEATURES = set(HIGH_INTEGRITY_FEATURES)

FEATURES_DELIBERATELY_EXCLUDED_FROM_HIGH_INTEGRITY = {
    "SHOTS_TARGET_CORNERS_CARDS": (
        "carried only by xgabora, whose last data commit before the August "
        "2026 evaluation window is 2025-06-27 -- fourteen months early. The "
        "recent history the rolling features need first appears in a commit "
        "dated 2026-09-05, AFTER the fixtures being predicted"),
    "REPOSITORY_ELO": (
        "NOT_PROVEN on revision grounds regardless of cadence; replaced by "
        "BETTOR_INTERNAL_ELO, which is PROVEN_DERIVED"),
    "ODDS": "excluded from P_BETTOR_INDEPENDENT on separate grounds as well",
    "XG": "no series held; XG_DATA_STATUS = NOT_IDENTIFIED",
    "PLAYER_AVAILABILITY": "no announcement-time source held",
}


def feature_status(feature):
    """A feature's own class, which is not always its source's class.

    A deterministic causal transformation of proven inputs is PROVEN_DERIVED,
    one step down from its inputs but still inside the high-integrity lane.
    Anything else simply carries its source's class.
    """
    src = FEATURE_SOURCES.get(feature)
    if src is None:
        return NOT_IDENTIFIED
    st = status_of(src)
    if feature in DERIVED_FEATURES and st in (PROVEN_NATIVE, PROVEN_ARCHIVAL):
        return PROVEN_DERIVED
    return st


def feature_census(features):
    """Counts by class -- the numbers the report asks for."""
    out = {c: [] for c in CLASSES}
    out[NOT_IDENTIFIED] = []
    for f in features:
        out.setdefault(feature_status(f), []).append(f)
    return {k: {"COUNT": len(v), "FEATURES": v} for k, v in out.items() if v}


def admit(feature, lane="HIGH_INTEGRITY"):
    """May this feature enter this lane? Returns (bool, reason)."""
    if lane not in LANES:
        return False, "UNKNOWN_LANE"
    if feature not in FEATURE_SOURCES:
        return False, "FEATURE_HAS_NO_DECLARED_SOURCE"
    st = feature_status(feature)
    if lane == "EXPLORATORY":
        return st != NOT_IDENTIFIED, st
    if lane == "RESEARCH":
        return st in RESEARCH_ADMITS, st
    return st in HIGH_INTEGRITY_ADMITS, st


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
        st = feature_status(f)
        if st not in HIGH_INTEGRITY_ADMITS:
            unproven.append({"FEATURE": f,
                             "SOURCE": FEATURE_SOURCES.get(f, NOT_IDENTIFIED),
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
