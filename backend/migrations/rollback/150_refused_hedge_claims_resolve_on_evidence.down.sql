-- down for 150. Refuses while any explicit-refusal evidence is recorded: a
-- claim released on it would lose the record of why it was released.
DO $$
BEGIN
    IF to_regclass('bettor_funded_operation_evidence') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM bettor_funded_operation_evidence
                    WHERE kind = 'VENUE_REFUSED_THE_ORDER_IN_ITS_ANSWER') THEN
            RAISE EXCEPTION 'explicit-refusal evidence is recorded; 150 is not '
                            'rolled back over it';
        END IF;
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_refused_ck;
        ALTER TABLE bettor_funded_operation_evidence
            DROP CONSTRAINT IF EXISTS bettor_funded_evidence_kind_ck;
        ALTER TABLE bettor_funded_operation_evidence
            ADD CONSTRAINT bettor_funded_evidence_kind_ck CHECK (
                kind IN ('VENUE_NAMED_THE_ORDER', 'VENUE_HAS_NO_SUCH_ORDER',
                         'READ_ESTABLISHED_NOTHING',
                         'OPERATOR_ATTESTED_NO_EXPOSURE',
                         'OPERATOR_NAMED_THE_ORDER'));
    END IF;
END $$;
