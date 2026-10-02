-- Recent paper fills: strategy, role, size, cost, and the strategy limits they sit under (read-only).
\echo '== A1 · fills since 2026-10-01 23:00 by strategy and role =='
SELECT o.strategy, f.role, count(*) AS fills, sum(f.qty) AS qty,
       round(sum(f.gross_usd + f.fee_usd)::numeric, 2) AS cost_incl_fees,
       round(max(f.gross_usd + f.fee_usd)::numeric, 2) AS max_fill_cost,
       min(f.filled_at) AS first_at, max(f.filled_at) AS last_at
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.filled_at >= '2026-10-01 23:00+00'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== A2 · each fill since 23:00 =='
SELECT f.filled_at, o.strategy, f.role, f.us_market_slug, f.holding_side, f.qty, f.price,
       round((f.gross_usd + f.fee_usd)::numeric, 2) AS cost, f.group_id, o.decision_id
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.filled_at >= '2026-10-01 23:00+00'
 ORDER BY f.filled_at;

\echo '== A3 · open cost basis by strategy (entries minus exits, all time) =='
SELECT o.strategy,
       round(sum(CASE WHEN f.direction = 'BUY' THEN f.gross_usd + f.fee_usd ELSE 0 END)::numeric, 2) AS bought,
       round(sum(CASE WHEN f.direction = 'SELL' THEN f.gross_usd - f.fee_usd ELSE 0 END)::numeric, 2) AS sold_proceeds,
       count(DISTINCT f.group_id) AS groups
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 GROUP BY 1 ORDER BY 1;

\echo '== A4 · ledger tail =='
SELECT seq, kind, round(cash_delta_usd::numeric, 2) AS cash_delta,
       round(reserved_delta_usd::numeric, 2) AS reserved_delta,
       round(cash_after_usd::numeric, 2) AS cash_after,
       round(reserved_after_usd::numeric, 2) AS reserved_after, group_id, committed_at
  FROM paper_ledger ORDER BY seq DESC LIMIT 14;
