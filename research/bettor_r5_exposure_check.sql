-- R5 EXPOSURE CHECK. Read only. No writes, no order calls.
--
-- workers/whale_exits.py is registered in the running workers service
-- (workers/all.py:280), WHALE_EXIT_ENABLED defaults to "1"
-- (whale_exits.py:61), and live_executor.mirror_exit never calls
-- active_venue() -- so LIVE_TRADING_ENABLED does not gate it.
--
-- Its only residual brake is that it can sell nothing unless a FILLED
-- live_orders row exists for a rostered whale. This asks whether that
-- brake is holding. It reports inventory, not whether code is enabled.
SELECT 'A_COLUMNS' AS section, ordinal_position::text, column_name
  FROM information_schema.columns
 WHERE table_name = 'live_orders'

UNION ALL

SELECT 'B_INVENTORY', k, v FROM (
    SELECT 'TOTAL_ROWS' AS k, count(*)::text AS v FROM live_orders
    UNION ALL
    SELECT 'FILLED_ROWS', count(*)::text
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'DISTINCT_STATUSES',
           coalesce(string_agg(DISTINCT status, ' | '), 'NONE')
      FROM live_orders
) b

ORDER BY 1, 2
