-- FOLLOW-UP TIMING, PER HORIZON, WITH DENOMINATORS.
--
-- Owner 2026-09-20 §2, for each horizon: eligible observations,
-- attempts and successful reads, reads within the ORIGINAL tolerance,
-- late reads, missing reads, and the actual observation-delay
-- distribution.
--
-- THE RULE THIS QUERY ENFORCES: a late read is never counted toward the
-- horizon it was scheduled for. ON_TIME and LATE_RECOVERY are reported
-- as separate columns, and only ON_TIME is admissible to a horizon's
-- gate. The 600s recovery window buys coverage, not validity.
--
-- Read only. Carries no settlement term.

-- ELIGIBLE is the denominator: observations old enough that the horizon
-- has come due at all. An observation 20 seconds old is not a missing
-- 300s read, and pooling it into the denominator would understate the
-- gap.
SELECT 'A_ELIGIBLE_BY_HORIZON' AS section,
       h.horizon::text || 's | ' || o.universe_version AS k,
       count(*)::text AS v
  FROM bettor_state_observations o
 CROSS JOIN (VALUES (60), (300), (900), (3600)) AS h(horizon)
 WHERE o.observed_at < now() - (h.horizon || ' seconds')::interval
 GROUP BY 2

UNION ALL

-- ATTEMPTS AND OUTCOMES, split by timing class. A row exists here only
-- if a read was attempted and recorded.
SELECT 'B_READS_BY_HORIZON',
       m.horizon_s::text || 's | ' ||
       coalesce(m.timing_class, 'UNCLASSIFIED_PRE_FIX'),
       count(*)::text
  FROM bettor_state_mids m
 GROUP BY 2

UNION ALL

-- THE GATE-ADMISSIBLE COUNT. This is the only figure a horizon's test
-- may use, and it is reported separately so it cannot be confused with
-- total coverage.
SELECT 'C_GATE_ADMISSIBLE',
       m.horizon_s::text || 's_ON_TIME_ONLY',
       count(*)::text
  FROM bettor_state_mids m
 WHERE m.timing_class = 'ON_TIME'
 GROUP BY 2

UNION ALL

-- MISSING: eligible but never read at all. The quantity that was 3.4%
-- before the fix.
SELECT 'D_MISSING_READS',
       h.horizon::text || 's_MISSING',
       count(*)::text
  FROM bettor_state_observations o
 CROSS JOIN (VALUES (60), (300), (900), (3600)) AS h(horizon)
 WHERE o.observed_at < now() - (h.horizon || ' seconds')::interval
   AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                    WHERE m.observation_id = o.observation_id
                      AND m.horizon_s = h.horizon)
 GROUP BY 2

UNION ALL

-- THE ACTUAL DELAY DISTRIBUTION, not a summary of it. Buckets are wide
-- enough to be readable and fine enough to show whether late reads
-- cluster just past the tolerance or trail far behind it.
SELECT 'E_DELAY_DISTRIBUTION',
       m.horizon_s::text || 's | ' ||
       CASE
         WHEN m.actual_lag_s IS NULL THEN 'NO_LAG_RECORDED'
         WHEN m.actual_lag_s::numeric < m.horizon_s - 30 THEN 'EARLY'
         WHEN m.actual_lag_s::numeric <= m.horizon_s + 30 THEN 'ON_TIME_WINDOW'
         WHEN m.actual_lag_s::numeric <= m.horizon_s + 120 THEN 'LATE_LE_2MIN'
         WHEN m.actual_lag_s::numeric <= m.horizon_s + 300 THEN 'LATE_LE_5MIN'
         ELSE 'LATE_GT_5MIN'
       END,
       count(*)::text
  FROM bettor_state_mids m
 GROUP BY 2

UNION ALL

SELECT 'F_DELAY_QUANTILES',
       m.horizon_s::text || 's',
       'p50=' || round((percentile_cont(0.5) WITHIN GROUP (
                  ORDER BY m.actual_lag_s::numeric))::numeric, 1)::text
       || ' p90=' || round((percentile_cont(0.9) WITHIN GROUP (
                  ORDER BY m.actual_lag_s::numeric))::numeric, 1)::text
       || ' max=' || round(max(m.actual_lag_s::numeric), 1)::text
       || ' n=' || count(*)::text
  FROM bettor_state_mids m
 WHERE m.actual_lag_s IS NOT NULL
   AND m.actual_lag_s <> 'NOT_IDENTIFIED'
 GROUP BY 2

UNION ALL

-- ── PER-TICK LOSSES, the denominator that leaves no observation row ──
SELECT 'G_TICK_LOSSES', k, v FROM (
    SELECT 'TICKS_RECORDED' AS k, count(*)::text AS v
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'OBS_SCHEDULED', coalesce(sum(obs_scheduled), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'OBS_ATTEMPTED', coalesce(sum(obs_attempted), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'OBS_NEVER_ATTEMPTED',
           coalesce(sum(obs_never_attempted), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'OBS_SKIPPED_BUDGET',
           coalesce(sum(obs_skipped_budget), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'OBS_SKIPPED_ABANDON',
           coalesce(sum(obs_skipped_abandon), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'FU_DUE', coalesce(sum(fu_due), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'FU_ATTEMPTED', coalesce(sum(fu_attempted), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'FU_SKIPPED_BUDGET',
           coalesce(sum(fu_skipped_budget), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'FU_ON_TIME', coalesce(sum(fu_on_time), 0)::text
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'FU_LATE', coalesce(sum(fu_late), 0)::text
      FROM bettor_capture_ticks
) g

UNION ALL

SELECT 'H_TICKS_BY_PACING_VERSION',
       pacing_version || ' | ' || universe_version,
       count(*)::text || ' ticks, first ' || min(tick_at)::text
  FROM bettor_capture_ticks
 GROUP BY 2

ORDER BY 1, 2
