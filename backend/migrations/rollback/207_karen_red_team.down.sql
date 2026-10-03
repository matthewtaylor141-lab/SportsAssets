-- Refuses while Karen has left any record: her challenges, her runs and her
-- loop challenges are part of the audit trail and are never dropped as
-- cleanup. With none, removes her tables, the no-authority triggers and her
-- identity rows, and restores the three-agent CHECKs.
DO $$
DECLARE
    present boolean;
BEGIN
    -- each probe through EXECUTE: a table this database lacks is skipped,
    -- never planned (the rollback is idempotent)
    IF to_regclass('karen_challenges') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM karen_challenges)'
            INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'karen_challenges holds records; rollback refused';
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM agent_runs WHERE agent_id = 'KAREN') THEN
        RAISE EXCEPTION 'agent_runs holds Karen runs; rollback refused';
    END IF;
    IF to_regclass('agent_finding_stages') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_finding_stages '
                ' WHERE actor = ''KAREN'')' INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'agent_finding_stages holds Karen challenges; '
                            'rollback refused';
        END IF;
    END IF;
    IF to_regclass('agent_slack_delivery') IS NOT NULL THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM agent_slack_delivery '
                ' WHERE agent = ''karen'')' INTO STRICT present;
        IF present THEN
            RAISE EXCEPTION 'agent_slack_delivery holds Karen deliveries; '
                            'rollback refused';
        END IF;
    END IF;
END $$;

DO $$
DECLARE
    r record;
BEGIN
    IF to_regprocedure('karen_authority_guarded_tables()') IS NOT NULL THEN
        FOR r IN SELECT * FROM karen_authority_guarded_tables() LOOP
            IF to_regclass(r.tbl) IS NOT NULL THEN
                EXECUTE format('DROP TRIGGER IF EXISTS karen_no_authority_trg '
                               'ON %I', r.tbl);
            END IF;
        END LOOP;
    END IF;
END $$;
DROP TRIGGER IF EXISTS karen_task_events_no_authority_trg ON agent_task_events;

-- each table's triggers are dropped with it
DROP TABLE IF EXISTS karen_challenge_events;
DROP TABLE IF EXISTS karen_challenges;
DROP FUNCTION IF EXISTS karen_challenges_guard();
DROP FUNCTION IF EXISTS karen_challenge_events_append_only();
DROP FUNCTION IF EXISTS karen_refuses_authority();
DROP FUNCTION IF EXISTS karen_task_events_no_authority();
DROP FUNCTION IF EXISTS karen_authority_guarded_tables();
DROP FUNCTION IF EXISTS karen_is_actor(text);
DROP FUNCTION IF EXISTS karen_refs_grounded(jsonb);
DROP FUNCTION IF EXISTS karen_no_authority(jsonb);

DELETE FROM agent_status WHERE agent_id = 'KAREN';
DELETE FROM agent_identities WHERE agent_id = 'KAREN';
ALTER TABLE agent_identities
    DROP CONSTRAINT IF EXISTS agent_identities_agent_id_check;
ALTER TABLE agent_identities
    ADD CONSTRAINT agent_identities_agent_id_check
    CHECK (agent_id IN ('DEREK', 'XAVIER', 'AUDREY'));

DO $$
BEGIN
    IF to_regclass('agent_finding_stages') IS NOT NULL THEN
        ALTER TABLE agent_finding_stages
            DROP CONSTRAINT IF EXISTS agent_finding_stages_actor_ck;
        ALTER TABLE agent_finding_stages
            ADD CONSTRAINT agent_finding_stages_actor_ck
            CHECK (actor IN ('DEREK', 'XAVIER', 'AUDREY'));
    END IF;
    IF to_regclass('agent_slack_delivery') IS NOT NULL THEN
        ALTER TABLE agent_slack_delivery
            DROP CONSTRAINT IF EXISTS agent_slack_delivery_agent_check;
        ALTER TABLE agent_slack_delivery
            ADD CONSTRAINT agent_slack_delivery_agent_check
            CHECK (agent IN ('derek', 'xavier', 'audrey'));
    END IF;
END $$;
