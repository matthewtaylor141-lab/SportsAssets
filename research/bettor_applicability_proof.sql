-- §18: THE FLAT-STATE PROOF, FROM PRODUCTION.
-- §16: BOOK READABILITY, MEASURED RATHER THAN TUNED AROUND.

-- A1. Applicability by state across V5, so the gate is visible in the
-- aggregate before any single decision is read.
SELECT 'A1_BY_STATE' AS section,
       r ->> 'INVENTORY_STATE'      AS inventory_state,
       r ->> 'APPLICABILITY_STATUS' AS applicability,
       count(*)                     AS action_rows,
       count(DISTINCT r ->> 'action') AS distinct_actions
  FROM (
    SELECT jsonb_array_elements(action_ev_components -> 'table') AS r
      FROM shadow_decisions
     WHERE lane = 'BETTOR_EV_SHADOW'
       AND policy_version = 'BETTOR_EV_SHADOW_V5'
  ) y
 GROUP BY 2, 3
 ORDER BY 2, 3;

-- A2. THE PROOF DECISION: a FLAT book, with each action's verdict.
SELECT 'A2_FLAT_PROOF' AS section,
       r ->> 'action'                 AS action,
       r ->> 'APPLICABILITY_STATUS'   AS applicability,
       r ->> 'ECONOMIC_STATUS'        AS economic_status,
       r #>> '{risk,RISK_STATUS}'     AS risk_status,
       r ->> 'decisionScope'          AS scope,
       r ->> 'P_FILL_STATUS'          AS p_fill_status,
       r ->> 'PAIR_EV_STATUS'         AS pair_ev_status,
       r ->> 'RESIDUAL_EV_STATUS'     AS residual_ev_status,
       left(r ->> 'APPLICABILITY_REASON', 58) AS reason
  FROM (
    SELECT jsonb_array_elements(c.comp -> 'table') AS r
      FROM (
        SELECT action_ev_components AS comp
          FROM shadow_decisions
         WHERE lane = 'BETTOR_EV_SHADOW'
           AND policy_version = 'BETTOR_EV_SHADOW_V5'
           AND action_ev_components::text LIKE '%"INVENTORY_STATE": "FLAT"%'
         ORDER BY decision_ts
         LIMIT 1
      ) c
  ) x
 ORDER BY 3, 2;

-- A3. §16 BOOK READABILITY, by market type. Measured, not selected.
SELECT 'A3_BOOK_READABILITY' AS section,
       coalesce(sport, 'UNKNOWN')   AS sport,
       count(*)                     AS opportunities_total,
       count(*) FILTER (WHERE market_bid IS NOT NULL
                          AND market_ask IS NOT NULL) AS readable_two_sided,
       count(*) FILTER (WHERE market_bid IS NULL
                          AND market_ask IS NULL)     AS unreadable,
       count(*) FILTER (WHERE (market_bid IS NULL) <> (market_ask IS NULL))
                                                      AS one_sided,
       count(*) FILTER (WHERE mid IS NOT NULL)        AS mid_present,
       count(*) FILTER (WHERE available_depth IS NOT NULL) AS depth_present
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 2
 ORDER BY opportunities_total DESC;

-- A4. The same, by policy version, so the rate can be tracked forward.
SELECT 'A4_READABILITY_BY_VERSION' AS section,
       policy_version,
       count(*) AS opportunities_total,
       count(*) FILTER (WHERE market_bid IS NOT NULL
                          AND market_ask IS NOT NULL) AS readable_two_sided,
       round(100.0 * count(*) FILTER (WHERE market_bid IS NOT NULL
                                        AND market_ask IS NOT NULL)
             / nullif(count(*), 0), 1) AS readable_pct
  FROM shadow_decisions
 WHERE lane = 'BETTOR_EV_SHADOW'
 GROUP BY 2
 ORDER BY 2;
