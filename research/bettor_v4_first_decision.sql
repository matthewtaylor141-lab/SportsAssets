-- THE FIRST POST-PACKAGING BETTOR DECISION, AND THE COHORT SPLIT.
--
-- §6: PRODUCTION_EV_MACHINERY_AVAILABLE may not be called YES until a
-- NEW decision has action_ev_status != EV_MACHINERY_UNAVAILABLE AND a
-- non-empty action table. Both are checked here rather than assumed
-- from the worker's own preflight.

-- F1. Every BETTOR policy version, with its status breakdown.
SELECT 'F1_BY_VERSION' AS section,
       policy_version,
       coalesce(action_ev_status, 'NULL') AS action_ev_status,
       count(*)         AS decisions,
       min(decision_ts) AS first_seen,
       max(decision_ts) AS last_seen
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 2, 3
 ORDER BY policy_version, decisions DESC;

-- F2. §5's two figures, reported separately.
SELECT 'F2_COHORT' AS section,
       count(*) FILTER (WHERE policy_version = 'BETTOR_EV_SHADOW_V3'
                        AND action_ev_status = 'EV_MACHINERY_UNAVAILABLE')
           AS v3_pre_packaging_ev_machinery_unavailable_decisions,
       count(*) FILTER (WHERE policy_version = 'BETTOR_EV_SHADOW_V3'
                        AND action_ev_status <> 'EV_MACHINERY_UNAVAILABLE')
           AS v3_post_packaging_ev_decisions
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW';

-- F3. THE FIRST post-packaging decision: machinery loaded AND a
-- non-empty table. Both conditions, because either alone is not proof.
SELECT 'F3_FIRST_POST_PACKAGING' AS section,
       shadow_decision_id,
       decision_ts,
       policy_version,
       action_ev_status,
       proposed_action,
       jsonb_array_length(action_ev_components -> 'table') AS action_table_rows
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
   AND action_ev_status <> 'EV_MACHINERY_UNAVAILABLE'
   AND action_ev_components ? 'table'
   AND jsonb_array_length(action_ev_components -> 'table') > 0
 ORDER BY decision_ts
 LIMIT 1;
