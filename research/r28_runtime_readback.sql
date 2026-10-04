-- R28 runtime readback (read-only): running SHAs, new runners, Xavier current
-- state, sleeves, SMALL LIVE / legacy mirror guards. "since" = workers boot.
\echo '== B · workers boot row (worker SHA) =='
SELECT left(value::text, 500) AS workers_boot FROM ingestion_state WHERE key = 'workers_boot';
\echo '== R1 · lol_runs / improve_runs / pos runners since 12:00Z =='
SELECT 'lol' AS runner, component, status, count(*) n, max(finished_at) last_at, left(max(error),160) err
  FROM lol_runs WHERE started_at > '2026-10-04 12:00Z' GROUP BY 1,2,3
UNION ALL
SELECT 'improve', '-', status, count(*), max(finished_at), NULL FROM improve_runs WHERE started_at > '2026-10-04 12:00Z' GROUP BY 1,2,3
ORDER BY 1,2,3;
\echo '== R2 · ledger / scores / forecasts / improvement items =='
SELECT classification, count(*) FROM lol_ledger GROUP BY 1 ORDER BY 2 DESC;
SELECT status, count(*) FROM lol_opportunity_scores_latest GROUP BY 1 ORDER BY 2 DESC;
SELECT DISTINCT ON (book, horizon) book, horizon, status, left(why,90) why, issued_at FROM lol_horizon_forecasts ORDER BY book, horizon, issued_at DESC;
SELECT stage, count(*) FROM improve_items GROUP BY 1 ORDER BY 2 DESC;
\echo '== X1 · Xavier: newest paper review per held group since workers boot =='
WITH boot AS (SELECT coalesce(to_timestamp((value->>'at')::float8), now() - interval '1 hour') AS at FROM ingestion_state WHERE key='workers_boot'),
newest AS (
  SELECT DISTINCT ON (r.group_id) r.group_id, r.review_id, r.reviewed_at, r.trigger, r.recommendation,
         r.measure->>'evidence_state' AS evidence_state,
         coalesce(r.measure->>'probability_age_s', r.measure->'freshness'->>'age_s') AS prob_age_s
    FROM paper_xavier_reviews r ORDER BY r.group_id, r.reviewed_at DESC)
SELECT recommendation, evidence_state, count(*) AS groups, min(reviewed_at) AS oldest, max(reviewed_at) AS newest,
       sum(CASE WHEN reviewed_at > (SELECT at FROM boot) THEN 1 ELSE 0 END) AS reviewed_since_boot
  FROM newest GROUP BY 1, 2 ORDER BY 3 DESC;
\echo '== X2 · reviews written since boot: stale-evidence HOLD (must be 0) and by recommendation/trigger =='
WITH boot AS (SELECT coalesce(to_timestamp((value->>'at')::float8), now() - interval '1 hour') AS at FROM ingestion_state WHERE key='workers_boot')
SELECT recommendation, trigger, measure->>'evidence_state' AS evidence_state, count(*) n, max(reviewed_at) last_at
  FROM paper_xavier_reviews WHERE reviewed_at > (SELECT at FROM boot) GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 20;
\echo '== X3 · Xavier assessments since boot by recommendation_state (222) =='
WITH boot AS (SELECT coalesce(to_timestamp((value->>'at')::float8), now() - interval '1 hour') AS at FROM ingestion_state WHERE key='workers_boot')
SELECT position_kind, recommendation_state, evidence_state, count(*) n, max(assessed_at) last_at
  FROM xavier_management_assessments WHERE assessed_at > (SELECT at FROM boot) GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 12;
\echo '== X4 · sample: 5 held groups, newest review + its valuation + whether a newer valuation exists =='
SELECT DISTINCT ON (r.group_id) r.group_id, r.review_id, r.reviewed_at, r.recommendation, r.trigger,
       r.measure->>'evidence_state' evidence_state, r.measure->>'valuation_id' valuation_id,
       r.measure->>'probability_source_at' prob_source_at
  FROM paper_xavier_reviews r ORDER BY r.group_id, r.reviewed_at DESC LIMIT 5;
\echo '== P · sleeves (223) =='
SELECT sleeve, classified_by, count(*) FROM paper_sleeve_classifications GROUP BY 1,2 ORDER BY 1,2;
\echo '== L · SMALL LIVE / legacy mirror guards (must be unchanged) =='
SELECT enabled, stopped, left(account_fingerprint,12) fp, scale, max_order_usd, revision FROM execmirror_control;
SELECT state, count(*) FROM execmirror_orders GROUP BY 1;
SELECT count(*) AS violations_since_cand24 FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict','') <> 'LIVE_ADMISSIBLE' AND created_at > '2026-10-04 05:00Z';
SELECT recommendation, count(*) FROM eddie_execution_estimates GROUP BY 1;
