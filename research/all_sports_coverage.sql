-- cand24 ALL-SPORTS COVERAGE AUDIT (read-only), Sunday 2026-10-04.
-- The configured / provider / venue universe per league, and where each
-- stops. Statuses are assigned from these rows by the rule in
-- agents/coverage_integrity.classify_status:
--   HEALTHY | REFUSING_BY_POLICY | EXPLICITLY_UNSUPPORTED |
--   COVERAGE_INCIDENT | UNAVAILABLE
--   ET  day = [2026-10-04 04:00Z, 2026-10-05 04:00Z)
-- The collector's declared maps (code, not tables) are restated in A0 so a
-- reader can see which venue tokens are in scope.
\echo '== A0 · read instant; collector maps as deployed (ext_pinnacle_loop) =='
\echo '   SPORTS_CONFIRMED: baseball_mlb (token mlb)'
\echo '   VENUE_TOKEN_TO_PROVIDER_KEY: unl mls lmx uwcl cnl uslc arg2 brb lco uru1 nwsl'
\echo '   VENUE_FOOTBALL_TOKEN_TO_PROVIDER_KEY (prod 4717460): cfb -- nfl NOT mapped'
\echo '   EXCLUDED: intf (deliberate); REFUTED: engnl (and one more by fixtures)'
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== A1 · PinnAPI scope and its heartbeat (sport ids, markets by sport|type|phase) =='
SELECT value AS scope FROM ingestion_state WHERE key = 'pinnapi_feed_scope';
SELECT value->>'state' AS state,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'sport_ids' AS sport_ids,
       value->'cache'->'markets_by_sport_type_phase' AS markets_by_sport_type_phase
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== A2 · the collector (odds provider): requested, confirmed, rejected, dropped =='
SELECT to_timestamp((value->>'at')::float8) AS at,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'metered_budget' AS budget,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board,
       value->'sports_selection'->'venue_football_board'->'tokens' AS football_board
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== A3 · VENUE BOARD: every league token the venue lists, by family (ET day / next 24 h) =='
SELECT split_part(coalesce(sports_type, ''), '_', 1) AS family,
       lower(split_part(coalesce(event_slug, ''), '-', 1)) AS token,
       count(DISTINCT event_slug) FILTER (
           WHERE game_start >= timestamptz '2026-10-04 04:00Z'
             AND game_start <  timestamptz '2026-10-05 04:00Z') AS events_et_day,
       count(DISTINCT event_slug) FILTER (
           WHERE game_start >= now() AND game_start < now() + interval '24 hours') AS events_next_24h,
       count(DISTINCT market_slug) FILTER (
           WHERE sports_type LIKE '%full_game_winner' OR sports_type LIKE '%full_time_winner'
              OR sports_type LIKE '%match_winner') AS winner_contracts
  FROM us_premap
 WHERE game_start >= timestamptz '2026-10-04 04:00Z'
   AND game_start <  now() + interval '36 hours'
   AND event_slug IS NOT NULL
 GROUP BY 1, 2
HAVING count(DISTINCT event_slug) > 0
 ORDER BY 1, 3 DESC, 4 DESC
 LIMIT 120;

\echo '== A4 · PROVIDER -> MAPPED -> SETTLEMENT per provider league, ET day (collection ledger) =='
WITH r AS (
    SELECT sport_key, provider_event_id, us_market_slug, first_refusal,
           CASE WHEN outcome IN ('ADMITTED', 'ALREADY_RECORDED') THEN 99
                WHEN outcome = 'REFUSED' AND stage ~ '^[1-8]_' THEN
                     greatest(substr(stage, 1, 1)::int,
                              CASE WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END)
                WHEN us_market_slug IS NOT NULL THEN 4 ELSE 0 END AS reach
      FROM ext_candidate_outcomes
     WHERE cycle_at >= timestamptz '2026-10-04 04:00Z'
       AND cycle_at <  timestamptz '2026-10-05 04:00Z'
       AND provider_event_id IS NOT NULL),
e AS (SELECT sport_key, provider_event_id, max(reach) AS reach FROM r GROUP BY 1, 2)
SELECT sport_key AS league, count(*) AS provider_events,
       count(*) FILTER (WHERE reach >= 3) AS normalized,
       count(*) FILTER (WHERE reach >= 4) AS mapped,
       count(*) FILTER (WHERE reach >= 5) AS settlement_reach,
       (SELECT jsonb_object_agg(first_refusal, n) FROM (
            SELECT first_refusal, count(DISTINCT provider_event_id) AS n
              FROM r r2 WHERE r2.sport_key = e.sport_key AND first_refusal IS NOT NULL
             GROUP BY 1 ORDER BY 2 DESC LIMIT 6) t) AS top_refusals
  FROM e GROUP BY sport_key ORDER BY 2 DESC;

\echo '== A5 · EVALUATED -> DECIDED -> ENTER per league token, ET day =='
WITH v AS (
    SELECT lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS token,
           sport_family, record_purpose, id, us_market_slug, probability
      FROM external_valuations
     WHERE decided_at >= timestamptz '2026-10-04 04:00Z'
       AND decided_at <  timestamptz '2026-10-05 04:00Z'),
d AS (
    SELECT lower(split_part(coalesce(us_market_slug, ''), '-', 2)) AS token,
           us_market_slug, verdict, refusal
      FROM paper_decisions
     WHERE decided_at >= timestamptz '2026-10-04 04:00Z'
       AND decided_at <  timestamptz '2026-10-05 04:00Z'
       AND strategy = 'DEREK_ENTRY_POLICY_V2')
SELECT coalesce(v.token, d.token) AS token,
       max(v.sport_family) AS family,
       count(DISTINCT v.us_market_slug) AS valued_contracts,
       count(DISTINCT v.us_market_slug) FILTER (WHERE v.probability IS NOT NULL) AS with_probability,
       count(DISTINCT d.us_market_slug) AS derek_decided,
       count(DISTINCT d.us_market_slug) FILTER (WHERE d.verdict = 'ENTER') AS derek_enter,
       (array_agg(DISTINCT d.refusal) FILTER (WHERE d.refusal IS NOT NULL))[1:4] AS derek_refusals
  FROM v FULL JOIN d ON d.us_market_slug = v.us_market_slug
 GROUP BY 1 ORDER BY 3 DESC, 5 DESC;

\echo '== A6 · persisted funnel snapshots, ET today, and today''s alerts =='
SELECT league, provider_events AS prov, normalized_events AS norm,
       venue_discovered AS disc, mapped_events AS mapped,
       settlement_supported AS settle, evaluated_events AS eval,
       decided_events AS decided, entered_events AS entered,
       refused_events AS refused, venue_catalogue_events AS venue,
       unavailable, computed_at
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day = date '2026-10-04'
 ORDER BY provider_events DESC NULLS LAST, league;
SELECT league, kind, stage_to, severity, audrey_finding_id IS NOT NULL AS to_audrey,
       left(detail->>'statement', 120) AS statement, detected_at
  FROM coverage_collapse_alerts
 WHERE day >= date '2026-10-03'
 ORDER BY detected_at DESC LIMIT 20;

\echo '== A7 · settlement and fixture records by token / family (7 days) =='
SELECT lower(split_part(us_market_slug, '-', 2)) AS token, outcome,
       count(*) AS paper_settlements, max(recorded_at) AS last
  FROM paper_settlements
 WHERE recorded_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;
SELECT sport_family, competition, count(*) AS venue_fixture_metadata_rows,
       max(retrieved_at) AS last
  FROM venue_fixture_metadata
 WHERE retrieved_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
