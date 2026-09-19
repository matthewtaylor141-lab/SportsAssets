-- THE T0 SEAL, WRITTEN DOWN BEFORE ITS ARRIVAL EVIDENCE EXISTS.
--
-- Owner directive 2026-09-19 21:2xZ §4: "Freeze these BEFORE the first
-- outcome is known." 22:4xZ §7: no lookahead.
--
-- WHY THIS TABLE HAS TO EXIST, rather than the worker holding the seal
-- in memory between ticks. The arrival book arrives through the GitHub
-- bridge MINUTES after the decision -- that is the regime we are in
-- until a persistent institutional worker exists. So a decision is
-- sealed on one tick and executed on a later one, and the gap is
-- exactly where lookahead would enter:
--
--   * A seal held only in a process is lost on restart, and the
--     obvious repair -- re-seal against a past instant once the
--     evidence is already in hand -- is the lookahead itself. The
--     features would still be honest; the CHOICE OF WHICH INSTANT TO
--     DECIDE would not be.
--   * A seal written here carries `sealed_at`, and its execution
--     carries the evidence's `received_timestamp`. The ledger can then
--     be read for the one property that matters: the book was observed
--     AFTER the decision, every time, or the row is not there.
--
-- WHAT IS MUTABLE HERE AND WHAT IS NOT. A queue must be claimable, so
-- `status`, `claimed_at`, `l2_request_id` and
-- `experimental_decision_id` may be set once the seal is served. The
-- DECISION ITSELF -- the sealed body, its hash, its action, its instant
-- -- may never change, and a trigger refuses the statement that would.
-- That is a narrower rule than the append-only tables use, for a
-- narrower reason: this is a work queue whose payload is evidence.

BEGIN;

CREATE TABLE IF NOT EXISTS bettor_experimental_seals (
    seal_sha                 TEXT NOT NULL,
    experimental_decision_id TEXT PRIMARY KEY,

    experiment_id            TEXT NOT NULL,
    experiment_sha           TEXT NOT NULL,
    eligible_population_id   TEXT
        REFERENCES bettor_eligible_populations (eligible_population_id),
    bettor_opportunity_id    TEXT,
    symbol                   TEXT NOT NULL,
    outcome_leg              TEXT,
    action                   TEXT NOT NULL,
    sealed_at                TIMESTAMPTZ NOT NULL,

    -- The whole sealed object, verbatim. `execute()` re-derives the
    -- hash from it, so a seal that was edited in the database cannot
    -- be executed at all -- it refuses rather than producing a trade.
    seal                     JSONB NOT NULL,

    l2_request_id            TEXT
        REFERENCES bettor_l2_requests (l2_request_id),
    status                   TEXT NOT NULL DEFAULT 'SEALED',
    claimed_at               TIMESTAMPTZ,
    written_at               TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT bettor_exp_seal_status
        CHECK (status IN ('SEALED', 'EXECUTED', 'EXPIRED')),
    CONSTRAINT bettor_exp_seal_action
        CHECK (action IN ('BUY_YES', 'BUY_NO', 'NO_TRADE'))
);

CREATE INDEX IF NOT EXISTS bettor_exp_seals_open_idx
    ON bettor_experimental_seals (status, sealed_at)
    WHERE status = 'SEALED';

-- The decision body is immutable; the queue fields are not.
CREATE OR REPLACE FUNCTION bettor_experimental_seal_body_immutable()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'refused: a sealed experimental decision is never deleted '
            '(%)', OLD.experimental_decision_id;
    END IF;
    IF NEW.seal IS DISTINCT FROM OLD.seal
       OR NEW.seal_sha IS DISTINCT FROM OLD.seal_sha
       OR NEW.action IS DISTINCT FROM OLD.action
       OR NEW.sealed_at IS DISTINCT FROM OLD.sealed_at
       OR NEW.experiment_id IS DISTINCT FROM OLD.experiment_id
       OR NEW.experiment_sha IS DISTINCT FROM OLD.experiment_sha
       OR NEW.symbol IS DISTINCT FROM OLD.symbol
       OR NEW.bettor_opportunity_id IS DISTINCT FROM
          OLD.bettor_opportunity_id
       OR NEW.eligible_population_id IS DISTINCT FROM
          OLD.eligible_population_id THEN
        RAISE EXCEPTION
            'refused: the sealed decision % may not be altered after T0; '
            'only status, claimed_at, l2_request_id may be set',
            OLD.experimental_decision_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS bettor_experimental_seals_body_immutable
    ON bettor_experimental_seals;
CREATE TRIGGER bettor_experimental_seals_body_immutable
    BEFORE UPDATE OR DELETE ON bettor_experimental_seals
    FOR EACH ROW
    EXECUTE FUNCTION bettor_experimental_seal_body_immutable();

-- ── two digests over one book, kept in two columns ───────────────────
--
-- `l2_book_sha` on a decision is the BRIDGE'S digest, taken over the
-- venue's raw levels: it is how the row joins back to the
-- bettor_l2_evidence that produced it, and it must equal that row's.
-- The frozen execution contract takes its OWN digest over the parsed
-- book the walk consumed. They are different functions of the same
-- observation, and putting the second in the first's column would make
-- a decision and its evidence appear to disagree about a book neither
-- of them changed.

ALTER TABLE bettor_experimental_decisions
    ADD COLUMN IF NOT EXISTS walked_book_sha TEXT;

COMMIT;
