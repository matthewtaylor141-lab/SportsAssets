-- READ-ONLY. RC6 lane archer-lifecycle, third pass: (P) the receipt's own
-- CANONICAL_FILLS_SQL (bettor_paper_reconciliation, RC6 archer-lifecycle),
-- verbatim with the account bound to paper_acct_main, so the candidate's
-- readback can be computed before deploy; (L) the PAPER order lifecycle and
-- quantity accounting by state, role and type, protective-order linkage to
-- positions, and the Xavier review's group-wide protection sum; (V) the
-- venue-confirmed (execution mirror) rows kept apart; (A) Audrey's
-- reconciliation source and the truth quorum's inputs. Every statement is a
-- SELECT.

\echo P1 CANONICAL_FILLS_SQL rows (the receipt candidate read)

    WITH x AS (
        SELECT f.fill_id, f.order_id, f.account_id, f.group_id,
               f.us_market_slug, f.holding_side, f.direction, f.role,
               f.qty, f.price, f.wire_price, f.book_obs_id, f.filled_at,
               extract(epoch FROM f.filled_at) AS filled_epoch,
               f.evidence->'level'->>'displayed' AS displayed_recorded,
               o.order_type, o.eligible_at, o.expires_at,
               CASE WHEN o.queue_basis->>'placement_obs_id' ~ '^[0-9]+$'
                    THEN (o.queue_basis->>'placement_obs_id')::bigint
                    ELSE 0 END AS placement_obs,
               count(*) OVER (PARTITION BY f.order_id, f.wire_price)
                   AS level_fills,
               count(*) OVER (PARTITION BY f.order_id, f.qty, f.price,
                                           f.filled_at) AS former_n
          FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
         WHERE ('paper_acct_main'::text IS NULL OR f.account_id = 'paper_acct_main')),
    j AS (
        SELECT x.*, extract(epoch FROM b.observed_at) AS read_epoch,
               b.source AS read_source, rd.first_obs AS read_first_obs,
               rd.rows_n AS read_rows, pv.obs_id AS prev_obs,
               extract(epoch FROM pv.observed_at) AS prev_read_epoch,
               (SELECT max(CASE WHEN coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size') ~ '^[0-9]*\.?[0-9]+$' THEN (coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size'))::numeric END)    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN pv.offers ELSE pv.bids END) =         'array' THEN CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN pv.offers ELSE pv.bids END ELSE '[]'::jsonb END) e   WHERE coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price') ~ '^[0-9]*\.?[0-9]+$'     AND abs((coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price'))::numeric - x.wire_price) < 0.0000005)
                   AS prev_shown,
               (SELECT max(CASE WHEN coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size') ~ '^[0-9]*\.?[0-9]+$' THEN (coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size'))::numeric END)    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN b.offers ELSE b.bids END) =         'array' THEN CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN b.offers ELSE b.bids END ELSE '[]'::jsonb END) e   WHERE coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price') ~ '^[0-9]*\.?[0-9]+$'     AND abs((coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price'))::numeric - x.wire_price) < 0.0000005)
                   AS shown
          FROM x
          LEFT JOIN paper_book_observations b ON b.obs_id = x.book_obs_id
          LEFT JOIN LATERAL (
              SELECT min(b2.obs_id) AS first_obs, count(*) AS rows_n
                FROM paper_book_observations b2
               WHERE b2.us_market_slug = b.us_market_slug
                 AND b2.observed_at = b.observed_at
                 AND coalesce(b2.error, '') = '') rd ON true
          LEFT JOIN LATERAL (
              SELECT p.obs_id, p.observed_at, p.bids, p.offers
                FROM paper_book_observations p
               WHERE x.order_type = 'RESTING'
                 AND p.us_market_slug = x.us_market_slug
                 AND p.observed_at >= x.eligible_at
                 AND p.observed_at <= x.expires_at
                 AND p.obs_id < x.book_obs_id
                 AND p.obs_id > x.placement_obs
                 AND coalesce(p.error, '') = ''
               ORDER BY p.obs_id DESC LIMIT 1) pv ON true)
    SELECT fill_id, order_id, account_id, group_id, us_market_slug,
           holding_side, direction, role, order_type, qty, price,
           wire_price, book_obs_id, filled_at, filled_epoch, read_epoch,
           read_source, read_first_obs, read_rows, placement_obs, prev_obs,
           prev_read_epoch, prev_shown, shown, displayed_recorded,
           level_fills, former_n
      FROM j
     WHERE (read_first_obs IS NOT NULL AND book_obs_id <> read_first_obs)
        OR coalesce(prev_shown, 0) > 0 OR former_n > 1
     ORDER BY filled_at DESC, order_id, wire_price, book_obs_id
     LIMIT 20000;

