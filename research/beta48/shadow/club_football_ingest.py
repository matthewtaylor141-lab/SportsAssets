"""Ingest the Club-Football-Match-Data dataset, with its provenance stated.

Directive sections 5 and 6.

WHAT THIS SOURCE IS, AND WHOSE DATA IT ACTUALLY IS
--------------------------------------------------
`xgabora/Club-Football-Match-Data` is a GitHub-hosted dataset of 238,858 club
football matches across 38 division codes, 2000-07-28 to 2026-09-03, carrying
kick-off times, full and half-time scores, shots, shots on target, fouls,
corners, cards, Bet365 and best-of-market odds, and Elo ratings.

It is a MIRROR, not a primary source. Its own README says so:

    match results and statistics  ->  Football-Data.co.uk
    Elo ratings                   ->  ClubElo

That matters twice over.

First, **Football-Data.co.uk is the host this environment's egress policy
blocks** (CONNECT 403). So we are reading the blocked source's data through a
permitted mirror. That is not a way around the policy -- the policy is about
which hosts may be contacted, and we contact only the permitted one -- but it
does mean we CANNOT verify the mirror against its upstream, because the upstream
is unreachable. The mirror is therefore trusted only as far as it can be
cross-checked against a THIRD source, and it is: see
`public_source_linkage.verify_scores`, which compares its scores to
openfootball's, match by match.

Second, the Elo column is not all the same thing. The README states that
records through 2025-06-01 come from ClubElo and that snapshots from 2025-06-15
onward are a PROVISIONAL CONTINUATION the repository generated itself from
match results. Those are different objects with different reliability and they
are kept apart here: `SOURCE_CLUBELO_HISTORICAL` and
`REPO_PROVISIONAL_ELO_CONTINUATION`. The corpus window is 2026-08/09, which
falls entirely in the provisional era -- so every Elo value relevant to
evaluation is provisional, and treating it as ground truth would be exactly the
silent substitution the directive forbids.

THE FEATURE SPLIT IS PHYSICAL, NOT A CONVENTION
-----------------------------------------------
The dataset contains bookmaker odds. Those odds are useful and they are NOT
allowed into `P_BETTOR_INDEPENDENT`. Section 6 asks for two logically distinct
groups and this module enforces the split at parse time: `FUNDAMENTAL_COLUMNS`
and `EXTERNAL_MARKET_COLUMNS` are disjoint, their union is checked against the
real header, and `fundamental_row()` physically cannot return an odds field.

A third group matters as much: `SAME_MATCH_OUTCOME_COLUMNS`. Shots, corners,
cards and the score itself are recorded FOR the match. They are outcomes, not
priors. They may be used to build rolling features for LATER matches and may
never appear as features of their own match. They are listed separately so that
membership is checkable rather than remembered.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone

NOT_IDENTIFIED = "NOT_IDENTIFIED"

# ---------------------------------------------------------------------------
# Standing constraints
# ---------------------------------------------------------------------------

NO_ORDERS = True
NO_CAPITAL = True
NO_CREDENTIALS = True
MIRROR_LIVE = False
READS_PRODUCTION_DATABASE = False

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------

REPO = "xgabora/Club-Football-Match-Data"
BRANCH = "main"
RAW_BASE = "https://raw.githubusercontent.com"
MATCHES_PATH = "data/Matches.csv"
ELO_PATH = "data/EloRatings.csv"

SOURCE_PROVENANCE = {
    "REPOSITORY": REPO,
    "HOST_FETCHED_FROM": "raw.githubusercontent.com",
    "IS_PRIMARY_SOURCE": False,
    "ORIGINAL_UPSTREAM_SOURCES": {
        "MATCH_RESULTS_AND_STATISTICS": "Football-Data.co.uk",
        "ELO_RATINGS_THROUGH_2025_06_01": "ClubElo (clubelo.com)",
        "ELO_SNAPSHOTS_FROM_2025_06_15": "repository's own provisional "
                                         "continuation, not ClubElo",
    },
    "UPSTREAM_REACHABLE_FROM_THIS_ENVIRONMENT": False,
    "WHY_UPSTREAM_UNREACHABLE": (
        "www.football-data.co.uk is denied by the egress policy (CONNECT 403). "
        "The mirror cannot be diffed against its upstream here."),
    "HOW_IT_IS_VERIFIED_INSTEAD": (
        "match-by-match score comparison against openfootball, an independent "
        "third source, through public_source_linkage.verify_scores"),
    "LICENSE_STATUS": NOT_IDENTIFIED,
    "LICENSE_NOTE": (
        "the repository states no SPDX licence in the metadata this session "
        "can read, and the GitHub API is out of scope for this session, so the "
        "licence is recorded as NOT_IDENTIFIED rather than assumed permissive. "
        "Research use here is read-only and nothing is redistributed; any "
        "wider use needs the licence resolved first."),
    "TIMESTAMP_SEMANTICS": {
        "MatchDate": "YYYY-MM-DD, the local match date",
        "MatchTime": "HH:MM:SS, stated by the README as 'CET-1', i.e. UTC",
        "MATCHTIME_TIMEZONE_CLAIM": "UTC",
        "MATCHTIME_TIMEZONE_VERIFIED": "SEE_verify_kickoff_timezone",
    },
}

ELO_SOURCE_HISTORICAL = "SOURCE_CLUBELO_HISTORICAL"
ELO_SOURCE_PROVISIONAL = "REPO_PROVISIONAL_ELO_CONTINUATION"
ELO_PROVISIONAL_FROM = "2025-06-15"
ELO_PROVISIONAL_WARNING = (
    "Elo snapshots dated on or after %s are the repository's own continuation "
    "from the last ClubElo snapshot, not ClubElo. The venue corpus window is "
    "2026-08 and 2026-09, so EVERY Elo value used in evaluation is provisional. "
    "It may be used as a feature; it may not be described as ClubElo, and a "
    "model whose edge rests on it is resting on one repository's arithmetic."
    % ELO_PROVISIONAL_FROM)

DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "club_football")

FETCH_TIMEOUT_S = 300


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url, timeout_s=FETCH_TIMEOUT_S):
    """GET a file. A refusal is recorded, never swallowed."""
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout_s), "-w", "%{http_code}",
         "-o", "-", url], capture_output=True)
    body = proc.stdout
    status = ""
    if len(body) >= 3 and body[-3:].isdigit():
        status, body = body[-3:].decode("ascii"), body[:-3]
    return {
        "URL": url, "HTTP_STATUS": status or "000",
        "OK": status == "200" and bool(body), "BYTES": len(body),
        "SHA256": hashlib.sha256(body).hexdigest() if body else None,
        "FETCHED_AT": _now(),
        "STDERR": proc.stderr.decode("utf-8", "replace").strip()[:400],
        "_body": body,
    }


# ---------------------------------------------------------------------------
# Section 6: the feature split, enforced
# ---------------------------------------------------------------------------

IDENTITY_COLUMNS = ("Division", "MatchDate", "MatchTime", "HomeTeam", "AwayTeam")

# Known BEFORE the match. These may be features of the match itself.
FUNDAMENTAL_COLUMNS = (
    "HomeElo", "AwayElo",
    "Form3Home", "Form5Home", "Form3Away", "Form5Away",
)

# Recorded FOR the match. Outcomes. Legal only as inputs to rolling features
# for LATER matches, never as features of their own match.
SAME_MATCH_OUTCOME_COLUMNS = (
    "FTHome", "FTAway", "FTResult", "HTHome", "HTAway", "HTResult",
    "HomeShots", "AwayShots", "HomeTarget", "AwayTarget",
    "HomeFouls", "AwayFouls", "HomeCorners", "AwayCorners",
    "HomeYellow", "AwayYellow", "HomeRed", "AwayRed",
)

# Somebody else's opinion of the match. Never an input to P_BETTOR_INDEPENDENT.
EXTERNAL_MARKET_COLUMNS = (
    "OddHome", "OddDraw", "OddAway", "MaxHome", "MaxDraw", "MaxAway",
    "Over25", "Under25", "MaxOver25", "MaxUnder25",
    "HandiSize", "HandiHome", "HandiAway",
)

# The repository's own cluster model. Derived from same-match statistics by an
# undocumented fit, so it is neither fundamental nor market and is not used.
DERIVED_OPAQUE_COLUMNS = ("C_LTH", "C_LTA", "C_VHD", "C_VAD", "C_HTB", "C_PHB")

GROUPS = {
    "IDENTITY": IDENTITY_COLUMNS,
    "FUNDAMENTAL": FUNDAMENTAL_COLUMNS,
    "SAME_MATCH_OUTCOME": SAME_MATCH_OUTCOME_COLUMNS,
    "EXTERNAL_MARKET": EXTERNAL_MARKET_COLUMNS,
    "DERIVED_OPAQUE": DERIVED_OPAQUE_COLUMNS,
}

INDEPENDENT_MODEL_MAY_USE = ("IDENTITY", "FUNDAMENTAL",
                             "SAME_MATCH_OUTCOME_OF_EARLIER_MATCHES_ONLY")
INDEPENDENT_MODEL_MAY_NOT_USE = ("EXTERNAL_MARKET", "DERIVED_OPAQUE",
                                 "SAME_MATCH_OUTCOME_OF_THIS_MATCH")


def check_groups(header):
    """Every column is in exactly one group, and the groups are disjoint."""
    seen = Counter()
    for g in GROUPS.values():
        for c in g:
            seen[c] += 1
    dupes = sorted(c for c, n in seen.items() if n > 1)
    known = set(seen)
    return {
        "HEADER_COLUMNS": len(header),
        "GROUPED_COLUMNS": len(known),
        "IN_MORE_THAN_ONE_GROUP": dupes,
        "IN_HEADER_BUT_UNGROUPED": sorted(set(header) - known),
        "GROUPED_BUT_NOT_IN_HEADER": sorted(known - set(header)),
        "GROUPS_ARE_DISJOINT": not dupes,
        "EVERY_COLUMN_IS_GROUPED": not (set(header) - known),
        "INDEPENDENT_MODEL_MAY_USE": list(INDEPENDENT_MODEL_MAY_USE),
        "INDEPENDENT_MODEL_MAY_NOT_USE": list(INDEPENDENT_MODEL_MAY_NOT_USE),
    }


# ---------------------------------------------------------------------------
# Division codes
# ---------------------------------------------------------------------------
#
# Football-Data's division codes are not self-explanatory and getting one wrong
# is silent: the fixtures still parse, the scores still compare, and the model
# simply learns the wrong league. The first attempt here mapped EC to the
# English Championship. EC is the National League -- the fifth tier. The
# Championship is E1. The error surfaced only because the cross-source score
# check found one club mapped out of 96 for that league, and it is recorded
# here so it is not made again.

DIVISION_TO_VENUE_LEAGUE = {
    "E0": "epl",      # Premier League
    "E1": "elc",      # Championship  (NOT 'EC' -- that is the National League)
    "SP1": "lal", "SP2": "es2",
    "I1": "sea", "I2": "itsb",
    "D1": "bun", "D2": "bl2",
    "F1": "fl1", "F2": "fr2",
    "N1": "ere",
    "P1": "por",
    "T1": "tur",
    "B1": "bel1",
    "G1": "gre1",
    "SC0": "spl",
}

DIVISION_MAP_CAVEAT = (
    "EC is the English National League (fifth tier), not the Championship. The "
    "Championship is E1. Any division code added here must be verified by the "
    "cross-source score check before it is trusted.")


def parse_matches(body, min_date=None, divisions=None):
    """Rows from Matches.csv, grouped and typed. Nothing is dropped silently."""
    rows, counts = [], Counter()
    rd = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
    header = rd.fieldnames or []
    for r in rd:
        counts["READ"] += 1
        d = r.get("MatchDate") or ""
        if min_date and d < min_date:
            counts["BEFORE_MIN_DATE"] += 1
            continue
        div = r.get("Division")
        if divisions is not None and div not in divisions:
            counts["DIVISION_FILTERED"] += 1
            continue
        rows.append(r)
        counts["KEPT"] += 1
    return rows, header, dict(counts)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fundamental_row(r):
    """Identity plus pre-match fundamentals. Physically excludes odds.

    This is the ONLY accessor `P_BETTOR_INDEPENDENT` uses. It cannot leak an
    external-market column because it never reads one.
    """
    out = {k: r.get(k) for k in IDENTITY_COLUMNS}
    for k in FUNDAMENTAL_COLUMNS:
        out[k] = _f(r.get(k))
    out["VENUE_LEAGUE"] = DIVISION_TO_VENUE_LEAGUE.get(r.get("Division"))
    return out


def outcome_row(r):
    """The match's own result and statistics. Labels and later-match inputs."""
    out = {k: r.get(k) for k in IDENTITY_COLUMNS}
    for k in SAME_MATCH_OUTCOME_COLUMNS:
        v = r.get(k)
        out[k] = v if k.endswith("Result") else _f(v)
    out["VENUE_LEAGUE"] = DIVISION_TO_VENUE_LEAGUE.get(r.get("Division"))
    return out


