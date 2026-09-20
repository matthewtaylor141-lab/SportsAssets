-- W3: THE ROTATION AND EARLY-ELIGIBILITY WINDOW.
--
-- Declared in sc.MEASUREMENT_WINDOW_W3 BEFORE the deploy. Bounds are
-- the deploy-completion rule used for W1 and W2, filled from the
-- deploy record and not chosen after seeing rows.
--
-- TWO MECHANISMS SHIPPED TOGETHER AND ARE ATTRIBUTED BY DIFFERENT
-- COLUMNS:
--   ALLOCATION  (PACING_VERSION ..._V3_ROTATED_FOLLOWUPS) moves
--               ATTEMPTS per horizon.  Section D.
--   ELIGIBILITY (ELIGIBILITY_VERSION ..._V3_EARLY_30) moves
--               TIMING_CLASS.          Section E.
-- A result with coverage but no on-time reads isolates the first; a
-- result with both isolates the pair. They are never pooled.
--
-- Read only. No settlement term.

-- BOUNDS ARE LITERALS, not psql variables: research-sql.yml refuses
-- every meta-command except \echo. They are filled from the deploy
-- record before the run and never adjusted afterwards. The sentinel
-- below is deliberately in the past so an UNFILLED run returns an
-- obviously empty result rather than a plausible one, and section A
-- prints the bounds it actually used.

-- ── A. the window, and whether it ran on what it claims to ─────────
SELECT 'A_WINDOW' AS section, k, v FROM (
    SELECT 'BOUNDS_USED' AS k,
           '2026-09-20T23:03:17Z'::timestamptz::text || ' -> '
           || '2026-09-20T23:33:17Z'::timestamptz::text AS v
    UNION ALL
    SELECT 'TICKS_IN_WINDOW', count(*)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    -- PROVENANCE. If more than one pacing version appears, the window
    -- spans a deploy and its numbers cannot be attributed.
    SELECT 'PACING_VERSIONS_SEEN',
           string_agg(DISTINCT pacing_version, ' + ')
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'COHORT_OBSERVATIONS', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
) a

UNION ALL

-- ── B. INITIAL-READ COVERAGE, the price of the reallocation ────────
-- Reported whether it rose or fell. W1 measured 44.6%.
SELECT 'B_INITIAL_READS', k, v FROM (
    SELECT 'OBS_SCHEDULED' AS k, coalesce(sum(obs_scheduled), 0)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'OBS_ATTEMPTED', coalesce(sum(obs_attempted), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'OBS_SKIPPED_BUDGET', coalesce(sum(obs_skipped_budget), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'ATTEMPTED_PCT_OF_SCHEDULED',
           CASE WHEN coalesce(sum(obs_scheduled), 0) = 0 THEN '-'
                ELSE round(100.0 * sum(obs_attempted)
                           / sum(obs_scheduled), 1)::text END
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
) b

UNION ALL

-- ── C. DID THE ROTATION ACTUALLY CYCLE? ────────────────────────────
-- THE DIRECT EVIDENCE, not an inference from attempt counts. Attempts
-- can look evenly spread while one horizon leads every tick and the
-- others live on its leftovers. A head count that is missing a horizon
-- entirely is the defect this column exists to catch.
-- NULL head = the tick had no follow-up budget, counted separately.
SELECT 'C_ROTATION_HEAD',
       coalesce(fu_rotation_head::text, 'NO_FOLLOWUP_BUDGET'),
       count(*)::text || ' ticks'
  FROM bettor_capture_ticks
 WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
   AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
 GROUP BY 2

UNION ALL

-- ── D. ALLOCATION: attempts per horizon, with full denominators ────
-- Every cohort observation lands in exactly one bucket per horizon.
SELECT 'D_HORIZON_' || lpad(h.horizon::text, 4, '0'), k, v
  FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
 CROSS JOIN LATERAL (
    SELECT 'COHORT_TOTAL' AS k, count(*)::text AS v
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'NOT_YET_DUE', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND o.observed_at >= now() - (h.horizon || ' seconds')::interval
    UNION ALL
    SELECT 'ELIGIBLE_HORIZON_DUE', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND o.observed_at < now() - (h.horizon || ' seconds')::interval
    UNION ALL
    -- THE ALLOCATION CRITERION. Zero here at a horizon with standing
    -- demand falsifies the rotation repair.
    SELECT 'ATTEMPTS_ANY_TIMING_CLASS', count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'ON_TIME_GATE_ADMISSIBLE', count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND m.timing_class = 'ON_TIME'
    UNION ALL
    SELECT 'LATE_RECOVERY_NOT_IN_GATE', count(*)::text
      FROM bettor_state_observations o
      JOIN bettor_state_mids m ON m.observation_id = o.observation_id
                              AND m.horizon_s = h.horizon
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND m.timing_class = 'LATE_RECOVERY'
    UNION ALL
    -- PENDING: due, unread, recovery deadline still ahead. NOT missing.
    SELECT 'PENDING_RECOVERY_DEADLINE_AHEAD', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND o.observed_at <  now() - (h.horizon || ' seconds')::interval
       AND o.observed_at >= now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
    UNION ALL
    SELECT 'FINALLY_MISSING', count(*)::text
      FROM bettor_state_observations o
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND o.observed_at <  now() - ((h.horizon + 600) || ' seconds')::interval
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
 ) x

UNION ALL

-- ── E. ELIGIBILITY: where the reads actually landed ────────────────
-- The eligibility change should move lags from "just past the band"
-- into it. If lags cluster at horizon+40..+120 the window opened but
-- the scheduler still could not reach them, which is an allocation
-- result, not an eligibility one.
SELECT 'E_LAG_PLACEMENT',
       m.horizon_s::text || 's | ' ||
       CASE
         WHEN m.actual_lag_s IS NULL THEN 'NO_LAG'
         WHEN m.actual_lag_s = 'NOT_IDENTIFIED' THEN 'NO_LAG'
         WHEN m.actual_lag_s::numeric <  m.horizon_s - 30 THEN 'EARLY_OUTSIDE'
         WHEN m.actual_lag_s::numeric <  m.horizon_s      THEN 'IN_BAND_EARLY_SIDE'
         WHEN m.actual_lag_s::numeric <= m.horizon_s + 30 THEN 'IN_BAND_LATE_SIDE'
         WHEN m.actual_lag_s::numeric <= m.horizon_s + 120 THEN 'MISSED_BY_LE_90S'
         ELSE 'MISSED_BY_GT_90S'
       END,
       count(*)::text
  FROM bettor_state_observations o
  JOIN bettor_state_mids m ON m.observation_id = o.observation_id
 WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
   AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
 GROUP BY 2

UNION ALL

-- ── F. demand vs what the budget admitted, still two columns ───────
SELECT 'F_DEMAND_VS_SELECTION', k, v FROM (
    SELECT 'FU_DUE_TRUE_OUTSTANDING' AS k,
           coalesce(sum(fu_due), 0)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'FU_SELECTED_BY_BUDGET', coalesce(sum(fu_selected), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'FU_ATTEMPTED', coalesce(sum(fu_attempted), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'FU_ON_TIME', coalesce(sum(fu_on_time), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'FU_LATE', coalesce(sum(fu_late), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
) f

ORDER BY 1, 2
