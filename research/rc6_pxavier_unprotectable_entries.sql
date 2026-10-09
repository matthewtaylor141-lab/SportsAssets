-- READ-ONLY. RC6.2 lane p-xavier, rework 2 (SW-1b). Exploration refuses an
-- entry Xavier cannot protect (paper_explore.R_XAVIER_CANNOT_PROTECT); the
-- other paper entry strategies (COMPLETED_GAME, the maker, the strict
-- benchmark, Derek) did not. paper_xavier.protective_price searches 1..99
-- cents, and the sale value q x c - fee(q, c) rises with c, so a position
-- has a protective price only if selling it all at 0.99 recovers its booked
-- cost, its buy fees and 0.01 per contract. Here every PAPER entry group is
-- measured that way, by strategy: all time, the last 7 days, the groups
-- still open now, and the decisions the check examines. The sale fee is the
-- deployed taker schedule 0.0695 x q x c x (1 - c) rounded to the cent
-- (numeric round, half away from zero; the schedule rounds half even, which
-- differs only on an exact half cent). Every statement is a SELECT.

\echo S0 read instant
SELECT now() AS read_at;

\echo S1 PAPER entry groups by account and strategy: no protective price (sale of the whole entry at 0.99 after its fee is below cost + buy fees + 0.01 x qty), all time and last 7 days
WITH e AS (
  SELECT f.account_id, f.group_id, f.strategy,
         min(f.filled_at) AS first_fill,
         sum(f.qty) AS qty, sum(f.qty * f.price) AS cost,
         sum(f.fee_usd) AS fees
    FROM paper_fills f
   WHERE f.role = 'ENTRY' AND f.direction = 'BUY'
   GROUP BY 1, 2, 3),
p AS (
  SELECT e.*, e.cost / e.qty AS vwap,
         e.qty * 0.99 - round(0.0695 * e.qty * 0.99 * 0.01, 2) AS sale_at_99,
         e.cost + e.fees + 0.01 * e.qty AS need
    FROM e)
SELECT account_id, strategy, count(*) AS groups,
       count(*) FILTER (WHERE vwap >= 0.98) AS vwap_ge_098,
       count(*) FILTER (WHERE sale_at_99 < need) AS no_protective_price,
       count(*) FILTER (WHERE first_fill > now() - interval '7 days')
         AS groups_7d,
       count(*) FILTER (WHERE first_fill > now() - interval '7 days'
                          AND sale_at_99 < need) AS no_protective_price_7d,
       min(first_fill) AS first_entry, max(first_fill) AS last_entry
  FROM p GROUP BY 1, 2 ORDER BY 1, 2;

\echo S2 each group with no protective price: entry, cost basis, the best sale, whether still open (bought minus sold > 0 and no settlement row)
WITH e AS (
  SELECT f.account_id, f.group_id, f.strategy,
         min(f.us_market_slug) AS slug, min(f.holding_side) AS side,
         min(f.filled_at) AS first_fill,
         sum(f.qty) AS qty, sum(f.qty * f.price) AS cost,
         sum(f.fee_usd) AS fees
    FROM paper_fills f
   WHERE f.role = 'ENTRY' AND f.direction = 'BUY'
   GROUP BY 1, 2, 3),
p AS (
  SELECT e.*, e.cost / e.qty AS vwap,
         e.qty * 0.99 - round(0.0695 * e.qty * 0.99 * 0.01, 2) AS sale_at_99,
         e.cost + e.fees + 0.01 * e.qty AS need
    FROM e),
sold AS (
  SELECT group_id, sum(qty) AS sold_qty
    FROM paper_fills WHERE direction = 'SELL' GROUP BY 1)
SELECT p.strategy, p.group_id, p.slug, p.side, p.first_fill,
       round(p.qty, 2) AS qty, round(p.vwap, 4) AS vwap,
       round(p.cost + p.fees, 2) AS cost_basis,
       round(p.sale_at_99, 2) AS sale_at_99, round(p.need, 2) AS need,
       coalesce(s.sold_qty, 0) AS sold_qty,
       EXISTS (SELECT 1 FROM paper_settlements x
                WHERE x.group_id = p.group_id) AS settled,
       (p.qty - coalesce(s.sold_qty, 0) > 0 AND NOT EXISTS (
          SELECT 1 FROM paper_settlements x
           WHERE x.group_id = p.group_id)) AS open_now
  FROM p LEFT JOIN sold s ON s.group_id = p.group_id
 WHERE p.sale_at_99 < p.need
 ORDER BY p.first_fill DESC
 LIMIT 200;

\echo S3 PAPER groups open now (bought minus sold > 0, no settlement row) by strategy, and how many of them have no protective price
WITH e AS (
  SELECT f.account_id, f.group_id, f.strategy,
         sum(f.qty) AS qty, sum(f.qty * f.price) AS cost,
         sum(f.fee_usd) AS fees
    FROM paper_fills f
   WHERE f.role = 'ENTRY' AND f.direction = 'BUY'
   GROUP BY 1, 2, 3),
sold AS (
  SELECT group_id, sum(qty) AS sold_qty
    FROM paper_fills WHERE direction = 'SELL' GROUP BY 1),
o AS (
  SELECT e.*, e.qty - coalesce(s.sold_qty, 0) AS open_qty,
         e.qty * 0.99 - round(0.0695 * e.qty * 0.99 * 0.01, 2) AS sale_at_99,
         e.cost + e.fees + 0.01 * e.qty AS need
    FROM e LEFT JOIN sold s ON s.group_id = e.group_id
   WHERE NOT EXISTS (SELECT 1 FROM paper_settlements x
                      WHERE x.group_id = e.group_id))
SELECT account_id, strategy,
       count(*) FILTER (WHERE open_qty > 0) AS open_groups,
       count(*) FILTER (WHERE open_qty > 0 AND sale_at_99 < need)
         AS open_no_protective_price
  FROM o GROUP BY 1, 2 ORDER BY 1, 2;

\echo S4 ENTER decisions by strategy (last 7 days): all, and at a limit price of 0.98 or more (the prices where the check can refuse under the deployed schedule)
SELECT strategy, count(*) AS enter_decisions,
       count(*) FILTER (WHERE limit_price >= 0.98) AS enter_at_limit_ge_098,
       count(*) FILTER (WHERE limit_price >= 0.97) AS enter_at_limit_ge_097,
       max(limit_price) AS max_limit
  FROM paper_decisions
 WHERE verdict = 'ENTER' AND decided_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 1;

\echo S5 decisions refused XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE by strategy and day (last 7 days)
SELECT strategy, date_trunc('day', decided_at) AS day, count(*) AS refused
  FROM paper_decisions
 WHERE 'XAVIER_CANNOT_PROTECT_THIS_ENTRY_NO_PROTECTIVE_PRICE' = ANY (refusals)
   AND decided_at > now() - interval '7 days'
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo S6 the paper entry switches as they stand (the strategies whose entries the check now covers)
SELECT control_key, enabled, updated_at
  FROM paper_control
 WHERE control_key IN ('PINNACLE_COMPLETED_GAME_PAPER',
                       'PINNACLE_COMPLETED_GAME_MAKER_PAPER',
                       'PINNACLE_ONLY_PAPER_BENCHMARK',
                       'PINNACLE_EXPLORATION_PAPER',
                       'PAPER_ENTRIES:DEREK_ENTRY_POLICY_V2')
 ORDER BY 1;
