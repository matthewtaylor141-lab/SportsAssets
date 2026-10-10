-- SETTLEMENT-EXCEPTION COMPONENT AVAILABILITY ON PRODUCTION DECISIONS (SELECT only; nothing here writes).
--
-- WHY. backend-tests 38076712880 on dd25c588 (RC6.3c candidate) failed
-- tests/test_settlement_exception_risk.py::test_the_cost_rides_on_the_intent_and_gates_nothing with the canonical
-- decision's settlement_exception_risk component recorded {status: UNAVAILABLE, why: COMPONENT_TIMEOUT_AT_DECISION_2.0S}:
-- canonical_components.settlement_exception_at_decision runs settlement_exception_risk.measure under the 2.0 s component
-- bound (COMPONENT_TIMEOUT_S) and caches the table for 300 s ONLY when the read finishes inside the bound -- a read cut by
-- the bound populates no cache, so every later decision runs the whole read again and is cut again. measure runs three
-- statements; PAPER_SQL is DISTINCT ON (position_key) over paper_settlements with a LATERAL per settlement row
-- (SELECT ... FROM external_valuations x WHERE x.us_market_slug = s.us_market_slug ORDER BY x.id DESC LIMIT 1), and
-- external_valuations has no index on us_market_slug, so its cost is (#paper_settlements rows) x (external_valuations heap).
-- Locally (ser_dd25, fresh migrated database): 1,216 settlement rows x 20,000 valuation rows = 6.7 s; x 5,000 = 1.0 s; 2 rows
-- x 20,000 = 0.01 s. In CI the rows came from a new proof file; the question here is whether PRODUCTION decisions
-- (workers' paper pass, canonical_decision_intents.evidence->'settlement_exception_risk') also carry the timeout, since when,
-- and how big the two tables the read multiplies are.
--
-- S1 the component's (status, why) over the last 24 h and the last 7 d
-- S2 by hour, last 24 h; S3 by day, last 7 d (UTC)
-- S4 first and last appearance of every (status, why) the component ever recorded, with the decision count
-- S5 Allie's component beside it (same bound; its own 2.0 s timeouts were root-caused in the lifecycle-proof lane)
-- S6 the two tables the read multiplies: row counts, settlement rows whose slug has no valuation row, the indexes
-- S7 the three statements of measure, EXPLAIN ANALYZE on the replica (their sum is the read's time here)

\echo S1 settlement_exception_risk component on canonical decision intents, last 24 h and last 7 d
SELECT CASE WHEN created_at >= now() - interval '24 hours' THEN 'last_24h' ELSE 'days_2_to_7' END AS window,
       coalesce(evidence->'settlement_exception_risk'->>'status', 'NOT_RECORDED') AS status,
       left(coalesce(evidence->'settlement_exception_risk'->>'why', '-'), 90) AS why,
       count(*) AS decisions,
       to_char(min(created_at), 'MM-DD HH24:MI') AS first_at, to_char(max(created_at), 'MM-DD HH24:MI') AS last_at
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;

\echo S2 by hour, last 24 h: decisions, how many carry the component timeout, how many a computed cost
SELECT to_char(date_trunc('hour', created_at), 'MM-DD HH24') AS hour_utc, count(*) AS decisions,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%') AS ser_timeout,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' IN ('MEASURED', 'PRIOR_BOUNDED')) AS ser_computed,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' = 'UNAVAILABLE'
                          AND evidence->'settlement_exception_risk'->>'why' NOT LIKE 'COMPONENT_TIMEOUT%') AS ser_other_unavailable,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk' IS NULL) AS ser_not_recorded
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '24 hours'
 GROUP BY 1 ORDER BY 1;

\echo S3 by day, last 7 d (UTC): the same split
SELECT to_char(date_trunc('day', created_at), 'YYYY-MM-DD') AS day_utc, count(*) AS decisions,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%') AS ser_timeout,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' IN ('MEASURED', 'PRIOR_BOUNDED')) AS ser_computed,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'status' = 'UNAVAILABLE'
                          AND evidence->'settlement_exception_risk'->>'why' NOT LIKE 'COMPONENT_TIMEOUT%') AS ser_other_unavailable,
       count(*) FILTER (WHERE evidence->'settlement_exception_risk' IS NULL) AS ser_not_recorded,
       round(100.0 * count(*) FILTER (WHERE evidence->'settlement_exception_risk'->>'why' LIKE 'COMPONENT_TIMEOUT%')
             / greatest(count(*), 1), 1) AS pct_timeout
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '7 days'
 GROUP BY 1 ORDER BY 1;

