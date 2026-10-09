"""AUTHORITATIVE FIXTURE EVIDENCE FOR VENUE-NATIVE SOCCER CONTRACTS.

WHY THIS EXISTS. A soccer contract whose identity came from the venue's own
catalogue has no global condition id, so `acquire_fixture_scope` refused it
(FIXTURE_METADATA_HAS_NO_CONDITION_KEY) and the settlement comparison could
never learn the competition phase, the game format or the event state --
every soccer verdict stayed UNKNOWN for want of evidence nobody fetched.

WHAT IT DOES. For a competition with an ORGANISER-PUBLISHED schedule source
declared in `SOURCES`, it reads that schedule for the fixture's official
date, matches the match on BOTH team names (either orientation) and the
date, and returns the organiser's own classification:

  phase   LEAGUE_OR_GROUP_STAGE  round mode GROUP and match type GROUP_STAGE
          KNOCKOUT_STAGE         a knockout round or match type
  format  SCHEDULED_NINETY_MINUTES  a group/league matchday of format REGULAR
          TIE_DECIDABLE_BY_EXTRA_TIME_OR_SHOOTOUT  a knockout match
  state   play_has_begun False for UPCOMING, True for LIVE/FINISHED-like
          states; anything else is None (unknown)

The evidence is persisted under the venue's OWN namespaced event key
(migration 183: ('PMUS', 'event:<event_slug>')), never under a borrowed or
invented condition id.

WHAT IT WILL NOT DO. It never defaults a phase or format: an unrecognised
round, matchday format or status leaves the field None with a named refusal.
A competition without a declared source is refused by name
(NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION) -- the Brazilian Serie B,
for example, has no organiser API we read. Nothing here writes outside
`venue_fixture_metadata`, and nothing here touches an order or a position.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone

from . import bettor_settlement_terms as ST

VERSION = "BETTOR_SOCCER_FIXTURE_V1"
VENUE = "PMUS"
TABLE = "venue_fixture_metadata"

UEFA_MATCHES_URL = "https://match.uefa.com/v5/matches"

#: ORGANISER SOURCES, by the odds provider's sport key. Only a competition
#: whose organiser publishes a machine-readable schedule is listed; the
#: competition id was read from that organiser's own payload (UEFA match
#: API, 2026-10-01: competition.id "2014", code "UNL").
SOURCES = {
    "soccer_uefa_nations_league": {
        "kind": "UEFA_MATCH_API", "competition_id": "2014",
        "competition": "UNL",
        "source": "UEFA match API (match.uefa.com/v5/matches)"},
}

#: (RC6.2, p-coverage) THE SAME SOURCES, BY THE VENUE'S OWN LEAGUE CODE.
#:
#: THE DEFECT. `SOURCES` is keyed by the odds provider's sport key, and since
#: PinnAPI became the primary reference the key on most soccer quotes is the
#: GENERIC 'pinnapi_soccer' (production, 14 days to 2026-10-09: research-sql
#: run 37946119133 G1 -- PinnAPI-sourced soccer money lines across 75 venue
#: league codes, none with a source), which no declared source can match.
#: The competition is not lost: the contract's own venue event slug names it
#: (`unl-wal-nor-2026-10-01` -> unl, the grammar market_plane.populate.
#: league_of states), and that is the venue's identity for the event, not a
#: name match. So the source is resolved from the venue league code first,
#: and from the provider key only when the venue code names none; the two
#: naming DIFFERENT declared competitions is a refusal, never a pick.
#:
#: UNL is the capture above (36 production rows, LEAGUE_OR_GROUP_STAGE /
#: SCHEDULED_NINETY_MINUTES, research-sql run 37945671144 F2). UCL / UEL /
#: UECL are listed on the venue under ucl / uel / uecl (18 events each on
#: 2026-10-13..15, run 37945671144 F1) and are served by the same organiser
#: API and parser; their competition ids (UEFA match API competitionId 1 /
#: 14 / 2019) have NOT yet been read back in this repository, so they carry
#: `declared_unverified` and every read VERIFIES the payload's own
#: competition id and code against the declaration (`parse`): a wrong
#: declaration yields ORGANISER_PAYLOAD_IS_NOT_THE_DECLARED_COMPETITION and
#: no row, never another competition's evidence. A round the parser does not
#: recognise is refused by name as before. Domestic leagues have no
#: organiser source here at all.
VENUE_LEAGUE_SOURCES = {
    "unl": SOURCES["soccer_uefa_nations_league"],
    "ucl": {"kind": "UEFA_MATCH_API", "competition_id": "1",
            "competition": "UCL",
            "source": "UEFA match API (match.uefa.com/v5/matches)",
            "declared_unverified": True},
    "uel": {"kind": "UEFA_MATCH_API", "competition_id": "14",
            "competition": "UEL",
            "source": "UEFA match API (match.uefa.com/v5/matches)",
            "declared_unverified": True},
    "uecl": {"kind": "UEFA_MATCH_API", "competition_id": "2019",
             "competition": "UECL",
             "source": "UEFA match API (match.uefa.com/v5/matches)",
             "declared_unverified": True},
}
#: provider sport keys that name no competition
GENERIC_SPORT_KEYS = frozenset({"pinnapi_soccer", "soccer", ""})

R_NO_SOURCE = "NO_AUTHORITATIVE_FIXTURE_SOURCE_FOR_COMPETITION"
R_SOURCES_DISAGREE = ("PROVIDER_KEY_AND_VENUE_LEAGUE_NAME_DIFFERENT_"
                      "COMPETITIONS")
R_COMPETITION = "ORGANISER_PAYLOAD_IS_NOT_THE_DECLARED_COMPETITION"
R_NO_EVENT_KEY = "VENUE_EVENT_KEY_NOT_ESTABLISHED"
R_FETCH = "FIXTURE_SCHEDULE_READ_FAILED"
R_NO_MATCH = "FIXTURE_NOT_FOUND_IN_THE_ORGANISER_SCHEDULE"
R_AMBIGUOUS = "FIXTURE_MATCHES_MORE_THAN_ONE_ORGANISER_MATCH"
R_PHASE = "ORGANISER_PHASE_NOT_RECOGNISED"
R_FORMAT = "ORGANISER_MATCH_FORMAT_NOT_RECOGNISED"
R_STATE = "ORGANISER_MATCH_STATUS_NOT_RECOGNISED"
R_NO_ROW = "NO_VENUE_FIXTURE_METADATA_ROW"
R_READ_FAILED = "VENUE_FIXTURE_METADATA_READ_FAILED"
R_BINDING = "FIXTURE_BINDING_INCOMPLETE"

STATUS_NOT_BEGUN = frozenset({"UPCOMING"})
STATUS_BEGUN = frozenset({"LIVE", "FINISHED", "PLAYED", "HALF_TIME",
                          "INTERRUPTED", "ABANDONED"})

MAX_ROW_AGE_S = 300.0


def venue_fixture_key(event_slug) -> str | None:
    """The venue's own event identity, namespaced. None when absent."""
    s = str(event_slug or "").strip()
    return "event:%s" % s if s else None


