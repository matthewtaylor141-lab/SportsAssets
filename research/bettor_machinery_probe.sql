-- WHAT PATH DID THE RUNNING WORKER ACTUALLY RESOLVE?
--
-- The bridge's fail-closed payload records `researchRoot` and `why`.
-- Reading them straight out of the ledger beats inferring from the
-- source tree, which is what §3 asks for.

SELECT 'M1_RESEARCH_ROOT_SEEN' AS section,
       action_ev_components ->> 'researchRoot' AS research_root_resolved,
       left(action_ev_components ->> 'why', 150) AS why,
       count(*)         AS rows,
       min(decision_ts) AS first_seen,
       max(decision_ts) AS last_seen
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
   AND action_ev_status = 'EV_MACHINERY_UNAVAILABLE'
 GROUP BY 2, 3
 ORDER BY max(decision_ts) DESC;

-- M2. The most recent BETTOR decision, whatever its status, so a flip
-- is visible the moment it happens.
SELECT 'M2_LATEST' AS section,
       decision_ts,
       policy_version,
       action_ev_status,
       proposed_action,
       left(action_ev_components::text, 300) AS components_head
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 ORDER BY decision_ts DESC
 LIMIT 3;
