"""Sections 4, 5 and 9. Archival as-of provenance from repository history.

RESEARCH ONLY. No orders. No capital. No credentials. mirror_live=false.

THE PRINCIPLE
-------------
If a specific value is demonstrably present in a repository commit timestamped
before BETTOR decision time T, then

    AVAILABLE_AS_OF_T = PROVEN_ARCHIVAL

This is deliberately conservative. The real world almost certainly knew a
football score within minutes of the final whistle, long before any repository
committed it. We are not trying to establish when the world knew. We are
establishing what we can PROVE was knowable by T, and a frozen commit is proof
in a way that a plausible story is not.

WHAT THIS REPLACES
------------------
The forbidden inference, stated plainly so it cannot creep back:

    Taking today's version of a historical CSV and concluding that every
    historical row in it was available at the time those matches were played.

That inference is wrong in three ways at once -- latency, revision and
backfill -- and nothing in a single-version file distinguishes them. So for
every field used in the HIGH_INTEGRITY lane we go to the archive and prove it
from a snapshot that predates the forecast. Where repository history is
insufficient, the answer is ASOF_STATUS = NOT_PROVEN and the field is excluded.
Not downgraded with a note. Excluded.

WHAT THE ARCHIVE ACTUALLY SUPPORTS
----------------------------------
The two repositories held have very different commit cadences, and the
difference decides everything:

  openfootball/football.json -- auto-updates WEEKLY, per league file. Inside
      the evaluation window there are commits on 2026-08-24, 2026-08-26,
      2026-09-02 and 2026-09-09. A match played before one of those commits and
      present in it is PROVEN to have been knowable before any later fixture.
      This is what makes a high-integrity lane possible at all.

  xgabora/Club-Football-Match-Data -- four data commits in total, the last one
      before the evaluation window dated 2025-06-27, fourteen months early.
      Its shots, corners, cards and Elo therefore CANNOT be archivally proven
      for an August 2026 fixture. That is a fact about the repository's release
      cadence, not about the quality of its data.

So the high-integrity feature set is built from RESULTS, which openfootball
proves, and excludes shots, corners, cards, repository Elo and odds, which it
does not carry and xgabora cannot prove in time.
"""

import datetime
import hashlib
import json
import os
import subprocess
from collections import defaultdict

NOT_IDENTIFIED = "NOT_IDENTIFIED"
NOT_PROVEN = "NOT_PROVEN"
PROVEN_ARCHIVAL = "PROVEN_ARCHIVAL"

THE_FORBIDDEN_INFERENCE = (
    "taking today's version of a historical CSV and concluding that every "
    "historical value in it was available at the time")

WHY_IT_IS_FORBIDDEN = (
    "a single-version file cannot distinguish publication latency, later "
    "revision, and outright backfill. All three look identical once the data "
    "is in a column")

ARCHIVE_PROOF_IS_CONSERVATIVE = (
    "a commit proves the value was knowable BY the commit time; the world "
    "probably knew it earlier. We claim only what the archive proves")

REPOS = {
    "openfootball/football.json": {
        "LOCAL": "/home/user/openfootball/football.json",
        "CADENCE": "WEEKLY_AUTO_UPDATE_PER_LEAGUE_FILE",
        "CARRIES": ("FIXTURES", "DATES", "LOCAL_KICKOFF_TIMES", "FULL_TIME_SCORES"),
        "DOES_NOT_CARRY": ("SHOTS", "CORNERS", "CARDS", "XG", "ODDS", "ELO"),
        "USABLE_FOR_HIGH_INTEGRITY": True,
    },
    "xgabora/Club-Football-Match-Data": {
        "LOCAL": "/home/user/xgabora/club-football-match-data",
        "CADENCE": "FOUR_DATA_COMMITS_TOTAL",
        "CARRIES": ("RESULTS", "SHOTS", "TARGET", "CORNERS", "CARDS", "ODDS", "ELO"),
        "LAST_COMMIT_BEFORE_EVALUATION_WINDOW": "2025-06-27",
        "USABLE_FOR_HIGH_INTEGRITY": False,
        "WHY_NOT": (
            "the last data commit before the August 2026 evaluation window is "
            "2025-06-27, fourteen months early. Every match in the 2025-26 "
            "season and the start of 2026-27 -- exactly the recent history the "
            "rolling features need -- first appears in a commit dated "
            "2026-09-05, AFTER the fixtures being predicted"),
    },
}


