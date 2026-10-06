-- READ-ONLY. P0 phantom open-position census (PAPER).
--
-- Canonical open quantity = bought - sold - authoritative settlement, per
-- (account, group_id, us_market_slug, holding_side) -- exactly
-- bettor_paper_ledger.POSITIONS_SQL. This file asks production which
-- "open" positions are fully (or almost fully) offset, and whether any fill
-- is an economic duplicate, so the reconciliation fixes the actual cause.
--
-- No writes. Every statement is a SELECT.

-- 1. Every position the ledger would call open, with its arithmetic.
WITH f AS (
    SELECT account_id, group_id, us_market_slug, holding_side,
           max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY')  AS bought,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           count(*) FILTER (WHERE direction='BUY')  AS n_buy,
           count(*) FILTER (WHERE direction='SELL') AS n_sell,
           string_agg(DISTINCT role || ':' || direction, ',') AS roles
      FROM paper_fills
     GROUP BY 1, 2, 3, 4),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty AS settled_qty,
           outcome, version
      FROM paper_settlements ORDER BY position_key, version DESC),
p AS (
    SELECT f.*, coalesce(s.settled_qty, 0) AS settled_qty, s.outcome,
           coalesce(f.bought,0) - coalesce(f.sold,0)
             - coalesce(s.settled_qty,0) AS open_qty
      FROM f LEFT JOIN s ON s.position_key =
           'paperpos:' || f.account_id || ':' || f.group_id || ':'
           || f.us_market_slug || ':' || f.holding_side)
SELECT account_id,
       count(*) FILTER (WHERE open_qty > 1e-9)                    AS ledger_open,
       count(*) FILTER (WHERE open_qty > 1e-9 AND open_qty < 1e-6) AS open_below_1e6,
       count(*) FILTER (WHERE open_qty > 1e-9 AND open_qty < 1)    AS open_below_one_contract,
       count(*) FILTER (WHERE open_qty < -1e-9)                    AS negative_open,
       count(*) FILTER (WHERE open_qty > 1e-9 AND sold > 0)        AS open_with_sales
  FROM p GROUP BY account_id ORDER BY account_id;

-- 2. The open positions that have sales or a sub-contract residual, with
--    full-precision arithmetic.
WITH f AS (
    SELECT account_id, group_id, us_market_slug, holding_side,
           max(strategy) AS strategy,
           sum(qty) FILTER (WHERE direction='BUY')  AS bought,
           sum(qty) FILTER (WHERE direction='SELL') AS sold,
           count(*) FILTER (WHERE direction='BUY')  AS n_buy,
           count(*) FILTER (WHERE direction='SELL') AS n_sell,
           string_agg(DISTINCT role || ':' || direction, ',') AS roles
      FROM paper_fills GROUP BY 1, 2, 3, 4),
s AS (
    SELECT DISTINCT ON (position_key) position_key, qty AS settled_qty
      FROM paper_settlements ORDER BY position_key, version DESC),
p AS (
    SELECT f.*, coalesce(s.settled_qty, 0) AS settled_qty,
           coalesce(f.bought,0) - coalesce(f.sold,0)
             - coalesce(s.settled_qty,0) AS open_qty
      FROM f LEFT JOIN s ON s.position_key =
           'paperpos:' || f.account_id || ':' || f.group_id || ':'
           || f.us_market_slug || ':' || f.holding_side)
SELECT account_id, group_id, left(us_market_slug, 60) AS slug, holding_side,
       strategy, bought::text, sold::text, settled_qty::text,
       open_qty::text, n_buy, n_sell, roles
  FROM p
 WHERE open_qty > 1e-9 AND (sold > 0 OR open_qty < 1)
 ORDER BY open_qty ASC
 LIMIT 120;

-- 3. Groups that hold the same market on BOTH holding sides (a sale booked
--    on the wrong side would leave both sides "open").
SELECT account_id, group_id, left(us_market_slug, 60) AS slug,
       string_agg(holding_side || ':' || direction || ':' || qty_sum::text,
                  ' ' ORDER BY holding_side, direction) AS legs
  FROM (SELECT account_id, group_id, us_market_slug, holding_side, direction,
               sum(qty) AS qty_sum
          FROM paper_fills GROUP BY 1,2,3,4,5) x
 GROUP BY 1, 2, 3
HAVING count(DISTINCT holding_side) > 1
 LIMIT 60;

-- 4. Economic duplicate fills: same order, same qty, price and instant but a
--    different fill id; or the same order filled beyond its own qty.
SELECT order_id, qty::text, price::text, filled_at, count(*) AS n,
       array_agg(fill_id) AS fill_ids
  FROM paper_fills
 GROUP BY order_id, qty, price, filled_at
HAVING count(*) > 1
 LIMIT 60;

SELECT o.order_id, o.role, o.direction, o.qty::text AS order_qty,
       o.filled_qty::text, sum(f.qty)::text AS fills_qty, count(*) AS n_fills
  FROM paper_orders o JOIN paper_fills f ON f.order_id = o.order_id
 GROUP BY o.order_id, o.role, o.direction, o.qty, o.filled_qty
HAVING sum(f.qty) > o.qty + 1e-9 OR abs(sum(f.qty) - o.filled_qty) > 1e-9
 LIMIT 60;

-- 5. SELL fills whose order belongs to a different position than the fill.
SELECT f.fill_id, f.order_id, f.role, f.group_id AS fill_group,
       o.group_id AS order_group, f.holding_side AS fill_side,
       o.holding_side AS order_side, left(f.us_market_slug,50) AS fill_slug,
       left(o.us_market_slug,50) AS order_slug
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.group_id <> o.group_id OR f.holding_side <> o.holding_side
    OR f.us_market_slug <> o.us_market_slug
 LIMIT 60;

-- 6. Positions with settlements: more than one settlement event at the
--    newest version (DISTINCT ON would pick one arbitrarily), and
--    settlements whose qty disagrees with the open qty at settlement.
SELECT position_key, version, count(*) AS n,
       array_agg(settlement_event_key) AS events,
       array_agg(qty::text) AS qtys
  FROM paper_settlements
 GROUP BY position_key, version
HAVING count(*) > 1
 LIMIT 40;

-- 7. Live STANDING_PROTECTION orders on positions whose ledger open qty is
--    <= 1e-9 (a closed position still carrying protection).
WITH f AS (
    SELECT account_id, group_id, us_market_slug, holding_side,
           coalesce(sum(qty) FILTER (WHERE direction='BUY'),0)
         - coalesce(sum(qty) FILTER (WHERE direction='SELL'),0) AS net
      FROM paper_fills GROUP BY 1,2,3,4)
SELECT o.order_id, o.state, o.group_id, left(o.us_market_slug,50) AS slug,
       o.holding_side, o.qty::text, o.filled_qty::text, f.net::text
  FROM paper_orders o
  LEFT JOIN f USING (account_id, group_id, us_market_slug, holding_side)
 WHERE o.role = 'STANDING_PROTECTION'
   AND o.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED',
                   'CANCEL_PENDING')
   AND coalesce(f.net, 0) <= 1e-9
 LIMIT 60;

-- 8. The freshness surface's own denominator vs the ledger's.
SELECT run_id, started_at, outcomes::text
  FROM paper_mark_refresh_runs ORDER BY started_at DESC LIMIT 1;