\echo P2 the same read, timed
EXPLAIN (ANALYZE, BUFFERS, SUMMARY, TIMING OFF, COSTS OFF)

    WITH x AS (
        SELECT f.fill_id, f.order_id, f.account_id, f.group_id,
               f.us_market_slug, f.holding_side, f.direction, f.role,
               f.qty, f.price, f.wire_price, f.book_obs_id, f.filled_at,
               extract(epoch FROM f.filled_at) AS filled_epoch,
               f.evidence->'level'->>'displayed' AS displayed_recorded,
               o.order_type, o.eligible_at, o.expires_at,
               CASE WHEN o.queue_basis->>'placement_obs_id' ~ '^[0-9]+$'
                    THEN (o.queue_basis->>'placement_obs_id')::bigint
                    ELSE 0 END AS placement_obs,
               count(*) OVER (PARTITION BY f.order_id, f.wire_price)
                   AS level_fills,
               count(*) OVER (PARTITION BY f.order_id, f.qty, f.price,
                                           f.filled_at) AS former_n
          FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
         WHERE ('paper_acct_main'::text IS NULL OR f.account_id = 'paper_acct_main')),
    j AS (
        SELECT x.*, extract(epoch FROM b.observed_at) AS read_epoch,
               b.source AS read_source, rd.first_obs AS read_first_obs,
               rd.rows_n AS read_rows, pv.obs_id AS prev_obs,
               extract(epoch FROM pv.observed_at) AS prev_read_epoch,
               (SELECT max(CASE WHEN coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size') ~ '^[0-9]*\.?[0-9]+$' THEN (coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size'))::numeric END)    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN pv.offers ELSE pv.bids END) =         'array' THEN CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN pv.offers ELSE pv.bids END ELSE '[]'::jsonb END) e   WHERE coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price') ~ '^[0-9]*\.?[0-9]+$'     AND abs((coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price'))::numeric - x.wire_price) < 0.0000005)
                   AS prev_shown,
               (SELECT max(CASE WHEN coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size') ~ '^[0-9]*\.?[0-9]+$' THEN (coalesce(e->'qty'->>'value', e->>'qty', e->'size'->>'value', e->>'size'))::numeric END)    FROM jsonb_array_elements(CASE WHEN jsonb_typeof(CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN b.offers ELSE b.bids END) =         'array' THEN CASE WHEN (x.direction = 'SELL') = (x.holding_side = 'SHORT') THEN b.offers ELSE b.bids END ELSE '[]'::jsonb END) e   WHERE coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price') ~ '^[0-9]*\.?[0-9]+$'     AND abs((coalesce(e->'px'->>'value', e->>'px', e->'price'->>'value', e->>'price'))::numeric - x.wire_price) < 0.0000005)
                   AS shown
          FROM x
          LEFT JOIN paper_book_observations b ON b.obs_id = x.book_obs_id
          LEFT JOIN LATERAL (
              SELECT min(b2.obs_id) AS first_obs, count(*) AS rows_n
                FROM paper_book_observations b2
               WHERE b2.us_market_slug = b.us_market_slug
                 AND b2.observed_at = b.observed_at
                 AND coalesce(b2.error, '') = '') rd ON true
          LEFT JOIN LATERAL (
              SELECT p.obs_id, p.observed_at, p.bids, p.offers
                FROM paper_book_observations p
               WHERE x.order_type = 'RESTING'
                 AND p.us_market_slug = x.us_market_slug
                 AND p.observed_at >= x.eligible_at
                 AND p.observed_at <= x.expires_at
                 AND p.obs_id < x.book_obs_id
                 AND p.obs_id > x.placement_obs
                 AND coalesce(p.error, '') = ''
               ORDER BY p.obs_id DESC LIMIT 1) pv ON true)
    SELECT fill_id, order_id, account_id, group_id, us_market_slug,
           holding_side, direction, role, order_type, qty, price,
           wire_price, book_obs_id, filled_at, filled_epoch, read_epoch,
           read_source, read_first_obs, read_rows, placement_obs, prev_obs,
           prev_read_epoch, prev_shown, shown, displayed_recorded,
           level_fills, former_n
      FROM j
     WHERE (read_first_obs IS NOT NULL AND book_obs_id <> read_first_obs)
        OR coalesce(prev_shown, 0) > 0 OR former_n > 1
     ORDER BY filled_at DESC, order_id, wire_price, book_obs_id
     LIMIT 20000;

