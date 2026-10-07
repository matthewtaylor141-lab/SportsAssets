DO $$
BEGIN
 IF EXISTS(SELECT 1 FROM paper_exit_intents LIMIT 1) THEN
   RAISE EXCEPTION 'Refusing rollback 313: paper_exit_intents contains evidence';
 END IF;
END $$;
DROP TABLE IF EXISTS paper_exit_intents;
