-- LAB-B CITATION INTEGRITY: STORED AGENT ANSWERS, PART 1 OF 4 (SELECT only).
-- The FULL-profile verifier (ids, timestamps, codes, measure words, agent
-- attribution) runs in Python; these rows are its input: each COMPLETE,
-- cited assistant answer (agent_chat_messages, migration 180) as one JSON
-- line -- message id, agent, provider mode, time, the question it answered,
-- the visible body and the facts it CITED (the only facts stored). Rows
-- ordered by (at, message_id) and cut into four bounded parts of 85.

\echo === part 1: answers 0 < n <= 85
SELECT jsonb_build_object(
           'm', z.message_id, 'a', z.agent_id, 'mode', z.mode,
           'at', z.at_epoch, 'q', z.question, 'b', z.body, 'f', z.facts)::text
         AS j
  FROM (SELECT m.message_id, m.agent_id,
               coalesce(m.provider->>'mode', m.outcome) AS mode,
               extract(epoch FROM m.at) AS at_epoch,
               coalesce(u.body, '') AS question, m.body, m.facts,
               row_number() OVER (ORDER BY m.at, m.message_id) AS n
          FROM agent_chat_messages m
          LEFT JOIN agent_chat_messages u ON u.message_id = m.in_reply_to
         WHERE m.role = 'ASSISTANT' AND m.status = 'COMPLETE'
           AND m.body ~ '\[F[0-9]+\]' AND m.at <= now()) z
 WHERE z.n > 0 AND z.n <= 85
 ORDER BY z.n;
