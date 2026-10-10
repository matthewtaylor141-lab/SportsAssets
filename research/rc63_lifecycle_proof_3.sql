-- RC6.3 lifecycle-proof lane, readback 3 (SELECT only).
-- The CALIBRATION input of the profitability-stack quarantine
-- (bettor_paper_profitability_stack.calibration_rows): CAL_ROWS_SQL for each
-- strategy with evaluations over the 120-day fit window, and, per evaluated
-- slug, the DISTINCT settlement-evidence tuples paper_xavier.outcome_for reads
-- from external_valuations (outcome_for depends only on the set of tuples).

\echo == G1 CAL_ROWS_SQL per strategy (json: strategy, slug, side, p_used, market_price, evaluated_epoch)
SELECT json_build_array(x.strategy, x.us_market_slug, x.holding_side, x.p_used::text,
                        x.market_price::text, extract(epoch FROM x.evaluated_at)::float8)::text AS row_json
  FROM (SELECT DISTINCT ON (strategy, us_market_slug, holding_side)
               strategy, us_market_slug, holding_side, p_used, market_price, evaluated_at
          FROM paper_profitability_evaluations
         WHERE account_id = 'paper_acct_main' AND p_used IS NOT NULL
           AND us_market_slug IS NOT NULL AND holding_side IS NOT NULL
           AND evaluated_at >= now() - interval '120 days'
         ORDER BY strategy, us_market_slug, holding_side, evaluated_at DESC, eval_id DESC) x
 ORDER BY 1;

\echo == G2 settlement-evidence tuples per evaluated slug (json: slug, [[outcome_basis, outcome_known, outcome, buy_intent], ...])
SELECT json_build_array(v.us_market_slug,
         json_agg(DISTINCT jsonb_build_array(v.outcome_basis, v.outcome_known, v.outcome, v.buy_intent)))::text AS row_json
  FROM external_valuations v
 WHERE v.outcome_basis IS NOT NULL
   AND v.us_market_slug IN (SELECT DISTINCT us_market_slug FROM paper_profitability_evaluations
                             WHERE account_id = 'paper_acct_main' AND p_used IS NOT NULL
                               AND evaluated_at >= now() - interval '120 days')
 GROUP BY v.us_market_slug ORDER BY 1;

\echo == G3 the bind cutover and the quarantine-step names (strategies with evaluations)
SELECT version, applied_at FROM schema_migrations WHERE version = '309_paper_profitability_bind.sql';
SELECT DISTINCT strategy FROM paper_profitability_evaluations WHERE account_id = 'paper_acct_main' ORDER BY 1;
