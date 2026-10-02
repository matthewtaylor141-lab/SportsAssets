-- WebSocket-triggered paper evaluation: production evidence (read-only).
-- Every timestamp below is a recorded one. Latency is measured on the
-- reactive attempt's own clocks: WS receipt -> queued -> worker start ->
-- persisted decision -> attempt finished. Venue-book age and order/fill
-- timing are reported separately. FeedCache.receipt_to_evaluation_ms is NOT
-- used here (it includes rejected stale cache reads).
\echo '== E0 · attempts by state and writer build (last 6 h) =='
SELECT state, detail->'writer'->>'build' AS writer_build,
       detail->'writer'->>'pid' AS writer_pid, count(*) AS attempts,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours'
 GROUP BY 1, 2, 3 ORDER BY 6 DESC;
\echo '== E1 · refusal and outcome reasons of the scheduler and the cycle (last 6 h) =='
SELECT state, coalesce(detail->>'reason', detail->'result'->>'state',
                       detail->>'error_type') AS why,
       count(*) AS attempts
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo '== E2 · cycle refusals inside completed reactive evaluations (last 6 h) =='
SELECT r.key AS refusal, sum((r.value)::text::numeric) AS n
  FROM pinnapi_reactive_attempts a,
       jsonb_each(coalesce(a.detail->'result'->'refusals', '{}'::jsonb)) r
 WHERE a.created_at > now() - interval '6 hours' AND a.state = 'COMPLETED'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