def source_for(sport_key) -> dict | None:
    return SOURCES.get(str(sport_key or ""))


def venue_league_of(event_slug) -> str | None:
    """The venue's league code of its own event slug (market_plane.populate.
    league_of: an EVENT slug's first segment, a MARKET slug's segment after
    its kind prefix). None when there is no slug."""
    if not str(event_slug or "").strip():
        return None
    from .market_plane.populate import league_of
    return league_of(event_slug)


def resolve_source(sport_key, event_slug=None) -> dict:
    """PURE. The organiser source for one venue soccer event:
    {"source": dict | None, "basis", "venue_league", "sport_key", "refusal",
    "refusal_subject"}. The venue league code first, the provider key
    second; both naming different declared competitions is refused."""
    key = str(sport_key or "")
    code = venue_league_of(event_slug)
    by_code = VENUE_LEAGUE_SOURCES.get(code) if code else None
    by_key = SOURCES.get(key)
    out = {"source": None, "basis": None, "venue_league": code,
           "sport_key": key or None, "refusal": None,
           # what the refusal names: the provider key, unless it is generic
           # and the venue's own league code says more
           "refusal_subject": (code if (key in GENERIC_SPORT_KEYS and code)
                               else (key or code))}
    if by_code and by_key and \
            by_code["competition_id"] != by_key["competition_id"]:
        out["refusal"] = R_SOURCES_DISAGREE
        return out
    if by_code:
        out.update(source=by_code, basis="VENUE_LEAGUE_CODE")
    elif by_key:
        out.update(source=by_key, basis="PROVIDER_SPORT_KEY")
    else:
        out["refusal"] = R_NO_SOURCE
    return out


