-- FINAL DELIVERY CONTRACT items 3-4 readback, part C (read-only audit):
-- the agent blocks carried on every canonical decision intent, by status.
-- SELECT only.

\echo C1 CANONICAL DECISION INTENTS: agent block status counts (Allie, Archer, Derek, Karen)
SELECT 'allie' AS block, coalesce(allie ->> 'status', '(no status key)') AS status, count(*) AS n, max(created_at) AS newest
  FROM canonical_decision_intents GROUP BY 1, 2
UNION ALL
SELECT 'archer', coalesce(eddie ->> 'status', '(no status key)'), count(*), max(created_at) FROM canonical_decision_intents GROUP BY 1, 2
UNION ALL
SELECT 'derek', coalesce(derek ->> 'status', derek ->> 'verdict', '(no status key)'), count(*), max(created_at) FROM canonical_decision_intents GROUP BY 1, 2
UNION ALL
SELECT 'karen', coalesce(karen ->> 'status', karen ->> 'state', '(no status key)'), count(*), max(created_at) FROM canonical_decision_intents GROUP BY 1, 2
ORDER BY 1, 3 DESC;

\echo C2 ALLIE BLOCK keys on the canonical intents whose block has no status key (top 3 key sets)
SELECT (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(allie) k) AS keys, count(*) AS n
  FROM canonical_decision_intents WHERE jsonb_typeof(allie) = 'object' AND NOT allie ? 'status'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 3;
