-- WebSocket-triggered paper evaluations (read-only). Codex readback, then the
-- chain from each attempt's valuation IDs to paper decisions, orders and fills.
-- Single snapshot; distinguishes worker completion from actual paper decisions.
WITH attempts AS MATERIALIZED (
  SELECT * FROM pinnapi_reactive_attempts
  WHERE created_at >= now() - interval '1 hour'
), ids AS (
  SELECT DISTINCT (jsonb_array_elements_text(
    COALESCE(detail->'valuation_ids', '[]'::jsonb)))::bigint AS valuation_id
  FROM attempts
)
SELECT jsonb_build_object(
  'read_at', now(),
  'state_counts', (SELECT jsonb_object_agg(state,n) FROM
    (SELECT state,count(*) n FROM attempts GROUP BY state) c),
  'distinct_feed_events', (SELECT count(DISTINCT event_id) FROM attempts),
  'persisted_valuation_count', (SELECT count(*) FROM ids),
  'receipt_to_worker_ms_p50', (SELECT percentile_cont(.5) WITHIN GROUP (
    ORDER BY 1000*((detail->>'evaluation_started_at')::double precision-
                  (detail->>'received_at')::double precision)) FROM attempts),
  'receipt_to_worker_ms_p95', (SELECT percentile_cont(.95) WITHIN GROUP (
    ORDER BY 1000*((detail->>'evaluation_started_at')::double precision-
                  (detail->>'received_at')::double precision)) FROM attempts),
  'unfinished_over_30s', (SELECT count(*) FROM attempts WHERE state='STARTED'
    AND created_at < now()-interval '30 seconds'),
  'latest', (SELECT jsonb_agg(t) FROM
    (SELECT attempt_id,event_id,state,created_at,updated_at,detail
     FROM attempts ORDER BY created_at DESC LIMIT 20) t)
) AS websocket_evaluation_readback;
\echo '== R1 · attempts -> valuations -> paper decisions (last 2 h) =='
WITH a AS (
  SELECT attempt_id, event_id, state, created_at, detail,
         (detail->>'received_at')::double precision AS rx,
         (detail->>'queued_at')::double precision AS q,
         (detail->>'evaluation_started_at')::double precision AS st,
         (detail->>'finished_at')::double precision AS fin,
         jsonb_array_elements_text(coalesce(detail->'valuation_ids','[]'::jsonb))::bigint AS valuation_id
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '2 hours')
SELECT a.attempt_id, a.event_id, a.state, a.valuation_id,
       round((1000*(a.q - a.rx))::numeric, 1) AS receipt_to_queue_ms,
       round((1000*(a.st - a.rx))::numeric, 1) AS receipt_to_worker_ms,
       round((1000*(a.fin - a.rx))::numeric, 1) AS receipt_to_finish_ms,
       d.decision_id, d.strategy, d.verdict, d.refusal,
       round((1000*(extract(epoch FROM d.decided_at) - a.rx))::numeric, 1) AS receipt_to_decision_ms,
       d.us_market_slug, d.book_obs_id,
       left(d.book::text, 220) AS book, left(d.economics::text, 260) AS economics
  FROM a LEFT JOIN paper_decisions d ON d.valuation_id = a.valuation_id
 ORDER BY a.created_at DESC LIMIT 30;
\echo '== R2 · orders and fills from those decisions =='
SELECT o.order_id, o.decision_id, o.group_id, o.role, o.state, o.created_at,
       (SELECT count(*) FROM paper_fills f WHERE f.order_id = o.order_id) AS fills
  FROM paper_orders o
 WHERE o.decision_id IN (
   SELECT d.decision_id FROM paper_decisions d WHERE d.valuation_id IN (
     SELECT jsonb_array_elements_text(coalesce(detail->'valuation_ids','[]'::jsonb))::bigint
       FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '2 hours'))
 ORDER BY o.created_at DESC LIMIT 20;
\echo '== R3 · scheduled collector cycles in the same window (to show between-cycle timing) =='
SELECT value->>'at' AS last_cycle_epoch, value->>'state' AS state,
       value->>'elapsed_s' AS elapsed_s FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
