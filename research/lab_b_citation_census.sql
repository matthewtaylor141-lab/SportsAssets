-- LAB-B CITATION INTEGRITY: CENSUS OF THE STORED AGENT ANSWERS (SELECT only).
--
-- PM question E asks how often an agent's sentence cites the wrong
-- supporting fact. The persona chat (agents/persona_chat.py, migration 180)
-- stores every answer's visible body and the facts it CITED (`facts`): the
-- full numbered fact list the answer was composed from is NOT persisted.
-- This census sizes what can be measured before the answers themselves are
-- read: how many answers per agent, by status / outcome / provider mode and
-- failure, how many carry [F#] citations and stored facts, the payload
-- size, and whether any table anywhere persists a fact set.
-- Bounded: aggregates only, plus one key-listing sample (LIMIT).

\echo === read time
SELECT now() AS read_at;

\echo === assistant answers by agent, status, outcome
SELECT agent_id, status, outcome, count(*) AS n,
       min(at) AS first_at, max(at) AS last_at,
       count(*) FILTER (WHERE jsonb_typeof(facts) = 'array'
                          AND jsonb_array_length(facts) > 0) AS with_facts,
       count(*) FILTER (WHERE body ~ '\[F[0-9]+\]') AS with_citations,
       round(avg(length(body))) AS avg_body_chars,
       round(avg(length(facts::text))) AS avg_facts_chars
  FROM agent_chat_messages
 WHERE role = 'ASSISTANT'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo === provider mode and failure class per agent (completed answers)
SELECT agent_id, provider->>'mode' AS mode, provider->>'failure' AS failure,
       count(*) AS n
  FROM agent_chat_messages
 WHERE role = 'ASSISTANT' AND status = 'COMPLETE'
 GROUP BY 1, 2, 3
 ORDER BY 1, 2, 3;

\echo === answers per day and agent (completed, cited)
SELECT date_trunc('day', at)::date AS day, agent_id, count(*) AS n,
       count(*) FILTER (WHERE body ~ '\[F[0-9]+\]') AS cited,
       count(*) FILTER (WHERE provider->>'mode' = 'LLM') AS llm
  FROM agent_chat_messages
 WHERE role = 'ASSISTANT' AND status = 'COMPLETE'
 GROUP BY 1, 2
 ORDER BY 1, 2;

\echo === requester roles
SELECT requester_role, count(*) AS n
  FROM agent_chat_messages
 WHERE role = 'ASSISTANT'
 GROUP BY 1
 ORDER BY 2 DESC;

\echo === keys of a stored fact (sample of 20 answers)
SELECT DISTINCT k
  FROM (SELECT facts->0 AS f0
          FROM agent_chat_messages
         WHERE role = 'ASSISTANT' AND jsonb_typeof(facts) = 'array'
           AND jsonb_array_length(facts) > 0
         ORDER BY at DESC
         LIMIT 20) s,
       LATERAL jsonb_object_keys(s.f0) AS k
 ORDER BY k;

\echo === payload size of completed answers with citations
SELECT count(*) AS answers, sum(length(body)) AS body_chars,
       sum(length(facts::text)) AS facts_chars,
       max(length(body) + length(facts::text)) AS max_row_chars
  FROM agent_chat_messages
 WHERE role = 'ASSISTANT' AND status = 'COMPLETE'
   AND body ~ '\[F[0-9]+\]';

\echo === columns anywhere that could persist a fact set
SELECT table_name, column_name, data_type
  FROM information_schema.columns
 WHERE table_schema = current_schema()
   AND (column_name ILIKE '%fact%' OR column_name ILIKE '%citation%')
 ORDER BY 1, 2;
