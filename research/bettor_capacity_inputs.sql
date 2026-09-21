-- THE MEASURED INPUTS A CAPACITY MODEL NEEDS. Read only.
--
-- Every admission threshold so far has been guessed, and the last one
-- stopped data collection because it compared a queue depth (a LEVEL,
-- tasks) against a per-tick read budget (a FLOW, reads/tick). Those do
-- not have the same units and the comparison latched shut.
--
-- This measures the four quantities a correct rule actually needs, each
-- with its unit stated, so the replacement can be derived rather than
-- picked:
--
--   ARRIVALS      observations admitted per minute
--   SERVICE       follow-up reads completed per minute
--   LATENCY       what a venue read costs, which bounds reads per tick
--   DEADLINES     how much slack a horizon gives before it expires
--
-- V1 and V2 rows are separated by admission_version throughout, because
-- pooling a window where intake was zero with one where it was not
-- would flatter both.
\echo == 1. TICK CADENCE AND BUDGET, BY SCHEDULER VERSION ==
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       count(*) AS ticks,
       round(avg(EXTRACT(epoch FROM gap))::numeric, 2) AS avg_tick_gap_s,
       round(min(EXTRACT(epoch FROM gap))::numeric, 2) AS min_gap_s,
       round(max(EXTRACT(epoch FROM gap))::numeric, 2) AS max_gap_s,
       round(avg(pacing_s)::numeric, 3) AS avg_pacing_s
  FROM (SELECT admission_version, pacing_s,
               tick_at - lag(tick_at) OVER (ORDER BY tick_at) AS gap
          FROM bettor_capture_ticks
         WHERE tick_at > now() - interval '12 hours') t
 WHERE gap IS NOT NULL
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 2. ARRIVALS: OBSERVATIONS ADMITTED PER MINUTE (a FLOW) ==
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       count(*) AS ticks,
       sum(obs_scheduled) AS scheduled,
       sum(obs_written) AS written,
       round(sum(obs_written)::numeric * 60.0
             / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0),
             3) AS observations_per_min,
       round(sum(obs_written)::numeric * 60.0 * 4
             / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0),
             3) AS followup_tasks_created_per_min
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 3. SERVICE: FOLLOW-UP READS COMPLETED PER MINUTE (a FLOW) ==
-- The matching unit to section 2. If tasks created per minute exceeds
-- reads completed per minute, the queue grows without bound and no
-- ordering rule can fix it -- that is the arithmetic every previous
-- window kept rediscovering.
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       sum(fu_attempted) AS attempted,
       sum(fu_on_time) AS on_time,
       sum(fu_late) AS late,
       sum(fu_failed) AS failed,
       round(sum(fu_attempted)::numeric * 60.0
             / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0),
             3) AS followup_reads_per_min
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 4. LATENCY: WHAT A READ ACTUALLY COSTS ==
-- actual_lag_s is text; only rows that parse as a number are used, and
-- the count of those is printed so a silent drop cannot flatter it.
SELECT horizon_s,
       count(*) AS reads_with_lag,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (actual_lag_s)::numeric)::numeric, 2) AS p50_lag_s,
       round(percentile_cont(0.90) WITHIN GROUP (
             ORDER BY (actual_lag_s)::numeric)::numeric, 2) AS p90_lag_s,
       round(percentile_cont(0.99) WITHIN GROUP (
             ORDER BY (actual_lag_s)::numeric)::numeric, 2) AS p99_lag_s
  FROM bettor_state_mids
 WHERE read_at > now() - interval '12 hours'
   AND actual_lag_s ~ '^[0-9]+(\.[0-9]+)?$'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 5. WHERE THE BUDGET GOES ==
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       count(*) AS ticks,
       round(avg(obs_attempted)::numeric, 2) AS avg_obs_attempted,
       round(avg(fu_attempted)::numeric, 2) AS avg_fu_attempted,
       round(avg(obs_attempted + fu_attempted)::numeric, 2) AS avg_reads_per_tick,
       max(obs_attempted + fu_attempted) AS max_reads_per_tick,
       sum(obs_rate_limited) AS rate_limited,
       sum(obs_unreadable_other) AS unreadable
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 6. THE BACKLOG AS A LEVEL, AND HOW LONG IT WOULD TAKE TO DRAIN ==
-- ticks_to_drain = backlog / reads per tick. THIS is the quantity that
-- can be compared against a deadline, because both are times. Queue
-- depth against a per-tick rate is what latched V1 shut.
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       round(avg(backlog_tasks)::numeric, 1) AS avg_backlog_tasks,
       max(backlog_tasks) AS max_backlog_tasks,
       round(avg(fu_attempted)::numeric, 2) AS avg_reads_per_tick,
       round((avg(backlog_tasks) / nullif(avg(fu_attempted), 0))::numeric,
             2) AS ticks_to_drain,
       round((avg(backlog_tasks) / nullif(avg(fu_attempted), 0)
              * 71.4)::numeric, 1) AS seconds_to_drain_at_71s_ticks
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
   AND backlog_tasks IS NOT NULL
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 7. TIMING BY HORIZON, ALL ERAS, UNCHANGED CLASSIFICATION ==
SELECT horizon_s, timing_class, count(*) AS reads
  FROM bettor_state_mids
 WHERE read_at > now() - interval '12 hours'
 GROUP BY 1, 2
 ORDER BY 1, 3 DESC;
