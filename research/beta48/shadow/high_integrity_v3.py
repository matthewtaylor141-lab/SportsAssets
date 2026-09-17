"""Sections 12, 13 and 14. Two lanes, built from what each can prove.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

LANE A -- HIGH_INTEGRITY
    Uses only PROVEN_NATIVE, PROVEN_ARCHIVAL and PROVEN_DERIVED features.
    Every input is a match result whose availability before the forecast is
    proven by an openfootball commit timestamp, or a deterministic causal
    transformation of such results. This is the only lane eligible to become
    BETTOR independent fair value.

LANE B -- RESEARCH
    May additionally use CONSERVATIVELY_BOUNDED features: shots, shots on
    target, corners, and the rest of the xgabora match statistics. It exists to
    estimate the CEILING that richer information would reach. Every result it
    produces is labelled RESEARCH_ONLY and it can never earn production status.

The comparison between the lanes is the point. If the research lane improves
materially while the high-integrity lane does not, that is not a modelling
disappointment -- it is a measurement of what better-timestamped data is worth,
and it becomes a procurement case.

WHAT IS NOT EXPECTED
--------------------
Nothing here assumes the high-integrity model will be better. It has strictly
less information than the research lane by construction. The experiment asks
one question:

    does a truly as-of defensible independent model add information?

and a negative answer is a result, provided the sample can support one.
"""

import datetime
from collections import defaultdict, deque

import asof_provenance as AP
import bettor_internal_elo as ELO

NOT_IDENTIFIED = "NOT_IDENTIFIED"

LANE_HIGH_INTEGRITY = "HIGH_INTEGRITY"
LANE_RESEARCH = "RESEARCH"

RESEARCH_RESULTS_ARE_LABELLED = "RESEARCH_ONLY"
RESEARCH_LANE_MAY_NOT_EARN_PRODUCTION_STATUS = True

HIGH_INTEGRITY_MODELS = (
    "HI_INTERNAL_ELO",
    "HI_POISSON",
    "HI_DIXON_COLES",
    "HI_DYNAMIC_ATTACK_DEFENCE",
    "HI_REGULARIZED_FUNDAMENTALS",
    "HI_GRADIENT_BOOSTED_FUNDAMENTALS",
)

# The decision time for a fixture. Deliberately conservative: midnight UTC on
# the day of the match, NOT kickoff. Start-time class A is empty, so we do not
# know kickoff to better than about an hour, and a feature must be proven
# before the EARLIEST moment the forecast could have been made.
DECISION_TIME_RULE = "MIDNIGHT_UTC_ON_THE_MATCH_DATE"
WHY_MIDNIGHT = (
    "start-time class A is empty, so kickoff is known only to about an hour. "
    "Anchoring the as-of cut at midnight UTC on the match day is earlier than "
    "any plausible kickoff and therefore cannot flatter the model")


def decision_time(date_str):
    return "%sT00:00:00+00:00" % str(date_str)[:10]


# ---------------------------------------------------------------------------
# The feature builder. One forward pass, strictly causal, archive-gated.
# ---------------------------------------------------------------------------

def _days(a, b):
    try:
        da = datetime.date.fromisoformat(str(a)[:10])
        db = datetime.date.fromisoformat(str(b)[:10])
        return (da - db).days
    except Exception:
        return None


