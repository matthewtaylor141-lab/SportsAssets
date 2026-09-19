\echo ''
\echo '=== BETTOR END-TO-END, BY IDENTITY NOT BY COUNT ==='
-- Owner directive 2026-09-19: "Use the SAME BETTOR_OPPORTUNITY_ID,
-- SHADOW_DECISION_ID, MARKET_ID, POLICY_VERSION, TIMESTAMPS through the
-- entire chain. Do not validate using counts alone." And: "Do not infer
-- deployment from Git SHA alone."
--
-- So the first thing this file reads is WHICH BUILD IS RUNNING, from
-- the boot marker the workers write themselves, and every later
-- question is asked relative to that boot instant. An opportunity
-- observed before the repaired worker started is not a test of it.
--
-- Read-only by construction: SELECT statements only.

\echo ''
\echo '--- RUNNING BUILD, from the workers own boot marker ---'
SELECT 'running_build|' || COALESCE(value ->> 'commit', 'NOT_IDENTIFIED')
       || '|' || COALESCE(value ->> 'at', 'NOT_IDENTIFIED')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - (value ->> 'at')::timestamptz)))::text,
             'NOT_IDENTIFIED')
  FROM ingestion_state WHERE key = 'workers_boot';

\echo ''
\echo '--- PIPELINE COUNTERS ---'
SELECT 'counter|bettor_opportunities_total|' || count(*)::text
  FROM bettor_opportunities;

SELECT 'counter|bettor_decisions_total|' || count(*)::text
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

SELECT 'counter|bettor_no_trade_decisions|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND proposed_action = 'NO_TRADE';

-- "BETTOR decided" and "BETTOR proposed acting" are different claims,
-- and only the second would need scrutiny today.
SELECT 'counter|bettor_shadow_trades|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND proposed_action <> 'NO_TRADE';

SELECT 'counter|last_bettor_opportunity_at|'
       || COALESCE(max(observed_at)::text, 'NONE')
  FROM bettor_opportunities;

SELECT 'counter|last_bettor_decision_at|'
       || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

\echo ''
\echo '--- ORPHANS, FAILURES, ANNOTATIONS (migration 073) ---'
-- THE ALLOWANCE IS DECLARED, NOT INFERRED: the worker ticks every 60s
-- and decides inside the same tick, so 180s is three chances. An
-- opportunity younger than that is still legitimately processing.
SELECT 'orphan|' || count(*)::text || '|allowance_s=180'
  FROM bettor_opportunities o
 WHERE o.observed_at < now() - interval '180 seconds'
   AND NOT EXISTS (SELECT 1 FROM shadow_decisions d
                    WHERE d.bettor_opportunity_id = o.bettor_opportunity_id);

SELECT 'orphan_oldest|' || COALESCE(min(o.observed_at)::text, 'NONE')
  FROM bettor_opportunities o
 WHERE o.observed_at < now() - interval '180 seconds'
   AND NOT EXISTS (SELECT 1 FROM shadow_decisions d
                    WHERE d.bettor_opportunity_id = o.bettor_opportunity_id);

SELECT 'failures_table|' || CASE
         WHEN to_regclass('public.bettor_decision_failures') IS NULL
         THEN 'ABSENT_MIGRATION_073_NOT_APPLIED' ELSE 'PRESENT' END;

SELECT 'annotations_table|' || CASE
         WHEN to_regclass('public.bettor_opportunity_annotations') IS NULL
         THEN 'ABSENT_MIGRATION_073_NOT_APPLIED' ELSE 'PRESENT' END;

\echo ''
\echo '--- THE FIRST OPPORTUNITY AFTER THE RUNNING WORKER BOOTED ---'
-- Not "the first ever" and not "the newest": the first one THIS BUILD
-- was alive to see. Anything earlier tests the old code.
SELECT 'first_post_boot_opportunity|' || o.bettor_opportunity_id || '|'
       || o.observed_at::text || '|'
       || COALESCE(o.market_id, 'NO_MARKET_ID') || '|'
       || o.symbol || '|' || o.evidence_source || '|'
       || o.universe_version || '|' || o.rn1_features_used::text || '|'
       || COALESCE(o.market_state_id, 'NO_MARKET_STATE') || '|'
       || 'decision_exists=' || EXISTS (
             SELECT 1 FROM shadow_decisions d
              WHERE d.bettor_opportunity_id = o.bettor_opportunity_id)::text
  FROM bettor_opportunities o
 WHERE o.observed_at > (SELECT (value ->> 'at')::timestamptz
                          FROM ingestion_state WHERE key = 'workers_boot')
 ORDER BY o.observed_at ASC
 LIMIT 1;

