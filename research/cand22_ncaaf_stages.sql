-- cand22 NCAAF stage census (read-only). 2026-10-03 is a college-football
-- Saturday. Windows are explicit:
--   ET  day = [2026-10-03 00:00 America/New_York, 2026-10-04 00:00 America/New_York)
--           = [2026-10-03 04:00Z, 2026-10-04 04:00Z)
--   UTC day = [2026-10-03 00:00Z, 2026-10-04 00:00Z)
-- Venue league token for college football is 'cfb' (us_premap market_slug
-- position 2, e.g. aec-cfb-bayl-aubrn-...). PinnAPI sport id 5 = football.
\echo '== S0 · read instant =='
SELECT now() AS read_at, now() AT TIME ZONE 'America/New_York' AS read_at_et;

\echo '== S1 · PinnAPI feed control / scope / heartbeat (sport 5 = football) =='
SELECT key, left(value::text, 400) AS value
  FROM ingestion_state WHERE key IN ('pinnapi_feed', 'pinnapi_feed_scope');
SELECT value->>'state' AS state,
       to_timestamp((value->>'beat_at')::float8) AS beat_at,
       value->'sport_ids' AS sport_ids, value->'streams' AS streams,
       value->'scope' AS scope,
       (SELECT jsonb_object_agg(k, v) FROM jsonb_each(
          coalesce(value->'cache'->'markets_by_sport_type_phase', '{}'::jsonb)) AS e(k, v)
         WHERE k LIKE '5|%') AS football_markets_by_type_phase,
       (SELECT jsonb_object_agg(k, v) FROM jsonb_each(
          coalesce(value->'cache'->'markets_by_sport_type_phase', '{}'::jsonb)) AS e(k, v)
         WHERE k LIKE '%|moneyline|%') AS moneyline_markets_by_sport,
       value->'cache'->'events' AS cache_events,
       left((value->'coverage_census')::text, 1500) AS coverage_census
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo '== S2 · scheduled collector heartbeat: what it requested, funnel by provider sport =='
SELECT to_timestamp((value->>'at')::float8) AS at, value->>'state' AS state,
       value->'sports_selection'->'requested' AS requested,
       value->'sports_selection'->'venue_board'->'tokens' AS soccer_board_tokens,
       value->'sports_selection'->'rejected' AS rejected,
       value->'sports_selection'->'budget_dropped' AS budget_dropped,
       value->'venue_universe_by_label' AS venue_universe_by_label,
       left((value->'funnel_by_provider_sport')::text, 1500) AS funnel
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';

\echo '== S3 · Polymarket US discovered: us_premap cfb rows by sports_type (ET day / UTC day) =='
SELECT coalesce(sports_type, '(null)') AS sports_type,
       count(*) FILTER (WHERE game_start >= timestamptz '2026-10-03 04:00Z'
                          AND game_start <  timestamptz '2026-10-04 04:00Z') AS contracts_et_day,
       count(DISTINCT event_slug) FILTER (WHERE game_start >= timestamptz '2026-10-03 04:00Z'
                          AND game_start <  timestamptz '2026-10-04 04:00Z') AS events_et_day,
       count(*) FILTER (WHERE game_start >= timestamptz '2026-10-03 00:00Z'
                          AND game_start <  timestamptz '2026-10-04 00:00Z') AS contracts_utc_day,
       count(DISTINCT event_slug) FILTER (WHERE game_start >= timestamptz '2026-10-03 00:00Z'
                          AND game_start <  timestamptz '2026-10-04 00:00Z') AS events_utc_day
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND game_start >= timestamptz '2026-10-03 00:00Z'
   AND game_start <  timestamptz '2026-10-04 04:00Z'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== S3b · cfb moneyline-shaped rows (sports_type *winner*), ET day, examples =='
SELECT event_slug, left(event_title, 60) AS title, market_slug, kind, side_norm,
       intent, line, team_name, team_abbr, team_league, sports_type,
       game_start, game_start AT TIME ZONE 'America/New_York' AS start_et
  FROM us_premap
 WHERE split_part(market_slug, '-', 2) = 'cfb'
   AND sports_type ILIKE '%winner%'
   AND game_start >= timestamptz '2026-10-03 04:00Z'
   AND game_start <  timestamptz '2026-10-04 04:00Z'
 ORDER BY game_start, event_slug, market_slug LIMIT 24;

