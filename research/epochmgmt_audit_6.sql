-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 6:
-- the PinnAPI feed heartbeat census for the competitions whose fixtures miss, and the
-- game-level contract series that state no postponement window.
\echo == A feed heartbeat age and census keys
SELECT key, value ->> 'beat_at' AS beat_at, extract(epoch FROM now())::bigint AS now_epoch, length(value::text) AS bytes
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT jsonb_object_keys(value -> 'coverage_census') AS census_key
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == B coverage census text (first 5000 chars)
SELECT left((value -> 'coverage_census')::text, 5000) AS coverage_census
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == C native discovery by sport league state (MLS, Liga MX, NCAAF, NFL, MLB rows)
SELECT k, v::text AS counts
  FROM ingestion_state, jsonb_each(coalesce(value -> 'native_discovery' -> 'by_sport_league_state', '{}'::jsonb)) AS e(k, v)
 WHERE key = 'pinnapi_feed_last'
   AND (k ILIKE '%mls%' OR k ILIKE '%mex%' OR k ILIKE '%ligamx%' OR k ILIKE '%ncaaf%' OR k ILIKE '%cfb%' OR k ILIKE '%nfl%' OR k ILIKE '%mlb%' OR k ILIKE '%wnba%')
 ORDER BY k LIMIT 60;

\echo == D game-level contract series (GAME, SPREAD, TOTAL) by parse status and stated postponement window
SELECT split_part(contract_id, '-', 1) AS series, parse_status, count(*) AS contracts,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'postponement_window_hours') AS with_window,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'postponement_payout') AS with_payout
  FROM market_plane_rules
 WHERE venue = 'KALSHI' AND observed_at >= now() - interval '2 days'
   AND split_part(contract_id, '-', 1) ~ '^kalshi:KX(NHL|NBA|MLB|NFL|NCAAF|WNBA|MLS|EPL|CFB|CBB)[0-9A-Z]*(GAME|SPREAD|TOTAL)$'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50;

\echo == E Polymarket US game-level prefixes by parse status and stated postponement window
SELECT regexp_replace(contract_id, '-20[0-9][0-9]-.*$', '') AS prefix_sample, parse_status, count(*) AS contracts,
       count(*) FILTER (WHERE evidence -> 'settlement' ? 'postponement_window_hours') AS with_window
  FROM market_plane_rules
 WHERE venue = 'POLYMARKET_US' AND observed_at >= now() - interval '2 days'
   AND contract_id ~ '^(aec|asc|tsc|atc)-(nhl|nba|mlb|nfl|cfb|wnba|mls)-'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 40;
