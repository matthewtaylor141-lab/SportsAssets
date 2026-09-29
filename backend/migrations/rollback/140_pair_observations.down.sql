-- down for 140. Refuses while any model names observations as its training
-- source: dropping the records would leave that model's provenance naming rows
-- that no longer exist.
DO $$
BEGIN
    IF to_regclass('bettor_funded_models') IS NOT NULL AND EXISTS (
        SELECT 1 FROM bettor_funded_models
         WHERE training_provenance ->> 'source' = 'PAIR_OBSERVATIONS') THEN
        RAISE EXCEPTION 'a model is trained on pair observations; 140 is not '
                        'rolled back under it';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_pair_observation_labels;
DROP TABLE IF EXISTS bettor_pair_observations;
DROP FUNCTION IF EXISTS bettor_pair_observation_label_is_append_only();
DROP FUNCTION IF EXISTS bettor_pair_observation_is_fixed();
DROP FUNCTION IF EXISTS bettor_pair_observation_version_has_history();
