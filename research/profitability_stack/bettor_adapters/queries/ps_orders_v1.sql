-- READ-ONLY. PROFITABILITY STACK V1 -- the paper order / fill / settlement
-- record, one JSON object per paper order: role, type, limit, queue-ahead,
-- the decision behind it (probability, decided_at), the book recorded at
-- submission (the latest observation at or before eligible_at), its fills
-- (qty, VWAP, fees, first fill, basis), cancel request / terminal times, the
-- first book observed at or after first_fill + {0.1, 0.5, 1, 5, 60, 300} s
-- (with the actual lag), the market's event start / sport type, and the
-- latest settlement of that position (payout per contract).
WITH lv AS (
  SELECT o.obs_id, o.us_market_slug, o.observed_at,
         (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
         (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
             CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
    FROM paper_book_observations o
   WHERE o.error IS NULL AND o.us_market_slug IN (SELECT DISTINCT us_market_slug FROM paper_orders)),
f AS (
  SELECT order_id, sum(qty) qty, sum(qty*price)/nullif(sum(qty),0) vwap, sum(fee_usd) fees,
         min(filled_at) first_fill, max(filled_at) last_fill, string_agg(DISTINCT basis, '+') basis, count(*) n
    FROM paper_fills GROUP BY order_id),
ev AS (
  SELECT order_id, min(at) FILTER (WHERE kind='CANCEL_REQUESTED') cancel_req,
         min(at) FILTER (WHERE kind IN ('CANCELED','EXPIRED')) terminal_ev
    FROM paper_order_events GROUP BY order_id),
st AS (
  SELECT DISTINCT ON (group_id, us_market_slug, holding_side) group_id, us_market_slug, holding_side,
         payout_per_contract, outcome, settled_at
    FROM paper_settlements ORDER BY group_id, us_market_slug, holding_side, version DESC)
SELECT json_build_object(
  'order_id', o.order_id, 'group_id', o.group_id, 'role', o.role, 'type', o.order_type,
  'tif', o.time_in_force, 'direction', o.direction, 'side', o.holding_side, 'slug', o.us_market_slug,
  'strategy', o.strategy, 'state', o.state, 'qty', o.qty, 'filled_qty', o.filled_qty,
  'limit', o.limit_price, 'queue_ahead', o.queue_ahead_qty, 'queue_basis', o.queue_basis,
  'created', extract(epoch FROM o.created_at), 'eligible', extract(epoch FROM o.eligible_at),
  'expires', extract(epoch FROM o.expires_at), 'terminal', extract(epoch FROM o.terminal_at),
  'terminal_reason', o.terminal_reason,
  'decision_id', o.decision_id, 'p_pin', d.p_pinnacle, 'p_blend', d.p_blended, 'p_int', d.p_internal,
  'decided', extract(epoch FROM d.decided_at),
  'sub_bid', sb.bid, 'sub_ask', sb.ask, 'sub_at', extract(epoch FROM sb.observed_at),
  'fill_qty', f.qty, 'vwap', f.vwap, 'fees', f.fees, 'first_fill', extract(epoch FROM f.first_fill),
  'fill_basis', f.basis, 'n_fills', f.n,
  'cancel_req', extract(epoch FROM ev.cancel_req), 'terminal_ev', extract(epoch FROM ev.terminal_ev),
  'marks', (SELECT json_agg(json_build_object('lag', lg, 'bid', m.bid, 'ask', m.ask,
                     'actual_lag', extract(epoch FROM m.observed_at - f.first_fill)))
              FROM unnest(ARRAY[0.1,0.5,1,5,60,300]::float8[]) lg
              LEFT JOIN LATERAL (SELECT bid, ask, observed_at FROM lv
                 WHERE lv.us_market_slug = o.us_market_slug
                   AND lv.observed_at >= f.first_fill + make_interval(secs => lg)
                 ORDER BY lv.observed_at LIMIT 1) m ON true
             WHERE f.first_fill IS NOT NULL),
  'sports_type', pm.sports_type, 'league', pm.team_league, 'start', extract(epoch FROM pm.game_start),
  'event', pm.event_slug,
  'settle_payout', st.payout_per_contract, 'settle_outcome', st.outcome, 'settled', extract(epoch FROM st.settled_at))
  FROM paper_orders o
  LEFT JOIN paper_decisions d ON d.decision_id = o.decision_id
  LEFT JOIN f ON f.order_id = o.order_id
  LEFT JOIN ev ON ev.order_id = o.order_id
  LEFT JOIN st ON st.group_id = o.group_id AND st.us_market_slug = o.us_market_slug AND st.holding_side = o.holding_side
  LEFT JOIN LATERAL (SELECT bid, ask, observed_at FROM lv WHERE lv.us_market_slug = o.us_market_slug
                       AND lv.observed_at <= o.eligible_at ORDER BY lv.observed_at DESC LIMIT 1) sb ON true
  LEFT JOIN LATERAL (SELECT sports_type, team_league, game_start, event_slug FROM us_premap
                      WHERE market_slug = o.us_market_slug LIMIT 1) pm ON true
 ORDER BY o.created_at;
