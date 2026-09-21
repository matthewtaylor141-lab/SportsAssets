-- COHORT ACCOUNTING. The arrivals-minus-service subtraction was wrong.
--
-- WHAT I CLAIMED AND WHY IT DOES NOT FOLLOW. I took a six-hour window,
-- computed arrivals 3.286 and achieved service 2.565 follow-up reads per
-- minute, subtracted, and called the 0.721/min difference "21.9% of created
-- work never serviced". Review is right that this does not follow:
--
--   * an observation admitted at minute 355 of a 359-minute window owes
--     reads at 60s, 300s, 900s and 3600s. Three of those four are not due
--     yet. Counting them as created-and-unserviced counts work that is
--     simply in the future;
--   * reads completed INSIDE the window belong to observations admitted
--     BEFORE it, so the numerator and the denominator are not the same
--     cohort at all.
--
-- The difference of two rates measured over one window is not a
-- completion rate. A flow identity is.
--
-- THE IDENTITY THIS FILE RECONCILES, over a window [A, B]:
--
--     opening_outstanding + new_obligations
--         = completed + terminal + closing_outstanding
--
-- HOW AN OBLIGATION IS COUNTED. bettor_state_mids rows are written when a
-- read is ATTEMPTED, never pre-created as pending, so outstanding work is
-- implied by the (observation_id, horizon_s) pairs that have no row. For
-- each observation at T0 and each of the four horizons h:
--
--     due(h)         = T0 + h
--     last_chance(h) = T0 + h + tolerance(30s) + recovery(600s)
--
--     row present                   -> RESOLVED, classified by timing_class
--     no row, B <  last_chance(h)   -> PENDING   (not yet failed)
--     no row, B >= last_chance(h)   -> EXPIRED   (never read, and now
--                                                 never will be)
--
-- PENDING IS NOT MISSING. Conflating them is what produced the 21.9%.
--
-- ELIGIBLE COHORT FOR DEADLINE PERFORMANCE: only observations old enough
-- that ALL FOUR horizons have had their full chance --
-- T0 <= B - (3600 + 30 + 600) = B - 4230s. A younger observation cannot
-- fail its 3600s read yet, so including it flatters the rate.
--
-- Read only.
\echo == 0. WHAT STATUSES AND TIMING CLASSES EXIST, AND HOW MANY ==
-- Attempts, successes and retries are different things. Before any rate is
-- computed, show what the status column actually holds.
SELECT status, timing_class, count(*) AS rows,
       count(*) FILTER (WHERE read_at IS NULL) AS no_read_at,
       count(*) FILTER (WHERE actual_lag_s IS NULL) AS no_lag,
       count(*) FILTER (WHERE actual_lag_s !~ '^[0-9]+(\.[0-9]+)?$'
                          AND actual_lag_s IS NOT NULL) AS lag_unparsable
  FROM bettor_state_mids
 WHERE written_at > now() - interval '24 hours'
 GROUP BY 1, 2
 ORDER BY 3 DESC;

\echo
\echo == 1. IS ONE ROW ONE ATTEMPT, OR ONE TASK ==
-- PRIMARY KEY (observation_id, horizon_s) means a retry OVERWRITES rather
-- than appending, so the table cannot show retry counts. If that is so,
-- attempts are not recoverable from here and the report must say so
-- instead of quoting an attempt-based rate.
SELECT count(*) AS mid_rows,
       count(DISTINCT (observation_id, horizon_s)) AS distinct_tasks,
       count(*) = count(DISTINCT (observation_id, horizon_s))
           AS one_row_per_task
  FROM bettor_state_mids
 WHERE written_at > now() - interval '24 hours';