\echo '== S3c · every league token on the ET day with football sports_type =='
SELECT split_part(market_slug, '-', 2) AS token, sports_type, count(*) AS n,
       count(DISTINCT event_slug) AS events
  FROM us_premap
 WHERE sports_type ILIKE 'football%' OR sports_type ILIKE 'american%'
       OR split_part(market_slug, '-', 2) IN ('cfb', 'ncaaf', 'nfl')
 GROUP BY 1, 2
HAVING count(*) FILTER (WHERE true) > 0
 ORDER BY 3 DESC LIMIT 30;

\echo '== S4 · external_valuations by sport_family since 2026-10-03 00:00Z =='
SELECT sport_family, provider, record_purpose, count(*) AS n,
       count(*) FILTER (WHERE us_market_slug IS NOT NULL) AS with_venue_contract,
       count(*) FILTER (WHERE split_part(us_market_slug, '-', 2) = 'cfb') AS cfb_slug,
       min(decided_at) AS first, max(decided_at) AS last
  FROM external_valuations
 WHERE decided_at >= timestamptz '2026-10-03 00:00Z'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo '== S4b · any valuation touching football / cfb, ever (last 30 d) =='
SELECT sport_family, settlement_rule, count(*) AS n, max(decided_at) AS last,
       (array_agg(us_market_slug ORDER BY decided_at DESC))[1:5] AS slugs,
       (array_agg(array_to_string(refusals, ',') ORDER BY decided_at DESC))[1:3] AS refusals
  FROM external_valuations
 WHERE decided_at > now() - interval '30 days'
   AND (sport_family ILIKE '%football%' OR split_part(us_market_slug, '-', 2) IN ('cfb', 'nfl')
        OR event_key ILIKE '%ncaaf%' OR event_key ILIKE '%americanfootball%')
 GROUP BY 1, 2;

\echo '== S5 · paper decisions since 2026-10-03 00:00Z by competition/slug token and strategy =='
SELECT strategy, split_part(us_market_slug, '-', 2) AS token,
       coalesce(label->>'competition', label->>'sport', '(none)') AS competition,
       verdict, count(*) AS n
  FROM paper_decisions
 WHERE decided_at >= timestamptz '2026-10-03 00:00Z'
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;

\echo '== S5b · cfb paper decisions / orders / fills (ET day and UTC day) =='
SELECT 'decisions' AS stage,
       count(*) FILTER (WHERE decided_at >= timestamptz '2026-10-03 04:00Z' AND decided_at < timestamptz '2026-10-04 04:00Z') AS et_day,
       count(*) FILTER (WHERE decided_at >= timestamptz '2026-10-03 00:00Z' AND decided_at < timestamptz '2026-10-04 00:00Z') AS utc_day
  FROM paper_decisions WHERE split_part(us_market_slug, '-', 2) = 'cfb'
UNION ALL
SELECT 'orders',
       count(*) FILTER (WHERE created_at >= timestamptz '2026-10-03 04:00Z' AND created_at < timestamptz '2026-10-04 04:00Z'),
       count(*) FILTER (WHERE created_at >= timestamptz '2026-10-03 00:00Z' AND created_at < timestamptz '2026-10-04 00:00Z')
  FROM paper_orders WHERE split_part(us_market_slug, '-', 2) = 'cfb'
UNION ALL
SELECT 'fills',
       count(*) FILTER (WHERE filled_at >= timestamptz '2026-10-03 04:00Z' AND filled_at < timestamptz '2026-10-04 04:00Z'),
       count(*) FILTER (WHERE filled_at >= timestamptz '2026-10-03 00:00Z' AND filled_at < timestamptz '2026-10-04 00:00Z')
  FROM paper_fills WHERE split_part(us_market_slug, '-', 2) = 'cfb'
UNION ALL
SELECT 'execution_intents',
       count(*) FILTER (WHERE created_at >= timestamptz '2026-10-03 04:00Z' AND created_at < timestamptz '2026-10-04 04:00Z'),
       count(*) FILTER (WHERE created_at >= timestamptz '2026-10-03 00:00Z' AND created_at < timestamptz '2026-10-04 00:00Z')
  FROM execution_intents WHERE split_part(us_market_slug, '-', 2) = 'cfb';

\echo '== S6 · latest Derek coverage census =='
SELECT at, left(categories::text, 1200) AS categories,
       left(blocked_by_reason::text, 800) AS blocked_by_reason
  FROM derek_coverage_census ORDER BY at DESC LIMIT 1;

\echo '== S7 · reactive attempts today by state =='
SELECT state, count(*) FROM pinnapi_reactive_attempts
 WHERE created_at >= timestamptz '2026-10-03 00:00Z' GROUP BY 1;
