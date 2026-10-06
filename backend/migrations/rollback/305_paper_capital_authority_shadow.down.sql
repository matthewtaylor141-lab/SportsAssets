-- Rollback of 305. REFUSES while any shadow counterfactual, outcome or
-- refusal-census row exists: they are forward evidence and part of the audit
-- trail, never dropped as cleanup. With none, removes the tables, their
-- triggers and the function.
DO $$
DECLARE
    present boolean := false;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_shadow_counterfactual_outcomes',
                             'paper_shadow_counterfactuals',
                             'paper_entry_refusal_census'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
              INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
END $$;
DROP TABLE IF EXISTS paper_shadow_counterfactual_outcomes;
DROP TABLE IF EXISTS paper_shadow_counterfactuals;
DROP TABLE IF EXISTS paper_entry_refusal_census;
DROP FUNCTION IF EXISTS paper_capital_authority_is_append_only();
