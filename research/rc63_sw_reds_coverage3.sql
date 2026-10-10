-- READ-ONLY. RC6.3b software-reds audit part 8: (a) which Pinnacle market types the PinnAPI feed
-- cache holds (ingestion_state pinnapi_feed_last, cache.markets_by_sport_type_phase) -- the fair
-- value source universe for line and prop contracts; (b) whether the venue publishes the fixture
-- scope (phase, game format) the plane needs for the soccer / baseball money lines the packet
-- counted as QUOTE_CONTEXT_NOT_ESTABLISHED. SELECT only.

\echo == M1 feed cache markets by sport, market type, phase (top 60 by count)
SELECT k AS sport_type_phase, v::bigint AS markets
  FROM ingestion_state, jsonb_each_text(value->'cache'->'markets_by_sport_type_phase') AS t(k, v)
 WHERE key = 'pinnapi_feed_last' AND jsonb_typeof(value->'cache'->'markets_by_sport_type_phase') = 'object'
 ORDER BY 2 DESC LIMIT 60;

\echo == M2 feed cache: events by sport id and child record classes
SELECT value->'cache'->'events_by_sport' AS events_by_sport, value->'cache'->'child_records' AS child_records,
       value->'cache'->>'markets' AS markets
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == M3 soccer and baseball money lines awaiting scope: does the venue fixture row exist
SELECT r.sport, count(*) AS contracts,
       count(*) FILTER (WHERE m.venue_fixture_key IS NOT NULL) AS with_scope_row,
       count(*) FILTER (WHERE m.venue_fixture_key IS NOT NULL AND m.phase IS NOT NULL AND m.game_format IS NOT NULL) AS with_phase_and_format
  FROM market_plane_registry r
  LEFT JOIN venue_fixture_metadata m ON m.venue = 'PMUS' AND m.venue_fixture_key = 'event:' || r.event_id
 WHERE r.active AND r.venue = 'POLYMARKET_US'
   AND r.settlement_why LIKE 'BOOKMAKER_TERMS_NOT_HELD:QUOTE_CONTEXT_NOT_ESTABLISHED%'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 6;

\echo == M4 venue_fixture_metadata phase and game_format values for PMUS (what the venue states)
SELECT coalesce(phase, '(null)') AS phase, coalesce(game_format, '(null)') AS game_format, count(*) AS fixtures
  FROM venue_fixture_metadata WHERE venue = 'PMUS' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 12;
