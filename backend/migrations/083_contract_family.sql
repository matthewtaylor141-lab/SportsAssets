-- CONTRACT FAMILY, recorded on every binding.
--
-- Owner production check 2026-09-20: all 16 bindings AMBIGUOUS, each
-- refused with "retail leg 'yes' does not name outcome '1.5'". The
-- venue's own eventAttributes.eventOutcome says EVENT_OUTCOME_DIRECTIONAL
-- on every one of them, while the MLS first-to-score set the
-- outcome-name rule was built for says MUTUALLY_EXCLUSIVE. The rule was
-- not wrong; it was being applied to a family it does not describe.
--
-- WHY THE FAMILY GOES ON THE ROW rather than being re-derived. A
-- binding is evidence a trade was taken under, and "which family did
-- we think this was" is part of that. A later reader must be able to
-- see that a spread was bound as a BINARY_PROPOSITION without having
-- to re-fetch refdata that may have changed since.
--
-- THE PROSE CONFLICT COLUMN. On
-- asc-cfb-nill-arz-2026-09-19-1h-pos-13pt5 the venue ships
-- instrument_rules and instrument_rules_display describing OPPOSITE
-- propositions (NIU +13.5 vs ARZ -13.5); with a .5 line there is no
-- push, so one of them is wrong for that instrument. Nothing binds
-- direction from prose -- identity is the registered contract id --
-- but the disagreement is recorded, because a venue field that
-- contradicts three others is worth knowing about before something
-- else starts reading it.

BEGIN;

ALTER TABLE bettor_identity_bindings
    ADD COLUMN IF NOT EXISTS contract_family           TEXT,
    ADD COLUMN IF NOT EXISTS settlement_rule           TEXT,
    ADD COLUMN IF NOT EXISTS settlement_prose_conflict TEXT,
    ADD COLUMN IF NOT EXISTS strike_value              TEXT,
    ADD COLUMN IF NOT EXISTS evaluation_type           TEXT,
    ADD COLUMN IF NOT EXISTS long_participant_id       TEXT,
    ADD COLUMN IF NOT EXISTS short_participant_id      TEXT,
    ADD COLUMN IF NOT EXISTS complement_instrument_id  TEXT;

ALTER TABLE bettor_identity_bindings
    DROP CONSTRAINT IF EXISTS bettor_identity_family_known;
-- Only families this system can actually classify. A new
-- eventOutcome value from the venue lands FAMILY_NOT_IDENTIFIED and is
-- refused rather than guessed at -- which is what stops a
-- three-outcome set from ever being priced as a binary.
ALTER TABLE bettor_identity_bindings
    ADD CONSTRAINT bettor_identity_family_known
        CHECK (contract_family IS NULL
               OR contract_family IN ('BINARY_PROPOSITION',
                                      'MULTI_OUTCOME_SET',
                                      'FAMILY_NOT_IDENTIFIED'));

-- AN ELIGIBLE BINDING MUST KNOW ITS FAMILY. Eligibility with an
-- unclassified family would mean the gate passed without knowing which
-- gate it was.
ALTER TABLE bettor_identity_bindings
    DROP CONSTRAINT IF EXISTS bettor_identity_eligible_knows_family;
ALTER TABLE bettor_identity_bindings
    ADD CONSTRAINT bettor_identity_eligible_knows_family
        CHECK (NOT execution_eligible
               OR contract_family IN ('BINARY_PROPOSITION',
                                      'MULTI_OUTCOME_SET'));

CREATE INDEX IF NOT EXISTS bettor_identity_family_idx
    ON bettor_identity_bindings (contract_family, execution_eligible);

COMMIT;
