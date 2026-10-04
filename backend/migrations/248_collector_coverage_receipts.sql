-- ══════════════════════════════════════════════════════════════════════
-- 248 · THE COLLECTOR'S COVERAGE RECEIPTS
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (P0 incident, owner 2026-10-04). The metered collector chose four
-- competitions a cycle and recorded the rest as `budget_dropped` on ONE
-- heartbeat row that the next cycle overwrote. So "how often was NCAAF
-- dropped this week?" had no record to answer it: the 7-day measurement
-- (research-sql incident_collector_cap_a, run 37233454453) had to be
-- RECONSTRUCTED from the provider events that were fetched (ext_candidate
-- outcomes) against the venue board -- a competition never fetched leaves no
-- row anywhere. And `select_sports` could not say WHEN a dropped competition
-- would next be served, because nothing guaranteed it ever would be.
--
-- WHAT THIS ADDS. One row per cycle per enabled competition (scope
-- COMPETITION), and one per provider event the per-cycle evaluation bound
-- deferred (scope CANDIDATE), appended once, never updated:
--
--   planned   what the coverage scheduler decided before any fetch:
--             SCHEDULED / DEFERRED_TO_SLOT / SKIPPED_NO_VENUE_EVENT_IN_HORIZON
--             / PROVIDER_DOES_NOT_LIST / PROVIDER_LISTS_INACTIVE /
--             PROVIDER_CATALOGUE_UNREAD / DEFERRED_NO_SLOT_WITHIN_ENVELOPE
--   receipt   what happened: FETCHED / FETCH_FAILED for a SCHEDULED one, the
--             planned receipt otherwise (CANDIDATE_DEFERRED_TO_SLOT for a
--             deferred provider event)
--   next_slot_at          REQUIRED on every DEFERRED_TO_SLOT receipt: the cycle
--                         in which the same deterministic rule fetches it
--   credits_charged       what the fetch cost, and credits_basis says whether
--                         that is the provider's own per-request usage header
--                         or the stated upper estimate; zero for anything not
--                         fetched (a deferred competition's discovery refresh,
--                         if the provider charged for it, is the one other
--                         spend, and names its `discovery`) -- a receipt
--                         cannot carry spend it did not incur
--
-- The rolling 24 h sum of credits_charged is the daily envelope's ledger: the
-- collector reads it back each cycle, so a restart cannot reset the spend.
--
-- ADDITIVE AND IDEMPOTENT. A new table; nothing existing is altered. The
-- writer checks for the table and reports COVERAGE_RECEIPTS_TABLE_ABSENT on a
-- database without it, so the code may ship before or after this.

