-- READ-ONLY. The PinnAPI feed heartbeat's coverage census and native
-- discovery, NCAAF (cfb) slices: how many venue events the provider holds,
-- matched, and why not. Every statement is a SELECT.
SELECT jsonb_object_keys(value->'coverage_census') FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT jsonb_object_keys(value->'native_discovery') FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT k, v::text FROM ingestion_state,
       jsonb_each(coalesce(value->'native_discovery'->'by_sport_league_state', '{}')) AS e(k, v)
 WHERE key = 'pinnapi_feed_last' AND (k ILIKE '%cfb%' OR k ILIKE '%ncaa%' OR k ILIKE 'americanfootball%' OR k ILIKE 'football%');
SELECT left((value->'coverage_census')::text, 3000) FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left((value->'native_discovery')::text, 3000) FROM ingestion_state WHERE key = 'pinnapi_feed_last';
SELECT left(value::text, 1500) FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
