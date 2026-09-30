-- down for 151. Refuses while any observation was recorded without admission:
-- dropping the column would make those rows read as admitted.
DO $$
BEGIN
    IF to_regclass('bettor_pair_observations') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'bettor_pair_observations'
                      AND column_name = 'admission_status') THEN
            IF EXISTS (SELECT 1 FROM bettor_pair_observations
                        WHERE admission_status <> 'ADMITTED_BY_DISCOVERY') THEN
                RAISE EXCEPTION 'observations recorded without admission exist; '
                                '151 is not rolled back over them';
            END IF;
        END IF;
        ALTER TABLE bettor_pair_observations
            DROP CONSTRAINT IF EXISTS bettor_pair_obs_admission_ck;
        ALTER TABLE bettor_pair_observations
            DROP COLUMN IF EXISTS cancellation_terms,
            DROP COLUMN IF EXISTS unresolved,
            DROP COLUMN IF EXISTS admission_status;
    END IF;
END $$;
