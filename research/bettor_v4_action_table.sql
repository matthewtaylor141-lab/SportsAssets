-- THE FIRST POST-PACKAGING ACTION TABLE ON A READABLE BOOK (§6).
--
-- The very first V4 decision priced all 15 actions but against an
-- unreadable book -- no mid, no two-sided quote -- so every cell was
-- NOT_IDENTIFIED for a market-data reason rather than a modelling one.
-- That is honest and uninformative. This finds the first decision where
-- the book WAS readable, so the engine's actual output is visible.

-- R0. How often the book is readable at all, which is its own finding.
SELECT 'R0_BOOK_READABILITY' AS section,
       count(*) AS v4_decisions,
       count(*) FILTER (
           WHERE action_ev_components::text
                 LIKE '%SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE": "-%')
           AS with_priced_execution_cost,
       min(decision_ts) AS first_v4,
       max(decision_ts) AS latest_v4
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
   AND policy_version = 'BETTOR_EV_SHADOW_V4';

-- R1. The decision-level facts of the first readable-book V4 decision.
SELECT 'R1_DECISION' AS section,
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
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
   AND policy_version = 'BETTOR_EV_SHADOW_V4'
   AND action_ev_components::text
       LIKE '%SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE": "-%'
 ORDER BY decision_ts
 LIMIT 1;

-- R2. Its full action table, every canonical action.
--
-- The decision is chosen FIRST and expanded second. An earlier version
-- put LIMIT 1 outside jsonb_array_elements, which limited the expanded
-- ROWS to one rather than the decisions to one, and printed a
-- one-action "table".
SELECT 'R2_ACTION_TABLE' AS section,
       r ->> 'action'                 AS action,
       r ->> 'leg'                    AS leg,
       r ->> 'aggression'             AS aggression,
       r ->> 'status'                 AS status,
       r ->> 'FV_BETTOR_INDEPENDENT'  AS fv_bettor_indep,
       r ->> 'SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE' AS exec_cost,
       r ->> 'feeStatus'              AS fee,
       r #>> '{BREAK_EVEN_P_FILL_BAND,P10}' AS be_p10,
       r #>> '{BREAK_EVEN_P_FILL_BAND,P50}' AS be_p50,
       r #>> '{BREAK_EVEN_P_FILL_BAND,P90}' AS be_p90,
       r ->> 'EV_IF_NO_FILL'          AS ev_if_no_fill,
       r ->> 'fillSelectionConvention' AS fs_convention,
       r #>> '{risk,permitted}'       AS risk_ok,
       r #>> '{risk,direction}'       AS risk_dir,
       left(coalesce(r ->> 'whyNot', r ->> 'whyIdentified',
                     r ->> 'whatThisDoesNotEstablish'), 52) AS why
  FROM (
    SELECT jsonb_array_elements(c.comp -> 'table') AS r
      FROM (
        SELECT action_ev_components AS comp
          FROM shadow_decisions
         WHERE lane = 'BETTOR_EV_SHADOW'
           AND policy_version = 'BETTOR_EV_SHADOW_V4'
           AND action_ev_components::text
               LIKE '%SNAPSHOT_EXECUTION_COST_VS_VENUE_PRICE": "-%'
         ORDER BY decision_ts
         LIMIT 1
      ) c
  ) x
 ORDER BY 2;
