-- Rollback of 311. REFUSES while any counterfactual variant or outcome row
-- exists, or any MANAGEMENT model row (the 309 kind constraint could not be
-- restored over it): they are PAPER learning records, never dropped as
-- cleanup.
DO $$
DECLARE
    present boolean := false;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_counterfactual_variant_outcomes',
                             'paper_counterfactual_variants'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
              INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
    IF to_regclass('paper_profitability_models') IS NOT NULL THEN
        SELECT EXISTS (SELECT 1 FROM paper_profitability_models
                        WHERE kind = 'MANAGEMENT') INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'paper_profitability_models holds MANAGEMENT '
                            'models; rollback refused';
        END IF;
    END IF;
END $$;
DROP TABLE IF EXISTS paper_counterfactual_variant_outcomes;
DROP TABLE IF EXISTS paper_counterfactual_variants;
ALTER TABLE IF EXISTS paper_profitability_models
    DROP CONSTRAINT IF EXISTS paper_prof_models_kind_ck;
ALTER TABLE IF EXISTS paper_profitability_models
    ADD CONSTRAINT paper_prof_models_kind_ck CHECK (kind IN (
        'CALIBRATION', 'EXECUTION', 'RESIDUAL'));
