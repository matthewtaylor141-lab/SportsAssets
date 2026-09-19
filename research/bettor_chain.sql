\echo ''
\echo '=== BETTOR END-TO-END, BY IDENTITY NOT BY COUNT ==='
-- Owner directive 2026-09-19: "Use the SAME BETTOR_OPPORTUNITY_ID,
-- SHADOW_DECISION_ID, MARKET_ID, POLICY_VERSION, TIMESTAMPS through the
-- entire chain. Do not validate using counts alone."
--
-- So this file returns IDS, and the API and UI halves of the proof are
-- asked for those same ids rather than for a tally that happens to
-- match. Every line is tagged so the runner keys on the tag, never on
-- line position.
--
-- Read-only by construction: SELECT statements only.

\echo ''
\echo '--- PIPELINE COUNTERS ---'
SELECT 'counter|bettor_opportunities_observed|' || count(*)::text
  FROM bettor_opportunities;

SELECT 'counter|bettor_decisions_recorded|' || count(*)::text
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

SELECT 'counter|bettor_no_trade_decisions|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND proposed_action = 'NO_TRADE';

-- A SHADOW TRADE IS ANYTHING THAT IS NOT A REFUSAL. Counted separately
-- because "BETTOR decided" and "BETTOR proposed acting" are different
-- claims and the second one is the one that would need scrutiny.
SELECT 'counter|bettor_shadow_trades|' || count(*)::text
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW' AND proposed_action <> 'NO_TRADE';

SELECT 'counter|last_bettor_opportunity_at|'
       || COALESCE(max(observed_at)::text, 'NONE')
  FROM bettor_opportunities;

SELECT 'counter|last_bettor_decision_at|'
       || COALESCE(max(decided_at)::text, 'NONE')
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

\echo ''
\echo '--- ORPHANS: an opportunity past its allowance with no decision ---'
-- THE ALLOWANCE IS DECLARED, NOT INFERRED. The worker ticks every 60s
-- and decides in the same tick, so 180s is three chances. An
-- opportunity younger than that is still legitimately processing and is
-- NOT an orphan -- the directive is explicit about that, and a counter
-- that flagged healthy in-flight work would be noise within a day.
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

\echo ''
\echo '--- THE FIRST POST-FIX CHAIN, WITH ITS REAL IDS ---'
-- The OLDEST BETTOR decision is the first one the repaired writer
-- managed; every earlier opportunity is an orphan of the incident.
SELECT 'first_chain|' || d.bettor_opportunity_id || '|'
       || d.shadow_decision_id || '|'
       || COALESCE(d.market_id, 'NO_MARKET_ID') || '|'
       || d.symbol || '|' || d.lane || '|' || d.proposed_action || '|'
       || d.policy_version || '|' || d.model_version || '|'
       || COALESCE(d.p_bettor_status, 'NULL') || '|'
       || COALESCE(d.p_fill_status, 'NULL') || '|'
       || (d.p_bettor IS NULL)::text || '|'
       || (d.information_ev IS NULL)::text || '|'
       || d.rn1_features_used::text || '|'
       || COALESCE(d.rn1_observation_id, 'NO_RN1_OBSERVATION') || '|'
       || d.evidence_source || '|'
       || d.decision_ts::text || '|' || d.decided_at::text || '|'
       || jsonb_array_length(COALESCE(d.blockers, '[]'::jsonb))::text || '|'
       || COALESCE(d.reason_codes::text, '[]')
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 ORDER BY d.decided_at ASC
 LIMIT 1;

\echo ''
\echo '--- ITS OPPORTUNITY, READ BACK BY THE SAME ID ---'
SELECT 'first_chain_opportunity|' || o.bettor_opportunity_id || '|'
       || o.symbol || '|' || COALESCE(o.market_id, 'NO_MARKET_ID') || '|'
       || o.universe_version || '|' || o.evidence_source || '|'
       || o.observed_at::text || '|' || o.rn1_features_used::text || '|'
       || COALESCE(o.market_state_id, 'NO_MARKET_STATE')
  FROM bettor_opportunities o
 WHERE o.bettor_opportunity_id = (
         SELECT d.bettor_opportunity_id FROM shadow_decisions d
          WHERE d.lane = 'BETTOR_EV_SHADOW'
          ORDER BY d.decided_at ASC LIMIT 1);

\echo ''
\echo '--- BLOCKERS ON THAT DECISION, NAMED ---'
SELECT 'first_chain_blocker|' || b.value ->> 'code'
  FROM shadow_decisions d,
       LATERAL jsonb_array_elements(COALESCE(d.blockers, '[]'::jsonb)) AS b
 WHERE d.lane = 'BETTOR_EV_SHADOW'
   AND d.shadow_decision_id = (
         SELECT d2.shadow_decision_id FROM shadow_decisions d2
          WHERE d2.lane = 'BETTOR_EV_SHADOW'
          ORDER BY d2.decided_at ASC LIMIT 1);

\echo ''
\echo '--- THE NEWEST CHAIN TOO, so the proof is not a single lucky row ---'
SELECT 'newest_chain|' || d.bettor_opportunity_id || '|'
       || d.shadow_decision_id || '|' || d.proposed_action || '|'
       || d.decided_at::text
  FROM shadow_decisions d
 WHERE d.lane = 'BETTOR_EV_SHADOW'
 ORDER BY d.decided_at DESC
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
