-- READ-ONLY. RC6 lane P0-AGENTS: why Archer's results phase
-- (agents.pos_workflow.attach_results) exceeds its 25 s phase bound on every
-- pass since 2026-10-05 22:25 (run 37936367236: TimeoutError, 265 of 265
-- runs in 24 h). The planner on its two SELECTs (EXPLAIN only, nothing is
-- executed), the sizes it walks, and whether the steps' guard triggers fire.

\echo Q1 sizes
SELECT c.relname, c.reltuples::bigint AS est_rows,
       pg_size_pretty(pg_total_relation_size(c.oid)) AS size
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname IN ('pos_candidate_review_steps', 'pos_candidate_reviews',
                     'eddie_execution_outcomes', 'paper_orders',
                     'paper_xavier_reviews')
 ORDER BY 1;

\echo Q2 step 4 walk (EXPLAIN)
EXPLAIN SELECT s.review_id, x.outcome_id, x.predicted_execution_loss_pp,
       x.realized_execution_loss_pp
  FROM pos_candidate_review_steps s
  JOIN pos_candidate_reviews v USING (review_id)
  JOIN eddie_execution_outcomes x ON x.decision_id = v.decision_id
 WHERE s.seq = 4 AND s.result IS NULL
   AND s.agent <> ALL(ARRAY['EDDIE']::text[]) LIMIT 20;

\echo Q3 step 7 walk (EXPLAIN)
EXPLAIN SELECT s.review_id, x.review_id AS xr, x.recommendation
  FROM pos_candidate_review_steps s
  JOIN pos_candidate_reviews v USING (review_id)
  JOIN LATERAL (SELECT xr.review_id, xr.recommendation FROM
       paper_orders o JOIN paper_xavier_reviews xr ON
       xr.group_id = o.group_id WHERE o.decision_id =
       v.decision_id ORDER BY xr.reviewed_at LIMIT 1) x
       ON true WHERE s.seq = 7 AND s.result IS NULL
   AND s.status <> 'ANSWERED' LIMIT 20;

\echo Q4 how many step-4 rows the walk could attach (an outcome exists), and step-7 rows with an order
SELECT (SELECT count(*) FROM pos_candidate_review_steps s
          JOIN pos_candidate_reviews v USING (review_id)
         WHERE s.seq = 4 AND s.result IS NULL AND s.agent <> 'EDDIE'
           AND EXISTS (SELECT 1 FROM eddie_execution_outcomes x
                        WHERE x.decision_id = v.decision_id)) AS step4_attachable,
       (SELECT count(*) FROM pos_candidate_review_steps s
          JOIN pos_candidate_reviews v USING (review_id)
         WHERE s.seq = 7 AND s.result IS NULL AND s.status <> 'ANSWERED'
           AND EXISTS (SELECT 1 FROM paper_orders o
                        WHERE o.decision_id = v.decision_id)) AS step7_with_order;

\echo Q5 triggers on the steps table
SELECT tgname, tgenabled FROM pg_trigger
 WHERE tgrelid = 'pos_candidate_review_steps'::regclass AND NOT tgisinternal;
