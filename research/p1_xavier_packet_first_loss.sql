-- READ-ONLY. Xavier's management packet on the latest review of every open
-- PAPER group: which element is missing and the measure's own reason
-- (source, why, feed refusal, age), the book's reason. Every statement is a
-- SELECT.
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, review_id, reviewed_at, trigger,
         recommendation, refusal, measure, selection, strategy
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC)
SELECT count(*) AS groups,
       count(*) FILTER (WHERE (selection->'management_packet'->'gate'->>'complete')::boolean) AS complete,
       max(reviewed_at) AS newest, min(reviewed_at) AS oldest
  FROM latest;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC)
SELECT m.code, count(*)
  FROM latest, jsonb_array_elements_text(
         coalesce(selection->'management_packet'->'gate'->'missing', '[]')) m(code)
 GROUP BY 1 ORDER BY 2 DESC;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC)
SELECT measure->>'evidence_state' AS ev, measure->>'probability_source' AS src,
       coalesce(measure->>'feed_refusal', '-') AS feed_refusal,
       coalesce(measure->>'why', '-') AS why, count(*),
       round(avg((measure->>'probability_age_s')::float)::numeric, 1) AS avg_age
  FROM latest GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 40;
WITH latest AS (
  SELECT DISTINCT ON (group_id) group_id, reviewed_at, measure, selection
    FROM paper_xavier_reviews
   WHERE reviewed_at > now() - interval '6 hours'
   ORDER BY group_id, reviewed_at DESC)
SELECT selection->'management_packet'->'book'->>'mark_class' AS cls,
       coalesce(selection->'management_packet'->'book'->>'reason', '-') AS reason,
       count(*)
  FROM latest GROUP BY 1,2 ORDER BY 3 DESC LIMIT 30;
SELECT jsonb_object_keys(measure) k, count(*) FROM (
  SELECT measure FROM paper_xavier_reviews ORDER BY reviewed_at DESC LIMIT 50) q
 GROUP BY 1 ORDER BY 1;
SELECT left(measure::text, 1500) FROM paper_xavier_reviews
 ORDER BY reviewed_at DESC LIMIT 2;
SELECT left((selection->'management_packet')::text, 1500) FROM paper_xavier_reviews
 ORDER BY reviewed_at DESC LIMIT 2;
SELECT position_kind, evidence_state, coalesce(recommendation,'-'), count(*)
  FROM xavier_management_assessments WHERE assessed_at > now() - interval '24 hours'
 GROUP BY 1,2,3 ORDER BY 4 DESC;
SELECT position_kind, status, count(*), max(computed_at)
  FROM xavier_value_add GROUP BY 1,2;
SELECT count(*), max(created_at) FROM xavier_entry_theses;