def _competition_of(m: dict) -> tuple:
    """(id, code) of one organiser match, from the match's own fields."""
    c = (m or {}).get("competition") or {}
    cid = str(c.get("id") or (m or {}).get("competitionId") or "")
    return cid, str(c.get("code") or "")


def _norm(name) -> str:
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.casefold())


def schedule_url(src: dict, date_str: str) -> str:
    return ("%s?competitionId=%s&fromDate=%s&toDate=%s&limit=100&offset=0"
            "&order=ASC" % (UEFA_MATCHES_URL, src["competition_id"],
                            date_str, date_str))


def fetch_blocking(src: dict, date_str: str, *, timeout=15.0) -> dict:
    """GET the organiser's schedule for one date. Never raises."""
    import httpx
    url = schedule_url(src, date_str)
    try:
        r = httpx.get(url, timeout=timeout,
                      headers={"Accept": "application/json",
                               "User-Agent": "sportsassets-fixture-reader"})
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "url": url, "error": type(exc).__name__}
    if r.status_code != 200:
        return {"ok": False, "url": url, "error": "HTTP_%s" % r.status_code}
    try:
        payload = r.json()
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "url": url, "error": type(exc).__name__}
    return {"ok": True, "url": url, "payload": payload,
            "retrieved_at": datetime.now(timezone.utc).isoformat()}


def _classify(m: dict) -> tuple:
    """(phase, format, refusals) from the organiser's own fields."""
    rnd = dict(m.get("round") or {})
    md = dict(m.get("matchday") or {})
    mode = str(rnd.get("mode") or "").upper()
    mtype = str(m.get("type") or "").upper()
    md_format = str(md.get("format") or "").upper()
    refusals = []
    if mode == "GROUP" and mtype == "GROUP_STAGE":
        phase = ST.PHASE_LEAGUE
        fmt = ST.FMT_NINETY if md_format == "REGULAR" else None
        if fmt is None:
            refusals.append(R_FORMAT)
    elif mode in ("KNOCKOUT", "CUP") or mtype in (
            "KNOCKOUT", "FINAL", "SEMIFINAL", "QUARTERFINAL",
            "PLAY_OFF", "PLAYOFF", "THIRD_PLACE"):
        phase, fmt = ST.PHASE_KNOCKOUT, ST.FMT_KNOCKOUT
    else:
        phase, fmt = None, None
        refusals += [R_PHASE, R_FORMAT]
    return phase, fmt, refusals


