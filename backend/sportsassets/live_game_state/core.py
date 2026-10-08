"""Pure, conservative game-state normalization and exact fixture identity.

Retrieval freshness is NOT a claim about provider update latency. In particular,
ESPN's scoreboard commonly supplies no event-level update timestamp. Those
records retain source_at=None and explicitly say RETRIEVAL_ONLY. A game marked
FINAL here never closes a position or changes a settlement decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

SCHEMA = "bettor.live_game.v1"
AUTHORITY = "DISPLAY_ONLY_NOT_A_PRICE_OR_SETTLEMENT_SOURCE"
START_TOLERANCE_S = 600
MAX_RECORD_BYTES = 32768
# endpoint sport, ESPN league, Odds API sport key. Explicit league namespace.
LEAGUES: dict[str, tuple[str, str, str]] = {
    "NFL": ("football", "nfl", "americanfootball_nfl"),
    "NCAAF": ("football", "college-football", "americanfootball_ncaaf"),
    "MLB": ("baseball", "mlb", "baseball_mlb"),
    "NBA": ("basketball", "nba", "basketball_nba"),
    "WNBA": ("basketball", "wnba", "basketball_wnba"),
    "NCAAB": ("basketball", "mens-college-basketball", "basketball_ncaab"),
    "NCAAW": ("basketball", "womens-college-basketball", "basketball_wncaab"),
    "NHL": ("hockey", "nhl", "icehockey_nhl"),
    "EPL": ("soccer", "eng.1", "soccer_epl"),
    "MLS": ("soccer", "usa.1", "soccer_usa_mls"),
    "UCL": ("soccer", "uefa.champions", "soccer_uefa_champs_league"),
}
LEAGUE_ALIASES = {
    "CFB": "NCAAF", "NCAA FOOTBALL": "NCAAF", "NCAA MEN'S BASKETBALL": "NCAAB",
    "NCAA WOMEN'S BASKETBALL": "NCAAW", "MAJOR LEAGUE BASEBALL": "MLB",
    "NATIONAL FOOTBALL LEAGUE": "NFL", "NATIONAL BASKETBALL ASSOCIATION": "NBA",
    "NATIONAL HOCKEY LEAGUE": "NHL", "WOMEN'S NATIONAL BASKETBALL ASSOCIATION": "WNBA",
    "ENGLISH PREMIER LEAGUE": "EPL", "MAJOR LEAGUE SOCCER": "MLS",
    "UEFA CHAMPIONS LEAGUE": "UCL",
}
VENUES = {"POLYMARKET_US", "KALSHI"}
PROVIDERS = {"ESPN", "THE_ODDS_API"}


class ScoreError(ValueError):
    """Safe reason code; never include credential-bearing URLs in this error."""


def obj(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def finite(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError, OverflowError):
        return None


def integer(v: Any, lo=0, hi=1000) -> int | None:
    n = finite(v)
    return int(n) if n is not None and n.is_integer() and lo <= n <= hi else None


def epoch(v: Any) -> float | None:
    if isinstance(v, datetime):
        return v.timestamp() if v.tzinfo is not None else None
    if isinstance(v, str):
        try:
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
            if d.tzinfo is not None:
                return d.timestamp()
        except (ValueError, OverflowError):
            pass
    n = finite(v)
    return n if n is not None and 0 <= n <= 4102444800 else None


def text(v: Any, limit=300) -> str:
    return str(v or "").strip()[:limit]


def norm(v: Any) -> str:
    # Full normalized equality only: no substring, city, ticker or odds inference.
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", text(v)).casefold()).split())


def league_of(v: Any) -> str | None:
    n = text(v).upper()
    n = LEAGUE_ALIASES.get(n, n)
    return n if n in LEAGUES else None


def digest(v: Any) -> str:
    return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def logo_url(v: Any) -> str | None:
    if not isinstance(v, str) or len(v) > 512:
        return None
    try:
        u = urlsplit(v)
        if (u.scheme == "https" and u.hostname == "a.espncdn.com" and
            not u.username and not u.password and u.port in (None, 443) and
            u.path.startswith("/i/teamlogos/") and not u.query and not u.fragment and
            re.fullmatch(r"/i/teamlogos/[a-zA-Z0-9_./-]+\.(png|webp|svg)", u.path) and
            ".." not in u.path):
            return v
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class Fixture:
    venue: str
    event_id: str
    league: str
    home: str
    away: str
    start_at: float
    evidence_id: str
    game_number: int | None = None

    def __post_init__(self):
        if self.venue not in VENUES or not self.event_id or len(self.event_id) > 250:
            raise ScoreError("CANONICAL_EVENT_IDENTITY_MISSING")
        if self.league not in LEAGUES:
            raise ScoreError("SCORE_LEAGUE_UNSUPPORTED")
        if not norm(self.home) or not norm(self.away) or norm(self.home) == norm(self.away):
            raise ScoreError("CANONICAL_PARTICIPANTS_INVALID")
        if epoch(self.start_at) is None or not self.evidence_id:
            raise ScoreError("CANONICAL_FIXTURE_EVIDENCE_MISSING")
        if self.game_number is not None and integer(self.game_number, 1, 9) is None:
            raise ScoreError("GAME_NUMBER_INVALID")

    @property
    def key(self) -> tuple[str, str]:
        return self.venue, self.event_id

    @property
    def fingerprint(self) -> str:
        return digest({"venue": self.venue, "event_id": self.event_id, "league": self.league,
                       "home": norm(self.home), "away": norm(self.away), "start_at": self.start_at,
                       "game_number": self.game_number})


class Aliases:
    """Optional explicit, evidence-backed full-name aliases, namespaced by league.

    No auto-learning. Example row: {league, canonical, alias, evidence_id}.
    Aliases cannot silently make one provider name refer to two different teams.
    """
    def __init__(self, rows: list[dict] | None = None):
        self._map: dict[tuple[str, str], tuple[str, str]] = {}
        for row in rows or []:
            league = league_of(row.get("league"))
            canonical, alias = norm(row.get("canonical")), norm(row.get("alias"))
            evidence = text(row.get("evidence_id"))
            if not league or not canonical or not alias or not evidence:
                raise ScoreError("TEAM_ALIAS_EVIDENCE_MISSING")
            key = (league, alias)
            if key in self._map and self._map[key][0] != canonical:
                raise ScoreError("TEAM_ALIAS_AMBIGUOUS")
            self._map[key] = canonical, evidence

    def resolve(self, league: str, name: str) -> tuple[str, str | None]:
        n = norm(name)
        return self._map.get((league, n), (n, None))


def match_fixture(fixture: Fixture, games: list[dict], aliases: Aliases | None = None,
                  binding: dict | None = None) -> tuple[dict | None, str, list[str]]:
    aliases = aliases or Aliases()
    expected_h, eh = aliases.resolve(fixture.league, fixture.home)
    expected_a, ea = aliases.resolve(fixture.league, fixture.away)
    hits, refs = {}, []
    for g in games:
        if g.get("league") != fixture.league or g.get("provider") not in PROVIDERS:
            continue
        h, hr = aliases.resolve(fixture.league, text(g.get("home")))
        a, ar = aliases.resolve(fixture.league, text(g.get("away")))
        if (h, a) != (expected_h, expected_a) or h == a:
            continue
        start = epoch(g.get("start_at"))
        if start is None or abs(start - fixture.start_at) > START_TOLERANCE_S:
            continue
        if fixture.game_number is not None and g.get("game_number") != fixture.game_number:
            continue
        if binding and (binding.get("fixture_fingerprint") != fixture.fingerprint or
                        binding.get("provider") != g.get("provider") or
                        binding.get("provider_event_id") != g.get("provider_event_id") or
                        binding.get("provider_home_id") != g.get("home_id") or
                        binding.get("provider_away_id") != g.get("away_id")):
            continue
        if not g.get("provider_event_id") or not g.get("home_id") or not g.get("away_id"):
            continue
        key = (g["provider"], g["provider_event_id"], g.get("provider_competition_id"))
        if key in hits and hits[key] != g:
            return None, "DUPLICATE_PROVIDER_ID_CONFLICT", []
        hits[key] = g
        refs.extend(r for r in (eh, ea, hr, ar) if r)
    if len(hits) != 1:
        return None, "AMBIGUOUS_SCORE_FIXTURE" if hits else "SCORE_FIXTURE_NOT_MATCHED", []
    return next(iter(hits.values())), "EXACT_LEAGUE_PARTICIPANTS_START", sorted(set(refs))


ESPN_STATES = {
    "STATUS_SCHEDULED": "SCHEDULED", "STATUS_IN_PROGRESS": "IN_PROGRESS",
    "STATUS_HALFTIME": "HALFTIME", "STATUS_END_PERIOD": "INTERMISSION",
    "STATUS_FINAL": "FINAL", "STATUS_FINAL_OVERTIME": "FINAL",
    "STATUS_FULL_TIME": "FINAL", "STATUS_POSTPONED": "POSTPONED",
    "STATUS_CANCELED": "CANCELED", "STATUS_CANCELLED": "CANCELED",
    "STATUS_SUSPENDED": "SUSPENDED", "STATUS_DELAYED": "DELAYED",
    "STATUS_RAIN_DELAY": "DELAYED", "STATUS_DELAYED_START": "DELAYED",
}


def parse_espn(body: Any, *, league: str, received_at: float, observed_at: float,
               payload_hash: str, summary=False) -> list[dict]:
    if league not in LEAGUES or not isinstance(body, dict):
        raise ScoreError("ESPN_PAYLOAD_INVALID")
    if summary:
        header = obj(body.get("header"))
        events = [header] if header else None
    else:
        events = body.get("events")
        for item in body.get("leagues") or []:
            slug = obj(item).get("slug")
            if slug and slug != LEAGUES[league][1]:
                raise ScoreError("ESPN_LEAGUE_MISMATCH")
    if not isinstance(events, list) or len(events) > 1000:
        raise ScoreError("ESPN_EVENTS_INVALID")
    out = []
    for event in events:
        e = obj(event)
        competitions = e.get("competitions")
        if not e.get("id") or not isinstance(competitions, list) or len(competitions) != 1:
            continue  # Never choose one competition from an ambiguous event.
        c = obj(competitions[0])
        cs = c.get("competitors")
        if not isinstance(cs, list) or len(cs) != 2:
            continue
        h = [x for x in cs if obj(x).get("homeAway") == "home"]
        a = [x for x in cs if obj(x).get("homeAway") == "away"]
        if len(h) != 1 or len(a) != 1:
            continue
        h, a = h[0], a[0]
        ht, at = obj(h.get("team")), obj(a.get("team"))
        status = obj(c.get("status")) or obj(e.get("status"))
        st = obj(status.get("type"))
        state = ESPN_STATES.get(st.get("name"), "UNKNOWN")
        # A generic 'post' is not enough to assert completion; only declared names.
        start = epoch(c.get("date") or e.get("date"))
        if start is None or not ht.get("id") or not at.get("id") or ht.get("id") == at.get("id"):
            continue
        situation = obj(c.get("situation"))
        if summary and not situation:
            situation = obj(body.get("situation"))
        last_play = obj(situation.get("lastPlay"))
        if summary and not last_play:
            plays = obj(obj(body.get("drives")).get("current")).get("plays")
            if isinstance(plays, list) and plays:
                last_play = obj(plays[-1])
        bases = [situation.get(k) for k in ("onFirst", "onSecond", "onThird")]
        bases = bases if all(isinstance(x, bool) for x in bases) else None
        possession = text(situation.get("possession")) or None
        if possession is not None and possession not in (text(ht["id"]), text(at["id"])):
            possession = None
        def team_logo(t):
            direct = logo_url(t.get("logoDark")) or logo_url(t.get("logo"))
            if direct:
                return direct
            return next((u for x in t.get("logos") or [] if (u := logo_url(obj(x).get("href")))), None)
        game = {
            "provider": "ESPN", "league": league, "provider_event_id": text(e["id"]),
            "provider_competition_id": text(c.get("id") or e["id"]), "start_at": start,
            "game_number": integer(c.get("gameNumber"), 1, 9),
            "home_id": text(ht["id"]), "away_id": text(at["id"]),
            "home": text(ht.get("displayName")), "away": text(at.get("displayName")),
            "home_abbreviation": text(ht.get("abbreviation"), 12),
            "away_abbreviation": text(at.get("abbreviation"), 12),
            "home_logo": team_logo(ht), "away_logo": team_logo(at),
            "home_score": integer(h.get("score")) if state != "SCHEDULED" else None,
            "away_score": integer(a.get("score")) if state != "SCHEDULED" else None,
            "game_status": state, "status_detail": text(st.get("shortDetail") or st.get("detail")),
            "period": integer(status.get("period"), 0, 99),
            "clock_seconds": finite(status.get("clock")) if state != "SCHEDULED" else None,
            "display_clock": text(status.get("displayClock"), 32) if state != "SCHEDULED" else "",
            # The scoreboard does not reliably establish that a clock is RUNNING.
            "clock_running": False, "clock_direction": None, "clock_estimated": False,
            "inning": integer(status.get("period"), 1, 99) if league == "MLB" else None,
            "inning_half": ("TOP" if text(st.get("shortDetail")).startswith("Top ") else
                            "BOTTOM" if text(st.get("shortDetail")).startswith("Bot ") else None),
            "outs": integer(situation.get("outs"), 0, 3),
            "balls": integer(situation.get("balls"), 0, 4),
            "strikes": integer(situation.get("strikes"), 0, 3), "bases": bases,
            "down": integer(situation.get("down"), 1, 4),
            "distance": integer(situation.get("distance"), 0, 100),
            "possession": possession, "possession_text": text(situation.get("possessionText"), 80),
            "last_play": text(last_play.get("text"), 1000),
            "source_at": None,  # Do NOT turn scheduled start or last-play time into source age.
            "source_timestamp_field": None, "received_at": received_at, "observed_at": observed_at,
            "provider_payload_hash": payload_hash, "detail_level": "SUMMARY" if summary else "SCOREBOARD",
        }
        if game["clock_seconds"] is not None and game["clock_seconds"] < 0:
            game["clock_seconds"] = None
            game["display_clock"] = ""
        out.append(game)
    return out


def parse_odds(body: Any, *, league: str, received_at: float, observed_at: float,
               payload_hash: str) -> list[dict]:
    if league not in LEAGUES or not isinstance(body, list) or len(body) > 1000:
        raise ScoreError("ODDS_SCORES_PAYLOAD_INVALID")
    out = []
    for event in body:
        e = obj(event)
        if e.get("sport_key") != LEAGUES[league][2] or not e.get("id"):
            continue
        home, away = text(e.get("home_team")), text(e.get("away_team"))
        start = epoch(e.get("commence_time"))
        if not norm(home) or not norm(away) or norm(home) == norm(away) or start is None:
            continue
        scores = e.get("scores")
        sm = {}
        if isinstance(scores, list):
            for s in scores:
                name = norm(obj(s).get("name"))
                if name in sm:
                    raise ScoreError("ODDS_DUPLICATE_TEAM_SCORE")
                sm[name] = integer(obj(s).get("score"))
        # 'completed=false' is not proof a game has begun. Keep it explicitly narrower.
        state = "FINAL" if e.get("completed") is True else "SCORES_REPORTED" if sm else "SCHEDULED"
        out.append({
            "provider": "THE_ODDS_API", "league": league, "provider_event_id": text(e["id"]),
            "provider_competition_id": text(e["id"]), "start_at": start, "game_number": None,
            "home_id": "name:" + norm(home), "away_id": "name:" + norm(away),
            "home": home, "away": away, "home_score": sm.get(norm(home)), "away_score": sm.get(norm(away)),
            "home_logo": None, "away_logo": None, "home_abbreviation": "", "away_abbreviation": "",
            "game_status": state, "status_detail": state.replace("_", " ").title(),
            "period": None, "display_clock": "", "clock_seconds": None, "clock_running": False,
            "clock_direction": None, "clock_estimated": False, "inning": None, "inning_half": None,
            "outs": None, "balls": None, "strikes": None, "bases": None, "down": None,
            "distance": None, "possession": None, "possession_text": "", "last_play": "",
            "source_at": epoch(e.get("last_update")), "source_timestamp_field": "last_update",
            "received_at": received_at, "observed_at": observed_at,
            "provider_payload_hash": payload_hash, "detail_level": "SCORES_ONLY",
        })
    return out


def bind_game(fixture: Fixture, g: dict, *, alias_refs: list[str] | None = None, aliases: Aliases | None = None) -> dict:
    checked, why, verified_refs = match_fixture(fixture, [g], aliases)
    if checked is None:
        raise ScoreError(why)
    alias_refs = verified_refs
    provider = g.get("provider")
    if provider not in PROVIDERS:
        raise ScoreError("SCORE_PROVIDER_UNSUPPORTED")
    binding = {"venue": fixture.venue, "event_id": fixture.event_id,
               "fixture_fingerprint": fixture.fingerprint, "fixture_evidence_id": fixture.evidence_id,
               "provider": provider, "provider_event_id": g["provider_event_id"],
               "provider_home_id": g["home_id"], "provider_away_id": g["away_id"],
               "league": fixture.league, "home": fixture.home, "away": fixture.away,
               "start_at": fixture.start_at, "game_number": fixture.game_number,
               "basis": "EXACT_LEAGUE_PARTICIPANTS_START", "alias_evidence": alias_refs or []}
    binding["binding_id"] = digest(binding)
    raw = dict(g, schema=SCHEMA, source=provider, venue=fixture.venue, event_id=fixture.event_id,
               identity_verified=True, identity=binding, mapping_evidence_id=binding["binding_id"],
               authority=AUTHORITY, execution_authority=False)
    return raw


def unavailable(venue: str, event_id: str, why: str) -> dict:
    return {"schema": SCHEMA, "status": "UNAVAILABLE", "why": why, "venue": venue,
            "event_id": event_id, "identity_verified": False, "clock_running": False,
            "authority": AUTHORITY, "execution_authority": False}


def display(raw: Any, *, venue: str, event_id: str, now: float, issue: str | None = None) -> dict:
    """Re-evaluate age on EVERY read. Reads never refresh a provider timestamp."""
    r = obj(raw)
    if not r:
        return unavailable(venue, event_id, issue or "NO_SCORE_PROVIDER_EVIDENCE_RECORDED")
    identity = obj(r.get("identity"))
    if (r.get("schema") != SCHEMA or r.get("venue") != venue or r.get("event_id") != event_id or
        r.get("identity_verified") is not True or identity.get("venue") != venue or
        identity.get("event_id") != event_id or not identity.get("binding_id") or
        r.get("authority") != AUTHORITY or r.get("execution_authority") is not False):
        return unavailable(venue, event_id, "SCORE_EVENT_IDENTITY_UNPROVEN")
    claimed = identity.get("binding_id")
    check_identity = {k: v for k, v in identity.items() if k != "binding_id"}
    try:
        identity_ok = digest(check_identity) == claimed
    except (ValueError, TypeError):
        identity_ok = False
    if not identity_ok or any(identity.get(a) != r.get(b) for a, b in (
        ("provider", "source"), ("provider_event_id", "provider_event_id"),
        ("provider_home_id", "home_id"), ("provider_away_id", "away_id"), ("league", "league"))):
        return unavailable(venue, event_id, "SCORE_BINDING_DIGEST_OR_DIMENSION_MISMATCH")
    if r.get("source") not in PROVIDERS:
        return unavailable(venue, event_id, "SCORE_PROVENANCE_ABSENT")
    recv, observed, source_at = (epoch(r.get(k)) for k in ("received_at", "observed_at", "source_at"))
    if recv is None or observed is None or observed > recv or recv > now or (source_at is not None and source_at > now):
        return unavailable(venue, event_id, "SCORE_TIMESTAMP_INVALID")
    limit = {"SCHEDULED": 300.0, "POSTPONED": 300.0, "FINAL": 3600.0, "CANCELED": 3600.0}.get(
        r.get("game_status"), 15.0 if r["source"] == "ESPN" else 45.0)
    expiry = observed + limit
    if source_at is not None:
        expiry = min(expiry, source_at + limit)
    why = None
    if r["source"] == "THE_ODDS_API" and source_at is None:
        why = "ODDS_UPDATE_TIMESTAMP_MISSING"
    elif now > expiry:
        why = "SCORE_EVIDENCE_STALE"
    elif r.get("game_status") == "UNKNOWN":
        why = "SCORE_GAME_STATE_UNDECLARED"
    elif r.get("game_status") not in ("SCHEDULED", "POSTPONED", "CANCELED") and (
            integer(r.get("home_score")) is None or integer(r.get("away_score")) is None):
        why = "SCORE_VALUE_MISSING"
    # Provider failures do not erase the last good observation; they are disclosed.
    out = dict(r, status="STALE" if why else "CURRENT", why=why, provider_issue=issue,
               source_age_s=None if source_at is None else now-source_at,
               received_age_s=now-recv, observed_age_s=now-observed, expires_at=expiry,
               freshness_basis="PROVIDER_TIMESTAMP_AND_RETRIEVAL" if source_at is not None else "RETRIEVAL_ONLY",
               provider_current=None if source_at is None else (not why),
               display_freshness_limit_s=limit, clock_running=False, clock_estimated=False)
    out["home_logo"], out["away_logo"] = logo_url(r.get("home_logo")), logo_url(r.get("away_logo"))
    return out
