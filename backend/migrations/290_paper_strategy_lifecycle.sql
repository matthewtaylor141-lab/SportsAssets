-- ══════════════════════════════════════════════════════════════════════
-- 290 · PAPER TURNAROUND: THE STRATEGY LIFECYCLE EVENT LOG
--       (PAPER ONLY, NO CAPITAL AUTHORITY, APPEND-ONLY)
-- ══════════════════════════════════════════════════════════════════════
--
-- WHAT THIS RECORDS. Every transition of a PAPER strategy between the
-- lifecycle states
--
--   ACTIVE_CHAMPION / ACTIVE_CHALLENGER / REDUCED_SIZE / SHADOW_ONLY /
--   QUARANTINED / RETIRED
--
-- with the PREDECLARED rule that fired (bettor_strategy_lifecycle,
-- RULES_VERSION + the SHA-256 of the declared constants) and the evidence
-- numbers it fired on (rolling realized P&L, $/capital-hour, drawdown,
-- execution cost, stale-management rate, sample sizes). A strategy with no
-- row is in the initial state ACTIVE_CHALLENGER: the allowlist and the
-- per-strategy entry switches (paper_control) still govern it unchanged.
--
-- A LIFECYCLE STATE IS AN ADDITIONAL GATE, NEVER A GRANT. It is read by the
-- paper ledger's submit_order for ENTRY BUY orders: SHADOW_ONLY /
-- QUARANTINED / RETIRED refuse the entry; REDUCED_SIZE halves the size and
-- caps the order's reservation. No state raises any per-order, per-market
-- or per-fixture cap, the $25 SMALL LIVE cap, the 1:1,000 scale or any
-- strategy allowlist, and nothing here reaches a live venue.
--
-- APPEND-ONLY. UPDATE, DELETE and TRUNCATE are refused by trigger: a
-- transition is a new row, a correction is a new row, a loss is never
-- rewritten or reclassified.
--
-- No BEGIN/COMMIT of its own: the runner applies the file inside one
-- transaction with its schema_migrations row. Idempotent.

CREATE TABLE IF NOT EXISTS paper_strategy_lifecycle_events (
    event_id        bigserial   PRIMARY KEY,
    account_id      text        NOT NULL,
    strategy        text        NOT NULL,
    from_state      text,
    to_state        text        NOT NULL,
    rule_id         text        NOT NULL,
    rules_version   text        NOT NULL,
    rules_sha       text        NOT NULL,
    actor           text        NOT NULL,
    evidence        jsonb       NOT NULL,
    why             text        NOT NULL,
    recorded_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    label           text        NOT NULL DEFAULT 'PAPER',
    authority       text        NOT NULL DEFAULT 'PAPER_ONLY_NO_CAPITAL_AUTHORITY',
    CONSTRAINT paper_lifecycle_label_ck CHECK (label = 'PAPER'),
    CONSTRAINT paper_lifecycle_authority_ck CHECK (
        authority = 'PAPER_ONLY_NO_CAPITAL_AUTHORITY'),
    CONSTRAINT paper_lifecycle_to_state_ck CHECK (to_state IN (
        'ACTIVE_CHAMPION', 'ACTIVE_CHALLENGER', 'REDUCED_SIZE',
        'SHADOW_ONLY', 'QUARANTINED', 'RETIRED')),
    CONSTRAINT paper_lifecycle_from_state_ck CHECK (from_state IS NULL OR
        from_state IN ('ACTIVE_CHAMPION', 'ACTIVE_CHALLENGER',
                       'REDUCED_SIZE', 'SHADOW_ONLY', 'QUARANTINED',
                       'RETIRED')),
    CONSTRAINT paper_lifecycle_changes_ck CHECK (
        from_state IS DISTINCT FROM to_state),
    -- an automatic move is only ever a tightening; anything else names a
    -- person (checked again in code, where the severity order lives)
    CONSTRAINT paper_lifecycle_actor_ck CHECK (
        actor = 'AUTOMATIC_RULE_EVALUATOR' OR actor LIKE 'person:%'),
    CONSTRAINT paper_lifecycle_evidence_ck CHECK (
        jsonb_typeof(evidence) = 'object')
);
CREATE INDEX IF NOT EXISTS paper_lifecycle_strategy_idx
    ON paper_strategy_lifecycle_events (account_id, strategy, event_id DESC);

CREATE OR REPLACE FUNCTION paper_lifecycle_is_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'paper_strategy_lifecycle_events is append-only (%): a '
                    'transition or a correction is a new row', TG_OP;
END $$;

DROP TRIGGER IF EXISTS paper_lifecycle_append_only_trg
    ON paper_strategy_lifecycle_events;
CREATE TRIGGER paper_lifecycle_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_strategy_lifecycle_events
    FOR EACH ROW EXECUTE FUNCTION paper_lifecycle_is_append_only();
DROP TRIGGER IF EXISTS paper_lifecycle_no_truncate_trg
    ON paper_strategy_lifecycle_events;
CREATE TRIGGER paper_lifecycle_no_truncate_trg
    BEFORE TRUNCATE ON paper_strategy_lifecycle_events
    FOR EACH STATEMENT EXECUTE FUNCTION paper_lifecycle_is_append_only();

-- THE CURRENT STATE PER (account, strategy): the newest event's to_state.
CREATE OR REPLACE VIEW paper_strategy_lifecycle_current_v AS
SELECT DISTINCT ON (account_id, strategy) event_id, account_id, strategy,
       from_state, to_state AS state, rule_id, rules_version, rules_sha,
       actor, evidence, why, recorded_at
  FROM paper_strategy_lifecycle_events
 ORDER BY account_id, strategy, event_id DESC;