\echo
\echo == 2. THE FLOW IDENTITY OVER A SIX-HOUR WINDOW ==
WITH bounds AS (
  SELECT now() - interval '6 hours' AS a, now() AS b,
         30.0 AS tol_s, 600.0 AS recovery_s
), h AS (
  SELECT unnest(ARRAY[60, 300, 900, 3600]) AS horizon_s
), ob AS (
  SELECT o.observation_id, o.observed_at, h.horizon_s,
         o.observed_at + make_interval(secs => h.horizon_s)      AS due_at,
         o.observed_at + make_interval(
             secs => h.horizon_s + bounds.tol_s + bounds.recovery_s)
                                                                 AS last_chance
    FROM bettor_state_observations o
   CROSS JOIN h
   CROSS JOIN bounds
   WHERE o.observed_at > bounds.a - interval '3 hours'
), j AS (
  SELECT ob.*, m.written_at AS resolved_at, m.timing_class
    FROM ob
    LEFT JOIN bettor_state_mids m
           ON m.observation_id = ob.observation_id
          AND m.horizon_s = ob.horizon_s
)
SELECT
  -- outstanding at A: created before A, not resolved by A, not yet expired
  count(*) FILTER (
    WHERE observed_at < (SELECT a FROM bounds)
      AND (resolved_at IS NULL OR resolved_at >= (SELECT a FROM bounds))
      AND last_chance >= (SELECT a FROM bounds))        AS opening_outstanding,
  count(*) FILTER (
    WHERE observed_at >= (SELECT a FROM bounds)
      AND observed_at <  (SELECT b FROM bounds))        AS new_obligations,
  count(*) FILTER (
    WHERE resolved_at >= (SELECT a FROM bounds)
      AND resolved_at <  (SELECT b FROM bounds))        AS completed_in_window,
  count(*) FILTER (
    WHERE resolved_at IS NULL
      AND last_chance >= (SELECT a FROM bounds)
      AND last_chance <  (SELECT b FROM bounds))        AS expired_in_window,
  count(*) FILTER (
    WHERE resolved_at IS NULL
      AND last_chance >= (SELECT b FROM bounds))        AS closing_outstanding
  FROM j;

\echo
\echo == 3. THE SAME NUMBERS, RECONCILED, SO A GAP CANNOT HIDE ==
-- opening + new - completed - expired - closing must be 0. Anything else
-- means an obligation is being counted twice or not at all, and the
-- residual is printed rather than assumed away.
WITH bounds AS (
  SELECT now() - interval '6 hours' AS a, now() AS b,
         30.0 AS tol_s, 600.0 AS recovery_s
), h AS (
  SELECT unnest(ARRAY[60, 300, 900, 3600]) AS horizon_s
), j AS (
  SELECT o.observation_id, o.observed_at, h.horizon_s,
         o.observed_at + make_interval(
             secs => h.horizon_s + 30.0 + 600.0) AS last_chance,
         m.written_at AS resolved_at
    FROM bettor_state_observations o
   CROSS JOIN h
   CROSS JOIN bounds
    LEFT JOIN bettor_state_mids m
           ON m.observation_id = o.observation_id AND m.horizon_s = h.horizon_s
   WHERE o.observed_at > bounds.a - interval '3 hours'
), t AS (
  SELECT
    count(*) FILTER (WHERE observed_at < (SELECT a FROM bounds)
                       AND (resolved_at IS NULL
                            OR resolved_at >= (SELECT a FROM bounds))
                       AND last_chance >= (SELECT a FROM bounds)) AS opening,
    count(*) FILTER (WHERE observed_at >= (SELECT a FROM bounds)
                       AND observed_at <  (SELECT b FROM bounds))  AS new_ob,
    count(*) FILTER (WHERE resolved_at >= (SELECT a FROM bounds)
                       AND resolved_at <  (SELECT b FROM bounds))  AS done,
    count(*) FILTER (WHERE resolved_at IS NULL
                       AND last_chance >= (SELECT a FROM bounds)
                       AND last_chance <  (SELECT b FROM bounds))  AS expired,
    count(*) FILTER (WHERE resolved_at IS NULL
                       AND last_chance >= (SELECT b FROM bounds))  AS closing
    FROM j
)
SELECT opening, new_ob, done, expired, closing,
       opening + new_ob AS inflow,
       done + expired + closing AS outflow,
       (opening + new_ob) - (done + expired + closing) AS residual_must_be_0
  FROM t;

