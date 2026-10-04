-- Rollback of 219 (the economic digital twin and profitability evidence).
-- Refuses while any preregistration, frozen scenario or kill-switch
-- recommendation exists: they are research evidence and are never dropped
-- as cleanup. Runs, snapshots, scorecards, ladder rows and evals alone are
-- re-derivable from the production tables and do not block the rollback.
-- Touches only twin_* objects.
DO $$
BEGIN
    IF to_regclass('twin_transfer_tests') IS NOT NULL
       AND EXISTS (SELECT 1 FROM twin_transfer_tests) THEN
        RAISE EXCEPTION 'twin_transfer_tests holds preregistrations; rollback refused';
    END IF;
    IF to_regclass('twin_scenario_results') IS NOT NULL
       AND EXISTS (SELECT 1 FROM twin_scenario_results) THEN
        RAISE EXCEPTION 'twin_scenario_results holds results; rollback refused';
    END IF;
    IF to_regclass('twin_kill_switch_recommendations') IS NOT NULL
       AND EXISTS (SELECT 1 FROM twin_kill_switch_recommendations) THEN
        RAISE EXCEPTION 'twin_kill_switch_recommendations holds records; rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS twin_evals;
DROP TABLE IF EXISTS twin_kill_switch_recommendations;
DROP TABLE IF EXISTS twin_evidence_ladder;
DROP TABLE IF EXISTS twin_agent_scorecards;
DROP TABLE IF EXISTS twin_transfer_evaluations;
DROP TABLE IF EXISTS twin_transfer_tests;
DROP TABLE IF EXISTS twin_decision_traces;
DROP TABLE IF EXISTS twin_scenario_results;
DROP TABLE IF EXISTS twin_scenarios;
DROP TABLE IF EXISTS twin_frozen_specs;
DROP TABLE IF EXISTS twin_snapshots;
DROP TABLE IF EXISTS twin_runs;
DROP FUNCTION IF EXISTS twin_transfer_forward_only();
DROP FUNCTION IF EXISTS twin_leading_level(boolean[]);
DROP FUNCTION IF EXISTS twin_append_only();
