-- down for 188. The owner-decision version and its activation are the audit
-- trail of what the paper agent ran under, and both tables are append-only,
-- so this file never deletes them. To run the 5.0 pp threshold again, use
-- the audited rollback route (paper_learning.rollback: named operator,
-- reason), which restores V1 as the active version. Refused here.
DO $$
BEGIN
    RAISE EXCEPTION '188 is not rolled back by deleting the owner decision; '
                    'use the audited parameter rollback to restore V1';
END $$;
