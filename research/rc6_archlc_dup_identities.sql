-- READ-ONLY. RC6 lane archer-lifecycle: every fill of the P0 economic-
-- duplicate rule's groups (same order, qty, price and instant, more than
-- one fill) re-derived from the ledger, with its CANONICAL IDENTITY (account,
-- session, venue source, contract, side, position group, order) checked
-- against its order, its wire level and book observation, the crossing
-- liquidity the books showed around each step, the consumed-liquidity
-- ledger, and the position each order protected. Then the same-level refill
-- census over the WHOLE ledger (any qty, any instant), which the P0 rule's
-- same-qty / same-instant key cannot see. Every statement is a SELECT.

\echo D1 candidate fills: canonical identity of each fill against its order (ident_ok false = a fill whose identity differs from its order)
WITH g AS (
  SELECT order_id, qty, price, filled_at FROM paper_fills
   GROUP BY 1, 2, 3, 4 HAVING count(*) > 1)
SELECT f.order_id, f.fill_id, f.account_id, f.session_id, f.group_id,
       f.us_market_slug, f.holding_side, f.direction, f.role,
       f.qty, f.price, f.wire_price, f.book_obs_id,
       to_char(f.filled_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') AS filled_at,
       to_char(f.book_observed_at AT TIME ZONE 'UTC', 'HH24:MI:SS.US') AS book_obs_at,
       f.basis, f.simulator_version,
       f.idempotency_key,
       (f.account_id = o.account_id AND f.session_id = o.session_id
        AND f.group_id = o.group_id AND f.us_market_slug = o.us_market_slug
        AND f.holding_side = o.holding_side AND f.direction = o.direction
        AND f.role = o.role) AS ident_ok,
       f.idempotency_key = f.order_id || ':obs' || f.book_obs_id || ':'
                           || to_char(f.wire_price, 'FM0.000000') AS key_ok,
       f.evidence->'level' AS level,
       f.evidence->>'crossing_qty' AS crossing_qty,
       f.evidence->>'queue_ahead_before' AS q_before,
       f.evidence->>'queue_ahead_after' AS q_after,
       b.source AS obs_source, b.us_market_slug = f.us_market_slug AS obs_market_ok
  FROM paper_fills f
  JOIN g USING (order_id, qty, price, filled_at)
  JOIN paper_orders o ON o.order_id = f.order_id
  LEFT JOIN paper_book_observations b ON b.obs_id = f.book_obs_id
 ORDER BY f.filled_at DESC, f.order_id, f.qty, f.wire_price, f.book_obs_id;

\echo D2 the orders of those groups: lifecycle and quantity accounting (fills_sum must equal filled_qty)
WITH g AS (
  SELECT DISTINCT order_id FROM (
    SELECT order_id FROM paper_fills GROUP BY order_id, qty, price, filled_at
    HAVING count(*) > 1) x),
s AS (SELECT order_id, count(*) AS fills_n, sum(qty) AS fills_sum,
             count(DISTINCT book_obs_id) AS obs_n,
             count(DISTINCT filled_at) AS steps_n,
             min(filled_at) AS first_fill, max(filled_at) AS last_fill
        FROM paper_fills WHERE order_id IN (SELECT order_id FROM g)
       GROUP BY order_id)
SELECT o.order_id, o.account_id, o.session_id, o.group_id, o.role,
       o.direction, o.holding_side, o.intent, o.us_market_slug, o.order_type,
       o.time_in_force, o.allow_partial, o.qty, o.limit_price, o.wire_price,
       o.filled_qty, s.fills_sum, s.fills_n, s.obs_n, s.steps_n,
       o.state, o.terminal_reason,
       to_char(o.created_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS created,
       to_char(o.eligible_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS eligible,
       to_char(o.expires_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS expires,
       to_char(o.terminal_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS terminal,
       to_char(s.first_fill AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS first_fill,
       to_char(s.last_fill AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS last_fill,
       o.queue_ahead_qty, o.simulator_version, o.strategy,
       o.queue_basis->>'placement_obs_id' AS placement_obs,
       o.queue_basis->>'last_obs_id' AS last_obs,
       o.queue_basis ? 'seen_crossing' AS has_seen_memory
  FROM paper_orders o JOIN s USING (order_id)
 ORDER BY s.last_fill DESC;

\echo D3 their order events by kind (FILL events must equal fills)
WITH g AS (
  SELECT DISTINCT order_id FROM (
    SELECT order_id FROM paper_fills GROUP BY order_id, qty, price, filled_at
    HAVING count(*) > 1) x)
SELECT e.order_id, e.kind, e.event_source, count(*) AS n,
       to_char(min(e.at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS first_at,
       to_char(max(e.at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS last_at,
       max(e.detail->>'reason') AS a_reason
  FROM paper_order_events e WHERE e.order_id IN (SELECT order_id FROM g)
 GROUP BY 1, 2, 3 ORDER BY 1, min(e.at);

\echo D4 the crossing liquidity the books showed for each filling step: the last 10 readable observations of the market at or before the last fill observation of the step (crossing = strictly better than the limit of the order on the side the SELL consumes)
WITH steps AS (
  SELECT f.order_id, f.filled_at, max(f.book_obs_id) AS last_obs,
         min(f.book_obs_id) AS first_obs
    FROM paper_fills f
    JOIN (SELECT order_id, qty, price, filled_at FROM paper_fills
           GROUP BY 1, 2, 3, 4 HAVING count(*) > 1) g
      USING (order_id, qty, price, filled_at)
   GROUP BY 1, 2),
obs AS (
  SELECT s.order_id, s.filled_at, s.first_obs, s.last_obs, o.limit_price,
         o.holding_side, o.direction, o.eligible_at, b.*
    FROM steps s JOIN paper_orders o USING (order_id)
    CROSS JOIN LATERAL (
      SELECT obs_id, observed_at, source, error, bids, offers
        FROM paper_book_observations
       WHERE us_market_slug = o.us_market_slug AND obs_id <= s.last_obs
         AND observed_at >= o.eligible_at - interval '10 minutes'
       ORDER BY obs_id DESC LIMIT 10) b),
lv AS (
  SELECT x.order_id, x.filled_at, x.obs_id, e,
         coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price') AS px_t,
         coalesce(e->'qty'->>'value', e->>'qty', e->>'size') AS qty_t,
         x.limit_price, x.holding_side
    FROM obs x
    CROSS JOIN LATERAL jsonb_array_elements(
      CASE WHEN x.error IS NULL OR x.error = '' THEN
        CASE WHEN x.holding_side = 'SHORT' THEN coalesce(x.offers, '[]'::jsonb)
             ELSE coalesce(x.bids, '[]'::jsonb) END
      ELSE '[]'::jsonb END) e)
SELECT x.order_id,
       to_char(x.filled_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS step,
       x.limit_price AS lim, x.holding_side AS side, x.first_obs, x.last_obs,
       x.obs_id,
       to_char(x.observed_at AT TIME ZONE 'UTC', 'HH24:MI:SS.MS') AS obs_at,
       x.source, coalesce(x.error, '') <> '' AS errored,
       (SELECT string_agg(lv.px_t || 'x' || lv.qty_t, ' ' ORDER BY lv.px_t)
          FROM lv WHERE lv.order_id = x.order_id AND lv.filled_at = x.filled_at
           AND lv.obs_id = x.obs_id AND lv.px_t ~ '^[0-9]*\.?[0-9]+$'
           AND (CASE WHEN x.holding_side = 'SHORT' THEN 1 - lv.px_t::numeric
                     ELSE lv.px_t::numeric END) > x.limit_price) AS crossing,
       (SELECT count(*) FROM lv WHERE lv.order_id = x.order_id
           AND lv.filled_at = x.filled_at AND lv.obs_id = x.obs_id) AS levels
  FROM obs x
 ORDER BY x.filled_at DESC, x.order_id, x.obs_id;

\echo D5 the consumed-liquidity ledger at those observations (one row per market, side, wire, observation)
WITH g AS (
  SELECT DISTINCT f.us_market_slug, f.book_obs_id FROM paper_fills f
    JOIN (SELECT order_id, qty, price, filled_at FROM paper_fills
           GROUP BY 1, 2, 3, 4 HAVING count(*) > 1) c
      USING (order_id, qty, price, filled_at))
SELECT c.us_market_slug, c.side_consumed, c.wire_price, c.book_obs_id,
       c.displayed_qty, c.consumed_qty
  FROM paper_liquidity_consumed c JOIN g USING (us_market_slug, book_obs_id)
 ORDER BY c.book_obs_id DESC, c.wire_price;

\echo D6 the positions those orders protected: bought, sold by role, settled, canonical open (per account, group, market, side)
WITH g AS (
  SELECT DISTINCT o.account_id, o.group_id, o.us_market_slug, o.holding_side
    FROM paper_orders o WHERE o.order_id IN (
      SELECT order_id FROM paper_fills GROUP BY order_id, qty, price, filled_at
      HAVING count(*) > 1))
SELECT g.account_id, g.group_id, g.us_market_slug, g.holding_side,
       coalesce(sum(f.qty) FILTER (WHERE f.direction = 'BUY'), 0) AS bought,
       coalesce(sum(f.qty) FILTER (WHERE f.direction = 'SELL'), 0) AS sold,
       coalesce(sum(f.qty) FILTER (WHERE f.role = 'STANDING_PROTECTION'), 0) AS sold_protection,
       coalesce(sum(f.qty) FILTER (WHERE f.role IN ('EXIT', 'REDUCE')), 0) AS sold_exit_reduce,
       (SELECT s.qty FROM paper_settlements s
         WHERE s.position_key = 'paperpos:' || g.account_id || ':' || g.group_id
                                || ':' || g.us_market_slug || ':' || g.holding_side
         ORDER BY s.version DESC LIMIT 1) AS settled_qty,
       (SELECT s.outcome FROM paper_settlements s
         WHERE s.position_key = 'paperpos:' || g.account_id || ':' || g.group_id
                                || ':' || g.us_market_slug || ':' || g.holding_side
         ORDER BY s.version DESC LIMIT 1) AS outcome,
       (SELECT count(*) FROM paper_orders o2 WHERE o2.group_id = g.group_id
           AND o2.us_market_slug = g.us_market_slug
           AND o2.holding_side = g.holding_side
           AND o2.role = 'STANDING_PROTECTION') AS protective_orders
  FROM g LEFT JOIN paper_fills f
    ON f.account_id = g.account_id AND f.group_id = g.group_id
   AND f.us_market_slug = g.us_market_slug AND f.holding_side = g.holding_side
 GROUP BY 1, 2, 3, 4 ORDER BY 2, 3;

\echo D7 accounts and groups: how many distinct accounts, sessions and group prefixes the candidate fills span
WITH c AS (
  SELECT f.* FROM paper_fills f
    JOIN (SELECT order_id, qty, price, filled_at FROM paper_fills
           GROUP BY 1, 2, 3, 4 HAVING count(*) > 1) g
      USING (order_id, qty, price, filled_at))
SELECT count(*) AS fills, count(DISTINCT c.account_id) AS accounts,
       count(DISTINCT c.session_id) AS sessions,
       count(DISTINCT c.order_id) AS orders, count(DISTINCT c.group_id) AS groups,
       count(DISTINCT (c.group_id, c.us_market_slug, c.holding_side)) AS positions,
       count(DISTINCT c.us_market_slug) AS markets,
       string_agg(DISTINCT split_part(c.group_id, ':', 1), ',') AS group_kinds,
       string_agg(DISTINCT c.account_id, ',') AS account_ids,
       string_agg(DISTINCT split_part(b.source, ':', 1), ',') AS obs_sources
  FROM c LEFT JOIN paper_book_observations b ON b.obs_id = c.book_obs_id;

\echo R1 SAME-LEVEL REFILL CENSUS over the whole ledger: one order: SAME wire level filled on more than one observation, by window (producer fix 2026-10-06T05:16:06Z), same or different instant, same or different qty
WITH lv AS (
  SELECT order_id, wire_price, count(*) AS n,
         count(DISTINCT book_obs_id) AS obs_n,
         count(DISTINCT filled_at) AS instants,
         count(DISTINCT qty) AS qtys, sum(qty) AS qty_sum, max(qty) AS qty_max,
         min(filled_at) AS first_at, max(filled_at) AS last_at
    FROM paper_fills GROUP BY order_id, wire_price
   HAVING count(DISTINCT book_obs_id) > 1)
SELECT CASE WHEN last_at >= timestamptz '2026-10-06 05:16:06+00'
            THEN 'AT_OR_AFTER_FIX' ELSE 'BEFORE_FIX' END AS win,
       instants = 1 AS one_instant, qtys = 1 AS one_qty,
       count(*) AS levels, sum(n) AS fills,
       round(sum(qty_sum - qty_max), 6) AS qty_beyond_first_max,
       count(DISTINCT order_id) AS orders,
       to_char(min(first_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS first,
       to_char(max(last_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS last
  FROM lv GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo R2 the same-level refills at or after the fix, each (bounded 200): the observations of the fills, quantities, displayed sizes and instants
WITH lv AS (
  SELECT order_id, wire_price FROM paper_fills GROUP BY order_id, wire_price
   HAVING count(DISTINCT book_obs_id) > 1
      AND max(filled_at) >= timestamptz '2026-10-06 05:16:06+00')
SELECT f.order_id, f.wire_price, f.role, f.direction, f.holding_side,
       f.book_obs_id, f.qty,
       f.evidence->'level'->>'displayed' AS displayed,
       f.evidence->>'crossing_qty' AS crossing_qty,
       to_char(f.filled_at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS.MS') AS filled_at,
       f.basis, f.simulator_version
  FROM paper_fills f JOIN lv USING (order_id, wire_price)
 ORDER BY f.order_id, f.wire_price, f.book_obs_id LIMIT 200;
