-- Can a sleeve section be built from the database for Week 38?
-- live_orders has no created_at; find its real timestamp and P&L columns
-- before attempting any aggregate. Read only.
SELECT 'A_LIVE_ORDERS_COLS' AS section,
       lpad(ordinal_position::text, 2, '0') AS k,
       column_name || ' :: ' || data_type AS v
  FROM information_schema.columns
 WHERE table_name = 'live_orders'

UNION ALL

SELECT 'B_WHALES_COLS', lpad(ordinal_position::text, 2, '0'),
       column_name || ' :: ' || data_type
  FROM information_schema.columns
 WHERE table_name = 'whales'

ORDER BY 1, 2
