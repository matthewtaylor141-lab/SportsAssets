-- down for 153. Refuses while any Derek record exists: they are decision
-- records (and the internal entry model's training vectors), and dropping
-- them would erase what was decided and why.
DO $$
BEGIN
    IF to_regclass('derek_entry_decisions') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_entry_decisions) THEN
            RAISE EXCEPTION 'derek_entry_decisions holds decision records; '
                            '153 is not rolled back over them';
        END IF;
    END IF;
    IF to_regclass('derek_coverage_census') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM derek_coverage_census) THEN
            RAISE EXCEPTION 'derek_coverage_census holds census records; '
                            '153 is not rolled back over them';
        END IF;
    END IF;
    DROP TABLE IF EXISTS derek_entry_decisions;
    DROP TABLE IF EXISTS derek_coverage_census;
    DROP FUNCTION IF EXISTS derek_record_is_append_only();
END $$;
