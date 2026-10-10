-- RC6.3 capability lane, readback 3: the server-side cost of the persona
-- position search for one real capability context, as the code reads it
-- today (JSON-text match over every row) and as the rc6.3 change reads it
-- (equality on the key columns). EXPLAIN ANALYZE of SELECT statements only:
-- nothing is written. Plus sizes of the other fact tables. SELECT only.
\echo E0 sizes of the non-paper fact tables
SELECT c.relname AS tbl, c.reltuples::bigint AS est_rows, pg_size_pretty(pg_total_relation_size(c.oid)) AS total
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=current_schema() AND c.relkind='r'
  AND c.relname IN ('derek_entry_decisions','audrey_audit_reports','bettor_funded_intents',
                    'bettor_xavier_decisions','bettor_standing_order_plans','agent_tasks')
ORDER BY 1;

\echo E1 the newest handoff and settlement capability contexts
SELECT split_part(spec->>'source_key',':',1) AS src, spec->'context' AS context, created_at
FROM (SELECT DISTINCT ON (split_part(spec->>'source_key',':',1)) spec, created_at
      FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1'
        AND split_part(spec->>'source_key',':',1) IN ('handoff','settlement')
      ORDER BY split_part(spec->>'source_key',':',1), created_at DESC) z;

\echo E2 BEFORE Xavier current-decision read for the handoff context (JSON-text match)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id', spec->'context'->>'decision_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'handoff:%'
           ORDER BY created_at DESC LIMIT 1),
     p AS (SELECT array_agg('%'||i||'%') AS pats FROM c, unnest(c.ids) i)
SELECT r.review_id, r.group_id, r.reviewed_at, r.trigger, r.recommendation, r.refusal, r.measure, r.selection
FROM paper_xavier_reviews r
WHERE r.group_id IN (SELECT x.group_id FROM paper_xavier_reviews x
                     WHERE to_jsonb(x)::text ILIKE ANY('{}'::text[]) OR to_jsonb(x)::text LIKE ANY(ARRAY(SELECT unnest(pats) FROM p))
                     UNION SELECT o.group_id FROM paper_orders o
                     WHERE o.us_market_slug ILIKE ANY('{}'::text[]) OR o.group_id LIKE ANY(ARRAY(SELECT unnest(pats) FROM p)))
ORDER BY r.group_id, r.reviewed_at DESC, r.review_id DESC LIMIT 40;

\echo E3 AFTER Xavier current-decision read for the handoff context (key columns)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id', spec->'context'->>'decision_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'handoff:%'
           ORDER BY created_at DESC LIMIT 1)
SELECT r.review_id, r.group_id, r.reviewed_at, r.trigger, r.recommendation, r.refusal, r.measure, r.selection
FROM paper_xavier_reviews r
WHERE r.group_id IN (SELECT g FROM unnest(ARRAY(SELECT unnest(ids) FROM c)) AS g
                     UNION SELECT o.group_id FROM paper_orders o WHERE o.decision_id = ANY(ARRAY(SELECT unnest(ids) FROM c)))
ORDER BY r.group_id, r.reviewed_at DESC, r.review_id DESC LIMIT 40;

\echo E4 BEFORE paper_decisions search for the handoff context
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id', spec->'context'->>'decision_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'handoff:%'
           ORDER BY created_at DESC LIMIT 1),
     p AS (SELECT array_agg('%'||i||'%') AS pats FROM c, unnest(c.ids) i)
SELECT to_jsonb(x) AS j FROM paper_decisions x
WHERE to_jsonb(x)::text ILIKE ANY('{}'::text[]) OR to_jsonb(x)::text LIKE ANY(ARRAY(SELECT unnest(pats) FROM p)) LIMIT 5;

\echo E5 AFTER paper_decisions search for the handoff context
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id', spec->'context'->>'decision_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'handoff:%'
           ORDER BY created_at DESC LIMIT 1)
SELECT to_jsonb(x) AS j FROM paper_decisions x WHERE x.decision_id = ANY(ARRAY(SELECT unnest(ids) FROM c)) LIMIT 5;

\echo E6 BEFORE paper_book_observations search for the settlement context (no key column: not read after)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'settlement:%'
           ORDER BY created_at DESC LIMIT 1),
     p AS (SELECT array_agg('%'||i||'%') AS pats FROM c, unnest(c.ids) i)
SELECT to_jsonb(x) AS j FROM paper_book_observations x
WHERE to_jsonb(x)::text ILIKE ANY('{}'::text[]) OR to_jsonb(x)::text LIKE ANY(ARRAY(SELECT unnest(pats) FROM p)) LIMIT 5;

\echo E7 BEFORE paper_order_events search for the settlement context (no key column: not read after)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'settlement:%'
           ORDER BY created_at DESC LIMIT 1),
     p AS (SELECT array_agg('%'||i||'%') AS pats FROM c, unnest(c.ids) i)
SELECT to_jsonb(x) AS j FROM paper_order_events x
WHERE to_jsonb(x)::text ILIKE ANY('{}'::text[]) OR to_jsonb(x)::text LIKE ANY(ARRAY(SELECT unnest(pats) FROM p)) LIMIT 5;

\echo E8 BEFORE paper_management_refusals search for the settlement context
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'settlement:%'
           ORDER BY created_at DESC LIMIT 1),
     p AS (SELECT array_agg('%'||i||'%') AS pats FROM c, unnest(c.ids) i)
SELECT to_jsonb(x) AS j FROM paper_management_refusals x
WHERE to_jsonb(x)::text ILIKE ANY('{}'::text[]) OR to_jsonb(x)::text LIKE ANY(ARRAY(SELECT unnest(pats) FROM p)) LIMIT 5;

\echo E9 AFTER paper_management_refusals search for the settlement context (group_id, no index)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'settlement:%'
           ORDER BY created_at DESC LIMIT 1)
SELECT to_jsonb(x) AS j FROM paper_management_refusals x WHERE x.group_id = ANY(ARRAY(SELECT unnest(ids) FROM c)) LIMIT 5;

\echo E10 AFTER paper_entry_refusal_census and paper_evaluation_attempts search for the handoff context (decision_id, no index)
EXPLAIN (ANALYZE, BUFFERS)
WITH c AS (SELECT ARRAY[spec->'context'->>'position_id', spec->'context'->>'decision_id'] AS ids
           FROM agent_tasks WHERE kind='AGENT_CAPABILITY_REVIEW_V1' AND spec->>'source_key' LIKE 'handoff:%'
           ORDER BY created_at DESC LIMIT 1)
SELECT (SELECT count(*) FROM (SELECT 1 FROM paper_entry_refusal_census x WHERE x.decision_id = ANY(ARRAY(SELECT unnest(ids) FROM c)) LIMIT 5) a) AS census,
       (SELECT count(*) FROM (SELECT 1 FROM paper_evaluation_attempts x WHERE x.decision_id = ANY(ARRAY(SELECT unnest(ids) FROM c)) LIMIT 5) b) AS attempts;