def parse(payload, *, home, away, date_str, retrieved_at, url,
          src: dict) -> dict:
    """The organiser's evidence for the ONE match on `date_str` between
    `home` and `away` (either orientation), or a named refusal. Pure."""
    out = {"ok": False, "source": src.get("source"), "source_url": url,
           "retrieved_at": retrieved_at, "competition": src.get("competition"),
           "reader_version": VERSION, "refusals": []}
    matches = payload if isinstance(payload, list) else \
        list((payload or {}).get("matches") or [])
    h, a = _norm(home), _norm(away)
    hits = []
    foreign = 0
    want = (str(src.get("competition_id") or ""),
            str(src.get("competition") or ""))
    for m in matches:
        # (RC6.2) THE PAYLOAD MUST BE THE DECLARED COMPETITION, by its own
        # id and code: a match of any other (or of none stated) is never
        # read, so a mis-declared source can yield no evidence at all
        if _competition_of(m) != want:
            foreign += 1
            continue
        mh = _norm(((m or {}).get("homeTeam") or {}).get("internationalName"))
        ma = _norm(((m or {}).get("awayTeam") or {}).get("internationalName"))
        day = str(((m or {}).get("kickOffTime") or {}).get("date") or "")
        if day != date_str:
            continue
        if (mh, ma) == (h, a):
            hits.append((m, "SAME"))
        elif (mh, ma) == (a, h):
            hits.append((m, "SWAPPED"))
    if not hits:
        if matches and foreign == len(matches):
            out["refusals"].append(R_COMPETITION)
            out["why"] = ("every one of the %d organiser matches read is a "
                          "competition other than the declared %s (id %s)"
                          % (len(matches), want[1], want[0]))
            out["examined"] = len(matches)
            out["foreign"] = foreign
            return out
        out["refusals"].append(R_NO_MATCH)
        out["why"] = ("no %s match on %s between %r and %r"
                      % (src.get("competition"), date_str, home, away))
        out["examined"] = len(matches)
        out["foreign"] = foreign
        return out
    if len(hits) > 1:
        out["refusals"].append(R_AMBIGUOUS)
        out["why"] = "%d organiser matches fit both names and the date" \
            % len(hits)
        return out
    m, orient = hits[0]
    phase, fmt, refusals = _classify(m)
    status = str(m.get("status") or "").upper()
    if status in STATUS_NOT_BEGUN:
        begun = False
    elif status in STATUS_BEGUN:
        begun = True
    else:
        begun = None
        refusals.append(R_STATE)
    ko = dict(m.get("kickOffTime") or {})
    out.update(ok=True, refusals=refusals, phase=phase, game_format=fmt,
               play_has_begun=begun, event_state_raw=status or None,
               # The organiser publishes the SCHEDULED kick-off, not an
               # observed start: it is never recorded as an actual start.
               actual_start_at=None, scheduled_kickoff=ko.get("dateTime"),
               start_evidence="ORGANISER_SCHEDULE_STATUS",
               official_date=date_str,
               home_team=((m.get("homeTeam") or {}).get("internationalName")),
               away_team=((m.get("awayTeam") or {}).get("internationalName")),
               orientation=orient, source_match_id=str(m.get("id") or ""),
               raw={"id": m.get("id"), "status": m.get("status"),
                    "type": m.get("type"),
                    "round": {k: (m.get("round") or {}).get(k)
                              for k in ("id", "mode", "metaData", "phase")},
                    "matchday": {k: (m.get("matchday") or {}).get(k)
                                 for k in ("id", "format", "longName")},
                    "kickOffTime": m.get("kickOffTime")})
    return out


READ_SQL = """
    SELECT *, extract(epoch FROM retrieved_at) AS retrieved_at_epoch
      FROM venue_fixture_metadata
     WHERE venue = $1 AND venue_fixture_key = $2
"""

