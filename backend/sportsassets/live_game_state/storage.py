"""Postgres display store and canonical held-fixture adapter.

Only new trader_display_score_* tables are written. Canonical PAPER holdings,
venue fixture metadata and catalogues are read, never modified. Bindings and
observations are append-only; latest/health are explicitly rebuildable views.
"""
from __future__ import annotations

import json
from typing import Any

from .core import (Fixture, ScoreError, obj, epoch, league_of, digest, text,
                   display, MAX_RECORD_BYTES, AUTHORITY)

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
    if not home or not away or not league or start is None:
        raise ScoreError("CANONICAL_SCORE_FIXTURE_FIELDS_MISSING")
    expected_key = "event:" + event_id
    if fm and fm.get("venue_fixture_key") != expected_key:
        raise ScoreError("CANONICAL_FIXTURE_JOIN_MISMATCH")
    if fm.get("orientation") and fm["orientation"] not in ("HOME_AWAY", "HOME_VS_AWAY", "home_away"):
        raise ScoreError("CANONICAL_HOME_AWAY_UNPROVEN")
    ev = {"basis": "EXACT_VENUE_CATALOGUE_AND_FIXTURE_ROW", "event_id": event_id,
          "market": row.get("us_market_slug"), "fixture_source": fm.get("source"),
          "fixture_source_match_id": fm.get("source_match_id"),
          "home": home, "away": away, "league": league, "scheduled_start": start}
    raw_meta = object_value(fm.get("raw"))
    return Fixture("POLYMARKET_US", event_id, league, home, away, start, digest(ev),
                   raw_meta.get("game_number") if isinstance(raw_meta.get("game_number"), int) else None)


def fixture_query(canonical_sql: str) -> str:
    return """
WITH held AS (
""" + canonical_sql + """
), markets AS (
 SELECT DISTINCT us_market_slug FROM held WHERE account_id=$1
)
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

    async def fixtures(self, account_id="paper_acct_main") -> tuple[list[Fixture], dict]:
        from ..open_position_canon import CANONICAL_OPEN_POSITIONS_SQL
        async with self.pool.acquire() as conn:
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
        return [f for f in fixtures.values() if f], {
            "held_market_rows": min(len(rows), 1000), "truncated": len(rows) > 1000,
            "established_fixtures": sum(f is not None for f in fixtures.values()),
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
        body = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(body.encode()) > MAX_RECORD_BYTES:
            raise ScoreError("SCORE_HEALTH_RECORD_TOO_LARGE")
        async with self.pool.acquire() as conn:
            await conn.execute("""
INSERT INTO trader_display_score_health(worker,heartbeat_at,payload)
VALUES('trader_live_scores',to_timestamp($1),$2::jsonb)
ON CONFLICT(worker) DO UPDATE SET heartbeat_at=excluded.heartbeat_at,payload=excluded.payload
""", now, body)
