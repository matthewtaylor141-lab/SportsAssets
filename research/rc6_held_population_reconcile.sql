-- READ-ONLY. RC6: the held PAPER population went from 3 open positions
-- (pm-acceptance packet, 2026-10-08 ~20:03Z) to 1 (Trader readback,
-- 2026-10-09 01:23Z). Before the smaller population is used anywhere as a
-- completeness denominator, reconcile every position that was open at the
-- packet against fills (exits), settlements and Xavier reviews: what closed
-- it, when, at what quantity and payout. Every statement is a SELECT.

\echo H1 positions per (group, contract, side) with any fill or settlement since 2026-10-08 12:00Z
WITH f AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         sum(qty) FILTER (WHERE direction = 'BUY')  AS bought,
         sum(qty) FILTER (WHERE direction = 'SELL') AS sold,
         sum(qty) FILTER (WHERE direction = 'SELL' AND filled_at >= '2026-10-08 20:00+00') AS sold_since_packet,
         min(filled_at) AS first_fill_at, max(filled_at) AS last_fill_at
    FROM paper_fills GROUP BY 1, 2, 3, 4),
s AS (
  SELECT DISTINCT ON (position_key) position_key, qty, payout_usd, outcome, version, settled_at
    FROM paper_settlements ORDER BY position_key, version DESC)
SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side, f.bought, f.sold,
       f.sold_since_packet, f.first_fill_at, f.last_fill_at,
       s.qty AS settled_qty, s.payout_usd, s.outcome, s.version AS settlement_version, s.settled_at,
       round((coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0))::numeric, 6) AS open_qty
  FROM f LEFT JOIN s ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id || ':' || f.us_market_slug || ':' || f.holding_side
 WHERE f.last_fill_at >= '2026-10-08 12:00+00' OR s.settled_at >= '2026-10-08 12:00+00'
    OR (coalesce(f.bought,0) - coalesce(f.sold,0) - coalesce(s.qty,0)) > 1e-6
 ORDER BY open_qty DESC, f.last_fill_at DESC
 LIMIT 40;

\echo H2 fills since the packet (2026-10-08 20:00Z): every exit with its order and role
SELECT f.filled_at, f.account_id, f.group_id, f.us_market_slug, f.holding_side, f.direction, f.qty,
       f.price, f.gross_usd, f.fee_usd, f.role, o.role AS order_role, o.state AS order_state, o.order_id
  FROM paper_fills f LEFT JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.filled_at >= '2026-10-08 20:00+00'
 ORDER BY f.filled_at
 LIMIT 60;

\echo H3 settlements recorded since the packet
SELECT settled_at, position_key, qty, payout_usd, outcome, version
  FROM paper_settlements WHERE settled_at >= '2026-10-08 20:00+00'
 ORDER BY settled_at LIMIT 40;

\echo H4 the three groups Xavier was reviewing: last review, recommendation, and whether it still reviews them
SELECT group_id, max(reviewed_at) AS last_review, count(*) AS reviews_since_packet,
       (array_agg(recommendation ORDER BY reviewed_at DESC))[1] AS last_recommendation,
       (array_agg(refusal ORDER BY reviewed_at DESC))[1] AS last_refusal
  FROM paper_xavier_reviews
 WHERE reviewed_at >= '2026-10-08 20:00+00'
 GROUP BY group_id ORDER BY last_review DESC;
