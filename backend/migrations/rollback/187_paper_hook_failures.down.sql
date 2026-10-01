-- down for 187. Refuses while any failure row exists (they are an audit
-- trail of decisions that did not happen).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM paper_hook_failures) THEN
        RAISE EXCEPTION 'paper_hook_failures has rows; 187 is not rolled '
                        'back over them';
    END IF;
    DROP TABLE IF EXISTS paper_hook_failures;
END $$;