\echo
\echo == 4. DEADLINE PERFORMANCE ON AN ELIGIBLE COHORT ONLY ==
-- Only observations where every horizon has had its full chance:
-- observed_at <= now() - (3600 + 30 + 600) = now() - 4230s. Pending is
-- excluded by construction here, not by a filter that might drop it
-- silently -- an eligible observation has no pending horizons left.
WITH h AS (
  SELECT unnest(ARRAY[60, 300, 900, 3600]) AS horizon_s
), cohort AS (
  SELECT o.observation_id, o.observed_at
    FROM bettor_state_observations o
   WHERE o.observed_at <= now() - interval '4230 seconds'
     AND o.observed_at >  now() - interval '24 hours'
), j AS (
  SELECT c.observation_id, h.horizon_s, m.timing_class, m.status,
         m.written_at IS NOT NULL AS was_read
    FROM cohort c
   CROSS JOIN h
    LEFT JOIN bettor_state_mids m
           ON m.observation_id = c.observation_id AND m.horizon_s = h.horizon_s
)
SELECT horizon_s,
       count(*)                                            AS obligations,
       count(*) FILTER (WHERE was_read)                    AS read_at_all,
       count(*) FILTER (WHERE NOT was_read)                AS never_read,
       count(*) FILTER (WHERE timing_class = 'ON_TIME')        AS on_time,
       count(*) FILTER (WHERE timing_class = 'LATE_RECOVERY')  AS late,
       count(*) FILTER (WHERE timing_class = 'NOT_OBSERVABLE') AS not_obs,
       round((100.0 * count(*) FILTER (WHERE timing_class = 'ON_TIME')
              / nullif(count(*), 0))::numeric, 2)
           AS pct_on_time_of_obligations,
       round((100.0 * count(*) FILTER (WHERE timing_class = 'ON_TIME')
              / nullif(count(*) FILTER (WHERE was_read), 0))::numeric, 2)
           AS pct_on_time_of_reads_taken
  FROM j
 GROUP BY 1
 ORDER BY 1;

\echo
\echo == 5. HOW BIG IS THE ELIGIBLE COHORT, AND WHAT IS STILL PENDING ==
SELECT count(*) FILTER (WHERE observed_at <= now() - interval '4230 seconds')
           AS eligible_observations,
       count(*) FILTER (WHERE observed_at >  now() - interval '4230 seconds')
           AS too_young_to_judge,
       count(*) AS observations_24h,
       min(observed_at) AS earliest, max(observed_at) AS latest
  FROM bettor_state_observations
 WHERE observed_at > now() - interval '24 hours';

\echo
\echo == 6. THE REFUSAL BUCKETS, WITH WHAT THEY CANNOT SHOW ==
-- I wrote "at <=6 reads per tick refusals stay <=3%". My own seven-day
-- table gives 6 reads a 7.00% refusal rate. The claim contradicted the
-- evidence printed beside it and I did not notice.
--
-- Worse, the bucket is not an experiment. reads_in_tick is an OUTCOME of
-- adaptive pacing and early stopping: a tick that got refused issues
-- fewer reads and backs off, so low-read buckets are partly populated BY
-- refusals rather than being the conditions that avoided them. Nothing
-- here establishes that 7 requests CAUSE throttling or that 6 is safe.
--
-- What it can show: the joint distribution, with sample sizes, over both
-- windows, so the difference between them is visible.
SELECT win, reads_in_tick, ticks, reads_issued, rate_limited, pct_429
  FROM (
    SELECT '7d' AS win, (obs_attempted + fu_attempted) AS reads_in_tick,
           count(*) AS ticks,
           sum(obs_attempted + fu_attempted) AS reads_issued,
           sum(obs_rate_limited) AS rate_limited,
           round((100.0 * sum(obs_rate_limited)
                  / nullif(sum(obs_attempted + fu_attempted), 0))::numeric, 2)
               AS pct_429
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '7 days'
     GROUP BY 1, 2
    UNION ALL
    SELECT '6h', (obs_attempted + fu_attempted),
           count(*), sum(obs_attempted + fu_attempted),
           sum(obs_rate_limited),
           round((100.0 * sum(obs_rate_limited)
                  / nullif(sum(obs_attempted + fu_attempted), 0))::numeric, 2)
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '6 hours'
     GROUP BY 1, 2
  ) t
 ORDER BY reads_in_tick, win DESC;

\echo
\echo == 7. THE CONFOUND, MADE VISIBLE ==
-- If reads_in_tick were an independent treatment, the pacing in force
-- would not vary systematically with it. It does: a refused tick backs
-- off and issues fewer reads next time, so each bucket carries its own
-- pacing distribution. Print it, so nobody reads the bucket table as an
-- experiment.
SELECT (obs_attempted + fu_attempted) AS reads_in_tick,
       count(*) AS ticks,
       round(avg(nullif(pacing_s, '')::numeric), 3) AS avg_pacing_s,
       round(min(nullif(pacing_s, '')::numeric), 2) AS min_pacing_s,
       round(max(nullif(pacing_s, '')::numeric), 2) AS max_pacing_s,
       round(avg(obs_skipped_budget)::numeric, 2)   AS avg_skipped_budget
  FROM bettor_capture_ticks
 WHERE tick_at > now() - interval '7 days'
   AND pacing_s IS NOT NULL AND pacing_s <> ''
 GROUP BY 1
 ORDER BY 1;
