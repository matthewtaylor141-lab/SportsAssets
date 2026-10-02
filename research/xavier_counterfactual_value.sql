-- The exact counterfactual values Xavier's latest lesson records (read-only):
-- is exit_at_first_review_net_usd stored, and at what precision?
\echo '== C0 · latest Xavier MANAGEMENT_OUTCOMES lesson, counterfactual positions =='
SELECT lesson_id, learned_at,
       p->>'group_id' AS group_id,
       p->>'realized_pnl_usd' AS realized_pnl_usd,
       p->>'exit_at_first_review_net_usd' AS exit_at_first_review_net_usd,
       p->>'exit_minus_realized_usd' AS exit_minus_realized_usd
  FROM (SELECT * FROM paper_agent_lessons
         WHERE agent_id = 'XAVIER' AND kind = 'MANAGEMENT_OUTCOMES'
         ORDER BY learned_at DESC LIMIT 1) l
  CROSS JOIN LATERAL jsonb_array_elements(
       COALESCE(l.metrics->'counterfactual_exit_at_first_review'->'positions', '[]'::jsonb)) AS p;
