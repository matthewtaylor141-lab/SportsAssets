-- down for 170. Refuses while any research observation exists: they are the
-- internal entry model's training and evaluation records, and a model in the
-- registry may name them in its provenance.
DO $$
BEGIN
    IF to_regclass('derek_research_observations') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_research_observations) THEN
            RAISE EXCEPTION 'derek_research_observations holds research '
                            'records; 170 is not rolled back over them';
        END IF;
    END IF;
    IF to_regclass('derek_research_model_runs') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_research_model_runs) THEN
            RAISE EXCEPTION 'derek_research_model_runs holds run records; '
                            '170 is not rolled back over them';
        END IF;
    END IF;
    IF to_regclass('derek_research_model_attempts') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_research_model_attempts) THEN
            RAISE EXCEPTION 'derek_research_model_attempts holds attempt '
                            'records; 170 is not rolled back over them';
        END IF;
    END IF;
    DROP TABLE IF EXISTS derek_research_model_attempts;
    DROP TABLE IF EXISTS derek_research_observations;
    DROP TABLE IF EXISTS derek_research_model_runs;
    DROP FUNCTION IF EXISTS derek_research_record_is_append_only();
END $$;
