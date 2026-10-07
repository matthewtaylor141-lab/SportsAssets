-- READ-ONLY. The venue-settlement join queue vs the open PAPER positions on
-- ended games: queue depth, how many held contracts are in it, when each
-- was last asked, what the venue last said. Every statement is a SELECT.
SELECT count(*) AS unjoined,
       count(*) FILTER (WHERE settlement_read_at IS NULL) AS never_asked,
       min(decided_at) AS oldest
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND outcome_known = FALSE
   AND outcome_basis IS NULL AND us_market_slug IS NOT NULL
   AND decided_at < now() - interval '2 hours';
WITH held AS (
  SELECT DISTINCT o.us_market_slug
    FROM paper_orders o
    JOIN paper_xavier_reviews r ON r.group_id = o.group_id
   WHERE r.reviewed_at > now() - interval '1 hour' AND o.role = 'ENTRY'
     AND (r.selection->'management_packet'->'book'->>'mark_class') = 'EXTERNAL_UNAVAILABLE')
SELECT h.us_market_slug,
       count(v.id) AS valuations,
       count(v.id) FILTER (WHERE v.outcome_basis IS NULL AND NOT v.outcome_known) AS unjoined,
       max(v.settlement_read_at) AS last_asked,
       left(max(v.settlement_read::text), 40) AS last_read,
       max(v.outcome_basis) AS basis
  FROM held h LEFT JOIN external_valuations v ON v.us_market_slug = h.us_market_slug
 GROUP BY 1 ORDER BY 1 LIMIT 40;
SELECT date_trunc('hour', settlement_read_at) h, count(*)
  FROM external_valuations WHERE settlement_read_at > now() - interval '12 hours'
 GROUP BY 1 ORDER BY 1 DESC;
SELECT count(*), max(settled_at) FROM paper_settlements WHERE settled_at > now() - interval '24 hours';
