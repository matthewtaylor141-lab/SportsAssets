-- Does a recorded lesson hold the figures the persona guard flagged in
-- Xavier's research answers (8.42, 11.33, 2.91)? Read-only.
\echo '== L0 · Xavier lessons whose statement or metrics mention the flagged figures =='
SELECT lesson_id, kind, strategy, version, learned_at,
       left(statement, 700) AS statement,
       left(metrics::text, 700) AS metrics
  FROM paper_agent_lessons
 WHERE agent_id = 'XAVIER'
   AND (statement ~ '8\.42|11\.33|2\.91' OR metrics::text ~ '8\.42|11\.33|2\.91')
 ORDER BY learned_at DESC
 LIMIT 10;

\echo '== L1 · latest Xavier lessons (what the lessons tool returns, newest first) =='
SELECT lesson_id, kind, learned_at, left(statement, 300) AS statement
  FROM paper_agent_lessons
 WHERE agent_id = 'XAVIER'
 ORDER BY learned_at DESC
 LIMIT 10;
