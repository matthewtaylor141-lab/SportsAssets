-- Did the evidence stored for Xavier's owner-goal turns contain the
-- recorded counterfactual -8.42? (read-only)
\echo '== E0 · per evidence item: tool, size, mentions 8.42 / 11.33 / 2.91 =='
SELECT t.task_id, t.spec->>'source_key' AS flow, i.ord - 1 AS item,
       i.elem->>'tool' AS tool, length(i.elem::text) AS chars,
       i.elem::text LIKE '%8.42%' AS has_8_42,
       i.elem::text LIKE '%11.33%' AS has_11_33,
       i.elem::text LIKE '%2.91%' AS has_2_91,
       jsonb_array_length(CASE WHEN jsonb_typeof(i.elem->'data') = 'array' THEN i.elem->'data' ELSE '[]'::jsonb END) AS n_rows
  FROM agent_tasks t
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.outcome->'investigation', '[]'::jsonb))
       WITH ORDINALITY AS i(elem, ord)
 WHERE t.spec->>'source_key' LIKE 'management:owner-20261002-management-execution-exits-%'
   AND t.assignee = 'XAVIER'
 ORDER BY t.spec->>'source_key', i.ord;

\echo '== E1 · lessons item of the v4 turn: kinds and learned_at of the rows it holds =='
SELECT r->>'lesson_id' AS lesson_id, r->>'kind' AS kind, r->>'learned_at' AS learned_at,
       (r->'metrics')::text LIKE '%8.42%' AS has_8_42
  FROM agent_tasks t
  CROSS JOIN LATERAL jsonb_array_elements(COALESCE(t.outcome->'investigation', '[]'::jsonb)) AS e(elem)
  CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(e.elem->'data') = 'array' THEN e.elem->'data' ELSE '[]'::jsonb END) AS r
 WHERE t.spec->>'source_key' = 'management:owner-20261002-management-execution-exits-v4'
   AND t.assignee = 'XAVIER' AND e.elem->>'tool' = 'lessons';