def market_row(r):
    """Somebody else's price. For P_EXTERNAL_CONSENSUS only."""
    out = {k: r.get(k) for k in IDENTITY_COLUMNS}
    for k in EXTERNAL_MARKET_COLUMNS:
        out[k] = _f(r.get(k))
    out["VENUE_LEAGUE"] = DIVISION_TO_VENUE_LEAGUE.get(r.get("Division"))
    return out


# ---------------------------------------------------------------------------
# Elo, split by who actually produced it
# ---------------------------------------------------------------------------


def parse_elo(body):
    rows = []
    rd = csv.DictReader(io.StringIO(body.decode("utf-8", "replace")))
    for r in rd:
        d = r.get("date") or ""
        rows.append({
            "DATE": d, "CLUB": r.get("club"), "COUNTRY": r.get("country"),
            "ELO": _f(r.get("elo")),
            "ELO_SOURCE": (ELO_SOURCE_PROVISIONAL if d >= ELO_PROVISIONAL_FROM
                           else ELO_SOURCE_HISTORICAL),
        })
    by = Counter(r["ELO_SOURCE"] for r in rows)
    return rows, {
        "ROWS": len(rows),
        "BY_SOURCE": dict(by),
        "PROVISIONAL_FROM": ELO_PROVISIONAL_FROM,
        "PROVISIONAL_WARNING": ELO_PROVISIONAL_WARNING,
        "SOURCES_ARE_TESTED_SEPARATELY": True,
    }


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest(out_dir=DEFAULT_OUT_DIR, min_date=None, divisions=None,
           fetcher=fetch):
    os.makedirs(os.path.join(out_dir, "raw"), exist_ok=True)
    man = {}
    for name, path in (("MATCHES", MATCHES_PATH), ("ELO", ELO_PATH)):
        url = "%s/%s/%s/%s" % (RAW_BASE, REPO, BRANCH, path)
        rec = fetcher(url)
        body = rec.pop("_body", b"")
        if rec["OK"]:
            with open(os.path.join(out_dir, "raw", os.path.basename(path)),
                      "wb") as fh:
                fh.write(body)
        rec["_kept_body"] = body
        man[name] = rec

    if not man["MATCHES"]["OK"]:
        return {"STATUS": "FETCH_FAILED", "MANIFEST": man}

    rows, header, counts = parse_matches(
        man["MATCHES"].pop("_kept_body"), min_date, divisions)
    elo_rows, elo_rep = parse_elo(man["ELO"].pop("_kept_body", b"") or b"")

    dates = sorted(r["MatchDate"] for r in rows if r.get("MatchDate"))
    with_time = sum(1 for r in rows if r.get("MatchTime"))
    summary = {
        "STATUS": "OK",
        "INGESTED_AT": _now(),
        "SOURCE_PROVENANCE": SOURCE_PROVENANCE,
        "MATCHES": len(rows),
        "PARSE_ACCOUNTING": counts,
        "DIVISIONS": sorted({r["Division"] for r in rows}),
        "VENUE_LEAGUES": sorted({v for v in
                                 (DIVISION_TO_VENUE_LEAGUE.get(r["Division"])
                                  for r in rows) if v}),
        "DIVISION_MAP_CAVEAT": DIVISION_MAP_CAVEAT,
        "EARLIEST_DATE": dates[0] if dates else NOT_IDENTIFIED,
        "LATEST_DATE": dates[-1] if dates else NOT_IDENTIFIED,
        "MATCHES_WITH_KICKOFF_TIME": with_time,
        "FEATURE_COLUMNS": {k: list(v) for k, v in GROUPS.items()},
        "GROUP_CHECK": check_groups(header),
        "ELO": elo_rep,
        "MANIFEST": {k: {kk: vv for kk, vv in v.items()
                         if not kk.startswith("_")} for k, v in man.items()},
    }
    with open(os.path.join(out_dir, "manifest_v1.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False, sort_keys=True)
    return summary, rows, elo_rows


def load_matches(out_dir=DEFAULT_OUT_DIR, min_date=None, divisions=None):
    """Read the already-downloaded Matches.csv from disk."""
    p = os.path.join(out_dir, "raw", "Matches.csv")
    if not os.path.exists(p):
        return [], []
    with open(p, "rb") as fh:
        rows, header, _ = parse_matches(fh.read(), min_date, divisions)
    return rows, header


def load_elo(out_dir=DEFAULT_OUT_DIR):
    p = os.path.join(out_dir, "raw", "EloRatings.csv")
    if not os.path.exists(p):
        return [], {}
    with open(p, "rb") as fh:
        return parse_elo(fh.read())


# ---------------------------------------------------------------------------
# Verifying the kick-off timezone claim rather than believing it
# ---------------------------------------------------------------------------

KICKOFF_TZ_TEST = (
    "The README says MatchTime is 'CET-1', which is UTC. That is a claim in a "
    "README. It is testable: for a bound fixture the venue's own settlement "
    "timestamp should follow kick-off by roughly the length of a football "
    "match plus a settlement delay -- call it one and a half to three hours. "
    "If MatchTime were CET rather than UTC every gap would be an hour shorter "
    "than that, and if it were a local clock across several countries the "
    "spread would be wide and country-dependent. So: compute the gap, per "
    "country, and look at its median and its spread."
)


def verify_kickoff_timezone(pairs):
    """pairs: [(match_row, resolved_at_iso)]. Returns the measured gaps.

    No verdict is asserted here beyond the numbers; the caller reads them
    against KICKOFF_TZ_TEST. Reporting per country matters: a single median
    could hide two offsetting zones.
    """
    from datetime import timedelta  # noqa: F401
    gaps, by_country = [], defaultdict(list)
    for r, resolved in pairs or ():
        d, t = r.get("MatchDate"), r.get("MatchTime")
        if not (d and t and resolved):
            continue
        try:
            ko = datetime.strptime("%s %s" % (d, t[:8]), "%Y-%m-%d %H:%M:%S")
            ko = ko.replace(tzinfo=timezone.utc)
            rs = datetime.fromisoformat(str(resolved).replace("Z", "+00:00"))
        except ValueError:
            continue
        g = (rs - ko).total_seconds() / 3600.0
        gaps.append(g)
        by_country[r.get("Division", "?")[:1]].append(g)
    gaps.sort()

    def q(a, p):
        return a[int(p * len(a))] if a else None

    return {
        "PAIRS": len(gaps),
        "SETTLEMENT_MINUS_KICKOFF_HOURS": {
            "p10": q(gaps, .10), "p25": q(gaps, .25), "median": q(gaps, .50),
            "p75": q(gaps, .75), "p90": q(gaps, .90),
        },
        "BY_COUNTRY_MEDIAN": {k: sorted(v)[len(v) // 2]
                              for k, v in sorted(by_country.items()) if v},
        "BY_COUNTRY_N": {k: len(v) for k, v in sorted(by_country.items())},
        "WHAT_THIS_TESTS": KICKOFF_TZ_TEST,
    }


# ---------------------------------------------------------------------------
# The kick-off timezone, MEASURED (and the claim does not survive)
# ---------------------------------------------------------------------------
#
# The README says MatchTime is "CET-1", i.e. UTC. Two tests were run.
#
# TEST 1 -- against the other public source. For the 8,688 fixtures linked to
# openfootball on the same date, with a kick-off time in both:
#
#     league   n      median offset (club-football minus openfootball)
#     bun      820    -1.00   (818 of 820 exactly -1.00)
#     ere      945    -1.00
#     fl1      931    -1.00
#     lal     1161    -1.00   (1161 of 1161 exactly -1.00)
#     sea     1150    -1.00
#     elc     1691     0.00
#     epl     1041     0.00
#     por      949     0.00
#
# The offset is a fixed per-country constant and it does NOT move with daylight
# saving -- p10 and p90 are the same value as the median in every league. Two
# sources that both wrote civil local time would agree; two sources that both
# wrote UTC would agree. These differ by exactly one hour for the continental
# leagues and not at all for the UK and Portugal, all year. So at least one of
# them is not writing the zone it claims, and the pair cannot say which.
#
# TEST 2 -- against the venue's settlement stamps, which are unambiguously UTC.
# On 109 fixtures bound all the way through, settlement-minus-kick-off medians
# by country came out D 1.22, I 1.30, S 1.18, F 1.69, E 2.93, N 3.00, P 2.98
# hours. A football match plus a settlement delay is not 1.2 hours, and it is
# not two different numbers for Spain and the Netherlands, which are in the
# same zone and which test 1 showed the two sources treat identically. The
# spread is too wide and the country pattern too inconsistent to pin an
# absolute offset; the counts are small and the chain has two one-day
# tolerances in it.

MATCHTIME_TIMEZONE_VERIFIED = "NO"
MATCHTIME_TIMEZONE_STATUS = NOT_IDENTIFIED
CROSS_SOURCE_KICKOFF_OFFSET_HOURS = {
    "bun": -1.0, "ere": -1.0, "fl1": -1.0, "lal": -1.0, "sea": -1.0,
    "elc": 0.0, "epl": 0.0, "por": 0.0,
}
CROSS_SOURCE_OFFSET_IS_SEASONAL = False
KICKOFF_UNCERTAINTY_HOURS = 2.0

WHY_THE_TIMEZONE_MATTERS = (
    "A fixed pregame horizon is defined relative to kick-off. With kick-off "
    "uncertain by about two hours, T-24H and T-6H are still meaningful -- the "
    "uncertainty is small against the horizon -- but T-2H and anything shorter "
    "is not, because the error bar swallows the label.\n\n"
    "Combined with the measured density of the retained corpus, that closes "
    "the door from both sides. The horizons we can ANCHOR (T-24H, T-6H) have "
    "almost no observations: 0 and 6 events of 1,092. The horizons that have "
    "observations (inside two hours of kick-off, where 80% of the corpus sits) "
    "cannot be ANCHORED. There is currently no horizon that is both.\n\n"
    "Resolving it needs kick-off to the minute in a stated zone from a source "
    "that states it, or our own capture clock. Both are already on the table."
)

NO_HORIZON_IS_BOTH_ANCHORABLE_AND_POPULATED = True
