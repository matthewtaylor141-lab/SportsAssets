-- down for 139. Refuses while any attested evidence row exists: dropping the
-- kind would leave rows the restored CHECK cannot describe, and those rows are
-- the record of why a reservation was released.
DO $$
BEGIN
    IF to_regclass('bettor_funded_operation_evidence') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_funded_operation_evidence
                    WHERE kind = 'OPERATOR_ATTESTED_NO_EXPOSURE') THEN
        RAISE EXCEPTION 'operator attestations are recorded; 139 is not rolled '
                        'back over them';
    END IF;
    IF to_regclass('bettor_funded_operation_evidence') IS NOT NULL THEN
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_attested_ck;
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_kind_ck;
        ALTER TABLE bettor_funded_operation_evidence
            ADD CONSTRAINT bettor_funded_evidence_kind_ck CHECK (
                kind IN ('VENUE_NAMED_THE_ORDER', 'VENUE_HAS_NO_SUCH_ORDER',
                         'READ_ESTABLISHED_NOTHING'));
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_funded_resolution_audit;
DROP TABLE IF EXISTS bettor_funded_investigations;
DROP FUNCTION IF EXISTS bettor_funded_resolution_audit_is_append_only();
DROP FUNCTION IF EXISTS bettor_funded_investigation_is_not_deleted();
DROP FUNCTION IF EXISTS bettor_funded_investigation_resolution_is_final();
