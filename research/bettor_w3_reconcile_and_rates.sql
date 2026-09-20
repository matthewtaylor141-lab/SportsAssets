-- W3 RECONCILIATION AND FLOW RATES.
--
-- TWO REPORTING DEFECTS THIS QUERY EXISTS TO FIX.
--
-- (1) I REPORTED 59 WINDOW FOLLOW-UPS AS "32 COHORT + 42 OLDER".
--     Those do not reconcile: 32 + 42 = 74. They were counted on
--     DIFFERENT CUTOFFS -- 32 filtered on observed_at with no bound on
--     read_at, 42 filtered on read_at. A row read after the window but
--     observed inside it lands in 32 and in neither part of 59.
--     Section B partitions every mid row on BOTH axes at once, so the
--     cells are disjoint and add up.
--
--     ATTEMPT IDENTITY is (observation_id, horizon_s), the mids
--     primary key. One task, one attempt, one row -- so "attempts" and
--     "stored outcomes" differ only by attempts that stored nothing,
--     which section B measures rather than assumes.
--
-- (2) I DIVIDED A SUM OF LEVELS BY A COUNT OF EVENTS AND CALLED IT A
--     17x SHORTFALL. fu_due is the OUTSTANDING BACKLOG at each tick;
--     summing it over 25 ticks counts the same waiting task up to 25
--     times. That sum is not demand and cannot be compared with an
--     attempt count. Section C reports a LEVEL as a level and FLOWS as
--     per-minute rates: newly eligible, completed, expired. Only those
--     are comparable with each other.
--
-- Read only. No settlement term.

-- ── A. the window, restated so every later number has its bounds ────
SELECT 'A_BOUNDS' AS section, k, v FROM (
    SELECT 'WINDOW' AS k,
           '2026-09-20T23:03:17Z -> 2026-09-20T23:33:17Z' AS v
    UNION ALL
    SELECT 'NOW', now()::text
    UNION ALL
    SELECT 'ATTEMPT_IDENTITY', 'distinct (observation_id, horizon_s)'
) a

UNION ALL

-- ── B. RECONCILIATION on one cutoff, cells disjoint ────────────────
-- Every mid row falls in exactly one cell of observed-in/out x
-- read-in/out. Counts are DISTINCT (observation_id, horizon_s).
SELECT 'B_RECONCILE',
       CASE WHEN o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
                 AND o.observed_at < '2026-09-20T23:33:17Z'::timestamptz
            THEN 'obs_IN_window' ELSE 'obs_OUTSIDE_window' END
       || ' | ' ||
       CASE WHEN m.read_at >= '2026-09-20T23:03:17Z'::timestamptz
                 AND m.read_at < '2026-09-20T23:33:17Z'::timestamptz
            THEN 'read_IN_window'
            WHEN m.read_at >= '2026-09-20T23:33:17Z'::timestamptz
            THEN 'read_AFTER_window'
            ELSE 'read_BEFORE_window_or_null' END,
       count(DISTINCT (m.observation_id, m.horizon_s))::text
  FROM bettor_state_mids m
  JOIN bettor_state_observations o
    ON o.observation_id = m.observation_id
 GROUP BY 2

UNION ALL

-- The tick counter against the stored rows it should equal. A gap is
-- attempts that stored nothing, and fu_failed should account for it.
SELECT 'B2_ATTEMPTS_VS_STORED', k, v FROM (
    SELECT 'TICK_FU_ATTEMPTED_IN_WINDOW' AS k,
           coalesce(sum(fu_attempted), 0)::text AS v
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'TICK_FU_FAILED_IN_WINDOW',
           coalesce(sum(fu_failed), 0)::text
      FROM bettor_capture_ticks
     WHERE tick_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND tick_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    SELECT 'STORED_ROWS_WITH_READ_AT_IN_WINDOW',
           count(DISTINCT (m.observation_id, m.horizon_s))::text
      FROM bettor_state_mids m
     WHERE m.read_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND m.read_at <  '2026-09-20T23:33:17Z'::timestamptz
    UNION ALL
    -- COHORT OUTCOMES COLLECTED AFTER THE WINDOW. These are cohort
    -- results but NOT window activity, and conflating them was half
    -- of defect (1).
    SELECT 'COHORT_ROWS_READ_AFTER_WINDOW_CLOSE',
           count(DISTINCT (m.observation_id, m.horizon_s))::text
      FROM bettor_state_mids m
      JOIN bettor_state_observations o
        ON o.observation_id = m.observation_id
     WHERE o.observed_at >= '2026-09-20T23:03:17Z'::timestamptz
       AND o.observed_at <  '2026-09-20T23:33:17Z'::timestamptz
       AND m.read_at >= '2026-09-20T23:33:17Z'::timestamptz
) b2

UNION ALL

-- ── C. BACKLOG IS A LEVEL; DEMAND IS A RATE ────────────────────────
--
-- OUTSTANDING is measured once, now: tasks currently eligible and
-- unread. It is a stock. It is never summed across ticks.
SELECT 'C1_OUTSTANDING_LEVEL_NOW',
       h.horizon::text || 's_ELIGIBLE_UNREAD',
       count(*)::text
  FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
 CROSS JOIN bettor_state_observations o
 WHERE o.observed_at <= now() - ((h.horizon - 30) || ' seconds')::interval
   AND o.observed_at >  now() - ((h.horizon + 600) || ' seconds')::interval
   AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                    WHERE m.observation_id = o.observation_id
                      AND m.horizon_s = h.horizon)
 GROUP BY 2

