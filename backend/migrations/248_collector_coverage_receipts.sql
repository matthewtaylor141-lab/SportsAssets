-- ══════════════════════════════════════════════════════════════════════
-- 248 · THE COLLECTOR'S COVERAGE RECEIPTS
-- ══════════════════════════════════════════════════════════════════════
--
-- THE DEFECT (P0 incident, owner 2026-10-04). The metered collector fetched
-- the first four competitions of a fixed ranking each cycle and recorded the
-- rest as `budget_dropped` on ONE heartbeat row that the next cycle
-- overwrote. So "how often was NCAAF dropped this week?" had no record to
-- answer it: the 7-day measurement (research-sql incident_collector_cap_a,
-- run 37233454453: NCAAF unfetched in 137 of the 153 cycles with a venue cfb
-- event in the next 24 h) had to be RECONSTRUCTED from the provider events
-- that were fetched (ext_candidate_outcomes) against the venue board -- a
-- competition never fetched leaves no row anywhere. And nothing could say
-- WHEN a dropped competition would next be served, because nothing
-- guaranteed it ever would be.
--
-- WHAT THIS ADDS, appended once per scheduled cycle, never updated:
--
--   scope CYCLE        one row: the DECLARED BUDGET (calls_budget metered
--                      calls, credits_allowance) beside what the cycle used
--                      (calls_made, credits_spent), and the writer's lease
--   scope COMPETITION  one row per enabled competition:
--     planned   what the coverage scheduler decided before any fetch --
--               SCHEDULED (requested) / DEFERRED_TO_SLOT (budget-dropped,
--               with its slot) / SKIPPED_NO_VENUE_EVENT_IN_HORIZON /
--               PROVIDER_DOES_NOT_LIST / PROVIDER_LISTS_INACTIVE /
--               PROVIDER_CATALOGUE_UNREAD / DEFERRED_NO_SLOT_WITHIN_ENVELOPE
--     receipt   what happened -- FETCHED (served) / FETCH_FAILED for a
--               SCHEDULED one, the planned receipt otherwise
--     why                      the reason, in words
--     cycles_since_served      whole cycles since it was last fetched (NULL:
--                              never, within 24 h of receipts)
--     bound_cycles             its stated staleness bound, and
--     starvation_bound_cycles  the bound that holds even when demand exceeds
--                              the budget (collector_coverage docstring)
--     next_slot_at             REQUIRED on every deferral: the cycle in which
--                              the same deterministic rule fetches it
--     credits_charged          what its fetch cost; credits_basis says
--                              whether that is the provider's own
--                              per-request usage header or the stated upper
--                              estimate; zero for anything not fetched (a
--                              deferred competition's discovery refresh, if
--                              the provider charged for it, is the one
--                              other spend, and names its `discovery`)
--   scope CANDIDATE    one row per provider event the per-cycle evaluation
--                      bound (MAX_PER_CYCLE) deferred, with its slot
--
-- The rolling 24 h sum of credits_charged is the daily envelope's ledger and
-- the latest FETCHED row per competition its last-served instant: the
-- collector reads both back each cycle, so a restart resets nothing.
-- coverage_integrity reads the same rows for league_status.collector, so the
-- operations desk shows real budget drops, not an overwritten heartbeat.
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
    in_play             boolean     NOT NULL DEFAULT false,
    venue_events_in_horizon integer,
    next_venue_start    timestamptz,
    last_served_at      timestamptz,
    cycles_since_served integer,
    bound_cycles        integer,
    starvation_bound_cycles integer,
    next_slot_at        timestamptz,
    credits_charged     double precision NOT NULL DEFAULT 0,
    credits_basis       text,
    request_shape       text,
    provider_events     integer,
    discovery           text,
    -- scope CYCLE only: the declared budget and what the cycle used of it
    calls_budget        integer,
    calls_made          integer,
    credits_allowance   double precision,
    credits_spent       double precision,
    why                 text,
    detail              jsonb       NOT NULL DEFAULT '{}'::jsonb,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT collector_coverage_scope_ck CHECK (scope IN
        ('CYCLE', 'COMPETITION', 'CANDIDATE')),
    -- a CANDIDATE row names its provider event; no other row does
    CONSTRAINT collector_coverage_candidate_names_event_ck CHECK (
        (scope = 'CANDIDATE') = (provider_event_id IS NOT NULL)),
    CONSTRAINT collector_coverage_planned_ck CHECK (planned IN
        ('CYCLE_BUDGET', 'SCHEDULED', 'DEFERRED_TO_SLOT',
         'SKIPPED_NO_VENUE_EVENT_IN_HORIZON',
         'PROVIDER_DOES_NOT_LIST', 'PROVIDER_LISTS_INACTIVE',
         'PROVIDER_CATALOGUE_UNREAD', 'DEFERRED_NO_SLOT_WITHIN_ENVELOPE',
         'CANDIDATE_DEFERRED_TO_SLOT')),
    CONSTRAINT collector_coverage_receipt_ck CHECK (receipt IN
        ('CYCLE_BUDGET', 'FETCHED', 'FETCH_FAILED', 'DEFERRED_TO_SLOT',
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
    -- the cycle row, and only it, carries the budget
    CONSTRAINT collector_coverage_cycle_row_ck CHECK (
        (scope = 'CYCLE') = (receipt = 'CYCLE_BUDGET')
        AND (scope = 'CYCLE') = (calls_budget IS NOT NULL)
        AND (scope = 'CYCLE') = (calls_made IS NOT NULL)),
    CONSTRAINT collector_coverage_calls_ck CHECK (
        (calls_budget IS NULL OR calls_budget >= 0)
        AND (calls_made IS NULL OR calls_made >= 0)),
    -- A DEFERRAL IS A PROMISE WITH A DATE: never a silent drop
    CONSTRAINT collector_coverage_deferral_has_slot_ck CHECK (
        receipt NOT IN ('DEFERRED_TO_SLOT', 'CANDIDATE_DEFERRED_TO_SLOT')
        OR next_slot_at IS NOT NULL),
    CONSTRAINT collector_coverage_slot_is_later_ck CHECK (
        next_slot_at IS NULL OR next_slot_at > cycle_at),
    CONSTRAINT collector_coverage_cycles_since_ck CHECK (
        cycles_since_served IS NULL OR cycles_since_served >= 0),
    -- spend only on a request actually made -- a metered fetch, or the
    -- discovery refresh a deferred competition got -- and never negative or
    -- unexplained; the cycle row carries its total in credits_spent
    CONSTRAINT collector_coverage_spend_ck CHECK (
        credits_charged >= 0
        AND (receipt IN ('FETCHED', 'FETCH_FAILED') OR discovery IS NOT NULL
             OR credits_charged = 0)
        AND (credits_charged = 0 OR credits_basis IS NOT NULL)
        AND (credits_spent IS NULL OR credits_spent >= 0)),
    CONSTRAINT collector_coverage_basis_ck CHECK (credits_basis IS NULL
        OR credits_basis IN ('MEASURED_PROVIDER_USAGE_HEADER',
                             'ESTIMATED_UPPER_BOUND_UNTIL_MEASURED')),
    CONSTRAINT collector_coverage_one_competition_row_ck
        UNIQUE (cycle_id, scope, competition, provider_event_id)
);

-- UNIQUE treats NULL provider_event_ids as distinct, so the one-row-per-
-- competition-per-cycle and one-budget-row-per-cycle rules are their own
-- partial indexes.
CREATE UNIQUE INDEX IF NOT EXISTS collector_coverage_one_per_competition_ix
    ON collector_coverage_receipts (cycle_id, competition)
    WHERE scope = 'COMPETITION';
CREATE UNIQUE INDEX IF NOT EXISTS collector_coverage_one_cycle_row_ix
    ON collector_coverage_receipts (cycle_id)
    WHERE scope = 'CYCLE';
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
DROP TRIGGER IF EXISTS collector_coverage_receipts_no_truncate_trg
    ON collector_coverage_receipts;
CREATE TRIGGER collector_coverage_receipts_no_truncate_trg
    BEFORE TRUNCATE ON collector_coverage_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION collector_coverage_receipts_append_only();
