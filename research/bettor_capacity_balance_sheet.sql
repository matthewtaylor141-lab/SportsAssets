-- ONE WINDOW, ONE UNIT: ARRIVALS vs ALLOCATED CAPACITY vs ACHIEVED SERVICE.
--
-- Every previous capacity number in this session was reported in whatever
-- unit its own query happened to produce, and that is how a LEVEL (backlog
-- tasks) ended up compared against a FLOW (reads per tick) inside V1. This
-- file fixes the window first and converts everything into FOLLOW-UP READS
-- PER MINUTE before comparing anything.
--
-- ALLOCATED CAPACITY IS NOT GUESSED AND NOT A CONSTANT I CHOSE. It is the
-- collector's own budget formula, replayed against the pacing each tick
-- actually recorded. From workers/bettor_state.py:
--
--     MAX_READS_PER_TICK = MAX_MARKETS_PER_TICK + 2
--                        = ceil(40 / 5) + 2  =  8 + 2  =  10
--     total_budget = max(1, int(10 * min(1.0, 1.0 / pacing)))
--     fu_reserve   = max(1, total_budget // 2)      when total_budget > 1
--                  = 0 or 1, alternating by tick    when total_budget = 1
--     budget       = max(1, total_budget - fu_reserve)
--
-- and from bettor_state_capture.py:
--
--     HORIZONS_OBSERVABLE_S = (60, 300, 900, 3600)   -> 4 per observation
--     HORIZON_TOLERANCE_S   = 30
--
-- so one admitted observation OBLIGES FOUR follow-up reads. That factor of
-- four is the whole problem and it has to appear in the arithmetic.
--
-- The alternating branch cannot be replayed from telemetry (it depends on
-- _TICK_SEQ parity, which is not recorded), so it is reported as a range
-- 0..1 rather than averaged into a false precision.
--
-- Read only.
\echo == 0. THE WINDOW, AND WHETHER IT IS CLEAN ==
-- A window that straddles the V1 rollback describes neither era. Print the
-- versions present so purity is visible rather than assumed.
SELECT coalesce(admission_version, '(none recorded)') AS admission_version,
       pacing_version,
       count(*) AS ticks,
       min(tick_at) AS first_tick,
       max(tick_at) AS last_tick,
       round(EXTRACT(epoch FROM (max(tick_at) - min(tick_at)))::numeric / 60.0,
             2) AS span_min
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '6 hours'
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo
\echo == 1. THE BALANCE SHEET, IN FOLLOW-UP READS PER MINUTE ==
-- ARRIVALS  = observations written x 4 horizons, per minute of wall clock.
-- ALLOCATED = the fu_reserve the budget formula grants, per minute.
-- ACHIEVED  = follow-up reads actually attempted, per minute.
--
-- If ARRIVALS > ALLOCATED the queue grows without bound and no ordering
-- rule fixes it. If ACHIEVED < ALLOCATED the reserve is not even being
-- spent, and the bottleneck is elsewhere.
WITH w AS (
  SELECT *,
         nullif(pacing_s, '')::numeric AS pace
    FROM bettor_capture_ticks
   WHERE tick_at > now() - interval '6 hours'
     AND pacing_s IS NOT NULL AND pacing_s <> ''
), b AS (
  SELECT *,
         greatest(1, floor(10 * least(1.0, 1.0 / pace))::int) AS total_budget
    FROM w
), r AS (
  SELECT *,
         CASE WHEN total_budget > 1 THEN greatest(1, total_budget / 2)
              ELSE NULL END AS fu_reserve   -- NULL = the 0/1 parity branch
    FROM b
)
SELECT count(*) AS ticks,
       round(EXTRACT(epoch FROM (max(tick_at) - min(tick_at)))::numeric / 60.0,
             2) AS span_min,
       round((sum(obs_written) * 4 * 60.0
              / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0)
             )::numeric, 3) AS arrivals_fu_reads_per_min,
       round((sum(coalesce(fu_reserve, 0)) * 60.0
              / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0)
             )::numeric, 3) AS allocated_low_per_min,
       round((sum(coalesce(fu_reserve, 1)) * 60.0
              / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0)
             )::numeric, 3) AS allocated_high_per_min,
       round((sum(fu_attempted) * 60.0
              / nullif(EXTRACT(epoch FROM (max(tick_at) - min(tick_at))), 0)
             )::numeric, 3) AS achieved_fu_reads_per_min,
       count(*) FILTER (WHERE fu_reserve IS NULL) AS parity_branch_ticks,
       round(avg(total_budget)::numeric, 2)  AS avg_total_budget_reads,
       round(avg(fu_reserve)::numeric, 2)    AS avg_fu_reserve_reads,
       round(avg(pace)::numeric, 3)          AS avg_pacing_s
  FROM r;

