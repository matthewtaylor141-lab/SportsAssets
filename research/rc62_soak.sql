-- READ-ONLY. RC6.2 production soak readback. The window is the newest
-- deploy: now() - interval '75 minutes' (hard-coded below; this surface admits
-- no psql variables). Every statement is a SELECT. Sections:
--   S1 running commit per process and loop health (runtime_loop_health,
--      workers_boot, the market-plane boot record, service_heartbeats)
--   S2 source-specific book freshness: Kalshi books by source (WS / REST),
--      the Kalshi WS runtime heartbeat, PMX / Polymarket US freshness by
--      source from the market plane persisted samples and snapshots and from
--      the paper runtime book reads, the market-plane boot record
--   S3 decision cycle: decisions by strategy and verdict, ledger refusals by
--      stage, newest decision per strategy, ENTER_WITHOUT_ORDER (named by the
--      backstop and recomputed directly)
--   S4 management cycle: Xavier reviews, open positions with their latest
--      review, the paper pass record that runs the Xavier steps
--   S5 refusal and reconciliation evidence: Audrey risk recompute, Archer,
--      TRUTH_QUORUM receipts, reconciliation tables, Audrey findings,
--      refusal codes
--   S6 SMALL LIVE = SHADOW, no venue order (PMUS and Kalshi small live)
--   S7 historical PAPER unchanged (all-time fills, and before the window)
--
-- Not covered because nothing persists it in the candidate schema: a stored
-- loop status column (status is derived below from the row with the 3x
-- cadence rule loop_health.classify uses, on this row alone); Kalshi WS
-- storm counters (the runtime keeps none); a persisted record of the held-
-- review scheduler (in-process only).

\echo S1a RUNNING COMMIT per process (all loop rows, and rows written inside the 75 min window)
SELECT process, left(commit_sha, 12) AS commit_sha, count(*) AS loops,
       count(*) FILTER (WHERE updated_at >= now() - interval '75 minutes') AS loops_written_in_window,
       max(last_success_at) AS newest_success, min(last_start_at) AS oldest_start,
       max(last_start_at) AS newest_start
  FROM runtime_loop_health GROUP BY 1, 2 ORDER BY 1, 2;

\echo S1b RELEASE COMMIT from every process record (loop rows in window, workers boot, market plane boot, red team receipts in window)
SELECT 'runtime_loop_health' AS record, process AS who, commit_sha, max(updated_at)::text AS at
  FROM runtime_loop_health WHERE updated_at >= now() - interval '75 minutes'
 GROUP BY 1, 2, 3
UNION ALL
SELECT 'ingestion_state.workers_boot', 'workers', value->>'commit_sha', value->>'at'
  FROM ingestion_state WHERE key = 'workers_boot'
UNION ALL
SELECT 'service_heartbeats.market_plane', coalesce(detail->>'service', 'market_plane'),
       detail->>'commit', beat_at::text
  FROM service_heartbeats WHERE service = 'market_plane'
UNION ALL
SELECT 'red_team_control_receipts', 'api', implementation_sha, max(computed_at)::text
  FROM red_team_control_receipts WHERE computed_at >= now() - interval '75 minutes'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2;

\echo S1c LOOP STATUS COUNTS per process (3x cadence rule on the row; HEALTHY UNHEALTHY STARTING UNAVAILABLE)
WITH s AS (
  SELECT process,
         CASE WHEN last_success_at IS NOT NULL
                   AND now() - last_success_at <= make_interval(secs => 3 * cadence_s) THEN 'HEALTHY'
              WHEN last_success_at IS NOT NULL THEN 'UNHEALTHY'
              WHEN last_error_at IS NOT NULL THEN 'UNHEALTHY'
              WHEN last_start_at IS NULL THEN 'UNAVAILABLE'
              WHEN now() - last_start_at > make_interval(secs => 3 * cadence_s) THEN 'UNHEALTHY'
              ELSE 'STARTING' END AS status
    FROM runtime_loop_health)
SELECT process, status, count(*) AS loops FROM s GROUP BY 1, 2 ORDER BY 1, 2;

