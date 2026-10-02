-- READ-ONLY. The stale-probability guard (76f0119) in production: every
-- Xavier review since :since (the deploy) whose measure was stale or absent,
-- whether EXIT/REDUCE were moved to not_rankable with the guard's blocker,
-- what was selected, and whether any EXIT/REDUCE order followed. Plus the
-- standing protection kept.
\echo '== G0 · who holds the ext writer lock now (backend start vs the fa41a91 deploy, live 2026-10-02T00:09:36Z) =='
SELECT l.pid, a.backend_start, a.application_name, a.backend_start > '2026-10-02 00:09:36+00' AS started_after_deploy
  FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
 WHERE l.locktype = 'advisory' AND l.granted
   AND ((l.classid::bigint << 32) | l.objid::bigint) = 7723901544120034;

\echo '== G1b · reviews by minute: guard blocker vs sale still rankable, stale only =='
SELECT date_trunc('minute', reviewed_at) AS minute, count(*) AS stale_reviews,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(alternatives->'not_rankable', '[]'::jsonb)) b
                WHERE b->>'blocker' = 'MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE')) AS with_guard_blocker,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(alternatives->'candidates', '[]'::jsonb)) c
                WHERE c->>'action' IN ('EXIT', 'REDUCE'))) AS sale_rankable
  FROM paper_xavier_reviews
 WHERE reviewed_at >= '2026-10-01 23:44:00+00'
   AND (measure->>'stale' = 'true' OR measure->>'p' IS NULL)
 GROUP BY 1 ORDER BY 1;

\echo '== G1 · reviews since the deploy by freshness, blocker present, selection, action =='
SELECT coalesce(measure->>'stale', '?') AS stale,
       (measure->>'p') IS NULL AS p_absent,
       EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(alternatives->'not_rankable', '[]'::jsonb)) b
                WHERE b->>'blocker' = 'MEASURE_STALE_OR_ABSENT_NO_DISCRETIONARY_SALE') AS guard_blocker,
       EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(alternatives->'candidates', '[]'::jsonb)) c
                WHERE c->>'action' IN ('EXIT', 'REDUCE')) AS sale_still_rankable,
       recommendation, action->>'taken' AS action_taken, count(*),
       min(reviewed_at) AS first_at, max(reviewed_at) AS last_at
  FROM paper_xavier_reviews
 WHERE reviewed_at >= '2026-10-01 23:44:10+00'
 GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 3, 4;

\echo '== G2 · one guarded review in full (latest) =='
SELECT review_id, group_id, reviewed_at, recommendation, measure->>'source' AS source,
       measure->>'stale' AS stale, measure->>'p' AS p,
       left((alternatives->'not_rankable')::text, 600) AS not_rankable,
       left(action::text, 300) AS action, left(standing::text, 300) AS standing
  FROM paper_xavier_reviews
 WHERE reviewed_at >= '2026-10-01 23:44:10+00'
   AND (measure->>'stale' = 'true' OR measure->>'p' IS NULL)
 ORDER BY reviewed_at DESC LIMIT 1;

\echo '== G3 · EXIT/REDUCE orders since the deploy (expected: none driven by a stale measure) =='
SELECT order_id, group_id, role, state, qty, limit_price, decided_at, strategy
  FROM paper_orders
 WHERE role IN ('EXIT', 'REDUCE') AND decided_at >= '2026-10-01 23:44:10+00'
 ORDER BY decided_at;

\echo '== G4 · standing protective orders per open group (kept by HOLD) =='
SELECT o.group_id, o.order_id, o.role, o.state, o.qty, o.limit_price, o.decided_at
  FROM paper_orders o
 WHERE o.role NOT IN ('ENTRY') AND o.state IN ('RESTING', 'WORKING', 'OPEN', 'PARTIAL')
 ORDER BY o.decided_at DESC LIMIT 20;
