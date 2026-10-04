-- Rolls back migration 218. Every table here is SHADOW / RESEARCH output of
-- sportsassets/poslearn (no order, limit, threshold, allowlist, ledger or
-- production probability reads it), so dropping it removes only the
-- tournaments', meta-models' and experiments' own history -- including any
-- human approval record, which had production_effect 'NONE'. The runner
-- checks to_regclass and idles when the tables are absent.
DROP TABLE IF EXISTS poslearn_experiment_outcomes;
DROP TABLE IF EXISTS poslearn_experiment_assignments;
DROP TABLE IF EXISTS poslearn_experiment_reviews;
DROP TABLE IF EXISTS poslearn_experiments;
DROP TABLE IF EXISTS poslearn_human_approvals;
DROP TABLE IF EXISTS poslearn_promotion_steps;
DROP TABLE IF EXISTS poslearn_forecasts;
DROP TABLE IF EXISTS poslearn_outcomes;
DROP TABLE IF EXISTS poslearn_opportunities;
DROP TABLE IF EXISTS poslearn_registrations;
DROP TABLE IF EXISTS poslearn_snapshots;
DROP TABLE IF EXISTS poslearn_runs;
DROP FUNCTION IF EXISTS poslearn_experiment_outcomes_guard();
DROP FUNCTION IF EXISTS poslearn_assignments_guard();
DROP FUNCTION IF EXISTS poslearn_experiments_guard();
DROP FUNCTION IF EXISTS poslearn_human_approvals_guard();
DROP FUNCTION IF EXISTS poslearn_promotion_steps_guard();
DROP FUNCTION IF EXISTS poslearn_forecasts_guard();
DROP FUNCTION IF EXISTS poslearn_opportunities_guard();
DROP FUNCTION IF EXISTS poslearn_source_outcome_known(text, bigint);
DROP FUNCTION IF EXISTS poslearn_registrations_guard();
DROP FUNCTION IF EXISTS poslearn_append_only();
DROP FUNCTION IF EXISTS poslearn_draw(text, text, text);
DROP FUNCTION IF EXISTS poslearn_no_authority(jsonb);
DROP FUNCTION IF EXISTS poslearn_is_agent_actor(text);
