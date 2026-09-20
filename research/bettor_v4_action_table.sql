-- THE FULL ACTION TABLE OF THE FIRST POST-PACKAGING DECISION (§6).
-- Read-only. Every canonical action, with what is identified and what
-- is not -- NOT_IDENTIFIED is never collapsed to zero.

WITH d AS (
    SELECT shadow_decision_id, decision_ts, policy_version,
           action_ev_status, action_ev_components
      FROM shadow_decisions
     WHERE shadow_decision_id =
           'bdec_e8e5d8f23f56df1d8bdc2cfa6a578899a17b025a'
), a AS (
    SELECT jsonb_array_elements(d.action_ev_components -> 'table') AS r
      FROM d
)
SELECT 'T1_ACTION_TABLE' AS section,
       r ->> 'action'                 AS action,
       r ->> 'leg'                    AS leg,
       r ->> 'aggression'             AS aggression,
       r ->> 'status'                 AS status,
       r ->> 'fairValueKind'          AS fv_venue_implied_kind,
       r ->> 'FV_BETTOR_INDEPENDENT'  AS fv_bettor_independent,
       r ->> 'settlementEv'           AS settlement_ev,
       r ->> 'SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE'
                                      AS snapshot_execution_cost,
       r ->> 'feeStatus'              AS fee_status,
       r -> 'BREAK_EVEN_P_FILL_BAND'  AS break_even_p_fill_band,
       r #>> '{risk,permitted}'       AS risk_permitted,
       r #>> '{risk,direction}'       AS risk_direction,
       coalesce(r ->> 'whyNot', r ->> 'whyIdentified',
                r ->> 'whatThisDoesNotEstablish') AS why_not
  FROM a
 ORDER BY 2;

-- T2. The decision-level facts and the fill-selection prior it carried.
SELECT 'T2_DECISION' AS section,
       shadow_decision_id,
       decision_ts,
       policy_version,
       action_ev_status,
       action_ev_components ->> 'settlementEvStatus' AS settlement_ev_status,
       action_ev_components ->> 'bestAction'         AS best_action,
       action_ev_components -> 'executionCostIdentifiedFor'
                                                     AS execution_cost_for,
       action_ev_components #>> '{fillSelectionPrior,P10}' AS fs_p10,
       action_ev_components #>> '{fillSelectionPrior,P50}' AS fs_p50,
       action_ev_components #>> '{fillSelectionPrior,P90}' AS fs_p90,
       action_ev_components #>> '{fillSelectionPrior,directionAssumed}'
                                                     AS fs_direction_assumed,
       action_ev_components #>> '{engineProvenance,loadedFrom}'
                                                     AS engine_loaded_from
  FROM d;