\echo S1d EVERY LOOP: commit, cadence, newest success and its age, status, errors in its counters
SELECT process, loop_name, left(commit_sha, 7) AS commit, cadence_s,
       last_success_at, round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s,
       CASE WHEN last_success_at IS NOT NULL
                 AND now() - last_success_at <= make_interval(secs => 3 * cadence_s) THEN 'HEALTHY'
            WHEN last_success_at IS NOT NULL THEN 'UNHEALTHY'
            WHEN last_error_at IS NOT NULL THEN 'UNHEALTHY'
            WHEN last_start_at IS NULL THEN 'UNAVAILABLE'
            WHEN now() - last_start_at > make_interval(secs => 3 * cadence_s) THEN 'UNHEALTHY'
            ELSE 'STARTING' END AS status,
       successes, errors, last_error_at, left(last_error, 100) AS last_error
  FROM runtime_loop_health ORDER BY process, loop_name;

\echo S1e LOOPS whose newest success is older than 2x cadence, or that never succeeded
SELECT process, loop_name, left(commit_sha, 7) AS commit, cadence_s, last_start_at, last_success_at,
       round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s,
       round((extract(epoch FROM now() - last_success_at) / cadence_s)::numeric, 1) AS cadences_late,
       last_error_at, left(last_error, 100) AS last_error
  FROM runtime_loop_health
 WHERE last_success_at IS NULL OR now() - last_success_at > make_interval(secs => 2 * cadence_s)
 ORDER BY process, loop_name;

\echo S1f SERVICE HEARTBEATS (every service, beat age, commit when the beat carries one)
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->>'commit' AS commit
  FROM service_heartbeats ORDER BY service;

\echo S2a KALSHI BOOKS CURRENT by source (WS or REST basis), tracked now or not, readable, with observed age percentiles
WITH w AS (
  SELECT DISTINCT t FROM (
    SELECT unnest(team_tickers) AS t FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED'
       AND start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours'
    UNION ALL
    SELECT tie_ticker FROM kalshi_fixtures_current
     WHERE mapping_status = 'ESTABLISHED' AND tie_ticker IS NOT NULL
       AND start_at BETWEEN now() - interval '4 hours' AND now() + interval '36 hours') x),
b AS (
  SELECT CASE WHEN book_basis LIKE 'KALSHI_WS%' THEN 'WS'
              WHEN book_basis = 'KALSHI_DOCUMENTED_RECIPROCAL_BOOK' THEN 'REST'
              ELSE coalesce(book_basis, '(none)') END AS source,
         (ticker IN (SELECT t FROM w)) AS tracked_now, readable,
         extract(epoch FROM now() - observed_at) AS obs_age_s,
         extract(epoch FROM now() - updated_at) AS upd_age_s
    FROM kalshi_books_current)
SELECT source, tracked_now, readable, count(*) AS books,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY obs_age_s)::numeric, 1) AS obs_age_p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY obs_age_s)::numeric, 1) AS obs_age_p90_s,
       round(max(obs_age_s)::numeric, 1) AS obs_age_max_s,
       count(*) FILTER (WHERE obs_age_s <= 60) AS observed_last_60s,
       count(*) FILTER (WHERE obs_age_s <= 300) AS observed_last_300s,
       count(*) FILTER (WHERE upd_age_s <= 75 * 60) AS rewritten_in_window
  FROM b GROUP BY 1, 2, 3 ORDER BY 1, 2 DESC, 3 DESC;

\echo S2b KALSHI WS RUNTIME heartbeat (service kalshi_ws_market_data): connections, resubscribes, gaps, errors, current books
SELECT status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->>'state' AS state, detail->>'why' AS why, detail->>'version' AS version,
       detail->'ws'->>'connected' AS connected, detail->>'connections' AS connections,
       detail->>'resubscribes' AS resubscribes, detail->>'subscribed_markets' AS subscribed_markets,
       detail->'ws'->>'markets' AS books_held, detail->'ws'->>'current' AS books_current,
       detail->'ws'->'by_state' AS by_state
  FROM service_heartbeats WHERE service = 'kalshi_ws_market_data';
