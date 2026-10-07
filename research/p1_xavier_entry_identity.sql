-- READ-ONLY. For open PAPER groups whose held read refused, the entry
-- valuation's provider / event key / market / line, and how the newest
-- valuation for the same contract was produced. Every statement is a SELECT.
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '3 hours'
   ORDER BY group_id, reviewed_at DESC),
ent AS (
  SELECT l.group_id, l.measure->>'feed_refusal' AS refusal,
         l.selection->'management_packet'->'book'->>'mark_class' AS book,
         d.valuation_id, o.us_market_slug
    FROM latest l
    JOIN LATERAL (SELECT decision_id, us_market_slug FROM paper_orders o
                   WHERE o.group_id = l.group_id AND o.role = 'ENTRY' LIMIT 1) o ON true
    JOIN paper_decisions d ON d.decision_id = o.decision_id)
SELECT e.refusal, e.book, v.provider, v.source_class, v.market, v.period,
       v.line IS NOT NULL AS has_line, v.sport_family,
       left(v.event_key, 40) AS event_key_sample, count(*)
  FROM ent e LEFT JOIN external_valuations v ON v.id = e.valuation_id
 WHERE e.book <> 'EXTERNAL_UNAVAILABLE'
 GROUP BY 1,2,3,4,5,6,7,8,9 ORDER BY 10 DESC LIMIT 50;
SELECT provider, source_class, market, count(*), max(decided_at)
  FROM external_valuations WHERE decided_at > now() - interval '6 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
SELECT key, left(value::text, 400) FROM ingestion_state
 WHERE key ILIKE '%pinnapi%' OR key ILIKE '%ext_pinnacle%' ORDER BY 1 LIMIT 20;
