-- HAS R5 ACTED? Read only.
--
-- Eight hours elapsed with whale_exits enabled by default, its
-- allowlist populated, PMUS credentials present, the pause row absent
-- (so _is_paused returns False) and 52 live_orders rows at filled.
-- This asks whether any order row has appeared or changed since.
SELECT 'A_COLUMNS' AS section, column_name AS k, data_type AS v
  FROM information_schema.columns
 WHERE table_name = 'live_orders'
   AND (data_type LIKE '%timestamp%' OR column_name LIKE '%_at'
        OR column_name LIKE '%ts%' OR column_name = 'status'
        OR column_name = 'lane')

UNION ALL

SELECT 'B_STATUS_COUNTS', status, count(*)::text
  FROM live_orders GROUP BY status

UNION ALL

SELECT 'C_LANES', coalesce(lane, 'NULL'), count(*)::text
  FROM live_orders GROUP BY lane

ORDER BY 1, 2
