-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 11:
-- growth of a position AFTER Xavier had already sold part of it: ENTRY BUY fills that follow a
-- filled management SELL (EXIT, REDUCE, STANDING_PROTECTION) of the same group and contract.
\echo == A same group: ENTRY fills after the first management sale fill of that group, contract and side
WITH first_sale AS (
  SELECT group_id, us_market_slug, holding_side, min(recorded_at) AS first_sale_at, min(role) AS some_role
    FROM paper_fills
   WHERE account_id = 'paper_acct_main' AND direction = 'SELL' AND role IN ('EXIT', 'REDUCE', 'STANDING_PROTECTION')
   GROUP BY 1, 2, 3)
SELECT count(*) AS entry_fills_after_a_management_sale, count(DISTINCT (f.group_id, f.us_market_slug, f.holding_side)) AS positions,
       round(sum(f.qty)::numeric, 2) AS qty, min(f.recorded_at) AS first_at, max(f.recorded_at) AS last_at
  FROM paper_fills f JOIN first_sale s ON s.group_id = f.group_id AND s.us_market_slug = f.us_market_slug AND s.holding_side = f.holding_side
 WHERE f.account_id = 'paper_acct_main' AND f.direction = 'BUY' AND f.role = 'ENTRY' AND f.recorded_at > s.first_sale_at;

\echo == B the same by strategy of the entry
SELECT coalesce(o.strategy, 'NONE') AS entry_strategy, count(*) AS fills, count(DISTINCT (f.group_id, f.us_market_slug, f.holding_side)) AS positions, round(sum(f.qty)::numeric, 2) AS qty
  FROM paper_fills f
  JOIN paper_orders o ON o.order_id = f.order_id
  JOIN (SELECT group_id, us_market_slug, holding_side, min(recorded_at) AS first_sale_at
          FROM paper_fills WHERE account_id = 'paper_acct_main' AND direction = 'SELL' AND role IN ('EXIT', 'REDUCE', 'STANDING_PROTECTION')
         GROUP BY 1, 2, 3) s ON s.group_id = f.group_id AND s.us_market_slug = f.us_market_slug AND s.holding_side = f.holding_side
 WHERE f.account_id = 'paper_acct_main' AND f.direction = 'BUY' AND f.role = 'ENTRY' AND f.recorded_at > s.first_sale_at
 GROUP BY 1 ORDER BY 2 DESC;

\echo == C delay between the first management sale fill and the later ENTRY fill, in seconds (distribution)
WITH s AS (
  SELECT group_id, us_market_slug, holding_side, min(recorded_at) AS first_sale_at
    FROM paper_fills WHERE account_id = 'paper_acct_main' AND direction = 'SELL' AND role IN ('EXIT', 'REDUCE', 'STANDING_PROTECTION')
   GROUP BY 1, 2, 3),
d AS (
  SELECT extract(epoch FROM (f.recorded_at - s.first_sale_at)) AS delay_s
    FROM paper_fills f JOIN s ON s.group_id = f.group_id AND s.us_market_slug = f.us_market_slug AND s.holding_side = f.holding_side
   WHERE f.account_id = 'paper_acct_main' AND f.direction = 'BUY' AND f.role = 'ENTRY' AND f.recorded_at > s.first_sale_at)
SELECT count(*) AS n, round(min(delay_s)::numeric, 1) AS min_s, round((percentile_cont(0.5) WITHIN GROUP (ORDER BY delay_s))::numeric, 1) AS p50_s,
       round(max(delay_s)::numeric, 1) AS max_s, count(*) FILTER (WHERE delay_s <= 300) AS within_5min, count(*) FILTER (WHERE delay_s > 3600) AS after_1h
  FROM d;

\echo == D the entry order behind those fills: type and time in force (a resting remainder fills after handoff, a new order does not)
SELECT o.order_type, o.time_in_force, count(*) AS fills
  FROM paper_fills f
  JOIN paper_orders o ON o.order_id = f.order_id
  JOIN (SELECT group_id, us_market_slug, holding_side, min(recorded_at) AS first_sale_at
          FROM paper_fills WHERE account_id = 'paper_acct_main' AND direction = 'SELL' AND role IN ('EXIT', 'REDUCE', 'STANDING_PROTECTION')
         GROUP BY 1, 2, 3) s ON s.group_id = f.group_id AND s.us_market_slug = f.us_market_slug AND s.holding_side = f.holding_side
 WHERE f.account_id = 'paper_acct_main' AND f.direction = 'BUY' AND f.role = 'ENTRY' AND f.recorded_at > s.first_sale_at
 GROUP BY 1, 2 ORDER BY 3 DESC;