SELECT detail->'ws'->>'snapshots' AS snapshots, detail->'ws'->>'deltas' AS deltas,
       detail->'ws'->>'gaps' AS gaps, detail->'ws'->>'disconnects' AS disconnects,
       detail->'ws'->>'errors' AS errors, detail->'ws'->>'ignored_after_gap' AS ignored_after_gap,
       detail->'ws'->>'snapshots_out_of_sequence' AS snapshots_out_of_sequence,
       detail->'ws'->>'ignored_dead_sid' AS ignored_dead_sid,
       detail->'untracked_dropped' AS untracked_dropped,
       detail->'current_book_update_age_s' AS current_book_update_age_s,
       detail->'freshness'->>'numerator' AS fresh_num, detail->'freshness'->>'denominator' AS fresh_den,
       detail->'freshness'->>'rate' AS fresh_rate, left(detail->>'last_error', 160) AS last_error,
       detail->'account_limits'->>'status' AS limits_status,
       detail->'account_limits'->>'usage_tier' AS usage_tier,
       detail->'key'->>'type' AS key_type, detail->>'authority' AS authority
  FROM service_heartbeats WHERE service = 'kalshi_ws_market_data';

\echo S2c KALSHI REST loop heartbeat (kalshi_market_data): book sources and freshness, plus account limits receipts in the window
SELECT status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->'book_sources' AS book_sources, detail->'freshness' AS freshness,
       detail->>'tracked_markets' AS tracked_markets, detail->>'error' AS error
  FROM service_heartbeats WHERE service = 'kalshi_market_data';
SELECT status, usage_tier, count(*) AS receipts, max(as_of) AS newest
  FROM kalshi_account_limits_receipts WHERE as_of >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 4 DESC;

\echo S2d MARKET PLANE BOOT record (service market_plane): commit, service, runtime, guard mode, refusal, process lock
SELECT status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->>'commit' AS commit, detail->>'service' AS service, detail->>'runtime' AS runtime,
       to_timestamp((detail->>'started_at')::float8) AS started_at,
       detail->'guard'->>'mode' AS guard_mode, detail->'guard'->>'refused' AS guard_refused,
       detail->'guard'->>'process_locked' AS process_locked,
       detail->'guard'->'forbidden_env_present' AS forbidden_env_present,
       detail->'guard'->'order_modules_already_loaded' AS order_modules_already_loaded,
       detail->'guard'->>'authority' AS authority
  FROM service_heartbeats WHERE service = 'market_plane';

\echo S2e UNIVERSAL MARKET PLANE pass heartbeat: arming, subscription, streams, fresh count, last freshness sample
SELECT status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s,
       detail->'arming'->>'why' AS arming_why, detail->>'subscription_mode' AS subscription_mode,
       detail->>'market_data_streams' AS streams, detail->>'fresh' AS fresh_symbols,
       detail->>'runtime' AS runtime, detail->'freshness_window' AS last_sample,
       detail->'freshness_task' AS freshness_task, detail->'memory'->>'rss_mb' AS rss_mb
  FROM service_heartbeats WHERE service = 'universal_market_plane';

\echo S2f PMUS FRESHNESS BY SOURCE over the window: frozen-membership samples (FRESHNESS_SAMPLE), member-samples per source code
WITH k AS (
  SELECT 'fsample:' || ((floor(extract(epoch FROM now()) / 60))::bigint - g) AS event_key
    FROM generate_series(0, 75) g),
s AS (
  SELECT e.payload FROM market_plane_events e JOIN k USING (event_key)
   WHERE e.kind = 'FRESHNESS_SAMPLE'),
tot AS (SELECT count(*) AS samples, sum((payload->>'n')::int) AS member_samples FROM s)
SELECT c.key AS source_code, sum(c.value::int) AS member_samples, max(tot.samples) AS samples,
       round(sum(c.value::int)::numeric / nullif(max(tot.member_samples), 0), 4) AS share
  FROM s CROSS JOIN LATERAL jsonb_each_text(s.payload->'counts') c CROSS JOIN tot
 GROUP BY 1 ORDER BY 2 DESC;

\echo S2g PMUS FRESHNESS samples, newest 6: current share, counts by source, receipt age (p50 p90 max)
WITH k AS (
  SELECT 'fsample:' || ((floor(extract(epoch FROM now()) / 60))::bigint - g) AS event_key
    FROM generate_series(0, 75) g)
SELECT to_timestamp((e.payload->>'verified_at')::float8) AS verified_at,
       (e.payload->>'n')::int AS members, e.payload->'counts' AS counts,
       e.payload->'times'->'receipt_age_s' AS receipt_age_s,
       e.payload->'stream'->>'connected' AS stream_connected,
       e.payload->'joined'->>'n' AS joined_since_freeze
  FROM market_plane_events e JOIN k USING (event_key)
 WHERE e.kind = 'FRESHNESS_SAMPLE'
 ORDER BY e.at DESC LIMIT 6;