\echo '== E3 · attempt -> valuation -> decision, with the recorded latency chain =='
WITH a AS (
  SELECT attempt_id, event_id, state, created_at, detail,
         (detail->>'received_at')::float8 AS rx,
         (detail->>'queued_at')::float8 AS qd,
         (detail->>'evaluation_started_at')::float8 AS st,
         (detail->>'finished_at')::float8 AS fin,
         detail->'version'->>0 AS feed_epoch,
         detail->'version'->>1 AS provider_change_ms,
         jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint AS valuation_id
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours')
SELECT a.attempt_id, a.event_id AS feed_event_id, a.feed_epoch,
       a.provider_change_ms, to_timestamp(a.rx) AS ws_received_at,
       a.valuation_id, v.provider AS valuation_source,
       v.settlement_comparison->'reference_input'->>'provider' AS reference_provider,
       v.settlement_comparison->'reference_input'->'raw_odds' AS ws_raw_odds,
       d.decision_id, d.strategy, d.verdict, d.refusal, d.decided_at,
       round((1000 * (a.qd - a.rx))::numeric, 1) AS receipt_to_queue_ms,
       round((1000 * (a.st - a.rx))::numeric, 1) AS receipt_to_worker_ms,
       round((1000 * (extract(epoch FROM d.decided_at) - a.rx))::numeric, 1) AS receipt_to_decision_ms,
       round((1000 * (a.fin - a.rx))::numeric, 1) AS receipt_to_finish_ms,
       d.us_market_slug, d.holding_side, d.p_pinnacle, d.proposed_qty, d.limit_price
  FROM a LEFT JOIN external_valuations v ON v.id = a.valuation_id
         LEFT JOIN paper_decisions d ON d.valuation_id = a.valuation_id
 ORDER BY (d.verdict = 'ENTER') DESC NULLS LAST, a.created_at DESC LIMIT 25;
\echo '== E4 · latency percentiles over decisions reached from WS attempts (last 6 h) =='
WITH a AS (
  SELECT (detail->>'received_at')::float8 AS rx,
         (detail->>'evaluation_started_at')::float8 AS st,
         jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint AS valuation_id
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours'),
x AS (
  SELECT 1000 * (a.st - a.rx) AS to_worker,
         1000 * (extract(epoch FROM d.decided_at) - a.rx) AS to_decision
    FROM a JOIN paper_decisions d ON d.valuation_id = a.valuation_id)
SELECT count(*) AS decisions,
       round(percentile_cont(.5) WITHIN GROUP (ORDER BY to_worker)::numeric, 1) AS worker_p50_ms,
       round(percentile_cont(.95) WITHIN GROUP (ORDER BY to_worker)::numeric, 1) AS worker_p95_ms,
       round(percentile_cont(.5) WITHIN GROUP (ORDER BY to_decision)::numeric, 1) AS decision_p50_ms,
       round(percentile_cont(.95) WITHIN GROUP (ORDER BY to_decision)::numeric, 1) AS decision_p95_ms,
       round(max(to_decision)::numeric, 1) AS decision_max_ms
  FROM x;
\echo '== E5 · the executable venue book behind each WS decision: age, levels, depth, economics =='
WITH ids AS (
  SELECT DISTINCT jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint AS valuation_id
    FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours')
SELECT d.decision_id, d.verdict, d.book_obs_id, b.source AS book_source,
       b.read_basis, b.observed_at AS book_observed_at,
       round(extract(epoch FROM d.decided_at - b.observed_at)::numeric, 3) AS book_age_at_decision_s,
       left(b.offers::text, 240) AS offers, left(b.bids::text, 160) AS bids,
       d.policy_decision->>'gross_edge_pp' AS gross_edge_pp,
       d.policy_decision->>'net_expected_profit_usd' AS net_ev_usd,
       left(d.economics::text, 700) AS economics
  FROM ids JOIN paper_decisions d ON d.valuation_id = ids.valuation_id
  LEFT JOIN paper_book_observations b ON b.obs_id = d.book_obs_id
 ORDER BY (d.verdict = 'ENTER') DESC, d.decided_at DESC LIMIT 15;
\echo '== E6 · orders, fills, Xavier handoffs and Audrey findings from WS decisions =='
WITH dec AS (
  SELECT d.decision_id, d.decided_at FROM paper_decisions d
   WHERE d.valuation_id IN (
     SELECT jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint
       FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours'))
SELECT dec.decision_id, o.order_id, o.state AS order_state, o.qty, o.limit_price,
       o.created_at AS order_created_at, o.eligible_at,
       f.fill_id, f.filled_at, f.qty AS fill_qty, f.price AS fill_price,
       f.fee_usd, f.gross_usd, f.book_observed_at AS fill_book_observed_at,
       round(extract(epoch FROM f.filled_at - dec.decided_at)::numeric, 3) AS decision_to_fill_s,
       h.handoff_id, h.owner AS handoff_owner, h.first_fill_at, h.created_at AS handoff_at,
       (SELECT count(*) FROM paper_xavier_reviews x WHERE x.group_id = o.group_id) AS xavier_reviews,
       (SELECT string_agg(af.kind || ':' || coalesce(af.detail->>'refusal', ''), ', ')
          FROM paper_audrey_findings af WHERE af.subject = dec.decision_id) AS audrey_findings
  FROM dec LEFT JOIN paper_orders o ON o.decision_id = dec.decision_id AND o.role = 'ENTRY'
           LEFT JOIN paper_fills f ON f.order_id = o.order_id
           LEFT JOIN paper_handoffs h ON h.group_id = o.group_id
 WHERE o.order_id IS NOT NULL OR EXISTS (
   SELECT 1 FROM paper_audrey_findings af WHERE af.subject = dec.decision_id)
 ORDER BY dec.decided_at DESC LIMIT 20;
\echo '== E7 · collector cycles around the WS decisions (Derek runs, last 6 h) =='
WITH dec AS (
  SELECT d.decision_id, d.decided_at FROM paper_decisions d
   WHERE d.valuation_id IN (
     SELECT jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint
       FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '6 hours')
   ORDER BY d.decided_at DESC LIMIT 10)
SELECT dec.decision_id, dec.decided_at,
       (SELECT max(r.started_at) FROM agent_runs r WHERE r.agent_id = 'DEREK'
         AND r.started_at <= dec.decided_at) AS prev_cycle_started,
       (SELECT r.finished_at FROM agent_runs r WHERE r.agent_id = 'DEREK'
         AND r.started_at <= dec.decided_at ORDER BY r.started_at DESC LIMIT 1) AS prev_cycle_finished,
       (SELECT min(r.started_at) FROM agent_runs r WHERE r.agent_id = 'DEREK'
         AND r.started_at > dec.decided_at) AS next_cycle_started
  FROM dec ORDER BY dec.decided_at DESC;
\echo '== E8 · the serving writer: last collector cycle and feed heartbeat builds =='
SELECT key, value->'writer'->>'build' AS cycle_writer_build,
       value->>'writer_pid' AS feed_writer_pid, value->>'state' AS state,
       to_timestamp(coalesce((value->>'at')::float8, (value->>'beat_at')::float8)) AS at
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'pinnapi_feed_last');