\echo ''
\echo '--- ITS DECISION, READ BACK BY THAT EXACT OPPORTUNITY ID ---'
SELECT 'first_post_boot_decision|' || d.shadow_decision_id || '|'
       || d.bettor_opportunity_id || '|'
       || COALESCE(d.market_id, 'NO_MARKET_ID') || '|'
       || d.lane || '|' || d.proposed_action || '|'
       || d.policy_version || '|' || d.model_version || '|'
       || d.decision_ts::text || '|' || d.created_at::text || '|'
       || COALESCE(d.p_market::text, 'NULL') || '|'
       || COALESCE(d.p_bettor::text, 'NULL') || '|'
       || COALESCE(d.p_bettor_status, 'NULL') || '|'
       || COALESCE(d.p_fill::text, 'NULL') || '|'
       || COALESCE(d.p_fill_status, 'NULL') || '|'
       || d.rn1_features_used::text || '|'
       || COALESCE(d.rn1_observation_id, 'NO_RN1_OBSERVATION') || '|'
       || d.evidence_source
  FROM shadow_decisions d
 WHERE d.bettor_opportunity_id = (
         SELECT o.bettor_opportunity_id FROM bettor_opportunities o
          WHERE o.observed_at > (SELECT (value ->> 'at')::timestamptz
                                   FROM ingestion_state
                                  WHERE key = 'workers_boot')
          ORDER BY o.observed_at ASC LIMIT 1);

\echo ''
\echo '--- ITS BLOCKERS AND LINEAGE ---'
SELECT 'first_post_boot_blocker|' || (b.value ->> 'code')
  FROM shadow_decisions d,
       LATERAL jsonb_array_elements(COALESCE(d.blockers, '[]'::jsonb)) AS b
 WHERE d.bettor_opportunity_id = (
         SELECT o.bettor_opportunity_id FROM bettor_opportunities o
          WHERE o.observed_at > (SELECT (value ->> 'at')::timestamptz
                                   FROM ingestion_state
                                  WHERE key = 'workers_boot')
          ORDER BY o.observed_at ASC LIMIT 1);

SELECT 'first_post_boot_lineage|' || l.key || '=' || (l.value #>> '{}')
  FROM shadow_decisions d,
       LATERAL jsonb_each(COALESCE(d.feature_lineage, '{}'::jsonb)) AS l
 WHERE d.bettor_opportunity_id = (
         SELECT o.bettor_opportunity_id FROM bettor_opportunities o
          WHERE o.observed_at > (SELECT (value ->> 'at')::timestamptz
                                   FROM ingestion_state
                                  WHERE key = 'workers_boot')
          ORDER BY o.observed_at ASC LIMIT 1);

\echo ''
\echo '--- IF THERE IS NO DECISION: the NAMED failure, verbatim ---'
-- The whole point of the watchdog is that this question has an answer
-- in the database rather than being reconstructed from symptoms.
SELECT 'failure|' || f.failure_id || '|'
       || COALESCE(f.bettor_opportunity_id, 'NO_OPPORTUNITY') || '|'
       || COALESCE(f.symbol, 'NO_SYMBOL') || '|'
       || f.failed_at::text || '|' || f.stage || '|'
       || f.error_class || '|' || f.error_text
  FROM bettor_decision_failures f
 ORDER BY f.failed_at DESC
 LIMIT 10;

SELECT 'failure_counter|' || count(*)::text || '|'
       || COALESCE(max(failed_at)::text, 'NONE')
  FROM bettor_decision_failures;

\echo ''
\echo '--- ANNOTATIONS: the incident orphans, kept and labelled ---'
SELECT 'annotation|' || a.annotation_kind || '|' || a.incident || '|'
       || count(*)::text || '|' || min(a.observed_at)::text || '|'
       || max(a.observed_at)::text
  FROM bettor_opportunity_annotations a
 GROUP BY a.annotation_kind, a.incident;

\echo ''
\echo '--- THE NEWEST FIVE DECISIONS, so the proof is not one lucky row ---'
SELECT 'newest_chain|' || d.bettor_opportunity_id || '|'
       || d.shadow_decision_id || '|' || d.proposed_action || '|'
       || d.created_at::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 ORDER BY d.created_at DESC
 LIMIT 5;

\echo ''
\echo '--- LANE INDEPENDENCE, restated from the rows themselves ---'
SELECT 'independence|rn1_features_used_true|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND rn1_features_used IS NOT FALSE;

SELECT 'independence|rn1_observation_present|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND rn1_observation_id IS NOT NULL;

\echo ''
\echo '--- WORKER HEARTBEATS ---'
SELECT 'heartbeat|' || h.service || '|' || h.status || '|'
       || h.beat_at::text || '|'
       || round(extract(epoch FROM (now() - h.beat_at)))::text || 's|'
       || COALESCE(h.detail::text, 'NO_DETAIL')
  FROM service_heartbeats h
 WHERE h.service IN ('shadow_bettor', 'shadow_rn1', 'chain_listener')
 ORDER BY h.service;

\echo ''
\echo '--- SAFETY, restated from rows ---'
SELECT 'safety|shadow_mode_false_rows|' || count(*)::text
  FROM shadow_decisions WHERE shadow_mode IS NOT TRUE;

SELECT 'safety|capital_at_risk_nonzero_rows|' || count(*)::text
  FROM shadow_decisions WHERE capital_at_risk <> 0;
