-- WEEK 38 (Mon 2026-09-14 .. Sun 2026-09-20 ET) — the report's figures.
-- Read only. Small aggregates only; nothing parsed from a masked blob.
SELECT 'A_DAILY' AS section, day::text AS k,
       'realized=' || round(realized::numeric, 2)::text
       || ' settled=' || settled::text
       || ' W-L=' || wins::text || '-' || losses::text
       || ' cost=' || round(cost::numeric, 2)::text
       || ' updated=' || to_char(updated_at, 'MM-DD HH24:MI') AS v
  FROM venue_truth_days
 WHERE day >= '2026-09-14' AND day <= '2026-09-20'

UNION ALL

SELECT 'B_WEEK_TOTAL', 'SEP14_TO_SEP19_6_DAYS',
       'realized=' || round(sum(realized)::numeric, 2)::text
       || ' settled=' || sum(settled)::text
       || ' W-L=' || sum(wins)::text || '-' || sum(losses)::text
       || ' cost=' || round(sum(cost)::numeric, 2)::text
  FROM venue_truth_days
 WHERE day >= '2026-09-14' AND day <= '2026-09-20'

UNION ALL

-- IS THE CRAWL ALIVE? A missing Sunday could be a gap or a dead crawl.
SELECT 'C_FRESHNESS', k, v FROM (
    SELECT 'NEWEST_ROW_DAY' AS k, max(day)::text AS v FROM venue_truth_days
    UNION ALL
    SELECT 'NEWEST_ROW_UPDATED_AT', max(updated_at)::text FROM venue_truth_days
    UNION ALL
    SELECT 'HOURS_SINCE_LAST_UPDATE',
           round(extract(epoch FROM (now() - max(updated_at)))/3600.0, 1)::text
      FROM venue_truth_days
    UNION ALL
    SELECT 'NOW', now()::text
) c

UNION ALL

-- What else is available for the sleeve and whale sections?
-- (the sleeve table is named from source, not pattern-matched: a
--  LIKE pattern spelling the word trips the read-only guard, and
--  splitting the string to slip past a safety scan is the wrong
--  instinct -- the guard is right, the query was lazy.)
SELECT 'D_TABLES', table_name, 'present'
  FROM information_schema.tables
 WHERE table_schema = 'public'
   AND (table_name LIKE '%whale%' OR table_name LIKE '%venue_truth%'
        OR table_name = 'live_orders' OR table_name = 'copy_probes')

ORDER BY 1, 2
