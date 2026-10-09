-- READ-ONLY. RC6 lane archer-lifecycle, fourth pass: the receipt's new
-- order-lifecycle reads (ORDER_LIFECYCLE_SQL, LIFECYCLE_BREAKS_SQL), the
-- venue-confirmed read kept apart (VENUE_CONFIRMED_SQL) and the truth
-- quorum's Audrey linkage read (AUDREY_POSITIONS_SQL), verbatim with the
-- account bound to paper_acct_main: their production answers and their
-- cost (the API reads the receipt under a 12 s statement timeout). Every
-- statement is a SELECT.

\echo Q1 ORDER_LIFECYCLE_SQL (answer)
SELECT o.role, o.order_type, o.state, count(*) AS orders,
           round(sum(o.qty), 6) AS qty, round(sum(o.filled_qty), 6) AS filled,
           count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill,
           count(*) FILTER (WHERE o.filled_qty > 0 AND o.filled_qty < o.qty)
               AS partly_filled
      FROM paper_orders o WHERE ('paper_acct_main'::text IS NULL OR o.account_id = 'paper_acct_main')
     GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo Q1 ORDER_LIFECYCLE_SQL (timed)
EXPLAIN (ANALYZE, SUMMARY, TIMING OFF, COSTS OFF)
SELECT o.role, o.order_type, o.state, count(*) AS orders,
           round(sum(o.qty), 6) AS qty, round(sum(o.filled_qty), 6) AS filled,
           count(*) FILTER (WHERE o.filled_qty > 0) AS with_fill,
           count(*) FILTER (WHERE o.filled_qty > 0 AND o.filled_qty < o.qty)
               AS partly_filled
      FROM paper_orders o WHERE ('paper_acct_main'::text IS NULL OR o.account_id = 'paper_acct_main')
     GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

\echo Q2 LIFECYCLE_BREAKS_SQL (answer)
WITH o AS (SELECT * FROM paper_orders
                WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')),
         s AS (SELECT order_id, sum(qty) AS q, count(*) AS n
                 FROM paper_fills WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')
                GROUP BY 1),
         e AS (SELECT ev.order_id,
                      count(*) FILTER (WHERE ev.kind = 'FILL') AS fill_ev,
                      bool_or(ev.kind = 'EXPIRED') AS expired_ev,
                      bool_or(ev.kind = 'CANCELED') AS canceled_ev,
                      bool_or(ev.kind = 'REJECTED') AS rejected_ev
                 FROM paper_order_events ev JOIN o USING (order_id)
                GROUP BY 1),
         p AS (SELECT account_id, group_id, us_market_slug, holding_side,
                      coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0)
                          AS bought,
                      coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0)
                          AS sold
                 FROM paper_fills WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')
                GROUP BY 1, 2, 3, 4)
    SELECT count(*) AS orders,
           count(*) FILTER (WHERE abs(coalesce(s.q, 0) - o.filled_qty) > 1e-6)
               AS fills_sum_ne_filled_qty,
           count(*) FILTER (WHERE o.state = 'FILLED'
                              AND o.filled_qty < o.qty - 1e-6)
               AS filled_state_not_complete,
           count(*) FILTER (WHERE o.state IN ('REJECTED', 'PENDING_SIMULATION')
                              AND o.filled_qty > 0)
               AS rejected_or_pending_with_fill,
           count(*) FILTER (WHERE o.state = 'RESTING' AND o.filled_qty > 0)
               AS resting_with_fill,
           count(*) FILTER (WHERE o.state = 'PARTIALLY_FILLED'
                              AND (o.filled_qty <= 0
                                   OR o.filled_qty >= o.qty))
               AS partial_without_fill_or_complete,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.terminal_at IS NULL)
               AS terminal_without_terminal_at,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.terminal_at IS NOT NULL)
               AS open_with_terminal_at,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.reserved_remaining_usd > 0)
               AS terminal_with_reservation_left,
           count(*) FILTER (WHERE coalesce(e.fill_ev, 0) <> coalesce(s.n, 0))
               AS fill_events_ne_fills,
           count(*) FILTER (WHERE (o.state = 'EXPIRED'
                                   AND NOT coalesce(e.expired_ev, false))
                               OR (o.state = 'CANCELED'
                                   AND NOT coalesce(e.canceled_ev, false))
                               OR (o.state = 'REJECTED'
                                   AND NOT coalesce(e.rejected_ev, false)))
               AS terminal_without_terminal_event,
           count(*) FILTER (WHERE o.direction = 'SELL' AND NOT EXISTS (
                   SELECT 1 FROM p WHERE p.account_id = o.account_id
                      AND p.group_id = o.group_id
                      AND p.us_market_slug = o.us_market_slug
                      AND p.holding_side = o.holding_side AND p.bought > 0))
               AS sale_without_a_position,
           (SELECT count(*) FROM p WHERE p.sold > p.bought + 1e-6)
               AS positions_sold_beyond_bought,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.expires_at < now())
               AS open_orders_past_expiry
      FROM o LEFT JOIN s USING (order_id) LEFT JOIN e USING (order_id);