\echo S2h PMUS PRIORITY UNIVERSE from market plane SNAPSHOT events in the window: rate and current by source (stream, snapshot read, REST)
WITH k AS (
  SELECT 'snapshot:' || ((floor(extract(epoch FROM now()) / 60))::bigint - g) AS event_key
    FROM generate_series(0, 75) g),
s AS (
  SELECT e.at, e.payload->'freshness'->'priority_universe' AS pu,
         e.payload->'freshness'->'total_universe' AS tu
    FROM market_plane_events e JOIN k USING (event_key)
   WHERE e.kind = 'SNAPSHOT')
SELECT count(*) AS snapshots, min(at) AS first_at, max(at) AS last_at,
       min((pu->>'rate')::float8) AS priority_rate_min,
       round(avg((pu->>'rate')::float8)::numeric, 4) AS priority_rate_avg,
       max((pu->>'rate')::float8) AS priority_rate_max,
       min((tu->>'rate')::float8) AS total_rate_min, max((tu->>'rate')::float8) AS total_rate_max
  FROM s;
WITH k AS (
  SELECT 'snapshot:' || ((floor(extract(epoch FROM now()) / 60))::bigint - g) AS event_key
    FROM generate_series(0, 75) g)
SELECT e.at, pu->>'denominator' AS den, pu->>'current_pmx_stream' AS pmx_stream,
       pu->>'current_pmx_snapshot_refresh' AS pmx_snapshot_read,
       pu->>'current_rest_fallback' AS rest_fallback,
       pu->'current_rest_fallback_by_origin' AS rest_by_origin,
       pu->'census'->'current_via_refresh_by_origin' AS plane_read_by_origin,
       pu->>'not_current' AS not_current, pu->>'external_unavailable' AS external,
       pu->>'rate' AS rate, pu->>'verified_age_s' AS verified_age_s
  FROM market_plane_events e JOIN k USING (event_key)
       CROSS JOIN LATERAL (SELECT e.payload->'freshness'->'priority_universe' AS pu) x
 WHERE e.kind = 'SNAPSHOT'
 ORDER BY e.at DESC LIMIT 4;

\echo S2i PMUS BOOK READS by the paper runtime: newest read per active priority member (6 h), by source, with age percentiles
WITH m AS (
  SELECT contract_id FROM market_plane_registry
   WHERE active AND venue = 'POLYMARKET_US' AND priority <= 10),
r AS (
  SELECT m.contract_id, o.source, o.error, extract(epoch FROM now() - o.observed_at) AS age_s
    FROM m LEFT JOIN LATERAL (
          SELECT source, error, observed_at FROM paper_book_observations p
           WHERE p.us_market_slug = m.contract_id AND p.observed_at > now() - interval '6 hours'
           ORDER BY p.observed_at DESC LIMIT 1) o ON true)
SELECT coalesce(source, '(no read in 6 h)') AS source, (error IS NULL AND source IS NOT NULL) AS readable,
       count(*) AS members,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY age_s)::numeric, 1) AS age_p50_s,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY age_s)::numeric, 1) AS age_p90_s,
       round(max(age_s)::numeric, 1) AS age_max_s,
       count(*) FILTER (WHERE age_s <= 300) AS within_300s
  FROM r GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;

\echo S3a DECISIONS in the window by strategy and verdict, with an order and with a ledger refusal
SELECT d.strategy, d.verdict, count(*) AS decisions, max(d.decided_at) AS newest,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)) AS with_order,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_entry_refusal_census c
                                       WHERE c.decision_id = d.decision_id AND c.account_id = d.account_id
                                         AND c.refused_at >= now() - interval '80 minutes')) AS with_ledger_refusal
  FROM paper_decisions d
 WHERE d.decided_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 1, 3 DESC;

\echo S3b LEDGER REFUSAL CENSUS in the window by strategy, stage and code (top 25)
SELECT strategy, stage, refusal, count(*) AS n, count(DISTINCT decision_id) AS decisions,
       max(refused_at) AS newest
  FROM paper_entry_refusal_census WHERE refused_at >= now() - interval '75 minutes'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;

\echo S3c EVERY ACCOUNT AND STRATEGY: lifecycle state, newest decision ever, decisions and ENTERs in the window
WITH st AS (
  SELECT account_id, strategy FROM paper_strategy_lifecycle_current_v
  UNION
  SELECT DISTINCT account_id, strategy FROM paper_decisions WHERE decided_at >= now() - interval '75 minutes')
