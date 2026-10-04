-- LAB-A EDGE DECAY: per-opportunity extraction (read-only, bounded).
--
-- ONE JSON LINE PER QUALIFIED OPPORTUNITY of the completed-game INVESTMENT
-- strategy (PINNACLE_COMPLETED_GAME_PAPER) in the last 7 days:
--   ENTER decisions, and NEAR-MISS refusals: the book was read, every
--   refusal is economic (BELOW_MIN_GROSS_EDGE / fees consume the edge /
--   net EV not positive / no sized quantity) and the best level's gross
--   edge was positive.
-- Each line carries ONLY recorded observations, each with its own recorded
-- stamp, so the lab's point-in-time accessor (sportsassets/lab/pit.py) can
-- re-apply the anti-lookahead rule when the line is replayed:
--   d      the decision record (paper_decisions) and its Pinnacle stamps
--   b0     the decision's own book (consumed side, published order)
--   v0     the decision's valuation row (stamps, quote context)
--   books  every later readable book of the same market, (t0, t0 + 600 s]
--   vals   every later valuation of the same contract orientation (same
--          slug, buy intent and payout event), (t0, t0 + 600 s]
--   orders / fills  the ENTRY order(s) and simulated fills (ENTER only)
--   xi     the ACTUAL lane's execution-intent timeline (ENTER only)
--   att    the evaluation attempt's elapsed seconds
--   pm     the catalogue's game start with its last-write stamp
-- Bounded: 7 days, LIMIT 1500 decisions, 25 levels per book side.
\echo == LAB_A_EDGE_DECAY_EXTRACT_V1 rows follow (one JSON object per line)
WITH q AS (
  SELECT d.*
    FROM paper_decisions d
   WHERE d.decided_at > now() - interval '7 days'
     AND d.strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
     AND d.book_obs_id IS NOT NULL
     AND (d.verdict = 'ENTER'
          OR (d.refusals <@ ARRAY['BELOW_MIN_GROSS_EDGE',
                'GROSS_EDGE_CLEARS_THRESHOLD_BUT_FEES_CONSUME_IT',
                'NET_EV_NOT_POSITIVE_AFTER_FEES',
                'NO_SIZED_QUANTITY']::text[]
              AND cardinality(d.refusals) > 0
              AND (d.economics->>'best_level_edge_pp')::float8 > 0))
   ORDER BY d.decided_at
   LIMIT 1500)