\echo L1 PAPER orders by role, type and state: quantities, fills, terminal stamps and reservation left
SELECT o.role, o.order_type, o.state, count(*) AS orders,
       round(sum(o.qty), 2) AS qty, round(sum(o.filled_qty), 2) AS filled,
       count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill,
       count(*) FILTER (WHERE o.filled_qty >= o.qty) AS complete,
       count(*) FILTER (WHERE o.terminal_at IS NULL) AS no_terminal_at,
       count(*) FILTER (WHERE o.reserved_remaining_usd > 0) AS reservation_left,
       count(*) FILTER (WHERE o.expires_at < now()) AS past_expiry,
       max(o.terminal_reason) AS a_reason
  FROM paper_orders o
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo L2 quantity accounting breaks (each must be 0): fills sum differs from filled_qty; FILLED not complete; REJECTED or PENDING with a fill; RESTING with a fill; PARTIALLY_FILLED at 0 or complete; terminal without terminal_at; open with terminal_at
WITH s AS (SELECT order_id, sum(qty) AS q, count(*) AS n FROM paper_fills GROUP BY 1)
SELECT count(*) FILTER (WHERE abs(coalesce(s.q, 0) - o.filled_qty) > 1e-6) AS fills_ne_filled_qty,
       count(*) FILTER (WHERE o.state = 'FILLED' AND o.filled_qty < o.qty - 1e-6) AS filled_not_complete,
       count(*) FILTER (WHERE o.state IN ('REJECTED', 'PENDING_SIMULATION') AND o.filled_qty > 0) AS rejected_or_pending_with_fill,
       count(*) FILTER (WHERE o.state = 'RESTING' AND o.filled_qty > 0) AS resting_with_fill,
       count(*) FILTER (WHERE o.state = 'PARTIALLY_FILLED' AND (o.filled_qty <= 0 OR o.filled_qty >= o.qty)) AS partial_bad,
       count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED', 'REJECTED') AND o.terminal_at IS NULL) AS terminal_no_stamp,
       count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING', 'PARTIALLY_FILLED', 'CANCEL_PENDING') AND o.terminal_at IS NOT NULL) AS open_with_stamp,
       count(*) FILTER (WHERE o.state IN ('EXPIRED', 'CANCELED', 'REJECTED', 'FILLED') AND o.reserved_remaining_usd > 0) AS terminal_reservation_left,
       count(*) AS orders
  FROM paper_orders o LEFT JOIN s USING (order_id);

\echo L3 order events against the order (each must be 0): FILL events differ from fills; terminal order without its terminal event; order without SUBMITTED
WITH e AS (SELECT order_id, count(*) FILTER (WHERE kind = 'FILL') AS fill_ev,
                  bool_or(kind = 'SUBMITTED') AS submitted,
                  bool_or(kind = 'EXPIRED') AS expired_ev,
                  bool_or(kind = 'CANCELED') AS canceled_ev,
                  bool_or(kind = 'REJECTED') AS rejected_ev
             FROM paper_order_events GROUP BY 1),
     s AS (SELECT order_id, count(*) AS n FROM paper_fills GROUP BY 1)
SELECT count(*) FILTER (WHERE coalesce(e.fill_ev, 0) <> coalesce(s.n, 0)) AS fill_events_ne_fills,
       count(*) FILTER (WHERE o.state = 'EXPIRED' AND NOT coalesce(e.expired_ev, false)) AS expired_no_event,
       count(*) FILTER (WHERE o.state = 'CANCELED' AND NOT coalesce(e.canceled_ev, false)) AS canceled_no_event,
       count(*) FILTER (WHERE o.state = 'REJECTED' AND NOT coalesce(e.rejected_ev, false)) AS rejected_no_event,
       count(*) FILTER (WHERE NOT coalesce(e.submitted, false)) AS no_submitted_event,
       count(*) AS orders
  FROM paper_orders o LEFT JOIN e USING (order_id) LEFT JOIN s USING (order_id);

\echo L4 protective orders and their positions: linked (a BUY fill on the same account, group, market and side), states, and EXPIRED or CANCELED with a partial fill (its filled part was protection, its remainder never)
SELECT o.role, o.state,
       count(*) AS orders,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM paper_fills f
                 WHERE f.account_id = o.account_id AND f.group_id = o.group_id
                   AND f.us_market_slug = o.us_market_slug
                   AND f.holding_side = o.holding_side AND f.direction = 'BUY'))
         AS linked_to_a_position,
       count(*) FILTER (WHERE o.filled_qty > 0 AND o.filled_qty < o.qty) AS partial_fill,
       round(sum(o.filled_qty), 2) AS filled, round(sum(o.qty - o.filled_qty), 2) AS unfilled
  FROM paper_orders o WHERE o.role IN ('STANDING_PROTECTION', 'HEDGE', 'EXIT', 'REDUCE')
 GROUP BY 1, 2 ORDER BY 1, 2;

\echo L5 protective orders past their GTD expiry still RESTING or PARTIALLY_FILLED now (they protect nothing until the simulator terminates them)
SELECT count(*) AS orders, round(max(extract(epoch FROM now() - expires_at))::numeric, 1) AS max_late_s
  FROM paper_orders WHERE role = 'STANDING_PROTECTION'
   AND state IN ('RESTING', 'PARTIALLY_FILLED') AND expires_at < now();

