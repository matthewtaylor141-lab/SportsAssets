-- READ-ONLY. RC6.3c settlement_exception_risk (SER) 2.0 s component bound:
-- the production lens, independent of the CI test pollution.
--   * row counts and physical size of paper_settlements / external_valuations
--   * the indexes on external_valuations (is us_market_slug indexed?)
--   * the exact three statements of settlement_exception_risk.measure on
--     ada9270c52ad5e1066e4fb701c4ec4197a23c07a with literal parameters
--     (EXPERIMENT_ID = 'EXT_PINNACLE_DEVIG_V1_SHADOW', MAX_MARKETS = 20000,
--     DIVERGENCE_WINDOW_DAYS = 45), each under EXPLAIN (ANALYZE, BUFFERS)
--     and bracketed by clock_timestamp() for the wall time on the replica
--   * the distribution of the component's status / why on
--     canonical_decision_intents.evidence over 24 h, 7 d and by day (21 d)
-- Every statement is a SELECT or an EXPLAIN of a SELECT.

\echo == F0 context
SELECT now() AS run_at, current_database() AS db, pg_is_in_recovery() AS replica,
       current_setting('server_version') AS pg, current_setting('jit') AS jit,
       current_setting('jit_above_cost') AS jit_above_cost,
       current_setting('statement_timeout') AS statement_timeout,
       current_setting('shared_buffers') AS shared_buffers,
       current_setting('work_mem') AS work_mem;

\echo == P1 row counts and physical size
SELECT 'paper_settlements' AS tbl, count(*) AS rows_total,
       count(DISTINCT position_key) AS distinct_position_keys,
       pg_relation_size('paper_settlements') / 8192 AS heap_pages,
       pg_size_pretty(pg_total_relation_size('paper_settlements')) AS total_size
  FROM paper_settlements
UNION ALL
SELECT 'external_valuations', count(*), count(DISTINCT us_market_slug),
       pg_relation_size('external_valuations') / 8192,
       pg_size_pretty(pg_total_relation_size('external_valuations'))
  FROM external_valuations;

\echo == P1b catalog statistics (reltuples / relpages as replayed from the primary) and replica-side tuple counters
SELECT c.relname, c.reltuples, c.relpages,
       s.n_live_tup, s.n_dead_tup, s.last_autovacuum, s.last_autoanalyze, s.autovacuum_count
  FROM pg_class c
  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
 WHERE c.relname IN ('paper_settlements', 'external_valuations')
 ORDER BY 1;

\echo == P2 indexes on the two tables
SELECT tablename, indexname, indexdef
  FROM pg_indexes
 WHERE tablename IN ('external_valuations', 'paper_settlements')
 ORDER BY 1, 2;

\echo == P3 latest-version settlement rows, and how many have NO valuation row on their slug (each of those is one full scan of external_valuations in PAPER_SQL)
WITH s AS (SELECT DISTINCT ON (position_key) position_key, us_market_slug
             FROM paper_settlements ORDER BY position_key, version DESC)
SELECT count(*) AS latest_rows,
       count(DISTINCT us_market_slug) AS distinct_slugs,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM external_valuations x
                                            WHERE x.us_market_slug = s.us_market_slug))
           AS rows_without_valuation,
       min(position_key) AS sample_position_key
  FROM s;

\echo == P4 PAPER_SQL wall time bracket (replica)
SELECT clock_timestamp() AS t_before_paper_sql;
SELECT count(*) AS paper_rows FROM (
    WITH s AS (
      SELECT DISTINCT ON (position_key) position_key, us_market_slug,
             outcome, payout_per_contract, holding_side,
             evidence->>'venue_long_price' AS venue_long_price
        FROM paper_settlements ORDER BY position_key, version DESC)
    SELECT s.*, v.sport_family, v.market, v.fixture
      FROM s LEFT JOIN LATERAL (
        SELECT sport_family, market,
               coalesce(event_key, us_market_slug) AS fixture
          FROM external_valuations x
         WHERE x.us_market_slug = s.us_market_slug
         ORDER BY x.id DESC LIMIT 1) v ON true
     LIMIT 20000
) q;
SELECT clock_timestamp() AS t_after_paper_sql;

\echo == P5 EXPLAIN (ANALYZE, BUFFERS) PAPER_SQL, literal 20000
EXPLAIN (ANALYZE, BUFFERS)
    WITH s AS (
      SELECT DISTINCT ON (position_key) position_key, us_market_slug,
             outcome, payout_per_contract, holding_side,
             evidence->>'venue_long_price' AS venue_long_price
        FROM paper_settlements ORDER BY position_key, version DESC)
    SELECT s.*, v.sport_family, v.market, v.fixture
      FROM s LEFT JOIN LATERAL (
        SELECT sport_family, market,
               coalesce(event_key, us_market_slug) AS fixture
          FROM external_valuations x
         WHERE x.us_market_slug = s.us_market_slug
         ORDER BY x.id DESC LIMIT 1) v ON true
     LIMIT 20000;

