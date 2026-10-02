-- Agent capability workbench readback (read-only). The control row defaults
-- OFF; absence means the code default ({"enabled": false}) applies.
\echo '== K0 · control and heartbeat rows =='
SELECT key, value FROM ingestion_state
 WHERE key IN ('agent.capabilities.v1:paper_acct_main',
               'agent.capabilities.heartbeat:paper_acct_main');

\echo '== K1 · capability work by kind and status =='
SELECT kind, status, count(*) AS n, max(updated_at) AS latest
  FROM agent_tasks WHERE kind LIKE 'CAPABILITY%' GROUP BY 1, 2 ORDER BY 1, 2;
