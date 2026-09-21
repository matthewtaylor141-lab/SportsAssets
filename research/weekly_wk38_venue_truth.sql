-- WEEK 38 (Mon 2026-09-14 .. Sun 2026-09-20 ET) — venue-truth daily lines.
--
-- WHY SQL AND NOT THE PROBE LOGS. The weekly report has been folded from
-- engine-diagnostic log blobs. Those logs download from
-- results-receiver.actions.githubusercontent.com, which this environment's
-- egress proxy denies -- the same unreachable host the Week 36 edition
-- documented. But /api/venue-truth is served from a TABLE,
-- venue_truth_days, written by the same crawl. Reading it directly gives
-- the identical daily lines in a form that can be verified and footed
-- without parsing a masked blob through a context window.
--
-- Read only.
SELECT 'A_SHAPE' AS section, ordinal_position::text AS k, column_name AS v
  FROM information_schema.columns
 WHERE table_name = 'venue_truth_days'

UNION ALL

SELECT 'B_COVERAGE', k, v FROM (
    SELECT 'ROWS_TOTAL' AS k, count(*)::text AS v FROM venue_truth_days
    UNION ALL
    SELECT 'MIN_DAY', min(day)::text FROM venue_truth_days
    UNION ALL
    SELECT 'MAX_DAY', max(day)::text FROM venue_truth_days
    UNION ALL
    SELECT 'ROWS_IN_WEEK38', count(*)::text
      FROM venue_truth_days
     WHERE day >= '2026-09-14' AND day <= '2026-09-20'
) b

ORDER BY 1, 2
