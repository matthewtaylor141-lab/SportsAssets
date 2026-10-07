-- completion probability / EV reads: previous vs rewritten statements on
-- production data (read only): rows in one and not the other, both ways,
-- event_slug compared separately (either statement takes an arbitrary
-- premap row), then EXPLAIN ANALYZE of the rewrites

WITH a AS (
WITH 
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
 LIMIT 5000), b AS (
WITH d0 AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
         d.us_market_slug slug, d.holding_side side, d.p_blended p_blend,
         d.p_pinnacle p_pin, (d.economics->>'probability')::float8 p_used,
         d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => 30)
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at),
slugs AS (SELECT DISTINCT slug FROM d0),
lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, settled_at at
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
     AND us_market_slug IN (SELECT slug FROM slugs)
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, outcome_at
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IN (SELECT slug FROM slugs)),
lab2 AS (SELECT slug, min(y) y_long, min(at) settled_at
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap WHERE market_slug IN (SELECT slug FROM lab2)
        ORDER BY market_slug)
SELECT d.*, l.y_long, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM d0 d JOIN lab2 l ON l.slug = d.slug
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.slug
 LIMIT 5000)
SELECT 'prob' q, (SELECT count(*) FROM a) a_rows, (SELECT count(*) FROM b) b_rows,
  (SELECT count(*) FROM (SELECT decision_id, at, strategy, slug, side, p_blend, p_pin, p_used, book_obs_id, y_long, bid, ask FROM a EXCEPT ALL SELECT decision_id, at, strategy, slug, side, p_blend, p_pin, p_used, book_obs_id, y_long, bid, ask FROM b) x) a_minus_b,
  (SELECT count(*) FROM (SELECT decision_id, at, strategy, slug, side, p_blend, p_pin, p_used, book_obs_id, y_long, bid, ask FROM b EXCEPT ALL SELECT decision_id, at, strategy, slug, side, p_blend, p_pin, p_used, book_obs_id, y_long, bid, ask FROM a) y) b_minus_a,
  (SELECT count(*) FROM a JOIN b USING (decision_id) WHERE a.event_slug IS DISTINCT FROM b.event_slug) event_slug_differs;

WITH a AS (
SELECT d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
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
   AND d.decided_at > now() - make_interval(days => 30)
 ORDER BY d.decided_at DESC
 LIMIT 5000), b AS (
WITH d0 AS (
  SELECT d.decision_id, d.decided_at, d.strategy, d.us_market_slug,
         d.holding_side, d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => 30)
   ORDER BY d.decided_at DESC
   LIMIT 5000),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap
        WHERE market_slug IN (SELECT us_market_slug FROM d0)
        ORDER BY market_slug)
SELECT d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
       d.us_market_slug slug, d.holding_side side, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM d0 d
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.us_market_slug
 ORDER BY d.decided_at DESC)
SELECT 'ev' q, (SELECT count(*) FROM a) a_rows, (SELECT count(*) FROM b) b_rows,
  (SELECT count(*) FROM (SELECT decision_id, at, strategy, slug, side, bid, ask FROM a EXCEPT ALL SELECT decision_id, at, strategy, slug, side, bid, ask FROM b) x) a_minus_b,
  (SELECT count(*) FROM (SELECT decision_id, at, strategy, slug, side, bid, ask FROM b EXCEPT ALL SELECT decision_id, at, strategy, slug, side, bid, ask FROM a) y) b_minus_a,
  (SELECT count(*) FROM a JOIN b USING (decision_id) WHERE a.event_slug IS DISTINCT FROM b.event_slug) event_slug_differs;

EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
WITH d0 AS (
  SELECT DISTINCT ON (d.strategy, d.us_market_slug, d.holding_side)
         d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
         d.us_market_slug slug, d.holding_side side, d.p_blended p_blend,
         d.p_pinnacle p_pin, (d.economics->>'probability')::float8 p_used,
         d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => 30)
   ORDER BY d.strategy, d.us_market_slug, d.holding_side, d.decided_at),
slugs AS (SELECT DISTINCT slug FROM d0),
lab AS (
  SELECT us_market_slug slug,
         CASE WHEN holding_side='LONG' THEN payout_per_contract
              ELSE 1 - payout_per_contract END::float8 y, settled_at at
    FROM paper_settlements WHERE outcome IN ('WON','LOST')
     AND us_market_slug IN (SELECT slug FROM slugs)
  UNION ALL
  SELECT us_market_slug, CASE WHEN buy_intent ILIKE '%SHORT%' THEN 1 - outcome
                              ELSE outcome END::float8, outcome_at
    FROM external_valuations WHERE outcome_known AND outcome IN (0,1)
     AND us_market_slug IN (SELECT slug FROM slugs)),
lab2 AS (SELECT slug, min(y) y_long, min(at) settled_at
           FROM lab GROUP BY slug HAVING min(y) = max(y)),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap WHERE market_slug IN (SELECT slug FROM lab2)
        ORDER BY market_slug)
SELECT d.*, l.y_long, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM d0 d JOIN lab2 l ON l.slug = d.slug
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.slug
 LIMIT 5000;

EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
WITH d0 AS (
  SELECT d.decision_id, d.decided_at, d.strategy, d.us_market_slug,
         d.holding_side, d.book_obs_id
    FROM paper_decisions d
   WHERE d.verdict = 'ENTER' AND d.book_obs_id IS NOT NULL
     AND d.decided_at > now() - make_interval(days => 30)
   ORDER BY d.decided_at DESC
   LIMIT 5000),
pm AS (SELECT DISTINCT ON (market_slug) market_slug, event_slug
         FROM us_premap
        WHERE market_slug IN (SELECT us_market_slug FROM d0)
        ORDER BY market_slug)
SELECT d.decision_id, extract(epoch FROM d.decided_at) at, d.strategy,
       d.us_market_slug slug, d.holding_side side, pm.event_slug,
  (SELECT max((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.bids)='array' THEN o.bids ELSE '[]' END) l) bid,
  (SELECT min((l->'px'->>'value')::float8) FROM jsonb_array_elements(
      CASE WHEN jsonb_typeof(o.offers)='array' THEN o.offers ELSE '[]' END) l) ask
  FROM d0 d
  LEFT JOIN paper_book_observations o ON o.obs_id = d.book_obs_id
                                     AND o.error IS NULL
  LEFT JOIN pm ON pm.market_slug = d.us_market_slug
 ORDER BY d.decided_at DESC;
