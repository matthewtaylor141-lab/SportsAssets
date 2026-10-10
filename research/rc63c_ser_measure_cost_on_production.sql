-- READ-ONLY. RC6.3c independent evidence lens on the settlement-exception
-- component timeout (backend-tests 38076712880 on dd25c588:
-- COMPONENT_TIMEOUT_AT_DECISION_2.0S). canonical_components.
-- settlement_exception_at_decision runs settlement_exception_risk.measure
-- under a 2.0 s bound; measure runs exactly the three SELECTs below
-- (MARKETS_SQL, PAPER_SQL, DIVERGENCE_SQL, parameters bound to the module's
-- constants: EXPERIMENT_ID, MAX_MARKETS 20000, DIVERGENCE_WINDOW_DAYS 45).
-- This prints, on production, what each statement costs and the plan shape
-- of PAPER_SQL's LATERAL over external_valuations (no index on
-- us_market_slug), beside the row counts that drive it.

\echo == 0. server, tables, statistics
SELECT version();
SELECT relname, n_live_tup, n_dead_tup, last_autovacuum, last_autoanalyze,
       last_analyze
  FROM pg_stat_user_tables
 WHERE relname IN ('external_valuations', 'paper_settlements')
 ORDER BY relname;
SELECT relname, relpages, reltuples
  FROM pg_class
 WHERE relname IN ('external_valuations', 'paper_settlements')
 ORDER BY relname;
SELECT attname, n_distinct, null_frac
  FROM pg_stats
 WHERE tablename = 'external_valuations'
   AND attname IN ('us_market_slug', 'experiment_id', 'outcome_basis');
SELECT indexname, indexdef
  FROM pg_indexes
 WHERE tablename = 'external_valuations'
 ORDER BY indexname;

\echo == 1. the rows PAPER_SQL walks
SELECT count(*) AS paper_settlements_rows,
       count(DISTINCT position_key) AS distinct_position_keys,
       count(DISTINCT us_market_slug) AS distinct_slugs
  FROM paper_settlements;
SELECT count(*) AS latest_settlements_without_a_valuation_row
  FROM (SELECT DISTINCT ON (position_key) position_key, us_market_slug
          FROM paper_settlements ORDER BY position_key, version DESC) s
 WHERE NOT EXISTS (SELECT 1 FROM external_valuations x
                    WHERE x.us_market_slug = s.us_market_slug);
SELECT count(*) AS external_valuations_rows,
       count(*) FILTER (WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW')
           AS entry_experiment_rows,
       min(id) AS min_id, max(id) AS max_id
  FROM external_valuations;

\echo == 2. MARKETS_SQL, EXPLAIN (ANALYZE, BUFFERS)
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

\echo == 3. PAPER_SQL, EXPLAIN (ANALYZE, BUFFERS)
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

\echo == 4. DIVERGENCE_SQL, EXPLAIN (ANALYZE, BUFFERS)
EXPLAIN (ANALYZE, BUFFERS)
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           sport_family, market,
           settlement_comparison->'per_condition' AS per_condition
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
       AND decided_at > to_timestamp(extract(epoch from now())) - make_interval(days => 45)
     ORDER BY us_market_slug, decided_at DESC
     LIMIT 20000;

\echo == 5. the component's recent verdicts on production intents (last 48 h)
SELECT evidence->'settlement_exception_risk'->>'status' AS status,
       left(evidence->'settlement_exception_risk'->>'why', 60) AS why,
       count(*) AS intents, min(created_at) AS first_at, max(created_at) AS last_at
  FROM canonical_decision_intents
 WHERE created_at > now() - interval '48 hours'
 GROUP BY 1, 2
 ORDER BY intents DESC;
