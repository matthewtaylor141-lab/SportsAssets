-- Rollback of 310. REFUSES while any capital-readiness evidence row exists:
-- the lab's history is append-only RESEARCH evidence, never dropped as
-- cleanup. An empty lab drops cleanly (tables first, then the function).
DO $$
DECLARE
    present boolean := false;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['capital_readiness_runs',
                             'capital_readiness_shadow_court',
                             'capital_readiness_agent_economics',
                             'capital_readiness_scale_trials'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
              INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
END $$;
DROP TABLE IF EXISTS capital_readiness_scale_trials;
DROP TABLE IF EXISTS capital_readiness_agent_economics;
DROP TABLE IF EXISTS capital_readiness_shadow_court;
DROP TABLE IF EXISTS capital_readiness_runs;
DROP FUNCTION IF EXISTS capital_readiness_append_only();
