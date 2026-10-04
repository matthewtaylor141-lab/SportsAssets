-- P0 INCIDENT (2026-10-04) -- AGENT DELIVERY, part 2: why ORDERED does not
-- become FILLED (expiry reasons, the books observed inside each order's
-- window), how the paper book reads fail, the PinnAPI reactive router's
-- losses (attempt states, refusal reasons, in-memory counters), and how often
-- each competition is in the metered REST cycle. READ-ONLY; 24 h windows;
-- every row dump has a LIMIT.

\echo == A1 ENTRY orders (24 h) by strategy x state x terminal_reason ==
SELECT strategy, state, coalesce(terminal_reason, '-') AS terminal_reason,
       count(*) AS orders, count(DISTINCT us_market_slug) AS markets
  FROM paper_orders
 WHERE role = 'ENTRY' AND created_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 60;

\echo == A2 each ENTRY order (24 h): books observed inside [eligible_at, expires_at], readable vs errored, and the first one ==
WITH o AS (
  SELECT order_id, strategy, us_market_slug, state, terminal_reason,
         eligible_at, expires_at, created_at
    FROM paper_orders
   WHERE role = 'ENTRY' AND created_at > now() - interval '24 hours'),
b AS (
  SELECT o.order_id,
         count(p.obs_id) AS books_in_window,
         count(p.obs_id) FILTER (WHERE p.error IS NULL) AS readable_in_window,
         count(p.obs_id) FILTER (WHERE p.error IS NOT NULL) AS errored_in_window,
         min(p.observed_at) AS first_obs_at,
         min(p.observed_at) FILTER (WHERE p.error IS NULL) AS first_readable_at
    FROM o
    LEFT JOIN paper_book_observations p
      ON p.us_market_slug = o.us_market_slug
     AND p.observed_at >= o.eligible_at AND p.observed_at <= o.expires_at
   GROUP BY o.order_id)
SELECT o.strategy, o.state, coalesce(o.terminal_reason, '-') AS terminal_reason,
       count(*) AS orders,
       count(*) FILTER (WHERE b.books_in_window = 0) AS no_book_in_window,
       count(*) FILTER (WHERE b.books_in_window > 0
                          AND b.first_readable_at IS NULL) AS only_errored_books,
       count(*) FILTER (WHERE b.first_readable_at IS NOT NULL
                          AND b.first_obs_at < b.first_readable_at) AS errored_first_then_readable,
       count(*) FILTER (WHERE b.first_readable_at IS NOT NULL
                          AND b.first_obs_at = b.first_readable_at) AS readable_first,
       round(avg(extract(epoch FROM (o.expires_at - o.eligible_at)))::numeric, 1) AS avg_window_s,
       round(avg(extract(epoch FROM (b.first_obs_at - o.eligible_at)))::numeric, 2) AS avg_first_obs_after_eligible_s
  FROM o JOIN b USING (order_id)
 GROUP BY 1, 2, 3
 ORDER BY 1, orders DESC
 LIMIT 60;

\echo == A3 the first observation of each EXPIRED order: its error text and read basis ==
WITH o AS (
  SELECT order_id, strategy, us_market_slug, eligible_at, expires_at
    FROM paper_orders
   WHERE role = 'ENTRY' AND state = 'EXPIRED'
     AND created_at > now() - interval '24 hours'),
f AS (
  SELECT DISTINCT ON (o.order_id) o.order_id, o.strategy, p.read_basis,
         p.source, left(coalesce(p.error, 'READABLE'), 90) AS err
    FROM o
    JOIN paper_book_observations p
      ON p.us_market_slug = o.us_market_slug
     AND p.observed_at >= o.eligible_at AND p.observed_at <= o.expires_at
   ORDER BY o.order_id, p.observed_at)
SELECT strategy, read_basis, source, err, count(*) AS orders
  FROM f
 GROUP BY 1, 2, 3, 4
 ORDER BY orders DESC
 LIMIT 40;

\echo == A4 paper book observations (24 h) by read_basis x source x error ==
SELECT read_basis, source, left(coalesce(error, 'READABLE'), 90) AS err,
       count(*) AS reads, count(DISTINCT us_market_slug) AS markets
  FROM paper_book_observations
 WHERE observed_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY reads DESC
 LIMIT 60;

\echo == A5 paper book read error rate per hour (24 h) ==
SELECT date_trunc('hour', observed_at) AS hour,
       count(*) AS reads,
       count(*) FILTER (WHERE error IS NOT NULL) AS errored,
       round(100.0 * count(*) FILTER (WHERE error IS NOT NULL)
             / greatest(count(*), 1), 1) AS pct_errored
  FROM paper_book_observations
 WHERE observed_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1
 LIMIT 30;

\echo == A6 order events (24 h) for ENTRY orders: kind x reason ==
SELECT o.strategy, e.kind, left(coalesce(e.detail->>'reason', '-'), 70) AS reason,
       count(*) AS events
  FROM paper_order_events e
  JOIN paper_orders o ON o.order_id = e.order_id
 WHERE o.role = 'ENTRY' AND e.at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, events DESC
 LIMIT 60;

\echo == R1 PinnAPI reactive attempts (24 h) by state x reason ==
SELECT state, left(coalesce(detail->>'reason', detail->>'error_type', '-'), 70) AS reason,
       count(*) AS attempts, count(DISTINCT event_id) AS events,
       count(*) FILTER (WHERE (detail->>'held')::boolean) AS held
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY attempts DESC
 LIMIT 40;

