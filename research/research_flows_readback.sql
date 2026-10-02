-- The three owner investigations (2026-10-02, request ids owner-20261002-*-v2)
-- and their peer review / audit turns, read-only. Shows who owns each turn,
-- what it depends on, its status, the recorded answer (truncated) and the
-- source records it cited; then every task event in order.
\echo '== R0 · control and heartbeat =='
SELECT key, value FROM ingestion_state
 WHERE key IN ('agent.capabilities.v1:paper_acct_main',
               'agent.capabilities.heartbeat:paper_acct_main');

\echo '== R1 · the flows, turn by turn =='
SELECT t.spec->>'source_key' AS flow, t.task_id, t.assignee, t.status,
       jsonb_array_length(COALESCE(t.spec->'dependencies', '[]'::jsonb)) AS n_deps,
       (t.spec->>'attempts')::int AS attempts,
       t.outcome->>'provider_mode' AS mode,
       t.outcome->>'error' AS error,
       t.outcome->>'message_id' AS message_id,
       jsonb_array_length(COALESCE(t.outcome->'investigation', '[]'::jsonb)) AS n_evidence,
       t.updated_at
  FROM agent_tasks t
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND t.created_at > now() - interval '1 day'
 ORDER BY t.spec->>'source_key', jsonb_array_length(COALESCE(t.spec->'dependencies', '[]'::jsonb));

\echo '== R2 · recorded answers (first 1800 chars) and cited sources =='
SELECT t.task_id, t.assignee, t.status,
       left(t.outcome->>'answer', 1800) AS answer,
       t.outcome->'source_ids' AS source_ids
  FROM agent_tasks t
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND t.created_at > now() - interval '1 day'
   AND t.outcome ? 'answer'
 ORDER BY t.updated_at;

\echo '== R3 · task events =='
SELECT e.at, e.task_id, e.kind, e.actor,
       left(COALESCE(e.detail->>'error', e.detail->>'attempt', ''), 120) AS note
  FROM agent_task_events e
  JOIN agent_tasks t ON t.task_id = e.task_id
 WHERE t.kind = 'AGENT_CAPABILITY_REVIEW_V1'
   AND t.created_at > now() - interval '1 day'
 ORDER BY e.at, e.task_id;
