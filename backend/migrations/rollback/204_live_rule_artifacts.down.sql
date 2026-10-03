-- Refuses while any live rule artifact carries an owner approval record:
-- an approval is part of the management record and is never dropped as
-- cleanup. Otherwise drops the table (the unapproved P5_LIVE_STREAM_BOOK_V1
-- row with it). With the table gone, live_rule_artifacts.
-- approved_live_book_rules returns only the code constant (empty): the
-- actual lane admits no live book rule.
DO $$
BEGIN
    IF to_regclass('live_rule_artifacts') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM live_rule_artifacts
                WHERE owner_approval_actor IS NOT NULL
                   OR status IN ('APPROVED', 'SUPERSEDED')) THEN
        RAISE EXCEPTION 'live_rule_artifacts holds an owner approval '
                        'record; rollback refused';
    END IF;
END $$;
-- the table's guard trigger is dropped with it
DROP TABLE IF EXISTS live_rule_artifacts;
DROP FUNCTION IF EXISTS live_rule_artifacts_guard();
