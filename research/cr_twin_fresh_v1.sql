-- READ-ONLY. COMPLETION READINESS V1 -- repaired twin, fresh orders after the diagnosis window (the backend readback's own query,
-- parameters inlined). SELECT only.
SELECT json_build_object(
  'order_id', o.order_id, 'role', o.role, 'tif', o.time_in_force,
  'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'state', o.state, 'qty', o.qty, 'limit', o.limit_price,
  'created', extract(epoch FROM o.created_at),
  'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at),
  'terminal', extract(epoch FROM o.terminal_at),
  'fills', (SELECT json_agg(json_build_object('at', extract(epoch FROM pf.filled_at),
                   'qty', pf.qty, 'price', pf.price) ORDER BY pf.filled_at)
              FROM paper_fills pf WHERE pf.order_id = o.order_id),
  'books', (SELECT json_agg(json_build_object('at', extract(epoch FROM b.observed_at),
                   'bids', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.bids)='array'
                                       THEN b.bids ELSE '[]' END) l),
                   'asks', (SELECT json_agg(json_build_object('px', (l->'px'->>'value')::float8,
                                    'qty', (l->>'qty')::float8))
                              FROM jsonb_array_elements(CASE WHEN jsonb_typeof(b.offers)='array'
                                       THEN b.offers ELSE '[]' END) l))
                   ORDER BY b.observed_at)
              FROM paper_book_observations b
             WHERE b.error IS NULL AND b.us_market_slug = o.us_market_slug
               AND b.observed_at >= o.eligible_at
               AND b.observed_at <= o.expires_at))::text AS j
  FROM paper_orders o
 WHERE o.time_in_force IN ('IOC', 'FOK') AND o.terminal_at IS NOT NULL
   AND o.role IN ('ENTRY', 'EXIT', 'REDUCE')
   AND o.eligible_at > to_timestamp(1791384986.0)
 ORDER BY o.eligible_at
 LIMIT 800;