UPSERT_SQL = """
    INSERT INTO venue_fixture_metadata (venue, venue_fixture_key,
        sport_family, competition, phase, game_format, play_has_begun,
        event_state_raw, actual_start_at, scheduled_kickoff, start_evidence,
        official_date, home_team, away_team, orientation, source_match_id,
        source, source_url, retrieved_at, reader_version, refusals, raw)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::timestamptz,$10::timestamptz,$11,
            $12::date,$13,$14,$15,$16,$17,$18,$19::timestamptz,$20,
            $21::jsonb,$22::jsonb)
    ON CONFLICT (venue, venue_fixture_key) DO UPDATE SET
        sport_family = EXCLUDED.sport_family,
        competition = EXCLUDED.competition, phase = EXCLUDED.phase,
        game_format = EXCLUDED.game_format,
        play_has_begun = EXCLUDED.play_has_begun,
        event_state_raw = EXCLUDED.event_state_raw,
        actual_start_at = EXCLUDED.actual_start_at,
        scheduled_kickoff = EXCLUDED.scheduled_kickoff,
        start_evidence = EXCLUDED.start_evidence,
        official_date = EXCLUDED.official_date,
        home_team = EXCLUDED.home_team, away_team = EXCLUDED.away_team,
        orientation = EXCLUDED.orientation,
        source_match_id = EXCLUDED.source_match_id,
        source = EXCLUDED.source, source_url = EXCLUDED.source_url,
        retrieved_at = EXCLUDED.retrieved_at,
        reader_version = EXCLUDED.reader_version,
        refusals = EXCLUDED.refusals, raw = EXCLUDED.raw, written_at = now()
"""


async def read(conn, key) -> dict:
    """The persisted row, or why there is none. Never raises."""
    try:
        row = await conn.fetchrow(READ_SQL, VENUE, str(key))
    except Exception as exc:                                   # noqa: BLE001
        return {"read": False, "error": type(exc).__name__,
                "refusal": R_READ_FAILED}
    if row is None:
        return {"read": False, "error": None, "refusal": R_NO_ROW}
    out = dict(row, read=True)
    for k in ("retrieved_at", "actual_start_at", "scheduled_kickoff"):
        if out.get(k) is not None and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    if out.get("official_date") is not None and \
            hasattr(out["official_date"], "isoformat"):
        out["official_date"] = out["official_date"].isoformat()
    return out


def _ts(v):
    """An ISO-8601 instant as an aware datetime, or None."""
    if v is None or isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _day(v):
    if v is None or isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


async def upsert(conn, key, ev: dict, *, sport_family="soccer") -> dict:
    try:
        await conn.execute(
            UPSERT_SQL, VENUE, str(key), sport_family, ev.get("competition"),
            ev.get("phase"), ev.get("game_format"), ev.get("play_has_begun"),
            ev.get("event_state_raw"), _ts(ev.get("actual_start_at")),
            _ts(ev.get("scheduled_kickoff")), ev.get("start_evidence"),
            _day(ev.get("official_date")), ev.get("home_team"),
            ev.get("away_team"),
            ev.get("orientation"), ev.get("source_match_id"),
            ev.get("source"), ev.get("source_url"),
            _ts(ev.get("retrieved_at")),
            VERSION, json.dumps(list(ev.get("refusals") or [])),
            json.dumps(ev.get("raw") or {}, default=str))
    except Exception as exc:                                   # noqa: BLE001
        return {"persisted": False, "error": type(exc).__name__,
                "detail": str(exc)[:300]}
    return {"persisted": True, "venue": VENUE, "venue_fixture_key": key,
            "writes": "ONE venue_fixture_metadata row. No order, position "
                      "or decision."}


def describe() -> dict:
    return {"version": VERSION, "table": TABLE, "venue": VENUE,
            "sources": {k: dict(v) for k, v in SOURCES.items()},
            "venue_league_sources": {k: dict(v) for k, v in
                                     VENUE_LEAGUE_SOURCES.items()},
            "key": "('PMUS', 'event:<venue event slug>') -- never a global "
                   "condition id",
            "refusals": [R_NO_SOURCE, R_SOURCES_DISAGREE, R_NO_EVENT_KEY,
                         R_FETCH, R_NO_MATCH, R_COMPETITION, R_AMBIGUOUS,
                         R_PHASE, R_FORMAT, R_STATE, R_BINDING]}
