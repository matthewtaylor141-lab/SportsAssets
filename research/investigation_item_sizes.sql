-- Size of each stored investigation item per research task (read-only). An
-- item over 9000 characters of JSON is left out of the persona fact bundle
-- (capability_tools.context_facts), so its figures cannot ground an answer.
\echo '== S0 · investigation item sizes, owner flows =='
SELECT t.task_id, t.assignee, t.status, i.ord - 1 AS item,
       i.elem->>'tool' AS tool, i.elem->>'status' AS item_status,
       length(i.elem::text) AS json_chars,
       length(i.elem::text) > 9000 AS dropped_from_facts
  FROM agent_tasks t
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.outcome->'investigation', '[]'::jsonb))
       WITH ORDINALITY AS i(elem, ord)
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND t.spec->>'source_key' LIKE 'management:owner-20261002-%'
 ORDER BY t.spec->>'source_key', t.task_id, i.ord;