SELECT jsonb_build_object(
  'd', jsonb_build_object(
     'decision_id', q.decision_id, 'strategy', q.strategy,
     'policy_version', q.policy_version, 'verdict', q.verdict,
     'refusals', to_jsonb(q.refusals),
     'decided_at', extract(epoch FROM q.decided_at),
     'recorded_at', extract(epoch FROM q.recorded_at),
     'us_market_slug', q.us_market_slug, 'holding_side', q.holding_side,
     'intent', q.intent, 'fixture', q.fixture, 'valuation_id', q.valuation_id,
     'p_pinnacle', q.p_pinnacle, 'proposed_qty', q.proposed_qty,
     'limit_price', q.limit_price, 'book_obs_id', q.book_obs_id,
     'label', jsonb_build_object(
        'competition', q.label->>'competition',
        'market_type', q.label->>'market_type',
        'event_date', q.label->>'event_date'),
     'economics', jsonb_build_object(
        'threshold_edge_pp', q.economics->'threshold_edge_pp',
        'best_level_edge_pp', q.economics->'best_level_edge_pp',
        'depth_within_limit', q.economics->'depth_within_limit',
        'expected_net_profit_usd',
            q.economics->'acquisition'->'expected_net_profit_usd',
        'fees_usd', q.economics->'acquisition'->'fees_usd',
        'qty', q.economics->'acquisition'->'qty',
        'vwap', q.economics->'acquisition'->'vwap',
        'sport_family', q.economics->'mapping_assumptions'->'sport_family'),
     'pinnacle', jsonb_build_object(
        'at', q.pinnacle->'at', 'received_at', q.pinnacle->'received_at',
        'age_s', q.pinnacle->'age_s', 'limit_s', q.pinnacle->'limit_s',
        'valuation_decided_at', q.pinnacle->'valuation_decided_at',
        'decided_via', q.pinnacle->'decided_via')),
  'b0', (SELECT jsonb_build_object(
            'obs_id', b.obs_id,
            'observed_at', extract(epoch FROM b.observed_at),
            'recorded_at', extract(epoch FROM b.recorded_at),
            'error', b.error,
            'levels', (SELECT jsonb_agg(jsonb_build_array(
                          coalesce(e->'px'->>'value', e->>'px', e->>'price'),
                          coalesce(e->>'qty', e->>'size')) ORDER BY i)
                         FROM jsonb_array_elements(
                                CASE WHEN jsonb_typeof(CASE WHEN q.holding_side = 'SHORT'
                                       THEN b.bids ELSE b.offers END) = 'array'
                                     THEN (CASE WHEN q.holding_side = 'SHORT'
                                       THEN b.bids ELSE b.offers END)
                                     ELSE '[]'::jsonb END)
                              WITH ORDINALITY AS x(e, i)
                        WHERE i <= 25))
           FROM paper_book_observations b WHERE b.obs_id = q.book_obs_id),
  'v0', (SELECT jsonb_build_object(
            'id', v.id, 'observed_at', extract(epoch FROM v.observed_at),
            'received_at', extract(epoch FROM v.received_at),
            'decided_at', extract(epoch FROM v.decided_at),
            'probability', v.probability, 'payout_event', v.payout_event,
            'buy_intent', v.buy_intent, 'sport_family', v.sport_family,
            'market', v.market,
            'quote_context', v.settlement_comparison->>'quote_context')
           FROM external_valuations v WHERE v.id = q.valuation_id),
  'books', (SELECT coalesce(jsonb_agg(jsonb_build_array(
               b.obs_id, extract(epoch FROM b.observed_at),
               extract(epoch FROM b.recorded_at),
               (SELECT jsonb_agg(jsonb_build_array(
                          coalesce(e->'px'->>'value', e->>'px', e->>'price'),
                          coalesce(e->>'qty', e->>'size')) ORDER BY i)
                  FROM jsonb_array_elements(
                         CASE WHEN jsonb_typeof(CASE WHEN q.holding_side = 'SHORT'
                                THEN b.bids ELSE b.offers END) = 'array'
                              THEN (CASE WHEN q.holding_side = 'SHORT'
                                THEN b.bids ELSE b.offers END)
                              ELSE '[]'::jsonb END)
                       WITH ORDINALITY AS x(e, i)
                 WHERE i <= 25)) ORDER BY b.observed_at), '[]'::jsonb)
              FROM paper_book_observations b
             WHERE b.us_market_slug = q.us_market_slug
               AND b.error IS NULL
               AND b.obs_id <> q.book_obs_id
               AND b.observed_at > q.decided_at
               AND b.observed_at <= q.decided_at + interval '600 seconds'),
  'vals', (SELECT coalesce(jsonb_agg(jsonb_build_array(
               v.id, extract(epoch FROM v.observed_at),
               extract(epoch FROM v.received_at),
               extract(epoch FROM v.decided_at), v.probability)
               ORDER BY v.decided_at), '[]'::jsonb)
             FROM external_valuations v, external_valuations v0
            WHERE v0.id = q.valuation_id
              AND v.us_market_slug = q.us_market_slug
              AND v.experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW'
              AND v.buy_intent IS NOT DISTINCT FROM v0.buy_intent
              AND v.payout_event IS NOT DISTINCT FROM v0.payout_event
              AND v.probability IS NOT NULL
              AND v.id <> q.valuation_id
              AND v.decided_at > q.decided_at
              AND v.decided_at <= q.decided_at + interval '600 seconds'),
  'orders', (SELECT coalesce(jsonb_agg(jsonb_build_object(
               'order_id', o.order_id, 'qty', o.qty,
               'limit_price', o.limit_price, 'order_type', o.order_type,
               'time_in_force', o.time_in_force,
               'decided_at', extract(epoch FROM o.decided_at),
               'eligible_at', extract(epoch FROM o.eligible_at),
               'expires_at', extract(epoch FROM o.expires_at),
               'created_at', extract(epoch FROM o.created_at),
               'state', o.state, 'filled_qty', o.filled_qty,
               'terminal_at', extract(epoch FROM o.terminal_at),
               'terminal_reason', o.terminal_reason)), '[]'::jsonb)
               FROM paper_orders o
              WHERE o.decision_id = q.decision_id AND o.role = 'ENTRY'),
  'fills', (SELECT coalesce(jsonb_agg(jsonb_build_array(
               extract(epoch FROM f.filled_at),
               extract(epoch FROM f.recorded_at), f.qty, f.price, f.fee_usd,
               f.book_obs_id, extract(epoch FROM f.book_observed_at))
               ORDER BY f.filled_at), '[]'::jsonb)
              FROM paper_fills f JOIN paper_orders o ON o.order_id = f.order_id
             WHERE o.decision_id = q.decision_id AND o.role = 'ENTRY'),
  'xi', (SELECT jsonb_build_object(
            'actual_state', x.actual_state, 'actual_refusal', x.actual_refusal,
            'created_at', extract(epoch FROM x.created_at),
            'timeline', x.timeline)
           FROM execution_intents x WHERE x.decision_id = q.decision_id),
  'att', (SELECT jsonb_build_object('elapsed_s', a.elapsed_s, 'via', a.via,
                                    'at', extract(epoch FROM a.at))
            FROM paper_evaluation_attempts a
           WHERE a.decision_id = q.decision_id AND a.outcome = 'DECIDED'
           ORDER BY a.attempt_id LIMIT 1),
  'pm', (SELECT jsonb_build_object(
            'game_start', extract(epoch FROM p.game_start),
            'updated_at', extract(epoch FROM p.updated_at))
           FROM us_premap p
          WHERE p.market_slug = q.us_market_slug AND p.game_start IS NOT NULL
          ORDER BY p.updated_at DESC NULLS LAST LIMIT 1)
  )::text AS lab_a_row
  FROM q
 ORDER BY q.decided_at;
\echo == LAB_A_EDGE_DECAY_EXTRACT_V1 end