SELECT st.account_id, st.strategy, l.state AS lifecycle_state, l.rule_id, l.recorded_at AS lifecycle_at,
       (SELECT max(d.decided_at) FROM paper_decisions d
         WHERE d.strategy = st.strategy AND d.account_id = st.account_id) AS newest_decision,
       (SELECT count(*) FROM paper_decisions d WHERE d.strategy = st.strategy AND d.account_id = st.account_id
           AND d.decided_at >= now() - interval '75 minutes') AS decisions_in_window,
       (SELECT count(*) FROM paper_decisions d WHERE d.strategy = st.strategy AND d.account_id = st.account_id
           AND d.verdict = 'ENTER' AND d.decided_at >= now() - interval '75 minutes') AS enter_in_window
  FROM st LEFT JOIN paper_strategy_lifecycle_current_v l
         ON l.strategy = st.strategy AND l.account_id = st.account_id
 ORDER BY 6 DESC NULLS LAST;

\echo S3d DECISIONS per 10 minutes in the window (the cycle keeps running)
SELECT date_trunc('hour', decided_at) + floor(extract(minute FROM decided_at) / 10) * interval '10 minutes' AS bucket,
       count(*) AS decisions, count(*) FILTER (WHERE verdict = 'ENTER') AS enter,
       count(DISTINCT strategy) AS strategies
  FROM paper_decisions WHERE decided_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 1;

\echo S3e ENTER_WITHOUT_ORDER recomputed: ENTERs in the window by strategy, order, ledger refusal, risk refusal, backstop finding
WITH e AS (
  SELECT d.decision_id, d.account_id, d.strategy, d.decided_at,
         EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id) AS has_order,
         EXISTS (SELECT 1 FROM paper_entry_refusal_census c WHERE c.decision_id = d.decision_id AND c.account_id = d.account_id
                    AND c.refused_at >= now() - interval '80 minutes') AS has_census_refusal,
         EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.account_id = d.account_id
                    AND f.kind = 'PAPER_RISK_REFUSED_THE_ORDER' AND f.subject = d.decision_id) AS has_risk_refusal,
         EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.account_id = d.account_id
                    AND f.kind = 'ENTER_WITHOUT_ORDER' AND f.subject = d.decision_id) AS named_by_backstop
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.decided_at >= now() - interval '75 minutes')
SELECT account_id, strategy, count(*) AS enter,
       count(*) FILTER (WHERE has_order) AS with_order,
       count(*) FILTER (WHERE has_census_refusal) AS with_ledger_refusal,
       count(*) FILTER (WHERE has_risk_refusal) AS with_risk_refusal,
       count(*) FILTER (WHERE NOT has_order AND NOT has_census_refusal AND NOT has_risk_refusal
                          AND decided_at <= now() - interval '60 seconds') AS enter_without_order_over_60s,
       count(*) FILTER (WHERE NOT has_order AND NOT has_census_refusal AND NOT has_risk_refusal
                          AND decided_at > now() - interval '60 seconds') AS still_within_60s,
       count(*) FILTER (WHERE named_by_backstop) AS named_by_backstop
  FROM e GROUP BY 1, 2 ORDER BY 3 DESC;

\echo S3f ENTERs in the window older than 60 s with neither an order nor any refusal (up to 20)
SELECT d.strategy, d.decision_id, d.decided_at, round(extract(epoch FROM now() - d.decided_at)::numeric, 1) AS age_s,
       d.us_market_slug, d.holding_side, d.pinnacle->>'decided_via' AS via,
       (SELECT i.actual_state FROM execution_intents i WHERE i.decision_id = d.decision_id) AS intent_state,
       EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.account_id = d.account_id
                  AND f.kind = 'ENTER_WITHOUT_ORDER' AND f.subject = d.decision_id) AS named_by_backstop
  FROM paper_decisions d
 WHERE d.verdict = 'ENTER' AND d.decided_at >= now() - interval '75 minutes'
   AND d.decided_at <= now() - interval '60 seconds'
   AND NOT EXISTS (SELECT 1 FROM paper_orders o WHERE o.decision_id = d.decision_id)
   AND NOT EXISTS (SELECT 1 FROM paper_entry_refusal_census c WHERE c.decision_id = d.decision_id AND c.account_id = d.account_id
                      AND c.refused_at >= now() - interval '80 minutes')
   AND NOT EXISTS (SELECT 1 FROM paper_audrey_findings f WHERE f.account_id = d.account_id
                      AND f.kind = 'PAPER_RISK_REFUSED_THE_ORDER' AND f.subject = d.decision_id)
 ORDER BY d.decided_at DESC LIMIT 20;

