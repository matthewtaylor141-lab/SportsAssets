-- POST_TELEMETRY_DEPLOY_W1 -- the declared measurement window.
--
-- The window was declared in bettor_state_capture.MEASUREMENT_WINDOW
-- and committed at 7f7a9a6 BEFORE any query of it was run. Its bounds
-- follow from the rule, not from choice:
--
--   deploy 1dc4854 ended   2026-09-20T21:18:19Z   (render-ops events)
--   + 10 min warm-up    -> 2026-09-20T22:18:17Z   WINDOW START
--   + 30 min declared   -> 2026-09-20T22:48:17Z   WINDOW END
--
-- THE COHORT IS FIXED BY THE WINDOW AND THEN FOLLOWED OUT OF IT.
-- Observations are selected by observed_at inside the window. Their
-- outcomes are then chased to each horizon's own recovery deadline,
-- which for the 3600s horizon lies well outside the window. Those are
-- reported PENDING, not missing. Shortening a horizon to fit a
-- measurement window would be measuring the window, not the market.
--
-- FINALLY_MISSING vs PENDING, per the declaration:
--   FINALLY_MISSING  now() > observed_at + horizon + 600s recovery
--                    and still no read. The deadline has passed.
--   PENDING          the deadline has not passed. Not a loss.
--
-- LATE NEVER COUNTS TOWARD THE HORIZON. ON_TIME is the only class
-- admissible to a horizon's gate; LATE_RECOVERY is reported beside it
-- and never inside it.
--
-- Read only. No settlement term anywhere.

-- ── A. the cohort, and what the window itself did ───────────────────
SELECT 'A_WINDOW' AS section, k, v FROM (
    SELECT 'WINDOW_START' AS k, '2026-09-20T22:18:17Z' AS v
    UNION ALL SELECT 'WINDOW_END', '2026-09-20T22:48:17Z'
    UNION ALL SELECT 'NOW', now()::text
    UNION ALL
    SELECT 'COHORT_OBSERVATIONS', count(*)::text
      FROM bettor_state_observations
     WHERE observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND observed_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL
    SELECT 'COHORT_MARKETS', count(DISTINCT market_id)::text
      FROM bettor_state_observations
     WHERE observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND observed_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL
    SELECT 'COHORT_EVENTS', count(DISTINCT event_id)::text
      FROM bettor_state_observations
     WHERE observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND observed_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL
    SELECT 'COHORT_UNIVERSE_VERSIONS',
           string_agg(DISTINCT universe_version, ',')
      FROM bettor_state_observations
     WHERE observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND observed_at <  '2026-09-20T22:48:17Z'::timestamptz
) a

UNION ALL

-- ── B. INITIAL READS: throughput and refusals, reported apart ───────
SELECT 'B_INITIAL_READS',
       CASE WHEN book_readability_status = 'READABLE' THEN 'READABLE'
            WHEN book_readability_status LIKE '%RateLimit%'
                 THEN 'RATE_LIMITED_429'
            ELSE 'UNREADABLE_OTHER' END,
       count(*)::text
  FROM bettor_state_observations
 WHERE observed_at >= '2026-09-20T22:18:17Z'::timestamptz
   AND observed_at <  '2026-09-20T22:48:17Z'::timestamptz
 GROUP BY 2

UNION ALL

