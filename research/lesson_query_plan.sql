-- Office v5 agent memory: plan and latency of the exact LESSON_QUERY per agent,
-- plus what the selector would see (read-only).
\echo '== M0 · lessons per agent: rows, distinct series, latest-version rows, basis, provenance shape =='
SELECT agent_id, count(*) AS rows, count(DISTINCT series_key) AS series,
       count(*) FILTER (WHERE basis = 'FORWARD_RECORDS_ONLY') AS forward_basis,
       count(*) FILTER (WHERE (provenance->>'ids_sha256') ~ '^[a-fA-F0-9]{64}$'
                          AND jsonb_typeof(provenance->'record_count') = 'number'
                          AND (provenance->>'record_count')::numeric >= 1) AS provenance_ok,
       count(*) FILTER (WHERE window_end > now() OR learned_at > now()) AS future_stamped,
       max(learned_at) AS newest
  FROM paper_agent_lessons WHERE account_id = 'paper_acct_main' GROUP BY 1 ORDER BY 1;

\echo '== M1 · EXPLAIN ANALYZE of the exact v5 LESSON_QUERY (XAVIER, limit 48) =='
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT l.lesson_id,l.account_id,l.agent_id,l.kind,l.strategy,l.series_key,
       l.version,l.statement,l.window_end,l.learned_at,l.provenance,
       l.evidence_category,l.basis,l.improvement_task_id
FROM paper_agent_lessons l
WHERE l.account_id='paper_acct_main' AND l.agent_id='XAVIER'
  AND NOT EXISTS (SELECT 1 FROM paper_agent_lessons n
                  WHERE n.account_id=l.account_id AND n.series_key=l.series_key
                    AND n.version>l.version)
ORDER BY l.learned_at DESC,l.lesson_id
LIMIT 48;

\echo '== M2 · EXPLAIN ANALYZE for DEREK and AUDREY (timing only) =='
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON, TIMING ON)
SELECT l.lesson_id FROM paper_agent_lessons l
WHERE l.account_id='paper_acct_main' AND l.agent_id='DEREK'
  AND NOT EXISTS (SELECT 1 FROM paper_agent_lessons n WHERE n.account_id=l.account_id
                  AND n.series_key=l.series_key AND n.version>l.version)
ORDER BY l.learned_at DESC,l.lesson_id LIMIT 48;
EXPLAIN (ANALYZE, COSTS OFF, SUMMARY ON, TIMING ON)
SELECT l.lesson_id FROM paper_agent_lessons l
WHERE l.account_id='paper_acct_main' AND l.agent_id='AUDREY'
  AND NOT EXISTS (SELECT 1 FROM paper_agent_lessons n WHERE n.account_id=l.account_id
                  AND n.series_key=l.series_key AND n.version>l.version)
ORDER BY l.learned_at DESC,l.lesson_id LIMIT 48;
