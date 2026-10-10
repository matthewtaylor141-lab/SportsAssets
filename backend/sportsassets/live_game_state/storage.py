"""Postgres display store and canonical held-fixture adapter.

Only new trader_display_score_* tables are written. Canonical PAPER holdings,
venue fixture metadata and catalogues are read, never modified. Bindings and
observations are append-only; latest/health are explicitly rebuildable views.
"""
from __future__ import annotations

import json
from typing import Any

from .core import (Fixture, ScoreError, obj, epoch, league_of, digest, text, norm,
                   display, MAX_RECORD_BYTES, AUTHORITY, LEAGUES, venue_league,
                   ORIENTATION_VENUE, ORIENTATION_PROVIDER, MAX_EVENT_TEAM_TUPLES)

LATEST_SQL = """
SELECT venue,event_id,payload,issue FROM trader_display_score_latest
WHERE (venue,event_id) IN (SELECT * FROM unnest($1::text[],$2::text[]))
"""
BINDING_SQL = """
SELECT payload FROM trader_display_score_bindings
WHERE venue=$1 AND event_id=$2 AND provider=$3 AND fixture_fingerprint=$4
ORDER BY recorded_at,binding_id LIMIT 1
"""
FACT_FIELDS = ("home_score", "away_score", "game_status", "period", "clock_seconds",
               "inning_half", "outs", "balls", "strikes", "bases", "down", "distance",
               "possession", "last_play")


def object_value(v):
    if isinstance(v, str):
        try:
            return obj(json.loads(v))
        except ValueError:
            return {}
    return obj(v)


VENUE = "POLYMARKET_US"


def venue_participants(teams, tuples, *, start: float | None) -> dict | None:
    """THE VENUE'S OWN TWO PARTICIPANTS of one event, from its us_premap team rows.

    WHY (requirement register F3, research-sql runs 37880110851 and
    37880299272). The adapter read premap keys home_team / away_team / league
    / sport, which us_premap has never had: the collector that owns the table
    (workers/premap._ensure_table) writes the venue team object of each side
    -- team_id, team_name, team_safe_name, team_abbr, team_league -- plus the
    market's game_start and sports_type. So every held market without a
    fixture-metadata row refused CANONICAL_SCORE_FIXTURE_FIELDS_MISSING,
    although every candidate event of the next 48 h in a score league names
    exactly two venue team ids with one league code and one start, as does
    every event a PAPER account has ever held.

    `teams` is the event's DISTINCT team tuples (rows with a team_id), at most
    MAX_EVENT_TEAM_TUPLES of them, and `tuples` how many there were. Returns
    None when the event has no team row at all (the caller then refuses
    CANONICAL_SCORE_FIXTURE_FIELDS_MISSING, as before); otherwise
    {participants, code, families, start} or a NAMED refusal:

      CANONICAL_VENUE_PARTICIPANTS_NOT_TWO      not exactly two team ids (a
                                                futures board, a team-less
                                                market), or more tuples than read
      CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN one team id under two names, or
                                                a team id with no name at all
      CANONICAL_VENUE_LEAGUE_NOT_ONE            the team rows do not state one
                                                league code
      CANONICAL_EVENT_START_NOT_ONE_INSTANT     the team rows do not state one
                                                start, or not the held row's

    Nothing is parsed from a title, slug or question, and nothing here says
    which participant is home: the venue does not."""
    if isinstance(teams, str):  # asyncpg hands jsonb over as text
        try:
            teams = json.loads(teams)
        except ValueError:
            teams = None
    rows = [obj(t) for t in (teams if isinstance(teams, list) else [])]
    if not rows and not tuples:
        return None
    if tuples > MAX_EVENT_TEAM_TUPLES or len(rows) != tuples:
        raise ScoreError("CANONICAL_VENUE_PARTICIPANTS_NOT_TWO")
    by_id: dict[str, list[dict]] = {}
    for r in rows:
        by_id.setdefault(text(r.get("team_id")), []).append(r)
    if len(by_id) != 2 or "" in by_id:
        raise ScoreError("CANONICAL_VENUE_PARTICIPANTS_NOT_TWO")
    participants = []
    for tid in sorted(by_id, key=lambda k: (len(k), k)):
        named = {(text(r.get("team_name")), text(r.get("team_safe_name"))) for r in by_id[tid]}
        if len(named) != 1:
            raise ScoreError("CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN")
        [(name, safe)] = named
        names = tuple(dict.fromkeys(n for n in (name, safe) if norm(n)))
        if not names:
            raise ScoreError("CANONICAL_VENUE_PARTICIPANT_NAME_UNPROVEN")
        abbrs = {text(r.get("team_abbr")) for r in by_id[tid]}
        participants.append({"team_id": tid, "names": names,
                             "team_abbr": sorted(abbrs)[0] if len(abbrs) == 1 else None})
    codes = {text(r.get("team_league")).lower() for r in rows}
    if len(codes) != 1 or "" in codes:
        raise ScoreError("CANONICAL_VENUE_LEAGUE_NOT_ONE")
    starts = {epoch(r.get("game_start")) for r in rows}
    if len(starts) != 1 or None in starts or (start is not None and starts != {start}):
        raise ScoreError("CANONICAL_EVENT_START_NOT_ONE_INSTANT")
    # a team row without a sportsMarketType states no family; it does not
    # contradict one, and it cannot establish one either
    families = sorted({f for r in rows if (f := text(r.get("sport_family")).lower())})
    return {"participants": participants, "code": next(iter(codes)),
            "families": families, "start": next(iter(starts))}


