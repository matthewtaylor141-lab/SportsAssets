-- IS MIGRATION 091 APPLIED, AND IS THE TICK LEDGER STILL BEING WRITTEN?
--
-- WHY THIS EXISTS. Migrations run in the API service's entrypoint; the
-- workers service runs `python -m sportsassets.workers.all` and applies
-- none. So a workers-only deploy carrying a worker that writes a new
-- column produces an INSERT against a column that does not exist --
-- and record_tick deliberately swallows every exception so telemetry
-- can never take down the capture. The failure mode is therefore
-- SILENT: every tick insert fails, the tick ledger stops growing, and
-- a window declared on top of it measures nothing while looking fine.
--
-- Run this AFTER the deploy and BEFORE the W3 window starts. Both
-- sections must pass.
--
-- Read only. No settlement term.

SELECT 'A_COLUMNS_PRESENT' AS section, column_name AS k, data_type AS v
  FROM information_schema.columns
 WHERE table_name = 'bettor_capture_ticks'
   AND column_name IN ('fu_rotation_head', 'fu_per_horizon_cap',
                       'fu_selected')

UNION ALL

-- THE LEDGER IS STILL GROWING. A present column proves the migration
-- ran; it does not prove the worker is writing. If the newest tick is
-- older than a couple of minutes, inserts are failing.
SELECT 'B_LEDGER_LIVE', k, v FROM (
    SELECT 'NEWEST_TICK_AT' AS k, max(tick_at)::text AS v
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'SECONDS_SINCE_NEWEST_TICK',
           coalesce(round(extract(epoch FROM (
               now() - max(tick_at)))::numeric, 1)::text, 'NO_TICKS')
      FROM bettor_capture_ticks
    UNION ALL
    SELECT 'TICKS_LAST_10_MIN', count(*)::text
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '10 minutes'
    UNION ALL
    SELECT 'PACING_VERSIONS_LAST_10_MIN',
           coalesce(string_agg(DISTINCT pacing_version, ' + '), '-')
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '10 minutes'
    UNION ALL
    -- The rotation head should be populated once V3 is live, and NULL
    -- only on ticks with no follow-up budget.
    SELECT 'ROTATION_HEADS_LAST_10_MIN',
           coalesce(string_agg(DISTINCT
               coalesce(fu_rotation_head::text, 'NULL'), ' '), '-')
      FROM bettor_capture_ticks
     WHERE tick_at > now() - interval '10 minutes'
) b

ORDER BY 1, 2
