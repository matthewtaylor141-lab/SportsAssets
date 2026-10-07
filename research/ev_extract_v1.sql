-- READ-ONLY. EV PROBABILITY ENGINE V1 DATASET (research / shadow; immutable
-- sources only). One JSON object per decision: EVERY ENTER decision, plus one
-- REFUSE per strategy x market x side x 15-minute bucket that holds no ENTER
-- (n_bucket = decisions the bucket held). Each row carries the decision's own
-- probability and freshness, the linked Pinnacle valuation (provider, book,
-- devig, source age, settlement rule, payout event), the venue book it
-- recorded, the last venue book before the event start (closing line), the
-- premap event / sport / family / start, the registry settlement state, and
-- the settled LONG-side outcome (paper_settlements, else external_valuations
-- 0/1). Markets whose label sources disagree are dropped.
WITH lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, 'PAPER_SETTLEMENT' src,
         settled_at at
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, 'VALUATION_OUTCOME', outcome_at
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IS NOT NULL),
lab2 AS (SELECT slug, min(y) y_long, string_agg(DISTINCT src, '+') src, min(at) settled_at
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
d0 AS (
  SELECT d.*, floor(extract(epoch FROM d.decided_at) / 900) bkt
    FROM paper_decisions d JOIN lab2 l ON l.slug = d.us_market_slug
   WHERE d.book_obs_id IS NOT NULL),
bk AS (
  SELECT strategy, us_market_slug, holding_side, bkt, count(*) n,
         bool_or(verdict = 'ENTER') has_enter
    FROM d0 GROUP BY 1,2,3,4),
dec AS (
  SELECT d0.* FROM d0 WHERE d0.verdict = 'ENTER'
  UNION ALL
  SELECT r.* FROM (
    SELECT DISTINCT ON (d0.strategy, d0.us_market_slug, d0.holding_side, d0.bkt) d0.*
      FROM d0 JOIN bk USING (strategy, us_market_slug, holding_side, bkt)
     WHERE NOT bk.has_enter
     ORDER BY d0.strategy, d0.us_market_slug, d0.holding_side, d0.bkt, d0.decided_at) r)
SELECT (jsonb_build_object(
  'id', dec.decision_id, 'at', extract(epoch FROM dec.decided_at),
  'strategy', dec.strategy, 'policy_version', dec.policy_version, 'slug', dec.us_market_slug,
  'side', dec.holding_side, 'verdict', dec.verdict, 'refusal', dec.refusal,
  'n_bucket', bk.n,
  'p_pin', dec.p_pinnacle, 'p_int', dec.p_internal, 'p_blend', dec.p_blended,
  'p_used', (dec.economics->>'probability')::float8,
  'prob_basis', dec.economics->>'probability_basis',
  'prob_age_s', (dec.economics->>'probability_age_at_decision_s')::float8,
  'book_age_s', (dec.economics->>'book_age_s')::float8,
  'exec_price', CASE WHEN jsonb_typeof(dec.economics->'executable_price') = 'number'
                     THEN (dec.economics->>'executable_price')::float8 END,
  'fees_usd', CASE WHEN jsonb_typeof(dec.economics->'fees_usd') = 'number'
                   THEN (dec.economics->>'fees_usd')::float8 END,
  'limit', dec.limit_price, 'qty', dec.proposed_qty) || jsonb_build_object(
  'v_provider', v.provider, 'v_book', v.book, 'v_devig', v.devig_method,
  'v_age_s', v.age_s, 'v_observed', extract(epoch FROM v.observed_at), 'v_prob', v.probability,
  'v_settlement_rule', v.settlement_rule, 'v_payout_event', v.payout_event,
  'v_identity_basis', v.contract_identity_basis, 'v_sport_family', v.sport_family,
  'v_market', v.market, 'v_period', v.period, 'v_line', v.line, 'v_event_key', v.event_key,
  'v_overround', v.overround,
  'y_long', lab2.y_long, 'label_src', lab2.src, 'settled_at', extract(epoch FROM lab2.settled_at),
  'sports_type', pm.sports_type, 'league', pm.team_league, 'event', pm.event_slug,
  'start', extract(epoch FROM pm.game_start),
  'registry_settlement', reg.settlement_state,
  'bid', b.bid, 'ask', b.ask, 'bid_q', b.bid_q, 'ask_q', b.ask_q, 'book_at', extract(epoch FROM b.at),
  'close_bid', cl.bid, 'close_ask', cl.ask, 'close_at', extract(epoch FROM cl.at)))::text
  FROM dec
  JOIN lab2 ON lab2.slug = dec.us_market_slug
  JOIN bk ON bk.strategy = dec.strategy AND bk.us_market_slug = dec.us_market_slug
         AND bk.holding_side = dec.holding_side AND bk.bkt = dec.bkt
  LEFT JOIN external_valuations v ON v.id = dec.valuation_id
  LEFT JOIN market_plane_registry reg ON reg.contract_id = dec.us_market_slug
  LEFT JOIN LATERAL (SELECT sports_type, team_league, game_start, event_slug FROM us_premap
                      WHERE market_slug = dec.us_market_slug LIMIT 1) pm ON true
  LEFT JOIN LATERAL (
    SELECT o.observed_at at,
           (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
           (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask,
           (SELECT (l->>'qty')::float8 FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l
             ORDER BY (l->'px'->>'value')::float8 DESC LIMIT 1) bid_q,
           (SELECT (l->>'qty')::float8 FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l
             ORDER BY (l->'px'->>'value')::float8 LIMIT 1) ask_q
      FROM paper_book_observations o WHERE o.obs_id = dec.book_obs_id AND o.error IS NULL) b ON true
  LEFT JOIN LATERAL (
    SELECT o.observed_at at,
           (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
           (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
      FROM paper_book_observations o
     WHERE o.us_market_slug = dec.us_market_slug AND o.error IS NULL
       AND pm.game_start IS NOT NULL AND o.observed_at <= pm.game_start
     ORDER BY o.observed_at DESC LIMIT 1) cl ON true
 ORDER BY dec.decided_at;
