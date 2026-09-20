-- THE POSITION LIFECYCLE, AS APPEND-ONLY EVENTS.
--
-- Owner directive 2026-09-20 §1:
--
--     "X1 positions can OPEN but can never CLOSE. EXIT_RULE_HORIZON is
--     declared but unexecutable. Do NOT UPDATE or DELETE the
--     append-only position row. Preserve append-only semantics.
--     Implement position lifecycle through append-only events."
--
-- WHY A NEW TABLE IS THE RIGHT ANSWER HERE, and not a second ledger.
-- `bettor_experimental_positions` carries ONE row per position with a
-- `status` column and an append-only trigger, so that status can never
-- change -- an UPDATE is refused. The position row is the OPEN event
-- and nothing more. This table holds every LATER fact about the same
-- position, keyed back to it, exactly as
-- `bettor_experimental_markouts` already holds later facts about a
-- decision. That is the existing architecture, not a new one: same
-- append-only trigger, same keyed-back shape, same refusal to mutate.
--
-- The position's CURRENT state is therefore DERIVED by folding its
-- events in order, never stored. A derived state cannot drift from its
-- evidence, and the evidence stays readable forever.
--
-- NOTHING HERE BACKDATES. §3: "Do not pretend they historically
-- exited." The four X1 positions and the 45 X1C positions opened under
-- infrastructure where the exit could not run, and the seed below
-- records that as its own event type rather than inventing exits.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_experimental_position_events (
    position_event_id    TEXT PRIMARY KEY,
    position_id          TEXT NOT NULL
        REFERENCES bettor_experimental_positions (position_id),
    experiment_id        TEXT NOT NULL,
    market_id            TEXT NOT NULL,
    event_type           TEXT NOT NULL,
    event_at             TIMESTAMPTZ NOT NULL,
    sequence_no          INTEGER NOT NULL,

    -- QUANTITY MOVED BY THIS EVENT, signed the way capital moves:
    -- positive opens exposure, negative releases it. An event that
    -- moves no quantity (EXIT_BECAME_ELIGIBLE) leaves these NULL
    -- rather than writing a zero that reads like a filled exit of
    -- nothing.
    qty_delta            DOUBLE PRECISION,
    notional_delta_usd   NUMERIC(20, 6),
    vwap                 DOUBLE PRECISION,

    -- §4's execution evidence. An exit that cannot name the book it
    -- was priced against is not an exit.
    intended_qty         DOUBLE PRECISION,
    filled_qty           DOUBLE PRECISION,
    unfilled_qty         DOUBLE PRECISION,
    spread_cost          DOUBLE PRECISION,
    slippage             DOUBLE PRECISION,
    book_sha             TEXT,
    book_source_timestamp      TEXT,
    book_received_timestamp    TIMESTAMPTZ,
    l2_evidence_id       TEXT,

    -- §4's four exit instants, named for the clock contract rather
    -- than reusing the decision lane's ambiguous names.
    exit_decision_timestamp    TIMESTAMPTZ,
    exit_model_start           TIMESTAMPTZ,
    exit_model_end             TIMESTAMPTZ,
    exit_arrival_timestamp     TIMESTAMPTZ,
    latency_status             TEXT,

    execution_status     TEXT,
    why                  TEXT,
    provenance           JSONB,
    written_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bettor_exp_pos_event_type CHECK (event_type IN (
        'POSITION_OPENED',
        'EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY',
        'EXIT_BECAME_ELIGIBLE',
        'EXIT_DECISION',
        'EXIT_EXECUTION',
        'POSITION_CLOSED',
        'SETTLED')),
    -- §4: an exit either names its book or says NOT_IDENTIFIED. There
    -- is no third state in which a quantity moved for no stated price.
    CONSTRAINT bettor_exp_pos_event_execution CHECK (
        execution_status IS NULL
        OR execution_status IN ('FILLED', 'PARTIAL', 'NOT_IDENTIFIED',
                                'REFUSED')),
    CONSTRAINT bettor_exp_pos_event_filled_has_a_book CHECK (
        execution_status NOT IN ('FILLED', 'PARTIAL')
        OR book_sha IS NOT NULL),
    -- An exit execution that moved quantity must have moved it DOWN.
    -- Nothing in this lane adds to a position after it opens.
    CONSTRAINT bettor_exp_pos_event_exit_releases CHECK (
        event_type <> 'EXIT_EXECUTION'
        OR qty_delta IS NULL OR qty_delta <= 0)
);