\echo S4 every (status, why) the component ever recorded: first and last decision, count (since when)
SELECT coalesce(evidence->'settlement_exception_risk'->>'status', 'NOT_RECORDED') AS status,
       left(coalesce(evidence->'settlement_exception_risk'->>'why', '-'), 90) AS why,
       count(*) AS decisions,
       to_char(min(created_at), 'YYYY-MM-DD HH24:MI') AS first_at, to_char(max(created_at), 'YYYY-MM-DD HH24:MI') AS last_at
  FROM canonical_decision_intents
 GROUP BY 1, 2 ORDER BY min(created_at);

\echo S4b the last 12 decisions: the component status and why beside Allie, with strategy and sleeve
SELECT to_char(created_at, 'MM-DD HH24:MI:SS') AS at, strategy, sleeve,
       evidence->'settlement_exception_risk'->>'status' AS ser_status,
       left(evidence->'settlement_exception_risk'->>'why', 60) AS ser_why,
       allie->>'status' AS allie_status, left(allie->>'why', 50) AS allie_why
  FROM canonical_decision_intents ORDER BY created_at DESC LIMIT 12;

\echo S5 Allie component (same 2.0 s bound) over the same windows, for comparison
SELECT CASE WHEN created_at >= now() - interval '24 hours' THEN 'last_24h' ELSE 'days_2_to_7' END AS window,
       coalesce(allie->>'status', 'NOT_RECORDED') AS allie_status, left(coalesce(allie->>'why', '-'), 70) AS allie_why,
       count(*) AS decisions
  FROM canonical_decision_intents
 WHERE created_at >= now() - interval '7 days'
 GROUP BY 1, 2, 3 ORDER BY 1 DESC, 4 DESC;

\echo S6a the tables the read multiplies: paper_settlements rows, distinct slugs, rows whose slug has NO valuation row
SELECT (SELECT count(*) FROM paper_settlements) AS paper_settlements_rows,
       (SELECT count(DISTINCT position_key) FROM paper_settlements) AS settlement_positions,
       (SELECT count(DISTINCT us_market_slug) FROM paper_settlements) AS settlement_slugs,
       (SELECT count(*) FROM paper_settlements s
         WHERE NOT EXISTS (SELECT 1 FROM external_valuations x WHERE x.us_market_slug = s.us_market_slug)) AS settlement_rows_without_a_valuation_row,
       (SELECT count(*) FROM external_valuations) AS external_valuations_rows,
       (SELECT count(*) FROM external_valuations WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW') AS ev_rows_of_the_entry_experiment,
       (SELECT count(*) FROM external_valuations WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
           AND (outcome_basis IS NOT NULL OR settlement_read IS NOT NULL)) AS ev_rows_markets_sql_groups;

\echo S6b physical size and dead tuples of the two tables (pg_stat_user_tables on this replica)
SELECT relname, n_live_tup, n_dead_tup, pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       to_char(last_autovacuum, 'MM-DD HH24:MI') AS last_autovacuum, to_char(last_autoanalyze, 'MM-DD HH24:MI') AS last_autoanalyze
  FROM pg_stat_user_tables WHERE relname IN ('paper_settlements', 'external_valuations', 'paper_fills') ORDER BY relname;

\echo S6c indexes on external_valuations and paper_settlements (is us_market_slug indexed anywhere)
SELECT tablename, indexname, left(indexdef, 150) AS indexdef FROM pg_indexes
 WHERE tablename IN ('external_valuations', 'paper_settlements') ORDER BY tablename, indexname;

\echo S7a EXPLAIN ANALYZE of the PAPER_SQL statement of measure on this replica (its time here is the number to read)
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
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

\echo S7b EXPLAIN ANALYZE of the MARKETS_SQL statement of measure
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
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

\echo S7c EXPLAIN ANALYZE of the DIVERGENCE_SQL statement of measure (45-day window)
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, TIMING OFF, SUMMARY ON)
    SELECT DISTINCT ON (us_market_slug) us_market_slug AS slug,
           sport_family, market,
           settlement_comparison->'per_condition' AS per_condition
      FROM external_valuations
     WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND us_market_slug IS NOT NULL
       AND jsonb_typeof(settlement_comparison->'per_condition') = 'object'
       AND decided_at > now() - make_interval(days => 45)
     ORDER BY us_market_slug, decided_at DESC
     LIMIT 20000;
