-- READ-ONLY. REVENUE RELIABILITY V1 probe: columns and row counts of every
-- table the stack binds to (production schema as it stands today).
SELECT table_name, string_agg(column_name || ':' || data_type, ', ' ORDER BY ordinal_position) cols
  FROM information_schema.columns
 WHERE table_schema = 'public' AND table_name IN (
   'paper_strategy_lifecycle_current_v','paper_profitability_models','paper_profitability_evaluations',
   'paper_cash_decisions','paper_shadow_counterfactuals','paper_shadow_counterfactual_outcomes',
   'paper_counterfactual_outcomes','paper_counterfactual_variants','paper_counterfactual_variant_outcomes',
   'opportunity_score_tournament','derek_entry_decisions','karen_challenges','xavier_entry_theses',
   'xavier_management_assessments','xavier_value_add','audrey_audit_reports','improvement_candidates',
   'improvement_trials','improvement_holdouts','improvement_releases','paper_entry_refusal_census')
 GROUP BY table_name ORDER BY table_name;
SELECT 'paper_strategy_lifecycle_current_v' t, count(*) FROM paper_strategy_lifecycle_current_v
UNION ALL SELECT 'paper_profitability_models', count(*) FROM paper_profitability_models
UNION ALL SELECT 'paper_profitability_evaluations', count(*) FROM paper_profitability_evaluations
UNION ALL SELECT 'paper_cash_decisions', count(*) FROM paper_cash_decisions
UNION ALL SELECT 'paper_shadow_counterfactuals', count(*) FROM paper_shadow_counterfactuals
UNION ALL SELECT 'paper_counterfactual_variants', count(*) FROM paper_counterfactual_variants
UNION ALL SELECT 'paper_counterfactual_variant_outcomes', count(*) FROM paper_counterfactual_variant_outcomes
UNION ALL SELECT 'opportunity_score_tournament', count(*) FROM opportunity_score_tournament
UNION ALL SELECT 'derek_entry_decisions', count(*) FROM derek_entry_decisions
UNION ALL SELECT 'karen_challenges', count(*) FROM karen_challenges
UNION ALL SELECT 'xavier_entry_theses', count(*) FROM xavier_entry_theses
UNION ALL SELECT 'xavier_management_assessments', count(*) FROM xavier_management_assessments
UNION ALL SELECT 'xavier_value_add', count(*) FROM xavier_value_add
UNION ALL SELECT 'audrey_audit_reports', count(*) FROM audrey_audit_reports
UNION ALL SELECT 'improvement_candidates', count(*) FROM improvement_candidates
UNION ALL SELECT 'improvement_trials', count(*) FROM improvement_trials
UNION ALL SELECT 'improvement_holdouts', count(*) FROM improvement_holdouts
UNION ALL SELECT 'improvement_releases', count(*) FROM improvement_releases;
SELECT * FROM paper_strategy_lifecycle_current_v;
SELECT state, change_class, change_kind, proposed_by, evaluated_by, approved_by, count(*)
  FROM improvement_candidates GROUP BY 1,2,3,4,5,6 ORDER BY 7 DESC LIMIT 30;
SELECT status, count(*) FROM xavier_value_add GROUP BY 1;
SELECT state, blocked, false_block, outcome, count(*) FROM karen_challenges GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 20;
SELECT variant, count(*), count(o.variant_id) settled FROM paper_counterfactual_variants v
  LEFT JOIN paper_counterfactual_variant_outcomes o USING (variant_id) GROUP BY 1;
SELECT strategy, stage, verdict, count(*) FROM paper_profitability_evaluations GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
