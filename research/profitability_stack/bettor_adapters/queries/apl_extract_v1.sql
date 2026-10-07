-- READ-ONLY. ALPHA PROOF LAB V1 DATASET (research/shadow; immutable sources
-- only). One JSON object per sampled decision: paper_decisions (one per
-- strategy x market x side x 15 min), its recorded venue book (book_obs_id),
-- the market's last book before the event start (closing line), the next
-- 15 minutes of book path (maker-fill evidence), the event start / sport
-- type, and the settled LONG-side payout from paper_settlements, else from
-- external_valuations 0/1 outcomes. Markets whose label sources disagree are
-- dropped (counted in the second statement).
WITH lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, 'PAPER_SETTLEMENT' src
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, 'VALUATION_OUTCOME'
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IS NOT NULL),
lab2 AS (SELECT slug, min(y) y_long, string_agg(DISTINCT src, '+') src
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
dec AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side,
                      floor(extract(epoch FROM d.decided_at) / 900))
         d.decision_id, d.decided_at, d.strategy, d.us_market_slug slug,
         d.holding_side, d.verdict, d.refusal, d.p_pinnacle, d.p_internal,
         d.p_blended, d.limit_price, d.proposed_qty, d.book_obs_id,
         d.economics->>'probability_basis' prob_basis
    FROM paper_decisions d JOIN lab2 l ON l.slug = d.us_market_slug
   WHERE d.p_pinnacle IS NOT NULL AND d.book_obs_id IS NOT NULL
   ORDER BY d.strategy, d.us_market_slug, d.holding_side,
            floor(extract(epoch FROM d.decided_at) / 900), d.decided_at)
SELECT json_build_object(
  'id', dec.decision_id, 'at', extract(epoch FROM dec.decided_at),
  'strategy', dec.strategy, 'slug', dec.slug, 'side', dec.holding_side,
  'verdict', dec.verdict, 'refusal', dec.refusal, 'p_pin', dec.p_pinnacle,
  'p_int', dec.p_internal, 'p_blend', dec.p_blended, 'limit', dec.limit_price,
  'qty', dec.proposed_qty, 'prob_basis', dec.prob_basis,
  'y_long', lab2.y_long, 'label_src', lab2.src,
  'sports_type', pm.sports_type, 'league', pm.team_league,
  'start', extract(epoch FROM pm.game_start),
  'bid', b.bid, 'ask', b.ask, 'bid_q', b.bid_q, 'ask_q', b.ask_q, 'book_at', extract(epoch FROM b.at),
  'close_bid', cl.bid, 'close_ask', cl.ask, 'close_at', extract(epoch FROM cl.at),
  'n15_min_ask', nx.min_ask, 'n15_max_bid', nx.max_bid, 'n15_obs', nx.n)
  FROM dec JOIN lab2 ON lab2.slug = dec.slug
  LEFT JOIN LATERAL (SELECT sports_type, team_league, game_start FROM us_premap
                      WHERE market_slug = dec.slug LIMIT 1) pm ON true
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
     WHERE o.us_market_slug = dec.slug AND o.error IS NULL
       AND pm.game_start IS NOT NULL AND o.observed_at <= pm.game_start
     ORDER BY o.observed_at DESC LIMIT 1) cl ON true
  LEFT JOIN LATERAL (
    SELECT count(*) n,
           min((SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l)) min_ask,
           max((SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
               CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l)) max_bid
      FROM paper_book_observations o
     WHERE o.us_market_slug = dec.slug AND o.error IS NULL
       AND o.observed_at > dec.decided_at
       AND o.observed_at <= dec.decided_at + interval '15 minutes') nx ON true
 ORDER BY dec.decided_at;
SELECT 'LABEL_CONFLICTS' k, count(*) FROM (SELECT slug FROM (
  SELECT us_market_slug slug, CASE WHEN holding_side='LONG' THEN payout_per_contract ELSE 1-payout_per_contract END y
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION ALL SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1-outcome ELSE outcome END
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1) AND us_market_slug IS NOT NULL) a
  GROUP BY slug HAVING min(y) <> max(y)) c;
