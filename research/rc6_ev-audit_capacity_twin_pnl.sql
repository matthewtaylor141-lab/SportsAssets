-- READ-ONLY. RC6 lane ev-audit: the inputs of three EV-methodology fixes.
--  1 capacity: bind evaluations vs distinct opportunities (strategy, contract,
--    held side) per size bucket, and the largest size evaluated per bucket
--  2 the 7 fresh twin orders: is there a book at/before eligible_at?
--  3 the strategy-selection study's input: realized PAPER P&L per strategy
--    per UTC entry day (Xavier value-add ACTUAL_XAVIER, FINAL), all events
-- SELECT only.

\echo == 1 capacity: evaluations vs opportunities per size bucket (14 d)
SELECT CASE WHEN qty_in <= 10 THEN '001-010' WHEN qty_in <= 50 THEN '011-050'
            WHEN qty_in <= 100 THEN '051-100' WHEN qty_in <= 500 THEN '101-500'
            ELSE '501+' END bucket,
       count(*) evaluations,
       count(DISTINCT strategy || '|' || us_market_slug || '|' || holding_side) opportunities,
       count(DISTINCT fixture) fixtures,
       max(qty_in) max_qty, min(qty_in) min_qty,
       sum(CASE WHEN qty_in <> floor(qty_in) THEN 1 ELSE 0 END) fractional_qty,
       round(sum(qty_in * market_price)::numeric, 2) capital_summed_over_evaluations
  FROM paper_profitability_evaluations
 WHERE evaluated_at > now() - interval '14 days' AND qty_in IS NOT NULL
   AND ev_per_contract_usd IS NOT NULL
 GROUP BY 1 ORDER BY 1;

\echo == 1b capital at the latest evaluation per opportunity per bucket
WITH b AS (
  SELECT CASE WHEN qty_in <= 10 THEN '001-010' WHEN qty_in <= 50 THEN '011-050'
              WHEN qty_in <= 100 THEN '051-100' WHEN qty_in <= 500 THEN '101-500'
              ELSE '501+' END bucket, *
    FROM paper_profitability_evaluations
   WHERE evaluated_at > now() - interval '14 days' AND qty_in IS NOT NULL
     AND ev_per_contract_usd IS NOT NULL),
l AS (
  SELECT DISTINCT ON (bucket, strategy, us_market_slug, holding_side)
         bucket, qty_in, market_price, all_in_ev_usd
    FROM b ORDER BY bucket, strategy, us_market_slug, holding_side, evaluated_at DESC)
SELECT bucket, count(*) opportunities,
       round(sum(qty_in * market_price)::numeric, 2) capital_latest,
       round(sum(all_in_ev_usd)::numeric, 2) net_latest
  FROM l GROUP BY 1 ORDER BY 1;

\echo == 2 the fresh twin orders: books in the window, and the last book at/before eligible_at
SELECT o.order_id, o.role, o.state, o.eligible_at,
       (SELECT count(*) FROM paper_book_observations b
         WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
           AND b.observed_at >= o.eligible_at AND b.observed_at <= o.expires_at) in_window,
       (SELECT max(p.observed_at) FROM paper_book_observations p
         WHERE p.error IS NULL AND p.us_market_slug = o.us_market_slug
           AND p.observed_at <= o.eligible_at) last_pre_activation_book,
       extract(epoch FROM o.eligible_at - (SELECT max(p.observed_at) FROM paper_book_observations p
         WHERE p.error IS NULL AND p.us_market_slug = o.us_market_slug
           AND p.observed_at <= o.eligible_at)) pre_book_age_s
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(1791384986)
 ORDER BY o.eligible_at;

\echo == 3 realized PAPER P&L per strategy per UTC entry day (value-add FINAL, latest per thesis)
WITH v AS (
  SELECT DISTINCT ON (v0.thesis_id) v0.thesis_id, v0.status,
         (v0.counterfactuals->'ACTUAL_XAVIER'->>'pnl_usd')::float8 pnl
    FROM xavier_value_add v0 ORDER BY v0.thesis_id, v0.computed_at DESC)
SELECT date_trunc('day', t.entered_at)::date d, coalesce(t.strategy, '-') strategy,
       count(*) positions, round(sum(v.pnl)::numeric, 2) pnl
  FROM xavier_entry_theses t JOIN v ON v.thesis_id = t.thesis_id
 WHERE v.status = 'FINAL' AND v.pnl IS NOT NULL
 GROUP BY 1, 2 ORDER BY 1, 2;
