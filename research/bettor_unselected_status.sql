-- §17 status block for BETTOR_UNSELECTED_STATE_V1.
--
-- Read only. Descriptive counts only: rows, markets, events, maturation
-- and rule integrity. NOT one of the pre-registered TESTS, and it may
-- not become one retrospectively (bettor_preregistration.
-- EARLY_LOOKS_ARE_NOT_TESTS).
--
-- No settlement-minus-quote term appears anywhere below, deliberately:
-- looking at the outcome distribution before the gate opens is the
-- thing the pre-registration exists to prevent, and a query is where
-- that would start.

SELECT 'A_CAPTURE' AS section, k, v FROM (
    SELECT 'PROSPECTIVE_ROWS_CAPTURED' AS k,
           count(*)::text AS v FROM bettor_state_observations
    UNION ALL
    SELECT 'INDEPENDENT_MARKETS_CAPTURED',
           count(DISTINCT market_id)::text FROM bettor_state_observations
    UNION ALL
    SELECT 'INDEPENDENT_EVENTS_CAPTURED',
           count(DISTINCT event_id)::text FROM bettor_state_observations
    UNION ALL
    SELECT 'FIRST_ROW_AT',
           coalesce(min(observed_at)::text, 'NONE')
      FROM bettor_state_observations
    UNION ALL
    SELECT 'LATEST_ROW_AT',
           coalesce(max(observed_at)::text, 'NONE')
      FROM bettor_state_observations
    UNION ALL
    SELECT 'DISTINCT_RULE_SHAS',
           count(DISTINCT rule_sha)::text FROM bettor_state_observations
    UNION ALL
    SELECT 'DISTINCT_UNIVERSE_VERSIONS',
           count(DISTINCT universe_version)::text
      FROM bettor_state_observations
    UNION ALL
    SELECT 'ROWS_WITH_TRUNCATED_SLICE',
           count(*) FILTER (WHERE slice_truncated)::text
      FROM bettor_state_observations
    UNION ALL
    SELECT 'DISTINCT_CYCLES_SAMPLED',
           count(DISTINCT selection_cycle)::text
      FROM bettor_state_observations
) a

UNION ALL

-- READABILITY IS REPORTED, NOT FILTERED. Unreadable rows are part of
-- the frame by design; their share is a property of the venue, and a
-- share that moves is a fact about the venue rather than a reason to
-- drop rows.
SELECT 'B_READABILITY', book_readability_status,
       count(*)::text
  FROM bettor_state_observations
 GROUP BY 2

UNION ALL

SELECT 'C_MATURATION', k, v FROM (
    SELECT 'MATURED_5S' AS k, 'NOT_OBSERVABLE_AT_THIS_CADENCE' AS v
    UNION ALL SELECT 'MATURED_15S', 'NOT_OBSERVABLE_AT_THIS_CADENCE'
    UNION ALL SELECT 'MATURED_30S', 'NOT_OBSERVABLE_AT_THIS_CADENCE'
    UNION ALL
    SELECT 'MATURED_60S',
           count(*) FILTER (WHERE horizon_s = 60
                              AND status = 'OBSERVED')::text
      FROM bettor_state_mids
    UNION ALL
    SELECT 'MATURED_300S',
           count(*) FILTER (WHERE horizon_s = 300
                              AND status = 'OBSERVED')::text
      FROM bettor_state_mids
    UNION ALL
    SELECT 'MATURED_900S',
           count(*) FILTER (WHERE horizon_s = 900
                              AND status = 'OBSERVED')::text
      FROM bettor_state_mids
    UNION ALL
    SELECT 'MATURED_3600S',
           count(*) FILTER (WHERE horizon_s = 3600
                              AND status = 'OBSERVED')::text
      FROM bettor_state_mids
    UNION ALL
    -- A read that landed outside its tolerance is counted apart. It is
    -- evidence about ITS OWN lag, never relabelled as the nominal one.
    SELECT 'MIDS_OUTSIDE_TOLERANCE',
           count(*) FILTER (WHERE within_tolerance IS FALSE)::text
      FROM bettor_state_mids
    UNION ALL
    SELECT 'MATURED_SETTLEMENT',
           count(*) FILTER (WHERE settlement_status = 'RESOLVED')::text
      FROM bettor_state_settlements
    UNION ALL
    SELECT 'SETTLEMENT_SEMANTICS_VERIFIED',
           count(*) FILTER (
               WHERE settlement_semantics_status <> 'SEMANTICS_NOT_VERIFIED'
           )::text
      FROM bettor_state_settlements
) c

UNION ALL

-- THE GATE'S BINDING QUANTITY. Rows within one event are not
-- independent, so events are what the pre-registration counts.
SELECT 'D_GATE', 'EVENTS_WITH_A_SETTLED_OBSERVATION',
       count(DISTINCT o.event_id)::text
  FROM bettor_state_observations o
  JOIN bettor_state_settlements s USING (observation_id)
 WHERE s.settlement_status = 'RESOLVED'

UNION ALL

-- Coverage by declared price band, so a thin band is visible before it
-- blocks the gate. THE BANDS ARE THE PRE-REGISTERED ONES; this query
-- does not invent its own.
SELECT 'E_BAND_COVERAGE',
       CASE
         WHEN mid IS NULL OR mid = 'NOT_IDENTIFIED' THEN 'NO_MID'
         WHEN mid::numeric <  0.05 THEN 'DEEP_LOW'
         WHEN mid::numeric <  0.15 THEN 'LOW'
         WHEN mid::numeric <  0.35 THEN 'MID_LOW'
         WHEN mid::numeric <  0.65 THEN 'CENTRE'
         WHEN mid::numeric <  0.85 THEN 'MID_HIGH'
         WHEN mid::numeric <  0.95 THEN 'HIGH'
         ELSE 'DEEP_HIGH'
       END,
       count(DISTINCT event_id)::text
  FROM bettor_state_observations
 GROUP BY 2

ORDER BY 1, 2
