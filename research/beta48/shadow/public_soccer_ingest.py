"""Reproducible ingestion of PUBLIC soccer results.

Directive section 12. Nothing here touches the venue, production, credentials or
capital. It reads public files over HTTPS, records provenance for every byte, and
writes a normalised fixture table to disk.

WHY THIS EXISTS
---------------
`P_MARKET_RAW` (B0) and `P_MARKET_SURFACE` are both derived from venue prices.
Neither is an independent opinion. An independent opinion needs an input the
venue did not produce. Match results are that input: they are the ground truth
the venue's own contracts settle against, they are published openly, and they are
available for seasons the venue corpus never covered.

EGRESS
------
This environment's proxy enforces a host policy. Measured 2026-09-17:

    raw.githubusercontent.com    PERMITTED  (HTTP 200)
    api.github.com               PERMITTED  (HTTP 200)
    www.football-data.co.uk      DENIED     (CONNECT 403, connect_rejected)
    api.football-data.org        DENIED     (CONNECT 403, connect_rejected)
    datahub.io                   DENIED     (CONNECT 403, connect_rejected)

So the football-data.co.uk route named in the directive is unavailable, but the
block is host-scoped rather than blanket, and two permitted sources carry what is
needed. We use them and record the refusal rather than reporting section 12 as
blocked.

SOURCES
-------
openfootball/football.json  -- season files of fixtures and full-time scores for
                               the leagues the venue corpus actually trades, in
                               the seasons it actually trades them. Club naming
                               is the long form ("Brighton & Hove Albion FC").
statsbomb/open-data         -- event-level data. Ingested for metadata only here:
                               its competition coverage stops at 2023/24-2025 and
                               does not overlap the corpus window, so it cannot
                               rate the clubs in the corpus. Recorded so the gap
                               is a measured fact rather than an omission.

PROVENANCE
----------
Every fetched file is written verbatim with a sidecar record carrying url, http
status, byte count, sha256 and the fetch timestamp. A downstream model that
cannot name the sha256 of its inputs is not reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import unicodedata
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Standing constraints
# ---------------------------------------------------------------------------

NO_ORDERS = True
NO_CAPITAL = True
NO_CREDENTIALS = True
MIRROR_LIVE = False
READS_PRODUCTION_DATABASE = False
TOUCHES_RUN85 = False

# ---------------------------------------------------------------------------
# Egress findings (section 12)
# ---------------------------------------------------------------------------

PUBLIC_SOCCER_DATA_STATUS = "INGESTED_FROM_PERMITTED_HOSTS"

EGRESS_PROBE_AT = "2026-09-17T13:31Z"
EGRESS_PERMITTED = ("raw.githubusercontent.com", "api.github.com")
EGRESS_DENIED = (
    "www.football-data.co.uk",
    "api.football-data.org",
    "datahub.io",
)
EGRESS_DENIAL_TEXT = (
    "curl: (56) CONNECT tunnel failed, response 403 -- the agent proxy records "
    "kind=connect_rejected, detail='gateway answered 403 to CONNECT (policy "
    "denial or upstream failure)'. The proxy reports selective=false, so this is "
    "an upstream host policy, not a per-tool scope."
)
EGRESS_BLOCK_IS_BLANKET = False

RAW_BASE = "https://raw.githubusercontent.com"
OPENFOOTBALL_REPO = "openfootball/football.json"
STATSBOMB_REPO = "statsbomb/open-data"

# ---------------------------------------------------------------------------
# What to ingest
# ---------------------------------------------------------------------------

# openfootball league file -> the venue league code that names the same
# competition. The mapping is a statement about which competition a file
# describes; it is NOT a team-identity decision and never binds an event.
LEAGUE_FILES = {
    "en.1": "epl",
    "en.2": "elc",
    "es.1": "lal",
    "it.1": "sea",
    "de.1": "bun",
    "fr.1": "fl1",
    "nl.1": "ere",
    "pt.1": "por",
}

# The venue corpus settles events dated 2026-08 and 2026-09, i.e. the opening
# weeks of 2026-27. Three prior seasons supply the ratings prior; without them a
# model trained on the corpus window alone has ~30 matches per league.
SEASONS = ("2023-24", "2024-25", "2025-26", "2026-27")

DEFAULT_OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "public_soccer",
)

FETCH_TIMEOUT_S = 40


# ---------------------------------------------------------------------------
# Fetch with provenance
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url, timeout_s=FETCH_TIMEOUT_S):
    """GET `url`. Return a provenance record; never raise on an HTTP failure.

    Uses curl so the environment's proxy and CA bundle apply unchanged. A refusal
    is data -- it is recorded, not swallowed.
    """
    proc = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout_s),
         "-w", "%{http_code}", "-o", "-", url],
        capture_output=True,
    )
    body = proc.stdout
    status = ""
    if len(body) >= 3 and body[-3:].isdigit():
        status, body = body[-3:].decode("ascii"), body[:-3]
    ok = status == "200" and bool(body)
    return {
        "URL": url,
        "HTTP_STATUS": status or "000",
        "OK": ok,
        "BYTES": len(body),
        "SHA256": hashlib.sha256(body).hexdigest() if body else None,
        "FETCHED_AT": _now(),
        "STDERR": proc.stderr.decode("utf-8", "replace").strip()[:400],
        "_body": body,
    }


def season_url(season, league_file):
    return "%s/%s/master/%s/%s.json" % (
        RAW_BASE, OPENFOOTBALL_REPO, season, league_file)


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalise_club(name):
    """Fold a club name to a comparison key.

    Case, surrounding whitespace and Unicode composition only. Diacritics are
    KEPT: 'Koln' and 'Koln' are different strings in a source that writes
    '1. FC Koeln' with an umlaut, and silently stripping accents is the first
    step of fuzzy matching. We want exact equality or a refusal.
    """
    if name is None:
        return None
    s = unicodedata.normalize("NFC", str(name))
    s = " ".join(s.split())
    return s.casefold()


def _score_pair(match):
    """Read (full_time, half_time) out of a match, whichever shape it is in.

    openfootball is not schema-stable across its season files. Three shapes
    appear in the files we fetch:

        {"score": {"ft": [3, 0], "ht": [2, 0]}}   the common one
        {"score": [3, 0]}                          a bare full-time pair
        {"score1": 3, "score2": 0}                 the older flat form

    Anything else returns (None, None) and the fixture is carried as not played.
    We do not guess: a shape we cannot read is an unplayed fixture, never a 0-0.
    """
    score = match.get("score")
    if isinstance(score, dict):
        ft = score.get("ft")
        ht = score.get("ht")
        ft = list(ft) if isinstance(ft, (list, tuple)) and len(ft) == 2 else None
        ht = list(ht) if isinstance(ht, (list, tuple)) and len(ht) == 2 else None
        return ft, ht
    if isinstance(score, (list, tuple)) and len(score) == 2:
        return list(score), None
    if match.get("score1") is not None and match.get("score2") is not None:
        return [match["score1"], match["score2"]], None
    return None, None


def parse_season_file(payload, season, league_file, venue_league):
    """Turn one openfootball season file into fixture rows.

    A fixture with no full-time score is retained with PLAYED=False: the fixture
    list itself is evidence (it says a match is scheduled), and a model that only
    ever sees played matches cannot be asked for a forward prediction.
    """
    rows = []
    for m in payload.get("matches") or ():
        ft, ht = _score_pair(m)
        played = bool(ft) and len(ft) == 2 and all(x is not None for x in ft)
        rows.append({
            "SEASON": season,
            "LEAGUE_FILE": league_file,
            "VENUE_LEAGUE": venue_league,
            "COMPETITION": payload.get("name"),
            "ROUND": m.get("round"),
            "DATE": m.get("date"),
            "TIME": m.get("time"),
            "HOME": m.get("team1"),
            "AWAY": m.get("team2"),
            "HOME_KEY": normalise_club(m.get("team1")),
            "AWAY_KEY": normalise_club(m.get("team2")),
            "PLAYED": played,
            "FT_HOME": ft[0] if played else None,
            "FT_AWAY": ft[1] if played else None,
            "HT_HOME": ht[0] if ht and len(ht) == 2 else None,
            "HT_AWAY": ht[1] if ht and len(ht) == 2 else None,
        })
    return rows


def ingest(out_dir=DEFAULT_OUT_DIR, seasons=SEASONS, league_files=None,
           fetcher=fetch):
    """Fetch every (season, league) file, write raw bytes plus a manifest.

    Returns a summary dict. Missing files (a league that did not exist that
    season, a season not yet published) are recorded as MISSING, not as an error:
    openfootball genuinely has no `es.2` for 2026-27 yet.
    """
    league_files = dict(league_files or LEAGUE_FILES)
    raw_dir = os.path.join(out_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    manifest, fixtures = [], []
    for season in seasons:
        for lf, venue_league in sorted(league_files.items()):
            url = season_url(season, lf)
            rec = fetcher(url)
            body = rec.pop("_body", b"")
            rec["SEASON"], rec["LEAGUE_FILE"] = season, lf
            rec["VENUE_LEAGUE"] = venue_league
            if not rec["OK"]:
                rec["STATUS"] = "MISSING" if rec["HTTP_STATUS"] == "404" \
                    else "FETCH_FAILED"
                rec["MATCHES"] = 0
                manifest.append(rec)
                continue
            path = os.path.join(raw_dir, "%s_%s.json" % (season, lf))
            with open(path, "wb") as fh:
                fh.write(body)
            payload = json.loads(body.decode("utf-8"))
            rows = parse_season_file(payload, season, lf, venue_league)
            fixtures.extend(rows)
            rec["STATUS"] = "OK"
            rec["MATCHES"] = len(rows)
            rec["PLAYED"] = sum(1 for r in rows if r["PLAYED"])
            rec["LOCAL_PATH"] = os.path.relpath(path, out_dir)
            manifest.append(rec)

    fixtures.sort(key=lambda r: (r["DATE"] or "", r["VENUE_LEAGUE"],
                                 r["HOME_KEY"] or "", r["AWAY_KEY"] or ""))
    fx_path = os.path.join(out_dir, "fixtures_v1.jsonl")
    with open(fx_path, "w", encoding="utf-8") as fh:
        for r in fixtures:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    summary = {
        "PUBLIC_SOCCER_DATA_STATUS": PUBLIC_SOCCER_DATA_STATUS,
        "INGESTED_AT": _now(),
        "SOURCE_REPO": OPENFOOTBALL_REPO,
        "FILES_REQUESTED": len(manifest),
        "FILES_OK": sum(1 for m in manifest if m["STATUS"] == "OK"),
        "FILES_MISSING": sum(1 for m in manifest if m["STATUS"] == "MISSING"),
        "FILES_FAILED": sum(1 for m in manifest if m["STATUS"] == "FETCH_FAILED"),
        "FIXTURES": len(fixtures),
        "FIXTURES_PLAYED": sum(1 for r in fixtures if r["PLAYED"]),
        "DISTINCT_CLUBS": len({k for r in fixtures
                               for k in (r["HOME_KEY"], r["AWAY_KEY"]) if k}),
        "SEASONS": list(seasons),
        "VENUE_LEAGUES": sorted(set(league_files.values())),
        "EGRESS_DENIED": list(EGRESS_DENIED),
        "FIXTURES_PATH": os.path.relpath(fx_path, out_dir),
        "MANIFEST": manifest,
    }
    with open(os.path.join(out_dir, "manifest_v1.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, ensure_ascii=False, sort_keys=True)
    return summary


def load_fixtures(out_dir=DEFAULT_OUT_DIR):
    path = os.path.join(out_dir, "fixtures_v1.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ---------------------------------------------------------------------------
# StatsBomb: recorded, and recorded as non-overlapping
# ---------------------------------------------------------------------------

STATSBOMB_COMPETITIONS_URL = (
    "%s/%s/master/data/competitions.json" % (RAW_BASE, STATSBOMB_REPO))

STATSBOMB_USE = "METADATA_ONLY"
STATSBOMB_WHY_NOT_USED_FOR_RATINGS = (
    "Its latest season for any competition in the venue corpus is 2023/24, and "
    "for most it is 2015/16-2020/21. The corpus settles 2026-08 and 2026-09 "
    "fixtures. Club strength three to ten seasons stale is not a usable prior "
    "for this window, and squads have turned over entirely. Event-level "
    "StatsBomb data becomes valuable only if a contemporaneous feed is bought."
)


def statsbomb_coverage(fetcher=fetch):
    """Record what StatsBomb open-data actually covers. No ratings are built."""
    rec = fetcher(STATSBOMB_COMPETITIONS_URL)
    body = rec.pop("_body", b"")
    if not rec["OK"]:
        return {"STATUS": "UNAVAILABLE", "PROVENANCE": rec}
    comps = json.loads(body.decode("utf-8"))
    latest = {}
    for c in comps:
        name = c.get("competition_name")
        season = c.get("season_name")
        if name and season and season > latest.get(name, ""):
            latest[name] = season
    return {
        "STATUS": "AVAILABLE",
        "USE": STATSBOMB_USE,
        "WHY_NOT_USED_FOR_RATINGS": STATSBOMB_WHY_NOT_USED_FOR_RATINGS,
        "COMPETITION_SEASONS": len(comps),
        "LATEST_SEASON_PER_COMPETITION": latest,
        "OVERLAPS_CORPUS_WINDOW": False,
        "PROVENANCE": rec,
    }


# ---------------------------------------------------------------------------
# Paid data, if management wants to decide
# ---------------------------------------------------------------------------

PAID_DATA_REQUEST = {
    "PROVIDER": "Football-Data.org (api.football-data.org) or Opta/Stats Perform",
    "DATA_NEEDED": (
        "kickoff-stamped fixtures, final and half-time scores, shots and "
        "expected goals per match, lineup and availability at kickoff"),
    "HISTORICAL_DEPTH": "five completed seasons plus the live season",
    "TIMESTAMP_RESOLUTION": (
        "kickoff to the minute; in-play events to the minute. The as-of gate "
        "cannot be enforced against a date-only source for a live model."),
    "EXPECTED_COVERAGE": (
        "the 484 clubs the venue corpus names across 32 soccer league codes; "
        "the free source covers 8 of those codes"),
    "WHY_IT_MATTERS": (
        "Free sources give final scores and a date. They do not give kickoff "
        "times, so no live or T-minus surface can be built from them, and they "
        "do not cover the South American, Scandinavian, Turkish or cup "
        "competitions that make up a large share of the corpus. Expected goals "
        "in particular is the single feature most likely to beat a "
        "goals-only Poisson model."),
    "STATUS": "NOT_PURCHASED_NOT_REQUESTED",
}
