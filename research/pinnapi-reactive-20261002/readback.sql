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
