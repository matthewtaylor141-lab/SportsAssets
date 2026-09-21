-- THE ECONOMIC STATE OF THE 52 FILLED ROWS. Read only.
--
-- A count of status='filled' proves historical real fills existed. It
-- does NOT prove current capital is at risk: a filled row whose market
-- later resolved is settled, and its money came back. Current exposure
-- is the subset still unsettled.
SELECT 'A_FILL_STATE' AS section, k, v FROM (
    SELECT 'HISTORICAL_REAL_FILLS' AS k, count(*)::text AS v
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'FILLED_AND_SETTLED', count(*)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NOT NULL
    UNION ALL
    SELECT 'FILLED_AND_UNSETTLED', count(*)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'EVER_FILLED_ANY_STATUS', count(*)::text
      FROM live_orders WHERE filled_usd IS NOT NULL AND filled_usd > 0
) a

UNION ALL

-- GROSS EXPOSURE is the money in unsettled filled rows.
SELECT 'B_EXPOSURE', k, v FROM (
    SELECT 'UNSETTLED_FILLED_GROSS_USD' AS k,
           coalesce(round(sum(filled_usd)::numeric, 2), 0)::text AS v
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'UNSETTLED_FILLED_SHARES',
           coalesce(round(sum(filled_shares)::numeric, 2), 0)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'UNSETTLED_DISTINCT_MARKETS',
           count(DISTINCT us_market_slug)::text
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
    UNION ALL
    SELECT 'UNSETTLED_SIDES',
           coalesce(string_agg(DISTINCT side, ' | '), 'NONE')
      FROM live_orders WHERE status = 'filled' AND settled_at IS NULL
) b

UNION ALL

-- RECENCY: when did this account last try to trade, and last fill?
SELECT 'C_RECENCY', k, v FROM (
    SELECT 'LAST_ORDER_PLACED_ANY_STATUS' AS k,
           coalesce(max(placed_at)::text, 'NONE') AS v FROM live_orders
    UNION ALL
    SELECT 'LAST_FILLED_ORDER_PLACED_AT',
           coalesce(max(placed_at)::text, 'NONE')
      FROM live_orders WHERE status = 'filled'
    UNION ALL
    SELECT 'LAST_SETTLED_AT',
           coalesce(max(settled_at)::text, 'NONE') FROM live_orders
    UNION ALL
    SELECT 'ORDERS_PLACED_LAST_7D', count(*)::text
      FROM live_orders WHERE placed_at > now() - interval '7 days'
    UNION ALL
    SELECT 'ORDERS_PLACED_LAST_24H', count(*)::text
      FROM live_orders WHERE placed_at > now() - interval '24 hours'
) c

UNION ALL

-- The unsettled rows themselves, if few enough to list.
SELECT 'D_UNSETTLED_ROWS',
       coalesce(us_market_slug, 'NULL') || ' / ' || coalesce(side, '?'),
       'filled_usd=' || coalesce(round(filled_usd::numeric, 2)::text, '-')
       || ' shares=' || coalesce(round(filled_shares::numeric, 2)::text, '-')
       || ' placed=' || coalesce(to_char(placed_at, 'YYYY-MM-DD'), '-')
       || ' whale=' || coalesce(whale_username, '-')
  FROM live_orders
 WHERE status = 'filled' AND settled_at IS NULL

ORDER BY 1, 2
