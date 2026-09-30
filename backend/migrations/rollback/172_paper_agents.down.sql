-- down for 172. Refuses while any paper decision, review or report exists:
-- they are the paper session's audit trail.
DO $$
BEGIN
    IF to_regclass('paper_decisions') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM paper_decisions) THEN
            RAISE EXCEPTION 'paper_decisions holds records; 172 is not '
                            'rolled back over them';
        END IF;
    END IF;
    IF to_regclass('paper_xavier_reviews') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM paper_xavier_reviews) THEN
            RAISE EXCEPTION 'paper_xavier_reviews holds records; 172 is not '
                            'rolled back over them';
        END IF;
    END IF;
    IF to_regclass('paper_audrey_reports') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM paper_audrey_reports) THEN
            RAISE EXCEPTION 'paper_audrey_reports holds records; 172 is not '
                            'rolled back over them';
        END IF;
    END IF;
    DROP TABLE IF EXISTS paper_audrey_findings;
    DROP TABLE IF EXISTS paper_audrey_reports;
    DROP TABLE IF EXISTS paper_equity_snapshots;
    DROP TABLE IF EXISTS paper_xavier_reviews;
    DROP TABLE IF EXISTS paper_handoffs;
    DROP TABLE IF EXISTS paper_decisions;
END $$;