\echo Q2 LIFECYCLE_BREAKS_SQL (timed)
EXPLAIN (ANALYZE, SUMMARY, TIMING OFF, COSTS OFF)
WITH o AS (SELECT * FROM paper_orders
                WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')),
         s AS (SELECT order_id, sum(qty) AS q, count(*) AS n
                 FROM paper_fills WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')
                GROUP BY 1),
         e AS (SELECT ev.order_id,
                      count(*) FILTER (WHERE ev.kind = 'FILL') AS fill_ev,
                      bool_or(ev.kind = 'EXPIRED') AS expired_ev,
                      bool_or(ev.kind = 'CANCELED') AS canceled_ev,
                      bool_or(ev.kind = 'REJECTED') AS rejected_ev
                 FROM paper_order_events ev JOIN o USING (order_id)
                GROUP BY 1),
         p AS (SELECT account_id, group_id, us_market_slug, holding_side,
                      coalesce(sum(qty) FILTER (WHERE direction = 'BUY'), 0)
                          AS bought,
                      coalesce(sum(qty) FILTER (WHERE direction = 'SELL'), 0)
                          AS sold
                 FROM paper_fills WHERE ('paper_acct_main'::text IS NULL OR account_id = 'paper_acct_main')
                GROUP BY 1, 2, 3, 4)
    SELECT count(*) AS orders,
           count(*) FILTER (WHERE abs(coalesce(s.q, 0) - o.filled_qty) > 1e-6)
               AS fills_sum_ne_filled_qty,
           count(*) FILTER (WHERE o.state = 'FILLED'
                              AND o.filled_qty < o.qty - 1e-6)
               AS filled_state_not_complete,
           count(*) FILTER (WHERE o.state IN ('REJECTED', 'PENDING_SIMULATION')
                              AND o.filled_qty > 0)
               AS rejected_or_pending_with_fill,
           count(*) FILTER (WHERE o.state = 'RESTING' AND o.filled_qty > 0)
               AS resting_with_fill,
           count(*) FILTER (WHERE o.state = 'PARTIALLY_FILLED'
                              AND (o.filled_qty <= 0
                                   OR o.filled_qty >= o.qty))
               AS partial_without_fill_or_complete,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.terminal_at IS NULL)
               AS terminal_without_terminal_at,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.terminal_at IS NOT NULL)
               AS open_with_terminal_at,
           count(*) FILTER (WHERE o.state IN ('FILLED', 'EXPIRED', 'CANCELED',
                                              'REJECTED')
                              AND o.reserved_remaining_usd > 0)
               AS terminal_with_reservation_left,
           count(*) FILTER (WHERE coalesce(e.fill_ev, 0) <> coalesce(s.n, 0))
               AS fill_events_ne_fills,
           count(*) FILTER (WHERE (o.state = 'EXPIRED'
                                   AND NOT coalesce(e.expired_ev, false))
                               OR (o.state = 'CANCELED'
                                   AND NOT coalesce(e.canceled_ev, false))
                               OR (o.state = 'REJECTED'
                                   AND NOT coalesce(e.rejected_ev, false)))
               AS terminal_without_terminal_event,
           count(*) FILTER (WHERE o.direction = 'SELL' AND NOT EXISTS (
                   SELECT 1 FROM p WHERE p.account_id = o.account_id
                      AND p.group_id = o.group_id
                      AND p.us_market_slug = o.us_market_slug
                      AND p.holding_side = o.holding_side AND p.bought > 0))
               AS sale_without_a_position,
           (SELECT count(*) FROM p WHERE p.sold > p.bought + 1e-6)
               AS positions_sold_beyond_bought,
           count(*) FILTER (WHERE o.state IN ('PENDING_SIMULATION', 'RESTING',
                                              'PARTIALLY_FILLED',
                                              'CANCEL_PENDING')
                              AND o.expires_at < now())
               AS open_orders_past_expiry
      FROM o LEFT JOIN s USING (order_id) LEFT JOIN e USING (order_id);

