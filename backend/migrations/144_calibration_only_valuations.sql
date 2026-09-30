-- ══════════════════════════════════════════════════════════════════════
-- 144 · A VALUATION RECORDED FOR CALIBRATION ONLY, AND STRUCTURALLY UNABLE
--       TO TRADE
-- ══════════════════════════════════════════════════════════════════════
--
-- THE GAP. The only writer of `external_valuations` ran only after an ok venue
-- read. Since d66e89e (2026-09-27) every venue read refuses
-- VENUE_BOOK_CURRENCY_NOT_ESTABLISHED, because the venue has not documented
-- its market-data timing (bettor_stream_currency P5). So no row has been
-- written since, and the odds-source calibration cohort -- which needs the
-- probability, the fixture and the venue contract whose settlement labels
-- it, NOT a tradable venue price -- stopped growing at 53 fixtures.
--
-- WHAT THIS ADDS. The lane now also records the valuation when the venue read
-- refused for currency. Such a row exists for calibration and for nothing
-- else, and "admissible = false" is one boolean a later edit could flip. So
-- the row says what it is FOR, and the database holds it to that:
--
--   record_purpose              ENTRY_DECISION (the default: every row
--                               written before this migration is one) or
--                               CALIBRATION_ONLY.
--   calibration_only_evidence   present exactly on a CALIBRATION_ONLY row:
--                               the refused venue read, the DISPLAYED price
--                               it was compared at (flagged unusable for
--                               orders) and the gate states it met.
--
-- A CALIBRATION_ONLY ROW CAN NEVER CARRY WHAT A TRADE NEEDS. The columns named
-- are exactly the ones migrations 103/116/117 use to carry an executable
-- decision:
--
--   103  admissible, decision, executable_price, cost_per_contract,
--        estimated_edge_per_contract, proposed_size
--   117  execution_estimate (the sized plan), risk_verdict,
--        exposure_observed
--
-- 103's own `external_valuations_admissible_is_complete` already refuses an
-- admissible row with no executable price; this states the rule directly, for
-- every one of those columns, so it does not rest on that side effect.
--
-- AND IT CANNOT BE RELABELLED. `record_purpose` and its evidence are fixed at
-- insert: an UPDATE that changes either raises. A position may not be opened
-- from a calibration-only row (`rn1x_positions.source_valuation_id`, 119).
--
-- The outcome join and the calibration measurement READ these rows -- that is
-- their purpose -- and nothing here restricts either.

BEGIN;

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS record_purpose text NOT NULL
        DEFAULT 'ENTRY_DECISION';

ALTER TABLE external_valuations
    ADD COLUMN IF NOT EXISTS calibration_only_evidence jsonb;

COMMENT ON COLUMN external_valuations.record_purpose IS
    'ENTRY_DECISION: the entry lane''s record of a decision, admitted or '
    'refused. CALIBRATION_ONLY: a valuation recorded while the venue read '
    'refused for book currency, kept so the odds source can be scored against '
    'the venue''s settlement. A CALIBRATION_ONLY row is never admissible, '
    'carries no executable price, size or plan, and is fixed at insert.';

COMMENT ON COLUMN external_valuations.calibration_only_evidence IS
    'On a CALIBRATION_ONLY row only: the refused venue read, the DISPLAYED '
    'price the valuation was compared at (usable_for_orders = false) and the '
    'gate states it met. NULL on every ENTRY_DECISION row.';

ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_record_purpose_declared;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuations_record_purpose_declared
    CHECK (record_purpose IN ('ENTRY_DECISION', 'CALIBRATION_ONLY'));

-- THE RULE ITSELF. Every column that carries an executable decision is empty
-- on a calibration-only row, and it is never admissible.
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_calibration_only_cannot_trade;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuations_calibration_only_cannot_trade
    CHECK (record_purpose <> 'CALIBRATION_ONLY'
           OR (admissible IS FALSE
               AND decision = 'NO_TRADE'
               AND executable_price IS NULL
               AND cost_per_contract IS NULL
               AND estimated_edge_per_contract IS NULL
               AND proposed_size IS NULL
               AND execution_estimate IS NULL
               AND risk_verdict IS NULL
               AND exposure_observed IS NULL));

-- THE EVIDENCE BELONGS TO THE PURPOSE, BOTH WAYS. A calibration-only row must
-- carry it and must say its displayed price is unusable for orders; an entry
-- decision must not carry it.
ALTER TABLE external_valuations
    DROP CONSTRAINT IF EXISTS external_valuations_calibration_evidence_matches;
ALTER TABLE external_valuations
    ADD CONSTRAINT external_valuations_calibration_evidence_matches
    CHECK ((record_purpose = 'ENTRY_DECISION'
            AND calibration_only_evidence IS NULL)
           OR (record_purpose = 'CALIBRATION_ONLY'
               AND calibration_only_evidence IS NOT NULL
               AND (calibration_only_evidence ->> 'usable_for_orders')
                   = 'false'));

-- FIXED AT INSERT. A relabel from CALIBRATION_ONLY to ENTRY_DECISION would
-- make a row whose venue price was never established read as an entry
-- candidate; the reverse would hide an entry decision. Neither is an edit.
CREATE OR REPLACE FUNCTION external_valuations_purpose_is_fixed()
RETURNS trigger AS $$
BEGIN
    IF NEW.record_purpose IS DISTINCT FROM OLD.record_purpose
       OR NEW.calibration_only_evidence
          IS DISTINCT FROM OLD.calibration_only_evidence THEN
        RAISE EXCEPTION
            'external valuation %: record_purpose and its evidence are fixed '
            'at insert (% -> %)', OLD.id, OLD.record_purpose,
            NEW.record_purpose
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS external_valuations_purpose_is_fixed_trg
    ON external_valuations;
CREATE TRIGGER external_valuations_purpose_is_fixed_trg
    BEFORE UPDATE ON external_valuations
    FOR EACH ROW EXECUTE FUNCTION external_valuations_purpose_is_fixed();

-- NO POSITION FROM A CALIBRATION-ONLY ROW. `source_valuation_id` (119) names
-- the valuation that admitted a position; a calibration-only row admitted
-- nothing and never can.
CREATE OR REPLACE FUNCTION rn1x_positions_not_from_calibration_only()
RETURNS trigger AS $$
BEGIN
    IF NEW.source_valuation_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM external_valuations v
             WHERE v.id = NEW.source_valuation_id
               AND v.record_purpose <> 'ENTRY_DECISION') THEN
        RAISE EXCEPTION
            'position %: valuation % is not an entry decision '
            '(CALIBRATION_ONLY_RECORD_IS_NOT_AN_ENTRY_CANDIDATE)',
            NEW.position_id, NEW.source_valuation_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS rn1x_positions_not_from_calibration_only_trg
    ON rn1x_positions;
CREATE TRIGGER rn1x_positions_not_from_calibration_only_trg
    BEFORE INSERT OR UPDATE OF source_valuation_id ON rn1x_positions
    FOR EACH ROW EXECUTE FUNCTION rn1x_positions_not_from_calibration_only();

-- The calibration measurement and the outcome join read by experiment and
-- time; this keeps the purpose split cheap for the census and the readers
-- that select entry decisions only.
CREATE INDEX IF NOT EXISTS external_valuations_purpose_idx
    ON external_valuations (experiment_id, record_purpose, decided_at DESC);

COMMIT;
