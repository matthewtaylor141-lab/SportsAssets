-- ══════════════════════════════════════════════════════════════════════
-- 189 · PAPER ONLY: THE EXPLORATION STRATEGY, THE MAKER-ENTRY POLICY, EVERY
--       EVALUATION ATTEMPT RECORDED, AND AUDREY'S OPERATIONAL AUDIT WITH
--       ASSIGNED RECOMMENDATIONS, RESPONSES AND LATER MEASUREMENTS
-- ══════════════════════════════════════════════════════════════════════
--
-- OWNER-AUTHORIZED 2026-10-01, PAPER ONLY. Real-money execution stays
-- disabled; nothing here is read by a funded module.
--
-- 1 · TWO NEW STRATEGY KEYS on the five strategy CHECKs (184's template):
--     PINNACLE_COMPLETED_GAME_MAKER_PAPER -- the completed-game match,
--       threshold (its active parameter version, never below the owner's
--       0.5 pp floor) and positive expected profit after the TAKER fee,
--       reached with a RESTING bid below the ask. A resting order is not a
--       fill; the venue maker rebate is published but not verified as
--       applied to this account and is never assumed.
--     PINNACLE_EXPLORATION_PAPER -- the owner's bounded TRAINING strategy:
--       positions that may fail the investment policy's edge or after-fee
--       requirement, each decision recording its estimated edge, fees,
--       selection probability and training purpose; at most $100 entry cost
--       incl. fees per position, $5,000 aggregate exposure incl.
--       reservations, one position per fixture, no overlap with another
--       strategy, new entries stop at $1,000 cumulative realized losses.
--       The same $500,000 account and ledger; no reset, no funding.
--     The existing investment strategy and its entry requirements are
--     unchanged.
--
-- 2 · paper_evaluation_attempts: EVERY ATTEMPTED PAPER EVALUATION, whatever
--     became of it -- decided, deferred for a book retry, timed out, raised,
--     skipped by budget -- with the book's source (shared read, fresh read,
--     reused in-context), its age, the venue cooldown and the elapsed time.
--     A missed evaluation is a row here, never an absence. Append-only.
--
-- 3 · paper_recommendations (+ append-only events): Audrey's operational
--     audit assigns evidence-backed recommendations to Derek or Xavier; the
--     owner's response and every later measurement of the metric are
--     events. OPERATIONAL improvements and claims of PROFITABLE LEARNING are
--     separate categories (CHECK), so an operational fix is never reported
--     as learning.
--
-- No BEGIN/COMMIT of its own (the runner wraps the file with its
-- schema_migrations row).

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['paper_decisions', 'paper_orders',
                             'paper_handoffs', 'paper_fills',
                             'paper_xavier_reviews'] LOOP
        EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I',
                       t, t || '_strategy_ck');
        EXECUTE format(
            'ALTER TABLE %I ADD CONSTRAINT %I CHECK (strategy IN '
            '(''DEREK_ENTRY_POLICY_V2'', ''PINNACLE_ONLY_PAPER_BENCHMARK'', '
            '''PINNACLE_COMPLETED_GAME_PAPER'', '
            '''PINNACLE_COMPLETED_GAME_MAKER_PAPER'', '
            '''PINNACLE_EXPLORATION_PAPER''))', t, t || '_strategy_ck');
    END LOOP;
END $$;

INSERT INTO paper_control (control_key, enabled, why, updated_by)
VALUES ('PINNACLE_COMPLETED_GAME_MAKER_PAPER', TRUE,
        'kill switch, inserted enabled at migration 189 (owner '
        'authorization 2026-10-01): the maker-entry paper policy also needs '
        'PAPER_BENCHMARK=on and the paper session enabled. PAPER ONLY; '
        'resting orders, conservative simulated fills, taker fee charged, '
        'maker rebate not assumed',
        'migration 189'),
       ('PINNACLE_EXPLORATION_PAPER', TRUE,
        'kill switch, inserted enabled at migration 189 (owner '
        'authorization 2026-10-01): the bounded paper EXPLORATION strategy '
        '(training / simulated execution). $100 per position incl. fees, '
        '$5,000 aggregate exposure, one per fixture, stops new entries at '
        '$1,000 realized losses. PAPER ONLY; negative expected value is a '
        'research cost, not investment performance',
        'migration 189')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS paper_evaluation_attempts (
    attempt_id      bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT now(),
    session_id      text,
    account_id      text,
    valuation_id    bigint,
    strategy        text        NOT NULL,
    via             text        NOT NULL,
    attempt_no      integer     NOT NULL DEFAULT 1,
    outcome         text        NOT NULL,
    decision_id     text,
    verdict         text,
    refusal         text,
    book_source     text,
    book_age_s      double precision,
    cooldown_s      double precision,
    elapsed_s       double precision,
    detail          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT paper_evaluation_attempts_outcome_ck CHECK (outcome IN (
        'DECIDED', 'DUPLICATE', 'DEFERRED_FOR_BOOK_RETRY',
        'DEFERRED_BOOK_BUDGET', 'TIMEOUT', 'ERROR', 'RETRY_SCHEDULED',
        'RETRY_NOT_SCHEDULED')),
    CONSTRAINT paper_evaluation_attempts_via_ck CHECK (via IN (
        'IN_CYCLE_AT_THE_VALUATION_INSTANT', 'BOOK_RETRY', 'PAPER_PASS'))
);
CREATE INDEX IF NOT EXISTS paper_evaluation_attempts_at_idx
    ON paper_evaluation_attempts (at DESC);
CREATE INDEX IF NOT EXISTS paper_evaluation_attempts_valuation_idx
    ON paper_evaluation_attempts (valuation_id, strategy);
DROP TRIGGER IF EXISTS paper_evaluation_attempts_append_only_trg
    ON paper_evaluation_attempts;
CREATE TRIGGER paper_evaluation_attempts_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_evaluation_attempts
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

CREATE TABLE IF NOT EXISTS paper_recommendations (
    recommendation_id   text PRIMARY KEY,
    account_id          text        NOT NULL,
    finding_id          text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    owner_agent         text        NOT NULL,
    category            text        NOT NULL,
    kind                text        NOT NULL,
    metric              text        NOT NULL,
    baseline            jsonb       NOT NULL,
    recommendation      text        NOT NULL,
    evidence            jsonb       NOT NULL,
    status              text        NOT NULL DEFAULT 'OPEN',
    CONSTRAINT paper_recommendations_id_ck CHECK (
        recommendation_id LIKE 'paperrec:%'),
    CONSTRAINT paper_recommendations_owner_ck CHECK (owner_agent IN (
        'DEREK', 'XAVIER', 'AUDREY')),
    -- AN OPERATIONAL IMPROVEMENT IS NEVER A CLAIM OF PROFITABLE LEARNING.
    CONSTRAINT paper_recommendations_category_ck CHECK (category IN (
        'OPERATIONAL', 'LEARNING_CLAIM')),
    CONSTRAINT paper_recommendations_status_ck CHECK (status IN (
        'OPEN', 'ACKNOWLEDGED', 'MEASURING', 'IMPROVED', 'NOT_IMPROVED',
        'CLOSED'))
);
CREATE INDEX IF NOT EXISTS paper_recommendations_owner_idx
    ON paper_recommendations (owner_agent, status);

CREATE TABLE IF NOT EXISTS paper_recommendation_events (
    event_id            bigserial PRIMARY KEY,
    recommendation_id   text        NOT NULL REFERENCES paper_recommendations,
    at                  timestamptz NOT NULL DEFAULT now(),
    actor               text        NOT NULL,
    kind                text        NOT NULL,
    body                text        NOT NULL,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT paper_recommendation_events_actor_ck CHECK (actor IN (
        'DEREK', 'XAVIER', 'AUDREY')),
    CONSTRAINT paper_recommendation_events_kind_ck CHECK (kind IN (
        'ASSIGNED', 'RESPONSE', 'MEASUREMENT', 'STATUS'))
);
CREATE INDEX IF NOT EXISTS paper_recommendation_events_rec_idx
    ON paper_recommendation_events (recommendation_id, event_id);
DROP TRIGGER IF EXISTS paper_recommendation_events_append_only_trg
    ON paper_recommendation_events;
CREATE TRIGGER paper_recommendation_events_append_only_trg
    BEFORE UPDATE OR DELETE ON paper_recommendation_events
    FOR EACH ROW EXECUTE FUNCTION paper_record_is_append_only();

COMMENT ON COLUMN paper_decisions.strategy IS
    'DEREK_ENTRY_POLICY_V2 (two-model), PINNACLE_ONLY_PAPER_BENCHMARK '
    '(strict), PINNACLE_COMPLETED_GAME_PAPER (investment policy: completed-'
    'game terms, owner threshold), PINNACLE_COMPLETED_GAME_MAKER_PAPER (same '
    'match and threshold, resting entry) or PINNACLE_EXPLORATION_PAPER '
    '(bounded training strategy; may fail the edge and after-fee '
    'requirements by design). One decision per (session, valuation, '
    'strategy).';
