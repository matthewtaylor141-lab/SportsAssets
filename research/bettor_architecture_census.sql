-- ═══════════════════════════════════════════════════════════════════
-- BETTOR ARCHITECTURE CENSUS -- PRODUCTION EVIDENCE
--
-- Owner directive 2026-09-20 "ROADMAP RESET" §1:
--
--   "First perform an exact architecture census against the current
--    repository... Do not call an interface an engine. Do not call a
--    model 'built' if it has never produced an observed prospective
--    output. Do not call something decision-grade merely because
--    tests pass."
--
-- This file answers ONLY the production-evidence half. A table that
-- exists with zero rows is an INTERFACE. A table with rows whose
-- load-bearing columns are all NULL is a WRITER, not an ENGINE. Both
-- distinctions are made explicit below rather than left to a reader
-- counting tables.
-- ═══════════════════════════════════════════════════════════════════

\echo '--- C1. DOES THE TABLE EXIST, AND HAS IT EVER BEEN WRITTEN? ---'
-- The floor of the census. No interpretation, just rows.
SELECT 'table|' || c.relname
       || '|rows=' || COALESCE(to_char(s.n_live_tup, 'FM99999999990'),
                               'UNKNOWN')
       || '|inserts=' || COALESCE(to_char(s.n_tup_ins, 'FM99999999990'),
                                  'UNKNOWN')
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
 WHERE n.nspname = 'public'
   AND c.relkind = 'r'
   AND c.relname IN (
       -- A. market data / state
       'bettor_l2_evidence', 'bettor_l2_requests',
       'bettor_identity_bindings', 'shadow_market_states',
       'market_starts', 'market_tokens', 'markets',
       -- B. bettor fair value
       'shadow_decisions', 'shadow_specialists', 'shadow_scores',
       'shadow_policy_versions', 'shadow_disagreements',
       'bettor_opportunities', 'bettor_decision_failures',
       'bettor_opportunity_annotations',
       -- C. execution
       'shadow_executions', 'engine_fills', 'live_orders',
       -- F. inventory / positions
       'shadow_positions', 'shadow_position_events',
       -- experimental lane (X-series infrastructure to be reused)
       'bettor_experimental_decisions', 'bettor_experimental_positions',
       'bettor_experimental_position_events',
       'bettor_experimental_markouts', 'bettor_experimental_seals',
       'bettor_experimental_observations', 'bettor_experiments',
       'bettor_eligible_populations', 'bettor_sizing_policies',
       -- calibration / real-money probe
       'calibration_lifecycles', 'calibration_sessions',
       'calibration_send_attempts')
 ORDER BY c.relname;

\echo ''
\echo '--- C2. BETTOR EV LANE: DOES IT DECIDE, OR ONLY REFUSE? ---'
-- THE CENTRAL QUESTION OF THE CENSUS. shadow_decisions is the
-- decision-grade lane. If every row is NO_TRADE with p_bettor NULL,
-- the lane is a REFUSAL RECORDER -- which is honest and was always
-- permitted -- but it is not a fair-value engine, and the census must
-- not call it one.
SELECT 'ev_lane|' || COALESCE(proposed_action, 'NULL')
       || '|n=' || count(*)
       || '|p_bettor_present=' || count(p_bettor)
       || '|p_market_present=' || count(p_market)
       || '|p_fill_present=' || count(p_fill)
       || '|total_action_ev_present=' || count(total_action_ev)
       || '|uncertainty_present=' || count(uncertainty)
  FROM shadow_decisions
 GROUP BY proposed_action
 ORDER BY count(*) DESC;

\echo ''
\echo '--- C3. WHY THE EV LANE DECLINES ---'
-- The closed blocker vocabulary, counted. This is what management
-- should eventually see as "which blockers prevent the most trades".
SELECT 'ev_blocker|' || stage || '|' || error_class
       || '|n=' || count(*)
       || '|last=' || to_char(max(failed_at), 'YYYY-MM-DD HH24:MI:SSZ')
  FROM bettor_decision_failures
 GROUP BY stage, error_class
 ORDER BY count(*) DESC
 LIMIT 25;

\echo ''
\echo '--- C4. HAS THE EV LANE EVER EXECUTED OR HELD A POSITION? ---'
SELECT 'ev_execution|executions=' || (SELECT count(*) FROM shadow_executions)
       || '|positions=' || (SELECT count(*) FROM shadow_positions)
       || '|position_events=' || (SELECT count(*)
                                    FROM shadow_position_events);

\echo ''
\echo '--- C5. SPECIALISTS: REGISTERED vs SCORED ---'
-- A specialist registry with no scores is an interface. §13/§14 want
-- champion/challenger; this says whether any challenger has ever been
-- evaluated on prospective evidence.
SELECT 'specialists_registered=' || (SELECT count(*)
                                     FROM shadow_specialists)
       || '|decisions_scored=' || (SELECT count(DISTINCT
                                          shadow_decision_id)
                                     FROM shadow_scores)
       || '|score_rows=' || (SELECT count(*) FROM shadow_scores)
       || '|observable_scores=' || (SELECT count(*) FROM shadow_scores
                                     WHERE observable);

\echo ''
\echo '--- C6. PER-LEG STATE: IS YES/NO EVER HELD SEPARATELY? ---'
-- §4 (RN1 finding): YES and NO must never collapse into one net
-- position for decision logic. This asks whether the production
-- ledger has ever carried BOTH legs of the same market at once --
-- the precondition for a pair/merge engine existing at all.
WITH legs AS (
    SELECT market_id,
           count(DISTINCT side) AS sides,
           count(*) AS positions
      FROM bettor_experimental_positions
     GROUP BY market_id
)
SELECT 'per_leg|markets=' || count(*)
       || '|markets_with_both_sides=' || count(*) FILTER (WHERE sides > 1)
       || '|max_sides_on_one_market=' || COALESCE(max(sides), 0)
  FROM legs;

\echo ''
\echo '--- C7. THE ACTION VOCABULARY ACTUALLY RECORDED ---'
-- §0 lists fourteen candidate actions. This is how many of them any
-- production row has ever named.
SELECT 'action_seen|experimental|' || COALESCE(action, 'NULL')
       || '|n=' || count(*)
  FROM bettor_experimental_decisions
 GROUP BY action
 ORDER BY count(*) DESC;

\echo ''
\echo '--- C8. LIFECYCLE STATES REACHED IN PRODUCTION ---'
-- QUOTE -> FILL -> INVENTORY -> COMPLEMENT -> PAIR -> MERGE ->
-- CASH RELEASED -> REALLOCATE (§15). How far has anything ever got?
SELECT 'lifecycle_event|' || event_type || '|n=' || count(*)
  FROM bettor_experimental_position_events
 GROUP BY event_type
 ORDER BY count(*) DESC;

\echo ''
\echo '--- C9. REAL MONEY: THE CALIBRATION PROBE ---'
SELECT 'calibration|' || COALESCE(state, 'NULL') || '|n=' || count(*)
  FROM calibration_lifecycles
 GROUP BY state
 ORDER BY count(*) DESC;
