-- ROLLBACK 234 · DURABLE WORK QUEUES FOR ALL SEVEN AGENTS, LESSON RETRIEVAL
-- AND SUPERSESSION, ROOT-CAUSE IMPROVEMENT CLUSTERS.
--
-- REFUSED while any record this migration made possible exists: a work item
-- of a 234 kind, an ATTEMPTED event, a lesson retrieval or supersession, a
-- root-cause cluster or its events. Those are the record of what each agent
-- owed, tried and learned, and are never dropped as cleanup. With none,
-- drops cleanly: 226's tables lose only the 234 columns, constraints and
-- triggers (their 226 CHECKs restored), and the 217 registry returns to
-- 225's list.
DO $$
BEGIN
    IF (to_regclass('agent_work_requests') IS NOT NULL AND EXISTS (
            SELECT 1 FROM agent_work_requests WHERE kind NOT IN (
                'PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                'MANAGEMENT_REASSESSMENT')))
       OR (to_regclass('agent_work_request_events') IS NOT NULL AND EXISTS (
            SELECT 1 FROM agent_work_request_events
             WHERE state = 'ATTEMPTED'))
       OR (to_regclass('agent_lesson_retrievals') IS NOT NULL
            AND EXISTS (SELECT 1 FROM agent_lesson_retrievals))
       OR (to_regclass('agent_lesson_supersessions') IS NOT NULL
            AND EXISTS (SELECT 1 FROM agent_lesson_supersessions))
       OR (to_regclass('improvement_clusters') IS NOT NULL
            AND EXISTS (SELECT 1 FROM improvement_clusters)) THEN
        RAISE EXCEPTION 'ROLLBACK_234_REFUSED: agent work, lesson or '
                        'root-cause records exist (audit chain)';
    END IF;
END $$;

DROP TABLE IF EXISTS improvement_cluster_events;
DROP TABLE IF EXISTS improvement_clusters;
DROP TABLE IF EXISTS agent_lesson_supersessions;
DROP TABLE IF EXISTS agent_lesson_retrievals;
DROP FUNCTION IF EXISTS improvement_cluster_events_guard();
DROP FUNCTION IF EXISTS improvement_cluster_record_is_append_only();
DROP FUNCTION IF EXISTS agent_lesson_supersessions_guard();
DROP FUNCTION IF EXISTS agent_lesson_retrievals_guard();
DROP FUNCTION IF EXISTS agent_lesson_exists(text, text, text);
DROP FUNCTION IF EXISTS agent_lesson_record_is_append_only();
DROP FUNCTION IF EXISTS agent_ops_no_authority(jsonb);
DROP FUNCTION IF EXISTS agent_ops_is_machine_actor(text);

DO $$
BEGIN
    IF to_regclass('agent_work_request_events') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS agent_work_events_attempt_bound_trg
            ON agent_work_request_events;
        DROP INDEX IF EXISTS agent_work_events_request_idx;
        ALTER TABLE agent_work_request_events
            DROP CONSTRAINT IF EXISTS agent_work_events_attempt_ck;
        ALTER TABLE agent_work_request_events
            DROP CONSTRAINT IF EXISTS agent_work_events_state_ck;
        ALTER TABLE agent_work_request_events
            ADD CONSTRAINT agent_work_events_state_ck CHECK (state IN (
                'ENQUEUED', 'DISPATCHED', 'COMPLETED', 'FAILED'));
        ALTER TABLE agent_work_request_events DROP COLUMN IF EXISTS outcome;
        ALTER TABLE agent_work_request_events DROP COLUMN IF EXISTS blocker;
        ALTER TABLE agent_work_request_events
            DROP COLUMN IF EXISTS next_attempt_at;
    END IF;
    IF to_regclass('agent_work_requests') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS agent_work_requests_terms_fill_trg
            ON agent_work_requests;
        DROP INDEX IF EXISTS agent_work_requests_agent_kind_idx;
        ALTER TABLE agent_work_requests
            DROP CONSTRAINT IF EXISTS agent_work_requests_owner_kind_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_terms_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_collaborator_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_depends_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_blocker_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_kind_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_position_kind_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_reason_ck,
            DROP CONSTRAINT IF EXISTS agent_work_requests_expiry_ck;
        -- 226's own CHECKs, verbatim
        ALTER TABLE agent_work_requests
            ADD CONSTRAINT agent_work_requests_kind_ck CHECK (kind IN (
                'PROBABILITY', 'VENUE_BOOK', 'GAME_STATE',
                'MANAGEMENT_REASSESSMENT')),
            ADD CONSTRAINT agent_work_requests_position_kind_ck CHECK (
                position_kind IN ('PAPER', 'ACTUAL')),
            ADD CONSTRAINT agent_work_requests_reason_ck CHECK (reason IN (
                'WAITING_FOR_FRESH_EVIDENCE',
                'MANAGEMENT_UNAVAILABLE_STALE_INPUT')),
            ADD CONSTRAINT agent_work_requests_expiry_ck CHECK (
                expires_at > enqueued_at
                AND expires_at <= enqueued_at + interval '1 hour');
        ALTER TABLE agent_work_requests
            DROP COLUMN IF EXISTS due_at,
            DROP COLUMN IF EXISTS blocker,
            DROP COLUMN IF EXISTS depends_on,
            DROP COLUMN IF EXISTS collaborator,
            DROP COLUMN IF EXISTS evidence_needed,
            DROP COLUMN IF EXISTS next_attempt_at;
    END IF;
END $$;
DROP FUNCTION IF EXISTS agent_work_attempt_bound();
DROP FUNCTION IF EXISTS agent_work_requests_terms_fill();

-- the 217 registry as migration 225 declares it (the two 234 tables are
-- gone, so their guard triggers went with them)
CREATE OR REPLACE FUNCTION pos_agents_authority_guarded_tables()
RETURNS TABLE (tbl text, cols text[], kind text) LANGUAGE sql IMMUTABLE AS $$
    VALUES
      ('agent_policy_versions',              ARRAY['created_by', 'approved_by'], 'APPROVAL'),
      ('agent_policy_artifacts',             ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('live_rule_artifacts',                ARRAY['created_by', 'owner_approval_actor'], 'APPROVAL'),
      ('bettor_funded_models',               ARRAY['approved_by'], 'APPROVAL'),
      ('calibration_lifecycles',             ARRAY['approved_by'], 'APPROVAL'),
      ('improvement_candidates',             ARRAY['proposed_by', 'approved_by'], 'PROMOTION'),
      ('improvement_releases',               ARRAY['released_by'], 'PROMOTION'),
      ('paper_improvement_proposals',        ARRAY['proposed_by', 'activated_by'], 'ACTIVATION'),
      ('paper_policy_parameter_activations', ARRAY['actor'], 'ACTIVATION'),
      ('paper_policy_parameter_versions',    ARRAY['approved_by'], 'APPROVAL'),
      ('execmirror_control',                 ARRAY['actor'], 'CONTROL'),
      ('kalshi_smalllive_control',           ARRAY['actor'], 'CONTROL'),
      ('agent_slack_control_audit',          ARRAY['actor'], 'CONTROL'),
      ('paper_control',                      ARRAY[]::text[], 'CONTROL'),
      ('bettor_funded_owner_authorization_audit',
                                             ARRAY['operator', 'authenticated_by'], 'CONTROL'),
      ('management_directive_events',        ARRAY['actor_label'], 'CONTROL'),
      ('derek_entry_decisions',              ARRAY['decided_by'], 'DECISION'),
      -- the order path: orders, intents, fills, their events and plans
      ('paper_orders',                       ARRAY['strategy', 'event_source'], 'ORDER'),
      ('paper_order_events',                 ARRAY['event_source'], 'ORDER'),
      ('paper_fills',                        ARRAY['strategy', 'event_source'], 'ORDER'),
      ('execution_intents',                  ARRAY['strategy'], 'ORDER'),
      ('bettor_funded_intents',              ARRAY[]::text[], 'ORDER'),
      ('bettor_funded_fills',                ARRAY[]::text[], 'ORDER'),
      ('live_orders',                        ARRAY['lane'], 'ORDER'),
      ('mirror_orders',                      ARRAY[]::text[], 'ORDER'),
      ('execmirror_orders',                  ARRAY['strategy'], 'ORDER'),
      ('execmirror_fills',                   ARRAY['source'], 'ORDER'),
      ('kalshi_live_intents',                ARRAY[]::text[], 'ORDER'),
      ('kalshi_live_fills',                  ARRAY['source'], 'ORDER'),
      ('bettor_desk_orders',                 ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_order_events',           ARRAY[]::text[], 'ORDER'),
      ('bettor_desk_fills',                  ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_plans',        ARRAY[]::text[], 'ORDER'),
      ('bettor_standing_order_events',       ARRAY['source'], 'ORDER'),
      ('rn1x_orders',                        ARRAY[]::text[], 'ORDER'),
      ('rn1x_fills',                         ARRAY[]::text[], 'ORDER'),
      -- R30 (migration 225): the canonical intents, both adapters' records,
      -- the parity ledger and the SMALL LIVE control
      ('canonical_decision_intents',         ARRAY['strategy'], 'ORDER'),
      ('canonical_management_intents',       ARRAY['strategy'], 'ORDER'),
      ('canonical_intent_executions',        ARRAY[]::text[], 'ORDER'),
      ('small_live_order_events',            ARRAY['source'], 'ORDER'),
      ('live_parity_ledger',                 ARRAY['strategy'], 'ORDER'),
      ('small_live_control',                 ARRAY['cleared_by'], 'CONTROL'),
      ('small_live_control_events',          ARRAY['actor'], 'CONTROL')
$$;