-- ── C. the tick ledger: scheduled vs attempted vs skipped ───────────
-- The reconciliation the observations table cannot provide, because
-- budget-skipped and abandoned reads write no observation row.
SELECT 'C_TICK_LEDGER', k, v FROM (
    SELECT 'TICKS' AS k, count(*)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND tick_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_SCHEDULED', coalesce(sum(obs_scheduled),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_ATTEMPTED', coalesce(sum(obs_attempted),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_SKIPPED_BUDGET', coalesce(sum(obs_skipped_budget),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_SKIPPED_ABANDON', coalesce(sum(obs_skipped_abandon),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_NEVER_ATTEMPTED', coalesce(sum(obs_never_attempted),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_WRITTEN', coalesce(sum(obs_written),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'OBS_RATE_LIMITED', coalesce(sum(obs_rate_limited),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_DUE', coalesce(sum(fu_due),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_ATTEMPTED', coalesce(sum(fu_attempted),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_SKIPPED_BUDGET', coalesce(sum(fu_skipped_budget),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_ON_TIME', coalesce(sum(fu_on_time),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_LATE', coalesce(sum(fu_late),0)::text
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'PACING_VERSIONS', coalesce(string_agg(DISTINCT pacing_version, ','), 'NONE')
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'PACING_S_RANGE',
           coalesce(min(pacing_s) || '..' || max(pacing_s), 'NONE')
      FROM bettor_capture_ticks WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz AND tick_at < '2026-09-20T22:48:17Z'::timestamptz
) c

UNION ALL

-- ── D. FOLLOW-UPS, per horizon, cohort-fixed ────────────────────────
-- ELIGIBLE counts cohort observations whose horizon has come due.
-- PENDING are those whose recovery deadline has NOT yet passed.
SELECT 'D_HORIZON_' || lpad(h.horizon::text, 4, '0'), k, v
  FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
 CROSS JOIN LATERAL (
    SELECT 'ELIGIBLE_HORIZON_DUE' AS k, count(*)::text AS v
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
       AND o.observed_at < now() - (h.horizon || ' seconds')::interval
    UNION ALL
    SELECT 'ON_TIME_GATE_ADMISSIBLE',
           count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
       AND m.timing_class = 'ON_TIME'
    UNION ALL
    SELECT 'LATE_RECOVERY_NOT_IN_GATE',
           count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
       AND m.timing_class = 'LATE_RECOVERY'
    UNION ALL
    -- PENDING: due, unread, but the recovery deadline is still ahead.
    SELECT 'PENDING_RECOVERY_DEADLINE_AHEAD',
           count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
       AND o.observed_at < now() - (h.horizon || ' seconds')::interval
       AND o.observed_at >= now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
    UNION ALL
    -- FINALLY_MISSING: the recovery deadline has passed unread.
    SELECT 'FINALLY_MISSING',
           count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
       AND o.observed_at < now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
 ) x

UNION ALL

-- ── E. the actual delay distribution for the cohort ─────────────────
SELECT 'E_DELAY',
       m.horizon_s::text || 's',
       'p50=' || coalesce(round((percentile_cont(0.5) WITHIN GROUP (
           ORDER BY m.actual_lag_s::numeric))::numeric, 1)::text, '-')
       || ' p90=' || coalesce(round((percentile_cont(0.9) WITHIN GROUP (
           ORDER BY m.actual_lag_s::numeric))::numeric, 1)::text, '-')
       || ' max=' || coalesce(round(max(m.actual_lag_s::numeric), 1)::text, '-')
       || ' n=' || count(*)::text
  FROM bettor_state_observations o
  JOIN bettor_state_mids m ON m.observation_id = o.observation_id
 WHERE o.observed_at >= '2026-09-20T22:18:17Z'::timestamptz
   AND o.observed_at <  '2026-09-20T22:48:17Z'::timestamptz
   AND m.actual_lag_s IS NOT NULL
   AND m.actual_lag_s <> 'NOT_IDENTIFIED'
 GROUP BY 2

UNION ALL

-- ── G. the new counters: TRUE demand vs what the budget admitted ────
-- fu_due is now mids_outstanding (no limit); fu_selected is the batch
-- the budget allowed. Before 7101382 these were the same number by
-- construction.
SELECT 'G_DEMAND_VS_SELECTION', k, v FROM (
    SELECT 'FU_DUE_TRUE_DEMAND' AS k,
           coalesce(sum(fu_due),0)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND tick_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'FU_SELECTED_BY_BUDGET',
           coalesce(sum(fu_selected),0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND tick_at <  '2026-09-20T22:48:17Z'::timestamptz
    UNION ALL SELECT 'INITIAL_COVERAGE_PCT',
           coalesce(round(100.0 * sum(obs_attempted)
                    / nullif(sum(obs_scheduled),0), 1)::text, '-')
           || '%  (W1 was 44.6%)'
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T22:18:17Z'::timestamptz
       AND tick_at <  '2026-09-20T22:48:17Z'::timestamptz
) g

ORDER BY 1, 2
