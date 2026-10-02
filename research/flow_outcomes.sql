-- Research flows' recorded outcomes (read-only): the Xavier v5 flow and the
-- owner's set (b), each turn's assignee, status, mode, genuineness, answer.
\echo '== F0 · turns =='
SELECT t.spec->>'source_key' AS flow, t.task_id, t.assignee, t.status,
       t.outcome->>'provider_mode' AS mode, t.outcome->>'reviewed' AS reviewed,
       t.outcome->>'error' AS error, t.outcome->>'message_id' AS message_id,
       t.updated_at
  FROM agent_tasks t
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND (t.spec->>'source_key' LIKE '%management-execution-exits-v5%'
        OR t.spec->>'source_key' LIKE '%owner-20261002b-%')
 ORDER BY t.spec->>'source_key', jsonb_array_length(coalesce(t.spec->'dependencies','[]'::jsonb));
\echo '== F1 · answers (first 1500 chars) =='
SELECT t.task_id, t.assignee, t.status, left(t.outcome->>'answer', 1500) AS answer
  FROM agent_tasks t
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1' AND t.outcome ? 'answer'
   AND (t.spec->>'source_key' LIKE '%management-execution-exits-v5%'
        OR t.spec->>'source_key' LIKE '%owner-20261002b-%')
 ORDER BY t.updated_at;
