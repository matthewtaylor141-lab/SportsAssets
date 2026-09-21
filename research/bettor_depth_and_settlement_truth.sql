-- TWO CLAIMS OF MINE, CHECKED AGAINST THE COLUMNS THEY WERE ABOUT.
--
-- I wrote "depth is ABSENT_IN_CAPTURE_SCHEMA -- there is no size column" and
-- "no outcome has matured for any observation". Neither was verified.
--
-- Migration 088 declares yes_depth, no_depth and multi_level_depth as JSONB
-- and bettor_state_store INSERTs all three. I never queried them.
--
-- And bettor_state_store.record_settlement() is DEFINED AND NEVER CALLED --
-- workers/bettor_state.py calls only record_mid. So an empty settlements
-- table is a MISSING INGESTION PATH, not evidence that nothing resolved.
-- Read only.
\echo == 1. IS DEPTH ACTUALLY POPULATED ==
SELECT count(*) AS rows,
       count(yes_depth) AS yes_depth_present,
       count(no_depth) AS no_depth_present,
       count(multi_level_depth) AS multi_level_present,
       count(*) FILTER (WHERE yes_depth::text NOT IN ('null','"NOT_IDENTIFIED"'))
           AS yes_depth_substantive
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days';

\echo
\echo == 2. WHAT DEPTH ACTUALLY LOOKS LIKE ==
SELECT market_id, outcome_leg, yes_bid, yes_ask,
       left(yes_depth::text, 120) AS yes_depth,
       left(multi_level_depth::text, 160) AS multi_level
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND yes_depth IS NOT NULL
 ORDER BY observed_at DESC
 LIMIT 8;

\echo
\echo == 3. HAVE THE OBSERVED MARKETS RESOLVED? ==
-- The question the empty settlements table cannot answer. A market whose
-- event date has passed has almost certainly resolved at the venue, whether
-- or not we ingested it.
SELECT count(DISTINCT market_id) AS distinct_markets,
       count(DISTINCT market_id) FILTER (
         WHERE observed_at < now() - interval '48 hours')
           AS observed_over_48h_ago,
       min(observed_at) AS earliest, max(observed_at) AS latest
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '14 days';

\echo
\echo == 4. DOES ANY OTHER TABLE KNOW AN OUTCOME FOR THESE MARKETS ==
SELECT 'bettor_state_settlements' AS src, count(*) AS rows
  FROM bettor_state_settlements
UNION ALL
SELECT 'live_orders settled', count(*) FROM live_orders WHERE status='settled'
UNION ALL
SELECT 'bettor_state_mids', count(*) FROM bettor_state_mids;

\echo
\echo == 5. MULTI-LEVEL LADDER SHAPE, IF PRESENT ==
SELECT jsonb_typeof(multi_level_depth) AS json_type, count(*) AS rows
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '7 days'
   AND multi_level_depth IS NOT NULL
 GROUP BY 1;
