-- Refuses while Eddie or Scout has left any record: their estimates,
-- outcomes, features, observations, tournaments, workflow steps, runs, loop
-- stages, persona versions, conversations and Slack deliveries are part of
-- the audit trail and are never dropped as cleanup. With none, removes their
-- tables, the no-authority triggers and their identity rows, and restores
-- the four-agent CHECKs of 207 / 212.
DO $$
DECLARE
    present boolean;
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['eddie_execution_estimates',
                             'eddie_execution_outcomes', 'scout_features',
                             'scout_feature_observations',
                             'scout_feature_tournaments',
                             'scout_tournament_samples',
                             'pos_candidate_reviews',
                             'pos_candidate_review_steps'] LOOP
        IF to_regclass(t) IS NOT NULL THEN
            EXECUTE format('SELECT EXISTS (SELECT 1 FROM %I)', t)
                INTO STRICT present;
            IF present THEN
                RAISE EXCEPTION '% holds records; rollback refused', t;
            END IF;
        END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM agent_runs WHERE agent_id IN ('EDDIE', 'SCOUT'))
    THEN
        RAISE EXCEPTION 'agent_runs holds Eddie / Scout runs; rollback refused';
    END IF;
    IF EXISTS (SELECT 1 FROM agent_tasks WHERE assignee IN ('EDDIE', 'SCOUT'))
    THEN
        RAISE EXCEPTION 'agent_tasks holds Eddie / Scout tasks; rollback '
                        'refused';
    END IF;
    IF to_regclass('agent_findings') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_findings WHERE proposer '
                ' IN (''EDDIE'', ''SCOUT'')) OR EXISTS (SELECT 1 FROM '
                ' agent_finding_stages WHERE actor IN (''EDDIE'', ''SCOUT''))'
            INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'the collaboration loop holds Eddie / Scout '
                            'records; rollback refused';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM agent_persona_versions
                WHERE agent_id IN ('EDDIE', 'SCOUT'))
       OR EXISTS (SELECT 1 FROM agent_chat_conversations
                   WHERE agent_id IN ('EDDIE', 'SCOUT')) THEN
        RAISE EXCEPTION 'Eddie / Scout persona records exist; rollback '
                        'refused';
    END IF;
    IF to_regclass('agent_slack_delivery') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_slack_delivery WHERE '
                ' agent IN (''eddie'', ''scout''))' INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'agent_slack_delivery holds Eddie / Scout '
                            'deliveries; rollback refused';
        END IF;
    END IF;
END $$;

DO $$
DECLARE
    r record;
BEGIN
    IF to_regprocedure('pos_agents_authority_guarded_tables()') IS NOT NULL
    THEN
        FOR r IN SELECT * FROM pos_agents_authority_guarded_tables() LOOP
            IF to_regclass(r.tbl) IS NOT NULL THEN
                EXECUTE format('DROP TRIGGER IF EXISTS '
                               'aa_pos_agents_no_authority_trg ON %I', r.tbl);
            END IF;
        END LOOP;
    END IF;
END $$;
-- and wherever ANY later migration attached the same guard (R30's 225 adds
-- its order-path tables): every trigger bound to the guard function, found
-- in the catalogue rather than in a list that a re-run of this migration's
-- UP could shorten
DO $$
DECLARE
    r record;
BEGIN
    IF to_regprocedure('pos_agents_refuse_authority()') IS NOT NULL THEN
        FOR r IN SELECT t.tgname, c.relname FROM pg_trigger t
                   JOIN pg_class c ON c.oid = t.tgrelid
                  WHERE t.tgfoid = 'pos_agents_refuse_authority()'::regprocedure
                    AND NOT t.tgisinternal LOOP
            EXECUTE format('DROP TRIGGER IF EXISTS %I ON %I', r.tgname, r.relname);
        END LOOP;
    END IF;
END $$;
DROP TRIGGER IF EXISTS pos_task_events_no_authority_trg ON agent_task_events;

DROP VIEW IF EXISTS pos_iface_scout_feature_effects;
DROP VIEW IF EXISTS pos_iface_eddie_execution;
-- each table's triggers are dropped with it
DROP TABLE IF EXISTS pos_candidate_review_steps;
DROP TABLE IF EXISTS pos_candidate_reviews;
DROP TABLE IF EXISTS scout_tournament_samples;
DROP TABLE IF EXISTS scout_feature_tournaments;
DROP TABLE IF EXISTS scout_feature_observations;
DROP TABLE IF EXISTS scout_features;
DROP TABLE IF EXISTS scout_sources;
DROP TABLE IF EXISTS eddie_execution_outcomes;
DROP TABLE IF EXISTS eddie_execution_estimates;
DROP FUNCTION IF EXISTS pos_steps_guard();
DROP FUNCTION IF EXISTS pos_reviews_guard();
DROP FUNCTION IF EXISTS scout_samples_guard();
DROP FUNCTION IF EXISTS scout_tournaments_guard();
DROP FUNCTION IF EXISTS scout_features_guard();
DROP FUNCTION IF EXISTS scout_compliant_source_guard();
DROP FUNCTION IF EXISTS pos_append_only();
DROP FUNCTION IF EXISTS pos_agents_refuse_authority();
DROP FUNCTION IF EXISTS pos_task_events_no_authority();
DROP FUNCTION IF EXISTS pos_agents_authority_guarded_tables();
DROP FUNCTION IF EXISTS pos_compliance_all_true(jsonb);
DROP FUNCTION IF EXISTS pos_no_authority(jsonb);
DROP FUNCTION IF EXISTS pos_refs_grounded(jsonb);
DROP FUNCTION IF EXISTS pos_session_agent();
DROP FUNCTION IF EXISTS pos_agent_actor(text);

DELETE FROM agent_status WHERE agent_id IN ('EDDIE', 'SCOUT');
DELETE FROM agent_identities WHERE agent_id IN ('EDDIE', 'SCOUT');
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));
ALTER TABLE agent_persona_versions
    DROP CONSTRAINT IF EXISTS agent_persona_versions_agent_id_check;
ALTER TABLE agent_persona_versions
    ADD CONSTRAINT agent_persona_versions_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));
ALTER TABLE agent_chat_conversations
    DROP CONSTRAINT IF EXISTS agent_chat_conversations_agent_id_check;
ALTER TABLE agent_chat_conversations
    ADD CONSTRAINT agent_chat_conversations_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY', 'KAREN'));

DO $$
BEGIN
    IF to_regclass('agent_findings') IS NOT NULL THEN
        ALTER TABLE agent_findings
            DROP CONSTRAINT IF EXISTS agent_findings_proposer_ck;
        ALTER TABLE agent_findings
            ADD CONSTRAINT agent_findings_proposer_ck
            CHECK (proposer IN ('DEREK', 'XAVIER', 'AUDREY'));
        ALTER TABLE agent_finding_stages
            DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
        ALTER TABLE agent_finding_stages
            ADD CONSTRAINT agent_finding_stages_actor_ck CHECK (
                actor IN ('DEREK', 'XAVIER', 'AUDREY')
                OR (actor = 'KAREN' AND stage = 'PEER_CHALLENGE'));
    END IF;
    IF to_regclass('agent_slack_delivery') IS NOT NULL THEN
        ALTER TABLE agent_slack_delivery
            DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
        ALTER TABLE agent_slack_delivery
            ADD CONSTRAINT agent_slack_delivery_agent_check
            CHECK (agent IN ('derek', 'xavier', 'audrey', 'karen'));
    END IF;
END $$;
