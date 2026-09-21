-- IS V4 ACTUALLY RUNNING, AND IS THE CORRECTION ACTUALLY IN EFFECT?
--
-- A deploy record says a commit went live. It does not say the code
-- behaves as intended, and I have twice reported a change as effective
-- on the strength of a push. These read the running system.
--
--   A  which pacing version is writing ticks right now
--   B  the request allowance actually issued, per tick
--   C  whether in-band reads have started appearing at all
--
-- V4 deployed af6ddf1 at 2026-09-21T00:54:41Z.
-- Read only. No settlement term.

SELECT 'A_LIVE_VERSION' AS section, k, v FROM (
    SELECT 'PACING_VERSIONS_SINCE_DEPLOY' AS k,
           coalesce(string_agg(DISTINCT pacing_version, ' + '), 'NO_TICKS') AS v
      FROM bettor_capture_ticks
     WHERE tick_at > '2026-09-21T00:54:41Z'::timestamptz
    UNION ALL
    SELECT 'TICKS_SINCE_DEPLOY', count(*)::text
      FROM bettor_capture_ticks
     WHERE tick_at > '2026-09-21T00:54:41Z'::timestamptz
    UNION ALL
    SELECT 'SECONDS_SINCE_NEWEST_TICK',
           coalesce(round(extract(epoch FROM (
               now() - max(tick_at)))::numeric, 1)::text, 'NO_TICKS')
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'ROTATION_HEADS_SINCE_DEPLOY',
           coalesce(string_agg(DISTINCT
               coalesce(fu_rotation_head::text, 'NULL'), ' '), '-')
      FROM bettor_capture_ticks
     WHERE tick_at > '2026-09-21T00:54:41Z'::timestamptz
) a

UNION ALL

-- THE ALLOWANCE, as issued. obs_attempted + fu_attempted is the reads
-- the tick actually sent. Under the restored floor a maximally
-- backed-off tick sends ONE; under the old floor it sent two.
SELECT 'B_REQUESTS_PER_TICK',
       'pacing=' || pacing_s,
       'ticks=' || count(*)::text
       || ' reads_min=' || min(obs_attempted + fu_attempted)::text
       || ' reads_max=' || max(obs_attempted + fu_attempted)::text
  FROM bettor_capture_ticks
 WHERE tick_at > '2026-09-21T00:54:41Z'::timestamptz
 GROUP BY 2

UNION ALL

-- IN-BAND READS. Before V4 this was 1 in 122 over an hour. Any
-- appearance here is early evidence only; a few minutes is not a
-- window and is not reported as one.
SELECT 'C_IN_BAND_SINCE_DEPLOY', k, v FROM (
    SELECT 'READS_SINCE_DEPLOY' AS k, count(*)::text AS v
      FROM bettor_state_mids m
     WHERE m.read_at > '2026-09-21T00:54:41Z'::timestamptz
       AND m.actual_lag_s IS NOT NULL
       AND m.actual_lag_s <> 'NOT_IDENTIFIED'
    UNION ALL
    SELECT 'READS_INSIDE_BAND', count(*)::text
      FROM bettor_state_mids m
     WHERE m.read_at > '2026-09-21T00:54:41Z'::timestamptz
       AND m.actual_lag_s IS NOT NULL
       AND m.actual_lag_s <> 'NOT_IDENTIFIED'
       AND abs(m.actual_lag_s::numeric - m.horizon_s) <= 30
    UNION ALL
    SELECT 'OVERDUE_AT_READ_P50',
           coalesce(round((percentile_cont(0.5) WITHIN GROUP (
               ORDER BY m.actual_lag_s::numeric - m.horizon_s))::numeric,
               1)::text, '-')
      FROM bettor_state_mids m
     WHERE m.read_at > '2026-09-21T00:54:41Z'::timestamptz
       AND m.actual_lag_s IS NOT NULL
       AND m.actual_lag_s <> 'NOT_IDENTIFIED'
) c

ORDER BY 1, 2
