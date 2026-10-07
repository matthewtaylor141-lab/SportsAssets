-- 313 · THE EXIT THAT CANCELLED ITS OWN PROTECTION, PERSISTED (PAPER ONLY)
-- An EXIT / REDUCE ranked on a complete packet first cancels the resting
-- protection. This table records that intent and every transition after it:
-- CANCEL_REQUESTED -> CANCEL_TERMINAL -> EXIT_SUBMITTED, or an explicit
-- ABANDONED (its resolution named) with the protection restored. One open intent per position.
-- Paper ledger only: NO venue order, cancel, funding or capital authority.

CREATE TABLE IF NOT EXISTS paper_exit_intents (
    intent_id text PRIMARY KEY,
    account_id text NOT NULL,
    group_id text NOT NULL,
    position_key text NOT NULL,
    us_market_slug text NOT NULL,
    holding_side text NOT NULL,
    decided_review_id text NOT NULL,
    selection text NOT NULL CHECK (selection IN ('EXIT', 'REDUCE')),
    decided_at timestamptz NOT NULL,
    decided_probability_source_at timestamptz,
    cancel_orders text[] NOT NULL,
    state text NOT NULL CHECK (state IN (
        'CANCEL_REQUESTED', 'CANCEL_TERMINAL', 'EXIT_SUBMITTED',
        'ABANDONED')),
    cancel_requested_at timestamptz NOT NULL,
    cancel_deadline_at timestamptz NOT NULL,
    cancel_terminal_at timestamptz,
    revalidate_by timestamptz,
    resolved_at timestamptz,
    resolution text,
    resolution_review_id text,
    exit_order_id text,
    protection_order_id text,
    transitions jsonb NOT NULL DEFAULT '[]'::jsonb,
    updated_at timestamptz NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS paper_exit_intents_one_open
    ON paper_exit_intents (account_id, group_id, position_key)
    WHERE state IN ('CANCEL_REQUESTED', 'CANCEL_TERMINAL');

CREATE INDEX IF NOT EXISTS paper_exit_intents_recent
    ON paper_exit_intents (decided_at DESC);

-- THE INTENT'S OWN DEADLINES ARE A REVIEW TRIGGER: a cancel not confirmed by
-- its deadline, or a terminal cancel with no fresh probability by
-- revalidate_by, is re-reviewed at that instant (EXIT_INTENT_DEADLINE).
DO $$
BEGIN
    IF to_regclass('paper_xavier_reviews') IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_constraint
                        WHERE conname = 'paper_xavier_reviews_trigger_v3_ck')
    THEN
        ALTER TABLE paper_xavier_reviews
            ADD CONSTRAINT paper_xavier_reviews_trigger_v3_ck CHECK (
                trigger IN ('FIRST_FILL', 'FILL_EVENT', 'MARKET_EVENT',
                            'SCHEDULED_BACKSTOP', 'ORDER_EVENT',
                            'VALUATION_CHANGE', 'GAME_STATE_CHANGE',
                            'FRESHNESS_EXPIRY', 'EXIT_INTENT_DEADLINE'))
            NOT VALID;
        ALTER TABLE paper_xavier_reviews
            DROP CONSTRAINT IF EXISTS paper_xavier_reviews_trigger_v2_ck;
    END IF;
END $$;
