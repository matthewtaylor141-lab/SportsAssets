-- down for 156. Refuses while any directive or management message is
-- recorded: they are the record of what management asked, what Audrey
-- answered from which records, and which work was assigned because of it.
DO $$
BEGIN
    IF to_regclass('management_directives') IS NOT NULL
       AND EXISTS (SELECT 1 FROM management_directives) THEN
        RAISE EXCEPTION 'management directives are recorded; 156 is not '
                        'rolled back over them';
    END IF;
    IF to_regclass('audrey_messages') IS NOT NULL
       AND EXISTS (SELECT 1 FROM audrey_messages) THEN
        RAISE EXCEPTION 'audrey conversations are recorded; 156 is not '
                        'rolled back over them';
    END IF;
END $$;
DROP TABLE IF EXISTS audrey_requests;
DROP TABLE IF EXISTS management_directive_events;
DROP TABLE IF EXISTS management_directives;
DROP TABLE IF EXISTS audrey_messages;
DROP TABLE IF EXISTS audrey_conversations;
DROP FUNCTION IF EXISTS management_directive_event_is_a_record();
DROP FUNCTION IF EXISTS management_directive_is_kept();
DROP FUNCTION IF EXISTS audrey_message_is_a_record();
