-- Narrow the provenance enumeration back to three origins.
--
-- THIS DISCARDS NO DATA AND MAY REFUSE TO APPLY. The constraint is NOT
-- VALID, so existing UNCALIBRATED_RESEARCH_SHADOW rows are not re-checked
-- and survive the rollback -- carrying a value the narrowed constraint no
-- longer admits. That is deliberate: dropping those positions to satisfy a
-- constraint would delete the only record of what the research lane did.
--
-- So after this rollback, a row written by the research lane can still be
-- READ but a new one cannot be INSERTED. If the intent is to remove the
-- capability, disable the control row (`research_shadow_uncalibrated`)
-- instead -- that stops new positions without touching the history.

BEGIN;

ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_provenance_declared;

ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_provenance_declared
    CHECK (provenance IS NULL
           OR provenance IN ('RN1_SIGNAL_DERIVED',
                             'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY',
                             'AUTONOMOUS_ENTRY_EXTERNAL_VALUATION_SHADOW'))
    NOT VALID;

COMMIT;
