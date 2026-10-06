-- READ-ONLY. Where do canonically CLOSED paper positions still appear as
-- "open"? For every position whose canonical open qty is <= 1e-9 (fully
-- offset by sales and/or settled), list the management surfaces that still
-- carry it after its close: a Xavier review AFTER the closing fill, an open
-- agent work request, a live standing order, a mark-refresh run that still
-- planned its market, and the economic-duplicate fills that closed it.
--
-- No writes. Every statement is a SELECT.

WITH pos AS (
    SELECT f.account_id, f.group_id, f.us_market_slug, f.holding_side,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0) AS bought,
           coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0) AS sold,
           max(f.filled_at) AS last_fill_at,
           count(*) FILTER (WHERE f.direction='SELL') AS n_sell
      FROM paper_fills f
     GROUP BY 1, 2, 3, 4),
closed AS (
    SELECT p.*, coalesce(s.qty, 0) AS settled
      FROM pos p
      LEFT JOIN (SELECT DISTINCT ON (position_key) position_key, qty
                   FROM paper_settlements
                  ORDER BY position_key, version DESC) s
        ON s.position_key = 'paperpos:' || p.account_id || ':' || p.group_id
                            || ':' || p.us_market_slug || ':'
                            || p.holding_side
     WHERE p.bought - p.sold - coalesce(s.qty, 0) <= 1e-9
       AND p.sold > 0)
SELECT left(c.group_id, 40) AS group_id, left(c.us_market_slug, 40) AS slug,
       c.holding_side, c.bought::text, c.sold::text, c.settled::text,
       c.n_sell, c.last_fill_at,
       (SELECT count(*) FROM paper_xavier_reviews r
         WHERE r.group_id = c.group_id
           AND r.reviewed_at > c.last_fill_at) AS reviews_after_close,
       (SELECT max(r.reviewed_at) FROM paper_xavier_reviews r
         WHERE r.group_id = c.group_id) AS last_review_at,
       (SELECT count(*) FROM agent_work_open w
         WHERE w.group_id = c.group_id) AS open_work,
       (SELECT count(*) FROM paper_orders o
         WHERE o.group_id = c.group_id
           AND o.state IN ('PENDING_SIMULATION','RESTING',
                           'PARTIALLY_FILLED','CANCEL_PENDING'))
           AS live_orders,
       (SELECT count(*) FROM xavier_management_assessments a
         WHERE a.group_id = c.group_id
           AND a.assessed_at > c.last_fill_at) AS assessments_after_close,
       (SELECT count(*) FROM (SELECT order_id FROM paper_fills x
                               WHERE x.group_id = c.group_id
                               GROUP BY order_id, qty, price, filled_at
                              HAVING count(*) > 1) d) AS duplicate_fill_groups
  FROM closed c
 WHERE c.sold > 0
 ORDER BY c.last_fill_at DESC
 LIMIT 80;

-- The two examples named in the directive: bought 8 with an 8 sale, and
-- bought 277 with 105.81 + 105.81 + 65.38 sales -- every fill, verbatim.
SELECT f.group_id, left(f.us_market_slug, 50) AS slug, f.holding_side,
       f.role, f.direction, f.qty::text, f.price::text, f.filled_at,
       f.book_obs_id, f.order_id, f.fill_id
  FROM paper_fills f
 WHERE f.group_id IN (
        SELECT group_id FROM paper_fills
         GROUP BY group_id
        HAVING (coalesce(sum(qty) FILTER (WHERE direction='BUY'),0) = 277
                OR coalesce(sum(qty) FILTER (WHERE direction='BUY'),0) = 8)
           AND coalesce(sum(qty) FILTER (WHERE direction='SELL'),0) > 0)
 ORDER BY f.group_id, f.filled_at, f.book_obs_id
 LIMIT 80;

-- Positions the Xavier work-state / management surfaces still list as
-- current (agent_work_open) whose canonical open qty is <= 1e-9.
SELECT w.agent_id, w.position_kind, left(w.group_id, 40) AS group_id,
       w.kind, w.opened_at
  FROM agent_work_open w
 WHERE w.position_kind = 'PAPER'
   AND NOT EXISTS (
        SELECT 1 FROM paper_fills f
         WHERE f.group_id = w.group_id
         GROUP BY f.group_id, f.us_market_slug, f.holding_side
        HAVING coalesce(sum(f.qty) FILTER (WHERE f.direction='BUY'), 0)
             - coalesce(sum(f.qty) FILTER (WHERE f.direction='SELL'), 0)
             > 1e-9)
 ORDER BY w.opened_at DESC
 LIMIT 60;
