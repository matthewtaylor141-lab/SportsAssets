-- IS 86.9% A CEILING, OR AN ASSUMPTION I DRESSED AS ONE? Read only.
--
-- I wrote that a 60-second tolerance window against a 69.03-second tick
-- caps on-time service at 60/69.03 = 86.9%. That ratio is not
-- automatically a scheduling ceiling. It holds only under assumptions I
-- did not state and did not test:
--
--   A1  service is instantaneous at a tick instant
--   A2  ticks are exactly periodic at the mean gap
--   A3  a task's due moment is uniform relative to tick phase
--   A4  one tick inside the window is enough (capacity is free)
--   A5  within-tick processing costs nothing
--
-- A2 IS FALSE ON THE MEASURED DATA -- min gap 34.21s, max 92.92s -- and
-- that matters in a direction worth getting right. For a due moment t,
-- on-time service needs a tick inside [t-30, t+30]. The covered
-- fraction of the timeline is
--
--     E[min(G, 2*tolerance)] / E[G]
--
-- where G is the gap between consecutive ticks. When every gap is at
-- least 60s this reduces to 60/E[G] -- the figure I quoted. When gaps
-- vary, intervals shorter than 60s are covered in full and contribute
-- G rather than 60, so the numerator falls and the true bound is
-- BELOW 86.9%, not above it.
--
-- This computes it from the gaps themselves instead of from their mean.
\echo == 1. THE GAP DISTRIBUTION, WHICH A2 ASSUMES AWAY ==
SELECT count(*) AS gaps,
       round(avg(g)::numeric, 2) AS mean_s,
       round(stddev_samp(g)::numeric, 2) AS sd_s,
       round(min(g)::numeric, 2) AS min_s,
       round(percentile_cont(0.10) WITHIN GROUP (ORDER BY g)::numeric, 2) AS p10,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY g)::numeric, 2) AS p50,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY g)::numeric, 2) AS p90,
       round(max(g)::numeric, 2) AS max_s,
       count(*) FILTER (WHERE g < 60) AS gaps_under_60s
  FROM (SELECT EXTRACT(epoch FROM (tick_at - lag(tick_at)
                                   OVER (ORDER BY tick_at))) AS g
          FROM bettor_capture_ticks
         WHERE tick_at > now() - interval '12 hours') t
 WHERE g IS NOT NULL AND g > 0 AND g < 600;

\echo
\echo == 2. THE BOUND FROM THE GAPS, NOT FROM THEIR MEAN ==
-- E[min(G,60)] / E[G]. Compared against the 60/mean figure I quoted so
-- the size of my error is visible rather than described.
SELECT round((sum(least(g, 60.0)) / nullif(sum(g), 0) * 100)::numeric, 2)
           AS bound_pct_from_gaps,
       round((60.0 / nullif(avg(g), 0) * 100)::numeric, 2)
           AS bound_pct_i_quoted,
       round((60.0 / nullif(avg(g), 0) * 100
              - sum(least(g, 60.0)) / nullif(sum(g), 0) * 100)::numeric, 2)
           AS overstatement_pp
  FROM (SELECT EXTRACT(epoch FROM (tick_at - lag(tick_at)
                                   OVER (ORDER BY tick_at))) AS g
          FROM bettor_capture_ticks
         WHERE tick_at > now() - interval '12 hours') t
 WHERE g IS NOT NULL AND g > 0 AND g < 600;

\echo
\echo == 3. A5: WITHIN-TICK PROCESSING IS NOT FREE ==
-- A tick serves up to fu_reserve tasks in sequence. The later ones in a
-- tick are served later than the tick instant, which A5 assumes away.
-- If reads cost real time the effective window shrinks further.
SELECT round(avg(nullif(pacing_s, '')::numeric), 3) AS avg_pacing_s,
       round(avg(fu_attempted)::numeric, 2) AS avg_fu_per_tick,
       max(fu_attempted) AS max_fu_per_tick,
       round((avg(nullif(pacing_s, '')::numeric)
              * avg(fu_attempted))::numeric, 2) AS avg_serial_pacing_s
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours';

\echo
\echo == 4. THE MEASUREMENT WINDOW BEHIND THE RATE FIGURES ==
-- Definitions, so 3.106 and 2.435 can be checked rather than believed:
--   created/min = sum(obs_written) * 60 * |horizons| / span_seconds
--   served/min  = sum(fu_attempted) * 60 / span_seconds
--   span        = max(tick_at) - min(tick_at) for that version's rows
SELECT coalesce(admission_version, 'V4 (pre-admission)') AS version,
       count(*) AS ticks,
       min(tick_at) AS window_start,
       max(tick_at) AS window_end,
       round(EXTRACT(epoch FROM (max(tick_at) - min(tick_at)))::numeric, 0)
           AS span_s,
       sum(obs_written) AS obs_written,
       sum(fu_attempted) AS fu_attempted
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 5. OBSERVED THROUGHPUT vs THE BUDGET THAT WAS AVAILABLE ==
-- Observed service is not demonstrated maximum capacity. The system was
-- never run with a full queue at full budget, so what follows is an
-- upper bound on what WAS available, not a measured maximum.
--   reserve = max(1, floor(10 * min(1, 1/pacing)) / 2)
SELECT round(avg(nullif(pacing_s, '')::numeric), 3) AS avg_pacing_s,
       round(avg(greatest(1, floor(10 * least(1, 1 / nullif(
             nullif(pacing_s, '')::numeric, 0))) / 2))::numeric, 2)
           AS avg_reserve_reads_per_tick,
       round(avg(fu_attempted)::numeric, 2) AS avg_used_reads_per_tick,
       round((avg(fu_attempted) / nullif(avg(greatest(1, floor(10 * least(1,
             1 / nullif(nullif(pacing_s, '')::numeric, 0))) / 2)), 0)
              * 100)::numeric, 1) AS pct_of_reserve_used
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '12 hours'
   AND pacing_s IS NOT NULL AND pacing_s <> '';