\echo Q3 VENUE_CONFIRMED_SQL (answer)
SELECT 'execmirror_orders' AS source, state, count(*) AS rows_n,
           count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS at_venue
      FROM execmirror_orders GROUP BY 1, 2
    UNION ALL
    SELECT 'execmirror_fills', 'VENUE_FILL', count(*), count(*)
      FROM execmirror_fills;

\echo Q3 VENUE_CONFIRMED_SQL (timed)
EXPLAIN (ANALYZE, SUMMARY, TIMING OFF, COSTS OFF)
SELECT 'execmirror_orders' AS source, state, count(*) AS rows_n,
           count(*) FILTER (WHERE venue_order_id IS NOT NULL) AS at_venue
      FROM execmirror_orders GROUP BY 1, 2
    UNION ALL
    SELECT 'execmirror_fills', 'VENUE_FILL', count(*), count(*)
      FROM execmirror_fills;

\echo Q4 AUDREY_POSITIONS_SQL (answer)
SELECT f.us_market_slug, f.group_id, f.held,
           extract(epoch FROM r.reconciled_at) AS reconciled_at, r.status,
           'execmirror_fills' AS source
      FROM (SELECT us_market_slug, group_id,
                   sum(CASE WHEN intent ILIKE '%SELL%' THEN -qty ELSE qty END)
                       AS held
              FROM execmirror_fills GROUP BY 1, 2) f
      LEFT JOIN smalllive_reconciliations r ON r.group_id = f.group_id
     WHERE f.held <> 0
    UNION ALL
    SELECT h.us_market_slug, h.group_id, h.live_held,
           extract(epoch FROM r.reconciled_at), r.status,
           'smalllive_handoffs (OPEN)'
      FROM smalllive_handoffs h
      LEFT JOIN smalllive_reconciliations r ON r.group_id = h.group_id
     WHERE h.state = 'OPEN';

\echo Q4 AUDREY_POSITIONS_SQL (timed)
EXPLAIN (ANALYZE, SUMMARY, TIMING OFF, COSTS OFF)
SELECT f.us_market_slug, f.group_id, f.held,
           extract(epoch FROM r.reconciled_at) AS reconciled_at, r.status,
           'execmirror_fills' AS source
      FROM (SELECT us_market_slug, group_id,
                   sum(CASE WHEN intent ILIKE '%SELL%' THEN -qty ELSE qty END)
                       AS held
              FROM execmirror_fills GROUP BY 1, 2) f
      LEFT JOIN smalllive_reconciliations r ON r.group_id = f.group_id
     WHERE f.held <> 0
    UNION ALL
    SELECT h.us_market_slug, h.group_id, h.live_held,
           extract(epoch FROM r.reconciled_at), r.status,
           'smalllive_handoffs (OPEN)'
      FROM smalllive_handoffs h
      LEFT JOIN smalllive_reconciliations r ON r.group_id = h.group_id
     WHERE h.state = 'OPEN';
