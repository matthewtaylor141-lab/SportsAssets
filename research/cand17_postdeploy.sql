-- Candidate 17 post-deploy readback (read-only): which build the writers run,
-- whether migrations 195-197 are applied, the corroboration rows the reactive
-- path now records, and the small-live / Kalshi tables' state.
\echo '== P0 · migrations 195-197 =='
SELECT version FROM schema_migrations
 WHERE version::text ~ '^(195|196|197)' ORDER BY 1;
\echo '== P1 · writer builds: collector cycle, feed heartbeat, reactive attempts (last 2 h) =='
SELECT key, value->'writer'->>'build' AS cycle_writer_build,
       value->>'writer_pid' AS feed_writer_pid, value->>'state' AS state,
       to_timestamp(coalesce((value->>'at')::float8, (value->>'beat_at')::float8)) AS at
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'pinnapi_feed_last');
SELECT detail->'writer'->>'build' AS writer_build, state, count(*) AS attempts,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM pinnapi_reactive_attempts WHERE created_at > now() - interval '2 hours'
 GROUP BY 1, 2 ORDER BY 5 DESC;
\echo '== P2 · corroboration outcomes =='
SELECT qualified, refusal, count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at
  FROM valuation_corroboration GROUP BY 1, 2 ORDER BY 3 DESC;
\echo '== P3 · latest corroboration rows =='
SELECT c.valuation_id, c.created_at, c.qualified, c.refusal, left(c.why, 160) AS why,
       c.pinnapi_provider, c.pinnapi_outcome_books, round(c.pinnapi_probability::numeric, 4) AS p_ws,
       c.corroborating_provider, c.corroborating_outcome_books, c.corroborating_books,
       round(c.corroboration_age_s::numeric, 2) AS corr_age_s, c.max_age_s, c.outcome, c.market,
       d.decision_id, d.strategy, d.verdict, d.refusal AS decision_refusal
  FROM valuation_corroboration c LEFT JOIN paper_decisions d ON d.valuation_id = c.valuation_id
 ORDER BY c.created_at DESC LIMIT 20;
\echo '== P4 · decision refusals on WS-reached valuations since the deploy =='
SELECT d.strategy, d.verdict, d.refusal, count(*) AS n
  FROM paper_decisions d
 WHERE d.decided_at > '2026-10-03T01:56:55Z'
   AND d.valuation_id IN (
     SELECT jsonb_array_elements_text(coalesce(detail->'valuation_ids', '[]'::jsonb))::bigint
       FROM pinnapi_reactive_attempts WHERE created_at > '2026-10-03T01:56:55Z')
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;
\echo '== P5 · small-live management and Kalshi tables =='
SELECT (SELECT count(*) FROM smalllive_handoffs) AS handoffs,
       (SELECT count(*) FROM smalllive_reviews) AS reviews,
       (SELECT count(*) FROM smalllive_reconciliations) AS reconciliations,
       (SELECT count(*) FROM kalshi_live_intents) AS kalshi_intents,
       (SELECT count(*) FROM kalshi_account_reconciliations) AS kalshi_recons;
SELECT * FROM kalshi_smalllive_control;
\echo '== P6 · execution mirror control =='
SELECT enabled, stopped, scale, rounding, cutover_at, left(account_fingerprint, 12) AS fp,
       (baseline <> '{}'::jsonb) AS has_baseline, max_order_usd, revision, updated_at
  FROM execmirror_control;
SELECT (SELECT count(*) FROM execmirror_orders) AS mirror_orders,
       (SELECT count(*) FROM execmirror_fills) AS mirror_fills;
