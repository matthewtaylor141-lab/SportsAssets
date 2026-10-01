-- down for 185. Refuses while any learning record exists: a decision written
-- with its provenance, a lesson, or a proposal (they are the paper agents'
-- audit trail and memory). Otherwise it drops the proposal tables and their
-- guard, the lessons table, the findings index, the provenance column and
-- the (disabled) activation control row.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM paper_decisions WHERE provenance IS NOT NULL)
       OR EXISTS (SELECT 1 FROM paper_agent_lessons)
       OR EXISTS (SELECT 1 FROM paper_improvement_proposals) THEN
        RAISE EXCEPTION 'paper learning records exist (decision provenance, '
                        'lessons or proposals); 185 is not rolled back over '
                        'them';
    END IF;
    DROP TABLE IF EXISTS paper_improvement_proposal_events;
    DROP TABLE IF EXISTS paper_improvement_proposals;
    DROP FUNCTION IF EXISTS paper_improvement_proposals_guard();
    DROP TABLE IF EXISTS paper_agent_lessons;
    DROP INDEX IF EXISTS paper_audrey_findings_kind_subject_idx;
    ALTER TABLE paper_decisions DROP COLUMN IF EXISTS provenance;
    DELETE FROM paper_control
     WHERE control_key = 'PAPER_LEARNING_PROPOSAL_ACTIVATION';
END $$;
