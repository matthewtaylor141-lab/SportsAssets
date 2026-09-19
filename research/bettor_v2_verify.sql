\echo ''
\echo '=== BETTOR_EV_SHADOW_V2 + HEALTH REPAIR: PRODUCTION VERIFICATION ==='
-- "Deploy and verify V2 + heartbeat + COMMAND health separation only."
-- Read-only: SELECT statements only.

\echo ''
\echo '--- 1. RUNNING BUILD AND POLICY INTEGRITY ---'
SELECT 'build|' || COALESCE(value ->> 'commit', 'NOT_IDENTIFIED')
       || '|at=' || COALESCE(value ->> 'at', 'NOT_IDENTIFIED')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - (value ->> 'at')::timestamptz)))::text, 'NONE')
       || '|policy=' || COALESCE(value ->> 'policy', 'ABSENT')
  FROM ingestion_state WHERE key = 'workers_boot';

SELECT 'integrity|' || COALESCE(value ->> 'policyIntegrity', 'ABSENT')
       || '|decisionWritingAllowed='
       || COALESCE(value ->> 'decisionWritingAllowed', 'ABSENT')
       || '|codeShaMatches='
       || COALESCE(value ->> 'codeShaMatches', 'ABSENT')
       || '|boundary=' || COALESCE(value ->> 'codeBoundary', 'ABSENT')
       || '|freeze=' || COALESCE(value ->> 'policyFreeze', 'ABSENT')
  FROM ingestion_state WHERE key = 'workers_boot';

SELECT 'integrity_why|' || COALESCE(value ->> 'policyIntegrityWhy', 'ABSENT')
  FROM ingestion_state WHERE key = 'workers_boot';

SELECT 'boot_sha|running_policy_sha='
       || COALESCE(value ->> 'policySha', 'ABSENT')
       || '|running_code_sha=' || COALESCE(value ->> 'policyCodeSha', 'ABSENT')
  FROM ingestion_state WHERE key = 'workers_boot';

\echo ''
\echo '--- 2. THE FROZEN POLICY ROWS: V1 IMMUTABLE, V2 BESIDE IT ---'
-- V1's row must be BYTE-FOR-BYTE what it was: 6db08437 / 34fbb4ab.
SELECT 'policy|' || p.policy_version || '|sha=' || left(p.policy_sha, 16)
       || '|code=' || left(COALESCE(p.policy_code_sha, 'NONE'), 16)
       || '|frozen=' || p.frozen_at::text
  FROM shadow_policy_versions p
 ORDER BY p.frozen_at;

SELECT 'v1_immutable|' || CASE
         WHEN policy_sha = '6db08437ceed0dc82fb491d23e9da0a534ef2554'
                        || '22f045a2d3345aa0b6b48101'
          AND policy_code_sha = '34fbb4ab992cf2a395c35a2b3b8bda89998a0209'
                             || '6fb57d719ea836b68a234ad1'
         THEN 'UNCHANGED' ELSE 'MOVED -- INVESTIGATE' END
  FROM shadow_policy_versions WHERE policy_version = 'BETTOR_EV_SHADOW_V1';

\echo ''
\echo '--- 3. V1 HISTORY, REPORTED SEPARATELY AND PRESERVED ---'
-- The V1 population splits on the one line that differed. Neither half
-- is invalid; the provenance is simply different and now recorded.
SELECT 'v1|code_matched_decisions|' || count(*)::text
       || '|first=' || COALESCE(min(created_at)::text, 'NONE')
       || '|last=' || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions
 WHERE policy_version = 'BETTOR_EV_SHADOW_V1'
   AND p_fill_status = 'NOT_ESTABLISHED';

SELECT 'v1|code_drifted_decisions|' || count(*)::text
       || '|first=' || COALESCE(min(created_at)::text, 'NONE')
       || '|last=' || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions
 WHERE policy_version = 'BETTOR_EV_SHADOW_V1'
   AND p_fill_status = 'NOT_IDENTIFIED';

-- THE PERMANENT HANDOVER ROW. "Accept the permanent historical
-- overlap. Do not try to make the historical metric zero."
SELECT 'v1|handover_overlap_rows|' || count(*)::text
  FROM shadow_decisions d
 WHERE d.policy_version = 'BETTOR_EV_SHADOW_V1'
   AND d.p_fill_status = 'NOT_ESTABLISHED'
   AND d.created_at > (SELECT min(created_at) FROM shadow_decisions
                        WHERE policy_version = 'BETTOR_EV_SHADOW_V1'
                          AND p_fill_status = 'NOT_IDENTIFIED');

