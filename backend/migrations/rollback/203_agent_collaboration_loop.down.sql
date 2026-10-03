-- Refuses while any finding exists: the collaboration loop's stages are the
-- audit trail of who proposed, challenged and evaluated what, and are never
-- dropped as cleanup. With none, drops both tables and their functions.
DO $$
BEGIN
    IF to_regclass('agent_findings') IS NULL THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM agent_findings) THEN
        RAISE EXCEPTION 'agent_findings holds records; rollback refused';
    END IF;
END $$;
-- each table's triggers are dropped with it
DROP TABLE IF EXISTS agent_finding_stages;
DROP TABLE IF EXISTS agent_findings;
DROP FUNCTION IF EXISTS agent_finding_stages_guard();
DROP FUNCTION IF EXISTS agent_finding_stages_advance();
DROP FUNCTION IF EXISTS agent_findings_guard();
DROP FUNCTION IF EXISTS agent_loop_refs_grounded(jsonb);
DROP FUNCTION IF EXISTS agent_loop_no_authority(jsonb);