-- ONE EVENT PER (position, type, sequence). A retried write lands on
-- the same key and is refused rather than double-counting a release.
CREATE UNIQUE INDEX IF NOT EXISTS bettor_exp_pos_event_once
    ON bettor_experimental_position_events
       (position_id, event_type, sequence_no);

CREATE INDEX IF NOT EXISTS bettor_exp_pos_event_by_position
    ON bettor_experimental_position_events (position_id, event_at);

CREATE INDEX IF NOT EXISTS bettor_exp_pos_event_by_experiment
    ON bettor_experimental_position_events (experiment_id, event_at);

DROP TRIGGER IF EXISTS bettor_experimental_position_events_immutable
    ON bettor_experimental_position_events;
CREATE TRIGGER bettor_experimental_position_events_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_position_events
    FOR EACH ROW EXECUTE FUNCTION shadow_append_only();

-- ── THE HISTORICAL TRUTH, SEEDED ONCE ────────────────────────────────
--
-- §3: "They opened under infrastructure where EXIT_RULE_HORIZON was
-- declared but could not execute. Preserve that fact... Do not create
-- a backdated exit."
--
-- Two events per existing position, both at its OWN opened_at:
--
--   POSITION_OPENED                     -- what actually happened
--   EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY -- the condition it opened under
--
-- The second is not an exit and not a failure to exit. It is the
-- statement that at the moment this position was created, no code path
-- existed that could ever close it. ON CONFLICT DO NOTHING so a re-run
-- of the migration cannot duplicate history.

INSERT INTO bettor_experimental_position_events (
    position_event_id, position_id, experiment_id, market_id,
    event_type, event_at, sequence_no, qty_delta, notional_delta_usd,
    vwap, why, provenance)
SELECT
    'xpe_' || substr(md5(p.position_id || '|POSITION_OPENED'), 1, 32),
    p.position_id, p.experiment_id, p.market_id,
    'POSITION_OPENED', p.opened_at, 0,
    p.entry_qty, p.entry_notional_usd, p.entry_vwap,
    'seeded from the position row itself; the position row IS the open '
    || 'event and is not restated',
    jsonb_build_object('seededBy', 'migration 085',
                       'derivedFrom', 'bettor_experimental_positions')
  FROM bettor_experimental_positions p
ON CONFLICT (position_id, event_type, sequence_no) DO NOTHING;

INSERT INTO bettor_experimental_position_events (
    position_event_id, position_id, experiment_id, market_id,
    event_type, event_at, sequence_no, why, provenance)
SELECT
    'xpe_' || substr(md5(p.position_id || '|EXIT_UNAVAILABLE'), 1, 32),
    p.position_id, p.experiment_id, p.market_id,
    'EXIT_MECHANISM_UNAVAILABLE_AT_ENTRY', p.opened_at, 0,
    'EXIT_RULE_HORIZON was declared in the frozen policy but no code '
    || 'path could execute it when this position opened: the positions '
    || 'table is append-only, carries no close event, and the only '
    || 'write in the codebase is the INSERT. This is recorded as the '
    || 'condition of entry, NOT as an exit and NOT as a missed exit.',
    jsonb_build_object('seededBy', 'migration 085',
                       'ownerDirective', '2026-09-20 section 3',
                       'backdatedExit', false)
  FROM bettor_experimental_positions p
ON CONFLICT (position_id, event_type, sequence_no) DO NOTHING;

COMMIT;