CREATE TABLE IF NOT EXISTS collector_coverage_receipts (
    id                  bigserial   PRIMARY KEY,
    cycle_id            text        NOT NULL,
    cycle_at            timestamptz NOT NULL,
    writer              text,
    scheduler_version   text        NOT NULL,
    scope               text        NOT NULL,
    competition         text        NOT NULL,
    venue_token         text,
    family              text,
    provider_event_id   text,
    planned             text        NOT NULL,
    receipt             text        NOT NULL,
    priority_rank       integer,
    held_positions      integer     NOT NULL DEFAULT 0,
    feed_covered        boolean     NOT NULL DEFAULT false,
    venue_events_in_horizon integer,
    next_venue_start    timestamptz,
    last_served_at      timestamptz,
    bound_cycles        integer,
    next_slot_at        timestamptz,
    credits_charged     double precision NOT NULL DEFAULT 0,
    credits_basis       text,
    request_shape       text,
    provider_events     integer,
    discovery           text,
    why                 text,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT collector_coverage_scope_ck CHECK (scope IN
        ('COMPETITION', 'CANDIDATE')),
    -- a CANDIDATE row names its provider event; a COMPETITION row does not
    CONSTRAINT collector_coverage_candidate_names_event_ck CHECK (
        (scope = 'CANDIDATE') = (provider_event_id IS NOT NULL)),
    CONSTRAINT collector_coverage_planned_ck CHECK (planned IN
        ('SCHEDULED', 'DEFERRED_TO_SLOT', 'SKIPPED_NO_VENUE_EVENT_IN_HORIZON',
         'PROVIDER_DOES_NOT_LIST', 'PROVIDER_LISTS_INACTIVE',
         'PROVIDER_CATALOGUE_UNREAD', 'DEFERRED_NO_SLOT_WITHIN_ENVELOPE',
         'CANDIDATE_DEFERRED_TO_SLOT')),
    CONSTRAINT collector_coverage_receipt_ck CHECK (receipt IN
        ('FETCHED', 'FETCH_FAILED', 'DEFERRED_TO_SLOT',
         'SKIPPED_NO_VENUE_EVENT_IN_HORIZON', 'PROVIDER_DOES_NOT_LIST',
         'PROVIDER_LISTS_INACTIVE', 'PROVIDER_CATALOGUE_UNREAD',
         'DEFERRED_NO_SLOT_WITHIN_ENVELOPE', 'CANDIDATE_DEFERRED_TO_SLOT')),
    -- a SCHEDULED competition ends FETCHED or FETCH_FAILED; anything else
    -- ends as it was planned
    CONSTRAINT collector_coverage_settled_ck CHECK (
        (planned = 'SCHEDULED') = (receipt IN ('FETCHED', 'FETCH_FAILED'))
        AND (planned = 'SCHEDULED' OR receipt = planned)),
    CONSTRAINT collector_coverage_candidate_receipt_ck CHECK (
        (scope = 'CANDIDATE') = (receipt = 'CANDIDATE_DEFERRED_TO_SLOT')),
    -- A DEFERRAL IS A PROMISE WITH A DATE: never a silent drop
    CONSTRAINT collector_coverage_deferral_has_slot_ck CHECK (
        receipt NOT IN ('DEFERRED_TO_SLOT', 'CANDIDATE_DEFERRED_TO_SLOT')
        OR next_slot_at IS NOT NULL),
    CONSTRAINT collector_coverage_slot_is_later_ck CHECK (
        next_slot_at IS NULL OR next_slot_at > cycle_at),
    -- spend only on a request actually made -- a metered fetch, or the
    -- discovery refresh a deferred competition got -- and never negative or
    -- unexplained
    CONSTRAINT collector_coverage_spend_ck CHECK (
        credits_charged >= 0
        AND (receipt IN ('FETCHED', 'FETCH_FAILED') OR discovery IS NOT NULL
             OR credits_charged = 0)
        AND (credits_charged = 0 OR credits_basis IS NOT NULL)),
    CONSTRAINT collector_coverage_basis_ck CHECK (credits_basis IS NULL
        OR credits_basis IN ('MEASURED_PROVIDER_USAGE_HEADER',
                             'ESTIMATED_UPPER_BOUND_UNTIL_MEASURED')),
    CONSTRAINT collector_coverage_one_competition_row_ck
        UNIQUE (cycle_id, scope, competition, provider_event_id)
);

-- UNIQUE treats NULL provider_event_ids as distinct, so the one-row-per-
-- competition-per-cycle rule for COMPETITION rows is its own partial index.
CREATE UNIQUE INDEX IF NOT EXISTS collector_coverage_one_per_competition_ix
    ON collector_coverage_receipts (cycle_id, competition)
    WHERE scope = 'COMPETITION';
CREATE INDEX IF NOT EXISTS collector_coverage_cycle_at_ix
    ON collector_coverage_receipts (cycle_at);
CREATE INDEX IF NOT EXISTS collector_coverage_competition_ix
    ON collector_coverage_receipts (competition, cycle_at);

-- APPEND-ONLY: a receipt is what the cycle decided and spent; it is never
-- rewritten after the fact.
CREATE OR REPLACE FUNCTION collector_coverage_receipts_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'collector_coverage_receipts is append-only (% refused)',
        TG_OP;
END $$;

DROP TRIGGER IF EXISTS collector_coverage_receipts_append_only_trg
    ON collector_coverage_receipts;
CREATE TRIGGER collector_coverage_receipts_append_only_trg
    BEFORE UPDATE OR DELETE ON collector_coverage_receipts
    FOR EACH ROW EXECUTE FUNCTION collector_coverage_receipts_append_only();
