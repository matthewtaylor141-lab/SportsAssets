-- DID THE CAPACITY-AWARE ADMISSION DEPLOY, AND IS PRODUCTION SHOWING IT?
--
-- cc5d3e5 shipped inside the 1fbfb26 deploy, live 2026-09-21T12:07:34Z.
-- The workers service had been running af6ddf1 (V4) since 00:54Z, so
-- every tick before 12:07:34Z is the old scheduler and every tick after
-- is the new one. That boundary is the comparison.
--
-- THE REPLAY IS NOT THE PRODUCTION RESULT. The replay drove the real
-- tick() against real PostgreSQL and completed 254/256 due tasks, all
-- on time, 0 expired. That is evidence the code does what it claims
-- against a recorded workload. It is NOT evidence that production
-- reproduced it, and this file exists so that claim gets made from
-- production rows or not at all.
--
-- Read only.
\echo == 1. IS THE NEW CODE RUNNING? admission_version is written only by cc5d3e5 ==
SELECT coalesce(admission_version, 'NULL (pre-cc5d3e5 tick)') AS admission_version,
       coalesce(pacing_version, '?') AS pacing_version,
       count(*) AS ticks,
       min(tick_at) AS first_tick,
       max(tick_at) AS last_tick
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '6 hours'
 GROUP BY 1, 2
 ORDER BY first_tick;

\echo
\echo == 2. ADMISSION BEHAVIOUR SINCE THE DEPLOY ==
SELECT count(*) AS ticks,
       count(*) FILTER (WHERE admission_saturated) AS saturated_ticks,
       round(avg(backlog_tasks)::numeric, 1) AS avg_backlog,
       max(backlog_tasks) AS max_backlog,
       round(avg(admit_cap)::numeric, 2) AS avg_admit_cap,
       coalesce(sum(obs_skipped_admission), 0) AS obs_declined_by_admission,
       coalesce(sum(obs_skipped_budget), 0) AS obs_lost_to_budget,
       coalesce(sum(obs_written), 0) AS obs_written,
       coalesce(sum(fu_due), 0) AS fu_due_sum_LEVEL_NOT_FLOW,
       coalesce(sum(fu_attempted), 0) AS fu_attempted,
       coalesce(sum(fu_on_time), 0) AS fu_on_time,
       coalesce(sum(fu_late), 0) AS fu_late,
       coalesce(sum(fu_failed), 0) AS fu_failed
  FROM bettor_capture_ticks
 WHERE admission_version IS NOT NULL;

\echo
\echo == 3. THE POINT OF THE EXERCISE: TIMING, BEFORE vs AFTER ==
-- ON_TIME is the only class admissible to the horizon gate. Rows with
-- a NULL timing_class are still in flight and are excluded from BOTH
-- numerator and denominator, so nothing pending can flatter the rate.
SELECT CASE WHEN read_at >= timestamptz '2026-09-21 12:07:34+00'
            THEN 'AFTER  (capacity-aware)'
            ELSE 'BEFORE (V4)' END AS era,
       timing_class,
       count(*) AS reads,
       round(100.0 * count(*) / nullif(sum(count(*)) OVER (
           PARTITION BY (read_at >= timestamptz '2026-09-21 12:07:34+00')
       ), 0), 1) AS pct
  FROM bettor_state_mids
 WHERE read_at > now() - interval '6 hours'
   AND timing_class IS NOT NULL
 GROUP BY 1, 2
 ORDER BY 1 DESC, reads DESC;

\echo
\echo == 4. PER-HORIZON SERVICE SINCE THE DEPLOY ==
-- W2's defect was that some horizons were never selected AT ALL. A
-- horizon missing from this list has had no read, which is the failure
-- mode, so absence is the signal to look for.
SELECT horizon_s,
       count(*) AS reads,
       count(*) FILTER (WHERE timing_class = 'TIMING_ON_TIME') AS on_time,
       count(*) FILTER (WHERE timing_class = 'TIMING_LATE_RECOVERY') AS late,
       count(*) FILTER (WHERE timing_class = 'TIMING_NOT_OBSERVABLE') AS not_obs,
       count(*) FILTER (WHERE timing_class IS NULL) AS pending
  FROM bettor_state_mids
 WHERE read_at >= timestamptz '2026-09-21 12:07:34+00'
    OR (read_at IS NULL AND written_at >= timestamptz '2026-09-21 12:07:34+00')
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 5. IS WORK STILL EXPIRING UNSERVED? ==
-- A mids row with status still pending and its horizon long past is
-- work the scheduler admitted and never completed.
SELECT status,
       count(*) AS rows,
       min(written_at) AS oldest,
       max(written_at) AS newest
  FROM bettor_state_mids
 WHERE written_at > now() - interval '3 hours'
 GROUP BY 1
 ORDER BY rows DESC;