\echo == R2 PinnAPI reactive: attempts per hour and evaluation latency (24 h) ==
SELECT date_trunc('hour', created_at) AS hour, count(*) AS attempts,
       count(*) FILTER (WHERE state = 'COMPLETED') AS completed,
       count(*) FILTER (WHERE state = 'TIMEOUT') AS timeouts,
       round(avg((detail->>'finished_at')::float8
                 - (detail->>'evaluation_started_at')::float8)::numeric, 2) AS avg_eval_s,
       round(avg((detail->>'evaluation_started_at')::float8
                 - (detail->>'queued_at')::float8)::numeric, 2) AS avg_queue_wait_s,
       round(max((detail->>'evaluation_started_at')::float8
                 - (detail->>'queued_at')::float8)::numeric, 2) AS max_queue_wait_s
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1
 LIMIT 30;

\echo == R3 PinnAPI reactive scheduler counters: the newest snapshot (cumulative since process start) ==
SELECT created_at, detail->>'writer' AS writer, detail->'counters' AS counters
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
 ORDER BY created_at DESC
 LIMIT 1;

\echo == R4 the oldest snapshot of the newest writer (to difference the counters) ==
WITH w AS (
  SELECT detail->'writer'->>'runtime_id' AS rid
    FROM pinnapi_reactive_attempts
   WHERE created_at > now() - interval '24 hours'
   ORDER BY created_at DESC LIMIT 1)
SELECT a.created_at, a.detail->'counters' AS counters
  FROM pinnapi_reactive_attempts a, w
 WHERE a.created_at > now() - interval '24 hours'
   AND a.detail->'writer'->>'runtime_id' = w.rid
 ORDER BY a.created_at
 LIMIT 1;

\echo == R5 reactive attempts per event (24 h): how many distinct PinnAPI events were evaluated, by valuation outcome ==
SELECT count(DISTINCT event_id) AS events_attempted,
       count(DISTINCT event_id) FILTER (WHERE state = 'COMPLETED') AS events_completed,
       count(*) FILTER (WHERE state = 'COMPLETED'
                          AND jsonb_array_length(coalesce(detail->'valuation_ids', '[]'::jsonb)) = 0)
           AS completed_without_valuation,
       count(*) FILTER (WHERE jsonb_array_length(coalesce(detail->'valuation_ids', '[]'::jsonb)) > 0)
           AS attempts_with_valuation
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours';

\echo == R6 why a COMPLETED reactive evaluation wrote no valuation: the cycle tally it returned ==
SELECT left((detail->'result'->'refusals')::text, 300) AS refusals, count(*) AS attempts
  FROM pinnapi_reactive_attempts
 WHERE created_at > now() - interval '24 hours'
   AND state = 'COMPLETED'
   AND jsonb_array_length(coalesce(detail->'valuation_ids', '[]'::jsonb)) = 0
 GROUP BY 1
 ORDER BY attempts DESC
 LIMIT 25;

\echo == S1 metered REST cycles (24 h): how often each competition is in the cycle (REST = a cycle with rows for 2+ sport keys) ==
WITH c AS (
  SELECT cycle_id, min(cycle_at) AS at, count(DISTINCT sport_key) AS sk
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1),
rest AS (SELECT cycle_id FROM c WHERE sk >= 2)
SELECT e.sport_key,
       count(DISTINCT e.cycle_id) AS rest_cycles_including,
       (SELECT count(*) FROM rest) AS rest_cycles_total,
       count(DISTINCT e.provider_event_id) AS events,
       count(*) FILTER (WHERE e.outcome = 'DEFERRED') AS deferred_rows
  FROM ext_candidate_outcomes e JOIN rest USING (cycle_id)
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo == S2 stream (single-event) cycles (24 h) per sport key ==
WITH c AS (
  SELECT cycle_id, count(DISTINCT sport_key) AS sk, count(*) AS n
    FROM ext_candidate_outcomes
   WHERE cycle_at > now() - interval '24 hours'
   GROUP BY 1)
SELECT e.sport_key, count(DISTINCT e.cycle_id) AS single_event_cycles,
       count(DISTINCT e.provider_event_id) AS events,
       count(*) FILTER (WHERE e.outcome = 'REFUSED') AS refused
  FROM ext_candidate_outcomes e JOIN c USING (cycle_id)
 WHERE c.sk = 1 AND c.n = 1
 GROUP BY 1
 ORDER BY 2 DESC
 LIMIT 40;

\echo == S3 PinnAPI feed scope and feed heartbeat ==
SELECT key, left(value::text, 1500) AS value
  FROM ingestion_state
 WHERE key IN ('pinnapi_feed_scope', 'pinnapi_feed')
 LIMIT 5;

SELECT key, value->>'at' AS at, left((value->'scope')::text, 200) AS scope,
       left((value->'census')::text, 1500) AS census
  FROM ingestion_state
 WHERE key = 'pinnapi_feed_last'
 LIMIT 1;

\echo == S4 football valuations (24 h): the lane refusals that make the probability unqualified ==
SELECT v.sport_family, v.provider, r.code, count(*) AS rows,
       count(DISTINCT v.us_market_slug) AS markets
  FROM external_valuations v
  CROSS JOIN LATERAL unnest(v.refusals) AS r(code)
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, rows DESC
 LIMIT 120;

\echo == S5 football markets valued (24 h): which league token, how many ==
SELECT split_part(v.us_market_slug, '-', 2) AS league_token, v.provider,
       count(*) AS rows, count(DISTINCT v.us_market_slug) AS markets
  FROM external_valuations v
 WHERE v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
   AND v.decided_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY rows DESC
 LIMIT 40;
