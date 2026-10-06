-- Rollback of 309. REFUSES while any evaluation or CASH decision row exists:
-- they are the audit trail of PAPER entry decisions, never dropped as
-- cleanup. Learned-model rows alone are re-fittable and do not block.
DO $$
DECLARE
    present boolean := false;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_profitability_evaluations',
                             'paper_cash_decisions'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
              INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
END $$;
DROP TABLE IF EXISTS paper_profitability_evaluations;
DROP TABLE IF EXISTS paper_cash_decisions;
DROP TABLE IF EXISTS paper_profitability_models;
DROP FUNCTION IF EXISTS paper_profitability_bind_is_append_only();
