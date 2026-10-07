-- READ-ONLY. The complete-packet Xavier reviews since 10af406 boot: which
-- probability made them current, what was recommended, what was done.
SELECT reviewed_at, group_id, recommendation,
       coalesce(measure->>'probability_source', measure->>'source') src,
       round((measure->>'probability_age_s')::numeric, 1) age_s,
       measure->'feed'->>'identity_basis' identity,
       action->>'taken' taken, selection->>'mechanical_selection' sel
  FROM paper_xavier_reviews
 WHERE reviewed_at > '2026-10-07 04:37:26+00'
   AND (selection->'management_packet'->'gate'->>'complete')::boolean
 ORDER BY reviewed_at;
SELECT o.role, o.state, o.direction, count(*), max(o.updated_at)
  FROM paper_orders o WHERE o.created_at > '2026-10-07 04:37:26+00'
   AND o.role IN ('EXIT','REDUCE') GROUP BY 1,2,3;
