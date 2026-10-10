-- RC6.3 paper pass stall: the coverage_integrity statements as the pass runs them -- prepared with bind
-- parameters -- under a GENERIC plan vs a CUSTOM plan, and the MATERIALIZED variant under a generic plan.
-- EXPLAIN ANALYZE of SELECT statements only, read-only transaction (SELECT only).
PREPARE w0(float8, float8) AS WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1;
PREPARE m0(float8, float8) AS WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1;
SET plan_cache_mode = force_generic_plan;
\echo EVALUATED_SQL AS_WRITTEN force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w0(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_custom_plan;
\echo EVALUATED_SQL AS_WRITTEN force_custom_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w0(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_generic_plan;
\echo EVALUATED_SQL MATERIALIZED force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m0(1791561600.0, 1791648000.0);
PREPARE w1(float8, float8) AS WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC),
    d AS (
        SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
         WHERE pd.decided_at >= to_timestamp($1)
           AND pd.decided_at < to_timestamp($2)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league;
PREPARE m1(float8, float8) AS WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC),
    d AS (
        SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
         WHERE pd.decided_at >= to_timestamp($1)
           AND pd.decided_at < to_timestamp($2)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league;
SET plan_cache_mode = force_generic_plan;
\echo DECISIONS_SQL AS_WRITTEN force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w1(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_custom_plan;
\echo DECISIONS_SQL AS_WRITTEN force_custom_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w1(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_generic_plan;
\echo DECISIONS_SQL MATERIALIZED force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m1(1791561600.0, 1791648000.0);
PREPARE w2(float8, float8) AS WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp($1) AND po.created_at < to_timestamp($2)
     GROUP BY 1;
PREPARE m2(float8, float8) AS WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp($1) AND po.created_at < to_timestamp($2)
     GROUP BY 1;
SET plan_cache_mode = force_generic_plan;
\echo ORDERS_SQL AS_WRITTEN force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w2(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_custom_plan;
\echo ORDERS_SQL AS_WRITTEN force_custom_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w2(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_generic_plan;
\echo ORDERS_SQL MATERIALIZED force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m2(1791561600.0, 1791648000.0);
PREPARE w3(float8, float8) AS WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, pf.us_market_slug)) AS n
      FROM paper_fills pf
      JOIN paper_orders po ON po.order_id = pf.order_id
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND pf.filled_at >= to_timestamp($1) AND pf.filled_at < to_timestamp($2)
     GROUP BY 1;
PREPARE m3(float8, float8) AS WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, pf.us_market_slug)) AS n
      FROM paper_fills pf
      JOIN paper_orders po ON po.order_id = pf.order_id
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND pf.filled_at >= to_timestamp($1) AND pf.filled_at < to_timestamp($2)
     GROUP BY 1;
SET plan_cache_mode = force_generic_plan;
\echo FILLS_SQL AS_WRITTEN force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w3(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_custom_plan;
\echo FILLS_SQL AS_WRITTEN force_custom_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w3(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_generic_plan;
\echo FILLS_SQL MATERIALIZED force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m3(1791561600.0, 1791648000.0);
PREPARE w4(float8, float8) AS WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               AS actual_intents,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE ei.actual_mirror_id IS NOT NULL)
               AS actual_submitted,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE EXISTS (
                   SELECT 1 FROM execmirror_orders xo
                    WHERE xo.execution_intent_id = ei.intent_id
                      AND coalesce(xo.cum_qty, 0) > 0)) AS actual_filled
      FROM execution_intents ei
      LEFT JOIN external_valuations ev ON ev.id = ei.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ei.decided_at >= to_timestamp($1) AND ei.decided_at < to_timestamp($2)
     GROUP BY 1;
PREPARE m4(float8, float8) AS WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp($1) - interval '14 days'
             AND cycle_at < to_timestamp($2) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp($1) - interval '14 days'
              AND cycle_at < to_timestamp($2) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               AS actual_intents,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE ei.actual_mirror_id IS NOT NULL)
               AS actual_submitted,
           count(DISTINCT coalesce(ev.event_key, ei.us_market_slug))
               FILTER (WHERE EXISTS (
                   SELECT 1 FROM execmirror_orders xo
                    WHERE xo.execution_intent_id = ei.intent_id
                      AND coalesce(xo.cum_qty, 0) > 0)) AS actual_filled
      FROM execution_intents ei
      LEFT JOIN external_valuations ev ON ev.id = ei.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ei.decided_at >= to_timestamp($1) AND ei.decided_at < to_timestamp($2)
     GROUP BY 1;
SET plan_cache_mode = force_generic_plan;
\echo ACTUAL_SQL AS_WRITTEN force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w4(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_custom_plan;
\echo ACTUAL_SQL AS_WRITTEN force_custom_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w4(1791561600.0, 1791648000.0);
SET plan_cache_mode = force_generic_plan;
\echo ACTUAL_SQL MATERIALIZED force_generic_plan
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m4(1791561600.0, 1791648000.0);
