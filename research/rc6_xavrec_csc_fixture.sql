-- READ-ONLY. RC6 lane xavier-records: the 25 PinnAPI-sourced valuations of
-- the CSC contract (provider pinnapi.com/raw-websocket) carry the metered
-- provider's event key (the venue event's sticky key, migration 261). Which
-- PinnAPI matchup did they price? Every provider-event-id field recorded
-- anywhere on those rows, and the same for every held-contract-shaped
-- PinnAPI valuation whose event key is NOT a pinnapi: key (how many held
-- reads would lack the entry-proven fixture). Every statement is a SELECT.

\echo F1 the CSC PinnAPI-sourced valuations: recorded provider event ids
SELECT v.id, v.decided_at, v.event_key, v.record_purpose, v.contract_selection,
       jsonb_path_query_array(to_jsonb(v) - 'raw_odds',
         'lax $.**.provider_event_id_is_not_the_event_key') AS pe_not_key,
       jsonb_path_query_array(to_jsonb(v) - 'raw_odds',
         'lax $.**.provider_event_id') AS provider_event_id,
       jsonb_path_query_array(to_jsonb(v) - 'raw_odds',
         'lax $.**.feed_event_id') AS feed_event_id,
       jsonb_path_query_array(to_jsonb(v) - 'raw_odds',
         'lax $.**.discovery_event_id') AS discovery_event_id
  FROM external_valuations v
 WHERE v.us_market_slug = 'atc-brb-csc-cri-2026-10-08-csc'
   AND v.provider = 'pinnapi.com/raw-websocket'
 ORDER BY v.decided_at LIMIT 30;

\echo F2 the venue fixture key fixed for the CSC event (migration 261)
SELECT * FROM venue_fixture_event_keys
 WHERE venue_event_slug = 'brb-csc-cri-2026-10-08';

\echo F3 PinnAPI-sourced valuations, last 7 days, by event key namespace (pinnapi: vs a metered key)
SELECT (v.event_key LIKE 'pinnapi:%') AS pinnapi_key, v.record_purpose,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS contracts,
       count(DISTINCT v.event_key) AS event_keys, max(v.decided_at) AS newest
  FROM external_valuations v
 WHERE v.provider = 'pinnapi.com/raw-websocket'
   AND v.decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo F4 paper ENTRY decisions, last 7 days, whose valuation was metered-keyed while PinnAPI also priced the same contract
SELECT d.strategy, count(DISTINCT o.group_id) AS groups,
       count(DISTINCT o.group_id) FILTER (WHERE EXISTS (
         SELECT 1 FROM external_valuations p
          WHERE p.us_market_slug = v.us_market_slug
            AND p.provider = 'pinnapi.com/raw-websocket'
            AND p.payout_event IS NOT DISTINCT FROM v.payout_event)) AS also_pinnapi_priced
  FROM paper_orders o JOIN paper_decisions d ON d.decision_id = o.decision_id
  JOIN external_valuations v ON v.id = d.valuation_id
 WHERE o.role = 'ENTRY' AND o.created_at > now() - interval '7 days'
   AND coalesce(v.event_key, '') NOT LIKE 'pinnapi:%'
 GROUP BY 1 ORDER BY 2 DESC;
