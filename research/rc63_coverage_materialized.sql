-- RC6.3 paper pass stall: coverage_integrity statements on the 2026-10-10 UTC day, as written vs with the
-- league CTEs MATERIALIZED (SELECT only; elapsed from clock_timestamp).
\echo EVALUATED_SQL AS_WRITTEN
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp(1791561600.0) AND ev.decided_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo EVALUATED_SQL MATERIALIZED
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, count(DISTINCT coalesce(ev.event_key,
                                                 ev.id::text)) AS n
      FROM external_valuations ev
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE ev.record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY')
       AND ev.decided_at >= to_timestamp(1791561600.0) AND ev.decided_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo DECISIONS_SQL AS_WRITTEN
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC),
    d AS (
        SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
         WHERE pd.decided_at >= to_timestamp(1791561600.0)
           AND pd.decided_at < to_timestamp(1791648000.0)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league) q;
\echo DECISIONS_SQL MATERIALIZED
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC),
    d AS (
        SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league, coalesce(ev.event_key, pd.us_market_slug) AS ek,
               bool_or(pd.verdict = 'ENTER') AS entered
          FROM paper_decisions pd
          JOIN external_valuations ev ON ev.id = pd.valuation_id
          LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
         WHERE pd.decided_at >= to_timestamp(1791561600.0)
           AND pd.decided_at < to_timestamp(1791648000.0)
         GROUP BY 1, 2)
    SELECT league, count(*) AS decided,
           count(*) FILTER (WHERE entered) AS entered,
           count(*) FILTER (WHERE NOT entered) AS refused
      FROM d GROUP BY league) q;
\echo ORDERS_SQL AS_WRITTEN
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp(1791561600.0) AND po.created_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo ORDERS_SQL MATERIALIZED
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
            ORDER BY us_market_slug, cycle_at DESC)
    SELECT coalesce(m.sport_key, ms.sport_key, 'UNATTRIBUTED:' || coalesce(ev.sport_family, 'unknown')) AS league,
           count(DISTINCT coalesce(ev.event_key, po.us_market_slug)) AS n
      FROM paper_orders po
      JOIN paper_decisions pd ON pd.decision_id = po.decision_id
      JOIN external_valuations ev ON ev.id = pd.valuation_id
      LEFT JOIN m ON m.provider_event_id = ev.event_key
      LEFT JOIN ms ON ms.us_market_slug = ev.us_market_slug
     WHERE po.role = 'ENTRY'
       AND po.created_at >= to_timestamp(1791561600.0) AND po.created_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo FILLS_SQL AS_WRITTEN
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
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
       AND pf.filled_at >= to_timestamp(1791561600.0) AND pf.filled_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo FILLS_SQL MATERIALIZED
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
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
       AND pf.filled_at >= to_timestamp(1791561600.0) AND pf.filled_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo ACTUAL_SQL AS_WRITTEN
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
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
     WHERE ei.decided_at >= to_timestamp(1791561600.0) AND ei.decided_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
\echo ACTUAL_SQL MATERIALIZED
SELECT count(*) AS result_rows, clock_timestamp() - statement_timestamp() AS elapsed FROM (WITH 
    m AS MATERIALIZED (SELECT DISTINCT ON (provider_event_id) provider_event_id,
                 sport_key
            FROM ext_candidate_outcomes
           WHERE provider_event_id IS NOT NULL
             AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
             AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
           ORDER BY provider_event_id, cycle_at DESC),
    ms AS MATERIALIZED (SELECT DISTINCT ON (us_market_slug) us_market_slug, sport_key
             FROM ext_candidate_outcomes
            WHERE us_market_slug IS NOT NULL
              AND cycle_at >= to_timestamp(1791561600.0) - interval '14 days'
              AND cycle_at < to_timestamp(1791648000.0) + interval '1 day'
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
     WHERE ei.decided_at >= to_timestamp(1791561600.0) AND ei.decided_at < to_timestamp(1791648000.0)
     GROUP BY 1) q;
