-- 365 rollback: the research training set's change stamp. Nothing is a
-- record of anything here -- the counter is rebuilt from zero by 365 -- so
-- dropping it loses no evidence. Without it paper_derek reads no stamp and
-- NEVER serves a cached provenance verification (every keyed context
-- re-verifies; concurrent decisions of one session share one verification).
-- Safe to apply twice.
DROP TRIGGER IF EXISTS research_training_set_observation_changed
    ON derek_research_observations;
DROP TRIGGER IF EXISTS research_training_set_observations_truncated
    ON derek_research_observations;
DROP TRIGGER IF EXISTS research_training_set_valuation_changed
    ON external_valuations;
DROP TRIGGER IF EXISTS research_training_set_valuations_truncated
    ON external_valuations;
DROP TRIGGER IF EXISTS research_training_set_model_changed
    ON bettor_funded_models;
DROP TRIGGER IF EXISTS research_training_set_models_truncated
    ON bettor_funded_models;
DROP FUNCTION IF EXISTS research_training_set_changed();
DROP TABLE IF EXISTS research_training_set_changes;
