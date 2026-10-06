-- READ-ONLY. P0 PAPER position reconciliation receipt (production).
--
-- The canonical rule (backend/sportsassets/open_position_canon.py): open =
-- bought - sold - the latest settlement version's qty per (account, group,
-- market, holding side); <= 1e-9 is CLOSED. Before the P0 closeout, surfaces
-- outside the ledger re-derived "open" with their own SQL (by group only, by
-- (group, market) without the holding side, `> 0`, "any settlement in the
-- group"). This file recomputes each LEGACY formula and lists what it calls
-- open that the canonical rule does not (phantom opens), the true opens, the
-- sub-contract remainders and the economic-duplicate fill suspects.
--
-- No writes. Every statement is a SELECT.

-- 0. The canonical open positions (true opens), counted.
SELECT count(*) AS true_opens,
       count(*) FILTER (WHERE open_qty < 1) AS sub_contract_remainders
  FROM (SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
               f.bought - f.sold - coalesce(s.qty, 0) AS open_qty
          FROM (SELECT account_id, group_id, us_market_slug, holding_side,
                       coalesce(sum(qty) FILTER (WHERE direction='BUY'), 0)
                           AS bought,
                       coalesce(sum(qty) FILTER (WHERE direction='SELL'), 0)
                           AS sold
                  FROM paper_fills
                 GROUP BY account_id, group_id, us_market_slug,
                          holding_side) f
          LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                       FROM paper_settlements
                      ORDER BY position_key, version DESC) s
            ON s.position_key = 'paperpos:' || f.account_id || ':'
               || f.group_id || ':' || f.us_market_slug || ':'
               || f.holding_side) c
 WHERE open_qty > 1e-9;

-- 1. Legacy reader phantoms: what each legacy formula called open that the
--    canonical rule does not, with the affected keys.
WITH canon AS (
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9),
work_state AS (
    SELECT f.group_id, f.us_market_slug
      FROM paper_fills f
     WHERE NOT EXISTS (SELECT 1 FROM paper_settlements x
                        WHERE x.group_id = f.group_id
                          AND x.us_market_slug = f.us_market_slug)
     GROUP BY f.group_id, f.us_market_slug
    HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END)
           > 1e-9),
held_watch AS (
    SELECT f.group_id, f.us_market_slug
      FROM paper_fills f JOIN paper_handoffs h ON h.group_id = f.group_id
     WHERE NOT EXISTS (SELECT 1 FROM paper_settlements s
                        WHERE s.group_id = f.group_id
                          AND s.us_market_slug = f.us_market_slug)
     GROUP BY f.group_id, f.us_market_slug
    HAVING sum(CASE WHEN f.direction='BUY' THEN f.qty ELSE -f.qty END) > 0),
per_group AS (
    SELECT h.group_id
      FROM paper_handoffs h
     WHERE coalesce((SELECT sum(qty) FILTER (WHERE direction='BUY')
                        - coalesce(sum(qty) FILTER (WHERE direction='SELL'),
                                   0)
                       FROM paper_fills f
                      WHERE f.group_id = h.group_id), 0) > 1e-9
       AND NOT EXISTS (SELECT 1 FROM paper_settlements s
                        WHERE s.group_id = h.group_id))
SELECT 'work_state_group_market' AS legacy_reader,
       (SELECT count(*) FROM work_state) AS legacy_open,
       (SELECT count(*) FROM work_state w WHERE NOT EXISTS (
            SELECT 1 FROM canon c WHERE c.group_id = w.group_id
               AND c.us_market_slug = w.us_market_slug)) AS phantom_opens
UNION ALL
SELECT 'pinnapi_held_group_market',
       (SELECT count(*) FROM held_watch),
       (SELECT count(*) FROM held_watch w WHERE NOT EXISTS (
            SELECT 1 FROM canon c WHERE c.group_id = w.group_id
               AND c.us_market_slug = w.us_market_slug))
UNION ALL
SELECT 'per_group_any_settlement',
       (SELECT count(*) FROM per_group),
       (SELECT count(*) FROM per_group g WHERE NOT EXISTS (
            SELECT 1 FROM canon c WHERE c.group_id = g.group_id))
UNION ALL
SELECT 'canonical (every reader after the P0 closeout)',
       (SELECT count(*) FROM canon), 0;

-- 1b. The affected keys (legacy-open, canonically closed or absent), and
--     the converse (canonically open but missed by a legacy reader).
WITH canon AS (
    SELECT f.group_id, f.us_market_slug, f.holding_side,
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
     WHERE f.bought - f.sold - coalesce(s.qty, 0) > 1e-9),
per_group AS (
    SELECT h.group_id,
           coalesce((SELECT sum(qty) FILTER (WHERE direction='BUY')
                        - coalesce(sum(qty) FILTER (WHERE direction='SELL'),
                                   0)
                       FROM paper_fills f
                      WHERE f.group_id = h.group_id), 0) AS net,
           EXISTS (SELECT 1 FROM paper_settlements s
                    WHERE s.group_id = h.group_id) AS any_settled
      FROM paper_handoffs h)
SELECT 'LEGACY_OPEN_CANONICALLY_CLOSED' AS kind, g.group_id, g.net::text
  FROM per_group g
 WHERE g.net > 1e-9 AND NOT g.any_settled
   AND NOT EXISTS (SELECT 1 FROM canon c WHERE c.group_id = g.group_id)
UNION ALL
SELECT 'CANONICALLY_OPEN_LEGACY_CLOSED', g.group_id, g.net::text
  FROM per_group g
 WHERE NOT (g.net > 1e-9 AND NOT g.any_settled)
   AND EXISTS (SELECT 1 FROM canon c WHERE c.group_id = g.group_id)
 LIMIT 200;

-- 2. Economic-duplicate fill suspects (same order, price, qty, instant on
--    distinct book observations), with the extra quantity they imply.
SELECT o.role, f.direction, count(*) AS suspect_groups,
       sum(n - 1) AS extra_fills,
       round(sum(qty * (n - 1))::numeric, 6) AS extra_qty
  FROM (SELECT order_id, qty, price, filled_at, max(direction) AS direction,
               count(*) AS n
          FROM paper_fills GROUP BY order_id, qty, price, filled_at
        HAVING count(*) > 1) f
  JOIN paper_orders o ON o.order_id = f.order_id
 GROUP BY o.role, f.direction;

-- 3. Live standing protection on a canonically closed position.
SELECT count(*) AS live_protection_on_closed
  FROM paper_orders o
 WHERE o.role = 'STANDING_PROTECTION'
   AND o.state IN ('PENDING_SIMULATION','RESTING','PARTIALLY_FILLED',
                   'CANCEL_PENDING')
   AND NOT EXISTS (
        SELECT 1 FROM paper_fills f
         WHERE f.group_id = o.group_id
           AND f.us_market_slug = o.us_market_slug
           AND f.holding_side = o.holding_side
         GROUP BY f.group_id
        HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
             - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0)
             > 1e-9);