\echo S3g ENTER_WITHOUT_ORDER findings written by the backstop in the window, by strategy
SELECT f.detail->>'strategy' AS strategy, count(*) AS findings, min(f.found_at) AS first_found,
       max(f.found_at) AS newest_found, max((f.detail->>'age_at_detection_s')::float8) AS max_age_at_detection_s
  FROM paper_audrey_findings f
 WHERE f.kind = 'ENTER_WITHOUT_ORDER' AND f.found_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC;

\echo S3h EVALUATION ATTEMPTS in the window by path and outcome (top 20)
SELECT via, outcome, count(*) AS attempts, count(decision_id) AS with_decision, max(at) AS newest,
       round(avg(elapsed_s)::numeric, 3) AS avg_elapsed_s
  FROM paper_evaluation_attempts WHERE at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo S4a XAVIER REVIEWS in the window by trigger and recommendation or refusal
SELECT trigger, coalesce(recommendation, '-') AS recommendation, coalesce(refusal, '-') AS refusal,
       count(*) AS reviews, count(DISTINCT group_id) AS groups, max(reviewed_at) AS newest
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '75 minutes'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25;
SELECT count(*) AS reviews_in_window, count(DISTINCT group_id) AS groups_in_window, max(reviewed_at) AS newest
  FROM paper_xavier_reviews WHERE reviewed_at >= now() - interval '75 minutes';

\echo S4b OPEN PAPER POSITIONS (bought - sold - latest settlement) with the latest Xavier review, any age
WITH open AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0) AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) AS sold
              FROM paper_fills GROUP BY 1, 2, 3, 4) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':' || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9)
SELECT o.us_market_slug, o.holding_side, o.open_qty, r.reviewed_at AS xavier_reviewed_at,
       round(extract(epoch FROM now() - r.reviewed_at)::numeric, 1) AS review_age_s,
       (r.reviewed_at >= now() - interval '75 minutes') AS reviewed_in_window,
       r.trigger, r.recommendation, r.refusal,
       r.measure->>'evidence_state' AS evidence_state,
       (r.selection->'management_packet'->'gate'->>'complete') AS packet_complete,
       r.selection->'management_packet'->'gate'->'missing' AS packet_missing
  FROM open o
  LEFT JOIN LATERAL (SELECT x.* FROM paper_xavier_reviews x WHERE x.group_id = o.group_id
                      ORDER BY x.reviewed_at DESC LIMIT 1) r ON true
 ORDER BY o.us_market_slug;

\echo S4c PAPER PASS record (runs the Derek, backstop and Xavier steps): last attempt and its step digests
SELECT CASE WHEN value->>'at' ~ '^[0-9]+([.][0-9]+)?$' THEN to_timestamp((value->>'at')::float8)::text
            ELSE value->>'at' END AS at,
       to_timestamp((value->>'written_at')::float8) AS written_at,
       round((extract(epoch FROM now()) - (value->>'written_at')::float8)::numeric, 1) AS age_s,
       value->>'ran' AS ran, value->>'trigger' AS trigger, value->>'refusal' AS refusal,
       left(value->>'why', 200) AS why, value->>'elapsed_s' AS elapsed_s, value->>'errors' AS errors,
       value->>'decisions_recorded' AS decisions_recorded, value->>'orders_submitted' AS orders_submitted,
       value->>'reviews' AS reviews, value->>'heartbeat_truncated' AS truncated
  FROM ingestion_state WHERE key = 'paper_session_last_pass';
SELECT s.key AS step, s.value AS digest
  FROM ingestion_state i CROSS JOIN LATERAL jsonb_each(i.value->'steps') s
 WHERE i.key = 'paper_session_last_pass'
   AND s.key IN ('derek', 'enter_backstop', 'settle', 'handoff', 'xavier', 'xavier_value_add',
                 'xavier_work_queue', 'audrey', 'simulate')
 ORDER BY 1;

