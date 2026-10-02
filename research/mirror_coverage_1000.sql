-- 1:1,000 live-mirror coverage (read-only). Each paper order placed in the
-- last 7 days, scaled to qty/1000 at its own limit price, tested against
-- CANDIDATE venue rules (whole-contract quantity, 1-contract minimum,
-- $1 minimum notional). The rules are hypotheses until the fresh account's
-- own market metadata is read; nothing here is rounded.
\echo '== M0 · paper orders by role and strategy, last 7 days =='
SELECT role, strategy, count(*) AS orders,
       round(avg(qty * limit_price), 2) AS avg_paper_notional_usd,
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY qty))::numeric, 2) AS median_qty,
       round(min(qty), 4) AS min_qty, round(max(qty), 4) AS max_qty
  FROM paper_orders
 WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== M1 · scaled quantity validity under each candidate rule =='
WITH o AS (
  SELECT role, strategy, qty, limit_price, qty / 1000.0 AS live_qty,
         qty / 1000.0 * limit_price AS live_notional
    FROM paper_orders
   WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days')
SELECT role, strategy, count(*) AS orders,
       count(*) FILTER (WHERE live_qty >= 1) AS qty_ge_1,
       count(*) FILTER (WHERE live_qty >= 1 AND live_qty = floor(live_qty)) AS whole_and_ge_1,
       count(*) FILTER (WHERE live_qty * 100 = floor(live_qty * 100) AND live_qty >= 0.01) AS cent_increment_ok,
       count(*) FILTER (WHERE live_notional >= 1) AS notional_ge_1usd,
       round(avg(live_qty), 3) AS avg_live_qty,
       round(avg(live_notional), 3) AS avg_live_notional_usd
  FROM o GROUP BY 1, 2 ORDER BY 1, 2;

\echo '== M2 · quantity grid of paper orders (does sizing land on multiples of 1,000?) =='
SELECT CASE WHEN qty = floor(qty) AND (qty::numeric % 1000) = 0 THEN 'multiple of 1000'
            WHEN qty = floor(qty) THEN 'whole, not a multiple of 1000'
            ELSE 'fractional' END AS grid,
       count(*) AS orders
  FROM paper_orders
 WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;

\echo '== M3 · ten most recent orders, scaled =='
SELECT decided_at, role, strategy, us_market_slug, holding_side, state, qty, limit_price,
       round(qty / 1000.0, 6) AS live_qty, round(qty / 1000.0 * limit_price, 4) AS live_notional_usd
  FROM paper_orders
 WHERE account_id = 'paper_acct_main'
 ORDER BY decided_at DESC LIMIT 10;

\echo '== M4 · venue shape of recent paper orders (what the mirror copies) =='
SELECT role, direction, holding_side, intent, order_type, time_in_force, allow_partial,
       count(*) AS orders, count(*) FILTER (WHERE state = 'FILLED') AS filled,
       count(*) FILTER (WHERE state IN ('EXPIRED','CANCELED')) AS expired_or_cancelled,
       count(*) FILTER (WHERE state = 'PARTIALLY_FILLED') AS partial
  FROM paper_orders
 WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days'
 GROUP BY 1,2,3,4,5,6,7 ORDER BY 8 DESC;

\echo '== M5 · rounding error if live qty = round(qty/1000) (closest whole contract) =='
WITH o AS (SELECT role, qty / 1000.0 AS q FROM paper_orders
            WHERE account_id = 'paper_acct_main' AND decided_at > now() - interval '7 days')
SELECT role, count(*) AS orders,
       count(*) FILTER (WHERE round(q) = 0) AS rounds_to_zero,
       round(avg(abs(round(q) - q) / nullif(q, 0)) * 100, 1) AS mean_abs_qty_error_pct,
       round(sum(round(q)) / nullif(sum(q), 0) * 100, 2) AS aggregate_qty_ratio_pct
  FROM o GROUP BY 1 ORDER BY 1;

\echo '== M6 · paper account: funding, cash, realized P&L (sizes the live account) =='
SELECT * FROM paper_accounts WHERE account_id = 'paper_acct_main';
