-- RC6.3 paper pass stall: ACTUAL_SQL and EVALUATED_SQL on window ny1010, prepared, generic plan, as written vs MATERIALIZED (EXPLAIN ANALYZE of SELECTs, read-only).
SET plan_cache_mode = force_generic_plan;
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
\echo ACTUAL_SQL ny1010 MATERIALIZED generic
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m0(1791576000.0, 1791662400.0);
\echo ACTUAL_SQL ny1010 AS_WRITTEN generic
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w0(1791576000.0, 1791662400.0);
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
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1;
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
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp($1) AND ev.decided_at < to_timestamp($2)
     GROUP BY 1;
\echo EVALUATED_SQL ny1010 MATERIALIZED generic
EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE m1(1791576000.0, 1791662400.0);
\echo EVALUATED_SQL ny1010 AS_WRITTEN generic
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON, COSTS OFF) EXECUTE w1(1791576000.0, 1791662400.0);