\echo L6 groups holding more than one position, and Xavier reviews (7 d) whose confirmed_protection (summed over the GROUP) differs from the protection filled on the reviewed position
WITH pos AS (SELECT account_id, group_id, count(DISTINCT (us_market_slug, holding_side)) AS n
               FROM paper_fills GROUP BY 1, 2)
SELECT (SELECT count(*) FROM pos WHERE n > 1) AS multi_position_groups,
       (SELECT count(*) FROM pos) AS groups,
       (SELECT count(*) FROM paper_xavier_reviews r
          JOIN pos p ON p.account_id = r.account_id AND p.group_id = r.group_id AND p.n > 1
         WHERE r.reviewed_at > now() - interval '7 days') AS reviews_of_multi_position_groups_7d,
       (SELECT count(*) FROM paper_xavier_reviews r
         WHERE r.reviewed_at > now() - interval '7 days') AS reviews_7d;

\echo V1 venue-confirmed rows (execution mirror and Kalshi intents) by state, apart from PAPER
SELECT 'execmirror_orders' AS src, state, count(*) AS n,
       count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS with_venue_id,
       to_char(max(created_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI') AS newest
  FROM execmirror_orders GROUP BY 1, 2
UNION ALL
SELECT 'execmirror_fills', 'ALL', count(*), count(*), to_char(max(observed_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI')
  FROM execmirror_fills
UNION ALL
SELECT 'kalshi_live_intents', state, count(*), 0, to_char(max(created_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI')
  FROM kalshi_live_intents GROUP BY 1, 2
ORDER BY 1, 2;

\echo A1 Audrey reconciliation rows by status: newest, oldest, within 15 min and 24 h, and the universe she reads (14 d)
SELECT status, count(*) AS groups,
       to_char(max(reconciled_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS newest,
       to_char(min(reconciled_at) AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS oldest,
       count(*) FILTER (WHERE reconciled_at > now() - interval '15 minutes') AS within_15m,
       count(*) FILTER (WHERE reconciled_at > now() - interval '24 hours') AS within_24h
  FROM smalllive_reconciliations GROUP BY 1 ORDER BY 1;

\echo A2 her universe now (the execmirror AUDREY_UNIVERSE_SQL) and how much of it was never reconciled
WITH u AS (
    SELECT DISTINCT group_id FROM execmirror_orders
     WHERE group_id IS NOT NULL AND (venue_order_id IS NOT NULL OR state = 'EXCLUDED')
       AND created_at > now() - interval '14 days'
    UNION
    SELECT DISTINCT group_id FROM execution_intents
     WHERE actual_state IN ('PAPER_ONLY', 'REFUSED', 'LANE_NOT_RUNNING')
       AND created_at > now() - interval '14 days')
SELECT count(*) AS universe,
       count(*) FILTER (WHERE r.group_id IS NULL) AS never_reconciled,
       (SELECT count(*) FROM execution_intents WHERE created_at > now() - interval '24 hours') AS intents_24h,
       (SELECT count(*) FROM smalllive_handoffs WHERE state = 'OPEN') AS open_actual_handoffs
  FROM u LEFT JOIN smalllive_reconciliations r ON r.group_id = u.group_id;

\echo A3 the account snapshot the quorum reads for VENUE_BALANCE and VENUE_POSITIONS: newest, whether it holds balances or positions, and the control
SELECT to_char(s.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') AS newest_snapshot,
       round(extract(epoch FROM now() - s.at)::numeric / 3600, 1) AS age_h,
       jsonb_typeof(s.balances) AS balances_type,
       CASE WHEN jsonb_typeof(s.balances) = 'array' THEN jsonb_array_length(s.balances) END AS balances_n,
       CASE WHEN jsonb_typeof(s.positions) = 'array' THEN jsonb_array_length(s.positions) END AS positions_n,
       (SELECT count(*) FROM execmirror_snapshots) AS snapshots,
       c.enabled, c.stopped, c.stop_done_at IS NOT NULL AS stop_done,
       c.account_fingerprint IS NOT NULL AS fingerprint_bound
  FROM execmirror_control c
  LEFT JOIN LATERAL (SELECT * FROM execmirror_snapshots ORDER BY at DESC LIMIT 1) s ON true;

\echo A4 Audrey reconciliation events (newest 20)
SELECT to_char(at AT TIME ZONE 'UTC', 'MM-DD"T"HH24:MI:SS') AS at, kind,
       detail->>'group_id' AS group_id
  FROM execmirror_events WHERE kind LIKE 'AUDREY_RECONCILIATION_%'
 ORDER BY at DESC LIMIT 20;