\echo ''
\echo '--- 4. V2: THE PROSPECTIVE INVARIANT ---'
SELECT 'v2|decisions|' || count(*)::text
       || '|no_trade=' || count(*) FILTER (
             WHERE proposed_action = 'NO_TRADE')::text
       || '|other=' || count(*) FILTER (
             WHERE proposed_action <> 'NO_TRADE')::text
       || '|first=' || COALESCE(min(created_at)::text, 'NONE')
       || '|last=' || COALESCE(max(created_at)::text, 'NONE')
  FROM shadow_decisions WHERE policy_version = 'BETTOR_EV_SHADOW_V2';

-- "Anything after stable V2 must use NOT_IDENTIFIED."
SELECT 'v2|P_FILL_NOT_ESTABLISHED_AFTER_STABLE_V2_START|' || count(*)::text
  FROM shadow_decisions
 WHERE policy_version = 'BETTOR_EV_SHADOW_V2'
   AND p_fill_status <> 'NOT_IDENTIFIED';

SELECT 'v2|p_fill|' || COALESCE(p_fill_status, 'NULL') || '|'
       || count(*)::text
  FROM shadow_decisions WHERE policy_version = 'BETTOR_EV_SHADOW_V2'
 GROUP BY p_fill_status ORDER BY p_fill_status;

SELECT 'v2|p_bettor|' || COALESCE(p_bettor_status, 'NULL') || '|'
       || count(*)::text
  FROM shadow_decisions WHERE policy_version = 'BETTOR_EV_SHADOW_V2'
 GROUP BY p_bettor_status ORDER BY p_bettor_status;

SELECT 'v2|rn1_features_used_true|' || count(*)::text
  FROM shadow_decisions
 WHERE policy_version = 'BETTOR_EV_SHADOW_V2'
   AND rn1_features_used IS NOT FALSE;

\echo ''
\echo '--- 5. THE TWO PLANES, SEPARATELY ---'
SELECT 'plane|opportunity_collector|' || count(*)::text
       || '|newest=' || COALESCE(max(observed_at)::text, 'NONE')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - max(observed_at))))::text, 'NONE')
  FROM bettor_opportunities;

SELECT 'plane|decision_pipeline|' || count(*)::text
       || '|newest=' || COALESCE(max(created_at)::text, 'NONE')
       || '|age_s=' || COALESCE(round(extract(epoch FROM
             (now() - max(created_at))))::text, 'NONE')
  FROM shadow_decisions WHERE lane = 'BETTOR_EV_SHADOW';

-- THE HEARTBEAT. A beat carrying a 'pipeline' key is the proof the
-- serialization fix landed: every beat since 19:13:51Z failed because
-- that block could not be serialized.
SELECT 'plane|telemetry|' || h.status
       || '|beat_at=' || h.beat_at::text
       || '|age_s=' || round(extract(epoch FROM (now() - h.beat_at)))::text
       || '|has_pipeline=' || (h.detail ? 'pipeline')::text
       || '|has_integrity=' || (h.detail ? 'policyIntegrity')::text
       || '|decisions_withheld='
       || COALESCE(h.detail ->> 'decisionsWithheld', 'ABSENT')
  FROM service_heartbeats h WHERE h.service = 'shadow_bettor';

SELECT 'plane|v2_decision_write_failures|' || count(*)::text
       || '|newest=' || COALESCE(max(failed_at)::text, 'NONE')
  FROM bettor_decision_failures
 WHERE failed_at > (SELECT (value ->> 'at')::timestamptz
                      FROM ingestion_state WHERE key = 'workers_boot');

\echo ''
\echo '--- 6. ACCOUNTING UNCHANGED, AND STILL THE HONEST ZERO ---'
SELECT 'sizing|' || s.sizing_policy_version || '|'
       || s.standard_notional_usd::text || '|' || left(s.policy_sha, 16)
  FROM bettor_sizing_policies s;

SELECT 'acct|positions=' || (
         SELECT count(*) FROM shadow_positions p
           JOIN shadow_decisions d
             ON d.shadow_decision_id = p.originating_decision_id
          WHERE d.lane = 'BETTOR_EV_SHADOW')::text
       || '|executions=' || (
         SELECT count(*) FROM shadow_executions e
           JOIN shadow_decisions d
             ON d.shadow_decision_id = e.shadow_decision_id
          WHERE d.lane = 'BETTOR_EV_SHADOW')::text;

\echo ''
\echo '--- 7. SAFETY ---'
SELECT 'safety|shadow_mode_false|' || count(*)::text
  FROM shadow_decisions WHERE shadow_mode IS NOT TRUE;
SELECT 'safety|capital_at_risk_nonzero|' || count(*)::text
  FROM shadow_decisions WHERE capital_at_risk <> 0;
