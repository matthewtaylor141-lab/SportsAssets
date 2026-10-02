-- Research turns answered in RECORDS_ONLY mode (read-only): which guard or
-- provider failure replaced the model's answer, per persona chat message.
\echo '== P0 · research-task chat replies and their provider outcome =='
SELECT m.at, m.message_id, m.agent_id, m.status,
       m.provider->>'mode' AS mode,
       m.provider->>'failure' AS failure,
       m.provider->'ungrounded' AS ungrounded,
       left(m.provider->>'ungrounded_context', 600) AS ungrounded_context,
       m.request_id
  FROM agent_chat_messages m
 WHERE m.role = 'ASSISTANT'
   AND m.request_id LIKE 'capreview:%'
   AND m.at > now() - interval '1 day'
 ORDER BY m.at;

\echo '== P1 · how many idempotent request rows each research request id holds =='
SELECT m.request_id, count(*) AS assistant_messages,
       min(m.at) AS first_at, max(m.at) AS last_at
  FROM agent_chat_messages m
 WHERE m.role = 'ASSISTANT' AND m.request_id LIKE 'capreview:%'
   AND m.at > now() - interval '1 day'
 GROUP BY 1 ORDER BY 3;
