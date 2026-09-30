-- down for 146. Refuses while any attempt is recorded: the audit is the only
-- evidence that an owner authorization in ingestion_state came through the
-- authenticated writer, and `authorize()` refuses an owner record without its
-- ACCEPTED row -- so dropping the table over rows would both erase the record
-- of who signed what and silently turn every live owner authorization into an
-- unauthenticated one.
DO $$
BEGIN
    IF to_regclass('bettor_funded_owner_authorization_audit') IS NOT NULL
       AND EXISTS (SELECT 1 FROM bettor_funded_owner_authorization_audit) THEN
        RAISE EXCEPTION 'owner-authorization attempts are recorded; 146 is '
                        'not rolled back over them';
    END IF;
END $$;
DROP TABLE IF EXISTS bettor_funded_owner_authorization_audit;
DROP FUNCTION IF EXISTS bettor_owner_auth_audit_is_append_only();