\echo
\echo == 2. THE SAME THREE NUMBERS PER TICK, SO THE RATIO IS VISIBLE ==
WITH w AS (
  SELECT tick_at, obs_written, fu_attempted, fu_due,
         nullif(pacing_s, '')::numeric AS pace,
         obs_attempted + fu_attempted AS reads_in_tick,
         obs_rate_limited
    FROM bettor_capture_ticks
   WHERE tick_at > now() - interval '6 hours'
     AND pacing_s IS NOT NULL AND pacing_s <> ''
), b AS (
  SELECT *, greatest(1, floor(10 * least(1.0, 1.0 / pace))::int) AS total_budget
    FROM w
)
SELECT total_budget,
       count(*) AS ticks,
       CASE WHEN total_budget > 1 THEN greatest(1, total_budget / 2)::text
            ELSE '0 or 1 (parity)' END AS fu_reserve_reads,
       round(avg(obs_written * 4)::numeric, 2)  AS avg_arrivals_tasks,
       round(avg(fu_due)::numeric, 2)           AS avg_fu_due,
       round(avg(fu_attempted)::numeric, 2)     AS avg_fu_served,
       round(avg(reads_in_tick)::numeric, 2)    AS avg_reads_issued,
       sum(obs_rate_limited)                    AS rate_limited
  FROM b
 GROUP BY 1, 3
 ORDER BY 1;

\echo
\echo == 3. DEADLINE FEASIBILITY, PER HORIZON, ALL FOUR PRESERVED ==
-- The four horizons stay in the report whatever the numbers say. Dropping
-- one is a scope decision, not a reporting convenience.
SELECT horizon_s,
       count(*) AS reads,
       count(*) FILTER (WHERE timing_class = 'ON_TIME')        AS on_time,
       count(*) FILTER (WHERE timing_class = 'LATE_RECOVERY')  AS late_recovery,
       count(*) FILTER (WHERE timing_class = 'NOT_OBSERVABLE') AS not_observable,
       round((100.0 * count(*) FILTER (WHERE timing_class = 'ON_TIME')
              / nullif(count(*), 0))::numeric, 2) AS pct_on_time,
       round(percentile_cont(0.50) WITHIN GROUP (
             ORDER BY (actual_lag_s)::numeric)::numeric, 1) AS p50_lag_s
  FROM bettor_state_mids
 WHERE read_at > now() - interval '6 hours'
   AND actual_lag_s ~ '^[0-9]+(\.[0-9]+)?$'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 4. THE DENOMINATOR, STATED SEPARATELY ==
-- An on-time RATE computed only over reads that happened says nothing
-- about the reads that never happened. Tasks that expired unread are the
-- missing denominator, and they must not be quietly dropped.
SELECT count(*) AS mids_rows_in_window,
       count(*) FILTER (WHERE actual_lag_s ~ '^[0-9]+(\.[0-9]+)?$')
           AS rows_with_parsable_lag,
       count(*) FILTER (WHERE actual_lag_s IS NULL) AS rows_lag_null,
       count(*) FILTER (WHERE actual_lag_s IS NOT NULL
                          AND actual_lag_s !~ '^[0-9]+(\.[0-9]+)?$')
           AS rows_lag_unparsable
  FROM bettor_state_mids
 WHERE read_at > now() - interval '6 hours';

\echo
\echo == 5. WHAT THE GATEWAY SERVED AT EACH BURST SIZE, SAME WINDOW ==
-- The 7-day view showed refusals rising to 23.7% at 7 reads in a tick and
-- then FALLING again at 8 and 9. That non-monotonicity is real and the
-- small-sample explanation has to be visible, so print the sample size
-- beside every rate rather than the rate alone.
SELECT (obs_attempted + fu_attempted) AS reads_in_tick,
       count(*) AS ticks,
       sum(obs_attempted + fu_attempted) AS reads_issued,
       sum(obs_rate_limited) AS rate_limited,
       round((100.0 * sum(obs_rate_limited)
              / nullif(sum(obs_attempted + fu_attempted), 0))::numeric, 2)
           AS pct_429
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '6 hours'
 GROUP BY 1
 ORDER BY 1;
