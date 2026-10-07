-- READ-ONLY. REVENUE RELIABILITY V1: JSON shapes of the evidence records.
SELECT status, outcome_basis, method, counterfactuals::text, incremental::text
  FROM xavier_value_add WHERE status = 'FINAL' ORDER BY computed_at DESC LIMIT 2;
SELECT counterfactuals::text FROM xavier_entry_theses ORDER BY recorded_at DESC LIMIT 1;
SELECT detector, target_agent, target_kind, category, production_effect, downstream_impact::text
  FROM karen_challenges ORDER BY challenged_at DESC LIMIT 3;
SELECT detector, category, production_effect, target_kind, count(*) FROM karen_challenges GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 15;
SELECT tournament_version, strategy, v1_status, v2_status, authority, count(*), count(v2_predicted_net_lcb_usd)
  FROM opportunity_score_tournament GROUP BY 1,2,3,4,5;
SELECT v.variant, v.style, v.stage, v.verdict, v.evidence_class, o.pnl_class, o.label, count(*),
       sum(o.counterfactual_pnl_usd), sum(v.expected_ev_usd)
  FROM paper_counterfactual_variants v LEFT JOIN paper_counterfactual_variant_outcomes o USING (variant_id)
 GROUP BY 1,2,3,4,5,6,7 ORDER BY 1;
SELECT audit_day, version, summary, left(report::text, 1500) FROM audrey_audit_reports ORDER BY computed_at DESC LIMIT 1;
SELECT jsonb_object_keys(report) k, count(*) FROM audrey_audit_reports GROUP BY 1 ORDER BY 2 DESC LIMIT 40;
SELECT kind, version, observations, left(payload::text, 600), fitted_at FROM paper_profitability_models
 ORDER BY fitted_at DESC LIMIT 3;
SELECT decision, label, count(*), left(max(binding_refusals::text), 400) FROM paper_cash_decisions GROUP BY 1,2;
SELECT candidate_id, change_class, change_kind, state, proposed_by, evaluated_by, release_scope,
       left(hypothesis,200), training_boundary::text, evaluation_boundary::text, left(success_metrics::text,300),
       left(harm_metrics::text,300), left(rollback_procedure,200), created_at
  FROM improvement_candidates ORDER BY created_at DESC LIMIT 2;
SELECT trial_id, segment, holdout_id, evidence_category, verdict, evaluated_by, left(metrics::text,300)
  FROM improvement_trials ORDER BY created_at DESC LIMIT 2;
SELECT source, capital_refusal, lifecycle_state, evidence_class, pnl_class, count(*) FROM paper_shadow_counterfactuals
 GROUP BY 1,2,3,4,5 ORDER BY 6 DESC LIMIT 10;