UNION ALL

-- FLOWS, per minute, over the same 60-minute span so they compare.
-- Each admitted observation eventually makes FOUR follow-up tasks
-- eligible, one per horizon; a task becomes newly eligible at
-- T0 + horizon - 30 and expires unread at T0 + horizon + 600.
SELECT 'C2_FLOW_PER_MIN', k, v FROM (
    SELECT 'INTAKE_OBSERVATIONS_PER_MIN' AS k,
           round(count(*)::numeric / 60.0, 2)::text AS v
      FROM bettor_state_observations o
     WHERE o.observed_at > now() - interval '60 minutes'
    UNION ALL
    -- NEWLY ELIGIBLE: counted at the instant each task opens, so a
    -- task is counted ONCE however long it then waits.
    SELECT 'TASKS_NEWLY_ELIGIBLE_PER_MIN',
           round(count(*)::numeric / 60.0, 2)::text
      FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
     CROSS JOIN bettor_state_observations o
     WHERE o.observed_at + ((h.horizon - 30) || ' seconds')::interval
             > now() - interval '60 minutes'
       AND o.observed_at + ((h.horizon - 30) || ' seconds')::interval
             <= now()
    UNION ALL
    SELECT 'ATTEMPTS_COMPLETED_PER_MIN',
           round(count(*)::numeric / 60.0, 2)::text
      FROM bettor_state_mids m
     WHERE m.read_at > now() - interval '60 minutes'
    UNION ALL
    -- EXPIRED: counted at the instant the recovery deadline passes
    -- unread. Also once per task.
    SELECT 'TASKS_EXPIRED_UNREAD_PER_MIN',
           round(count(*)::numeric / 60.0, 2)::text
      FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
     CROSS JOIN bettor_state_observations o
     WHERE o.observed_at + ((h.horizon + 600) || ' seconds')::interval
             > now() - interval '60 minutes'
       AND o.observed_at + ((h.horizon + 600) || ' seconds')::interval
             <= now()
       AND NOT EXISTS (SELECT 1 FROM bettor_state_mids m
                        WHERE m.observation_id = o.observation_id
                          AND m.horizon_s = h.horizon)
    UNION ALL
    SELECT 'TICKS_PER_MIN',
           round(count(*)::numeric / 60.0, 2)::text
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '60 minutes'
) c2

UNION ALL

-- THE QUESTION THOSE RATES EXIST TO ANSWER: does intake generate more
-- follow-up work than capacity sustains? Eligible-task creation
-- against completion, in the same unit.
SELECT 'C3_SUSTAINABILITY', k, v FROM (
    SELECT 'ELIGIBLE_TASK_CREATION_PER_MIN' AS k,
           round(count(*)::numeric / 60.0, 2)::text AS v
      FROM (VALUES (60), (300), (900), (3600)) AS h(horizon)
     CROSS JOIN bettor_state_observations o
     WHERE o.observed_at + ((h.horizon - 30) || ' seconds')::interval
             > now() - interval '60 minutes'
       AND o.observed_at + ((h.horizon - 30) || ' seconds')::interval
             <= now()
    UNION ALL
    SELECT 'COMPLETION_PER_MIN',
           round(count(*)::numeric / 60.0, 2)::text
      FROM bettor_state_mids m
     WHERE m.read_at > now() - interval '60 minutes'
) c3

UNION ALL

-- ── D. QUEUE TRACE: how overdue is the work when it is chosen? ─────
-- mids_due orders oldest-first. If the chosen task is habitually far
-- past its band, the ordering rule is selecting recovery work over
-- on-time work. Overdue-at-read = lag - horizon.
SELECT 'D_OVERDUE_AT_READ',
       m.horizon_s::text || 's',
       'p50=' || round((percentile_cont(0.5) WITHIN GROUP (
           ORDER BY m.actual_lag_s::numeric - m.horizon_s))::numeric, 1)::text
       || ' p90=' || round((percentile_cont(0.9) WITHIN GROUP (
           ORDER BY m.actual_lag_s::numeric - m.horizon_s))::numeric, 1)::text
       || ' min=' || round(min(m.actual_lag_s::numeric - m.horizon_s), 1)::text
       || ' n=' || count(*)::text
  FROM bettor_state_mids m
 WHERE m.read_at > now() - interval '60 minutes'
   AND m.actual_lag_s IS NOT NULL
   AND m.actual_lag_s <> 'NOT_IDENTIFIED'
 GROUP BY 2

UNION ALL

-- Were there ANY tasks inside their band at the moment of selection?
-- If the minimum overdue is always large, on-time candidates existed
-- but were never at the front of the queue.
SELECT 'E_BAND_AVAILABLE_VS_CHOSEN', k, v FROM (
    SELECT 'READS_INSIDE_BAND_LAST_60_MIN' AS k, count(*)::text AS v
      FROM bettor_state_mids m
     WHERE m.read_at > now() - interval '60 minutes'
       AND m.actual_lag_s IS NOT NULL
       AND m.actual_lag_s <> 'NOT_IDENTIFIED'
       AND abs(m.actual_lag_s::numeric - m.horizon_s) <= 30
    UNION ALL
    SELECT 'READS_TOTAL_LAST_60_MIN', count(*)::text
      FROM bettor_state_mids m
     WHERE m.read_at > now() - interval '60 minutes'
       AND m.actual_lag_s IS NOT NULL
       AND m.actual_lag_s <> 'NOT_IDENTIFIED'
) e

ORDER BY 1, 2
