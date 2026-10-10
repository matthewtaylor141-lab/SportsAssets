-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 3:
-- no-growth through a sale transition (duplicate EXIT / REDUCE sales of one position)
-- and the EXIT intent state machine (migration 313).
\echo == A pairs of EXIT or REDUCE orders of the same position decided within 600 s of each other
WITH s AS (
  SELECT order_id, group_id, us_market_slug, holding_side, role, qty, filled_qty, state, decided_at,
         row_number() OVER (PARTITION BY group_id, us_market_slug, holding_side ORDER BY decided_at, order_id) AS rn
    FROM paper_orders
   WHERE account_id = 'paper_acct_main' AND role IN ('EXIT', 'REDUCE'))
SELECT a.us_market_slug, a.holding_side, a.role AS first_role, a.qty AS first_qty, a.filled_qty AS first_filled, a.state AS first_state,
       b.role AS second_role, b.qty AS second_qty, b.filled_qty AS second_filled, b.state AS second_state,
       round(extract(epoch FROM (b.decided_at - a.decided_at))::numeric, 1) AS gap_s
  FROM s a JOIN s b ON a.group_id = b.group_id AND a.us_market_slug = b.us_market_slug
                   AND a.holding_side = b.holding_side AND b.rn = a.rn + 1
 WHERE b.decided_at - a.decided_at < interval '600 seconds'
 ORDER BY a.decided_at DESC LIMIT 60;

\echo == B number of positions with more than one EXIT or REDUCE order, and with more than one that FILLED
WITH s AS (
  SELECT group_id, us_market_slug, holding_side, count(*) AS n_sales,
         count(*) FILTER (WHERE filled_qty > 0) AS n_filled
    FROM paper_orders WHERE account_id = 'paper_acct_main' AND role IN ('EXIT', 'REDUCE')
   GROUP BY 1, 2, 3)
SELECT count(*) AS positions_with_a_sale, count(*) FILTER (WHERE n_sales > 1) AS with_more_than_one_sale_order,
       count(*) FILTER (WHERE n_filled > 1) AS with_more_than_one_filled_sale
  FROM s;

\echo == C a REDUCE followed by a further sale of the same position that filled (a REDUCE that became a full EXIT)
WITH r AS (
  SELECT group_id, us_market_slug, holding_side, min(decided_at) AS first_reduce_at
    FROM paper_orders WHERE account_id = 'paper_acct_main' AND role = 'REDUCE' AND filled_qty > 0
   GROUP BY 1, 2, 3)
SELECT count(*) AS reduced_positions,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_orders o
              WHERE o.account_id = 'paper_acct_main' AND o.group_id = r.group_id AND o.us_market_slug = r.us_market_slug
                AND o.holding_side = r.holding_side AND o.role = 'EXIT' AND o.filled_qty > 0 AND o.decided_at > r.first_reduce_at
                AND o.decided_at < r.first_reduce_at + interval '1 hour')) AS exit_filled_within_1h_of_the_reduce
  FROM r;

\echo == D EXIT intents (migration 313) by state and resolution
SELECT state, resolution, count(*) AS intents, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_exit_intents WHERE account_id = 'paper_acct_main'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == E reviews that recorded a sale action, by action taken, last 14 days
SELECT action ->> 'taken' AS taken, count(*) AS reviews
  FROM paper_xavier_reviews
 WHERE account_id = 'paper_acct_main' AND reviewed_at >= now() - interval '14 days'
 GROUP BY 1 ORDER BY 2 DESC;
