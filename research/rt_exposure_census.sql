-- READ-ONLY. RED TEAM CLOSEOUT V1 -- canonical exposure census of the PAPER
-- book: open cost basis (avg cost incl. fees x open qty) plus open BUY
-- reservations, by fixture (event) and by contract side (claim), against
-- the existing single-fixture concentration cap (allie_capital
-- FIXTURE_CAP_USD = 125,000). SELECT only.
WITH f AS (
  SELECT account_id, group_id, us_market_slug, holding_side, max(fixture) fixture,
         sum(qty) FILTER (WHERE direction='BUY') bought,
         sum(gross_usd + fee_usd) FILTER (WHERE direction='BUY') buy_cost,
         coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
s AS (SELECT DISTINCT ON (position_key) position_key, qty FROM paper_settlements
       ORDER BY position_key, version DESC),
p AS (
  SELECT f.*, coalesce(s.qty, 0) settled,
         f.bought - f.sold - coalesce(s.qty, 0) open_qty,
         CASE WHEN f.bought > 0 THEN f.buy_cost / f.bought ELSE 0 END avg_cost
    FROM f LEFT JOIN s ON s.position_key = 'paperpos:' || f.account_id || ':' ||
         f.group_id || ':' || f.us_market_slug || ':' || f.holding_side),
o AS (SELECT account_id, fixture, us_market_slug, holding_side,
             sum(reserved_remaining_usd) res
        FROM paper_orders WHERE direction = 'BUY'
         AND state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED','SUBMITTED','ACKNOWLEDGED')
       GROUP BY 1, 2, 3, 4)
SELECT 'by_fixture' AS k, account_id, fixture AS key, count(*) positions,
       round(sum(open_qty * avg_cost)::numeric, 2) open_cost_usd
  FROM p WHERE open_qty > 0.000001 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 12;
WITH f AS (
  SELECT account_id, group_id, us_market_slug, holding_side, max(fixture) fixture, max(strategy) strategy,
         sum(qty) FILTER (WHERE direction='BUY') bought,
         sum(gross_usd + fee_usd) FILTER (WHERE direction='BUY') buy_cost,
         coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0) sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
s AS (SELECT DISTINCT ON (position_key) position_key, qty FROM paper_settlements
       ORDER BY position_key, version DESC),
p AS (
  SELECT f.*, f.bought - f.sold - coalesce(s.qty, 0) open_qty,
         CASE WHEN f.bought > 0 THEN f.buy_cost / f.bought ELSE 0 END avg_cost
    FROM f LEFT JOIN s ON s.position_key = 'paperpos:' || f.account_id || ':' ||
         f.group_id || ':' || f.us_market_slug || ':' || f.holding_side)
SELECT 'by_claim' AS k, account_id, us_market_slug || ':' || holding_side AS key,
       count(*) groups, count(DISTINCT strategy) strategies,
       round(sum(open_qty * avg_cost)::numeric, 2) open_cost_usd
  FROM p WHERE open_qty > 0.000001 GROUP BY 1, 2, 3 ORDER BY 6 DESC LIMIT 12;
SELECT DISTINCT state FROM paper_orders;
SELECT account_id, count(*) open_positions_approx FROM paper_fills GROUP BY 1;