\echo S4d PAPER SESSION health and the servicing / entry loops that schedule the pass
SELECT session_id, heartbeat_at, round(extract(epoch FROM now() - heartbeat_at)::numeric, 1) AS age_s,
       passes, errors, mutation_attempts, left(last_error, 120) AS last_error
  FROM paper_session_health ORDER BY heartbeat_at DESC NULLS LAST LIMIT 3;
SELECT key, CASE WHEN coalesce(value->>'at', value->>'beat_at') ~ '^[0-9]+([.][0-9]+)?$'
                 THEN to_timestamp(coalesce(value->>'at', value->>'beat_at')::float8)::text
                 ELSE coalesce(value->>'at', value->>'beat_at') END AS at,
       value->>'state' AS state
  FROM ingestion_state
 WHERE key IN ('ext_pinnacle_last_servicing', 'ext_pinnacle_last_cycle', 'xavier_daily_review_last')
 ORDER BY 1;
SELECT loop_name, process, left(commit_sha, 7) AS commit, cadence_s, last_success_at,
       round(extract(epoch FROM now() - last_success_at)::numeric, 1) AS success_age_s,
       successes, errors, last_error_at
  FROM runtime_loop_health
 WHERE loop_name IN ('ext_pinnacle.entry_cycle', 'ext_pinnacle.servicing', 'pinnapi_feed.heartbeat',
                     'intel.runner', 'agents.archer_runner', 'redteam.runner')
    OR loop_name LIKE 'pinnapi_held.%'
 ORDER BY 1, 2;

\echo S4e XAVIER CURRENT REVIEW pointers advanced in the window
SELECT position_kind, review_table, coalesce(recommendation_state, '-') AS recommendation_state,
       count(*) AS groups, max(advanced_at) AS newest_advance
  FROM xavier_current_review WHERE advanced_at >= now() - interval '75 minutes'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC;

\echo S5a AUDREY INDEPENDENT RISK RECOMPUTE in the window: agreement by component (book and metric)
SELECT count(DISTINCT run_id) AS runs, count(*) AS checks,
       count(*) FILTER (WHERE agrees) AS agrees, count(*) FILTER (WHERE NOT agrees) AS disagrees,
       count(*) FILTER (WHERE agrees IS NULL) AS not_comparable, max(computed_at) AS newest
  FROM intel_audrey_risk_checks WHERE computed_at >= now() - interval '75 minutes';
SELECT book, metric, count(*) AS checks,
       count(*) FILTER (WHERE agrees) AS agrees, count(*) FILTER (WHERE NOT agrees) AS disagrees,
       count(*) FILTER (WHERE agrees IS NULL) AS not_comparable,
       max(abs_diff) AS max_abs_diff, max(tolerance) AS tolerance, max(computed_at) AS newest
  FROM intel_audrey_risk_checks WHERE computed_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 5 DESC, 1, 2 LIMIT 40;

\echo S5b ARCHER in the window: execution estimates by recommendation, outcomes by source, runner heartbeat, agent findings by proposer
SELECT recommendation, count(*) AS estimates, max(estimated_at) AS newest
  FROM eddie_execution_estimates WHERE estimated_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT source, count(*) AS outcomes, max(measured_at) AS newest
  FROM eddie_execution_outcomes WHERE measured_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT service, status, beat_at, round(extract(epoch FROM now() - beat_at)::numeric, 1) AS age_s
  FROM service_heartbeats WHERE service IN ('agent_archer', 'red_team_readiness') ORDER BY 1;
SELECT proposer, coalesce(stage, '-') AS stage, count(*) AS findings, max(updated_at) AS newest
  FROM agent_findings WHERE updated_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;

\echo S5c TRUTH QUORUM and the other red team controls in the window (receipts by control and status), readiness receipts
SELECT control, status, count(*) AS receipts, max(computed_at) AS newest,
       (array_agg(blockers ORDER BY computed_at DESC))[1] AS newest_blockers
  FROM red_team_control_receipts WHERE computed_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 1, 2;
SELECT status, count(*) AS receipts, max(computed_at) AS newest,
       (array_agg(blockers ORDER BY computed_at DESC))[1] AS newest_blockers
  FROM red_team_readiness_receipts WHERE computed_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 1;

\echo S5d RECONCILIATION records in the window (small live, funded accounts, Kalshi account, paper ledger report)
SELECT 'smalllive_reconciliations' AS record, status AS outcome, count(*) AS n, max(reconciled_at) AS newest
  FROM smalllive_reconciliations WHERE reconciled_at >= now() - interval '75 minutes' GROUP BY 1, 2
