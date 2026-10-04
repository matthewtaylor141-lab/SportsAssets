-- Candidate 27 readback, read-only: migrations 220/221, the Lost Opportunity
-- Ledger runner, Opportunity Scores, horizon forecasts, the improvement
-- pipeline, plus the standing actual-lane guard and control.
\echo '== M · migrations 216-222 =='
SELECT version, applied_at FROM schema_migrations WHERE version ~ '^(216|217|218|219|220|221|222)' ORDER BY 1;
\echo '== W · workers boot row =='
SELECT left(value::text, 400) AS value FROM ingestion_state WHERE key = 'workers_boot';
\echo '== L1 · lol_runs by component/status (last 3 h) =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at, left(max(error), 160) AS err
  FROM lol_runs WHERE started_at > now() - interval '3 hours' GROUP BY 1, 2 ORDER BY 1, 2;
\echo '== L2 · Lost Opportunity Ledger: classification x attribution =='
SELECT classification, attribution, count(*) AS n, max(classified_at) AS last_at
  FROM lol_ledger GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
\echo '== L3 · Opportunity Scores by status (latest view) =='
SELECT status, count(*) AS n, round(avg(opportunity_score)::numeric, 6) AS avg_score,
       max(computed_at) AS last_at FROM lol_opportunity_scores_latest GROUP BY 1 ORDER BY 2 DESC;
SELECT us_market_slug, holding_side, round(opportunity_score::numeric, 6) AS score,
       round(expected_net_executable_ev_usd::numeric, 4) AS ev_usd, status, left(why, 80) AS why
  FROM lol_opportunity_scores_latest ORDER BY opportunity_score DESC NULLS LAST LIMIT 5;
\echo '== L4 · latest horizon forecasts per book/horizon =='
SELECT DISTINCT ON (book, horizon) book, horizon, status, method, sample_days, sample_positions,
       round(p10_pnl_usd::numeric, 2) AS p10, round(p50_pnl_usd::numeric, 2) AS p50,
       round(p90_pnl_usd::numeric, 2) AS p90, left(why, 100) AS why, label, authority, issued_at
  FROM lol_horizon_forecasts ORDER BY book, horizon, issued_at DESC;
\echo '== I1 · improve_runs (last 3 h) =='
SELECT status, count(*) AS n, max(finished_at) AS last_at, left(max(summary::text), 300) AS summary
  FROM improve_runs WHERE started_at > now() - interval '3 hours' GROUP BY 1;
\echo '== I2 · improvement items by stage / owner / source =='
SELECT stage, owner_agent, source_kind, requires_human_review, count(*) AS n,
       max(updated_at) AS last_at, min(production_effect) AS effect, min(label) AS label
  FROM improve_items GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 20;
\echo '== I3 · improvement events by stage / actor class; self-approval check =='
SELECT stage, actor_class, count(*) AS n FROM improve_events GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 20;
SELECT count(*) AS self_evaluations
  FROM improve_events e JOIN improve_items i USING (item_id)
 WHERE e.stage = 'INDEPENDENT_EVALUATION' AND e.actor = i.owner_agent;
SELECT count(*) AS open_disagreements FROM improve_disagreements;
\echo '== G · actual-lane guard (since cand24) and control =='
SELECT count(*) AS violations_since_cand24 FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE'
   AND created_at > '2026-10-04 05:00Z';
SELECT state, count(*) FROM execmirror_orders WHERE execution_intent_id IS NOT NULL GROUP BY 1;
SELECT enabled, stopped, left(account_fingerprint, 12) AS fp, scale, max_order_usd, revision FROM execmirror_control;