def fixture_from_row(row: dict) -> Fixture:
    pm, fm = object_value(row.get("premap")), object_value(row.get("fixture_metadata"))
    event_id = text(pm.get("event_slug"), 250)
    if not event_id:
        raise ScoreError("CANONICAL_VENUE_EVENT_MISSING")
    # Use explicit participants from the exact joined venue fixture/catalogue row.
    # Never extract opponents from a title, ticker, substring or market price.
    home = text(fm.get("home_team") or pm.get("home_team"))
    away = text(fm.get("away_team") or pm.get("away_team"))
    league = next((v for k in (fm.get("competition"), pm.get("league"), pm.get("sport"))
                   if (v := league_of(k))), None)
    start = epoch(pm.get("game_start") or fm.get("scheduled_kickoff"))
    orientation, home_names, away_names, venue_side = ORIENTATION_VENUE, (), (), None
    if not home and not away:
        # No row states home and away. The venue's own two team objects on the
        # event's catalogue rows ARE the fixture's participants; which one is
        # home is left to the score provider (core.ORIENTATION_PROVIDER).
        try:
            tuples = int(row.get("event_team_tuples") or 0)
        except (TypeError, ValueError):
            tuples = MAX_EVENT_TEAM_TUPLES + 1
        venue_side = venue_participants(row.get("event_teams"), tuples, start=start)
        if venue_side is not None:
            p1, p2 = venue_side["participants"]
            home, home_names = p1["names"][0], p1["names"][1:]
            away, away_names = p2["names"][0], p2["names"][1:]
            orientation = ORIENTATION_PROVIDER
            start = venue_side["start"] if start is None else start
            if not league:
                league = venue_league(VENUE, venue_side["code"])
                if not league:
                    raise ScoreError("SCORE_LEAGUE_UNSUPPORTED")
                # the venue's own sportsMarketType must be this league's sport
                if venue_side["families"] != [LEAGUES[league][0]]:
                    raise ScoreError("CANONICAL_VENUE_SPORT_FAMILY_MISMATCH")
    if not home or not away or not league or start is None:
        raise ScoreError("CANONICAL_SCORE_FIXTURE_FIELDS_MISSING")
    expected_key = "event:" + event_id
    if fm and fm.get("venue_fixture_key") != expected_key:
        raise ScoreError("CANONICAL_FIXTURE_JOIN_MISMATCH")
    if fm.get("orientation") and fm["orientation"] not in ("HOME_AWAY", "HOME_VS_AWAY", "home_away"):
        raise ScoreError("CANONICAL_HOME_AWAY_UNPROVEN")
    if venue_side is None:
        ev = {"basis": "EXACT_VENUE_CATALOGUE_AND_FIXTURE_ROW", "event_id": event_id,
              "market": row.get("us_market_slug"), "fixture_source": fm.get("source"),
              "fixture_source_match_id": fm.get("source_match_id"),
              "home": home, "away": away, "league": league, "scheduled_start": start}
    else:
        ev = {"basis": "EXACT_VENUE_CATALOGUE_TEAM_ROWS", "event_id": event_id,
              "market": row.get("us_market_slug"), "fixture_source": fm.get("source"),
              "fixture_source_match_id": fm.get("source_match_id"),
              "participants": venue_side["participants"], "orientation": orientation,
              "venue_league_code": venue_side["code"], "league": league,
              "scheduled_start": start}
    raw_meta = object_value(fm.get("raw"))
    return Fixture(VENUE, event_id, league, home, away, start, digest(ev),
                   raw_meta.get("game_number") if isinstance(raw_meta.get("game_number"), int) else None,
                   orientation=orientation, home_names=home_names, away_names=away_names)


