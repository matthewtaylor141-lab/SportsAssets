-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 8:
-- the PinnAPI heartbeat samples of unmatched venue events and of feed events, and the codes the
-- exact-fixture misses carry in the provider ledger.
\echo == A unmatched venue event sample (first 6000 chars)
SELECT left((value -> 'coverage_census' -> 'unmatched_event_sample')::text, 6000) AS unmatched_event_sample
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == B feed event sample (first 4000 chars)
SELECT left((value -> 'coverage_census' -> 'feed_event_sample')::text, 4000) AS feed_event_sample
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == C events by state in the census
SELECT left((value -> 'coverage_census' -> 'events_by_state')::text, 2500) AS events_by_state
  FROM ingestion_state WHERE key = 'pinnapi_feed_last';

\echo == D codes carried by the exact-fixture misses, last 6 hours, distinct code sets (top 15)
SELECT sport_key, outcome, first_refusal, codes::text AS code_set, count(DISTINCT provider_event_id) AS events
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '6 hours'
   AND (first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE' OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%')
 GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 15;

\echo == E the same events: how many have a mapped venue market (us_market_slug) and how many do not
SELECT sport_key, count(DISTINCT provider_event_id) FILTER (WHERE us_market_slug IS NOT NULL) AS with_a_venue_slug,
       count(DISTINCT provider_event_id) FILTER (WHERE us_market_slug IS NULL) AS without_a_venue_slug
  FROM ext_candidate_outcomes
 WHERE cycle_at >= now() - interval '6 hours'
   AND (first_refusal = 'PINNAPI_PRIMARY_NO_EXACT_FIXTURE' OR codes::text LIKE '%PINNAPI_PRIMARY_NO_EXACT_FIXTURE%')
 GROUP BY 1 ORDER BY 1;
