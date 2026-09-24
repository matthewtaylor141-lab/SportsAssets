-- 113 — A POSITION THAT EXISTS TO DEMONSTRATE THE MANAGER, LABELLED.
--
-- The manager can only be demonstrated on inventory whose inputs exist.
-- The one live challenger position is an ATP TENNIS fixture, and tennis is
-- not in the provider set, so its chain stops at 3_PROVIDER_FIXTURE with
-- THE_HELD_SPORT_IS_NOT_IN_THE_PROVIDER_SET -- a coverage fact, not a bug
-- to fix by widening anything. Waiting for an RN1 seed to appear in a
-- covered sport is waiting on a coincidence.
--
-- So an ACCEPTANCE POSITION may be created on a supported venue contract:
-- a modelled acquisition at a contemporaneous executable price, with
-- explicit fees, that the existing management lifecycle then manages like
-- any other. It is NOT an RN1 signal, NOT a filled order, and NOT funded.
--
-- `provenance` exists so that label is DURABLE and QUERYABLE rather than
-- living in a policy string somebody might parse loosely. Every read that
-- reports performance must exclude it, and its policy id differs from the
-- benchmark's and the challenger's precisely so no aggregation can fold
-- it in by accident.
--
-- NOTHING EXISTING IS RELABELLED. Rows written before this migration keep
-- a NULL provenance: they came from the lanes they came from, and
-- stamping them now would be a claim about their history.

BEGIN;

ALTER TABLE rn1x_positions
    ADD COLUMN IF NOT EXISTS provenance text;

COMMENT ON COLUMN rn1x_positions.provenance IS
    'How this position came to exist. NULL for rows written before '
    'migration 113. ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY means: created to '
    'demonstrate the manager on a covered exposure, entered at a '
    'contemporaneous executable venue price with explicit fees, NOT '
    'derived from an RN1 signal, NOT an executed order, NOT funded, and '
    'excluded from every benchmark result.';

-- A DECLARED VOCABULARY, so a later reader cannot invent a third meaning.
ALTER TABLE rn1x_positions
    DROP CONSTRAINT IF EXISTS rn1x_provenance_declared;
ALTER TABLE rn1x_positions
    ADD CONSTRAINT rn1x_provenance_declared
    CHECK (provenance IS NULL
           OR provenance IN ('RN1_SIGNAL_DERIVED',
                             'ACCEPTANCE_SYNTHETIC_MODELLED_ENTRY'))
    NOT VALID;

COMMIT;
