-- R5 EXPOSURE CHECK. Read only. No writes, no order calls.
--
-- workers/whale_exits.py is registered in the running workers service,
-- WHALE_EXIT_ENABLED defaults to "1", and live_executor.mirror_exit
-- never calls active_venue() -- so LIVE_TRADING_ENABLED does not gate
-- it. Its only residual brake is that it can sell nothing unless a
-- filled live_orders row exists for a rostered whale.
--
-- THIS QUERY ASKS WHETHER THAT BRAKE IS HOLDING. It reports whether
-- sellable inventory exists, not whether the code is enabled.
SELECT 'A_LIVE_ORDERS' AS section, k, v FROM (
    SELECT 'TOTAL_ROWS' AS k, count(*)::text AS v FROM live_orders
    UNION ALL
    SELECT 'FILLED_ROWS', count(*)::text
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'FILLED_LAST_30D', count(*)::text
      FROM live_orders WHERE status = 'filled'
       AND created_at > now() - interval '30 days'
    UNION ALL
    SELECT 'NEWEST_FILLED_AT',
           coalesce(max(created_at)::text, 'NONE')
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'NEWEST_ROW_AT', coalesce(max(created_at)::text, 'NONE')
      FROM live_orders
    UNION ALL
    SELECT 'DISTINCT_STATUSES',
           coalesce(string_agg(DISTINCT status, ' | '), 'NONE')
      FROM live_orders
) a
ORDER BY 1, 2
