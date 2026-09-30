-- down for 155. WHAT IS LOST: every Audrey daily report and its versions,
-- the audit watermark, the collector throughput samples, and the whole
-- improvement record (candidates, trials, holdout budgets, releases,
-- events). The released policy versions themselves live in
-- agent_policy_versions (152) and are NOT touched: roll a release back
-- (improvement.rollback) BEFORE dropping this, or the policy row stays
-- ACTIVE with no release record explaining it.
--
-- Refuses while a release is live (CANARY or ACTIVE).
BEGIN;
DO $$
BEGIN
    IF to_regclass('improvement_releases') IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM improvement_releases
                    WHERE state IN ('CANARY', 'ACTIVE')) THEN
            RAISE EXCEPTION 'a released improvement is live; roll it back '
                            'before rolling back 155';
        END IF;
    END IF;
END $$;
DROP TABLE IF EXISTS improvement_events;
DROP TABLE IF EXISTS improvement_releases;
DROP TABLE IF EXISTS improvement_trials;
DROP TABLE IF EXISTS improvement_candidates;
DROP TABLE IF EXISTS improvement_holdouts;
DROP TABLE IF EXISTS audrey_collection_samples;
DROP TABLE IF EXISTS audrey_audit_watermarks;
DROP TABLE IF EXISTS audrey_audit_reports;
DROP FUNCTION IF EXISTS improvement_trial_within_budget();
DROP FUNCTION IF EXISTS improvement_candidate_guard();
DROP FUNCTION IF EXISTS audrey_is_append_only();
COMMIT;