def build_high_integrity_frame(archive_matches, fixtures=None):
    """Features for every match, using only earlier matches.

    `archive_matches` are rows with DATE, LEAGUE, HOME, AWAY, FT_HOME, FT_AWAY
    and FIRST_PROVEN_AVAILABLE_AT -- already filtered to what the archive
    proves. The pass is forward and state is read before each match is
    consumed, so no row can see its own result or any later one.

    The archive gate is applied here, not later: a prior match contributes to a
    fixture's features only if its FIRST_PROVEN_AVAILABLE_AT precedes that
    fixture's decision time. Two matches on the same day can therefore differ
    in what they are allowed to know, which is correct and is exactly what a
    single-version CSV cannot represent.
    """
    ms = sorted(archive_matches, key=lambda m: (m["DATE"], m["HOME"], m["AWAY"]))
    elo = ELO.InternalElo()
    gd_elo = ELO.InternalElo(goal_difference=True)
    ewma = ELO.EwmaForm()

    gf, ga = defaultdict(lambda: deque(maxlen=10)), defaultdict(lambda: deque(maxlen=10))
    gf_home, ga_home = defaultdict(lambda: deque(maxlen=5)), defaultdict(lambda: deque(maxlen=5))
    gf_away, ga_away = defaultdict(lambda: deque(maxlen=5)), defaultdict(lambda: deque(maxlen=5))
    form = defaultdict(lambda: deque(maxlen=5))
    last_played = {}
    # when each club's most recent contributing result became provable
    club_last_proven = {}

    out = []
    for m in ms:
        h, a, d = m["HOME"], m["AWAY"], m["DATE"]
        e = elo.read_before(h, a)
        g = gd_elo.read_before(h, a)
        w = ewma.read_before(h, a)

        def mean(dq, n=None):
            v = list(dq)[-n:] if n else list(dq)
            return (sum(v) / len(v)) if v else None

        row = {
            "DATE": d, "LEAGUE": m.get("LEAGUE"), "HOME": h, "AWAY": a,
            "FT_HOME": m.get("FT_HOME"), "FT_AWAY": m.get("FT_AWAY"),
            "HI_ELO_HOME": e["INT_ELO_HOME"], "HI_ELO_AWAY": e["INT_ELO_AWAY"],
            "HI_ELO_DIFF": e["INT_ELO_DIFF"],
            "HI_ELO_EXPECTED_HOME": e["INT_ELO_EXPECTED_HOME"],
            "HI_GD_ELO_HOME": g["INT_ELO_HOME"], "HI_GD_ELO_AWAY": g["INT_ELO_AWAY"],
            "HI_GD_ELO_DIFF": g["INT_ELO_DIFF"],
            "HI_GD_ELO_EXPECTED_HOME": g["INT_ELO_EXPECTED_HOME"],
            "HI_EWMA_GF_HOME": w["INT_EWMA_GF_HOME"],
            "HI_EWMA_GA_HOME": w["INT_EWMA_GA_HOME"],
            "HI_EWMA_GF_AWAY": w["INT_EWMA_GF_AWAY"],
            "HI_EWMA_GA_AWAY": w["INT_EWMA_GA_AWAY"],
            "HI_GF5_HOME": mean(gf[h], 5), "HI_GA5_HOME": mean(ga[h], 5),
            "HI_GF5_AWAY": mean(gf[a], 5), "HI_GA5_AWAY": mean(ga[a], 5),
            "HI_GF10_HOME": mean(gf[h]), "HI_GA10_HOME": mean(ga[h]),
            "HI_GF10_AWAY": mean(gf[a]), "HI_GA10_AWAY": mean(ga[a]),
            "HI_HOME_GF5_AT_HOME": mean(gf_home[h]),
            "HI_HOME_GA5_AT_HOME": mean(ga_home[h]),
            "HI_AWAY_GF5_AT_AWAY": mean(gf_away[a]),
            "HI_AWAY_GA5_AT_AWAY": mean(ga_away[a]),
            "HI_FORM5_HOME": mean(form[h]), "HI_FORM5_AWAY": mean(form[a]),
            "HI_REST_DAYS_HOME": _days(d, last_played.get(h)) if h in last_played else None,
            "HI_REST_DAYS_AWAY": _days(d, last_played.get(a)) if a in last_played else None,
            "HI_MATCHES_SEEN_HOME": e["INT_ELO_HOME_MATCHES"],
            "HI_MATCHES_SEEN_AWAY": e["INT_ELO_AWAY_MATCHES"],
            # the latest moment any contributing prior result became provable;
            # the fixture may use this row only if its decision time is later
            "FEATURES_PROVEN_BY": max(
                [t for t in (club_last_proven.get(h), club_last_proven.get(a))
                 if t] or [None]) if (club_last_proven.get(h)
                                      or club_last_proven.get(a)) else None,
        }
        out.append(row)

        fh, fa = m.get("FT_HOME"), m.get("FT_AWAY")
        if fh is None or fa is None:
            continue
        elo.update(h, a, fh, fa, d)
        gd_elo.update(h, a, fh, fa, d)
        ewma.update(h, a, fh, fa)
        gf[h].append(fh); ga[h].append(fa)
        gf[a].append(fa); ga[a].append(fh)
        gf_home[h].append(fh); ga_home[h].append(fa)
        gf_away[a].append(fa); ga_away[a].append(fh)
        form[h].append(1.0 if fh > fa else (0.5 if fh == fa else 0.0))
        form[a].append(1.0 if fa > fh else (0.5 if fh == fa else 0.0))
        last_played[h] = last_played[a] = d
        pv = m.get("FIRST_PROVEN_AVAILABLE_AT")
        if pv:
            club_last_proven[h] = max(club_last_proven.get(h, ""), pv)
            club_last_proven[a] = max(club_last_proven.get(a, ""), pv)
    return out