\echo == P6 EXPLAIN (ANALYZE, BUFFERS) MARKETS_SQL, literals
EXPLAIN (ANALYZE, BUFFERS)
    SELECT us_market_slug AS slug,
           min(sport_family) AS sport_family, min(market) AS market,
           min(coalesce(event_key, us_market_slug)) AS fixture,
           array_agg(DISTINCT outcome_basis)
               FILTER (WHERE outcome_basis IS NOT NULL) AS bases,
           array_agg(DISTINCT trim(settlement_read))
               FILTER (WHERE outcome_basis IS NULL
                         AND settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$')
               AS numeric_reads,
           max(settlement_read_at) AS last_read_at
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
     GROUP BY us_market_slug
     LIMIT 20000;

\echo == P7 EXPLAIN (ANALYZE, BUFFERS) DIVERGENCE_SQL, literals (now, 45 days, 20000)
EXPLAIN (ANALYZE, BUFFERS)
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           sport_family, market,
           settlement_comparison->'per_condition' AS per_condition
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
       AND decided_at > now() - make_interval(days => 45)
     ORDER BY us_market_slug, decided_at DESC
     LIMIT 20000;

\echo == P8 whole-measure wall time bracket: the three statements back to back (counts only)
SELECT clock_timestamp() AS t_before_measure;
SELECT count(*) AS markets_rows FROM (
    SELECT us_market_slug AS slug,
           min(sport_family) AS sport_family, min(market) AS market,
           min(coalesce(event_key, us_market_slug)) AS fixture,
           array_agg(DISTINCT outcome_basis)
               FILTER (WHERE outcome_basis IS NOT NULL) AS bases,
           array_agg(DISTINCT trim(settlement_read))
               FILTER (WHERE outcome_basis IS NULL
                         AND settlement_read ~ '^\s*[0-9]*\.?[0-9]+\s*$')
               AS numeric_reads,
           max(settlement_read_at) AS last_read_at
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)
     GROUP BY us_market_slug
     LIMIT 20000) q;
SELECT count(*) AS paper_rows FROM (
    WITH s AS (
      SELECT DISTINCT ON (position_key) position_key, us_market_slug,
             outcome, payout_per_contract, holding_side,
             evidence->>'venue_long_price' AS venue_long_price
        FROM paper_settlements ORDER BY position_key, version DESC)
    SELECT s.*, v.sport_family, v.market, v.fixture
      FROM s LEFT JOIN LATERAL (
        SELECT sport_family, market,
               coalesce(event_key, us_market_slug) AS fixture
          FROM external_valuations x
         WHERE x.us_market_slug = s.us_market_slug
         ORDER BY x.id DESC LIMIT 1) v ON true
     LIMIT 20000) q;
SELECT count(*) AS divergence_rows FROM (
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           sport_family, market,
           settlement_comparison->'per_condition' AS per_condition
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
       AND decided_at > now() - make_interval(days => 45)
     ORDER BY us_market_slug, decided_at DESC
     LIMIT 20000) q;
SELECT clock_timestamp() AS t_after_measure;

\echo == C1 SER component status / why on canonical_decision_intents, last 24 h
SELECT coalesce(evidence->'settlement_exception_risk'->>'status', '(key absent)') AS status,
       left(coalesce(evidence->'settlement_exception_risk'->>'why', ''), 70) AS why,
       count(*) AS intents,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY intents DESC;

\echo == C2 the same, last 7 d
SELECT coalesce(evidence->'settlement_exception_risk'->>'status', '(key absent)') AS status,
       left(coalesce(evidence->'settlement_exception_risk'->>'why', ''), 70) AS why,
       count(*) AS intents,
       min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '7 days'
 GROUP BY 1, 2
 ORDER BY intents DESC;

\echo == C3 by day, last 21 d: intents, SER timeouts, SER other UNAVAILABLE, SER computed
SELECT date_trunc('day', created_at)::date AS day,
       count(*) AS intents,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%') AS ser_timeout,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' = 'UNAVAILABLE'
                          AND coalesce(evidence->'settlement_exception_risk'->>'why', '') NOT LIKE 'COMPONENT_TIMEOUT%') AS ser_other_unavailable,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' IN ('MEASURED', 'PRIOR_BOUNDED')) AS ser_computed,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk' IS NULL) AS key_absent
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '21 days'
 GROUP BY 1
 ORDER BY 1;

\echo == C4 first and last SER COMPONENT_TIMEOUT ever recorded, and the total
SELECT count(*) AS ser_timeouts_all_time,
       min(created_at) AS first_at, max(created_at) AS last_at,
       (SELECT count(*) FROM canonical_decision_intents) AS intents_all_time,
       (SELECT min(created_at) FROM canonical_decision_intents) AS intents_since
  FROM canonical_decision_intents
 WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%';

\echo == C5 every canonical component: COMPONENT_TIMEOUT counts, last 7 d (is SER alone?)
SELECT k.key AS component,
       count(*) AS intents_with_key,
       count(*) FILTER (WHERE (evidence->k.key->>'why') LIKE 'COMPONENT_TIMEOUT%') AS timeouts,
       count(*) FILTER (WHERE evidence->k.key->>'status' = 'UNAVAILABLE') AS unavailable
  FROM canonical_decision_intents, LATERAL (VALUES ('eddie'), ('allie'), ('karen'), ('opportunity_score'), ('settlement_exception_risk')) AS k(key)
 WHERE created_at >= now() - interval '7 days'
   AND evidence ? k.key
 GROUP BY 1
 ORDER BY 1;

\echo == C6 three most recent SER timeouts, if any
SELECT created_at, strategy, us_market_slug,
       left(evidence->'settlement_exception_risk'->>'why', 60) AS why
  FROM canonical_decision_intents
 WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%'
 ORDER BY created_at DESC
 LIMIT 3;

\echo == end
