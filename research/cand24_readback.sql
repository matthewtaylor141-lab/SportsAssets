-- Candidate 24 readback (read-only): migration 213, the P5 focus universe,
-- same-book samples (S1), C12 decision-time proofs, the actual-lane guard,
-- the mirror control and the workers boot row.
\echo '== M · migrations 212-214 =='
SELECT version, applied_at FROM schema_migrations WHERE version ~ '^(212|213|214)' ORDER BY 1;
\echo '== F1 · focus universe: newest snapshot per service =='
SELECT DISTINCT ON (service) service, process_id, universe_id, computed_at, bound,
       (SELECT count(*) FROM institutional_focus_universe f2 WHERE f2.universe_id = f.universe_id) AS members
  FROM institutional_focus_universe f ORDER BY service, computed_at DESC;
\echo '== F2 · newest universe by tier / identity =='
WITH u AS (SELECT universe_id FROM institutional_focus_universe ORDER BY computed_at DESC LIMIT 1)
SELECT tier, identity_status, coalesce(unavailable_reason, '') AS unavailable_reason, count(*) AS n,
       sum(CASE WHEN grants_live_eligibility THEN 1 ELSE 0 END) AS grants_live
  FROM institutional_focus_universe WHERE universe_id = (SELECT universe_id FROM u)
 GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC;
\echo '== F3 · newest universe members (exact only) =='
WITH u AS (SELECT universe_id FROM institutional_focus_universe ORDER BY computed_at DESC LIMIT 1)
SELECT rank, tier, retail_slug, outcome_side, institutional_symbol, market_type, period
  FROM institutional_focus_universe WHERE universe_id = (SELECT universe_id FROM u)
   AND identity_status = 'EXACT' ORDER BY rank LIMIT 32;
\echo '== S1 · same-book probes, 24 h: exact-identity comparable vs agreement =='
SELECT count(*) AS probes,
       count(*) FILTER (WHERE identity_exact) AS exact_identity,
       count(*) FILTER (WHERE agreed IS NOT NULL) AS compared,
       count(*) FILTER (WHERE agreed) AS agreed,
       count(*) FILTER (WHERE verdict <> 'NOT_COMPARABLE') AS comparable_verdicts,
       max(probed_at) AS last_at, sum(orders_placed) AS orders_placed
  FROM institutional_same_book_probe WHERE probed_at > now() - interval '24 hours';
SELECT coalesce(incomparable_reason, verdict) AS reason, count(*) AS n
  FROM institutional_same_book_probe WHERE probed_at > now() - interval '24 hours'
 GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
SELECT coalesce(focus_tier, '(none)') AS tier, count(*) AS n, count(*) FILTER (WHERE agreed IS NOT NULL) AS compared
  FROM institutional_same_book_probe WHERE probed_at > now() - interval '24 hours' GROUP BY 1 ORDER BY 2 DESC;
\echo '== C12 · decision-time proofs =='
SELECT proof_status, coalesce(refusal, '') AS refusal, count(*) AS n, max(recorded_at) AS last_at
  FROM p5_c12_decision_proof GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;
\echo '== G · actual-lane guard: live-eligible without LIVE_ADMISSIBLE (must be 0) =='
SELECT count(*) AS violations FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE';
SELECT state, count(*) FROM execmirror_orders WHERE execution_intent_id IS NOT NULL GROUP BY 1;
SELECT enabled, stopped, left(account_fingerprint, 12) AS fp, scale, max_order_usd, revision FROM execmirror_control;
\echo '== A · approval artifacts =='
SELECT rule_id, version, status, owner_approval_actor, owner_approved_at FROM live_rule_artifacts;
\echo '== W · workers boot row =='
SELECT key, left(value::text, 400) AS value FROM ingestion_state WHERE key = 'workers_boot';
