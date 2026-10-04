-- Rollback of 226 (the agents' fresh-evidence work queue and the one
-- current review per position). Refuses while any work request exists:
-- the requests and their lifecycle are the record of what each agent asked
-- for and what evidence it got, and are never dropped as cleanup. The
-- current-review pointer is DERIVED (each position's newest review, which
-- the append-only review tables keep) and does not block it. Touches only
-- 226's objects; paper_xavier_reviews and xavier_management_assessments
-- lose only the two pointer triggers.
DO $$
BEGIN
    IF to_regclass('agent_work_requests') IS NOT NULL
       AND EXISTS (SELECT 1 FROM agent_work_requests) THEN
        RAISE EXCEPTION 'agent_work_requests holds recorded requests; '
                        'rollback refused';
    END IF;
END $$;
DO $$
BEGIN
    IF to_regclass('paper_xavier_reviews') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS paper_xavier_reviews_current_trg
            ON paper_xavier_reviews;
    END IF;
    IF to_regclass('xavier_management_assessments') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS xavier_management_assessments_current_trg
            ON xavier_management_assessments;
    END IF;
END $$;
DROP TABLE IF EXISTS xavier_current_review;
DROP TABLE IF EXISTS agent_work_open;
DROP TABLE IF EXISTS agent_work_request_events;
DROP TABLE IF EXISTS agent_work_requests;
DROP FUNCTION IF EXISTS xavier_current_review_advance_assessment();
DROP FUNCTION IF EXISTS xavier_current_review_advance_paper();
DROP FUNCTION IF EXISTS xavier_current_review_guard();
DROP FUNCTION IF EXISTS agent_work_event_close();
DROP FUNCTION IF EXISTS agent_work_open_guard();
DROP FUNCTION IF EXISTS agent_work_event_guard();
DROP FUNCTION IF EXISTS agent_work_record_is_append_only();
