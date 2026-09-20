-- DID THE V2->V3 TRANSITION LAND, AND WHAT DID IT TOUCH?
--
-- Read-only. Asked because a commit of mine reached the default branch
-- without [skip render] and therefore auto-deployed, carrying the
-- BETTOR_EV_SHADOW_V3 policy with it. This establishes what production
-- actually recorded rather than inferring it from the push.

-- P1. Every BETTOR policy version row, oldest first.
SELECT 'P1_POLICY_VERSIONS' AS section,
       policy_version,
       left(policy_sha, 16)      AS policy_sha_16,
       left(policy_code_sha, 16) AS policy_code_sha_16,
       lane,
       action_set,
       frozen_at
  FROM shadow_policy_versions
 WHERE lane = 'BETTOR_EV_SHADOW'
 ORDER BY frozen_at;

-- P2. Decisions by policy version. V2's rows must be unchanged in
-- count and V3's are whatever has been written since the boot.
SELECT 'P2_DECISIONS_BY_VERSION' AS section,
       policy_version,
       count(*)              AS decisions,
       min(decision_ts)      AS first_decision,
       max(decision_ts)      AS last_decision,
       count(*) FILTER (WHERE action_ev_status = 'NOT_IDENTIFIED')
           AS action_ev_not_identified,
       count(*) FILTER (WHERE action_ev_components IS NOT NULL)
           AS with_components
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY policy_version
 ORDER BY policy_version;

-- P3. THE SAFETY QUESTION. Any BETTOR action other than NO_TRADE, ever.
SELECT 'P3_NON_NO_TRADE_ACTIONS' AS section,
       coalesce(action, 'NULL') AS action,
       count(*)                 AS rows
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1
 ORDER BY 2 DESC;

-- P4. Any position the BETTOR EV lane has ever opened.
SELECT 'P4_BETTOR_POSITIONS' AS section,
       count(*) AS positions
  FROM shadow_positions
 WHERE lane = 'BETTOR_EV_SHADOW';
