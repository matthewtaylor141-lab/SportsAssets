-- DID THE ACCIDENTAL DEPLOY DO ANYTHING UNSAFE, AND WHICH BRIDGE IS LIVE?
--
-- Read-only. Two commits of mine reached the default branch without
-- [skip render] and auto-deployed. This establishes what production
-- actually recorded rather than inferring it from the push.

-- P3. THE SAFETY QUESTION. Any BETTOR action other than NO_TRADE, ever.
SELECT 'P3_ACTIONS' AS section,
       policy_version,
       proposed_action,
       coalesce(proposed_side, 'NULL') AS proposed_side,
       count(*) AS rows
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1, 2, 3, 4
 ORDER BY policy_version, rows DESC;

-- P4. Any position the BETTOR EV lane has ever opened.
SELECT 'P4_BETTOR_POSITIONS' AS section,
       count(*) AS positions
  FROM shadow_positions
 WHERE lane = 'BETTOR_EV_SHADOW';

-- P5. WHICH BRIDGE IS LIVE. The uncorrected first version reported
-- actionEvStatus = PARTIALLY_IDENTIFIED and called crossing REFUTED;
-- the corrected one reports NOT_IDENTIFIED and never uses that word.
SELECT 'P5_ACTION_EV_STATUS' AS section,
       policy_version,
       coalesce(action_ev_status, 'NULL') AS action_ev_status,
       count(*) AS rows,
       min(decision_ts) AS first_seen,
       max(decision_ts) AS last_seen
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 1, 2, 3
 ORDER BY policy_version, rows DESC;

-- P6. The tell, read straight out of the stored table: does any row
-- carry the retracted sign-constrained lower bound, or the retracted
-- REFUTED verdict?
SELECT 'P6_RETRACTED_CLAIMS' AS section,
       count(*) FILTER (WHERE action_ev_components::text
                        LIKE '%BREAK_EVEN_P_FILL_LOWER_BOUND%')
           AS rows_with_sign_constrained_bound,
       count(*) FILTER (WHERE action_ev_components::text
                        LIKE '%REFUTED%')
           AS rows_with_refuted_verdict,
       count(*) FILTER (WHERE action_ev_components::text
                        LIKE '%SNAPSHOT_EXECUTION_COST%')
           AS rows_with_corrected_framing,
       count(*) AS total_rows_with_components
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
   AND action_ev_components IS NOT NULL;
