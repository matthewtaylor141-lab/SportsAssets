-- READ-ONLY. RC6 lane C (software reds): (M) the global catalogue rows behind
-- the one VENUE_MAPPING_AMBIGUOUS event of the RC5 runtime (Milwaukee Brewers
-- v Los Angeles Dodgers, commence 2026-10-12T00:00Z) and the venue's own
-- catalogue rows for the fixture; (P) whether anything produces BETTOR
-- decision-pipeline orphans now: opportunities and decisions per hour since
-- the V6 freeze, the newest orphan, failures, and how close each tick's
-- opportunity and decision land. SELECT only.

\echo M1 open global catalogue rows naming both teams (what map_event saw)
SELECT condition_id, left(title, 70) AS title, left(event_title, 60) AS event_title,
       slug, event_slug, sport, closed, resolved, updated_at
  FROM markets
 WHERE (title || ' ' || coalesce(event_title, '')) ILIKE '%brewers%'
   AND (title || ' ' || coalesce(event_title, '')) ILIKE '%dodgers%'
   AND updated_at >= timestamptz '2026-10-07 00:00:00+00'
 ORDER BY updated_at DESC
 LIMIT 30;

\echo M2 the venue catalogue rows for the series (us_premap)
SELECT market_slug, event_slug, left(event_title, 60) AS event_title, side_norm,
       kind, sports_type, team_league, game_start, updated_at
  FROM us_premap
 WHERE event_slug ILIKE '%mlb%lad%mil%' OR event_slug ILIKE '%mlb%mil%lad%'
 ORDER BY game_start NULLS LAST, market_slug
 LIMIT 30;

\echo P1 opportunities, decisions and orphans per hour since 2026-10-08 05:00Z
SELECT date_trunc('hour', o.observed_at) AS hour, count(*) AS opportunities,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM shadow_decisions d
                                       WHERE d.bettor_opportunity_id = o.bettor_opportunity_id)) AS decided,
       count(*) FILTER (WHERE o.observed_at < now() - interval '180 seconds'
                          AND NOT EXISTS (SELECT 1 FROM shadow_decisions d
                                           WHERE d.bettor_opportunity_id = o.bettor_opportunity_id)) AS orphans
  FROM bettor_opportunities o
 WHERE o.observed_at >= timestamptz '2026-10-08 05:00:00+00'
 GROUP BY 1 ORDER BY 1;

\echo P2 the newest orphans (migration 073 view) and the newest failures
SELECT 'orphan' AS kind, bettor_opportunity_id AS id, symbol, observed_at AS at,
       annotated::text AS note
  FROM bettor_orphan_opportunities
 WHERE observed_at >= now() - interval '48 hours'
UNION ALL
SELECT 'failure', bettor_opportunity_id, symbol, failed_at, stage || ':' || error_class
  FROM bettor_decision_failures
 WHERE failed_at >= now() - interval '48 hours'
 ORDER BY at DESC
 LIMIT 20;

\echo P3 decision lag behind its opportunity (seconds), last 24 h
SELECT round(percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM d.decision_ts - o.observed_at))::numeric, 2) AS p50,
       round(percentile_disc(0.99) WITHIN GROUP (ORDER BY extract(epoch FROM d.decision_ts - o.observed_at))::numeric, 2) AS p99,
       round(max(extract(epoch FROM d.decision_ts - o.observed_at))::numeric, 2) AS max_s,
       count(*) AS n
  FROM bettor_opportunities o JOIN shadow_decisions d USING (bettor_opportunity_id)
 WHERE o.observed_at >= now() - interval '24 hours';

\echo P4 the shadow bettor heartbeat now (status, tick stats)
SELECT service, status, beat_at, left(detail::text, 1500) AS detail
  FROM service_heartbeats
 WHERE service ILIKE '%shadow_bettor%' OR service ILIKE '%bettor_shadow%'
 ORDER BY service;
