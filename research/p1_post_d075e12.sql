-- READ-ONLY. Evidence after release d075e12 (workers live 2026-10-07 00:27Z):
-- Xavier packets on reviews written by the new workers, probability
-- sources, settlement drainage, value-add, mirror heartbeat, settlement
-- states. Every statement is a SELECT.
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews WHERE reviewed_at > '2026-10-07 00:27:30+00'
   ORDER BY group_id, reviewed_at DESC)
SELECT count(*) AS groups_reviewed_since_boot,
       count(*) FILTER (WHERE (selection->'management_packet'->'gate'->>'complete')::boolean) AS complete,
       count(*) FILTER (WHERE selection->'management_packet'->'book'->>'mark_class' = 'EXTERNAL_UNAVAILABLE') AS market_closed,
       count(*) FILTER (WHERE measure->>'evidence_state' = 'FRESH_CURRENT_PROBABILITY') AS fresh_probability
  FROM latest;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, measure, selection
    FROM paper_xavier_reviews WHERE reviewed_at > '2026-10-07 00:27:30+00'
   ORDER BY group_id, reviewed_at DESC)
SELECT selection->'management_packet'->'book'->>'mark_class' AS book,
       measure->>'probability_source' AS src,
       coalesce(measure->>'feed_refusal','-') AS feed_refusal,
       coalesce(measure->'feed'->>'identity_basis', measure->'feed_detail'->>'identity_basis','-') AS identity,
       count(*)
  FROM latest GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 30;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, selection
    FROM paper_xavier_reviews WHERE reviewed_at > '2026-10-07 00:27:30+00'
   ORDER BY group_id, reviewed_at DESC)
SELECT m.code, count(*) FROM latest,
       jsonb_array_elements_text(coalesce(selection->'management_packet'->'gate'->'missing','[]')) m(code)
 GROUP BY 1 ORDER BY 2 DESC;
SELECT count(*) AS unjoined,
       count(*) FILTER (WHERE settlement_read_at IS NULL) AS never_asked
  FROM external_valuations
 WHERE experiment_id = 'EXT_PINNACLE_DEVIG_V1_SHADOW' AND outcome_known = FALSE
   AND outcome_basis IS NULL AND us_market_slug IS NOT NULL
   AND decided_at < now() - interval '2 hours';
SELECT date_trunc('hour', settlement_read_at) h, count(*) rows_asked,
       count(DISTINCT us_market_slug) markets_asked,
       count(*) FILTER (WHERE outcome_basis IS NOT NULL) joined
  FROM external_valuations WHERE settlement_read_at > now() - interval '4 hours'
 GROUP BY 1 ORDER BY 1 DESC;
SELECT count(*) AS settled_since_boot, max(settled_at) FROM paper_settlements
 WHERE settled_at > '2026-10-07 00:27:30+00';
SELECT status, count(*), max(computed_at) FROM xavier_value_add GROUP BY 1;
SELECT service, status, beat_at, left(detail::text, 1500) FROM service_heartbeats
 WHERE service = 'mirror_shadow';
SELECT coalesce(settlement_state,'NULL') s, count(*) FROM market_plane_registry
 WHERE active GROUP BY 1 ORDER BY 2 DESC;
SELECT kind, count(*), max(at) FROM market_plane_events
 WHERE at > now() - interval '1 hour' GROUP BY 1;
SELECT venue, count(*), count(*) FILTER (WHERE rules_published) published
  FROM market_plane_rules GROUP BY 1;
SELECT key, left(value::text, 400) FROM ingestion_state WHERE key = 'pinnapi_discovery_watch';
