-- DID INTAKE RESUME UNDER V2?
--
-- V1 ran 12:07:48Z to ~12:18Z and wrote zero observations: its gate
-- closed on all seven of its ticks. V2 went live 12:18:44Z with the
-- measurement kept and the enforcement removed.
--
-- admission_version separates the two exactly, so this does not depend
-- on me picking a boundary timestamp correctly.
--
-- Read only.
\echo == 1. INTAKE BY SCHEDULER VERSION ==
SELECT coalesce(admission_version, 'NULL (V4, pre-admission)') AS version,
       count(*) AS ticks,
       min(tick_at) AS first_tick,
       max(tick_at) AS last_tick,
       coalesce(sum(obs_written), 0) AS obs_written,
       round(coalesce(sum(obs_written), 0)::numeric
             / nullif(count(*), 0), 2) AS obs_per_tick,
       count(*) FILTER (WHERE admission_saturated) AS saturated_ticks,
       round(avg(backlog_tasks)::numeric, 1) AS avg_backlog,
       coalesce(sum(obs_skipped_admission), 0) AS declined,
       coalesce(sum(fu_attempted), 0) AS fu_attempted,
       coalesce(sum(fu_on_time), 0) AS fu_on_time,
       coalesce(sum(fu_late), 0) AS fu_late
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '3 hours'
 GROUP BY 1
 ORDER BY first_tick;

\echo
\echo == 2. TIMING BY ERA, FROM THE READS THEMSELVES ==
SELECT era, timing_class, reads,
       round(100.0 * reads / nullif(sum(reads) OVER (PARTITION BY era), 0),
             1) AS pct
  FROM (
    SELECT CASE
             WHEN read_at >= timestamptz '2026-09-21 12:18:44+00'
               THEN 'C. V2 measure-only'
             WHEN read_at >= timestamptz '2026-09-21 12:07:34+00'
               THEN 'B. V1 gate shut'
             ELSE 'A. V4 baseline' END AS era,
           timing_class,
           count(*) AS reads
      FROM bettor_state_mids
     WHERE read_at > now() - interval '3 hours'
       AND timing_class IS NOT NULL
     GROUP BY 1, 2
  ) t
 ORDER BY era, reads DESC;

\echo
\echo == 3. ARE NEW OBSERVATIONS BEING WRITTEN RIGHT NOW? ==
SELECT date_trunc('minute', observed_at) AS minute,
       count(*) AS observations
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '25 minutes'
 GROUP BY 1
 ORDER BY 1;
