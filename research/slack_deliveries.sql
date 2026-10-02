-- Slack bridge deliveries (read-only): state, agent, thread, Slack ts and the
-- app permalink parts (team, channel, ts are identifiers, not credentials).
\echo '== S0 · control and audit =='
SELECT value FROM ingestion_state WHERE key IN ('agent.slack.bridge', 'agent.slack.updates');
SELECT enabled, actor, changed_at FROM agent_slack_control_audit ORDER BY audit_id DESC LIMIT 5;
\echo '== S1 · deliveries (newest 40) =='
SELECT agent, state, attempts, error_code, left(source_key, 48) AS source,
       requested_by, thread_ts, slack_ts, message_id,
       'https://app.slack.com/client/' || team_id || '/' || channel_id ||
         CASE WHEN slack_ts IS NULL THEN ''
              WHEN thread_ts IS NOT NULL THEN '/thread/' || channel_id || '-' || thread_ts
              ELSE '/p' || replace(slack_ts, '.', '') END AS link,
       created_at, updated_at,
       left(regexp_replace(coalesce(question, ''), '\s+', ' ', 'g'), 120) AS question,
       left(regexp_replace(coalesce(answer, ''), '\s+', ' ', 'g'), 260) AS answer
  FROM agent_slack_delivery ORDER BY created_at DESC LIMIT 40;
\echo '== S2 · by state =='
SELECT agent, state, count(*) FROM agent_slack_delivery GROUP BY 1, 2 ORDER BY 1, 2;