def _git(repo, *args):
    return subprocess.run(("git", "-C", repo) + args, capture_output=True,
                          text=True, timeout=300).stdout


def commit_log(repo, path=None):
    """(sha, iso_timestamp) for every commit, oldest first."""
    args = ["log", "--reverse", "--format=%H\t%cI"]
    if path:
        args += ["--", path]
    out = _git(repo, *args)
    rows = []
    for line in out.splitlines():
        if "\t" in line:
            sha, ts = line.split("\t", 1)
            rows.append((sha.strip(), ts.strip()))
    return rows


def file_at(repo, sha, path):
    r = subprocess.run(("git", "-C", repo, "show", "%s:%s" % (sha, path)),
                       capture_output=True, text=True, timeout=300)
    return r.stdout if r.returncode == 0 else None


def _parse(ts):
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        d = datetime.datetime.fromisoformat(s)
    except Exception:
        try:
            d = datetime.datetime.strptime(s[:10], "%Y-%m-%d")
        except Exception:
            return None
    return d.replace(tzinfo=d.tzinfo or datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Walking openfootball's history into a first-seen index.
# ---------------------------------------------------------------------------

def _rows_from_json(text):
    """(home, away, date, ft_home, ft_away) for every PLAYED match in a file."""
    try:
        d = json.loads(text)
    except Exception:
        return []
    out = []
    for m in (d.get("matches") or []):
        sc = m.get("score")
        ft = None
        if isinstance(sc, dict):
            ft = sc.get("ft")
        elif isinstance(sc, (list, tuple)) and len(sc) == 2:
            ft = list(sc)
        if not (isinstance(ft, (list, tuple)) and len(ft) == 2):
            continue
        try:
            fh, fa = int(ft[0]), int(ft[1])
        except Exception:
            continue
        h, a, dt = m.get("team1"), m.get("team2"), m.get("date")
        if isinstance(h, dict):
            h = h.get("name")
        if isinstance(a, dict):
            a = a.get("name")
        if h and a and dt:
            out.append((str(h), str(a), str(dt)[:10], fh, fa))
    return out


def match_key(league_file, home, away, date):
    """The row key. Names come from ONE source, so no cross-source matching."""
    return "%s|%s|%s|%s" % (league_file, date, home, away)


def build_first_seen(repo=None, path_filter=None, since=None, verbose=False):
    """FIRST_PROVEN_AVAILABLE_AT for every (match, result) in the archive.

    Walks every commit oldest-first. The first commit in which a match appears
    WITH A FINAL SCORE is the moment its result became provably knowable. Later
    commits are still read, so that a value which CHANGES after first
    publication is caught and flagged rather than trusted.
    """
    repo = repo or REPOS["openfootball/football.json"]["LOCAL"]
    commits = commit_log(repo)
    if since:
        commits = [(s, t) for s, t in commits if t >= since]
    first_seen = {}       # key -> record
    changed = 0
    scanned_commits = 0
    for sha, ts in commits:
        files = [f for f in _git(repo, "ls-tree", "-r", "--name-only",
                                 sha).splitlines() if f.endswith(".json")]
        if path_filter:
            files = [f for f in files if path_filter(f)]
        if not files:
            continue
        scanned_commits += 1
        for f in files:
            text = file_at(repo, sha, f)
            if not text:
                continue
            for home, away, date, fh, fa in _rows_from_json(text):
                k = match_key(f, home, away, date)
                val = (fh, fa)
                rec = first_seen.get(k)
                if rec is None:
                    first_seen[k] = {
                        "SOURCE_REPOSITORY": "openfootball/football.json",
                        "FILE_PATH": f,
                        "COMMIT_SHA": sha,
                        "COMMIT_TIMESTAMP": ts,
                        "ROW_KEY": k,
                        "MATCH_TIMESTAMP": date,
                        "FEATURE_NAME": "FULL_TIME_SCORE",
                        "FEATURE_VALUE": val,
                        "FIRST_PROVEN_AVAILABLE_AT": ts,
                        "LAST_VERIFIED_VALUE": val,
                        "VALUE_CHANGED_LATER": "NO",
                    }
                else:
                    rec["LAST_VERIFIED_VALUE"] = val
                    if val != rec["FEATURE_VALUE"] and \
                            rec["VALUE_CHANGED_LATER"] == "NO":
                        rec["VALUE_CHANGED_LATER"] = "YES"
                        changed += 1
        if verbose:
            print("  %s %s  rows=%d" % (sha[:8], ts[:10], len(first_seen)))
    return first_seen, {
        "COMMITS_IN_REPO": len(commits),
        "COMMITS_CARRYING_DATA": scanned_commits,
        "ROWS_INDEXED": len(first_seen),
        "ROWS_WHOSE_VALUE_CHANGED_LATER": changed,
        "ARCHIVAL_PROVENANCE_STATUS": "BUILT",
    }


def proven_before(record, decision_time):
    """Was this value in a commit dated before the decision? True/False."""
    a = _parse(record.get("FIRST_PROVEN_AVAILABLE_AT"))
    b = _parse(decision_time)
    if a is None or b is None:
        return NOT_IDENTIFIED
    return a < b


def admissible_history(first_seen, decision_time, league_file=None):
    """Every archive row provably available before `decision_time`.

    This is the ONLY door through which history reaches a high-integrity
    model. A row that fails the test does not get used with a caveat; it does
    not get used.
    """
    out = []
    for k, rec in first_seen.items():
        if league_file and rec["FILE_PATH"] != league_file:
            continue
        if proven_before(rec, decision_time) is True:
            out.append(rec)
    return out


def coverage(first_seen, fixtures, decision_time_of):
    """How much of the history each fixture needs is archivally proven.

    `fixtures` are the matches being predicted; `decision_time_of` maps one to
    the moment the forecast is made. Returns the proven share and, crucially,
    the reason the rest fails -- a cadence gap is a different problem from a
    missing league.
    """
    by_file = defaultdict(list)
    for rec in first_seen.values():
        by_file[rec["FILE_PATH"]].append(rec)
    n_ok = n_no = 0
    reasons = defaultdict(int)
    for fx in fixtures:
        t = decision_time_of(fx)
        prior = [r for r in first_seen.values()
                 if r["MATCH_TIMESTAMP"] < str(fx.get("DATE", ""))]
        if not prior:
            reasons["NO_PRIOR_MATCH_IN_ARCHIVE"] += 1
            n_no += 1
            continue
        ok = [r for r in prior if proven_before(r, t) is True]
        if ok:
            n_ok += 1
        else:
            reasons["PRIOR_MATCHES_ONLY_COMMITTED_AFTER_THE_DECISION"] += 1
            n_no += 1
    return {
        "FIXTURES": len(fixtures),
        "WITH_PROVEN_PRIOR_HISTORY": n_ok,
        "WITHOUT": n_no,
        "REASONS": dict(reasons),
        "COVERAGE_PCT": 100.0 * n_ok / max(len(fixtures), 1),
    }


def provenance_rows(first_seen, limit=None):
    """ARCHIVAL_FEATURE_PROVENANCE, the record the directive asked for."""
    rows = sorted(first_seen.values(), key=lambda r: r["MATCH_TIMESTAMP"])
    if limit:
        rows = rows[:limit]
    return [{k: r[k] for k in (
        "SOURCE_REPOSITORY", "FILE_PATH", "COMMIT_SHA", "COMMIT_TIMESTAMP",
        "ROW_KEY", "MATCH_TIMESTAMP", "FEATURE_NAME", "FEATURE_VALUE",
        "FIRST_PROVEN_AVAILABLE_AT", "LAST_VERIFIED_VALUE",
        "VALUE_CHANGED_LATER")} for r in rows]


def index_digest(first_seen):
    """A stable hash of the archive index, so a report can be checked."""
    h = hashlib.sha256()
    for k in sorted(first_seen):
        r = first_seen[k]
        h.update(("%s|%s|%s\n" % (k, r["FIRST_PROVEN_AVAILABLE_AT"],
                                  r["FEATURE_VALUE"])).encode())
    return h.hexdigest()


def describe():
    return {
        "PRINCIPLE": ("a value in a commit timestamped before T is "
                      "AVAILABLE_AS_OF_T = PROVEN_ARCHIVAL"),
        "THE_FORBIDDEN_INFERENCE": THE_FORBIDDEN_INFERENCE,
        "WHY_IT_IS_FORBIDDEN": WHY_IT_IS_FORBIDDEN,
        "ARCHIVE_PROOF_IS_CONSERVATIVE": ARCHIVE_PROOF_IS_CONSERVATIVE,
        "REPOS": {k: dict(v) for k, v in REPOS.items()},
    }
