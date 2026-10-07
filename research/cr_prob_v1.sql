-- READ-ONLY. COMPLETION READINESS V1 -- probability authority evidence (the backend readback's own query,
-- parameters inlined). SELECT only.
SELECT row_to_json(t)::text FROM (WITH 
lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, settled_at at
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, outcome_at
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IS NOT NULL),
lab2 AS (SELECT slug, min(y) y_long, min(at) settled_at
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
d AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
         d.us_market_slug slug, d.holding_side side, d.p_blended p_blend,
         d.p_pinnacle p_pin, (d.economics->>'probability')::float8 p_used,
         d.book_obs_id
    FROM paper_decisions d JOIN lab2 l ON l.slug = d.us_market_slug
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => 30)
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at)
SELECT d.*, l.y_long, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM d JOIN lab2 l ON l.slug = d.slug
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN LATERAL (SELECT event_slug FROM us_premap
                      WHERE market_slug = d.slug LIMIT 1) pm ON true
 LIMIT 5000) t;
