-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 14:
-- the two positions whose REDUCE order filled twice: group identity, and what each REDUCE sold against what was held.
\echo == A orders of the two repeated-reduce positions with their group
SELECT o.us_market_slug, o.holding_side, o.group_id, o.role, o.direction, o.qty, o.filled_qty, o.state, o.decided_at
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main'
   AND o.us_market_slug IN ('atc-u21eq-ita-pol-2026-10-05-ita', 'aec-npb-trge-fsh-2026-10-05')
 ORDER BY o.us_market_slug, o.decided_at;

\echo == B positions with more than one filled EXIT or REDUCE order: bought, sold by management, sold by protection
WITH f AS (
  SELECT group_id, us_market_slug, holding_side,
         sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
         sum(qty) FILTER (WHERE direction = 'SELL' AND role IN ('EXIT', 'REDUCE')) AS sold_by_management,
         sum(qty) FILTER (WHERE direction = 'SELL' AND role = 'STANDING_PROTECTION') AS sold_by_protection,
         count(DISTINCT order_id) FILTER (WHERE direction = 'SELL' AND role IN ('EXIT', 'REDUCE')) AS management_sale_orders_filled
    FROM paper_fills WHERE account_id = 'paper_acct_main' GROUP BY 1, 2, 3)
SELECT us_market_slug, holding_side, round(bought::numeric, 4) AS bought, round(sold_by_management::numeric, 4) AS sold_by_management,
       round(coalesce(sold_by_protection, 0)::numeric, 4) AS sold_by_protection, management_sale_orders_filled
  FROM f WHERE management_sale_orders_filled > 1 ORDER BY 6 DESC, 1;

\echo == C each REDUCE order that filled: quantity requested against the position held at its decision instant
SELECT o.us_market_slug, o.holding_side, o.decided_at, o.qty AS reduce_qty, o.filled_qty,
       (SELECT round(coalesce(sum(CASE WHEN f.direction = 'BUY' THEN f.qty ELSE -f.qty END), 0)::numeric, 4)
          FROM paper_fills f
         WHERE f.account_id = o.account_id AND f.group_id = o.group_id AND f.us_market_slug = o.us_market_slug
           AND f.holding_side = o.holding_side AND f.recorded_at <= o.decided_at) AS held_at_decision
  FROM paper_orders o
 WHERE o.account_id = 'paper_acct_main' AND o.role = 'REDUCE' AND o.filled_qty > 0
 ORDER BY o.decided_at;
