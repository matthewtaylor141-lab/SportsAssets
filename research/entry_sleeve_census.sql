-- ============================================================================
-- ENTRY-SLEEVE CENSUS (2026-09-10). What is actually stored for the
-- non-mirror, model-driven "software plays", and how each sleeve has done.
--
-- WHY A CENSUS AND NOT A REPORT ON ONE TABLE. There are several candidate
-- homes for a model-driven play and guessing which one is in use would be
-- guessing. These statements enumerate every sleeve that has rows, name it by
-- the label it writes, and only then measure it.
--
--   engine_fills  -- the INTERNAL-MODEL lane. Rows arrive by POST to
--                    /api/engine/fills behind an engine token (app.py:1254),
--                    carrying fair_value, edge, band, league, limit_price and
--                    a top-of-book snapshot. The model that computes fair
--                    value is NOT in this repository; this table is its
--                    record. If devigged Pinnacle prices drive anything here,
--                    this is where their output lands.
--   ai_trades     -- the whale-copy PAPER account (whale_username, his_price,
--                    counterfactual_pnl at his price). Model-free.
--   live_orders   -- real venue orders, labelled by whale_username. The
--                    underdog sleeve writes 'underdog' here.
--
-- P&L CONVENTION. Only rows the settlement sweep has graded carry pnl. Open
-- and unsettled rows are counted and reported apart, never folded into a
-- return. A sleeve with no graded rows gets no performance line at all.
--
-- Read-only: six SELECTs. Nothing here writes.
-- ============================================================================


\echo '== 1. DOES THE INTERNAL-MODEL LANE HAVE ANY ROWS AT ALL? =='
SELECT count(*) AS rows,
       count(*) FILTER (WHERE settled) AS settled,
       count(*) FILTER (WHERE would_fill) AS would_fill_true,
       count(DISTINCT venue) AS venues,
       count(DISTINCT league) AS leagues,
       count(DISTINCT band) AS bands,
       count(*) FILTER (WHERE fair_value IS NOT NULL) AS have_fair_value,
       count(*) FILTER (WHERE edge IS NOT NULL) AS have_edge,
       min(ts)::date AS first_day, max(ts)::date AS last_day,
       min(created_at)::date AS first_written, max(created_at)::date AS last_written
  FROM engine_fills;


\echo '== 2. INTERNAL-MODEL LANE BY VENUE / LEAGUE / BAND, with graded P&L =='
SELECT COALESCE(venue, '(null)') AS venue,
       COALESCE(league, '(null)') AS league,
       COALESCE(band, '(null)') AS band,
       count(*) AS plays,
       count(*) FILTER (WHERE would_fill) AS would_fill,
       count(*) FILTER (WHERE settled) AS settled,
       round(avg(limit_price)::numeric, 4) AS avg_limit_px,
       round(avg(fair_value)::numeric, 4) AS avg_fair_value,
       round(avg(edge)::numeric, 4) AS avg_edge,
       round(sum(size_usd)::numeric, 2) AS staked_usd,
       count(*) FILTER (WHERE settled AND pnl > 0) AS wins,
       count(*) FILTER (WHERE settled AND pnl < 0) AS losses,
       round(sum(pnl) FILTER (WHERE settled)::numeric, 2) AS pnl_usd,
       round((100.0 * sum(pnl) FILTER (WHERE settled)
              / NULLIF(sum(size_usd) FILTER (WHERE settled), 0))::numeric, 2) AS roi_pct
  FROM engine_fills
 GROUP BY ROLLUP (venue, league, band)
 ORDER BY count(*) DESC
 LIMIT 60;


\echo '== 3. INTERNAL-MODEL LANE BY MONTH -- is it still running? =='
SELECT to_char(date_trunc('month', ts), 'YYYY-MM') AS month,
       count(*) AS plays,
       count(DISTINCT date_trunc('day', ts)) AS days_with_plays,
       count(*) FILTER (WHERE settled) AS settled,
       round(sum(size_usd)::numeric, 2) AS staked_usd,
       round(sum(pnl) FILTER (WHERE settled)::numeric, 2) AS pnl_usd,
       round((100.0 * sum(pnl) FILTER (WHERE settled)
              / NULLIF(sum(size_usd) FILTER (WHERE settled), 0))::numeric, 2) AS roi_pct
  FROM engine_fills
 GROUP BY 1 ORDER BY 1;


\echo '== 4. EVERY SLEEVE THAT HAS REAL VENUE ORDERS, by its own label =='
SELECT COALESCE(whale_username, '(null)') AS sleeve,
       count(*) AS orders,
       count(*) FILTER (WHERE status = 'filled') AS filled,
       count(*) FILTER (WHERE status = 'cashed_out') AS cashed_out,
       count(*) FILTER (WHERE status = 'settled') AS settled,
       count(*) FILTER (WHERE status IN ('unfilled','rejected','error')) AS failed,
       min(placed_at)::date AS first_day, max(placed_at)::date AS last_day,
       round(sum(filled_usd)::numeric, 2) AS filled_usd,
       count(*) FILTER (WHERE pnl IS NOT NULL) AS graded,
       round(sum(pnl)::numeric, 2) AS pnl_usd,
       round((100.0 * sum(pnl) / NULLIF(sum(filled_usd) FILTER (WHERE pnl IS NOT NULL), 0))::numeric, 2)
         AS roi_pct
  FROM live_orders
 GROUP BY 1 ORDER BY 2 DESC LIMIT 30;


\echo '== 5. THE WHALE-COPY PAPER ACCOUNT (ai_trades), for contrast =='
SELECT COALESCE(whale_username, '(null)') AS whale,
       count(*) AS rows,
       count(*) FILTER (WHERE status = 'settled') AS settled,
       count(*) FILTER (WHERE status = 'open') AS still_open,
       count(*) FILTER (WHERE status = 'missed') AS missed,
       min(placed_at)::date AS first_day, max(placed_at)::date AS last_day,
       round(sum(filled_notional)::numeric, 2) AS staked_usd,
       round(sum(pnl) FILTER (WHERE status = 'settled')::numeric, 2) AS pnl_usd,
       round(sum(counterfactual_pnl) FILTER (WHERE status = 'settled')::numeric, 2) AS cf_pnl_at_his_px,
       round((100.0 * sum(pnl) FILTER (WHERE status = 'settled')
              / NULLIF(sum(filled_notional) FILTER (WHERE status = 'settled'), 0))::numeric, 2)
         AS roi_pct
  FROM ai_trades
 GROUP BY 1 ORDER BY 2 DESC LIMIT 25;


\echo '== 6. THE UNDERDOG SLEEVE MONTH BY MONTH (the cash-out yardstick) =='
SELECT to_char(date_trunc('month', placed_at), 'YYYY-MM') AS month,
       count(*) AS entries,
       count(*) FILTER (WHERE status = 'cashed_out') AS cashed_out,
       count(*) FILTER (WHERE status = 'settled') AS rode_to_settlement,
       count(*) FILTER (WHERE status IN ('unfilled','rejected','error')) AS failed,
       round((100.0 * count(*) FILTER (WHERE status = 'cashed_out')
              / NULLIF(count(*) FILTER (WHERE status IN ('cashed_out','settled')), 0))::numeric, 1)
         AS cash_out_pct,
       round(sum(filled_usd)::numeric, 2) AS filled_usd,
       round(sum(pnl)::numeric, 2) AS pnl_usd
  FROM live_orders
 WHERE whale_username = 'underdog'
 GROUP BY 1 ORDER BY 1;
