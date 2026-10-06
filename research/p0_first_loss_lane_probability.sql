-- READ-ONLY. P0 first-loss plumbing, part 3: WHY THE LANE DID NOT QUALIFY
-- THE PINNACLE PROBABILITY (PINNACLE_PROBABILITY_NOT_QUALIFIED_BY_THE_LANE).
--
-- Part 1 (research-sql run 37411912022) showed the lane codes behind the
-- wrapper on the census sample events are freshness codes:
-- FEED_QUOTE_OLDER_THAN_LIMIT (a PinnAPI quote, NBA spread 22:58Z, NPB
-- 15:39Z with VENUE_TIMEOUT) and QUOTE_STALE (a the-odds-api quote). This
-- splits the WS quote's age at the decision into its clocks -- change ->
-- frame receipt -> queued -> evaluation started -> decided -- so the time
-- is attributed to the source, the reactive queue or our evaluation.
--
-- No writes. Every statement is a SELECT.

\echo L1 the reference_input keys on one recent stale PinnAPI valuation
SELECT jsonb_object_keys(settlement_comparison->'reference_input') AS key
  FROM (SELECT settlement_comparison FROM external_valuations
         WHERE decided_at >= now() - interval '25 hours'
           AND provider = 'pinnapi.com/raw-websocket'
           AND 'FEED_QUOTE_OLDER_THAN_LIMIT' = ANY (refusals)
         ORDER BY decided_at DESC LIMIT 1) s;

\echo L2 stale PinnAPI valuations: the clocks, per family and market kind
WITH v AS (
    SELECT ev.sport_family,
           CASE WHEN ev.us_market_slug ~ '^(aec|atc)-' THEN 'MONEYLINE'
                ELSE 'LINE' END AS kind,
           ri,
           (ri->>'change_ms')::float8 / 1000.0          AS change_s,
           (ri->>'received_ms')::float8 / 1000.0        AS frame_rx_s,
           (ri->'evaluation_trigger'->>'received_at')::float8 AS trig_rx_s,
           (ri->'evaluation_trigger'->>'queued_at')::float8   AS queued_s,
           (ri->'evaluation_trigger'->>'evaluation_started_at')::float8 AS started_s,
           (ri->'decision_check'->'provenance'->>'evaluated_ms')::float8 / 1000.0 AS checked_s,
           (ri->'decision_check'->'provenance'->>'quote_age_s')::float8 AS age_at_check_s,
           extract(epoch FROM ev.decided_at) AS decided_s
      FROM external_valuations ev,
           LATERAL (SELECT ev.settlement_comparison->'reference_input' AS ri) x
     WHERE ev.decided_at >= now() - interval '25 hours'
       AND ev.provider = 'pinnapi.com/raw-websocket'
       AND 'FEED_QUOTE_OLDER_THAN_LIMIT' = ANY (ev.refusals))
SELECT sport_family, kind, count(*) AS n,
       count(*) FILTER (WHERE queued_s IS NOT NULL) AS reactive,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY frame_rx_s - change_s)::numeric, 1) AS change_to_rx_p50,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY queued_s - change_s)::numeric, 1) AS change_to_queued_p50,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY started_s - queued_s)::numeric, 1) AS queue_wait_p50,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY started_s - queued_s)::numeric, 1) AS queue_wait_p90,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY decided_s - started_s)::numeric, 1) AS eval_p50,
       round(percentile_disc(0.9) WITHIN GROUP (ORDER BY decided_s - started_s)::numeric, 1) AS eval_p90,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY decided_s - change_s)::numeric, 1) AS age_at_decision_p50,
       round(min(started_s - change_s)::numeric, 1) AS age_at_start_min,
       round(percentile_disc(0.5) WITHIN GROUP (ORDER BY started_s - change_s)::numeric, 1) AS age_at_start_p50
  FROM v GROUP BY 1, 2 ORDER BY n DESC;

\echo L3 the two PinnAPI sample events: every valuation clock
SELECT ev.id, ev.decided_at, ev.event_key, ev.us_market_slug,
       ev.settlement_comparison->'reference_input'->>'change_ms' AS change_ms,
       ev.settlement_comparison->'reference_input'->>'received_ms' AS received_ms,
       ev.settlement_comparison->'reference_input'->'evaluation_trigger' AS trigger,
       ev.settlement_comparison->'reference_input'->'decision_check'->>'reason' AS check_reason,
       ev.settlement_comparison->'reference_input'->'decision_check'->'provenance'->>'quote_age_s' AS age_at_check,
       ev.settlement_comparison->'reference_input'->'decision_check'->'provenance'->>'evaluated_ms' AS checked_ms,
       ev.refusals[1:4] AS refusals
  FROM external_valuations ev
 WHERE ev.decided_at >= now() - interval '30 hours'
   AND ev.event_key IN ('pinnapi:1637471033', 'pinnapi:1637608659')
 ORDER BY ev.decided_at;

\echo L4 reactive attempts for those two fixtures (queue, deadline, outcome)
SELECT attempt_id, event_id, created_at, updated_at, state,
       detail->>'reason' AS reason, detail->>'queued_at' AS queued_at,
       detail->>'evaluation_started_at' AS started_at,
       detail->>'received_at' AS received_at, detail->>'held' AS held
  FROM pinnapi_reactive_attempts
 WHERE created_at >= now() - interval '30 hours'
   AND event_id IN ('1637471033', '1637608659')
 ORDER BY created_at LIMIT 40;