def fixture_query(canonical_sql: str) -> str:
    # The event's team rows are read in ONE pass over us_premap for every held
    # event at once (a semi-join on the held events), never one scan per market.
    return """
WITH held AS (
""" + canonical_sql + """
), markets AS (
 SELECT DISTINCT us_market_slug FROM held WHERE account_id=$1
), joined AS (
 SELECT p.us_market_slug,to_jsonb(pm) AS premap,to_jsonb(fm) AS fixture_metadata
 FROM markets p
 LEFT JOIN LATERAL (
  SELECT u.* FROM us_premap u WHERE u.identifier=p.us_market_slug
  AND u.market_slug=p.us_market_slug ORDER BY u.updated_at DESC,u.event_slug LIMIT 1
 ) pm ON true
 LEFT JOIN LATERAL (
  SELECT m.* FROM venue_fixture_metadata m
  WHERE m.venue IN ('PMUS','POLYMARKET_US') AND m.venue_fixture_key='event:'||pm.event_slug
  ORDER BY m.retrieved_at DESC LIMIT 1
 ) fm ON true
 ORDER BY p.us_market_slug LIMIT 1001
), team_rows AS (
 SELECT DISTINCT u.event_slug,u.team_id,u.team_name,u.team_safe_name,u.team_abbr,
        u.team_league,u.game_start,split_part(u.sports_type,'_',1) AS sport_family
 FROM us_premap u
 WHERE u.team_id IS NOT NULL
   AND u.event_slug IN (SELECT j.premap->>'event_slug' FROM joined j)
), ranked AS (
 SELECT t.*,row_number() OVER (PARTITION BY t.event_slug ORDER BY t.team_id,t.team_name,
        t.team_safe_name,t.team_abbr,t.team_league,t.game_start,t.sport_family) AS rk
 FROM team_rows t
), event_teams AS (
 SELECT r.event_slug,count(*) AS tuples,
        jsonb_agg(jsonb_build_object('team_id',r.team_id,'team_name',r.team_name,
          'team_safe_name',r.team_safe_name,'team_abbr',r.team_abbr,'team_league',r.team_league,
          'game_start',r.game_start,'sport_family',r.sport_family) ORDER BY r.rk)
          FILTER (WHERE r.rk<=""" + str(MAX_EVENT_TEAM_TUPLES) + """) AS teams
 FROM ranked r GROUP BY r.event_slug
)
SELECT j.us_market_slug,j.premap,j.fixture_metadata,et.teams AS event_teams,
       coalesce(et.tuples,0) AS event_team_tuples
FROM joined j
LEFT JOIN event_teams et ON et.event_slug=j.premap->>'event_slug'
ORDER BY j.us_market_slug
"""


def validate_transition(previous: dict, new: dict):
    if not previous:
        return
    p, n = previous, new
    # Cross-provider fallback cannot overwrite newer evidence with an older result.
    pat = epoch(p.get("source_at")) or epoch(p.get("observed_at"))
    nat = epoch(n.get("source_at")) or epoch(n.get("observed_at"))
    if pat is not None and nat is not None and nat < pat:
        raise ScoreError("SCORE_OBSERVATION_TIME_REGRESSION")
    if p.get("source") == n.get("source"):
        ps, ns = epoch(p.get("source_at")), epoch(n.get("source_at"))
        if ps is not None and ns == ps and any(p.get(k) != n.get(k) for k in FACT_FIELDS):
            raise ScoreError("SCORE_SAME_UPDATE_CONFLICT")
    # Lower scores at a NEW source time are legitimate corrections, not discarded.


