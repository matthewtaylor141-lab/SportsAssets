-- Rollback of 243 (the agent citation-integrity verdict ledger). Refuses
-- while any verdict is recorded: the ledger is the record of what each agent
-- published and what the verifier found, and is never dropped as cleanup.
-- Touches only 243's objects.
DO $$
BEGIN
    IF to_regclass('agent_citation_checks') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_citation_checks) THEN
        RAISE EXCEPTION 'agent_citation_checks holds recorded verdicts; '
                        'rollback refused';
    END IF;
END $$;
DROP TABLE IF EXISTS agent_citation_verdicts;
DROP TABLE IF EXISTS agent_citation_checks;
DROP FUNCTION IF EXISTS agent_citation_record_is_append_only();
