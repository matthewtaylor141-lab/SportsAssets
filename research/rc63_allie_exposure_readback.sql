\echo rc63 allie-exposure READBACK (SELECT only): the book and fixture exposure inputs Allie reads, before and after the fix
\echo before = the previous statements verbatim from 8b4573b5 (an ENTRY order with fills counts as open when its group has no paper_settlements row)
\echo after = open_position_canon OPEN_EXPOSURE_BOOK_SQL / OPEN_EXPOSURE_FIXTURE_SQL verbatim (open quantity = bought - sold - latest settlement qty)
\echo only the account parameter is replaced by NULL (every account) and the fixture parameter by the fixture the previous read counted the most dollars on

\echo C0 accounts holding paper fills
SELECT account_id, count(*) AS fills, count(DISTINCT group_id) AS groups FROM paper_fills GROUP BY 1 ORDER BY 2 DESC LIMIT 20;

\echo A1 BEFORE book statement verbatim: dollars counted open
SELECT coalesce(sum(o.filled_qty * o.limit_price), 0)
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id);

\echo A2 BEFORE the same predicate: groups, fixtures and dollars counted open
SELECT count(DISTINCT o.group_id) AS groups_counted_open, count(DISTINCT o.fixture) AS fixtures, coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd FROM paper_orders o WHERE o.role = 'ENTRY' AND o.filled_qty > 0 AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id);

\echo A3 BEFORE the previous fixture statement verbatim on the fixture it counted the most dollars on
SELECT count(DISTINCT o.group_id) AS n,
                      coalesce(sum(o.filled_qty * o.limit_price), 0) AS usd
                 FROM paper_orders o
                WHERE o.role = 'ENTRY' AND o.fixture = (SELECT o.fixture FROM paper_orders o WHERE o.role = 'ENTRY' AND o.filled_qty > 0 AND o.fixture IS NOT NULL AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id) GROUP BY o.fixture ORDER BY sum(o.filled_qty * o.limit_price) DESC, o.fixture LIMIT 1) AND o.filled_qty > 0
                  AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                                   WHERE s.group_id = o.group_id);

\echo B1 AFTER book statement verbatim (all accounts): dollars open
SELECT coalesce(sum(e.exposure_usd), 0) AS usd FROM (
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           b.fixture,
           c.open_qty * (b.buy_cost / nullif(b.bought, 0)) AS exposure_usd
      FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) c
      JOIN (SELECT account_id, group_id, us_market_slug, holding_side,
                   max(fixture) AS fixture,
                   sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) b
        ON b.account_id = c.account_id AND b.group_id = c.group_id
       AND b.us_market_slug = c.us_market_slug
       AND b.holding_side = c.holding_side
     WHERE (NULL::text IS NULL OR c.account_id = NULL::text)
) e;

\echo B2 AFTER the rows the statements sum, by account (no rows means nothing is open)
SELECT account_id, count(*) AS open_positions, count(DISTINCT group_id) AS open_groups, coalesce(sum(exposure_usd), 0) AS usd FROM (
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           b.fixture,
           c.open_qty * (b.buy_cost / nullif(b.bought, 0)) AS exposure_usd
      FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) c
      JOIN (SELECT account_id, group_id, us_market_slug, holding_side,
                   max(fixture) AS fixture,
                   sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) b
        ON b.account_id = c.account_id AND b.group_id = c.group_id
       AND b.us_market_slug = c.us_market_slug
       AND b.holding_side = c.holding_side
     WHERE (NULL::text IS NULL OR c.account_id = NULL::text)
) e GROUP BY account_id ORDER BY 4 DESC;

\echo B3 AFTER fixture statement verbatim on the same fixture as A3
SELECT count(DISTINCT e.group_id) AS n, coalesce(sum(e.exposure_usd), 0) AS usd FROM (
    SELECT c.account_id, c.group_id, c.us_market_slug, c.holding_side,
           b.fixture,
           c.open_qty * (b.buy_cost / nullif(b.bought, 0)) AS exposure_usd
      FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) c
      JOIN (SELECT account_id, group_id, us_market_slug, holding_side,
                   max(fixture) AS fixture,
                   sum(qty) FILTER (WHERE direction = 'BUY') AS bought,
                   sum(gross_usd + fee_usd) FILTER (WHERE direction = 'BUY')
                       AS buy_cost
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) b
        ON b.account_id = c.account_id AND b.group_id = c.group_id
       AND b.us_market_slug = c.us_market_slug
       AND b.holding_side = c.holding_side
     WHERE (NULL::text IS NULL OR c.account_id = NULL::text)
) e WHERE e.fixture = (SELECT o.fixture FROM paper_orders o WHERE o.role = 'ENTRY' AND o.filled_qty > 0 AND o.fixture IS NOT NULL AND NOT EXISTS (SELECT 1 FROM paper_settlements s WHERE s.group_id = o.group_id) GROUP BY o.fixture ORDER BY sum(o.filled_qty * o.limit_price) DESC, o.fixture LIMIT 1);

\echo C1 canonical open positions (CANONICAL_OPEN_POSITIONS_SQL verbatim): positions and open quantity
SELECT count(*) AS open_positions, coalesce(sum(open_qty), 0) AS open_qty FROM (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
      FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                   coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                       AS bought,
                   coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                       AS sold
              FROM paper_fills
             GROUP BY account_id, group_id, us_market_slug, holding_side) f
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || f.account_id || ':' || f.group_id
                            || ':' || f.us_market_slug || ':'
                            || f.holding_side
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9
) q;

\echo C2 every position with fills (account, group, market, side) by how it closed
WITH p AS (
  SELECT account_id, group_id, us_market_slug, holding_side,
         coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0) AS bought,
         coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0) AS sold
    FROM paper_fills GROUP BY 1, 2, 3, 4),
s AS (
  SELECT DISTINCT ON (position_key) position_key, qty
    FROM paper_settlements ORDER BY position_key, version DESC),
j AS (
  SELECT p.*, s.position_key AS settled_key,
         p.bought - p.sold - coalesce(s.qty, 0) AS open_qty
    FROM p LEFT JOIN s ON s.position_key = 'paperpos:' || p.account_id || ':' || p.group_id || ':' || p.us_market_slug || ':' || p.holding_side)
SELECT count(*) AS positions,
       count(*) FILTER (WHERE open_qty > 1e-9) AS open,
       count(*) FILTER (WHERE settled_key IS NOT NULL) AS with_a_settlement_row,
       count(*) FILTER (WHERE settled_key IS NULL AND open_qty <= 1e-9) AS exited_to_zero_with_no_settlement_row,
       count(DISTINCT group_id) FILTER (WHERE settled_key IS NULL AND open_qty <= 1e-9) AS groups_of_those
  FROM j;

\echo D the exposure inputs the last 10 MEASURED Allie components recorded (the previous reads)
SELECT created_at, allie->'correlation_concentration'->>'fixture_open_groups' AS fixture_open_groups, allie->'correlation_concentration'->>'fixture_open_usd' AS fixture_open_usd, allie->'correlation_concentration'->>'book_open_usd' AS book_open_usd FROM canonical_decision_intents WHERE allie->>'status' = 'MEASURED' ORDER BY created_at DESC LIMIT 10;
