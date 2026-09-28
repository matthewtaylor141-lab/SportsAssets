-- THE CANDIDATE CROSS-TAB: measured negative economics separated from
-- missing evidence.
--
-- Directive: "The 1,073 NO_ACTION_HAS_POSITIVE_NET_EDGE rows and 1,067
-- EXECUTION_ESTIMATE_NOT_IDENTIFIED rows may substantially overlap. A
-- missing execution estimate cannot establish a negative economic
-- result." And: "Prove the claim that only 42 are blocked by settlement
-- alone by showing that every other required stage was actually
-- evaluated and passed. Absence of another refusal code is insufficient
-- where evaluation stopped early."
--
-- So this reports FIRST refusal separately from EVERY refusal, and the
-- co-occurrence of the two economics codes directly.
--
-- READ-ONLY: SELECT only, no mutating keyword in executable SQL.

\echo == A == WINDOW, DISTINCT CANDIDATES, DISTINCT EVENTS, REPEATS

SELECT
    min(decided_at)                              AS window_from,
    max(decided_at)                              AS window_to,
    count(*)                                     AS evaluation_rows,
    count(DISTINCT event_key)                    AS distinct_events,
    count(DISTINCT (event_key, condition_id))    AS distinct_candidates,
    round(count(*)::numeric
          / greatest(count(DISTINCT (event_key, condition_id)), 1), 2)
                                                 AS evaluations_per_candidate,
    count(*) FILTER (WHERE admissible)           AS admissible
  FROM external_valuations
 WHERE decided_at >= now() - interval '30 days';

\echo == B == DO THE TWO ECONOMICS CODES OVERLAP?

