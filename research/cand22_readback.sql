-- Candidate 22 (recovery) readback, read-only: migrations 206-212, writer
-- builds, NCAAF in decisions, Xavier fresh management + policy record,
-- Karen, shadow intel, coverage, postmortems, P5 stream evidence, the
-- actual-lane guard and the mirror control.
\echo '== K0 · migrations 204-212 (with time) =='
SELECT version, applied_at FROM schema_migrations WHERE version ~ '^(204|205|206|207|208|209|210|211|212)' ORDER BY 1;
\echo '== K1 · writer builds (collector cycle, feed) =='
SELECT key, value->'writer'->>'build' AS writer_build, value->>'state' AS state,
       to_timestamp(coalesce((value->>'at')::float8, (value->>'beat_at')::float8)) AS at
  FROM ingestion_state WHERE key IN ('ext_pinnacle_last_cycle', 'pinnapi_feed_last');
\echo '== K1b · requested provider keys in the last collector cycle =='
SELECT left(coalesce(value->'selection'->'requested', value->'requested', value->'sports')::text, 400) AS requested
  FROM ingestion_state WHERE key = 'ext_pinnacle_last_cycle';
\echo '== K2 · NCAAF decisions (cfb slugs) since 2026-10-03 =='
SELECT verdict, refusal, count(*) AS n, max(decided_at) AS last_at
  FROM paper_decisions WHERE us_market_slug LIKE '%-cfb-%' AND decided_at > '2026-10-03'
 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10;
\echo '== K3 · Xavier assessments by evidence state / thesis state (last 2 h) =='
SELECT position_kind, evidence_state, thesis_state, count(*) AS n,
       round(avg(probability_age_s)::numeric, 1) AS avg_prob_age_s,
       sum(CASE WHEN within_bound THEN 1 ELSE 0 END) AS within_bound,
       max(assessed_at) AS last_at
  FROM xavier_management_assessments WHERE assessed_at > now() - interval '2 hours'
 GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 12;
\echo '== K3b · policy record on the latest assessment =='
SELECT policy->>'status' AS policy_status, policy->>'policy_id' AS policy_id,
       policy->>'version' AS version, left(policy->>'sha256', 16) AS sha256_prefix, assessed_at
  FROM xavier_management_assessments ORDER BY assessed_at DESC LIMIT 1;
SELECT count(*) AS theses FROM xavier_entry_theses;
\echo '== K4 · Karen challenges by state / detector =='
SELECT target_agent, detector, state, count(*) AS n, max(challenged_at) AS last_at
  FROM karen_challenges GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 15;
SELECT agent_id, state, last_heartbeat_at FROM agent_status WHERE agent_id = 'KAREN';
\echo '== K5 · shadow intel runs (last 2 h) =='
SELECT component, status, count(*) AS n, max(finished_at) AS last_at
  FROM intel_runs WHERE started_at > now() - interval '2 hours' GROUP BY 1, 2 ORDER BY 1;
\echo '== K6 · coverage funnel (America/New_York, today) and alerts =='
SELECT league, provider_events, normalized_events, venue_discovered, mapped_events,
       settlement_supported, evaluated_events, entered_events, refused_events,
       ordered_events, filled_events, computed_at
  FROM coverage_funnel_snapshots
 WHERE tz = 'America/New_York' AND day = (now() AT TIME ZONE 'America/New_York')::date
 ORDER BY provider_events DESC NULLS LAST LIMIT 15;
SELECT league, kind, stage_from, stage_to, severity, audrey_finding_id, detected_at
  FROM coverage_collapse_alerts ORDER BY detected_at DESC LIMIT 10;
\echo '== K7 · postmortems by book =='
SELECT book, count(*) AS n, sum(CASE WHEN decomposition_complete THEN 1 ELSE 0 END) AS complete,
       max(computed_at) AS last_at FROM position_postmortems GROUP BY 1;
\echo '== K8 · P5 stream evidence and same-book probe (last 2 h) =='
SELECT service, stream_state, stream_state_why, count(*) AS rows, max(recorded_at) AS last_at
  FROM institutional_stream_evidence WHERE recorded_at > now() - interval '2 hours'
 GROUP BY 1, 2, 3 ORDER BY 5 DESC LIMIT 6;
SELECT verdict, count(*) AS n, sum(orders_placed) AS orders_placed, max(probed_at) AS last_at
  FROM institutional_same_book_probe WHERE probed_at > now() - interval '24 hours' GROUP BY 1;
\echo '== K9 · actual-lane guard: live-eligible without LIVE_ADMISSIBLE (must be 0) =='
SELECT count(*) AS violations FROM execution_intents
 WHERE live_eligible AND coalesce(live_eligibility->'admission'->>'verdict', '') <> 'LIVE_ADMISSIBLE';
SELECT actual_state, actual_refusal, count(*) AS n, max(created_at) AS last_at
  FROM execution_intents WHERE created_at > now() - interval '2 hours' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8;
\echo '== K10 · actual orders from intents (must be 0 while stopped) and control =='
SELECT state, count(*) FROM execmirror_orders WHERE execution_intent_id IS NOT NULL GROUP BY 1;
SELECT enabled, stopped, left(account_fingerprint, 12) AS fp, scale, max_order_usd, revision
  FROM execmirror_control;
\echo '== K11 · approval artifacts (never self-approved) =='
SELECT policy_id, version, status, left(sha256, 16) AS sha256_prefix FROM agent_policy_artifacts;
SELECT rule_id, version, status, left(sha256, 16) AS sha256_prefix, owner_approval_actor FROM live_rule_artifacts;
\echo '== K12 · workers boot row =='
SELECT key, left(value::text, 600) AS value FROM ingestion_state WHERE key = 'workers_boot';
