-- WHAT THE DEPLOYS OF 2026-09-21 DID, IF ANYTHING, TO ORDERS.
--
-- Twelve deploys reached sportsassets-workers today, every one of them
-- from claude/session-njaewf. I had reported that nothing from that
-- branch was running. It was, and the operational record has to say so
-- with more than a branch name.
--
-- THE ABSENCE OF AN OBSERVATION-LOOP REGISTRATION IS NOT EVIDENCE OF
-- NO EFFECT. Those images also carried the execution gate, the
-- live_executor lane tags and the capture pacing change. This asks the
-- order and control planes directly, by hour, so "no change" is a
-- measurement rather than an inference.
--
-- READ-ONLY. Every statement is a SELECT.

\echo
\echo ===== 0. which order-bearing tables exist
SELECT table_name
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND table_name IN ('ai_trades', 'trades', 'engine_fills',
                      'trade_marks', 'mirror_fill_answers',
                      'bettor_state_settlements', 'copy_probes')
 ORDER BY 1;

\echo
\echo ===== 1. ai_trades by hour, last 36 hours (the paper/copy plane)
SELECT date_trunc('hour', created_at) AS hour,
       count(*)                       AS rows,
       count(DISTINCT status)         AS distinct_statuses,
       string_agg(DISTINCT status, ',' ORDER BY status) AS statuses
  FROM ai_trades
 WHERE created_at > now() - interval '36 hours'
 GROUP BY 1 ORDER BY 1;

\echo
\echo ===== 2. anything that reached a venue today, by status
SELECT status, count(*) AS n,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM ai_trades
 WHERE created_at > now() - interval '36 hours'
 GROUP BY 1 ORDER BY 2 DESC;

\echo
\echo ===== 3. engine_fills, last 36 hours
SELECT count(*) AS fills, min(created_at) AS first_at,
       max(created_at) AS last_at
  FROM engine_fills
 WHERE created_at > now() - interval '36 hours';

\echo
\echo ===== 4. the control plane, as stored
SELECT key, left(value::text, 120) AS value
  FROM ingestion_state
 WHERE key IN ('live_trading_paused', 'mirror_live', 'mirror_live_trip')
 ORDER BY key;

\echo
\echo ===== 5. worker boots today, from the heartbeat plane
SELECT service, status, beat_at
  FROM service_heartbeats
 WHERE beat_at > now() - interval '36 hours'
 ORDER BY beat_at DESC
 LIMIT 40;
