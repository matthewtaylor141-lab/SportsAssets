-- R30 capital-critical program baseline (read only): today's NFL funnel by
-- stage and refusal, Eddie pre-trade measurability, Karen challenge mix,
-- lost-opportunity classes, and agent evidence freshness for open positions.
\echo == NFL valuations (36 h)
SELECT count(*) AS valuations, count(DISTINCT us_market_slug) AS markets,
       min(decided_at) AS first, max(decided_at) AS last
  FROM external_valuations WHERE decided_at > now() - interval '36 hours'
   AND us_market_slug LIKE 'aec-nfl-%';
\echo == NFL paper decisions by strategy / verdict / first refusal (36 h)
SELECT strategy, verdict, refusal, count(*), count(DISTINCT us_market_slug) AS markets
  FROM paper_decisions WHERE decided_at > now() - interval '36 hours'
   AND us_market_slug LIKE 'aec-nfl-%'
 GROUP BY 1,2,3 ORDER BY 1,2,4 DESC;
\echo == NFL refusal codes (every refusal, CG strategy, 36 h)
SELECT r AS refusal, count(*) AS rows, count(DISTINCT us_market_slug) AS markets
  FROM paper_decisions, unnest(refusals) r
 WHERE decided_at > now() - interval '36 hours' AND us_market_slug LIKE 'aec-nfl-%'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 GROUP BY 1 ORDER BY 2 DESC;
\echo == all-sport CG refusal codes (24 h), unique markets vs rows
SELECT r AS refusal, count(*) AS rows, count(DISTINCT us_market_slug) AS markets
  FROM paper_decisions, unnest(refusals) r
 WHERE decided_at > now() - interval '24 hours'
   AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER'
 GROUP BY 1 ORDER BY 3 DESC LIMIT 25;
\echo == Eddie estimates (7 d): recommendation and measurability
SELECT recommendation, count(*),
       count(*) FILTER (WHERE expected_fill_probability IS NULL) AS no_fill_p,
       count(*) FILTER (WHERE expected_executable_ev_usd IS NULL) AS no_ev,
       count(*) FILTER (WHERE expected_executable_ev_usd > 0) AS ev_pos
  FROM eddie_execution_estimates WHERE estimated_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC;
SELECT k AS unmeasured_key, count(*) FROM eddie_execution_estimates,
       jsonb_object_keys(unmeasured) k WHERE estimated_at > now() - interval '7 days'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 12;
\echo == Karen challenges (7 d) by detector / state / severity
SELECT detector, state, severity, count(*) FROM karen_challenges
 WHERE challenged_at > now() - interval '7 days' GROUP BY 1,2,3 ORDER BY 4 DESC LIMIT 25;
\echo == lost-opportunity classes (all)
SELECT classification, count(*), count(DISTINCT decision_ref) FROM lol_ledger GROUP BY 1 ORDER BY 2 DESC;
\echo == open paper groups and their latest review evidence state
SELECT x.evidence_state, count(*) FROM (
  SELECT DISTINCT ON (r.group_id) r.group_id,
         r.measure->>'evidence_state' AS evidence_state
    FROM paper_xavier_reviews r JOIN paper_handoffs h USING (group_id)
   ORDER BY r.group_id, r.reviewed_at DESC) x GROUP BY 1 ORDER BY 2 DESC;
\echo == Pinnacle valuation receipt age at decision (CG, 24 h)
SELECT percentile_cont(ARRAY[0.5,0.9,0.99]) WITHIN GROUP (ORDER BY (pinnacle->>'age_s')::float8) AS pin_age_p50_p90_p99,
       count(*) FILTER (WHERE r = 'PINNACLE_STALE' OR pinnacle->>'qualification' <> 'FRESH') AS not_fresh
  FROM paper_decisions LEFT JOIN LATERAL unnest(refusals) r ON true
 WHERE decided_at > now() - interval '24 hours' AND strategy = 'PINNACLE_COMPLETED_GAME_PAPER';
