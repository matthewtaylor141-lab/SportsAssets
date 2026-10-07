-- READ-ONLY. PROFITABILITY STACK V1 -- DIGITAL TWIN input. One JSON object per
-- non-protection paper order (ENTRY / EXIT / REDUCE): the decision behind it
-- (decided_at, probability), its recorded fills, and EVERY venue book
-- observation of that market from 180 s before the decision to 60 s after the
-- order's terminal time (or expiry), each with its observed_at and the top 5
-- price levels per side. The twin replays these strictly in event-time order.
WITH o AS (
  SELECT o.*, d.decided_at AS dec_at, d.p_pinnacle AS dec_p
    FROM paper_orders o
    LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
   WHERE o.role IN ('ENTRY', 'EXIT', 'REDUCE'))
SELECT json_build_object(
  'order_id', o.order_id, 'group_id', o.group_id, 'role', o.role, 'type', o.order_type,
  'tif', o.time_in_force, 'direction', o.direction, 'side', o.holding_side,
  'slug', o.us_market_slug, 'strategy', o.strategy, 'state', o.state, 'qty', o.qty,
  'limit', o.limit_price, 'queue_ahead', o.queue_ahead_qty,
  'decided', extract(epoch FROM o.dec_at), 'p_pin', o.dec_p,
  'created', extract(epoch FROM o.created_at), 'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at), 'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at), 'qty', pf.qty,
                   'price', pf.price, 'fee', pf.fee_usd, 'basis', pf.basis) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8) ORDER BY (l->'px'->>'value')::float8 DESC)
                              FROM (SELECT l FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l
                                     ORDER BY (l->'px'->>'value')::float8 DESC LIMIT 5) x),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8) ORDER BY (l->'px'->>'value')::float8)
                              FROM (SELECT l FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l
                                     ORDER BY (l->'px'->>'value')::float8 LIMIT 5) y))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND b.observed_at >= coalesce(o.dec_at, o.created_at) - interval '180 seconds'
               AND b.observed_at <= coalesce(o.terminal_at, o.expires_at) + interval '60 seconds'))
  FROM o
 ORDER BY o.created_at;
