-- down for 144. REFUSES while any CALIBRATION_ONLY row exists: dropping
-- `record_purpose` would leave those rows indistinguishable from entry
-- decisions (inadmissible, but carrying a probability and an identity that a
-- reader selecting "the freshest eligible valuation" would take). Such rows
-- must be removed or archived deliberately first; this does not do it for you.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'external_valuations'
                  AND column_name = 'record_purpose')
       AND EXISTS (SELECT 1 FROM external_valuations
                    WHERE record_purpose <> 'ENTRY_DECISION') THEN
        RAISE EXCEPTION 'calibration-only valuations are recorded; 144 is not '
                        'rolled back over them';
    END IF;
END $$;
DROP TRIGGER IF EXISTS rn1x_positions_not_from_calibration_only_trg
    ON rn1x_positions;
DROP FUNCTION IF EXISTS rn1x_positions_not_from_calibration_only();
DROP TRIGGER IF EXISTS external_valuations_purpose_is_fixed_trg
    ON external_valuations;
DROP FUNCTION IF EXISTS external_valuations_purpose_is_fixed();
DROP INDEX IF EXISTS external_valuations_purpose_idx;
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_calibration_evidence_matches;
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_calibration_only_cannot_trade;
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_record_purpose_declared;
ALTER TABLE external_valuations DROP COLUMN IF EXISTS calibration_only_evidence;
ALTER TABLE external_valuations DROP COLUMN IF EXISTS record_purpose;
