-- Rollback of 302 (ARCHER across 301's objects).
--
-- REFUSES while any of 301's records names ARCHER (a work request or its
-- collaborator, a lesson retrieval or supersession, a cluster or a cluster
-- event): they are audit records and are never dropped as cleanup. With
-- none, removes the alias guard from 301's tables (agent_work_requests
-- keeps 266's agent_id guard), and restores each CHECK and the
-- machine-actor function to 301's definition. Nothing written as 'EDDIE'
-- is touched.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM agent_work_requests
                WHERE 'ARCHER' IN (agent_id, coalesce(collaborator, '')))
       OR EXISTS (SELECT 1 FROM agent_lesson_retrievals
                   WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM agent_lesson_supersessions
                   WHERE agent_id = 'ARCHER')
       OR EXISTS (SELECT 1 FROM improvement_clusters
                   WHERE 'ARCHER' IN (owner_agent,
                                      coalesce(target_agent, '')))
       OR EXISTS (SELECT 1 FROM improvement_cluster_events
                   WHERE owner_agent = 'ARCHER') THEN
        RAISE EXCEPTION 'ARCHER has left records in 301''s tables: '
                        'rollback refused';
    END IF;
END $$;

DROP TRIGGER IF EXISTS agent_historical_alias_trg ON agent_lesson_retrievals;
DROP TRIGGER IF EXISTS agent_historical_alias_trg ON agent_lesson_supersessions;
DROP TRIGGER IF EXISTS agent_historical_alias_trg ON improvement_clusters;
DROP TRIGGER IF EXISTS agent_historical_alias_trg ON improvement_cluster_events;
DROP TRIGGER IF EXISTS agent_historical_alias_trg ON agent_work_requests;
CREATE TRIGGER agent_historical_alias_trg BEFORE INSERT ON agent_work_requests
    FOR EACH ROW EXECUTE FUNCTION agent_historical_alias_guard('agent_id');

ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS agent_work_requests_owner_kind_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_owner_kind_ck CHECK (
        (kind IN ('PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                  'MANAGEMENT_REASSESSMENT')
         AND position_kind IN ('PAPER', 'ACTUAL'))
        OR (kind = 'CANDIDATE_FRESH_EVIDENCE' AND agent_id = 'DEREK'
            AND position_kind = 'MARKET')
        OR (kind = 'CHALLENGE_RESPONSE' AND agent_id IN (
                'DEREK', 'XAVIER', 'AUDREY', 'CHIEF_ALLOCATOR')
            AND position_kind = 'CHALLENGE')
        OR (kind = 'CHALLENGE_EVALUATION' AND agent_id IN ('AUDREY', 'XAVIER')
            AND position_kind = 'CHALLENGE')
        OR (kind = 'CHALLENGE_INVESTIGATION' AND agent_id = 'KAREN'
            AND position_kind = 'RECORD')
        OR (kind = 'ALLOCATION_REVIEW' AND agent_id = 'CHIEF_ALLOCATOR'
            AND position_kind = 'DECISION')
        OR (kind = 'EXECUTION_ESTIMATE' AND agent_id = 'EDDIE'
            AND position_kind = 'DECISION')
        OR (kind = 'OUTCOME_CALIBRATION' AND agent_id = 'EDDIE'
            AND position_kind = 'ESTIMATE')
        OR (kind = 'RESEARCH_QUESTION' AND agent_id = 'SCOUT'
            AND position_kind = 'FEATURE')
        OR (kind = 'AUDIT_RECONCILIATION' AND agent_id = 'AUDREY'
            AND position_kind = 'RECONCILIATION')
        OR (kind = 'ROOT_CAUSE_TRIAGE' AND agent_id = 'AUDREY'
            AND position_kind = 'CLUSTER')
        OR (kind = 'AUDIT_FINDING_FOLLOWUP' AND agent_id = 'AUDREY'
            AND position_kind = 'RECORD')) NOT VALID;

ALTER TABLE agent_work_requests DROP CONSTRAINT IF EXISTS agent_work_requests_collaborator_ck;
ALTER TABLE agent_work_requests ADD CONSTRAINT agent_work_requests_collaborator_ck CHECK (
        collaborator IS NULL OR (collaborator IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT') AND collaborator <> agent_id)) NOT VALID;

ALTER TABLE agent_lesson_retrievals DROP CONSTRAINT IF EXISTS agent_lesson_retrievals_agent_ck;
ALTER TABLE agent_lesson_retrievals ADD CONSTRAINT agent_lesson_retrievals_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT'));

ALTER TABLE agent_lesson_retrievals DROP CONSTRAINT IF EXISTS agent_lesson_retrievals_source_ck;
ALTER TABLE agent_lesson_retrievals ADD CONSTRAINT agent_lesson_retrievals_source_ck CHECK (
        (decision_table = 'paper_decisions' AND agent_id = 'DEREK')
        OR (decision_table = 'paper_xavier_reviews' AND agent_id = 'XAVIER')
        OR (decision_table = 'eddie_execution_estimates'
            AND agent_id = 'EDDIE')
        OR (decision_table = 'intel_allocations'
            AND agent_id = 'CHIEF_ALLOCATOR')
        OR (decision_table = 'karen_challenges' AND agent_id = 'KAREN')
        OR (decision_table = 'paper_audrey_findings' AND agent_id = 'AUDREY')
        OR (decision_table = 'scout_features' AND agent_id = 'SCOUT'));

ALTER TABLE agent_lesson_supersessions DROP CONSTRAINT IF EXISTS agent_lesson_supersessions_agent_ck;
ALTER TABLE agent_lesson_supersessions ADD CONSTRAINT agent_lesson_supersessions_agent_ck CHECK (agent_id IN (
        'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
        'SCOUT'));

ALTER TABLE improvement_clusters DROP CONSTRAINT IF EXISTS improvement_clusters_agents_ck;
ALTER TABLE improvement_clusters ADD CONSTRAINT improvement_clusters_agents_ck CHECK (
        owner_agent IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN',
                        'CHIEF_ALLOCATOR', 'EDDIE', 'SCOUT')
        AND (target_agent IS NULL OR target_agent IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT')));

ALTER TABLE improvement_cluster_events DROP CONSTRAINT IF EXISTS improvement_cluster_events_owner_ck;
ALTER TABLE improvement_cluster_events ADD CONSTRAINT improvement_cluster_events_owner_ck CHECK (
        (kind <> 'OWNER_ASSIGNED' OR owner_agent IS NOT NULL)
        AND (owner_agent IS NULL OR owner_agent IN (
            'DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'CHIEF_ALLOCATOR', 'EDDIE',
            'SCOUT')));

CREATE OR REPLACE FUNCTION agent_ops_is_machine_actor(v text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT v IS NULL
        OR btrim(v) = ''
        OR upper(btrim(v)) IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN', 'EDDIE',
                               'SCOUT', 'ALLOCATOR', 'CHIEF_ALLOCATOR',
                               'ADRIANA',
                               'CALIBRATION_ENGINE', 'MODEL_TOURNAMENT',
                               'CLAUDE', 'SYSTEM', 'RUNNER', 'MIGRATION',
                               'ROOT', 'POSTGRES', 'BOT', 'AGENT', 'CI',
                               'GITHUB', 'GITHUB_ACTIONS', 'RENDER', 'NETLIFY',
                               'DEPENDABOT', 'IMPROVEMENT_PIPELINE',
                               'POS_LEARN_RUNNER', 'POS_WORKFLOW', 'UNKNOWN',
                               'ANONYMOUS', 'SERVICE', 'AUTOMATION', 'CRON')
        OR upper(btrim(v)) ~ '^(AGENT|AGENTS|BOT|SLACK|SYSTEM|ROLE|CLAUDE|MIGRATION|POS_LEARN|POSLEARN|RUNNER|INTEL|AUTOMATION|SERVICE|IMPROVEMENT|PIPELINE|GITHUB|CI|CRON|WORKER|DEPLOY)([:/ ._-]|$)'
        OR upper(btrim(v)) ~ '(DEREK|XAVIER|AUDREY|KAREN|EDDIE|SCOUT|ALLOCATOR|ADRIANA)[ ._:-]*(AGENT|BOT|V[0-9]|CHALLENGER|RED[ ._-]*TEAM|EXECUTION|RESEARCH|INTEL|ARB|ARBITRAGE)'
        OR lower(btrim(v)) ~ '\[bot\]'
        OR lower(btrim(v)) ~ '(^|[^a-z])(bot|runner|daemon|scheduler)$'
$$;