WITH w AS (
    SELECT id, coalesce(refusals, ARRAY[]::text[]) AS r
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
)
SELECT
    count(*)                                                   AS rows,
    count(*) FILTER (WHERE 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r))
                                                               AS no_positive_edge,
    count(*) FILTER (WHERE 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                                               AS exec_unmeasured,
    count(*) FILTER (WHERE 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r)
                       AND 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                                               AS both,
    count(*) FILTER (WHERE 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r)
                       AND NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                                               AS edge_only,
    count(*) FILTER (WHERE NOT 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r)
                       AND 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                                               AS exec_only
  FROM w;

\echo == C == THE CROSS-TAB, ONE BUCKET PER EVALUATION ROW

WITH w AS (
    SELECT id, event_key, condition_id, decided_at, admissible,
           coalesce(refusals, ARRAY[]::text[]) AS r
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
), f AS (
    SELECT id, event_key, condition_id, decided_at, admissible, r,
           'INDEPENDENT_FAIR_VALUE_NOT_ESTABLISHED' = ANY(r)
               OR 'NO_QUALIFIED_MODEL' = ANY(r)
               OR 'QUOTE_STALE' = ANY(r)                AS valuation_missing,
           'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r)
               OR 'NO_OBSERVED_DEPTH_INSIDE_THE_BREAK_EVEN_LIMIT' = ANY(r)
               OR 'SIZING_POLICY_NOT_APPLICABLE' = ANY(r) AS exec_unmeasured,
           'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r)     AS non_positive,
           'VOID_ABANDONMENT_RULE_CONFLICTS_WITH_BOOK_RULE' = ANY(r)
               OR 'OVERTIME_RULE_CONFLICTS_WITH_BOOK_RULE' = ANY(r)
               OR 'SETTLEMENT_TERMS_CONFLICT' = ANY(r)    AS settle_conflict,
           'VOID_ABANDONMENT_RULE_NOT_ESTABLISHED' = ANY(r)
               OR 'OVERTIME_RULE_NOT_ESTABLISHED' = ANY(r)
               OR 'VOID_ABANDONMENT_BOOK_RULE_NOT_HELD' = ANY(r)
               OR 'DRAW_HANDLING_NOT_RECONCILED' = ANY(r) AS settle_unresolved,
           'RISK_GATE_BLOCKED' = ANY(r)                   AS risk_blocked
      FROM w
)
SELECT
    CASE
      -- A non-positive edge is only MEASURED when execution economics and
      -- valuation were both available. Otherwise the refusal is an
      -- artefact of a missing input, not an economic finding.
      WHEN non_positive AND NOT exec_unmeasured AND NOT valuation_missing
           THEN '1_QUALIFIED_NON_POSITIVE_ECONOMICS'
      WHEN NOT non_positive AND NOT exec_unmeasured AND NOT valuation_missing
           AND (settle_conflict OR settle_unresolved OR risk_blocked)
           THEN '2_POSITIVE_OR_UNREFUSED_ECONOMICS_BLOCKED_ELSEWHERE'
      WHEN exec_unmeasured AND NOT valuation_missing
           THEN '3_EXECUTION_ECONOMICS_UNMEASURED'
      WHEN valuation_missing
           THEN '4_VALUATION_EVIDENCE_MISSING'
      WHEN settle_conflict THEN '5_ESTABLISHED_SETTLEMENT_CONFLICT'
      WHEN settle_unresolved THEN '6_SETTLEMENT_EVIDENCE_UNRESOLVED'
      ELSE '7_OTHER'
    END                                        AS bucket,
    count(*)                                   AS evaluation_rows,
    count(DISTINCT (event_key, condition_id))  AS distinct_candidates,
    count(DISTINCT event_key)                  AS distinct_events,
    count(*) FILTER (WHERE admissible)         AS admissible
  FROM f
 GROUP BY 1
 ORDER BY 1;

\echo == D == FIRST REFUSAL VERSUS EVERY REFUSAL

-- The refusals array preserves the order the lane appended them, so
-- element 1 is the FIRST stage that refused. A candidate whose first
-- refusal is a valuation gap never reached its economics, so its
-- economics code -- if present at all -- says nothing about its edge.

WITH w AS (
    SELECT id, coalesce(refusals, ARRAY[]::text[]) AS r
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
)
SELECT r[1]                              AS first_refusal,
       count(*)                          AS rows_where_it_is_first,
       round(avg(array_length(r, 1)), 2) AS avg_total_refusals
  FROM w
 WHERE array_length(r, 1) >= 1
 GROUP BY 1
 ORDER BY rows_where_it_is_first DESC;

\echo == E == SETTLEMENT-ALONE, PROVED BY STAGES EVALUATED NOT BY ABSENCE

-- The earlier "42 blocked on settlement alone" counted rows carrying a
-- settlement code and no other family's code. Absence is not evidence
-- that the other stages RAN. A stage that ran and passed leaves a
-- positive trace, so this counts only rows where those traces exist.

WITH w AS (
    SELECT id, coalesce(refusals, ARRAY[]::text[]) AS r,
           overround, outcomes_priced, expected_outcomes, age_s,
           outcome_books, settlement_rule
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
)
SELECT
    count(*)                                                AS rows,
    count(*) FILTER (WHERE settlement_rule IS NOT NULL)      AS settlement_scope_captured,
    count(*) FILTER (WHERE overround IS NOT NULL)            AS valuation_stage_ran,
    count(*) FILTER (WHERE age_s IS NOT NULL)                AS currency_stage_ran,
    count(*) FILTER (WHERE outcomes_priced = expected_outcomes)
                                                            AS all_outcomes_priced,
    count(*) FILTER (WHERE NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                                            AS execution_stage_produced_an_estimate,
    count(*) FILTER (
        WHERE overround IS NOT NULL
          AND age_s IS NOT NULL
          AND outcomes_priced = expected_outcomes
          AND NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r)
          AND NOT 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r)
          AND NOT 'RISK_GATE_BLOCKED' = ANY(r)
    )                                                       AS every_other_stage_ran_and_passed
  FROM w;

\echo == F == WHAT THE ECONOMICS STAGE ACTUALLY SAW WHERE IT RAN

WITH w AS (
    SELECT coalesce(refusals, ARRAY[]::text[]) AS r,
           overround, age_s, outcome_books
      FROM external_valuations
     WHERE decided_at >= now() - interval '30 days'
)
SELECT
    count(*) FILTER (WHERE NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))
                                             AS execution_estimate_present,
    count(*) FILTER (WHERE NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r)
                       AND 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r))
                                             AS and_edge_was_non_positive,
    count(*) FILTER (WHERE NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r)
                       AND NOT 'NO_ACTION_HAS_POSITIVE_NET_EDGE' = ANY(r))
                                             AS and_edge_was_not_refused,
    round(avg(overround) FILTER (
        WHERE NOT 'EXECUTION_ESTIMATE_NOT_IDENTIFIED' = ANY(r))::numeric, 4)
                                             AS avg_overround_where_measured
  FROM w;
