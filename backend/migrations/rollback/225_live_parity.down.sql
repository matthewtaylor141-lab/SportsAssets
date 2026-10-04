-- ROLLBACK 225 · LIVE PARITY. Refused while ANY canonical intent, adapter
-- record, parity row, cutover, owner LIVE approval or control event exists:
-- the audit chain is never dropped as cleanup. With none, drops cleanly.
DO $$
BEGIN
    IF (to_regclass('canonical_decision_intents') IS NOT NULL
            AND EXISTS (SELECT 1 FROM canonical_decision_intents))
       OR (to_regclass('canonical_management_intents') IS NOT NULL
            AND EXISTS (SELECT 1 FROM canonical_management_intents))
       OR (to_regclass('canonical_intent_executions') IS NOT NULL
            AND EXISTS (SELECT 1 FROM canonical_intent_executions))
       OR (to_regclass('live_parity_ledger') IS NOT NULL
            AND EXISTS (SELECT 1 FROM live_parity_ledger))
       OR (to_regclass('live_parity_cutover') IS NOT NULL
            AND EXISTS (SELECT 1 FROM live_parity_cutover))
       OR (to_regclass('live_approvals') IS NOT NULL
            AND EXISTS (SELECT 1 FROM live_approvals))
       OR (to_regclass('small_live_control_events') IS NOT NULL
            AND EXISTS (SELECT 1 FROM small_live_control_events)) THEN
        RAISE EXCEPTION 'ROLLBACK_225_REFUSED: live parity records exist (audit chain)';
    END IF;
END $$;

DROP VIEW IF EXISTS live_approvals_current;
DROP VIEW IF EXISTS live_parity_effective_cutover;
DROP TABLE IF EXISTS live_approvals;
DROP TABLE IF EXISTS live_parity_cutover;
DROP TABLE IF EXISTS live_parity_hook_installs;
DROP TABLE IF EXISTS small_live_order_events;
DROP TABLE IF EXISTS live_parity_ledger;
DROP TABLE IF EXISTS canonical_intent_executions;
DROP TABLE IF EXISTS canonical_management_intents;
DROP TABLE IF EXISTS canonical_decision_intents;
DROP TABLE IF EXISTS small_live_control_events;
DROP TABLE IF EXISTS small_live_control;
DROP FUNCTION IF EXISTS live_approvals_stamp();
DROP FUNCTION IF EXISTS live_parity_cutover_stamp();
DROP FUNCTION IF EXISTS cmi_alternative_set_ok(jsonb);
DROP FUNCTION IF EXISTS small_live_control_guard();
DROP FUNCTION IF EXISTS live_parity_append_only();