def archive_gate(frame_row, fixture_decision_time):
    """May this feature row be used at that decision time? Fail closed.

    A row whose contributing history has no proving timestamp at all is
    REFUSED, not waved through. NOT_IDENTIFIED is never a pass.
    """
    pv = frame_row.get("FEATURES_PROVEN_BY")
    if not pv:
        return False, "NO_PROVING_COMMIT_FOR_THE_CONTRIBUTING_HISTORY"
    if pv < fixture_decision_time:
        return True, AP.PROVEN_ARCHIVAL
    return False, "CONTRIBUTING_HISTORY_COMMITTED_AFTER_THE_DECISION"


def gate_report(frame, decision_time_of):
    ok = refused = 0
    reasons = defaultdict(int)
    for r in frame:
        passed, why = archive_gate(r, decision_time_of(r))
        if passed:
            ok += 1
        else:
            refused += 1
            reasons[why] += 1
    return {"ROWS": len(frame), "ADMITTED": ok, "REFUSED": refused,
            "REFUSAL_REASONS": dict(reasons),
            "ADMITTED_PCT": 100.0 * ok / max(len(frame), 1)}


def feature_lane(lane=LANE_HIGH_INTEGRITY, extra=()):
    """The feature list a lane may use, filtered through the provenance gate."""
    cand = list(AP.HIGH_INTEGRITY_FEATURES) + list(extra)
    keep = [f for f in cand if AP.admit(f, lane)[0]]
    return keep


def describe():
    return {
        "LANES": {
            LANE_HIGH_INTEGRITY: dict(AP.LANE_RULES["HIGH_INTEGRITY"]),
            LANE_RESEARCH: dict(AP.LANE_RULES["RESEARCH"]),
        },
        "HIGH_INTEGRITY_MODELS": HIGH_INTEGRITY_MODELS,
        "DECISION_TIME_RULE": DECISION_TIME_RULE,
        "WHY_MIDNIGHT": WHY_MIDNIGHT,
        "RESEARCH_RESULTS_ARE_LABELLED": RESEARCH_RESULTS_ARE_LABELLED,
        "RESEARCH_LANE_MAY_NOT_EARN_PRODUCTION_STATUS":
            RESEARCH_LANE_MAY_NOT_EARN_PRODUCTION_STATUS,
        "NOT_EXPECTED_TO_IMPROVE": (
            "the high-integrity lane has strictly less information than the "
            "research lane by construction. The experiment asks whether a "
            "truly as-of defensible model adds information, and a negative "
            "answer is a result"),
    }
