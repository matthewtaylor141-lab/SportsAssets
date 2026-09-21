-- R5 EXPOSURE, AGGREGATES ONLY. Read only.
-- The detailed listing was truncated by the output head cap; these are
-- the figures themselves.
SELECT 'EXPOSURE' AS section, k, v FROM (
    SELECT 'HISTORICAL_REAL_FILLS' AS k, count(*)::text AS v
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'CURRENT_UNSETTLED_FILLED_ORDERS', count(*)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'CURRENT_OPEN_REAL_POSITIONS_DISTINCT_MARKETS',
           count(DISTINCT us_market_slug)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'CURRENT_REAL_GROSS_EXPOSURE_USD',
           coalesce(round(sum(filled_usd)::numeric, 2), 0)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'UNSETTLED_SIDES_PRESENT',
           coalesce(string_agg(DISTINCT side, ' | '), 'NONE')
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'OLDEST_UNSETTLED_PLACED_AT',
           coalesce(min(placed_at)::text, 'NONE')
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'NEWEST_UNSETTLED_PLACED_AT',
           coalesce(max(placed_at)::text, 'NONE')
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'LAST_REAL_ORDER_SUBMITTED_AT',
           coalesce(max(placed_at)::text, 'NONE') FROM live_orders
    UNION ALL
    SELECT 'LAST_REAL_FILL_PLACED_AT',
           coalesce(max(placed_at)::text, 'NONE')
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'ORDERS_PLACED_LAST_7D', count(*)::text
      FROM live_orders WHERE placed_at > now() - interval '7 days'
    UNION ALL
    SELECT 'ORDERS_PLACED_LAST_24H', count(*)::text
      FROM live_orders WHERE placed_at > now() - interval '24 hours'
) x
ORDER BY 1, 2
