-- READ-ONLY. COMPLETION READINESS V1 -- executable EV evidence (the backend readback's own query,
-- parameters inlined). SELECT only.
SELECT row_to_json(t)::text FROM (SELECT d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
       d.us_market_slug slug, d.holding_side side, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM paper_decisions d
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN LATERAL (SELECT event_slug FROM us_premap
                      WHERE market_slug = d.us_market_slug LIMIT 1) pm ON true
 WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
   AND d.decided_at > now() - make_interval(days => 7)
 ORDER BY d.decided_at DESC
 LIMIT 2000) t;
