-- ══════════════════════════════════════════════════════════════════════
-- 300 · R30C: LIVE EXECUTION EVIDENCE KEPT APART, AND THE OPPORTUNITY
--       SCORE V1 / V2 SHADOW TOURNAMENT
-- ══════════════════════════════════════════════════════════════════════
--
-- 1 · ACTUAL CAN NEVER BE WRITTEN FROM A SHADOW PROPOSAL. Migration 225
--     keeps the three execution-evidence classes in separate tables
--     (PAPER_SIMULATION: paper_orders / paper_fills, event_source CHECKed
--     'SIMULATOR'; LIVE_SHADOW: canonical_intent_executions adapter
--     SMALL_LIVE mode SHADOW; ACTUAL: small_live_order_events source CHECKed
--     'VENUE_ORDER_RECORD') -- but nothing stopped a venue event row from
--     naming a SHADOW execution, and a reader counting "events" would then
--     have counted a proposal nobody sent as a real fill. The guard below
--     refuses any small_live_order_events row whose execution is not a
--     SMALL_LIVE execution in a non-SHADOW mode. Today the database admits
--     no such mode (225's cie_mode_ck), so the table stays empty by
--     construction and ACTUAL stays UNMEASURED -- with that reason, never a
--     zero (execution_evidence.R_NO_ACTUAL).
--
--     It is an AFTER INSERT CONSTRAINT trigger, not a BEFORE trigger: a
--     BEFORE trigger runs ahead of the CHECK constraints and would answer
--     first for every bad row, so 225's own refusals (sloe_source_ck: a
--     fill never comes from PAPER) would no longer be the refusal anyone
--     sees or any test proves. As an AFTER trigger it fires only on a row
--     225's CHECKs and foreign key already admitted, and each refusal stays
--     separately provable (tests/test_execution_calibration.py).
--
-- 2 · opportunity_score_tournament: for EVERY canonical decision intent,
--     the Opportunity Score V1 (LOL_OPPORTUNITY_SCORE_V1) and V2
--     (LOL_OPPORTUNITY_SCORE_V2_LCB) computed AT THE DECISION INSTANT from
--     the same inputs, keyed by the intent (id, version, sha) and the unique
--     opportunity it is (opportunity_id + key version). Outcomes are NOT
--     stored here: they join later from the paper ledger (resolved net per
--     unique opportunity, INVESTMENT sleeve only, forward from the R30
--     production cutover). V2 HAS NO AUTHORITY: authority is CHECKed
--     'SHADOW_NO_AUTHORITY'; nothing reads a score here to decide; the
--     ENTER rule is untouched; promotion needs out-of-sample evidence and a
--     later owner decision (a new migration and code change).
--
--     NO BACKFILL. recorded_at is the DATABASE's clock at the insert: a
--     BEFORE INSERT trigger overwrites whatever the writer supplied with
--     clock_timestamp(), and the same trigger refuses a row whose intent is
--     not recorded in canonical_decision_intents or whose decided_at,
--     intent sha, version, decision, strategy, sleeve, market or side
--     differ from that intent's. The CHECK then bounds recording time on
--     BOTH sides of the intent's own decision instant: no later than 15
--     minutes after it (no score can be computed after its outcome is
--     known) and no earlier than 2 minutes before it (the application's
--     clock, which stamps the decision, against the database's).
--     The series (tournament / V1 / V2 version and the V2 spec sha) is
--     recorded with every row: a V2 re-tuned after its outcomes were seen
--     is a different series, never the same one -- the report and the
--     promotion rule evaluate one series at a time
--     (opportunity_tournament.current_series).
--
-- APPEND-ONLY. No capital, order, venue, limit, threshold or allowlist is
-- touched. No BEGIN/COMMIT of its own: the runner applies the file inside
-- one transaction with its schema_migrations row. Idempotent.

-- ── 1 · small_live_order_events: only a LIVE-mode execution has fills ──
CREATE OR REPLACE FUNCTION small_live_order_event_live_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    ad text;
    md text;
BEGIN
    SELECT adapter, mode INTO ad, md FROM canonical_intent_executions
     WHERE execution_id = NEW.execution_id;
    IF ad IS DISTINCT FROM 'SMALL_LIVE' OR md IS NULL OR md = 'SHADOW' THEN
        RAISE EXCEPTION 'ACTUAL_FILL_ON_A_NON_LIVE_EXECUTION: % is adapter % '
                        'mode %; a SHADOW proposal was never sent and can '
                        'carry no venue fill', NEW.execution_id, ad, md
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DO $$
BEGIN
    IF to_regclass('small_live_order_events') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS small_live_order_events_live_only_trg
            ON small_live_order_events;
        CREATE CONSTRAINT TRIGGER small_live_order_events_live_only_trg
            AFTER INSERT ON small_live_order_events NOT DEFERRABLE
            FOR EACH ROW EXECUTE FUNCTION small_live_order_event_live_only();
    END IF;
END $$;

-- ── 2 · the V1 / V2 shadow tournament ──────────────────────────────────
CREATE OR REPLACE FUNCTION opportunity_tournament_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'OPPORTUNITY_TOURNAMENT_APPEND_ONLY: % on % is refused',
        TG_OP, TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END $$;

CREATE TABLE IF NOT EXISTS opportunity_score_tournament (
    entry_id                 text PRIMARY KEY,
    tournament_version       text NOT NULL,
    -- the canonical intent this row scores. No foreign key: a reference
    -- would change how the append-only canonical_decision_intents refuses a
    -- TRUNCATE (225's trigger must stay the refusal). Instead the BEFORE
    -- INSERT trigger below refuses a row whose intent is not recorded, or
    -- whose decided_at / sha / version / decision / strategy / sleeve /
    -- market / side are not that intent's. The only writer
    -- (opportunity_tournament.record_entry) runs after the intent is
    -- recorded, inside the decision hook.
    intent_id                text NOT NULL UNIQUE,
    intent_version           text NOT NULL,
    intent_sha               text NOT NULL,
    decision_id              text NOT NULL,
    opportunity_id           text NOT NULL,
    opportunity_key_version  text NOT NULL,
    opportunity_key          jsonb NOT NULL,
    event_key                text NOT NULL,
    strategy                 text NOT NULL,
    sleeve                   text NOT NULL,
    us_market_slug           text NOT NULL,
    holding_side             text NOT NULL,
    decided_at               timestamptz NOT NULL,
    v1_version               text NOT NULL,
    v1_status                text NOT NULL,
    v1_score                 double precision,
    v1_predicted_net_usd     double precision,
    v1_why                   text,
    v1_detail                jsonb NOT NULL,
    v2_version               text NOT NULL,
    v2_spec_sha              text NOT NULL,
    v2_status                text NOT NULL,
    v2_score                 double precision,
    v2_predicted_net_lcb_usd double precision,
    v2_why                   text,
    v2_detail                jsonb NOT NULL,
    authority                text NOT NULL DEFAULT 'SHADOW_NO_AUTHORITY',
    -- the database clock at the insert: whatever a writer supplies is
    -- overwritten by the trigger below (clock_timestamp())
    recorded_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ost_id_ck CHECK (entry_id ~ '^ost_[0-9a-f]{24}$'),
    CONSTRAINT ost_intent_ck CHECK (intent_id ~ '^cdi_[0-9a-f]{24}$'
                                    AND intent_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ost_spec_sha_ck CHECK (v2_spec_sha ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ost_sleeve_ck CHECK (sleeve IN ('INVESTMENT', 'TRAINING',
                                               'BENCHMARK', 'UNCLASSIFIED')),
    CONSTRAINT ost_side_ck CHECK (holding_side IN ('LONG', 'SHORT')),
    CONSTRAINT ost_status_ck CHECK (v1_status IN ('MEASURED', 'UNAVAILABLE')
                                    AND v2_status IN ('MEASURED',
                                                      'UNAVAILABLE')),
    -- a MEASURED score has a value; an UNAVAILABLE one has no value and a
    -- named reason (never a manufactured zero)
    CONSTRAINT ost_v1_measured_ck CHECK (
        (v1_status = 'MEASURED' AND v1_score IS NOT NULL)
        OR (v1_status = 'UNAVAILABLE' AND v1_score IS NULL
            AND v1_why IS NOT NULL AND length(btrim(v1_why)) > 0)),
    CONSTRAINT ost_v2_measured_ck CHECK (
        (v2_status = 'MEASURED' AND v2_score IS NOT NULL)
        OR (v2_status = 'UNAVAILABLE' AND v2_score IS NULL
            AND v2_why IS NOT NULL AND length(btrim(v2_why)) > 0)),
    -- V2 (and the tournament) has no authority
    CONSTRAINT ost_no_authority_ck CHECK (authority = 'SHADOW_NO_AUTHORITY')
);
-- scored at decision time: never backfilled after an outcome is known
-- (recorded_at is the database clock, forced by the trigger below). Dropped
-- and re-added so a re-run converges on this definition.
ALTER TABLE opportunity_score_tournament
    DROP CONSTRAINT IF EXISTS ost_at_decision_ck;
ALTER TABLE opportunity_score_tournament
    ADD CONSTRAINT ost_at_decision_ck CHECK (
        recorded_at <= decided_at + interval '15 minutes'
        AND recorded_at >= decided_at - interval '2 minutes');
CREATE INDEX IF NOT EXISTS ost_decided_idx
    ON opportunity_score_tournament (sleeve, decided_at);
CREATE INDEX IF NOT EXISTS ost_opportunity_idx
    ON opportunity_score_tournament (opportunity_id, decided_at);

-- recorded_at is the database's clock, and the row is its intent's
CREATE OR REPLACE FUNCTION opportunity_tournament_at_decision()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    i record;
BEGIN
    NEW.recorded_at := clock_timestamp();
    SELECT created_at, content_sha, intent_version, decision_id, strategy,
           sleeve, us_market_slug, holding_side
      INTO i FROM canonical_decision_intents WHERE intent_id = NEW.intent_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'TOURNAMENT_ENTRY_WITHOUT_ITS_INTENT: % is not a '
                        'recorded canonical decision intent', NEW.intent_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF i.created_at IS DISTINCT FROM NEW.decided_at
       OR i.content_sha IS DISTINCT FROM NEW.intent_sha
       OR i.intent_version IS DISTINCT FROM NEW.intent_version
       OR i.decision_id IS DISTINCT FROM NEW.decision_id
       OR i.strategy IS DISTINCT FROM NEW.strategy
       OR i.sleeve IS DISTINCT FROM NEW.sleeve
       OR i.us_market_slug IS DISTINCT FROM NEW.us_market_slug
       OR i.holding_side IS DISTINCT FROM NEW.holding_side THEN
        RAISE EXCEPTION 'TOURNAMENT_ENTRY_DOES_NOT_MATCH_ITS_INTENT: % '
                        '(decided_at, sha, version, decision, strategy, '
                        'sleeve, market and side must be the intent''s own)',
                        NEW.intent_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS opportunity_score_tournament_at_decision_trg
    ON opportunity_score_tournament;
CREATE TRIGGER opportunity_score_tournament_at_decision_trg
    BEFORE INSERT ON opportunity_score_tournament
    FOR EACH ROW EXECUTE FUNCTION opportunity_tournament_at_decision();

DROP TRIGGER IF EXISTS opportunity_score_tournament_append_only_trg
    ON opportunity_score_tournament;
CREATE TRIGGER opportunity_score_tournament_append_only_trg
    BEFORE UPDATE OR DELETE ON opportunity_score_tournament
    FOR EACH ROW EXECUTE FUNCTION opportunity_tournament_append_only();
DROP TRIGGER IF EXISTS opportunity_score_tournament_no_truncate_trg
    ON opportunity_score_tournament;
CREATE TRIGGER opportunity_score_tournament_no_truncate_trg
    BEFORE TRUNCATE ON opportunity_score_tournament
    FOR EACH STATEMENT EXECUTE FUNCTION opportunity_tournament_append_only();
