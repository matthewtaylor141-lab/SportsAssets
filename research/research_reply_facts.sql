-- Which facts each research-task reply was checked against (read-only):
-- did the stored investigation evidence reach the bundle?
\echo '== F0 · research replies: facts, investigation facts, missing notes =='
SELECT m.at, m.message_id, m.agent_id, m.provider->>'mode' AS mode,
       m.provider->>'failure' AS failure,
       jsonb_array_length(COALESCE(m.facts, '[]'::jsonb)) AS n_facts,
       (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.facts, '[]'::jsonb)) f
         WHERE f->>'field' LIKE 'investigation_%') AS n_investigation_facts,
       (SELECT count(*) FROM jsonb_array_elements(COALESCE(m.facts, '[]'::jsonb)) f
         WHERE f->>'text' LIKE '%8.42%') AS facts_with_8_42,
       left(COALESCE(m.missing_evidence, '[]'::jsonb)::text, 300) AS missing
  FROM agent_chat_messages m
 WHERE m.role = 'ASSISTANT' AND m.request_id LIKE 'capreview:%'
   AND m.at > now() - interval '1 day' AND m.agent_id = 'XAVIER'
 ORDER BY m.at;