class PostgresStore:
    def __init__(self, pool):
        self.pool = pool

    async def fixtures(self, account_id=None) -> tuple[list[Fixture], dict]:
        """The held fixtures of `account_id`; None (the live-scores worker)
        is the durable PAPER selector, so Day One's open positions get their
        live-game display after an activation (display only)."""
        from ..open_position_canon import CANONICAL_OPEN_POSITIONS_SQL
        async with self.pool.acquire() as conn:
            if account_id is None:
                from ..simulated_account_context import selected_account
                account_id = await selected_account(conn)
            async with conn.transaction(readonly=True):
                await conn.execute("SET LOCAL statement_timeout = 2000")
                rows = await conn.fetch(fixture_query(CANONICAL_OPEN_POSITIONS_SQL), account_id)
        fixtures, reasons = {}, {}
        for r in rows[:1000]:
            try:
                f = fixture_from_row(dict(r))
                existing = fixtures.get(f.key)
                if existing and existing.fingerprint != f.fingerprint:
                    reasons["CONFLICTING_CANONICAL_FIXTURE_ROWS"] = reasons.get("CONFLICTING_CANONICAL_FIXTURE_ROWS", 0)+1
                    fixtures[f.key] = None
                elif f.key not in fixtures:
                    fixtures[f.key] = f
            except ScoreError as exc:
                code = str(exc)
                reasons[code] = reasons.get(code, 0)+1
        established = [f for f in fixtures.values() if f]
        by_orientation = {o: sum(f.orientation == o for f in established)
                          for o in (ORIENTATION_VENUE, ORIENTATION_PROVIDER)}
        return established, {
            "held_market_rows": min(len(rows), 1000), "truncated": len(rows) > 1000,
            "established_fixtures": len(established),
            "established_by_orientation": by_orientation,
            "missing_by_reason": reasons,
            "scope": "CANONICAL_PAPER_HOLDINGS; NO TITLE-BASED IDENTITY INFERENCE"}

    async def binding(self, f: Fixture, provider: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(BINDING_SQL, f.venue, f.event_id, provider, f.fingerprint)
            return object_value(row["payload"]) if row else None

    async def write(self, f: Fixture, raw: dict, *, now: float, issue: str | None = None):
        normalized = display(raw, venue=f.venue, event_id=f.event_id, now=now)
        if normalized["status"] != "CURRENT":
            raise ScoreError(normalized.get("why") or "SCORE_NOT_CURRENT")
        if obj(raw.get("identity")).get("fixture_fingerprint") != f.fingerprint:
            raise ScoreError("SCORE_FIXTURE_FINGERPRINT_MISMATCH")
        body = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(body.encode()) > MAX_RECORD_BYTES:
            raise ScoreError("SCORE_RECORD_TOO_LARGE")
        binding = raw["identity"]
        bbody = json.dumps(binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
        oid = digest(raw)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SET LOCAL statement_timeout = 1500")
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",
                                   "display-score:" + f.venue + ":" + f.event_id)
                prev = await conn.fetchrow("SELECT payload FROM trader_display_score_latest WHERE venue=$1 AND event_id=$2", *f.key)
                validate_transition(object_value(prev["payload"]) if prev else {}, raw)
                old_binding = await conn.fetchrow(BINDING_SQL, f.venue, f.event_id, raw["source"], f.fingerprint)
                if old_binding:
                    ob = object_value(old_binding["payload"])
                    for k in ("provider_event_id", "provider_home_id", "provider_away_id"):
                        if ob.get(k) != binding.get(k):
                            raise ScoreError("SCORE_BINDING_CHANGED_REVIEW_REQUIRED")
                await conn.execute("""
INSERT INTO trader_display_score_bindings(binding_id,venue,event_id,provider,fixture_fingerprint,payload)
VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT(binding_id) DO NOTHING
""", binding["binding_id"], f.venue, f.event_id, raw["source"], f.fingerprint, bbody)
                await conn.execute("""
INSERT INTO trader_display_score_observations
 (observation_id,binding_id,venue,event_id,provider,received_at,source_at,payload)
VALUES($1,$2,$3,$4,$5,to_timestamp($6),to_timestamp($7),$8::jsonb)
ON CONFLICT(observation_id) DO NOTHING
""", oid, binding["binding_id"], f.venue, f.event_id, raw["source"], raw["received_at"], raw.get("source_at"), body)
                await conn.execute("""
INSERT INTO trader_display_score_latest(venue,event_id,observation_id,payload,issue,checked_at)
VALUES($1,$2,$3,$4::jsonb,$5,to_timestamp($6))
ON CONFLICT(venue,event_id) DO UPDATE SET observation_id=excluded.observation_id,
 payload=excluded.payload,issue=excluded.issue,checked_at=excluded.checked_at
""", f.venue, f.event_id, oid, body, issue, now)
        return oid

    async def issue(self, f: Fixture, why: str, *, now: float):
        async with self.pool.acquire() as conn:
            await conn.execute("""
INSERT INTO trader_display_score_latest(venue,event_id,issue,checked_at)
VALUES($1,$2,$3,to_timestamp($4))
ON CONFLICT(venue,event_id) DO UPDATE SET issue=excluded.issue,checked_at=excluded.checked_at
""", f.venue, f.event_id, why[:300], now)

    async def heartbeat(self, payload: dict, *, now: float):
        # default=str: the repository's heartbeat rule (test_r30a_runtime_defects).
        body = json.dumps(payload, sort_keys=True, allow_nan=False, default=str)
        if len(body.encode()) > MAX_RECORD_BYTES:
            raise ScoreError("SCORE_HEALTH_RECORD_TOO_LARGE")
        async with self.pool.acquire() as conn:
            await conn.execute("""
INSERT INTO trader_display_score_health(worker,heartbeat_at,payload)
VALUES('trader_live_scores',to_timestamp($1),$2::jsonb)
ON CONFLICT(worker) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,payload=excluded.payload
""", now, body)
