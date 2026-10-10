-- READ-ONLY (SELECT only). Root-cause audit, group epoch-mgmt, part 1:
-- the $500,000 management epoch ledger state and the no-growth invariant.
\echo == A accounts and Day One epoch tables (absent means PR 5 migration 317 not applied)
SELECT account_id FROM paper_accounts ORDER BY 1;
SELECT to_regclass('paper_account_epochs') AS day_one_epochs, to_regclass('paper_epoch_control') AS day_one_control;

\echo == B ledger by kind, paper_acct_main
SELECT kind, count(*) AS n, round(sum(cash_delta_usd)::numeric, 6) AS cash,
       round(sum(reserved_delta_usd)::numeric, 6) AS reserved,
       min(committed_at) AS first_at, max(committed_at) AS last_at
  FROM paper_ledger WHERE account_id = 'paper_acct_main'
 GROUP BY kind ORDER BY kind;

\echo == C ledger totals
SELECT count(*) AS ledger_rows, max(seq) AS max_seq,
       round(sum(cash_delta_usd)::numeric, 6) AS cash_now,
       round(sum(reserved_delta_usd)::numeric, 6) AS reserved_now,
       sum(cash_delta_usd) FILTER (WHERE kind = 'INITIAL_FUNDING') AS funding
  FROM paper_ledger WHERE account_id = 'paper_acct_main';

\echo == D order roles and directions, all time
SELECT role, direction, count(*) AS orders, min(decided_at) AS first_at, max(decided_at) AS last_at
  FROM paper_orders WHERE account_id = 'paper_acct_main'
 GROUP BY role, direction ORDER BY 1, 2;

\echo == E fills by the role of their order and direction (a BUY fill whose order is not an ENTRY would be growth by management)
SELECT o.role, f.direction, count(*) AS fills, round(sum(f.qty)::numeric, 2) AS qty,
       min(f.recorded_at) AS first_at, max(f.recorded_at) AS last_at
  FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
 WHERE f.account_id = 'paper_acct_main'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == F oversold positions: sales above purchases per (group, slug, side)
WITH p AS (
  SELECT group_id, us_market_slug, holding_side,
         sum(CASE WHEN direction = 'BUY' THEN qty ELSE 0 END) AS bq,
         sum(CASE WHEN direction = 'SELL' THEN qty ELSE 0 END) AS sq
    FROM paper_fills WHERE account_id = 'paper_acct_main' GROUP BY 1, 2, 3)
SELECT count(*) AS positions, count(*) FILTER (WHERE sq > 0) AS with_sales,
       count(*) FILTER (WHERE sq > bq + 0.000001) AS oversold,
       max(sq - bq) AS max_excess_qty
  FROM p;

\echo == G management sale orders (EXIT, REDUCE, STANDING_PROTECTION) by state, last 14 days
SELECT role, state, count(*) AS orders, round(sum(qty)::numeric, 2) AS qty, round(sum(filled_qty)::numeric, 2) AS filled
  FROM paper_orders
 WHERE account_id = 'paper_acct_main' AND direction = 'SELL' AND decided_at >= now() - interval '14 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo == H newest fill and newest order, open positions now
SELECT (SELECT max(recorded_at) FROM paper_fills WHERE account_id = 'paper_acct_main') AS newest_fill,
       (SELECT max(decided_at) FROM paper_orders WHERE account_id = 'paper_acct_main') AS newest_order;
WITH p AS (
  SELECT group_id, us_market_slug, holding_side,
         sum(CASE WHEN direction = 'BUY' THEN qty ELSE -qty END) AS net
    FROM paper_fills WHERE account_id = 'paper_acct_main' GROUP BY 1, 2, 3)
SELECT count(*) FILTER (WHERE net > 0.000001) AS positions_with_net_qty_gt0,
       count(*) FILTER (WHERE net < -0.000001) AS positions_with_negative_net
  FROM p;