UNION ALL
SELECT 'bettor_account_reconciliation_reports', venue || ' blocking=' || (blocking_count > 0)::text,
       count(*), max(recorded_at)
  FROM bettor_account_reconciliation_reports WHERE recorded_at >= now() - interval '75 minutes' GROUP BY 1, 2
UNION ALL
SELECT 'kalshi_account_reconciliations', verdict || ' complete=' || complete::text, count(*), max(at)
  FROM kalshi_account_reconciliations WHERE at >= now() - interval '75 minutes' GROUP BY 1, 2
UNION ALL
SELECT 'paper_audrey_reports', 'reconciles=' || reconciles::text || ' final=' || final::text, count(*), max(generated_at)
  FROM paper_audrey_reports WHERE generated_at >= now() - interval '75 minutes' GROUP BY 1, 2
 ORDER BY 1, 2;

\echo S5e AUDREY PAPER FINDINGS in the window by kind and severity (PAPER_RISK_REFUSED_THE_ORDER, ENTER_WITHOUT_ORDER and the rest)
SELECT kind, severity, count(*) AS findings, count(DISTINCT subject) AS subjects, max(found_at) AS newest
  FROM paper_audrey_findings WHERE found_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 25;

\echo S5f REFUSALS by code in the window: ledger census (all stages) and decision refusals (top 20 each)
SELECT refusal, count(*) AS n, count(DISTINCT strategy) AS strategies, string_agg(DISTINCT stage, ',') AS stages,
       max(refused_at) AS newest
  FROM paper_entry_refusal_census WHERE refused_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;
SELECT coalesce(refusal, '(ENTER)') AS decision_refusal, count(*) AS n, max(decided_at) AS newest
  FROM paper_decisions WHERE decided_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo S6 SMALL LIVE = SHADOW: control, execution intents in the window, venue order events (window and all time), Kalshi small live
SELECT id, mode, halted, halt_reason, updated_at FROM small_live_control ORDER BY updated_at DESC NULLS LAST LIMIT 3;
SELECT actual_state, coalesce(actual_refusal, '-') AS actual_refusal, count(*) AS intents,
       count(*) FILTER (WHERE actual_mirror_id IS NOT NULL) AS with_mirror_id, max(created_at) AS newest
  FROM execution_intents WHERE created_at >= now() - interval '75 minutes'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15;
SELECT count(*) FILTER (WHERE observed_at >= now() - interval '75 minutes') AS small_live_order_events_window,
       count(*) AS small_live_order_events_all_time, max(observed_at) AS newest_ever
  FROM small_live_order_events;
SELECT id, enabled, stopped, kalshi_env, revision, updated_at FROM kalshi_smalllive_control ORDER BY id;
SELECT state, count(*) AS intents, count(venue_order_id) AS with_venue_order_id, max(created_at) AS newest
  FROM kalshi_live_intents WHERE created_at >= now() - interval '75 minutes'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT (SELECT count(*) FROM kalshi_live_fills WHERE observed_at >= now() - interval '75 minutes') AS kalshi_live_fills_window,
       (SELECT count(*) FROM kalshi_live_events WHERE at >= now() - interval '75 minutes') AS kalshi_live_events_window,
       (SELECT count(*) FROM kalshi_live_intents WHERE venue_order_id IS NOT NULL) AS kalshi_intents_with_venue_order_all_time;

\echo S7 HISTORICAL PAPER (immutable): all-time fills and cash flows, and the same before the window (compare with the pre-deploy value)
SELECT 'all_time' AS scope, count(*) AS fills,
       round(sum(CASE WHEN direction = 'BUY' THEN gross_usd ELSE -gross_usd END)::numeric, 2) AS net_bought_usd,
       round(sum(fee_usd)::numeric, 2) AS fees_usd, min(filled_at) AS first_fill, max(filled_at) AS last_fill
  FROM paper_fills
UNION ALL
SELECT 'before_window', count(*),
       round(sum(CASE WHEN direction = 'BUY' THEN gross_usd ELSE -gross_usd END)::numeric, 2),
       round(sum(fee_usd)::numeric, 2), min(filled_at), max(filled_at)
  FROM paper_fills WHERE filled_at < now() - interval '75 minutes';
